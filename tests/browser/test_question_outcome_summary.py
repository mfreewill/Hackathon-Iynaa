"""Request-specific question outcome summary in a real browser.

One bounded case, selected only by the required
``BROWSER_QUESTION_OUTCOME`` environment value; there is no
default mode. The parent launches the unchanged real app with
the matching server-only synthetic provider and runs this
module once per mode. Browser setup, the external-network
guard and sandbox Chromium are reused from the adjacent drafts
module, instantiated only inside the test. Mutations happen
only through real DOM controls; inspection is GET snapshot
only. ``BROWSER_ARTIFACT_DIR``, when set, receives one
screenshot and one concise outcome JSON for the mode.
"""
import contextlib
import json
import os
import pathlib
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect

import test_review_drafts as drafts

GENERATE_PATH = "/api/session/questions/generate"
SOURCE_PATH = "/api/session/source-analysis"
# mode: (HTTP status, request outcome, six slot outcomes, phrase)
EXPECTED = {
    "incomplete": (502, "incomplete", ["pending"] * 6,
                   "غير مكتمل"),
    "invalid_schema": (502, "invalid_schema", ["pending"] * 6,
                       "البنية المطلوبة"),
    "all_abstain": (201, "ok", ["abstain"] * 6, None),
    "mixed": (201, "ok", ["draft"] * 2 + ["abstain"] * 4, None),
}
LEVELS = {"remember", "understand", "apply", "analyze",
          "evaluate", "create"}
GENERIC_EMPTY = "تظهر الأسئلة هنا بعد توليد ناجح"
NO_DRAFT_MARKS = ("لم تصل", "لم ترد", "لم تُنشر", "لم تنشر",
                  "لا مسود", "دون مسود", "صفر", "٠", "0 مسود")


