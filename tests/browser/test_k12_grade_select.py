"""Real-browser regression: setup grade is a native K-12 SELECT.

One setup-only behavior tracer per viewport subTest (390x844,
1280x900), each in a fresh browser context against an externally
served real loopback app (``BROWSER_BASE_URL`` /
``BROWSER_EXECUTABLE``). The synthetic ``ControlsProvider`` stays
server-side in the parent harness and is explicitly not Meta;
this module imports ``test_review_journey`` as a module only for
its ``load_fixture()`` constants/PNG (never its test case or its
``fill``-based grade setup), starts no backend/provider, fulfils
no route, injects no app state, and mutates only through real
visible DOM controls. Shared-cookie context requests are
GET-only inspection (``/api/session/snapshot``,
``/api/session/page``).

Contract under test: the setup control labelled exactly
``الصف الدراسي`` is a native ``SELECT`` whose first option is an
empty-value non-grade prompt, followed by exactly 13 selectable
nonempty choices — ``الروضة`` then ``الصف الأول`` through
``الصف الثاني عشر`` — each option's value and visible text being
that exact Arabic string. No grade is auto-selected. The first
new-behavior assertion is the ``SELECT`` tag check (the current
baseline renders an ``INPUT``, so each subTest stops there as
the expected RED and ``select_option`` is never reached).

After that, every choice is exercised with ``select_option`` /
``input_value``, an unknown custom string typed at the control
must not become a selectable grade, and normal keyboard
selection/focus is checked. Empty-grade behavior follows the
existing client contract in ``workspace.js`` (form is
``novalidate``; ``preparePage`` rejects a blank grade with the
visible ``setup-error`` text ``أدخل الصف الدراسي.``, focuses the
grade control, and sends nothing) — no HTML ``required`` or
native-validity behavior is assumed. The tracer then selects
الصف الخامس, keeps the fixture objective exact, and uses the
real Prepare control (real settings PUT then real Start) to
prove the exact selected Arabic value survives the settings
wire body, GET snapshot/page readback, and in-page navigation
away/back — with exactly one Start and never any
source-analysis or question-generation send (the confirmation
control is never clicked, so no provider/model call happens).

Out of scope: the question editor's grade field
(``question-edit-grade`` in ``review-render.js``) is not tested
here; the parent adds a focused regression for it separately.
"""
import contextlib
import json
import os
import pathlib
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

import test_review_journey as journey

NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000
VIEWPORTS = ({"width": 390, "height": 844},
             {"width": 1280, "height": 900})
SETTINGS = "/api/session/settings"
START = "/api/session/start"
ANALYSIS = "/api/session/source-analysis"
GENERATE = "/api/session/questions/generate"
SNAPSHOT = "/api/session/snapshot"
PAGE = "/api/session/page"

GRADE_LABEL = "الصف الدراسي"
GRADE_CHOICES = (
    "الروضة",
    "الصف الأول",
    "الصف الثاني",
    "الصف الثالث",
    "الصف الرابع",
    "الصف الخامس",
    "الصف السادس",
    "الصف السابع",
    "الصف الثامن",
    "الصف التاسع",
    "الصف العاشر",
    "الصف الحادي عشر",
    "الصف الثاني عشر",
)
# The exact grade the parent harness's synthetic source fixture
# expects; the tracer never sends source analysis, so this value
# is proven through settings/Start readback only.
EXPECTED_GRADE = "الصف الخامس"
CUSTOM_GRADE = "صف مخصص غير معتمد ٩٩"
EMPTY_GRADE_ERROR = "أدخل الصف الدراسي."


