"""Teacher-domain adapter tests. Synthetic fixtures only.

No model API, no network, no real school pages. Stdlib unittest only.
All Arabic strings are original harmless inventions for this file.
"""
import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from source_contract import SourcePage  # noqa: E402
from teacher_demo import DecodedSourcePage, TeacherWorkspace  # noqa: E402

GRADE = "الرابع"
OBJECTIVE = "أن يميّز الطالب بين السبب والنتيجة في النص"
# Exact Unicode fixtures: tashkeel and Arabic-Indic digits must survive.
T1 = "دَرْسُ النَّهْرِ الصَّغِيرِ"
T2 = "يَجْرِي النَّهْرُ بَيْنَ الْحُقُولِ فِي الصَّبَاحِ.\nوَيَسْقِي الْمَاءُ ١٢٣ شَجَرَةً."
T3 = "وَيَتَبَخَّرُ الْمَاءُ عِنْدَ التَّسْخِينِ ثُمَّ يَتَكَاثَفُ عَلَى سَطْحٍ بَارِدٍ."


def make_page():
    return DecodedSourcePage(SourcePage(
        source_id="synthetic-teacher-page-1",
        source_sha256="a" * 64, page_index=0, render_sha256="b" * 64,
        width=1000, height=1400, rights_ref="synthetic-fixture-rights-note",
        coordinate_convention="normalized_0_1000_top_left"))


def raw_source():
    return {
        "sections": [
            {"section_id": "s-title", "kind": "title", "text": T1,
             "review_reasons": []},
            {"section_id": "s-body", "kind": "body", "text": T2,
             "review_reasons": ["كَلِمَةٌ بَاهِتَةٌ قُرْبَ السَّطْرِ"]},
            {"section_id": "s-more", "kind": "body", "text": T3,
             "review_reasons": []},
        ],
        "page_continues": False,
        "review_notes": [],
    }


def make_workspace():
    return TeacherWorkspace(make_page(), grade=GRADE, objective=OBJECTIVE)


def admitted_workspace():
    """Workspace with one finished source request; returns (ws, ids)."""
    ws = make_workspace()
    rid = ws.begin_source_request("src-req-1")
    ids = ws.admit_source_proposal(rid, raw_source(), model_name="synthetic-model-x")
    ws.finish_source_request(rid, "ok")
    return ws, list(ids)


class Cycle1SourceAdmissionTests(unittest.TestCase):
    def test_admission_namespaces_ids_full_page_extent_unreviewed(self):
        ws = make_workspace()
        rid = ws.begin_source_request("src-req-1")
        raw = raw_source()
        before = copy.deepcopy(raw)
        ids = ws.admit_source_proposal(rid, raw, model_name="synthetic-model-x")
        self.assertEqual(raw, before)  # caller input never mutated
        self.assertEqual(len(ids), 3)
        for region_id in ids:
            self.assertTrue(region_id.startswith("src-req-1:"))
        snap = ws.snapshot()
        self.assertEqual(len(snap.regions), 3)
        first = snap.regions[0]
        self.assertEqual(first.external_section_id, "s-title")
        self.assertEqual(first.inclusion, "unreviewed")
        self.assertEqual(first.raw_text, T1)
        self.assertEqual(first.current_text, T1)
        self.assertEqual(first.raw_label, "title")
        self.assertEqual(first.current_label, "title")
        self.assertEqual(first.text_author, "model")
        self.assertEqual(first.label_author, "model")
        # Decoder-owned full-page extent, explicitly not model geometry.
        self.assertEqual(tuple(first.source_box), (0.0, 0.0, 1000.0, 1000.0))
        self.assertIn("full_page", first.box_origin)
        self.assertIn("not_model_geometry", first.box_origin)
        self.assertEqual(first.page_link, "full_page")
        # Model flags preserved exactly; exact Unicode preserved.
        self.assertEqual(snap.regions[1].model_flags,
                         ("كَلِمَةٌ بَاهِتَةٌ قُرْبَ السَّطْرِ",))
        self.assertEqual(snap.regions[1].raw_text, T2)

    def test_raw_output_preserved_without_namespacing_rewrite(self):
        ws, _ = admitted_workspace()
        stored = ws.raw_source_output("src-req-1")
        self.assertEqual(stored, raw_source())
        self.assertEqual(stored["sections"][0]["section_id"], "s-title")

    def test_explicit_review_edits_and_authorship(self):
        ws, ids = admitted_workspace()
        r2 = ids[1]
        ws.set_region_inclusion(r2, "included")
        edited = T2 + " إِضَافَةُ الْمُعَلِّمِ."
        ws.edit_region_text(r2, edited)
        ws.edit_region_label(r2, "vocabulary")
        snap = ws.snapshot()
        by_id = {r.region_id: r for r in snap.regions}
        self.assertEqual(by_id[r2].inclusion, "included")
        self.assertEqual(by_id[r2].current_text, edited)
        self.assertEqual(by_id[r2].raw_text, T2)  # raw never overwritten
        self.assertEqual(by_id[r2].text_author, "teacher")
        self.assertEqual(by_id[r2].current_label, "vocabulary")
        self.assertEqual(by_id[r2].raw_label, "body")
        self.assertEqual(by_id[r2].label_author, "teacher")
        # Untouched region keeps model authorship on every field.
        self.assertEqual(by_id[ids[0]].text_author, "model")
        self.assertEqual(by_id[ids[0]].label_author, "model")
        # Core teacher text agrees with adapter current text.
        self.assertEqual(ws.core_workspace.get_reviewed_region(r2).teacher_text,
                         edited)

    def test_inclusion_states_and_no_manual_creation_surface(self):
        ws, ids = admitted_workspace()
        ws.set_region_inclusion(ids[0], "excluded")
        ws.set_region_inclusion(ids[2], "unresolved")
        by_id = {r.region_id: r for r in ws.snapshot().regions}
        self.assertEqual(by_id[ids[0]].inclusion, "excluded")
        self.assertEqual(by_id[ids[2]].inclusion, "unresolved")
        self.assertEqual(by_id[ids[1]].inclusion, "unreviewed")
        # No manual create-from-blank region/question path is exposed.
        self.assertFalse(hasattr(ws, "create_question"))
        self.assertFalse(hasattr(ws, "create_region"))


