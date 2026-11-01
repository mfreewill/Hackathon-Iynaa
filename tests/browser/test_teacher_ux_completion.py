"""Teacher-UX completion and retained-currentness browser tests.

The test harness serves
app on a fresh app/browser context per selected method and injects
one server-only synthetic fixture per case (never chosen through
HTTP, explicitly not Meta): the held ``ControlsProvider`` for the
three currentness regressions and the settings case,
``TeacherUXProvider(mode="metadata")`` for the blocked-approval
case, ``mode="structured"`` for the complete-original case and
``mode="terminal", outcome=<BROWSER_PROVIDER_OUTCOME>`` for the
terminal-copy case (see ``tests/teacher_ux_provider.py``).

This module defines exactly one TestCase and composes the
complete current helpers instead of copying them: each case
instantiates ``lifecycle.ProviderOperationLifecycleBrowserTests``
under one of its existing method names, runs its ``setUp``, and
drives its ``browser_page``/journey/marker/assertion helpers. That
helper object is not independently discovered or run. Environment
``BROWSER_BASE_URL``, ``BROWSER_EXECUTABLE``,
``BROWSER_ARTIFACT_DIR``, ``BROWSER_HOLD_DIR`` and
``BROWSER_OPERATION_KIND`` keep their lifecycle meanings;
``BROWSER_QC_MODE`` is ``normal``/``recovered`` only for
``test_source_metadata_does_not_block_review`` and
``BROWSER_PROVIDER_OUTCOME`` only for
``test_terminal_outcome_is_named_and_preserves_prior_work``.
Missing prerequisites fail, never skip.

All mutations are visible DOM actions; context requests are
GET-only snapshot/raw/page/status/preview observations of actual
responses, captured by method/path without an expected-status
filter and asserted after capture. Nothing is fulfilled, replaced,
injected or seeded. The only transport faults are the inherited
genuine-GET forwards: the withdrawn-currentness case forwards the
actual snapshot GET once with ``route.fetch(max_redirects=0,
max_retries=0)`` and then aborts its delivery, and the
complete-original case does the same for a later generation's raw
GETs; no response body is fabricated. Chromium sandbox stays on,
contexts are fresh, outside origins are blocked by the lifecycle
route handler, marker waits that need browser work pump the page,
every armed hold is released in ``finally``, and each case writes
its own nonempty observation JSON plus screenshot (actual
stages/statuses/IDs and final DOM text; never cookies, CSRF,
header values or credentials) into ``BROWSER_ARTIFACT_DIR``.

The first three methods are currentness regressions. The last
four make their named repair assertions only after real setup
succeeds.
"""
import contextlib
import hashlib
import json
import os
import pathlib
import unittest
from urllib.parse import unquote, urlsplit

from playwright.sync_api import expect

import test_provider_operation_lifecycle as lifecycle
import test_review_journey as journey

HELPER_METHOD = "test_completion_wins_cancel_and_old_response_is_inert"
ASSERT_TIMEOUT_MS = lifecycle.ASSERT_TIMEOUT_MS

SESSIONS_PATH = "/api/sessions"
SLOT_APPLY = "slot-apply"
SLOT_EVALUATE = "slot-evaluate"
SLOT_CREATE = "slot-create"

NEW_OBJECTIVE = "  هَدَفٌ جَدِيدٌ: قِرَاءَةُ النَّصِّ ٧٨٩  "
CHANGED_GRADE = "  الصَّفُّ ٦  "
TEACHER_EDITED_WORDING = (
    "  صِيَاغَةُ الْمُعَلِّمِ الْمَحْفُوظَةُ لِسُؤَالِ التَّطْبِيقِ؟  ")
TEACHER_EDITED_GUIDANCE = (
    "إِرْشَادُ الْمُعَلِّمِ الْمُصَنَّعُ لِلْإِجَابَةِ: "
    "صِفْ سُرْعَةَ الْمَاءِ بِكَلِمَاتِكَ.")
TEACHER_EDITED_COGNITIVE = (
    "تَعْلِيلُ الْمُعَلِّمِ الْمُصَنَّعُ: مُهِمَّةُ فَهْمٍ "
    "تَجْرِيبِيَّةٌ لِلصَّفِّ الْمُحَدَّدِ.")
TEACHER_EDITED_LEVEL = "understand"
SESSION_REFUSAL_TOKEN = "انتهت جلسة"
SETTINGS_CURRENCY_TOKEN = "بعد إقرار الإعدادات"
IDENTITY_CURRENCY_TOKEN = "بعد رفض الجلسة"
STALE_PREVIEW_TOKEN = "المعاينة الحالية غير متاحة"
INITIAL_PREVIEW_EMPTY = (
    "لا أسئلة معتمدة حاليًا؛ اعتمد سؤالًا من مراجعة الأسئلة لتظهر هنا.")
GENERATION_SUCCESS_TEXT = (
    "اكتمل توليد الأسئلة؛ راجع المسودات أدناه قبل أي اعتماد.")
GENERIC_CLOSED_NON_OK = (
    "أُغلق الطلب دون اكتمال ناجح؛ "
    "الحالة المعروضة هي آخر حالة مؤكدة من الخادم.")
FULL_PAGE_CAPTION = (
    "مرجع الدليل: الصفحة كاملة — لا يوجد قناع أو تحديد موضعي آلي.")

TERMINAL_ARABIC = {
    "incomplete": "ناتج غير مكتمل",
    "invalid_schema": "البنية المطلوبة",
    "empty": "لم يصل أيّ ناتج",
    "error": "حدث خطأ",
    "refusal": "رفض",
    "timeout": "انتهى وقت",
    "rate_limited": "مشغولة",
}
TERMINAL_HTTP_STATUS = {
    "timeout": 504, "rate_limited": 503}


