"""Focused safety tests for the new teacher-control HTTP writers.

Real app/domain/decoder through TestClient with a synthetic
in-process provider only (explicitly not Meta). The complete
successful journey stays in ``test_http_teacher_controls.py``;
this module adds only new-writer boundary cases: session/Origin/
CSRF and cross-session isolation, malformed and unrepresentable
input, real logical-budget refusal, and one-shot response
preparation failure. The capacity test observes private session
closure state only to choose a fixed cap and to wait for worker
reservations to drain, as the publication tests do; it never changes production decisions.
"""
import copy
import hashlib
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
from teacher_demo.http_teacher_publication import (  # noqa: E402
    accounted_total,
)
from teacher_demo.question_protocol_v2 import (  # noqa: E402
    question_prompt_v2,
)

BASE_URL = "http://127.0.0.1:8000"
GRADE = "  الصَّفُّ ٥  "
OBJECTIVE = "  هَدَفٌ: قِرَاءَةُ النَّصِّ ١٢٣  "
RIGHTS_REF = "synthetic-fixture-rights-note"
SYNTHETIC_MODEL = "synthetic-test-provider (not Meta)"
MARK = "\U0001d11e"

RAW_A = "  اَلْمَاءُ يَجْرِي فِي النَّهْرِ ١٢٣  "
FACT_A = "اَلْمَاءُ يَجْرِي بِسُرْعَةٍ"
EDITED_A = f"  {FACT_A} فِي النَّهْرِ ١٢٣ {MARK}  "
RAW_B = "  اَلشَّجَرُ يَنْمُو عَلَى الضِّفَّةِ ٧٨٩  "
FACT_B = "اَلشَّجَرُ يَخْضَرُّ فِي الرَّبِيعِ"
EDITED_B = f"  {FACT_B} عَلَى الضِّفَّةِ ٧٨٩ {MARK}  "

QUESTION_1 = "  مَاذَا يَجْرِي فِي النَّهْرِ؟  "
GUIDANCE_1 = "يُشِيرُ النَّصُّ الْمُرَاجَعُ إِلَى جَرَيَانِ الْمَاءِ بِسُرْعَةٍ."
COGNITIVE_1 = "Synthetic recall task for the stated grade."
QUESTION_2 = "  مَاذَا يَحْدُثُ لِلشَّجَرِ فِي الرَّبِيعِ؟  "
GUIDANCE_2 = "يَخْضَرُّ الشَّجَرُ فِي الرَّبِيعِ عَلَى ضِفَّةِ النَّهْرِ."
COGNITIVE_2 = "Synthetic understanding task for the stated grade."

AUTH_EDIT_WORDING = "  صِيَاغَةٌ بَعْدَ نَجَاحِ الْحِرَاسَةِ؟  "
VALID_WORDING = "  صِيَاغَةٌ صَالِحَةٌ بَعْدَ الرَّفْضِ؟  "
FAULT_WORDING = "  صِيَاغَةٌ بَعْدَ عَطَلِ التَّجْهِيزِ؟  "
UNICODE_QUESTION = f"  مَاذَا يَجْرِي بِسُرْعَةٍ فِي النَّهْرِ؟ {MARK}  "
UNICODE_GUIDANCE = "  اَلْمَاءُ يَجْرِي بِسُرْعَةٍ فِي النَّهْرِ.  "
UNICODE_COGNITIVE = "  تَعْلِيلٌ دَقِيقٌ يُنَاسِبُ الصَّفَّ.  "
UNICODE_ASSUMPTION = f"  افْتِرَاضٌ مَطْرُوحٌ لِلْمُنَاقَشَةِ {MARK}  "
UNICODE_CRITERION = "  مُرَاعَاةُ هَدَفِ الدَّرْسِ فِي الْإِجَابَةِ  "


