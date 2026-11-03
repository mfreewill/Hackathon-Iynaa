"""Measured teacher-UX remainder browser regressions.

The test harness serves
app on a fresh app/browser context per selected run and injects
one server-only synthetic fixture (never chosen through HTTP,
explicitly not Meta): the held ``ControlsProvider`` for
``test_identity_refusal_withdraws_current_claims`` (six isolated
runs selected by ``BROWSER_REFUSAL_ACTION``: ``settings``,
``start``, ``region-review``, ``region-move``, ``question-edit``
or ``question-reject``; default ``settings``),
``test_settings_permission_preserves_legitimate_recovery`` and
``test_rejected_empty_preview_has_no_invented_history``, and the
unchanged ``ReviewMetadataProvider`` for
``test_original_required_order_matches_retained_raw``. Four
methods, nine isolated runs.

This module defines exactly one TestCase and composes the
existing helpers instead of copying them: each case
instantiates ``ux.TeacherUXCompletionBrowserTests`` privately
under one of its existing method names, runs its ``setUp``
(which in turn composes the lifecycle helper), and drives the
ux/lifecycle ``browser_page``/journey/marker/assertion helpers
through qualified module references. Those helper objects are
not independently discovered or run. Environment
``BROWSER_BASE_URL``, ``BROWSER_EXECUTABLE``,
``BROWSER_ARTIFACT_DIR``, ``BROWSER_HOLD_DIR`` and
``BROWSER_OPERATION_KIND=source`` keep their lifecycle
meanings; no armed holds are needed, and missing prerequisites
fail, never skip.

All mutations are visible DOM actions; context requests are
GET-only snapshot/raw observations of actual responses. The
only delivery-loss probe is in the settings-recovery method:
one actual snapshot GET is forwarded with
``route.fetch(max_redirects=0, max_retries=0)`` and then its
delivery is aborted. No response is fulfilled or rewritten,
no cookie/CSRF value is read or written, no app state is
injected, no direct API mutation or real model call happens,
and ``Response.finished()`` is never used where a body or a
visible result already establishes delivery. Chromium sandbox
stays on, contexts are fresh, outside origins are blocked by
the inherited lifecycle route handler, waits are finite, and
each case writes its own nonempty observation JSON plus
screenshot (actual IDs/statuses/counts/errors and final DOM
text) into ``BROWSER_ARTIFACT_DIR`` from ``finally``.

The identity-refusal paths, rejected-empty copy, and
original-order markers are checked directly; the
settings-recovery method checks the opposite direction.
"""
import contextlib
import json
import os
import pathlib
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect

import test_provider_operation_lifecycle as lifecycle
import test_teacher_ux_completion as ux

HELPER_METHOD = "test_settings_update_preserves_source_and_review_history"
ASSERT_TIMEOUT_MS = lifecycle.ASSERT_TIMEOUT_MS
NAV_TIMEOUT_MS = lifecycle.NAV_TIMEOUT_MS

REFUSAL_ACTIONS = (
    "settings", "start", "region-review", "region-move",
    "question-edit", "question-reject")
REGION_LOCAL_TEXT = (
    "  نَصٌّ مَحَلِّيٌّ غَيْرُ مَحْفُوظٍ لِلْمَقْطَعِ الْأَوَّلِ  ")
TEACHER_ANALYZE_WORDING = (
    "  صِيَاغَةُ الْمُعَلِّمِ الْمَحْفُوظَةُ لِسُؤَالِ التَّحْلِيلِ؟  ")
SLOT_ANALYZE = "slot-analyze"
SETTINGS_SAVED_TEXT = "حُفظ الصف والهدف."
NO_APPROVED_TOKEN = "لا أسئلة معتمدة حاليًا"
REJECTED_TOKEN = "مرفوض"
PRIOR_APPROVAL_CLAIM = "اعتُمدت سابقًا"
ALL_REJECTED_CLAIM = "كل الأسئلة"
OUTDATED_TOKEN = "تحتاج إلى تحديث"


