"""First real-browser tracer for the Arabic setup/source increment.

The test harness serves
``teacher_demo.http_app.create_app`` (default provider: none) on a
random 127.0.0.1-only origin and runs this module under Playwright
Python with two required environment variables:

- ``BROWSER_BASE_URL``: the actual served origin.
- ``BROWSER_EXECUTABLE``: the installed browser executable path.

``BROWSER_ARTIFACT_DIR`` is optional; when supplied, one final
screenshot and a cookie-free observations JSON go there and
nowhere else. This test spawns no server, discovers or installs no
browser, loads no credentials, fulfills or mocks no response, and
serves no fabricated page. All DOM actions and API inspections go
to the real backend at ``BROWSER_BASE_URL``; any HTTP(S) request
outside that origin is aborted, recorded, and fails the test
(``data:``/``blob:`` URLs for user-selected bytes are not external
traffic).

The same-origin client contract exercised here is:

- ``<html lang="ar" dir="rtl">``; the permanent
  ``[data-testid="non-durable-warning"]`` with the exact
  non-durable text below; stage heading ``١ إعداد الصفحة`` and
  ``[data-testid="setup-form"]``.
- Accessible labels: file ``ملف صفحة المصدر``, grade
  ``الصف الدراسي``, objective ``الهدف التعليمي``, one-based page
  number ``رقم الصفحة``, and the single primary button
  ``بدء تحليل الصفحة``. ``[data-testid="rights-note"]`` carries the
  exact authorized-use-without-clearance text below.
- Work/source panes ``[data-testid="work-pane"]`` /
  ``[data-testid="source-pane"]``; persistent status
  ``[data-testid="workspace-status"]`` (``role="status"``) outside
  both panes; refusal alert ``[data-testid="setup-error"]``
  (``role="alert"``); source preview image
  ``[data-testid="source-preview"]`` (alt ``معاينة صفحة المصدر``)
  served from ``/api/session/page/preview.png``; original link
  ``[data-testid="source-original-link"]``
  (``فتح ملف الصفحة الأصلي``) to ``/api/session/page/original``;
  whole-page caption ``[data-testid="source-reference-caption"]``;
  collapsed ``[data-testid="source-details"]`` (a ``details``
  element summarised by ``تفاصيل المصدر``) with
  ``source-id``/``source-sha256``/``render-sha256``/
  ``selected-page-number`` value markers; discard warning
  ``[data-testid="replacement-warning"]``; and
  ``[data-testid="future-stages-note"]`` stating the later stages
  are unavailable in this increment.
- Narrow layout: ``[data-testid="pane-switch"]`` is a tablist with
  tabs ``المصدر``/``العمل``
  (``[data-testid="pane-tab-source"]``/``pane-tab-work``,
  ``aria-selected`` maintained). Exactly one pane is exposed: the
  inactive pane carries the ``hidden`` attribute. Each pane
  restores its last focused control when reactivated through the
  switch. At desktop width both panes are exposed and the switch
  is hidden.
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
UNSAVED_GRADE = "الصف السادس"
NON_DURABLE_WARNING = (
    "مساحة تجريبية غير محفوظة: قد يؤدي تحديث الصفحة أو إغلاقها أو "
    "إعادة التشغيل إلى فقدان هذا العمل.")
RIGHTS_NOTE = (
    "استخدم صفحة يحقّ لك استخدامها في هذا النموذج التجريبي. "
    "لا يتحقّق هذا النموذج من الحقوق ولا يمنح تصريحًا.")
UNAVAILABLE_MESSAGE = (
    "خدمة تحليل المصدر غير متصلة في هذا التشغيل. لم يُقترح أيّ "
    "مقطع أو سؤال، وتبقى صفحتك وإعداداتك كما هي.")
SOURCE_CAPTION = "مرجع الدليل: الصفحة كاملة — لا يوجد قناع أو تحديد موضعي آلي."
REPLACEMENT_WARNING = (
    "الضغط على «بدء تحليل الصفحة» مجددًا سيستبدل مساحة العمل الحالية في هذه الجلسة — "
    "مقاطعها وأسئلتها وقراراتها ومعاينتها — ويبدأ فارغًا، حتى لو اخترت الملف والصفحة نفسيهما.")
INVALID_FILE_MESSAGE = (
    "تعذّرت قراءة ملف الصفحة المختار. لم يتغيّر المصدر الحالي؛ "
    "اختر ملف صفحة صالحًا ثم حاول مجددًا.")
FUTURE_STAGES_NOTE = "تبدأ مراجعة المصدر عند وصول مقاطع من التحليل، وتظهر الأسئلة والمعاينة بعد خطوات المراجعة."
INVALID_BYTES = b"synthetic invalid image bytes - not a page image"


def tiny_png(rgb=(200, 30, 40)):
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (*rgb, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def arabic_indic(number):
    return str(number).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


class SetupSourceBrowserTests(unittest.TestCase):
    # -- small observation helpers (no state is changed here) -----
    def api_json(self, context, base_url, path):
        response = context.request.get(
            base_url + path, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(response.status, 200, path)
        return response.json()

    def api_bytes(self, context, base_url, path):
        response = context.request.get(
            base_url + path, timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(response.status, 200, path)
        return response.body()

    def test_setup_source_reference_journey(self):
        base_url = os.environ.get("BROWSER_BASE_URL", "").rstrip("/")
        executable = os.environ.get("BROWSER_EXECUTABLE", "")
        self.assertTrue(
            base_url,
            "BROWSER_BASE_URL (actual served origin) is required")
        self.assertTrue(
            executable,
            "BROWSER_EXECUTABLE (installed browser path) is required")
        self.assertTrue(
            pathlib.Path(executable).is_file(),
            "BROWSER_EXECUTABLE must name the installed browser file")
        artifact_dir = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        png_bytes = tiny_png()
        png_sha256 = hashlib.sha256(png_bytes).hexdigest()
        base_origin = "{0.scheme}://{0.netloc}".format(
            urlsplit(base_url))

        requests_log = []
        responses_log = []
        external_urls = []
        page_errors = []
        observations = {"base_origin": base_origin}

        def capture_request(request):
            parts = urlsplit(request.url)
            entry = {"method": request.method, "path": parts.path,
                     "query": parts.query}
            with contextlib.suppress(Exception):
                if request.post_data:
                    try:
                        entry["json"] = json.loads(request.post_data)
                    except ValueError:
                        entry["post_data_length"] = len(request.post_data)
            with contextlib.suppress(Exception):
                buffer = request.post_data_buffer
                if buffer:
                    entry["body_sha256"] = hashlib.sha256(
                        buffer).hexdigest()
                    entry["body_length"] = len(buffer)
            requests_log.append(entry)

        def capture_response(response):
            responses_log.append({
                "method": response.request.method,
                "path": urlsplit(response.url).path,
                "status": response.status, "response": response})

        def guard_external(route, request):
            parts = urlsplit(request.url)
            if parts.scheme in ("data", "blob"):
                route.continue_()
                return
            if "{0.scheme}://{0.netloc}".format(parts) == base_origin:
                route.continue_()
                return
            external_urls.append(request.url)
            route.abort()

        def mutations_since(index):
            return [entry for entry in requests_log[index:]
                    if entry["method"] in ("POST", "PUT", "PATCH")]

        def mutation_paths(entries):
            return [(entry["method"], entry["path"]) for entry in entries]

        def responses_since(index, method, path):
            return [entry for entry in responses_log[index:]
                    if entry["method"] == method
                    and entry["path"] == path]

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=executable, headless=True,
                chromium_sandbox=True)
            context = None
            page = None
            try:
                context = browser.new_context(
                    base_url=base_url, service_workers="block",
                    viewport={"width": 1280, "height": 900})
                context.set_default_timeout(ASSERT_TIMEOUT_MS)
                context.set_default_navigation_timeout(NAV_TIMEOUT_MS)
                context.route("**/*", guard_external)
                page = context.new_page()
                page.on("request", capture_request)
                page.on("response", capture_response)
                page.on("pageerror",
                        lambda error: page_errors.append(str(error)))
                try:
                    response = page.goto(
                        "/", wait_until="domcontentloaded",
                        timeout=NAV_TIMEOUT_MS)
                    self.assertIsNotNone(response, "no response for /")
                    # First UI expectation: a real setup page at /.
                    # Today this is 404 — the genuine browser RED.
                    self.assertEqual(response.status, 200, page.url)
                    html = page.locator("html")
                    self.assertEqual(html.get_attribute("lang"), "ar")
                    self.assertEqual(html.get_attribute("dir"), "rtl")
                    expect(page.get_by_role(
                        "heading", name="١ إعداد الصفحة", exact=True)
                        ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                    warning = page.get_by_test_id("non-durable-warning")
                    expect(warning).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(warning).to_have_text(
                        NON_DURABLE_WARNING, timeout=ASSERT_TIMEOUT_MS)
                    rights_note = page.get_by_test_id("rights-note")
                    expect(rights_note).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(rights_note).to_have_text(
                        RIGHTS_NOTE, timeout=ASSERT_TIMEOUT_MS)
                    form = page.get_by_test_id("setup-form")
                    expect(form).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(page.get_by_test_id("future-stages-note")
                           ).to_have_text(
                        FUTURE_STAGES_NOTE, timeout=ASSERT_TIMEOUT_MS)
                    status_line = page.get_by_test_id("workspace-status")
                    expect(status_line).to_have_attribute(
                        "role", "status", timeout=ASSERT_TIMEOUT_MS)
                    page.wait_for_load_state(
                        "networkidle", timeout=NAV_TIMEOUT_MS)
                    load_mutations = mutations_since(0)
                    self.assertLessEqual(
                        mutation_paths(load_mutations).count(
                            ("POST", "/api/sessions")), 1)
                    self.assertTrue(all(
                        path == "/api/sessions"
                        for _method, path in
                        mutation_paths(load_mutations)),
                        f"unexpected load mutations: {load_mutations}")
                    self.assertEqual(external_urls, [])

                    # Fill/upload alone mutates nothing on the server.
                    fill_at = len(requests_log)
                    grade_input = page.get_by_label(
                        "الصف الدراسي", exact=True)
                    objective_input = page.get_by_label(
                        "الهدف التعليمي", exact=True)
                    page_number_input = page.get_by_label(
                        "رقم الصفحة", exact=True)
                    file_input = page.get_by_label(
                        "ملف صفحة المصدر", exact=True)
                    grade_input.select_option(value=GRADE)
                    objective_input.fill(OBJECTIVE)
                    page_number_input.fill("1")
                    file_input.set_input_files({
                        "name": "source-page.png",
                        "mimeType": "image/png", "buffer": png_bytes})
                    expect(grade_input).to_have_value(GRADE)
                    expect(objective_input).to_have_value(OBJECTIVE)
                    expect(page_number_input).to_have_value("1")
                    self.assertEqual(mutations_since(fill_at), [])
                    primary = page.get_by_role(
                        "button", name="بدء تحليل الصفحة", exact=True)
                    expect(primary).to_have_count(1)
                    expect(primary).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)

                    # The one explicit action: settings, then Start
                    # (one-based page 1 -> page_index 0), then exactly
                    # one source-analysis attempt.
                    click_at = len(requests_log)
                    click_responses_at = len(responses_log)
                    primary.click()
                    expect(status_line).to_have_text(
                        UNAVAILABLE_MESSAGE, timeout=ASSERT_TIMEOUT_MS)
                    click_mutations = mutations_since(click_at)
                    paths = mutation_paths(click_mutations)
                    if paths[:1] == [("POST", "/api/sessions")]:
                        paths = paths[1:]
                    self.assertEqual(paths, [
                        ("PUT", "/api/session/settings"),
                        ("POST", "/api/session/start"),
                        ("POST", "/api/session/source-analysis")])
                    all_sessions = mutation_paths(
                        mutations_since(0)).count(
                            ("POST", "/api/sessions"))
                    self.assertEqual(all_sessions, 1)
                    settings_entry = next(
                        entry for entry in click_mutations
                        if entry["path"] == "/api/session/settings")
                    self.assertEqual(settings_entry.get("json"), {
                        "grade": GRADE, "objective": OBJECTIVE,
                        "rights_ref": ""})
                    start_entry = next(
                        entry for entry in click_mutations
                        if entry["path"] == "/api/session/start")
                    self.assertEqual(
                        parse_qs(start_entry["query"]),
                        {"page_index": ["0"]})
                    self.assertEqual(
                        start_entry.get("body_sha256"), png_sha256)
                    settings_responses = responses_since(
                        click_responses_at, "PUT",
                        "/api/session/settings")
                    start_responses = responses_since(
                        click_responses_at, "POST", "/api/session/start")
                    analysis_responses = responses_since(
                        click_responses_at, "POST",
                        "/api/session/source-analysis")
                    self.assertEqual(
                        [item["status"] for item in settings_responses],
                        [200])
                    self.assertEqual(
                        [item["status"] for item in start_responses],
                        [201])
                    self.assertEqual(
                        [item["status"] for item in analysis_responses],
                        [503])
                    self.assertEqual(
                        analysis_responses[0]["response"].json()
                        ["detail"]["code"], "provider_unavailable")

                    # Server-owned source reference, bound to the
                    # real metadata and the exact uploaded bytes.
                    metadata = self.api_json(
                        context, base_url, "/api/session/page")
                    self.assertEqual(metadata["source_sha256"],
                                     png_sha256)
                    self.assertEqual(metadata["grade"], GRADE)
                    self.assertEqual(metadata["objective"], OBJECTIVE)
                    self.assertEqual(metadata["rights_ref"], "")
                    self.assertIs(metadata["rights_is_clearance"], False)
                    self.assertEqual(metadata["page_index"], 0)
                    self.assertEqual(
                        metadata["original_url"],
                        "/api/session/page/original")
                    self.assertEqual(
                        metadata["preview_url"],
                        "/api/session/page/preview.png")
                    original_bytes = self.api_bytes(
                        context, base_url, metadata["original_url"])
                    self.assertEqual(original_bytes, png_bytes)
                    preview_bytes = self.api_bytes(
                        context, base_url, metadata["preview_url"])
                    self.assertEqual(
                        hashlib.sha256(preview_bytes).hexdigest(),
                        metadata["render_sha256"])
                    source_pane = page.get_by_test_id("source-pane")
                    expect(source_pane).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    preview_image = page.get_by_test_id("source-preview")
                    expect(preview_image).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(preview_image).to_have_attribute(
                        "alt", "معاينة صفحة المصدر",
                        timeout=ASSERT_TIMEOUT_MS)
                    self.assertEqual(
                        urlsplit(preview_image.get_attribute("src"))
                        .path, "/api/session/page/preview.png")
                    page.wait_for_function(
                        "() => { const el = document.querySelector("
                        "\"[data-testid='source-preview']\"); return "
                        "Boolean(el && el.complete && "
                        "el.naturalWidth > 0); }",
                        timeout=ASSERT_TIMEOUT_MS)
                    natural = preview_image.evaluate(
                        "(el) => [el.naturalWidth, el.naturalHeight]")
                    self.assertEqual(
                        natural, [metadata["width"], metadata["height"]])
                    original_link = page.get_by_test_id(
                        "source-original-link")
                    expect(original_link).to_have_text(
                        "فتح ملف الصفحة الأصلي",
                        timeout=ASSERT_TIMEOUT_MS)
                    self.assertEqual(
                        urlsplit(original_link.get_attribute("href"))
                        .path, "/api/session/page/original")
                    expect(page.get_by_test_id(
                        "source-reference-caption")).to_have_text(
                        SOURCE_CAPTION, timeout=ASSERT_TIMEOUT_MS)
                    details = page.get_by_test_id("source-details")
                    self.assertIsNone(details.get_attribute("open"))
                    details.get_by_text(
                        "تفاصيل المصدر", exact=True).click()
                    expect(page.get_by_test_id("source-id")
                           ).to_have_text(
                        metadata["source_id"],
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(page.get_by_test_id("source-sha256")
                           ).to_have_text(
                        metadata["source_sha256"],
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(page.get_by_test_id("render-sha256")
                           ).to_have_text(
                        metadata["render_sha256"],
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(page.get_by_test_id("selected-page-number")
                           ).to_have_text(
                        f"{arabic_indic(metadata['page_index'] + 1)} "
                        f"من {arabic_indic(metadata['page_count'])}",
                        timeout=ASSERT_TIMEOUT_MS)

                    # Disconnected provider: no proposals, no claimed
                    # success/approval, and the workspace stays usable.
                    snapshot = self.api_json(
                        context, base_url, "/api/session/snapshot")
                    self.assertEqual(snapshot["source_id"],
                                     metadata["source_id"])
                    self.assertEqual(snapshot["grade"], GRADE)
                    self.assertEqual(snapshot["objective"], OBJECTIVE)
                    self.assertEqual(snapshot["regions"], [])
                    self.assertEqual(snapshot["questions"], [])
                    self.assertEqual(snapshot["preview_items"], [])
                    self.assertEqual(snapshot["requests"], [])
                    self.assertFalse(snapshot["can_generate"])
                    expect(page.get_by_test_id("region-card")
                           ).to_have_count(0)
                    expect(page.get_by_test_id("question-card")
                           ).to_have_count(0)
                    expect(page.get_by_role(
                        "button", name="اعتماد", exact=True)
                        ).to_have_count(0)
                    expect(grade_input).to_have_value(GRADE)
                    expect(objective_input).to_have_value(OBJECTIVE)
                    observations.update({
                        "source_sha256": metadata["source_sha256"],
                        "render_sha256": metadata["render_sha256"],
                        "natural_dimensions": natural,
                        "analysis_status": 503})

                    # Every Start replaces the workspace. Once a
                    # source is committed, its discard notice remains
                    # visible; selecting a replacement mutates nothing.
                    replacement_warning = page.get_by_test_id(
                        "replacement-warning")
                    expect(replacement_warning).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    select_at = len(requests_log)
                    file_input.set_input_files({
                        "name": "replacement-page.png",
                        "mimeType": "image/png",
                        "buffer": INVALID_BYTES})
                    expect(replacement_warning).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(replacement_warning).to_have_text(
                        REPLACEMENT_WARNING, timeout=ASSERT_TIMEOUT_MS)
                    self.assertEqual(mutations_since(select_at), [])
                    self.assertEqual(
                        self.api_json(context, base_url,
                                      "/api/session/page"), metadata)
                    self.assertEqual(
                        self.api_json(context, base_url,
                                      "/api/session/snapshot"), snapshot)
                    self.assertEqual(
                        preview_image.evaluate(
                            "(el) => [el.naturalWidth, "
                            "el.naturalHeight]"), natural)

                    # Identical committed settings + invalid bytes:
                    # an actionable refusal, prior source preserved,
                    # and no second analysis attempt.
                    expect(grade_input).to_have_value(GRADE)
                    expect(objective_input).to_have_value(OBJECTIVE)
                    retry_at = len(requests_log)
                    retry_responses_at = len(responses_log)
                    primary.click()
                    setup_error = page.get_by_test_id("setup-error")
                    expect(setup_error).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(setup_error).to_have_attribute(
                        "role", "alert", timeout=ASSERT_TIMEOUT_MS)
                    expect(setup_error).to_have_text(
                        INVALID_FILE_MESSAGE,
                        timeout=ASSERT_TIMEOUT_MS)
                    retry_mutations = mutations_since(retry_at)
                    self.assertEqual(mutation_paths(retry_mutations), [
                        ("PUT", "/api/session/settings"),
                        ("POST", "/api/session/start")])
                    retry_settings = next(
                        entry for entry in retry_mutations
                        if entry["path"] == "/api/session/settings")
                    self.assertEqual(retry_settings.get("json"), {
                        "grade": GRADE, "objective": OBJECTIVE,
                        "rights_ref": ""})
                    retry_start = next(
                        entry for entry in retry_mutations
                        if entry["path"] == "/api/session/start")
                    self.assertEqual(
                        retry_start.get("body_sha256"),
                        hashlib.sha256(INVALID_BYTES).hexdigest())
                    retry_start_responses = responses_since(
                        retry_responses_at, "POST", "/api/session/start")
                    self.assertEqual(len(retry_start_responses), 1)
                    self.assertIn(
                        retry_start_responses[0]["status"], (400, 415))
                    self.assertEqual(responses_since(
                        retry_responses_at, "POST",
                        "/api/session/source-analysis"), [])
                    expect(replacement_warning).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.assertEqual(
                        self.api_json(context, base_url,
                                      "/api/session/page"), metadata)
                    self.assertEqual(
                        self.api_json(context, base_url,
                                      "/api/session/snapshot"), snapshot)
                    self.assertEqual(
                        self.api_bytes(context, base_url,
                                       metadata["original_url"]),
                        png_bytes)
                    expect(preview_image).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.assertEqual(
                        preview_image.evaluate(
                            "(el) => [el.naturalWidth, "
                            "el.naturalHeight]"), natural)

                    # Narrow viewport: only the active pane is
                    # exposed; drafts, focus and the selected source
                    # survive switching and returning to desktop.
                    narrow_at = len(requests_log)
                    page.set_viewport_size(
                        {"width": 390, "height": 844})
                    pane_switch = page.get_by_test_id("pane-switch")
                    expect(pane_switch).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(pane_switch).to_have_attribute(
                        "role", "tablist", timeout=ASSERT_TIMEOUT_MS)
                    source_tab = page.get_by_test_id("pane-tab-source")
                    work_tab = page.get_by_test_id("pane-tab-work")
                    expect(source_tab).to_have_text(
                        "المصدر", timeout=ASSERT_TIMEOUT_MS)
                    expect(work_tab).to_have_text(
                        "العمل", timeout=ASSERT_TIMEOUT_MS)
                    expect(source_tab).to_have_attribute(
                        "role", "tab", timeout=ASSERT_TIMEOUT_MS)
                    expect(work_tab).to_have_attribute(
                        "role", "tab", timeout=ASSERT_TIMEOUT_MS)
                    work_pane = page.get_by_test_id("work-pane")
                    work_tab.click()
                    expect(work_tab).to_have_attribute(
                        "aria-selected", "true",
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(source_tab).to_have_attribute(
                        "aria-selected", "false",
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(work_pane).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(source_pane).to_be_hidden(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.assertEqual(
                        source_pane.get_attribute("hidden"), "")
                    expect(page.get_by_role(
                        "img", name="معاينة صفحة المصدر", exact=True)
                        ).to_have_count(0)
                    grade_input.click()
                    grade_input.select_option(value=UNSAVED_GRADE)
                    expect(grade_input).to_have_value(UNSAVED_GRADE)
                    expect(grade_input).to_be_focused(
                        timeout=ASSERT_TIMEOUT_MS)
                    source_tab.click()
                    expect(source_tab).to_have_attribute(
                        "aria-selected", "true",
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(source_pane).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(work_pane).to_be_hidden(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.assertEqual(
                        work_pane.get_attribute("hidden"), "")
                    expect(page.get_by_role(
                        "textbox", name="الصف الدراسي", exact=True)
                        ).to_have_count(0)
                    self.assertLessEqual(
                        page.get_by_test_id("region-list").count(), 1)
                    self.assertEqual(
                        preview_image.evaluate(
                            "(el) => [el.naturalWidth, "
                            "el.naturalHeight]"), natural)
                    original_link.focus()
                    expect(original_link).to_be_focused(
                        timeout=ASSERT_TIMEOUT_MS)
                    work_tab.click()
                    expect(grade_input).to_be_focused(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(grade_input).to_have_value(UNSAVED_GRADE)
                    expect(objective_input).to_have_value(OBJECTIVE)
                    source_tab.click()
                    expect(original_link).to_be_focused(
                        timeout=ASSERT_TIMEOUT_MS)
                    work_tab.click()
                    expect(grade_input).to_be_focused(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(grade_input).to_have_value(UNSAVED_GRADE)
                    page.set_viewport_size(
                        {"width": 1280, "height": 900})
                    expect(pane_switch).to_be_hidden(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(work_pane).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(source_pane).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    self.assertIsNone(work_pane.get_attribute("hidden"))
                    self.assertIsNone(
                        source_pane.get_attribute("hidden"))
                    expect(grade_input).to_be_focused(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(grade_input).to_have_value(UNSAVED_GRADE)
                    self.assertEqual(mutations_since(narrow_at), [])
                    self.assertEqual(
                        self.api_json(context, base_url,
                                      "/api/session/page"), metadata)
                    self.assertEqual(
                        self.api_json(context, base_url,
                                      "/api/session/snapshot"), snapshot)
                    observations.update({
                        "final_status":
                        status_line.text_content(),
                        "unsaved_grade_preserved": True,
                        "request_count": len(requests_log),
                        "response_count": len(responses_log)})
                    self.assertEqual(external_urls, [])
                    self.assertEqual(page_errors, [])
                finally:
                    observations.update({
                        "external_urls": external_urls,
                        "page_errors": page_errors,
                        "mutations": [
                            {"method": entry["method"],
                             "path": entry["path"]}
                            for entry in mutations_since(0)],
                        "responses": [
                            {"method": entry["method"],
                             "path": entry["path"],
                             "status": entry["status"]}
                            for entry in responses_log]})
                    if artifact_dir and page is not None:
                        with contextlib.suppress(Exception):
                            directory = pathlib.Path(artifact_dir)
                            directory.mkdir(parents=True, exist_ok=True)
                            page.screenshot(
                                path=str(directory
                                         / "setup-source-final.png"),
                                full_page=True,
                                timeout=ASSERT_TIMEOUT_MS)
                            (directory / "observations.json").write_text(
                                json.dumps(
                                    observations, ensure_ascii=False,
                                    indent=2),
                                encoding="utf-8")
            finally:
                if context is not None:
                    with contextlib.suppress(Exception):
                        context.close()
                with contextlib.suppress(Exception):
                    browser.close()


if __name__ == "__main__":
    unittest.main()
