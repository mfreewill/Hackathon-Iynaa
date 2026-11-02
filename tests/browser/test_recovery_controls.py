"""Native recovery-control independence: one method, three exact schedules. Parent holds genuine providers/bodies via BROWSER_RECOVERY_DIR markers; this test drives only the real UI via a composed helper instance, with same-origin inspection GET (max_redirects=0)."""
import contextlib, json, os, pathlib, time, unittest
from urllib.parse import urlsplit
from playwright.sync_api import expect
import test_review_drafts
MODES = ("held-check", "held-cancel", "held-snapshot"); DRAFT = "UNSAVED-RECOVERY-DRAFT-7f3a"; WAIT_S = 15.0
RELEASES = ("CHECK_BODY_RELEASE", "CANCEL_BODY_RELEASE", "SNAPSHOT_BODY_RELEASE", "PROVIDER_1_RELEASE", "PROVIDER_2_RELEASE")
def _make_helper():
    return test_review_drafts.ReviewDraftPreservationBrowserTests()
def _path(url):
    return urlsplit(url).path
def _touch(folder, name):
    pathlib.Path(folder, name).touch()
def _wait_json(page, folder, name):
    target, deadline = pathlib.Path(folder, name), time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        if target.is_file():
            with contextlib.suppress(ValueError, OSError):
                return json.loads(target.read_text(encoding="utf-8"))
        page.wait_for_timeout(100)
    raise AssertionError(f"marker not ready within 15s: {name}")
def _snapshot(h, context):
    resp = context.request.get(h.base_url + "/api/session/snapshot", max_redirects=0, timeout=10000); assert resp.status == 200, resp.status
    return resp.json()
def _draft_value(h, page, question):
    return h.tid(h.question_card(page, question), "question-edit-question").input_value()
def _pump_frame(page):
    page.evaluate("() => new Promise(requestAnimationFrame)")
def _state(h, context, page, understand):
    return {"snapshot": _snapshot(h, context), "status": h.tid(page, "provider-operation-status").text_content(), "step": h.tid(page, "work-pane").get_attribute("data-active-step"), "buttons": (h.tid(page, "provider-operation-check").is_disabled(), h.tid(page, "provider-operation-cancel").is_disabled()), "draft": _draft_value(h, page, understand)}
def _find_resp(resps, pred, start=0):
    return next((r for r in reversed(resps[start:]) if pred(r)), None)
def _finish_held(page, resps, pred, start, known):
    resp, deadline = known or _find_resp(resps, pred, start), time.monotonic() + WAIT_S
    while resp is None and time.monotonic() < deadline:
        page.wait_for_timeout(100); resp = _find_resp(resps, pred, start)
    assert resp is not None, "held response was never observed"; resp.finished(); return resp
def _is_gen_resp(resp):
    return resp.request.method == "POST" and _path(resp.url) == "/api/session/questions/generate"
def _wait_gen_resp(page, resps, rid):
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        resp = next((r for r in resps if _is_gen_resp(r) and r.request.post_data_json["request_id"] == rid), None)
        if resp is not None:
            return resp
        page.wait_for_timeout(100)
    raise AssertionError(f"generation response for {rid} not observed within 15s")
def _wait_cancelled(h, context, page, rid):
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        snap = _snapshot(h, context)
        if any(row["request_id"] == rid for row in snap["requests"]):
            row = next(row for row in snap["requests"] if row["request_id"] == rid)
            if row["kind"] == "questions" and row["state"] == "terminal" and row["outcome"] == "cancelled":
                status = h.tid(page, "provider-operation-status"); expect(status).to_be_visible(timeout=15000); expect(status).to_contain_text("ملغيًا", timeout=15000)
                return snap
        page.wait_for_timeout(100)
    raise AssertionError("visible cancelled state never settled")
