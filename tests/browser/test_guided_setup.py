"""First usable-flow browser tracer: prepare page, then confirm.

This file targets the NEW guided-setup contract below. It does
not describe current behavior, where one click sends settings,
Start and source analysis together. Here the setup primary
``prepare-page`` (``تهيئة الصفحة``) sends settings plus exactly
one Start and ZERO source-analysis requests. Only the explicit
``confirm-page`` control (``تحليل هذه الصفحة``), shown after the
Start 201 and image readiness, sends the single source-analysis
request, with no second Start. Any state before that explicit
confirmation is preparation only, never analysis.

Navigation uses ``workflow-nav-setup/source/questions/preview``
(``type=button``). ``#work-pane`` carries ``data-active-step``
(``setup|source|questions|preview``); inactive stage bodies are
``hidden`` with no exposed focusables. Setup/work/source panes
keep ``setup-form``/``work-pane``/``source-pane``. Preview must
never become active merely because analysis finished; question
generation and approval belong to a later tracer and are not
exercised here.

The app is served separately and reached only through
``BROWSER_BASE_URL`` with ``BROWSER_EXECUTABLE`` (headless
Chromium, sandbox enabled). The parent server injects the
existing synthetic test provider; this process imports no app,
provider or model code, fabricates no DOM, and mutates only
through real page controls. A route guard passes same-origin
requests unchanged and aborts/records foreign ones, substituting
no response. Snapshot, page-metadata, original and preview reads
are GET only. Fixture constants and the tiny PNG come from
``test_review_journey.load_fixture()`` (module imported directly;
its test case is never imported, so discovery is not doubled).
This is a synthetic test, never a user PDF or live inference.

Held-wait protocol (second test, parent-owned directory in
``BROWSER_HOLD_DIR``, fresh per run): the test writes
``ARM.json`` as ``{"phase": "provider", "method": "POST",
"path": "/api/session/source-analysis"}``; the server holds the
first matching invocation that arrives while armed and writes
``READY.json`` echoing method/path (plus request/source when
known); the test releases by writing ``RELEASE`` (content
ignored), in ``finally`` even on failure. This is a controlled
provider wait, NOT a host failure. Nothing here changes any
model/HTTP deadline or retries automatically; timeouts below
bound only this test's own waits.

Optional ``BROWSER_ARTIFACT_DIR`` receives per-test viewport
screenshots and compact cookie-free observation JSON. Bootstrap
(load, panes, labelled inputs) is checked before any guided
assertion, so a dead server or missing session is reported as a
bootstrap failure, not as a guided-flow result. If the first
guided control is absent, the failure names the expectations
that were not executed; they are also saved in the JSON.
"""
import contextlib
import hashlib
import json
import os
import pathlib
import sys
import time
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import test_review_journey as _review_fixture

NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000
HOLD_READY_TIMEOUT_S = 15.0
RELEASE_TIMEOUT_S = 20.0

SETTINGS_PATH = "/api/session/settings"
START_PATH = "/api/session/start"
ANALYSIS_PATH = "/api/session/source-analysis"
SNAPSHOT_PATH = "/api/session/snapshot"
PAGE_PATH = "/api/session/page"

PREPARE_LABEL = "تهيئة الصفحة"
CONFIRM_LABEL = "تحليل هذه الصفحة"
OLD_START_LABEL = "بدء تحليل الصفحة"
GRADE_LABEL = "الصف الدراسي"
OBJECTIVE_LABEL = "الهدف التعليمي"
PAGE_LABEL = "رقم الصفحة"
FILE_LABEL = "ملف صفحة المصدر"
COMPLETION_PHRASES = (
    "اكتمل الطلب", "طُبّقت نتيجته", "وصلت مقترحات", "أُغلق الطلب")

AFTER_GATE = [
    "fill fixture through labelled inputs and prepare one page",
    "zero source-analysis before explicit confirmation",
    "image, source id and page metadata after Start 201",
    "confirm-page as the only live initial-analysis control",
    "one source-analysis 201 reaching two synthetic regions",
    "active source step with setup hidden and heading in viewport",
    "setup/source round-trip preserving values, counts and state",
]


class GuidedSetupBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx, cls.png_bytes = _review_fixture.load_fixture()
        cls.fixture_id = {
            "grade": cls.fx["GRADE"],
            "objective": cls.fx["OBJECTIVE"],
            "mark": cls.fx.get("MARK"),
            "png_sha256": hashlib.sha256(cls.png_bytes).hexdigest(),
            "png_bytes": len(cls.png_bytes),
        }

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
        self.unexecuted = []
        self.observations = {
            "test": self._testMethodName,
            "fixture": self.fixture_id,
            "origin": self.base_origin,
        }

    # -- recording and route guarding ----------------------------
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
        if parts.scheme in ("http", "https"):
            if "{0.scheme}://{0.netloc}".format(
                    parts) != self.base_origin:
                self.external_urls.append(request.url)

    def capture_response(self, response):
        self.responses_log.append({
            "method": response.request.method,
            "path": urlsplit(response.url).path,
            "status": response.status})

    def req_count(self, method, path):
        return sum(1 for entry in self.requests_log
                   if entry["method"] == method
                   and entry["path"] == path)

    def resp_count(self, method, path, status):
        return sum(1 for entry in self.responses_log
                   if entry["method"] == method
                   and entry["path"] == path
                   and entry["status"] == status)

    def req_entries(self, method, path):
        return [entry for entry in self.requests_log
                if entry["method"] == method
                and entry["path"] == path]

    def mutations_since(self, index):
        return [(entry["method"], entry["path"])
                for entry in self.requests_log[index:]
                if entry["method"] in ("POST", "PUT", "PATCH", "DELETE")]

    def assert_clean_network(self):
        self.assertEqual(self.external_urls, [])
        self.assertEqual(self.page_errors, [])

    @contextlib.contextmanager
    def browser_page(self, viewport):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=self.executable, headless=True,
                chromium_sandbox=True)
            context = None
            page = None
            try:
                context = browser.new_context(
                    base_url=self.base_url, service_workers="block",
                    viewport=viewport)
                context.set_default_timeout(ASSERT_TIMEOUT_MS)
                context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
                base_parts = urlsplit(self.base_url)

                def guard(route):
                    parts = urlsplit(route.request.url)
                    if (parts.scheme in ("data", "blob")
                            or (parts.scheme == base_parts.scheme
                                and parts.netloc == base_parts.netloc)):
                        route.continue_()
                    else:
                        self.external_urls.append(route.request.url)
                        route.abort()

                context.route("**/*", guard)
                page = context.new_page()
                page.on("request", self.capture_request)
                page.on("response", self.capture_response)
                page.on(
                    "pageerror",
                    lambda error: self.page_errors.append(str(error)))
                yield context, page
            finally:
                self._save_evidence(page)
                if context is not None:
                    with contextlib.suppress(Exception):
                        context.close()
                with contextlib.suppress(Exception):
                    browser.close()

    def _save_evidence(self, page, suffix="final"):
        artifact_dir = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        if not artifact_dir:
            return
        with contextlib.suppress(Exception):
            directory = pathlib.Path(artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            stem = f"guided-setup-{self._testMethodName}-{suffix}"
            if page is not None:
                with contextlib.suppress(Exception):
                    page.screenshot(
                        path=str(directory / (stem + ".png")),
                        full_page=False, timeout=ASSERT_TIMEOUT_MS)
            payload = dict(self.observations)
            payload.update({
                "counts": {
                    "settings_put": self.req_count(
                        "PUT", SETTINGS_PATH),
                    "start_post": self.req_count("POST", START_PATH),
                    "start_201": self.resp_count(
                        "POST", START_PATH, 201),
                    "analysis_post": self.req_count(
                        "POST", ANALYSIS_PATH),
                    "analysis_201": self.resp_count(
                        "POST", ANALYSIS_PATH, 201),
                },
                "responses": self.responses_log,
                "mutations": [
                    {"method": m, "path": p}
                    for m, p in self.mutations_since(0)],
                "external_urls": self.external_urls,
                "page_errors": self.page_errors,
                "unexecuted": self.unexecuted,
            })
            (directory / (stem + ".json")).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8")

    # -- inspection helpers (GET only) ---------------------------
    @staticmethod
    def tid(scope, name):
        return scope.get_by_test_id(name)

    @staticmethod
    def work_pane(page):
        return page.locator("#work-pane")

    def snapshot(self, context):
        response = context.request.get(
            self.base_url + SNAPSHOT_PATH,
            timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(response.status, 200, SNAPSHOT_PATH)
        return response.json()

    @staticmethod
    def fingerprint(snap):
        # Complete snapshot, incl. source_revision/settings_revision
        # and requests/regions/questions/slot_outcomes/preview_items.
        return json.dumps(
            snap, sort_keys=True, ensure_ascii=False)

    def active_step(self, page):
        pane = self.work_pane(page)
        self.assertEqual(pane.count(), 1, "#work-pane must exist once")
        return pane.get_attribute("data-active-step")

    def assert_active_step(self, page, expected):
        expect(self.work_pane(page)).to_have_attribute(
            "data-active-step", expected, timeout=ASSERT_TIMEOUT_MS)

    def assert_no_exposed_hidden_focusables(self, page):
        exposed = page.evaluate(
            "() => { const els = document.querySelectorAll("
            "\"[hidden] :is(button,input,select,textarea,a[href],"
            "[tabindex])\"); let n = 0; for (const el of els) {"
            " if (el.getClientRects().length > 0) n += 1; }"
            " return n; }")
        self.assertEqual(exposed, 0)

    def measure(self, page, testids):
        """Read rects, centers and hit targets in place."""
        return page.evaluate(
            "(ids) => {"
            " const out = {innerWidth: window.innerWidth,"
            " innerHeight: window.innerHeight, items: {}};"
            " for (const id of ids) {"
            "  const el = document.querySelector("
            "   `[data-testid=\"${id}\"]`);"
            "  if (!el) { out.items[id] = {present: false}; continue; }"
            "  const r = el.getBoundingClientRect();"
            "  const cx = r.left + r.width / 2;"
            "  const cy = r.top + r.height / 2;"
            "  const inView = r.width > 0 && r.height > 0"
            "   && cx >= 0 && cy >= 0"
            "   && cx < window.innerWidth && cy < window.innerHeight;"
            "  const hit = inView"
            "   ? document.elementFromPoint(cx, cy) : null;"
            "  out.items[id] = {present: true,"
            "   hidden: !!el.hidden, disabled: !!el.disabled,"
            "   text: (el.textContent || '').trim(),"
            "   rect: {top: r.top, bottom: r.bottom, left: r.left,"
            "    right: r.right, width: r.width, height: r.height},"
            "   center: {x: cx, y: cy}, centerInView: inView,"
            "   fullyInView: r.width > 0 && r.height > 0"
            "    && r.left >= 0 && r.top >= 0"
            "    && r.right <= window.innerWidth"
            "    && r.bottom <= window.innerHeight,"
            "   hit: !!hit && (hit === el || el.contains(hit))}; }"
            " const wp = document.querySelector('#work-pane')"
            "  || document.querySelector("
            "   \"[data-testid='work-pane']\");"
            " out.activeStep = wp"
            "  ? wp.getAttribute('data-active-step') : null;"
            " return out; }",
            list(testids))

    def assert_center_reachable(self, measured, testid, full=False):
        item = measured["items"][testid]
        self.assertTrue(item["present"], testid)
        self.assertFalse(item["hidden"], testid)
        self.assertTrue(item["centerInView"], (testid, item))
        self.assertTrue(item["hit"], (testid, item))
        if full:
            self.assertTrue(item["fullyInView"], (testid, item))

    # -- guided sequence (self-contained; new order only) --------
    def bootstrap(self, page):
        response = page.goto(
            "/", wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS)
        self.assertIsNotNone(response, "bootstrap: no response")
        self.assertEqual(
            response.status, 200,
            f"bootstrap: unexpected status for {page.url}")
        page.wait_for_load_state(
            "networkidle", timeout=NAV_TIMEOUT_MS)
        expect(self.tid(page, "setup-form")).to_be_visible(
            timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(page, "source-pane")).to_have_count(1)
        for label in (GRADE_LABEL, OBJECTIVE_LABEL, PAGE_LABEL,
                      FILE_LABEL):
            expect(page.get_by_label(label, exact=True)).to_have_count(1)
        self.assertEqual(
            self.page_errors, [], "bootstrap: page errors on load")

    def guided_gate(self, page):
        pane = self.work_pane(page)
        if (pane.count() != 1
                or self.tid(page, "prepare-page").count() == 0
                or pane.get_attribute("data-active-step") is None):
            self.unexecuted = list(AFTER_GATE)
            self.fail(
                "guided setup not present (need #work-pane with "
                "data-active-step and prepare-page); unexecuted: "
                + "; ".join(self.unexecuted))
        self.assertEqual(
            pane.get_attribute("data-testid"), "work-pane")
        self.assert_active_step(page, "setup")
        for step in ("setup", "source", "questions", "preview"):
            nav = self.tid(page, f"workflow-nav-{step}")
            expect(nav).to_have_count(1)
            self.assertEqual(nav.get_attribute("type"), "button")
        prepare = self.tid(page, "prepare-page")
        expect(prepare).to_have_count(1)
        expect(prepare).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(prepare).to_have_text(
            PREPARE_LABEL, timeout=ASSERT_TIMEOUT_MS)
        for candidate in self.tid(page, "confirm-page").all():
            self.assertFalse(candidate.is_visible())
        for later in ("region-list", "question-list", "preview-list"):
            for candidate in self.tid(page, later).all():
                self.assertFalse(candidate.is_visible(), later)
        self.assert_no_exposed_hidden_focusables(page)

    def prepare_one_page(self, page, context):
        """Fill the real setup inputs; prepare only (no analysis)."""
        fx = self.fx
        page.get_by_label(GRADE_LABEL, exact=True).fill(fx["GRADE"])
        page.get_by_label(OBJECTIVE_LABEL, exact=True).fill(
            fx["OBJECTIVE"])
        page.get_by_label(PAGE_LABEL, exact=True).fill("1")
        page.get_by_label(FILE_LABEL, exact=True).set_input_files({
            "name": "source-page.png",
            "mimeType": "image/png", "buffer": self.png_bytes})
        self.assertEqual(self.req_count("POST", START_PATH), 0)
        self.assertEqual(self.req_count("POST", ANALYSIS_PATH), 0)
        with page.expect_response(
                lambda item: item.request.method == "POST"
                and urlsplit(item.url).path == START_PATH
                and item.status == 201,
                timeout=ASSERT_TIMEOUT_MS):
            self.tid(page, "prepare-page").click()
        page.wait_for_load_state(
            "networkidle", timeout=NAV_TIMEOUT_MS)
        self.assertEqual(self.req_count("POST", START_PATH), 1)
        self.assertEqual(self.resp_count("POST", START_PATH, 201), 1)
        self.assertEqual(
            self.req_count("POST", ANALYSIS_PATH), 0,
            "preparation must send zero source-analysis requests")
        settings = self.req_entries("PUT", SETTINGS_PATH)
        self.assertEqual(len(settings), 1)
        body = settings[0].get("json") or {}
        self.assertEqual(body.get("grade"), fx["GRADE"])
        self.assertEqual(body.get("objective"), fx["OBJECTIVE"])
        self.assertIn("page_index=0", self.req_entries(
            "POST", START_PATH)[0]["query"])
        # Preparation only: still the setup step, image ready.
        # Loading alone is readiness only; a preview hidden in an
        # inactive phone pane is inspected via the pane tabs below.
        self.assert_active_step(page, "setup")
        page.wait_for_function(
            "() => { const img = document.querySelector("
            "\"[data-testid='source-preview']\"); return !!img"
            " && img.complete && img.naturalWidth > 0; }",
            timeout=ASSERT_TIMEOUT_MS)
        source_id = (self.tid(page, "source-id")
                     .text_content(timeout=ASSERT_TIMEOUT_MS) or ""
                     ).strip()
        self.assertTrue(source_id, "prepared source id must show")
        selected = (self.tid(page, "selected-page-number")
                    .text_content(timeout=ASSERT_TIMEOUT_MS) or ""
                    ).strip()
        self.assertEqual(selected, "١ من ١")
        snap = self.snapshot(context)
        self.assertEqual(snap["source_id"], source_id)
        self.assertEqual(snap["grade"], fx["GRADE"])
        self.assertEqual(snap["objective"], fx["OBJECTIVE"])
        self.assertEqual(snap["regions"], [])
        self.assertEqual(snap["questions"], [])
        # Fixture is one tiny PNG (page 0 of 1), not a PDF case.
        page_response = context.request.get(
            self.base_url + PAGE_PATH, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(page_response.status, 200, PAGE_PATH)
        meta = page_response.json()
        self.assertEqual(meta["source_id"], source_id)
        self.assertEqual(meta["page_index"], 0)
        self.assertEqual(meta["page_count"], 1)
        original_url = meta["original_url"]
        original_abs = (original_url if original_url.startswith(
            ("http://", "https://")) else self.base_url + original_url)
        self.assertTrue(original_abs.startswith(self.base_origin))
        original_resp = context.request.get(
            original_abs, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(original_resp.status, 200)
        original_bytes = original_resp.body()
        self.assertEqual(original_bytes, self.png_bytes)
        self.assertEqual(
            hashlib.sha256(original_bytes).hexdigest(),
            meta["source_sha256"])
        preview_url = meta["preview_url"]
        preview_abs = (preview_url if preview_url.startswith(
            ("http://", "https://")) else self.base_url + preview_url)
        self.assertTrue(preview_abs.startswith(self.base_origin))
        preview_resp = context.request.get(
            preview_abs, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(preview_resp.status, 200)
        preview_bytes = preview_resp.body()
        self.assertTrue(
            preview_bytes.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(
            hashlib.sha256(preview_bytes).hexdigest(),
            meta["render_sha256"])
        preview = self.tid(page, "source-preview")
        dims = preview.evaluate(
            "img => ({width: img.naturalWidth,"
            " height: img.naturalHeight})")
        self.assertEqual(dims, {
            "width": meta["width"], "height": meta["height"]})
        if not preview.is_visible():
            checkpoint = len(self.requests_log)
            self.tid(page, "pane-tab-source").click()
            expect(preview).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            self.tid(page, "pane-tab-work").click()
            expect(self.tid(page, "confirm-page")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(checkpoint), [])
            self.assert_active_step(page, "setup")
        confirm = self.tid(page, "confirm-page")
        expect(confirm).to_have_count(1)
        expect(confirm).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        expect(confirm).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
        expect(confirm).to_have_text(
            CONFIRM_LABEL, timeout=ASSERT_TIMEOUT_MS)
        nearby = confirm.evaluate(
            "el => { let n = el; for (let i = 0; i < 3"
            " && n.parentElement; i += 1) { n = n.parentElement; }"
            " return n.innerText || ''; }")
        self.observations["confirm_context"] = nearby
        self.observations["prepared_source_id"] = source_id
        self.assertGreater(len(nearby.strip()), len(CONFIRM_LABEL))
        note = self.tid(page, "selected-file-page-note")
        expect(note).to_have_count(1)
        expect(note).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        note_text = (note.text_content(
            timeout=ASSERT_TIMEOUT_MS) or "").strip()
        self.observations["selected_file_page_note"] = note_text
        self.assertIn("الملف", note_text)
        self.assertIn("المطبوع", note_text)
        self.assertIn("الملف", nearby)
        self.assertIn("المطبوع", nearby)
        # Fields keep their original labels and entered values.
        expect(page.get_by_label(GRADE_LABEL, exact=True)
               ).to_have_value(fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
        expect(page.get_by_label(OBJECTIVE_LABEL, exact=True)
               ).to_have_value(
            fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
        expect(page.get_by_label(PAGE_LABEL, exact=True)
               ).to_have_value("1", timeout=ASSERT_TIMEOUT_MS)
        # The old combined start label is not a second live CTA.
        old = page.get_by_role(
            "button", name=OLD_START_LABEL, exact=True)
        for candidate in old.all():
            self.assertFalse(
                candidate.is_visible() and candidate.is_enabled(),
                "old start must not remain a live control")
        return source_id

    def assert_source_step(self, page, context, source_id):
        snap = self.snapshot(context)
        self.assertEqual(snap["source_id"], source_id)
        self.assertEqual(len(snap["regions"]), 2)
        self.assertTrue(snap["source_analysis_terminal"])
        self.assertEqual(
            {item["external_section_id"] for item in snap["regions"]},
            {"synthetic-section-river", "synthetic-section-trees"})
        self.assertEqual(
            {item["raw_text"] for item in snap["regions"]},
            {self.fx["RAW_A"], self.fx["RAW_B"]})
        self.assert_active_step(page, "source")
        expect(self.tid(page, "setup-form")).to_be_hidden(
            timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(self.work_pane(page), "region-list")
               ).to_have_count(1)
        self.assertEqual(self.tid(
            self.tid(page, "source-pane"), "region-list").count(), 0)
        expect(self.tid(page, "region-item")).to_have_count(2)
        first = snap["regions"][0]
        expect(page.locator(
            "[data-testid='region-item']"
            f"[data-region-id=\"{first['region_id']}\"]")
        ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        measured = self.measure(page, ("region-heading",))
        self.observations["heading_measure"] = measured
        self.assert_center_reachable(measured, "region-heading")
        self.assert_no_exposed_hidden_focusables(page)
        return snap

    def test_held_confirm_progress_visible_on_phone(self):
        hold_value = os.environ.get("BROWSER_HOLD_DIR", "")
        self.assertTrue(
            hold_value, "BROWSER_HOLD_DIR is required for the hold")
        hold_dir = pathlib.Path(hold_value)
        self.assertTrue(hold_dir.is_dir(), "hold dir must exist")
        arm_path = hold_dir / "ARM.json"
        ready_path = hold_dir / "READY.json"
        self.assertFalse(
            ready_path.exists(), "stale READY.json: need a fresh dir")
        with self.browser_page(
                {"width": 390, "height": 844}) as (context, page):
            self.bootstrap(page)
            self.guided_gate(page)
            source_id = self.prepare_one_page(page, context)
            arm_path.write_text(json.dumps({
                "phase": "provider", "method": "POST",
                "path": ANALYSIS_PATH}), encoding="utf-8")
            self.observations["hold_armed"] = True
            try:
                self.tid(page, "confirm-page").click()
                deadline = time.monotonic() + HOLD_READY_TIMEOUT_S
                while (not ready_path.is_file()
                       and time.monotonic() < deadline):
                    page.wait_for_timeout(100)
                self.assertTrue(
                    ready_path.is_file(),
                    "held request never signalled READY.json")
                ready = None
                for _attempt in range(10):
                    try:
                        ready = json.loads(
                            ready_path.read_text(encoding="utf-8"))
                        break
                    except ValueError:
                        page.wait_for_timeout(100)
                self.assertIsInstance(ready, dict)
                self.observations["ready"] = ready
                self.assertEqual(ready.get("method"), "POST")
                self.assertEqual(ready.get("path"), ANALYSIS_PATH)
                if "source_id" in ready:
                    self.assertEqual(ready["source_id"], source_id)
                sent = self.req_entries("POST", ANALYSIS_PATH)
                self.assertEqual(len(sent), 1)
                sent_body = sent[0].get("json") or {}
                self.assertEqual(sent_body.get("source_id"), source_id)
                if "request_id" in ready:
                    self.assertEqual(
                        ready["request_id"],
                        sent_body.get("request_id"))
                self.assertEqual(
                    self.resp_count("POST", ANALYSIS_PATH, 201), 0)
                self.assertEqual(self.req_count("POST", START_PATH), 1)
                # Measure first, exactly as the page was left.
                measured = self.measure(page, (
                    "provider-operation", "provider-operation-status",
                    "provider-operation-cancel",
                    "provider-operation-check", "workspace-status",
                    "prepare-page", "confirm-page"))
                self.observations["hold_measure"] = measured
                self._save_evidence(page, suffix="hold")
                self.assertEqual(measured["activeStep"], "setup")
                self.assert_center_reachable(
                    measured, "provider-operation")
                self.assert_center_reachable(
                    measured, "provider-operation-status")
                self.assert_center_reachable(
                    measured, "provider-operation-cancel", full=True)
                self.assert_center_reachable(
                    measured, "provider-operation-check", full=True)
                for testid in ("workspace-status",
                               "provider-operation-status"):
                    text = measured["items"][testid]["text"]
                    for phrase in COMPLETION_PHRASES:
                        self.assertNotIn(phrase, text, testid)
                for testid in ("prepare-page", "confirm-page"):
                    item = measured["items"][testid]
                    if item["present"]:
                        self.assertTrue(
                            item["hidden"] or item["disabled"],
                            f"{testid} must not invite a second send")
                self.assertEqual(
                    self.tid(page, "region-item").count(), 0)
            finally:
                with contextlib.suppress(Exception):
                    (hold_dir / "RELEASE").write_text(
                        "", encoding="utf-8")
                self.observations["hold_released"] = True
            deadline = time.monotonic() + RELEASE_TIMEOUT_S
            while (self.resp_count("POST", ANALYSIS_PATH, 201) == 0
                   and time.monotonic() < deadline):
                page.wait_for_timeout(100)
            self.assertEqual(
                self.resp_count("POST", ANALYSIS_PATH, 201), 1)
            self.assertEqual(self.req_count("POST", ANALYSIS_PATH), 1)
            self.assertEqual(self.req_count("POST", START_PATH), 1)
            self.assert_source_step(page, context, source_id)
            self.assert_clean_network()

    def test_prepare_confirm_then_source_review(self):
        with self.browser_page(
                {"width": 1280, "height": 900}) as (context, page):
            self.bootstrap(page)
            self.guided_gate(page)
            self.assertEqual(
                self.tid(page, "region-item").count(), 0)
            source_id = self.prepare_one_page(page, context)
            # Original source is servable for this prepared page;
            # reading it must not mutate or navigate the work away.
            checkpoint = len(self.requests_log)
            href = self.tid(
                page, "source-original-link").get_attribute("href")
            self.assertTrue(href, "original link must carry an href")
            absolute = (href if href.startswith(("http://", "https://"))
                        else self.base_url + href)
            self.assertTrue(
                absolute.startswith(self.base_origin), href)
            original = context.request.get(
                absolute, timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(original.status, 200)
            expect(self.tid(page, "source-preview")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(checkpoint), [])
            self.assertEqual(self.req_count("POST", ANALYSIS_PATH), 0)
            # Explicit confirmation: one analysis, no second Start.
            with page.expect_response(
                    lambda item: item.request.method == "POST"
                    and urlsplit(item.url).path == ANALYSIS_PATH
                    and item.status == 201,
                    timeout=ASSERT_TIMEOUT_MS):
                self.tid(page, "confirm-page").click()
            self.assertEqual(self.req_count("POST", START_PATH), 1)
            self.assertEqual(self.req_count("POST", ANALYSIS_PATH), 1)
            sent_body = (self.req_entries(
                "POST", ANALYSIS_PATH)[0].get("json") or {})
            self.assertEqual(sent_body.get("source_id"), source_id)
            self.assertRegex(
                sent_body.get("request_id", ""), r"^[0-9a-f]{32}$")
            snap = self.assert_source_step(page, context, source_id)
            before = self.fingerprint(snap)
            # Re-enter setup, then return: values and state survive,
            # and navigation itself sends no mutation.
            checkpoint = len(self.requests_log)
            self.tid(page, "workflow-nav-setup").click()
            self.assert_active_step(page, "setup")
            expect(self.tid(page, "setup-form")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(page.get_by_label(GRADE_LABEL, exact=True)
                   ).to_have_value(
                self.fx["GRADE"], timeout=ASSERT_TIMEOUT_MS)
            expect(page.get_by_label(OBJECTIVE_LABEL, exact=True)
                   ).to_have_value(
                self.fx["OBJECTIVE"], timeout=ASSERT_TIMEOUT_MS)
            expect(page.get_by_label(PAGE_LABEL, exact=True)
                   ).to_have_value("1", timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(checkpoint), [])
            self.assertEqual(self.req_count("POST", START_PATH), 1)
            self.assertEqual(self.req_count("POST", ANALYSIS_PATH), 1)
            self.assertEqual(
                self.fingerprint(self.snapshot(context)), before)
            checkpoint = len(self.requests_log)
            self.tid(page, "workflow-nav-source").click()
            self.assert_active_step(page, "source")
            expect(self.tid(page, "region-list")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(checkpoint), [])
            self.assertEqual(
                self.fingerprint(self.snapshot(context)), before)
            self.assert_clean_network()


if __name__ == "__main__":
    unittest.main()