class Cycle2OrderTests(unittest.TestCase):
    def test_display_moves_reorder_without_source_revision_change(self):
        ws, ids = admitted_workspace()
        rev_before = ws.source_revision
        self.assertTrue(ws.move_region_down(ids[0]))
        order = [r.region_id for r in ws.snapshot().regions]
        self.assertEqual(order, [ids[1], ids[0], ids[2]])
        self.assertTrue(ws.move_region_up(ids[0]))
        self.assertEqual([r.region_id for r in ws.snapshot().regions], ids)
        # Ends are honest no-moves; display numbers follow position.
        self.assertFalse(ws.move_region_up(ids[0]))
        self.assertFalse(ws.move_region_down(ids[2]))
        self.assertEqual(ws.source_revision, rev_before)
        self.assertEqual(ws.snapshot().regions[1].display_position, 2)

    def test_required_precedence_actual_change_only(self):
        ws, ids = admitted_workspace()
        rev_before = ws.source_revision
        ws.set_required_precedence(ids[0], ids[1], True)
        self.assertEqual(ws.source_revision, rev_before + 1)
        ws.set_required_precedence(ids[0], ids[1], True)  # identical: no-op
        self.assertEqual(ws.source_revision, rev_before + 1)
        ws.set_required_precedence(ids[0], ids[1], False)
        self.assertEqual(ws.source_revision, rev_before + 2)

    def test_identical_text_label_inclusion_edits_are_noops(self):
        ws, ids = admitted_workspace()
        ws.set_region_inclusion(ids[1], "included")
        rev_before = ws.source_revision
        ws.edit_region_text(ids[1], T2)
        ws.edit_region_label(ids[1], "body")
        ws.set_region_inclusion(ids[1], "included")
        self.assertEqual(ws.source_revision, rev_before)


from teacher_demo import RequestStateError  # noqa: E402


