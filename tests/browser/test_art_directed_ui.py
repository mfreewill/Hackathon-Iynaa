"""First-screen art/motion regression: structural checks, not a beauty proof.

Bounds: headless Chromium is not physical Safari/WebKit on a phone, and this
covers one boot state (the first screen), not the entire UI. A parent harness
serves the real loopback app; this test never starts it and sends no work to
model endpoints. It only observes the normal session bootstrap.
"""

import json
import os
import pathlib
import re
import unittest
import urllib.parse

from playwright.sync_api import expect, sync_playwright

ARABIC = re.compile("[" + chr(0x600) + "-" + chr(0x6FF) + "]")
NON_DURABLE = re.compile(
    r"not\s+(saved|stored|kept|durable|permanent)|won'?t\s+be\s+saved|"
    r"nothing\s+is\s+saved|never\s+saved|temporary|session[-\s]?only|"
    r"disappear|lost\s+when|gone\s+when|غير\s+محفوظ|غير\s+دائم|"
    r"لن\s+يتم\s+حفظ|لن\s+تُحفظ|لا\s+يتم\s+حفظ|مؤقت|ستختفي|عند\s+(إغلاق|تحديث|مغادرة)",
    re.I,
)
DYNAMIC_HOSTED = re.compile(
    r"hosted|hosting|dynamic|server|online|مستضاف|استضافة|ديناميك|خادم|عبر\s+الإنترنت",
    re.I,
)
FORBIDDEN_PATH = re.compile(
    r"source[-_/]*analy|/analy[sz]e(?:/|$)|/analysis(?:/|$)|"
    r"questions?[-_/]+generate|generate[-_/]+questions?|/generate(?:/|$)"
)
DETAILS_SELECTOR = (
    "details.session-details, details#session-details, "
    ".session-details details, details:has(summary.unsaved-summary)"
)
PREPARE_SELECTOR = "[data-testid='prepare-page']"


