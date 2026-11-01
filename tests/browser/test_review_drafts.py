"""Real-browser draft-preservation cases for the review UI.

The test harness serves
``teacher_demo.http_app.create_app`` on a random 127.0.0.1 origin
with the original ``ControlsProvider`` injected server-side; this
module never imports that provider, the app or any backend module
in the browser interpreter. Fixture constants come only from
``test_review_journey.load_fixture()`` in the adjacent unchanged
journey module; its TestCase is not imported or inherited and its
long journey is not duplicated. Each case below owns its full
setup through real DOM controls and derives directly from
``unittest.TestCase``.

Environment and safety match the journey tracer: required
``BROWSER_BASE_URL``/``BROWSER_EXECUTABLE``, optional
``BROWSER_ARTIFACT_DIR``, headless Chromium with sandbox enabled,
a fresh context per case with service workers blocked, bounded
waits and ``finally`` cleanup. Requests outside the supplied
origin are aborted and fail the case. Teacher mutations come
only from real DOM controls; shared-cookie context requests are
GET-only snapshot inspection. No route is fulfilled or mocked.

What is pinned here is implemented behavior; these are
preservation cases:
a dirty region B draft survives an acknowledged region A commit,
region Cancel resets only its own last-acknowledged state with
no request, a dirty question B editor survives sibling A
approval while the preview keeps acknowledged wording, and
question Cancel resets only its own editor. Before generation
both canonical ``EDITED_A``/``EDITED_B`` texts are committed,
because the original provider deliberately rejects other text.
"""
import contextlib
import json
import os
import pathlib
import unittest
from urllib.parse import quote, urlsplit

from playwright.sync_api import expect, sync_playwright

import test_review_journey as journey

NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000

# Teacher draft wording authored by these cases (never committed
# where a case says a value is a local draft).
REGION_A_DRAFT_TEXT = "  نص مسودة المعلم للمقطع الأول  "
REGION_B_DRAFT_TEXT = "  نص مسودة المعلم للمقطع الثاني  "
REGION_COMMITTED_LABEL = "تصنيف المعلم المعتمد"
REGION_B_DRAFT_LABEL = "تصنيف مسودة المعلم"
QUESTION_A_DRAFT_WORDING = "  صياغة مسودة المعلم للسؤال الأول؟  "


