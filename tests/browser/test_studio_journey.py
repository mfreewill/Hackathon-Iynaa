"""Real-browser tracer for the new Lesson Studio presentation shell.

One coherent journey per viewport subTest (390x844, 1280x900)
against an externally served real loopback app. The stored
synthetic ``ControlsProvider`` stays server-side; this module
imports ``test_review_journey`` as a module only for
``load_fixture()`` constants/PNG, starts no backend/provider,
fulfils no route and mutates only through real DOM controls.
Shared-cookie context requests are GET-only inspection
(``/api/session/page`` JSON metadata, the metadata-provided
preview path, ``/api/session/snapshot``). Exact
statuses are pinned on every waited response, so an unknown
outcome is never read as success.

Presentation contract under test: ``.studio-app`` carries
``data-studio-stage=setup|source|questions|preview`` and
``data-setup-phase=choose|configure|confirm``; the existing
``workflow-nav-*`` controls and ``#work-pane[data-active-step]``
remain the navigation authority. Setup never auto-analyses,
question cards are retained with exactly one ``hidden=false``,
selection happens only through ``[data-studio='question-nav']``
buttons (never a forced click on an invisible card), and each
context sends exactly one start, one source-analysis and one
question-generation request. On the pre-studio baseline the
first assertion (``.studio-app`` visible) fails in each
context and nothing later in that context executes.
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
VIEWPORTS = ({"width": 390, "height": 844},
             {"width": 1280, "height": 900})
START = "/api/session/start"
ANALYSIS = "/api/session/source-analysis"
GENERATE = "/api/session/questions/generate"
DRAFT_WORDING = "  صياغة مسودة استديو مؤقتة؟  "


class StudioJourneyBrowserTests(unittest.TestCase):
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

    # -- small DOM helpers -----------------------------------------
    @staticmethod
    def tid(scope, name):
        return scope.get_by_test_id(name)

    @staticmethod
    def studio(page):
        return page.locator(".studio-app")

    @staticmethod
    def work_pane(page):
        return page.locator("#work-pane, [data-testid='work-pane']")

    def snapshot(self, context):
        response = context.request.get(
            self.base_url + "/api/session/snapshot",
            timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            response.status, 200, "/api/session/snapshot")
        return response.json()

    def assert_stage(self, page, stage):
        expect(self.studio(page)).to_have_attribute(
            "data-studio-stage", stage, timeout=ASSERT_TIMEOUT_MS)
        expect(self.work_pane(page)).to_have_attribute(
            "data-active-step", stage, timeout=ASSERT_TIMEOUT_MS)

    def click_expect(self, page, method, path, status, locator):
        with page.expect_response(
                lambda item: item.request.method == method
                and urlsplit(item.url).path == path
                and item.status == status,
                timeout=ASSERT_TIMEOUT_MS):
            locator.click()

    @staticmethod
    def region_path(region_id):
        return "/api/session/regions/" + quote(region_id, safe="")

    @staticmethod
    def question_path(question_id, suffix=""):
        return ("/api/session/questions/"
                + quote(question_id, safe="") + suffix)

    @staticmethod
    def region_item(page, region_id):
        return page.locator(
            "[data-testid='region-item']"
            f"[data-region-id=\"{region_id}\"]")

    @staticmethod
    def question_card(page, question_id):
        return page.locator(
            "[data-testid='question-card']"
            f"[data-question-id=\"{question_id}\"]")

    @staticmethod
    def value_of(locator):
        return locator.evaluate("el => el.value")

    @staticmethod
    def hidden_of(card):
        return card.evaluate("el => el.hidden")

    @staticmethod
    def same_node(handle, locator):
        return handle.evaluate(
            "(node, other) => node === other",
            locator.element_handle())

    def ensure_work_visible(self, page):
        region_list = self.tid(page, "region-list")
        if region_list.count() == 0 or not region_list.is_visible():
            tab = self.tid(page, "pane-tab-work")
            if tab.count() > 0 and tab.is_visible():
                tab.click()
        expect(region_list).to_be_visible(timeout=ASSERT_TIMEOUT_MS)

    def source_image_src(self, page, allow_tab=True):
        preview = self.tid(page, "source-preview")
        switched = False
        if allow_tab and (
                preview.count() == 0 or not preview.is_visible()):
            tab = self.tid(page, "pane-tab-source")
            if tab.count() > 0 and tab.is_visible():
                tab.click()
                switched = True
        expect(preview).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        img = page.locator(
            "img[data-testid='source-preview'], "
            "[data-testid='source-preview'] img").first
        expect(img).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(img).to_have_js_property(
            "complete", True, timeout=ASSERT_TIMEOUT_MS)
        self.assertGreater(
            img.evaluate("el => el.naturalWidth"), 0)
        src = img.evaluate("el => el.src")
        self.assertTrue(
            src.startswith(self.base_origin + "/"), src)
        if switched:
            tab = self.tid(page, "pane-tab-work")
            if tab.count() > 0 and tab.is_visible():
                tab.click()
        return src

    def open_region_editor(self, item):
        text_input = self.tid(item, "region-current-text-input")
        if text_input.count() == 0 or not text_input.is_visible():
            self.tid(item, "region-edit").click()
        expect(text_input).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        return text_input

    def commit_region(self, page, region, text):
        item = self.region_item(page, region["region_id"])
        expect(item).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.open_region_editor(item).fill(text)
        self.tid(item, "region-current-label-input").fill(
            region["current_label"])
        self.tid(item, "region-inclusion-input").select_option(
            "included")
        path = self.region_path(region["region_id"])
        self.click_expect(
            page, "PUT", path, 200,
            self.tid(item, "region-review-commit"))
        self.assertEqual(
            self.last_body("PUT", path),
            {"inclusion": "included", "current_text": text,
             "current_label": region["current_label"]})

    def open_question_editor(self, card):
        wording = self.tid(card, "question-edit-question")
        if wording.count() == 0 or not wording.is_visible():
            self.tid(card, "question-edit").click()
        expect(wording).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        return wording

    def select_question(self, page, question_id, other_id):
        nav = page.locator("[data-studio='question-nav']")
        nav.locator(
            f"button[data-question-id=\"{question_id}\"]").click()
        expect(self.question_card(page, question_id)).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        self.assertFalse(
            self.hidden_of(self.question_card(page, question_id)))
        self.assertTrue(
            self.hidden_of(self.question_card(page, other_id)))

    @staticmethod
    def stage_shot(page, label, stage):
        artifact_dir = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        if not artifact_dir:
            return
        with contextlib.suppress(Exception):
            page.evaluate(
                "async () => { const finite = "
                "document.getAnimations().filter(a => { try { "
                "return !!a.effect && a.effect.getTiming()"
                ".iterations !== Infinity; } catch { "
                "return false; } }); "
                "await Promise.allSettled("
                "finite.map(a => a.finished)); }")
            directory = pathlib.Path(artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(directory / f"studio-{label}-{stage}.png"),
                timeout=ASSERT_TIMEOUT_MS)

    # -- the one journey -------------------------------------------
    def run_journey(self, context, page, label):
        fx = self.fx
        self.requests_log, self.responses_log = [], []
        self.external_urls, self.page_errors = [], []
        payload = {"name": "source-page.png",
                   "mimeType": "image/png", "buffer": self.png_bytes}

        # 1. Setup: choose -> configure, with no server send yet.
        response = page.goto(
            "/", wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS)
        expect(self.studio(page)).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        self.assertIsNotNone(response)
        self.assertEqual(response.status, 200, page.url)
        expect(self.studio(page)).to_have_count(1)
        expect(self.studio(page)).to_have_attribute(
            "data-studio-stage", "setup", timeout=ASSERT_TIMEOUT_MS)
        expect(self.studio(page)).to_have_attribute(
            "data-setup-phase", "choose", timeout=ASSERT_TIMEOUT_MS)
        file_input = page.locator("input#page-file")
        expect(file_input).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        grade = page.get_by_label("الصف الدراسي", exact=True)
        objective = page.get_by_label("الهدف التعليمي", exact=True)
        page_number = page.get_by_label("رقم الصفحة", exact=True)
        expect(grade).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
        expect(objective).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
        file_input.set_input_files(payload)
        expect(self.studio(page)).to_have_attribute(
            "data-setup-phase", "configure",
            timeout=ASSERT_TIMEOUT_MS)
        expect(grade).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(objective).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(page_number).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(self.count_requests("POST", START), 0)
        self.assertEqual(self.count_requests("POST", ANALYSIS), 0)
        grade.fill(fx["GRADE"])
        objective.fill(fx["OBJECTIVE"])
        page_number.fill("1")

        # setup-back is optional in the brief: exercise it only
        # when the view actually renders it.
        back = self.tid(page, "setup-back")
        if back.count() == 0:
            back = page.locator("[data-studio='setup-back']")
        if back.count() > 0 and back.is_visible():
            back.click()
            expect(self.studio(page)).to_have_attribute(
                "data-setup-phase", "choose",
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(
                file_input.evaluate(
                    "el => Array.from(el.files).map(f => f.name)"),
                ["source-page.png"])
            self.assertEqual(self.value_of(grade), fx["GRADE"])
            self.assertEqual(self.value_of(objective), fx["OBJECTIVE"])
            self.assertEqual(self.value_of(page_number), "1")
            file_input.set_input_files(payload)
            expect(self.studio(page)).to_have_attribute(
                "data-setup-phase", "configure",
                timeout=ASSERT_TIMEOUT_MS)
            expect(grade).to_have_value(
                fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
            expect(objective).to_have_value(
                fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
            expect(page_number).to_have_value(
                "1", timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.count_requests("POST", START), 0)
            self.assertEqual(
                self.count_requests("POST", ANALYSIS), 0)

        # 2. Prepare sends Start only; analysis waits for confirm.
        prepare = self.tid(page, "prepare-page")
        expect(prepare).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.click_expect(page, "POST", START, 201, prepare)
        self.assertEqual(self.count_requests("POST", ANALYSIS), 0)
        expect(self.studio(page)).to_have_attribute(
            "data-setup-phase", "confirm", timeout=ASSERT_TIMEOUT_MS)
        confirm = self.tid(page, "confirm-page")
        expect(confirm).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
        page_metadata_response = context.request.get(
            self.base_url + "/api/session/page",
            timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            page_metadata_response.status, 200, "/api/session/page")
        page_metadata = page_metadata_response.json()
        preview_url = page_metadata["preview_url"]
        self.assertIsInstance(preview_url, str)
        preview_parts = urlsplit(preview_url)
        self.assertTrue(preview_url.startswith("/"), preview_url)
        self.assertFalse(preview_url.startswith("//"), preview_url)
        self.assertEqual(preview_parts.scheme, "", preview_url)
        self.assertEqual(preview_parts.netloc, "", preview_url)
        self.assertTrue(
            preview_parts.path.startswith("/"), preview_url)
        preview_response = context.request.get(
            self.base_url + preview_parts.path,
            timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            preview_response.status, 200, preview_parts.path)
        self.assertTrue(
            preview_response.body().startswith(b"\x89PNG"))
        snap = self.snapshot(context)
        self.assertEqual(page_metadata["source_id"], snap["source_id"])
        self.assertEqual(page_metadata["grade"], snap["grade"])
        self.assertEqual(page_metadata["objective"], snap["objective"])
        self.assertEqual(snap["grade"], fx["GRADE"])
        self.assertEqual(snap["objective"], fx["OBJECTIVE"])
        src0 = self.source_image_src(page)
        expect(confirm).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
        self.stage_shot(page, label, "setup")
        self.click_expect(page, "POST", ANALYSIS, 201, confirm)
        self.assert_stage(page, "source")
        expect(prepare).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
        expect(file_input).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
        self.ensure_work_visible(page)
        expect(self.tid(page, "region-item")).to_have_count(2)
        self.stage_shot(page, label, "source")

        # 3. Commit both regions exactly, then generate once.
        snap = self.snapshot(context)
        self.assertEqual(len(snap["regions"]), 2)
        region_a = next(item for item in snap["regions"]
                        if item["raw_text"] == fx["RAW_A"])
        region_b = next(item for item in snap["regions"]
                        if item["raw_text"] == fx["RAW_B"])
        self.commit_region(page, region_a, fx["EDITED_A"])
        self.commit_region(page, region_b, fx["EDITED_B"])
        snap = self.snapshot(context)
        self.assertTrue(snap["can_generate"])
        current = {item["region_id"]: item["current_text"]
                   for item in snap["regions"]}
        self.assertEqual(
            current[region_a["region_id"]], fx["EDITED_A"])
        self.assertEqual(
            current[region_b["region_id"]], fx["EDITED_B"])
        self.assertEqual(self.count_requests("POST", START), 1)
        self.assertEqual(self.count_requests("POST", ANALYSIS), 1)
        self.assertEqual(self.count_requests("POST", GENERATE), 0)
        generate = self.tid(page, "generate-questions")
        if generate.count() == 0:
            generate = page.get_by_role(
                "button", name="اعتماد المصدر وتوليد الأسئلة",
                exact=True)
        expect(generate).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
        self.click_expect(page, "POST", GENERATE, 201, generate)
        expect(self.tid(page, "question-card")).to_have_count(2)
        self.assert_stage(page, "questions")
        snap = self.snapshot(context)
        self.assertEqual(len(snap["questions"]), 2)
        gen_questions = snap["questions"]
        qids = [item["question_id"] for item in gen_questions]
        region_a = next(item for item in snap["regions"]
                        if item["raw_text"] == fx["RAW_A"])
        region_b = next(item for item in snap["regions"]
                        if item["raw_text"] == fx["RAW_B"])
        self.assertEqual(len(region_a["linked_question_ids"]), 1)
        self.assertEqual(len(region_b["linked_question_ids"]), 1)
        qa_id = region_a["linked_question_ids"][0]
        qb_id = region_b["linked_question_ids"][0]
        by_id = {item["question_id"]: item for item in gen_questions}
        qa, qb = by_id[qa_id], by_id[qb_id]
        self.assertEqual(qa["status"], "draft")
        self.assertEqual(qb["status"], "draft")

        # 4. Question nav: truthful buttons, one visible card.
        nav = page.locator("[data-studio='question-nav']")
        expect(nav).to_have_count(1)
        expect(nav).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        buttons = nav.locator("button[data-question-id]")
        expect(buttons).to_have_count(len(qids))
        self.assertEqual(
            [button.get_attribute("data-question-id")
             for button in buttons.all()], qids)
        hidden_map = {
            qid: self.hidden_of(self.question_card(page, qid))
            for qid in qids}
        self.assertEqual(
            sorted(hidden_map.values()), [False, True])
        visible_id = next(
            qid for qid, hidden in hidden_map.items() if not hidden)
        expect(self.question_card(page, visible_id)).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        self.stage_shot(page, label, "questions")

        # Dirty A, round-trip B/A by nav only, then cancel A.
        self.select_question(page, qa_id, qb_id)
        card_a = self.question_card(page, qa_id)
        wording_a = self.open_question_editor(card_a)
        wording_a.fill(DRAFT_WORDING)
        expect(self.tid(card_a, "question-approve")).to_be_disabled(
            timeout=ASSERT_TIMEOUT_MS)
        card_a_handle = card_a.element_handle()
        wording_a_handle = wording_a.element_handle()
        self.assertIsNotNone(card_a_handle)
        self.assertIsNotNone(wording_a_handle)
        mark = len(self.requests_log)
        self.select_question(page, qb_id, qa_id)
        self.assertEqual(
            self.value_of(
                self.tid(card_a, "question-edit-question")),
            DRAFT_WORDING)
        self.select_question(page, qa_id, qb_id)
        self.assertTrue(self.same_node(
            card_a_handle, self.question_card(page, qa_id)))
        self.assertTrue(self.same_node(
            wording_a_handle,
            self.tid(card_a, "question-edit-question")))
        expect(wording_a).to_have_value(
            DRAFT_WORDING, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(self.mutations_since(mark), [])
        self.assertEqual(
            self.snapshot(context)["questions"], gen_questions)
        mark = len(self.requests_log)
        self.tid(card_a, "question-edit-cancel").click()
        expect(self.open_question_editor(card_a)).to_have_value(
            qa["current_question"], timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(len(self.requests_log), mark)
        self.assertEqual(self.mutations_since(mark), [])

        # Approve only B explicitly; no auto-preview follows.
        self.select_question(page, qb_id, qa_id)
        card_b = self.question_card(page, qb_id)
        self.click_expect(
            page, "POST", self.question_path(qb_id, "/approve"), 200,
            self.tid(card_b, "question-approve"))
        self.assert_stage(page, "questions")
        expect(card_b).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        snap = self.snapshot(context)
        by_id = {item["question_id"]: item
                 for item in snap["questions"]}
        self.assertEqual(by_id[qb_id]["status"], "approved")
        self.assertEqual(by_id[qa_id], qa)
        self.assertEqual(
            [item["question_id"] for item in snap["preview_items"]],
            [qb_id])
        self.assertEqual(
            snap["preview_items"][0]["question"],
            by_id[qb_id]["current_question"])
        card_b_handle = card_b.element_handle()
        self.assertIsNotNone(card_b_handle)

        # Preview shows approved B only; return retains selection.
        mark = len(self.requests_log)
        self.tid(page, "workflow-nav-preview").click()
        self.assert_stage(page, "preview")
        expect(self.tid(page, "preview-item")).to_have_count(1)
        preview_b = page.locator(
            "[data-testid='preview-item']"
            f"[data-question-id=\"{qb_id}\"]")
        expect(preview_b).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(preview_b, "preview-question")).to_have_text(
            by_id[qb_id]["current_question"],
            timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(page.locator(
            "[data-testid='preview-item']"
            f"[data-question-id=\"{qa_id}\"]").count(), 0)
        self.assertNotIn(
            qa["current_question"].strip(),
            self.tid(page, "preview-list").text_content())
        self.stage_shot(page, label, "preview")
        self.tid(page, "preview-return").click()
        self.assert_stage(page, "questions")
        expect(card_b).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.assertFalse(self.hidden_of(card_b))
        self.assertTrue(self.hidden_of(card_a))
        self.assertTrue(self.same_node(
            card_b_handle, self.question_card(page, qb_id)))
        self.assertEqual(
            self.value_of(
                self.tid(card_a, "question-edit-question")),
            qa["current_question"])
        self.assertEqual(self.mutations_since(mark), [])

        # 5. Linked-question reveal, then full-page evidence.
        snap = self.snapshot(context)
        region_a = next(
            item for item in snap["regions"]
            if qa_id in item["linked_question_ids"])
        mark = len(self.requests_log)
        self.tid(page, "workflow-nav-source").click()
        self.assert_stage(page, "source")
        self.ensure_work_visible(page)
        item_a = self.region_item(page, region_a["region_id"])
        expect(item_a).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        link = item_a.locator(
            "[data-testid='region-linked-question']"
            f"[data-question-id=\"{qa_id}\"]")
        expect(link).to_have_count(1)
        expect(link).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        link.click()
        self.assert_stage(page, "questions")
        expect(card_a).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.assertFalse(self.hidden_of(card_a))
        self.assertTrue(self.hidden_of(card_b))
        # A hidden card cannot hold focus, so focus inside the
        # now-visible card witnesses reveal-before-focus.
        expect(card_a.locator(":focus")).to_have_count(
            1, timeout=ASSERT_TIMEOUT_MS)
        self.assertTrue(self.same_node(
            card_a_handle, self.question_card(page, qa_id)))
        self.assertTrue(self.same_node(
            wording_a_handle,
            self.tid(card_a, "question-edit-question")))
        self.assertEqual(
            self.value_of(
                self.tid(card_a, "question-edit-question")),
            qa["current_question"])
        self.assertEqual(self.mutations_since(mark), [])

        mark = len(self.requests_log)
        self.tid(page, "workflow-nav-source").click()
        self.assert_stage(page, "source")
        self.ensure_work_visible(page)
        expect(item_a).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.tid(item_a, "region-evidence-link").click()
        self.assertEqual(
            self.source_image_src(page, allow_tab=False), src0)
        self.tid(page, "workflow-nav-questions").click()
        self.assert_stage(page, "questions")
        expect(card_a).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.assertFalse(self.hidden_of(card_a))
        self.assertTrue(self.hidden_of(card_b))
        self.assertTrue(self.same_node(
            card_a_handle, self.question_card(page, qa_id)))
        self.assertEqual(
            self.value_of(
                self.tid(card_a, "question-edit-question")),
            qa["current_question"])
        self.assertEqual(self.mutations_since(mark), [])

        # 6. Clean ending and exactly-once sends, per context.
        widths = page.evaluate(
            "() => [document.documentElement.scrollWidth, "
            "document.documentElement.clientWidth]")
        self.assertLessEqual(widths[0], widths[1])
        self.assertEqual(self.external_urls, [])
        self.assertEqual(self.page_errors, [])
        for path in (START, ANALYSIS, GENERATE):
            self.assertEqual(
                self.count_requests("POST", path), 1, path)
            self.assertEqual(
                self.count_responses("POST", path, 201), 1, path)

    def test_studio_presentation_journey(self):
        for viewport in VIEWPORTS:
            label = f"{viewport['width']}x{viewport['height']}"
            with self.subTest(viewport=label):
                with self.browser_page(viewport) as (context, page):
                    self.run_journey(context, page, label)


if __name__ == "__main__":
    unittest.main()


