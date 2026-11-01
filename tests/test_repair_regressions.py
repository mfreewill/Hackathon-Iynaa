"""Regression tests for the causal-repair round.

Each test pins one behavior the parent's independent regressions (or the
repair brief) identified. Synthetic fixtures only; imports the real
implementation.
"""
import dataclasses
import math
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from source_contract import (  # noqa: E402
    ContractError, FactAnchor, QuestionProposal, RegionProposal,
    SourcePage, Workspace)


def page(**kw):
    base = dict(source_id="src-1", source_sha256="a" * 64, page_index=0,
                render_sha256="b" * 64, width=1000, height=1400,
                rights_ref="synthetic", coordinate_convention="normalized_0_1000_top_left")
    base.update(kw)
    return SourcePage(**base)


def region(pg, rid, **kw):
    vals = dict(region_id=rid, source_id=pg.source_id,
                source_sha256=pg.source_sha256, page_index=pg.page_index,
                render_sha256=pg.render_sha256,
                source_box=(10.0, 20.0, 100.0, 120.0),
                box_origin="synthetic-geometry", semantic_label="paragraph",
                semantic_label_origin="synthetic-label",
                text_candidate=rid + " fact", source_crop_ref="crop:" + rid,
                order_candidate=1, review_reasons=(),
                coordinate_convention=pg.coordinate_convention)
    vals.update(kw)
    return RegionProposal(**vals)


def ws3():
    pg = page()
    w = Workspace(pg)
    for rid in ("r1", "r2", "r3"):
        w.submit_region_proposal(region(pg, rid))
        w.review_region(rid, teacher_text=rid + " fact", inclusion="included")
    return w


def draft(qid="q1", rid="r1", **kw):
    vals = dict(question_id=qid, grade="5", objective="obj",
                bloom_task_rationale="why", question="question?",
                answer_guidance="answer", supporting_region_ids=(rid,),
                source_fact_anchors=(FactAnchor(rid, "fact"),),
                required_order_relationships=())
    vals.update(kw)
    return vals


def proposal(w, **kw):
    vals = draft(**kw)
    pg = w.source
    vals.update(source_id=pg.source_id, source_sha256=pg.source_sha256,
                page_index=pg.page_index, render_sha256=pg.render_sha256,
                generated_at_epoch=w.workspace_epoch, outcome="ok")
    return QuestionProposal(**vals)


def preview_questions(w):
    return {i.question_id: i.question for i in w.approved_preview().items}


class QuestionOwnershipTests(unittest.TestCase):
    def test_duplicate_create_rejected_and_teacher_work_preserved(self):
        w = ws3()
        w.create_question(**draft())
        w.approve_question("q1")
        w.edit_question("q1", question="teacher changed")
        with self.assertRaises(ContractError):
            w.create_question(**draft())
        self.assertEqual(w.question_status("q1"), "invalidated")
        w.approve_question("q1")  # teacher's edited text survived, not the dup
        self.assertEqual(preview_questions(w), {"q1": "teacher changed"})

    def test_duplicate_provider_proposal_rejected_and_teacher_work_preserved(self):
        w = ws3()
        w.create_question(**draft())
        w.approve_question("q1")
        w.edit_question("q1", question="teacher changed")
        with self.assertRaises(ContractError):
            w.submit_question_proposal(proposal(w))
        self.assertEqual(w.question_status("q1"), "invalidated")
        w.approve_question("q1")
        self.assertEqual(preview_questions(w), {"q1": "teacher changed"})

    def test_duplicate_create_on_approved_rejected_approval_untouched(self):
        w = ws3()
        w.create_question(**draft())
        w.approve_question("q1")
        with self.assertRaises(ContractError):
            w.create_question(**draft())
        self.assertEqual(w.question_status("q1"), "approved")

    def test_question_edit_back_to_original_still_needs_reapproval(self):
        w = ws3()
        w.create_question(**draft())
        w.approve_question("q1")
        w.edit_question("q1", question="other")
        w.edit_question("q1", question="question?")
        self.assertEqual(w.question_status("q1"), "invalidated")


class OrderPersistenceTests(unittest.TestCase):
    def _ordered_approved(self):
        w = ws3()
        w.set_order_relationship("r1", "r2", True)
        w.create_question(**draft(
            supporting_region_ids=("r1", "r2"),
            source_fact_anchors=(FactAnchor("r1", "fact"), FactAnchor("r2", "fact")),
            required_order_relationships=(("r1", "r2"),)))
        w.create_question(**draft("q2", "r3"))
        w.approve_question("q1")
        w.approve_question("q2")
        return w

    def test_order_remove_restore_stays_revoked_until_reapproved(self):
        w = self._ordered_approved()
        w.set_order_relationship("r1", "r2", False)
        self.assertNotEqual(w.question_status("q1"), "approved")
        w.set_order_relationship("r1", "r2", True)
        self.assertEqual(w.question_status("q1"), "invalidated")
        self.assertEqual(w.question_status("q2"), "approved")  # independent
        w.approve_question("q1")
        self.assertEqual(w.question_status("q1"), "approved")

    def test_noop_order_set_is_not_a_dependency_change(self):
        w = self._ordered_approved()
        w.set_order_relationship("r1", "r2", True)  # already present
        self.assertEqual(w.question_status("q1"), "approved")

    def test_region_edit_back_to_original_still_needs_reapproval(self):
        w = ws3()
        w.create_question(**draft())
        w.approve_question("q1")
        w.review_region("r1", teacher_text="changed fact", inclusion="included")
        w.review_region("r1", teacher_text="r1 fact", inclusion="included")
        self.assertEqual(w.question_status("q1"), "invalidated")