def source_result():
    return {
        "raw": {
            "sections": [
                {"section_id": "synthetic-section-river",
                 "kind": "body", "text": RAW_A,
                 "review_reasons": ["synthetic-flag-river"]},
                {"section_id": "synthetic-section-trees",
                 "kind": "body", "text": RAW_B,
                 "review_reasons": ["synthetic-flag-trees"]},
            ],
            "page_continues": False,
            "review_notes": ["synthetic note"],
        },
        "model_name": SYNTHETIC_MODEL,
        "response_id": "synthetic-source-response-1",
        "outcome": "ok",
    }


def draft_item(slot, question, guidance, cognitive, region_id, fact):
    return {
        "slot_id": slot["slot_id"], "level": slot["level"],
        "status": "draft", "question": question,
        "answer_guidance": guidance,
        "cognitive_explanation": cognitive,
        "hypothetical_assumptions": [], "evaluate_criteria": [],
        "create_rubric": [],
        "evidence": [{"region_id": region_id, "quote": fact}],
        "required_order_relationships": [], "abstain_reason": None,
    }


def abstain_item(slot):
    return {
        "slot_id": slot["slot_id"], "level": slot["level"],
        "status": "abstain", "question": "", "answer_guidance": "",
        "cognitive_explanation": "", "hypothetical_assumptions": [],
        "evaluate_criteria": [], "create_rubric": [], "evidence": [],
        "required_order_relationships": [],
        "abstain_reason": "synthetic abstention",
    }


class SafetyProvider:
    """Deterministic in-process test provider; never a default."""

    def __init__(self):
        self.requests = []
        self.source_results = {}
        self.question_results = {}
        self._lock = threading.Lock()

    def __call__(self, request):
        with self._lock:
            self.requests.append(copy.deepcopy(dict(request)))
        if request["kind"] == "source_analysis":
            return self._source(request)
        return self._questions(request)

    def _source(self, request):
        assert request["grade"] == GRADE
        assert request["objective"] == OBJECTIVE
        assert request["media_type"] == "image/png"
        assert hashlib.sha256(
            request["png_bytes"]).hexdigest() == (
                request["render_sha256"])
        result = source_result()
        with self._lock:
            self.source_results[request["request_id"]] = (
                copy.deepcopy(result))
        return copy.deepcopy(result)

    def _questions(self, request):
        assert request["kind"] == "question_generation"
        reviewed = dict(request["reviewed_regions"])
        slots = [dict(slot) for slot in request["requested_slots"]]
        assert set(reviewed.values()) == {EDITED_A, EDITED_B}
        assert request["grade"] == GRADE
        assert request["objective"] == OBJECTIVE
        assert request["prompt"] == question_prompt_v2(
            reviewed, slots, grade=GRADE, objective=OBJECTIVE)
        river_id = next(
            region_id for region_id, text in reviewed.items()
            if text == EDITED_A)
        trees_id = next(
            region_id for region_id, text in reviewed.items()
            if text == EDITED_B)
        drafts = {
            "remember": (QUESTION_1, GUIDANCE_1, COGNITIVE_1,
                         river_id, FACT_A),
            "understand": (QUESTION_2, GUIDANCE_2, COGNITIVE_2,
                           trees_id, FACT_B),
        }
        items = []
        for slot in slots:
            draft = drafts.get(slot["level"])
            items.append(
                abstain_item(slot) if draft is None
                else draft_item(slot, *draft))
        result = {
            "raw": {"items": items},
            "model_name": SYNTHETIC_MODEL,
            "response_id": "synthetic-question-response-1",
            "outcome": "ok",
        }
        with self._lock:
            self.question_results[request["request_id"]] = (
                copy.deepcopy(result))
        return copy.deepcopy(result)


def operation_command(source_id):
    return {"request_id": secrets.token_hex(16),
            "source_id": source_id}