class Cycle3RequestLifecycleTests(unittest.TestCase):
    def test_generation_requires_terminal_source_and_included_subset(self):
        ws = make_workspace()
        with self.assertRaises(RequestStateError):
            ws.begin_question_request("q-early")
        rid = ws.begin_source_request("src-w")
        ws.admit_source_proposal(rid, raw_source())
        snap = ws.snapshot()
        self.assertFalse(snap.can_generate)  # still working: moving target
        with self.assertRaises(RequestStateError):
            ws.begin_question_request("q-early-2")
        ws.finish_source_request(rid, "ok")
        # Terminal but nothing included yet: still cannot generate.
        self.assertTrue(ws.snapshot().source_analysis_terminal)
        self.assertFalse(ws.snapshot().can_generate)
        self.assertEqual(ws.snapshot().included_count, 0)
        self.assertEqual(ws.snapshot().withheld_count, 3)
        with self.assertRaises(RequestStateError):
            ws.begin_question_request("q-early-3")

    def test_cancel_preserves_admitted_and_late_result_refused(self):
        ws = make_workspace()
        rid = ws.begin_source_request("src-c")
        ids = ws.admit_source_proposal(rid, raw_source())
        ws.set_region_inclusion(ids[0], "included")
        ws.cancel_source_request(rid)
        snap = ws.snapshot()
        self.assertEqual(len(snap.regions), 3)  # same-request items kept
        by_id = {r.region_id: r for r in snap.regions}
        self.assertEqual(by_id[ids[0]].inclusion, "included")
        reqs = {r.request_id: r for r in snap.requests}
        self.assertEqual(reqs[rid].state, "terminal")
        self.assertEqual(reqs[rid].outcome, "cancelled")
        self.assertNotEqual(reqs[rid].outcome, "ok")
        with self.assertRaises(RequestStateError):
            ws.admit_source_proposal(rid, raw_source())

    def test_failure_outcomes_named_and_work_preserved(self):
        for outcome in ("incomplete", "invalid_schema", "empty", "error",
                        "refusal", "timeout", "unknown"):
            ws = make_workspace()
            rid = ws.begin_source_request(f"src-{outcome}")
            ws.admit_source_proposal(rid, raw_source())
            ws.finish_source_request(rid, outcome)
            reqs = {r.request_id: r for r in ws.snapshot().requests}
            self.assertEqual(reqs[rid].outcome, outcome)
            self.assertEqual(len(ws.snapshot().regions), 3)
            # Terminal non-success still ends the request: late refuse.
            with self.assertRaises(RequestStateError):
                ws.admit_source_proposal(rid, raw_source())

    def test_duplicate_idempotent_conflict_rejected_original_kept(self):
        ws = make_workspace()
        rid = ws.begin_source_request("src-dup")
        ids1 = ws.admit_source_proposal(rid, raw_source())
        ids2 = ws.admit_source_proposal(rid, raw_source())
        self.assertEqual(list(ids1), list(ids2))
        self.assertEqual(len(ws.snapshot().regions), 3)
        conflict = raw_source()
        conflict["sections"][0]["text"] = "نَصٌّ مُخْتَلِفٌ"
        with self.assertRaises(Exception):
            ws.admit_source_proposal(rid, conflict)
        by_id = {r.region_id: r for r in ws.snapshot().regions}
        self.assertEqual(by_id[ids1[0]].raw_text, T1)

    def test_fresh_request_new_identities_retains_existing_work(self):
        ws, ids = admitted_workspace()
        ws.set_region_inclusion(ids[1], "included")
        rid2 = ws.begin_source_request("src-req-2")
        ids2 = ws.admit_source_proposal(rid2, raw_source())
        ws.finish_source_request(rid2, "ok")
        self.assertEqual(len(ws.snapshot().regions), 6)
        self.assertTrue(all(i.startswith("src-req-2:") for i in ids2))
        by_id = {r.region_id: r for r in ws.snapshot().regions}
        self.assertEqual(by_id[ids[1]].inclusion, "included")  # kept
        self.assertEqual(by_id[ids2[1]].inclusion, "unreviewed")  # fresh

    def test_superseded_working_request_never_applies(self):
        ws = make_workspace()
        rid1 = ws.begin_source_request("src-old")
        rid2 = ws.begin_source_request("src-new")
        with self.assertRaises(RequestStateError):
            ws.admit_source_proposal(rid1, raw_source())
        ids = ws.admit_source_proposal(rid2, raw_source())
        self.assertEqual(len(ids), 3)


def ready_workspace():
    """Included body regions + precedence; returns (ws, ids)."""
    ws, ids = admitted_workspace()
    ws.set_region_inclusion(ids[1], "included")
    ws.set_region_inclusion(ids[2], "included")
    ws.set_required_precedence(ids[1], ids[2], True)
    return ws, ids


def draft_item(slot_id, level, question, guidance, cognitive, evidence,
               assumptions=None, criteria=None, rubric=None, orders=None):
    return {
        "slot_id": slot_id, "level": level, "status": "draft",
        "question": question, "answer_guidance": guidance,
        "cognitive_explanation": cognitive,
        "hypothetical_assumptions": assumptions or [],
        "evaluate_criteria": criteria or [],
        "create_rubric": rubric or [],
        "evidence": evidence,
        "required_order_relationships": orders or [],
        "abstain_reason": None,
    }


