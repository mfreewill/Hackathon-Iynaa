"""One real-HTTP teacher journey tracer.

Existing session/setup/Start runs first. The source-analysis route ``POST /api/session/source-analysis``
is exercised below.

Teacher API used by this tracer:
- ``POST /api/session/source-analysis`` with exact
  {request_id, source_id} -> 201 {request_id, snapshot}
- ``GET /api/session/snapshot`` -> snapshot projection
- ``GET /api/session/requests/{request_id}/raw`` ->
  {request_id, kind, provider_result}
- ``PUT /api/session/regions/{region_id}`` with exact
  {inclusion, current_text, current_label} -> 200 {snapshot}
- ``POST /api/session/questions/generate`` with exact
  {request_id, source_id} -> 201 {request_id, snapshot}
- ``POST /api/session/questions/{question_id}/approve`` ->
  200 {snapshot}
- ``GET /api/session/preview`` -> {items}

The synthetic provider below exists only in this test and is
injected only as ``create_app(provider=...)``. It is callable with
one immutable request mapping. Source requests carry
kind/request_id/source+render identity/media type/png_bytes/
grade/objective; question requests carry kind/request_id/source_id,
the real domain prompt, reviewed_regions, requested_slots, and
grade/objective. Results are
{raw, model_name, response_id, outcome}; raw is the exact source
proposal or teacher-question/v2 ``{"items": [...]}`` object.
"""
import copy
import hashlib
import io
import pathlib
import secrets
import sys
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
RAW_TEXT = "  اَلْمَاءُ يَجْرِي فِي النَّهْرِ ١٢٣  "
FACT = "اَلْمَاءُ يَجْرِي بِسُرْعَةٍ"
EDITED_TEXT = f"  {FACT} فِي النَّهْرِ ١٢٣  "
CHANGED_TEXT = "  تَغَيَّرَ النَّصُّ بَعْدَ الاِعْتِمَادِ ٤٥٦  "
QUESTION_TEXT = "  مَاذَا يَجْرِي فِي النَّهْرِ؟  "
GUIDANCE = "يُشِيرُ النَّصُّ الْمُرَاجَعُ إِلَى جَرَيَانِ الْمَاءِ بِسُرْعَةٍ."

SOURCE_RESULT = {
    "raw": {
        "sections": [{
            "section_id": "synthetic-section-1",
            "kind": "body",
            "text": RAW_TEXT,
            "review_reasons": ["synthetic-flag"],
        }],
        "page_continues": False,
        "review_notes": ["synthetic note"],
    },
    "model_name": SYNTHETIC_MODEL,
    "response_id": "synthetic-source-response-1",
    "outcome": "ok",
}


class SyntheticProvider:
    """Deterministic in-process test provider; never a default."""

    def __init__(self):
        self.requests = []
        self.question_result = None

    def __call__(self, request):
        self.requests.append(copy.deepcopy(dict(request)))
        if request["kind"] == "source_analysis":
            assert request["grade"] == GRADE
            assert request["objective"] == OBJECTIVE
            assert request["media_type"] == "image/png"
            assert hashlib.sha256(
                request["png_bytes"]).hexdigest() == (
                    request["render_sha256"])
            return copy.deepcopy(SOURCE_RESULT)
        assert request["kind"] == "question_generation"
        reviewed = dict(request["reviewed_regions"])
        slots = [dict(slot) for slot in request["requested_slots"]]
        assert list(reviewed.values()) == [EDITED_TEXT]
        assert request["grade"] == GRADE
        assert request["objective"] == OBJECTIVE
        assert request["prompt"] == question_prompt_v2(
            reviewed, slots, grade=GRADE, objective=OBJECTIVE)
        region_id = next(iter(reviewed))
        items = []
        for slot in slots:
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
                        {"region_id": region_id, "quote": FACT}],
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
            "model_name": SYNTHETIC_MODEL,
            "response_id": "synthetic-question-response-1",
            "outcome": "ok",
        }
        return copy.deepcopy(self.question_result)


def operation_command(source_id):
    return {"request_id": secrets.token_hex(16),
            "source_id": source_id}


