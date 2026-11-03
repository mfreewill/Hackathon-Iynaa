"""Real-browser test for the hosted session notice and idle expiry UI.

Drives the actual app on one loopback origin with an installed Chrome via
sync Playwright. Only method, path and status are recorded for traffic.
The test itself makes no API calls after navigation; every later check
reads the DOM only, so nothing renews the idle lease while waiting.
"""

import json
import os
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


class TestHostedNotice(unittest.TestCase):
    def test_hosted_notice_and_idle_expiry_preserves_form_values(self):
        base_url = os.environ.get("BROWSER_BASE_URL", "")
        executable = os.environ.get("BROWSER_EXECUTABLE", "")
        artifact_raw = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        idle_raw = os.environ.get("BROWSER_SESSION_IDLE_SECONDS", "")

        self.assertTrue(base_url, "BROWSER_BASE_URL is required")
        parsed_base = urlparse(base_url)
        self.assertEqual(parsed_base.scheme, "http", "base URL must be one http loopback origin")
        self.assertIn(parsed_base.hostname, {"127.0.0.1", "localhost", "::1"})
        self.assertIn(parsed_base.path, {"", "/"})
        self.assertFalse(parsed_base.query or parsed_base.fragment)
        base_url = base_url.rstrip("/")
        self.assertTrue(
            executable and Path(executable).is_file(),
            "BROWSER_EXECUTABLE must be an installed Chrome binary",
        )
        self.assertTrue(artifact_raw, "BROWSER_ARTIFACT_DIR is required")
        artifact_dir = Path(artifact_raw)
        self.assertTrue(artifact_dir.is_dir(), "BROWSER_ARTIFACT_DIR must be an existing directory")
        self.assertTrue(idle_raw.isdigit(), "BROWSER_SESSION_IDLE_SECONDS must be whole seconds")
        idle_seconds = int(idle_raw)
        self.assertGreaterEqual(idle_seconds, 4)

        request_log = []
        response_log = []
        page_errors = []
        external_urls = []

        browser = None
        context = None
        page = None
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    executable_path=executable,
                    headless=True,
                    chromium_sandbox=True,
                )
                context = browser.new_context(
                    service_workers="block",
                    viewport={"width": 1280, "height": 900},
                )

                def handle_route(route, *_args):
                    url = route.request.url
                    if (
                        url == base_url
                        or url.startswith(base_url + "/")
                        or url.startswith(("data:", "blob:", "about:"))
                    ):
                        route.continue_()
                    else:
                        parsed = urlparse(url)
                        external_urls.append(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")
                        route.abort()

                context.route("**/*", handle_route)
                page = context.new_page()
                page.on(
                    "request",
                    lambda req: request_log.append(
                        {"method": req.method, "path": urlparse(req.url).path or "/"}
                    ),
                )
                page.on(
                    "response",
                    lambda res: response_log.append(
                        {
                            "method": res.request.method,
                            "path": urlparse(res.url).path or "/",
                            "status": res.status,
                        }
                    ),
                )
                page.on("pageerror", lambda err: page_errors.append(str(err)[:200]))

                notice = page.locator('[data-testid="hosted-session-notice"]')
                expired = page.locator('[data-testid="hosted-session-expired"]')
                grade = page.locator('[data-testid="grade"], #grade, [name="grade"]').first
                objective = page.locator('[data-testid="objective"], #objective, [name="objective"]').first
                settings_apply = page.locator('[data-testid="settings-apply"]')
                primary_start = page.locator('form#setup-form button[type=submit]')

                with page.expect_response(
                    lambda res: res.request.method == "POST"
                    and urlparse(res.url).path == "/api/sessions"
                    and res.status == 201,
                    timeout=15000,
                ):
                    page.goto(base_url + "/", wait_until="domcontentloaded")

                # Final compact shell: an always-visible unsaved
                # summary; the full notice sits inside the
                # session-details disclosure until it is opened.
                unsaved_summary = page.locator(".unsaved-summary")
                expect(unsaved_summary).to_be_visible(timeout=5000)
                summary_text = unsaved_summary.inner_text()
                self.assertTrue(
                    "مؤقت" in summary_text or "موقت" in summary_text,
                    "compact summary must call the session temporary (mu'aqqat)",
                )
                self.assertTrue(
                    any(
                        phrase in summary_text
                        for phrase in (
                            "غير محفوظ",
                            "غير محفوظة",
                            "غير المحفوظ",
                            "غير المحفوظة",
                            "لن يتم حفظ",
                            "لا يتم حفظ",
                            "لم يتم حفظ",
                            "لن يحفظ",
                            "لن يُحفظ",
                            "لا يحفظ",
                            "لا يُحفظ",
                            "بدون حفظ",
                        )
                    ),
                    "compact summary must truthfully say the work is unsaved",
                )
                session_details = page.locator("[data-testid=session-details]")
                session_summary = session_details.locator("xpath=./summary[1]")
                expect(session_summary).to_be_visible(timeout=5000)
                if session_details.get_attribute("open") is None:
                    session_summary.click()

                expect(notice).to_be_visible(timeout=5000)
                notice_text = notice.inner_text()
                latin_digits = str(idle_seconds)
                arabic_digits = latin_digits.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
                self.assertTrue(
                    latin_digits in notice_text or arabic_digits in notice_text,
                    "notice must contain the supplied idle duration",
                )
                self.assertTrue(
                    "ثان" in notice_text or "ثوان" in notice_text or "دقيق" in notice_text,
                    "notice must name the duration unit",
                )
                self.assertIn("جلس", notice_text)
                self.assertTrue(
                    "الخادم" in notice_text or "الخدمة" in notice_text,
                    "notice must say the session lives on the server/service",
                )
                self.assertTrue(
                    any(word in notice_text for word in ("مؤقت", "عدم النشاط", "غير نشط", "تنتهي", "تنتهى")),
                    "notice must describe a temporary session that ends on inactivity",
                )
                for promise in ("سيتم حفظ", "سيبقى محفوظ", "حفظ دائم", "محفوظ بشكل دائم"):
                    self.assertNotIn(promise, notice_text, "notice must not promise persisted work")

                # Close the disclosure again before the form save
                # and idle wait; expiry must surface while closed.
                if session_details.get_attribute("open") is not None:
                    session_summary.click()
                self.assertIsNone(
                    session_details.get_attribute("open"),
                    "session details must be closed before the form save and idle wait",
                )

                grade_value = "Grade 5 Synthetic"
                objective_value = "Synthetic objective 12345"
                # Explicit navigation: reveal settings when hidden.
                setup_settings = page.locator('[data-studio="setup-settings"]')
                if (not grade.is_visible() and setup_settings.count() == 1
                        and setup_settings.is_visible()):
                    setup_settings.click()
                grade.fill(grade_value)
                objective.fill(objective_value)
                expect(settings_apply).to_contain_text("حفظ الصف والهدف", timeout=3000)
                with page.expect_response(
                    lambda res: res.request.method == "PUT"
                    and urlparse(res.url).path == "/api/session/settings"
                    and res.status == 200,
                    timeout=8000,
                ):
                    settings_apply.click()
                put_at = time.monotonic()
                requests_at_put = len(request_log)

                mid_wait_ms = max(0, int((put_at + idle_seconds / 2 - time.monotonic()) * 1000))
                page.wait_for_timeout(mid_wait_ms)
                expect(expired).to_be_hidden(timeout=1000)

                remaining_ms = max(0, int((put_at + idle_seconds - time.monotonic()) * 1000))
                expect(expired).to_be_visible(timeout=remaining_ms + 4000)
                self.assertGreaterEqual(
                    time.monotonic(),
                    put_at + idle_seconds - 0.25,
                    "expiry UI must appear only after the idle lease",
                )
                self.assertIsNone(
                    session_details.get_attribute("open"),
                    "expiry must be visible while session details stay closed",
                )

                self.assertEqual(grade.input_value(), grade_value)
                self.assertEqual(objective.input_value(), objective_value)
                expect(primary_start).to_be_disabled(timeout=3000)
                expect(settings_apply).to_be_disabled(timeout=3000)

                session_posts = [
                    entry
                    for entry in request_log
                    if entry["method"] == "POST" and entry["path"] == "/api/sessions"
                ]
                self.assertEqual(len(session_posts), 1, "exactly one session may be created")
                settings_puts = [
                    entry
                    for entry in response_log
                    if entry["method"] == "PUT"
                    and entry["path"] == "/api/session/settings"
                    and entry["status"] == 200
                ]
                self.assertEqual(len(settings_puts), 1)
                self.assertFalse(
                    any("heartbeat" in entry["path"] for entry in request_log),
                    "no heartbeat request may be emitted",
                )
                self.assertFalse(
                    any(
                        entry["method"] == "POST"
                        and entry["path"]
                        in {
                            "/api/session/source-analysis",
                            "/api/session/questions/generate",
                        }
                        for entry in request_log
                    ),
                    "no model route may be emitted",
                )
                api_after_put = [
                    entry
                    for entry in request_log[requests_at_put:]
                    if entry["path"].startswith("/api/")
                ]
                self.assertEqual(api_after_put, [], "no further API requests may fire while idling to expiry")
                self.assertEqual(page_errors, [])
                self.assertEqual(external_urls, [])
            finally:
                if page is not None:
                    try:
                        page.screenshot(path=str(artifact_dir / "hosted-notice.png"), full_page=True)
                    except Exception:
                        pass
                    try:
                        (artifact_dir / "hosted-notice.html").write_text(page.content(), encoding="utf-8")
                    except Exception:
                        pass
                try:
                    summary = {
                        "base_url": base_url,
                        "session_idle_seconds": idle_seconds,
                        "requests": request_log,
                        "responses": response_log,
                        "page_errors": page_errors,
                        "external_urls": external_urls,
                    }
                    (artifact_dir / "request-summary.json").write_text(
                        json.dumps(summary, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass


if __name__ == "__main__":
    unittest.main()
