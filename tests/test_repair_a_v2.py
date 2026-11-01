"""Retained tests for repair-A v2: complete criterion/rubric source
dependencies (QC-01), immutable nested source support (IR-A-01), and
atomic owned structured edits (QC-02/QC-03).

Synthetic fixtures only. No model API, no network, no real school
pages. Stdlib unittest only.
"""
import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from source_contract import SourcePage  # noqa: E402
from teacher_demo import (  # noqa: E402
    DecodedSourcePage, TeacherDemoError, TeacherWorkspace)
from teacher_demo.adapter import CriterionView  # noqa: E402

GRADE = "الرابع"
OBJECTIVE = "أن يميّز الطالب بين السبب والنتيجة في النص"
T1 = "دَرْسُ النَّهْرِ الصَّغِيرِ"
T2 = "يَجْرِي النَّهْرُ بَيْنَ الْحُقُولِ فِي الصَّبَاحِ.\nوَيَسْقِي الْمَاءُ ١٢٣ شَجَرَةً."
T3 = "وَيَتَبَخَّرُ الْمَاءُ عِنْدَ التَّسْخِينِ ثُمَّ يَتَكَاثَفُ عَلَى سَطْحٍ بَارِدٍ."
Q_A = "دَرْسُ النَّهْرِ"
Q_B1 = "يَجْرِي النَّهْرُ"
Q_B2 = "١٢٣ شَجَرَةً"
Q_C = "يَتَبَخَّرُ الْمَاءُ"


def make_page():
    return DecodedSourcePage(SourcePage(
        source_id="synthetic-teacher-page-1",
        source_sha256="a" * 64, page_index=0, render_sha256="b" * 64,
        width=1000, height=1400, rights_ref="synthetic-fixture-rights-note",
        coordinate_convention="normalized_0_1000_top_left"))


def raw_source():
    return {"sections": [
        {"section_id": "s-a", "kind": "title", "text": T1,
         "review_reasons": []},
        {"section_id": "s-b", "kind": "body", "text": T2,
         "review_reasons": []},
        {"section_id": "s-c", "kind": "body", "text": T3,
         "review_reasons": []}],
        "page_continues": False, "review_notes": []}


def fact(region_id, quote):
    return {"region_id": region_id, "quote": quote}


def source_criterion(region_id, *quotes, text="مِعْيَارٌ مِنَ الْمَصْدَرِ"):
    return {"text": text, "origin": "source",
            "supporting_facts": [fact(region_id, q) for q in quotes]}


def objective_criterion(text="مِعْيَارٌ مِنَ الْهَدَفِ"):
    return {"text": text, "origin": "objective", "supporting_facts": []}


def item(slot, level, region, quote, **updates):
    value = {"slot_id": slot, "level": level, "status": "draft",
             "question": "سُؤَالٌ؟ " + slot,
             "answer_guidance": "إِرْشَادٌ مُسْتَنْتَجٌ.",
             "cognitive_explanation": "مَهَمَّةٌ تُنَاسِبُ الصَّفَّ.",
             "hypothetical_assumptions": [], "evaluate_criteria": [],
             "create_rubric": [], "evidence": [fact(region, quote)],
             "required_order_relationships": [], "abstain_reason": None}
    value.update(updates)
    return value