class TeacherUXCompletionBrowserTests(unittest.TestCase):
    """Seven composed cases; the parent selects one per fresh app."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx, cls.png_bytes = journey.load_fixture()
        lifecycle.ProviderOperationLifecycleBrowserTests.setUpClass()

    def setUp(self):
        self.h = lifecycle.ProviderOperationLifecycleBrowserTests(
            HELPER_METHOD)
        self.h.setUp()
        self.assertTrue(
            self.h.artifact_dir,
            "BROWSER_ARTIFACT_DIR (per-case output dir) is required")
        self.obs = {"statuses": {}, "ids": {}, "notes": {}}

    # -- composed lifecycle accessors --------------------------------
    @property
    def tid(self):
        return self.h.tid

    def phase(self, name):
        self.h.mark_phase(name)

    def snapshot(self, context):
        return self.h.snapshot(context)

    def write_artifacts(self, page):
        """This case's own observation JSON and screenshot.

        Runs before the helper's context-manager finally populates
        the observation counters/errors/focus, so serialize the
        actual helper counters and page reads taken here. A failed
        optional read is recorded as null plus a capture error,
        never as clean state; nothing here is an assertion.
        """
        h = self.h
        directory = pathlib.Path(h.artifact_dir)
        directory.mkdir(parents=True, exist_ok=True)
        record = dict(h.observation)
        record["case"] = self._testMethodName
        record["method"] = self._testMethodName
        record["teacher_ux_statuses"] = self.obs["statuses"]
        record["teacher_ux_ids"] = self.obs["ids"]
        record["teacher_ux_notes"] = self.obs["notes"]
        record["request_counts"] = {
            "settings": h.request_count(
                "PUT", lifecycle.SETTINGS_PATH),
            "start": h.request_count("POST", lifecycle.START_PATH),
            "source_analysis": h.request_count(
                "POST", lifecycle.SOURCE_PATH),
            "question_generation": h.request_count(
                "POST", lifecycle.QUESTIONS_PATH)}
        record["markers"] = h.marker_names()
        record["external_urls"] = list(h.external_urls)
        record["page_errors"] = list(h.page_errors)
        capture_errors = {}
        record["capture_errors"] = capture_errors

        def capture(field, read):
            try:
                record[field] = read()
            except Exception as exc:
                record[field] = None
                capture_errors[field] = (
                    f"{type(exc).__name__}: {exc}")

        if page is not None:
            capture(
                "final_dom_text",
                lambda: page.locator("body").inner_text(
                    timeout=ASSERT_TIMEOUT_MS))
            capture(
                "workspace_status",
                lambda: self.tid(
                    page, "workspace-status").inner_text(
                        timeout=ASSERT_TIMEOUT_MS))
            capture(
                "operation_status",
                lambda: self.tid(
                    page, "provider-operation-status").inner_text(
                        timeout=ASSERT_TIMEOUT_MS))
            capture("focus", lambda: h.focus_name(page))
            screenshot_name = (
                f"teacher_ux_{self._testMethodName}.png")
            try:
                page.screenshot(
                    path=str(directory / screenshot_name),
                    full_page=True, timeout=ASSERT_TIMEOUT_MS)
                record["screenshot"] = screenshot_name
            except Exception as exc:
                record["screenshot"] = None
                capture_errors["screenshot"] = (
                    f"{type(exc).__name__}: {exc}")
        else:
            for field in ("final_dom_text", "workspace_status",
                          "operation_status", "focus", "screenshot"):
                record[field] = None
            capture_errors["page"] = "unavailable"
        (directory / f"teacher_ux_{self._testMethodName}.json"
         ).write_text(
             json.dumps(record, ensure_ascii=False, indent=2) + "\n",
             encoding="utf-8")

    def wait_marker_pumped(self, page, fixture_id, marker):
        """Wait for a marker while Playwright events keep moving."""
        self.h.assert_armed((fixture_id,))
        for _ in range(600):
            if self.h.marker_is_file(fixture_id, marker):
                return
            self.assertFalse(
                self.h.marker_is_file(fixture_id, "expired"),
                f"hold {fixture_id} expired before {marker}")
            page.wait_for_timeout(20)
        self.fail(f"marker {fixture_id}.{marker} not observed")

    # -- small shared DOM actions (all through lifecycle capture) ----
    def generate_expect(self, page, context, source_id, occurrence,
                        total_questions, label):
        """One real generation; question total is fixture-specific."""
        response = self.h.capture_click(
            page, "POST", lifecycle.QUESTIONS_PATH,
            self.h.generate(page))
        self.obs["statuses"][label] = response.status
        self.assertEqual(response.status, 201, label)
        body = self.h.wait_body(
            page, lifecycle.QUESTIONS_PATH, occurrence)
        request_id = self.h.assert_provider_command(
            body, source_id, label)
        envelope = response.json()
        self.assertEqual(envelope["request_id"], request_id)
        self.assertEqual(envelope["snapshot"]["source_id"], source_id)
        snapshot = self.snapshot(context)
        self.h.assert_record(
            snapshot, request_id, "questions", "terminal", "ok")
        self.assertEqual(
            len(snapshot["questions"]), total_questions, label)
        self.phase(label)
        return request_id, snapshot

    @staticmethod
    def question_by_id(snapshot, question_id):
        matches = [item for item in snapshot["questions"]
                   if item["question_id"] == question_id]
        if len(matches) != 1:
            raise AssertionError(f"question {question_id} not unique")
        return matches[0]

    def decide(self, page, context, question, action):
        card = self.h.question_card(page, question)
        path = self.h.question_path(question, f"/{action}")
        response = self.h.capture_click(
            page, "POST", path,
            self.tid(card, f"question-{action}"))
        self.obs["statuses"][f"{action}:{question['slot_id']}"] = (
            response.status)
        self.assertEqual(response.status, 200, path)
        return self.question_by_id(
            self.snapshot(context), question["question_id"])

    def edit_question(self, page, context, question, field_testid,
                      value):
        card = self.h.question_card(page, question)
        form = self.tid(card, "question-edit-form")
        if not form.is_visible():
            self.tid(card, "question-edit").click(
                timeout=ASSERT_TIMEOUT_MS)
        expect(form).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.tid(card, field_testid).fill(
            value, timeout=ASSERT_TIMEOUT_MS)
        path = self.h.question_path(question)
        response = self.h.capture_click(
            page, "PATCH", path,
            self.tid(card, "question-edit-commit"))
        self.obs["statuses"][f"edit:{question['slot_id']}"] = (
            response.status)
        self.assertEqual(response.status, 200, path)
        return self.question_by_id(
            self.snapshot(context), question["question_id"])

    def assert_status_announces(self, page, tokens, label):
        locator = self.tid(page, "workspace-status")
        expect(locator).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        text = ""
        for _ in range(100):
            text = locator.inner_text(timeout=ASSERT_TIMEOUT_MS)
            if any(token in text for token in tokens):
                break
            page.wait_for_timeout(20)
        self.obs["notes"][label] = text
        self.assertTrue(
            any(token in text for token in tokens),
            f"{label}: workspace status {text!r} names none of {tokens}")
        return text

    @staticmethod
    def open_disclosure(scope, testid):
        details = scope.get_by_test_id(testid)
        if details.get_attribute("open") is None:
            details.locator("summary").click()
        return details

    # -- 1. source raw metadata never blocks committed review -------
    def test_source_metadata_does_not_block_review(self):
        h = self.h
        self.assertEqual(
            h.kind, "source", "this case runs against a separately served test application")
        mode = os.environ.get("BROWSER_QC_MODE", "")
        self.assertIn(
            mode, ("normal", "recovered"),
            "BROWSER_QC_MODE must be normal or recovered")
        self.obs["notes"]["qc_mode"] = mode
        holds = ("old-response", "raw-response")
        h.assert_armed(holds)
        fx = self.fx
        with h.browser_page() as (context, page):
            try:
                metadata = h.begin_held_source(page)
                source_id = metadata["source_id"]
                h.wait_marker("old-response", "ready")
                self.assertTrue(
                    h.marker_is_file("old-response", "produced"))
                request_id = h.assert_provider_command(
                    h.wait_body(page, lifecycle.SOURCE_PATH, 1),
                    source_id, "source_analysis")
                # The source commit itself already succeeded.
                h.assert_status_endpoint(
                    context, request_id, source_id, "source",
                    "terminal", "ok")
                committed = self.snapshot(context)
                self.assertEqual(len(committed["regions"]), 2)
                if mode == "normal":
                    original = h.await_original(
                        page, lifecycle.SOURCE_PATH, request_id,
                        lambda: h.release_hold("old-response"))
                    self.obs["statuses"]["original_source"] = (
                        original.status)
                    self.assertEqual(original.status, 201)
                else:
                    cancel = h.click_cancel(page, request_id)
                    self.obs["statuses"]["cancel_closed"] = cancel.status
                    self.assertEqual(cancel.status, 409)
                    self.assertEqual(
                        cancel.json(), {"detail": "request is closed"})
                    expect(h.op(page, "check")).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                    check = h.click_check(page, request_id)
                    self.obs["statuses"]["check_ok"] = check.status
                    self.assertEqual(check.status, 200)
                    check_body = check.json()
                    self.assertEqual(
                        check_body["request"]["state"], "terminal")
                    self.assertEqual(
                        check_body["request"]["outcome"], "ok")
                self.phase(f"source_committed_{mode}")

                # The browser's own raw GET is now held server-side.
                self.wait_marker_pumped(page, "raw-response", "ready")
                self.assertFalse(
                    h.marker_is_file("raw-response", "released"))
                expect(self.tid(page, "region-item")).to_have_count(2)
                river = h.region_by_section(
                    self.snapshot(context), lifecycle.REGION_RIVER)
                item = h.region_item(page, river)
                # Logical success is usable before raw arrives.
                h.assert_op_status(page, lifecycle.OP_SUCCEEDED)
                expect(self.tid(
                    item, "region-current-text-input")).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    item, "region-inclusion-input")).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)

                checkpoint = len(h.requests_log)
                label_before = river["current_label"]
                self.tid(item, "region-current-text-input").fill(
                    fx["EDITED_A"], timeout=ASSERT_TIMEOUT_MS)
                self.tid(item, "region-inclusion-input").select_option(
                    "included", timeout=ASSERT_TIMEOUT_MS)
                path = h.region_path(river)
                commit = self.tid(item, "region-review-commit")
                response = h.capture_click(page, "PUT", path, commit)
                self.obs["statuses"]["region_edit_while_raw_held"] = (
                    response.status)
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    h.request_bodies("PUT", path, checkpoint),
                    [{"inclusion": "included",
                      "current_text": fx["EDITED_A"],
                      "current_label": label_before}])
                edited_snapshot = self.snapshot(context)
                edited_river = h.region_by_section(
                    edited_snapshot, lifecycle.REGION_RIVER)
                self.assertEqual(
                    edited_river["current_text"], fx["EDITED_A"])
                self.assertEqual(edited_river["raw_text"], fx["RAW_A"])
                self.assertEqual(edited_river["inclusion"], "included")
                expect(commit).to_be_focused(
                    timeout=ASSERT_TIMEOUT_MS)
                # The most recent real teacher action owns the
                # persistent status; capture its actual text before
                # raw release so metadata cannot overwrite it.
                status_before_release = self.tid(
                    page, "workspace-status").inner_text(
                        timeout=ASSERT_TIMEOUT_MS)
                self.assertTrue(
                    status_before_release.strip(),
                    "workspace status empty before raw release")
                self.obs["notes"][
                    "workspace_status_before_raw_release"] = (
                        status_before_release)
                self.assertFalse(
                    h.marker_is_file("raw-response", "released"))
                self.phase("region_edited_while_raw_held")

                h.release_hold("raw-response")
                raw = h.raw_result(context, request_id)
                actual_model = raw["model_name"]
                self.obs["ids"]["source_request_id"] = request_id
                # Only matching request/model attribution appears.
                expect(self.tid(item, "region-request-id")
                       ).to_have_text(
                           request_id, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-model-name")
                       ).to_have_text(
                           actual_model, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(page, "source-model-name")
                       ).to_have_text(
                           actual_model, timeout=ASSERT_TIMEOUT_MS)
                # Edit, focus and the current snapshot are unchanged.
                self.assertEqual(
                    self.snapshot(context), edited_snapshot)
                expect(self.tid(item, "region-current-text-input")
                       ).to_have_value(
                           fx["EDITED_A"], timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-inclusion-input")
                       ).to_have_value(
                           "included", timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-review-commit")
                       ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
                h.assert_op_status(page, lifecycle.OP_SUCCEEDED)
                expect(self.tid(page, "workspace-status")
                       ).to_have_text(
                           status_before_release,
                           timeout=ASSERT_TIMEOUT_MS)

                if mode == "recovered":
                    late = h.await_original(
                        page, lifecycle.SOURCE_PATH, request_id,
                        lambda: h.release_hold("old-response"))
                    self.obs["statuses"]["late_original"] = late.status
                    self.assertEqual(late.status, 201)
                    stale_river = h.region_by_section(
                        late.json()["snapshot"], lifecycle.REGION_RIVER)
                    self.assertEqual(
                        stale_river["current_text"], fx["RAW_A"])
                    self.assertEqual(
                        self.snapshot(context), edited_snapshot)
                    expect(self.tid(page, "workspace-status")
                           ).to_have_text(
                               status_before_release,
                               timeout=ASSERT_TIMEOUT_MS)
                h.assert_counts(1, 1, 1, 0)
                h.assert_clean()
                self.phase("raw_release_painted_attribution_only")
            finally:
                self.write_artifacts(page)
                h.release_quietly(holds)

    # -- 2. withdrawn currentness blocks the same-source retry ------
    def test_withdrawn_currentness_blocks_source_retry(self):
        h = self.h
        self.assertEqual(
            h.kind, "source", "this case runs against a separately served test application")
        holds = ("old-provider",)
        h.assert_armed(holds)
        forwarded = []

        def fail_snapshot(route, request):
            # The one inherited fault: forward the genuine GET
            # once, record only its status/path, discard the
            # response and abort its delivery. Nothing fabricated.
            response = route.fetch(max_redirects=0, max_retries=0)
            forwarded.append({
                "status": response.status,
                "path": urlsplit(request.url).path})
            with contextlib.suppress(Exception):
                response.dispose()
            route.abort("failed")

        with h.browser_page() as (context, page):
            try:
                metadata = h.begin_held_source(page)
                source_id = metadata["source_id"]
                h.wait_marker("old-provider", "ready")
                old_id = h.assert_provider_command(
                    h.wait_body(page, lifecycle.SOURCE_PATH, 1),
                    source_id, "old")
                cancel = h.click_cancel(page, old_id)
                self.obs["statuses"]["cancel_old"] = cancel.status
                self.assertEqual(cancel.status, 200)
                h.assert_cancel_body(
                    cancel.json(), old_id, source_id, "source")
                h.assert_status_endpoint(
                    context, old_id, source_id, "source", "terminal",
                    "cancelled")
                h.assert_op_status(page, lifecycle.OP_CANCELLED)
                expect(h.op(page, "retry")).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(h.op(page, "retry")).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                # The existing warning names possible provider
                # continuation/billing and the new-request meaning.
                retry_warning = self.tid(
                    page, "provider-retry-warning")
                expect(retry_warning).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                warning_text = retry_warning.inner_text(
                    timeout=ASSERT_TIMEOUT_MS)
                self.obs["notes"]["provider_retry_warning"] = (
                    warning_text)
                for token in ("يستمر", "يُحتسب", "طلبًا جديدًا"):
                    self.assertIn(token, warning_text)
                late = h.await_original(
                    page, lifecycle.SOURCE_PATH, old_id,
                    lambda: h.release_hold("old-provider"))
                self.obs["statuses"]["late_old"] = late.status
                self.assertEqual(late.status, 409)
                self.phase("cancelled_retry_enabled_provider_released")

                page.route("**/api/session/snapshot", fail_snapshot)
                page.get_by_label(
                    "الهدف التعليمي", exact=True).fill(
                        self.fx["OBJECTIVE"] + " changed",
                        timeout=ASSERT_TIMEOUT_MS)
                page.get_by_label(
                    "ملف صفحة المصدر", exact=True).set_input_files(
                        {"name": "invalid.png",
                         "mimeType": "image/png",
                         "buffer": b"not an image"},
                        timeout=ASSERT_TIMEOUT_MS)
                start = h.capture_click(
                    page, "POST", lifecycle.START_PATH, h.primary(page))
                self.obs["statuses"]["invalid_start"] = start.status
                self.obs["notes"]["forwarded_snapshot_gets"] = forwarded
                self.assertIn(start.status, (400, 415))
                self.assertTrue(forwarded)
                self.assertTrue(all(
                    entry["path"] == lifecycle.SNAPSHOT_PATH
                    for entry in forwarded))
                currency = self.tid(page, "review-currency-status")
                expect(currency).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(currency).to_contain_text(
                    SETTINGS_CURRENCY_TOKEN, timeout=ASSERT_TIMEOUT_MS)
                # Known source retained; retry must not be usable
                # and no extra provider POST may have happened.
                h.assert_source_visible(page, source_id)
                self.assertEqual(
                    self.snapshot(context)["source_id"], source_id)
                retry = h.op(page, "retry")
                self.assertTrue(
                    retry.is_hidden() or retry.is_disabled(),
                    "retry stayed usable while currentness withdrawn")
                self.assertEqual(
                    h.request_count("POST", lifecycle.SOURCE_PATH), 1)
                self.phase("withdrawn_retry_blocked_no_extra_post")

                page.unroute("**/api/session/snapshot", fail_snapshot)
                page.get_by_label(
                    "الهدف التعليمي", exact=True).fill(
                        self.fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
                restore = h.capture_click(
                    page, "POST", lifecycle.START_PATH, h.primary(page))
                self.obs["statuses"]["restore_start"] = restore.status
                self.assertIn(restore.status, (400, 415))
                expect(currency).to_be_hidden(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(retry).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(retry).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
                checkpoint = len(h.requests_log)
                permitted = h.capture_click(
                    page, "POST", lifecycle.SOURCE_PATH, retry)
                self.obs["statuses"]["permitted_retry"] = (
                    permitted.status)
                self.assertEqual(permitted.status, 201)
                self.assertEqual(
                    h.mutations_since(checkpoint),
                    [("POST", lifecycle.SOURCE_PATH)])
                new_id = h.assert_provider_command(
                    h.wait_body(page, lifecycle.SOURCE_PATH, 2),
                    source_id, "retry")
                self.assertNotEqual(new_id, old_id)
                body = permitted.json()
                self.assertEqual(body["request_id"], new_id)
                self.assertEqual(body["snapshot"]["source_id"], source_id)
                h.assert_record(
                    body["snapshot"], new_id, "source", "terminal",
                    "ok")
                self.assertEqual(len(body["snapshot"]["regions"]), 2)
                h.assert_counts(3, 3, 2, 0)
                h.assert_clean()
                self.phase("acknowledged_restore_then_explicit_retry")
            finally:
                with contextlib.suppress(Exception):
                    page.unroute(
                        "**/api/session/snapshot", fail_snapshot)
                self.write_artifacts(page)
                h.release_quietly(holds)

    # -- 3. session refusal withdraws preview, keeps the draft ------
    def test_session_refusal_withdraws_preview_and_keeps_draft(self):
        h = self.h
        with h.browser_page() as (context, page):
            second = None
            try:
                metadata, _source_request = h.start_normal_source(page)
                source_id = metadata["source_id"]
                h.review_both_regions(page, context)
                first_request, _snapshot = h.generate_first_normal(
                    page, context, source_id)
                approved, other = h.approve_and_dirty(
                    page, context, first_request)
                h.assert_questions_preserved(
                    page, context, first_request, approved, other)
                session_posts = h.request_count("POST", SESSIONS_PATH)
                self.assertEqual(session_posts, 1)
                self.phase("approved_preview_and_dirty_draft_current")

                # A second ordinary page in the SAME context creates
                # a real new session; no cookie value is read or set.
                second = context.new_page()
                with second.expect_response(
                        lambda item: item.request.method == "POST"
                        and urlsplit(item.url).path == SESSIONS_PATH,
                        timeout=ASSERT_TIMEOUT_MS) as session_info:
                    second.goto(
                        h.base_url, wait_until="domcontentloaded",
                        timeout=lifecycle.NAV_TIMEOUT_MS)
                session_response = session_info.value
                self.obs["statuses"]["second_page_session"] = (
                    session_response.status)
                self.assertEqual(session_response.status, 201)

                refused = h.capture_click(
                    page, "POST", lifecycle.QUESTIONS_PATH,
                    h.generate(page))
                self.obs["statuses"]["refused_generation"] = (
                    refused.status)
                self.assertEqual(refused.status, 403)
                self.assertEqual(
                    refused.json(), {"detail": "forbidden request"})
                self.phase("generation_refused_403")

                workspace_status = self.tid(page, "workspace-status")
                expect(workspace_status).to_contain_text(
                    SESSION_REFUSAL_TOKEN, timeout=ASSERT_TIMEOUT_MS)
                currency = self.tid(page, "review-currency-status")
                expect(currency).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(currency).to_contain_text(
                    IDENTITY_CURRENCY_TOKEN, timeout=ASSERT_TIMEOUT_MS)
                # No current preview or approval claim survives.
                expect(self.tid(page, "preview-item")).to_have_count(0)
                expect(self.tid(page, "preview-empty")
                       ).to_contain_text(
                           STALE_PREVIEW_TOKEN,
                           timeout=ASSERT_TIMEOUT_MS)
                approved_card = h.question_card(page, approved)
                expect(self.tid(approved_card, "question-status")
                       ).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(approved_card, "question-decision")
                       ).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
                # Generation, retry and write controls are disabled.
                expect(h.generate(page)).to_be_disabled(
                    timeout=ASSERT_TIMEOUT_MS)
                retry = h.op(page, "retry")
                self.assertTrue(
                    retry.is_hidden() or retry.is_disabled(),
                    "retry stayed usable after session refusal")
                snapshot_regions = page.locator(
                    "[data-testid='region-item']").first
                expect(self.tid(
                    snapshot_regions, "region-review-commit")
                ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                other_card = h.question_card(page, other)
                expect(self.tid(other_card, "question-approve")
                       ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(other_card, "question-edit-commit")
                       ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                # The unsaved draft is unchanged and still readable.
                expect(self.tid(other_card, "question-edit-form")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(other_card, "question-edit-question")
                       ).to_have_value(
                           lifecycle.DIRTY_WORDING,
                           timeout=ASSERT_TIMEOUT_MS)
                # No automatic session recreation and no retry POST
                # on the first page; its old work is not re-certified
                # by an arbitrary GET against the new session.
                self.assertEqual(
                    h.request_count("POST", SESSIONS_PATH),
                    session_posts)
                self.assertEqual(
                    h.request_count(
                        "POST", lifecycle.QUESTIONS_PATH), 2)
                probe = h.api_get(context, lifecycle.SNAPSHOT_PATH)
                self.obs["statuses"]["new_session_snapshot_get"] = (
                    probe.status)
                # The second page's new session is empty: the
                # exact no-workspace contract, never the old work.
                self.assertEqual(probe.status, 404)
                self.assertEqual(
                    probe.json(), {"detail": "page not found"})
                self.assertEqual(h.external_urls, [])
                self.assertEqual(h.page_errors, [])
                self.phase("refusal_withdrew_preview_draft_kept")
            finally:
                if second is not None:
                    with contextlib.suppress(Exception):
                        second.close()
                self.write_artifacts(page)

    # -- 4. independent settings save preserves source/history ------
    def test_settings_update_preserves_source_and_review_history(self):
        h = self.h
        fx = self.fx
        with h.browser_page() as (context, page):
            try:
                metadata, source_request = h.start_normal_source(page)
                source_id = metadata["source_id"]
                h.review_both_regions(page, context)
                question_request, _snapshot = h.generate_first_normal(
                    page, context, source_id)
                approved, other = h.approve_and_dirty(
                    page, context, question_request)
                baseline = self.snapshot(context)
                self.assertEqual(baseline["grade"], fx["GRADE"])
                self.assertEqual(baseline["objective"], fx["OBJECTIVE"])
                self.assertEqual(
                    [item["question_id"]
                     for item in baseline["preview_items"]],
                    [approved["question_id"]])
                original_bytes = h.api_bytes(
                    context, metadata["original_url"])
                self.assertEqual(original_bytes, self.png_bytes)
                self.assertEqual(
                    hashlib.sha256(original_bytes).hexdigest(),
                    metadata["source_sha256"])
                baseline_source_raw = h.raw_result(
                    context, source_request)
                baseline_question_raw = h.raw_result(
                    context, question_request)
                baseline_questions = {
                    item["question_id"]: item
                    for item in baseline["questions"]}
                self.phase("setup_approved_preview_dirty_sibling")

                # FIRST repair assertion (count first, no long wait):
                # one usable settings-apply button in the existing
                # setup form. Absent on the frozen product.
                form = self.tid(page, "setup-form")
                apply_button = self.tid(page, "settings-apply")
                self.assertEqual(
                    apply_button.count(), 1,
                    "settings-apply control is missing")
                self.assertEqual(self.tid(form, "settings-apply").count(), 1)
                expect(apply_button).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(apply_button).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(apply_button).to_have_text(
                    "حفظ الصف والهدف", timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    apply_button.get_attribute("type"), "button")
                # A dedicated impact warning next to the control:
                # all generated approvals will need review/update,
                # without deleting work. Not stray form words.
                impact = self.tid(form, "settings-impact-warning")
                self.assertEqual(impact.count(), 1)
                expect(impact).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                impact_text = impact.inner_text(
                    timeout=ASSERT_TIMEOUT_MS)
                self.obs["notes"]["settings_impact_warning"] = (
                    impact_text)
                self.assertIn("كل", impact_text)
                self.assertTrue(
                    any(token in impact_text for token in
                        ("معتمد", "اعتماد")), impact_text)
                self.assertTrue(
                    any(token in impact_text for token in
                        ("مراجعة", "تحديث")), impact_text)
                self.assertTrue(
                    any(token in impact_text for token in
                        ("تحتاج", "يلزم")), impact_text)
                self.assertIn("حذف", impact_text)
                self.assertTrue(
                    any(token in impact_text for token in
                        ("دون", "لن", "لا تُحذف", "لا يُحذف")),
                    impact_text)
                self.phase("first_repair_assertion_settings_apply")

                def apply_settings(objective_value, label):
                    page.get_by_label(
                        "الهدف التعليمي", exact=True).fill(
                            objective_value, timeout=ASSERT_TIMEOUT_MS)
                    checkpoint = len(h.requests_log)
                    response = h.capture_click(
                        page, "PUT", lifecycle.SETTINGS_PATH,
                        apply_button)
                    self.obs["statuses"][label] = response.status
                    self.assertEqual(response.status, 200, label)
                    expected_body = {
                        "grade": fx["GRADE"],
                        "objective": objective_value, "rights_ref": ""}
                    self.assertEqual(
                        h.request_bodies(
                            "PUT", lifecycle.SETTINGS_PATH, checkpoint),
                        [expected_body])
                    self.assertEqual(response.json(), expected_body)
                    self.assertEqual(
                        h.mutations_since(checkpoint),
                        [("PUT", lifecycle.SETTINGS_PATH)])
                    return self.snapshot(context)

                changed = apply_settings(NEW_OBJECTIVE, "settings_apply")
                self.assertEqual(changed["source_id"], source_id)
                self.assertEqual(changed["grade"], fx["GRADE"])
                self.assertEqual(changed["objective"], NEW_OBJECTIVE)
                self.assertEqual(
                    changed["settings_revision"],
                    baseline["settings_revision"] + 1)
                self.assertEqual(
                    changed["source_revision"],
                    baseline["source_revision"])
                self.assertEqual(changed["regions"], baseline["regions"])
                self.assertEqual(
                    {item["question_id"] for item in changed["questions"]},
                    set(baseline_questions))
                changed_approved = self.question_by_id(
                    changed, approved["question_id"])
                changed_other = self.question_by_id(
                    changed, other["question_id"])
                self.assertEqual(changed_approved["status"], "outdated")
                self.assertEqual(changed_other["status"], "draft")
                self.assertEqual(changed["preview_items"], [])
                for current in (changed_approved, changed_other):
                    before = baseline_questions[current["question_id"]]
                    for field in ("original_question",
                                  "original_answer_guidance",
                                  "original_cognitive_explanation",
                                  "current_question",
                                  "current_answer_guidance",
                                  "current_cognitive_explanation",
                                  "current_grade", "current_objective",
                                  "generation_grade",
                                  "generation_objective"):
                        self.assertEqual(
                            current[field], before[field], field)
                approved_card = h.question_card(page, changed_approved)
                expect(self.tid(
                    approved_card, "question-generation-grade")
                ).to_have_text(
                    fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    approved_card, "question-generation-objective")
                ).to_have_text(
                    fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(approved_card, "question-grade")
                       ).to_have_text(
                           fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(approved_card, "question-objective")
                       ).to_have_text(
                           fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(page, "preview-item")).to_have_count(0)
                # The empty preview now states the truthful reason:
                # prior approvals are outdated, not never-approved.
                outdated_empty = self.tid(page, "preview-empty")
                expect(outdated_empty).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                outdated_text = outdated_empty.inner_text(
                    timeout=ASSERT_TIMEOUT_MS)
                self.obs["notes"][
                    "preview_empty_after_settings_change"] = (
                        outdated_text)
                self.assertNotEqual(
                    outdated_text, INITIAL_PREVIEW_EMPTY)
                self.assertTrue(
                    any(token in outdated_text for token in
                        ("لم تعد", "تحديث", "تحتاج")), outdated_text)
                other_card = h.question_card(page, changed_other)
                expect(self.tid(other_card, "question-edit-form")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(other_card, "question-edit-question")
                       ).to_have_value(
                           lifecycle.DIRTY_WORDING,
                           timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    h.api_bytes(context, metadata["original_url"]),
                    self.png_bytes)
                self.assertEqual(
                    h.raw_result(context, source_request),
                    baseline_source_raw)
                self.assertEqual(
                    h.raw_result(context, question_request),
                    baseline_question_raw)
                self.phase("settings_changed_approvals_outdated")

                # Identical re-save: a semantic no-op.
                repeated = apply_settings(
                    NEW_OBJECTIVE, "settings_resave_identical")
                self.assertEqual(repeated, changed)
                # Restoring the old values never resurrects approval.
                restored = apply_settings(
                    fx["OBJECTIVE"], "settings_restore_old")
                self.assertEqual(restored["objective"], fx["OBJECTIVE"])
                self.assertEqual(
                    restored["settings_revision"],
                    changed["settings_revision"] + 1)
                restored_approved = self.question_by_id(
                    restored, approved["question_id"])
                self.assertEqual(restored_approved["status"], "outdated")
                self.assertEqual(restored["preview_items"], [])
                expect(outdated_empty).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    outdated_empty.inner_text(
                        timeout=ASSERT_TIMEOUT_MS), outdated_text)
                expect(self.tid(other_card, "question-edit-question")
                       ).to_have_value(
                           lifecycle.DIRTY_WORDING,
                           timeout=ASSERT_TIMEOUT_MS)
                h.assert_counts(4, 1, 1, 1)
                h.assert_clean()
                self.phase("settings_noop_and_restore_verified")
            finally:
                self.write_artifacts(page)

    # -- 5. blocked approval is explained, never mutated ------------
    def test_blocked_approval_is_explained_without_mutation(self):
        h = self.h
        with h.browser_page() as (context, page):
            try:
                metadata, source_request = h.start_normal_source(page)
                source_id = metadata["source_id"]
                h.review_both_regions(page, context)
                question_request, snapshot = self.generate_expect(
                    page, context, source_id, 1, 3,
                    "metadata_generation")
                blocked = [
                    item for item in snapshot["questions"]
                    if "required_order_missing" in item["blocks"]]
                self.assertEqual(len(blocked), 1)
                analyze = blocked[0]
                self.assertEqual(analyze["request_id"], question_request)
                self.assertEqual(analyze["slot_id"], "slot-analyze")
                self.assertTrue(
                    analyze["required_order_relationships"])
                ordinary = h.question_by_request_slot(
                    snapshot, question_request,
                    lifecycle.SLOT_REMEMBER)
                other = h.question_by_request_slot(
                    snapshot, question_request,
                    lifecycle.SLOT_UNDERSTAND)
                self.assertEqual(ordinary["blocks"], [])
                baseline_source_raw = h.raw_result(
                    context, source_request)
                baseline_question_raw = h.raw_result(
                    context, question_request)
                baseline_originals = {
                    item["question_id"]: (
                        item["original_question"],
                        item["original_answer_guidance"],
                        item["original_cognitive_explanation"])
                    for item in snapshot["questions"]}
                self.phase("setup_blocked_analyze_and_two_drafts")

                card = h.question_card(page, analyze)
                expect(card).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                # The card is not busy: its edit control is usable.
                expect(self.tid(card, "question-edit")).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                # FIRST repair assertion: approval is disabled for
                # an actually blocked card. Enabled on frozen code.
                approve = self.tid(card, "question-approve")
                self.assertEqual(approve.count(), 1)
                self.assertTrue(
                    approve.is_disabled(),
                    "blocked card approval stayed enabled")
                chips = self.tid(card, "question-block")
                expect(chips).to_have_count(len(analyze["blocks"]))
                chip = card.locator(
                    "[data-testid='question-block']"
                    "[data-block='required_order_missing']")
                self.assertEqual(chip.count(), 1)
                chip_text = chip.inner_text(timeout=ASSERT_TIMEOUT_MS)
                self.assertNotEqual(
                    chip_text.strip(), "required_order_missing")
                self.assertIn("ترتيب", chip_text)
                self.assertNotIn(
                    "required_order_missing",
                    card.inner_text(timeout=ASSERT_TIMEOUT_MS))
                self.phase("first_repair_assertion_blocked_approve")

                # Evidence and required-order endpoints stay usable
                # read navigation; no precedence writer is offered.
                checkpoint = len(h.requests_log)
                endpoints = self.tid(card, "question-order-source")
                expect(endpoints).to_have_count(2)
                self.assertTrue(endpoints.first.is_enabled())
                endpoints.first.click(timeout=ASSERT_TIMEOUT_MS)
                expect(page.locator("#source-heading")).to_be_focused(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(h.mutations_since(checkpoint), [])
                self.assertEqual(
                    page.locator(
                        "[data-testid*='precedence'], "
                        "[data-testid*='order-save'], "
                        "[data-testid*='order-commit']").count(), 0)
                self.assertEqual(self.snapshot(context), snapshot)

                # Grade mismatch on an ordinary card, via its real
                # editor, must disable approval with an Arabic reason.
                mismatched = self.edit_question(
                    page, context, ordinary, "question-edit-grade",
                    CHANGED_GRADE)
                self.assertEqual(mismatched["current_grade"], CHANGED_GRADE)
                self.assertEqual(
                    mismatched["generation_grade"], self.fx["GRADE"])
                self.assertIn(
                    "grade_objective_mismatch", mismatched["blocks"])
                ordinary_card = h.question_card(page, mismatched)
                mismatch_chip = ordinary_card.locator(
                    "[data-testid='question-block']"
                    "[data-block='grade_objective_mismatch']")
                expect(mismatch_chip).to_have_count(
                    1, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(ordinary_card, "question-edit")
                       ).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
                self.assertTrue(
                    self.tid(ordinary_card, "question-approve")
                    .is_disabled(),
                    "mismatched card approval stayed enabled")
                mismatch_text = mismatch_chip.inner_text(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertTrue(
                    "الصف" in mismatch_text or "الهدف" in mismatch_text,
                    mismatch_text)
                self.assertNotIn(
                    "grade_objective_mismatch",
                    ordinary_card.inner_text(timeout=ASSERT_TIMEOUT_MS))
                self.phase("grade_mismatch_block_explained")

                restored = self.edit_question(
                    page, context, mismatched, "question-edit-grade",
                    self.fx["GRADE"])
                self.assertEqual(restored["blocks"], [])
                approved_now = self.decide(
                    page, context, restored, "approve")
                self.assertEqual(approved_now["status"], "approved")
                preview = h.api_json(
                    context, lifecycle.PREVIEW_PATH)["items"]
                self.assertEqual(
                    [item["question_id"] for item in preview],
                    [approved_now["question_id"]])
                expect(h.preview_item(page, approved_now["question_id"])
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                # Both decision controls stay visible in the
                # decided (approved) state.
                approved_card = h.question_card(page, approved_now)
                expect(self.tid(approved_card, "question-approve")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(approved_card, "question-reject")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)

                rejected = self.decide(page, context, other, "reject")
                self.assertEqual(rejected["status"], "rejected")
                rejected_card = h.question_card(page, rejected)
                expect(self.tid(rejected_card, "question-status")
                       ).to_have_text(
                           journey.STATUS_ARABIC["rejected"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(rejected_card, "question-decision")
                       ).to_have_text(
                           journey.DECISION_ARABIC["rejected"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(rejected_card, "question-approve")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(rejected_card, "question-approve")
                       ).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(rejected_card, "question-reject")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)

                final = self.snapshot(context)
                for item in final["questions"]:
                    self.assertEqual(
                        (item["original_question"],
                         item["original_answer_guidance"],
                         item["original_cognitive_explanation"]),
                        baseline_originals[item["question_id"]])
                self.assertEqual(
                    h.raw_result(context, source_request),
                    baseline_source_raw)
                self.assertEqual(
                    h.raw_result(context, question_request),
                    baseline_question_raw)
                h.assert_counts(1, 1, 1, 1)
                h.assert_clean()
                self.phase("blocked_explained_then_restored_approved")
            finally:
                self.write_artifacts(page)

    # -- 6. complete original drafts and decision feedback ----------
    def test_complete_original_and_decision_feedback(self):
        h = self.h
        known_raw_ids = set()
        forwarded_raw = []

        def fail_unknown_raw(route, request):
            path = urlsplit(request.url).path
            request_id = unquote(
                path[len("/api/session/requests/"):-len("/raw")])
            if request_id in known_raw_ids:
                route.continue_()
                return
            response = route.fetch(max_redirects=0, max_retries=0)
            forwarded_raw.append(
                {"status": response.status, "path": path})
            with contextlib.suppress(Exception):
                response.dispose()
            route.abort("failed")

        with h.browser_page() as (context, page):
            try:
                metadata, source_request = h.start_normal_source(page)
                source_id = metadata["source_id"]
                h.review_both_regions(page, context)
                question_request, snapshot = self.generate_expect(
                    page, context, source_id, 1, 5,
                    "structured_generation")
                known_raw_ids.update((source_request, question_request))
                raw_items = {
                    item["slot_id"]: item
                    for item in h.raw_result(
                        context, question_request)["raw"]["items"]}
                self.assertTrue(
                    raw_items[SLOT_APPLY]["hypothetical_assumptions"])
                self.assertTrue(
                    raw_items[SLOT_EVALUATE]["evaluate_criteria"])
                self.assertEqual(
                    len(raw_items[SLOT_CREATE]["create_rubric"]), 2)
                self.assertEqual(
                    raw_items["slot-analyze"]["status"], "abstain")
                by_slot = {
                    slot: h.question_by_request_slot(
                        snapshot, question_request, slot)
                    for slot in (lifecycle.SLOT_REMEMBER,
                                 lifecycle.SLOT_UNDERSTAND, SLOT_APPLY,
                                 SLOT_EVALUATE, SLOT_CREATE)}
                for slot in (SLOT_APPLY, SLOT_EVALUATE, SLOT_CREATE):
                    self.assertEqual(by_slot[slot]["blocks"], [], slot)
                initial_empty = self.tid(
                    page, "preview-empty").inner_text(
                        timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(initial_empty, INITIAL_PREVIEW_EMPTY)
                self.phase("setup_structured_five_drafts")

                apply_card = h.question_card(page, by_slot[SLOT_APPLY])
                self.open_disclosure(apply_card, "question-original")
                # FIRST repair assertion: the original level marker.
                # Omitted by the frozen product.
                self.assertEqual(
                    self.tid(apply_card, "question-original-level")
                    .count(), 1,
                    "question-original-level marker is missing")
                self.phase("first_repair_assertion_original_level")

                def fact_row(scope_locator, quote, label):
                    # The renderer's row: exact-quote span plus a
                    # source navigation button, in one .evidence-row.
                    for row in scope_locator.locator(
                            ".evidence-row").all():
                        if any(span.text_content() == quote
                               for span in row.locator("span").all()):
                            return row
                    raise AssertionError(
                        f"{label}: no evidence row for {quote!r}")

                def assert_fact_identity(scope_locator, fact, label):
                    row = fact_row(
                        scope_locator, fact["quote"], label)
                    source = row.locator("button")
                    self.assertEqual(source.count(), 1, label)
                    self.assertEqual(
                        source.first.get_attribute("data-region-id"),
                        fact["region_id"], label)
                    return source.first

                def entry_item(container, entry, label):
                    for item in container.locator("li").all():
                        if any(span.text_content() == entry["text"]
                               for span in item.locator("span").all()):
                            return item
                    raise AssertionError(
                        f"{label}: entry {entry['text']!r} not rendered")

                def assert_original(scope, prefix, raw_item):
                    self.assertEqual(
                        scope.get_by_test_id(f"{prefix}-text")
                        .text_content(), raw_item["question"], prefix)
                    self.assertEqual(
                        scope.get_by_test_id(f"{prefix}-guidance")
                        .text_content(), raw_item["answer_guidance"],
                        prefix)
                    self.assertEqual(
                        scope.get_by_test_id(f"{prefix}-cognitive")
                        .text_content(),
                        raw_item["cognitive_explanation"], prefix)
                    self.assertEqual(
                        scope.get_by_test_id(f"{prefix}-level")
                        .text_content(),
                        journey.BLOOM_ARABIC[raw_item["level"]], prefix)
                    evidence_box = scope.get_by_test_id(
                        f"{prefix}-evidence")
                    if raw_item["evidence"]:
                        self.assertEqual(
                            evidence_box.count(), 1, prefix)
                        self.assertEqual(
                            evidence_box.locator(
                                ".evidence-row").count(),
                            len(raw_item["evidence"]), prefix)
                        for fact in raw_item["evidence"]:
                            assert_fact_identity(
                                evidence_box, fact,
                                f"{prefix}-evidence")
                    else:
                        self.assertTrue(
                            evidence_box.count() == 0
                            or not evidence_box.text_content().strip(),
                            f"fabricated rows in empty "
                            f"{prefix}-evidence")
                    assumptions_box = scope.get_by_test_id(
                        f"{prefix}-assumptions")
                    assumptions = list(
                        raw_item["hypothetical_assumptions"])
                    if assumptions:
                        self.assertEqual(
                            assumptions_box.count(), 1, prefix)
                        text = assumptions_box.text_content()
                        for value in assumptions:
                            self.assertIn(value, text, prefix)
                    else:
                        self.assertTrue(
                            assumptions_box.count() == 0
                            or not assumptions_box.text_content()
                            .strip(),
                            f"fabricated rows in empty "
                            f"{prefix}-assumptions")
                    for testid, entries in (
                            (f"{prefix}-criteria",
                             raw_item["evaluate_criteria"]),
                            (f"{prefix}-rubric",
                             raw_item["create_rubric"])):
                        container = scope.get_by_test_id(testid)
                        if entries:
                            self.assertEqual(
                                container.count(), 1, testid)
                            for entry in entries:
                                item = entry_item(
                                    container, entry, testid)
                                self.assertIn(
                                    "من المصدر"
                                    if entry["origin"] == "source"
                                    else "من الهدف",
                                    item.text_content(), testid)
                                if entry["origin"] == "objective":
                                    # Objective-origin entries
                                    # carry no source-fact row.
                                    self.assertEqual(
                                        entry["supporting_facts"], [],
                                        testid)
                                    self.assertEqual(
                                        item.locator(
                                            ".evidence-row").count(),
                                        0, testid)
                                else:
                                    for fact in (
                                            entry["supporting_facts"]):
                                        assert_fact_identity(
                                            item, fact, testid)
                        else:
                            self.assertTrue(
                                container.count() == 0
                                or not container.text_content().strip(),
                                f"fabricated rows in empty {testid}")

                # Teacher edit first; originals are checked after it.
                edited_apply = self.edit_question(
                    page, context, by_slot[SLOT_APPLY],
                    "question-edit-question", TEACHER_EDITED_WORDING)
                self.assertEqual(
                    edited_apply["current_question"],
                    TEACHER_EDITED_WORDING)
                self.assertEqual(
                    edited_apply["original_question"],
                    raw_items[SLOT_APPLY]["question"])
                apply_card = h.question_card(page, edited_apply)
                expect(self.tid(apply_card, "question-heading")
                       ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
                self.assert_status_announces(
                    page, ("حُفظ", "حفظ"), "announce_save")

                # The other scalar currents must discriminate too:
                # guidance and rationale through the same editor,
                # the Bloom level through its select control.
                edited_apply = self.edit_question(
                    page, context, edited_apply,
                    "question-edit-guidance", TEACHER_EDITED_GUIDANCE)
                edited_apply = self.edit_question(
                    page, context, edited_apply,
                    "question-edit-cognitive",
                    TEACHER_EDITED_COGNITIVE)
                apply_card = h.question_card(page, edited_apply)
                form = self.tid(apply_card, "question-edit-form")
                if not form.is_visible():
                    self.tid(apply_card, "question-edit").click(
                        timeout=ASSERT_TIMEOUT_MS)
                expect(form).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                self.tid(apply_card, "question-edit-level"
                         ).select_option(
                             TEACHER_EDITED_LEVEL,
                             timeout=ASSERT_TIMEOUT_MS)
                path = h.question_path(edited_apply)
                response = h.capture_click(
                    page, "PATCH", path,
                    self.tid(apply_card, "question-edit-commit"))
                self.obs["statuses"]["edit:slot-apply:level"] = (
                    response.status)
                self.assertEqual(response.status, 200, path)
                edited_apply = self.question_by_id(
                    self.snapshot(context),
                    edited_apply["question_id"])
                raw_apply = raw_items[SLOT_APPLY]
                self.assertEqual(
                    edited_apply["current_question"],
                    TEACHER_EDITED_WORDING)
                self.assertEqual(
                    edited_apply["current_answer_guidance"],
                    TEACHER_EDITED_GUIDANCE)
                self.assertEqual(
                    edited_apply["current_cognitive_explanation"],
                    TEACHER_EDITED_COGNITIVE)
                self.assertEqual(
                    edited_apply["current_level"],
                    TEACHER_EDITED_LEVEL)
                self.assertNotEqual(
                    TEACHER_EDITED_WORDING, raw_apply["question"])
                self.assertNotEqual(
                    TEACHER_EDITED_GUIDANCE,
                    raw_apply["answer_guidance"])
                self.assertNotEqual(
                    TEACHER_EDITED_COGNITIVE,
                    raw_apply["cognitive_explanation"])
                self.assertNotEqual(
                    TEACHER_EDITED_LEVEL, raw_apply["level"])
                # Snapshot originals and the model level are
                # immutable, and the edited card stays approvable.
                self.assertEqual(
                    edited_apply["original_answer_guidance"],
                    raw_apply["answer_guidance"])
                self.assertEqual(
                    edited_apply["original_cognitive_explanation"],
                    raw_apply["cognitive_explanation"])
                self.assertEqual(
                    edited_apply["model_level"], raw_apply["level"])
                self.assertEqual(edited_apply["blocks"], [])
                apply_card = h.question_card(page, edited_apply)

                approved = {}
                for slot, question in (
                        (SLOT_APPLY, edited_apply),
                        (SLOT_EVALUATE, by_slot[SLOT_EVALUATE]),
                        (SLOT_CREATE, by_slot[SLOT_CREATE])):
                    approved[slot] = self.decide(
                        page, context, question, "approve")
                    self.assertEqual(
                        approved[slot]["status"], "approved", slot)
                # After explicit valid approval the currents still
                # differ from the immutable raw originals.
                approved_apply = approved[SLOT_APPLY]
                self.assertEqual(
                    approved_apply["current_question"],
                    TEACHER_EDITED_WORDING)
                self.assertEqual(
                    approved_apply["current_answer_guidance"],
                    TEACHER_EDITED_GUIDANCE)
                self.assertEqual(
                    approved_apply["current_cognitive_explanation"],
                    TEACHER_EDITED_COGNITIVE)
                self.assertEqual(
                    approved_apply["current_level"],
                    TEACHER_EDITED_LEVEL)
                self.assertEqual(
                    approved_apply["original_question"],
                    raw_apply["question"])
                self.assertEqual(
                    approved_apply["original_answer_guidance"],
                    raw_apply["answer_guidance"])
                self.assertEqual(
                    approved_apply["original_cognitive_explanation"],
                    raw_apply["cognitive_explanation"])
                self.assertEqual(
                    approved_apply["model_level"], raw_apply["level"])
                create_card = h.question_card(
                    page, approved[SLOT_CREATE])
                expect(self.tid(create_card, "question-heading")
                       ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
                self.assert_status_announces(
                    page, ("اعتُمد", "معتمد"), "announce_approve")
                # Both decision controls stay visible on each
                # actually approved card.
                for slot in (SLOT_APPLY, SLOT_EVALUATE, SLOT_CREATE):
                    decided_card = h.question_card(
                        page, approved[slot])
                    expect(self.tid(decided_card, "question-approve")
                           ).to_be_visible(
                               timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(decided_card, "question-reject")
                           ).to_be_visible(
                               timeout=ASSERT_TIMEOUT_MS)
                self.phase("edited_then_three_approvals_announced")

                # Author and teacher decision stay distinct markers.
                expect(self.tid(apply_card, "question-author")
                       ).to_have_text(
                           journey.CURRENT_AUTHOR_ARABIC["teacher"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(apply_card, "question-decision")
                       ).to_have_text(
                           journey.DECISION_ARABIC["approved"],
                           timeout=ASSERT_TIMEOUT_MS)
                remember_card = h.question_card(
                    page, by_slot[lifecycle.SLOT_REMEMBER])
                expect(self.tid(
                    remember_card, "question-approve")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    remember_card, "question-reject")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    remember_card, "question-approve")).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)

                # Complete originals survive edit and approval, in
                # cards and in the teacher preview.
                for slot in (SLOT_APPLY, SLOT_EVALUATE, SLOT_CREATE,
                             lifecycle.SLOT_REMEMBER):
                    question = approved.get(slot) or by_slot[slot]
                    card = h.question_card(page, question)
                    self.open_disclosure(card, "question-original")
                    assert_original(
                        card, "question-original", raw_items[slot])
                for slot in (SLOT_APPLY, SLOT_EVALUATE, SLOT_CREATE):
                    item = h.preview_item(
                        page, approved[slot]["question_id"])
                    expect(item).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.open_disclosure(item, "preview-original")
                    assert_original(
                        item, "preview-original", raw_items[slot])
                self.phase("complete_originals_in_cards_and_preview")

                # Exercise one original-evidence source link: it
                # selects that fact's exact source region and sends
                # no mutation.
                apply_card = h.question_card(
                    page, approved[SLOT_APPLY])
                self.open_disclosure(apply_card, "question-original")
                original_evidence = self.tid(
                    apply_card, "question-original-evidence")
                first_fact = raw_apply["evidence"][0]
                source_button = assert_fact_identity(
                    original_evidence, first_fact,
                    "question-original-evidence")
                fact_region = next(
                    region for region
                    in self.snapshot(context)["regions"]
                    if region["region_id"] == first_fact["region_id"])
                checkpoint = len(h.requests_log)
                source_button.click(timeout=ASSERT_TIMEOUT_MS)
                expect(page.locator("#source-heading")).to_be_focused(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    self.tid(page, "source-selected-text").count(), 1)
                self.assertEqual(
                    self.tid(page, "source-selected-text")
                    .text_content(), fact_region["current_text"])
                self.assertEqual(h.mutations_since(checkpoint), [])

                # Display-position numbering and honest full-page
                # evidence selection (scoped optional markers).
                current = self.snapshot(context)
                for region in current["regions"]:
                    item = h.region_item(page, region)
                    self.assertEqual(
                        self.tid(item, "region-position").count(), 1)
                    expect(self.tid(item, "region-position")
                           ).to_contain_text(
                               journey.arabic_indic(
                                   region["display_position"]),
                               timeout=ASSERT_TIMEOUT_MS)
                river = h.region_by_section(
                    current, lifecycle.REGION_RIVER)
                self.tid(
                    h.region_item(page, river),
                    "region-evidence-link").click(
                        timeout=ASSERT_TIMEOUT_MS)
                expect(page.locator("#source-heading")).to_be_focused(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    self.tid(page, "source-selected-text").count(), 1)
                self.assertEqual(
                    self.tid(page, "source-selected-text")
                    .text_content(), river["current_text"])
                expect(self.tid(page, "source-reference-caption")
                       ).to_have_text(
                           FULL_PAGE_CAPTION, timeout=ASSERT_TIMEOUT_MS)

                rejected = self.decide(
                    page, context, by_slot[lifecycle.SLOT_UNDERSTAND],
                    "reject")
                self.assertEqual(rejected["status"], "rejected")
                rejected_card = h.question_card(page, rejected)
                expect(self.tid(rejected_card, "question-heading")
                       ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
                self.assert_status_announces(
                    page, ("رُفض", "مرفوض"), "announce_reject")
                # Both decision controls stay visible on the
                # actually rejected card.
                expect(self.tid(rejected_card, "question-approve")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(rejected_card, "question-reject")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                self.phase("rejection_announced")

                # A failed raw read shows the full original as
                # unknown and never blocks edits or approval.
                page.route(
                    "**/api/session/requests/*/raw", fail_unknown_raw)
                second_request, second_snapshot = (
                    self.generate_expect(
                        page, context, source_id, 2, 10,
                        "structured_generation_2"))
                self.obs["notes"]["forwarded_failed_raw"] = forwarded_raw
                self.assertTrue(forwarded_raw)
                new_apply = h.question_by_request_slot(
                    second_snapshot, second_request, SLOT_APPLY)
                new_apply_card = h.question_card(page, new_apply)
                self.open_disclosure(
                    new_apply_card, "question-original")
                evidence_box = self.tid(
                    new_apply_card, "question-original-evidence")
                self.assertEqual(evidence_box.count(), 1)
                expect(evidence_box).to_contain_text(
                    "غير متاح", timeout=ASSERT_TIMEOUT_MS)
                evidence_text = evidence_box.text_content()
                self.assertIn("غير متاح", evidence_text)
                real_new_raw = {
                    item["slot_id"]: item
                    for item in h.raw_result(
                        context, second_request)["raw"]["items"]}
                for fact in real_new_raw[SLOT_APPLY]["evidence"]:
                    self.assertNotIn(fact["quote"], evidence_text)
                new_remember = h.question_by_request_slot(
                    second_snapshot, second_request,
                    lifecycle.SLOT_REMEMBER)
                new_remember = self.edit_question(
                    page, context, new_remember,
                    "question-edit-question", TEACHER_EDITED_WORDING)
                new_remember = self.decide(
                    page, context, new_remember, "approve")
                self.assertEqual(new_remember["status"], "approved")
                page.unroute(
                    "**/api/session/requests/*/raw", fail_unknown_raw)
                self.phase("failed_raw_unknown_but_not_load_bearing")

                # When every approved item is rejected again, the
                # empty preview states a reason, not the initial text.
                for question in (approved[SLOT_APPLY],
                                 approved[SLOT_EVALUATE],
                                 approved[SLOT_CREATE], new_remember):
                    self.decide(page, context, question, "reject")
                expect(self.tid(page, "preview-item")).to_have_count(0)
                empty = self.tid(page, "preview-empty")
                expect(empty).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                empty_text = empty.inner_text(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertNotEqual(empty_text, initial_empty)
                self.assertTrue(
                    any(token in empty_text for token in
                        ("مرفوض", "رُفض", "تحديث", "لم تعد")),
                    empty_text)
                h.assert_counts(1, 1, 1, 2)
                h.assert_clean()
                self.phase("empty_preview_reason_after_rejections")
            finally:
                with contextlib.suppress(Exception):
                    page.unroute(
                        "**/api/session/requests/*/raw",
                        fail_unknown_raw)
                self.write_artifacts(page)

    # -- 7. named terminal outcomes preserve prior work -------------
    def test_terminal_outcome_is_named_and_preserves_prior_work(self):
        h = self.h
        outcome = os.environ.get("BROWSER_PROVIDER_OUTCOME", "")
        self.assertIn(
            outcome, TERMINAL_ARABIC,
            "BROWSER_PROVIDER_OUTCOME must name one permitted outcome")
        self.obs["notes"]["provider_outcome"] = outcome
        with h.browser_page() as (context, page):
            try:
                metadata, source_request = h.start_normal_source(page)
                source_id = metadata["source_id"]
                reviewed = h.review_both_regions(page, context)
                baseline_source_raw = h.raw_result(
                    context, source_request)
                self.assertEqual(reviewed["questions"], [])
                self.phase("setup_source_reviewed")

                response = h.capture_click(
                    page, "POST", lifecycle.QUESTIONS_PATH,
                    h.generate(page))
                self.obs["statuses"]["terminal_generation"] = (
                    response.status)
                self.assertEqual(
                    response.status,
                    TERMINAL_HTTP_STATUS.get(outcome, 502))
                body = h.wait_body(page, lifecycle.QUESTIONS_PATH, 1)
                request_id = h.assert_provider_command(
                    body, source_id, "terminal_generation")
                envelope = response.json()
                self.assertEqual(envelope["request_id"], request_id)
                self.assertEqual(
                    envelope["detail"]["code"], outcome)
                h.assert_record(
                    envelope["snapshot"], request_id, "questions",
                    "terminal", outcome)
                h.assert_status_endpoint(
                    context, request_id, source_id, "questions",
                    "terminal", outcome)
                self.phase("terminal_envelope_captured")

                # FIRST repair assertion: the persistent status text
                # names this exact outcome in Arabic. The frozen
                # product shows only the generic closed-non-ok text.
                expect(h.generate(page)).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                operation_text = h.op(page, "status").inner_text(
                    timeout=ASSERT_TIMEOUT_MS)
                workspace_text = self.tid(
                    page, "workspace-status").inner_text(
                        timeout=ASSERT_TIMEOUT_MS)
                combined = operation_text + "\n" + workspace_text
                self.obs["notes"]["terminal_operation_text"] = (
                    operation_text)
                self.obs["notes"]["terminal_workspace_text"] = (
                    workspace_text)
                self.assertIn(TERMINAL_ARABIC[outcome], combined)
                self.assertNotIn(lifecycle.OP_SUCCEEDED, operation_text)
                self.assertNotIn(GENERATION_SUCCESS_TEXT, workspace_text)
                self.assertNotEqual(
                    operation_text.strip(), GENERIC_CLOSED_NON_OK)
                self.phase("first_repair_assertion_named_outcome")

                # Explicitly unsuccessful: nothing substituted, no
                # approval, prior source/review/originals preserved,
                # and no automatic retry was sent.
                after = self.snapshot(context)
                self.assertEqual(after["source_id"], source_id)
                self.assertEqual(after["regions"], reviewed["regions"])
                self.assertEqual(after["questions"], [])
                self.assertEqual(after["preview_items"], [])
                self.assertEqual(after["grade"], reviewed["grade"])
                self.assertEqual(
                    after["objective"], reviewed["objective"])
                expect(self.tid(page, "question-card")).to_have_count(0)
                # Beside the existing generation action, the
                # explicit-new-request warning stays honest: the
                # prior request may continue at the provider and
                # may be billed; generating again is a new request.
                generation_stage = self.tid(
                    page, "stage-source-review")
                generation_warning = self.tid(
                    generation_stage, "generation-retry-warning")
                self.assertEqual(generation_warning.count(), 1)
                expect(generation_warning).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                generation_warning_text = (
                    generation_warning.inner_text(
                        timeout=ASSERT_TIMEOUT_MS))
                self.obs["notes"]["generation_retry_warning"] = (
                    generation_warning_text)
                for token in ("يستمر", "يُحتسب", "طلبًا جديدًا"):
                    self.assertIn(token, generation_warning_text)
                for region in reviewed["regions"]:
                    item = h.region_item(page, region)
                    expect(self.tid(item, "region-current-text-input")
                           ).to_have_value(
                               region["current_text"],
                               timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "region-inclusion-input")
                           ).to_have_value(
                               "included", timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    h.api_bytes(context, metadata["original_url"]),
                    self.png_bytes)
                self.assertEqual(
                    h.raw_result(context, source_request),
                    baseline_source_raw)
                self.assertEqual(
                    h.request_count(
                        "POST", lifecycle.QUESTIONS_PATH), 1)
                raw_probe = h.api_get(
                    context, "/api/session/requests/"
                    + request_id + "/raw")
                self.obs["statuses"]["terminal_raw_get"] = (
                    raw_probe.status)
                if raw_probe.status == 200:
                    payload = raw_probe.json()
                    self.obs["notes"]["terminal_raw_retention"] = (
                        payload.get("raw_retention"))
                    if payload.get("raw_retention") == "retained":
                        result = payload["provider_result"]
                        self.assertEqual(result["outcome"], outcome)
                        self.assertIsNone(result["raw"])
                        self.assertIn("not Meta", result["model_name"])
                h.assert_counts(1, 1, 1, 1)
                h.assert_clean()
                self.phase("terminal_preserved_prior_work_no_retry")
            finally:
                self.write_artifacts(page)


if __name__ == "__main__":
    unittest.main()