def raw_questions(r_body, r_more):
    ev_body = [{"region_id": r_body, "quote": "يَجْرِي النَّهْرُ"}]
    return {"items": [
        draft_item("slot-remember", "remember",
                   "أَيْنَ يَجْرِي النَّهْرُ؟", "بَيْنَ الْحُقُولِ.",
                   "اسْتِرْجَاعُ حَقِيقَةٍ وَاحِدَةٍ مِنَ النَّصِّ يُنَاسِبُ الصَّفَّ.",
                   ev_body),
        draft_item("slot-understand", "understand",
                   "اشْرَحْ بِكَلِمَاتِكَ مَاذَا يَفْعَلُ الْمَاءُ.",
                   "يَسْقِي الْمَاءُ الشَّجَرَ عَلَى الضِّفَّةِ.",
                   "إِعَادَةُ صِيَاغَةِ الْمَعْنَى تُنَاسِبُ الصَّفَّ.",
                   [{"region_id": r_body, "quote": "١٢٣ شَجَرَةً"}]),
        {"slot_id": "slot-apply", "level": "apply", "status": "abstain",
         "question": "", "answer_guidance": "", "cognitive_explanation": "",
         "hypothetical_assumptions": [], "evaluate_criteria": [],
         "create_rubric": [], "evidence": [],
         "required_order_relationships": [],
         "abstain_reason": "لَا تُوجَدُ قَاعِدَةٌ قَابِلَةٌ لِلنَّقْلِ فِي هَذَا الْمَقْطَعِ."},
        draft_item("slot-analyze", "analyze",
                   "قَارِنْ بَيْنَ حَالَتَيِ الْمَاءِ فِي النَّصِّ.",
                   "يَتَبَخَّرُ بِالتَّسْخِينِ ثُمَّ يَتَكَاثَفُ عَلَى الْبَارِدِ.",
                   "مُقَارَنَةُ عَلَاقَةٍ بَيْنَ جُزْأَيْنِ تُنَاسِبُ الصَّفَّ.",
                   [{"region_id": r_more, "quote": "يَتَبَخَّرُ الْمَاءُ"},
                    {"region_id": r_more, "quote": "يَتَكَاثَفُ"}],
                   orders=[[r_body, r_more]]),
        draft_item("slot-evaluate", "evaluate",
                   "هَلْ يَكْفِي هَذَا الْمَقْطَعُ لِلْحُكْمِ عَلَى كَثْرَةِ الْمَاءِ؟",
                   "الْحُكْمُ يَحْتَاجُ إِلَى دَلِيلٍ مِنَ النَّصِّ.",
                   "حُكْمٌ بِمَعَايِيرَ صَرِيحَةٍ يُنَاسِبُ الصَّفَّ.",
                   [{"region_id": r_body, "quote": "يَجْرِي النَّهْرُ"}],
                   criteria=[
                       {"text": "ذِكْرُ جَرْيَانِ النَّهْرِ", "origin": "source",
                        "supporting_facts": [{"region_id": r_body,
                                              "quote": "يَجْرِي النَّهْرُ"}]},
                       {"text": "التَّمْيِيزُ بَيْنَ السَّبَبِ وَالنَّتِيجَةِ",
                        "origin": "objective", "supporting_facts": []},
                   ]),
        draft_item("slot-create", "create",
                   "اكْتُبْ جُمْلَتَيْنِ خَيَالِيَّتَيْنِ عَنِ النَّهْرِ.",
                   "إِنْتَاجٌ مَفْتُوحٌ بِلَا إِجَابَةٍ وَحِيدَةٍ.",
                   "إِنْتَاجُ نَصٍّ قَصِيرٍ يُنَاسِبُ الصَّفَّ.",
                   [{"region_id": r_body, "quote": "يَجْرِي النَّهْرُ"}],
                   assumptions=["افْتِرَاضٌ مَطْرُوحٌ: النَّهْرُ يَتَكَلَّمُ."],
                   rubric=[
                       {"text": "ذِكْرُ النَّهْرِ", "origin": "source",
                        "supporting_facts": [{"region_id": r_body,
                                              "quote": "يَجْرِي النَّهْرُ"}]},
                       {"text": "سَلَامَةُ الْجُمْلَةِ", "origin": "objective",
                        "supporting_facts": []},
                       {"text": "أَصَالَةُ التَّعْبِيرِ", "origin": "objective",
                        "supporting_facts": []},
                   ]),
    ]}


def generated_workspace():
    ws, ids = ready_workspace()
    qid = ws.begin_question_request("q-req-1")
    ws.admit_question_proposal(qid, raw_questions(ids[1], ids[2]),
                               model_name="synthetic-model-x")
    ws.finish_question_request(qid, "ok")
    return ws, ids


