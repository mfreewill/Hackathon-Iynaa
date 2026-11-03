"""Final-review regression cases for the approved compact UI.

Exactly two focused cases in one ordinary ``unittest.TestCase``.
The shared browser machinery is reused by importing
``test_review_drafts`` as a module and instantiating its test
class only inside each method; nothing here inherits or aliases
that class. Shared setup now chooses the file first and
question actions select the retained card through studio
question navigation at action/visibility boundaries only;
snapshot and locator observation never navigates.

The harness serves the app with the existing server-only
synthetic ``ControlsProvider`` (not Meta); this module imports
no provider, app or backend code and makes no live-model claim.
Every teacher mutation goes through real DOM controls.
Inspection requests are GET-only snapshot and retained-raw
reads on the supplied same-origin base. Required environment:
``BROWSER_BASE_URL`` and ``BROWSER_EXECUTABLE``; optional
``BROWSER_ARTIFACT_DIR`` receives the shared helper's short
JSON plus screenshot capture in its ``finally`` path.

Intended first RED points before the final UI exists: the
source case stops at the missing read-mode
``region-current-text``; the question case stops at a dirty
``question-approve`` that is not yet disabled. Assertions after
those points stay unexecuted until the approved UI lands.
Arabic wording values are always compared exactly as the
server acknowledged them.
"""
import copy
import unittest
from urllib.parse import quote

from playwright.sync_api import expect

import test_review_drafts