class K12GradeSelectBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx, cls.png_bytes = journey.load_fixture()

    def setUp(self):
        self.base_url = os.environ.get(
            "BROWSER_BASE_URL", "").rstrip("/")
        self.executable = os.environ.get("BROWSER_EXECUTABLE", "")
        self.assertTrue(
            self.base_url,
            "BROWSER_BASE_URL (actual served origin) is required")
        self.assertTrue(
            self.executable,
            "BROWSER_EXECUTABLE (installed browser path) is required")
        self.assertTrue(
            pathlib.Path(self.executable).is_file(),
            "BROWSER_EXECUTABLE must name the installed browser file")
        self.base_origin = "{0.scheme}://{0.netloc}".format(
            urlsplit(self.base_url))
        self.requests_log = []
        self.responses_log = []
        self.external_urls = []
        self.page_errors = []

    # -- recording / guarding --------------------------------------
    def handle_route(self, route, request):
        parts = urlsplit(request.url)
        if parts.scheme in ("http", "https") and (
                "{0.scheme}://{0.netloc}".format(parts)
                != self.base_origin):
            self.external_urls.append(request.url)
            route.abort("blockedbyclient")
        else:  # same-origin, data: and blob: are left alone
            route.continue_()

    def capture_request(self, request):
        entry = {"method": request.method,
                 "path": urlsplit(request.url).path}
        with contextlib.suppress(Exception):
            if request.post_data:
                try:
                    entry["json"] = json.loads(request.post_data)
                except ValueError:
                    pass
        self.requests_log.append(entry)

    def capture_response(self, response):
        self.responses_log.append({
            "method": response.request.method,
            "path": urlsplit(response.url).path,
            "status": response.status})

    def count_requests(self, method, path):
        return sum(1 for entry in self.requests_log
                   if entry["method"] == method
                   and entry["path"] == path)

    def count_responses(self, method, path, status):
        return sum(1 for entry in self.responses_log
                   if entry["method"] == method
                   and entry["path"] == path
                   and entry["status"] == status)

    def mutations_since(self, index):
        return [(entry["method"], entry["path"])
                for entry in self.requests_log[index:]
                if entry["method"] in ("POST", "PUT", "PATCH")]

    def last_body(self, method, path):
        matches = [entry.get("json") for entry in self.requests_log
                   if entry["method"] == method
                   and entry["path"] == path]
        self.assertTrue(matches, (method, path))
        return matches[-1]

    @contextlib.contextmanager
    def browser_page(self, viewport):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=self.executable, headless=True,
                chromium_sandbox=True)
            context = None
            try:
                context = browser.new_context(
                    base_url=self.base_url, service_workers="block",
                    viewport=viewport)
                context.set_default_timeout(ASSERT_TIMEOUT_MS)
                context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
                context.route("**/*", self.handle_route)
                page = context.new_page()
                page.on("request", self.capture_request)
                page.on("response", self.capture_response)
                page.on(
                    "pageerror",
                    lambda error: self.page_errors.append(str(error)))
                yield context, page
            finally:
                if context is not None:
                    with contextlib.suppress(Exception):
                        context.close()
                with contextlib.suppress(Exception):
                    browser.close()

    # -- small DOM / GET-only helpers ------------------------------
    @staticmethod
    def tid(scope, name):
        return scope.get_by_test_id(name)

    @staticmethod
    def studio(page):
        return page.locator(".studio-app")

    @staticmethod
    def options_of(locator):
        return locator.evaluate(
            "el => Array.from(el.options).map(o => "
            "({value: o.value, text: o.textContent, "
            "disabled: o.disabled}))")

    @staticmethod
    def value_of(locator):
        return locator.evaluate("el => el.value")

    def api_get_json(self, context, path):
        response = context.request.get(
            self.base_url + path, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(response.status, 200, path)
        return response.json()

    def click_expect(self, page, method, path, status, locator):
        with page.expect_response(
                lambda item: item.request.method == method
                and urlsplit(item.url).path == path
                and item.status == status,
                timeout=ASSERT_TIMEOUT_MS):
            locator.click()

    def assert_phase(self, page, phase):
        expect(self.studio(page)).to_have_attribute(
            "data-setup-phase", phase, timeout=ASSERT_TIMEOUT_MS)

    # -- the one tracer --------------------------------------------
    def run_tracer(self, context, page):
        fx = self.fx
        self.requests_log, self.responses_log = [], []
        self.external_urls, self.page_errors = [], []
        payload = {"name": "source-page.png",
                   "mimeType": "image/png", "buffer": self.png_bytes}

        # Baseline-passing setup first: choose -> configure via
        # the real file picker, exactly as the studio tracer does.
        response = page.goto(
            "/", wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS)
        expect(self.studio(page)).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        self.assertIsNotNone(response)
        self.assertEqual(response.status, 200, page.url)
        expect(self.studio(page)).to_have_attribute(
            "data-studio-stage", "setup", timeout=ASSERT_TIMEOUT_MS)
        self.assert_phase(page, "choose")
        file_input = page.locator("input#page-file")
        expect(file_input).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        grade = page.get_by_label(GRADE_LABEL, exact=True)
        objective = page.get_by_label("الهدف التعليمي", exact=True)
        page_number = page.get_by_label("رقم الصفحة", exact=True)
        expect(grade).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
        file_input.set_input_files(payload)
        self.assert_phase(page, "configure")
        expect(grade).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(objective).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(page_number).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
        self.assertEqual(self.count_requests("PUT", SETTINGS), 0)
        self.assertEqual(self.count_requests("POST", START), 0)
        self.assertEqual(self.count_requests("POST", ANALYSIS), 0)
        self.assertEqual(self.count_requests("POST", GENERATE), 0)

        # FIRST NEW-BEHAVIOR ASSERTION (expected baseline RED:
        # the current control is an INPUT). Nothing below runs in
        # this subTest until the control really is a SELECT, and
        # select_option is never used before this check.
        self.assertEqual(
            grade.evaluate("el => el.tagName"), "SELECT")

        # Associated Arabic label on the one real control.
        expect(grade).to_have_count(1)
        self.assertEqual(grade.get_attribute("id"), "grade")
        expect(page.locator("label[for='grade']")).to_have_text(
            GRADE_LABEL, timeout=ASSERT_TIMEOUT_MS)

        # Exactly one empty prompt plus 13 selectable grades,
        # values and visible texts exact, in order.
        options = self.options_of(grade)
        self.assertEqual(len(options), 1 + len(GRADE_CHOICES), options)
        self.assertEqual(options[0]["value"], "")
        self.assertNotIn(options[0]["text"].strip(), GRADE_CHOICES)
        self.assertEqual(
            [item["value"] for item in options[1:]],
            list(GRADE_CHOICES))
        self.assertEqual(
            [item["text"].strip() for item in options[1:]],
            list(GRADE_CHOICES))
        for item in options[1:]:
            self.assertTrue(item["value"], item)
            self.assertFalse(item["disabled"], item)

        # No auto-selected grade on arrival.
        self.assertEqual(
            grade.input_value(timeout=ASSERT_TIMEOUT_MS), "")
        self.assertEqual(
            grade.evaluate("el => el.selectedIndex"), 0)

        # Existing empty-grade contract, through real Prepare:
        # visible setup error, focus returns to the grade
        # control, and no settings/Start/analysis is sent.
        objective.fill(fx["OBJECTIVE"])
        page_number.fill("1")
        expect(objective).to_have_value(
            fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
        prepare = self.tid(page, "prepare-page")
        expect(prepare).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(prepare).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
        mark = len(self.requests_log)
        prepare.click()
        setup_error = self.tid(page, "setup-error")
        expect(setup_error).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(setup_error).to_have_text(
            EMPTY_GRADE_ERROR, timeout=ASSERT_TIMEOUT_MS)
        expect(grade).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            grade.input_value(timeout=ASSERT_TIMEOUT_MS), "")
        self.assertEqual(self.mutations_since(mark), [])
        self.assertEqual(self.count_requests("PUT", SETTINGS), 0)
        self.assertEqual(self.count_requests("POST", START), 0)
        self.assertEqual(self.count_requests("POST", ANALYSIS), 0)
        self.assertEqual(self.count_requests("POST", GENERATE), 0)

        # Every one of the 13 choices, via the real control.
        for choice in GRADE_CHOICES:
            grade.select_option(
                value=choice, timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(
                grade.input_value(timeout=ASSERT_TIMEOUT_MS), choice)

        # An unknown custom string never becomes a selectable
        # grade: typing it at the focused native control adds no
        # option and leaves only a known value (or the prompt).
        self.assertNotIn(
            CUSTOM_GRADE, [item["value"] for item in options])
        grade.focus(timeout=ASSERT_TIMEOUT_MS)
        expect(grade).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
        page.keyboard.type(CUSTOM_GRADE, delay=20)
        options_after = self.options_of(grade)
        self.assertEqual(options_after, options)
        self.assertNotIn(
            CUSTOM_GRADE,
            [item["value"] for item in options_after])
        self.assertNotIn(
            CUSTOM_GRADE,
            [item["text"].strip() for item in options_after])
        self.assertIn(
            grade.input_value(timeout=ASSERT_TIMEOUT_MS),
            ("",) + GRADE_CHOICES)
        # Typing at a native SELECT can leave Chromium's popup
        # open with only a highlighted (uncommitted) choice.
        # Dismiss that native state before the independent
        # keyboard test below.
        page.keyboard.press("Escape")
        expect(grade).to_be_focused(timeout=ASSERT_TIMEOUT_MS)

        # Normal keyboard selection with focus retained.
        # Deliberately open the native popup, move the
        # highlight, then commit with a single Enter while it
        # is open: in popup state arrow keys move only the
        # highlight and el.value commits at Enter. Enter is
        # never pressed on the closed select (no form submit
        # is expected or relied on).
        grade.select_option(
            value="الصف الأول", timeout=ASSERT_TIMEOUT_MS)
        grade.focus(timeout=ASSERT_TIMEOUT_MS)
        expect(grade).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
        page.keyboard.press("Alt+ArrowDown")
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        expect(grade).to_have_value(
            "الصف الثاني", timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            grade.input_value(timeout=ASSERT_TIMEOUT_MS),
            "الصف الثاني")
        expect(grade).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
        page.keyboard.press("ArrowUp")
        expect(grade).to_have_value(
            "الصف الأول", timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            grade.input_value(timeout=ASSERT_TIMEOUT_MS),
            "الصف الأول")
        expect(grade).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(self.count_requests("PUT", SETTINGS), 0)
        self.assertEqual(self.count_requests("POST", START), 0)

        # Real settings + Start through Prepare, exact value in,
        # exact value back. The confirmation control is never
        # clicked, so no provider/model call can follow.
        self.assertIn(EXPECTED_GRADE, GRADE_CHOICES)
        grade.select_option(
            value=EXPECTED_GRADE, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            grade.input_value(timeout=ASSERT_TIMEOUT_MS),
            EXPECTED_GRADE)
        expect(objective).to_have_value(
            fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
        expect(page_number).to_have_value(
            "1", timeout=ASSERT_TIMEOUT_MS)
        self.click_expect(page, "POST", START, 201, prepare)
        self.assert_phase(page, "confirm")
        self.assertEqual(self.count_requests("PUT", SETTINGS), 1)
        self.assertEqual(
            self.count_responses("PUT", SETTINGS, 200), 1)
        self.assertEqual(
            self.last_body("PUT", SETTINGS),
            {"grade": EXPECTED_GRADE, "objective": fx["OBJECTIVE"],
             "rights_ref": ""})
        self.assertEqual(self.count_requests("POST", START), 1)
        self.assertEqual(self.count_responses("POST", START, 201), 1)
        self.assertEqual(self.count_requests("POST", ANALYSIS), 0)
        self.assertEqual(self.count_requests("POST", GENERATE), 0)

        # GET-only readback: snapshot and page metadata agree on
        # the exact grade/objective the teacher actually chose.
        snapshot = self.api_get_json(context, SNAPSHOT)
        self.assertEqual(snapshot["grade"], EXPECTED_GRADE)
        self.assertEqual(snapshot["objective"], fx["OBJECTIVE"])
        self.assertEqual(snapshot["questions"], [])
        page_metadata = self.api_get_json(context, PAGE)
        self.assertEqual(page_metadata["grade"], EXPECTED_GRADE)
        self.assertEqual(page_metadata["objective"], fx["OBJECTIVE"])
        self.assertEqual(
            page_metadata["source_id"], snapshot["source_id"])

        # Navigation away/back inside setup sends nothing and
        # never silently changes the chosen grade or objective:
        # confirm -> configure (edit) -> choose (back) ->
        # configure (settings entry), then pane tabs if shown.
        mark = len(self.requests_log)
        edit = page.locator("[data-studio='setup-edit']")
        expect(edit).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        edit.click()
        self.assert_phase(page, "configure")
        expect(grade).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            grade.input_value(timeout=ASSERT_TIMEOUT_MS),
            EXPECTED_GRADE)
        expect(objective).to_have_value(
            fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)

        back = page.locator("[data-studio='setup-back']")
        expect(back).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        back.click()
        self.assert_phase(page, "choose")
        self.assertEqual(self.value_of(grade), EXPECTED_GRADE)
        self.assertEqual(self.value_of(objective), fx["OBJECTIVE"])

        settings_entry = page.locator("[data-studio='setup-settings']")
        expect(settings_entry).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        settings_entry.click()
        self.assert_phase(page, "configure")
        expect(grade).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            grade.input_value(timeout=ASSERT_TIMEOUT_MS),
            EXPECTED_GRADE)
        expect(objective).to_have_value(
            fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)

        source_tab = self.tid(page, "pane-tab-source")
        work_tab = self.tid(page, "pane-tab-work")
        if source_tab.count() > 0 and source_tab.is_visible():
            source_tab.click()
            expect(self.tid(page, "source-preview")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            if work_tab.count() > 0 and work_tab.is_visible():
                work_tab.click()
            expect(grade).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(
                grade.input_value(timeout=ASSERT_TIMEOUT_MS),
                EXPECTED_GRADE)

        self.assertEqual(self.mutations_since(mark), [])
        snapshot = self.api_get_json(context, SNAPSHOT)
        self.assertEqual(snapshot["grade"], EXPECTED_GRADE)
        self.assertEqual(snapshot["objective"], fx["OBJECTIVE"])

        # Clean ending: one Start at most (exactly one here),
        # one settings PUT, and no model-bound send ever.
        widths = page.evaluate(
            "() => [document.documentElement.scrollWidth, "
            "document.documentElement.clientWidth]")
        self.assertLessEqual(widths[0], widths[1])
        self.assertEqual(self.external_urls, [])
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.count_requests("PUT", SETTINGS), 1)
        self.assertEqual(
            self.count_responses("PUT", SETTINGS, 200), 1)
        self.assertEqual(self.count_requests("POST", START), 1)
        self.assertEqual(self.count_responses("POST", START, 201), 1)
        self.assertEqual(self.count_requests("POST", ANALYSIS), 0)
        self.assertEqual(self.count_requests("POST", GENERATE), 0)

    def test_setup_grade_is_native_k12_select(self):
        for viewport in VIEWPORTS:
            label = f"{viewport['width']}x{viewport['height']}"
            with self.subTest(viewport=label):
                with self.browser_page(viewport) as (context, page):
                    self.run_tracer(context, page)


if __name__ == "__main__":
    unittest.main()
