"""Real-browser metadata/navigation tracer for the review UI.

The test harness serves
``teacher_demo.http_app.create_app`` on a random 127.0.0.1 origin
with the server-only ``ReviewMetadataProvider`` (see
``tests/review_metadata_provider.py``; synthetic test code,
explicitly not Meta) injected through the server-side provider
seam. This module never imports that provider, the app or any
backend module in the browser interpreter. Fixture constants
come only from ``test_review_journey.load_fixture()`` in the
adjacent unchanged journey module; its TestCase is not imported
or inherited. Each case owns its setup and this class derives
directly from ``unittest.TestCase``.

Environment and safety match the journey tracer: required
``BROWSER_BASE_URL``/``BROWSER_EXECUTABLE``, optional
``BROWSER_ARTIFACT_DIR``, headless Chromium with sandbox
enabled, fresh contexts with service workers blocked, bounded
waits and ``finally`` cleanup. Teacher mutations come only
from real DOM controls; shared-cookie context requests are
GET-only snapshot/raw observers. The single routing exception
is a narrowly labelled abort of a real forwarded GET (raw or
snapshot read) used for failed-read assertions: the real GET
is forwarded once with retries/redirects disabled, its body
is discarded and the request is aborted. No body is ever
fulfilled or replaced and no mutation is ever aborted.

Each surface below (per-request model/request
markers, generation settings markers, required-order endpoint
links, linked-question current status, display-move position
announcement) is checked after real API/DOM setup
is established: missing markers fail promptly with a short
marker timeout instead of spending a full assertion timeout
per card. Required-order cards are admitted by the fixture
but blocked by the current real HTTP contract, so no
order-bearing preview is demanded or manufactured.
"""
import contextlib
import json
import os
import pathlib
import unittest
from urllib.parse import quote, unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

import test_review_journey as journey

NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000
# Missing prospective markers fail promptly, not per-card slowly.
MARKER_TIMEOUT_MS = 2_000

UNAVAILABLE_MODEL = "غير متاح"
CHANGED_GRADE = "الصف السادس"
CHANGED_OBJECTIVE = "  هَدَفٌ مُعَدَّلٌ: قِرَاءَةُ النَّصِّ ٤٥٦  "
CHANGED_REGION_LABEL = "تصنيف المعلم بعد التعديل"
CHANGED_SETUP_OBJECTIVE = "  هَدَفٌ جَدِيدٌ: قِرَاءَةُ النَّصِّ ٧٨٩  "


