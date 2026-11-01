"""Executable offline example for the source/review contract slice.

SYNTHETIC INPUT ONLY. No model API calls, no network, no real school
pages. This proves contract plumbing only -- it is NOT visual/OCR
quality proof and NOT evidence that any real model works.

Run from this file's directory:  python example_offline.py
"""
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")  # Arabic output on any console
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from source_contract import (  # noqa: E402
    FactAnchor, RegionProposal, SourcePage, Workspace)

PAGE = SourcePage(
    source_id="synthetic-page-1", source_sha256="a" * 64, page_index=0,
    render_sha256="b" * 64, width=1000, height=1400,
    rights_ref="synthetic-fixture-rights-note",
    coordinate_convention="normalized_0_1000_top_left")


def proposal(rid, text, order):
    return RegionProposal(
        region_id=rid, source_id=PAGE.source_id, source_sha256=PAGE.source_sha256,
        page_index=PAGE.page_index, render_sha256=PAGE.render_sha256,
        source_box=(10.0, 10.0, 500.0, 120.0),
        box_origin="synthetic_geometry_fixture", semantic_label="paragraph",
        semantic_label_origin="synthetic_label_fixture", text_candidate=text,
        source_crop_ref=f"crop://{rid}", order_candidate=order,
        review_reasons=("needs_teacher_review",),
        coordinate_convention=PAGE.coordinate_convention)


def main() -> int:
    ws = Workspace(PAGE)
    print(f"durability: {ws.durability_label} (is_durable={ws.is_durable})")
    print(f"rights_is_clearance: {PAGE.rights_is_clearance} (metadata only)")

    text1 = "الماءُ يَتَبَخَّرُ عِنْدَ التَّسْخينِ"
    text2 = "البُخارُ يَتَكاثَفُ على سَطْحٍ بارِدٍ"
    ws.submit_region_proposal(proposal("r1", text1, 1))  # synthetic fixture
    ws.submit_region_proposal(proposal("r2", text2, 2))
    ws.review_region("r1", teacher_text=text1, inclusion="included")
    ws.review_region("r2", teacher_text=text2, inclusion="included")
    ws.set_order_relationship("r1", "r2", present=True)

    ws.create_question(
        question_id="q1", grade="5", objective="اشرح دورة الماء",
        bloom_task_rationale="explain across two facts",
        question="ماذا يحدث للماء عند التسخين ثم التبريد؟",
        answer_guidance="يتبخر الماء بالتسخين ثم يتكاثف بخاره على البارد.",
        supporting_region_ids=("r1", "r2"),
        source_fact_anchors=(FactAnchor("r1", "يَتَبَخَّرُ"),
                             FactAnchor("r2", "يَتَكاثَفُ")),
        required_order_relationships=(("r1", "r2"),))
    print(f"before approval: q1={ws.question_status('q1')}")

    ws.edit_question("q1", question="اشرح ماذا يحدث للماء عند التسخين ثم التبريد؟")
    snap = ws.question_snapshot("q1")  # original draft stays intact
    print(f"lineage origin={snap.origin}")
    print(f"  original question: {snap.original.question}")
    print(f"  current  question: {snap.current.question}")

    ws.approve_question("q1")  # the only path into the preview
    print(f"after approval:  q1={ws.question_status('q1')}")
    preview = ws.approved_preview()
    for item in preview.items:
        print(f"preview[{item.question_id}] fingerprint={item.dependency_fingerprint[:12]}...")
        for anchor in item.resolved_anchors:
            print(f"  anchor {anchor.region_id}: {anchor.exact_quote!r}")

    ws.reject_question("q1")  # explicit teacher rejection
    print(f"after rejection: q1={ws.question_status('q1')} "
          f"(preview items={len(ws.approved_preview().items)})")
    ws.approve_question("q1")  # only an explicit approval restores it
    print(f"after reapprove: q1={ws.question_status('q1')} "
          f"(preview items={len(ws.approved_preview().items)})")

    ws.review_region("r1", teacher_text="نص معدّل", inclusion="included")
    print(f"after r1 edit:   q1={ws.question_status('q1')} "
          f"(preview items={len(ws.approved_preview().items)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
