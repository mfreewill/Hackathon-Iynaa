"""Correction-boundary safety tests for the setup/source client.

The test harness serves the
app with ``provider=None`` on a random 127.0.0.1-only origin and
runs this module under Playwright Python with required
``BROWSER_BASE_URL``/``BROWSER_EXECUTABLE`` and optional
``BROWSER_ARTIFACT_DIR`` (screenshots and cookie-free observations
go only there). Fresh contexts, explicit installed executable,
``chromium_sandbox=True``, service workers blocked, bounded waits,
and every HTTP(S) request outside the base origin is aborted and
fails the test. One controlled route handler either forwards a
real Start request with ``route.fetch`` (redirects/retries 0) and
then aborts browser delivery of its actual response, or aborts a
Start without forwarding it; no API/HTML response is ever
fulfilled or mocked and no client business logic is injected.
Actual server state is read through the context's shared-cookie
request client, keeping the two response-loss directions distinct.
"""
import contextlib
import hashlib
import io
import json
import os
import pathlib
import unittest
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from playwright.sync_api import expect, sync_playwright

NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000
GRADE = "الصف الخامس"
OBJECTIVE = "  هَدَفٌ: قِرَاءَةُ النَّصِّ ١٢٣ \U0001d11e  "
REPLACEMENT_WARNING = (
    "الضغط على «بدء تحليل الصفحة» مجددًا سيستبدل مساحة العمل الحالية في هذه الجلسة — "
    "مقاطعها وأسئلتها وقراراتها ومعاينتها — ويبدأ فارغًا، حتى لو اخترت الملف والصفحة نفسيهما.")
UNAVAILABLE_MESSAGE = (
    "خدمة تحليل المصدر غير متصلة في هذا التشغيل. لم يُقترح أيّ "
    "مقطع أو سؤال، وتبقى صفحتك وإعداداتك كما هي.")
INVALID_FILE_MESSAGE = (
    "تعذّرت قراءة ملف الصفحة المختار. لم يتغيّر المصدر الحالي؛ "
    "اختر ملف صفحة صالحًا ثم حاول مجددًا.")
UNKNOWN_STATUS = (
    "نتيجة الطلب غير معروفة: لا يمكن التحقق من المصدر المعروض في هذه الزيارة. "
    "أوقف العمل هنا؛ لإعادة البدء حدّث المتصفح عمدًا وابدأ من جديد، مع العلم أن هذا العمل غير المحفوظ قد يُفقد.")
UNKNOWN_SOURCE_NOTE = (
    "لا يمكن التحقق من المصدر المعروض بعد طلب نتيجته غير معروفة، لذا أُخفيت معاينته وروابطه في هذه الزيارة.")
INVALID_BYTES = b"synthetic invalid image bytes - not a page image"