class TeacherUXRemainderBrowserTests(unittest.TestCase):
    """Four composed remainder cases; parent selects per run."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        ux.TeacherUXCompletionBrowserTests.setUpClass()
        cls.fx = ux.TeacherUXCompletionBrowserTests.fx
        cls.png_bytes = (
            ux.TeacherUXCompletionBrowserTests.png_bytes)

    def setUp(self):
        self.u = ux.TeacherUXCompletionBrowserTests(HELPER_METHOD)
        self.u.setUp()
        self.h = self.u.h
        self.assertEqual(
            self.h.kind, "source",
            "remainder cases run against a separately served test application")
        self.obs = {"statuses": {}, "ids": {}, "notes": {}}

    # -- composed helper accessors ---------------------------------
    @property
    def tid(self):
        return self.h.tid

    def phase(self, name):
        self.h.mark_phase(name)

    def snapshot(self, context):
        return self.h.snapshot(context)

    def write_artifacts(self, page):
        """This case's own remainder observation JSON/screenshot.

        Mirrors the ux writer's capture-error honesty under the
        remainder case name: a failed optional read is recorded
        as null plus a capture error, never as clean state, and
        nothing here is an assertion.
        """
        h = self.h
        directory = pathlib.Path(h.artifact_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"teacher_ux_remainder_{self._testMethodName}"
        action = self.obs["notes"].get("refusal_action")
        if action:
            stem = f"{stem}_{action}"
        record = dict(h.observation)
        record["case"] = self._testMethodName
        record["method"] = self._testMethodName
        record["remainder_statuses"] = {
            **self.u.obs["statuses"], **self.obs["statuses"]}
        record["remainder_ids"] = {
            **self.u.obs["ids"], **self.obs["ids"]}
        record["remainder_notes"] = {
            **self.u.obs["notes"], **self.obs["notes"]}
        record["request_counts"] = {
            "sessions": h.request_count("POST", ux.SESSIONS_PATH),
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
            screenshot_name = f"{stem}.png"
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
                          "operation_status", "focus",
                          "screenshot"):
                record[field] = None
            capture_errors["page"] = "unavailable"
        (directory / f"{stem}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    # -- shared journey pieces --------------------------------------
    def start_approved_dirty(self, page, context):
        """ControlsProvider journey to one approval + dirty draft."""
        h = self.h
        metadata, _source_request = h.start_normal_source(page)
        source_id = metadata["source_id"]
        h.review_both_regions(page, context)
        question_request, _snapshot = h.generate_first_normal(
            page, context, source_id)
        approved, other = h.approve_and_dirty(
            page, context, question_request)
        baseline = h.assert_questions_preserved(
            page, context, question_request, approved, other)
        self.obs["ids"]["source_id"] = source_id
        self.obs["ids"]["question_request_id"] = question_request
        self.obs["ids"]["approved_question_id"] = (
            approved["question_id"])
        self.obs["ids"]["dirty_question_id"] = other["question_id"]
        self.phase("approved_preview_and_dirty_draft_current")
        return metadata, question_request, approved, other, baseline

    def open_second_session(self, context):
        """A second ordinary page creates the real new session."""
        h = self.h
        second = context.new_page()
        with second.expect_response(
                lambda item: item.request.method == "POST"
                and urlsplit(item.url).path == ux.SESSIONS_PATH,
                timeout=ASSERT_TIMEOUT_MS) as session_info:
            second.goto(
                h.base_url, wait_until="domcontentloaded",
                timeout=NAV_TIMEOUT_MS)
        self.obs["statuses"]["second_page_session"] = (
            session_info.value.status)
        self.assertEqual(session_info.value.status, 201)
        self.phase("second_page_new_session_201")
        return second

    # -- 1. identity refusal on each ordinary write path ----------
    def test_identity_refusal_withdraws_current_claims(self):
        h = self.h
        fx = self.fx
        action = (
            os.environ.get("BROWSER_REFUSAL_ACTION", "") or "settings")
        self.assertIn(
            action, REFUSAL_ACTIONS,
            "BROWSER_REFUSAL_ACTION must name one refusal action")
        self.obs["notes"]["refusal_action"] = action
        with h.browser_page() as (context, page):
            second = None
            try:
                (metadata, _question_request, approved, other,
                 baseline) = self.start_approved_dirty(page, context)
                source_id = metadata["source_id"]
                river = h.region_by_section(
                    baseline, lifecycle.REGION_RIVER)
                if action == "region-review":
                    item = h.region_item(page, river)
                    expect(item).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.tid(
                        item, "region-current-text-input").fill(
                            REGION_LOCAL_TEXT,
                            timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(
                        item, "region-current-text-input")
                    ).to_have_value(
                        REGION_LOCAL_TEXT, timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "region-review-commit")
                           ).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
                    self.phase("region_dirty_before_second_session")
                sessions_before = h.request_count(
                    "POST", ux.SESSIONS_PATH)
                self.assertEqual(sessions_before, 1)
                second = self.open_second_session(context)

                # Local-only preparation; the first page still
                # believes itself current until the refusal lands.
                if action in ("settings", "start"):
                    page.get_by_label(
                        "الصف الدراسي", exact=True).select_option(value=
                            fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
                    page.get_by_label(
                        "الهدف التعليمي", exact=True).fill(
                            fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
                if action == "start":
                    page.get_by_label(
                        "ملف صفحة المصدر", exact=True
                    ).set_input_files(
                        {"name": "source-page.png",
                         "mimeType": "image/png",
                         "buffer": self.png_bytes},
                        timeout=ASSERT_TIMEOUT_MS)
                if action == "settings":
                    method = "PUT"
                    path = lifecycle.SETTINGS_PATH
                    trigger = self.tid(page, "settings-apply")
                    expected_bodies = [{
                        "grade": fx["GRADE"],
                        "objective": fx["OBJECTIVE"],
                        "rights_ref": ""}]
                elif action == "start":
                    # Start sends its settings PUT first; the
                    # refusal must land before any destructive
                    # Start POST is issued. Its exact body shape
                    # is the workspace's own; only the canonical
                    # grade/objective it carries are asserted.
                    method = "PUT"
                    path = lifecycle.SETTINGS_PATH
                    trigger = h.primary(page)
                    expected_bodies = None
                elif action == "region-review":
                    method = "PUT"
                    path = h.region_path(river)
                    trigger = self.tid(
                        h.region_item(page, river),
                        "region-review-commit")
                    expected_bodies = [{
                        "inclusion": "included",
                        "current_text": REGION_LOCAL_TEXT,
                        "current_label": river["current_label"]}]
                elif action == "region-move":
                    method = "POST"
                    path = h.region_path(river) + "/move-down"
                    trigger = self.tid(
                        h.region_item(page, river), "region-move-down")
                    expected_bodies = [{}]
                elif action == "question-edit":
                    method = "PATCH"
                    path = h.question_path(other)
                    trigger = self.tid(
                        h.question_card(page, other),
                        "question-edit-commit")
                    expected_bodies = [
                        {"question": lifecycle.DIRTY_WORDING}]
                else:
                    method = "POST"
                    path = h.question_path(approved, "/reject")
                    trigger = self.tid(
                        h.question_card(page, approved),
                        "question-reject")
                    expected_bodies = [{}]
                expect(trigger).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(trigger).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                start_before = h.request_count(
                    "POST", lifecycle.START_PATH)
                source_before = h.request_count(
                    "POST", lifecycle.SOURCE_PATH)
                questions_before = h.request_count(
                    "POST", lifecycle.QUESTIONS_PATH)
                checkpoint = len(h.requests_log)
                refused = h.capture_click(page, method, path, trigger)
                self.obs["statuses"][f"refused_{action}"] = (
                    refused.status)
                self.assertEqual(refused.status, 403, action)
                self.assertEqual(
                    refused.json(), {"detail": "forbidden request"})
                self.assertEqual(
                    h.mutations_since(checkpoint), [(method, path)])
                sent_bodies = h.request_bodies(
                    method, path, checkpoint)
                if expected_bodies is None:
                    self.assertEqual(len(sent_bodies), 1)
                    self.assertIsInstance(sent_bodies[0], dict)
                    self.assertEqual(
                        sent_bodies[0].get("grade"), fx["GRADE"])
                    self.assertEqual(
                        sent_bodies[0].get("objective"),
                        fx["OBJECTIVE"])
                else:
                    self.assertEqual(sent_bodies, expected_bodies)
                self.phase(f"identity_refusal_403_{action}")

                # The one identity currentness disclosure; no
                # current preview or approval claim survives.
                refusal_surface = self.tid(
                    page, "setup-error" if action in ("settings", "start")
                    else "workspace-status")
                expect(refusal_surface).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(refusal_surface).to_contain_text(
                    ux.SESSION_REFUSAL_TOKEN,
                    timeout=ASSERT_TIMEOUT_MS)
                currency = self.tid(page, "review-currency-status")
                expect(currency).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(currency).to_contain_text(
                    ux.IDENTITY_CURRENCY_TOKEN,
                    timeout=ASSERT_TIMEOUT_MS)
                self.obs["notes"]["identity_currency_text"] = (
                    currency.inner_text(timeout=ASSERT_TIMEOUT_MS))
                expect(self.tid(page, "preview-item")).to_have_count(
                    0, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(page, "preview-empty")
                       ).to_contain_text(
                           ux.STALE_PREVIEW_TOKEN,
                           timeout=ASSERT_TIMEOUT_MS)
                approved_card = h.question_card(page, approved)
                expect(self.tid(approved_card, "question-status")
                       ).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(approved_card, "question-decision")
                       ).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
                # Server-mutation controls are withdrawn. Local
                # edit/cancel/typing availability is deliberately
                # not asserted here: it grants no server write.
                expect(h.generate(page)).to_be_disabled(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(page, "settings-apply")
                       ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                retry = h.op(page, "retry")
                self.assertTrue(
                    retry.is_hidden() or retry.is_disabled(),
                    "retry stayed usable after identity refusal")
                river_item = h.region_item(page, river)
                expect(self.tid(river_item, "region-review-commit")
                       ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(river_item, "region-move-up")
                       ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(river_item, "region-move-down")
                       ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                other_card = h.question_card(page, other)
                for card in (approved_card, other_card):
                    expect(self.tid(card, "question-approve")
                           ).to_be_disabled(
                               timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(card, "question-reject")
                           ).to_be_disabled(
                               timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(other_card, "question-edit-commit")
                       ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                # Known source, question identities and the exact
                # unsaved drafts stay readable on the first page.
                h.assert_source_visible(page, source_id)
                expect(self.tid(page, "question-card")).to_have_count(
                    2, timeout=ASSERT_TIMEOUT_MS)
                expect(approved_card).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(other_card).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(other_card, "question-edit-form")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(other_card, "question-edit-question")
                       ).to_have_value(
                           lifecycle.DIRTY_WORDING,
                           timeout=ASSERT_TIMEOUT_MS)
                if action == "region-review":
                    expect(self.tid(
                        river_item, "region-current-text-input")
                    ).to_have_value(
                        REGION_LOCAL_TEXT, timeout=ASSERT_TIMEOUT_MS)
                # No automatic session recreation and no provider
                # or destructive-Start retry followed the refusal.
                self.assertEqual(
                    h.request_count("POST", ux.SESSIONS_PATH),
                    sessions_before)
                self.assertEqual(
                    h.request_count("POST", lifecycle.START_PATH),
                    start_before)
                self.assertEqual(
                    h.request_count("POST", lifecycle.SOURCE_PATH),
                    source_before)
                self.assertEqual(
                    h.request_count("POST", lifecycle.QUESTIONS_PATH),
                    questions_before)
                self.assertEqual(
                    h.mutations_since(checkpoint), [(method, path)])
                probe = h.api_get(context, lifecycle.SNAPSHOT_PATH)
                self.obs["statuses"]["new_session_snapshot_get"] = (
                    probe.status)
                self.assertEqual(probe.status, 404)
                self.assertEqual(
                    probe.json(), {"detail": "page not found"})
                h.assert_clean()
                self.phase(f"identity_withdrawal_verified_{action}")
            finally:
                if second is not None:
                    with contextlib.suppress(Exception):
                        second.close()
                self.write_artifacts(page)

    # -- 2. acknowledged settings keep legitimate recovery -----
    def test_settings_permission_preserves_legitimate_recovery(self):
        h = self.h
        fx = self.fx
        forwarded = []

        def fail_snapshot(route, request):
            # The sole delivery-loss probe: forward the genuine
            # snapshot GET once, keep only its status/path, then
            # abort its delivery. Nothing is fabricated.
            response = route.fetch(max_redirects=0, max_retries=0)
            forwarded.append({
                "status": response.status,
                "method": request.method,
                "path": urlsplit(request.url).path})
            with contextlib.suppress(Exception):
                response.dispose()
            route.abort()

        canonical_body = {
            "grade": fx["GRADE"], "objective": fx["OBJECTIVE"],
            "rights_ref": ""}
        with h.browser_page() as (context, page):
            try:
                # Initial no-source save: canonical grade/objective
                # without selecting any file, and no Start or
                # provider send of any kind.
                response = page.goto(
                    "/", wait_until="domcontentloaded",
                    timeout=NAV_TIMEOUT_MS)
                self.assertIsNotNone(response)
                self.assertEqual(response.status, 200, page.url)
                page.wait_for_load_state(
                    "networkidle", timeout=NAV_TIMEOUT_MS)
                page.get_by_label(
                    "الصف الدراسي", exact=True).select_option(value=
                        fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
                page.get_by_label(
                    "الهدف التعليمي", exact=True).fill(
                        fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
                apply_button = self.tid(page, "settings-apply")
                self.assertEqual(apply_button.count(), 1)
                expect(apply_button).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(apply_button).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                gets_before = h.request_count(
                    "GET", lifecycle.SNAPSHOT_PATH)
                checkpoint = len(h.requests_log)
                initial = h.capture_click(
                    page, "PUT", lifecycle.SETTINGS_PATH, apply_button)
                self.obs["statuses"]["initial_settings"] = (
                    initial.status)
                self.assertEqual(initial.status, 200)
                self.assertEqual(
                    h.request_bodies(
                        "PUT", lifecycle.SETTINGS_PATH, checkpoint),
                    [canonical_body])
                self.assertEqual(initial.json(), canonical_body)
                expect(self.tid(page, "workspace-status")
                       ).to_contain_text(
                           SETTINGS_SAVED_TEXT,
                           timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    h.request_count("GET", lifecycle.SNAPSHOT_PATH),
                    gets_before)
                self.assertEqual(
                    h.request_count("POST", lifecycle.START_PATH), 0)
                self.assertEqual(
                    h.request_count("POST", lifecycle.SOURCE_PATH), 0)
                self.assertEqual(
                    h.request_count("POST", lifecycle.QUESTIONS_PATH),
                    0)
                self.phase("initial_no_source_settings_saved")

                (metadata, _question_request, approved, other,
                 baseline) = self.start_approved_dirty(page, context)
                source_id = metadata["source_id"]
                self.assertEqual(baseline["grade"], fx["GRADE"])
                self.assertEqual(
                    baseline["objective"], fx["OBJECTIVE"])
                apply_button = self.tid(page, "settings-apply")
                expect(apply_button).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)

                # Same-value re-save while current: exactly one
                # PUT, a semantic no-op, and no unnecessary page
                # snapshot read follows the acknowledgement.
                page.get_by_label(
                    "الصف الدراسي", exact=True).select_option(value=
                        fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
                page.get_by_label(
                    "الهدف التعليمي", exact=True).fill(
                        fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
                gets_before = h.request_count(
                    "GET", lifecycle.SNAPSHOT_PATH)
                checkpoint = len(h.requests_log)
                same_value = h.capture_click(
                    page, "PUT", lifecycle.SETTINGS_PATH, apply_button)
                self.obs["statuses"]["same_value_settings"] = (
                    same_value.status)
                self.assertEqual(same_value.status, 200)
                self.assertEqual(
                    h.request_bodies(
                        "PUT", lifecycle.SETTINGS_PATH, checkpoint),
                    [canonical_body])
                self.assertEqual(same_value.json(), canonical_body)
                self.assertEqual(
                    h.mutations_since(checkpoint),
                    [("PUT", lifecycle.SETTINGS_PATH)])
                expect(self.tid(page, "workspace-status")
                       ).to_contain_text(
                           SETTINGS_SAVED_TEXT,
                           timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    h.request_count("GET", lifecycle.SNAPSHOT_PATH),
                    gets_before)
                self.assertEqual(self.snapshot(context), baseline)
                currency = self.tid(page, "review-currency-status")
                expect(currency).to_be_hidden(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(page, "preview-item")).to_have_count(
                    1, timeout=ASSERT_TIMEOUT_MS)
                expect(h.preview_item(page, approved["question_id"])
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                self.phase("same_value_settings_noop_no_reread")

                # Changed objective: the PUT is acknowledged, but
                # its genuine resulting snapshot GET is forwarded
                # once and its delivery aborted. Currentness is
                # withdrawn for acknowledged settings only: never
                # source-outcome uncertainty, never sticky identity.
                source_before = h.request_count(
                    "POST", lifecycle.SOURCE_PATH)
                questions_before = h.request_count(
                    "POST", lifecycle.QUESTIONS_PATH)
                start_before = h.request_count(
                    "POST", lifecycle.START_PATH)
                changed_body = {
                    "grade": fx["GRADE"],
                    "objective": ux.NEW_OBJECTIVE, "rights_ref": ""}
                page.route(
                    "**/api/session/snapshot", fail_snapshot)
                page.get_by_label(
                    "الهدف التعليمي", exact=True).fill(
                        ux.NEW_OBJECTIVE, timeout=ASSERT_TIMEOUT_MS)
                checkpoint = len(h.requests_log)
                changed = h.capture_click(
                    page, "PUT", lifecycle.SETTINGS_PATH, apply_button)
                self.obs["statuses"]["changed_settings"] = (
                    changed.status)
                self.assertEqual(changed.status, 200)
                self.assertEqual(
                    h.request_bodies(
                        "PUT", lifecycle.SETTINGS_PATH, checkpoint),
                    [changed_body])
                self.assertEqual(changed.json(), changed_body)
                self.assertEqual(
                    h.mutations_since(checkpoint),
                    [("PUT", lifecycle.SETTINGS_PATH)])
                expect(currency).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(currency).to_contain_text(
                    ux.SETTINGS_CURRENCY_TOKEN,
                    timeout=ASSERT_TIMEOUT_MS)
                currency_text = currency.inner_text(
                    timeout=ASSERT_TIMEOUT_MS)
                self.obs["notes"]["settings_currency_text"] = (
                    currency_text)
                self.assertNotIn(
                    ux.IDENTITY_CURRENCY_TOKEN, currency_text)
                for _ in range(100):
                    if forwarded:
                        break
                    page.wait_for_timeout(20)
                self.obs["notes"]["forwarded_snapshot_gets"] = (
                    forwarded)
                self.assertEqual(len(forwarded), 1, forwarded)
                self.assertEqual(forwarded[0]["method"], "GET")
                self.assertEqual(
                    forwarded[0]["path"], lifecycle.SNAPSHOT_PATH)
                self.assertEqual(forwarded[0]["status"], 200)
                expect(self.tid(page, "preview-item")).to_have_count(
                    0, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(page, "preview-empty")
                       ).to_contain_text(
                           ux.STALE_PREVIEW_TOKEN,
                           timeout=ASSERT_TIMEOUT_MS)
                self.assertNotEqual(
                    self.tid(page, "provider-operation-status")
                    .inner_text(timeout=ASSERT_TIMEOUT_MS).strip(),
                    lifecycle.OP_UNCONFIRMED)
                h.assert_source_visible(page, source_id)
                other_card = h.question_card(page, other)
                expect(self.tid(other_card, "question-edit-question")
                       ).to_have_value(
                           lifecycle.DIRTY_WORDING,
                           timeout=ASSERT_TIMEOUT_MS)
                # The deliberate correction path stays open while
                # ordinary generation is withdrawn.
                expect(apply_button).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(h.generate(page)).to_be_disabled(
                    timeout=ASSERT_TIMEOUT_MS)
                changed_snapshot = self.snapshot(context)
                self.assertEqual(
                    changed_snapshot["source_id"], source_id)
                self.assertEqual(
                    changed_snapshot["objective"], ux.NEW_OBJECTIVE)
                self.assertEqual(
                    changed_snapshot["settings_revision"],
                    baseline["settings_revision"] + 1)
                self.assertEqual(
                    changed_snapshot["source_revision"],
                    baseline["source_revision"])
                self.assertEqual(
                    changed_snapshot["regions"], baseline["regions"])
                self.assertEqual(
                    {item["question_id"]
                     for item in changed_snapshot["questions"]},
                    {approved["question_id"], other["question_id"]})
                self.assertEqual(
                    self.u.question_by_id(
                        changed_snapshot, approved["question_id"]
                    )["status"], "outdated")
                self.assertEqual(
                    changed_snapshot["preview_items"], [])
                page.unroute("**/api/session/snapshot", fail_snapshot)
                self.phase("changed_settings_snapshot_delivery_lost")

                # Deliberately restore canonical settings through
                # that same button: real PUT plus matching readback
                # restore currentness, with the same source and
                # questions, no automatic model request, and no
                # resurrected approval.
                page.get_by_label(
                    "الصف الدراسي", exact=True).select_option(value=
                        fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
                page.get_by_label(
                    "الهدف التعليمي", exact=True).fill(
                        fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
                checkpoint = len(h.requests_log)
                restored_put = h.capture_click(
                    page, "PUT", lifecycle.SETTINGS_PATH, apply_button)
                self.obs["statuses"]["restored_settings"] = (
                    restored_put.status)
                self.assertEqual(restored_put.status, 200)
                self.assertEqual(
                    h.request_bodies(
                        "PUT", lifecycle.SETTINGS_PATH, checkpoint),
                    [canonical_body])
                self.assertEqual(restored_put.json(), canonical_body)
                self.assertEqual(
                    h.mutations_since(checkpoint),
                    [("PUT", lifecycle.SETTINGS_PATH)])
                expect(currency).to_be_hidden(
                    timeout=ASSERT_TIMEOUT_MS)
                restored = self.snapshot(context)
                self.assertEqual(restored["source_id"], source_id)
                self.assertEqual(restored["grade"], fx["GRADE"])
                self.assertEqual(
                    restored["objective"], fx["OBJECTIVE"])
                self.assertEqual(
                    restored["settings_revision"],
                    changed_snapshot["settings_revision"] + 1)
                self.assertEqual(
                    restored["source_revision"],
                    baseline["source_revision"])
                self.assertEqual(
                    restored["regions"], baseline["regions"])
                self.assertEqual(
                    {item["question_id"]
                     for item in restored["questions"]},
                    {approved["question_id"], other["question_id"]})
                self.assertEqual(
                    self.u.question_by_id(
                        restored, approved["question_id"])["status"],
                    "outdated")
                self.assertEqual(restored["preview_items"], [])
                expect(self.tid(page, "preview-item")).to_have_count(
                    0, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(page, "preview-empty")
                       ).to_contain_text(
                           OUTDATED_TOKEN,
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(other_card, "question-edit-question")
                       ).to_have_value(
                           lifecycle.DIRTY_WORDING,
                           timeout=ASSERT_TIMEOUT_MS)
                expect(h.generate(page)).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    h.request_count("POST", lifecycle.SOURCE_PATH),
                    source_before)
                self.assertEqual(
                    h.request_count("POST", lifecycle.QUESTIONS_PATH),
                    questions_before)
                self.assertEqual(
                    h.request_count("POST", lifecycle.START_PATH),
                    start_before)
                h.assert_clean()
                self.phase("canonical_settings_recovery_verified")
            finally:
                with contextlib.suppress(Exception):
                    page.unroute(
                        "**/api/session/snapshot", fail_snapshot)
                self.write_artifacts(page)

    # -- 3. rejected empty preview states current facts only ---
    def test_rejected_empty_preview_has_no_invented_history(self):
        h = self.h
        with h.browser_page() as (context, page):
            try:
                metadata, _source_request = h.start_normal_source(page)
                source_id = metadata["source_id"]
                h.review_both_regions(page, context)
                question_request, snapshot = h.generate_first_normal(
                    page, context, source_id)
                remember = h.question_by_request_slot(
                    snapshot, question_request, lifecycle.SLOT_REMEMBER)
                understand = h.question_by_request_slot(
                    snapshot, question_request,
                    lifecycle.SLOT_UNDERSTAND)
                self.assertEqual(remember["status"], "draft")
                self.assertEqual(understand["status"], "draft")
                self.assertEqual(snapshot["preview_items"], [])
                self.obs["ids"]["remember_question_id"] = (
                    remember["question_id"])
                self.obs["ids"]["understand_question_id"] = (
                    understand["question_id"])
                baseline_by_id = {
                    item["question_id"]: item
                    for item in snapshot["questions"]}
                baseline_raw = h.raw_result(context, question_request)
                initial_empty = self.tid(
                    page, "preview-empty").inner_text(
                        timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    initial_empty, ux.INITIAL_PREVIEW_EMPTY)
                self.phase("setup_two_drafts_no_approvals")

                # Direct draft-to-reject, with no approval anywhere
                # in this workspace's history.
                rejected = self.u.decide(
                    page, context, remember, "reject")
                self.assertEqual(rejected["status"], "rejected")
                mixed = self.snapshot(context)
                self.assertEqual(
                    self.u.question_by_id(
                        mixed, understand["question_id"])["status"],
                    "draft")
                self.assertEqual(mixed["preview_items"], [])
                self.assertEqual(
                    h.api_json(context, lifecycle.PREVIEW_PATH)
                    ["items"], [])
                expect(self.tid(page, "preview-item")).to_have_count(
                    0, timeout=ASSERT_TIMEOUT_MS)
                empty = self.tid(page, "preview-empty")
                expect(empty).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                # FIRST repair assertion: truthful current-facts
                # copy. The predecessor claims prior approval here.
                expect(empty).to_contain_text(
                    REJECTED_TOKEN, timeout=ASSERT_TIMEOUT_MS)
                mixed_text = empty.inner_text(
                    timeout=ASSERT_TIMEOUT_MS)
                self.obs["notes"]["preview_empty_draft_rejected"] = (
                    mixed_text)
                self.assertTrue(mixed_text.strip(), mixed_text)
                self.assertNotEqual(mixed_text, initial_empty)
                self.assertIn(NO_APPROVED_TOKEN, mixed_text)
                self.assertIn(REJECTED_TOKEN, mixed_text)
                self.assertNotIn(PRIOR_APPROVAL_CLAIM, mixed_text)
                self.assertNotIn(ALL_REJECTED_CLAIM, mixed_text)
                self.phase("first_repair_assertion_rejected_copy")
                # Neither card nor its content was deleted.
                expect(self.tid(page, "question-card")).to_have_count(
                    2, timeout=ASSERT_TIMEOUT_MS)
                for current in mixed["questions"]:
                    before = baseline_by_id[current["question_id"]]
                    for field in (
                            "current_question", "current_answer_guidance",
                            "current_cognitive_explanation",
                            "original_question",
                            "original_answer_guidance",
                            "original_cognitive_explanation"):
                        self.assertEqual(
                            current[field], before[field], field)

                # A genuine approve-then-reject of the sibling
                # still ends with an honestly empty preview.
                sibling = self.u.question_by_id(
                    mixed, understand["question_id"])
                approved_sibling = self.u.decide(
                    page, context, sibling, "approve")
                self.assertEqual(
                    approved_sibling["status"], "approved")
                expect(h.preview_item(
                    page, approved_sibling["question_id"])
                ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                rejected_sibling = self.u.decide(
                    page, context, approved_sibling, "reject")
                self.assertEqual(
                    rejected_sibling["status"], "rejected")
                final = self.snapshot(context)
                self.assertEqual(final["source_id"], source_id)
                self.assertEqual(final["preview_items"], [])
                self.assertEqual(
                    h.api_json(context, lifecycle.PREVIEW_PATH)
                    ["items"], [])
                self.assertEqual(
                    {item["question_id"] for item in final["questions"]},
                    set(baseline_by_id))
                for current in final["questions"]:
                    before = baseline_by_id[current["question_id"]]
                    self.assertEqual(current["status"], "rejected")
                    for field in (
                            "current_question", "current_answer_guidance",
                            "current_cognitive_explanation",
                            "original_question",
                            "original_answer_guidance",
                            "original_cognitive_explanation"):
                        self.assertEqual(
                            current[field], before[field], field)
                self.assertEqual(
                    final["regions"], snapshot["regions"])
                expect(self.tid(page, "preview-item")).to_have_count(
                    0, timeout=ASSERT_TIMEOUT_MS)
                expect(empty).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(empty).to_contain_text(
                    REJECTED_TOKEN, timeout=ASSERT_TIMEOUT_MS)
                final_text = empty.inner_text(
                    timeout=ASSERT_TIMEOUT_MS)
                self.obs["notes"]["preview_empty_after_cycle"] = (
                    final_text)
                self.assertTrue(final_text.strip(), final_text)
                self.assertIn(NO_APPROVED_TOKEN, final_text)
                self.assertIn(REJECTED_TOKEN, final_text)
                self.assertNotIn(PRIOR_APPROVAL_CLAIM, final_text)
                self.assertNotIn(ALL_REJECTED_CLAIM, final_text)
                self.assertEqual(
                    h.raw_result(context, question_request),
                    baseline_raw)
                h.assert_counts(1, 1, 1, 1)
                h.assert_clean()
                self.phase("approve_reject_cycle_preview_still_empty")
            finally:
                self.write_artifacts(page)

    # -- 4. original disclosure keeps its required order -------
    def test_original_required_order_matches_retained_raw(self):
        h = self.h
        with h.browser_page() as (context, page):
            try:
                metadata, source_request = h.start_normal_source(page)
                source_id = metadata["source_id"]
                h.review_both_regions(page, context)
                question_request, snapshot = self.u.generate_expect(
                    page, context, source_id, 1, total_questions=3,
                    label="metadata_generation")
                river = h.region_by_section(
                    snapshot, lifecycle.REGION_RIVER)
                trees = h.region_by_section(
                    snapshot, lifecycle.REGION_TREES)
                expected_pair = [
                    [river["region_id"], trees["region_id"]]]
                blocked = [
                    item for item in snapshot["questions"]
                    if "required_order_missing" in item["blocks"]]
                self.assertEqual(len(blocked), 1)
                analyze = blocked[0]
                self.assertEqual(analyze["request_id"], question_request)
                self.assertEqual(analyze["slot_id"], SLOT_ANALYZE)
                self.assertEqual(
                    analyze["required_order_relationships"],
                    expected_pair)
                remember = h.question_by_request_slot(
                    snapshot, question_request, lifecycle.SLOT_REMEMBER)
                self.assertEqual(remember["blocks"], [])
                self.assertEqual(
                    remember["required_order_relationships"], [])
                self.obs["ids"]["analyze_question_id"] = (
                    analyze["question_id"])
                self.obs["ids"]["remember_question_id"] = (
                    remember["question_id"])
                self.obs["ids"]["river_region_id"] = (
                    river["region_id"])
                self.obs["ids"]["trees_region_id"] = (
                    trees["region_id"])
                # The retained raw of this exact request/slot is
                # the only source for the original pair.
                baseline_source_raw = h.raw_result(
                    context, source_request)
                baseline_question_raw = h.raw_result(
                    context, question_request)
                raw_items = {
                    item["slot_id"]: item
                    for item in baseline_question_raw["raw"]["items"]}
                self.assertIn(SLOT_ANALYZE, raw_items)
                analyze_raw = raw_items[SLOT_ANALYZE]
                self.assertEqual(
                    analyze_raw["required_order_relationships"],
                    expected_pair)
                self.assertEqual(
                    raw_items[lifecycle.SLOT_REMEMBER].get(
                        "required_order_relationships", []), [])
                self.phase("setup_blocked_analyze_raw_pair_verified")

                card = h.question_card(page, analyze)
                expect(card).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-edit")).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                approve = self.tid(card, "question-approve")
                self.assertEqual(approve.count(), 1)
                self.assertTrue(
                    approve.is_disabled(),
                    "blocked Analyze approval stayed enabled")
                # Current-card order links stay exactly their own.
                current_sources = self.tid(
                    card, "question-order-source")
                expect(current_sources).to_have_count(
                    2, timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    [link.get_attribute("data-region-id")
                     for link in current_sources.all()],
                    [river["region_id"], trees["region_id"]])
                self.assertEqual(
                    page.locator(
                        "[data-testid*='precedence'], "
                        "[data-testid*='order-save'], "
                        "[data-testid*='order-commit']").count(), 0)

                self.u.open_disclosure(card, "question-original")
                relationships = self.tid(
                    card, "question-original-order-relationship")
                # FIRST repair assertion: the original disclosure
                # carries the retained pair. Omitted entirely by
                # the predecessor painter.
                expect(relationships).to_have_count(
                    1, timeout=ASSERT_TIMEOUT_MS)
                self.phase(
                    "first_repair_assertion_original_order_present")
                relationship_text = (
                    relationships.first.text_content() or "")
                self.obs["notes"]["original_order_text"] = (
                    relationship_text)
                self.assertIn(river["region_id"], relationship_text)
                self.assertIn(trees["region_id"], relationship_text)
                self.assertLess(
                    relationship_text.index(river["region_id"]),
                    relationship_text.index(trees["region_id"]),
                    relationship_text)
                original_sources = self.tid(
                    relationships, "question-original-order-source")
                expect(original_sources).to_have_count(
                    2, timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    [link.get_attribute("data-region-id")
                     for link in original_sources.all()],
                    [river["region_id"], trees["region_id"]])
                # Opening the original never inflates the current
                # card's own order-source selector.
                expect(self.tid(card, "question-order-source")
                       ).to_have_count(2, timeout=ASSERT_TIMEOUT_MS)

                # Each original endpoint navigates read-only to
                # that region's current whole-page source text.
                source_before = h.request_count(
                    "POST", lifecycle.SOURCE_PATH)
                questions_before = h.request_count(
                    "POST", lifecycle.QUESTIONS_PATH)
                checkpoint = len(h.requests_log)
                for index, region in enumerate((river, trees)):
                    link = original_sources.nth(index)
                    expect(link).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(link).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                    link.click(timeout=ASSERT_TIMEOUT_MS)
                    expect(page.locator("#source-heading")
                           ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
                    h.assert_source_visible(page, source_id)
                    selected = self.tid(page, "source-selected-text")
                    self.assertEqual(selected.count(), 1)
                    self.assertEqual(
                        selected.text_content(),
                        region["current_text"])
                    self.assertEqual(
                        h.mutations_since(checkpoint), [])
                self.assertEqual(self.snapshot(context), snapshot)
                self.assertEqual(
                    h.request_count("POST", lifecycle.SOURCE_PATH),
                    source_before)
                self.assertEqual(
                    h.request_count("POST", lifecycle.QUESTIONS_PATH),
                    questions_before)
                self.phase("original_endpoints_navigated_read_only")

                # An ordinary supported approval reaches preview;
                # its empty original order fabricates no rows, and
                # the blocked Analyze never enters preview.
                approved = self.u.decide(
                    page, context, remember, "approve")
                self.assertEqual(approved["status"], "approved")
                final = self.snapshot(context)
                self.assertEqual(
                    [item["question_id"]
                     for item in final["preview_items"]],
                    [approved["question_id"]])
                final_analyze = self.u.question_by_id(
                    final, analyze["question_id"])
                self.assertEqual(final_analyze["status"], "draft")
                self.assertIn(
                    "required_order_missing", final_analyze["blocks"])
                self.assertEqual(
                    final_analyze["required_order_relationships"],
                    expected_pair)
                item = h.preview_item(page, approved["question_id"])
                expect(item).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                self.u.open_disclosure(item, "preview-original")
                self.assertEqual(
                    self.tid(
                        item, "preview-original-order-relationship"
                    ).count(), 0)
                self.assertEqual(
                    self.tid(
                        page, "preview-original-order-relationship"
                    ).count(), 0)
                self.assertEqual(
                    h.raw_result(context, source_request),
                    baseline_source_raw)
                self.assertEqual(
                    h.raw_result(context, question_request),
                    baseline_question_raw)
                h.assert_counts(1, 1, 1, 1)
                h.assert_clean()
                self.phase("ordinary_preview_empty_original_order")
            finally:
                self.write_artifacts(page)