class FinalReviewBrowserTests(unittest.TestCase):
    def test_source_read_mode_and_notes(self):
        helper = test_review_drafts.ReviewDraftPreservationBrowserTests()
        type(helper).setUpClass()
        helper.setUp()
        helper._testMethodName = self._testMethodName
        timeout = test_review_drafts.ASSERT_TIMEOUT_MS
        with helper.browser_page() as (context, page):
            snapshot = helper.start_source_analysis(context, page)
            region_a = helper.region_by_raw(snapshot, helper.fx["RAW_A"])
            region_b = helper.region_by_raw(snapshot, helper.fx["RAW_B"])
            item_a = helper.region_item(page, region_a)
            item_b = helper.region_item(page, region_b)

            # First final-UI assertion (intended first RED on the
            # pre-final UI): read mode shows each exact
            # acknowledged current text and its model flags before
            # any edit, while the existing editor fields are
            # present but hidden, not removed.
            for region, item in ((region_a, item_a), (region_b, item_b)):
                current = helper.tid(item, "region-current-text")
                expect(current).to_be_visible(timeout=timeout)
                expect(current).to_have_text(
                    region["current_text"], timeout=timeout)
                flags = helper.tid(item, "region-model-flag")
                expect(flags).to_have_count(len(region["model_flags"]))
                for flag_locator, flag in zip(
                        flags.all(), region["model_flags"]):
                    # Open the flag's nearest ancestor disclosure
                    # first when one wraps it; with no details (or
                    # zero flags) this changes nothing.
                    flag_details = flag_locator.locator(
                        "xpath=ancestor::details[1]")
                    if flag_details.count() > 0:
                        flag_summary = flag_details.locator(
                            "xpath=./summary[1]")
                        expect(flag_summary).to_be_visible(
                            timeout=timeout)
                        if flag_details.get_attribute("open") is None:
                            flag_summary.click()
                    expect(flag_locator).to_be_visible(timeout=timeout)
                    expect(flag_locator).to_have_text(
                        flag, timeout=timeout)
                for marker in ("region-current-text-input",
                               "region-current-label-input"):
                    field = helper.tid(item, marker)
                    expect(field).to_have_count(1)
                    expect(field).to_be_hidden(timeout=timeout)

            # Retained source raw for the actual request ID carried
            # by the source regions (GET-only inspection).
            request_id = region_a["request_id"]
            helper.assertEqual(region_b["request_id"], request_id)
            raw_path = ("/api/session/requests/"
                        + quote(request_id, safe="") + "/raw")
            payload = helper.api_json(context, raw_path)
            helper.assertEqual(payload["request_id"], request_id)
            baseline_raw = copy.deepcopy(payload)
            raw = payload["provider_result"]["raw"]

            notes_box = helper.tid(page, "source-review-notes")
            # Same disclosure handling as the flags: open the
            # notes' nearest ancestor details (if any) through
            # its direct summary before requiring visibility.
            notes_details = notes_box.locator(
                "xpath=ancestor::details[1]")
            if notes_details.count() > 0:
                notes_summary = notes_details.locator(
                    "xpath=./summary[1]")
                expect(notes_summary).to_be_visible(timeout=timeout)
                if notes_details.get_attribute("open") is None:
                    notes_summary.click()
            expect(notes_box).to_be_visible(timeout=timeout)
            for note in raw["review_notes"]:
                expect(notes_box).to_contain_text(note, timeout=timeout)
            helper.assertIs(raw["page_continues"], False)
            continuation = helper.tid(page, "source-page-continuation")
            expect(continuation).to_be_visible(timeout=timeout)
            helper.assertEqual(
                continuation.get_attribute("data-continues"), "false")
            # Readable Arabic wording attributed to the model. The
            # false flag is only the model's statement for this
            # submitted page; nothing here treats it as proof
            # about content anywhere else.
            expect(continuation).to_contain_text(
                "النموذج", timeout=timeout)

            # Opening A's editor (or a disclosure) and editing
            # without Save must not mutate anything.
            checkpoint = len(helper.requests_log)
            helper.tid(item_a, "region-edit").click()
            text_input = helper.tid(item_a, "region-current-text-input")
            label_input = helper.tid(item_a, "region-current-label-input")
            expect(text_input).to_be_visible(timeout=timeout)
            expect(label_input).to_be_visible(timeout=timeout)
            expect(item_a.get_by_label(
                "النص الحالي", exact=True)).to_be_visible(timeout=timeout)
            expect(item_a.get_by_label(
                "التصنيف الحالي", exact=True)).to_be_visible(
                    timeout=timeout)
            expect(text_input).to_have_value(
                region_a["current_text"], timeout=timeout)
            expect(label_input).to_have_value(
                region_a["current_label"], timeout=timeout)
            expect(helper.tid(item_a, "region-review-commit")
                   ).to_be_visible(timeout=timeout)
            disclosures = item_a.locator("details")
            if disclosures.count() > 0:
                summary = disclosures.first.locator("summary")
                summary.click()
                summary.click()
            text_input.fill(helper.fx["EDITED_A"])
            helper.tid(item_a, "region-inclusion-input").select_option(
                "included")
            helper.assertEqual(helper.mutations_since(checkpoint), [])
            untouched = helper.snapshot(context)
            helper.assertEqual(
                helper.region_by_raw(untouched, helper.fx["RAW_A"]),
                region_a)
            helper.assertEqual(
                helper.region_by_raw(untouched, helper.fx["RAW_B"]),
                region_b)

            # One real acknowledged commit: only current_text
            # changes (exact canonical EDITED_A), the label keeps
            # its value and inclusion becomes included.
            helper.commit_region(
                page, region_a, helper.fx["EDITED_A"],
                region_a["current_label"], "included")
            helper.assertEqual(
                helper.mutations_since(checkpoint),
                [("PUT", helper.region_path(region_a))])
            acknowledged = helper.snapshot(context)
            ack_a = helper.region_by_raw(
                acknowledged, helper.fx["RAW_A"])
            ack_b = helper.region_by_raw(
                acknowledged, helper.fx["RAW_B"])
            helper.assertEqual(ack_a["current_text"], helper.fx["EDITED_A"])
            helper.assertEqual(
                ack_a["current_label"], region_a["current_label"])
            helper.assertEqual(ack_a["inclusion"], "included")
            helper.assertEqual(ack_a["raw_text"], region_a["raw_text"])
            helper.assertEqual(
                ack_a["model_flags"], region_a["model_flags"])
            helper.assertEqual(ack_b, region_b)
            helper.assertEqual(ack_b["inclusion"], "unreviewed")
            helper.assertEqual(
                helper.api_json(context, raw_path), baseline_raw)

            # Read text updated, editor closed after the
            # acknowledgment, and B's read view preserved.
            item_a = helper.region_item(page, ack_a)
            current_a = helper.tid(item_a, "region-current-text")
            expect(current_a).to_be_visible(timeout=timeout)
            expect(current_a).to_have_text(
                helper.fx["EDITED_A"], timeout=timeout)
            expect(helper.tid(item_a, "region-current-text-input")
                   ).to_be_hidden(timeout=timeout)
            expect(helper.tid(item_a, "region-current-label-input")
                   ).to_be_hidden(timeout=timeout)
            expect(helper.tid(item_b, "region-current-text")
                   ).to_have_text(
                       region_b["current_text"], timeout=timeout)

            # The genuine server gate opens with one included
            # region. Generation is deliberately not requested in
            # this case (the server-only provider requires both
            # canonical texts before it can answer).
            helper.assertTrue(acknowledged["can_generate"])
            helper.assertEqual(acknowledged["included_count"], 1)
            expect(page.get_by_role(
                "button", name="اعتماد المصدر وتوليد الأسئلة",
                exact=True)).to_be_enabled(timeout=timeout)
            helper.assert_clean_network()

    def test_dirty_question_requires_explicit_save_or_discard(self):
        helper = test_review_drafts.ReviewDraftPreservationBrowserTests()
        type(helper).setUpClass()
        helper.setUp()
        helper._testMethodName = self._testMethodName
        timeout = test_review_drafts.ASSERT_TIMEOUT_MS
        draft_a = test_review_drafts.QUESTION_A_DRAFT_WORDING
        saved_b = test_review_drafts.journey.EDITED_B_WORDING
        with helper.browser_page() as (context, page):
            snapshot = helper.setup_generated(context, page)
            region_a = helper.region_by_raw(snapshot, helper.fx["RAW_A"])
            region_b = helper.region_by_raw(snapshot, helper.fx["RAW_B"])
            question_a = helper.question_by_id(
                snapshot, region_a["linked_question_ids"][0])
            question_b = helper.question_by_id(
                snapshot, region_b["linked_question_ids"][0])
            helper.assertEqual(question_a["status"], "draft")
            helper.assertEqual(question_b["status"], "draft")

            # Exact original raw questions before any edit
            # (GET-only inspection under the actual request ID).
            request_id = question_a["request_id"]
            helper.assertEqual(question_b["request_id"], request_id)
            raw_path = ("/api/session/requests/"
                        + quote(request_id, safe="") + "/raw")
            baseline_payload = helper.api_json(context, raw_path)
            helper.assertEqual(baseline_payload["request_id"], request_id)
            baseline_raw = copy.deepcopy(baseline_payload)

            card_a = helper.question_card(page, question_a)
            card_b = helper.question_card(page, question_b)
            checkpoint = len(helper.requests_log)
            helper.open_question_editor(card_a).fill(draft_a)
            helper.open_question_editor(card_b).fill(saved_b)
            handle_b = card_b.element_handle()

            # Intended first RED on the old baseline: a question
            # with unsaved wording must not be approvable, and its
            # dirty state must be described in save wording.
            expect(helper.tid(card_a, "question-approve")
                   ).to_be_disabled(timeout=timeout)
            expect(helper.tid(card_b, "question-approve")
                   ).to_be_disabled(timeout=timeout)
            helper.ensure_question_visible(card_a)
            draft_status = helper.tid(card_a, "question-draft-status")
            expect(draft_status).to_be_visible(timeout=timeout)
            status_text = draft_status.text_content() or ""
            helper.assertTrue(
                "حفظ" in status_text or "محفوظ" in status_text,
                status_text)
            helper.assertEqual(helper.mutations_since(checkpoint), [])
            untouched = helper.snapshot(context)
            helper.assertEqual(
                helper.question_by_id(
                    untouched, question_a["question_id"]), question_a)
            helper.assertEqual(
                helper.question_by_id(
                    untouched, question_b["question_id"]), question_b)

            # Discard only A: acknowledged wording returns and
            # approval is eligible again; B's dirty editor and
            # node stay intact and nothing was sent.
            cancel_mark = len(helper.requests_log)
            helper.tid(card_a, "question-edit-cancel").click()
            helper.assertEqual(helper.mutations_since(cancel_mark), [])
            expect(helper.tid(card_a, "question-heading")
                   ).to_have_text(
                       question_a["current_question"], timeout=timeout)
            expect(helper.tid(card_a, "question-approve")
                   ).to_be_enabled(timeout=timeout)
            helper.assertTrue(helper.same_node(
                handle_b, helper.question_card(page, question_b)))
            expect(helper.tid(card_b, "question-edit-question")
                   ).to_have_value(saved_b, timeout=timeout)

            # Explicit approval of clean A approves only A, with
            # exactly its original saved wording in the server
            # preview; neither local draft wording appears there.
            helper.approve_question(page, question_a)
            acknowledged = helper.snapshot(context)
            ack_a = helper.question_by_id(
                acknowledged, question_a["question_id"])
            ack_b = helper.question_by_id(
                acknowledged, question_b["question_id"])
            helper.assertEqual(ack_a["status"], "approved")
            helper.assertEqual(ack_b, question_b)
            preview = acknowledged["preview_items"]
            helper.assertEqual(
                [entry["question_id"] for entry in preview],
                [question_a["question_id"]])
            helper.assertEqual(
                preview[0]["question"], question_a["current_question"])
            helper.assertNotIn(draft_a, preview[0]["question"])
            helper.assertNotIn(saved_b, preview[0]["question"])
            helper.assertTrue(helper.same_node(
                handle_b, helper.question_card(page, ack_b)))
            expect(helper.tid(card_b, "question-edit-question")
                   ).to_have_value(saved_b, timeout=timeout)

            # Guided navigation, when present, must stay on the
            # questions step after this first approval. The check
            # is conditional on the attribute existing so the old
            # baseline still stops first at dirty approval above.
            work_pane = helper.tid(page, "work-pane")
            if work_pane.count() > 0:
                active_step = work_pane.first.get_attribute(
                    "data-active-step")
                if active_step is not None:
                    helper.assertEqual(active_step, "questions")

            # Supplementary evidence stays reachable on A's card:
            # the primary question and guidance are visible before
            # any optional details disclosure is opened, and the
            # evidence keeps its raw/current source region IDs.
            card_a = helper.question_card(page, ack_a)
            helper.ensure_question_visible(card_a)
            expect(helper.tid(card_a, "question-heading")
                   ).to_be_visible(timeout=timeout)
            guidance = helper.tid(card_a, "question-guidance")
            expect(guidance).to_be_visible(timeout=timeout)
            expect(guidance).to_have_text(
                ack_a["current_answer_guidance"], timeout=timeout)
            details_all = helper.tid(card_a, "question-details")
            if details_all.count() > 0:
                details = details_all.first
                summary = details.locator("summary")
                if summary.count() > 0:
                    if details.get_attribute("open") is None:
                        summary.click()
                else:
                    details.click()
            helper.assertEqual(ack_a["anchors"], question_a["anchors"])
            helper.assertEqual(
                ack_a["supporting_region_ids"],
                question_a["supporting_region_ids"])
            facts = helper.tid(card_a, "question-evidence-fact")
            expect(facts).to_have_count(len(question_a["anchors"]))
            for fact_locator, anchor in zip(
                    facts.all(), question_a["anchors"]):
                helper.assertEqual(
                    fact_locator.get_attribute("data-region-id"),
                    anchor[0])
                expect(fact_locator).to_be_visible(timeout=timeout)
                expect(fact_locator).to_have_text(
                    anchor[1], timeout=timeout)

            # Actual preview through guided navigation when
            # offered: A is visible with no unsaved wording, and
            # B's draft survives the round trip.
            nav_all = helper.tid(page, "workflow-nav-preview")
            if nav_all.count() > 0:
                nav = nav_all.first
                expect(nav).to_be_visible(timeout=timeout)
                nav.click()
                expect(helper.tid(page, "preview-item")).to_have_count(1)
                item_a = page.locator(
                    "[data-testid='preview-item']"
                    f"[data-question-id=\"{ack_a['question_id']}\"]")
                expect(item_a).to_be_visible(timeout=timeout)
                expect(item_a).to_contain_text(
                    question_a["current_question"], timeout=timeout)
                preview_text = helper.tid(
                    page, "preview-list").text_content() or ""
                helper.assertNotIn(draft_a, preview_text)
                helper.assertNotIn(saved_b, preview_text)
                return_all = helper.tid(page, "preview-return")
                if return_all.count() > 0:
                    expect(return_all.first).to_be_visible(
                        timeout=timeout)
                    return_all.first.click()
            card_b = helper.question_card(page, question_b)
            helper.assertTrue(helper.same_node(handle_b, card_b))
            expect(helper.tid(card_b, "question-edit-question")
                   ).to_have_value(saved_b, timeout=timeout)

            # Explicit save of B is a genuine PATCH and is not an
            # approval; only the later explicit approval adds the
            # second preview entry with the new saved wording.
            helper.ensure_question_visible(card_b)
            helper.click_expect(
                page, "PATCH", helper.question_path(question_b), 200,
                helper.tid(card_b, "question-edit-commit"))
            saved = helper.snapshot(context)
            saved_b_question = helper.question_by_id(
                saved, question_b["question_id"])
            helper.assertEqual(saved_b_question["status"], "draft")
            helper.assertEqual(
                saved_b_question["current_question"], saved_b)
            helper.assertEqual(
                saved_b_question["original_question"],
                question_b["original_question"])
            helper.assertEqual(
                [entry["question_id"]
                 for entry in saved["preview_items"]],
                [question_a["question_id"]])
            helper.approve_question(page, saved_b_question)
            final = helper.snapshot(context)
            final_a = helper.question_by_id(
                final, question_a["question_id"])
            final_b = helper.question_by_id(
                final, question_b["question_id"])
            helper.assertEqual(final_b["status"], "approved")
            helper.assertEqual(
                final_a["original_question"],
                question_a["original_question"])
            helper.assertEqual(
                final_b["original_question"],
                question_b["original_question"])
            final_preview = final["preview_items"]
            helper.assertEqual(len(final_preview), 2)
            helper.assertEqual(
                {entry["question_id"] for entry in final_preview},
                {question_a["question_id"], question_b["question_id"]})
            entry_b = next(
                entry for entry in final_preview
                if entry["question_id"] == question_b["question_id"])
            helper.assertEqual(entry_b["question"], saved_b)
            helper.assertEqual(
                helper.api_json(context, raw_path), baseline_raw)
            helper.assert_clean_network()


if __name__ == "__main__":
    unittest.main()


