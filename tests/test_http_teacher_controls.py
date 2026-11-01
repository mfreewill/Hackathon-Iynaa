"""One real-HTTP teacher-controls tracer.

Existing session/setup/Start, source-analysis, region review,
question generation, approve, snapshot, preview and raw routes run
first and remain unchanged. The question-edit route is
exercised below.

Teacher-control API exercised by this tracer. Every
route is a session mutation (allowed Origin + CSRF headers and a
bounded JSON object body) returning ``200 {"snapshot": ...}``:

- ``PATCH /api/session/questions/{question_id}`` wraps
  ``TeacherWorkspace.edit_question(question_id, **changes)``. PATCH
  because the body is a *subset* of the accepted domain input
  names ``question``, ``answer_guidance``,
  ``cognitive_explanation``, ``grade``, ``objective``,
  ``bloom_level``, ``hypothetical_assumptions``,
  ``evaluate_criteria`` and ``create_rubric``, with their domain
  value shapes: the first five and ``bloom_level`` are strings
  (``bloom_level`` in the protocol-v2 Bloom levels),
  ``hypothetical_assumptions`` is a list of nonblank strings, and
  criteria/rubric entries are exactly ``{text, origin,
  supporting_facts}`` whose facts are ``{region_id, quote}``
  objects (the domain canonicalizes the ``[region_id, quote]``
  pair encoding projected in snapshots to the same facts), under
  the domain origin and immutable source-support rules. Body
  values are forwarded to ``edit_question`` without re-encoding
  evidence. A body whose values are all identical to the current
  values is a no-op. Unknown question: ``404 {"detail": "question
  not found"}``. Any key outside the editable names, malformed
  value or support-rebinding nested evidence: ``400 {"detail":
  "invalid request"}`` and nothing is published.
- ``POST /api/session/questions/{question_id}/reject`` with ``{}``
  wraps ``reject_question`` (unknown question: same 404 as above).
- ``POST /api/session/regions/{region_id}/move-up`` and
  ``.../move-down`` with ``{}`` wrap ``move_region_up`` /
  ``move_region_down`` (unknown region: ``404 {"detail": "region
  not found"}``). Pure display movement; at a boundary the returned
  snapshot is the unchanged snapshot.

The synthetic provider below exists only in this test and is
injected only as ``create_app(provider=...)``; it is explicitly
not Meta. It is callable with one immutable request mapping.
Source requests carry kind/request_id/source+render
identity/media type/png_bytes/grade/objective; question requests
carry kind/request_id/source_id, the real domain prompt,
reviewed_regions, requested_slots, and grade/objective. Results
are ``{raw, model_name, response_id, outcome}``; raw is the exact
source proposal or teacher-question/v2 ``{"items": [...]}``
object. Returned envelopes are captured under the actual request
ID of each provider request, never by completion-list position.
"""
import copy
import hashlib
import io
import pathlib
import secrets
import sys
import threading
import unittest
from urllib.parse import quote

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from teacher_demo.http_app import create_app  # noqa: E402
from teacher_demo.question_protocol_v2 import (  # noqa: E402
    question_prompt_v2,
)

BASE_URL = "http://127.0.0.1:8000"
GRADE = "  الصَّفُّ ٥  "
OBJECTIVE = "  هَدَفٌ: قِرَاءَةُ النَّصِّ ١٢٣  "
RIGHTS_REF = "synthetic-fixture-rights-note"
SYNTHETIC_MODEL = "synthetic-test-provider (not Meta)"
# Supplementary-plane marker whose codepoint must survive exactly.
MARK = "\U0001d11e"

RAW_A = "  اَلْمَاءُ يَجْرِي فِي النَّهْرِ ١٢٣  "
FACT_A = "اَلْمَاءُ يَجْرِي بِسُرْعَةٍ"
EDITED_A = f"  {FACT_A} فِي النَّهْرِ ١٢٣ {MARK}  "
# A real text change that still contains the exact quote FACT_A.
CHANGED_A = f"  {FACT_A} فِي النَّهْرِ صَبَاحًا ٤٥٦ {MARK}  "
RAW_B = "  اَلشَّجَرُ يَنْمُو عَلَى الضِّفَّةِ ٧٨٩  "
FACT_B = "اَلشَّجَرُ يَخْضَرُّ فِي الرَّبِيعِ"
EDITED_B = f"  {FACT_B} عَلَى الضِّفَّةِ ٧٨٩ {MARK}  "

