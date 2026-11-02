"""Real-browser hosted usage-budget journey (test only).

The parent harness serves the real full app at ``BROWSER_BASE_URL``
on loopback with the existing ``ControlsProvider`` injected
server-side (explicitly not Meta), an installed sandboxed Chrome
at ``BROWSER_EXECUTABLE``, a spent-permit cap of exactly 2 (one
source analysis and one question generation) and an 1800-second
idle lease, so the only exhaustion in this journey comes from the
second generation attempt below. ``BROWSER_ARTIFACT_DIR``
receives one screenshot and one JSON of synthetic observations
only (method/path/status, request/source IDs and visible values);
no cookies or header dumps are captured anywhere.

All browser mechanics are composed from the adjacent shipped
``test_review_drafts`` module: this file imports that module only,
never its TestCase class at global scope and never by inheritance,
so discovery sees exactly one new TestCase here. The helper
instance is created with an existing helper test-method name and
driven through its own ``setUpClass``/``setUp``; setup, region and
question lookup, editor opening, approval, region commit and
snapshot/raw GET inspection are the helper's real DOM/GET paths.
A local raw GET is read-only inspection, never a model
invocation, and is not counted as one below.

After a real generation, approval and visible teacher preview,
one new DOM generation must be refused as an exact terminal
``error`` for its own request with validated detail
``usage_exhausted`` at HTTP 503 - never relabelled as a
pre-admission refusal or provider unavailability. Its pending
slots keep ``data-outcome="pending"`` while reading as closed
with no result, the visible UI names the usage limit in Arabic,
old source, questions, preview and unsent drafts survive, no
model result is fabricated and nothing resends the refused
generation. Ordinary review (a label-only region commit that
leaves every source fact untouched) still succeeds afterwards.
This test never fulfills a route, injects a proposal, calls a
backend mutation directly or alters any product clock.
"""
import json
import os
import pathlib
import unittest
from urllib.parse import quote, urlsplit

from playwright.sync_api import expect

import test_review_drafts as drafts

ASSERT_TIMEOUT_MS = 10_000

# An existing shipped helper case name: unittest requires the
# helper instance to name a real method, and the helper's own
# artifact capture keys file names off it.
EXISTING_HELPER_TEST = (
    "test_dirty_question_editors_survive_sibling_approval"
    "_and_preview_stays_acknowledged")

UNSENT_QUESTION_WORDING = "  صياغة سؤال غير مرسلة عند حد الاستخدام؟  "
NEW_REGION_LABEL = "تصنيف مراجعة لاحقة"
GENERATE_PATH = "/api/session/questions/generate"
SOURCE_PATH = "/api/session/source-analysis"

# Arabic stems that honestly name a usage limit / a closed slot
# with no result. Exact sentences belong to the shipped copy; the
# machine codes below (status, detail, outcome) stay exact.
USAGE_STEMS = (
    "استخدام", "استعمال", "استنفد", "نفدت الحصة", "حدّ الاستخدام",
    "بلغت حد")
NO_RESULT_STEMS = (
    "لا نتيجة", "دون نتيجة", "لم تصل نتيجة", "لا توجد نتيجة",
    "أُغلق", "مغلق", "لا مسودة", "دون مسودة")


def _detail_code(body):
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return detail.get("code")
    return detail


def _response_count(helper, method, path, status):
    return sum(
        1 for entry in helper.responses_log
        if entry["method"] == method and entry["path"] == path
        and entry["status"] == status)