class ReviewMetadataBrowserTests(unittest.TestCase):
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
        # Labelled failed-read state (real forwarded GETs only).
        self.fail_unknown_raw_reads = False
        self.known_raw_request_ids = set()
        self.fail_snapshot_gets = False
        self.forwarded_failed_reads = []

    # -- recording / guarding --------------------------------------
    def _labelled_failed_read(self, path):
        if self.fail_snapshot_gets and path == "/api/session/snapshot":
            return True
        prefix = "/api/session/requests/"
        if (self.fail_unknown_raw_reads and path.startswith(prefix)
                and path.endswith("/raw")):
            request_id = unquote(path[len(prefix):-len("/raw")])
            return request_id not in self.known_raw_request_ids
        return False

    def handle_route(self, route, request):
        parts = urlsplit(request.url)
        if parts.scheme in ("data", "blob"):
            route.continue_()
        elif "{0.scheme}://{0.netloc}".format(parts) != self.base_origin:
            self.external_urls.append(request.url)
            route.abort("blockedbyclient")
        elif (request.method == "GET"
              and self._labelled_failed_read(parts.path)):
            # Forward the real GET once, discard its body, abort.
            with contextlib.suppress(Exception):
                fetched = route.fetch(max_redirects=0, max_retries=0)
                self.forwarded_failed_reads.append(
                    (parts.path, fetched.status))
            route.abort("failed")
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
                "forwarded_failed_reads": self.forwarded_failed_reads,
                "responses": self.responses_log}
            (directory / (self._testMethodName + ".json")).write_text(
                json.dumps(observations, ensure_ascii=False, indent=2),
                encoding="utf-8")

    @contextlib.contextmanager
    def browser_page(self, viewport=None):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=self.executable, headless=True,
                chromium_sandbox=True)
            context = None
            page = None
            try:
                context = browser.new_context(
                    base_url=self.base_url, service_workers="block",
                    viewport=viewport or {"width": 1280, "height": 900})
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

    def raw_result(self, context, request_id, kind=None):
        payload = self.api_json(
            context, "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["raw_retention"], "retained")
        if kind is not None:
            self.assertEqual(payload["kind"], kind)
        result = payload["provider_result"]
        self.assertTrue(result["model_name"].strip())
        return result

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

    def ordinary_question(self, snapshot, slot_id):
        """Select the named ordinary draft, not the sorted dependency list."""
        matches = [item for item in snapshot["questions"]
                   if item["slot_id"] == slot_id]
        self.assertEqual(len(matches), 1, slot_id)
        self.assertEqual(matches[0]["required_order_relationships"], [])
        return matches[0]

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
    def region_path(region, suffix=""):
        return ("/api/session/regions/"
                + quote(region["region_id"], safe="") + suffix)

    @staticmethod
    def question_path(question, suffix=""):
        return ("/api/session/questions/"
                + quote(question["question_id"], safe="") + suffix)

    def assert_clean_network(self):
        self.assertEqual(self.external_urls, [])
        self.assertEqual(self.page_errors, [])

    # -- prospective marker assertions (prompt on absence) ----------
    def required_marker(self, scope, name):
        locator = self.tid(scope, name)
        expect(locator).to_have_count(1, timeout=MARKER_TIMEOUT_MS)
        expect(locator).to_be_visible(timeout=MARKER_TIMEOUT_MS)
        return locator

    def assert_marker_text(self, scope, name, expected):
        locator = self.required_marker(scope, name)
        # Await the asynchronous metadata observation, then require raw
        # equality so whitespace/codepoint differences still fail.
        expect(locator).to_have_text(expected, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(locator.text_content(), expected, name)
        return locator

    def assert_isolated_identifier(self, scope, name, expected):
        locator = self.assert_marker_text(scope, name, expected)
        bidi = locator.evaluate(
            "element => getComputedStyle(element).unicodeBidi")
        self.assertIn(
            bidi, ("isolate", "isolate-override", "plaintext"), name)
        return locator

    # -- real DOM setup (one per case) ------------------------------
    def start_source_analysis(self, context, page):
        """Drive the existing setup form to a real source analysis."""
        page.goto("/")
        page.get_by_label("الصف الدراسي", exact=True).select_option(value=self.fx["GRADE"])
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

    def generate_questions(self, context, page, expected_card_count):
        button = page.get_by_role(
            "button", name="اعتماد المصدر وتوليد الأسئلة", exact=True)
        expect(button).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
        button.click()
        expect(page.get_by_test_id("question-card")).to_have_count(
            expected_card_count, timeout=ASSERT_TIMEOUT_MS)
        snapshot = self.snapshot(context)
        self.assertEqual(
            len(snapshot["questions"]), expected_card_count)
        return snapshot

    def setup_generated(self, context, page):
        """Source analysis + both canonical commits + one generation."""
        snapshot = self.start_source_analysis(context, page)
        region_a = self.region_by_raw(snapshot, self.fx["RAW_A"])
        region_b = self.region_by_raw(snapshot, self.fx["RAW_B"])
        self.commit_region(
            page, region_a, self.fx["EDITED_A"],
            region_a["current_label"], "included")
        self.commit_region(
            page, region_b, self.fx["EDITED_B"],
            region_b["current_label"], "included")
        # Metadata fixture: two ordinary drafts + one Analyze draft.
        return self.generate_questions(context, page, 3)

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

    def model_for(self, context, cache, request_id):
        if request_id not in cache:
            cache[request_id] = self.raw_result(
                context, request_id, kind="questions")["model_name"]
        return cache[request_id]

    def assert_question_lineage(self, context, page, questions, cache):
        for question in questions:
            card = self.question_card(page, question)
            expect(card).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            # Existing surface first, then the new markers.
            expect(self.tid(card, "question-status")).to_have_text(
                journey.STATUS_ARABIC[question["status"]],
                timeout=ASSERT_TIMEOUT_MS)
            self.assert_isolated_identifier(
                card, "question-request-id", question["request_id"])
            self.assert_isolated_identifier(
                card, "question-model-name",
                self.model_for(context, cache, question["request_id"]))
            self.assert_marker_text(
                card, "question-generation-grade",
                question["generation_grade"])
            self.assert_marker_text(
                card, "question-generation-objective",
                question["generation_objective"])
            self.assert_marker_text(
                card, "question-grade", question["current_grade"])
            self.assert_marker_text(
                card, "question-objective", question["current_objective"])

    def assert_single_preview_lineage(
            self, context, page, snapshot, question, cache):
        self.assertEqual(
            [entry["question_id"] for entry in snapshot["preview_items"]],
            [question["question_id"]])
        items = page.get_by_test_id("preview-item")
        expect(items).to_have_count(1, timeout=ASSERT_TIMEOUT_MS)
        self.assert_isolated_identifier(
            items.first, "preview-request-id", question["request_id"])
        self.assert_isolated_identifier(
            items.first, "preview-model-name",
            self.model_for(context, cache, question["request_id"]))

    # -- case 1: lineage, generations, generation/current settings --
    def test_model_lineage_across_two_generations_and_generation_settings(
            self):
        with self.browser_page() as (context, page):
            snapshot = self.start_source_analysis(context, page)
            region_a = self.region_by_raw(snapshot, self.fx["RAW_A"])
            region_b = self.region_by_raw(snapshot, self.fx["RAW_B"])

            # Real API setup first: both regions share the source
            # request, whose exact model comes from raw readback.
            source_request_id = region_a["request_id"]
            self.assertEqual(
                {item["request_id"] for item in snapshot["regions"]},
                {source_request_id})
            source_model = self.raw_result(
                context, source_request_id, kind="source")["model_name"]
            for region in (region_a, region_b):
                item = self.region_item(page, region)
                expect(item).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-heading")).to_have_text(
                    region["current_label"], timeout=ASSERT_TIMEOUT_MS)
                self.assert_isolated_identifier(
                    item, "region-request-id", region["request_id"])
                self.assert_isolated_identifier(
                    item, "region-model-name", source_model)

            # First generation through the real UI.
            self.commit_region(
                page, region_a, self.fx["EDITED_A"],
                region_a["current_label"], "included")
            self.commit_region(
                page, region_b, self.fx["EDITED_B"],
                region_b["current_label"], "included")
            gen1 = self.generate_questions(context, page, 3)
            cache = {}
            gen1_request_ids = {
                item["request_id"] for item in gen1["questions"]}
            self.assertEqual(len(gen1_request_ids), 1)
            self.assert_question_lineage(
                context, page, gen1["questions"], cache)
            gen1_model = self.model_for(
                context, cache, next(iter(gen1_request_ids)))
            self.assertNotEqual(gen1_model, source_model)

            # Approve an ordinary first-generation question, then
            # generate again; its preview lineage must stay on the
            # first envelope, never the latest or source model.
            river_gen1 = self.region_by_id(gen1, region_a["region_id"])
            question_a1 = self.ordinary_question(gen1, "slot-remember")
            self.approve_question(page, question_a1)
            self.assert_single_preview_lineage(
                context, page, self.snapshot(context), question_a1, cache)
            gen1_ids = {
                item["question_id"] for item in gen1["questions"]}
            gen2 = self.generate_questions(context, page, 6)
            new_questions = [
                item for item in gen2["questions"]
                if item["question_id"] not in gen1_ids]
            self.assertEqual(len(new_questions), 3)
            gen2_request_ids = {
                item["request_id"] for item in new_questions}
            self.assertEqual(len(gen2_request_ids), 1)
            self.assertTrue(gen2_request_ids.isdisjoint(gen1_request_ids))
            gen2_model = self.model_for(
                context, cache, next(iter(gen2_request_ids)))
            self.assertNotEqual(gen2_model, gen1_model)
            self.assertNotEqual(gen2_model, source_model)
            # Every retained card stays tied to its own envelope.
            self.assert_question_lineage(
                context, page, gen2["questions"], cache)
            ack_a1 = self.question_by_id(
                gen2, question_a1["question_id"])
            self.assert_single_preview_lineage(
                context, page, gen2, ack_a1, cache)

            # Generation vs current settings on an unapproved
            # ordinary second-generation card, compared to the
            # actual GET snapshot; restore before finishing.
            target = next(
                item for item in new_questions
                if "required_order_missing" not in item.get("blocks", []))
            card = self.question_card(page, target)
            self.open_question_editor(card)
            self.tid(card, "question-edit-grade").select_option(value=CHANGED_GRADE)
            self.tid(card, "question-edit-objective").fill(
                CHANGED_OBJECTIVE)
            self.click_expect(
                page, "PATCH", self.question_path(target), 200,
                card.get_by_role(
                    "button", name="حفظ التعديل", exact=True))
            edited = self.question_by_id(
                self.snapshot(context), target["question_id"])
            self.assertEqual(
                edited["generation_grade"], target["generation_grade"])
            self.assertEqual(
                edited["generation_objective"],
                target["generation_objective"])
            self.assertEqual(edited["current_grade"], CHANGED_GRADE)
            self.assertEqual(edited["current_objective"], CHANGED_OBJECTIVE)
            self.assert_marker_text(
                card, "question-generation-grade",
                edited["generation_grade"])
            self.assert_marker_text(
                card, "question-generation-objective",
                edited["generation_objective"])
            self.assert_marker_text(
                card, "question-grade", edited["current_grade"])
            self.assert_marker_text(
                card, "question-objective", edited["current_objective"])

            self.open_question_editor(card)
            self.tid(card, "question-edit-grade").select_option(
                value=edited["generation_grade"])
            self.tid(card, "question-edit-objective").fill(
                edited["generation_objective"])
            self.click_expect(
                page, "PATCH", self.question_path(target), 200,
                card.get_by_role(
                    "button", name="حفظ التعديل", exact=True))
            restored = self.question_by_id(
                self.snapshot(context), target["question_id"])
            self.assertEqual(restored["current_grade"], restored["generation_grade"])
            self.assertEqual(
                restored["current_objective"], restored["generation_objective"])
            self.assert_marker_text(
                card, "question-grade", restored["current_grade"])
            self.assert_marker_text(
                card, "question-objective", restored["current_objective"])
            self.assert_clean_network()

    # -- case 2: order links, linked status, display movement -------
    def test_order_endpoint_links_linked_status_and_display_move_announcement(
            self):
        with self.browser_page(
                viewport={"width": 390, "height": 844}) as (context, page):
            snapshot = self.setup_generated(context, page)
            region_a = self.region_by_raw(snapshot, self.fx["RAW_A"])
            region_b = self.region_by_raw(snapshot, self.fx["RAW_B"])

            # Establish the real blocked-card setup before any
            # missing-UI assertion: one card carries a nonempty
            # required-order pair and the named server block.
            blocked = [
                item for item in snapshot["questions"]
                if "required_order_missing" in item.get("blocks", [])]
            self.assertEqual(len(blocked), 1)
            analyze = blocked[0]
            pairs = analyze["required_order_relationships"]
            self.assertTrue(pairs, "snapshot order pair must be nonempty")
            self.assertEqual(pairs, [[region_a["region_id"], region_b["region_id"]]])
            self.assertNotEqual(analyze["status"], "approved")

            card = self.question_card(page, analyze)
            expect(card).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            relationship = self.required_marker(
                card, "question-order-relationship")
            endpoints = self.tid(relationship, "question-order-source")
            expect(endpoints).to_have_count(2, timeout=MARKER_TIMEOUT_MS)
            self.assertEqual(
                {item.get_attribute("data-region-id")
                 for item in endpoints.all()},
                {region_a["region_id"], region_b["region_id"]})

            # Each endpoint is a native button opening the known
            # whole source page; focus lands on the source heading
            # and the return path refocuses the invoking button.
            # Navigation sends no mutation.
            request_count = len(self.requests_log)
            for region_id in (
                    region_a["region_id"], region_b["region_id"]):
                button = relationship.locator(
                    "[data-testid='question-order-source']"
                    f"[data-region-id=\"{region_id}\"]")
                expect(button).to_be_visible(timeout=MARKER_TIMEOUT_MS)
                self.assertEqual(
                    button.evaluate("element => element.tagName"),
                    "BUTTON")
                button.click()
                heading = page.locator("#source-heading")
                expect(heading).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(heading).to_be_focused(
                    timeout=ASSERT_TIMEOUT_MS)
                page.get_by_test_id("pane-tab-work").click()
                expect(button).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(request_count), [])

            # Blocking continues and no order-bearing preview is
            # reachable through this UI (not manufactured here).
            still = self.snapshot(context)
            still_blocked = self.question_by_id(
                still, analyze["question_id"])
            self.assertIn(
                "required_order_missing",
                still_blocked.get("blocks", []))
            self.assertNotEqual(still_blocked["status"], "approved")
            self.assertNotIn(
                analyze["question_id"],
                [entry["question_id"]
                 for entry in still["preview_items"]])
            expect(page.get_by_test_id(
                "preview-order-source")).to_have_count(0)

            # Linked-question current status: approve both ordinary
            # questions, then change only the river source label
            # through the region form (canonical text unchanged).
            question_a = self.ordinary_question(snapshot, "slot-remember")
            question_b = self.ordinary_question(snapshot, "slot-understand")
            self.approve_question(page, question_a)
            self.approve_question(page, question_b)

            def linked_status(region, question, expected_status):
                item = self.region_item(page, region)
                control = item.locator(
                    "[data-testid='region-linked-question']"
                    f"[data-question-id=\"{question['question_id']}\"]")
                expect(control).to_have_count(
                    1, timeout=MARKER_TIMEOUT_MS)
                status = self.assert_marker_text(
                    control, "region-linked-question-status",
                    journey.STATUS_ARABIC[expected_status])
                card_status = self.tid(
                    self.question_card(page, question), "question-status")
                self.assertEqual(
                    status.text_content(), card_status.text_content())

            approved = self.snapshot(context)
            for region, question in (
                    (region_a, question_a), (region_b, question_b)):
                actual = self.question_by_id(
                    approved, question["question_id"])
                self.assertEqual(actual["status"], "approved")
                linked_status(
                    self.region_by_id(approved, region["region_id"]),
                    actual, "approved")

            river = self.region_by_id(approved, region_a["region_id"])
            self.commit_region(
                page, river, self.fx["EDITED_A"],
                CHANGED_REGION_LABEL, "included")
            after = self.snapshot(context)
            after_a = self.question_by_id(
                after, question_a["question_id"])
            after_b = self.question_by_id(
                after, question_b["question_id"])
            # The independent ordinary question survives approved;
            # the affected link follows the server exactly (the
            # actual status is read, never fabricated here).
            self.assertEqual(after_b["status"], "approved")
            self.assertNotEqual(after_a["status"], "approved")
            linked_status(
                self.region_by_id(after, region_a["region_id"]),
                after_a, after_a["status"])
            linked_status(
                self.region_by_id(after, region_b["region_id"]),
                after_b, "approved")

            # One acknowledged down/up display move: same button
            # node and focus, actual position/total announced in
            # the outside-pane workspace status line.
            ordered = sorted(
                after["regions"], key=lambda item: item["display_position"])
            total = len(ordered)
            first = ordered[0]
            item = self.region_item(page, first)
            down = item.get_by_role(
                "button", name="نقل المقطع لأسفل", exact=True)
            down_handle = down.element_handle()
            self.click_expect(
                page, "POST", self.region_path(first, "/move-down"), 200,
                down)
            moved = self.snapshot(context)
            moved_first = self.region_by_id(moved, first["region_id"])
            self.assertEqual(
                moved_first["display_position"],
                first["display_position"] + 1)
            self.assertEqual(
                [node.get_attribute("data-region-id")
                 for node in
                 page.locator("[data-testid='region-item']").all()],
                [item_["region_id"] for item_ in sorted(
                    moved["regions"],
                    key=lambda entry: entry["display_position"])])
            down_after = self.region_item(page, moved_first).get_by_role(
                "button", name="نقل المقطع لأسفل", exact=True)
            self.assertTrue(down_handle.evaluate(
                "(node, other) => node === other",
                down_after.element_handle()))
            expect(down_after).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
            announcement = self.required_marker(
                page, "workspace-status").text_content()
            self.assertIn("موضع المقطع", announcement)
            self.assertIn(
                journey.arabic_indic(moved_first["display_position"]),
                announcement)
            self.assertIn(journey.arabic_indic(total), announcement)

            up = self.region_item(page, moved_first).get_by_role(
                "button", name="نقل المقطع لأعلى", exact=True)
            up_handle = up.element_handle()
            self.click_expect(
                page, "POST", self.region_path(first, "/move-up"), 200, up)
            restored = self.snapshot(context)
            restored_first = self.region_by_id(
                restored, first["region_id"])
            self.assertEqual(
                restored_first["display_position"],
                first["display_position"])
            up_after = self.region_item(
                page, restored_first).get_by_role(
                    "button", name="نقل المقطع لأعلى", exact=True)
            self.assertTrue(up_handle.evaluate(
                "(node, other) => node === other",
                up_after.element_handle()))
            expect(up_after).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
            announcement = self.required_marker(
                page, "workspace-status").text_content()
            self.assertIn("موضع المقطع", announcement)
            self.assertIn(
                journey.arabic_indic(restored_first["display_position"]),
                announcement)
            self.assertIn(journey.arabic_indic(total), announcement)
            self.assert_clean_network()

    # -- case 3a: failed metadata read opposite ---------------------
    def test_failed_metadata_read_shows_unavailable_without_fallback(
            self):
        with self.browser_page() as (context, page):
            gen1 = self.setup_generated(context, page)
            region_a = self.region_by_raw(gen1, self.fx["RAW_A"])
            cache = {}

            # Healthy lineage first, then a real approved preview.
            source_model = self.raw_result(
                context, region_a["request_id"],
                kind="source")["model_name"]
            self.assert_isolated_identifier(
                self.region_item(page, region_a),
                "region-model-name", source_model)
            self.assert_question_lineage(
                context, page, gen1["questions"], cache)
            gen1_request_id = next(iter(
                {item["request_id"] for item in gen1["questions"]}))
            gen1_model = self.model_for(context, cache, gen1_request_id)
            question_a = self.ordinary_question(gen1, "slot-remember")
            self.approve_question(page, question_a)
            self.assert_single_preview_lineage(
                context, page, self.snapshot(context), question_a, cache)

            # Fail only the next generation's raw GETs (real GET
            # forwarded once by the route handler, then aborted).
            self.known_raw_request_ids = (
                {region_a["request_id"], gen1_request_id})
            self.fail_unknown_raw_reads = True
            with page.expect_event(
                    "requestfailed",
                    predicate=lambda request: request.method == "GET"
                    and self._labelled_failed_read(urlsplit(request.url).path),
                    timeout=ASSERT_TIMEOUT_MS):
                gen2 = self.generate_questions(context, page, 6)
            gen1_ids = {
                item["question_id"] for item in gen1["questions"]}
            new_questions = [
                item for item in gen2["questions"]
                if item["question_id"] not in gen1_ids]
            self.assertEqual(len(new_questions), 3)
            gen2_request_ids = {
                item["request_id"] for item in new_questions}
            self.assertEqual(len(gen2_request_ids), 1)
            gen2_request_id = next(iter(gen2_request_ids))
            self.assertTrue(self.forwarded_failed_reads)
            self.assertTrue(all(
                path.endswith("/raw") and status == 200
                for path, status in self.forwarded_failed_reads))

            # The separate GET observer still reads the real
            # envelope; the UI must nevertheless show the unknown
            # value, with no source/latest-model fallback.
            real_gen2_model = self.raw_result(
                context, gen2_request_id, kind="questions")["model_name"]
            self.assertNotIn(
                real_gen2_model, (UNAVAILABLE_MODEL, gen1_model,
                                  source_model))
            for question in new_questions:
                card = self.question_card(page, question)
                expect(card).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                self.assert_isolated_identifier(
                    card, "question-request-id", question["request_id"])
                self.assert_isolated_identifier(
                    card, "question-model-name", UNAVAILABLE_MODEL)

            # Known source identity, first-generation cards and
            # the real approved preview survive the failure.
            self.assertEqual(gen2["source_id"], gen1["source_id"])
            expect(page.get_by_test_id("source-preview")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(page.get_by_role("button", name="بدء تحليل الصفحة", exact=True)).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)
            self.assert_isolated_identifier(
                self.region_item(page, region_a),
                "region-model-name", source_model)
            retained = [
                item for item in gen2["questions"]
                if item["question_id"] in gen1_ids]
            self.assert_question_lineage(
                context, page, retained, cache)
            ack_a = self.question_by_id(gen2, question_a["question_id"])
            self.assert_single_preview_lineage(
                context, page, gen2, ack_a, cache)
            self.assert_clean_network()

    # -- case 3b: withdrawn-currentness opposite --------------------
    def test_withdrawn_currentness_hides_stale_linked_status(self):
        with self.browser_page() as (context, page):
            snapshot = self.setup_generated(context, page)
            region_a = self.region_by_raw(snapshot, self.fx["RAW_A"])
            question_a = self.ordinary_question(snapshot, "slot-remember")
            self.approve_question(page, question_a)

            # Healthy currentness first: the linked status is
            # visible and matches the actual approved status.
            item = self.region_item(page, region_a)
            control = item.locator(
                "[data-testid='region-linked-question']"
                f"[data-question-id=\"{question_a['question_id']}\"]")
            expect(control).to_have_count(1, timeout=MARKER_TIMEOUT_MS)
            self.assert_marker_text(
                control, "region-linked-question-status",
                journey.STATUS_ARABIC["approved"])

            # Acknowledge a changed setup objective, fail Start
            # with a real invalid file, and abort the real
            # forwarded snapshot GET. No mutation is aborted and
            # no response body is invented.
            self.fail_snapshot_gets = True
            page.get_by_label("الهدف التعليمي", exact=True).fill(
                CHANGED_SETUP_OBJECTIVE)
            page.locator("input[type='file']").set_input_files({
                "name": "invalid.txt", "mimeType": "text/plain",
                "buffer": b"not an image"})
            start_button = page.get_by_role(
                "button", name="بدء تحليل الصفحة", exact=True)
            self.click_expect(
                page, "PUT", "/api/session/settings", 200, start_button)
            expect(start_button).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)
            # The existing currency disclosure shows once the
            # failed refresh settles (this waits for the chain).
            self.required_marker(page, "review-currency-status")
            start_statuses = [
                entry["status"] for entry in self.responses_log
                if entry["method"] == "POST"
                and entry["path"] == "/api/session/start"]
            self.assertTrue(start_statuses)
            self.assertGreaterEqual(start_statuses[-1], 400)
            self.assertIn(
                ("/api/session/snapshot", 200),
                self.forwarded_failed_reads)

            # The stale linked status is not visible and the
            # link's visible text makes no stale approval claim.
            expect(
                self.tid(control, "region-linked-question-status")
            ).to_be_hidden(timeout=MARKER_TIMEOUT_MS)
            self.assertNotIn(
                journey.STATUS_ARABIC["approved"], control.inner_text())

            # Known source/read navigation remains usable after
            # busy ends, still with zero mutations.
            request_count = len(self.requests_log)
            item.get_by_role(
                "button", name="إظهار الصفحة الكاملة", exact=True).click()
            expect(page.locator("#source-heading")).to_be_focused(
                timeout=ASSERT_TIMEOUT_MS)
            control.click()
            expect(
                self.question_card(page, question_a)
                .get_by_role("heading").first
            ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(request_count), [])
            self.assert_clean_network()


if __name__ == "__main__":
    unittest.main()