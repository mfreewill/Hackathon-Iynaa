"""One capacity recovery browser regression (BROWSER_RECOVERY_MODE=capacity)."""
import os
import pathlib
import re
import unittest
from urllib.parse import urlsplit

import test_review_drafts
from playwright.sync_api import expect


def _fx(fx, key):
    raw = fx.get(key, fx.get(key.lower())) if isinstance(fx, dict) else getattr(fx, key, getattr(fx, key.lower(), None))
    return str(raw)


def _tid(page, name):
    return page.locator(f'[data-testid="{name}"]')


def _assert_not_useful(h, loc, label):
    h.assertEqual(loc.count(), 1, f"{label} must exist exactly once")
    item = loc.first
    h.assertFalse(item.is_visible() and item.is_enabled(),
                  f"{label} must be hidden or disabled after capacity rejection")


class RecoveryCapacityBrowserTests(unittest.TestCase):
    def test_capacity_413_leaves_snapshot_and_source_intact(self):
        mode = os.environ.get("BROWSER_RECOVERY_MODE", "")
        if mode != "capacity":
            self.fail(f"BROWSER_RECOVERY_MODE must be 'capacity', got {mode!r}")
        recovery_dir = os.environ.get("BROWSER_RECOVERY_DIR", "")
        self.assertTrue(recovery_dir, "BROWSER_RECOVERY_DIR is required")
        pathlib.Path(recovery_dir).mkdir(parents=True, exist_ok=True)
        h = test_review_drafts.ReviewDraftPreservationBrowserTests()
        type(h).setUpClass()
        h.setUp()
        with h.browser_page() as (context, page):
            def snapshot():
                resp = context.request.get(h.base_url + '/api/session/snapshot', max_redirects=0)
                h.assertEqual(resp.status, 200, "snapshot GET must return 200")
                return resp.json()

            page.goto('/')
            grade = page.locator("#grade")
            objective = page.locator("#objective")
            page_number = page.locator("#page-number")
            upload = page.locator("#page-file")
            grade.fill(_fx(h.fx, "GRADE"))
            objective.fill(_fx(h.fx, "OBJECTIVE"))
            page_number.fill("1")
            upload.set_input_files({"name": "page1.png", "mimeType": "image/png", "buffer": h.png_bytes})
            prepare = _tid(page, "prepare-page").first
            with page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == "/api/session/start") as start_info:
                prepare.click()
            h.assertEqual(start_info.value.status, 201, "Start must return 201")
            confirm = _tid(page, "confirm-page")
            expect(confirm).to_be_enabled()
            baseline = snapshot()
            acknowledged_refs = {name: (_tid(page, name).text_content() or "").strip() for name in ("source-id", "source-sha256", "render-sha256")}
            for name, value in acknowledged_refs.items():
                h.assertTrue(value, f"{name} must show acknowledged source metadata before the capacity attempt")
            acknowledged_href = _tid(page, "source-original-link").get_attribute("href")
            h.assertTrue(acknowledged_href, "original page link must have an href before the capacity attempt")
            # Parent owns the actual retained-cap injection; this marker stays in BROWSER_RECOVERY_DIR.
            (pathlib.Path(recovery_dir) / "capacity.armed").write_text("capacity\n", encoding="utf-8")
            with page.expect_response(lambda r: r.request.method == "POST" and urlsplit(r.url).path == "/api/session/source-analysis") as source_info:
                confirm.click()
            source_resp = source_info.value
            h.assertEqual(source_resp.status, 413, "source-analysis must return 413 at the retained cap")
            try:
                body = source_resp.json()
            except Exception:
                h.fail(f"413 body must be JSON, got: {source_resp.text()!r}")
            h.assertEqual(set(body), {"detail"}, f"413 body must contain only 'detail', got {sorted(body)}")
            for leaked in ("snapshot", "request_id", "raw_retention"):
                h.assertNotIn(leaked, body)
            detail = body["detail"]
            h.assertIsInstance(detail, dict, f"detail must be a dict, got {type(detail).__name__}: {detail!r}")
            h.assertEqual(detail.get("code"), "retained_capacity_exceeded")
            h.assertEqual(detail.get("admission"), "not_started")
            h.assertLessEqual(set(detail), {"code", "admission", "message"})
            h.assertEqual(snapshot(), baseline, "snapshot must equal baseline after rejected admission (no request created)")
            status = _tid(page, "provider-operation-status").first
            expect(status).to_be_visible()
            expect(status).to_contain_text(re.compile(r"سعة|مساحة"))
            text = status.inner_text()
            h.assertTrue("سعة" in text or "مساحة" in text, f"status must name capacity/space, got: {text!r}")
            for bad in ("unconfirmed", "pending", "غير مؤكد", "قيد الانتظار", "بانتظار التأكيد"):
                h.assertNotIn(bad.lower(), text.lower(), f"status must not use unconfirmed/pending wording: {text!r}")
            _assert_not_useful(h, _tid(page, "provider-operation-check"), "provider-operation-check")
            _assert_not_useful(h, _tid(page, "provider-operation-cancel"), "provider-operation-cancel")
            expect(prepare).to_be_enabled()
            preview = _tid(page, "source-preview")
            link = _tid(page, "source-original-link")
            if preview.count() == 0 or not preview.first.is_visible():
                tab = _tid(page, "pane-tab-source")
                if tab.count() > 0 and tab.first.is_visible():
                    tab.first.click()
            h.assertGreater(preview.count(), 0, "source-preview must still exist")
            h.assertGreater(link.count(), 0, "source-original-link must still exist")
            h.assertEqual(link.first.get_attribute("href"), acknowledged_href, "original page link must keep its acknowledged href")
            for name, expected in acknowledged_refs.items():
                h.assertEqual((_tid(page, name).text_content() or "").strip(), expected, f"{name} must match acknowledged source metadata")
            original_resp = context.request.get(h.base_url + acknowledged_href, max_redirects=0)
            h.assertEqual(original_resp.status, 200, "original page file must remain accessible")
            # A warned Retry may be offered; leave it untouched so no replay can occur.
            page.wait_for_load_state("networkidle")
            posts = [r for r in h.requests_log if r.get("method") == "POST"]
            paths = [urlsplit(r.get("path", "")).path for r in posts]
            h.assertEqual(paths.count("/api/session/start"), 1, f"expected one Start POST, got {posts}")
            h.assertEqual(paths.count("/api/session/source-analysis"), 1, f"expected one source-analysis POST (no replay), got {posts}")
            h.assert_clean_network()


if __name__ == "__main__":
    unittest.main()