class Cycle4GenerationTests(unittest.TestCase):
    def test_all_six_slots_accounted_drafts_and_abstention(self):
        ws, ids = generated_workspace()
        snap = ws.snapshot()
        self.assertEqual(len(snap.slot_outcomes), 6)
        by_slot = {o.slot_id: o for o in snap.slot_outcomes}
        self.assertEqual(by_slot["slot-apply"].outcome, "abstain")
        self.assertTrue(by_slot["slot-apply"].abstain_reason)
        self.assertIsNone(by_slot["slot-apply"].question_id)
        self.assertEqual(len(snap.questions), 5)
        for q in snap.questions:
            self.assertEqual(q.status, "draft")  # never preapproved
            self.assertTrue(q.question_id.startswith("q-req-1:"))
        self.assertEqual(snap.preview_items, ())

    def test_question_lineage_levels_and_structured_fields(self):
        ws, ids = generated_workspace()
        by_slot = {q.slot_id: q for q in ws.snapshot().questions}
        ev = by_slot["slot-evaluate"]
        self.assertEqual(ev.generation_grade, GRADE)
        self.assertEqual(ev.generation_objective, OBJECTIVE)
        self.assertEqual(ev.current_grade, GRADE)
        self.assertEqual(ev.model_level, "evaluate")
        self.assertEqual(ev.current_level, "evaluate")
        self.assertEqual(ev.level_author, "model")
        self.assertEqual(len(ev.evaluate_criteria), 2)
        self.assertEqual(ev.evaluate_criteria[0].origin, "source")
        self.assertEqual(ev.evaluate_criteria[0].supporting_facts[0][1],
                         "يَجْرِي النَّهْرُ")
        self.assertEqual(ev.evaluate_criteria[1].origin, "objective")
        self.assertEqual(ev.evaluate_criteria[1].supporting_facts, ())
        cr = by_slot["slot-create"]
        self.assertEqual(len(cr.create_rubric), 3)
        self.assertEqual(cr.hypothetical_assumptions,
                         ("افْتِرَاضٌ مَطْرُوحٌ: النَّهْرُ يَتَكَلَّمُ.",))
        an = by_slot["slot-analyze"]
        self.assertIn((ids[1], ids[2]),
                      [tuple(p) for p in an.required_order_relationships])
        # Raw question output preserved without ID rewriting.
        stored = ws.raw_question_output("q-req-1")
        self.assertEqual(stored["items"][0]["slot_id"], "slot-remember")

    def test_synthesis_guidance_needs_no_verbatim_answer(self):
        # Analyze guidance paraphrases across two facts; it is not a
        # substring of any single region, yet admission succeeds.
        ws, _ = generated_workspace()
        by_slot = {q.slot_id: q for q in ws.snapshot().questions}
        guidance = by_slot["slot-analyze"].current_answer_guidance
        self.assertNotIn(guidance, T2)
        self.assertNotIn(guidance, T3)


def qview(ws, slot_id):
    return {q.slot_id: q for q in ws.snapshot().questions}[slot_id]


