"""Safety tests for the teacher HTTP journey.

Real app/domain/decoder through TestClient with synthetic
in-process providers only.
"""
import copy
import io
import pathlib
import secrets
import sys
import threading
import time
import unittest
from unittest import mock
from urllib.parse import quote

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from teacher_demo import http_app as http_app_module  # noqa: E402
from teacher_demo.http_app import create_app  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
GRADE = "  الصَّفُّ ٥  "
OBJECTIVE = "  هَدَفٌ: قِرَاءَةُ النَّصِّ ١٢٣  "
RAW_TEXT = "  اَلْمَاءُ يَجْرِي فِي النَّهْرِ ١٢٣  "
FACT = "اَلْمَاءُ يَجْرِي بِسُرْعَةٍ"
EDITED_TEXT = f"  {FACT} فِي النَّهْرِ ١٢٣  "
SOURCE_RESULT = {
    "raw": {
        "sections": [{
            "section_id": "synthetic-section-1", "kind": "body",
            "text": RAW_TEXT, "review_reasons": ["synthetic-flag"]}],
        "page_continues": False, "review_notes": [],
    },
    "model_name": "synthetic-test-provider (not Meta)",
    "response_id": "synthetic-source-response-1",
    "outcome": "ok",
}


def operation_command(source_id):
    return {"request_id": secrets.token_hex(16),
            "source_id": source_id}