def setup(field="evaluate_criteria", two_fact_group=False):
    """QA depends on B only through a source criterion/rubric fact.

    Ordinary evidence uses A; independent QC and rejected QR use C.
    Returns (ws, ids, (qa, qc, qr), structured_value_as_admitted).
    """
    ws = TeacherWorkspace(make_page(), grade=GRADE, objective=OBJECTIVE)
    rid = ws.begin_source_request("src-req-1")
    ids = list(ws.admit_source_proposal(rid, raw_source()))
    ws.finish_source_request(rid, "ok")
    for region_id in ids:
        ws.set_region_inclusion(region_id, "included")
    quotes = (Q_B1, Q_B2) if two_fact_group else (Q_B1,)
    structured = [source_criterion(ids[1], *quotes), objective_criterion()]
    if field == "create_rubric":
        structured.append(objective_criterion("مِعْيَارٌ هَدَفِيٌّ ثَانٍ"))
        level = "create"
    else:
        level = "evaluate"
    slots = [{"slot_id": "QA", "level": level},
             {"slot_id": "QC", "level": "understand"},
             {"slot_id": "QR", "level": "remember"}]
    qid = ws.begin_question_request("q-req-1", requested_slots=slots)
    qa, qc, qr = ws.admit_question_proposal(qid, {"items": [
        item("QA", level, ids[0], Q_A, **{field: copy.deepcopy(structured)}),
        item("QC", "understand", ids[2], Q_C),
        item("QR", "remember", ids[2], Q_C)]})
    ws.finish_question_request(qid, "ok")
    ws.approve_question(qa)
    ws.approve_question(qc)
    ws.reject_question(qr)
    return ws, ids, (qa, qc, qr), structured


def qview(ws, question_id):
    return {q.question_id: q for q in ws.snapshot().questions}[question_id]


def preview_ids(ws):
    return tuple(p.question_id for p in ws.snapshot().preview_items)


class CriterionDependencyTests(unittest.TestCase):
    def test_criterion_only_region_linked_and_blocked_when_excluded(self):
        ws, ids, (qa, qc, _), _ = setup()
        regions = {r.region_id: r for r in ws.snapshot().regions}
        self.assertIn(qa, regions[ids[1]].linked_question_ids)
        self.assertNotIn(qc, regions[ids[1]].linked_question_ids)
        self.assertIn(ids[1], qview(ws, qa).supporting_region_ids)
        ws.set_region_inclusion(ids[1], "excluded")
        self.assertEqual(qview(ws, qa).status, "outdated")
        self.assertIn("dependency_not_included", qview(ws, qa).blocks)
        self.assertEqual(preview_ids(ws), (qc,))

    def test_criterion_only_changes_outdate_dependent_only_no_resurrection(self):
        for kind in ("label", "text", "inclusion"):
            with self.subTest(kind=kind):
                ws, ids, (qa, qc, qr), _ = setup()
                # Identical edits and display moves invalidate nothing.
                ws.edit_region_label(ids[1], "body")
                ws.edit_region_text(ids[1], T2)
                ws.set_region_inclusion(ids[1], "included")
                self.assertTrue(ws.move_region_down(ids[1]))
                self.assertTrue(ws.move_region_up(ids[1]))
                self.assertEqual(set(preview_ids(ws)), {qa, qc})
                if kind == "label":
                    ws.edit_region_label(ids[1], "title")
                elif kind == "text":
                    ws.edit_region_text(ids[1], T2 + " زِيَادَةٌ.")
                else:
                    ws.set_region_inclusion(ids[1], "unresolved")
                self.assertEqual(qview(ws, qa).status, "outdated")
                self.assertEqual(qview(ws, qc).status, "approved")
                self.assertEqual(qview(ws, qr).status, "rejected")
                self.assertEqual(preview_ids(ws), (qc,))
                if kind == "label":
                    ws.edit_region_label(ids[1], "body")
                elif kind == "text":
                    ws.edit_region_text(ids[1], T2)
                else:
                    ws.set_region_inclusion(ids[1], "included")
                # Change-back never resurrects; explicit approval does.
                self.assertEqual(preview_ids(ws), (qc,))
                ws.approve_question(qa)
                self.assertEqual(set(preview_ids(ws)), {qa, qc})

    def test_rubric_only_region_changeback_cannot_resurrect(self):
        ws, ids, (qa, qc, _), _ = setup(field="create_rubric")
        regions = {r.region_id: r for r in ws.snapshot().regions}
        self.assertIn(qa, regions[ids[1]].linked_question_ids)
        ws.set_region_inclusion(ids[1], "excluded")
        self.assertEqual(preview_ids(ws), (qc,))
        ws.set_region_inclusion(ids[1], "included")
        self.assertEqual(preview_ids(ws), (qc,))
        ws.approve_question(qa)
        self.assertEqual(set(preview_ids(ws)), {qa, qc})