QUESTION_1 = "  مَاذَا يَجْرِي فِي النَّهْرِ؟  "
GUIDANCE_1 = "يُشِيرُ النَّصُّ الْمُرَاجَعُ إِلَى جَرَيَانِ الْمَاءِ بِسُرْعَةٍ."
COGNITIVE_1 = "Synthetic recall task for the stated grade."
QUESTION_2 = "  مَاذَا يَحْدُثُ لِلشَّجَرِ فِي الرَّبِيعِ؟  "
GUIDANCE_2 = "يَخْضَرُّ الشَّجَرُ فِي الرَّبِيعِ عَلَى ضِفَّةِ النَّهْرِ."
COGNITIVE_2 = "Synthetic understanding task for the stated grade."

EDITED_Q1 = f"  أَيُّ شَيْءٍ يَجْرِي بِسُرْعَةٍ فِي النَّهْرِ؟ {MARK}  "
EDITED_GUIDANCE_1 = "  اَلْمَاءُ هُوَ الَّذِي يَجْرِي بِسُرْعَةٍ فِي النَّهْرِ.  "
EDITED_COGNITIVE_1 = (
    "  تَعْلِيلٌ مُعَدَّلٌ لِمَهِمَّةِ اسْتِرْجَاعٍ تُنَاسِبُ الصَّفَّ.  ")
REJECTED_EDIT_Q1 = "  مَا الَّذِي يَجْرِي فِي النَّهْرِ بِسُرْعَةٍ؟  "
REFUSAL_WORDING = "  صِيَاغَةٌ مَرْفُوضَةٌ لَا يَجِبُ أَنْ تُنْشَرَ؟  "


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


class ControlsProvider:
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
        assert reviewed and set(reviewed.values()) == {EDITED_A, EDITED_B}
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
            if draft is None:
                items.append(abstain_item(slot))
            else:
                items.append(draft_item(slot, *draft))
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


