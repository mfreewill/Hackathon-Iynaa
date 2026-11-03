"""Focused lifetime tests for the browser provider-operation owner.

The test harness serves
``teacher_demo.http_app.create_app`` with the test
``ControlsProvider`` wrapped by an external hold fixture, on a
fresh app/browser context/marker directory per isolated run, and
runs the first two methods once with ``BROWSER_OPERATION_KIND=
source`` and once with ``questions`` and the other two with
``source`` (six runs). Holds are observed only through empty
marker files in ``BROWSER_HOLD_DIR``: the harness pre-arms each
fixture ID (``<id>.armed``), this test waits for ``<id>.ready``
(rejecting ``<id>.expired``; missing markers fail, never skip) and
releases by touching ``<id>.release`` after verifying the armed
file. ``.entered`` is wrapper entry, ``.cancel_seen`` observes the
exact context Event and ``.returned`` is normal delegate return;
callable exit is never inferred from a cancel 200. ``.produced``
is complete app response production, not Chrome adoption.

Chromium runs headless with ``chromium_sandbox=True``, service
workers blocked and finite waits. Teacher mutations come only
from visible DOM controls; shared-cookie context requests are
GET observations of actual snapshot/raw/status/page/preview.
Provider commands are captured by actual path/method regardless
of status and must be exactly ``{request_id, source_id}`` with
32-lowercase-hex IDs. The sole transport-fault exception is in
``test_lost_cancel_ack_recovers_by_exact_status``: the actual
matching cancel is forwarded once with
``route.fetch(max_redirects=0, max_retries=0)`` and its genuine
response discarded with ``route.abort()``. Nothing is fulfilled,
rewritten, faked or injected anywhere; interception elsewhere
only blocks outside-origin traffic. Cookie/CSRF/header values
are never persisted. A small observation JSON and final
screenshot are written to ``BROWSER_ARTIFACT_DIR`` even on
failure. Every named hold is released in ``finally``.
"""

import contextlib
import hashlib
import json
import os
import pathlib
import time
import unittest
from urllib.parse import quote, urljoin, urlsplit

from playwright.sync_api import expect, sync_playwright

import test_review_journey

NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000
MARKER_TIMEOUT_S = 30.0

START_PATH = "/api/session/start"
SETTINGS_PATH = "/api/session/settings"
SOURCE_PATH = "/api/session/source-analysis"
QUESTIONS_PATH = "/api/session/questions/generate"
SNAPSHOT_PATH = "/api/session/snapshot"
PAGE_PATH = "/api/session/page"
PREVIEW_PATH = "/api/session/preview"

REGION_RIVER = "synthetic-section-river"
REGION_TREES = "synthetic-section-trees"
SLOT_REMEMBER = "slot-remember"
SLOT_UNDERSTAND = "slot-understand"
DIRTY_WORDING = "  صِيَاغَةٌ مَحَلِّيَّةٌ غَيْرُ مَحْفُوظَةٍ لِلسُّؤَالِ الثَّانِي  "

OP_UNCONFIRMED = (
    "حالة هذا الطلب غير مؤكدة بعد؛ لم نُعد إرساله ولم نبدأ طلبًا آخر. "
    "استخدم «التحقق من حالة الطلب» أو «إلغاء الطلب» لهذا الطلب نفسه.")
OP_STILL_WORKING = "الطلب ما زال قيد التنفيذ لدى المزوّد؛ لم يُغلق بعد."
OP_SUCCEEDED = "اكتمل الطلب وطُبّقت نتيجته."
OP_CANCELLED = (
    "أُغلق الطلب ملغيًا؛ لن تُطبَّق أي نتيجة متأخرة منه في مساحة العمل.")
OP_WORKING = {
    "source": "طلب تحليل المصدر قيد التنفيذ لدى المزوّد.",
    "questions": "طلب توليد الأسئلة قيد التنفيذ لدى المزوّد."}
WS_RECEIVED = (
    "وصلت مقترحات من خدمة التحليل وحُفظت في مساحة العمل؛ "
    "راجع المقاطع أدناه قبل توليد الأسئلة.")
WS_ANALYSIS_CANCELLED = (
    "أُلغي طلب تحليل المصدر قبل اكتماله ولم تُطبَّق نتيجته. "
    "تبقى صفحتك وإعداداتك كما هي.")
WS_GENERATION_CANCELLED = (
    "أُلغي طلب توليد الأسئلة ولم تُنشر أسئلة جديدة في هذه الخطوة. "
    "بقيت مراجعة المصدر كما هي.")