class ReviewDraftPreservationBrowserTests(unittest.TestCase):
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
        if parts.scheme in ("data", "blob"):
            route.continue_()
        elif "{0.scheme}://{0.netloc}".format(parts) != self.base_origin:
            self.external_urls.append(request.url)
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def capture_request(self, request):
        parts = urlsplit(request.url)
        entry = {"method": request.method, "path": parts.path,
                 "query": parts.query}
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

    def mutations_since(self, index):
        return [(entry["method"], entry["path"])
                for entry in self.requests_log[index:]
                if entry["method"] in ("POST", "PUT", "PATCH")]

    def capture_artifacts(self, page):
        artifact_dir = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        if not artifact_dir or page is None:
            return
        with contextlib.suppress(Exception):
            directory = pathlib.Path(artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(directory / (self._testMethodName + "-final.png")),
                full_page=True, timeout=ASSERT_TIMEOUT_MS)
            observations = {
                "external_urls": self.external_urls,
                "page_errors": self.page_errors,
                "mutations": [
                    {"method": m, "path": p}
                    for m, p in self.mutations_since(0)],
                "responses": self.responses_log}
            (directory / (self._testMethodName + ".json")).write_text(
                json.dumps(observations, ensure_ascii=False, indent=2),
                encoding="utf-8")

    @contextlib.contextmanager
    def browser_page(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=self.executable, headless=True,
                chromium_sandbox=True)
            context = None
            page = None
            try:
                context = browser.new_context(
                    base_url=self.base_url, service_workers="block",
                    viewport={"width": 1280, "height": 900})
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
                self.capture_artifacts(page)
                if context is not None:
                    with contextlib.suppress(Exception):
                        context.close()
                with contextlib.suppress(Exception):
                    browser.close()

    # -- inspection helpers (GET only) ------------------------------
    @staticmethod
    def tid(scope, name):
        return scope.get_by_test_id(name)

    def api_json(self, context, path):
        response = context.request.get(
            self.base_url + path, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(response.status, 200, path)
        return response.json()

    def snapshot(self, context):
        return self.api_json(context, "/api/session/snapshot")

    @staticmethod
    def region_by_raw(snapshot, raw_text):
        return next(item for item in snapshot["regions"]
                    if item["raw_text"] == raw_text)

    @staticmethod
    def region_by_id(snapshot, region_id):
        return next(item for item in snapshot["regions"]
                    if item["region_id"] == region_id)

    @staticmethod
    def question_by_id(snapshot, question_id):
        return next(item for item in snapshot["questions"]
                    if item["question_id"] == question_id)

    def region_item(self, page, region):
        return page.locator(
            "[data-testid='region-item']"
            f"[data-region-id=\"{region['region_id']}\"]")

    def question_card(self, page, question):
        return page.locator(
            "[data-testid='question-card']"
            f"[data-question-id=\"{question['question_id']}\"]")

    def click_expect(self, page, method, path, status, locator):
        with page.expect_response(
                lambda item: item.request.method == method
                and urlsplit(item.url).path == path
                and item.status == status,
                timeout=ASSERT_TIMEOUT_MS):
            locator.click()

    @staticmethod
    def region_path(region):
        return ("/api/session/regions/"
                + quote(region["region_id"], safe=""))

    @staticmethod
    def question_path(question, suffix=""):
        return ("/api/session/questions/"
                + quote(question["question_id"], safe="") + suffix)

    def assert_clean_network(self):
        self.assertEqual(self.external_urls, [])
        self.assertEqual(self.page_errors, [])

    # -- real DOM setup (one per case) ------------------------------
    def start_source_analysis(self, context, page):
        """Drive the existing setup form to a real source analysis."""
        page.goto("/")
        page.get_by_label("الصف الدراسي", exact=True).fill(self.fx["GRADE"])
        page.get_by_label("الهدف التعليمي", exact=True).fill(self.fx["OBJECTIVE"])
        page.locator("input[type='file']").set_input_files({
            "name": "synthetic-page.png", "mimeType": "image/png",
            "buffer": self.png_bytes})
        # The browser page-number field is one-based.
        page.get_by_label("رقم الصفحة", exact=True).fill("1")
        with page.expect_response(
                lambda item: item.request.method == "POST"
                and urlsplit(item.url).path
                == "/api/session/source-analysis"
                and item.status == 201,
                timeout=ASSERT_TIMEOUT_MS):
            page.get_by_role(
                "button", name="بدء تحليل الصفحة", exact=True).click()
        snapshot = self.snapshot(context)
        self.assertEqual(len(snapshot["regions"]), 2)
        expect(page.get_by_test_id("region-list")).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        return snapshot

    def commit_region(self, page, region, text, label, inclusion):
        item = self.region_item(page, region)
        self.tid(item, "region-current-text-input").fill(text)
        self.tid(item, "region-current-label-input").fill(label)
        self.tid(item, "region-inclusion-input").select_option(inclusion)
        self.click_expect(
            page, "PUT", self.region_path(region), 200,
            item.get_by_role(
                "button", name="حفظ مراجعة المقطع", exact=True))

    def generate_questions(self, context, page):
        button = page.get_by_role(
            "button", name="اعتماد المصدر وتوليد الأسئلة", exact=True)
        expect(button).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
        button.click()
        expect(page.get_by_test_id("question-card")).to_have_count(
            2, timeout=ASSERT_TIMEOUT_MS)
        snapshot = self.snapshot(context)
        self.assertEqual(len(snapshot["questions"]), 2)
        return snapshot

    def setup_generated(self, context, page):
        """Source analysis + both canonical commits + generation."""
        snapshot = self.start_source_analysis(context, page)
        region_a = self.region_by_raw(snapshot, self.fx["RAW_A"])
        region_b = self.region_by_raw(snapshot, self.fx["RAW_B"])
        self.commit_region(
            page, region_a, self.fx["EDITED_A"],
            region_a["current_label"], "included")
        self.commit_region(
            page, region_b, self.fx["EDITED_B"],
            region_b["current_label"], "included")
        return self.generate_questions(context, page)

    def open_question_editor(self, card):
        wording = self.tid(card, "question-edit-question")
        if wording.count() == 0 or not wording.is_visible():
            card.get_by_role(
                "button", name="تحرير السؤال", exact=True).click()
        expect(wording).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        return wording

    def approve_question(self, page, question):
        card = self.question_card(page, question)
        self.click_expect(
            page, "POST", self.question_path(question, "/approve"), 200,
            card.get_by_role(
                "button", name="اعتماد السؤال", exact=True))
        expect(self.tid(card, "question-status")).to_have_text(
            journey.STATUS_ARABIC["approved"],
            timeout=ASSERT_TIMEOUT_MS)

    @staticmethod
    def same_node(first_handle, locator):
        return first_handle.evaluate(
            "(node, other) => node === other",
            locator.element_handle())

    # -- region draft preservation ----------------------------------
    def test_dirty_region_b_survives_acknowledged_region_a_commit(self):
        with self.browser_page() as (context, page):
            snapshot = self.start_source_analysis(context, page)
            region_a = self.region_by_raw(snapshot, self.fx["RAW_A"])
            region_b = self.region_by_raw(snapshot, self.fx["RAW_B"])
            item_b = self.region_item(page, region_b)
            expect(item_b).to_be_visible(timeout=ASSERT_TIMEOUT_MS)

            # Open B's read-only originals disclosure and dirty all
            # three B draft fields without committing them.
            details_b = item_b.locator("details").first
            details_b.locator("summary").click()
            self.assertTrue(details_b.evaluate("node => node.open"))
            self.tid(item_b, "region-current-text-input").fill(
                REGION_B_DRAFT_TEXT)
            self.tid(item_b, "region-current-label-input").fill(
                REGION_B_DRAFT_LABEL)
            self.tid(item_b, "region-inclusion-input").select_option(
                "excluded")
            handle_b = item_b.element_handle()

            # Acknowledge region A only, with its canonical text.
            self.commit_region(
                page, region_a, self.fx["EDITED_A"],
                region_a["current_label"], "included")
            acknowledged = self.snapshot(context)
            ack_a = self.region_by_id(acknowledged, region_a["region_id"])
            ack_b = self.region_by_id(acknowledged, region_b["region_id"])
            self.assertEqual(ack_a["current_text"], self.fx["EDITED_A"])
            self.assertEqual(ack_a["inclusion"], "included")
            self.assertEqual(ack_b["current_text"], region_b["current_text"])
            self.assertEqual(ack_b["current_label"],
                             region_b["current_label"])
            self.assertEqual(ack_b["inclusion"], region_b["inclusion"])

            # B keeps its keyed node, its open disclosure and every
            # uncommitted draft value.
            self.assertTrue(
                self.same_node(handle_b, self.region_item(page, ack_b)))
            self.assertTrue(
                item_b.locator("details").first.evaluate(
                    "node => node.open"))
            expect(
                self.tid(item_b, "region-current-text-input")
            ).to_have_value(
                REGION_B_DRAFT_TEXT, timeout=ASSERT_TIMEOUT_MS)
            expect(
                self.tid(item_b, "region-current-label-input")
            ).to_have_value(
                REGION_B_DRAFT_LABEL, timeout=ASSERT_TIMEOUT_MS)
            expect(
                self.tid(item_b, "region-inclusion-input")
            ).to_have_value("excluded", timeout=ASSERT_TIMEOUT_MS)
            self.assert_clean_network()

    def test_region_cancel_resets_only_its_own_last_acknowledged_state(
            self):
        with self.browser_page() as (context, page):
            snapshot = self.start_source_analysis(context, page)
            region_a = self.region_by_raw(snapshot, self.fx["RAW_A"])
            region_b = self.region_by_raw(snapshot, self.fx["RAW_B"])

            # Give B a last-acknowledged state distinct from its
            # initial one (canonical text, teacher label, included).
            self.commit_region(
                page, region_b, self.fx["EDITED_B"],
                REGION_COMMITTED_LABEL, "included")
            acknowledged = self.snapshot(context)
            ack_b = self.region_by_id(acknowledged, region_b["region_id"])
            self.assertEqual(ack_b["current_text"], self.fx["EDITED_B"])
            self.assertEqual(ack_b["current_label"], REGION_COMMITTED_LABEL)

            # Dirty A (never committed), then dirty B again.
            item_a = self.region_item(page, region_a)
            self.tid(item_a, "region-current-text-input").fill(
                REGION_A_DRAFT_TEXT)
            item_b = self.region_item(page, ack_b)
            self.tid(item_b, "region-current-text-input").fill(
                REGION_B_DRAFT_TEXT)
            self.tid(item_b, "region-current-label-input").fill(
                REGION_B_DRAFT_LABEL)
            self.tid(item_b, "region-inclusion-input").select_option(
                "unresolved")

            request_count = len(self.requests_log)
            item_b.get_by_role(
                "button", name="إلغاء التعديل", exact=True).click()

            # B returns to its last-acknowledged values with no
            # request at all; A's separate draft is untouched.
            expect(
                self.tid(item_b, "region-current-text-input")
            ).to_have_value(
                self.fx["EDITED_B"], timeout=ASSERT_TIMEOUT_MS)
            expect(
                self.tid(item_b, "region-current-label-input")
            ).to_have_value(
                REGION_COMMITTED_LABEL, timeout=ASSERT_TIMEOUT_MS)
            expect(
                self.tid(item_b, "region-inclusion-input")
            ).to_have_value("included", timeout=ASSERT_TIMEOUT_MS)
            expect(
                self.tid(item_a, "region-current-text-input")
            ).to_have_value(
                REGION_A_DRAFT_TEXT, timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(len(self.requests_log), request_count)
            self.assertEqual(self.mutations_since(request_count), [])
            still = self.snapshot(context)
            self.assertEqual(
                self.region_by_id(still, region_b["region_id"]), ack_b)
            self.assert_clean_network()

    # -- question draft preservation --------------------------------
    def test_dirty_question_editors_survive_sibling_approval_and_preview_stays_acknowledged(
            self):
        with self.browser_page() as (context, page):
            snapshot = self.setup_generated(context, page)
            region_a = self.region_by_raw(snapshot, self.fx["RAW_A"])
            region_b = self.region_by_raw(snapshot, self.fx["RAW_B"])
            question_a = self.question_by_id(
                snapshot, region_a["linked_question_ids"][0])
            question_b = self.question_by_id(
                snapshot, region_b["linked_question_ids"][0])
            card_a = self.question_card(page, question_a)
            card_b = self.question_card(page, question_b)

            # Dirty both editors; neither wording is committed.
            self.open_question_editor(card_a).fill(
                QUESTION_A_DRAFT_WORDING)
            self.open_question_editor(card_b).fill(
                journey.EDITED_B_WORDING)
            handle_b = card_b.element_handle()

            # Approve sibling A through its real card action. The
            # server approves A's acknowledged wording, not either
            # local draft.
            self.approve_question(page, question_a)
            acknowledged = self.snapshot(context)
            ack_a = self.question_by_id(
                acknowledged, question_a["question_id"])
            ack_b = self.question_by_id(
                acknowledged, question_b["question_id"])
            self.assertEqual(ack_a["status"], "approved")
            self.assertEqual(ack_b["status"], question_b["status"])

            # B keeps its keyed card and its open dirty editor.
            self.assertTrue(
                self.same_node(
                    handle_b, self.question_card(page, ack_b)))
            expect(
                self.tid(card_b, "question-edit-question")
            ).to_have_value(
                journey.EDITED_B_WORDING, timeout=ASSERT_TIMEOUT_MS)

            # The preview is exactly the server's preview entry for
            # A: acknowledged approved wording, no local draft text.
            preview = acknowledged["preview_items"]
            self.assertEqual(
                [entry["question_id"] for entry in preview],
                [question_a["question_id"]])
            preview_items = page.get_by_test_id("preview-item")
            expect(preview_items).to_have_count(
                1, timeout=ASSERT_TIMEOUT_MS)
            expect(preview_items.first).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(preview_items.first).to_contain_text(
                preview[0]["question"], timeout=ASSERT_TIMEOUT_MS)
            preview_text = page.get_by_test_id(
                "preview-list").text_content()
            self.assertNotIn(
                QUESTION_A_DRAFT_WORDING.strip(), preview_text)
            self.assertNotIn(
                journey.EDITED_B_WORDING.strip(), preview_text)
            self.assert_clean_network()

    def test_question_cancel_resets_only_its_own_editor_without_a_request(
            self):
        with self.browser_page() as (context, page):
            snapshot = self.setup_generated(context, page)
            region_a = self.region_by_raw(snapshot, self.fx["RAW_A"])
            region_b = self.region_by_raw(snapshot, self.fx["RAW_B"])
            question_a = self.question_by_id(
                snapshot, region_a["linked_question_ids"][0])
            question_b = self.question_by_id(
                snapshot, region_b["linked_question_ids"][0])
            card_a = self.question_card(page, question_a)
            card_b = self.question_card(page, question_b)

            # Dirty A's editor first; it must survive everything
            # below, including B's acknowledged edit.
            self.open_question_editor(card_a).fill(
                QUESTION_A_DRAFT_WORDING)

            # Acknowledge a new B wording with one real PATCH.
            self.open_question_editor(card_b).fill(
                journey.EDITED_B_WORDING)
            self.click_expect(
                page, "PATCH", self.question_path(question_b), 200,
                card_b.get_by_role(
                    "button", name="حفظ التعديل", exact=True))
            expect(
                self.tid(card_a, "question-edit-question")
            ).to_have_value(
                QUESTION_A_DRAFT_WORDING, timeout=ASSERT_TIMEOUT_MS)
            acknowledged = self.snapshot(context)
            ack_b = self.question_by_id(
                acknowledged, question_b["question_id"])

            # Dirty B a second time, then cancel only B.
            self.open_question_editor(card_b).fill(
                journey.EDITED_B_WORDING_2)
            request_count = len(self.requests_log)
            card_b.get_by_role(
                "button", name="إلغاء التعديل", exact=True).click()

            # No request was sent, B is back at its acknowledged
            # wording, and A's different dirty editor is intact.
            self.assertEqual(len(self.requests_log), request_count)
            self.assertEqual(self.mutations_since(request_count), [])
            expect(card_b.get_by_role("heading").first).to_have_text(
                journey.EDITED_B_WORDING, timeout=ASSERT_TIMEOUT_MS)
            expect(self.open_question_editor(card_b)).to_have_value(
                journey.EDITED_B_WORDING, timeout=ASSERT_TIMEOUT_MS)
            expect(
                self.tid(card_a, "question-edit-question")
            ).to_have_value(
                QUESTION_A_DRAFT_WORDING, timeout=ASSERT_TIMEOUT_MS)
            still = self.snapshot(context)
            self.assertEqual(
                self.question_by_id(still, question_b["question_id"]),
                ack_b)
            self.assertEqual(
                self.question_by_id(still, question_a["question_id"]),
                question_a)
            self.assert_clean_network()


if __name__ == "__main__":
    unittest.main()