def synthetic_png():
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (200, 30, 40, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


class HttpTeacherControlsTracerTests(unittest.TestCase):
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

    def preview(self, client):
        response = client.get("/api/session/preview")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["items"]

    @staticmethod
    def by_question_id(items):
        return {item["question_id"]: item for item in items}

    def raw_result(self, client, request_id, kind):
        response = client.get(
            "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["kind"], kind)
        self.assertEqual(payload["raw_retention"], "retained")
        return payload["provider_result"]

    def checked_snapshot_response(self, client, response):
        self.assertEqual(response.status_code, 200, response.text)
        snapshot = response.json()["snapshot"]
        fetched = self.snapshot(client)
        self.assertEqual(snapshot, fetched)
        return fetched

    def approve(self, client, headers, question_id):
        return self.checked_snapshot_response(client, client.post(
            "/api/session/questions/"
            + quote(question_id, safe="") + "/approve",
            json={}, headers=headers))

    def patch_question(self, client, headers, question_id, payload):
        return client.patch(
            "/api/session/questions/" + quote(question_id, safe=""),
            json=payload, headers=headers)

    def edit_question_ok(self, client, headers, question_id, payload):
        return self.checked_snapshot_response(
            client,
            self.patch_question(client, headers, question_id, payload))

    def command_ok(self, client, headers, url):
        return self.checked_snapshot_response(
            client, client.post(url, json={}, headers=headers))

    def put_region_text(self, client, headers, region, text):
        return self.checked_snapshot_response(client, client.put(
            "/api/session/regions/"
            + quote(region["region_id"], safe=""),
            json={"inclusion": "included", "current_text": text,
                  "current_label": region["current_label"]},
            headers=headers))

    def test_teacher_edit_reject_move_and_region_change_journey(self):
        provider = ControlsProvider()
        with TestClient(create_app(provider=provider),
                        base_url=BASE_URL) as client:
            created = client.post(
                "/api/sessions", json={},
                headers={"Origin": BASE_URL})
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
                content=synthetic_png(),
                headers={**headers,
                         "Content-Type": "application/octet-stream"})
            self.assertEqual(started.status_code, 201, started.text)
            page = started.json()

            source_command = operation_command(page["source_id"])
            analyzed = client.post(
                "/api/session/source-analysis", json=source_command,
                headers=headers)
            self.assertEqual(analyzed.status_code, 201, analyzed.text)
            source_request_id = source_command["request_id"]
            self.assertEqual(analyzed.json()["request_id"],
                             source_request_id)
            snapshot = analyzed.json()["snapshot"]
            self.assertEqual(self.snapshot(client), snapshot)
            self.assertEqual(snapshot["source_id"], page["source_id"])
            self.assertEqual(snapshot["grade"], GRADE)
            self.assertEqual(snapshot["objective"], OBJECTIVE)
            self.assertTrue(snapshot["source_analysis_terminal"])
            self.assertFalse(snapshot["can_generate"])
            self.assertEqual(snapshot["included_count"], 0)
            self.assertEqual(snapshot["withheld_count"], 2)
            source_request = next(
                item for item in snapshot["requests"]
                if item["request_id"] == source_request_id)
            self.assertEqual(source_request["kind"], "source")
            self.assertEqual(source_request["state"], "terminal")
            self.assertEqual(source_request["outcome"], "ok")
            self.assertEqual(len(snapshot["regions"]), 2)
            river = self.region(snapshot, "synthetic-section-river")
            trees = self.region(snapshot, "synthetic-section-trees")
            river_id = river["region_id"]
            trees_id = trees["region_id"]
            for view, raw, flags, position in (
                    (river, RAW_A, ["synthetic-flag-river"], 1),
                    (trees, RAW_B, ["synthetic-flag-trees"], 2)):
                self.assertEqual(view["request_id"], source_request_id)
                self.assertEqual(view["raw_text"], raw)
                self.assertEqual(view["current_text"], raw)
                self.assertEqual(view["raw_label"], "body")
                self.assertEqual(view["current_label"], "body")
                self.assertEqual(view["text_author"], "model")
                self.assertEqual(view["label_author"], "model")
                self.assertEqual(view["inclusion"], "unreviewed")
                self.assertEqual(view["model_flags"], flags)
                self.assertEqual(view["display_position"], position)
                self.assertEqual(
                    view["source_box"],
                    [0, 0, page["width"], page["height"]])
            baseline_source_raw = self.raw_result(
                client, source_request_id, "source")
            self.assertEqual(
                baseline_source_raw,
                provider.source_results[source_request_id])
            source_provider_request = next(
                item for item in provider.requests
                if item["request_id"] == source_request_id)
            self.assertEqual(
                source_provider_request["kind"], "source_analysis")
            self.assertEqual(
                source_provider_request["source_id"], page["source_id"])
            self.assertEqual(
                source_provider_request["source_sha256"],
                page["source_sha256"])
            self.assertEqual(
                source_provider_request["render_sha256"],
                page["render_sha256"])

            snapshot = self.put_region_text(
                client, headers, river, EDITED_A)
            snapshot = self.put_region_text(
                client, headers,
                self.region(snapshot, "synthetic-section-trees"),
                EDITED_B)
            river = self.region(snapshot, "synthetic-section-river")
            trees = self.region(snapshot, "synthetic-section-trees")
            for view, raw, current in (
                    (river, RAW_A, EDITED_A), (trees, RAW_B, EDITED_B)):
                self.assertEqual(view["raw_text"], raw)
                self.assertEqual(view["current_text"], current)
                self.assertEqual(view["text_author"], "teacher")
                self.assertEqual(view["inclusion"], "included")
            self.assertTrue(snapshot["can_generate"])
            self.assertEqual(snapshot["included_count"], 2)
            self.assertEqual(snapshot["withheld_count"], 0)
            self.assertEqual(snapshot["questions"], [])
            self.assertEqual(snapshot["preview_items"], [])

            question_command = operation_command(
                snapshot["source_id"])
            generated = client.post(
                "/api/session/questions/generate",
                json=question_command, headers=headers)
            self.assertEqual(generated.status_code, 201, generated.text)
            question_request_id = question_command["request_id"]
            self.assertEqual(generated.json()["request_id"],
                             question_request_id)
            snapshot = generated.json()["snapshot"]
            self.assertEqual(self.snapshot(client), snapshot)
            outcomes = [item for item in snapshot["slot_outcomes"]
                        if item["request_id"] == question_request_id]
            self.assertEqual(len(outcomes), 6)
            self.assertEqual(
                sorted(item["outcome"] for item in outcomes),
                ["abstain"] * 4 + ["draft"] * 2)
            self.assertEqual(
                sorted(item["level"] for item in outcomes
                       if item["outcome"] == "draft"),
                ["remember", "understand"])
            self.assertTrue(all(
                item["abstain_reason"] == "synthetic abstention"
                for item in outcomes if item["outcome"] == "abstain"))
            outcome_by_slot = {
                item["slot_id"]: item for item in outcomes}
            self.assertEqual(len(snapshot["questions"]), 2)
            q1 = self.question(snapshot, "slot-remember")
            q2 = self.question(snapshot, "slot-understand")
            q1_id = q1["question_id"]
            q2_id = q2["question_id"]
            self.assertEqual(
                q1_id, outcome_by_slot["slot-remember"]["question_id"])
            self.assertEqual(
                q2_id, outcome_by_slot["slot-understand"]["question_id"])
            for view, region_id, fact, text, guidance, cognitive, level in (
                    (q1, river_id, FACT_A, QUESTION_1, GUIDANCE_1,
                     COGNITIVE_1, "remember"),
                    (q2, trees_id, FACT_B, QUESTION_2, GUIDANCE_2,
                     COGNITIVE_2, "understand")):
                self.assertEqual(view["request_id"], question_request_id)
                self.assertEqual(view["status"], "draft")
                self.assertEqual(view["blocks"], [])
                self.assertEqual(view["original_question"], text)
                self.assertEqual(view["current_question"], text)
                self.assertEqual(view["original_answer_guidance"], guidance)
                self.assertEqual(view["current_answer_guidance"], guidance)
                self.assertEqual(
                    view["original_cognitive_explanation"], cognitive)
                self.assertEqual(
                    view["current_cognitive_explanation"], cognitive)
                self.assertEqual(view["generation_grade"], GRADE)
                self.assertEqual(view["generation_objective"], OBJECTIVE)
                self.assertEqual(view["current_grade"], GRADE)
                self.assertEqual(view["current_objective"], OBJECTIVE)
                self.assertEqual(view["model_level"], level)
                self.assertEqual(view["current_level"], level)
                self.assertEqual(view["level_author"], "model")
                self.assertEqual(view["question_author"], "model")
                self.assertEqual(view["supporting_region_ids"], [region_id])
                self.assertEqual(view["anchors"], [[region_id, fact]])
                self.assertEqual(view["required_order_relationships"], [])
            river = self.region(snapshot, "synthetic-section-river")
            trees = self.region(snapshot, "synthetic-section-trees")
            self.assertEqual(river["linked_question_ids"], [q1_id])
            self.assertEqual(trees["linked_question_ids"], [q2_id])
            self.assertEqual(snapshot["preview_items"], [])
            self.assertEqual(self.preview(client), [])
            baseline_question_raw = self.raw_result(
                client, question_request_id, "questions")
            self.assertEqual(
                baseline_question_raw,
                provider.question_results[question_request_id])
            question_provider_request = next(
                item for item in provider.requests
                if item["request_id"] == question_request_id)
            self.assertEqual(
                question_provider_request["kind"], "question_generation")
            self.assertEqual(
                question_provider_request["source_id"], page["source_id"])
            self.assertEqual(
                dict(question_provider_request["reviewed_regions"]),
                {river_id: EDITED_A, trees_id: EDITED_B})
            self.assertEqual(
                [dict(slot) for slot in
                 question_provider_request["requested_slots"]],
                [{"slot_id": f"slot-{level}", "level": level}
                 for level in ("remember", "understand", "apply",
                               "analyze", "evaluate", "create")])

            # Step 1 baseline: approve both drafts explicitly.
            snapshot = self.approve(client, headers, q1_id)
            self.assertEqual(
                [item["question_id"] for item in self.preview(client)],
                [q1_id])
            snapshot = self.approve(client, headers, q2_id)
            baseline_q2 = self.question(snapshot, "slot-understand")
            baseline_preview = self.preview(client)
            self.assertEqual(
                snapshot["preview_items"], baseline_preview)
            baseline_q1_preview = {
                "question_id": q1_id, "question": QUESTION_1,
                "bloom_level": "remember", "author": "model",
                "answer_guidance": GUIDANCE_1,
                "resolved_anchors": [[river_id, FACT_A, EDITED_A]],
                "original_question": QUESTION_1,
                "original_answer_guidance": GUIDANCE_1}
            baseline_q2_preview = {
                "question_id": q2_id, "question": QUESTION_2,
                "bloom_level": "understand", "author": "model",
                "answer_guidance": GUIDANCE_2,
                "resolved_anchors": [[trees_id, FACT_B, EDITED_B]],
                "original_question": QUESTION_2,
                "original_answer_guidance": GUIDANCE_2}
            self.assertEqual(
                self.by_question_id(baseline_preview),
                {q1_id: baseline_q1_preview,
                 q2_id: baseline_q2_preview})
            self.assertEqual(baseline_q2["status"], "approved")

            # Step 2: first prospective operation edits one approved
            # question. On the accepted app this PATCH route is
            # absent (404), so the 200 assertion is the RED.
            snapshot = self.edit_question_ok(
                client, headers, q1_id,
                {"question": EDITED_Q1,
                 "answer_guidance": EDITED_GUIDANCE_1,
                 "cognitive_explanation": EDITED_COGNITIVE_1})
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["status"], "draft")
            self.assertEqual(q1["current_question"], EDITED_Q1)
            self.assertEqual(q1["current_answer_guidance"],
                             EDITED_GUIDANCE_1)
            self.assertEqual(q1["current_cognitive_explanation"],
                             EDITED_COGNITIVE_1)
            self.assertEqual(q1["original_question"], QUESTION_1)
            self.assertEqual(q1["original_answer_guidance"], GUIDANCE_1)
            self.assertEqual(q1["original_cognitive_explanation"],
                             COGNITIVE_1)
            self.assertEqual(q1["anchors"], [[river_id, FACT_A]])
            self.assertEqual(q1["supporting_region_ids"], [river_id])
            self.assertEqual(q1["question_author"], "teacher")
            field_authors = dict(q1["field_authors"])
            self.assertEqual(field_authors["question"], "teacher")
            self.assertEqual(field_authors["answer_guidance"], "teacher")
            self.assertEqual(
                field_authors["cognitive_explanation"], "teacher")
            self.assertEqual(field_authors["bloom_level"], "model")
            self.assertEqual(field_authors["grade"], "model")
            self.assertEqual(field_authors["objective"], "model")
            self.assertEqual(
                self.question(snapshot, "slot-understand"), baseline_q2)
            self.assertEqual(snapshot["preview_items"],
                             [baseline_q2_preview])
            self.assertEqual(self.preview(client), [baseline_q2_preview])
            self.assertEqual(
                self.raw_result(client, source_request_id, "source"),
                baseline_source_raw)
            self.assertEqual(
                self.raw_result(client, question_request_id, "questions"),
                baseline_question_raw)

            # A valid identical edit is a no-op: it revokes nothing.
            before_identical = snapshot
            snapshot = self.edit_question_ok(
                client, headers, q2_id,
                {"question": QUESTION_2,
                 "answer_guidance": GUIDANCE_2,
                 "cognitive_explanation": COGNITIVE_2,
                 "grade": GRADE, "objective": OBJECTIVE,
                 "bloom_level": "understand",
                 "hypothetical_assumptions": [],
                 "evaluate_criteria": [], "create_rubric": []})
            self.assertEqual(snapshot, before_identical)
            self.assertEqual(
                self.question(snapshot, "slot-understand"), baseline_q2)
            self.assertEqual(
                self.question(snapshot, "slot-remember")["status"],
                "draft")
            self.assertEqual(self.preview(client), [baseline_q2_preview])

            # Step 3: re-approve, then reject only that question.
            snapshot = self.approve(client, headers, q1_id)
            edited_q1_preview = {
                "question_id": q1_id, "question": EDITED_Q1,
                "bloom_level": "remember", "author": "teacher",
                "answer_guidance": EDITED_GUIDANCE_1,
                "resolved_anchors": [[river_id, FACT_A, EDITED_A]],
                "original_question": QUESTION_1,
                "original_answer_guidance": GUIDANCE_1}
            self.assertEqual(
                self.by_question_id(self.preview(client)),
                {q1_id: edited_q1_preview,
                 q2_id: baseline_q2_preview})
            before_unknown_reject = snapshot
            rejected = client.post(
                "/api/session/questions/"
                + quote("missing-question", safe="") + "/reject",
                json={}, headers=headers)
            self.assertEqual(rejected.status_code, 404, rejected.text)
            self.assertEqual(
                rejected.json(), {"detail": "question not found"})
            self.assertEqual(
                self.snapshot(client), before_unknown_reject)
            snapshot = self.command_ok(
                client, headers,
                "/api/session/questions/"
                + quote(q1_id, safe="") + "/reject")
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["status"], "rejected")
            self.assertEqual(q1["current_question"], EDITED_Q1)
            self.assertEqual(q1["current_answer_guidance"],
                             EDITED_GUIDANCE_1)
            self.assertEqual(q1["original_question"], QUESTION_1)
            self.assertEqual(q1["anchors"], [[river_id, FACT_A]])
            self.assertEqual(
                self.question(snapshot, "slot-understand"), baseline_q2)
            self.assertEqual(self.preview(client), [baseline_q2_preview])

            # Editing rejected wording keeps it rejected; only a new
            # explicit approval restores it.
            snapshot = self.edit_question_ok(
                client, headers, q1_id,
                {"question": REJECTED_EDIT_Q1})
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["status"], "rejected")
            self.assertEqual(q1["current_question"], REJECTED_EDIT_Q1)
            self.assertEqual(q1["current_answer_guidance"],
                             EDITED_GUIDANCE_1)
            self.assertEqual(q1["original_question"], QUESTION_1)
            self.assertEqual(self.preview(client), [baseline_q2_preview])
            snapshot = self.approve(client, headers, q1_id)
            restored_q1_preview = {
                "question_id": q1_id, "question": REJECTED_EDIT_Q1,
                "bloom_level": "remember", "author": "teacher",
                "answer_guidance": EDITED_GUIDANCE_1,
                "resolved_anchors": [[river_id, FACT_A, EDITED_A]],
                "original_question": QUESTION_1,
                "original_answer_guidance": GUIDANCE_1}
            self.assertEqual(
                self.question(snapshot, "slot-remember")["status"],
                "approved")
            self.assertEqual(
                self.by_question_id(self.preview(client)),
                {q1_id: restored_q1_preview,
                 q2_id: baseline_q2_preview})

            # Step 4: pure display movement down/up. It changes no
            # source/settings revision and revokes no approval.
            before_moves = snapshot
            revisions = (snapshot["source_revision"],
                         snapshot["settings_revision"])
            preview_before_moves = self.preview(client)
            moved = client.post(
                "/api/session/regions/"
                + quote("missing-region", safe="") + "/move-down",
                json={}, headers=headers)
            self.assertEqual(moved.status_code, 404, moved.text)
            self.assertEqual(
                moved.json(), {"detail": "region not found"})
            self.assertEqual(self.snapshot(client), before_moves)
            river_url = ("/api/session/regions/"
                         + quote(river_id, safe=""))
            snapshot = self.command_ok(
                client, headers, river_url + "/move-down")
            self.assertEqual(
                [item["region_id"] for item in snapshot["regions"]],
                [trees_id, river_id])
            self.assertEqual(
                self.region(snapshot, "synthetic-section-trees")
                ["display_position"], 1)
            self.assertEqual(
                self.region(snapshot, "synthetic-section-river")
                ["display_position"], 2)
            self.assertEqual(
                (snapshot["source_revision"],
                 snapshot["settings_revision"]), revisions)
            self.assertEqual(
                self.question(snapshot, "slot-remember")["status"],
                "approved")
            self.assertEqual(
                self.question(snapshot, "slot-understand"), baseline_q2)
            self.assertEqual(self.preview(client), preview_before_moves)
            before_boundary = snapshot
            snapshot = self.command_ok(
                client, headers, river_url + "/move-down")
            self.assertEqual(snapshot, before_boundary)
            snapshot = self.command_ok(
                client, headers, river_url + "/move-up")
            self.assertEqual(
                [item["region_id"] for item in snapshot["regions"]],
                [river_id, trees_id])
            self.assertEqual(
                self.region(snapshot, "synthetic-section-river")
                ["display_position"], 1)
            self.assertEqual(
                self.region(snapshot, "synthetic-section-trees")
                ["display_position"], 2)
            self.assertEqual(
                (snapshot["source_revision"],
                 snapshot["settings_revision"]), revisions)
            self.assertEqual(self.preview(client), preview_before_moves)
            before_boundary = snapshot
            snapshot = self.command_ok(
                client, headers, river_url + "/move-up")
            self.assertEqual(snapshot, before_boundary)

            # Step 5: every refusal is atomic. Each attempt carries
            # a valid wording change that must not be published.
            before_refusals = snapshot
            attempts = (
                ("valid wording plus invalid Bloom level",
                 {"question": REFUSAL_WORDING,
                  "bloom_level": "synthetic-invalid-level"}),
                ("valid wording plus malformed assumptions",
                 {"question": REFUSAL_WORDING,
                  "hypothetical_assumptions": "  افْتِرَاضٌ وَاحِدٌ  "}),
                ("raw original question",
                 {"question": REFUSAL_WORDING,
                  "original_question": "  نَصٌّ أَصْلِيٌّ مُزَوَّرٌ؟  "}),
                ("generation grade",
                 {"question": REFUSAL_WORDING,
                  "generation_grade": "  الصَّفُّ ٩٩  "}),
                ("source anchors",
                 {"question": REFUSAL_WORDING,
                  "anchors": [[trees_id, FACT_B]]}),
                ("supporting regions",
                 {"question": REFUSAL_WORDING,
                  "supporting_region_ids": [trees_id]}),
                ("model level",
                 {"question": REFUSAL_WORDING,
                  "model_level": "create"}),
                ("question author",
                 {"question": REFUSAL_WORDING,
                  "question_author": "model"}),
                ("field authors",
                 {"question": REFUSAL_WORDING,
                  "field_authors": [["question", "model"]]}),
                ("approval status",
                 {"question": REFUSAL_WORDING,
                  "status": "approved"}),
                ("nested source support rebind",
                 {"question": REFUSAL_WORDING,
                  "evaluate_criteria": [{
                      "text": "  مِعْيَارٌ مُسْتَنِدٌ إِلَى النَّصِّ  ",
                      "origin": "source",
                      "supporting_facts": [{
                          "region_id": trees_id, "quote": FACT_B}]}]}),
            )
            for label, payload in attempts:
                with self.subTest(attempt=label):
                    refused = self.patch_question(
                        client, headers, q1_id, payload)
                    self.assertEqual(
                        refused.status_code, 400, refused.text)
                    self.assertEqual(
                        refused.json(), {"detail": "invalid request"})
                    self.assertEqual(
                        self.snapshot(client), before_refusals)
            refused = self.patch_question(
                client, headers, "missing-question",
                {"question": REFUSAL_WORDING})
            self.assertEqual(refused.status_code, 404, refused.text)
            self.assertEqual(
                refused.json(), {"detail": "question not found"})
            self.assertEqual(self.snapshot(client), before_refusals)
            self.assertEqual(self.preview(client), preview_before_moves)

            # Step 6: change the river region's current text through
            # the existing route, preserving its exact quote. Only
            # the dependent question becomes outdated.
            snapshot = self.put_region_text(
                client, headers,
                self.region(snapshot, "synthetic-section-river"),
                CHANGED_A)
            river = self.region(snapshot, "synthetic-section-river")
            self.assertEqual(river["raw_text"], RAW_A)
            self.assertEqual(river["current_text"], CHANGED_A)
            self.assertEqual(river["text_author"], "teacher")
            self.assertEqual(river["inclusion"], "included")
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["status"], "outdated")
            self.assertEqual(q1["blocks"], [])
            self.assertEqual(q1["anchors"], [[river_id, FACT_A]])
            self.assertEqual(q1["original_question"], QUESTION_1)
            self.assertEqual(
                self.question(snapshot, "slot-understand"), baseline_q2)
            self.assertEqual(self.preview(client), [baseline_q2_preview])

            # A fresh valid explicit approval restores only the
            # reviewed dependent question, against the changed text.
            snapshot = self.approve(client, headers, q1_id)
            changed_q1_preview = {
                "question_id": q1_id, "question": REJECTED_EDIT_Q1,
                "bloom_level": "remember", "author": "teacher",
                "answer_guidance": EDITED_GUIDANCE_1,
                "resolved_anchors": [[river_id, FACT_A, CHANGED_A]],
                "original_question": QUESTION_1,
                "original_answer_guidance": GUIDANCE_1}
            self.assertEqual(
                self.by_question_id(self.preview(client)),
                {q1_id: changed_q1_preview,
                 q2_id: baseline_q2_preview})

            # A real change away and back never resurrects approval
            # by itself: back at the just-approved text, only a new
            # explicit approval restores the dependent question.
            snapshot = self.put_region_text(
                client, headers,
                self.region(snapshot, "synthetic-section-river"),
                EDITED_A)
            self.assertEqual(
                self.question(snapshot, "slot-remember")["status"],
                "outdated")
            self.assertEqual(self.preview(client), [baseline_q2_preview])
            snapshot = self.put_region_text(
                client, headers,
                self.region(snapshot, "synthetic-section-river"),
                CHANGED_A)
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["status"], "outdated")
            self.assertEqual(q1["blocks"], [])
            self.assertEqual(
                self.question(snapshot, "slot-understand"), baseline_q2)
            self.assertEqual(self.preview(client), [baseline_q2_preview])
            snapshot = self.approve(client, headers, q1_id)

            # Final boundary state: every original is preserved and
            # no control operation invoked the provider again.
            snapshot = self.snapshot(client)
            self.assertEqual(
                [item["region_id"] for item in snapshot["regions"]],
                [river_id, trees_id])
            river = self.region(snapshot, "synthetic-section-river")
            trees = self.region(snapshot, "synthetic-section-trees")
            self.assertEqual(river["raw_text"], RAW_A)
            self.assertEqual(river["current_text"], CHANGED_A)
            self.assertEqual(trees["raw_text"], RAW_B)
            self.assertEqual(trees["current_text"], EDITED_B)
            q1 = self.question(snapshot, "slot-remember")
            self.assertEqual(q1["status"], "approved")
            self.assertEqual(q1["current_question"], REJECTED_EDIT_Q1)
            self.assertEqual(q1["current_answer_guidance"],
                             EDITED_GUIDANCE_1)
            self.assertEqual(q1["current_cognitive_explanation"],
                             EDITED_COGNITIVE_1)
            self.assertEqual(q1["original_question"], QUESTION_1)
            self.assertEqual(q1["original_answer_guidance"], GUIDANCE_1)
            self.assertEqual(q1["original_cognitive_explanation"],
                             COGNITIVE_1)
            self.assertEqual(q1["anchors"], [[river_id, FACT_A]])
            self.assertEqual(
                self.question(snapshot, "slot-understand"), baseline_q2)
            self.assertEqual(
                self.by_question_id(self.preview(client)),
                {q1_id: changed_q1_preview,
                 q2_id: baseline_q2_preview})
            self.assertEqual(snapshot["preview_items"],
                             self.preview(client))
            self.assertEqual(
                self.raw_result(client, source_request_id, "source"),
                baseline_source_raw)
            self.assertEqual(
                baseline_source_raw,
                provider.source_results[source_request_id])
            self.assertEqual(
                self.raw_result(client, question_request_id, "questions"),
                baseline_question_raw)
            self.assertEqual(
                baseline_question_raw,
                provider.question_results[question_request_id])
            self.assertEqual(len(provider.requests), 2)
            self.assertEqual(len(snapshot["requests"]), 2)


if __name__ == "__main__":
    unittest.main()
