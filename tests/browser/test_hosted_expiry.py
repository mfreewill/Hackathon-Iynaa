"""Real-browser hosted idle-lease expiry journey (test only).

The parent harness serves the real full app at ``BROWSER_BASE_URL``
on loopback with the existing ``ControlsProvider`` injected
server-side (explicitly not Meta), an installed sandboxed Chrome at
``BROWSER_EXECUTABLE`` and a real shortened session idle lease
named by ``BROWSER_SESSION_IDLE_SECONDS`` (this test requires at
least 8 seconds; the parent uses 20-30 so real setup completes).
``BROWSER_ARTIFACT_DIR`` receives one screenshot and one JSON of
synthetic observations only (method/path/status, source IDs and
visible values); no cookies, headers or private values are written.

All browser mechanics are composed from the adjacent shipped
``test_review_drafts`` module: this file imports that module only,
never its TestCase class at global scope and never by inheritance,
so discovery sees exactly one new TestCase here. The helper
instance is created with an existing helper test-method name and
driven through its own ``setUpClass``/``setUp``; setup, region and
question lookup, editor opening, approval and snapshot reads are
the helper's real DOM/GET paths.

The journey completes source setup, both canonical
``EDITED_A``/``EDITED_B`` region commits and generation, approves
the actual ``slot-remember`` question after checking it has no
approval blocks, verifies the visible teacher preview, then
leaves a unique unsent question wording and region text open.
After every real response has settled, the idle interval is
observed with DOM waits only: this test makes no context/API
request, sends no heartbeat or keepalive, never fulfills a route,
never injects a proposal, never calls a backend mutation directly
and never alters any product clock.

What expiry must prove is conservative client withdrawal only:
the hosted-session-expired notice and the persistent
review-currency warning become visible, no teacher preview item
remains current, new writes (Start, settings, generate, region
commit, question approve/edit commit) are disabled, while the
source preview/reference, setup inputs and readable open drafts
stay exactly as displayed. Nothing here claims the server
deleted retained work.
"""
import json
import os
import pathlib
import time
import unittest

from playwright.sync_api import expect

import test_review_drafts as drafts

NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000
EXPIRY_SLACK_MS = 15_000

# An existing shipped helper case name: unittest requires the
# helper instance to name a real method, and the helper's own
# artifact capture keys file names off it.
EXISTING_HELPER_TEST = (
    "test_dirty_question_editors_survive_sibling_approval"
    "_and_preview_stays_acknowledged")

# Teacher values authored by this journey; both are deliberately
# left unsent when the lease lapses.
UNSENT_QUESTION_WORDING = "  صياغة سؤال غير مرسلة عند انتهاء الجلسة؟  "
UNSENT_REGION_TEXT = "  نص مقطع غير مرسل عند انتهاء الجلسة  "


def _has_arabic(text):
    return any("\u0600" <= char <= "\u06ff" for char in text)


