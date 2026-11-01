"""Retained-publication tests for teacher HTTP.

Real app/domain/decoder through TestClient with a synthetic
in-process provider only (explicitly not Meta). Tests may observe
private session closure state to choose fixed logical-byte caps and
to assert accounted totals; they never change decision logic.
"""
import contextlib
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
from teacher_demo.adapter import (  # noqa: E402
    RequestView,
    WorkspaceSnapshot,
)
from teacher_demo.http_app import create_app  # noqa: E402
from teacher_demo.http_teacher_publication import (  # noqa: E402
    TERMINAL_SLACK_BYTES,
    accounted_total,
    retain_terminal_reserves,
)

BASE_URL = "http://127.0.0.1:8000"
GRADE = "  الصَّفُّ ٥  "
OBJECTIVE = "  هَدَفٌ: قِرَاءَةُ النَّصِّ ١٢٣  "
RAW_TEXT = "  اَلْمَاءُ يَجْرِي فِي النَّهْرِ ١٢٣  "
FACT = "اَلْمَاءُ يَجْرِي بِسُرْعَةٍ"
EDITED_TEXT = f"  {FACT} فِي النَّهْرِ ١٢٣  "
OLDER_TEXT = "  نَصُّ الطَّلَبِ الْأَقْدَمِ ٧٨٩  "
NEWER_TEXT = "  نَصُّ الطَّلَبِ الْأَحْدَثِ ٧٨٩  "
MODEL_NAME = "synthetic-test-provider (not Meta)"


def operation_command(source_id):
    return {"request_id": secrets.token_hex(16),
            "source_id": source_id}