def synthetic_png():
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (200, 30, 40, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


class HttpTeacherFlowTracerTests(unittest.TestCase):
    def test_teacher_review_generation_approval_journey(self):
        provider = SyntheticProvider()
        original = synthetic_png()
        with TestClient(create_app(provider=provider),
                        base_url=BASE_URL) as client:
            created = client.post(
                "/api/sessions", json={},
                headers={"Origin": BASE_URL})
            self.assertEqual(created.status_code, 201, created.text)
            csrf = created.json()["csrf_token"]
            headers = {"Origin": BASE_URL, "X-CSRF-Token": csrf}
            settings = client.put(
                "/api/session/settings",
                json={"grade": GRADE, "objective": OBJECTIVE,
                      "rights_ref": RIGHTS_REF}, headers=headers)
            self.assertEqual(settings.status_code, 200, settings.text)
            started = client.post(
                "/api/session/start", params={"page_index": 0},
                content=original,
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
            fetched = client.get("/api/session/snapshot")
            self.assertEqual(fetched.status_code, 200, fetched.text)
            self.assertEqual(fetched.json(), snapshot)
            self.assertEqual(snapshot["source_id"], page["source_id"])
            self.assertEqual(snapshot["grade"], GRADE)
            self.assertEqual(snapshot["objective"], OBJECTIVE)
            self.assertTrue(snapshot["source_analysis_terminal"])
            self.assertFalse(snapshot["can_generate"])
            source_request = next(
                item for item in snapshot["requests"]
                if item["request_id"] == source_request_id)
            self.assertEqual(source_request["kind"], "source")
            self.assertEqual(source_request["state"], "terminal")
            self.assertEqual(source_request["outcome"], "ok")
            self.assertEqual(len(snapshot["regions"]), 1)
            region = snapshot["regions"][0]
            self.assertEqual(region["request_id"], source_request_id)
            self.assertEqual(region["external_section_id"],
                             "synthetic-section-1")
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
            raw_source = client.get(
                "/api/session/requests/"
                f"{quote(source_request_id, safe='')}/raw")
            self.assertEqual(raw_source.status_code, 200,
                             raw_source.text)
            self.assertEqual(raw_source.json()["kind"], "source")
            self.assertEqual(
                raw_source.json()["provider_result"], SOURCE_RESULT)

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

            question_command = operation_command(
                snapshot["source_id"])
            generated = client.post(
                "/api/session/questions/generate",
                json=question_command, headers=headers)
            self.assertEqual(generated.status_code, 201,
                             generated.text)
            question_request_id = question_command["request_id"]
            self.assertEqual(generated.json()["request_id"],
                             question_request_id)
            snapshot = generated.json()["snapshot"]
            outcomes = [item for item in snapshot["slot_outcomes"]
                        if item["request_id"] == question_request_id]
            self.assertEqual(len(outcomes), 6)
            self.assertEqual(
                sorted(item["outcome"] for item in outcomes),
                ["abstain"] * 5 + ["draft"])
            draft_outcome = next(
                item for item in outcomes
                if item["outcome"] == "draft")
            self.assertEqual(draft_outcome["level"], "remember")
            self.assertTrue(all(
                item["abstain_reason"] == "synthetic abstention"
                for item in outcomes if item["outcome"] == "abstain"))
            self.assertEqual(len(snapshot["questions"]), 1)
            question = snapshot["questions"][0]
            question_id = question["question_id"]
            self.assertEqual(question_id,
                             draft_outcome["question_id"])
            self.assertEqual(question["status"], "draft")
            self.assertEqual(question["blocks"], [])
            self.assertEqual(question["current_question"],
                             QUESTION_TEXT)
            self.assertEqual(question["original_question"],
                             QUESTION_TEXT)
            self.assertEqual(question["anchors"],
                             [[region_id, FACT]])
            self.assertEqual(snapshot["preview_items"], [])
            raw_questions = client.get(
                "/api/session/requests/"
                f"{quote(question_request_id, safe='')}/raw")
            self.assertEqual(raw_questions.status_code, 200,
                             raw_questions.text)
            self.assertEqual(raw_questions.json()["kind"],
                             "questions")
            self.assertEqual(
                raw_questions.json()["provider_result"],
                provider.question_result)

            approved = client.post(
                "/api/session/questions/"
                f"{quote(question_id, safe='')}/approve",
                json={}, headers=headers)
            self.assertEqual(approved.status_code, 200, approved.text)
            snapshot = approved.json()["snapshot"]
            self.assertEqual(snapshot["questions"][0]["status"],
                             "approved")
            preview = client.get("/api/session/preview")
            self.assertEqual(preview.status_code, 200, preview.text)
            items = preview.json()["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["question_id"], question_id)
            self.assertEqual(items[0]["question"], QUESTION_TEXT)
            self.assertEqual(items[0]["original_question"],
                             QUESTION_TEXT)
            self.assertEqual(items[0]["resolved_anchors"],
                             [[region_id, FACT, EDITED_TEXT]])

            changed = client.put(
                region_url,
                json={"inclusion": "included",
                      "current_text": CHANGED_TEXT,
                      "current_label": region["current_label"]},
                headers=headers)
            self.assertEqual(changed.status_code, 200, changed.text)
            snapshot = changed.json()["snapshot"]
            self.assertEqual(snapshot["regions"][0]["raw_text"],
                             RAW_TEXT)
            self.assertEqual(snapshot["regions"][0]["current_text"],
                             CHANGED_TEXT)
            self.assertEqual(snapshot["questions"][0]["status"],
                             "outdated")
            self.assertEqual(snapshot["preview_items"], [])
            preview = client.get("/api/session/preview")
            self.assertEqual(preview.json()["items"], [])
            raw_source = client.get(
                "/api/session/requests/"
                f"{quote(source_request_id, safe='')}/raw")
            self.assertEqual(
                raw_source.json()["provider_result"], SOURCE_RESULT)
            source_provider_request = provider.requests[0]
            self.assertEqual(source_provider_request["source_id"],
                             page["source_id"])
            self.assertEqual(
                source_provider_request["source_sha256"],
                page["source_sha256"])
            self.assertEqual(
                source_provider_request["render_sha256"],
                page["render_sha256"])


if __name__ == "__main__":
    unittest.main()
