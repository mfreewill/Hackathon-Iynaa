"""Exact provider-command browser tracer for the teacher app.

The test harness serves the app with its server-only synthetic provider.
This module observes the commands sent by the visible browser controls.
It never seeds teacher state through a mutation request of its own.
"""

import contextlib
import hashlib
import json
import os
import pathlib
import unittest
from urllib.parse import quote, urljoin, urlsplit

from playwright.sync_api import expect, sync_playwright

import test_review_journey as journey


NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000

START_PATH = "/api/session/start"
SOURCE_ANALYSIS_PATH = "/api/session/source-analysis"
QUESTIONS_GENERATE_PATH = "/api/session/questions/generate"
SNAPSHOT_PATH = "/api/session/snapshot"
PREVIEW_PATH = "/api/session/preview"

BLOOM_ARABIC = {
    "remember": "تذكّر", "understand": "فهم", "apply": "تطبيق",
    "analyze": "تحليل", "evaluate": "تقييم", "create": "إنشاء"}
INCLUSION_ARABIC = {
    "unreviewed": "بانتظار المراجعة", "included": "مشمول",
    "excluded": "مستبعد", "unresolved": "غير محسوم"}
STATUS_ARABIC = {
    "draft": "مسودة", "approved": "معتمد", "rejected": "مرفوض",
    "outdated": "يحتاج إلى تحديث"}
DECISION_ARABIC = {
    "draft": "قرار المعلم: بانتظار القرار",
    "approved": "قرار المعلم: معتمد",
    "rejected": "قرار المعلم: مرفوض",
    "outdated": "قرار المعلم: كان معتمدًا ويحتاج إلى مراجعة"}
CURRENT_AUTHOR_ARABIC = {
    "model": "اقتراح النموذج كما ورد",
    "teacher": "نسخة معدّلة بواسطة المعلم"}
TEACHER_PREVIEW_WARNING = (
    "معاينة للمعلم فقط — ليست نشرًا للطلاب ولا اعتمادًا تربويًا نهائيًا.")
NON_DURABLE_WARNING = (
    "مساحة تجريبية غير محفوظة: قد يؤدي تحديث الصفحة أو إغلاقها أو "
    "إعادة التشغيل إلى فقدان هذا العمل.")
PREVIEW_EMPTY_TEXT = (
    "لا أسئلة معتمدة حاليًا؛ اعتمد سؤالًا من مراجعة الأسئلة لتظهر هنا.")


