"""Real-HTTP hosted-usage denial tests (tests only, no product edit).

Both tests run the real FastAPI ``TestClient`` app with an internal
``BudgetedProvider(delegate, real ledger)`` injected only as
``create_app(provider=...)``. The delegate is the original
server-side synthetic ``ControlsProvider`` loaded from
``tests/test_http_teacher_controls.py`` (explicitly not Meta); the
ledger is a real SQLite store in a test-owned tempfile created by
``initialize_usage``. No network, credential, or live budget is
touched. ``teacher_demo.hosted_usage._now`` is the only clock seam
and is patched to a test-controlled value for each app lifetime.

Expected integration: at the
worker-future error path, after domain admission, a trusted
``UsageDenied`` is terminalized as
``terminalize_request(state, binding, 'error', 503, error.code)``
for the fixed codes ``usage_exhausted``, ``usage_window_closed``,
``usage_rate_limited`` and ``usage_accounting_unavailable``.
No fake ``UsageDenied`` is ever raised here: every denial is caused
by real ledger state (spent count, window, spacing, missing file).
"""
import importlib.util
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

import teacher_demo.hosted_usage as hosted_usage  # noqa: E402
from teacher_demo.hosted_usage import (  # noqa: E402
    BudgetedProvider,
    UsageLedger,
    UsagePolicy,
    initialize_usage,
)
from teacher_demo.http_app import create_app  # noqa: E402

# Load the original controls fixture by exact sibling path so the
# provider, texts and helpers below are that file's own objects.
_FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parent
    / "test_http_teacher_controls.py")
_SPEC = importlib.util.spec_from_file_location(
    "hosted_usage_controls_fixture", _FIXTURE_PATH)
_FIXTURE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _FIXTURE
_SPEC.loader.exec_module(_FIXTURE)

ControlsProvider = _FIXTURE.ControlsProvider
BASE_URL = _FIXTURE.BASE_URL
GRADE = _FIXTURE.GRADE
OBJECTIVE = _FIXTURE.OBJECTIVE
RIGHTS_REF = _FIXTURE.RIGHTS_REF
RAW_A = _FIXTURE.RAW_A
RAW_B = _FIXTURE.RAW_B
EDITED_A = _FIXTURE.EDITED_A
EDITED_B = _FIXTURE.EDITED_B
CHANGED_A = _FIXTURE.CHANGED_A
operation_command = _FIXTURE.operation_command
synthetic_png = _FIXTURE.synthetic_png

BASE_NOW = 2_000_000_000.0
WINDOW_END = BASE_NOW + 3600.0
AFTER_GRADE = "Grade 6 after exhaustion"
AFTER_OBJECTIVE = "Objective after exhaustion: read the reviewed text"
_HEX = frozenset("0123456789abcdef")


class _Delegate:
    """Adapt the one-argument fixture provider to the budgeted call.

    ``BudgetedProvider`` delegates as ``delegate(request,
    context=...)`` after reserving; the original fixture provider
    keeps its exact one-argument call and recording behaviour.
    """

    def __init__(self, inner):
        self.inner = inner

    def __call__(self, request, *, context):
        return self.inner(request)


def _is_32hex(value):
    return (isinstance(value, str) and len(value) == 32
            and all(char in _HEX for char in value))