class AnchorAndReferenceTests(unittest.TestCase):
    def test_empty_quote_cannot_support_approval(self):
        w = ws3()
        with self.assertRaises(ContractError):
            w.create_question(**draft(source_fact_anchors=(FactAnchor("r1", ""),)))
            w.approve_question("q1")
        self.assertEqual(w.approved_preview().items, ())

    def test_dangling_order_ids_rejected(self):
        w = ws3()
        with self.assertRaises(ContractError):
            w.set_order_relationship("ghost", "r1", True)
        with self.assertRaises(ContractError):
            w.create_question(**draft(required_order_relationships=(("ghost", "r1"),)))
            w.approve_question("q1")
        self.assertEqual(w.approved_preview().items, ())

    def test_unknown_supporting_or_anchor_region_rejected(self):
        w = ws3()
        with self.assertRaises(ContractError):
            w.create_question(**draft(supporting_region_ids=("ghost",),
                                      source_fact_anchors=(FactAnchor("ghost", "x"),)))
            w.approve_question("q1")
        with self.assertRaises(ContractError):
            w.create_question(**draft(source_fact_anchors=(FactAnchor("r2", "fact"),)))
            w.approve_question("q1")  # anchor outside supporting set
        self.assertEqual(w.approved_preview().items, ())


class RegionImmutabilityTests(unittest.TestCase):
    def test_conflicting_reproposal_rejected_original_and_approval_intact(self):
        w = ws3()
        w.create_question(**draft())
        w.approve_question("q1")
        old = w.get_region_proposal("r1")
        replacement = dataclasses.replace(
            old, text_candidate="new raw", source_box=(800.0, 850.0, 999.0, 999.0),
            source_crop_ref="crop:different")
        with self.assertRaises(ContractError):
            w.submit_region_proposal(replacement)
        self.assertEqual(w.get_region_proposal("r1"), old)
        self.assertEqual(w.question_status("q1"), "approved")

    def test_identical_reproposal_is_idempotent_noop(self):
        w = ws3()
        w.create_question(**draft())
        w.approve_question("q1")
        w.submit_region_proposal(w.get_region_proposal("r1"))
        self.assertEqual(w.question_status("q1"), "approved")


class IdentityBindingTests(unittest.TestCase):
    def test_region_other_page_or_render_refused(self):
        pg = page()
        for target_page in (page(page_index=1), page(render_sha256="c" * 64),
                            page(source_sha256="c" * 64), page(source_id="other")):
            target = Workspace(target_page)
            with self.assertRaises(ContractError):
                target.submit_region_proposal(region(pg, "r1"))

    def test_question_other_bytes_reused_id_refused(self):
        w = ws3()
        p = proposal(w)
        self.assertEqual(p.identity, w.source.identity)
        target_pg = page(source_sha256="c" * 64)
        target = Workspace(target_pg)
        for rid in ("r1", "r2", "r3"):
            target.submit_region_proposal(region(target_pg, rid))
            target.review_region(rid, teacher_text=rid + " fact", inclusion="included")
        with self.assertRaises(ContractError):
            target.submit_question_proposal(p)

    def test_malformed_identity_rejected_not_filled(self):
        pg = page()
        with self.assertRaises(ContractError):
            region(pg, "r1", render_sha256="")
        with self.assertRaises(ContractError):
            region(pg, "r1", page_index=-1)
        with self.assertRaises(ContractError):
            region(pg, "r1", page_index=True)
        with self.assertRaises(ContractError):
            region(pg, "r1", source_id="")


class BoxAndAliasingTests(unittest.TestCase):
    def _submit_box(self, box):
        pg = page()
        w = Workspace(pg)
        w.submit_region_proposal(region(pg, "r1", source_box=box))
        return w

    def test_malformed_nonfinite_out_of_range_boxes_rejected(self):
        bad = [(0.0, 0.0, 1001.0, 10.0), (0.0, 0.0, 10.0, 1000.1),
               (-1.0, 0.0, 10.0, 10.0), (0.0, 0.0, math.inf, 10.0),
               (0.0, 0.0, math.nan, 10.0), (10.0, 0.0, 10.0, 10.0),
               (0.0, 0.0, 10.0), (0.0, 0.0, "10", 10.0),
               (0.0, 0.0, True, 10.0), None, "box"]
        for box in bad:
            with self.assertRaises(ContractError, msg=repr(box)):
                self._submit_box(box)

    def test_edge_of_range_box_accepted(self):
        w = self._submit_box((0, 0, 1000, 1000))
        self.assertEqual(w.get_region_proposal("r1").source_box,
                         (0.0, 0.0, 1000.0, 1000.0))

    def test_list_box_and_reasons_cannot_alias_stored_record(self):
        pg = page()
        box = [10.0, 20.0, 100.0, 120.0]
        reasons = ["needs_review"]
        w = Workspace(pg)
        w.submit_region_proposal(region(pg, "r1", source_box=box, review_reasons=reasons))
        box[0] = 999.0
        reasons.append("injected")
        stored = w.get_region_proposal("r1")
        self.assertEqual(stored.source_box, (10.0, 20.0, 100.0, 120.0))
        self.assertEqual(stored.review_reasons, ("needs_review",))


if __name__ == "__main__":
    unittest.main()
