"""Standalone real-browser session-capacity refusal test.

Run this module alone
against a fresh dedicated server, because the test fills
the server's finite in-memory session registry and would
contaminate sibling browser tests on a shared server. Nothing
here mutates or clears backend state through any test-only route;
capacity is filled only with real ``POST /api/sessions`` calls.

Contract: required ``BROWSER_BASE_URL`` (the actual served
origin) and ``BROWSER_EXECUTABLE`` (installed browser file), plus
optional ``BROWSER_ARTIFACT_DIR`` for one screenshot and a
token-free observations JSON. Seeding uses a separate Playwright
API request context with the exact supplied ``Origin``, at most
16 attempts, discarding every token-bearing 201 body unread and
retaining only statuses and a count. The browser context is
fresh, with ``chromium_sandbox=True`` and service workers blocked;
any HTTP(S) request outside the base origin is aborted and fails
the test. No response is fulfilled or mocked, no business logic
is injected, and no capacity recovery is asserted: this server
releases no session slots here.
"""
import contextlib
import hashlib
import io
import json
import os
import pathlib
import unittest
from urllib.parse import urlsplit

from PIL import Image
from playwright.sync_api import expect, sync_playwright

NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000
MAX_SEED_ATTEMPTS = 16

GRADE = "  الصَّفُّ ٥  "
OBJECTIVE = "  هَدَفٌ: قِرَاءَةُ النَّصِّ ١٢٣  "
UNKNOWN_PHRASE = "نتيجة الطلب غير معروفة"
BOOT_FAILURE_STATUS = (
    "تعذّر بدء جلسة هذا المتصفح. تحقّق من الخادم المحلي ثم اضغط "
    "«بدء تحليل الصفحة» للمحاولة بضغطة جديدة.")
DEFINITE_FAILURE_ERROR = (
    "تعذّر إكمال هذه الخطوة. لم نُعد المحاولة تلقائيًا؛ راجع ما هو "
    "ظاهر ثم حاول مجددًا بضغطة جديدة.")