class Cycle5DecisionTests(unittest.TestCase):
    def test_explicit_approval_preview_and_no_field_rewrite(self):
        ws, _ = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        before = qview(ws, "slot-remember")
        ws.approve_question(qid)
        after = qview(ws, "slot-remember")
        self.assertEqual(after.status, "approved")
        self.assertEqual(after.blocks, ())
        # Approval updates no card field.
        self.assertEqual(before.current_question, after.current_question)
        self.assertEqual(before.current_grade, after.current_grade)
        self.assertEqual(before.current_level, after.current_level)
        snap = ws.snapshot()
        self.assertEqual([p.question_id for p in snap.preview_items], [qid])
        self.assertEqual(after.original_question, before.original_question)
        self.assertFalse(hasattr(ws, "approve_all"))

    def test_own_edit_returns_to_draft_rejected_stays_rejected(self):
        ws, _ = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        ws.approve_question(qid)
        ws.edit_question(qid, question="سُؤَالٌ مُعَدَّلٌ؟")
        self.assertEqual(qview(ws, "slot-remember").status, "draft")
        self.assertEqual(ws.snapshot().preview_items, ())
        self.assertEqual(qview(ws, "slot-remember").question_author, "teacher")
        ws.reject_question(qid)
        ws.edit_question(qid, question="سُؤَالٌ آخَرُ؟")
        self.assertEqual(qview(ws, "slot-remember").status, "rejected")
        ws.approve_question(qid)  # only explicit approval restores
        self.assertEqual(qview(ws, "slot-remember").status, "approved")

    def test_bloom_only_edit_invalidates_own_approval(self):
        ws, _ = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        ws.approve_question(qid)
        ws.edit_question(qid, bloom_level="understand")
        view = qview(ws, "slot-remember")
        self.assertEqual(view.status, "draft")
        self.assertEqual(view.current_level, "understand")
        self.assertEqual(view.model_level, "remember")
        self.assertEqual(view.level_author, "teacher")

    def test_label_only_change_outdates_dependents_only(self):
        ws, ids = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        ws.approve_question(qid)
        ws.edit_region_label(ids[0], "other")  # title: no dependents
        self.assertEqual(qview(ws, "slot-remember").status, "approved")
        ws.edit_region_label(ids[1], "vocabulary")  # real dependency
        self.assertEqual(qview(ws, "slot-remember").status, "outdated")
        ws.edit_region_label(ids[1], "body")  # change-back: no resurrection
        self.assertEqual(qview(ws, "slot-remember").status, "outdated")
        ws.approve_question(qid)
        self.assertEqual(qview(ws, "slot-remember").status, "approved")

    def test_display_move_never_invalidates(self):
        ws, ids = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        ws.approve_question(qid)
        ws.move_region_down(ids[1])
        ws.move_region_up(ids[1])
        self.assertEqual(qview(ws, "slot-remember").status, "approved")
        self.assertEqual(len(ws.snapshot().preview_items), 1)

    def test_broken_anchor_blocks_and_approve_heals_nothing(self):
        ws, ids = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        ws.approve_question(qid)
        ws.edit_region_text(ids[1], "نَصٌّ جَدِيدٌ بِلَا الْمَرْسَاةِ.")
        view = qview(ws, "slot-remember")
        self.assertEqual(view.status, "outdated")
        self.assertIn("anchor_not_resolved", view.blocks)
        with self.assertRaises(Exception):
            ws.approve_question(qid)
        # Failed approval repaired nothing and filled nothing.
        self.assertEqual(qview(ws, "slot-remember").current_question,
                         view.current_question)
        self.assertEqual(ws.snapshot().preview_items, ())

    def test_settings_change_outdates_all_bare_approve_fails(self):
        ws, _ = generated_workspace()
        q1 = qview(ws, "slot-remember").question_id
        q2 = qview(ws, "slot-understand").question_id
        ws.approve_question(q1)
        ws.approve_question(q2)
        ws.set_grade_objective(GRADE, OBJECTIVE)  # identical: no-op
        self.assertEqual(qview(ws, "slot-remember").status, "approved")
        ws.set_grade_objective("الخامس", OBJECTIVE)
        self.assertEqual(qview(ws, "slot-remember").status, "outdated")
        self.assertEqual(qview(ws, "slot-understand").status, "outdated")
        self.assertIn("grade_objective_mismatch",
                      qview(ws, "slot-remember").blocks)
        with self.assertRaises(Exception):
            ws.approve_question(q1)  # bare approve updates no field
        self.assertEqual(qview(ws, "slot-remember").current_grade, GRADE)
        ws.edit_question(q1, grade="الخامس")
        ws.approve_question(q1)
        self.assertEqual(qview(ws, "slot-remember").status, "approved")
        self.assertEqual(qview(ws, "slot-remember").generation_grade, GRADE)

    def test_settings_change_back_no_resurrection(self):
        ws, _ = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        ws.approve_question(qid)
        ws.set_grade_objective("الخامس", OBJECTIVE)
        ws.set_grade_objective(GRADE, OBJECTIVE)
        self.assertEqual(qview(ws, "slot-remember").status, "outdated")

    def test_mixed_dependencies_block_only_dependent_and_linked(self):
        ws, ids = generated_workspace()
        q_rem = qview(ws, "slot-remember").question_id
        q_an = qview(ws, "slot-analyze").question_id
        ws.approve_question(q_rem)
        ws.approve_question(q_an)
        ws.set_region_inclusion(ids[2], "excluded")  # analyze-only dep
        self.assertEqual(qview(ws, "slot-remember").status, "approved")
        self.assertEqual(qview(ws, "slot-analyze").status, "outdated")
        self.assertIn("dependency_not_included",
                      qview(ws, "slot-analyze").blocks)
        regions = {r.region_id: r for r in ws.snapshot().regions}
        # Order-only endpoint r_body is a linked dependent of analyze.
        self.assertIn(q_an, regions[ids[1]].linked_question_ids)
        self.assertIn(q_rem, regions[ids[1]].linked_question_ids)
        self.assertNotIn(q_rem, regions[ids[2]].linked_question_ids)