def _counts(log, rid, src):
    source = [e for e in log if e.get("method") == "POST" and _path(e.get("path", "")) == "/api/session/source-analysis"]
    gens = [e for e in log if e.get("method") == "POST" and _path(e.get("path", "")) == "/api/session/questions/generate" and (e.get("json") or {}).get("source_id") == src]
    checks = [e for e in log if _path(e.get("path", "")) == f"/api/session/requests/{rid}/status"]
    cancels = [e for e in log if _path(e.get("path", "")) == f"/api/session/requests/{rid}/cancel"]
    return len(source), len(gens), len(checks), len(cancels)
class TestRecoveryControls(unittest.TestCase):
    def setUp(self):
        self.h = _make_helper(); type(self.h).setUpClass(); self.h.setUp()
    def test_recovery_controls_independence(self):
        h, mode, raw_dir = self.h, os.environ.get("BROWSER_RECOVERY_MODE", ""), os.environ.get("BROWSER_RECOVERY_DIR", "")
        self.assertIn(mode, MODES); self.assertTrue(raw_dir, "BROWSER_RECOVERY_DIR is required")
        folder = pathlib.Path(raw_dir); folder.mkdir(parents=True, exist_ok=True)
        try:
            with h.browser_page() as (context, page):
                resps = []; page.on("response", lambda response: resps.append(response))
                chk = lambda: h.tid(page, "provider-operation-check"); can = lambda: h.tid(page, "provider-operation-cancel"); gen = lambda: page.locator("#generate-questions")
                qs0 = h.setup_generated(context, page)["questions"]; h.assertEqual(len(qs0), 2)
                remember = next(q for q in qs0 if q["slot_id"] == "slot-remember"); understand = next(q for q in qs0 if q["slot_id"] == "slot-understand")
                h.approve_question(page, remember)
                editor = h.open_question_editor(h.question_card(page, understand)); editor.fill(DRAFT); h.assertEqual(editor.input_value(), DRAFT)
                baseline = _snapshot(h, context); base_preview = baseline["preview_items"]; base_ids = [q["question_id"] for q in baseline["questions"]]; h.assertEqual(len(base_ids), 2)
                h.tid(page, "workflow-nav-source").click(); expect(gen()).to_be_visible(timeout=15000)
                tmp = folder / "ARM.json.tmp"; tmp.write_text(json.dumps({"mode": mode}), encoding="utf-8"); os.replace(tmp, folder / "ARM.json")
                gen().click(); ready1 = _wait_json(page, folder, "PROVIDER_1_READY.json")
                rid1, src = str(ready1["request_id"]), str(ready1["source_id"])
                expect(chk()).to_be_visible(timeout=15000); expect(can()).to_be_visible(timeout=15000)
                is_check = lambda r: _path(r.url) == f"/api/session/requests/{rid1}/status"
                is_cancel = lambda r: _path(r.url) == f"/api/session/requests/{rid1}/cancel"; is_snap = lambda r: _path(r.url) == "/api/session/snapshot"
                held_pred, held_start, held_resp = None, 0, None
                if mode == "held-check":
                    held_pred, held_start = is_check, len(resps); chk().click(); _wait_json(page, folder, "CHECK_BODY_READY.json"); held_resp = _find_resp(resps, is_check, held_start)
                    expect(chk()).to_be_disabled(); expect(can()).to_be_enabled()
                    with page.expect_response(is_cancel, timeout=15000) as got:
                        can().click()
                    h.assertEqual(got.value.status, 200)
                elif mode == "held-cancel":
                    held_pred, held_start = is_cancel, len(resps); can().click(); _wait_json(page, folder, "CANCEL_BODY_READY.json"); held_resp = _find_resp(resps, is_cancel, held_start)
                    expect(can()).to_be_disabled(); expect(chk()).to_be_enabled()
                    with page.expect_response(is_check, timeout=15000) as got:
                        chk().click()
                    h.assertEqual(got.value.status, 200)
                elif mode == "held-snapshot":
                    cstart = len(resps); can().click(); _wait_json(page, folder, "CANCEL_BODY_READY.json"); cancel_resp = _find_resp(resps, is_cancel, cstart)
                    expect(can()).to_be_disabled(); expect(chk()).to_be_enabled()
                    held_pred, held_start = is_snap, len(resps); chk().click(); _wait_json(page, folder, "SNAPSHOT_BODY_READY.json"); held_resp = _find_resp(resps, is_snap, held_start)
                    _touch(folder, "CANCEL_BODY_RELEASE"); _finish_held(page, resps, is_cancel, cstart, cancel_resp); _pump_frame(page)
                settled = _wait_cancelled(h, context, page, rid1)
                h.assertEqual(settled["questions"], baseline["questions"]); h.assertEqual(settled["preview_items"], base_preview); h.assertEqual(_draft_value(h, page, understand), DRAFT)
                if not gen().is_visible():
                    h.tid(page, "workflow-nav-source").click()
                expect(gen()).to_be_enabled(timeout=15000); gen().click(); ready2 = _wait_json(page, folder, "PROVIDER_2_READY.json")
                rid2 = str(ready2["request_id"]); h.assertNotEqual(rid2, rid1); h.assertEqual(str(ready2["source_id"]), src)
                expect(chk()).to_be_visible(timeout=15000); chk().focus(); expect(chk()).to_be_focused(); before = _state(h, context, page, understand)
                old = {"held-check": ("CHECK_BODY_RELEASE", "CHECK_BODY_DELIVERED.json"), "held-cancel": ("CANCEL_BODY_RELEASE", "CANCEL_BODY_DELIVERED.json"), "held-snapshot": ("SNAPSHOT_BODY_RELEASE", "SNAPSHOT_BODY_DELIVERED.json")}[mode]
                _touch(folder, old[0]); _finish_held(page, resps, held_pred, held_start, held_resp); h.assertIsInstance(_wait_json(page, folder, old[1]), dict); _pump_frame(page)
                h.assertEqual(_state(h, context, page, understand), before); expect(chk()).to_be_focused()
                _touch(folder, "PROVIDER_1_RELEASE"); original_gen_resp = _wait_gen_resp(page, resps, rid1); original_gen_resp.finished(); _wait_json(page, folder, "PROVIDER_1_RETURNED.json"); _pump_frame(page)
                h.assertEqual(_state(h, context, page, understand), before); expect(chk()).to_be_focused()
                _touch(folder, "PROVIDER_2_RELEASE"); final = _wait_gen_resp(page, resps, rid2); h.assertEqual(final.status, 201); final.finished()
                deadline, final_snap = time.monotonic() + WAIT_S, None
                while time.monotonic() < deadline and final_snap is None:
                    cand = _snapshot(h, context); final_snap = cand if len(cand["questions"]) == 4 else None
                    if final_snap is None:
                        page.wait_for_timeout(100)
                h.assertIsNotNone(final_snap, "final snapshot never reached 4 questions"); final_ids = [q["question_id"] for q in final_snap["questions"]]
                h.assertTrue(set(base_ids) <= set(final_ids)); h.assertEqual(final_snap["preview_items"], base_preview); h.assertEqual(_draft_value(h, page, understand), DRAFT)
                approved = next(q for q in final_snap["questions"] if q["question_id"] == remember["question_id"]); h.assertEqual(approved["status"], "approved")
                counts = _counts(h.requests_log, rid1, src); h.assertEqual(counts, (1, 3, 1, 1), h.requests_log); h.assert_clean_network()
                arts = pathlib.Path(os.environ.get("BROWSER_ARTIFACT_DIR", "artifacts")); arts.mkdir(parents=True, exist_ok=True)
                summary = {"mode": mode, "request_id_1": rid1, "request_id_2": rid2, "source_id": src, "questions": final_ids, "draft_preserved": True, "counts": list(counts)}
                (arts / f"recovery-controls-{mode}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8"); page.screenshot(path=str(arts / f"recovery-controls-{mode}.png"))
        finally:
            for name in RELEASES:
                with contextlib.suppress(Exception):
                    _touch(folder, name)