def synthetic_png(rgb=(200, 30, 40)):
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (*rgb, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


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


class HttpTeacherControlsSafetyTests(unittest.TestCase):
    def snapshot(self, client):
        response = client.get("/api/session/snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def question(snapshot, slot_id):
        return next(item for item in snapshot["questions"]
                    if item["slot_id"] == slot_id)

    @staticmethod
    def region(snapshot, section_id):
        return next(item for item in snapshot["regions"]
                    if item["external_section_id"] == section_id)

    @staticmethod
    def question_url(question_id):
        return ("/api/session/questions/"
                + quote(question_id, safe=""))

    @staticmethod
    def region_url(region_id):
        return "/api/session/regions/" + quote(region_id, safe="")

    def preview(self, client):
        response = client.get("/api/session/preview")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["items"]

    def raw_result(self, client, request_id, kind):
        response = client.get(
            "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["kind"], kind)
        return payload["provider_result"]

    def checked(self, client, response):
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()["snapshot"]
        fetched = self.snapshot(client)
        self.assertEqual(snapshot, fetched)
        return fetched

    def approve(self, client, headers, question_id):
        return self.checked(client, client.post(
            self.question_url(question_id) + "/approve",
            json={}, headers=headers))

    def build_baseline(self, client, provider, rgb=(200, 30, 40)):
        """Approved two-region/two-question baseline on one client."""
        created = client.post(
            "/api/sessions", json={}, headers={"Origin": BASE_URL})
        self.assertEqual(created.status_code, 201, created.text)
        headers = {
            "Origin": BASE_URL,
            "X-CSRF-Token": created.json()["csrf_token"]}
        settings = client.put(
            "/api/session/settings",
            json={"grade": GRADE, "objective": OBJECTIVE,
                  "rights_ref": RIGHTS_REF}, headers=headers)
        self.assertEqual(settings.status_code, 200, settings.text)
        started = client.post(
            "/api/session/start", params={"page_index": 0},
            content=synthetic_png(rgb),
            headers={**headers,
                     "Content-Type": "application/octet-stream"})
        self.assertEqual(started.status_code, 201, started.text)
        source_id = started.json()["source_id"]
        source_command = operation_command(source_id)
        analyzed = client.post(
            "/api/session/source-analysis", json=source_command,
            headers=headers)
        self.assertEqual(analyzed.status_code, 201, analyzed.text)
        source_request_id = source_command["request_id"]
        self.assertEqual(analyzed.json()["request_id"],
                         source_request_id)
        snapshot = analyzed.json()["snapshot"]
        self.assertEqual(len(snapshot["regions"]), 2)
        river = self.region(snapshot, "synthetic-section-river")
        trees = self.region(snapshot, "synthetic-section-trees")
        for region, text in ((river, EDITED_A), (trees, EDITED_B)):
            reviewed = client.put(
                self.region_url(region["region_id"]),
                json={"inclusion": "included", "current_text": text,
                      "current_label": region["current_label"]},
                headers=headers)
            self.assertEqual(reviewed.status_code, 200, reviewed.text)
            snapshot = reviewed.json()["snapshot"]
        question_command = operation_command(source_id)
        generated = client.post(
            "/api/session/questions/generate", json=question_command,
            headers=headers)
        self.assertEqual(generated.status_code, 201, generated.text)
        question_request_id = question_command["request_id"]
        self.assertEqual(generated.json()["request_id"],
                         question_request_id)
        snapshot = generated.json()["snapshot"]
        self.assertEqual(len(snapshot["questions"]), 2)
        q1_id = self.question(snapshot, "slot-remember")["question_id"]
        q2_id = self.question(
            snapshot, "slot-understand")["question_id"]
        snapshot = self.approve(client, headers, q1_id)
        snapshot = self.approve(client, headers, q2_id)
        baseline_source_raw = self.raw_result(
            client, source_request_id, "source")
        self.assertEqual(
            baseline_source_raw,
            provider.source_results[source_request_id])
        baseline_question_raw = self.raw_result(
            client, question_request_id, "questions")
        self.assertEqual(
            baseline_question_raw,
            provider.question_results[question_request_id])
        return {
            "headers": headers,
            "source_request_id": source_request_id,
            "question_request_id": question_request_id,
            "river_id": river["region_id"],
            "trees_id": trees["region_id"],
            "q1_id": q1_id, "q2_id": q2_id,
            "baseline_q2": self.question(snapshot, "slot-understand"),
            "baseline_preview": self.preview(client),
            "baseline_source_raw": baseline_source_raw,
            "baseline_question_raw": baseline_question_raw,
        }

    def assert_raws_intact(self, client, provider, ctx):
        self.assertEqual(
            self.raw_result(client, ctx["source_request_id"], "source"),
            ctx["baseline_source_raw"])
        self.assertEqual(
            ctx["baseline_source_raw"],
            provider.source_results[ctx["source_request_id"]])
        self.assertEqual(
            self.raw_result(
                client, ctx["question_request_id"], "questions"),
            ctx["baseline_question_raw"])
        self.assertEqual(
            ctx["baseline_question_raw"],
            provider.question_results[ctx["question_request_id"]])

    def test_new_writers_require_session_origin_and_csrf(self):
        provider = SafetyProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            ctx = self.build_baseline(client, provider)
            headers = ctx["headers"]
            before = self.snapshot(client)
            operations = (
                ("edit", lambda c, h: c.patch(
                    self.question_url(ctx["q1_id"]),
                    json={"question": AUTH_EDIT_WORDING}, headers=h)),
                ("reject", lambda c, h: c.post(
                    self.question_url(ctx["q1_id"]) + "/reject",
                    json={}, headers=h)),
                ("move-down", lambda c, h: c.post(
                    self.region_url(ctx["river_id"]) + "/move-down",
                    json={}, headers=h)),
                ("move-up", lambda c, h: c.post(
                    self.region_url(ctx["trees_id"]) + "/move-up",
                    json={}, headers=h)),
            )
            with TestClient(app, base_url=BASE_URL) as anon:
                variants = (
                    ("no session", anon, headers, 401,
                     "session required"),
                    ("wrong origin", client,
                     {**headers, "Origin": "http://127.0.0.1:9999"},
                     403, "forbidden origin"),
                    ("missing csrf", client, {"Origin": BASE_URL},
                     403, "forbidden request"),
                    ("wrong csrf", client,
                     {**headers,
                      "X-CSRF-Token": "synthetic-wrong-token"},
                     403, "forbidden request"),
                )
                for op_name, invoke in operations:
                    for label, used, used_headers, status, detail in (
                            variants):
                        with self.subTest(writer=op_name, guard=label):
                            response = invoke(used, used_headers)
                            self.assertEqual(
                                response.status_code, status,
                                response.text)
                            self.assertEqual(
                                response.json(), {"detail": detail})
            self.assertEqual(self.snapshot(client), before)
            self.assertEqual(
                self.preview(client), ctx["baseline_preview"])
            self.assert_raws_intact(client, provider, ctx)

            # Healthy opposite: the same writers succeed with the
            # session's own Origin and CSRF token.
            snapshot = self.checked(client, client.patch(
                self.question_url(ctx["q1_id"]),
                json={"question": AUTH_EDIT_WORDING}, headers=headers))
            self.assertEqual(
                self.question(snapshot, "slot-remember")["status"],
                "draft")
            snapshot = self.approve(client, headers, ctx["q1_id"])
            snapshot = self.checked(client, client.post(
                self.question_url(ctx["q1_id"]) + "/reject",
                json={}, headers=headers))
            self.assertEqual(
                self.question(snapshot, "slot-remember")["status"],
                "rejected")
            snapshot = self.approve(client, headers, ctx["q1_id"])
            snapshot = self.checked(client, client.post(
                self.region_url(ctx["river_id"]) + "/move-down",
                json={}, headers=headers))
            self.assertEqual(
                [item["region_id"] for item in snapshot["regions"]],
                [ctx["trees_id"], ctx["river_id"]])
            snapshot = self.checked(client, client.post(
                self.region_url(ctx["river_id"]) + "/move-up",
                json={}, headers=headers))
            self.assertEqual(
                [item["region_id"] for item in snapshot["regions"]],
                [ctx["river_id"], ctx["trees_id"]])
            self.assertEqual(
                self.question(snapshot, "slot-understand"),
                ctx["baseline_q2"])

    def test_cross_session_ids_are_not_visible_to_new_writers(self):
        provider = SafetyProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client_a, \
                TestClient(app, base_url=BASE_URL) as client_b:
            ctx_a = self.build_baseline(client_a, provider)
            ctx_b = self.build_baseline(
                client_b, provider, rgb=(20, 80, 160))
            self.assertNotEqual(ctx_a["q1_id"], ctx_b["q1_id"])
            self.assertNotEqual(ctx_a["river_id"], ctx_b["river_id"])
            before_a = self.snapshot(client_a)
            before_b = self.snapshot(client_b)
            attempts = (
                ("edit", client_b.patch(
                    self.question_url(ctx_a["q1_id"]),
                    json={"question": AUTH_EDIT_WORDING},
                    headers=ctx_b["headers"]),
                 "question not found"),
                ("reject", client_b.post(
                    self.question_url(ctx_a["q1_id"]) + "/reject",
                    json={}, headers=ctx_b["headers"]),
                 "question not found"),
                ("move-down", client_b.post(
                    self.region_url(ctx_a["river_id"]) + "/move-down",
                    json={}, headers=ctx_b["headers"]),
                 "region not found"),
                ("move-up", client_b.post(
                    self.region_url(ctx_a["trees_id"]) + "/move-up",
                    json={}, headers=ctx_b["headers"]),
                 "region not found"),
            )
            for label, response, detail in attempts:
                with self.subTest(writer=label):
                    self.assertEqual(
                        response.status_code, 404, response.text)
                    self.assertEqual(
                        response.json(), {"detail": detail})
            mismatched = client_a.patch(
                self.question_url(ctx_a["q1_id"]),
                json={"question": AUTH_EDIT_WORDING},
                headers={"Origin": BASE_URL,
                         "X-CSRF-Token":
                         ctx_b["headers"]["X-CSRF-Token"]})
            self.assertEqual(mismatched.status_code, 403, mismatched.text)
            self.assertEqual(
                mismatched.json(), {"detail": "forbidden request"})
            self.assertEqual(self.snapshot(client_a), before_a)
            self.assertEqual(self.snapshot(client_b), before_b)

            # Healthy opposites stay inside their own sessions.
            snapshot_a = self.checked(client_a, client_a.patch(
                self.question_url(ctx_a["q1_id"]),
                json={"question": AUTH_EDIT_WORDING},
                headers=ctx_a["headers"]))
            self.assertEqual(
                self.question(snapshot_a, "slot-remember")
                ["current_question"], AUTH_EDIT_WORDING)
            self.assertEqual(self.snapshot(client_b), before_b)
            snapshot_b = self.checked(client_b, client_b.post(
                self.region_url(ctx_b["river_id"]) + "/move-down",
                json={}, headers=ctx_b["headers"]))
            self.assertEqual(
                [item["region_id"] for item in snapshot_b["regions"]],
                [ctx_b["trees_id"], ctx_b["river_id"]])
            self.assertEqual(self.snapshot(client_a), snapshot_a)

    def test_nonempty_bodies_and_malformed_edits_refuse(self):
        provider = SafetyProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            ctx = self.build_baseline(client, provider)
            headers = ctx["headers"]
            before = self.snapshot(client)
            commands = (
                ("reject nonempty", client.post(
                    self.question_url(ctx["q1_id"]) + "/reject",
                    json={"unexpected": True}, headers=headers),
                 400, "invalid request"),
                ("move-down nonempty", client.post(
                    self.region_url(ctx["river_id"]) + "/move-down",
                    json={"moved": True}, headers=headers),
                 400, "invalid request"),
                ("move-up nonempty", client.post(
                    self.region_url(ctx["trees_id"]) + "/move-up",
                    json={"moved": True}, headers=headers),
                 400, "invalid request"),
                ("edit wrong media type", client.patch(
                    self.question_url(ctx["q1_id"]), content=b"{}",
                    headers={**headers,
                             "Content-Type": "text/plain"}),
                 415, "unsupported media type"),
                ("edit over command byte limit", client.patch(
                    self.question_url(ctx["q1_id"]),
                    json={"question": "x" * 17000}, headers=headers),
                 413, "request too large"),
            )
            for label, response, status, detail in commands:
                with self.subTest(command=label):
                    self.assertEqual(
                        response.status_code, status, response.text)
                    self.assertEqual(
                        response.json(), {"detail": detail})
                    self.assertEqual(self.snapshot(client), before)
            attempts = (
                ("question not a string", {"question": 123}),
                ("bloom level not a string", {"bloom_level": 7}),
                ("blank hypothetical assumption",
                 {"question": VALID_WORDING,
                  "hypothetical_assumptions": ["   "]}),
                ("objective criterion claiming source facts",
                 {"question": VALID_WORDING,
                  "evaluate_criteria": [{
                      "text": "  مِعْيَارُ الْهَدَفِ  ",
                      "origin": "objective",
                      "supporting_facts": [{
                          "region_id": ctx["river_id"],
                          "quote": FACT_A}]}]}),
                ("criterion missing origin",
                 {"question": VALID_WORDING,
                  "create_rubric": [{
                      "text": "  مِعْيَارٌ نَاقِصٌ  ",
                      "supporting_facts": []}]}),
                ("pair-encoded source support rebind",
                 {"question": VALID_WORDING,
                  "evaluate_criteria": [{
                      "text": "  مِعْيَارُ النَّصِّ  ",
                      "origin": "source",
                      "supporting_facts": [[
                          ctx["trees_id"], FACT_B]]}]}),
            )
            for label, payload in attempts:
                with self.subTest(attempt=label):
                    refused = client.patch(
                        self.question_url(ctx["q1_id"]),
                        json=payload, headers=headers)
                    self.assertEqual(
                        refused.status_code, 400, refused.text)
                    self.assertEqual(
                        refused.json(), {"detail": "invalid request"})
                    self.assertEqual(self.snapshot(client), before)
            self.assertEqual(
                self.preview(client), ctx["baseline_preview"])
            self.assert_raws_intact(client, provider, ctx)

            # Healthy opposites: the empty edit is an exact no-op,
            # then a valid edit publishes normally.
            snapshot = self.checked(client, client.patch(
                self.question_url(ctx["q1_id"]), json={},
                headers=headers))
            self.assertEqual(snapshot, before)
            snapshot = self.checked(client, client.patch(
                self.question_url(ctx["q1_id"]),
                json={"question": VALID_WORDING}, headers=headers))
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["current_question"], VALID_WORDING)
            self.assertEqual(q1["original_question"], QUESTION_1)
            self.assertEqual(q1["status"], "draft")

    def test_exact_unicode_blank_and_unrepresentable_edits(self):
        provider = SafetyProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            ctx = self.build_baseline(client, provider)
            headers = ctx["headers"]
            before = self.snapshot(client)
            json_headers = {**headers,
                            "Content-Type": "application/json"}
            unrepresentable = (
                ("top-level wording", b'{"question": "  \\ud800  "}'),
                ("nested assumption",
                 b'{"question": "synthetic-unpublished", '
                 b'"hypothetical_assumptions": ["\\udc00"]}'),
                ("nested criterion text",
                 b'{"question": "synthetic-unpublished", '
                 b'"evaluate_criteria": [{"text": "\\ud800", '
                 b'"origin": "objective", "supporting_facts": []}]}'),
                ("unrepresentable key", b'{"\\ud800": "x"}'),
            )
            for label, content in unrepresentable:
                with self.subTest(case=label):
                    refused = client.patch(
                        self.question_url(ctx["q1_id"]),
                        content=content, headers=json_headers)
                    self.assertEqual(
                        refused.status_code, 400, refused.text)
                    self.assertEqual(
                        refused.json(), {"detail": "invalid request"})
                    self.assertEqual(self.snapshot(client), before)
            self.assertEqual(
                self.preview(client), ctx["baseline_preview"])
            self.assert_raws_intact(client, provider, ctx)

            snapshot = self.checked(client, client.patch(
                self.question_url(ctx["q1_id"]),
                json={
                    "question": UNICODE_QUESTION,
                    "answer_guidance": UNICODE_GUIDANCE,
                    "cognitive_explanation": UNICODE_COGNITIVE,
                    "hypothetical_assumptions": [UNICODE_ASSUMPTION],
                    "evaluate_criteria": [{
                        "text": UNICODE_CRITERION,
                        "origin": "objective",
                        "supporting_facts": []}]},
                headers=headers))
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["current_question"], UNICODE_QUESTION)
            self.assertEqual(
                q1["current_answer_guidance"], UNICODE_GUIDANCE)
            self.assertEqual(
                q1["current_cognitive_explanation"], UNICODE_COGNITIVE)
            self.assertEqual(
                q1["hypothetical_assumptions"], [UNICODE_ASSUMPTION])
            self.assertEqual(q1["evaluate_criteria"], [{
                "text": UNICODE_CRITERION, "origin": "objective",
                "supporting_facts": []}])
            self.assertEqual(q1["original_question"], QUESTION_1)
            snapshot = self.approve(client, headers, ctx["q1_id"])
            entry = next(item for item in self.preview(client)
                         if item["question_id"] == ctx["q1_id"])
            self.assertEqual(entry["question"], UNICODE_QUESTION)
            self.assertEqual(entry["answer_guidance"], UNICODE_GUIDANCE)
            self.assertEqual(entry["author"], "teacher")

            # Blank current wording is stored exactly and stays
            # approval-blocked; Approve does not repair it.
            snapshot = self.checked(client, client.patch(
                self.question_url(ctx["q1_id"]),
                json={"question": "   "}, headers=headers))
            blank_snapshot = snapshot
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["current_question"], "   ")
            self.assertEqual(q1["status"], "draft")
            self.assertEqual(q1["blocks"], ["blank_field"])
            self.assertEqual(q1["original_question"], QUESTION_1)
            self.assertEqual(
                [item["question_id"] for item in self.preview(client)],
                [ctx["q2_id"]])
            blocked = client.post(
                self.question_url(ctx["q1_id"]) + "/approve",
                json={}, headers=headers)
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertEqual(
                blocked.json()["detail"]["code"], "approval_blocked")
            self.assertEqual(self.snapshot(client), blank_snapshot)
            snapshot = self.checked(client, client.patch(
                self.question_url(ctx["q1_id"]),
                json={"question": UNICODE_QUESTION}, headers=headers))
            self.assertEqual(
                self.question(snapshot, "slot-remember")["blocks"], [])
            snapshot = self.approve(client, headers, ctx["q1_id"])
            self.assertEqual(
                self.question(snapshot, "slot-remember")["status"],
                "approved")
            self.assertEqual(
                self.question(snapshot, "slot-understand"),
                ctx["baseline_q2"])
            self.assert_raws_intact(client, provider, ctx)

    def test_oversized_retained_edit_refused_by_real_budget(self):
        provider = SafetyProvider()
        app = create_app(provider=provider)
        with TestClient(app, base_url=BASE_URL) as client:
            ctx = self.build_baseline(client, provider)
            headers = ctx["headers"]
            sessions = closure_values(app)["sessions"]
            state = next(
                item for item in sessions.values()
                if item.csrf_token == headers["X-CSRF-Token"])
            inflight = closure_values(app)["inflight"]

            def drained():
                return (not state.worker_reservations
                        and inflight["provider"] == 0)

            deadline = time.monotonic() + 5
            while not drained() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(drained(), "provider workers did not drain")
            before = self.snapshot(client)
            accounted_before = accounted_total(
                state.graph_cost, state.worker_reservations,
                state.terminal_reserves)
            cap = accounted_before + 1
            self.assertLess(accounted_before, cap)
            large_wording = "x" * 12000
            with mock.patch.object(
                    http_app_module,
                    "MAX_RETAINED_BYTES_PER_SESSION", cap):
                refused = client.patch(
                    self.question_url(ctx["q1_id"]),
                    json={"question": large_wording}, headers=headers)
                self.assertEqual(refused.status_code, 413, refused.text)
                self.assertEqual(
                    refused.json(), {"detail": "request too large"})
                self.assertEqual(self.snapshot(client), before)
                self.assertEqual(
                    accounted_total(
                        state.graph_cost, state.worker_reservations,
                        state.terminal_reserves),
                    accounted_before)
            self.assertEqual(
                self.preview(client), ctx["baseline_preview"])
            self.assert_raws_intact(client, provider, ctx)

            # Healthy opposite: the same edit fits the real budget.
            snapshot = self.checked(client, client.patch(
                self.question_url(ctx["q1_id"]),
                json={"question": large_wording}, headers=headers))
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["current_question"], large_wording)
            self.assertEqual(q1["original_question"], QUESTION_1)
            self.assertEqual(q1["status"], "draft")
            self.assertEqual(
                self.question(snapshot, "slot-understand"),
                ctx["baseline_q2"])
            self.assertEqual(
                [item["question_id"] for item in self.preview(client)],
                [ctx["q2_id"]])
            self.assert_raws_intact(client, provider, ctx)

    def test_response_preparation_failure_publishes_nothing(self):
        for writer in ("edit", "reject", "move-down", "move-up"):
            with self.subTest(writer=writer):
                provider = SafetyProvider()
                app = create_app(provider=provider)
                with TestClient(
                        app, base_url=BASE_URL,
                        raise_server_exceptions=False) as client:
                    ctx = self.build_baseline(client, provider)
                    headers = ctx["headers"]
                    before = self.snapshot(client)
                    before_preview = self.preview(client)

                    def invoke():
                        if writer == "edit":
                            return client.patch(
                                self.question_url(ctx["q1_id"]),
                                json={"question": FAULT_WORDING},
                                headers=headers)
                        if writer == "reject":
                            return client.post(
                                self.question_url(ctx["q1_id"])
                                + "/reject", json={}, headers=headers)
                        if writer == "move-down":
                            return client.post(
                                self.region_url(ctx["river_id"])
                                + "/move-down", json={},
                                headers=headers)
                        return client.post(
                            self.region_url(ctx["trees_id"])
                            + "/move-up", json={}, headers=headers)

                    real_json_response = http_app_module.JSONResponse
                    fault = {"fired": False}

                    def flaky_json_response(*args, **kwargs):
                        if not fault["fired"]:
                            fault["fired"] = True
                            raise RuntimeError(
                                "synthetic one-shot response failure")
                        return real_json_response(*args, **kwargs)

                    with mock.patch.object(
                            http_app_module, "JSONResponse",
                            flaky_json_response):
                        failed = invoke()
                    self.assertEqual(failed.status_code, 500, failed.text)
                    self.assertTrue(fault["fired"])
                    self.assertEqual(self.snapshot(client), before)
                    self.assertEqual(
                        self.preview(client), before_preview)
                    self.assert_raws_intact(client, provider, ctx)

                    # Healthy opposite through the same real seam.
                    snapshot = self.checked(client, invoke())
                    if writer == "edit":
                        q1 = self.question(snapshot, "slot-remember")
                        self.assertEqual(
                            q1["current_question"], FAULT_WORDING)
                        self.assertEqual(q1["status"], "draft")
                    elif writer == "reject":
                        self.assertEqual(
                            self.question(
                                snapshot, "slot-remember")["status"],
                            "rejected")
                    else:
                        self.assertEqual(
                            [item["region_id"]
                             for item in snapshot["regions"]],
                            [ctx["trees_id"], ctx["river_id"]])
                    self.assertEqual(
                        self.question(snapshot, "slot-understand"),
                        ctx["baseline_q2"])


if __name__ == "__main__":
    unittest.main()
