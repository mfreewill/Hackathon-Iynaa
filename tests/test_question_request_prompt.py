"""Completion-slice tests for ``TeacherWorkspace.question_request_prompt``.

Synthetic fixtures only. No model API, no network, no real school
pages. Stdlib unittest only. All Arabic strings are original harmless
inventions for this file (mirroring ``test_teacher_domain`` fixtures).

The method under test only *builds* the packaged v2 prompt string
from a question request's frozen bindings; it must reject unknown,
closed, and stale requests at the same lifecycle boundary used by
``admit_question_proposal`` and must never fabricate a model response.
"""
import copy
import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from source_contract import SourcePage  # noqa: E402
from teacher_demo import (  # noqa: E402
    DecodedSourcePage, RequestStateError, TeacherWorkspace)
from teacher_demo.question_protocol_v2 import question_prompt_v2  # noqa: E402

GRADE = "الرابع"
OBJECTIVE = "أن يميّز الطالب بين السبب والنتيجة في النص"
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
             "review_reasons": []},
            {"section_id": "s-more", "kind": "body", "text": T3,
             "review_reasons": []},
        ],
        "page_continues": False,
        "review_notes": [],
    }


def ready_workspace():
    """Terminal source request; body regions included, title withheld."""
    ws = TeacherWorkspace(make_page(), grade=GRADE, objective=OBJECTIVE)
    rid = ws.begin_source_request("src-req-1")
    ids = list(ws.admit_source_proposal(rid, raw_source()))
    ws.finish_source_request(rid, "ok")
    ws.set_region_inclusion(ids[1], "included")
    ws.set_region_inclusion(ids[2], "included")
    ws.set_required_precedence(ids[1], ids[2], True)
    return ws, ids


class QuestionRequestPromptTests(unittest.TestCase):
    def prompt(self, ws, request_id):
        fn = getattr(ws, "question_request_prompt", None)
        self.assertTrue(callable(fn), "question_request_prompt missing")
        return fn(request_id)

    def test_prompt_built_from_frozen_bound_values_exact_unicode(self):
        ws, ids = ready_workspace()
        slots = [{"slot_id": "only-remember", "level": "remember"},
                 {"slot_id": "only-create", "level": "create"}]
        slots_as_passed = copy.deepcopy(slots)
        qid = ws.begin_question_request("q-custom", requested_slots=slots)
        self.assertEqual(slots, slots_as_passed)  # caller data untouched
        prompt = self.prompt(ws, qid)
        self.assertEqual(slots, slots_as_passed)  # still untouched
        self.assertIsInstance(prompt, str)
        # Exactly what the packaged builder produces from the frozen
        # included subset, the requested slots, and bound settings.
        expected = question_prompt_v2(
            {ids[1]: T2, ids[2]: T3}, slots_as_passed,
            grade=GRADE, objective=OBJECTIVE)
        self.assertEqual(prompt, expected)
        # Exact Unicode survives as JSON data; withheld text never appears.
        self.assertIn(json.dumps(T2, ensure_ascii=False), prompt)
        self.assertIn(json.dumps(T3, ensure_ascii=False), prompt)
        self.assertIn(json.dumps(GRADE, ensure_ascii=False), prompt)
        self.assertIn(json.dumps(OBJECTIVE, ensure_ascii=False), prompt)
        self.assertNotIn(T1, prompt)
        self.assertNotIn(ids[0], prompt)
        self.assertNotIn("slot-understand", prompt)
        # Mutating the caller's list after begin changes nothing frozen.
        slots[0]["slot_id"] = "mutated-slot"
        slots.append({"slot_id": "extra-slot", "level": "apply"})
        again = self.prompt(ws, qid)
        self.assertEqual(again, expected)
        self.assertNotIn("mutated-slot", again)
        self.assertNotIn("extra-slot", again)

    def test_prompt_is_pure_repeatable_and_ignores_display_moves(self):
        ws, ids = ready_workspace()
        qid = ws.begin_question_request("q-pure")
        before = ws.snapshot()
        first = self.prompt(ws, qid)
        self.assertEqual(first, self.prompt(ws, qid))
        # No state changed: no admission, revision bump, or fabricated items.
        self.assertEqual(ws.snapshot(), before)
        self.assertEqual(ws.snapshot().questions, ())
        self.assertEqual(ws.source_revision, before.source_revision)
        self.assertEqual(ws.settings_revision, before.settings_revision)
        # A display-only move touches no revision and no frozen binding.
        self.assertTrue(ws.move_region_down(ids[1]))
        self.assertEqual(self.prompt(ws, qid), first)

    def test_unknown_and_wrong_kind_requests_rejected(self):
        ws, _ = ready_workspace()
        with self.assertRaises(RequestStateError):
            self.prompt(ws, "no-such-request")
        # A source request id is not a question request (same boundary
        # ``admit_question_proposal`` applies via ``_request``).
        with self.assertRaises(RequestStateError):
            self.prompt(ws, "src-req-1")
        self.assertEqual(ws.snapshot().questions, ())

    def test_cancelled_finished_and_superseded_requests_rejected(self):
        ws, _ = ready_workspace()
        cancelled = ws.begin_question_request("q-cancelled")
        ws.cancel_question_request(cancelled)
        with self.assertRaises(RequestStateError):
            self.prompt(ws, cancelled)

        ws, _ = ready_workspace()
        finished = ws.begin_question_request("q-finished")
        ws.finish_question_request(finished, "error")
        with self.assertRaises(RequestStateError):
            self.prompt(ws, finished)

        ws, _ = ready_workspace()
        old = ws.begin_question_request("q-old")
        new = ws.begin_question_request("q-new")  # supersedes working q-old
        with self.assertRaises(RequestStateError):
            self.prompt(ws, old)
        self.assertIn("slot-remember", self.prompt(ws, new))

    def test_stale_after_source_edit_rejected(self):
        ws, ids = ready_workspace()
        qid = ws.begin_question_request("q-stale-source")
        self.prompt(ws, qid)  # current: prompt builds
        ws.edit_region_text(ids[1], T2 + " جُمْلَةٌ إِضَافِيَّةٌ.")
        with self.assertRaises(RequestStateError):
            self.prompt(ws, qid)

    def test_stale_after_settings_change_rejected_no_resurrection(self):
        ws, _ = ready_workspace()
        qid = ws.begin_question_request("q-stale-settings")
        ws.set_grade_objective(GRADE, OBJECTIVE)  # identical: no-op
        self.prompt(ws, qid)  # still current
        ws.set_grade_objective("الخامس", OBJECTIVE)
        with self.assertRaises(RequestStateError):
            self.prompt(ws, qid)
        ws.set_grade_objective(GRADE, OBJECTIVE)  # change-back: still stale
        with self.assertRaises(RequestStateError):
            self.prompt(ws, qid)


if __name__ == "__main__":
    unittest.main()