class ProviderOperationBrowserTests(unittest.TestCase):
    """One vertical tracer from setup to one approved preview item."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx, cls.png_bytes = journey.load_fixture()

    def setUp(self):
        self.base_url = os.environ.get(
            "BROWSER_BASE_URL", "").rstrip("/")
        self.executable = os.environ.get("BROWSER_EXECUTABLE", "")
        self.artifact_dir = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        self.assertTrue(
            self.base_url,
            "BROWSER_BASE_URL (actual served origin) is required")
        self.assertTrue(
            self.executable,
            "BROWSER_EXECUTABLE (installed browser path) is required")
        self.assertTrue(
            pathlib.Path(self.executable).is_file(),
            "BROWSER_EXECUTABLE must name the installed browser file")
        parts = urlsplit(self.base_url)
        self.assertIn(parts.scheme, ("http", "https"))
        self.assertTrue(parts.netloc)
        self.base_origin = "{0.scheme}://{0.netloc}".format(parts)
        self.requests_log = []
        self.external_urls = []
        self.page_errors = []
        self.observation = {
            "statuses": {},
            "provider_commands": {
                "source_analysis": None,
                "question_generation": None},
            "provider_post_counts": {
                "source_analysis": 0,
                "question_generation": 0},
            "response_request_ids": {
                "source_analysis": None,
                "question_generation": None},
            "request_record_ids": {
                "source_analysis": None,
                "question_generation": None},
            "source_id": None,
            "acknowledged_source_id": None,
            "completed_phases": [],
            "external_urls": [],
            "page_errors": [],
            "final_dom_text": "",
        }

    # -- recording / guarding --------------------------------------
    def handle_route(self, route, request):
        parts = urlsplit(request.url)
        if parts.scheme in ("data", "blob"):
            route.continue_()
        elif parts.scheme in ("http", "https") and (
                "{0.scheme}://{0.netloc}".format(parts)
                != self.base_origin):
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

    def request_entries(self, method, path, start=0):
        return [entry for entry in self.requests_log[start:]
                if entry["method"] == method
                and entry["path"] == path]

    def request_bodies(self, method, path, start=0):
        return [entry.get("json")
                for entry in self.request_entries(method, path, start)]

    def provider_post_count(self, path):
        return len(self.request_entries("POST", path))

    def mark_phase(self, name):
        self.observation["completed_phases"].append(name)

    def capture_artifacts(self, page):
        if not self.artifact_dir:
            return
        with contextlib.suppress(Exception):
            directory = pathlib.Path(self.artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            self.observation["provider_post_counts"] = {
                "source_analysis": self.provider_post_count(
                    SOURCE_ANALYSIS_PATH),
                "question_generation": self.provider_post_count(
                    QUESTIONS_GENERATE_PATH)}
            self.observation["external_urls"] = list(
                self.external_urls)
            self.observation["page_errors"] = list(self.page_errors)
            if page is not None:
                with contextlib.suppress(Exception):
                    self.observation["final_dom_text"] = (
                        page.locator("body").inner_text(
                            timeout=ASSERT_TIMEOUT_MS))
            (directory / "provider_operations_observation.json"
             ).write_text(
                 json.dumps(
                     self.observation, ensure_ascii=False, indent=2)
                 + "\n",
                 encoding="utf-8")
            if page is not None:
                with contextlib.suppress(Exception):
                    page.screenshot(
                        path=str(
                            directory
                            / "provider_operations_final.png"),
                        full_page=True, timeout=ASSERT_TIMEOUT_MS)

    @contextlib.contextmanager
    def browser_page(self):
        with sync_playwright() as playwright:
            browser = None
            context = None
            page = None
            try:
                browser = playwright.chromium.launch(
                    executable_path=self.executable, headless=True,
                    chromium_sandbox=True)
                context = browser.new_context(
                    base_url=self.base_url, service_workers="block",
                    viewport={"width": 1280, "height": 900})
                context.set_default_timeout(ASSERT_TIMEOUT_MS)
                context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
                context.route("**/*", self.handle_route)
                page = context.new_page()
                page.on("request", self.capture_request)
                page.on(
                    "pageerror",
                    lambda error: self.page_errors.append(str(error)))
                yield context, page
            finally:
                self.capture_artifacts(page)
                if context is not None:
                    with contextlib.suppress(Exception):
                        context.close()
                if browser is not None:
                    with contextlib.suppress(Exception):
                        browser.close()

    # -- inspection helpers (context GET only) ---------------------
    @staticmethod
    def tid(scope, name):
        return scope.get_by_test_id(name)

    def absolute_server_url(self, value):
        self.assertIsInstance(value, str)
        self.assertTrue(value)
        parts = urlsplit(value)
        if parts.scheme in ("http", "https"):
            self.assertEqual(
                "{0.scheme}://{0.netloc}".format(parts),
                self.base_origin)
            return value
        self.assertFalse(parts.scheme, value)
        return urljoin(self.base_url + "/", value)

    def api_json(self, context, path):
        response = context.request.get(
            self.base_url + path, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(response.status, 200, path)
        return response.json()

    def api_bytes(self, context, server_url):
        response = context.request.get(
            self.absolute_server_url(server_url),
            timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(response.status, 200, server_url)
        return response.body()

    def snapshot(self, context):
        return self.api_json(context, SNAPSHOT_PATH)

    def raw_result(self, context, request_id):
        payload = self.api_json(
            context, "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["raw_retention"], "retained")
        return payload["provider_result"]

    def request_status(self, context, request_id, source_id):
        payload = self.api_json(
            context, "/api/session/requests/"
            + quote(request_id, safe="") + "/status?source_id="
            + quote(source_id, safe=""))
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["source_id"], source_id)
        return payload

    @staticmethod
    def region_by_section(snapshot, section_id):
        return next(item for item in snapshot["regions"]
                    if item["external_section_id"] == section_id)

    @staticmethod
    def question_by_slot(snapshot, slot_id):
        return next(item for item in snapshot["questions"]
                    if item["slot_id"] == slot_id)

    @staticmethod
    def region_path(region):
        return ("/api/session/regions/"
                + quote(region["region_id"], safe=""))

    @staticmethod
    def question_path(question, suffix=""):
        return ("/api/session/questions/"
                + quote(question["question_id"], safe="") + suffix)

    def region_item(self, page, region):
        return page.locator(
            "[data-testid='region-item']"
            f"[data-region-id=\"{region['region_id']}\"]"
            "[data-external-section-id="
            f"\"{region['external_section_id']}\"]")

    def question_card(self, page, question):
        return page.locator(
            "[data-testid='question-card']"
            f"[data-question-id=\"{question['question_id']}\"]")

    def preview_item(self, page, question_id):
        return page.locator(
            "[data-testid='preview-item']"
            f"[data-question-id=\"{question_id}\"]")

    def capture_click(self, page, method, path, locator):
        with page.expect_response(
                lambda item: item.request.method == method
                and urlsplit(item.url).path == path,
                timeout=ASSERT_TIMEOUT_MS) as response_info:
            locator.click(timeout=ASSERT_TIMEOUT_MS)
        return response_info.value

    @staticmethod
    def open_disclosure(details):
        if details.get_attribute("open") is None:
            details.locator("summary").click(
                timeout=ASSERT_TIMEOUT_MS)

    def assert_provider_command(self, body, acknowledged_source_id):
        self.assertIsInstance(body, dict)
        self.assertEqual(set(body), {"request_id", "source_id"})
        self.assertIsInstance(body["request_id"], str)
        self.assertRegex(
            body["request_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(
            body["source_id"], acknowledged_source_id)
        return body["request_id"]

    def assert_envelope_agrees(
            self, body, request_id, acknowledged_source_id):
        self.assertIsInstance(body, dict)
        self.assertEqual(body["request_id"], request_id)
        self.assertIsInstance(body.get("snapshot"), dict)
        self.assertEqual(
            body["snapshot"]["source_id"], acknowledged_source_id)

    def assert_terminal_ok(self, snapshot, request_id, kind):
        matches = [item for item in snapshot["requests"]
                   if item["request_id"] == request_id]
        self.assertEqual(len(matches), 1, request_id)
        record = matches[0]
        self.assertEqual(record["kind"], kind)
        self.assertEqual(record["state"], "terminal")
        self.assertEqual(record["outcome"], "ok")
        return record

    def assert_status_agrees(
            self, context, snapshot, request_id, kind,
            acknowledged_source_id):
        record = self.assert_terminal_ok(snapshot, request_id, kind)
        payload = self.request_status(
            context, request_id, acknowledged_source_id)
        status_record = payload["request"]
        for field in ("request_id", "kind", "state", "outcome"):
            self.assertEqual(status_record[field], record[field])
        return record

    def test_exact_provider_commands_to_approved_preview(self):
        fx = self.fx
        with self.browser_page() as (context, page):
            response = page.goto(
                "/", wait_until="domcontentloaded",
                timeout=NAV_TIMEOUT_MS)
            self.observation["statuses"]["page"] = (
                response.status if response is not None else None)
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200, page.url)
            page.wait_for_load_state(
                "networkidle", timeout=NAV_TIMEOUT_MS)

            setup_form = self.tid(page, "setup-form")
            expect(setup_form).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            grade_input = page.get_by_label(
                "الصف الدراسي", exact=True)
            objective_input = page.get_by_label(
                "الهدف التعليمي", exact=True)
            page_number_input = page.get_by_label(
                "رقم الصفحة", exact=True)
            expect(grade_input).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)
            expect(objective_input).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)
            expect(page_number_input).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)
            grade_input.select_option(value=
                fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
            objective_input.fill(
                fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
            page_number_input.fill(
                "1", timeout=ASSERT_TIMEOUT_MS)
            page.get_by_label(
                "ملف صفحة المصدر", exact=True).set_input_files(
                    {"name": "source-page.png",
                     "mimeType": "image/png",
                     "buffer": self.png_bytes},
                    timeout=ASSERT_TIMEOUT_MS)
            start_button = page.get_by_role(
                "button", name="بدء تحليل الصفحة", exact=True)
            expect(start_button).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(start_button).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)

            start_checkpoint = len(self.requests_log)
            with page.expect_response(
                    lambda item: item.request.method == "POST"
                    and urlsplit(item.url).path == START_PATH,
                    timeout=ASSERT_TIMEOUT_MS) as start_info, \
                    page.expect_response(
                        lambda item: item.request.method == "POST"
                        and urlsplit(item.url).path
                        == SOURCE_ANALYSIS_PATH,
                        timeout=ASSERT_TIMEOUT_MS) as analysis_info:
                start_button.click(timeout=ASSERT_TIMEOUT_MS)
            start_response = start_info.value
            analysis_response = analysis_info.value
            self.observation["statuses"]["start"] = (
                start_response.status)
            self.observation["statuses"]["source_analysis"] = (
                analysis_response.status)
            bodies_seen = self.request_bodies(
                "POST", SOURCE_ANALYSIS_PATH, start_checkpoint)
            if bodies_seen:
                self.observation["provider_commands"][
                    "source_analysis"] = bodies_seen[0]

            self.assertEqual(
                start_response.status, 201,
                "Start must be acknowledged before the "
                "source-analysis repair assertion")
            metadata = start_response.json()
            self.assertIsInstance(metadata, dict)
            acknowledged_source_id = metadata["source_id"]
            self.assertIsInstance(acknowledged_source_id, str)
            self.assertTrue(acknowledged_source_id)
            self.observation["source_id"] = acknowledged_source_id
            self.observation["acknowledged_source_id"] = (
                acknowledged_source_id)

            original_bytes = self.api_bytes(
                context, metadata["original_url"])
            render_bytes = self.api_bytes(
                context, metadata["preview_url"])
            self.assertEqual(
                hashlib.sha256(original_bytes).hexdigest(),
                metadata["source_sha256"])
            self.assertEqual(
                hashlib.sha256(render_bytes).hexdigest(),
                metadata["render_sha256"])
            self.assertEqual(original_bytes, self.png_bytes)

            source_reference = self.tid(page, "source-reference")
            expect(source_reference).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "source-preview")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            self.open_disclosure(
                self.tid(page, "source-details"))
            expect(self.tid(page, "source-id")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "source-id")).to_have_text(
                acknowledged_source_id, timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "source-sha256")).to_have_text(
                metadata["source_sha256"],
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "render-sha256")).to_have_text(
                metadata["render_sha256"],
                timeout=ASSERT_TIMEOUT_MS)
            self.mark_phase(
                "start_acknowledged_and_page_bytes_checked")

            # First repair point: on the unchanged browser the
            # captured actual body above is the obsolete empty
            # object, so this is a genuine 400-versus-201 failure.
            self.assertEqual(
                analysis_response.status, 201,
                "first repair point: source-analysis must accept "
                "the exact provider command")

            source_bodies = self.request_bodies(
                "POST", SOURCE_ANALYSIS_PATH, start_checkpoint)
            self.assertEqual(len(source_bodies), 1)
            source_command = source_bodies[0]
            self.observation["provider_commands"][
                "source_analysis"] = source_command
            source_request_id = self.assert_provider_command(
                source_command, acknowledged_source_id)
            analysis_body = analysis_response.json()
            if isinstance(analysis_body, dict):
                self.observation["response_request_ids"][
                    "source_analysis"] = analysis_body.get(
                        "request_id")
            self.assert_envelope_agrees(
                analysis_body, source_request_id,
                acknowledged_source_id)

            snapshot = self.snapshot(context)
            self.assertEqual(
                snapshot["source_id"], acknowledged_source_id)
            self.assertEqual(snapshot["grade"], fx["GRADE"])
            self.assertEqual(snapshot["objective"], fx["OBJECTIVE"])
            self.assertTrue(snapshot["source_analysis_terminal"])
            self.assertFalse(snapshot["can_generate"])
            self.assertEqual(snapshot["included_count"], 0)
            self.assertEqual(snapshot["withheld_count"], 2)
            self.assertEqual(len(snapshot["regions"]), 2)
            self.assertEqual(snapshot["questions"], [])
            self.assertEqual(snapshot["preview_items"], [])
            source_record = self.assert_status_agrees(
                context, snapshot, source_request_id, "source",
                acknowledged_source_id)
            self.observation["request_record_ids"][
                "source_analysis"] = source_record["request_id"]

            region_a = self.region_by_section(
                snapshot, "synthetic-section-river")
            region_b = self.region_by_section(
                snapshot, "synthetic-section-trees")
            for region, raw_text in (
                    (region_a, fx["RAW_A"]),
                    (region_b, fx["RAW_B"])):
                self.assertEqual(
                    region["request_id"], source_request_id)
                self.assertEqual(region["inclusion"], "unreviewed")
                self.assertEqual(region["raw_text"], raw_text)
                self.assertEqual(region["current_text"], raw_text)

            source_raw = self.raw_result(
                context, source_request_id)
            self.assertEqual(source_raw["outcome"], "ok")
            actual_model_name = source_raw["model_name"]
            self.assertIsInstance(actual_model_name, str)
            self.assertTrue(actual_model_name)
            self.assertEqual(
                source_raw["raw"],
                {"sections": [
                    {"section_id": "synthetic-section-river",
                     "kind": "body", "text": fx["RAW_A"],
                     "review_reasons": ["synthetic-flag-river"]},
                    {"section_id": "synthetic-section-trees",
                     "kind": "body", "text": fx["RAW_B"],
                     "review_reasons": ["synthetic-flag-trees"]}],
                 "page_continues": False,
                 "review_notes": ["synthetic note"]})
            expect(self.tid(page, "source-model-name")
                   ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "source-model-name")
                   ).to_have_text(
                       actual_model_name,
                       timeout=ASSERT_TIMEOUT_MS)

            for region in (region_a, region_b):
                item = self.region_item(page, region)
                expect(item).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    item.get_attribute("data-region-id"),
                    region["region_id"])
                self.assertEqual(
                    item.get_attribute("data-external-section-id"),
                    region["external_section_id"])
                expect(self.tid(item, "region-current-text-input")
                       ).to_have_value(
                           region["current_text"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-current-label-input")
                       ).to_have_value(
                           region["current_label"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-inclusion-input")
                       ).to_have_value(
                           "unreviewed", timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-inclusion-state")
                       ).to_have_text(
                           INCLUSION_ARABIC["unreviewed"],
                           timeout=ASSERT_TIMEOUT_MS)
                self.open_disclosure(
                    self.tid(item, "region-original"))
                expect(self.tid(item, "region-raw-text")
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-raw-text")
                       ).to_have_text(
                           region["raw_text"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-raw-label")
                       ).to_have_text(
                           region["raw_label"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-request-id")
                       ).to_have_text(
                           region["request_id"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-model-name")
                       ).to_have_text(
                           self.raw_result(
                               context, region["request_id"]
                           )["model_name"],
                           timeout=ASSERT_TIMEOUT_MS)
            self.mark_phase("source_analysis_command_checked")

            labels_before = {
                region["region_id"]: region["current_label"]
                for region in (region_a, region_b)}
            updated_regions = {}
            for region, edited_text in (
                    (region_a, fx["EDITED_A"]),
                    (region_b, fx["EDITED_B"])):
                label_before = labels_before[region["region_id"]]
                item = self.region_item(page, region)
                expect(item).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                checkpoint = len(self.requests_log)
                self.tid(item, "region-current-text-input").fill(
                    edited_text, timeout=ASSERT_TIMEOUT_MS)
                self.tid(item, "region-inclusion-input"
                         ).select_option(
                             "included",
                             timeout=ASSERT_TIMEOUT_MS)
                review_path = self.region_path(region)
                review_response = self.capture_click(
                    page, "PUT", review_path,
                    self.tid(item, "region-review-commit"))
                status_key = (
                    "region_review:"
                    + region["external_section_id"])
                self.observation["statuses"][status_key] = (
                    review_response.status)
                self.assertEqual(review_response.status, 200)
                review_bodies = self.request_bodies(
                    "PUT", review_path, checkpoint)
                self.assertEqual(len(review_bodies), 1)
                self.assertEqual(
                    review_bodies[0],
                    {"inclusion": "included",
                     "current_text": edited_text,
                     "current_label": label_before})
                snapshot = self.snapshot(context)
                self.assertEqual(
                    snapshot["source_id"], acknowledged_source_id)
                updated = self.region_by_section(
                    snapshot, region["external_section_id"])
                self.assertEqual(
                    updated["region_id"], region["region_id"])
                self.assertEqual(
                    updated["current_text"], edited_text)
                self.assertEqual(
                    updated["raw_text"], region["raw_text"])
                self.assertEqual(
                    updated["inclusion"], "included")
                self.assertEqual(
                    updated["current_label"], label_before)
                updated_item = self.region_item(page, updated)
                expect(updated_item).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    updated_item, "region-current-text-input")
                    ).to_have_value(
                        edited_text, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    updated_item, "region-current-label-input")
                    ).to_have_value(
                        label_before, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    updated_item, "region-inclusion-input")
                    ).to_have_value(
                        "included", timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    updated_item, "region-inclusion-state")
                    ).to_have_text(
                        INCLUSION_ARABIC["included"],
                        timeout=ASSERT_TIMEOUT_MS)
                updated_regions[
                    region["external_section_id"]] = updated

            region_a = updated_regions["synthetic-section-river"]
            region_b = updated_regions["synthetic-section-trees"]
            self.assertTrue(snapshot["can_generate"])
            self.assertEqual(snapshot["included_count"], 2)
            self.assertEqual(snapshot["withheld_count"], 0)
            generate_button = page.get_by_role(
                "button", name="اعتماد المصدر وتوليد الأسئلة",
                exact=True)
            expect(generate_button).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(generate_button).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)
            self.mark_phase("source_review_checked")

            generation_checkpoint = len(self.requests_log)
            generation_response = self.capture_click(
                page, "POST", QUESTIONS_GENERATE_PATH,
                generate_button)
            self.observation["statuses"]["question_generation"] = (
                generation_response.status)
            bodies_seen = self.request_bodies(
                "POST", QUESTIONS_GENERATE_PATH,
                generation_checkpoint)
            if bodies_seen:
                self.observation["provider_commands"][
                    "question_generation"] = bodies_seen[0]
            self.assertEqual(generation_response.status, 201)
            generation_bodies = self.request_bodies(
                "POST", QUESTIONS_GENERATE_PATH,
                generation_checkpoint)
            self.assertEqual(len(generation_bodies), 1)
            generation_command = generation_bodies[0]
            self.observation["provider_commands"][
                "question_generation"] = generation_command
            question_request_id = self.assert_provider_command(
                generation_command, acknowledged_source_id)
            self.assertNotEqual(
                question_request_id, source_request_id)
            generation_body = generation_response.json()
            if isinstance(generation_body, dict):
                self.observation["response_request_ids"][
                    "question_generation"] = generation_body.get(
                        "request_id")
            self.assert_envelope_agrees(
                generation_body, question_request_id,
                acknowledged_source_id)

            snapshot = self.snapshot(context)
            self.assertEqual(
                snapshot["source_id"], acknowledged_source_id)
            self.assertEqual(len(snapshot["questions"]), 2)
            self.assertEqual(len(snapshot["slot_outcomes"]), 6)
            self.assertEqual(
                sorted(item["outcome"]
                       for item in snapshot["slot_outcomes"]),
                ["abstain"] * 4 + ["draft"] * 2)
            self.assertEqual(snapshot["preview_items"], [])
            question_record = self.assert_status_agrees(
                context, snapshot, question_request_id,
                "questions", acknowledged_source_id)
            self.observation["request_record_ids"][
                "question_generation"] = (
                    question_record["request_id"])
            self.assert_terminal_ok(
                snapshot, source_request_id, "source")

            question_raw = self.raw_result(
                context, question_request_id)
            self.assertEqual(question_raw["outcome"], "ok")
            self.assertEqual(
                question_raw["model_name"], actual_model_name)
            raw_items = question_raw["raw"]["items"]
            self.assertEqual(len(raw_items), 6)
            self.assertEqual(
                sum(1 for item in raw_items
                    if item["status"] == "draft"), 2)
            self.assertEqual(
                sum(1 for item in raw_items
                    if item["status"] == "abstain"), 4)
            raw_by_slot = {
                item["slot_id"]: item for item in raw_items}
            self.assertEqual(len(raw_by_slot), 6)

            question_a = self.question_by_slot(
                snapshot, "slot-remember")
            question_b = self.question_by_slot(
                snapshot, "slot-understand")
            expected_questions = (
                (question_a, region_a, fx["FACT_A"],
                 fx["QUESTION_1"], fx["GUIDANCE_1"],
                 fx["COGNITIVE_1"], "remember"),
                (question_b, region_b, fx["FACT_B"],
                 fx["QUESTION_2"], fx["GUIDANCE_2"],
                 fx["COGNITIVE_2"], "understand"))
            for (question, region, fact, text, guidance,
                    cognitive, level) in expected_questions:
                self.assertEqual(
                    question["request_id"], question_request_id)
                self.assertEqual(question["status"], "draft")
                self.assertEqual(question["blocks"], [])
                self.assertEqual(
                    question["original_question"], text)
                self.assertEqual(
                    question["current_question"], text)
                self.assertEqual(
                    question["original_answer_guidance"], guidance)
                self.assertEqual(
                    question["current_answer_guidance"], guidance)
                self.assertEqual(
                    question["original_cognitive_explanation"],
                    cognitive)
                self.assertEqual(
                    question["current_cognitive_explanation"],
                    cognitive)
                self.assertEqual(
                    question["generation_grade"], fx["GRADE"])
                self.assertEqual(
                    question["current_grade"], fx["GRADE"])
                self.assertEqual(
                    question["generation_objective"],
                    fx["OBJECTIVE"])
                self.assertEqual(
                    question["current_objective"], fx["OBJECTIVE"])
                self.assertEqual(question["model_level"], level)
                self.assertEqual(question["current_level"], level)
                self.assertEqual(
                    question["question_author"], "model")
                self.assertEqual(
                    question["supporting_region_ids"],
                    [region["region_id"]])
                self.assertEqual(
                    question["anchors"],
                    [[region["region_id"], fact]])
                raw_item = raw_by_slot[question["slot_id"]]
                self.assertEqual(raw_item["status"], "draft")
                self.assertEqual(raw_item["level"], level)
                self.assertEqual(raw_item["question"], text)
                self.assertEqual(
                    raw_item["answer_guidance"], guidance)
                self.assertEqual(
                    raw_item["cognitive_explanation"], cognitive)
                self.assertEqual(
                    raw_item["evidence"],
                    [{"region_id": region["region_id"],
                      "quote": fact}])

            for question in (question_a, question_b):
                card = self.question_card(page, question)
                expect(card).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    card.get_attribute("data-slot-id"),
                    question["slot_id"])
                expect(self.tid(card, "question-heading")
                       ).to_have_text(
                           question["current_question"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-status")
                       ).to_have_text(
                           STATUS_ARABIC[question["status"]],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-decision")
                       ).to_have_text(
                           DECISION_ARABIC[question["status"]],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-level")
                       ).to_have_text(
                           BLOOM_ARABIC[question["current_level"]],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-guidance")
                       ).to_have_text(
                           question["current_answer_guidance"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-cognitive")
                       ).to_have_text(
                           question[
                               "current_cognitive_explanation"],
                           timeout=ASSERT_TIMEOUT_MS)
                facts = self.tid(card, "question-evidence-fact")
                expect(facts).to_have_count(
                    len(question["anchors"]))
                for fact_locator, anchor in zip(
                        facts.all(), question["anchors"]):
                    self.assertEqual(
                        fact_locator.get_attribute(
                            "data-region-id"), anchor[0])
                    expect(fact_locator).to_have_text(
                        anchor[1], timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-block")
                       ).to_have_count(len(question["blocks"]))
                expect(self.tid(card, "question-request-id")
                       ).to_have_text(
                           question["request_id"],
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-model-name")
                       ).to_have_text(
                           self.raw_result(
                               context, question["request_id"]
                           )["model_name"],
                           timeout=ASSERT_TIMEOUT_MS)
            self.mark_phase("question_generation_command_checked")

            preview_stage = self.tid(
                page, "stage-teacher-preview")
            expect(preview_stage).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "preview-empty")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "preview-empty")).to_have_text(
                PREVIEW_EMPTY_TEXT, timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "preview-item")).to_have_count(0)
            self.assertEqual(
                self.api_json(context, PREVIEW_PATH),
                {"items": []})
            self.assertEqual(snapshot["preview_items"], [])
            self.mark_phase("empty_preview_checked")

            approved_before = question_a
            other_before = question_b
            approve_path = self.question_path(
                approved_before, "/approve")
            approve_response = self.capture_click(
                page, "POST", approve_path,
                self.tid(
                    self.question_card(page, approved_before),
                    "question-approve"))
            self.observation["statuses"]["question_approve"] = (
                approve_response.status)
            self.assertEqual(approve_response.status, 200)

            snapshot = self.snapshot(context)
            self.assertEqual(
                snapshot["source_id"], acknowledged_source_id)
            approved = self.question_by_slot(
                snapshot, "slot-remember")
            other = self.question_by_slot(
                snapshot, "slot-understand")
            self.assertEqual(
                approved["question_id"],
                approved_before["question_id"])
            self.assertEqual(
                other["question_id"], other_before["question_id"])
            self.assertEqual(approved["status"], "approved")
            self.assertEqual(other["status"], "draft")
            self.assertEqual(
                approved["current_question"], fx["QUESTION_1"])
            self.assertEqual(
                approved["original_question"], fx["QUESTION_1"])
            self.assertEqual(approved["current_level"], "remember")
            self.assertEqual(
                approved["anchors"],
                [[region_a["region_id"], fx["FACT_A"]]])
            preview = self.api_json(context, PREVIEW_PATH)["items"]
            self.assertEqual(snapshot["preview_items"], preview)
            self.assertEqual(len(preview), 1)
            preview_ids = [item["question_id"] for item in preview]
            self.assertEqual(
                preview_ids, [approved["question_id"]])
            self.assertNotIn(other["question_id"], preview_ids)
            entry = preview[0]
            self.assertEqual(
                entry["question"], approved["current_question"])
            self.assertEqual(entry["question"], fx["QUESTION_1"])
            self.assertEqual(
                entry["bloom_level"], approved["current_level"])
            self.assertEqual(entry["bloom_level"], "remember")
            self.assertEqual(entry["author"], "model")
            self.assertEqual(
                entry["answer_guidance"], fx["GUIDANCE_1"])
            self.assertEqual(
                entry["original_answer_guidance"], fx["GUIDANCE_1"])
            self.assertEqual(len(entry["resolved_anchors"]), 1)
            self.assertEqual(
                entry["resolved_anchors"][0][:2],
                [region_a["region_id"], fx["FACT_A"]])

            expect(preview_stage).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "preview-empty")).to_be_hidden(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "preview-item")).to_have_count(1)
            expect(self.preview_item(
                page, other["question_id"])).to_have_count(0)
            item = self.preview_item(
                page, approved["question_id"])
            expect(item).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-question")
                   ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-question")
                   ).to_have_text(
                       entry["question"],
                       timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-author")).to_have_text(
                CURRENT_AUTHOR_ARABIC[entry["author"]],
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-decision")
                   ).to_have_text(
                       "قرار المعلم: معتمد",
                       timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-level")).to_have_text(
                BLOOM_ARABIC[entry["bloom_level"]],
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-request-id")
                   ).to_have_text(
                       approved["request_id"],
                       timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-model-name")
                   ).to_have_text(
                       self.raw_result(
                           context, approved["request_id"]
                       )["model_name"],
                       timeout=ASSERT_TIMEOUT_MS)

            self.open_disclosure(
                self.tid(item, "preview-checking"))
            expect(self.tid(item, "preview-guidance")
                   ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-guidance")
                   ).to_have_text(
                       entry["answer_guidance"],
                       timeout=ASSERT_TIMEOUT_MS)
            preview_facts = self.tid(
                item, "preview-evidence-fact")
            expect(preview_facts).to_have_count(
                len(entry["resolved_anchors"]))
            for fact_locator, anchor in zip(
                    preview_facts.all(),
                    entry["resolved_anchors"]):
                expect(fact_locator).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    fact_locator.get_attribute("data-region-id"),
                    anchor[0])
                expect(self.tid(
                    fact_locator, "preview-evidence-quote")
                    ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    fact_locator, "preview-evidence-quote")
                    ).to_have_text(
                        anchor[1], timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    fact_locator, "preview-evidence-text")
                    ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(
                    fact_locator, "preview-evidence-text")
                    ).to_have_text(
                        anchor[2], timeout=ASSERT_TIMEOUT_MS)

            self.open_disclosure(
                self.tid(item, "preview-original"))
            expect(self.tid(item, "preview-original-text")
                   ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-original-text")
                   ).to_have_text(
                       approved["original_question"],
                       timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-original-guidance")
                   ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(item, "preview-original-guidance")
                   ).to_have_text(
                       entry["original_answer_guidance"],
                       timeout=ASSERT_TIMEOUT_MS)

            approved_card = self.question_card(page, approved)
            expect(self.tid(approved_card, "question-status")
                   ).to_have_text(
                       STATUS_ARABIC["approved"],
                       timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(approved_card, "question-decision")
                   ).to_have_text(
                       DECISION_ARABIC["approved"],
                       timeout=ASSERT_TIMEOUT_MS)
            self.open_disclosure(
                self.tid(approved_card, "question-original"))
            expect(self.tid(
                approved_card, "question-original-text")
                ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(
                approved_card, "question-original-text")
                ).to_have_text(
                    fx["QUESTION_1"], timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(
                approved_card, "question-original-guidance")
                ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(
                approved_card, "question-original-guidance")
                ).to_have_text(
                    fx["GUIDANCE_1"], timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(
                approved_card, "question-original-cognitive")
                ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(
                approved_card, "question-original-cognitive")
                ).to_have_text(
                    fx["COGNITIVE_1"], timeout=ASSERT_TIMEOUT_MS)

            expect(self.tid(page, "teacher-preview-warning")
                   ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "teacher-preview-warning")
                   ).to_have_text(
                       TEACHER_PREVIEW_WARNING,
                       timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "non-durable-warning")
                   ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "non-durable-warning")
                   ).to_have_text(
                       NON_DURABLE_WARNING,
                       timeout=ASSERT_TIMEOUT_MS)
            self.mark_phase("approved_preview_checked")

            final_original_bytes = self.api_bytes(
                context, metadata["original_url"])
            self.assertEqual(final_original_bytes, self.png_bytes)
            self.assertEqual(
                hashlib.sha256(final_original_bytes).hexdigest(),
                metadata["source_sha256"])
            final_snapshot = self.snapshot(context)
            self.assertEqual(
                final_snapshot["source_id"],
                acknowledged_source_id)
            self.assertEqual(
                final_snapshot["preview_items"], preview)
            self.assert_terminal_ok(
                final_snapshot, source_request_id, "source")
            self.assert_terminal_ok(
                final_snapshot, question_request_id, "questions")
            expect(self.tid(page, "source-id")).to_have_text(
                acknowledged_source_id,
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(
                self.provider_post_count(SOURCE_ANALYSIS_PATH), 1)
            self.assertEqual(
                self.provider_post_count(
                    QUESTIONS_GENERATE_PATH), 1)
            self.assertEqual(self.external_urls, [])
            self.assertEqual(self.page_errors, [])
            self.mark_phase("final_reconciliation_checked")
