"""Genuine-HTTP Meta tracer: prospective exact-operation commands.

Real FastAPI TestClient, existing session/settings/PNG Start, the
actual ``MetaProvider`` and ``meta_protocol.build_payload``, and the
genuine HTTP/domain/retained-state path. The only synthetic
behavioral boundary is ``meta_transport.run_payload``: a finite
test callback installed before app requests and restored in
``finally``. It records each full builder payload, credential,
timeouts and cancellation event, and returns a clearly labelled
synthetic four-field result. No sockets, listener, subprocess,
provider/model or network call happens in this file, and no clock,
validator, route, bridge or retained-cost logic is replaced.
Synthetic output is fixture data only, never Meta proof.

First prospective RED in each test: existing setup/Start succeed,
then ``POST /api/session/source-analysis`` with exactly
``{request_id, source_id}`` must return 201; the accepted app
rejects any non-``{}`` body there with 400.
"""
import base64
import copy
import hashlib
import io
import pathlib
import sys
import threading
import unittest
from urllib.parse import quote

_HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "src"))
sys.path.insert(0, str(_HERE.parent))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from teacher_demo import meta_protocol, meta_transport  # noqa: E402
from teacher_demo.http_app import SESSION_COOKIE, create_app  # noqa: E402
from teacher_demo.meta_provider import MetaProvider  # noqa: E402
from teacher_demo.question_protocol_v2 import (  # noqa: E402
    BLOOM_LEVELS,
    question_prompt_v2,
)
from test_http_teacher_flow import (  # noqa: E402
    BASE_URL,
    EDITED_TEXT,
    FACT,
    GRADE,
    GUIDANCE,
    OBJECTIVE,
    QUESTION_TEXT,
    RAW_TEXT,
    RIGHTS_REF,
    synthetic_png,
)

SYNTHETIC_CREDENTIAL = "synthetic-meta-test-credential-0001"
SYNTHETIC_MODEL_NAME = "synthetic-run-payload-double (not Meta)"
MAX_OUTPUT_TOKENS = 4096
PROVIDER_TIMEOUT_SECONDS_ARG = 30.0
SOURCE_FORMAT = "teacher_source_v1"
QUESTION_FORMAT = "teacher_question_v2"
ENTERED_WAIT_SECONDS = 10.0
RELEASE_BACKSTOP_SECONDS = 30.0
JOIN_WAIT_SECONDS = 15.0

SYNTHETIC_SOURCE_RESULT = {
    "raw": {
        "sections": [{
            "section_id": "synthetic-meta-section-1",
            "kind": "body",
            "text": RAW_TEXT,
            "review_reasons": ["synthetic-flag"],
        }],
        "page_continues": False,
        "review_notes": ["synthetic note (transport double)"],
    },
    "model_name": SYNTHETIC_MODEL_NAME,
    "response_id": "synthetic-meta-source-response-1",
    "outcome": "ok",
}


def requested_slots():
    return [{"slot_id": f"slot-{level}", "level": level}
            for level in BLOOM_LEVELS]