class ImmutableSourceSupportTests(unittest.TestCase):
    def test_wording_objective_and_reorder_edits_preserve_support(self):
        ws, ids, (qa, qc, qr), structured = setup()
        support_before = qview(ws, qa).evaluate_criteria[0].supporting_facts
        edited = copy.deepcopy(structured)
        edited[0]["text"] = "صِيَاغَةُ الْمُعَلِّمِ بِالدَّلِيلِ نَفْسِهِ"
        edited[1]["text"] = "مِعْيَارٌ هَدَفِيٌّ مُرَاجَعٌ"
        edited = [edited[1], edited[0]]  # reorder: same support groups
        edited.append(objective_criterion("مِعْيَارٌ هَدَفِيٌّ مُضَافٌ"))
        raw_before = ws.raw_question_output("q-req-1")
        ws.edit_question(qa, evaluate_criteria=edited)
        view = qview(ws, qa)
        self.assertEqual(view.status, "draft")
        source_views = [c for c in view.evaluate_criteria
                        if c.origin == "source"]
        self.assertEqual(len(source_views), 1)
        self.assertEqual(source_views[0].supporting_facts, support_before)
        self.assertEqual(source_views[0].text,
                         "صِيَاغَةُ الْمُعَلِّمِ بِالدَّلِيلِ نَفْسِهِ")
        self.assertEqual(dict(view.field_authors)["evaluate_criteria"],
                         "teacher")
        ws.approve_question(qa)
        self.assertIn(qa, preview_ids(ws))
        self.assertEqual(qview(ws, qr).status, "rejected")
        self.assertEqual(ws.raw_question_output("q-req-1"), raw_before)
        # Objective removal (within approveable shape) is also allowed.
        trimmed = [c for c in edited if c["origin"] == "source"]
        ws.edit_question(qa, evaluate_criteria=trimmed)
        ws.approve_question(qa)
        self.assertIn(qa, preview_ids(ws))
        self.assertIn(qc, preview_ids(ws))

    def test_source_support_rebinding_refused_atomically_mixed_field(self):
        ops = {
            "replace_region": lambda s, ids: [
                source_criterion(ids[2], Q_C), objective_criterion()],
            "replace_quote": lambda s, ids: [
                source_criterion(ids[1], Q_B2), objective_criterion()],
            "drop_source": lambda s, ids: [objective_criterion()],
            "relabel_objective": lambda s, ids: [
                objective_criterion(), objective_criterion()],
            "append_fact": lambda s, ids: [
                source_criterion(ids[1], Q_B1, Q_B2), objective_criterion()],
            "append_source": lambda s, ids: list(s) + [
                source_criterion(ids[2], Q_C, text="جَدِيدٌ")],
            "duplicate_source": lambda s, ids: list(s) + [copy.deepcopy(s[0])],
        }
        for name, make in ops.items():
            with self.subTest(operation=name):
                ws, ids, (qa, qc, qr), structured = setup()
                before = ws.snapshot()
                raw_before = ws.raw_question_output("q-req-1")
                with self.assertRaises(TeacherDemoError):
                    ws.edit_question(
                        qa, question="سُؤَالٌ صَالِحٌ لَا يَجِبُ أَنْ يَصِلَ؟",
                        evaluate_criteria=make(structured, ids))
                # Nothing landed: not even the valid question edit.
                self.assertEqual(ws.snapshot(), before)
                self.assertEqual(ws.raw_question_output("q-req-1"), raw_before)
                self.assertIn(qa, preview_ids(ws))
                self.assertIn(qc, preview_ids(ws))
                self.assertEqual(qview(ws, qr).status, "rejected")

    def test_splitting_a_support_group_is_refused(self):
        ws, ids, (qa, _, _), structured = setup(two_fact_group=True)
        before = ws.snapshot()
        split = [source_criterion(ids[1], Q_B1),
                 source_criterion(ids[1], Q_B2, text="ثَانٍ"),
                 objective_criterion()]
        with self.assertRaises(TeacherDemoError):
            ws.edit_question(qa, evaluate_criteria=split)
        self.assertEqual(ws.snapshot(), before)

    def test_source_support_cannot_move_between_fields(self):
        ws, ids, (qa, qc, _), structured = setup()
        before = ws.snapshot()
        with self.assertRaises(TeacherDemoError):
            ws.edit_question(qa, evaluate_criteria=structured[1:],
                             create_rubric=structured)
        self.assertEqual(ws.snapshot(), before)
        self.assertEqual(set(preview_ids(ws)), {qa, qc})

    def test_broken_support_wording_cannot_heal_new_generation_can(self):
        ws, ids, (qa, qc, qr), structured = setup()
        raw_before = ws.raw_question_output("q-req-1")
        ws.edit_region_text(ids[1], "نَصٌّ بَدِيلٌ بِلَا الدَّلِيلِ الْمُعْتَمَدِ")
        support_before = qview(ws, qa).evaluate_criteria[0].supporting_facts
        edited = copy.deepcopy(structured)
        edited[0]["text"] = "صِيَاغَةٌ جَدِيدَةٌ لَيْسَتْ دَلِيلًا جَدِيدًا"
        ws.edit_question(qa, evaluate_criteria=edited)  # wording is allowed
        with self.assertRaises(TeacherDemoError):
            ws.approve_question(qa)
        self.assertNotIn(qa, preview_ids(ws))
        self.assertEqual(
            qview(ws, qa).evaluate_criteria[0].supporting_facts,
            support_before)
        fresh_request = ws.begin_question_request(
            "q-req-2", requested_slots=[{"slot_id": "QA", "level": "evaluate"}])
        regrounded = [source_criterion(ids[2], Q_C), objective_criterion()]
        (fresh,) = ws.admit_question_proposal(
            fresh_request,
            {"items": [item("QA", "evaluate", ids[0], Q_A,
                            evaluate_criteria=regrounded)]})
        ws.finish_question_request(fresh_request, "ok")
        self.assertNotEqual(fresh, qa)
        self.assertEqual(qview(ws, fresh).status, "draft")
        ws.approve_question(fresh)
        self.assertEqual(set(preview_ids(ws)), {fresh, qc})
        self.assertEqual(qview(ws, qr).status, "rejected")
        self.assertEqual(ws.raw_question_output("q-req-1"), raw_before)