def synthetic_png(rgb=(200, 30, 40)):
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (*rgb, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def source_result(text=RAW_TEXT, response_id="synthetic-source-1"):
    return {
        "raw": {
            "sections": [{
                "section_id": "synthetic-section-1",
                "kind": "body", "text": text,
                "review_reasons": ["synthetic-flag"]}],
            "page_continues": False, "review_notes": [],
        },
        "model_name": MODEL_NAME,
        "response_id": response_id,
        "outcome": "ok",
    }


class PublicationProvider:
    """Synthetic provider with per-call source/question gates."""

    def __init__(self, *, source_mode="valid", question_mode="valid",
                 block_source=(), block_questions=()):
        self.source_mode = source_mode
        self.question_mode = question_mode
        self.block_source = set(block_source)
        self.block_questions = set(block_questions)
        self.requests = []
        self.source_results = {}
        self.question_results = {}
        self.on_source_result = None
        self._lock = threading.Lock()
        self._source_calls = 0
        self._question_calls = 0
        self.source_entered = [threading.Event() for _ in range(8)]
        self.source_release = [threading.Event() for _ in range(8)]
        self.question_entered = [threading.Event() for _ in range(8)]
        self.question_release = [threading.Event() for _ in range(8)]

    def release_all(self):
        for event in self.source_release + self.question_release:
            event.set()

    def __call__(self, request):
        with self._lock:
            self.requests.append(copy.deepcopy(dict(request)))
        if request["kind"] == "source_analysis":
            return self._source(request)
        return self._questions(request)

    def _source(self, request):
        with self._lock:
            index = self._source_calls
            self._source_calls += 1
        if index < len(self.source_entered):
            self.source_entered[index].set()
        if index in self.block_source:
            self.source_release[index].wait(timeout=10)
        response_id = f"synthetic-source-{index + 1}"
        if self.source_mode == "terminal_error":
            result = {
                "raw": {
                    "note": "synthetic terminal error",
                    "index": index,
                    "padding": "e" * 4000},
                "model_name": MODEL_NAME,
                "response_id": f"synthetic-source-error-{index + 1}",
                "outcome": "error"}
        elif self.source_mode == "large":
            result = source_result(
                text="  " + "x" * 30000 + "  ",
                response_id=response_id)
        elif self.source_mode == "two_texts":
            result = source_result(
                text=OLDER_TEXT if index == 0 else NEWER_TEXT,
                response_id=response_id)
        elif self.source_mode == "detach_invalid":
            result = source_result(response_id=response_id)
            result["raw"]["sections"][0]["text"] = object()
        elif self.source_mode == "envelope_invalid":
            result = source_result(response_id=response_id)
            result.pop("response_id")
        elif self.source_mode == "proposal_invalid":
            result = source_result(response_id=response_id)
            result["raw"]["sections"][0].pop("text")
        else:
            result = source_result(response_id=response_id)
        with self._lock:
            self.source_results[request["request_id"]] = (
                copy.deepcopy(result))
        if self.on_source_result is not None:
            self.on_source_result()
        return copy.deepcopy(result)

    def _questions(self, request):
        with self._lock:
            index = self._question_calls
            self._question_calls += 1
        if index < len(self.question_entered):
            self.question_entered[index].set()
        if index in self.block_questions:
            self.question_release[index].wait(timeout=10)
        if self.question_mode == "error":
            result = {
                "raw": {"note": "synthetic question terminal error"},
                "model_name": MODEL_NAME,
                "response_id": "synthetic-question-error-1",
                "outcome": "error"}
            with self._lock:
                self.question_results[request["request_id"]] = (
                    copy.deepcopy(result))
            return copy.deepcopy(result)
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
        result = {"raw": {"items": items}, "model_name": MODEL_NAME,
                  "response_id": f"synthetic-question-{index + 1}",
                  "outcome": "ok"}
        with self._lock:
            self.question_results[request["request_id"]] = (
                copy.deepcopy(result))
        return copy.deepcopy(result)


def closure_values(app):
    """Test-only observation of create_app closure authorities."""
    values = {}
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        code = getattr(endpoint, "__code__", None)
        closure = getattr(endpoint, "__closure__", None)
        if code is None or closure is None:
            continue
        for name, cell in zip(code.co_freevars, closure):
            try:
                values.setdefault(name, cell.cell_contents)
            except ValueError:
                continue
    return values


class TeacherPublicationTests(unittest.TestCase):
    def state_for(self, app, headers):
        sessions = closure_values(app)["sessions"]
        csrf = headers["X-CSRF-Token"]
        return next(state for state in sessions.values()
                    if state.csrf_token == csrf)

    def inflight(self, app):
        return closure_values(app)["inflight"]

    def registry_lock(self, app):
        return closure_values(app)["registry_lock"]

    def accounted(self, app, state):
        with self.registry_lock(app):
            graph_cost = state.graph_cost
            worker = dict(state.worker_reservations)
            reserves = dict(state.terminal_reserves)
        return accounted_total(graph_cost, worker, reserves)

    def worker_drained(self, app, state, op_key):
        inflight = self.inflight(app)
        with self.registry_lock(app):
            return (op_key not in state.worker_reservations
                    and inflight["provider"] == 0)

    def wait_until(self, predicate, message, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail(message)

    def wait_worker_drained(self, app, state, op_key):
        self.wait_until(
            lambda: self.worker_drained(app, state, op_key),
            "provider worker did not drain after actual return")

    def session_with_settings(self, client):
        created = client.post(
            "/api/sessions", json={}, headers={"Origin": BASE_URL})
        self.assertEqual(created.status_code, 201, created.text)
        cookie = created.headers["set-cookie"].split(";", 1)[0]
        headers = {
            "Origin": BASE_URL,
            "X-CSRF-Token": created.json()["csrf_token"],
            "Cookie": cookie}
        settings = client.put(
            "/api/session/settings",
            json={"grade": GRADE, "objective": OBJECTIVE,
                  "rights_ref": "synthetic"}, headers=headers)
        self.assertEqual(settings.status_code, 200, settings.text)
        return headers

    def start_session(self, client, image=None):
        headers = self.session_with_settings(client)
        started = client.post(
            "/api/session/start", params={"page_index": 0},
            content=synthetic_png() if image is None else image,
            headers={**headers,
                     "Content-Type": "application/octet-stream"})
        self.assertEqual(started.status_code, 201, started.text)
        return headers, started.json()

    def snapshot(self, client):
        response = client.get("/api/session/snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def request_from(self, snapshot, request_id):
        return next(item for item in snapshot["requests"]
                    if item["request_id"] == request_id)

    def raw_result(self, client, request_id):
        return client.get(
            "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")

    def post_async(self, app, path, headers, *, body=None,
                   content=None):
        result = {}
        payload = {} if body is None else body

        def run():
            try:
                with TestClient(app, base_url=BASE_URL) as client:
                    if content is None:
                        result["response"] = client.post(
                            path, json=payload, headers=headers)
                    else:
                        result["response"] = client.post(
                            path, params={"page_index": 0},
                            content=content, headers=headers)
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
        return analyzed.json()["request_id"], region

    def test_retain_terminal_reserves_pure_contract(self):
        snapshot = WorkspaceSnapshot(requests=(
            RequestView(
                request_id="working", kind="source", state="working"),
            RequestView(
                request_id="closed-unpublished", kind="source",
                state="terminal", outcome="cancelled"),
            RequestView(
                request_id="closed-published", kind="source",
                state="terminal", outcome="ok"),
            RequestView(
                request_id="superseded-unpublished", kind="questions",
                state="superseded", outcome="superseded")))
        reserves = {
            "working": TERMINAL_SLACK_BYTES + 321,
            "closed-unpublished": TERMINAL_SLACK_BYTES + 321,
            "closed-published": TERMINAL_SLACK_BYTES + 321,
            "superseded-unpublished": 99,
            "missing": TERMINAL_SLACK_BYTES}
        original = dict(reserves)
        retained = retain_terminal_reserves(
            reserves, snapshot, {"closed-published": "retained"})
        self.assertEqual(retained, {
            "working": TERMINAL_SLACK_BYTES + 321,
            "closed-unpublished": TERMINAL_SLACK_BYTES,
            "superseded-unpublished": 99})
        self.assertEqual(reserves, original)
        self.assertIsNot(retained, reserves)
        self.assertEqual(
            accounted_total(100, {"worker": 23}, retained),
            100 + 23 + TERMINAL_SLACK_BYTES + 321
            + TERMINAL_SLACK_BYTES + 99)
        self.assertEqual(
            retain_terminal_reserves(reserves, None, {}), {})

    def test_healthy_source_request_is_invoked_and_published(self):
        provider = PublicationProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            before = self.accounted(app, state)
            generation = state.source_generation
            command = operation_command(page["source_id"])
            request_id = command["request_id"]
            op_key = (generation, request_id)
            analyzed = client.post(
                "/api/session/source-analysis", json=command,
                headers=headers)
            self.assertEqual(analyzed.status_code, 201, analyzed.text)
            self.assertEqual(analyzed.json()["request_id"], request_id)
            self.assertEqual(len(provider.requests), 1)
            request = provider.requests[0]
            self.assertEqual(request["kind"], "source_analysis")
            self.assertEqual(request["request_id"], request_id)
            self.assertEqual(request["source_id"], page["source_id"])
            self.assertEqual(
                request["source_sha256"], page["source_sha256"])
            self.assertEqual(request["media_type"], "image/png")
            self.assertEqual(
                provider.source_results[request_id]["model_name"],
                MODEL_NAME)
            self.assertIn("(not Meta)", MODEL_NAME)
            snapshot = analyzed.json()["snapshot"]
            self.assertEqual(snapshot["regions"][0]["raw_text"], RAW_TEXT)
            self.assertEqual(
                self.request_from(snapshot, request_id)["outcome"], "ok")
            self.assertEqual(
                self.raw_result(client, request_id)
                .json()["provider_result"],
                provider.source_results[request_id])
            self.wait_worker_drained(app, state, op_key)
            self.assertEqual(dict(state.terminal_reserves), {})
            self.assertGreater(self.accounted(app, state), before)

    def test_per_session_capacity_refusal_before_invocation(self):
        provider = PublicationProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            before = self.snapshot(client)
            accounted_before = self.accounted(app, state)
            cap = accounted_before + 1
            self.assertLess(accounted_before, cap)
            with mock.patch.object(
                    http_app_module,
                    "MAX_RETAINED_BYTES_PER_SESSION", cap):
                refused = client.post(
                    "/api/session/source-analysis",
                    json=operation_command(page["source_id"]),
                    headers=headers)
            self.assertEqual(refused.status_code, 413, refused.text)
            self.assertEqual(provider.requests, [])
            self.assertEqual(self.snapshot(client), before)
            self.assertEqual(
                self.accounted(app, state), accounted_before)
            self.assertEqual(dict(state.provider_results), {})
            self.assertEqual(dict(state.raw_status), {})

    def test_global_capacity_refusal_before_invocation(self):
        provider = PublicationProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client_a, \
                TestClient(app, base_url=BASE_URL) as client_b:
            headers_a, page_a = self.start_session(client_a)
            headers_b, _ = self.start_session(
                client_b, image=synthetic_png((20, 80, 160)))
            state_a = self.state_for(app, headers_a)
            state_b = self.state_for(app, headers_b)
            before_a = self.snapshot(client_a)
            before_b = self.snapshot(client_b)
            total_before = (self.accounted(app, state_a)
                            + self.accounted(app, state_b))
            cap = total_before + 1
            self.assertLess(total_before, cap)
            with mock.patch.object(
                    http_app_module, "MAX_TOTAL_RETAINED_BYTES", cap):
                refused = client_a.post(
                    "/api/session/source-analysis",
                    json=operation_command(page_a["source_id"]),
                    headers=headers_a)
            self.assertEqual(refused.status_code, 413, refused.text)
            self.assertEqual(provider.requests, [])
            self.assertEqual(self.snapshot(client_a), before_a)
            self.assertEqual(self.snapshot(client_b), before_b)
            self.assertEqual(
                self.accounted(app, state_a)
                + self.accounted(app, state_b),
                total_before)

    def test_repeated_terminal_errors_accumulate_until_refusal(self):
        provider = PublicationProvider(source_mode="terminal_error")
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            cap = self.accounted(app, state) + 32000
            settled_accounted = []
            refused = None
            before_refusal = None
            with mock.patch.object(
                    http_app_module,
                    "MAX_RETAINED_BYTES_PER_SESSION", cap):
                for _attempt in range(12):
                    before = self.snapshot(client)
                    response = client.post(
                        "/api/session/source-analysis",
                        json=operation_command(page["source_id"]),
                        headers=headers)
                    if response.status_code == 413:
                        refused = response
                        before_refusal = before
                        break
                    self.assertEqual(
                        response.status_code, 502, response.text)
                    self.assertEqual(
                        response.json()["detail"]["code"], "error")
                    snapshot = self.snapshot(client)
                    settled_accounted.append(
                        self.accounted(app, state))
                else:
                    self.fail("repeated terminal history never refused")
            self.assertIsNotNone(refused)
            self.assertGreaterEqual(len(settled_accounted), 2)
            self.assertEqual(
                settled_accounted, sorted(settled_accounted))
            self.assertLess(settled_accounted[0], settled_accounted[-1])
            self.assertEqual(self.snapshot(client), before_refusal)
            snapshot = self.snapshot(client)
            self.assertEqual(
                len(snapshot["requests"]), len(settled_accounted))
            self.assertTrue(all(
                item["state"] == "terminal"
                and item["outcome"] == "error"
                for item in snapshot["requests"]))
            self.assertEqual(snapshot["regions"], [])
            self.assertEqual(
                len(provider.requests), len(settled_accounted))
            self.assertEqual(
                len(state.raw_status), len(settled_accounted))
            first_id = snapshot["requests"][0]["request_id"]
            if state.raw_status[first_id] == "retained":
                self.assertEqual(
                    self.raw_result(client, first_id)
                    .json()["provider_result"],
                    provider.source_results[first_id])

    def test_large_success_refused_at_final_per_session_publication(self):
        provider = PublicationProvider(
            source_mode="large", block_source={0})
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            cap = self.accounted(app, state) + 20000
            generation = state.source_generation
            command = operation_command(page["source_id"])
            request_id = command["request_id"]
            op_key = (generation, request_id)
            with mock.patch.object(
                    http_app_module,
                    "MAX_RETAINED_BYTES_PER_SESSION", cap):
                thread, result = self.post_async(
                    app, "/api/session/source-analysis", headers,
                    body=command)
                try:
                    self.assertTrue(
                        provider.source_entered[0].wait(timeout=5))
                    self.assertLess(self.accounted(app, state), cap)
                    self.request_from(
                        self.snapshot(client), request_id)
                finally:
                    provider.release_all()
                response = self.finish_async(thread, result)
                self.assertEqual(response.status_code, 413, response.text)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "retained_budget_exceeded")
                self.assertEqual(
                    response.json()["raw_retention"], "omitted_capacity")
                self.wait_worker_drained(app, state, op_key)
            snapshot = self.snapshot(client)
            self.assertEqual(snapshot["regions"], [])
            request = self.request_from(snapshot, request_id)
            self.assertEqual(request["state"], "terminal")
            self.assertEqual(request["outcome"], "error")
            self.assertEqual(
                self.raw_result(client, request_id).status_code, 404)
            self.assertLessEqual(self.accounted(app, state), cap)

    def test_large_success_refused_at_final_global_publication(self):
        provider = PublicationProvider(
            source_mode="large", block_source={0})
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client_a, \
                TestClient(app, base_url=BASE_URL) as client_b:
            headers_a, page_a = self.start_session(client_a)
            headers_b, _ = self.start_session(
                client_b, image=synthetic_png((20, 80, 160)))
            state_a = self.state_for(app, headers_a)
            state_b = self.state_for(app, headers_b)
            before_b = self.snapshot(client_b)
            cap = (self.accounted(app, state_a)
                   + self.accounted(app, state_b) + 20000)
            generation_a = state_a.source_generation
            command = operation_command(page_a["source_id"])
            request_id = command["request_id"]
            op_key = (generation_a, request_id)
            with mock.patch.object(
                    http_app_module, "MAX_TOTAL_RETAINED_BYTES", cap):
                thread, result = self.post_async(
                    app, "/api/session/source-analysis", headers_a,
                    body=command)
                try:
                    self.assertTrue(
                        provider.source_entered[0].wait(timeout=5))
                    self.assertLess(
                        self.accounted(app, state_a)
                        + self.accounted(app, state_b),
                        cap)
                    self.request_from(
                        self.snapshot(client_a), request_id)
                finally:
                    provider.release_all()
                response = self.finish_async(thread, result)
                self.assertEqual(response.status_code, 413, response.text)
                self.assertEqual(
                    response.json()["detail"]["code"],
                    "retained_budget_exceeded")
                self.wait_worker_drained(app, state_a, op_key)
            self.assertEqual(self.snapshot(client_b), before_b)
            snapshot_a = self.snapshot(client_a)
            self.assertEqual(snapshot_a["regions"], [])
            self.assertEqual(
                self.request_from(snapshot_a, request_id)["outcome"],
                "error")
            self.assertLessEqual(
                self.accounted(app, state_a)
                + self.accounted(app, state_b), cap)

    def test_protected_terminal_after_cross_session_fill_and_edit(self):
        provider = PublicationProvider(
            question_mode="error", block_questions={0})
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client_a, \
                TestClient(app, base_url=BASE_URL) as client_b:
            headers_a, page_a = self.start_session(client_a)
            headers_b, _ = self.start_session(
                client_b, image=synthetic_png((20, 80, 160)))
            _source_request_id, region = self.analyze_and_include(
                client_a, headers_a, page_a["source_id"])
            state_a = self.state_for(app, headers_a)
            state_b = self.state_for(app, headers_b)
            before_b = self.snapshot(client_b)
            cap = (self.accounted(app, state_a)
                   + self.accounted(app, state_b) + 20000)
            generation_a = state_a.source_generation
            command = operation_command(page_a["source_id"])
            request_id = command["request_id"]
            op_key = (generation_a, request_id)
            with mock.patch.object(
                    http_app_module, "MAX_TOTAL_RETAINED_BYTES", cap):
                thread, result = self.post_async(
                    app, "/api/session/questions/generate", headers_a,
                    body=command)
                try:
                    self.assertTrue(
                        provider.question_entered[0].wait(timeout=5))
                    during = self.snapshot(client_a)
                    working = [item for item in during["requests"]
                               if item["state"] == "working"]
                    self.assertEqual(len(working), 1)
                    self.assertEqual(working[0]["request_id"], request_id)
                    self.assertGreater(
                        state_a.terminal_reserves[request_id], 0)
                    self.assertLess(
                        self.accounted(app, state_a)
                        + self.accounted(app, state_b),
                        cap)
                    large_edit = client_a.put(
                        "/api/session/regions/"
                        + quote(region["region_id"], safe=""),
                        json={"inclusion": "included",
                              "current_text": "x" * 12000,
                              "current_label": region["current_label"]},
                        headers=headers_a)
                    self.assertEqual(
                        large_edit.status_code, 413, large_edit.text)
                    self.assertEqual(self.snapshot(client_a), during)
                finally:
                    provider.release_all()
                response = self.finish_async(thread, result)
                self.assertEqual(response.status_code, 502, response.text)
                self.assertEqual(
                    response.json()["detail"]["code"], "error")
                self.assertEqual(
                    response.json()["raw_retention"], "retained")
                self.wait_worker_drained(app, state_a, op_key)
            snapshot = self.snapshot(client_a)
            self.assertEqual(
                snapshot["regions"][0]["current_text"], EDITED_TEXT)
            self.assertEqual(snapshot["questions"], [])
            request = self.request_from(snapshot, request_id)
            self.assertEqual(request["state"], "terminal")
            self.assertEqual(request["outcome"], "error")
            self.assertEqual(
                self.raw_result(client_a, request_id)
                .json()["provider_result"],
                provider.question_results[request_id])
            self.assertEqual(self.snapshot(client_b), before_b)
            self.assertNotIn(request_id, state_a.terminal_reserves)
            self.assertLessEqual(
                self.accounted(app, state_a)
                + self.accounted(app, state_b), cap)

    def test_cancelled_late_large_result_omits_raw_without_overdraw(self):
        provider = PublicationProvider(
            source_mode="large", block_source={0})
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            cap = self.accounted(app, state) + 20000
            generation = state.source_generation
            command = operation_command(page["source_id"])
            request_id = command["request_id"]
            op_key = (generation, request_id)
            with mock.patch.object(
                    http_app_module,
                    "MAX_RETAINED_BYTES_PER_SESSION", cap):
                thread, result = self.post_async(
                    app, "/api/session/source-analysis", headers,
                    body=command)
                try:
                    self.assertTrue(
                        provider.source_entered[0].wait(timeout=5))
                    self.request_from(
                        self.snapshot(client), request_id)
                    cancelled = client.post(
                        "/api/session/requests/"
                        + quote(request_id, safe="") + "/cancel",
                        json={"source_id": page["source_id"]},
                        headers=headers)
                    self.assertEqual(
                        cancelled.status_code, 200, cancelled.text)
                    self.assertEqual(
                        state.terminal_reserves[request_id],
                        TERMINAL_SLACK_BYTES)
                    self.assertNotIn(request_id, state.raw_status)
                finally:
                    provider.release_all()
                late = self.finish_async(thread, result)
                self.assertEqual(late.status_code, 409, late.text)
                self.assertEqual(
                    late.json()["detail"]["code"], "request_closed")
                self.assertEqual(
                    late.json()["raw_retention"], "omitted_capacity")
                self.wait_worker_drained(app, state, op_key)
            snapshot = self.snapshot(client)
            self.assertEqual(snapshot["regions"], [])
            self.assertEqual(
                self.request_from(snapshot, request_id)["outcome"],
                "cancelled")
            self.assertEqual(
                self.raw_result(client, request_id).status_code, 404)
            self.assertEqual(
                state.raw_status[request_id], "omitted_capacity")
            self.assertNotIn(request_id, state.terminal_reserves)
            self.assertLessEqual(self.accounted(app, state), cap)

    def test_superseded_late_result_does_not_overwrite_newer(self):
        provider = PublicationProvider(
            source_mode="two_texts", block_source={0, 1})
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            first_command = operation_command(page["source_id"])
            older_id = first_command["request_id"]
            second_command = operation_command(page["source_id"])
            newer_id = second_command["request_id"]
            first_thread, first_result = self.post_async(
                app, "/api/session/source-analysis", headers,
                body=first_command)
            second_thread = None
            second_result = None
            try:
                self.assertTrue(
                    provider.source_entered[0].wait(timeout=5))
                self.request_from(self.snapshot(client), older_id)
                second_thread, second_result = self.post_async(
                    app, "/api/session/source-analysis", headers,
                    body=second_command)
                self.assertTrue(
                    provider.source_entered[1].wait(timeout=5))
                snapshot = self.snapshot(client)
                self.assertEqual(len(snapshot["requests"]), 2)
                older = self.request_from(snapshot, older_id)
                self.assertEqual(older["state"], "superseded")
                self.assertEqual(older["outcome"], "superseded")
                self.request_from(snapshot, newer_id)
                provider.source_release[1].set()
                newer_response = self.finish_async(
                    second_thread, second_result)
                self.assertEqual(
                    newer_response.status_code, 201, newer_response.text)
                self.assertEqual(
                    newer_response.json()["request_id"], newer_id)
                after_newer = self.snapshot(client)
                self.assertEqual(len(after_newer["regions"]), 1)
                self.assertEqual(
                    after_newer["regions"][0]["raw_text"], NEWER_TEXT)
            finally:
                provider.release_all()
            older_response = self.finish_async(
                first_thread, first_result)
            self.assertEqual(older_response.status_code, 409,
                             older_response.text)
            self.assertEqual(
                older_response.json()["detail"]["code"],
                "request_closed")
            self.wait_until(
                lambda: self.inflight(app)["provider"] == 0,
                "superseded workers did not drain")
            self.assertEqual(dict(state.worker_reservations), {})
            snapshot = self.snapshot(client)
            self.assertEqual(len(snapshot["regions"]), 1)
            self.assertEqual(
                snapshot["regions"][0]["request_id"], newer_id)
            self.assertEqual(
                snapshot["regions"][0]["raw_text"], NEWER_TEXT)
            self.assertEqual(
                self.request_from(snapshot, older_id)["outcome"],
                "superseded")
            self.assertEqual(
                self.request_from(snapshot, newer_id)["outcome"], "ok")
            self.assertEqual(
                self.raw_result(client, newer_id)
                .json()["provider_result"],
                provider.source_results[newer_id])
            if older_response.json()["raw_retention"] == "retained":
                self.assertEqual(
                    self.raw_result(client, older_id)
                    .json()["provider_result"],
                    provider.source_results[older_id])

    def test_newer_success_preserves_older_closed_pending_room(self):
        provider = PublicationProvider(
            source_mode="two_texts", block_source={0})
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            generation = state.source_generation
            older_command = operation_command(page["source_id"])
            older_id = older_command["request_id"]
            older_key = (generation, older_id)
            thread, result = self.post_async(
                app, "/api/session/source-analysis", headers,
                body=older_command)
            try:
                self.assertTrue(
                    provider.source_entered[0].wait(timeout=5))
                self.request_from(self.snapshot(client), older_id)
                cancelled = client.post(
                    "/api/session/requests/"
                    + quote(older_id, safe="") + "/cancel",
                    json={"source_id": page["source_id"]},
                    headers=headers)
                self.assertEqual(cancelled.status_code, 200,
                                 cancelled.text)
                self.assertEqual(
                    state.terminal_reserves[older_id],
                    TERMINAL_SLACK_BYTES)
                newer_command = operation_command(page["source_id"])
                newer_id = newer_command["request_id"]
                newer_key = (generation, newer_id)
                newer = client.post(
                    "/api/session/source-analysis", json=newer_command,
                    headers=headers)
                self.assertEqual(newer.status_code, 201, newer.text)
                self.assertEqual(newer.json()["request_id"], newer_id)
                snapshot = newer.json()["snapshot"]
                self.assertEqual(len(snapshot["regions"]), 1)
                self.assertEqual(
                    snapshot["regions"][0]["raw_text"], NEWER_TEXT)
                self.wait_until(
                    lambda: newer_key not in state.worker_reservations,
                    "newer callable reservation did not drain")
                # This is the migration discriminator: the newer
                # success retired only its own reserve and left the
                # older cancelled request's unpublished-status room.
                self.assertEqual(
                    dict(state.terminal_reserves),
                    {older_id: TERMINAL_SLACK_BYTES})
                self.assertNotIn(older_id, state.raw_status)
                self.assertEqual(
                    state.raw_status[newer_id], "retained")
                cap = self.accounted(app, state)
                with mock.patch.object(
                        http_app_module,
                        "MAX_RETAINED_BYTES_PER_SESSION", cap):
                    provider.release_all()
                    late = self.finish_async(thread, result)
                    self.assertEqual(late.status_code, 409, late.text)
                    self.assertEqual(
                        late.json()["detail"]["code"], "request_closed")
                    self.assertEqual(
                        late.json()["raw_retention"], "retained")
                    self.wait_worker_drained(app, state, older_key)
            finally:
                provider.release_all()
            snapshot = self.snapshot(client)
            self.assertEqual(len(snapshot["regions"]), 1)
            self.assertEqual(
                snapshot["regions"][0]["request_id"], newer_id)
            self.assertEqual(
                snapshot["regions"][0]["raw_text"], NEWER_TEXT)
            self.assertEqual(
                self.request_from(snapshot, older_id)["outcome"],
                "cancelled")
            self.assertEqual(
                self.raw_result(client, older_id)
                .json()["provider_result"],
                provider.source_results[older_id])
            self.assertEqual(
                self.raw_result(client, newer_id)
                .json()["provider_result"],
                provider.source_results[newer_id])
            self.assertEqual(dict(state.terminal_reserves), {})
            self.assertEqual(state.raw_status[older_id], "retained")
            self.assertLessEqual(self.accounted(app, state), cap)

    def test_atomic_processing_stages_publish_no_content(self):
        stages = (
            ("detach", "detach_invalid", 502, "invalid_schema",
             "invalid_schema", "not_available"),
            ("envelope", "envelope_invalid", 502, "invalid_schema",
             "invalid_schema", "retained"),
            ("admission", "proposal_invalid", 502, "invalid_schema",
             "invalid_schema", "retained"),
            ("rendering", "valid", 502, "provider_error",
             "error", "retained"))
        for stage, mode, status, code, outcome, retention in stages:
            with self.subTest(stage=stage):
                provider = PublicationProvider(source_mode=mode)
                app = create_app(provider=provider)
                real_json_response = http_app_module.JSONResponse
                render_state = {"armed": False, "fired": False}

                def arm_render_failure():
                    render_state["armed"] = True

                def flaky_json_response(*args, **kwargs):
                    if render_state["armed"] and not render_state["fired"]:
                        render_state["fired"] = True
                        raise RuntimeError(
                            "synthetic one-shot response render failure")
                    return real_json_response(*args, **kwargs)

                if stage == "rendering":
                    provider.on_source_result = arm_render_failure
                with TestClient(app, base_url=BASE_URL) as client:
                    headers, page = self.start_session(client)
                    state = self.state_for(app, headers)
                    before = self.snapshot(client)
                    generation = state.source_generation
                    command = operation_command(page["source_id"])
                    request_id = command["request_id"]
                    op_key = (generation, request_id)
                    with contextlib.ExitStack() as stack:
                        if stage == "rendering":
                            stack.enter_context(mock.patch.object(
                                http_app_module, "JSONResponse",
                                flaky_json_response))
                        response = client.post(
                            "/api/session/source-analysis", json=command,
                            headers=headers)
                    self.assertEqual(
                        response.status_code, status, response.text)
                    self.assertEqual(
                        response.json()["detail"]["code"], code)
                    self.assertEqual(
                        response.json()["raw_retention"], retention)
                    if stage == "rendering":
                        self.assertTrue(render_state["fired"])
                    self.assertEqual(
                        response.json()["request_id"], request_id)
                    snapshot = self.snapshot(client)
                    self.assertEqual(snapshot["regions"], [])
                    self.assertEqual(snapshot["questions"], [])
                    self.assertEqual(
                        snapshot["source_id"], before["source_id"])
                    self.assertEqual(
                        snapshot["grade"], before["grade"])
                    self.assertEqual(
                        snapshot["objective"], before["objective"])
                    request = self.request_from(snapshot, request_id)
                    self.assertEqual(request["state"], "terminal")
                    self.assertEqual(request["outcome"], outcome)
                    self.assertNotEqual(request["outcome"], "ok")
                    self.assertEqual(
                        state.raw_status[request_id], retention)
                    raw = self.raw_result(client, request_id)
                    if retention == "retained":
                        self.assertEqual(raw.status_code, 200, raw.text)
                        self.assertEqual(
                            raw.json()["provider_result"],
                            provider.source_results[request_id])
                    else:
                        self.assertEqual(raw.status_code, 404, raw.text)
                    self.wait_worker_drained(app, state, op_key)

    def test_cancel_does_not_release_callable_until_actual_return(self):
        provider = PublicationProvider(block_source={0})
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            generation = state.source_generation
            command = operation_command(page["source_id"])
            request_id = command["request_id"]
            op_key = (generation, request_id)
            thread, result = self.post_async(
                app, "/api/session/source-analysis", headers,
                body=command)
            try:
                self.assertTrue(
                    provider.source_entered[0].wait(timeout=5))
                self.request_from(self.snapshot(client), request_id)
                self.assertGreater(
                    state.worker_reservations[op_key], 0)
                self.assertEqual(self.inflight(app)["provider"], 1)
                cancelled = client.post(
                    "/api/session/requests/"
                    + quote(request_id, safe="") + "/cancel",
                    json={"source_id": page["source_id"]},
                    headers=headers)
                self.assertEqual(cancelled.status_code, 200,
                                 cancelled.text)
                self.assertIn(op_key, state.worker_reservations)
                self.assertGreater(
                    state.worker_reservations[op_key], 0)
                self.assertEqual(self.inflight(app)["provider"], 1)
                self.assertEqual(
                    state.terminal_reserves[request_id],
                    TERMINAL_SLACK_BYTES)
            finally:
                provider.release_all()
            late = self.finish_async(thread, result)
            self.assertEqual(late.status_code, 409, late.text)
            self.wait_worker_drained(app, state, op_key)
            snapshot = self.snapshot(client)
            self.assertEqual(
                self.request_from(snapshot, request_id)["outcome"],
                "cancelled")
            self.assertEqual(snapshot["regions"], [])

    def test_timeout_does_not_release_callable_until_actual_return(self):
        provider = PublicationProvider(block_source={0})
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            generation = state.source_generation
            command = operation_command(page["source_id"])
            request_id = command["request_id"]
            op_key = (generation, request_id)
            try:
                with mock.patch.object(
                        http_app_module,
                        "PROVIDER_TIMEOUT_SECONDS", 0.25):
                    timed_out = client.post(
                        "/api/session/source-analysis", json=command,
                        headers=headers)
                self.assertEqual(timed_out.status_code, 504,
                                 timed_out.text)
                self.assertEqual(
                    timed_out.json()["request_id"], request_id)
                self.assertTrue(
                    provider.source_entered[0].wait(timeout=5))
                self.assertIn(op_key, state.worker_reservations)
                self.assertGreater(
                    state.worker_reservations[op_key], 0)
                self.assertEqual(self.inflight(app)["provider"], 1)
                snapshot = self.snapshot(client)
                self.assertEqual(
                    self.request_from(snapshot, request_id)["outcome"],
                    "timeout")
            finally:
                provider.release_all()
            self.wait_worker_drained(app, state, op_key)
            snapshot = self.snapshot(client)
            self.assertEqual(snapshot["regions"], [])
            self.assertEqual(
                self.request_from(snapshot, request_id)["state"],
                "terminal")
            self.assertEqual(
                self.raw_result(client, request_id).status_code, 404)

    def test_new_start_does_not_release_callable_until_return(self):
        provider = PublicationProvider(block_source={0})
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            headers, page = self.start_session(client)
            state = self.state_for(app, headers)
            old_generation = state.source_generation
            command = operation_command(page["source_id"])
            request_id = command["request_id"]
            old_key = (old_generation, request_id)
            thread, result = self.post_async(
                app, "/api/session/source-analysis", headers,
                body=command)
            try:
                self.assertTrue(
                    provider.source_entered[0].wait(timeout=5))
                self.request_from(self.snapshot(client), request_id)
                restarted = client.post(
                    "/api/session/start", params={"page_index": 0},
                    content=synthetic_png((30, 90, 150)),
                    headers={**headers,
                             "Content-Type": "application/octet-stream"})
                self.assertEqual(restarted.status_code, 201,
                                 restarted.text)
                self.assertIn(old_key, state.worker_reservations)
                self.assertGreater(
                    state.worker_reservations[old_key], 0)
                self.assertEqual(self.inflight(app)["provider"], 1)
                fresh = self.snapshot(client)
                self.assertEqual(fresh["requests"], [])
                self.assertEqual(fresh["regions"], [])
            finally:
                provider.release_all()
            late = self.finish_async(thread, result)
            self.assertEqual(late.status_code, 409, late.text)
            self.assertEqual(
                late.json()["detail"]["code"], "source_changed")
            self.wait_worker_drained(app, state, old_key)
            self.assertEqual(self.snapshot(client)["requests"], [])

    def test_start_setup_noop_and_away_back_currency(self):
        real_decode = http_app_module.decode_page
        for mode in ("same_value", "away_and_back"):
            with self.subTest(mode=mode):
                entered = threading.Event()
                release = threading.Event()

                def delayed_decode(data, page_index, rights_ref=None):
                    decoded = real_decode(
                        data, page_index, rights_ref=rights_ref)
                    entered.set()
                    release.wait(timeout=10)
                    return decoded

                app = create_app(provider=PublicationProvider())
                with TestClient(app, base_url=BASE_URL) as client:
                    headers = self.session_with_settings(client)
                    state = self.state_for(app, headers)
                    start_headers = {
                        **headers,
                        "Content-Type": "application/octet-stream"}
                    with mock.patch.object(
                            http_app_module, "decode_page",
                            delayed_decode):
                        thread, result = self.post_async(
                            app, "/api/session/start", start_headers,
                            content=synthetic_png())
                        try:
                            self.assertTrue(entered.wait(timeout=5))
                            pending_before = state.pending
                            epoch_before = state.publication_epoch
                            if mode == "same_value":
                                repeated = client.put(
                                    "/api/session/settings",
                                    json={
                                        "grade": GRADE,
                                        "objective": OBJECTIVE,
                                        "rights_ref": "synthetic"},
                                    headers=headers)
                                self.assertEqual(
                                    repeated.status_code, 200,
                                    repeated.text)
                                self.assertIs(
                                    state.pending, pending_before)
                                self.assertEqual(
                                    state.publication_epoch, epoch_before)
                            else:
                                away = client.put(
                                    "/api/session/settings",
                                    json={
                                        "grade": "  الصَّفُّ ٦  ",
                                        "objective": OBJECTIVE,
                                        "rights_ref": "synthetic"},
                                    headers=headers)
                                self.assertEqual(
                                    away.status_code, 200, away.text)
                                back = client.put(
                                    "/api/session/settings",
                                    json={
                                        "grade": GRADE,
                                        "objective": OBJECTIVE,
                                        "rights_ref": "synthetic"},
                                    headers=headers)
                                self.assertEqual(
                                    back.status_code, 200, back.text)
                                self.assertEqual(
                                    state.pending, pending_before)
                                self.assertIsNot(
                                    state.pending, pending_before)
                                self.assertEqual(
                                    state.publication_epoch,
                                    epoch_before + 2)
                        finally:
                            release.set()
                        response = self.finish_async(thread, result)
                    if mode == "same_value":
                        self.assertEqual(
                            response.status_code, 201, response.text)
                        self.assertEqual(
                            self.snapshot(client)["grade"], GRADE)
                    else:
                        self.assertEqual(
                            response.status_code, 409, response.text)
                        self.assertEqual(
                            response.json()["detail"], "setup changed")
                        page = client.get("/api/session/page")
                        self.assertEqual(page.status_code, 404, page.text)


if __name__ == "__main__":
    unittest.main()