def synthetic_png_variant():
    buffer = io.BytesIO()
    with Image.new("RGBA", (4, 6), (30, 200, 40, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


class RecordingRunPayload:
    """Finite synthetic ``run_payload`` double; records, never sends."""

    def __init__(self):
        self.calls = []
        self._lock = threading.Lock()
        self.region_id = None
        self.question_result = None
        self.block_source = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.blocked_returned = threading.Event()

    def recorded(self):
        with self._lock:
            return list(self.calls)

    def __call__(self, payload, *, credential, timeout_seconds,
                 total_timeout_seconds, cancel_event):
        record = {
            "payload": copy.deepcopy(payload),
            "credential": credential,
            "timeout_seconds": timeout_seconds,
            "total_timeout_seconds": total_timeout_seconds,
            "cancel_event": cancel_event,
            "cancel_set_at_entry": cancel_event.is_set(),
        }
        with self._lock:
            self.calls.append(record)
        name = payload["text"]["format"]["name"]
        if self.block_source and name == SOURCE_FORMAT:
            self.entered.set()
            self.release.wait(timeout=RELEASE_BACKSTOP_SECONDS)
            self.blocked_returned.set()
            return copy.deepcopy(SYNTHETIC_SOURCE_RESULT)
        if name == SOURCE_FORMAT:
            return copy.deepcopy(SYNTHETIC_SOURCE_RESULT)
        if name == QUESTION_FORMAT:
            return self._question_result()
        raise AssertionError(f"unexpected payload format: {name}")

    def _question_result(self):
        if self.region_id is None:
            raise AssertionError("region_id not set by test")
        items = []
        for slot in requested_slots():
            if slot["level"] == "remember":
                items.append({
                    "slot_id": slot["slot_id"], "level": "remember",
                    "status": "draft", "question": QUESTION_TEXT,
                    "answer_guidance": GUIDANCE,
                    "cognitive_explanation": (
                        "Synthetic recall task for the stated grade."),
                    "hypothetical_assumptions": [],
                    "evaluate_criteria": [], "create_rubric": [],
                    "evidence": [
                        {"region_id": self.region_id, "quote": FACT}],
                    "required_order_relationships": [],
                    "abstain_reason": None,
                })
            else:
                items.append({
                    "slot_id": slot["slot_id"], "level": slot["level"],
                    "status": "abstain", "question": "",
                    "answer_guidance": "", "cognitive_explanation": "",
                    "hypothetical_assumptions": [],
                    "evaluate_criteria": [], "create_rubric": [],
                    "evidence": [], "required_order_relationships": [],
                    "abstain_reason": "synthetic abstention",
                })
        self.question_result = {
            "raw": {"items": items},
            "model_name": SYNTHETIC_MODEL_NAME,
            "response_id": "synthetic-meta-question-response-1",
            "outcome": "ok",
        }
        return copy.deepcopy(self.question_result)


class HttpMetaIntegrationTest(unittest.TestCase):
    def _provider(self):
        return MetaProvider(
            credential=SYNTHETIC_CREDENTIAL,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            timeout_seconds=PROVIDER_TIMEOUT_SECONDS_ARG)

    def _start(self, client, headers, png_bytes):
        started = client.post(
            "/api/session/start", params={"page_index": 0},
            content=png_bytes,
            headers={**headers,
                     "Content-Type": "application/octet-stream"})
        self.assertEqual(started.status_code, 201, started.text)
        page = started.json()
        original = client.get("/api/session/page/original")
        self.assertEqual(original.status_code, 200, original.text)
        self.assertEqual(original.content, png_bytes)
        self.assertEqual(
            hashlib.sha256(png_bytes).hexdigest(),
            page["source_sha256"])
        self.assertEqual(
            hashlib.sha256(original.content).hexdigest(),
            page["source_sha256"])
        preview = client.get("/api/session/page/preview.png")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(
            hashlib.sha256(preview.content).hexdigest(),
            page["render_sha256"])
        return page, preview.content

    def _establish(self, client, png_bytes):
        created = client.post(
            "/api/sessions", json={}, headers={"Origin": BASE_URL})
        self.assertEqual(created.status_code, 201, created.text)
        csrf = created.json()["csrf_token"]
        cookie = created.headers["set-cookie"].split(";")[0]
        self.assertTrue(cookie.startswith(f"{SESSION_COOKIE}="))
        headers = {"Origin": BASE_URL, "X-CSRF-Token": csrf}
        settings = client.put(
            "/api/session/settings",
            json={"grade": GRADE, "objective": OBJECTIVE,
                  "rights_ref": RIGHTS_REF}, headers=headers)
        self.assertEqual(settings.status_code, 200, settings.text)
        self.assertEqual(settings.json(), {
            "grade": GRADE, "objective": OBJECTIVE,
            "rights_ref": RIGHTS_REF})
        page, render_bytes = self._start(client, headers, png_bytes)
        return csrf, cookie, headers, page, render_bytes

    def _snapshot(self, client):
        fetched = client.get("/api/session/snapshot")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        return fetched.json()

    def _view(self, snapshot, request_id):
        matches = [item for item in snapshot["requests"]
                   if item["request_id"] == request_id]
        self.assertEqual(len(matches), 1, snapshot["requests"])
        return matches[0]

    def _assert_call(self, call, format_name):
        self.assertEqual(call["credential"], SYNTHETIC_CREDENTIAL)
        self.assertEqual(
            call["timeout_seconds"], PROVIDER_TIMEOUT_SECONDS_ARG)
        self.assertGreater(call["total_timeout_seconds"], 0)
        self.assertIsInstance(
            call["cancel_event"], threading.Event)
        self.assertFalse(call["cancel_set_at_entry"])
        payload = call["payload"]
        self.assertEqual(set(payload), {
            "model", "reasoning", "max_output_tokens", "store",
            "stream", "text", "input"})
        self.assertEqual(payload["model"], meta_protocol.MODEL)
        self.assertEqual(
            payload["reasoning"], {"effort": meta_protocol.EFFORT})
        self.assertEqual(payload["reasoning"]["effort"], "max")
        self.assertEqual(
            payload["max_output_tokens"], MAX_OUTPUT_TOKENS)
        self.assertIs(payload["store"], False)
        self.assertIs(payload["stream"], False)
        fmt = payload["text"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertEqual(fmt["name"], format_name)
        self.assertIs(fmt["strict"], True)
        self.assertIsInstance(fmt["schema"], dict)
        self.assertTrue(fmt["schema"])
        self.assertEqual(len(payload["input"]), 1)
        self.assertEqual(payload["input"][0]["type"], "message")
        self.assertEqual(payload["input"][0]["role"], "user")
        return payload["input"][0]["content"]

    def _assert_status(self, client, request_id, source_id, snapshot):
        response = client.get(
            "/api/session/requests/"
            f"{quote(request_id, safe='')}/status",
            params={"source_id": source_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn(
            "no-store", response.headers.get("cache-control", ""))
        body = response.json()
        self.assertEqual(set(body), {"request_id", "source_id", "request"})
        self.assertEqual(body["request_id"], request_id)
        self.assertEqual(body["source_id"], source_id)
        self.assertEqual(body["request"], self._view(snapshot, request_id))
        return body["request"]

    def _assert_raw(self, client, request_id, kind, expected):
        response = client.get(
            "/api/session/requests/"
            f"{quote(request_id, safe='')}/raw")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["request_id"], request_id)
        self.assertEqual(body["kind"], kind)
        self.assertEqual(body["raw_retention"], "retained")
        self.assertEqual(body["provider_result"], expected)
        return body

    def test_exact_action_meta_teacher_journey(self):
        transport = RecordingRunPayload()
        original_png = synthetic_png()
        original_run_payload = meta_transport.run_payload
        meta_transport.run_payload = transport
        try:
            with TestClient(create_app(provider=self._provider()),
                            base_url=BASE_URL) as client:
                _csrf, _cookie, headers, page, render_bytes = (
                    self._establish(client, original_png))
                source_id = page["source_id"]
                snapshot = self._snapshot(client)
                self.assertEqual(snapshot["source_id"], source_id)
                self.assertEqual(snapshot["grade"], GRADE)
                self.assertEqual(snapshot["objective"], OBJECTIVE)
                self.assertEqual(snapshot["requests"], [])
                self.assertEqual(snapshot["regions"], [])
                self.assertEqual(snapshot["preview_items"], [])
                self.assertEqual(transport.recorded(), [])

                source_action_id = "a1" * 16
                self.assertRegex(source_action_id, r"^[0-9a-f]{32}$")
                analyzed = client.post(
                    "/api/session/source-analysis",
                    json={"request_id": source_action_id,
                          "source_id": source_id},
                    headers=headers)
                # First repair point: accepted app answers 400 here.
                self.assertEqual(
                    analyzed.status_code, 201, analyzed.text)
                body = analyzed.json()
                self.assertEqual(
                    set(body), {"request_id", "snapshot", "raw_retention"})
                self.assertEqual(body["request_id"], source_action_id)
                self.assertEqual(body["raw_retention"], "retained")
                snapshot = body["snapshot"]
                self.assertEqual(snapshot["source_id"], source_id)
                self.assertEqual(self._snapshot(client), snapshot)

                calls = transport.recorded()
                self.assertEqual(len(calls), 1)
                content = self._assert_call(calls[0], SOURCE_FORMAT)
                self.assertEqual(content[0], {
                    "type": "input_text",
                    "text": meta_protocol.SOURCE_PROMPT})
                self.assertEqual(content[1]["type"], "input_image")
                prefix = "data:image/png;base64,"
                self.assertTrue(content[1]["image_url"].startswith(prefix))
                sent_png = base64.b64decode(
                    content[1]["image_url"][len(prefix):])
                self.assertEqual(sent_png, render_bytes)
                self.assertEqual(
                    hashlib.sha256(sent_png).hexdigest(),
                    page["render_sha256"])
                self.assertFalse(calls[0]["cancel_event"].is_set())

                self.assertTrue(snapshot["source_analysis_terminal"])
                self.assertFalse(snapshot["can_generate"])
                view = self._view(snapshot, source_action_id)
                self.assertEqual(view["kind"], "source")
                self.assertEqual(view["state"], "terminal")
                self.assertEqual(view["outcome"], "ok")
                self.assertEqual(len(snapshot["regions"]), 1)
                region = snapshot["regions"][0]
                self.assertEqual(region["request_id"], source_action_id)
                self.assertEqual(
                    region["external_section_id"],
                    "synthetic-meta-section-1")
                self.assertEqual(region["raw_text"], RAW_TEXT)
                self.assertEqual(region["current_text"], RAW_TEXT)
                self.assertEqual(region["text_author"], "model")
                self.assertEqual(region["inclusion"], "unreviewed")
                self.assertEqual(region["model_flags"], ["synthetic-flag"])
                self.assertEqual(
                    region["box_origin"],
                    "decoder_full_page_extent_not_model_geometry")
                self.assertEqual(region["page_link"], "full_page")
                self.assertEqual(
                    region["source_box"],
                    [0, 0, page["width"], page["height"]])
                self._assert_status(
                    client, source_action_id, source_id, snapshot)
                raw_before = self._assert_raw(
                    client, source_action_id, "source",
                    SYNTHETIC_SOURCE_RESULT)

                closed = client.post(
                    "/api/session/requests/"
                    f"{quote(source_action_id, safe='')}/cancel",
                    json={"source_id": source_id}, headers=headers)
                self.assertEqual(closed.status_code, 409, closed.text)
                self.assertEqual(self._snapshot(client), snapshot)
                self.assertEqual(
                    self._assert_raw(
                        client, source_action_id, "source",
                        SYNTHETIC_SOURCE_RESULT),
                    raw_before)
                self.assertEqual(len(transport.recorded()), 1)
                self.assertFalse(
                    transport.recorded()[0]["cancel_event"].is_set())

                duplicate = client.post(
                    "/api/session/source-analysis",
                    json={"request_id": source_action_id,
                          "source_id": source_id},
                    headers=headers)
                self.assertEqual(duplicate.status_code, 409, duplicate.text)
                self.assertEqual(self._snapshot(client), snapshot)
                self.assertEqual(len(transport.recorded()), 1)

                region_id = region["region_id"]
                region_url = (
                    f"/api/session/regions/{quote(region_id, safe='')}")
                reviewed = client.put(
                    region_url,
                    json={"inclusion": "included",
                          "current_text": EDITED_TEXT,
                          "current_label": region["current_label"]},
                    headers=headers)
                self.assertEqual(reviewed.status_code, 200, reviewed.text)
                snapshot = reviewed.json()["snapshot"]
                region = snapshot["regions"][0]
                self.assertEqual(region["raw_text"], RAW_TEXT)
                self.assertEqual(region["current_text"], EDITED_TEXT)
                self.assertEqual(region["text_author"], "teacher")
                self.assertEqual(region["inclusion"], "included")
                self.assertTrue(snapshot["can_generate"])
                self.assertEqual(snapshot["included_count"], 1)
                self.assertEqual(snapshot["questions"], [])
                self.assertEqual(snapshot["preview_items"], [])

                transport.region_id = region_id
                question_action_id = "b2" * 16
                self.assertRegex(question_action_id, r"^[0-9a-f]{32}$")
                generated = client.post(
                    "/api/session/questions/generate",
                    json={"request_id": question_action_id,
                          "source_id": source_id},
                    headers=headers)
                self.assertEqual(generated.status_code, 201, generated.text)
                body = generated.json()
                self.assertEqual(
                    set(body), {"request_id", "snapshot", "raw_retention"})
                self.assertEqual(body["request_id"], question_action_id)
                self.assertEqual(body["raw_retention"], "retained")
                snapshot = body["snapshot"]
                self.assertEqual(snapshot["source_id"], source_id)

                calls = transport.recorded()
                self.assertEqual(len(calls), 2)
                self.assertIsNot(
                    calls[1]["cancel_event"], calls[0]["cancel_event"])
                content = self._assert_call(calls[1], QUESTION_FORMAT)
                self.assertEqual(len(requested_slots()), 6)
                expected_prompt = question_prompt_v2(
                    {region_id: EDITED_TEXT}, requested_slots(),
                    grade=GRADE, objective=OBJECTIVE)
                self.assertEqual(content, [{
                    "type": "input_text", "text": expected_prompt}])

                outcomes = [
                    item for item in snapshot["slot_outcomes"]
                    if item["request_id"] == question_action_id]
                self.assertEqual(len(outcomes), 6)
                self.assertEqual(
                    sorted(item["outcome"] for item in outcomes),
                    ["abstain"] * 5 + ["draft"])
                draft = next(
                    item for item in outcomes
                    if item["outcome"] == "draft")
                self.assertEqual(draft["level"], "remember")
                self.assertTrue(all(
                    item["abstain_reason"] == "synthetic abstention"
                    for item in outcomes if item["outcome"] == "abstain"))
                self.assertEqual(len(snapshot["questions"]), 1)
                question = snapshot["questions"][0]
                question_id = question["question_id"]
                self.assertEqual(question_id, draft["question_id"])
                self.assertEqual(question["status"], "draft")
                self.assertEqual(question["blocks"], [])
                self.assertEqual(question["current_question"], QUESTION_TEXT)
                self.assertEqual(
                    question["original_question"], QUESTION_TEXT)
                self.assertEqual(question["anchors"], [[region_id, FACT]])
                self.assertEqual(snapshot["preview_items"], [])
                self._assert_status(
                    client, question_action_id, source_id, snapshot)
                self._assert_raw(
                    client, question_action_id, "questions",
                    transport.question_result)

                preview = client.get("/api/session/preview")
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertEqual(preview.json()["items"], [])
                approved = client.post(
                    "/api/session/questions/"
                    f"{quote(question_id, safe='')}/approve",
                    json={}, headers=headers)
                self.assertEqual(approved.status_code, 200, approved.text)
                snapshot = approved.json()["snapshot"]
                self.assertEqual(
                    snapshot["questions"][0]["status"], "approved")
                preview = client.get("/api/session/preview")
                self.assertEqual(preview.status_code, 200, preview.text)
                items = preview.json()["items"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["question_id"], question_id)
                self.assertEqual(items[0]["question"], QUESTION_TEXT)
                self.assertEqual(
                    items[0]["original_question"], QUESTION_TEXT)
                self.assertEqual(
                    items[0]["resolved_anchors"],
                    [[region_id, FACT, EDITED_TEXT]])
                self._assert_raw(
                    client, source_action_id, "source",
                    SYNTHETIC_SOURCE_RESULT)
                original = client.get("/api/session/page/original")
                self.assertEqual(original.content, original_png)

                new_page, _new_render = self._start(
                    client, headers, synthetic_png_variant())
                new_source_id = new_page["source_id"]
                self.assertNotEqual(new_source_id, source_id)
                fresh = self._snapshot(client)
                self.assertEqual(fresh["source_id"], new_source_id)
                self.assertEqual(fresh["requests"], [])
                self.assertEqual(fresh["regions"], [])
                self.assertEqual(fresh["questions"], [])
                self.assertEqual(fresh["preview_items"], [])
                self.assertEqual(len(transport.recorded()), 2)

                delayed_id = "c3" * 16
                delayed = client.post(
                    "/api/session/source-analysis",
                    json={"request_id": delayed_id,
                          "source_id": source_id},
                    headers=headers)
                self.assertEqual(delayed.status_code, 409, delayed.text)
                self.assertEqual(self._snapshot(client), fresh)
                self.assertEqual(len(transport.recorded()), 2)

                status = client.get(
                    "/api/session/requests/"
                    f"{quote(source_action_id, safe='')}/status",
                    params={"source_id": source_id})
                self.assertEqual(status.status_code, 409, status.text)
                cancel = client.post(
                    "/api/session/requests/"
                    f"{quote(source_action_id, safe='')}/cancel",
                    json={"source_id": source_id}, headers=headers)
                self.assertEqual(cancel.status_code, 409, cancel.text)
                self.assertEqual(self._snapshot(client), fresh)
                preview = client.get("/api/session/preview")
                self.assertEqual(preview.status_code, 200, preview.text)
                self.assertEqual(preview.json()["items"], [])
                self.assertEqual(len(transport.recorded()), 2)

                fresh_id = "d4" * 16
                invalid = [
                    ("source missing request_id",
                     "/api/session/source-analysis",
                     {"source_id": new_source_id}),
                    ("source missing source_id",
                     "/api/session/source-analysis",
                     {"request_id": fresh_id}),
                    ("source empty body",
                     "/api/session/source-analysis", {}),
                    ("source extra field",
                     "/api/session/source-analysis",
                     {"request_id": fresh_id,
                      "source_id": new_source_id, "extra": "x"}),
                    ("source short request_id",
                     "/api/session/source-analysis",
                     {"request_id": "abc", "source_id": new_source_id}),
                    ("source uppercase request_id",
                     "/api/session/source-analysis",
                     {"request_id": "A1" * 16,
                      "source_id": new_source_id}),
                    ("source nonhex request_id",
                     "/api/session/source-analysis",
                     {"request_id": "g1" * 16,
                      "source_id": new_source_id}),
                    ("questions empty body",
                     "/api/session/questions/generate", {}),
                    ("questions extra field",
                     "/api/session/questions/generate",
                     {"request_id": fresh_id,
                      "source_id": new_source_id, "extra": "x"}),
                ]
                for case, url, command in invalid:
                    with self.subTest(case=case):
                        refused = client.post(
                            url, json=command, headers=headers)
                        self.assertEqual(
                            refused.status_code, 400, refused.text)
                        self.assertEqual(len(transport.recorded()), 2)
                self.assertEqual(self._snapshot(client), fresh)
        finally:
            meta_transport.run_payload = original_run_payload

    def test_exact_action_cancel_keeps_single_invocation(self):
        transport = RecordingRunPayload()
        original_png = synthetic_png()
        original_run_payload = meta_transport.run_payload
        meta_transport.run_payload = transport
        try:
            app = create_app(provider=self._provider())
            with TestClient(app, base_url=BASE_URL) as client:
                with TestClient(app, base_url=BASE_URL) as worker_client:
                    csrf, cookie, headers, page, _render = (
                        self._establish(client, original_png))
                    source_id = page["source_id"]
                    self.assertEqual(transport.recorded(), [])

                    first_id = "e5" * 16
                    first = client.post(
                        "/api/session/source-analysis",
                        json={"request_id": first_id,
                              "source_id": source_id},
                        headers=headers)
                    # First repair point, before any concurrency.
                    self.assertEqual(first.status_code, 201, first.text)
                    self.assertEqual(len(transport.recorded()), 1)
                    self._assert_call(
                        transport.recorded()[0], SOURCE_FORMAT)
                    snapshot = first.json()["snapshot"]
                    self.assertEqual(len(snapshot["regions"]), 1)
                    region = snapshot["regions"][0]
                    reviewed = client.put(
                        "/api/session/regions/"
                        f"{quote(region['region_id'], safe='')}",
                        json={"inclusion": "included",
                              "current_text": EDITED_TEXT,
                              "current_label": region["current_label"]},
                        headers=headers)
                    self.assertEqual(
                        reviewed.status_code, 200, reviewed.text)
                    prior = reviewed.json()["snapshot"]
                    prior_regions = prior["regions"]
                    first_view = self._view(prior, first_id)

                    transport.block_source = True
                    blocked_id = "f6" * 16
                    self.assertRegex(blocked_id, r"^[0-9a-f]{32}$")
                    holder = {}
                    worker_headers = {
                        "Origin": BASE_URL,
                        "X-CSRF-Token": csrf,
                        "Cookie": cookie,
                    }

                    def submit():
                        try:
                            holder["response"] = worker_client.post(
                                "/api/session/source-analysis",
                                json={"request_id": blocked_id,
                                      "source_id": source_id},
                                headers=worker_headers)
                        except Exception as error:  # reported in main thread
                            holder["error"] = error

                    worker = threading.Thread(
                        target=submit, name="meta-tracer-long-post")
                    worker.start()
                    cancelled_view = None
                    try:
                        self.assertTrue(
                            transport.entered.wait(
                                timeout=ENTERED_WAIT_SECONDS),
                            "synthetic transport was not entered; "
                            "cancellation is unproven")
                        calls = transport.recorded()
                        self.assertEqual(len(calls), 2)
                        self._assert_call(calls[1], SOURCE_FORMAT)
                        blocked_event = calls[1]["cancel_event"]
                        self.assertFalse(blocked_event.is_set())

                        working = self._snapshot(client)
                        self.assertEqual(
                            self._view(working, blocked_id)["state"],
                            "working")
                        seen = self._assert_status(
                            client, blocked_id, source_id, working)
                        self.assertEqual(seen["state"], "working")

                        duplicate = client.post(
                            "/api/session/source-analysis",
                            json={"request_id": blocked_id,
                                  "source_id": source_id},
                            headers=headers)
                        self.assertEqual(
                            duplicate.status_code, 409, duplicate.text)
                        self.assertEqual(len(transport.recorded()), 2)
                        self.assertFalse(blocked_event.is_set())
                        self.assertEqual(self._snapshot(client), working)

                        cancel = client.post(
                            "/api/session/requests/"
                            f"{quote(blocked_id, safe='')}/cancel",
                            json={"source_id": source_id},
                            headers=headers)
                        self.assertEqual(
                            cancel.status_code, 200, cancel.text)
                        body = cancel.json()
                        self.assertEqual(
                            set(body),
                            {"request_id", "source_id", "snapshot"})
                        self.assertEqual(body["request_id"], blocked_id)
                        self.assertEqual(body["source_id"], source_id)
                        cancelled = body["snapshot"]
                        cancelled_view = self._view(
                            cancelled, blocked_id)
                        self.assertEqual(
                            cancelled_view["outcome"], "cancelled")
                        self.assertNotEqual(
                            cancelled_view["state"], "working")
                        self.assertTrue(blocked_event.is_set())
                        self.assertEqual(
                            cancelled["regions"], prior_regions)
                        self.assertEqual(
                            cancelled["questions"], prior["questions"])
                        self.assertEqual(
                            self._view(cancelled, first_id), first_view)
                        original = client.get(
                            "/api/session/page/original")
                        self.assertEqual(original.content, original_png)
                        self.assertEqual(
                            self._assert_status(
                                client, blocked_id, source_id,
                                cancelled),
                            cancelled_view)

                        repeat = client.post(
                            "/api/session/requests/"
                            f"{quote(blocked_id, safe='')}/cancel",
                            json={"source_id": source_id},
                            headers=headers)
                        self.assertEqual(
                            repeat.status_code, 409, repeat.text)
                        current = self._snapshot(client)
                        self.assertEqual(
                            self._view(current, blocked_id),
                            cancelled_view)
                        self.assertEqual(len(transport.recorded()), 2)
                        self.assertTrue(worker.is_alive())
                        self.assertFalse(
                            transport.blocked_returned.is_set())
                        self.assertNotIn("response", holder)
                        self.assertNotIn("error", holder)
                    finally:
                        transport.release.set()
                        worker.join(timeout=JOIN_WAIT_SECONDS)
                    self.assertFalse(
                        worker.is_alive(),
                        "long POST worker did not finish after release")
                    self.assertNotIn(
                        "error", holder, repr(holder.get("error")))
                    original_response = holder["response"]
                    self.assertNotEqual(
                        original_response.status_code, 201,
                        original_response.text)
                    self.assertGreaterEqual(
                        original_response.status_code, 400)
                    self.assertLess(original_response.status_code, 600)
                    self.assertEqual(len(transport.recorded()), 2)
                    final = self._snapshot(client)
                    self.assertEqual(
                        self._view(final, blocked_id), cancelled_view)
                    self.assertEqual(
                        self._assert_status(
                            client, blocked_id, source_id, final),
                        cancelled_view)
                    self.assertEqual(final["regions"], prior_regions)
                    self._assert_raw(
                        client, first_id, "source",
                        SYNTHETIC_SOURCE_RESULT)
        finally:
            meta_transport.run_payload = original_run_payload


if __name__ == "__main__":
    unittest.main()






