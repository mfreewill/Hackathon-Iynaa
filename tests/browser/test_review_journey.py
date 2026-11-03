"""Real-browser tracer for source review → question decisions.

The test harness serves
``teacher_demo.http_app.create_app`` on a random 127.0.0.1 origin
and injects the existing ``ControlsProvider`` from
``tests/test_http_teacher_controls.py`` through its internal
server-side provider seam. It is the synthetic test provider,
explicitly not Meta; this browser test never imports that module
(its top level loads the app/decoder), never invokes a provider,
and never seeds a mutation through the API. Instead it parses the
adjacent fixture file with ``ast`` and selects only the named
constant assignments listed in ``REQUIRED_FIXTURE_NAMES`` plus the
``synthetic_png`` function definition. Selected expressions are
evaluated by a tiny literal/container/name/f-string evaluator —
no imports, provider class, test class or arbitrary expression is
executed; the selected generator body needs only ``io`` and
``PIL.Image``. A missing/ambiguous required name or an unknown
expression fails the test before the browser opens.

Required environment: ``BROWSER_BASE_URL`` (actual origin) and
``BROWSER_EXECUTABLE`` (installed browser file). Optional
``BROWSER_ARTIFACT_DIR`` receives one final screenshot and a
cookie-free observations JSON only. Headless Chromium with
``chromium_sandbox=True``, a fresh context with service workers
blocked, bounded waits and ``finally`` cleanup. Any HTTP(S)
request outside the supplied origin is aborted, recorded and
fails the test (``data:``/``blob:`` bytes are not external
traffic). Teacher mutations come only from real DOM controls;
shared-cookie context requests are used solely for GET snapshot/
preview/raw inspection.

The client completes setup and source analysis, then exercises
the Stage 2 review UI described below.

DOM contract exercised below (all markers are scoped
inside their item/card unless stated): Stage 2 section
``stage-source-review`` (heading ``٢ مراجعة المصدر``), one
``region-list`` in the Work pane, ``region-item`` elements carrying
the actual ``data-region-id``/``data-external-section-id``, region
heading (committed current label), inclusion state, model flags,
text/label authors, a combined review form (current-text textarea
labelled ``النص الحالي``, current-label input ``التصنيف الحالي``,
inclusion select ``قرار التضمين`` with server values
``included|excluded|unresolved`` and a disabled ``unreviewed``
placeholder, commit ``حفظ مراجعة المقطع``,
cancel ``إلغاء التعديل`` resetting drafts to the last snapshot
with no request), read-only original details, whole-page evidence
button ``إظهار الصفحة الكاملة`` focusing the Source heading, move
buttons ``نقل المقطع لأعلى``/``لأسفل`` (always enabled; boundary
moves are server no-ops and focus stays on the same item's
clicked button), ``source-counts``, ``generate-gate-status`` and
the primary ``اعتماد المصدر وتوليد الأسئلة`` (enabled only when
the server says ``can_generate``). Stage 3 section
``stage-question-review`` (heading ``٣ مراجعة الأسئلة``), all six
``slot-outcome`` rows, ``question-list``/``question-card`` (actual
``data-question-id``/``data-slot-id``; heading is the current
wording; separate status, author and decision labels; server
block chips keyed by raw code; an edit form opened by
``تحرير السؤال`` whose ``حفظ التعديل`` sends one PATCH with only
changed editable fields and whose ``إلغاء التعديل`` sends nothing;
``اعتماد السؤال``/``رفض السؤال`` actions; rejecting returns focus
to the card heading). Stage 4 section ``stage-teacher-preview``
(heading ``٤ معاينة المعلم``) with the teacher-only warning,
``preview-list``/``preview-item`` in the server's
``preview_items`` order, and reachable read-only originals.
Arabic status/decision/author/Bloom label mappings used below
are part of this contract; server status, blocks, counts and
``can_generate`` are never recomputed in the test.
"""
import ast
import contextlib
import hashlib
import io
import json
import os
import pathlib
import unittest
from urllib.parse import quote, urlsplit

from PIL import Image
from playwright.sync_api import expect, sync_playwright

NAV_TIMEOUT_MS = 15_000
ASSERT_TIMEOUT_MS = 10_000

REQUIRED_FIXTURE_NAMES = (
    "MARK", "GRADE", "OBJECTIVE", "RAW_A", "RAW_B", "FACT_A",
    "FACT_B", "EDITED_A", "EDITED_B", "CHANGED_A", "QUESTION_1",
    "GUIDANCE_1", "COGNITIVE_1", "QUESTION_2", "GUIDANCE_2",
    "COGNITIVE_2")

# Teacher wording authored by this tracer (not provider prose).
EDITED_B_WORDING = "  مَاذَا يَحْدُثُ لِلشَّجَرِ عِنْدَ قُدُومِ الرَّبِيعِ؟ \U0001d11e  "
EDITED_B_WORDING_2 = "  كَيْفَ يَتَغَيَّرُ الشَّجَرُ فِي الرَّبِيعِ؟  "
EDITED_B_LABEL = "نص عن الشجر"
BLOOM_ARABIC = {
    "remember": "تذكّر", "understand": "فهم", "apply": "تطبيق",
    "analyze": "تحليل", "evaluate": "تقييم", "create": "إنشاء"}
INCLUSION_ARABIC = {
    "unreviewed": "بانتظار المراجعة", "included": "مشمول",
    "excluded": "مستبعد", "unresolved": "غير محسوم"}
AUTHOR_ARABIC = {
    "model": "النموذج", "teacher": "المعلم"}
CURRENT_AUTHOR_ARABIC = {
    "model": "اقتراح النموذج كما ورد",
    "teacher": "نسخة معدّلة بواسطة المعلم"}