class ProviderOperationLifecycleBrowserTests(unittest.TestCase):
    """Cancel/retry/early-absence/completion/lost-ACK schedules."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.fx, cls.png_bytes = test_review_journey.load_fixture()

    def setUp(self):
        self.base_url = os.environ.get(
            "BROWSER_BASE_URL", "").rstrip("/")
        self.executable = os.environ.get("BROWSER_EXECUTABLE", "")
        self.artifact_dir = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        self.kind = os.environ.get("BROWSER_OPERATION_KIND", "")
        hold_dir = os.environ.get("BROWSER_HOLD_DIR", "")
        self.assertTrue(
            self.base_url,
            "BROWSER_BASE_URL (actual served origin) is required")
        self.assertTrue(
            self.executable,
            "BROWSER_EXECUTABLE (installed browser path) is required")
        self.assertTrue(
            pathlib.Path(self.executable).is_file(),
            "BROWSER_EXECUTABLE must name the installed browser file")
        self.assertIn(
            self.kind, ("source", "questions"),
            "BROWSER_OPERATION_KIND must be source or questions")
        self.assertTrue(
            hold_dir, "BROWSER_HOLD_DIR (marker directory) is required")
        self.hold_dir = pathlib.Path(hold_dir)
        self.assertTrue(
            self.hold_dir.is_dir(),
            "BROWSER_HOLD_DIR must name the parent marker directory")
        parts = urlsplit(self.base_url)
        self.assertIn(parts.scheme, ("http", "https"))
        self.assertTrue(parts.netloc)
        self.base_origin = "{0.scheme}://{0.netloc}".format(parts)
        self.requests_log = []
        self.external_urls = []
        self.page_errors = []
        self.cancel_loss_path = None
        self.forwarded_cancel = None
        self.observation = {
            "case": f"{self._testMethodName}:{self.kind}",
            "method": self._testMethodName,
            "kind": self.kind,
            "request_ids": {},
            "source_id": None,
            "statuses": {},
            "phases": [],
            "request_counts": {},
            "markers": [],
            "operation_status": "",
            "focus": "",
            "forwarded_cancel": None,
            "external_urls": [],
            "page_errors": [],
            "final_dom_text": "",
        }

    # -- routing / recording ---------------------------------------
    def handle_route(self, route, request):
        parts = urlsplit(request.url)
        if parts.scheme in ("data", "blob"):
            route.continue_()
            return
        if "{0.scheme}://{0.netloc}".format(parts) != self.base_origin:
            self.external_urls.append(request.url)
            route.abort("blockedbyclient")
            return
        if (self.cancel_loss_path is not None
                and self.forwarded_cancel is None
                and request.method == "POST"
                and parts.path == self.cancel_loss_path):
            # Sole permitted loss (method 4 only): forward the
            # actual cancel once, keep only safe IDs/status, then
            # discard the genuine response undelivered.
            response = route.fetch(max_redirects=0, max_retries=0)
            recorded = {"status": response.status, "request_id": None,
                        "source_id": None, "record_state": None,
                        "record_outcome": None}
            with contextlib.suppress(Exception):
                body = response.json()
                if isinstance(body, dict):
                    recorded["request_id"] = body.get("request_id")
                    recorded["source_id"] = body.get("source_id")
                    snapshot = body.get("snapshot") or {}
                    for item in snapshot.get("requests") or []:
                        if item.get("request_id") == body.get(
                                "request_id"):
                            recorded["record_state"] = item.get("state")
                            recorded["record_outcome"] = item.get("outcome")
            with contextlib.suppress(Exception):
                response.dispose()
            self.forwarded_cancel = recorded
            self.observation["forwarded_cancel"] = recorded
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
        self.requests_log.append(entry)

    def request_entries(self, method, path, start=0):
        return [entry for entry in self.requests_log[start:]
                if entry["method"] == method
                and entry["path"] == path]

    def request_bodies(self, method, path, start=0):
        return [entry.get("json")
                for entry in self.request_entries(method, path, start)]

    def request_count(self, method, path):
        return len(self.request_entries(method, path))

    def mutations_since(self, index):
        return [(entry["method"], entry["path"])
                for entry in self.requests_log[index:]
                if entry["method"] in ("POST", "PUT", "PATCH")]

    def wait_body(self, page, path, occurrence):
        deadline = time.monotonic() + ASSERT_TIMEOUT_MS / 1000
        while True:
            bodies = self.request_bodies("POST", path)
            if (len(bodies) >= occurrence
                    and isinstance(bodies[occurrence - 1], dict)):
                return bodies[occurrence - 1]
            if time.monotonic() >= deadline:
                self.fail(
                    f"provider POST #{occurrence} to {path} not captured")
            page.wait_for_timeout(50)

    def mark_phase(self, name):
        self.observation["phases"].append(name)

    # -- hold markers -----------------------------------------------
    def hold_path(self, fixture_id, marker):
        return self.hold_dir / f"{fixture_id}.{marker}"

    def marker_is_file(self, fixture_id, marker):
        return self.hold_path(fixture_id, marker).is_file()

    def assert_armed(self, fixture_ids):
        for fixture_id in fixture_ids:
            self.assertTrue(
                self.marker_is_file(fixture_id, "armed"),
                f"hold {fixture_id} was not pre-armed by the parent")

    def wait_marker(self, fixture_id, marker,
                    timeout=MARKER_TIMEOUT_S):
        self.assertTrue(
            self.marker_is_file(fixture_id, "armed"),
            f"hold {fixture_id} was not pre-armed by the parent")
        deadline = time.monotonic() + timeout
        while not self.marker_is_file(fixture_id, marker):
            self.assertFalse(
                self.marker_is_file(fixture_id, "expired"),
                f"hold {fixture_id} expired before {marker}")
            if time.monotonic() >= deadline:
                self.fail(
                    f"marker {fixture_id}.{marker} not observed; "
                    f"directory holds {self.marker_names()}")
            time.sleep(0.05)

    def release_hold(self, fixture_id):
        self.assertTrue(
            self.marker_is_file(fixture_id, "armed"),
            f"hold {fixture_id} was not pre-armed by the parent")
        self.hold_path(fixture_id, "release").touch(exist_ok=True)

    def release_quietly(self, fixture_ids):
        for fixture_id in fixture_ids:
            with contextlib.suppress(Exception):
                if self.marker_is_file(fixture_id, "armed"):
                    self.hold_path(
                        fixture_id, "release").touch(exist_ok=True)

    def marker_names(self):
        with contextlib.suppress(Exception):
            return sorted(
                path.name for path in self.hold_dir.iterdir()
                if path.is_file())
        return []

    # -- artifacts / browser ----------------------------------------
    def capture_artifacts(self, page):
        if not self.artifact_dir:
            return
        with contextlib.suppress(Exception):
            directory = pathlib.Path(self.artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            stem = (f"provider_operation_lifecycle_"
                    f"{self._testMethodName}_{self.kind}")
            self.observation["request_counts"] = {
                "settings": self.request_count("PUT", SETTINGS_PATH),
                "start": self.request_count("POST", START_PATH),
                "source_analysis": self.request_count(
                    "POST", SOURCE_PATH),
                "question_generation": self.request_count(
                    "POST", QUESTIONS_PATH),
            }
            self.observation["markers"] = self.marker_names()
            self.observation["external_urls"] = list(self.external_urls)
            self.observation["page_errors"] = list(self.page_errors)
            if page is not None:
                with contextlib.suppress(Exception):
                    self.observation["operation_status"] = (
                        self.tid(page, "provider-operation-status")
                        .inner_text(timeout=ASSERT_TIMEOUT_MS))
                with contextlib.suppress(Exception):
                    self.observation["focus"] = self.focus_name(page)
                with contextlib.suppress(Exception):
                    self.observation["final_dom_text"] = (
                        page.locator("body").inner_text(
                            timeout=ASSERT_TIMEOUT_MS))
            (directory / f"{stem}.json").write_text(
                json.dumps(
                    self.observation, ensure_ascii=False, indent=2)
                + "\n", encoding="utf-8")
            if page is not None:
                with contextlib.suppress(Exception):
                    page.screenshot(
                        path=str(directory / f"{stem}.png"),
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

    # -- GET observations (context only) ------------------------------
    @staticmethod
    def tid(scope, name):
        return scope.get_by_test_id(name)

    def api_get(self, context, path):
        return context.request.get(
            self.base_url + path, timeout=ASSERT_TIMEOUT_MS)

    def api_json(self, context, path):
        response = self.api_get(context, path)
        self.assertEqual(response.status, 200, path)
        return response.json()

    def api_bytes(self, context, server_url):
        parts = urlsplit(server_url)
        url = server_url if parts.scheme else urljoin(
            self.base_url + "/", server_url)
        if parts.scheme:
            self.assertEqual(
                "{0.scheme}://{0.netloc}".format(parts),
                self.base_origin)
        response = context.request.get(
            url, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(response.status, 200, server_url)
        return response.body()

    def snapshot(self, context):
        return self.api_json(context, SNAPSHOT_PATH)

    @staticmethod
    def status_path(request_id):
        return ("/api/session/requests/"
                + quote(request_id, safe="") + "/status")

    @staticmethod
    def cancel_path(request_id):
        return ("/api/session/requests/"
                + quote(request_id, safe="") + "/cancel")

    def status_via_context(self, context, request_id, source_id):
        response = self.api_get(
            context, self.status_path(request_id)
            + "?source_id=" + quote(source_id, safe=""))
        try:
            payload = response.json()
        except Exception:
            payload = None
        return response.status, payload

    def raw_result(self, context, request_id):
        payload = self.api_json(
            context, "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["raw_retention"], "retained")
        return payload["provider_result"]

    # -- exact command / record assertions ---------------------------
    def assert_provider_command(self, body, source_id, label):
        self.assertIsInstance(body, dict)
        self.assertEqual(set(body), {"request_id", "source_id"})
        self.assertRegex(body["request_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(body["source_id"], source_id)
        self.observation["request_ids"][label] = body["request_id"]
        return body["request_id"]

    def record(self, snapshot, request_id):
        matches = [item for item in snapshot["requests"]
                   if item["request_id"] == request_id]
        self.assertEqual(len(matches), 1, request_id)
        return matches[0]

    def assert_record(self, snapshot, request_id, kind, state,
                      outcome):
        item = self.record(snapshot, request_id)
        self.assertEqual(item["kind"], kind)
        self.assertEqual(item["state"], state)
        if outcome is not None:
            self.assertEqual(item["outcome"], outcome)
        return item

    def assert_status_endpoint(self, context, request_id, source_id,
                               kind, state, outcome):
        status, payload = self.status_via_context(
            context, request_id, source_id)
        self.assertEqual(status, 200, (request_id, payload))
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["source_id"], source_id)
        item = payload["request"]
        self.assertEqual(item["request_id"], request_id)
        self.assertEqual(item["kind"], kind)
        self.assertEqual(item["state"], state)
        if outcome is not None:
            self.assertEqual(item["outcome"], outcome)
        return item

    def assert_cancel_body(self, body, request_id, source_id, kind):
        self.assertIsInstance(body, dict)
        self.assertEqual(body["request_id"], request_id)
        self.assertEqual(body["source_id"], source_id)
        self.assertIsInstance(body.get("snapshot"), dict)
        self.assertEqual(body["snapshot"]["source_id"], source_id)
        return self.assert_record(
            body["snapshot"], request_id, kind, "terminal",
            "cancelled")

    def assert_clean(self):
        self.assertEqual(self.external_urls, [])
        self.assertEqual(self.page_errors, [])

    def assert_counts(self, settings, start, source, questions):
        self.assertEqual(
            self.request_count("PUT", SETTINGS_PATH), settings)
        self.assertEqual(
            self.request_count("POST", START_PATH), start)
        self.assertEqual(
            self.request_count("POST", SOURCE_PATH), source)
        self.assertEqual(
            self.request_count("POST", QUESTIONS_PATH), questions)

    # -- DOM helpers --------------------------------------------------
    def op(self, page, name):
        return self.tid(page, f"provider-operation-{name}")

    def primary(self, page):
        return page.get_by_role(
            "button", name="بدء تحليل الصفحة", exact=True)

    def generate(self, page):
        return page.get_by_role(
            "button", name="اعتماد المصدر وتوليد الأسئلة",
            exact=True)

    def focus_name(self, page):
        with contextlib.suppress(Exception):
            return page.evaluate(
                "() => document.activeElement"
                " ? document.activeElement.getAttribute('data-testid')"
                " || '' : ''") or ""
        return ""

    def assert_op_controls_enabled(self, page):
        expect(self.tid(page, "provider-operation")).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        for name in ("cancel", "check"):
            expect(self.op(page, name)).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.op(page, name)).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)

    def assert_op_status(self, page, text):
        expect(self.op(page, "status")).to_have_text(
            text, timeout=ASSERT_TIMEOUT_MS)

    def assert_source_visible(self, page, source_id):
        expect(self.tid(page, "source-reference")).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(page, "source-preview")).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(page, "source-id")).to_have_text(
            source_id, timeout=ASSERT_TIMEOUT_MS)

    def capture_click(self, page, method, path, locator):
        with page.expect_response(
                lambda item: item.request.method == method
                and urlsplit(item.url).path == path,
                timeout=ASSERT_TIMEOUT_MS) as response_info:
            locator.click(timeout=ASSERT_TIMEOUT_MS)
        return response_info.value

    def click_cancel(self, page, request_id):
        return self.capture_click(
            page, "POST", self.cancel_path(request_id),
            self.op(page, "cancel"))

    def click_check(self, page, request_id):
        return self.capture_click(
            page, "GET", self.status_path(request_id),
            self.op(page, "check"))

    def await_original(self, page, path, request_id, trigger):
        def is_original(item):
            if (item.request.method != "POST"
                    or urlsplit(item.url).path != path):
                return False
            try:
                return json.loads(
                    item.request.post_data or "{}"
                ).get("request_id") == request_id
            except Exception:
                return False

        with page.expect_response(
                is_original,
                timeout=ASSERT_TIMEOUT_MS) as response_info:
            trigger()
        return response_info.value

    # -- teacher journey pieces ---------------------------------------
    def region_by_section(self, snapshot, section_id):
        matches = [item for item in snapshot["regions"]
                   if item["external_section_id"] == section_id]
        self.assertEqual(len(matches), 1, section_id)
        return matches[0]

    def question_by_request_slot(self, snapshot, request_id, slot_id):
        matches = [item for item in snapshot["questions"]
                   if item["request_id"] == request_id
                   and item["slot_id"] == slot_id]
        self.assertEqual(len(matches), 1, (request_id, slot_id))
        return matches[0]

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

    def goto_and_fill(self, page):
        fx = self.fx
        response = page.goto(
            "/", wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS)
        self.observation["statuses"]["page"] = (
            response.status if response is not None else None)
        self.assertIsNotNone(response)
        self.assertEqual(response.status, 200, page.url)
        page.wait_for_load_state("networkidle", timeout=NAV_TIMEOUT_MS)
        page.get_by_label("الصف الدراسي", exact=True).select_option(value=
            fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
        page.get_by_label("الهدف التعليمي", exact=True).fill(
            fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
        page.get_by_label("رقم الصفحة", exact=True).fill(
            "1", timeout=ASSERT_TIMEOUT_MS)
        page.get_by_label("ملف صفحة المصدر", exact=True).set_input_files(
            {"name": "source-page.png", "mimeType": "image/png",
             "buffer": self.png_bytes}, timeout=ASSERT_TIMEOUT_MS)
        start_button = self.primary(page)
        expect(start_button).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(start_button).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
        return start_button

    def begin_held_source(self, page):
        start_button = self.goto_and_fill(page)
        with page.expect_response(
                lambda item: item.request.method == "POST"
                and urlsplit(item.url).path == START_PATH,
                timeout=ASSERT_TIMEOUT_MS) as start_info:
            start_button.click(timeout=ASSERT_TIMEOUT_MS)
        start_response = start_info.value
        self.observation["statuses"]["start"] = start_response.status
        self.assertEqual(start_response.status, 201)
        metadata = start_response.json()
        self.assertIsInstance(metadata, dict)
        self.assertIsInstance(metadata["source_id"], str)
        self.assertTrue(metadata["source_id"])
        self.observation["source_id"] = metadata["source_id"]
        self.mark_phase("start_acknowledged")
        return metadata

    def start_normal_source(self, page):
        start_button = self.goto_and_fill(page)
        with page.expect_response(
                lambda item: item.request.method == "POST"
                and urlsplit(item.url).path == START_PATH,
                timeout=ASSERT_TIMEOUT_MS) as start_info, \
                page.expect_response(
                    lambda item: item.request.method == "POST"
                    and urlsplit(item.url).path == SOURCE_PATH,
                    timeout=ASSERT_TIMEOUT_MS) as analysis_info:
            start_button.click(timeout=ASSERT_TIMEOUT_MS)
        start_response = start_info.value
        analysis_response = analysis_info.value
        self.observation["statuses"]["start"] = start_response.status
        self.observation["statuses"]["source_analysis"] = (
            analysis_response.status)
        self.assertEqual(start_response.status, 201)
        metadata = start_response.json()
        source_id = metadata["source_id"]
        self.observation["source_id"] = source_id
        self.assertEqual(analysis_response.status, 201)
        body = self.wait_body(page, SOURCE_PATH, 1)
        request_id = self.assert_provider_command(
            body, source_id, "source_analysis")
        envelope = analysis_response.json()
        self.assertEqual(envelope["request_id"], request_id)
        self.assertEqual(envelope["snapshot"]["source_id"], source_id)
        self.mark_phase("source_analysis_201")
        return metadata, request_id

    def review_both_regions(self, page, context):
        fx = self.fx
        snapshot = self.snapshot(context)
        self.assertEqual(len(snapshot["regions"]), 2)
        for section_id, edited in ((REGION_RIVER, fx["EDITED_A"]),
                                    (REGION_TREES, fx["EDITED_B"])):
            region = self.region_by_section(snapshot, section_id)
            label_before = region["current_label"]
            item = self.region_item(page, region)
            expect(item).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            checkpoint = len(self.requests_log)
            self.tid(item, "region-current-text-input").fill(
                edited, timeout=ASSERT_TIMEOUT_MS)
            self.tid(item, "region-inclusion-input").select_option(
                "included", timeout=ASSERT_TIMEOUT_MS)
            path = self.region_path(region)
            response = self.capture_click(
                page, "PUT", path,
                self.tid(item, "region-review-commit"))
            self.assertEqual(response.status, 200, section_id)
            bodies = self.request_bodies("PUT", path, checkpoint)
            self.assertEqual(
                bodies,
                [{"inclusion": "included", "current_text": edited,
                  "current_label": label_before}])
            snapshot = self.snapshot(context)
            updated = self.region_by_section(snapshot, section_id)
            self.assertEqual(updated["region_id"], region["region_id"])
            self.assertEqual(updated["current_text"], edited)
            self.assertEqual(updated["inclusion"], "included")
        self.assertTrue(snapshot["can_generate"])
        self.assertEqual(snapshot["included_count"], 2)
        self.assertEqual(snapshot["withheld_count"], 0)
        expect(self.generate(page)).to_be_enabled(
            timeout=ASSERT_TIMEOUT_MS)
        self.mark_phase("source_reviewed_both_included")
        return snapshot

    def generate_first_normal(self, page, context, source_id):
        response = self.capture_click(
            page, "POST", QUESTIONS_PATH, self.generate(page))
        self.observation["statuses"]["question_generation_1"] = (
            response.status)
        self.assertEqual(response.status, 201)
        body = self.wait_body(page, QUESTIONS_PATH, 1)
        request_id = self.assert_provider_command(
            body, source_id, "question_generation_1")
        envelope = response.json()
        self.assertEqual(envelope["request_id"], request_id)
        self.assertEqual(
            envelope["snapshot"]["source_id"], source_id)
        snapshot = self.snapshot(context)
        self.assert_record(
            snapshot, request_id, "questions", "terminal", "ok")
        self.assertEqual(len(snapshot["questions"]), 2)
        self.mark_phase("first_generation_201")
        return request_id, snapshot

    def approve_and_dirty(self, page, context, first_request_id):
        fx = self.fx
        snapshot = self.snapshot(context)
        approved = self.question_by_request_slot(
            snapshot, first_request_id, SLOT_REMEMBER)
        other = self.question_by_request_slot(
            snapshot, first_request_id, SLOT_UNDERSTAND)
        self.assertEqual(approved["status"], "draft")
        self.assertEqual(other["status"], "draft")
        self.assertEqual(
            approved["current_question"], fx["QUESTION_1"])
        self.assertEqual(
            other["current_question"], fx["QUESTION_2"])
        approve_path = self.question_path(approved, "/approve")
        response = self.capture_click(
            page, "POST", approve_path,
            self.tid(
                self.question_card(page, approved),
                "question-approve"))
        self.assertEqual(response.status, 200)
        snapshot = self.snapshot(context)
        approved = self.question_by_request_slot(
            snapshot, first_request_id, SLOT_REMEMBER)
        other = self.question_by_request_slot(
            snapshot, first_request_id, SLOT_UNDERSTAND)
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(other["status"], "draft")
        preview = self.api_json(context, PREVIEW_PATH)["items"]
        self.assertEqual(
            [item["question_id"] for item in preview],
            [approved["question_id"]])
        self.assertEqual(preview[0]["question"], fx["QUESTION_1"])
        checkpoint = len(self.requests_log)
        card = self.question_card(page, other)
        self.tid(card, "question-edit").click(
            timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(card, "question-edit-form")).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        field = self.tid(card, "question-edit-question")
        expect(field).to_have_value(
            fx["QUESTION_2"], timeout=ASSERT_TIMEOUT_MS)
        field.fill(DIRTY_WORDING, timeout=ASSERT_TIMEOUT_MS)
        expect(field).to_have_value(
            DIRTY_WORDING, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(self.mutations_since(checkpoint), [])
        self.mark_phase("approved_and_dirty_draft_open")
        return approved, other

    def assert_questions_preserved(
            self, page, context, first_request_id, approved, other):
        fx = self.fx
        snapshot = self.snapshot(context)
        kept_approved = self.question_by_request_slot(
            snapshot, first_request_id, SLOT_REMEMBER)
        kept_other = self.question_by_request_slot(
            snapshot, first_request_id, SLOT_UNDERSTAND)
        self.assertEqual(
            kept_approved["question_id"], approved["question_id"])
        self.assertEqual(
            kept_other["question_id"], other["question_id"])
        self.assertEqual(kept_approved["status"], "approved")
        self.assertEqual(kept_other["status"], "draft")
        self.assertEqual(
            kept_other["current_question"], fx["QUESTION_2"])
        self.assertEqual(
            [item["question_id"]
             for item in snapshot["preview_items"]],
            [approved["question_id"]])
        item = self.preview_item(page, approved["question_id"])
        expect(item).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(item, "preview-question")).to_have_text(
            fx["QUESTION_1"], timeout=ASSERT_TIMEOUT_MS)
        card = self.question_card(page, other)
        expect(self.tid(card, "question-edit-form")).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(card, "question-edit-question")).to_have_value(
            DIRTY_WORDING, timeout=ASSERT_TIMEOUT_MS)
        return snapshot

    # -- 1. cancel, retry and the inert late response -----------------
    def test_cancel_retry_and_late_response_preserve_owner(self):
        holds = ("old-provider", "old-response", "next-provider")
        self.assert_armed(holds)
        kind = self.kind
        path = SOURCE_PATH if kind == "source" else QUESTIONS_PATH
        with self.browser_page() as (context, page):
            try:
                first_request_id = None
                approved = other = None
                if kind == "source":
                    metadata = self.begin_held_source(page)
                    source_id = metadata["source_id"]
                    old_occurrence = 1
                else:
                    metadata, source_request_id = (
                        self.start_normal_source(page))
                    source_id = metadata["source_id"]
                    self.assert_status_endpoint(
                        context, source_request_id, source_id,
                        "source", "terminal", "ok")
                    self.review_both_regions(page, context)
                    first_request_id, _snapshot = (
                        self.generate_first_normal(
                            page, context, source_id))
                    approved, other = self.approve_and_dirty(
                        page, context, first_request_id)
                    expect(self.generate(page)).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.generate(page).click(
                        timeout=ASSERT_TIMEOUT_MS)
                    old_occurrence = 2
                self.wait_marker("old-provider", "ready")
                self.assertTrue(
                    self.marker_is_file("old-provider", "entered"))
                old_id = self.assert_provider_command(
                    self.wait_body(page, path, old_occurrence),
                    source_id, "old")
                self.assertNotEqual(old_id, first_request_id)
                self.assert_status_endpoint(
                    context, old_id, source_id, kind, "working", None)
                self.assert_op_controls_enabled(page)
                self.assert_op_status(page, OP_WORKING[kind])
                self.assert_source_visible(page, source_id)
                expect(self.primary(page)).to_be_disabled(
                    timeout=ASSERT_TIMEOUT_MS)
                if kind == "source":
                    self.assertEqual(
                        self.snapshot(context)["regions"], [])
                    expect(self.tid(page, "region-item")
                           ).to_have_count(0)
                else:
                    self.assert_questions_preserved(
                        page, context, first_request_id, approved,
                        other)
                self.mark_phase("old_provider_held_working")

                cancel_response = self.click_cancel(page, old_id)
                self.observation["statuses"]["cancel_old"] = (
                    cancel_response.status)
                self.assertEqual(cancel_response.status, 200)
                self.assert_cancel_body(
                    cancel_response.json(), old_id, source_id, kind)
                self.wait_marker("old-provider", "cancel_seen")
                self.assertFalse(
                    self.marker_is_file("old-provider", "returned"))
                self.assertFalse(
                    self.marker_is_file("old-provider", "released"))
                self.assert_op_status(page, OP_CANCELLED)
                expect(self.tid(page, "workspace-status")
                       ).to_have_text(
                           WS_ANALYSIS_CANCELLED if kind == "source"
                           else WS_GENERATION_CANCELLED,
                           timeout=ASSERT_TIMEOUT_MS)
                self.assert_source_visible(page, source_id)
                if kind == "source":
                    expect(self.op(page, "retry")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.op(page, "retry")).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.assertEqual(
                        self.snapshot(context)["regions"], [])
                else:
                    expect(self.generate(page)).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.assert_questions_preserved(
                        page, context, first_request_id, approved,
                        other)
                self.mark_phase("old_cancelled_provider_still_held")

                checkpoint = len(self.requests_log)
                if kind == "source":
                    self.op(page, "retry").click(
                        timeout=ASSERT_TIMEOUT_MS)
                else:
                    self.generate(page).click(
                        timeout=ASSERT_TIMEOUT_MS)
                self.wait_marker("next-provider", "ready")
                new_id = self.assert_provider_command(
                    self.wait_body(page, path, old_occurrence + 1),
                    source_id, "new")
                self.assertNotEqual(new_id, old_id)
                self.assertNotEqual(new_id, first_request_id)
                self.assertEqual(
                    self.mutations_since(checkpoint),
                    [("POST", path)])
                self.assert_status_endpoint(
                    context, new_id, source_id, kind, "working", None)
                if kind == "source":
                    original = self.api_bytes(
                        context, metadata["original_url"])
                    self.assertEqual(original, self.png_bytes)
                    self.assertEqual(
                        hashlib.sha256(original).hexdigest(),
                        metadata["source_sha256"])
                    self.assertEqual(
                        self.api_json(context, PAGE_PATH)["source_id"],
                        source_id)
                else:
                    self.assert_questions_preserved(
                        page, context, first_request_id, approved,
                        other)
                    expect(self.generate(page)).to_be_disabled(
                        timeout=ASSERT_TIMEOUT_MS)
                self.assert_op_controls_enabled(page)
                self.assert_op_status(page, OP_WORKING[kind])
                self.observation["focus"] = self.focus_name(page)
                self.op(page, "cancel").focus(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.op(page, "cancel")).to_be_focused(
                    timeout=ASSERT_TIMEOUT_MS)
                self.mark_phase("new_provider_held_working")

                self.release_hold("old-provider")
                self.wait_marker("old-response", "ready")
                self.assertTrue(
                    self.marker_is_file("old-response", "produced"))
                late = self.await_original(
                    page, path, old_id,
                    lambda: self.release_hold("old-response"))
                self.observation["statuses"]["late_old"] = late.status
                self.assertEqual(late.status, 409)
                late_body = late.json()
                self.assertEqual(late_body["request_id"], old_id)
                self.assert_record(
                    late_body["snapshot"], old_id, kind, "terminal",
                    "cancelled")
                self.assert_status_endpoint(
                    context, new_id, source_id, kind, "working", None)
                self.assert_op_controls_enabled(page)
                self.assert_op_status(page, OP_WORKING[kind])
                expect(self.op(page, "cancel")).to_be_focused(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.primary(page)).to_be_disabled(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assert_source_visible(page, source_id)
                if kind == "source":
                    self.assertEqual(
                        self.snapshot(context)["regions"], [])
                    expect(self.tid(page, "region-item")
                           ).to_have_count(0)
                else:
                    self.assert_questions_preserved(
                        page, context, first_request_id, approved,
                        other)
                self.mark_phase("late_old_response_inert")

                new_cancel = self.click_cancel(page, new_id)
                self.observation["statuses"]["cancel_new"] = (
                    new_cancel.status)
                self.assertEqual(new_cancel.status, 200)
                self.assert_cancel_body(
                    new_cancel.json(), new_id, source_id, kind)
                self.wait_marker("next-provider", "cancel_seen")
                self.assertFalse(
                    self.marker_is_file("next-provider", "returned"))
                self.assert_status_endpoint(
                    context, new_id, source_id, kind, "terminal",
                    "cancelled")
                self.assert_status_endpoint(
                    context, old_id, source_id, kind, "terminal",
                    "cancelled")
                if kind == "source":
                    self.assert_counts(1, 1, 2, 0)
                else:
                    self.assert_counts(1, 1, 1, 3)
                    self.assert_questions_preserved(
                        page, context, first_request_id, approved,
                        other)
                self.assert_clean()
                self.mark_phase("new_cancel_verified_owner")
            finally:
                self.release_quietly(holds)

    # -- 2. early 404 absence must not release the operation ----------
    def test_early_absence_does_not_release_operation(self):
        holds = ("old-admission", "old-provider")
        self.assert_armed(holds)
        kind = self.kind
        path = SOURCE_PATH if kind == "source" else QUESTIONS_PATH
        with self.browser_page() as (context, page):
            try:
                if kind == "source":
                    metadata = self.begin_held_source(page)
                    source_id = metadata["source_id"]
                else:
                    metadata, _source_request_id = (
                        self.start_normal_source(page))
                    source_id = metadata["source_id"]
                    reviewed = self.review_both_regions(page, context)
                    expect(self.generate(page)).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.generate(page).click(
                        timeout=ASSERT_TIMEOUT_MS)
                self.wait_marker("old-admission", "ready")
                old_id = self.assert_provider_command(
                    self.wait_body(page, path, 1), source_id, "old")
                status, payload = self.status_via_context(
                    context, old_id, source_id)
                self.observation["statuses"]["early_status_get"] = (
                    status)
                self.assertEqual(status, 404)
                self.assertEqual(
                    payload, {"detail": "request not found"})
                self.assert_op_controls_enabled(page)
                self.mark_phase("admission_held_no_record_yet")

                check404 = self.click_check(page, old_id)
                self.observation["statuses"]["early_check"] = (
                    check404.status)
                self.assertEqual(check404.status, 404)
                self.assertEqual(
                    check404.json(), {"detail": "request not found"})
                self.assert_op_status(page, OP_UNCONFIRMED)
                expect(self.op(page, "cancel")).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                cancel404 = self.click_cancel(page, old_id)
                self.observation["statuses"]["early_cancel"] = (
                    cancel404.status)
                self.assertEqual(cancel404.status, 404)
                self.assertEqual(
                    cancel404.json(),
                    {"detail": "request not found"})
                self.assert_op_status(page, OP_UNCONFIRMED)
                self.assert_op_controls_enabled(page)
                self.assert_source_visible(page, source_id)
                expect(self.op(page, "retry")).to_be_hidden(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.primary(page)).to_be_disabled(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    self.request_count("POST", path), 1)
                if kind == "questions":
                    expect(self.generate(page)).to_be_disabled(
                        timeout=ASSERT_TIMEOUT_MS)
                    river = self.region_by_section(
                        reviewed, REGION_RIVER)
                    item = self.region_item(page, river)
                    expect(self.tid(
                        item, "region-current-text-input")
                    ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(
                        item, "region-review-commit")
                    ).to_be_disabled(timeout=ASSERT_TIMEOUT_MS)
                self.mark_phase("early_404s_kept_operation_locked")

                self.release_hold("old-admission")
                self.wait_marker("old-provider", "ready")
                self.assertTrue(
                    self.marker_is_file("old-admission", "passed"))
                self.assertTrue(
                    self.marker_is_file("old-provider", "entered"))
                self.assertEqual(
                    self.request_count("POST", path), 1)
                check200 = self.click_check(page, old_id)
                self.observation["statuses"]["check_working"] = (
                    check200.status)
                self.assertEqual(check200.status, 200)
                check_body = check200.json()
                self.assertEqual(check_body["request_id"], old_id)
                self.assertEqual(check_body["source_id"], source_id)
                self.assertEqual(
                    check_body["request"]["request_id"], old_id)
                self.assertEqual(
                    check_body["request"]["kind"], kind)
                self.assertEqual(
                    check_body["request"]["state"], "working")
                self.assert_op_status(page, OP_STILL_WORKING)
                self.assertEqual(
                    self.request_count("POST", path), 1)
                self.mark_phase("admitted_check_working_no_duplicate")

                cancel200 = self.click_cancel(page, old_id)
                self.observation["statuses"]["cancel_old"] = (
                    cancel200.status)
                self.assertEqual(cancel200.status, 200)
                self.assert_cancel_body(
                    cancel200.json(), old_id, source_id, kind)
                self.wait_marker("old-provider", "cancel_seen")
                self.assertFalse(
                    self.marker_is_file("old-provider", "returned"))
                self.assert_op_status(page, OP_CANCELLED)
                if kind == "source":
                    expect(self.op(page, "retry")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.op(page, "retry")).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.primary(page)).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                else:
                    expect(self.generate(page)).to_be_enabled(
                        timeout=ASSERT_TIMEOUT_MS)
                self.mark_phase("cancelled_ui_settled")

                late = self.await_original(
                    page, path, old_id,
                    lambda: self.release_hold("old-provider"))
                self.observation["statuses"]["late_old"] = late.status
                self.assertEqual(late.status, 409)
                late_body = late.json()
                self.assertEqual(late_body["request_id"], old_id)
                self.assert_record(
                    late_body["snapshot"], old_id, kind, "terminal",
                    "cancelled")
                snapshot = self.snapshot(context)
                self.assertEqual(snapshot["source_id"], source_id)
                self.assertEqual(snapshot["questions"], [])
                if kind == "source":
                    self.assertEqual(snapshot["regions"], [])
                    self.assert_counts(1, 1, 1, 0)
                else:
                    self.assertEqual(
                        self.region_by_section(
                            snapshot, REGION_RIVER)["current_text"],
                        self.fx["EDITED_A"])
                    self.assertEqual(
                        self.region_by_section(
                            snapshot, REGION_TREES)["current_text"],
                        self.fx["EDITED_B"])
                    self.assertTrue(snapshot["can_generate"])
                    self.assert_counts(1, 1, 1, 1)
                self.assert_clean()
                self.mark_phase("late_cancelled_admitted_nothing")
            finally:
                self.release_quietly(holds)

    # -- 3. committed completion wins; held success stays inert -------
    def test_completion_wins_cancel_and_old_response_is_inert(self):
        self.assertEqual(
            self.kind, "source",
            "this case runs against a separately served test application")
        holds = ("old-response",)
        self.assert_armed(holds)
        fx = self.fx
        with self.browser_page() as (context, page):
            try:
                metadata = self.begin_held_source(page)
                source_id = metadata["source_id"]
                self.wait_marker("old-response", "ready")
                self.assertTrue(
                    self.marker_is_file("old-response", "produced"))
                old_id = self.assert_provider_command(
                    self.wait_body(page, SOURCE_PATH, 1),
                    source_id, "old")
                self.assert_status_endpoint(
                    context, old_id, source_id, "source",
                    "terminal", "ok")
                committed = self.snapshot(context)
                self.assertEqual(len(committed["regions"]), 2)
                self.assert_record(
                    committed, old_id, "source", "terminal", "ok")
                self.assert_op_controls_enabled(page)
                self.mark_phase("success_committed_response_held")

                cancel409 = self.click_cancel(page, old_id)
                self.observation["statuses"]["cancel_old"] = (
                    cancel409.status)
                self.assertEqual(cancel409.status, 409)
                self.assertEqual(
                    cancel409.json(),
                    {"detail": "request is closed"})
                self.assert_status_endpoint(
                    context, old_id, source_id, "source",
                    "terminal", "ok")
                self.assert_op_status(page, OP_UNCONFIRMED)
                expect(self.op(page, "check")).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                self.mark_phase("cancel_409_completion_kept")

                check200 = self.click_check(page, old_id)
                self.observation["statuses"]["check_ok"] = (
                    check200.status)
                self.assertEqual(check200.status, 200)
                check_body = check200.json()
                self.assertEqual(check_body["request_id"], old_id)
                self.assertEqual(check_body["source_id"], source_id)
                self.assertEqual(
                    check_body["request"]["state"], "terminal")
                self.assertEqual(
                    check_body["request"]["outcome"], "ok")
                snapshot = self.snapshot(context)
                river = self.region_by_section(snapshot, REGION_RIVER)
                trees = self.region_by_section(snapshot, REGION_TREES)
                for region in (river, trees):
                    self.assertEqual(region["request_id"], old_id)
                    self.assertEqual(region["inclusion"], "unreviewed")
                    item = self.region_item(page, region)
                    expect(item).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(
                        item, "region-current-text-input")
                    ).to_have_value(
                        region["current_text"],
                        timeout=ASSERT_TIMEOUT_MS)
                self.assert_op_status(page, OP_SUCCEEDED)
                expect(self.tid(page, "workspace-status")
                       ).to_have_text(
                           WS_RECEIVED, timeout=ASSERT_TIMEOUT_MS)
                self.assert_source_visible(page, source_id)
                original = self.api_bytes(
                    context, metadata["original_url"])
                self.assertEqual(original, self.png_bytes)
                self.assertEqual(
                    hashlib.sha256(original).hexdigest(),
                    metadata["source_sha256"])
                baseline_raw = self.raw_result(context, old_id)
                self.assertEqual(baseline_raw["outcome"], "ok")
                actual_model = baseline_raw["model_name"]
                self.assertIsInstance(actual_model, str)
                self.assertTrue(actual_model)
                expect(self.tid(page, "source-model-name")
                       ).to_have_text(
                           actual_model, timeout=ASSERT_TIMEOUT_MS)
                self.mark_phase("check_recovered_success_snapshot")

                self.assertFalse(
                    self.marker_is_file("old-response", "released"))
                item = self.region_item(page, river)
                label_before = river["current_label"]
                checkpoint = len(self.requests_log)
                self.tid(item, "region-current-text-input").fill(
                    fx["EDITED_A"], timeout=ASSERT_TIMEOUT_MS)
                self.tid(item, "region-inclusion-input").select_option(
                    "included", timeout=ASSERT_TIMEOUT_MS)
                path = self.region_path(river)
                commit = self.tid(item, "region-review-commit")
                response = self.capture_click(
                    page, "PUT", path, commit)
                self.assertEqual(response.status, 200)
                self.assertEqual(
                    self.request_bodies("PUT", path, checkpoint),
                    [{"inclusion": "included",
                      "current_text": fx["EDITED_A"],
                      "current_label": label_before}])
                edited_snapshot = self.snapshot(context)
                edited_river = self.region_by_section(
                    edited_snapshot, REGION_RIVER)
                self.assertEqual(
                    edited_river["current_text"], fx["EDITED_A"])
                self.assertEqual(
                    edited_river["raw_text"], fx["RAW_A"])
                self.assertEqual(
                    edited_river["inclusion"], "included")
                expect(commit).to_be_focused(
                    timeout=ASSERT_TIMEOUT_MS)
                self.observation["focus"] = self.focus_name(page)
                # The real save owns the persistent status now;
                # capture its actual text before the held response
                # is released so the late response cannot take it.
                status_after_save = self.tid(
                    page, "workspace-status").inner_text(
                        timeout=ASSERT_TIMEOUT_MS)
                self.assertTrue(
                    status_after_save.strip(),
                    "workspace status empty after region save")
                self.assert_op_status(page, OP_SUCCEEDED)
                expect(self.tid(page, "workspace-status")
                       ).to_have_text(
                           status_after_save,
                           timeout=ASSERT_TIMEOUT_MS)
                self.mark_phase("region_edited_while_response_held")

                late = self.await_original(
                    page, SOURCE_PATH, old_id,
                    lambda: self.release_hold("old-response"))
                self.observation["statuses"]["late_old"] = late.status
                self.assertEqual(late.status, 201)
                late_body = late.json()
                self.assertEqual(late_body["request_id"], old_id)
                self.assertEqual(
                    late_body["snapshot"]["source_id"], source_id)
                stale_river = self.region_by_section(
                    late_body["snapshot"], REGION_RIVER)
                self.assertEqual(
                    stale_river["current_text"], fx["RAW_A"])
                self.assertEqual(
                    stale_river["inclusion"], "unreviewed")
                current = self.snapshot(context)
                current_river = self.region_by_section(
                    current, REGION_RIVER)
                self.assertEqual(
                    current_river["current_text"], fx["EDITED_A"])
                self.assertEqual(
                    current_river["inclusion"], "included")
                item = self.region_item(page, current_river)
                expect(self.tid(item, "region-current-text-input")
                       ).to_have_value(
                           fx["EDITED_A"], timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-inclusion-input")
                       ).to_have_value(
                           "included", timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-review-commit")
                       ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
                self.assert_op_status(page, OP_SUCCEEDED)
                expect(self.tid(page, "workspace-status")
                       ).to_have_text(
                           status_after_save,
                           timeout=ASSERT_TIMEOUT_MS)
                self.assert_source_visible(page, source_id)
                self.assertEqual(
                    self.raw_result(context, old_id), baseline_raw)
                expect(self.tid(page, "source-model-name")
                       ).to_have_text(
                           actual_model, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-request-id")
                       ).to_have_text(
                           old_id, timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(item, "region-model-name")
                       ).to_have_text(
                           actual_model, timeout=ASSERT_TIMEOUT_MS)
                self.assert_counts(1, 1, 1, 0)
                self.assert_clean()
                self.mark_phase("held_success_response_inert")
            finally:
                self.release_quietly(holds)

    # -- 4. lost cancel ACK recovers by exact status ------------------
    def test_lost_cancel_ack_recovers_by_exact_status(self):
        self.assertEqual(
            self.kind, "source",
            "this case runs against a separately served test application")
        holds = ("old-provider", "old-response")
        self.assert_armed(holds)
        with self.browser_page() as (context, page):
            try:
                metadata = self.begin_held_source(page)
                source_id = metadata["source_id"]
                self.wait_marker("old-provider", "ready")
                old_id = self.assert_provider_command(
                    self.wait_body(page, SOURCE_PATH, 1),
                    source_id, "old")
                self.assert_status_endpoint(
                    context, old_id, source_id, "source",
                    "working", None)
                self.assert_op_controls_enabled(page)
                self.assert_op_status(page, OP_WORKING["source"])
                self.mark_phase("provider_held_before_cancel_loss")

                # Arm the sole permitted loss for this exact URL.
                self.cancel_loss_path = self.cancel_path(old_id)
                self.op(page, "cancel").click(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assert_op_status(page, OP_UNCONFIRMED)
                self.assertIsNotNone(self.forwarded_cancel)
                forwarded = self.forwarded_cancel
                self.observation["statuses"]["forwarded_cancel"] = (
                    forwarded["status"])
                self.assertEqual(forwarded["status"], 200)
                self.assertEqual(forwarded["request_id"], old_id)
                self.assertEqual(forwarded["source_id"], source_id)
                self.assertEqual(forwarded["record_state"], "terminal")
                self.assertEqual(
                    forwarded["record_outcome"], "cancelled")
                self.assertEqual(
                    self.request_count(
                        "POST", self.cancel_path(old_id)), 1)
                self.assertEqual(
                    self.request_count("POST", SOURCE_PATH), 1)
                expect(self.tid(page, "workspace-status")
                       ).to_have_text(
                           OP_UNCONFIRMED,
                           timeout=ASSERT_TIMEOUT_MS)
                self.assert_source_visible(page, source_id)
                expect(self.primary(page)).to_be_disabled(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.op(page, "retry")).to_be_hidden(
                    timeout=ASSERT_TIMEOUT_MS)
                self.wait_marker("old-provider", "cancel_seen")
                self.assertFalse(
                    self.marker_is_file("old-provider", "returned"))
                self.assertFalse(
                    self.marker_is_file("old-provider", "released"))
                expect(self.op(page, "check")).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.op(page, "check")).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                self.mark_phase("cancel_ack_lost_unresolved")

                check200 = self.click_check(page, old_id)
                self.observation["statuses"]["check_cancelled"] = (
                    check200.status)
                self.assertEqual(check200.status, 200)
                check_body = check200.json()
                self.assertEqual(check_body["request_id"], old_id)
                self.assertEqual(check_body["source_id"], source_id)
                self.assertEqual(
                    check_body["request"]["state"], "terminal")
                self.assertEqual(
                    check_body["request"]["outcome"], "cancelled")
                snapshot = self.snapshot(context)
                self.assert_record(
                    snapshot, old_id, "source", "terminal",
                    "cancelled")
                self.assertEqual(snapshot["regions"], [])
                self.assertEqual(snapshot["questions"], [])
                self.assert_op_status(page, OP_CANCELLED)
                expect(self.tid(page, "workspace-status")
                       ).to_have_text(
                           WS_ANALYSIS_CANCELLED,
                           timeout=ASSERT_TIMEOUT_MS)
                expect(self.op(page, "retry")).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.op(page, "retry")).to_be_enabled(
                    timeout=ASSERT_TIMEOUT_MS)
                self.assertEqual(
                    self.request_count(
                        "POST", self.cancel_path(old_id)), 1)
                self.assertEqual(
                    self.request_count("POST", SOURCE_PATH), 1)
                self.assertFalse(
                    self.marker_is_file("old-provider", "returned"))
                self.mark_phase("check_recovered_cancelled")

                self.release_hold("old-provider")
                self.wait_marker("old-response", "ready")
                self.assertTrue(
                    self.marker_is_file("old-response", "produced"))
                late = self.await_original(
                    page, SOURCE_PATH, old_id,
                    lambda: self.release_hold("old-response"))
                self.observation["statuses"]["late_old"] = late.status
                self.assertEqual(late.status, 409)
                late_body = late.json()
                self.assertEqual(late_body["request_id"], old_id)
                self.assert_record(
                    late_body["snapshot"], old_id, "source",
                    "terminal", "cancelled")
                final = self.snapshot(context)
                self.assertEqual(final["source_id"], source_id)
                self.assertEqual(final["regions"], [])
                self.assertEqual(final["questions"], [])
                expect(self.tid(page, "region-item")).to_have_count(0)
                expect(self.tid(page, "question-card")
                       ).to_have_count(0)
                self.assert_op_status(page, OP_CANCELLED)
                expect(self.tid(page, "workspace-status")
                       ).to_have_text(
                           WS_ANALYSIS_CANCELLED,
                           timeout=ASSERT_TIMEOUT_MS)
                self.assert_counts(1, 1, 1, 0)
                self.assert_clean()
                self.mark_phase("late_cancelled_added_nothing")
            finally:
                self.release_quietly(holds)


if __name__ == "__main__":
    unittest.main()