class TestArtDirectedUi(unittest.TestCase):
    """One boot-state regression: art presence, pick area, notices, motion."""

    def test_first_screen_art_and_motion(self):
        base_url = os.environ.get("BROWSER_BASE_URL", "").strip()
        self.assertTrue(base_url, "BROWSER_BASE_URL required (parent serves the app)")
        executable = os.environ.get("BROWSER_EXECUTABLE", "").strip()
        self.assertTrue(executable, "BROWSER_EXECUTABLE required (no bundled-browser fallback)")
        self.assertTrue(
            pathlib.Path(executable).exists(),
            f"BROWSER_EXECUTABLE path does not exist: {executable}",
        )
        artifact_raw = os.environ.get("BROWSER_ARTIFACT_DIR")
        artifact_path = pathlib.Path(artifact_raw) if artifact_raw else None
        if artifact_path:
            artifact_path.mkdir(parents=True, exist_ok=True)
        summaries = []

        def origin_of(url):
            parsed = urllib.parse.urlparse(url)
            return f"{parsed.scheme}://{parsed.netloc}"

        base_origin = origin_of(base_url)

        def is_api(item):
            return (
                "/api" in item["path"]
                or item["method"] != "GET"
                or item["type"] in ("xhr", "fetch")
            )

        def save_json():
            if artifact_path:
                (artifact_path / "art-ui-summary.json").write_text(
                    json.dumps(summaries, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        def brand_visible(page):
            brand_check = r"""() => {
              const chainVisible = (el) => {
                if (!el) return false;
                if (typeof el.checkVisibility === 'function') {
                  let ok = true;
                  try { ok = el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}); }
                  catch (err) { ok = el.checkVisibility(); }
                  if (!ok) return false;
                }
                let node = el;
                while (node && node.nodeType === 1) {
                  if (node.hidden || node.hasAttribute('hidden')) return false;
                  const cs = getComputedStyle(node);
                  if (cs.display === 'none') return false;
                  if (cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
                  if (!(parseFloat(cs.opacity) > 0)) return false;
                  node = node.parentElement;
                }
                const rect = el.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
              };
              return [...document.querySelectorAll('.brand')].some((brand) => {
                if (!chainVisible(brand)) return false;
                return [...brand.querySelectorAll('.brand-word, .brand-latin')].some((child) => {
                  const text = (child.textContent || '').trim();
                  if (text !== 'IYNAA' && text !== 'إيناع') return false;
                  return chainVisible(child);
                });
              });
            }"""
            for _ in range(30):
                if page.evaluate(brand_check):
                    return True
                page.wait_for_timeout(100)
            return False

        def notice_text(page):
            return page.evaluate(r"""() => {
              const d = document.querySelector('details.session-details, details#session-details, .session-details details, details:has(> summary.unsaved-summary)');
              if (!d) return '';
              const parts = [...d.children].filter((c) => c.tagName !== 'SUMMARY').map((c) => c.innerText || c.textContent || '');
              return parts.join('\n').trim() || (d.innerText || '');
            }""")

        def motion_snapshot(page):
            return page.evaluate(r"""() => {
              const sels = ['.iynaa-artwork', '.iynaa-artwork *', '[class*="artwork"]', '[class*="stage"]', '[class*="workflow"]', '.stage', '.workflow'];
              const seen = new Set(); const out = [];
              const secs = (v) => v.split(',').map((x) => { x = x.trim(); return x.endsWith('ms') ? parseFloat(x) / 1000 : parseFloat(x) || 0; });
              for (const s of sels) document.querySelectorAll(s).forEach((e) => {
                if (seen.has(e)) return; seen.add(e);
                const cs = getComputedStyle(e);
                out.push({key: out.length + ':' + e.tagName + ':' + (e.getAttribute('class') || ''), animation: secs(cs.animationDuration), transition: secs(cs.transitionDuration)});
              });
              return out;
            }""")

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, chromium_sandbox=True, executable_path=executable
            )
            try:
                for width, height in ((390, 844), (430, 860)):
                    with self.subTest(viewport=f"{width}x{height}"):
                        seen, foreign, page_errors = [], [], []
                        context = browser.new_context(
                            viewport={"width": width, "height": height},
                            service_workers="block",
                        )
                        try:
                            def handle_route(route):
                                req = route.request
                                seen.append({
                                    "method": req.method,
                                    "url": req.url,
                                    "path": urllib.parse.urlparse(req.url).path,
                                    "type": req.resource_type,
                                })
                                scheme = urllib.parse.urlparse(req.url).scheme
                                if scheme in ("http", "https") and origin_of(req.url) != base_origin:
                                    foreign.append(req.url)
                                    route.abort()
                                else:
                                    route.continue_()

                            context.route("**/*", handle_route)
                            page = context.new_page()
                            page.on("pageerror", lambda err: page_errors.append(str(err)))
                            page.set_default_timeout(10_000)
                            page.goto(base_url, wait_until="domcontentloaded", timeout=30_000)

                            # Boot-ready: the real prepare control becomes enabled.
                            prepare = page.locator(PREPARE_SELECTOR)
                            expect(prepare).to_have_count(1)
                            expect(prepare).to_be_visible(timeout=15_000)
                            expect(prepare).to_be_enabled(timeout=15_000)

                            # 1. Brand + safe inline artwork (structure, not beauty).
                            self.assertTrue(brand_visible(page), "visible IYNAA / إيناع brand text")
                            svg = page.locator(".iynaa-artwork svg").first
                            expect(svg).to_be_visible()
                            svg_box = svg.bounding_box()
                            self.assertIsNotNone(svg_box, "artwork svg has no box")
                            self.assertGreater(svg_box["width"], 0)
                            self.assertGreater(svg_box["height"], 0)
                            audit = page.evaluate(r"""() => {
                              const s = document.querySelector('.iynaa-artwork svg');
                              if (!s) return null;
                              let handlers = 0; let foreignHref = 0;
                              for (const e of [s, ...s.querySelectorAll('*')]) for (const a of e.attributes || []) {
                                const n = a.name.toLowerCase(); const v = a.value.replace(/\s+/g, '');
                                if (n.startsWith('on')) handlers += 1;
                                if ((n === 'href' || n.endsWith(':href')) && !a.value.startsWith('#')) foreignHref += 1;
                                if ((n === 'fill' || n === 'stroke' || n === 'style') && v.includes('url(') && !v.includes('url(#') && !v.includes('url("#')) foreignHref += 1;
                              }
                              return {geometry: s.querySelectorAll('path,circle,ellipse,rect,line,polyline,polygon').length,
                                      forbidden: s.querySelectorAll('script,foreignObject,image').length,
                                      handlers: handlers, foreignHref: foreignHref};
                            }""")
                            self.assertIsNotNone(audit)
                            self.assertGreaterEqual(audit["geometry"], 3)
                            self.assertEqual(0, audit["forbidden"], "script/foreignObject/image in svg")
                            self.assertEqual(0, audit["handlers"], "event handler attribute in svg")
                            self.assertEqual(0, audit["foreignHref"], "foreign href/url in svg")

                            # 2. Real pick input. A transparent overlay over the
                            #    Arabic affordance is allowed by design.
                            file_input = page.locator("#page-file")
                            file_box = file_input.bounding_box()
                            self.assertIsNotNone(file_box, "#page-file has no layout box")
                            self.assertGreaterEqual(file_box["width"], 44)
                            self.assertGreaterEqual(file_box["height"], 44)
                            self.assertLessEqual(file_box["y"], 400)
                            label_audit = page.evaluate(r"""() => {
                              const input = document.querySelector('#page-file');
                              if (!input) return null;
                              let label = (input.labels && input.labels[0]) || (input.id && document.querySelector('label[for="' + input.id + '"]')) || input.closest('label');
                              const area = document.querySelector('.field-file');
                              const vis = (e) => { if (!e) return false; const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); return r.width > 0 && r.height > 0 && cs.display !== 'none' && cs.visibility !== 'hidden'; };
                              return {labelText: label ? (label.textContent || '').trim() : '', labelVisible: vis(label),
                                      areaText: area ? (area.innerText || '') : '', areaVisible: vis(area)};
                            }""")
                            self.assertIsNotNone(label_audit)
                            self.assertRegex(label_audit["labelText"], ARABIC)
                            field_file = page.locator(".field-file").first
                            expect(field_file).to_be_visible()
                            self.assertTrue(label_audit["areaVisible"])
                            self.assertTrue(
                                label_audit["labelVisible"] or ARABIC.search(label_audit["areaText"]),
                                "visible Arabic pick affordance",
                            )
                            overflow = page.evaluate(
                                "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth, bw: document.body ? document.body.scrollWidth : 0})"
                            )
                            self.assertLessEqual(max(overflow["sw"], overflow["bw"]), overflow["iw"] + 1, "horizontal overflow")
                            if artifact_path:
                                page.screenshot(path=str(artifact_path / f"first-screen-{width}x{height}.png"))

                            file_input.focus()
                            focus_audit = page.evaluate(r"""() => {
                              const input = document.querySelector('#page-file');
                              if (!input) return null;
                              const wrap = input.closest('.field-file') || (input.labels && input.labels[0] && input.labels[0].closest('.field-file'));
                              return {active: document.activeElement === input, focused: input.matches(':focus'), hasWrap: !!wrap, focusWithin: wrap ? wrap.matches(':focus-within') : false};
                            }""")
                            self.assertTrue(focus_audit["active"] and focus_audit["focused"])
                            self.assertTrue(focus_audit["hasWrap"] and focus_audit["focusWithin"], ".field-file :focus-within")

                            # 3. Unsaved summary + full disclosure, no API fetch on toggle.
                            unsaved = page.locator(".unsaved-summary").first
                            expect(unsaved).to_be_visible()
                            details = page.locator(DETAILS_SELECTOR).first
                            expect(details).to_have_js_property("open", False)
                            summary = details.locator("summary").first
                            api_before = len([i for i in seen if is_api(i)])
                            summary.click()
                            expect(details).to_have_js_property("open", True)
                            expect(unsaved).to_be_visible()
                            notices = notice_text(page)
                            self.assertGreaterEqual(len(notices.strip()), 20, "full notices, not just the summary")
                            self.assertRegex(notices, NON_DURABLE)
                            self.assertRegex(notices, DYNAMIC_HOSTED)
                            summary.click()
                            expect(details).to_have_js_property("open", False)
                            expect(unsaved).to_be_visible()
                            page.wait_for_timeout(250)
                            self.assertEqual(api_before, len([i for i in seen if is_api(i)]), "extra API request on details toggle")

                            # 4. Motion by computed style, then reduced-motion off.
                            default_motion = motion_snapshot(page)
                            nonzero = {m["key"] for m in default_motion if any(v > 0 for v in m["animation"] + m["transition"])}
                            self.assertTrue(nonzero, "art/stage/workflow has a nonzero animation/transition duration")
                            page.emulate_media(reduced_motion="reduce")
                            reduced_motion = motion_snapshot(page)
                            still = [m["key"] for m in reduced_motion if m["key"] in nonzero and any(v > 0.001 for v in m["animation"] + m["transition"])]
                            self.assertEqual([], still, "reduced motion must disable the measured animations/transitions")
                            self.assertTrue(brand_visible(page), "brand visible under reduced motion")
                            expect(field_file).to_be_visible()
                            self.assertIsNotNone(file_input.bounding_box())

                            # 5. Network bounds for this fresh context.
                            session_posts = [i for i in seen if i["method"] == "POST" and "session" in i["path"].lower()]
                            bad_paths = [i["url"] for i in seen if FORBIDDEN_PATH.search(i["path"].lower())]
                            summaries.append({
                                "viewport": f"{width}x{height}",
                                "svg_geometry": audit["geometry"],
                                "file_box": {k: round(file_box[k], 1) for k in ("x", "y", "width", "height")},
                                "motion_nonzero": sorted(nonzero),
                                "session_posts": len(session_posts),
                                "foreign": foreign,
                                "page_errors": page_errors,
                                "forbidden": bad_paths,
                            })
                            save_json()
                            self.assertEqual([], foreign, "foreign-origin requests")
                            self.assertEqual([], page_errors, "page errors")
                            self.assertEqual([], bad_paths, "source-analysis / questions-generate requests")
                            self.assertEqual(1, len(session_posts), "exactly one session POST per fresh context")
                        finally:
                            context.close()
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