class AtomicStructuredEditTests(unittest.TestCase):
    def test_malformed_structured_edits_refused_atomically(self):
        bad_values = {
            "text_int": [{"text": 42, "origin": "objective",
                          "supporting_facts": []}],
            "quote_list": [source_criterion("x", ["not-a-string"])],
            "blank_quote": [source_criterion("x", "")],
            "whitespace_quote": [source_criterion("x", "   ")],
            "blank_region": [source_criterion(" ", Q_B1)],
            "objective_with_facts": [{"text": "t", "origin": "objective",
                                      "supporting_facts": [fact("x", "y")]}],
            "bad_origin": [{"text": "t", "origin": "guess",
                            "supporting_facts": []}],
            "missing_key": [{"text": "t", "origin": "objective"}],
            "extra_key": [{"text": "t", "origin": "objective",
                           "supporting_facts": [], "surprise": 1}],
            "entry_string": ["not-a-criterion"],
            "source_no_facts": [{"text": "t", "origin": "source",
                                 "supporting_facts": []}],
            "container_string": "not-a-container",
        }
        for name, bad in bad_values.items():
            with self.subTest(value=name):
                ws, ids, (qa, qc, _), _ = setup()
                # The quote/region cases need a real region id to reach
                # the same refusal for the right reason; shape refusal
                # does not depend on it, so keep the values as built.
                before = ws.snapshot()
                with self.assertRaises(TeacherDemoError):
                    ws.edit_question(
                        qa, question="سُؤَالٌ صَالِحٌ لَا يَجِبُ أَنْ يَصِلَ؟",
                        evaluate_criteria=bad)
                self.assertEqual(ws.snapshot(), before)  # reads still work
                self.assertEqual(set(preview_ids(ws)), {qa, qc})

    def test_malformed_assumptions_refused_atomically(self):
        for bad in ("plain-string", [42], ["   "], [["nested"]], 7):
            with self.subTest(value=repr(bad)):
                ws, ids, (qa, qc, _), _ = setup()
                before = ws.snapshot()
                with self.assertRaises(TeacherDemoError):
                    ws.edit_question(
                        qa, answer_guidance="إِرْشَادٌ صَالِحٌ.",
                        hypothetical_assumptions=bad)
                self.assertEqual(ws.snapshot(), before)
                self.assertIn(qc, preview_ids(ws))

    def test_list_owned_criterion_view_input_does_not_alias(self):
        ws, ids, (qa, _, _), _ = setup()
        facts = [[ids[1], Q_B1]]
        value = CriterionView(text="صِيَاغَةُ الْمُعَلِّمِ", origin="source",
                              supporting_facts=facts)
        caller_list = [value, CriterionView(
            text="هَدَفِيٌّ", origin="objective", supporting_facts=[])]
        ws.edit_question(qa, evaluate_criteria=caller_list)
        ws.approve_question(qa)
        old = ws.snapshot()
        facts[0][1] = Q_B2          # mutate caller-owned nested list
        caller_list.append(value)   # and the outer caller list
        self.assertEqual(ws.snapshot(), old)
        self.assertEqual(
            qview(ws, qa).evaluate_criteria[0].supporting_facts,
            ((ids[1], Q_B1),))
        self.assertIn(qa, preview_ids(ws))

    def test_dict_and_assumption_inputs_are_owned_after_edit(self):
        ws, ids, (qa, _, _), structured = setup()
        edited = copy.deepcopy(structured)
        edited[0]["text"] = "صِيَاغَةٌ أُخْرَى"
        assumptions = ["افْتِرَاضٌ مَطْرُوحٌ لِلتَّفْكِيرِ."]
        ws.edit_question(qa, evaluate_criteria=edited,
                         hypothetical_assumptions=assumptions)
        after_edit = ws.snapshot()
        edited[0]["supporting_facts"][0]["quote"] = Q_B2
        assumptions.append("افْتِرَاضٌ مُتَأَخِّرٌ.")
        self.assertEqual(ws.snapshot(), after_edit)
        self.assertEqual(qview(ws, qa).hypothetical_assumptions,
                         ("افْتِرَاضٌ مَطْرُوحٌ لِلتَّفْكِيرِ.",))

    def test_same_value_structured_edit_is_noop(self):
        ws, ids, (qa, _, _), structured = setup()
        before = ws.snapshot()
        # Round-trip the snapshot's own immutable views unchanged.
        ws.edit_question(qa,
                         evaluate_criteria=qview(ws, qa).evaluate_criteria,
                         hypothetical_assumptions=[])
        # And the equal caller-dict shape.
        ws.edit_question(qa, evaluate_criteria=copy.deepcopy(structured))
        self.assertEqual(ws.snapshot(), before)
        self.assertEqual(qview(ws, qa).status, "approved")
        self.assertEqual(
            dict(qview(ws, qa).field_authors)["evaluate_criteria"], "model")


if __name__ == "__main__":
    unittest.main()