class HostedUsageBudgetBrowserTests(unittest.TestCase):
    def test_usage_exhausted_generation_keeps_review_working(self):
        artifact_raw = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        self.assertTrue(
            artifact_raw, "BROWSER_ARTIFACT_DIR is required")

        drafts.ReviewDraftPreservationBrowserTests.setUpClass()
        helper = drafts.ReviewDraftPreservationBrowserTests(
            EXISTING_HELPER_TEST)
        helper.setUp()
        fx = helper.fx

        with helper.browser_page() as (context, page):
            # The two permitted provider operations: one source
            # analysis and this first generation.
            generated = helper.setup_generated(context, page)
            approved = next(
                item for item in generated["questions"]
                if item["slot_id"] == "slot-remember")
            other = next(
                item for item in generated["questions"]
                if item["slot_id"] == "slot-understand")
            self.assertEqual(approved["current_level"], "remember")
            self.assertEqual(other["current_level"], "understand")
            self.assertEqual(approved["status"], "draft")
            self.assertEqual(approved["blocks"], [])
            initial_slot_ids = [
                item["slot_id"]
                for item in generated["slot_outcomes"]]
            self.assertEqual(len(initial_slot_ids), 6)
            initial_generation_request_id = approved["request_id"]

            helper.approve_question(page, approved)
            acknowledged = helper.snapshot(context)
            ack_approved = helper.question_by_id(
                acknowledged, approved["question_id"])
            ack_other = helper.question_by_id(
                acknowledged, other["question_id"])
            self.assertEqual(ack_approved["status"], "approved")
            self.assertEqual(ack_approved["blocks"], [])
            preview = acknowledged["preview_items"]
            self.assertEqual(
                [entry["question_id"] for entry in preview],
                [approved["question_id"]])
            helper.tid(page, "workflow-nav-preview").click()
            expect(
                page.get_by_test_id("stage-teacher-preview")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            preview_items = page.get_by_test_id("preview-item")
            expect(preview_items).to_have_count(
                1, timeout=ASSERT_TIMEOUT_MS)
            expect(preview_items.first).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(preview_items.first, "preview-question")
            ).to_have_text(
                preview[0]["question"], timeout=ASSERT_TIMEOUT_MS)
            question_ids_before = [
                item["question_id"]
                for item in acknowledged["questions"]]
            helper.tid(page, "preview-return").click()
            expect(
                page.get_by_test_id("stage-question-review")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)

            # An unsent draft plus the displayed source reference
            # must survive the refusal untouched.
            other_card = helper.question_card(page, ack_other)
            helper.open_question_editor(other_card).fill(
                UNSENT_QUESTION_WORDING)
            source_image = helper.tid(page, "source-preview")
            expect(source_image).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            image_src = source_image.get_attribute("src")
            self.assertTrue(image_src)
            original_href = helper.tid(
                page, "source-original-link").get_attribute("href")
            self.assertTrue(original_href)
            visible_source_id = helper.tid(
                page, "source-id").text_content()
            self.assertEqual(
                visible_source_id, acknowledged["source_id"])
            self.assertEqual(
                _response_count(helper, "POST", SOURCE_PATH, 201), 1)
            self.assertEqual(
                _response_count(
                    helper, "POST", GENERATE_PATH, 201), 1)

            # Trigger one new generation through the DOM. The
            # response predicate deliberately does not filter on
            # status, so a wrong status cannot be waited away.
            helper.tid(page, "workflow-nav-source").click()
            expect(
                page.get_by_test_id("stage-source-review")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            generate_button = page.get_by_role(
                "button", name="اعتماد المصدر وتوليد الأسئلة",
                exact=True)
            expect(generate_button).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)
            checkpoint = len(helper.requests_log)
            with page.expect_response(
                    lambda item: item.request.method == "POST"
                    and urlsplit(item.url).path == GENERATE_PATH,
                    timeout=ASSERT_TIMEOUT_MS) as refused_info:
                generate_button.click()
            refused_response = refused_info.value
            self.assertEqual(
                refused_response.status, 503,
                "the over-cap generation must be refused with 503")
            body = refused_response.json()
            self.assertIsInstance(body, dict)

            refused_requests = [
                entry for entry in helper.requests_log[checkpoint:]
                if entry["method"] == "POST"
                and entry["path"] == GENERATE_PATH]
            self.assertEqual(len(refused_requests), 1)
            refused_body = refused_requests[0].get("json")
            self.assertIsInstance(refused_body, dict)
            refused_request_id = refused_body["request_id"]
            self.assertRegex(refused_request_id, r"^[0-9a-f]{32}$")
            self.assertEqual(
                refused_body["source_id"], acknowledged["source_id"])
            self.assertNotEqual(
                refused_request_id, initial_generation_request_id)

            # Exact terminal envelope for this very request: a
            # validated usage detail a teacher UI may name, never
            # a single-detail pre-admission body and never
            # provider_unavailable.
            self.assertEqual(
                body.get("request_id"), refused_request_id)
            self.assertIsInstance(body.get("raw_retention"), str)
            self.assertTrue(body["raw_retention"])
            self.assertEqual(
                _detail_code(body), "usage_exhausted")
            envelope_snapshot = body.get("snapshot")
            self.assertIsInstance(envelope_snapshot, dict)
            self.assertEqual(
                envelope_snapshot["source_id"],
                acknowledged["source_id"])
            refused_record = next(
                item for item in envelope_snapshot["requests"]
                if item["request_id"] == refused_request_id
                and item["kind"] == "questions")
            self.assertEqual(refused_record["state"], "terminal")
            self.assertEqual(refused_record["outcome"], "error")
            envelope_pending = [
                item for item in envelope_snapshot["slot_outcomes"]
                if item["request_id"] == refused_request_id]
            self.assertEqual(
                {item["slot_id"] for item in envelope_pending},
                set(initial_slot_ids))
            self.assertTrue(all(
                item["outcome"] == "pending"
                for item in envelope_pending))

            # No automatic resend follows the refusal.
            page.wait_for_timeout(1000)
            self.assertEqual(
                helper.mutations_since(checkpoint),
                [("POST", GENERATE_PATH)])

            # The acknowledged snapshot agrees and fabricates no
            # question, decision or preview change.
            fetched = helper.snapshot(context)
            self.assertEqual(
                fetched["source_id"], acknowledged["source_id"])
            self.assertEqual(
                [item["question_id"]
                 for item in fetched["questions"]],
                question_ids_before)
            self.assertFalse(any(
                item["request_id"] == refused_request_id
                for item in fetched["questions"]))
            self.assertEqual(
                helper.question_by_id(
                    fetched, approved["question_id"])["status"],
                "approved")
            self.assertEqual(
                [entry["question_id"]
                 for entry in fetched["preview_items"]],
                [approved["question_id"]])
            fetched_pending = [
                item for item in fetched["slot_outcomes"]
                if item["request_id"] == refused_request_id]
            self.assertEqual(
                {item["slot_id"] for item in fetched_pending},
                set(initial_slot_ids))
            self.assertTrue(all(
                item["outcome"] == "pending"
                for item in fetched_pending))

            # In the DOM this request's slots stay pending-coded
            # but read as closed with no result, not as working.
            helper.tid(page, "workflow-nav-questions").click()
            expect(
                page.get_by_test_id("stage-question-review")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            pending_rows = page.locator(
                "[data-testid='slot-outcome']"
                "[data-outcome='pending']")
            expect(pending_rows).to_have_count(
                len(initial_slot_ids), timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(
                {row.get_attribute("data-slot-id")
                 for row in pending_rows.all()},
                set(initial_slot_ids))
            for row in pending_rows.all():
                slot_text = helper.tid(
                    row, "slot-outcome-text").inner_text()
                self.assertTrue(
                    any(stem in slot_text
                        for stem in NO_RESULT_STEMS), slot_text)
                self.assertNotIn("pending", slot_text)
                self.assertNotIn("وصلت مسودة", slot_text)
                self.assertNotIn("امتنع النموذج", slot_text)
            operation_status = page.get_by_test_id(
                "provider-operation-status")
            operation_text = (
                operation_status.text_content() or ""
                if operation_status.count() else "")
            self.assertNotIn("قيد التنفيذ لدى المزوّد", operation_text)
            self.assertNotIn("ما زال قيد التنفيذ", operation_text)

            # The visible status names the usage limit in Arabic
            # and does not pass the refusal off as unavailability.
            visible_status_texts = []
            for test_id in (
                    "workspace-status", "provider-operation-status"):
                locator = page.get_by_test_id(test_id)
                if locator.count() and locator.first.is_visible():
                    visible_status_texts.append(
                        locator.first.inner_text())
            combined_status = " ".join(visible_status_texts)
            self.assertTrue(
                any(stem in combined_status
                    for stem in USAGE_STEMS), combined_status)
            self.assertNotIn("provider_unavailable", combined_status)
            self.assertNotIn("غير متاحة في هذا التشغيل", combined_status)

            # Capture the refused state before ordinary review
            # below legitimately replaces the status line.
            artifact_dir = pathlib.Path(artifact_raw)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = (
                artifact_dir / "hosted-budget-final.png")
            page.screenshot(
                path=str(screenshot_path), full_page=True,
                timeout=ASSERT_TIMEOUT_MS)

            # Old source, cards, preview and the unsent draft are
            # still exactly what they were before the attempt.
            self.assertEqual(
                source_image.get_attribute("src"), image_src)
            self.assertEqual(
                helper.tid(page, "source-original-link")
                .get_attribute("href"), original_href)
            self.assertEqual(
                helper.tid(page, "source-id").text_content(),
                visible_source_id)
            expect(
                page.get_by_test_id("question-card")
            ).to_have_count(2, timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.open_question_editor(other_card)
            ).to_have_value(
                UNSENT_QUESTION_WORDING, timeout=ASSERT_TIMEOUT_MS)
            helper.tid(page, "workflow-nav-preview").click()
            expect(
                page.get_by_test_id("stage-teacher-preview")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(preview_items).to_have_count(
                1, timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(preview_items.first, "preview-question")
            ).to_have_text(
                preview[0]["question"], timeout=ASSERT_TIMEOUT_MS)
            helper.tid(page, "preview-return").click()
            expect(
                page.get_by_test_id("stage-question-review")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)

            # Local raw GET inspection is permitted and proves
            # nothing about a new invocation: provider POST
            # successes remain exactly the initial two.
            raw = helper.api_json(
                context, "/api/session/requests/"
                + quote(initial_generation_request_id, safe="")
                + "/raw")
            self.assertEqual(
                raw["request_id"], initial_generation_request_id)
            self.assertEqual(raw["kind"], "questions")
            self.assertEqual(raw["raw_retention"], "retained")
            self.assertEqual(
                _response_count(helper, "POST", SOURCE_PATH, 201), 1)
            self.assertEqual(
                _response_count(
                    helper, "POST", GENERATE_PATH, 201), 1)
            self.assertEqual(
                _response_count(
                    helper, "POST", GENERATE_PATH, 503), 1)
            self.assertEqual(
                sum(1 for entry in helper.requests_log
                    if entry["method"] == "POST"
                    and entry["path"] == GENERATE_PATH), 2)

            # Ordinary review still works after the limit refusal:
            # a label-only region commit (same acknowledged source
            # text and inclusion, so no source fact changes) is
            # accepted with 200 and visibly acknowledged.
            region_b = helper.region_by_raw(fetched, fx["RAW_B"])
            self.assertEqual(
                region_b["current_text"], fx["EDITED_B"])
            helper.tid(page, "workflow-nav-source").click()
            expect(
                page.get_by_test_id("stage-source-review")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            helper.commit_region(
                page, region_b, region_b["current_text"],
                NEW_REGION_LABEL, "included")
            expect(
                helper.tid(
                    helper.region_item(page, region_b),
                    "region-heading")
            ).to_have_text(
                NEW_REGION_LABEL, timeout=ASSERT_TIMEOUT_MS)
            expect(
                page.get_by_test_id("workspace-status")
            ).to_have_text(
                "حُفظت مراجعة المقطع.", timeout=ASSERT_TIMEOUT_MS)
            after_review = helper.snapshot(context)
            reviewed_b = helper.region_by_raw(
                after_review, fx["RAW_B"])
            self.assertEqual(
                reviewed_b["current_label"], NEW_REGION_LABEL)
            self.assertEqual(
                reviewed_b["current_text"], fx["EDITED_B"])
            still_approved = helper.question_by_id(
                after_review, approved["question_id"])
            still_other = helper.question_by_id(
                after_review, other["question_id"])
            self.assertEqual(still_approved["status"], "approved")
            self.assertEqual(still_approved["blocks"], [])
            self.assertEqual(still_other["blocks"], [])
            self.assertEqual(
                [entry["question_id"]
                 for entry in after_review["preview_items"]],
                [approved["question_id"]])
            self.assertEqual(
                sum(1 for entry in helper.requests_log
                    if entry["method"] == "POST"
                    and entry["path"] == GENERATE_PATH), 2)

            # Synthetic-only JSON beside the refused-state
            # screenshot captured above.
            observations = {
                "source_id": visible_source_id,
                "initial_generation_request_id":
                    initial_generation_request_id,
                "refused_request_id": refused_request_id,
                "refused_status": 503,
                "refused_detail_code": "usage_exhausted",
                "refused_request_state": "terminal",
                "refused_request_outcome": "error",
                "pending_slot_ids": sorted(initial_slot_ids),
                "provider_successes": {
                    "source_analysis_201": 1,
                    "questions_generate_201": 1,
                    "questions_generate_503": 1,
                },
                "responses": helper.responses_log,
                "external_urls": helper.external_urls,
                "page_errors": helper.page_errors,
            }
            observations_path = (
                artifact_dir / "hosted-budget.json")
            observations_path.write_text(
                json.dumps(
                    observations, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self.assertGreater(screenshot_path.stat().st_size, 0)
            self.assertGreater(observations_path.stat().st_size, 0)
            helper.assert_clean_network()


if __name__ == "__main__":
    unittest.main()