def synthetic_png():
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (200, 30, 40, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


class ConfigurableProvider:
    def __init__(self, source="valid", questions="valid"):
        self.source = source
        self.questions = questions
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def _maybe_block(self, behavior):
        if behavior == "block":
            self.release.clear()
            self.entered.set()
            self.release.wait(timeout=10)

    def __call__(self, request):
        if request["kind"] == "source_analysis":
            self._maybe_block(self.source)
            if self.source == "raise":
                raise RuntimeError("synthetic provider failure")
            if self.source == "invalid":
                result = copy.deepcopy(SOURCE_RESULT)
                result["raw"]["sections"][0].pop("text")
                return result
            return copy.deepcopy(SOURCE_RESULT)
        self._maybe_block(self.questions)
        reviewed = dict(request["reviewed_regions"])
        region_id = next(iter(reviewed))
        items = []
        for slot in request["requested_slots"]:
            slot = dict(slot)
            if slot["level"] == "remember":
                items.append({
                    "slot_id": slot["slot_id"], "level": "remember",
                    "status": "draft", "question": "  سُؤَالٌ؟  ",
                    "answer_guidance": "إرشادٌ مدعومٌ بالنص.",
                    "cognitive_explanation": "Synthetic recall task.",
                    "hypothetical_assumptions": [],
                    "evaluate_criteria": [], "create_rubric": [],
                    "evidence": [
                        {"region_id": region_id, "quote": FACT}],
                    "required_order_relationships": [],
                    "abstain_reason": None})
            else:
                items.append({
                    "slot_id": slot["slot_id"], "level": slot["level"],
                    "status": "abstain", "question": "",
                    "answer_guidance": "", "cognitive_explanation": "",
                    "hypothetical_assumptions": [],
                    "evaluate_criteria": [], "create_rubric": [],
                    "evidence": [], "required_order_relationships": [],
                    "abstain_reason": "synthetic abstention"})
        return {"raw": {"items": items},
                "model_name": "synthetic-test-provider (not Meta)",
                "response_id": "synthetic-question-response-1",
                "outcome": "ok"}


class TeacherSafetyTests(unittest.TestCase):
    def start_session(self, client):
        created = client.post(
            "/api/sessions", json={}, headers={"Origin": BASE_URL})
        self.assertEqual(created.status_code, 201, created.text)
        cookie = created.headers["set-cookie"].split(";", 1)[0]
        headers = {"Origin": BASE_URL,
                   "X-CSRF-Token": created.json()["csrf_token"],
                   "Cookie": cookie}
        settings = client.put(
            "/api/session/settings",
            json={"grade": GRADE, "objective": OBJECTIVE,
                  "rights_ref": "synthetic"}, headers=headers)
        self.assertEqual(settings.status_code, 200, settings.text)
        started = client.post(
            "/api/session/start", params={"page_index": 0},
            content=synthetic_png(),
            headers={**headers,
                     "Content-Type": "application/octet-stream"})
        self.assertEqual(started.status_code, 201, started.text)
        return headers, started.json()["source_id"]

    def snapshot(self, client):
        response = client.get("/api/session/snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def analyze_and_include(self, client, headers, source_id):
        command = operation_command(source_id)
        analyzed = client.post(
            "/api/session/source-analysis", json=command,
            headers=headers)
        self.assertEqual(analyzed.status_code, 201, analyzed.text)
        self.assertEqual(analyzed.json()["request_id"],
                         command["request_id"])
        snapshot = analyzed.json()["snapshot"]
        region = snapshot["regions"][0]
        reviewed = client.put(
            "/api/session/regions/"
            + quote(region["region_id"], safe=""),
            json={"inclusion": "included",
                  "current_text": EDITED_TEXT,
                  "current_label": region["current_label"]},
            headers=headers)
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        return analyzed.json()["request_id"], region["region_id"]

    def post_async(self, app, path, headers, *, body=None):
        result = {}
        payload = {} if body is None else body

        def run():
            try:
                with TestClient(app, base_url=BASE_URL) as client:
                    result["response"] = client.post(
                        path, json=payload, headers=headers)
            except BaseException as exc:  # surfaced by finish_async
                result["error"] = repr(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, result

    def finish_async(self, thread, result):
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "async HTTP call hung")
        self.assertNotIn("error", result, result.get("error"))
        return result["response"]

    def request_from(self, snapshot, request_id):
        return next(item for item in snapshot["requests"]
                    if item["request_id"] == request_id)

    def put_region(self, client, headers, region, *,
                   inclusion=None, current_text=None,
                   current_label=None):
        return client.put(
            "/api/session/regions/"
            + quote(region["region_id"], safe=""),
            json={
                "inclusion": (region["inclusion"] if inclusion is None
                              else inclusion),
                "current_text": (region["current_text"]
                                 if current_text is None
                                 else current_text),
                "current_label": (region["current_label"]
                                  if current_label is None
                                  else current_label)},
            headers=headers)

    def put_settings(self, client, headers, *, grade=GRADE,
                     objective=OBJECTIVE, rights_ref="synthetic"):
        return client.put(
            "/api/session/settings",
            json={"grade": grade, "objective": objective,
                  "rights_ref": rights_ref}, headers=headers)

    def raw_result(self, client, request_id):
        return client.get(
            "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")

    def generate(self, client, headers, source_id):
        command = operation_command(source_id)
        response = client.post(
            "/api/session/questions/generate", json=command,
            headers=headers)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["request_id"],
                         command["request_id"])
        return response.json()

    def approve(self, client, headers, question_id):
        response = client.post(
            "/api/session/questions/"
            + quote(question_id, safe="") + "/approve",
            json={}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["snapshot"]

    def test_cross_session_isolation_and_raw_404(self):
        app = create_app(provider=ConfigurableProvider())
        with TestClient(app, base_url=BASE_URL) as client_a, \
                TestClient(app, base_url=BASE_URL) as client_b:
            headers_a, source_a = self.start_session(client_a)
            headers_b, _source_b = self.start_session(client_b)
            request_a, _region_a = self.analyze_and_include(
                client_a, headers_a, source_a)
            snapshot_a = self.snapshot(client_a)
            snapshot_b = self.snapshot(client_b)
            self.assertEqual(len(snapshot_a["regions"]), 1)
            self.assertEqual(snapshot_b["regions"], [])
            self.assertEqual(snapshot_b["requests"], [])
            self.assertEqual(
                self.raw_result(client_b, request_a).status_code, 404)
            changed_b = self.put_settings(
                client_b, headers_b, grade="  الصَّفُّ ٩  ")
            self.assertEqual(changed_b.status_code, 200, changed_b.text)
            self.assertEqual(
                self.snapshot(client_b)["grade"], "  الصَّفُّ ٩  ")
            self.assertEqual(self.snapshot(client_a), snapshot_a)
            self.assertEqual(
                self.raw_result(client_a, request_a)
                .json()["provider_result"], SOURCE_RESULT)

    def test_host_origin_csrf_and_no_store_controls(self):
        app = create_app(provider=ConfigurableProvider())
        with TestClient(app, base_url="http://invalid.example") as bad:
            refused = bad.post(
                "/api/sessions", json={},
                headers={"Origin": BASE_URL})
            self.assertEqual(refused.status_code, 400, refused.text)
            self.assertEqual(
                refused.json()["detail"], "invalid host")
        with TestClient(app, base_url=BASE_URL) as client:
            for headers in ({}, {"Origin": "http://evil.example"}):
                refused = client.post(
                    "/api/sessions", json={}, headers=headers)
                self.assertEqual(refused.status_code, 403, refused.text)
            headers, _source_id = self.start_session(client)
            before = self.snapshot(client)
            created_probe = client.get("/api/session/snapshot")
            self.assertEqual(
                created_probe.headers.get("cache-control"), "no-store")
            for csrf in (None, "wrong-token"):
                attempt_headers = {"Origin": BASE_URL}
                if csrf is not None:
                    attempt_headers["X-CSRF-Token"] = csrf
                refused = client.put(
                    "/api/session/settings",
                    json={"grade": "changed", "objective": OBJECTIVE,
                          "rights_ref": "synthetic"},
                    headers=attempt_headers)
                self.assertEqual(refused.status_code, 403, refused.text)
            self.assertEqual(self.snapshot(client), before)
            original = client.get("/api/session/page/original")
            self.assertEqual(original.status_code, 200, original.text)
            self.assertEqual(
                original.headers.get("cache-control"), "no-store")
            self.assertEqual(original.content, synthetic_png())

    def test_default_provider_unavailable_without_beginning_request(self):
        with TestClient(create_app(), base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            refused = client.post(
                "/api/session/source-analysis",
                json=operation_command(source_id), headers=headers)
            self.assertEqual(refused.status_code, 503, refused.text)
            self.assertEqual(
                refused.json()["detail"]["code"],
                "provider_unavailable")
            snapshot = self.snapshot(client)
            self.assertEqual(snapshot["requests"], [])
            self.assertEqual(snapshot["regions"], [])
            self.assertFalse(snapshot["can_generate"])

    def test_invalid_combined_region_edit_changes_nothing(self):
        app = create_app(provider=ConfigurableProvider())
        with TestClient(app, base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            request_id, _region_id = self.analyze_and_include(
                client, headers, source_id)
            before = self.snapshot(client)
            region = before["regions"][0]
            unpublished = "  لَا يَجِبُ أَنْ يُنْشَرَ هٰذَا  "
            invalid = []
            invalid.append(self.put_region(
                client, headers, region, inclusion="sometimes",
                current_text=unpublished))
            invalid.append(self.put_region(
                client, headers, region, current_text=unpublished,
                current_label="   "))
            invalid.append(client.put(
                "/api/session/regions/"
                + quote(region["region_id"], safe=""),
                json={"inclusion": "included",
                      "current_text": unpublished,
                      "current_label": region["current_label"],
                      "unexpected": True}, headers=headers))
            for response in invalid:
                self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(self.snapshot(client), before)
            self.assertEqual(
                self.raw_result(client, request_id)
                .json()["provider_result"], SOURCE_RESULT)

    def test_review_gate_and_preview_only_after_explicit_approval(self):
        app = create_app(provider=ConfigurableProvider())
        with TestClient(app, base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            source_command = operation_command(source_id)
            analyzed = client.post(
                "/api/session/source-analysis", json=source_command,
                headers=headers)
            self.assertEqual(analyzed.status_code, 201, analyzed.text)
            self.assertEqual(analyzed.json()["request_id"],
                             source_command["request_id"])
            snapshot = analyzed.json()["snapshot"]
            self.assertFalse(snapshot["can_generate"])
            self.assertEqual(snapshot["included_count"], 0)
            self.assertEqual(
                snapshot["regions"][0]["inclusion"], "unreviewed")
            refused = client.post(
                "/api/session/questions/generate",
                json=operation_command(source_id), headers=headers)
            self.assertEqual(refused.status_code, 409, refused.text)
            snapshot = self.snapshot(client)
            self.assertEqual(
                [item["kind"] for item in snapshot["requests"]],
                ["source"])
            region = snapshot["regions"][0]
            reviewed = self.put_region(
                client, headers, region, inclusion="included",
                current_text=EDITED_TEXT)
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            generated = self.generate(client, headers, source_id)
            snapshot = generated["snapshot"]
            self.assertEqual(len(snapshot["questions"]), 1)
            self.assertEqual(snapshot["questions"][0]["status"], "draft")
            self.assertEqual(snapshot["preview_items"], [])
            preview = client.get("/api/session/preview")
            self.assertEqual(preview.json()["items"], [])
            question_id = snapshot["questions"][0]["question_id"]
            snapshot = self.approve(client, headers, question_id)
            self.assertEqual(
                snapshot["questions"][0]["status"], "approved")
            preview = client.get("/api/session/preview")
            items = preview.json()["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["question_id"], question_id)
            self.assertEqual(
                items[0]["resolved_anchors"],
                [[region["region_id"], FACT, EDITED_TEXT]])

    def test_padded_arabic_supplementary_unicode_and_jpeg_exact(self):
        inner = ConfigurableProvider()
        seen = []

        def recording_provider(request):
            seen.append(copy.deepcopy(dict(request)))
            return inner(request)

        buffer = io.BytesIO()
        with Image.new("RGB", (7, 5), (10, 120, 200)) as image:
            image.save(buffer, format="JPEG")
        jpeg_bytes = buffer.getvalue()
        supplementary = f"  {FACT} 𝄞𐍈🧪 فِي النَّهْرِ ١٢٣  "
        with TestClient(create_app(provider=recording_provider),
                        base_url=BASE_URL) as client:
            created = client.post(
                "/api/sessions", json={}, headers={"Origin": BASE_URL})
            self.assertEqual(created.status_code, 201, created.text)
            headers = {
                "Origin": BASE_URL,
                "X-CSRF-Token": created.json()["csrf_token"]}
            settings = self.put_settings(client, headers)
            self.assertEqual(settings.status_code, 200, settings.text)
            started = client.post(
                "/api/session/start", params={"page_index": 0},
                content=jpeg_bytes,
                headers={**headers,
                         "Content-Type": "application/octet-stream"})
            self.assertEqual(started.status_code, 201, started.text)
            page = started.json()
            source_id = page["source_id"]
            self.assertEqual(page["media_type"], "image/jpeg")
            source_command = operation_command(source_id)
            analyzed = client.post(
                "/api/session/source-analysis", json=source_command,
                headers=headers)
            self.assertEqual(analyzed.status_code, 201, analyzed.text)
            self.assertEqual(analyzed.json()["request_id"],
                             source_command["request_id"])
            self.assertEqual(seen[0]["kind"], "source_analysis")
            self.assertEqual(seen[0]["media_type"], "image/png")
            self.assertTrue(
                seen[0]["png_bytes"].startswith(b"\x89PNG"))
            snapshot = analyzed.json()["snapshot"]
            self.assertEqual(snapshot["grade"], GRADE)
            self.assertEqual(snapshot["objective"], OBJECTIVE)
            region = snapshot["regions"][0]
            self.assertEqual(region["raw_text"], RAW_TEXT)
            edited = self.put_region(
                client, headers, region, inclusion="included",
                current_text=supplementary)
            self.assertEqual(edited.status_code, 200, edited.text)
            snapshot = self.snapshot(client)
            self.assertEqual(
                snapshot["regions"][0]["current_text"], supplementary)
            self.assertEqual(snapshot["regions"][0]["raw_text"], RAW_TEXT)
            request_id = analyzed.json()["request_id"]
            self.assertEqual(
                self.raw_result(client, request_id)
                .json()["provider_result"], SOURCE_RESULT)
            original = client.get("/api/session/page/original")
            self.assertEqual(original.status_code, 200, original.text)
            self.assertEqual(original.content, jpeg_bytes)
            self.assertEqual(
                original.headers["content-type"], "image/jpeg")
            generated = self.generate(client, headers, source_id)
            question_id = (
                generated["snapshot"]["questions"][0]["question_id"])
            self.approve(client, headers, question_id)
            preview = client.get("/api/session/preview")
            self.assertEqual(
                preview.json()["items"][0]["resolved_anchors"],
                [[region["region_id"], FACT, supplementary]])

    def test_blocked_provider_snapshot_cancel_and_late_refusal(self):
        provider = ConfigurableProvider(source="block")
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            command = operation_command(source_id)
            request_id = command["request_id"]
            thread, result = self.post_async(
                app, "/api/session/source-analysis", headers,
                body=command)
            try:
                self.assertTrue(
                    provider.entered.wait(timeout=5),
                    "provider was not entered")
                snapshot = self.snapshot(client)
                self.assertEqual(len(snapshot["requests"]), 1)
                working = self.request_from(snapshot, request_id)
                self.assertEqual(working["state"], "working")
                cancelled = client.post(
                    "/api/session/requests/"
                    + quote(request_id, safe="") + "/cancel",
                    json={"source_id": source_id}, headers=headers)
                self.assertEqual(
                    cancelled.status_code, 200, cancelled.text)
                snapshot = self.snapshot(client)
                closed = self.request_from(snapshot, request_id)
                self.assertEqual(closed["state"], "terminal")
                self.assertEqual(closed["outcome"], "cancelled")
            finally:
                provider.release.set()
            late = self.finish_async(thread, result)
            self.assertEqual(late.status_code, 409, late.text)
            self.assertEqual(
                late.json()["detail"]["code"], "request_closed")
            snapshot = self.snapshot(client)
            self.assertEqual(snapshot["regions"], [])
            closed = self.request_from(snapshot, request_id)
            self.assertEqual(closed["outcome"], "cancelled")
            claimed = late.json()["raw_retention"]
            raw = self.raw_result(client, request_id)
            if claimed == "retained":
                self.assertEqual(raw.status_code, 200, raw.text)
                self.assertEqual(
                    raw.json()["provider_result"], SOURCE_RESULT)
            else:
                self.assertEqual(claimed, "omitted_capacity")
                self.assertEqual(raw.status_code, 404, raw.text)

    def test_blocked_provider_new_start_and_late_source_ignored(self):
        provider = ConfigurableProvider(source="block")
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            command = operation_command(source_id)
            request_id = command["request_id"]
            thread, result = self.post_async(
                app, "/api/session/source-analysis", headers,
                body=command)
            try:
                self.assertTrue(
                    provider.entered.wait(timeout=5),
                    "provider was not entered")
                working_snapshot = self.snapshot(client)
                self.assertEqual(
                    self.request_from(working_snapshot, request_id)
                    ["state"], "working")
                restarted = client.post(
                    "/api/session/start", params={"page_index": 0},
                    content=synthetic_png(),
                    headers={**headers,
                             "Content-Type": "application/octet-stream"})
                self.assertEqual(restarted.status_code, 201,
                                 restarted.text)
                fresh = self.snapshot(client)
                self.assertEqual(fresh["requests"], [])
                self.assertEqual(fresh["regions"], [])
            finally:
                provider.release.set()
            late = self.finish_async(thread, result)
            self.assertEqual(late.status_code, 409, late.text)
            self.assertEqual(
                late.json()["detail"]["code"], "source_changed")
            self.assertEqual(
                late.json()["raw_retention"], "not_available")
            snapshot = self.snapshot(client)
            self.assertEqual(snapshot["requests"], [])
            self.assertEqual(snapshot["regions"], [])

    def test_same_value_settings_do_not_stale_blocked_source(self):
        provider = ConfigurableProvider(source="block")
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            command = operation_command(source_id)
            thread, result = self.post_async(
                app, "/api/session/source-analysis", headers,
                body=command)
            try:
                self.assertTrue(provider.entered.wait(timeout=5))
                before = self.snapshot(client)
                repeated = self.put_settings(client, headers)
                self.assertEqual(repeated.status_code, 200, repeated.text)
                during = self.snapshot(client)
                self.assertEqual(during, before)
                self.assertEqual(
                    during["settings_revision"],
                    before["settings_revision"])
                self.assertEqual(
                    during["requests"][0]["bound_settings_revision"],
                    during["settings_revision"])
            finally:
                provider.release.set()
            completed = self.finish_async(thread, result)
            self.assertEqual(completed.status_code, 201, completed.text)
            snapshot = completed.json()["snapshot"]
            self.assertEqual(len(snapshot["regions"]), 1)
            self.assertEqual(snapshot["regions"][0]["raw_text"], RAW_TEXT)
            request_id = command["request_id"]
            self.assertEqual(completed.json()["request_id"], request_id)
            self.assertEqual(
                self.request_from(snapshot, request_id)["outcome"], "ok")

    def test_settings_change_away_and_back_stales_blocked_source(self):
        provider = ConfigurableProvider(source="block")
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            thread, result = self.post_async(
                app, "/api/session/source-analysis", headers,
                body=operation_command(source_id))
            try:
                self.assertTrue(provider.entered.wait(timeout=5))
                before = self.snapshot(client)
                away = self.put_settings(
                    client, headers, grade="  الصَّفُّ ٦  ")
                self.assertEqual(away.status_code, 200, away.text)
                back = self.put_settings(client, headers)
                self.assertEqual(back.status_code, 200, back.text)
                during = self.snapshot(client)
                self.assertEqual(during["grade"], before["grade"])
                self.assertEqual(during["objective"], before["objective"])
                self.assertEqual(
                    during["settings_revision"],
                    before["settings_revision"] + 2)
                self.assertEqual(
                    during["requests"][0]["bound_settings_revision"],
                    before["settings_revision"])
            finally:
                provider.release.set()
            stale = self.finish_async(thread, result)
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(
                stale.json()["detail"]["code"], "request_stale")
            snapshot = self.snapshot(client)
            self.assertEqual(snapshot["regions"], [])
            self.assertEqual(snapshot["grade"], GRADE)
            self.assertEqual(
                snapshot["requests"][0]["state"], "terminal")
            self.assertEqual(
                snapshot["requests"][0]["outcome"], "stale")

    def test_same_value_region_and_reapproval_keep_question_current(self):
        provider = ConfigurableProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            self.analyze_and_include(client, headers, source_id)
            first = self.generate(client, headers, source_id)
            approved_id = (
                first["snapshot"]["questions"][0]["question_id"])
            self.approve(client, headers, approved_id)
            provider.questions = "block"
            second_command = operation_command(source_id)
            thread, result = self.post_async(
                app, "/api/session/questions/generate", headers,
                body=second_command)
            try:
                self.assertTrue(provider.entered.wait(timeout=5))
                before = self.snapshot(client)
                working = [item for item in before["requests"]
                           if item["state"] == "working"]
                self.assertEqual(len(working), 1)
                region = before["regions"][0]
                repeated_region = self.put_region(
                    client, headers, region)
                self.assertEqual(
                    repeated_region.status_code, 200, repeated_region.text)
                self.assertEqual(self.snapshot(client), before)
                repeated_approval = client.post(
                    "/api/session/questions/"
                    + quote(approved_id, safe="") + "/approve",
                    json={}, headers=headers)
                self.assertEqual(
                    repeated_approval.status_code, 200,
                    repeated_approval.text)
                during = self.snapshot(client)
                self.assertEqual(
                    during["source_revision"],
                    before["source_revision"])
                self.assertEqual(
                    during["settings_revision"],
                    before["settings_revision"])
                self.assertEqual(
                    during["questions"][0]["status"], "approved")
            finally:
                provider.release.set()
            completed = self.finish_async(thread, result)
            self.assertEqual(completed.status_code, 201, completed.text)
            snapshot = completed.json()["snapshot"]
            statuses = {item["question_id"]: item["status"]
                        for item in snapshot["questions"]}
            self.assertEqual(statuses[approved_id], "approved")
            second_id = second_command["request_id"]
            self.assertEqual(completed.json()["request_id"], second_id)
            second_questions = [
                item for item in snapshot["questions"]
                if item["request_id"] == second_id]
            self.assertEqual(len(second_questions), 1)
            self.assertEqual(second_questions[0]["status"], "draft")
            preview = client.get("/api/session/preview")
            self.assertEqual(
                [item["question_id"] for item in preview.json()["items"]],
                [approved_id])

    def test_region_change_away_and_back_stales_blocked_question(self):
        provider = ConfigurableProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            self.analyze_and_include(client, headers, source_id)
            provider.questions = "block"
            thread, result = self.post_async(
                app, "/api/session/questions/generate", headers,
                body=operation_command(source_id))
            try:
                self.assertTrue(provider.entered.wait(timeout=5))
                before = self.snapshot(client)
                region = before["regions"][0]
                away_text = "  نَصٌّ آخَرُ تَمَامًا ٤٥٦  "
                away = self.put_region(
                    client, headers, region, current_text=away_text)
                self.assertEqual(away.status_code, 200, away.text)
                away_region = self.snapshot(client)["regions"][0]
                back = self.put_region(
                    client, headers, away_region,
                    current_text=EDITED_TEXT)
                self.assertEqual(back.status_code, 200, back.text)
                during = self.snapshot(client)
                self.assertEqual(
                    during["regions"][0]["current_text"], EDITED_TEXT)
                self.assertEqual(
                    during["source_revision"],
                    before["source_revision"] + 2)
                working = [item for item in during["requests"]
                           if item["state"] == "working"][0]
                self.assertEqual(
                    working["bound_source_revision"],
                    before["source_revision"])
            finally:
                provider.release.set()
            stale = self.finish_async(thread, result)
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertEqual(
                stale.json()["detail"]["code"], "request_stale")
            snapshot = self.snapshot(client)
            self.assertEqual(snapshot["questions"], [])
            self.assertEqual(snapshot["preview_items"], [])
            question_requests = [
                item for item in snapshot["requests"]
                if item["kind"] == "questions"]
            self.assertEqual(question_requests[0]["state"], "terminal")
            self.assertEqual(question_requests[0]["outcome"], "stale")

    def test_provider_timeout_is_terminal_and_worker_eventually_drains(self):
        inner = ConfigurableProvider(source="block")
        returned = threading.Event()

        def provider(request):
            try:
                return inner(request)
            finally:
                returned.set()

        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, source_id = self.start_session(client)
            try:
                with mock.patch.object(
                        http_app_module,
                        "PROVIDER_TIMEOUT_SECONDS", 0.25):
                    timed_out = client.post(
                        "/api/session/source-analysis",
                        json=operation_command(source_id),
                        headers=headers)
                self.assertEqual(
                    timed_out.status_code, 504, timed_out.text)
                self.assertEqual(
                    timed_out.json()["detail"]["code"], "timeout")
                self.assertEqual(
                    timed_out.json()["raw_retention"], "not_available")
                self.assertTrue(inner.entered.wait(timeout=5))
                self.assertFalse(returned.is_set())
                snapshot = self.snapshot(client)
                self.assertEqual(snapshot["regions"], [])
                self.assertEqual(
                    snapshot["requests"][0]["state"], "terminal")
                self.assertEqual(
                    snapshot["requests"][0]["outcome"], "timeout")
            finally:
                inner.release.set()
            self.assertTrue(
                returned.wait(timeout=5), "provider did not return")
            inner.source = "valid"
            deadline = time.monotonic() + 5
            completed = None
            while time.monotonic() < deadline:
                completed = client.post(
                    "/api/session/source-analysis",
                    json=operation_command(source_id), headers=headers)
                if completed.status_code == 201:
                    break
                self.assertEqual(
                    completed.status_code, 429, completed.text)
                time.sleep(0.05)
            self.assertIsNotNone(completed)
            self.assertEqual(completed.status_code, 201, completed.text)
            snapshot = self.snapshot(client)
            outcomes = [item["outcome"] for item in snapshot["requests"]]
            self.assertEqual(outcomes, ["timeout", "ok"])
            self.assertEqual(len(snapshot["regions"]), 1)


if __name__ == "__main__":
    unittest.main()