class HostedUsageHttpTests(unittest.TestCase):
    def _snapshot(self, client):
        response = client.get("/api/session/snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def _request_view(snapshot, request_id):
        return next(item for item in snapshot["requests"]
                    if item["request_id"] == request_id)

    @staticmethod
    def _region(snapshot, section_id):
        return next(item for item in snapshot["regions"]
                    if item["external_section_id"] == section_id)

    @staticmethod
    def _spent(ledger):
        return ledger.snapshot()["spent"]

    def _started_page(self, client):
        created = client.post(
            "/api/sessions", json={}, headers={"Origin": BASE_URL})
        self.assertEqual(created.status_code, 201, created.text)
        csrf = created.json()["csrf_token"]
        self.assertIsInstance(csrf, str)
        self.assertTrue(csrf.strip())
        headers = {"Origin": BASE_URL, "X-CSRF-Token": csrf}
        settings_body = {"grade": GRADE, "objective": OBJECTIVE,
                         "rights_ref": RIGHTS_REF}
        settings = client.put(
            "/api/session/settings", json=settings_body,
            headers=headers)
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertEqual(settings.json(), settings_body)
        started = client.post(
            "/api/session/start", params={"page_index": 0},
            content=synthetic_png(),
            headers={**headers,
                     "Content-Type": "application/octet-stream"})
        self.assertEqual(started.status_code, 201, started.text)
        page = started.json()
        self.assertTrue(page["source_id"].strip())
        self.assertEqual(page["grade"], GRADE)
        self.assertEqual(page["objective"], OBJECTIVE)
        self.assertEqual(page["rights_ref"], RIGHTS_REF)
        self.assertGreater(page["width"], 0)
        self.assertGreater(page["height"], 0)
        return headers, page

    def _checked_snapshot(self, client, response):
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()["snapshot"]
        self.assertEqual(snapshot, self._snapshot(client))
        return snapshot

    def _put_region(self, client, headers, region, text):
        return self._checked_snapshot(client, client.put(
            "/api/session/regions/"
            + quote(region["region_id"], safe=""),
            json={"inclusion": "included", "current_text": text,
                  "current_label": region["current_label"]},
            headers=headers))

    def _raw_ok(self, client, request_id, kind):
        response = client.get(
            "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["kind"], kind)
        self.assertEqual(payload["raw_retention"], "retained")
        return payload["provider_result"]

    def _assert_raw_missing(self, client, request_id):
        response = client.get(
            "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json(),
                         {"detail": "request not found"})

    def _assert_denied(self, client, response, request_id, code, kind):
        self.assertEqual(response.status_code, 503, response.text)
        body = response.json()
        self.assertEqual(set(body), {"request_id", "snapshot",
                                     "raw_retention", "detail"})
        self.assertEqual(body["request_id"], request_id)
        self.assertEqual(body["raw_retention"], "not_available")
        self.assertEqual(set(body["detail"]), {"code", "message"})
        self.assertEqual(body["detail"]["code"], code)
        self.assertEqual(
            body["detail"]["message"],
            "provider request did not complete successfully")
        snapshot = body["snapshot"]
        self.assertEqual(snapshot, self._snapshot(client))
        view = self._request_view(snapshot, request_id)
        self.assertEqual(view["kind"], kind)
        self.assertEqual(view["state"], "terminal")
        self.assertEqual(view["outcome"], "error")
        self._assert_raw_missing(client, request_id)
        return snapshot

    def test_exhausted_questions_after_one_source_attempt(self):
        clock = [BASE_NOW]
        policy = UsagePolicy(
            max_attempts=1, max_output_tokens=64,
            window_end=WINDOW_END, minimum_spacing_seconds=1.0)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                hosted_usage, "_now", lambda: clock[0]):
            path = pathlib.Path(tmp) / "usage.sqlite3"
            initialize_usage(path, policy)
            ledger = UsageLedger(path, policy)
            inner = ControlsProvider()
            budgeted = BudgetedProvider(_Delegate(inner), ledger)
            with TestClient(create_app(provider=budgeted),
                            base_url=BASE_URL) as client:
                headers, page = self._started_page(client)
                self.assertEqual(self._spent(ledger), 0)

                source_command = operation_command(page["source_id"])
                self.assertTrue(_is_32hex(source_command["request_id"]))
                analyzed = client.post(
                    "/api/session/source-analysis",
                    json=source_command, headers=headers)
                self.assertEqual(analyzed.status_code, 201,
                                 analyzed.text)
                source_request_id = source_command["request_id"]
                self.assertEqual(analyzed.json()["request_id"],
                                 source_request_id)
                self.assertEqual(analyzed.json()["raw_retention"],
                                 "retained")
                snapshot = analyzed.json()["snapshot"]
                self.assertEqual(snapshot, self._snapshot(client))
                self.assertEqual(snapshot["source_id"],
                                 page["source_id"])
                self.assertTrue(snapshot["source_analysis_terminal"])
                self.assertFalse(snapshot["can_generate"])
                self.assertEqual(snapshot["included_count"], 0)
                self.assertEqual(snapshot["withheld_count"], 2)
                source_view = self._request_view(
                    snapshot, source_request_id)
                self.assertEqual(source_view["kind"], "source")
                self.assertEqual(source_view["state"], "terminal")
                self.assertEqual(source_view["outcome"], "ok")
                self.assertEqual(len(snapshot["regions"]), 2)
                river = self._region(
                    snapshot, "synthetic-section-river")
                trees = self._region(
                    snapshot, "synthetic-section-trees")
                self.assertEqual(river["raw_text"], RAW_A)
                self.assertEqual(river["current_text"], RAW_A)
                self.assertEqual(trees["raw_text"], RAW_B)
                self.assertEqual(trees["current_text"], RAW_B)
                self.assertEqual(
                    self._raw_ok(client, source_request_id, "source"),
                    inner.source_results[source_request_id])
                self.assertEqual(len(inner.requests), 1)
                self.assertEqual(inner.requests[0]["kind"],
                                 "source_analysis")
                self.assertEqual(inner.requests[0]["request_id"],
                                 source_request_id)
                self.assertEqual(self._spent(ledger), 1)

                snapshot = self._put_region(
                    client, headers, river, EDITED_A)
                snapshot = self._put_region(
                    client, headers,
                    self._region(snapshot, "synthetic-section-trees"),
                    EDITED_B)
                self.assertTrue(snapshot["can_generate"])
                self.assertEqual(snapshot["included_count"], 2)
                self.assertEqual(snapshot["questions"], [])
                self.assertEqual(
                    self._region(snapshot, "synthetic-section-river")
                    ["current_text"], EDITED_A)
                self.assertEqual(
                    self._region(snapshot, "synthetic-section-trees")
                    ["current_text"], EDITED_B)
                self.assertEqual(len(inner.requests), 1)
                self.assertEqual(self._spent(ledger), 1)

                clock[0] = BASE_NOW + 2.0
                question_command = operation_command(
                    snapshot["source_id"])
                self.assertTrue(
                    _is_32hex(question_command["request_id"]))
                denied = client.post(
                    "/api/session/questions/generate",
                    json=question_command, headers=headers)
                snapshot = self._assert_denied(
                    client, denied, question_command["request_id"],
                    "usage_exhausted", "questions")
                self.assertEqual(snapshot["source_id"],
                                 page["source_id"])
                self.assertEqual(snapshot["questions"], [])
                self.assertEqual(snapshot["preview_items"], [])
                preview = client.get("/api/session/preview")
                self.assertEqual(preview.status_code, 200,
                                 preview.text)
                self.assertEqual(preview.json(), {"items": []})
                question_slots = [
                    item for item in snapshot["slot_outcomes"]
                    if item["request_id"]
                    == question_command["request_id"]]
                self.assertEqual(6, len(question_slots))
                self.assertEqual(
                    6, len(set(item["slot_id"]
                    for item in question_slots)))
                self.assertTrue(all(
                    item["outcome"] == "pending"
                    and item["question_id"] is None
                    and item["abstain_reason"] is None
                    for item in question_slots))
                self.assertEqual(len(inner.requests), 1)
                self.assertEqual(self._spent(ledger), 1)
                self.assertEqual(
                    self._raw_ok(client, source_request_id, "source"),
                    inner.source_results[source_request_id])

                # Exhaustion closes only budgeted provider work:
                # settings, review and snapshot reads still commit
                # and project their exact new values.
                after_body = {"grade": AFTER_GRADE,
                              "objective": AFTER_OBJECTIVE,
                              "rights_ref": RIGHTS_REF}
                updated = client.put(
                    "/api/session/settings", json=after_body,
                    headers=headers)
                self.assertEqual(updated.status_code, 200,
                                 updated.text)
                self.assertEqual(updated.json(), after_body)
                snapshot = self._snapshot(client)
                self.assertEqual(snapshot["grade"], AFTER_GRADE)
                self.assertEqual(snapshot["objective"],
                                 AFTER_OBJECTIVE)
                self.assertEqual(snapshot["source_id"],
                                 page["source_id"])
                snapshot = self._put_region(
                    client, headers,
                    self._region(snapshot, "synthetic-section-river"),
                    CHANGED_A)
                river = self._region(
                    snapshot, "synthetic-section-river")
                self.assertEqual(river["raw_text"], RAW_A)
                self.assertEqual(river["current_text"], CHANGED_A)
                self.assertEqual(river["text_author"], "teacher")
                self.assertEqual(river["inclusion"], "included")
                self.assertTrue(snapshot["can_generate"])
                self.assertEqual(snapshot["questions"], [])
                self.assertEqual(len(inner.requests), 1)
                self.assertEqual(self._spent(ledger), 1)

    def _check_source_denial(self, code, spacing, prime):
        clock = [BASE_NOW]
        policy = UsagePolicy(
            max_attempts=5, max_output_tokens=64,
            window_end=WINDOW_END, minimum_spacing_seconds=spacing)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
                hosted_usage, "_now", lambda: clock[0]):
            path = pathlib.Path(tmp) / "usage.sqlite3"
            initialize_usage(path, policy)
            ledger = UsageLedger(path, policy)
            inner = ControlsProvider()
            budgeted = BudgetedProvider(_Delegate(inner), ledger)

            def check_unchanged(expected_spent):
                self.assertEqual(len(inner.requests), 0)
                if expected_spent is None:
                    self.assertFalse(path.exists())
                else:
                    self.assertEqual(self._spent(ledger),
                                     expected_spent)

            with TestClient(create_app(provider=budgeted),
                            base_url=BASE_URL) as client:
                headers, page = self._started_page(client)
                # Real ledger cause only: prime, expiry, or removal.
                if prime == "rate":
                    ledger.reserve()
                    expected_spent = 1
                elif prime == "window":
                    clock[0] = WINDOW_END
                    expected_spent = 0
                else:
                    for suffix in ("", "-journal", "-wal", "-shm"):
                        try:
                            pathlib.Path(str(path) + suffix).unlink()
                        except FileNotFoundError:
                            pass
                    expected_spent = None
                check_unchanged(expected_spent)
                before = self._snapshot(client)
                self.assertEqual(before["source_id"],
                                 page["source_id"])
                self.assertEqual(before["requests"], [])
                self.assertEqual(before["regions"], [])

                # Pre-admission rules run before any reserve, even
                # with the ledger primed, expired, or missing.
                wrong = client.post(
                    "/api/session/source-analysis",
                    json=operation_command(page["source_id"] + "-wrong"),
                    headers=headers)
                self.assertEqual(wrong.status_code, 409, wrong.text)
                self.assertEqual(wrong.json(),
                                 {"detail": "source changed"})
                self.assertEqual(self._snapshot(client), before)
                check_unchanged(expected_spent)
                not_ready = client.post(
                    "/api/session/questions/generate",
                    json=operation_command(before["source_id"]),
                    headers=headers)
                self.assertEqual(not_ready.status_code, 409,
                                 not_ready.text)
                self.assertEqual(not_ready.json(),
                                 {"detail": "generation not ready"})
                self.assertEqual(self._snapshot(client), before)
                check_unchanged(expected_spent)

                denied_command = operation_command(before["source_id"])
                self.assertTrue(_is_32hex(denied_command["request_id"]))
                denied = client.post(
                    "/api/session/source-analysis",
                    json=denied_command, headers=headers)
                snapshot = self._assert_denied(
                    client, denied, denied_command["request_id"],
                    code, "source")
                self.assertEqual(snapshot["source_id"],
                                 page["source_id"])
                self.assertEqual(snapshot["regions"], [])
                self.assertEqual(snapshot["questions"], [])
                self.assertEqual(snapshot["preview_items"], [])
                check_unchanged(expected_spent)
                if prime == "window":
                    self.assertTrue(ledger.snapshot()["closed"])

                # The admitted denial does not re-order later
                # pre-admission refusals, and they still never spend.
                duplicate = client.post(
                    "/api/session/source-analysis",
                    json=denied_command, headers=headers)
                self.assertEqual(duplicate.status_code, 409,
                                 duplicate.text)
                self.assertEqual(duplicate.json(),
                                 {"detail": "duplicate request"})
                self.assertEqual(self._snapshot(client), snapshot)
                check_unchanged(expected_spent)
                wrong_after = client.post(
                    "/api/session/source-analysis",
                    json=operation_command(page["source_id"] + "-wrong"),
                    headers=headers)
                self.assertEqual(wrong_after.status_code, 409,
                                 wrong_after.text)
                self.assertEqual(wrong_after.json(),
                                 {"detail": "source changed"})
                self.assertEqual(self._snapshot(client), snapshot)
                check_unchanged(expected_spent)

                if prime == "rate":
                    # Capacity returns on this same nonexhausted
                    # store once spacing elapses. A fresh session
                    # keeps the healthy proof independent of any
                    # retry-after-error rule on the denied page.
                    clock[0] = BASE_NOW + 61.0
                    headers, page = self._started_page(client)
                    healthy_command = operation_command(
                        page["source_id"])
                    self.assertTrue(
                        _is_32hex(healthy_command["request_id"]))
                    healthy = client.post(
                        "/api/session/source-analysis",
                        json=healthy_command, headers=headers)
                    self.assertEqual(healthy.status_code, 201,
                                     healthy.text)
                    self.assertEqual(healthy.json()["request_id"],
                                     healthy_command["request_id"])
                    self.assertEqual(healthy.json()["raw_retention"],
                                     "retained")
                    snapshot = healthy.json()["snapshot"]
                    self.assertEqual(snapshot, self._snapshot(client))
                    self.assertEqual(snapshot["source_id"],
                                     page["source_id"])
                    view = self._request_view(
                        snapshot, healthy_command["request_id"])
                    self.assertEqual(view["kind"], "source")
                    self.assertEqual(view["state"], "terminal")
                    self.assertEqual(view["outcome"], "ok")
                    self.assertEqual(len(snapshot["regions"]), 2)
                    self.assertEqual(
                        self._raw_ok(
                            client, healthy_command["request_id"],
                            "source"),
                        inner.source_results[
                            healthy_command["request_id"]])
                    self.assertEqual(len(inner.requests), 1)
                    self.assertEqual(self._spent(ledger), 2)

    def test_source_denials_preserve_preadmission_rules(self):
        for code, spacing, prime in (
                ("usage_window_closed", 1.0, "window"),
                ("usage_rate_limited", 60.0, "rate"),
                ("usage_accounting_unavailable", 1.0, "unavailable")):
            with self.subTest(code=code):
                self._check_source_denial(code, spacing, prime)


if __name__ == "__main__":
    unittest.main()