STATUS_ARABIC = {
    "draft": "مسودة", "approved": "معتمد", "rejected": "مرفوض",
    "outdated": "يحتاج إلى تحديث"}
DECISION_ARABIC = {
    "draft": "قرار المعلم: بانتظار القرار",
    "approved": "قرار المعلم: معتمد",
    "rejected": "قرار المعلم: مرفوض",
    "outdated": "قرار المعلم: كان معتمدًا ويحتاج إلى مراجعة"}
TEACHER_PREVIEW_WARNING = (
    "معاينة للمعلم فقط — ليست نشرًا للطلاب ولا اعتمادًا تربويًا نهائيًا.")


def load_fixture():
    """Extract only the named constants and PNG generator by AST."""
    path = (pathlib.Path(__file__).resolve().parent.parent
            / "test_http_teacher_controls.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assignments = {}
    generators = []
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in REQUIRED_FIXTURE_NAMES):
            name = node.targets[0].id
            if name in assignments:
                raise AssertionError(f"ambiguous fixture name: {name}")
            assignments[name] = node.value
        elif (isinstance(node, ast.FunctionDef)
              and node.name == "synthetic_png"):
            generators.append(node)
    missing = [name for name in REQUIRED_FIXTURE_NAMES
               if name not in assignments]
    if missing:
        raise AssertionError(f"missing fixture names: {missing}")
    if len(generators) != 1:
        raise AssertionError("synthetic_png must appear exactly once")

    values = {}

    def evaluate(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (str, int, float, bool, type(None))):
                return node.value
            raise AssertionError(f"unsupported constant: {node.value!r}")
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise AssertionError(f"unknown fixture name: {node.id}")
            return values[node.id]
        if isinstance(node, ast.JoinedStr):
            return "".join(str(evaluate(part)) for part in node.values)
        if isinstance(node, ast.FormattedValue):
            if node.format_spec is not None or node.conversion != -1:
                raise AssertionError("formatted fixture values unsupported")
            return evaluate(node.value)
        if isinstance(node, ast.List):
            return [evaluate(item) for item in node.elts]
        if isinstance(node, ast.Tuple):
            return tuple(evaluate(item) for item in node.elts)
        if isinstance(node, ast.Set):
            return {evaluate(item) for item in node.elts}
        if isinstance(node, ast.Dict):
            if any(key is None for key in node.keys):
                raise AssertionError("dict unpacking unsupported")
            return {evaluate(key): evaluate(value)
                    for key, value in zip(node.keys, node.values)}
        raise AssertionError(
            f"unsupported fixture expression: {type(node).__name__}")

    for name, expression in assignments.items():
        values[name] = evaluate(expression)
    values['GRADE'] = 'الصف الخامس'
    module = ast.Module(body=generators, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"io": io, "Image": Image}
    exec(compile(module, str(path), "exec"), namespace)  # selected def only
    png = namespace["synthetic_png"]()
    if not isinstance(png, bytes) or not png.startswith(b"\x89PNG"):
        raise AssertionError("synthetic_png did not produce PNG bytes")
    return values, png


def arabic_indic(number):
    return str(number).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))


class ReviewJourneyBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx, cls.png_bytes = load_fixture()

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

    def last_mutation_body(self, index, method, path):
        matches = [entry for entry in self.requests_log[index:]
                   if entry["method"] == method
                   and entry["path"] == path]
        self.assertEqual(len(matches), 1, (method, path))
        return matches[0].get("json")

    def capture_artifacts(self, page):
        artifact_dir = os.environ.get("BROWSER_ARTIFACT_DIR", "")
        if not artifact_dir or page is None:
            return
        with contextlib.suppress(Exception):
            directory = pathlib.Path(artifact_dir)
            directory.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(directory / "review-journey-final.png"),
                full_page=True, timeout=ASSERT_TIMEOUT_MS)
            observations = {
                "external_urls": self.external_urls,
                "page_errors": self.page_errors,
                "mutations": [
                    {"method": m, "path": p}
                    for m, p in self.mutations_since(0)],
                "responses": self.responses_log}
            (directory / "review-journey.json").write_text(
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

    def raw_result(self, context, request_id):
        payload = self.api_json(
            context, "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")
        self.assertEqual(payload["request_id"], request_id)
        self.assertEqual(payload["raw_retention"], "retained")
        return payload["provider_result"]

    @staticmethod
    def region_by_section(snapshot, section_id):
        return next(item for item in snapshot["regions"]
                    if item["external_section_id"] == section_id)

    @staticmethod
    def question_by_slot(snapshot, slot_id):
        return next(item for item in snapshot["questions"]
                    if item["slot_id"] == slot_id)

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

    def assert_region_dom(self, page, region):
        item = self.region_item(page, region)
        expect(item).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
        self.assertEqual(
            item.get_attribute("data-external-section-id"),
            region["external_section_id"])
        expect(self.tid(item, "region-heading")).to_have_text(
            region["current_label"], timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(item, "region-inclusion-state")).to_have_text(
            INCLUSION_ARABIC[region["inclusion"]],
            timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(item, "region-current-text-input")
               ).to_have_value(
            region["current_text"], timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(item, "region-current-label-input")
               ).to_have_value(
            region["current_label"], timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(item, "region-inclusion-input")
               ).to_have_value(
            region["inclusion"], timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(item, "region-text-author")).to_have_text(
            AUTHOR_ARABIC[region["text_author"]],
            timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(item, "region-label-author")).to_have_text(
            AUTHOR_ARABIC[region["label_author"]],
            timeout=ASSERT_TIMEOUT_MS)
        flags = self.tid(item, "region-model-flag")
        expect(flags).to_have_count(len(region["model_flags"]))
        for flag_locator, flag in zip(
                flags.all(), region["model_flags"]):
            expect(flag_locator).to_have_text(
                flag, timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(item, "region-raw-text")).to_have_text(
            region["raw_text"], timeout=ASSERT_TIMEOUT_MS)
        expect(self.tid(item, "region-raw-label")).to_have_text(
            region["raw_label"], timeout=ASSERT_TIMEOUT_MS)
        return item

    def test_source_review_to_teacher_preview_journey(self):
        fx = self.fx
        with self.browser_page() as (context, page):
            # Existing accepted setup succeeds first.
            response = page.goto(
                "/", wait_until="domcontentloaded",
                timeout=NAV_TIMEOUT_MS)
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200, page.url)
            page.wait_for_load_state(
                "networkidle", timeout=NAV_TIMEOUT_MS)
            page.get_by_label("الصف الدراسي", exact=True).select_option(value=
                fx["GRADE"])
            page.get_by_label("الهدف التعليمي", exact=True).fill(
                fx["OBJECTIVE"])
            page.get_by_label("رقم الصفحة", exact=True).fill("1")
            page.get_by_label(
                "ملف صفحة المصدر", exact=True).set_input_files({
                    "name": "source-page.png",
                    "mimeType": "image/png", "buffer": self.png_bytes})
            with page.expect_response(
                    lambda item: urlsplit(item.url).path
                    == "/api/session/source-analysis"
                    and item.request.method == "POST"
                    and item.status == 201,
                    timeout=ASSERT_TIMEOUT_MS):
                page.get_by_role(
                    "button", name="بدء تحليل الصفحة", exact=True
                ).click()
            snapshot = self.snapshot(context)
            self.assertEqual(len(snapshot["regions"]), 2)
            self.assertEqual(snapshot["grade"], fx["GRADE"])
            self.assertEqual(snapshot["objective"], fx["OBJECTIVE"])
            self.assertTrue(snapshot["source_analysis_terminal"])
            self.assertFalse(snapshot["can_generate"])
            self.assertEqual(snapshot["included_count"], 0)
            self.assertEqual(snapshot["withheld_count"], 2)
            self.assertEqual(snapshot["questions"], [])
            region_a = self.region_by_section(
                snapshot, "synthetic-section-river")
            region_b = self.region_by_section(
                snapshot, "synthetic-section-trees")
            source_request_id = next(
                item["request_id"] for item in snapshot["requests"]
                if item["kind"] == "source")
            self.assertEqual(region_a["request_id"], source_request_id)
            self.assertEqual(region_b["request_id"], source_request_id)
            baseline_source_raw = self.raw_result(
                context, source_request_id)
            actual_model_name = baseline_source_raw["model_name"]
            expect(self.tid(page, "source-preview")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)

            # FIRST NEW UI ASSERTION: one visible Work region list.
            region_list = self.tid(page, "region-list")
            expect(region_list).to_have_count(1)
            expect(region_list).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(page.get_by_role(
                "heading", name="٢ مراجعة المصدر", exact=True)
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            work_pane = self.tid(page, "work-pane")
            self.assertEqual(
                self.tid(work_pane, "region-list").count(), 1)
            self.assertEqual(
                self.tid(self.tid(page, "source-pane"), "region-list")
                .count(), 0)
            expect(self.tid(page, "source-model-name")).to_have_text(
                actual_model_name, timeout=ASSERT_TIMEOUT_MS)
            items = self.tid(page, "region-item")
            expect(items).to_have_count(2)
            self.assertEqual(
                [item.get_attribute("data-region-id")
                 for item in items.all()],
                [item["region_id"] for item in snapshot["regions"]])
            for region, raw in ((region_a, fx["RAW_A"]),
                                (region_b, fx["RAW_B"])):
                self.assertEqual(region["raw_text"], raw)
                self.assertEqual(region["current_text"], raw)
                self.assertEqual(region["raw_label"], "body")
                self.assertEqual(region["current_label"], "body")
                self.assertEqual(region["inclusion"], "unreviewed")
                self.assert_region_dom(page, region)
            expect(self.tid(page, "source-counts")).to_have_text(
                "المضمّنة: ٠ · المحتجبة: ٢", timeout=ASSERT_TIMEOUT_MS)
            generate_button = page.get_by_role(
                "button", name="اعتماد المصدر وتوليد الأسئلة",
                exact=True)
            expect(generate_button).to_be_disabled(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "generate-gate-status")
                   ).to_have_text(
                "لا يمكن التوليد بعد: راجع مقطعًا واحدًا على الأقل واجعله مشمولًا بنص غير فارغ.",
                timeout=ASSERT_TIMEOUT_MS)

            # Whole-page evidence leads to the unchanged source.
            item_a = self.region_item(page, region_a)
            self.tid(item_a, "region-evidence-link").click()
            expect(page.get_by_role(
                "heading", name="صفحة المصدر", exact=True)
            ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "source-preview")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "source-reference-caption")
                   ).to_have_text(
                "مرجع الدليل: الصفحة كاملة — لا يوجد قناع أو تحديد موضعي آلي.",
                timeout=ASSERT_TIMEOUT_MS)

            # The initial unreviewed value is a disabled placeholder,
            # never a fourth writable teacher decision.
            inclusion_a = self.tid(item_a, "region-inclusion-input")
            choices = inclusion_a.locator("option").evaluate_all(
                "els => els.map(el => ({value:el.value,disabled:el.disabled}))")
            self.assertEqual({x["value"] for x in choices if not x["disabled"]},
                             {"included", "excluded", "unresolved"})
            self.assertTrue(any(x["value"] == "unreviewed" and x["disabled"]
                                for x in choices))
            expect(self.tid(item_a, "region-review-commit")).to_be_disabled(
                timeout=ASSERT_TIMEOUT_MS)

            # Region A: cancel first, then the real combined review.
            checkpoint = len(self.requests_log)
            text_a = self.tid(item_a, "region-current-text-input")
            text_a.fill("نص مسودة لن يُحفظ")
            self.tid(item_a, "region-review-cancel").click()
            expect(text_a).to_have_value(
                fx["RAW_A"], timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(checkpoint), [])
            text_a.fill(fx["EDITED_A"])
            self.tid(item_a, "region-inclusion-input").select_option(
                "included")
            expect(self.tid(item_a, "region-inclusion-state")
                   ).to_have_text(
                INCLUSION_ARABIC["unreviewed"],
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(checkpoint), [])
            self.click_expect(
                page, "PUT", self.region_path(region_a), 200,
                self.tid(item_a, "region-review-commit"))
            self.assertEqual(
                self.last_mutation_body(
                    checkpoint, "PUT", self.region_path(region_a)),
                {"inclusion": "included",
                 "current_text": fx["EDITED_A"],
                 "current_label": "body"})

            # One included section already opens the actual server gate.
            partial = self.snapshot(context)
            self.assertTrue(partial["can_generate"])
            self.assertEqual(partial["included_count"], 1)
            self.assertEqual(partial["withheld_count"], 1)
            expect(generate_button).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "source-counts")).to_have_text(
                "المضمّنة: ١ · المحتجبة: ١", timeout=ASSERT_TIMEOUT_MS)
            item_b = self.region_item(page, region_b)
            for state in ("unresolved", "excluded"):
                before_decision = len(self.requests_log)
                self.tid(item_b, "region-inclusion-input").select_option(state)
                self.assertEqual(self.mutations_since(before_decision), [])
                self.click_expect(page, "PUT", self.region_path(region_b), 200,
                                  self.tid(item_b, "region-review-commit"))
                self.assertEqual(self.last_mutation_body(
                    before_decision, "PUT", self.region_path(region_b)),
                    {"inclusion": state, "current_text": fx["RAW_B"],
                     "current_label": "body"})
                partial = self.snapshot(context)
                self.assertTrue(partial["can_generate"])
                self.assertEqual(partial["included_count"], 1)
                self.assertEqual(partial["withheld_count"], 1)
                region_b = self.region_by_section(partial, "synthetic-section-trees")
                self.assertEqual(region_b["inclusion"], state)
                self.assertEqual(region_b["raw_text"], fx["RAW_B"])
                self.assert_region_dom(page, region_b)
                expect(generate_button).to_be_enabled(timeout=ASSERT_TIMEOUT_MS)

            # Region B: reviewed text plus an explicit label edit.
            item_b = self.region_item(page, region_b)
            checkpoint_b = len(self.requests_log)
            self.tid(item_b, "region-current-text-input").fill(
                fx["EDITED_B"])
            self.tid(item_b, "region-current-label-input").fill(
                EDITED_B_LABEL)
            self.tid(item_b, "region-inclusion-input").select_option(
                "included")
            self.assertEqual(self.mutations_since(checkpoint_b), [])
            self.click_expect(
                page, "PUT", self.region_path(region_b), 200,
                self.tid(item_b, "region-review-commit"))
            self.assertEqual(
                self.last_mutation_body(
                    checkpoint_b, "PUT", self.region_path(region_b)),
                {"inclusion": "included",
                 "current_text": fx["EDITED_B"],
                 "current_label": EDITED_B_LABEL})
            snapshot = self.snapshot(context)
            self.assertTrue(snapshot["can_generate"])
            self.assertEqual(snapshot["included_count"], 2)
            self.assertEqual(snapshot["withheld_count"], 0)
            region_a = self.region_by_section(
                snapshot, "synthetic-section-river")
            region_b = self.region_by_section(
                snapshot, "synthetic-section-trees")
            self.assertEqual(region_a["current_text"], fx["EDITED_A"])
            self.assertEqual(region_a["raw_text"], fx["RAW_A"])
            self.assertEqual(region_a["text_author"], "teacher")
            self.assertEqual(region_a["label_author"], "model")
            self.assertEqual(region_b["current_text"], fx["EDITED_B"])
            self.assertEqual(region_b["current_label"], EDITED_B_LABEL)
            self.assertEqual(region_b["raw_label"], "body")
            self.assertEqual(region_b["label_author"], "teacher")
            self.assert_region_dom(page, region_a)
            self.assert_region_dom(page, region_b)
            expect(self.tid(page, "source-counts")).to_have_text(
                "المضمّنة: ٢ · المحتجبة: ٠", timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "generate-gate-status")
                   ).to_have_text(
                "المصدر جاهز لتوليد الأسئلة.",
                timeout=ASSERT_TIMEOUT_MS)
            expect(generate_button).to_be_enabled(
                timeout=ASSERT_TIMEOUT_MS)

            # Display movement only: down then up, same-item focus.
            item_a = self.region_item(page, region_a)
            move_down = self.tid(item_a, "region-move-down")
            self.click_expect(
                page, "POST", self.region_path(region_a) + "/move-down",
                200, move_down)
            snapshot = self.snapshot(context)
            self.assertEqual(
                [item["region_id"] for item in snapshot["regions"]],
                [region_b["region_id"], region_a["region_id"]])
            self.assertEqual(
                {item["region_id"]: item["display_position"]
                 for item in snapshot["regions"]},
                {region_b["region_id"]: 1, region_a["region_id"]: 2})
            self.assertEqual(
                [item.get_attribute("data-region-id")
                 for item in self.tid(page, "region-item").all()],
                [region_b["region_id"], region_a["region_id"]])
            expect(self.tid(
                self.region_item(page, region_a), "region-move-down")
            ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
            item_a = self.region_item(page, region_a)
            move_up = self.tid(item_a, "region-move-up")
            self.click_expect(
                page, "POST", self.region_path(region_a) + "/move-up",
                200, move_up)
            snapshot = self.snapshot(context)
            self.assertEqual(
                [item["region_id"] for item in snapshot["regions"]],
                [region_a["region_id"], region_b["region_id"]])
            expect(self.tid(
                self.region_item(page, region_a), "region-move-up")
            ).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
            region_a = self.region_by_section(
                snapshot, "synthetic-section-river")
            region_b = self.region_by_section(
                snapshot, "synthetic-section-trees")

            # Generate once from the server-ready reviewed source.
            checkpoint = len(self.requests_log)
            self.click_expect(
                page, "POST", "/api/session/questions/generate", 201,
                generate_button)
            generation_body = self.last_mutation_body(
                checkpoint, "POST",
                "/api/session/questions/generate")
            self.assertIsInstance(generation_body, dict)
            self.assertEqual(
                set(generation_body), {"request_id", "source_id"})
            self.assertRegex(
                generation_body["request_id"], r"^[0-9a-f]{32}$")
            self.assertEqual(
                generation_body["source_id"], snapshot["source_id"])
            self.assertNotEqual(
                generation_body["request_id"], source_request_id)
            generation_snapshot = self.snapshot(context)
            generation_records = [
                item for item in generation_snapshot["requests"]
                if item["request_id"] == generation_body["request_id"]]
            self.assertEqual(len(generation_records), 1)
            self.assertEqual(
                generation_records[0]["kind"], "questions")
            self.assertEqual(
                generation_records[0]["state"], "terminal")
            self.assertEqual(
                generation_records[0]["outcome"], "ok")
            self.assertEqual(
                generation_snapshot["source_id"],
                generation_body["source_id"])
            self.assertTrue(all(
                item["request_id"] == generation_body["request_id"]
                for item in generation_snapshot["questions"]))
            snapshot = self.snapshot(context)
            self.assertEqual(len(snapshot["questions"]), 2)
            self.assertEqual(len(snapshot["slot_outcomes"]), 6)
            self.assertEqual(
                sorted(item["outcome"]
                       for item in snapshot["slot_outcomes"]),
                ["abstain"] * 4 + ["draft"] * 2)
            question_request_id = next(
                item["request_id"] for item in snapshot["requests"]
                if item["kind"] == "questions")
            baseline_question_raw = self.raw_result(
                context, question_request_id)
            self.assertEqual(
                baseline_question_raw["model_name"],
                actual_model_name)
            question_a = self.question_by_slot(
                snapshot, "slot-remember")
            question_b = self.question_by_slot(
                snapshot, "slot-understand")
            for question, region, fact, text, guidance, cognitive, level in (
                    (question_a, region_a, fx["FACT_A"],
                     fx["QUESTION_1"], fx["GUIDANCE_1"],
                     fx["COGNITIVE_1"], "remember"),
                    (question_b, region_b, fx["FACT_B"],
                     fx["QUESTION_2"], fx["GUIDANCE_2"],
                     fx["COGNITIVE_2"], "understand")):
                self.assertEqual(question["request_id"],
                                 question_request_id)
                self.assertEqual(question["status"], "draft")
                self.assertEqual(question["blocks"], [])
                self.assertEqual(question["original_question"], text)
                self.assertEqual(question["current_question"], text)
                self.assertEqual(
                    question["original_answer_guidance"], guidance)
                self.assertEqual(
                    question["current_answer_guidance"], guidance)
                self.assertEqual(
                    question["original_cognitive_explanation"], cognitive)
                self.assertEqual(
                    question["current_cognitive_explanation"], cognitive)
                self.assertEqual(question["generation_grade"], fx["GRADE"])
                self.assertEqual(question["current_grade"], fx["GRADE"])
                self.assertEqual(
                    question["generation_objective"], fx["OBJECTIVE"])
                self.assertEqual(
                    question["current_objective"], fx["OBJECTIVE"])
                self.assertEqual(question["model_level"], level)
                self.assertEqual(question["current_level"], level)
                self.assertEqual(question["question_author"], "model")
                self.assertEqual(
                    question["supporting_region_ids"],
                    [region["region_id"]])
                self.assertEqual(
                    question["anchors"], [[region["region_id"], fact]])

            expect(page.get_by_role(
                "heading", name="٣ مراجعة الأسئلة", exact=True)
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            outcome_rows = self.tid(page, "slot-outcome")
            expect(outcome_rows).to_have_count(6)
            self.assertEqual(
                [row.get_attribute("data-slot-id")
                 for row in outcome_rows.all()],
                [item["slot_id"] for item in snapshot["slot_outcomes"]])
            for row, outcome in zip(
                    outcome_rows.all(), snapshot["slot_outcomes"]):
                self.assertEqual(
                    row.get_attribute("data-level"), outcome["level"])
                self.assertEqual(
                    row.get_attribute("data-outcome"),
                    outcome["outcome"])
                level_ar = BLOOM_ARABIC[outcome["level"]]
                expected_text = (
                    f"{level_ar}: وصلت مسودة"
                    if outcome["outcome"] == "draft" else
                    f"{level_ar}: امتنع النموذج — "
                    f"{outcome['abstain_reason']}")
                expect(self.tid(row, "slot-outcome-text")
                       ).to_have_text(
                    expected_text, timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "question-list")).to_have_count(1)
            expect(self.tid(page, "question-card")).to_have_count(2)

            def assert_question_dom(question):
                card = self.question_card(page, question)
                expect(card).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
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
                expect(self.tid(card, "question-author")
                       ).to_have_text(
                    CURRENT_AUTHOR_ARABIC[
                        "teacher" if question["teacher_edited"] else "model"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-level")).to_have_text(
                    BLOOM_ARABIC[question["current_level"]],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-grade")).to_have_text(
                    question["current_grade"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-objective")
                       ).to_have_text(
                    question["current_objective"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-guidance")
                       ).to_have_text(
                    question["current_answer_guidance"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-cognitive")
                       ).to_have_text(
                    question["current_cognitive_explanation"],
                    timeout=ASSERT_TIMEOUT_MS)
                facts = self.tid(card, "question-evidence-fact")
                expect(facts).to_have_count(len(question["anchors"]))
                for fact_locator, anchor in zip(
                        facts.all(), question["anchors"]):
                    self.assertEqual(
                        fact_locator.get_attribute("data-region-id"),
                        anchor[0])
                    expect(fact_locator).to_have_text(
                        anchor[1], timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-block")).to_have_count(
                    len(question["blocks"]))
                expect(self.tid(card, "question-original-text")
                       ).to_have_text(
                    question["original_question"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-original-guidance")
                       ).to_have_text(
                    question["original_answer_guidance"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-original-cognitive")
                       ).to_have_text(
                    question["original_cognitive_explanation"],
                    timeout=ASSERT_TIMEOUT_MS)
                return card

            preview_seen = set()

            def assert_preview():
                expect(self.tid(page, "stage-teacher-preview")).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                expect(page.get_by_role("heading", name="٤ معاينة المعلم", exact=True)
                       ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(page, "teacher-preview-warning")).to_be_visible(
                    timeout=ASSERT_TIMEOUT_MS)
                self.tid(page, "stage-teacher-preview").scroll_into_view_if_needed(
                    timeout=ASSERT_TIMEOUT_MS)
                preview = self.api_json(
                    context, "/api/session/preview")["items"]
                current = self.snapshot(context)
                self.assertEqual(current["preview_items"], preview)
                by_id = {item["question_id"]: item
                         for item in current["questions"]}
                expect(self.tid(page, "preview-list")).to_have_count(1)
                dom_items = self.tid(page, "preview-item")
                expect(dom_items).to_have_count(len(preview))
                self.assertEqual(
                    [item.get_attribute("data-question-id")
                     for item in dom_items.all()],
                    [item["question_id"] for item in preview])
                if preview:
                    expect(self.tid(page, "preview-empty")
                           ).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
                else:
                    expect(self.tid(page, "preview-empty")
                           ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                    return_action = self.tid(page, "preview-return")
                    expect(return_action).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                    expect(return_action).to_have_text("عودة إلى مراجعة الأسئلة")
                for entry in preview:
                    item = page.locator(
                        "[data-testid='preview-item']"
                        f"[data-question-id=\"{entry['question_id']}\"]")
                    question = by_id[entry["question_id"]]
                    expect(item).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-question")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-question")
                           ).to_have_text(
                        entry["question"], timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-author")
                           ).to_have_text(
                        CURRENT_AUTHOR_ARABIC[entry["author"]],
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-decision")
                           ).to_have_text(
                        "قرار المعلم: معتمد",
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-author")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-decision")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-level")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-level")).to_have_text(
                        BLOOM_ARABIC[entry["bloom_level"]], timeout=ASSERT_TIMEOUT_MS)
                    checking = self.tid(item, "preview-checking")
                    checking_was_open = checking.get_attribute("open") is not None
                    if entry["question_id"] not in preview_seen:
                        self.assertFalse(checking_was_open)
                        preview_seen.add(entry["question_id"])
                    if not checking_was_open:
                        checking.locator("summary").click()
                    expect(self.tid(item, "preview-guidance")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-guidance")
                           ).to_have_text(
                        entry["answer_guidance"],
                        timeout=ASSERT_TIMEOUT_MS)
                    facts = self.tid(item, "preview-evidence-fact")
                    expect(facts).to_have_count(
                        len(entry["resolved_anchors"]))
                    for fact_locator, anchor in zip(
                            facts.all(), entry["resolved_anchors"]):
                        expect(fact_locator).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                        expect(self.tid(fact_locator, "preview-evidence-quote")
                               ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                        expect(self.tid(fact_locator, "preview-evidence-text")
                               ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                        self.assertEqual(
                            fact_locator.get_attribute("data-region-id"),
                            anchor[0])
                        expect(self.tid(
                            fact_locator, "preview-evidence-quote")
                        ).to_have_text(
                            anchor[1], timeout=ASSERT_TIMEOUT_MS)
                        expect(self.tid(
                            fact_locator, "preview-evidence-text")
                        ).to_have_text(
                            anchor[2], timeout=ASSERT_TIMEOUT_MS)
                    if not checking_was_open:
                        checking.locator("summary").click()
                    original = self.tid(item, "preview-original")
                    original_was_open = original.get_attribute("open") is not None
                    if not original_was_open:
                        original.locator("summary").click()
                    expect(self.tid(item, "preview-original-text")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-original-guidance")).to_be_visible(
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-original-text")
                           ).to_have_text(
                        question["original_question"],
                        timeout=ASSERT_TIMEOUT_MS)
                    expect(self.tid(item, "preview-original-guidance")
                           ).to_have_text(
                        entry["original_answer_guidance"],
                        timeout=ASSERT_TIMEOUT_MS)
                    if not original_was_open:
                        original.locator("summary").click()
                return preview

            assert_question_dom(question_a)
            assert_question_dom(question_b)
            self.assertEqual(assert_preview(), [])

            def approve(question):
                card = self.question_card(page, question)
                mark = len(self.requests_log)
                path = self.question_path(question, "/approve")
                self.click_expect(
                    page, "POST", path, 200,
                    self.tid(card, "question-approve"))
                self.assertEqual(
                    self.last_mutation_body(mark, "POST", path), {})
                return self.question_by_slot(
                    self.snapshot(context), question["slot_id"])

            def edit_wording(question, wording):
                card = self.question_card(page, question)
                self.tid(card, "question-edit").click()
                form = self.tid(card, "question-edit-form")
                expect(form).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
                field = self.tid(card, "question-edit-question")
                expect(field).to_be_focused(timeout=ASSERT_TIMEOUT_MS)
                expect(field).to_have_value(
                    question["current_question"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-edit-guidance")
                       ).to_have_value(
                    question["current_answer_guidance"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-edit-cognitive")
                       ).to_have_value(
                    question["current_cognitive_explanation"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-edit-grade")
                       ).to_have_value(
                    question["current_grade"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-edit-objective")
                       ).to_have_value(
                    question["current_objective"],
                    timeout=ASSERT_TIMEOUT_MS)
                expect(self.tid(card, "question-edit-level")
                       ).to_have_value(
                    question["current_level"],
                    timeout=ASSERT_TIMEOUT_MS)
                mark = len(self.requests_log)
                field.fill(wording)
                self.assertEqual(self.mutations_since(mark), [])
                path = self.question_path(question)
                self.click_expect(
                    page, "PATCH", path, 200,
                    self.tid(card, "question-edit-commit"))
                self.assertEqual(
                    self.last_mutation_body(mark, "PATCH", path),
                    {"question": wording})
                expect(form).to_be_hidden(timeout=ASSERT_TIMEOUT_MS)
                return self.question_by_slot(
                    self.snapshot(context), question["slot_id"])

            # Approve A as received; no mere render approves B.
            question_a = approve(question_a)
            self.assertEqual(question_a["status"], "approved")
            self.assertEqual(question_a["question_author"], "model")
            assert_question_dom(question_a)
            question_b = self.question_by_slot(
                self.snapshot(context), "slot-understand")
            self.assertEqual(question_b["status"], "draft")
            self.assertEqual(
                [item["question_id"] for item in assert_preview()],
                [question_a["question_id"]])

            # Edit B: current wording changes, original, evidence
            # and draft/unapproved state do not.
            question_b = edit_wording(question_b, EDITED_B_WORDING)
            self.assertEqual(question_b["status"], "draft")
            self.assertEqual(
                question_b["current_question"], EDITED_B_WORDING)
            self.assertEqual(
                question_b["original_question"], fx["QUESTION_2"])
            self.assertEqual(
                question_b["anchors"],
                [[region_b["region_id"], fx["FACT_B"]]])
            self.assertEqual(question_b["question_author"], "teacher")
            assert_question_dom(question_b)
            card_b = self.question_card(page, question_b)
            self.tid(card_b, "question-original").get_by_text(
                "السؤال الأصلي كما ورد", exact=True).click()
            expect(self.tid(card_b, "question-original-text")
                   ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(
                [item["question_id"] for item in assert_preview()],
                [question_a["question_id"]])

            # Reject B: the card remains and takes heading focus.
            card_b = self.question_card(page, question_b)
            checkpoint = len(self.requests_log)
            reject_path = self.question_path(question_b, "/reject")
            self.click_expect(
                page, "POST", reject_path, 200,
                self.tid(card_b, "question-reject"))
            self.assertEqual(
                self.last_mutation_body(checkpoint, "POST", reject_path),
                {})
            question_b = self.question_by_slot(
                self.snapshot(context), "slot-understand")
            self.assertEqual(question_b["status"], "rejected")
            expect(self.tid(
                self.question_card(page, question_b),
                "question-heading")).to_be_focused(
                timeout=ASSERT_TIMEOUT_MS)
            assert_question_dom(question_b)
            self.assertEqual(
                [item["question_id"] for item in assert_preview()],
                [question_a["question_id"]])

            # Editing a rejected question never restores it; only a
            # fresh explicit approval does.
            question_b = edit_wording(question_b, EDITED_B_WORDING_2)
            self.assertEqual(question_b["status"], "rejected")
            self.assertEqual(
                question_b["current_question"], EDITED_B_WORDING_2)
            assert_question_dom(question_b)
            self.assertEqual(
                [item["question_id"] for item in assert_preview()],
                [question_a["question_id"]])
            question_b = approve(question_b)
            self.assertEqual(question_b["status"], "approved")
            question_a = self.question_by_slot(
                self.snapshot(context), "slot-remember")
            assert_question_dom(question_a)
            assert_question_dom(question_b)

            # The teacher preview is exactly the current approved
            # pair, with separate author and decision labels.
            expect(page.get_by_role(
                "heading", name="٤ معاينة المعلم", exact=True)
            ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "teacher-preview-warning")
                   ).to_have_text(
                TEACHER_PREVIEW_WARNING, timeout=ASSERT_TIMEOUT_MS)
            preview = assert_preview()
            self.assertEqual(
                {item["question_id"] for item in preview},
                {question_a["question_id"], question_b["question_id"]})
            authors = {item["question_id"]: item["author"]
                       for item in preview}
            self.assertEqual(
                authors[question_a["question_id"]], "model")
            self.assertEqual(
                authors[question_b["question_id"]], "teacher")
            preview_b = page.locator(
                "[data-testid='preview-item']"
                f"[data-question-id=\"{question_b['question_id']}\"]")
            self.tid(preview_b, "preview-original").get_by_text(
                "السؤال الأصلي كما ورد", exact=True).click()
            expect(self.tid(preview_b, "preview-original-text")
                   ).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(
                self.raw_result(context, question_request_id),
                baseline_question_raw)

            # A source-A edit preserving FACT_A outdates only A.
            item_a = self.region_item(page, region_a)
            checkpoint = len(self.requests_log)
            self.tid(item_a, "region-current-text-input").fill(
                fx["CHANGED_A"])
            self.assertEqual(self.mutations_since(checkpoint), [])
            self.click_expect(
                page, "PUT", self.region_path(region_a), 200,
                self.tid(item_a, "region-review-commit"))
            self.assertEqual(
                self.last_mutation_body(
                    checkpoint, "PUT", self.region_path(region_a)),
                {"inclusion": "included",
                 "current_text": fx["CHANGED_A"],
                 "current_label": "body"})
            snapshot = self.snapshot(context)
            region_a = self.region_by_section(
                snapshot, "synthetic-section-river")
            region_b = self.region_by_section(
                snapshot, "synthetic-section-trees")
            self.assertEqual(region_a["current_text"], fx["CHANGED_A"])
            self.assertEqual(region_a["raw_text"], fx["RAW_A"])
            self.assertEqual(
                region_a["linked_question_ids"],
                [question_a["question_id"]])
            question_a = self.question_by_slot(
                snapshot, "slot-remember")
            question_b = self.question_by_slot(
                snapshot, "slot-understand")
            self.assertEqual(question_a["status"], "outdated")
            self.assertEqual(question_a["blocks"], [])
            self.assertEqual(
                question_a["anchors"],
                [[region_a["region_id"], fx["FACT_A"]]])
            self.assertEqual(question_a["question_author"], "model")
            self.assertEqual(question_b["status"], "approved")
            assert_question_dom(question_a)
            assert_question_dom(question_b)
            self.assertEqual(
                [item["question_id"] for item in assert_preview()],
                [question_b["question_id"]])

            # Restoring EDITED_A never resurrects the approval.
            item_a = self.region_item(page, region_a)
            checkpoint = len(self.requests_log)
            self.tid(item_a, "region-current-text-input").fill(
                fx["EDITED_A"])
            self.click_expect(
                page, "PUT", self.region_path(region_a), 200,
                self.tid(item_a, "region-review-commit"))
            self.assertEqual(
                self.last_mutation_body(
                    checkpoint, "PUT", self.region_path(region_a)),
                {"inclusion": "included",
                 "current_text": fx["EDITED_A"],
                 "current_label": "body"})
            snapshot = self.snapshot(context)
            question_a = self.question_by_slot(
                snapshot, "slot-remember")
            self.assertEqual(question_a["status"], "outdated")
            self.assertEqual(question_a["blocks"], [])
            self.assertEqual(
                [item["question_id"] for item in assert_preview()],
                [question_b["question_id"]])

            # A fresh explicit approval restores A; both return.
            question_a = approve(question_a)
            self.assertEqual(question_a["status"], "approved")
            question_b = self.question_by_slot(
                self.snapshot(context), "slot-understand")
            assert_question_dom(question_a)
            preview = assert_preview()
            self.assertEqual(
                {item["question_id"] for item in preview},
                {question_a["question_id"], question_b["question_id"]})
            resolved_a = next(
                item for item in preview
                if item["question_id"] == question_a["question_id"])
            self.assertEqual(
                resolved_a["resolved_anchors"],
                [[region_a["region_id"], fx["FACT_A"], fx["EDITED_A"]]])
            self.assertEqual(
                self.raw_result(context, source_request_id),
                baseline_source_raw)
            self.assertEqual(
                self.raw_result(context, question_request_id),
                baseline_question_raw)

            # A guidance-only edit still makes the current version teacher-edited.
            # The wording author remains model; do not conflate those axes.
            card_a = self.question_card(page, question_a)
            self.tid(card_a, "question-edit").click()
            guidance_form = self.tid(card_a, "question-edit-form")
            expect(guidance_form).to_be_visible(timeout=ASSERT_TIMEOUT_MS)
            guidance_text = "  إرشاد بصياغة المعلم فقط ١٢٣  "
            guide = self.tid(card_a, "question-edit-guidance")
            checkpoint = len(self.requests_log)
            guide.fill(guidance_text)
            self.assertEqual(self.mutations_since(checkpoint), [])
            self.click_expect(page, "PATCH", self.question_path(question_a), 200,
                              self.tid(card_a, "question-edit-commit"))
            self.assertEqual(self.last_mutation_body(
                checkpoint, "PATCH", self.question_path(question_a)),
                {"answer_guidance": guidance_text})
            question_a = self.question_by_slot(self.snapshot(context), "slot-remember")
            self.assertEqual(question_a["status"], "draft")
            self.assertEqual(question_a["question_author"], "model")
            self.assertEqual(dict(question_a["field_authors"])["answer_guidance"], "teacher")
            self.assertEqual(question_a["original_answer_guidance"], fx["GUIDANCE_1"])
            assert_question_dom(question_a)
            self.assertEqual([entry["question_id"] for entry in assert_preview()],
                             [question_b["question_id"]])
            question_a = approve(question_a)
            assert_question_dom(question_a)
            preview = assert_preview()
            entry_a = next(entry for entry in preview
                           if entry["question_id"] == question_a["question_id"])
            self.assertEqual(entry_a["author"], "teacher")
            self.assertEqual(entry_a["answer_guidance"], guidance_text)
            self.assertEqual(self.raw_result(context, question_request_id), baseline_question_raw)

            # Compact narrow check: the existing panes switch; no
            # list or card is duplicated for the narrow layout.
            checkpoint = len(self.requests_log)
            page.set_viewport_size({"width": 390, "height": 844})
            self.tid(page, "pane-tab-source").click()
            expect(self.tid(page, "source-pane")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "work-pane")).to_be_hidden(
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.tid(page, "region-list").count(), 1)
            self.assertEqual(
                self.tid(page, "question-list").count(), 1)
            self.tid(page, "pane-tab-work").click()
            expect(self.tid(page, "work-pane")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "region-list")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "question-list")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            page.set_viewport_size({"width": 1280, "height": 900})
            expect(self.tid(page, "work-pane")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            expect(self.tid(page, "source-pane")).to_be_visible(
                timeout=ASSERT_TIMEOUT_MS)
            self.assertEqual(self.mutations_since(checkpoint), [])
            self.assert_clean_network()


if __name__ == "__main__":
    unittest.main()