class HostedSessionExpiryBrowserTests(unittest.TestCase):
    def test_idle_lease_expiry_withdraws_currentness_and_preserves_visible_work(
            self):
        idle_raw = os.environ.get("BROWSER_SESSION_IDLE_SECONDS", "")
        self.assertTrue(
            idle_raw.isdigit(),
            "BROWSER_SESSION_IDLE_SECONDS must be whole seconds")
        idle_seconds = int(idle_raw)
        self.assertGreaterEqual(
            idle_seconds, 8,
            "the shortened lease must leave room for real setup")
        artifact_raw = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        self.assertTrue(
            artifact_raw, "BROWSER_ARTIFACT_DIR is required")

        drafts.ReviewDraftPreservationBrowserTests.setUpClass()
        helper = drafts.ReviewDraftPreservationBrowserTests(
            EXISTING_HELPER_TEST)
        helper.setUp()
        fx = helper.fx

        with helper.browser_page() as (context, page):
            # Source -> both canonical region commits -> generation.
            snapshot = helper.setup_generated(context, page)
            approved = next(
                item for item in snapshot["questions"]
                if item["slot_id"] == "slot-remember")
            other = next(
                item for item in snapshot["questions"]
                if item["slot_id"] == "slot-understand")
            self.assertEqual(approved["current_level"], "remember")
            self.assertEqual(other["current_level"], "understand")
            self.assertEqual(approved["status"], "draft")
            self.assertEqual(approved["blocks"], [])

            # Approve the normal supported remember question and
            # see its actual teacher preview before any idling.
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

            # Leave one question editor and one region text dirty
            # and unsent; typing alone must send nothing.
            region_b = helper.region_by_raw(
                acknowledged, fx["RAW_B"])
            other_card = helper.question_card(page, ack_other)
            region_b_item = helper.region_item(page, region_b)
            mark_before_drafts = len(helper.requests_log)
            helper.tid(page, "preview-return").click()
            expect(
                page.get_by_test_id("stage-question-review")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            helper.open_question_editor(other_card).fill(
                UNSENT_QUESTION_WORDING)
            helper.tid(page, "workflow-nav-source").click()
            expect(
                page.get_by_test_id("stage-source-review")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            helper.open_region_editor(region_b_item).fill(
                UNSENT_REGION_TEXT)
            self.assertEqual(
                helper.mutations_since(mark_before_drafts), [])
            self.assertEqual(
                len(helper.requests_log), mark_before_drafts)

            # Record the displayed source reference and setup
            # inputs that expiry must not rewrite or clear.
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
            grade_input = page.get_by_label(
                "الصف الدراسي", exact=True)
            objective_input = page.get_by_label(
                "الهدف التعليمي", exact=True)
            page_number_input = page.get_by_label(
                "رقم الصفحة", exact=True)
            self.assertEqual(grade_input.input_value(), fx["GRADE"])
            self.assertEqual(
                objective_input.input_value(), fx["OBJECTIVE"])
            self.assertEqual(page_number_input.input_value(), "1")
            settled_url = page.url

            # Show Preview while it is still available: expiry
            # must preserve this active step so its empty
            # noncurrent message stays visible afterwards.
            helper.tid(page, "workflow-nav-preview").click()
            expect(
                page.get_by_test_id("stage-teacher-preview")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)

            # Settle every real outstanding response, then make no
            # context/API request at all: DOM waits only from here.
            page.wait_for_load_state(
                "networkidle", timeout=NAV_TIMEOUT_MS)
            request_checkpoint = len(helper.requests_log)
            response_checkpoint = len(helper.responses_log)
            settled_at = time.monotonic()
            expired = page.locator(
                '[data-testid="hosted-session-expired"]')
            currency = page.get_by_test_id("review-currency-status")
            expect(expired).to_be_hidden(timeout=2_000)
            expect(currency).to_be_hidden(timeout=2_000)
            expect(expired).to_be_visible(
                timeout=idle_seconds * 1000 + EXPIRY_SLACK_MS)
            elapsed = time.monotonic() - settled_at
            self.assertGreaterEqual(
                elapsed, idle_seconds - 3,
                "expiry must follow the real idle lease, "
                "not appear at settle time")

            # The withdrawal notice persists; a brief further DOM
            # wait must not heal it or fire any request.
            page.wait_for_timeout(500)
            expect(expired).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expired_text = expired.inner_text()
            self.assertTrue(expired_text.strip())
            self.assertTrue(_has_arabic(expired_text))
            for deletion_claim in (
                    "تم حذف", "حذف الخادم", "حُذفت من الخادم"):
                self.assertNotIn(deletion_claim, expired_text)
            expect(currency).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            self.assertTrue(currency.inner_text().strip())

            # No preview item may remain presented as current.
            expect(
                page.get_by_test_id("preview-item")
            ).to_have_count(0, timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(page, "preview-empty")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(page, "preview-empty")
            ).to_contain_text(
                "غير مؤكدة", timeout=ASSERT_TIMEOUT_MS)

            # DOM-only local navigation back to questions; no
            # request may follow the settle checkpoint.
            helper.tid(page, "preview-return").click()
            expect(
                page.get_by_test_id("stage-question-review")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)

            # Displayed work stays readable and byte-identical:
            # the question editor and headings on this step, the
            # region value in its hidden keyed node, the source
            # reference and the original setup inputs.
            expect(
                helper.tid(other_card, "question-edit-question")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(other_card, "question-edit-question")
            ).to_have_value(
                UNSENT_QUESTION_WORDING, timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(
                    region_b_item, "region-current-text-input")
            ).to_have_value(
                UNSENT_REGION_TEXT, timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(
                    helper.question_card(page, ack_approved),
                    "question-heading")
            ).to_have_text(
                ack_approved["current_question"],
                timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(other_card, "question-heading")
            ).to_have_text(
                ack_other["current_question"],
                timeout=ASSERT_TIMEOUT_MS)
            expect(source_image).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(
                source_image.get_attribute("src"), image_src)
            self.assertEqual(
                helper.tid(page, "source-original-link")
                .get_attribute("href"), original_href)
            self.assertEqual(
                helper.tid(page, "source-id").text_content(),
                visible_source_id)
            expect(
                helper.tid(page, "source-reference")
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(grade_input.input_value(), fx["GRADE"])
            self.assertEqual(
                objective_input.input_value(), fx["OBJECTIVE"])
            self.assertEqual(page_number_input.input_value(), "1")

            # Every server-write control is withdrawn: Prepare,
            # standalone settings, generation, both region commits
            # and both question approvals (plus the open editor's
            # own commit).
            expect(
                helper.tid(page, "prepare-page")
            ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(page, "settings-apply")
            ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
            expect(
                page.locator('#generate-questions')
            ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
            region_commits = page.get_by_test_id(
                "region-review-commit")
            expect(region_commits).to_have_count(
                2, timeout=ASSERT_TIMEOUT_MS)
            for commit in region_commits.all():
                expect(commit).to_be_disabled(
                    timeout=ASSERT_TIMEOUT_MS)
            for question in (ack_approved, ack_other):
                expect(
                    helper.tid(
                        helper.question_card(page, question),
                        "question-approve")
                ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
            expect(
                helper.tid(other_card, "question-edit-commit")
            ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)

            # Expiry itself fired nothing: no automatic
            # POST/PUT/PATCH, no reload and no second session.
            self.assertEqual(
                helper.requests_log[request_checkpoint:], [])
            self.assertEqual(
                len(helper.responses_log), response_checkpoint)
            self.assertEqual(
                helper.mutations_since(request_checkpoint), [])
            self.assertEqual(
                sum(1 for entry in helper.requests_log
                    if entry["method"] == "POST"
                    and entry["path"] == "/api/sessions"), 1)
            self.assertEqual(page.url, settled_url)

            # Synthetic-only artifacts of the withdrawn state.
            artifact_dir = pathlib.Path(artifact_raw)
            artifact_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = (
                artifact_dir / "hosted-expiry-final.png")
            page.screenshot(
                path=str(screenshot_path), full_page=True,
                timeout=ASSERT_TIMEOUT_MS)
            observations = {
                "session_idle_seconds": idle_seconds,
                "source_id": visible_source_id,
                "approved_question_id": approved["question_id"],
                "other_question_id": other["question_id"],
                "unsent_question_wording": UNSENT_QUESTION_WORDING,
                "unsent_region_text": UNSENT_REGION_TEXT,
                "preview_items_before_expiry": 1,
                "preview_items_after_expiry": 0,
                "requests_after_settle": 0,
                "session_posts": 1,
                "responses": helper.responses_log,
                "external_urls": helper.external_urls,
                "page_errors": helper.page_errors,
            }
            observations_path = (
                artifact_dir / "hosted-expiry.json")
            observations_path.write_text(
                json.dumps(
                    observations, ensure_ascii=False, indent=2),
                encoding="utf-8")
            self.assertGreater(screenshot_path.stat().st_size, 0)
            self.assertGreater(observations_path.stat().st_size, 0)
            helper.assert_clean_network()


if __name__ == "__main__":
    unittest.main()
