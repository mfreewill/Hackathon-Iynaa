"""Retained regression controls for inherited QC-04 / QC-05 behavior.

These are small self-contained controls. They exercise retained
A/B behavior only. Public adapter only; synthetic fixtures.

QC-04: the first accepted item of every requested slot (draft or
abstention) owns that slot identity. An exact repeat is idempotent;
any differing item refuses the whole proposal atomically, preserving
originals, teacher edits/decisions, and accepted identity data
against caller/readback mutation.

QC-05: preview authorship aggregates every per-field teacher edit.
Editing a non-question field makes the current preview version
teacher-authored while untouched fields keep their own authors;
approval alone and same-value no-op edits confer no authorship.
"""
import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from source_contract import SourcePage  # noqa: E402
from teacher_demo import (  # noqa: E402
    DecodedSourcePage,
    TeacherDemoError,
    TeacherWorkspace,
)

GRADE = "الخامس"
OBJECTIVE = "أن يقارن الطالب بين حالتين في النص"
TEXTS = ("تَسَاقَطَ الْمَطَرُ ٥ / 5 مَرَّاتٍ.",
         "امْتَلَأَ الْخَزَّانُ بِالْمَاءِ.")
ABSTAIN_REASON = "لا تكفي الوقائع لهذا المستوى."


def make_workspace():
    page = DecodedSourcePage(SourcePage(
        source_id="qc0405-synthetic-page", source_sha256="c" * 64,
        page_index=0, render_sha256="d" * 64, width=800, height=1200,
        rights_ref="synthetic local fixture",
        coordinate_convention="normalized_0_1000_top_left"))
    ws = TeacherWorkspace(page, grade=GRADE, objective=OBJECTIVE)
    rid = ws.begin_source_request("source")
    ids = ws.admit_source_proposal(rid, {
        "sections": [
            {"section_id": key, "kind": "body", "text": text,
             "review_reasons": []}
            for key, text in zip(("A", "B"), TEXTS)],
        "page_continues": False, "review_notes": []},
        model_name="synthetic-model")
    for region_id in ids:
        ws.set_region_inclusion(region_id, "included")
    ws.finish_source_request(rid, "ok")
    return ws, list(ids)


def item(slot, level, region=None, quote=None, status="draft", **updates):
    draft = status == "draft"
    value = {
        "slot_id": slot, "level": level, "status": status,
        "question": "ماذا حدث للماء؟ " + slot if draft else "",
        "answer_guidance": "استنتاج من الوقائع المقتبسة." if draft else "",
        "cognitive_explanation": "استرجاع حقيقة يناسب الصف." if draft else "",
        "hypothetical_assumptions": [], "evaluate_criteria": [],
        "create_rubric": [],
        "evidence": [{"region_id": region, "quote": quote}] if draft else [],
        "required_order_relationships": [],
        "abstain_reason": None if draft else ABSTAIN_REASON,
    }
    value.update(updates)
    return value


def admit(ws, items, finish=False):
    ws.begin_question_request("questions", requested_slots=[
        {"slot_id": i["slot_id"], "level": i["level"]} for i in items])
    ids = ws.admit_question_proposal("questions",
                                     {"items": copy.deepcopy(items)})
    if finish:
        ws.finish_question_request("questions", "ok")
    return ids


def retained(ws):
    return copy.deepcopy((ws.snapshot(), ws.raw_question_output("questions")))