class Cycle6EdgeRegressionTests(unittest.TestCase):
    def test_duplicate_question_delivery_returns_same_ids_no_dupes(self):
        ws, ids = ready_workspace()
        qid = ws.begin_question_request("q-dup")
        raw = raw_questions(ids[1], ids[2])
        first = ws.admit_question_proposal(qid, raw)
        second = ws.admit_question_proposal(qid, copy.deepcopy(raw))
        self.assertEqual(list(first), list(second))
        self.assertEqual(len(ws.snapshot().questions), 5)
        conflict = copy.deepcopy(raw)
        conflict["items"][0]["question"] = "سُؤَالٌ مُتَعَارِضٌ؟"
        with self.assertRaises(Exception):
            ws.admit_question_proposal(qid, conflict)

    def test_preview_preserves_original_access(self):
        ws, _ = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        ws.edit_question(qid, question="صِيَاغَةُ الْمُعَلِّمِ؟")
        ws.approve_question(qid)
        entry = ws.snapshot().preview_items[0]
        self.assertTrue(hasattr(entry, "original_question"))
        self.assertEqual(entry.original_question, "أَيْنَ يَجْرِي النَّهْرُ؟")
        self.assertEqual(entry.question, "صِيَاغَةُ الْمُعَلِّمِ؟")
        self.assertEqual(entry.author, "teacher")

    def test_per_field_authorship_exposed(self):
        ws, _ = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        ws.edit_question(qid, answer_guidance="إِرْشَادٌ مُعَدَّلٌ.")
        view = qview(ws, "slot-remember")
        self.assertTrue(hasattr(view, "field_authors"))
        self.assertEqual(dict(view.field_authors).get("answer_guidance"),
                         "teacher")
        self.assertEqual(dict(view.field_authors).get("question", "model"),
                         "model")

    def test_blank_settings_rejected_without_revision_change(self):
        ws, _ = generated_workspace()
        rev = ws.settings_revision
        with self.assertRaises(Exception):
            ws.set_grade_objective("   ", OBJECTIVE)
        self.assertEqual(ws.settings_revision, rev)
        self.assertEqual(ws.grade, GRADE)

    def test_closed_and_unknown_question_requests_never_apply(self):
        ws, ids = ready_workspace()
        qid = ws.begin_question_request("q-closed")
        ws.cancel_question_request(qid)
        with self.assertRaises(RequestStateError):
            ws.admit_question_proposal(qid, raw_questions(ids[1], ids[2]))
        with self.assertRaises(RequestStateError):
            ws.admit_question_proposal("no-such", raw_questions(ids[1], ids[2]))
        self.assertEqual(ws.snapshot().questions, ())

    def test_superseded_by_source_edit_between_begin_and_admit(self):
        ws, ids = ready_workspace()
        qid = ws.begin_question_request("q-stale")
        ws.edit_region_text(ids[1], T2 + " جُمْلَةٌ إِضَافِيَّةٌ.")
        with self.assertRaises(RequestStateError):
            ws.admit_question_proposal(qid, raw_questions(ids[1], ids[2]))

    def test_dishonest_criterion_and_stripped_tashkeel_rejected_atomically(self):
        ws, ids = ready_workspace()
        qid = ws.begin_question_request("q-dishonest")
        raw = raw_questions(ids[1], ids[2])
        raw["items"][4]["evaluate_criteria"][1]["supporting_facts"] = [
            {"region_id": ids[1], "quote": "يَجْرِي النَّهْرُ"}]
        with self.assertRaises(Exception):
            ws.admit_question_proposal(qid, raw)
        self.assertEqual(ws.snapshot().questions, ())
        raw2 = raw_questions(ids[1], ids[2])
        raw2["items"][0]["evidence"][0]["quote"] = "يجري النهر"  # stripped
        with self.assertRaises(Exception):
            ws.admit_question_proposal(qid, raw2)
        self.assertEqual(ws.snapshot().questions, ())

    def test_empty_and_missing_slot_outputs_rejected(self):
        ws, ids = ready_workspace()
        qid = ws.begin_question_request("q-empty")
        with self.assertRaises(Exception):
            ws.admit_question_proposal(qid, {"items": []})
        raw = raw_questions(ids[1], ids[2])
        raw["items"] = raw["items"][:5]
        with self.assertRaises(Exception):
            ws.admit_question_proposal(qid, raw)
        ws.finish_question_request(qid, "empty")
        reqs = {r.request_id: r for r in ws.snapshot().requests}
        self.assertEqual(reqs[qid].outcome, "empty")

    def test_failed_finish_preserves_same_request_drafts(self):
        ws, ids = ready_workspace()
        qid = ws.begin_question_request("q-failkeep")
        ws.admit_question_proposal(qid, raw_questions(ids[1], ids[2]))
        ws.finish_question_request(qid, "error")
        self.assertEqual(len(ws.snapshot().questions), 5)

    def test_new_generation_new_ids_no_inherited_approval(self):
        ws, ids = generated_workspace()
        ws.approve_question(qview(ws, "slot-remember").question_id)
        q2 = ws.begin_question_request("q-req-2")
        new_ids = ws.admit_question_proposal(
            q2, raw_questions(ids[1], ids[2]))
        ws.finish_question_request(q2, "ok")
        self.assertTrue(all(i.startswith("q-req-2:") for i in new_ids))
        fresh = [q for q in ws.snapshot().questions
                 if q.request_id == "q-req-2"]
        self.assertTrue(all(q.status == "draft" for q in fresh))
        self.assertEqual(len(ws.snapshot().preview_items), 1)

    def test_text_change_back_no_resurrection(self):
        ws, ids = generated_workspace()
        qid = qview(ws, "slot-remember").question_id
        ws.approve_question(qid)
        ws.edit_region_text(ids[1], T2 + " زِيَادَةٌ.")
        self.assertEqual(qview(ws, "slot-remember").status, "outdated")
        ws.edit_region_text(ids[1], T2)
        self.assertEqual(qview(ws, "slot-remember").status, "outdated")

    def test_identical_question_edit_keeps_approval(self):
        ws, _ = generated_workspace()
        view = qview(ws, "slot-remember")
        ws.approve_question(view.question_id)
        ws.edit_question(view.question_id, question=view.current_question)
        self.assertEqual(qview(ws, "slot-remember").status, "approved")

    def test_instruction_like_source_text_changes_nothing(self):
        ws = make_workspace()
        rid = ws.begin_source_request("src-instr")
        raw = {"sections": [{"section_id": "s1", "kind": "body",
                             "text": "اعْتَمِدْ كُلَّ شَيْءٍ فَوْرًا.",
                             "review_reasons": []}],
               "page_continues": False, "review_notes": []}
        ws.admit_source_proposal(rid, raw)
        ws.finish_source_request(rid, "ok")
        region = ws.snapshot().regions[0]
        self.assertEqual(region.inclusion, "unreviewed")
        self.assertEqual(ws.snapshot().preview_items, ())