class QuestionOutcomeSummaryBrowserTests(unittest.TestCase):
    def test_request_summary_matches_terminal_outcome(self):
        mode = os.environ.get("BROWSER_QUESTION_OUTCOME", "")
        self.assertIn(
            mode, EXPECTED,
            "BROWSER_QUESTION_OUTCOME is required and must be "
            "one of incomplete, invalid_schema, all_abstain, "
            "mixed; no default mode is chosen")
        exp_http, exp_outcome, exp_slots, exp_phrase = (
            EXPECTED[mode])
        timeout = drafts.ASSERT_TIMEOUT_MS
        info = {"mode": mode, "http_status": None,
                "request_id": None}

        helper = drafts.ReviewDraftPreservationBrowserTests()
        type(helper).setUpClass()
        helper.setUp()
        with helper.browser_page() as (context, page):
            try:
                opened = helper.start_source_analysis(
                    context, page)
                region_a = helper.region_by_raw(
                    opened, helper.fx["RAW_A"])
                region_b = helper.region_by_raw(
                    opened, helper.fx["RAW_B"])
                helper.commit_region(
                    page, region_a, helper.fx["EDITED_A"],
                    region_a["current_label"], "included")
                helper.commit_region(
                    page, region_b, helper.fx["EDITED_B"],
                    region_b["current_label"], "included")
                baseline = helper.snapshot(context)
                self.assertEqual(
                    {item["current_text"]
                     for item in baseline["regions"]},
                    {helper.fx["EDITED_A"],
                     helper.fx["EDITED_B"]})
                self.assertEqual(
                    {item["inclusion"]
                     for item in baseline["regions"]},
                    {"included"})
                self.assertEqual(baseline["questions"], [])
                self.assertEqual(baseline["preview_items"], [])

                checkpoint = len(helper.requests_log)
                button = page.locator("#generate-questions")
                expect(button).to_have_count(1)
                expect(button).to_be_enabled(timeout=timeout)
                with page.expect_response(
                        lambda item: (
                            item.request.method == "POST"
                            and urlsplit(item.url).path
                            == GENERATE_PATH
                            and item.status == exp_http),
                        timeout=timeout) as pending:
                    button.click()
                response = pending.value
                self.assertEqual(response.status, exp_http)
                payload = response.json()
                self.assertIsInstance(payload, dict)
                request_id = payload.get("request_id")
                self.assertIsInstance(request_id, str)
                self.assertTrue(request_id)
                info.update(http_status=response.status,
                            request_id=request_id)
                sent = [entry for entry in helper.requests_log
                        if entry["method"] == "POST"
                        and entry["path"] == GENERATE_PATH]
                self.assertEqual(len(sent), 1)
                self.assertEqual(
                    sent[0].get("json", {}).get("request_id"),
                    request_id)
                self.assertEqual(
                    sent[0].get("json", {}).get("source_id"),
                    baseline["source_id"])

                # Wait for the exact request to be terminal in
                # confirmed snapshot data (GET inspection only).
                snapshot = record = None
                for _ in range(40):
                    snapshot = helper.snapshot(context)
                    found = [item for item in snapshot["requests"]
                             if item["request_id"] == request_id
                             and item["kind"] == "questions"]
                    if (len(found) == 1
                            and found[0]["state"] == "terminal"):
                        record = found[0]
                        break
                    page.wait_for_timeout(250)
                self.assertIsNotNone(
                    record, "questions request never terminal")
                self.assertEqual(record["outcome"], exp_outcome)
                slots = [item for item in snapshot["slot_outcomes"]
                         if item["request_id"] == request_id]
                self.assertEqual(len(slots), 6)
                self.assertEqual(
                    len({item["slot_id"] for item in slots}), 6)
                self.assertEqual(
                    {item["level"] for item in slots}, LEVELS)
                self.assertEqual(
                    sorted(item["outcome"] for item in slots),
                    sorted(exp_slots))
                drafted = [item for item in slots
                           if item["outcome"] == "draft"]
                abstained = [item for item in slots
                             if item["outcome"] == "abstain"]
                self.assertEqual(
                    record["admitted_count"], len(drafted))
                self.assertTrue(all(
                    not item["question_id"] for item in slots
                    if item["outcome"] != "draft"))
                questions = [item for item in snapshot["questions"]
                             if item["request_id"] == request_id]
                self.assertEqual(
                    len(snapshot["questions"]), len(drafted))
                self.assertEqual(
                    {item["question_id"] for item in drafted},
                    {item["question_id"] for item in questions})
                self.assertTrue(all(
                    item["status"] == "draft"
                    for item in questions))
                self.assertEqual(snapshot["preview_items"], [])
                info.update(
                    request_outcome=record["outcome"],
                    draft_count=len(drafted),
                    abstain_count=len(abstained),
                    slot_outcomes=[item["outcome"]
                                   for item in slots],
                    question_ids=[item["question_id"]
                                  for item in questions])

                def regions_without_links(snap):
                    return [{key: value for key, value
                             in item.items()
                             if key != "linked_question_ids"}
                            for item in snap["regions"]]

                self.assertEqual(
                    snapshot["source_id"], baseline["source_id"])
                self.assertEqual(
                    regions_without_links(snapshot),
                    regions_without_links(baseline))
                self.assertEqual(
                    (snapshot["source_revision"],
                     snapshot["settings_revision"],
                     snapshot["grade"], snapshot["objective"]),
                    (baseline["source_revision"],
                     baseline["settings_revision"],
                     baseline["grade"], baseline["objective"]))
                after_by_id = {item["request_id"]: item
                               for item in snapshot["requests"]}
                for prior in baseline["requests"]:
                    self.assertEqual(
                        after_by_id.get(prior["request_id"]), prior)
                linked = {q_id for item in snapshot["regions"]
                          for q_id in item["linked_question_ids"]}
                self.assertEqual(
                    linked,
                    {item["question_id"] for item in questions})

                # Examine the summary at its own step when the
                # guided navigation control is offered enabled.
                nav = page.get_by_test_id("workflow-nav-questions")
                if (nav.count() == 1 and nav.is_visible()
                        and nav.is_enabled()):
                    nav.click()

                stage = page.get_by_test_id(
                    "stage-question-review")
                expect(stage).to_have_count(1)
                expect(stage).to_be_visible(timeout=timeout)
                summary = stage.locator(
                    "[data-testid='question-request-summary']"
                    f"[data-request-id=\"{request_id}\"]")
                expect(summary).to_have_count(1, timeout=timeout)
                expect(summary).to_be_visible(timeout=timeout)
                self.assertEqual(
                    summary.locator(
                        "xpath=ancestor::details[not(@open)]"
                    ).count(), 0)
                self.assertEqual(
                    summary.get_attribute("data-outcome"),
                    record["outcome"])
                self.assertEqual(
                    summary.get_attribute("data-draft-count"),
                    str(len(drafted)))
                self.assertEqual(
                    summary.get_attribute("data-abstain-count"),
                    str(len(abstained)))
                text = summary.inner_text()
                self.assertTrue(text.strip())
                self.assertNotIn(GENERIC_EMPTY, text)
                no_draft_said = (
                    ("مسود" in text or "أسئلة جديدة" in text)
                    and any(mark in text
                            for mark in NO_DRAFT_MARKS))
                if exp_phrase is not None:
                    self.assertIn(exp_phrase, text)
                    self.assertTrue(
                        no_draft_said,
                        "failure summary must say no draft arrived")
                    self.assertNotIn("امتنع", text)
                    self.assertNotIn("امتناع", text)
                elif mode == "all_abstain":
                    self.assertTrue(
                        "امتناع" in text or "امتنع" in text, text)
                    self.assertTrue(
                        no_draft_said,
                        "abstention summary must say zero drafts")
                    self.assertNotIn("غير مكتمل", text)
                    self.assertNotIn("البنية المطلوبة", text)
                else:  # mixed: 2 drafts and 4 abstentions
                    self.assertIn("مسود", text)
                    self.assertTrue(
                        "امتناع" in text or "امتنع" in text, text)

                # Explicit navigation: open enclosing outcomes details.
                slot_details = stage.get_by_test_id(
                    "slot-outcome").first.locator(
                    "xpath=ancestor::details[contains(@class, "
                    "'slot-outcomes-details')][1]")
                if (slot_details.count() == 1
                        and slot_details.get_attribute("open") is None):
                    slot_details.locator(
                        "xpath=./summary[1]").click()
                    expect(slot_details).to_have_attribute(
                        "open", "", timeout=timeout)
                rows = stage.get_by_test_id("slot-outcome")
                expect(rows).to_have_count(6, timeout=timeout)
                for slot in slots:
                    row = stage.locator(
                        "[data-testid='slot-outcome']"
                        f"[data-slot-id=\"{slot['slot_id']}\"]")
                    expect(row).to_have_count(1)
                    expect(row).to_be_visible(timeout=timeout)
                    self.assertEqual(
                        row.get_attribute("data-level"),
                        slot["level"])
                    self.assertEqual(
                        row.get_attribute("data-outcome"),
                        slot["outcome"])
                    if slot["outcome"] == "abstain":
                        expect(row).to_contain_text(
                            "امتنع", timeout=timeout)
                        if slot["abstain_reason"]:
                            expect(row).to_contain_text(
                                slot["abstain_reason"],
                                timeout=timeout)

                cards = stage.get_by_test_id("question-card")
                expect(cards).to_have_count(
                    len(questions), timeout=timeout)
                for question in questions:
                    card = stage.locator(
                        "[data-testid='question-card']"
                        "[data-question-id=\"{0}\"]".format(
                            question["question_id"]))
                    # Explicit navigation: show this question card.
                    helper.ensure_question_visible(card)
                    expect(card).to_be_visible(timeout=timeout)
                    expect(card).to_contain_text(
                        question["current_question"].strip(),
                        timeout=timeout)

                preview_stage = page.get_by_test_id(
                    "stage-teacher-preview")
                expect(
                    page.get_by_test_id("preview-item")
                ).to_have_count(0)
                if not questions:
                    # No card exists: nothing may invite approval
                    # of a nonexistent draft, and the generic
                    # future claim is never the only outcome
                    # content (the asserted summary above is).
                    preview_text = (
                        preview_stage.text_content() or ""
                        if preview_stage.count() else "")
                    self.assertNotIn("اعتمد سؤال", preview_text)
                    self.assertNotIn(
                        "اعتمد سؤال", stage.text_content() or "")
                    empty = stage.get_by_test_id(
                        "question-review-empty")
                    if empty.count():
                        self.assertFalse(
                            GENERIC_EMPTY
                            in (empty.first.text_content() or "")
                            and not text.strip())

                final = helper.snapshot(context)
                final_requests = [
                    item for item in final["requests"]
                    if item["kind"] == "questions"]
                self.assertEqual(
                    [item["request_id"]
                     for item in final_requests], [request_id])
                self.assertEqual(final["preview_items"], [])
                generation_posts = len([
                    entry for entry in helper.requests_log
                    if entry["method"] == "POST"
                    and entry["path"] == GENERATE_PATH])
                source_posts = len([
                    entry for entry in helper.requests_log
                    if entry["method"] == "POST"
                    and entry["path"] == SOURCE_PATH])
                source_after = len([
                    entry
                    for entry in helper.requests_log[checkpoint:]
                    if entry["method"] == "POST"
                    and entry["path"] == SOURCE_PATH])
                self.assertEqual(generation_posts, 1)
                self.assertEqual(source_after, 0)
                self.assertEqual(source_posts, 1)
                info.update(
                    generation_posts=generation_posts,
                    source_posts=source_posts,
                    preview_count=0,
                    external_urls=helper.external_urls,
                    page_errors=helper.page_errors)
                helper.assert_clean_network()
            finally:
                artifact_dir = os.environ.get(
                    "BROWSER_ARTIFACT_DIR", "")
                if artifact_dir:
                    with contextlib.suppress(Exception):
                        folder = pathlib.Path(artifact_dir)
                        folder.mkdir(parents=True, exist_ok=True)
                        page.screenshot(
                            path=str(folder / (
                                "question-outcome-"
                                + mode + ".png")),
                            full_page=True, timeout=timeout)
                        (folder / ("question-outcome-" + mode
                                   + ".json")).write_text(
                            json.dumps(info, ensure_ascii=False,
                                       indent=2),
                            encoding="utf-8")