class AcceptedSlotIdentityControls(unittest.TestCase):
    """QC-04 controls (inherited behavior, post-repair)."""

    def test_accepted_abstention_identity_is_complete(self):
        ws, ids = make_workspace()
        accepted = item("s", "remember", status="abstain")
        ws.begin_question_request(
            "questions",
            requested_slots=[{"slot_id": "s", "level": "remember"}])
        self.assertEqual(
            ws.admit_question_proposal("questions", {"items": [accepted]}),
            ())
        outcome, = ws.snapshot().slot_outcomes
        self.assertEqual((outcome.outcome, outcome.abstain_reason),
                         ("abstain", ABSTAIN_REASON))
        self.assertIsNone(outcome.question_id)
        before = retained(ws)
        # Exact repeat: idempotent no-op.
        self.assertEqual(
            ws.admit_question_proposal(
                "questions", {"items": [copy.deepcopy(accepted)]}), ())
        self.assertEqual(retained(ws), before)
        # Differing abstention reason: refused, nothing applied.
        with self.assertRaises(TeacherDemoError):
            ws.admit_question_proposal("questions", {"items": [
                item("s", "remember", status="abstain",
                     abstain_reason="سبب مختلف ٢")]})
        self.assertEqual(retained(ws), before)
        # Converting the accepted abstention into a draft: refused.
        with self.assertRaises(TeacherDemoError):
            ws.admit_question_proposal("questions", {"items": [
                item("s", "remember", ids[0], TEXTS[0])]})
        self.assertEqual(retained(ws), before)

    def test_multi_slot_conflict_refuses_atomically(self):
        ws, ids = make_workspace()
        accepted = [item("first", "remember", status="abstain"),
                    item("second", "understand", ids[1], TEXTS[1])]
        existing, = admit(ws, accepted)
        ws.approve_question(existing)
        before = retained(ws)
        conflict = [item("first", "remember", ids[0], TEXTS[0]),
                    item("second", "understand", status="abstain")]
        with self.assertRaises(TeacherDemoError):
            ws.admit_question_proposal("questions", {"items": conflict})
        # Earlier abstention not replaced; approval and originals kept.
        self.assertEqual(retained(ws), before)
        statuses = {q.question_id: q.status
                    for q in ws.snapshot().questions}
        self.assertEqual(statuses[existing], "approved")
        outcomes = {o.slot_id: o.outcome
                    for o in ws.snapshot().slot_outcomes}
        self.assertEqual(outcomes, {"first": "abstain", "second": "draft"})

    def test_identical_redelivery_after_edit_and_decision_is_noop(self):
        ws, ids = make_workspace()
        original = item("s", "remember", ids[0], TEXTS[0])
        caller_copy = copy.deepcopy(original)
        ws.begin_question_request(
            "questions",
            requested_slots=[{"slot_id": "s", "level": "remember"}])
        question, = ws.admit_question_proposal(
            "questions", {"items": [caller_copy]})
        ws.edit_question(question, answer_guidance="صياغة المعلم ١٢ / 12")
        ws.approve_question(question)
        before = retained(ws)
        # Caller/readback mutation must not reach accepted identity.
        caller_copy["answer_guidance"] = "تسريب خارجي"
        ws.raw_question_output("questions")["items"][0][
            "answer_guidance"] = "تسريب خارجي"
        self.assertEqual(retained(ws), before)
        # Exact repeat of the original: same ids, state untouched.
        self.assertEqual(
            ws.admit_question_proposal(
                "questions", {"items": [copy.deepcopy(original)]}),
            (question,))
        self.assertEqual(retained(ws), before)


class PreviewAuthorshipControls(unittest.TestCase):
    """QC-05 controls (inherited behavior, post-repair)."""

    def test_nonquestion_field_edit_credited_in_preview(self):
        cases = (
            ("answer_guidance", "إرشاد إجابة بصياغة المعلم ١٢ / 12"),
            ("cognitive_explanation", "تعليل المعلم لمهمة المقارنة."),
            ("hypothetical_assumptions", ["افتراض مطروح لا حقيقة مقتبسة"]),
        )
        for field, value in cases:
            with self.subTest(field=field):
                ws, ids = make_workspace()
                question, = admit(
                    ws, [item("s", "remember", ids[0], TEXTS[0])],
                    finish=True)
                ws.approve_question(question)
                approved = ws.snapshot()
                self.assertEqual(approved.preview_items[0].author, "model")
                ws.edit_question(question, **{field: value})
                edited = ws.snapshot()
                self.assertEqual(edited.preview_items, ())  # edit != approve
                authors = dict(edited.questions[0].field_authors)
                self.assertEqual(authors[field], "teacher")
                self.assertEqual(authors["question"], "model")
                ws.approve_question(question)
                preview = ws.snapshot().preview_items
                self.assertEqual(preview[0].author, "teacher")
                # Originals preserved for "view original" access.
                self.assertEqual(
                    preview[0].original_question,
                    approved.preview_items[0].original_question)
                self.assertEqual(
                    preview[0].original_answer_guidance,
                    approved.preview_items[0].original_answer_guidance)

    def test_approval_alone_and_noop_edit_keep_model_authorship(self):
        ws, ids = make_workspace()
        first = item("s", "remember", ids[0], TEXTS[0])
        question, = admit(ws, [first], finish=True)
        ws.approve_question(question)
        before = retained(ws)
        # Approval itself confers no teacher authorship.
        self.assertEqual(ws.snapshot().preview_items[0].author, "model")
        # Same-value edit: no-op for authorship, status, and approval.
        ws.edit_question(question, question=first["question"],
                         answer_guidance=first["answer_guidance"],
                         cognitive_explanation=first["cognitive_explanation"],
                         grade=GRADE, objective=OBJECTIVE, bloom_level="remember",
                         hypothetical_assumptions=[],
                         evaluate_criteria=[], create_rubric=[])
        self.assertEqual(retained(ws), before)
        self.assertEqual(ws.snapshot().preview_items[0].author, "model")
        self.assertEqual(ws.snapshot().questions[0].status, "approved")


if __name__ == "__main__":
    unittest.main()