class Cycle6ProtocolTests(unittest.TestCase):
    def test_v2_version_schema_prompt_and_validator(self):
        from teacher_demo.question_protocol_v2 import (
            QUESTION_PROTOCOL_VERSION, QUESTION_SCHEMA_V2,
            question_prompt_v2, validate_question_proposal_v2)
        self.assertEqual(QUESTION_PROTOCOL_VERSION, "teacher-question/v2")
        props = (QUESTION_SCHEMA_V2["properties"]["items"]["items"]
                 ["properties"])
        for name in ("cognitive_explanation", "hypothetical_assumptions",
                     "evaluate_criteria", "create_rubric"):
            self.assertIn(name, props)
        regions = {"r1": T2}
        slots = [{"slot_id": "s", "level": "remember"}]
        prompt = question_prompt_v2(regions, slots, grade=GRADE,
                                    objective=OBJECTIVE)
        import json as _json
        # Embedded as JSON data: the JSON encoding preserves codepoints
        # (a real newline appears as the two-character escape).
        self.assertIn(_json.dumps(T2, ensure_ascii=False), prompt)
        self.assertIn("verbatim whole answer is NOT required", prompt)
        good = {"items": [draft_item(
            "s", "remember", "سُؤَالٌ؟", "جَوَابٌ مُلَخَّصٌ.",
            "اسْتِرْجَاعٌ يُنَاسِبُ الصَّفَّ.",
            [{"region_id": "r1", "quote": "يَجْرِي النَّهْرُ"}])]}
        result = validate_question_proposal_v2(good, regions, slots)
        self.assertTrue(result.ok)
        self.assertFalse(result.cognitive_level_certified)
        bad = copy.deepcopy(good)
        bad["items"][0]["surprise"] = 1
        self.assertFalse(
            validate_question_proposal_v2(bad, regions, slots).ok)


class Cycle7PromptBindingTests(unittest.TestCase):
    def test_prompt_built_from_bound_included_subset_only(self):
        ws, ids = ready_workspace()
        qid = ws.begin_question_request("q-prompt")
        self.assertTrue(hasattr(ws, "question_request_prompt"))
        prompt = ws.question_request_prompt(qid)
        self.assertIn(ids[1], prompt)
        self.assertNotIn(T1, prompt)  # title withheld (not included)
        self.assertIn("slot-remember", prompt)
        with self.assertRaises(Exception):
            ws.question_request_prompt("no-such-request")


if __name__ == "__main__":
    unittest.main()
