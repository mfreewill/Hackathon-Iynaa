"""QC-06: one usable generation population (UX_SPEC section 5).

Self-contained synthetic fixtures; public adapter only. The reviewed
*included* regions whose current teacher text is nonblank are the one
population behind ``snapshot().can_generate``, the sent
(``included_count``) / withheld (``withheld_count``) counts,
``begin_question_request`` eligibility, and the frozen prompt.
Inclusion alone is not usable text: a blank included region keeps its
teacher ``included`` decision visible but is withheld from generation.
Stripping decides blankness only; usable text is preserved exactly.
"""
import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from source_contract import SourcePage  # noqa: E402
from teacher_demo import (  # noqa: E402
    DecodedSourcePage,
    RequestStateError,
    TeacherWorkspace,
)
from teacher_demo.question_protocol_v2 import question_prompt_v2  # noqa: E402

GRADE = "الرابع"
OBJECTIVE = "أن يفسّر الطالب سبب نموّ النبتة"
# Exact Unicode fixtures: tashkeel, Arabic-Indic and Western digits.
TEXTS = (
    "أَشْرَقَتِ الشَّمْسُ عَلَى ١٢ شَجَرَةً / 12",
    "نَمَتِ النَّبْتَةُ بَعْدَ الْمَاءِ.",
    "هَبَّتِ الرِّيحُ ٢٣ / 23 مَرَّةً.",
)


def make_workspace(finish=None):
    """Three admitted regions, all reviewed *included*.

    ``finish`` is the source-request terminal outcome; ``None`` leaves
    the source request working.
    """
    page = DecodedSourcePage(SourcePage(
        source_id="qc06-synthetic-page", source_sha256="a" * 64,
        page_index=0, render_sha256="b" * 64, width=900, height=1400,
        rights_ref="synthetic local fixture",
        coordinate_convention="normalized_0_1000_top_left"))
    ws = TeacherWorkspace(page, grade=GRADE, objective=OBJECTIVE)
    rid = ws.begin_source_request("source")
    ids = ws.admit_source_proposal(rid, {
        "sections": [
            {"section_id": key, "kind": "body", "text": text,
             "review_reasons": []}
            for key, text in zip(("A", "B", "C"), TEXTS)],
        "page_continues": False, "review_notes": []},
        model_name="synthetic-model")
    for region_id in ids:
        ws.set_region_inclusion(region_id, "included")
    if finish is not None:
        ws.finish_source_request(rid, finish)
    return ws, list(ids)


def region_view(ws, region_id):
    return {r.region_id: r for r in ws.snapshot().regions}[region_id]


class UsablePopulationTests(unittest.TestCase):
    def test_counts_gate_and_prompt_share_exact_usable_text(self):
        for outcome in ("ok", "incomplete"):
            with self.subTest(terminal_outcome=outcome):
                ws, ids = make_workspace(finish=None)
                exact = "\n  " + TEXTS[0] + "  \n"
                ws.edit_region_text(ids[0], exact)
                ws.edit_region_text(ids[1], " \t\n")
                ws.set_region_inclusion(ids[2], "unresolved")
                # Still working: no generation from a moving target.
                self.assertFalse(ws.snapshot().can_generate)
                with self.assertRaises(RequestStateError):
                    ws.begin_question_request("early")
                ws.finish_source_request("source", outcome)
                snap = ws.snapshot()
                # One usable region is sent; the blank-included and
                # unresolved regions are withheld.
                self.assertEqual((snap.included_count, snap.withheld_count),
                                 (1, 2))
                self.assertTrue(snap.can_generate)
                # The blank region's teacher decision is unchanged and
                # its current/raw text preserved exactly.
                self.assertEqual(region_view(ws, ids[1]).inclusion, "included")
                self.assertEqual(region_view(ws, ids[1]).current_text, " \t\n")
                self.assertEqual(region_view(ws, ids[1]).raw_text, TEXTS[1])
                self.assertEqual(region_view(ws, ids[0]).current_text, exact)
                slots = [{"slot_id": "only", "level": "remember"}]
                request = ws.begin_question_request("questions",
                                                    requested_slots=slots)
                expected = question_prompt_v2(
                    {ids[0]: exact}, slots, grade=GRADE, objective=OBJECTIVE)
                self.assertEqual(ws.question_request_prompt(request),
                                 expected)
                frozen = ws.snapshot()
                self.assertEqual(ws.question_request_prompt(request),
                                 expected)
                self.assertEqual(ws.snapshot(), frozen)  # prompt read pure

    def test_named_terminal_failures_keep_admitted_usable_text_eligible(self):
        for outcome in ("invalid_schema", "empty", "error", "refusal",
                        "timeout", "rate_limited", "unknown"):
            with self.subTest(terminal_outcome=outcome):
                ws, ids = make_workspace(finish=outcome)
                snap = ws.snapshot()
                self.assertTrue(snap.source_analysis_terminal)
                self.assertTrue(snap.can_generate)
                self.assertEqual((snap.included_count, snap.withheld_count),
                                 (3, 0))
                request = ws.begin_question_request(
                    "questions",
                    requested_slots=[{"slot_id": "s", "level": "remember"}])
                self.assertTrue(ws.question_request_prompt(request))

    def test_all_blank_disables_generation_without_erasing_decisions(self):
        for blank in ("", " \t\n"):
            with self.subTest(blank=repr(blank)):
                ws, ids = make_workspace(finish="ok")
                for region_id in ids:
                    ws.edit_region_text(region_id, blank)
                snap = ws.snapshot()
                self.assertFalse(snap.can_generate)
                self.assertEqual((snap.included_count, snap.withheld_count),
                                 (0, len(ids)))
                self.assertTrue(all(r.inclusion == "included"
                                    for r in snap.regions))
                before = copy.deepcopy(snap)
                with self.assertRaises(RequestStateError):
                    ws.begin_question_request("blank")
                self.assertEqual(ws.snapshot(), before)

    def test_excluded_unresolved_unreviewed_withheld_despite_text(self):
        ws, ids = make_workspace(finish="ok")
        ws.set_region_inclusion(ids[1], "excluded")
        ws.set_region_inclusion(ids[2], "unresolved")
        snap = ws.snapshot()
        self.assertEqual((snap.included_count, snap.withheld_count), (1, 2))
        self.assertTrue(snap.can_generate)
        slots = [{"slot_id": "only", "level": "understand"}]
        request = ws.begin_question_request("questions",
                                            requested_slots=slots)
        expected = question_prompt_v2(
            {ids[0]: TEXTS[0]}, slots, grade=GRADE, objective=OBJECTIVE)
        self.assertEqual(ws.question_request_prompt(request), expected)

    def test_nonblank_padded_text_is_usable_and_never_stripped(self):
        ws, ids = make_workspace(finish="ok")
        padded = "  نَمَتِ النَّبْتَةُ ١٢ / 12  \n"
        ws.edit_region_text(ids[1], padded)
        ws.set_region_inclusion(ids[0], "excluded")
        ws.set_region_inclusion(ids[2], "excluded")
        snap = ws.snapshot()
        self.assertEqual((snap.included_count, snap.withheld_count), (1, 2))
        self.assertEqual(region_view(ws, ids[1]).current_text, padded)
        slots = [{"slot_id": "only", "level": "remember"}]
        request = ws.begin_question_request("questions",
                                            requested_slots=slots)
        expected = question_prompt_v2(
            {ids[1]: padded}, slots, grade=GRADE, objective=OBJECTIVE)
        self.assertEqual(ws.question_request_prompt(request), expected)


if __name__ == "__main__":
    unittest.main()