def tiny_png():
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (200, 30, 40, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


class SessionCapacityBrowserTests(unittest.TestCase):
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
        self.seeded_created = 0

    def seed_full_registry(self, playwright):
        api = playwright.request.new_context(
            base_url=self.base_url,
            extra_http_headers={"Origin": self.base_url})
        try:
            refusal_status = None
            refusal_json = None
            for _attempt in range(MAX_SEED_ATTEMPTS):
                response = api.post(
                    "/api/sessions", data="{}",
                    headers={"Content-Type": "application/json"},
                    timeout=ASSERT_TIMEOUT_MS)
                if response.status == 201:
                    self.seeded_created += 1
                    response.dispose()
                    continue
                refusal_status = response.status
                refusal_json = response.json()
                response.dispose()
                break
            self.assertGreaterEqual(
                self.seeded_created, 1,
                "no real session was created while seeding")
            self.assertEqual(refusal_status, 503)
            self.assertEqual(
                refusal_json, {"detail": "session limit reached"})
        finally:
            api.dispose()

    def handle_route(self, route, request):
        parts = urlsplit(request.url)
        if parts.scheme in ("data", "blob"):
            route.continue_()
            return
        if "{0.scheme}://{0.netloc}".format(parts) != self.base_origin:
            self.external_urls.append(request.url)
            route.abort("blockedbyclient")
            return
        route.continue_()

    def capture_request(self, request):
        parts = urlsplit(request.url)
        entry = {"method": request.method, "path": parts.path}
        with contextlib.suppress(Exception):
            if request.post_data_buffer:
                entry["body_sha256"] = hashlib.sha256(
                    request.post_data_buffer).hexdigest()
        self.requests_log.append(entry)

    def capture_response(self, response):
        self.responses_log.append({
            "method": response.request.method,
            "path": urlsplit(response.url).path,
            "status": response.status, "response": response})

    def mutations_since(self, index):
        return [(entry["method"], entry["path"])
                for entry in self.requests_log[index:]
                if entry["method"] in ("POST", "PUT", "PATCH")]

    def browser_session_responses(self):
        return [entry for entry in self.responses_log
                if entry["method"] == "POST"
                and entry["path"] == "/api/sessions"]

    def capture_artifacts(self, page):
        artifact_dir = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        if not artifact_dir or page is None:
            return
        with contextlib.suppress(Exception):
            directory = pathlib.Path(artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(directory / "session-capacity-final.png"),
                full_page=True, timeout=ASSERT_TIMEOUT_MS)
            observations = {
                "seeded_created_count": self.seeded_created,
                "external_urls": self.external_urls,
                "page_errors": self.page_errors,
                "mutations": [
                    {"method": m, "path": p}
                    for m, p in self.mutations_since(0)],
                "responses": [
                    {"method": entry["method"], "path": entry["path"],
                     "status": entry["status"]}
                    for entry in self.responses_log]}
            (directory / "session-capacity.json").write_text(
                json.dumps(observations, ensure_ascii=False, indent=2),
                encoding="utf-8")

    def test_full_registry_known_refusal_never_becomes_unknown(self):
        with sync_playwright() as playwright:
            self.seed_full_registry(playwright)
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
                response = page.goto(
                    "/", wait_until="domcontentloaded",
                    timeout=NAV_TIMEOUT_MS)
                self.assertIsNotNone(response)
                self.assertEqual(response.status, 200, page.url)

                # Wait for the page's own failed-session callback to
                # complete (it writes this exact status), then for
                # the network to go quiet before judging the state.
                status = page.get_by_test_id("workspace-status")
                expect(status).to_have_text(
                    BOOT_FAILURE_STATUS, timeout=ASSERT_TIMEOUT_MS)
                page.wait_for_load_state(
                    "networkidle", timeout=NAV_TIMEOUT_MS)
                browser_sessions = self.browser_session_responses()
                self.assertEqual(len(browser_sessions), 1)
                self.assertEqual(browser_sessions[0]["status"], 503)
                self.assertEqual(
                    browser_sessions[0]["response"].json(),
                    {"detail": "session limit reached"})
                self.assertNotIn(
                    UNKNOWN_PHRASE, status.text_content() or "")
                primary = page.get_by_role(
                    "button", name="بدء تحليل الصفحة", exact=True)
                expect(primary).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(
                    page.get_by_test_id("source-reference")
                ).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(self.mutations_since(0), [
                    ("POST", "/api/sessions")])

                # One deliberate activation meets the same known
                # refusal. It stays definite — never unknown — and
                # never reaches settings, Start or analysis.
                grade = page.get_by_label("الصف الدراسي", exact=True)
                objective = page.get_by_label(
                    "الهدف التعليمي", exact=True)
                page_number = page.get_by_label(
                    "رقم الصفحة", exact=True)
                grade.fill(GRADE)
                objective.fill(OBJECTIVE)
                page_number.fill("1")
                page.get_by_label(
                    "ملف صفحة المصدر", exact=True).set_input_files({
                        "name": "source-page.png",
                        "mimeType": "image/png", "buffer": tiny_png()})
                checkpoint = len(self.requests_log)
                primary.click()
                setup_error = page.get_by_test_id("setup-error")
                expect(setup_error).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(setup_error).to_have_text(
                    DEFINITE_FAILURE_ERROR, timeout=ASSERT_TIMEOUT_MS)
                expect(status).to_have_text(
                    BOOT_FAILURE_STATUS, timeout=ASSERT_TIMEOUT_MS)
                page.wait_for_load_state(
                    "networkidle", timeout=NAV_TIMEOUT_MS)
                self.assertEqual(self.mutations_since(checkpoint), [
                    ("POST", "/api/sessions")])
                self.assertEqual(
                    len(self.browser_session_responses()), 2)
                self.assertEqual(
                    self.browser_session_responses()[1]["response"]
                    .json(), {"detail": "session limit reached"})
                expect(primary).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertNotIn(
                    UNKNOWN_PHRASE, status.text_content() or "")
                self.assertNotIn(
                    UNKNOWN_PHRASE, setup_error.text_content() or "")
                expect(
                    page.get_by_test_id("source-reference")
                ).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
                expect(grade).to_have_value(GRADE)
                expect(objective).to_have_value(OBJECTIVE)
                expect(page_number).to_have_value("1")
                self.assertEqual(self.external_urls, [])
                self.assertEqual(self.page_errors, [])
            finally:
                self.capture_artifacts(page)
                if context is not None:
                    with contextlib.suppress(Exception):
                        context.close()
                with contextlib.suppress(Exception):
                    browser.close()


if __name__ == "__main__":
    unittest.main()
