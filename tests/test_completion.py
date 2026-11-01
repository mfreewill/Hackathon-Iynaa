"""Retained tests for the completion round: order-only dependencies,
original/current lineage + rejection, digest/whitespace grammar, and
explicit coordinate conventions. Synthetic fixtures only."""
import dataclasses
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from source_contract import (  # noqa: E402
    ContractError, FactAnchor, QuestionProposal, RegionProposal,
    SourcePage, Workspace)

CONV = "normalized_0_1000_top_left"
RASTER = "raster_pixels_top_left"


def page(**kw):
    base = dict(source_id="src-1", source_sha256="a" * 64, page_index=0,
                render_sha256="b" * 64, width=1000, height=1400,
                rights_ref="synthetic", coordinate_convention=CONV)
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


def ws3(pg=None):
    pg = pg or page()
    w = Workspace(pg)
    for rid in ("r1", "r2", "r3"):
        w.submit_region_proposal(region(pg, rid))
        w.review_region(rid, teacher_text=rid + " fact", inclusion="included")
    return w


def draft(qid="q1", rid="r1", **kw):
    vals = dict(question_id=qid, grade="5", objective="obj",
                bloom_task_rationale="why", question="original question",
                answer_guidance="original answer",
                supporting_region_ids=(rid,),
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


def snapshot(case, w, qid):
    getter = getattr(w, "question_snapshot", None)
    case.assertTrue(callable(getter), "question_snapshot missing")
    return getter(qid)


def reject(case, w, qid):
    fn = getattr(w, "reject_question", None)
    case.assertTrue(callable(fn), "reject_question missing")
    return fn(qid)


class OrderOnlyDependencyTests(unittest.TestCase):
    def _ordered(self):
        w = ws3()
        w.set_order_relationship("r1", "r2", True)
        w.create_question(**draft(required_order_relationships=(("r1", "r2"),)))
        return w

    def test_order_only_endpoint_states_block_approval(self):
        for state in ("excluded", "unresolved"):
            w = ws3()
            w.review_region("r2", teacher_text="r2 fact", inclusion=state)
            w.set_order_relationship("r1", "r2", True)
            w.create_question(**draft(required_order_relationships=(("r1", "r2"),)))
            with self.assertRaises(ContractError, msg=state):
                w.approve_question("q1")
            self.assertEqual(w.approved_preview().items, ())

    def test_order_only_endpoint_unreviewed_blocks_approval(self):
        pg = page()
        w = Workspace(pg)
        for rid in ("r1", "r2"):
            w.submit_region_proposal(region(pg, rid))
        w.review_region("r1", teacher_text="r1 fact", inclusion="included")
        w.set_order_relationship("r1", "r2", True)  # r2 admitted, not reviewed
        w.create_question(**draft(required_order_relationships=(("r1", "r2"),)))
        with self.assertRaises(ContractError):
            w.approve_question("q1")

    def test_order_only_endpoint_needs_no_fact_anchor(self):
        w = self._ordered()  # anchor only on r1; r2 is order support only
        w.approve_question("q1")
        self.assertEqual(w.question_status("q1"), "approved")

    def test_order_only_edit_revokes_without_resurrection_unrelated_survives(self):
        w = self._ordered()
        w.create_question(**draft("q2", "r3"))
        w.approve_question("q1")
        w.approve_question("q2")
        w.review_region("r2", teacher_text="changed fact", inclusion="included")
        self.assertEqual(w.question_status("q1"), "invalidated")
        self.assertEqual(w.question_status("q2"), "approved")
        w.review_region("r2", teacher_text="r2 fact", inclusion="included")
        self.assertEqual(w.question_status("q1"), "invalidated")
        w.approve_question("q1")
        self.assertEqual(w.question_status("q1"), "approved")


class LineageSnapshotTests(unittest.TestCase):
    def test_direct_create_original_preserved_across_edits(self):
        w = ws3()
        w.create_question(**draft())
        before = snapshot(self, w, "q1")
        w.edit_question("q1", question="teacher question",
                        answer_guidance="teacher answer", grade="6",
                        objective="new obj", bloom_task_rationale="new why")
        after = snapshot(self, w, "q1")
        self.assertEqual(after.original.question, "original question")
        self.assertEqual(after.original.answer_guidance, "original answer")
        self.assertEqual(after.original.grade, "5")
        self.assertEqual(after.original.objective, "obj")
        self.assertEqual(after.original.bloom_task_rationale, "why")
        self.assertEqual(after.original.supporting_region_ids, ("r1",))
        self.assertEqual(after.original.source_fact_anchors,
                         (FactAnchor("r1", "fact"),))
        self.assertEqual(after.current.question, "teacher question")
        self.assertEqual(after.current.grade, "6")
        self.assertEqual(after.origin, "teacher_create")
        self.assertEqual(before.current.question, "original question")
        self.assertTrue(dataclasses.is_dataclass(after))
        with self.assertRaises((dataclasses.FrozenInstanceError, AttributeError)):
            after.current.question = "caller edit"
        with self.assertRaises((dataclasses.FrozenInstanceError, AttributeError)):
            after.original = after.current

    def test_provider_proposal_original_preserved_with_provenance(self):
        w = ws3()
        w.submit_question_proposal(proposal(w))
        snap0 = snapshot(self, w, "q1")
        self.assertEqual(snap0.origin, "model_proposal")
        self.assertEqual(snap0.original.question, "original question")
        w.edit_question("q1", question="teacher rewrite")
        snap1 = snapshot(self, w, "q1")
        self.assertEqual(snap1.original.question, "original question")
        self.assertEqual(snap1.current.question, "teacher rewrite")
        self.assertEqual(snap1.origin, "model_proposal")

    def test_snapshot_unknown_question_raises(self):
        w = ws3()
        getter = getattr(w, "question_snapshot", None)
        self.assertTrue(callable(getter))
        with self.assertRaises(ContractError):
            getter("nope")


class RejectionTests(unittest.TestCase):
    def test_rejected_edited_then_explicit_reapproval(self):
        w = ws3()
        w.create_question(**draft())
        w.create_question(**draft("q2", "r3"))
        w.approve_question("q1")
        w.approve_question("q2")
        reject(self, w, "q1")
        self.assertEqual(w.question_status("q1"), "rejected")
        self.assertEqual([i.question_id for i in w.approved_preview().items], ["q2"])
        # Unrelated source edit and a content edit keep it rejected.
        w.review_region("r3", teacher_text="r3 changed", inclusion="included")
        w.edit_question("q1", question="edited while rejected")
        self.assertEqual(w.question_status("q1"), "rejected")
        self.assertNotIn("q1", [i.question_id for i in w.approved_preview().items])
        # Explicit approval restores it, with the edited text.
        w.approve_question("q1")
        self.assertEqual(w.question_status("q1"), "approved")
        items = {i.question_id: i for i in w.approved_preview().items}
        self.assertEqual(items["q1"].question, "edited while rejected")

    def test_reapproval_requires_current_dependencies(self):
        w = ws3()
        w.create_question(**draft())
        reject(self, w, "q1")
        w.review_region("r1", teacher_text="no anchors here", inclusion="included")
        with self.assertRaises(ContractError):
            w.approve_question("q1")
        self.assertEqual(w.question_status("q1"), "rejected")

    def test_reject_provisional_and_unknown(self):
        w = ws3()
        w.create_question(**draft())
        reject(self, w, "q1")
        self.assertEqual(w.question_status("q1"), "rejected")
        with self.assertRaises(ContractError):
            reject(self, w, "ghost")


class GrammarTests(unittest.TestCase):
    def test_sha256_grammar_for_both_hashes(self):
        for field in ("source_sha256", "render_sha256"):
            for bad in ("", "x", "z" * 64, "a" * 63, "a" * 65, "a" * 63 + " "):
                with self.assertRaises(ContractError, msg=f"{field}={bad!r}"):
                    page(**{field: bad})

    def test_uppercase_digest_preserved_as_supplied(self):
        pg = page(source_sha256="A" * 64, render_sha256="B" * 64)
        self.assertEqual(pg.source_sha256, "A" * 64)
        self.assertEqual(pg.identity.render_sha256, "B" * 64)

    def test_whitespace_only_quote_rejected_exact_quote_kept(self):
        w = ws3()  # teacher text "r1 fact" does contain a space character
        with self.assertRaises(ContractError):
            w.create_question(**draft(source_fact_anchors=(FactAnchor("r1", " "),)))
            w.approve_question("q1")
        self.assertEqual(w.approved_preview().items, ())
        w = ws3()
        text = "  keep my spaces  "
        w.review_region("r1", teacher_text=text, inclusion="included")
        w.create_question(**draft(source_fact_anchors=(FactAnchor("r1", text),)))
        w.approve_question("q1")
        self.assertEqual(
            w.approved_preview().items[0].resolved_anchors[0].exact_quote, text)

    def test_blank_fields_provisional_but_not_approvable(self):
        for field in ("grade", "objective", "bloom_task_rationale",
                      "question", "answer_guidance"):
            w = ws3()
            w.create_question(**draft(**{field: "   "}))  # draft may exist
            self.assertEqual(w.question_status("q1"), "provisional", field)
            with self.assertRaises(ContractError, msg=field):
                w.approve_question("q1")
            self.assertNotEqual(w.question_status("q1"), "approved", field)
            self.assertEqual(w.approved_preview().items, (), field)
            w.edit_question("q1", **{field: "filled"})
            w.approve_question("q1")
            self.assertEqual(w.question_status("q1"), "approved", field)


class CoordinateAndBindingTests(unittest.TestCase):
    def test_unsupported_convention_rejected(self):
        with self.assertRaises(ContractError):
            Workspace(page(coordinate_convention="unsupported_scale_top_left"))

    def test_raster_pixels_asymmetric_bounds(self):
        pg = page(coordinate_convention=RASTER)  # 1000 wide, 1400 high
        w = Workspace(pg)
        w.submit_region_proposal(region(pg, "r1", source_box=(0, 0, 1000, 1400)))
        for bad in ((0, 0, 1200, 100), (0, 0, 100, 1401), (900, 0, 1001, 10)):
            with self.assertRaises(ContractError, msg=repr(bad)):
                w.submit_region_proposal(region(pg, "rx", source_box=bad))

    def test_source_cannot_be_rebound_publicly(self):
        w = ws3()
        w.create_question(**draft())
        w.approve_question("q1")
        old = w.source
        with self.assertRaises((AttributeError, ContractError)):
            w.source = page(source_sha256="c" * 64)
        self.assertEqual(w.source, old)
        self.assertEqual(w.question_status("q1"), "approved")


if __name__ == "__main__":
    unittest.main()