def tiny_png(rgb=(200, 30, 40)):
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (*rgb, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


class SetupSourceSafetyTests(unittest.TestCase):
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
        self.start_fault = None
        self.forwarded_start = None

    # -- routing and recording ------------------------------------
    def handle_route(self, route, request):
        parts = urlsplit(request.url)
        if parts.scheme in ("data", "blob"):
            route.continue_()
            return
        if "{0.scheme}://{0.netloc}".format(parts) != self.base_origin:
            self.external_urls.append(request.url)
            route.abort("blockedbyclient")
            return
        if (self.start_fault and request.method == "POST"
                and parts.path == "/api/session/start"):
            mode = self.start_fault
            self.start_fault = None
            if mode == "forward_then_abort":
                response = route.fetch(max_redirects=0, max_retries=0)
                self.forwarded_start = {
                    "status": response.status,
                    "json": (response.json()
                             if response.status == 201 else None)}
                with contextlib.suppress(Exception):
                    response.dispose()
            route.abort("internetdisconnected")
            return
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
        with contextlib.suppress(Exception):
            buffer = request.post_data_buffer
            if buffer:
                entry["body_sha256"] = hashlib.sha256(buffer).hexdigest()
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
            name = self._testMethodName
            page.screenshot(
                path=str(directory / f"{name}-final.png"),
                full_page=True, timeout=ASSERT_TIMEOUT_MS)
            observations = {
                "external_urls": self.external_urls,
                "page_errors": self.page_errors,
                "mutations": [
                    {"method": m, "path": p}
                    for m, p in self.mutations_since(0)],
                "responses": self.responses_log,
                "forwarded_start_status": (
                    self.forwarded_start
                    and self.forwarded_start["status"])}
            (directory / f"{name}.json").write_text(
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

    # -- shared journey pieces -------------------------------------
    @staticmethod
    def tid(page, name):
        return page.get_by_test_id(name)

    def api_json(self, context, path):
        response = context.request.get(
            self.base_url + path, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(response.status, 200, path)
        return response.json()

    def primary(self, page):
        return page.get_by_role(
            "button", name="بدء تحليل الصفحة", exact=True)

    def fields(self, page):
        return {
            "file": page.get_by_label("ملف صفحة المصدر", exact=True),
            "grade": page.get_by_label("الصف الدراسي", exact=True),
            "objective": page.get_by_label("الهدف التعليمي", exact=True),
            "page_number": page.get_by_label("رقم الصفحة", exact=True)}

    def set_file(self, page, name, payload):
        self.fields(page)["file"].set_input_files({
            "name": name, "mimeType": "image/png", "buffer": payload})

    def baseline(self, context, page, png, filename="source-page.png"):
        response = page.goto(
            "/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        self.assertIsNotNone(response)
        self.assertEqual(response.status, 200, page.url)
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
        warning = self.tid(page, "replacement-warning")
        expect(warning).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
        fields = self.fields(page)
        fields["grade"].select_option(value=GRADE)
        fields["objective"].fill(OBJECTIVE)
        fields["page_number"].fill("1")
        self.set_file(page, filename, png)
        with page.expect_response(
                lambda item: urlsplit(item.url).path
                == "/api/session/source-analysis"
                and item.status == 503,
                timeout=ASSERT_TIMEOUT_MS):
            self.primary(page).click()
        status = self.tid(page, "workspace-status")
        expect(status).to_have_text(
            UNAVAILABLE_MESSAGE, timeout=ASSERT_TIMEOUT_MS)
        expect(self.primary(page)).to_be_enabled(
            timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(page, "source-reference")).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        expect(warning).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(warning).to_have_text(
            REPLACEMENT_WARNING, timeout=ASSERT_TIMEOUT_MS)
        metadata = self.api_json(context, "/api/session/page")
        self.assertEqual(
            metadata["source_sha256"],
            hashlib.sha256(png).hexdigest())
        snapshot = self.api_json(context, "/api/session/snapshot")
        self.assertEqual(snapshot["regions"], [])
        self.assertEqual(snapshot["questions"], [])
        self.assertEqual(snapshot["grade"], GRADE)
        self.assertEqual(snapshot["objective"], OBJECTIVE)
        return {"metadata": metadata, "snapshot": snapshot}

    def assert_no_external_or_page_errors(self):
        self.assertEqual(self.external_urls, [])
        self.assertEqual(self.page_errors, [])

    def assert_unknown_lock(self, page, filename, size):
        expect(self.tid(page, "workspace-status")).to_have_text(
            UNKNOWN_STATUS, timeout=ASSERT_TIMEOUT_MS)
        setup_error = self.tid(page, "setup-error")
        expect(setup_error).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(setup_error).to_have_text(
            UNKNOWN_STATUS, timeout=ASSERT_TIMEOUT_MS)
        source_empty = self.tid(page, "source-empty")
        expect(source_empty).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(source_empty).to_have_text(
            UNKNOWN_SOURCE_NOTE, timeout=ASSERT_TIMEOUT_MS)
        primary = self.primary(page)
        expect(primary).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(page, "source-reference")).to_be_hidden(
            timeout=ASSERT_TIMEOUT_MS)
        self.assertIsNone(
            self.tid(page, "source-preview").get_attribute("src"))
        self.assertIsNone(
            self.tid(page, "source-original-link").get_attribute("href"))
        fields = self.fields(page)
        expect(fields["grade"]).to_have_value(GRADE)
        expect(fields["objective"]).to_have_value(OBJECTIVE)
        expect(fields["page_number"]).to_have_value("1")
        self.assertEqual(
            fields["file"].evaluate(
                "(el) => el.files.length "
                "? [el.files[0].name, el.files[0].size] : []"),
            [filename, size])
        checkpoint = len(self.requests_log)
        fields["grade"].press("Enter")
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
        self.assertEqual(self.mutations_since(checkpoint), [])
        expect(primary).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(page, "workspace-status")).to_have_text(
            UNKNOWN_STATUS, timeout=ASSERT_TIMEOUT_MS)

    def test_persistent_warning_and_original_link_geometry(self):
        with self.browser_page() as (context, page):
            before = self.baseline(context, page, tiny_png())
            link = self.tid(page, "source-original-link")
            for width, height in ((1280, 900), (390, 844)):
                page.set_viewport_size(
                    {"width": width, "height": height})
                if width < 1024:
                    self.tid(page, "pane-tab-source").click()
                expect(link).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                box = link.bounding_box()
                self.assertIsNotNone(box)
                self.assertGreaterEqual(box["width"], 44)
                self.assertGreaterEqual(box["height"], 44)
            page.set_viewport_size({"width": 1280, "height": 900})
            warning = self.tid(page, "replacement-warning")
            expect(warning).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(warning).to_have_text(
                REPLACEMENT_WARNING, timeout=ASSERT_TIMEOUT_MS)

            # The visible warning precedes an unchanged-source Start.
            checkpoint = len(self.requests_log)
            with page.expect_response(
                    lambda item: urlsplit(item.url).path
                    == "/api/session/source-analysis"
                    and item.status == 503,
                    timeout=ASSERT_TIMEOUT_MS):
                self.primary(page).click()
            self.assertEqual(self.mutations_since(checkpoint), [
                ("PUT", "/api/session/settings"),
                ("POST", "/api/session/start"),
                ("POST", "/api/session/source-analysis")])
            again = self.api_json(context, "/api/session/page")
            self.assertEqual(
                again["source_sha256"],
                before["metadata"]["source_sha256"])
            expect(warning).to_be_visible(timeout=ASSERT_TIMEOUT_MS)

            # It also precedes a page-only change's submission, and
            # preparing that change sends nothing.
            checkpoint = len(self.requests_log)
            self.fields(page)["page_number"].fill("2")
            expect(warning).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(warning).to_have_text(
                REPLACEMENT_WARNING, timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(checkpoint), [])
            self.assert_no_external_or_page_errors()

    def test_start_response_loss_after_commit_locks_unknown(self):
        with self.browser_page() as (context, page):
            self.baseline(context, page, tiny_png())
            replacement = tiny_png((20, 80, 160))
            self.set_file(page, "replacement-page.png", replacement)
            checkpoint = len(self.requests_log)
            self.start_fault = "forward_then_abort"
            self.primary(page).click()
            expect(self.tid(page, "workspace-status")).to_have_text(
                UNKNOWN_STATUS, timeout=ASSERT_TIMEOUT_MS)
            self.assertIsNotNone(self.forwarded_start)
            self.assertEqual(self.forwarded_start["status"], 201)
            forwarded = self.forwarded_start["json"]
            self.assertEqual(
                forwarded["source_sha256"],
                hashlib.sha256(replacement).hexdigest())
            self.assertEqual(self.mutations_since(checkpoint), [
                ("PUT", "/api/session/settings"),
                ("POST", "/api/session/start")])
            # The server really committed the replacement even
            # though the browser never received its response.
            actual = self.api_json(context, "/api/session/page")
            self.assertEqual(actual["source_id"], forwarded["source_id"])
            self.assertEqual(
                actual["source_sha256"], forwarded["source_sha256"])
            self.assert_unknown_lock(
                page, "replacement-page.png", len(replacement))
            self.assert_no_external_or_page_errors()

    def test_start_abort_without_forwarding_also_locks_unknown(self):
        with self.browser_page() as (context, page):
            before = self.baseline(context, page, tiny_png())
            replacement = tiny_png((30, 120, 70))
            self.set_file(page, "unforwarded-page.png", replacement)
            checkpoint = len(self.requests_log)
            self.start_fault = "abort"
            self.primary(page).click()
            expect(self.tid(page, "workspace-status")).to_have_text(
                UNKNOWN_STATUS, timeout=ASSERT_TIMEOUT_MS)
            self.assertIsNone(self.forwarded_start)
            self.assertEqual(self.mutations_since(checkpoint), [
                ("PUT", "/api/session/settings"),
                ("POST", "/api/session/start")])
            # Distinct from the forwarded case: the server still
            # holds the first source, yet the browser must stop.
            self.assertEqual(
                self.api_json(context, "/api/session/page"),
                before["metadata"])
            self.assertEqual(
                self.api_json(context, "/api/session/snapshot"),
                before["snapshot"])
            self.assert_unknown_lock(
                page, "unforwarded-page.png", len(replacement))
            self.assert_no_external_or_page_errors()

    def test_definite_refusal_and_default_unavailable_stay_usable(
            self):
        with self.browser_page() as (context, page):
            before = self.baseline(context, page, tiny_png())
            self.set_file(page, "invalid-page.png", INVALID_BYTES)
            responses_at = len(self.responses_log)
            self.primary(page).click()
            setup_error = self.tid(page, "setup-error")
            expect(setup_error).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(setup_error).to_have_text(
                INVALID_FILE_MESSAGE, timeout=ASSERT_TIMEOUT_MS)
            start_statuses = [
                entry["status"] for entry
                in self.responses_log[responses_at:]
                if entry["method"] == "POST"
                and entry["path"] == "/api/session/start"]
            self.assertEqual(len(start_statuses), 1)
            self.assertIn(start_statuses[0], (400, 415))
            expect(self.primary(page)).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "workspace-status")).to_have_text(
                UNAVAILABLE_MESSAGE, timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "source-reference")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            self.assertIsNotNone(
                self.tid(page, "source-preview").get_attribute("src"))
            self.assertIsNotNone(
                self.tid(page, "source-original-link")
                .get_attribute("href"))
            self.assertEqual(
                self.api_json(context, "/api/session/page"),
                before["metadata"])
            self.assertEqual(
                self.api_json(context, "/api/session/snapshot"),
                before["snapshot"])

            # A later deliberate valid Start still works and still
            # reaches only the honest disconnected outcome.
            replacement = tiny_png((90, 40, 160))
            self.set_file(page, "recovered-page.png", replacement)
            with page.expect_response(
                    lambda item: urlsplit(item.url).path
                    == "/api/session/source-analysis"
                    and item.status == 503,
                    timeout=ASSERT_TIMEOUT_MS):
                self.primary(page).click()
            expect(self.tid(page, "workspace-status")).to_have_text(
                UNAVAILABLE_MESSAGE, timeout=ASSERT_TIMEOUT_MS)
            expect(self.primary(page)).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(
                self.api_json(context, "/api/session/page")
                ["source_sha256"],
                hashlib.sha256(replacement).hexdigest())
            self.assert_no_external_or_page_errors()


if __name__ == "__main__":
    unittest.main()
