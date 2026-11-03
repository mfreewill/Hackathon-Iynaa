"""Shared request-body defense regressions against the actual ASGI app.

Fixture deadline ownership: healthy setup, parse-error and recovery
requests run under the shipping 30s/300s body deadlines. A short
test-only deadline is scoped to one deliberately withheld request at
a time (restored in ``finally``); it is never a suite-wide budget.
The contract guarded here is a resource upper bound (one total
deadline per body read), not an exact no-earlier-than instant: on
Windows CPython the loop clock ticks at 15.625ms and
``asyncio.timeout_at`` cancellation was observed ~9ms before the
recorded deadline, so no test asserts a minimum elapsed time.
"""
import asyncio, binascii, contextlib, json, struct, sys, unittest, zlib
from pathlib import Path
import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from teacher_demo import http_app  # noqa: E402
HOST = "127.0.0.1:8000"
ORIGIN = f"http://{HOST}"
MISSING = object()
# Only reader capacity is patched suite-wide. Body deadlines keep
# their shipping values except inside one scoped negative request.
_PATCH = {"MAX_CONCURRENT_BODY_READERS": 2}
SCOPED_BODY_DEADLINE_SECONDS = 0.4  # test-only total deadline, one withheld request
OUTER_BOUND_SECONDS = 2.0  # finite guard: a no-timeout implementation fails here
READY_BOUND_SECONDS = 1.0  # event-readiness bound only, not a throughput benchmark
CLOCK_ALLOWANCE_SECONDS = 0.02  # one Windows 15.625ms tick; arm observation only
OCCUPANCY_HOLD_SECONDS = 2.0  # reader-hold budget only, never a measured deadline
DRIP_GAP_SECONDS = 0.03  # per-chunk gap, far below the scoped total deadline
PLAIN = {"grade": "Grade 4", "objective": "Fractions", "rights_ref": "rights-a"}
def _chunk(tag, data): return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)
def _png():
    raw = b"".join(b"\x00" + b"\xff\xff\xff" * 16 for _ in range(16))
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))
_PNG = _png()
async def _stream(schedule):
    for delay, chunk in schedule:
        if delay: await asyncio.sleep(delay)
        yield chunk
def _held(started, release):
    async def body():
        yield b"{"
        started.set()
        await release.wait()
        yield b"}"
    return body()
@contextlib.contextmanager
def _scoped_attr(name, value):
    saved = getattr(http_app, name, MISSING)
    setattr(http_app, name, value)
    try:
        yield
    finally:
        if saved is MISSING:
            if hasattr(http_app, name): delattr(http_app, name)
        else: setattr(http_app, name, saved)
@contextlib.contextmanager
def _observe_timeout_at(armed):
    """Record real deadline arms; the real timeout still runs."""
    arms = []
    real = asyncio.timeout_at
    def observing(when):
        arms.append((asyncio.get_running_loop().time(), when))
        armed.set()
        return real(when)
    asyncio.timeout_at = observing
    try:
        yield arms
    finally:
        asyncio.timeout_at = real
def _withheld(first, received, release, closed, tail):
    async def body():
        try:
            yield first
            received.set()  # next pull: the first chunk was received
            await release.wait()  # held until the product deadline cancels the read
            yield tail
        finally:
            closed.set()
    return body()
def _dripping(chunks, progressed, flowed, release, closed, tail):
    async def body():
        loop = asyncio.get_running_loop()
        try:
            for chunk in chunks:
                yield chunk
                progressed.append(loop.time())  # acknowledged on the next pull
                await asyncio.sleep(DRIP_GAP_SECONDS)
            flowed.set()  # every genuine chunk flowed; completion withheld below
            await release.wait()
            yield tail
        finally:
            closed.set()
    return body()
class BodyDefenseDefaultsTests(unittest.TestCase):
    def test_defaults_keep_provider_lifetime_separate(self):
        for name, expected in (("JSON_BODY_TIMEOUT_SECONDS", 30),
                               ("UPLOAD_BODY_TIMEOUT_SECONDS", 300),
                               ("MAX_CONCURRENT_BODY_READERS", 16)):
            with self.subTest(name=name):
                actual = getattr(http_app, name, MISSING)
                self.assertIsNot(actual, MISSING); self.assertEqual(actual, expected)
        self.assertEqual(http_app.PROVIDER_TIMEOUT_SECONDS, 600)
class BodyDefenseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._saved = {name: getattr(http_app, name, MISSING) for name in _PATCH}
        self.addCleanup(self._restore)
        for name, value in _PATCH.items(): setattr(http_app, name, value)
        app = http_app.create_app(allowed_hosts=(HOST,), allowed_origin=ORIGIN)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url=ORIGIN, headers={"Origin": ORIGIN})
    async def asyncTearDown(self): await self.client.aclose()
    def _restore(self):
        for name, value in self._saved.items():
            if value is MISSING:
                if hasattr(http_app, name): delattr(http_app, name)
            else: setattr(http_app, name, value)
    def _mut(self, csrf, ctype="application/json"): return {"Content-Type": ctype, "X-CSRF-Token": csrf}
    async def _post_session(self, body): return await self.client.post("/api/sessions", content=body, headers={"Content-Type": "application/json"})
    def _created(self, resp):
        self.assertEqual(resp.status_code, 201)
        token = resp.json().get("csrf_token")
        self.assertTrue(isinstance(token, str) and token)
        self.assertIn(http_app.SESSION_COOKIE + "=", resp.headers.get("set-cookie", "")); return token
    async def _new_session(self, body=b"{}"): return self._created(await self._post_session(body))
    def _refusal(self, resp, status, code):
        self.assertEqual(resp.status_code, status)
        detail = resp.json().get("detail"); self.assertIsInstance(detail, dict)
        self.assertEqual(detail.get("code"), code); self.assertEqual(detail.get("admission"), "not_started")
        self.assertNotIn("set-cookie", resp.headers)
    async def _settings(self, csrf, body): return await self.client.put("/api/session/settings", content=body, headers=self._mut(csrf))
    async def _upload(self, csrf, body): return await self.client.post("/api/session/start", content=body, headers=self._mut(csrf, "application/octet-stream"))
    async def _page_json(self):
        resp = await self.client.get("/api/session/page")
        self.assertEqual(resp.status_code, 200); return resp.json()
    async def _session_settings(self):
        csrf = await self._new_session()
        resp = await self._settings(csrf, json.dumps(PLAIN).encode())
        self.assertEqual(resp.status_code, 200); self.assertEqual(resp.json(), PLAIN)
        return csrf
    async def _ready_page(self):
        csrf = await self._session_settings()
        resp = await self._upload(csrf, _PNG)
        self.assertEqual(resp.status_code, 201)
        return csrf, await self._page_json()
    async def _finish_withheld(self, coro, armed, acknowledged, release, closed):
        task = asyncio.create_task(coro)
        try:
            await asyncio.wait_for(armed.wait(), timeout=READY_BOUND_SECONDS)
            await asyncio.wait_for(acknowledged.wait(), timeout=READY_BOUND_SECONDS)
            response = await asyncio.wait_for(task, timeout=OUTER_BOUND_SECONDS)
            await asyncio.wait_for(closed.wait(), timeout=READY_BOUND_SECONDS)
            return response
        finally:
            release.set()
            if not task.done(): task.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=READY_BOUND_SECONDS)
    def _single_scoped_arm(self, arms, scoped):
        matching = [(at, deadline) for at, deadline in arms
                    if abs((deadline - at) - scoped) <= CLOCK_ALLOWANCE_SECONDS]
        self.assertEqual(len(matching), 1, f"expected one scoped deadline arm, saw {arms!r}")
        return matching[0]
    async def test_healthy_and_timely_streamed_sessions(self):
        await self._new_session(b"{}")
        await self._new_session(_stream([(0, b"{"), (0.005, b"}")]))
    async def test_settings_roundtrip_strict_json_and_slow_body_keep_state(self):
        csrf, _ = await self._ready_page()
        padded = {"grade": "  Grade 𝟙  ", "objective": "\tCount 𝄞 objects  ", "rights_ref": "  rights-𐍈  "}
        resp = await self._settings(csrf, json.dumps(padded, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(resp.status_code, 200); self.assertEqual(resp.json(), padded)
        before = await self._page_json()
        self.assertEqual((before["grade"], before["objective"]), (padded["grade"], padded["objective"]))
        bad_settings = (
            b'{"grade":"CHANGED","grade":"LAST","objective":"Changed","rights_ref":"changed"}',
            b'{"grade":NaN,"grade":"CHANGED","objective":"Changed","rights_ref":"changed"}',
            b'{"grade":"CHANGED","objective":Infinity,"objective":"Changed","rights_ref":"changed"}',
            b'{"grade":NaN,"objective":"Changed","rights_ref":"changed"}',
            b'{"grade":Infinity,"objective":"Changed","rights_ref":"changed"}',
            b'{"grade":-Infinity,"objective":"Changed","rights_ref":"changed"}')
        for raw in bad_settings:
            with self.subTest(raw=raw):
                resp = await self._settings(csrf, raw); self.assertEqual(resp.status_code, 400)
        bad_nested = (
            b'{"hypothetical_assumptions":[{"x":1,"x":2}]}',
            b'{"hypothetical_assumptions":[{"x":NaN,"x":"ok"}]}',
            b'{"evaluate_criteria":[{"x":-Infinity,"x":"ok"}]}')
        for raw in bad_nested:
            with self.subTest(raw=raw):
                resp = await self.client.patch("/api/session/questions/missing", content=raw, headers=self._mut(csrf))
                self.assertEqual(resp.status_code, 400)
        changed = json.dumps({"grade": "CHANGED SLOW", "objective": "Changed", "rights_ref": "changed"}).encode()
        mid = len(changed) // 2
        received, release, closed, armed = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
        with _scoped_attr("JSON_BODY_TIMEOUT_SECONDS", SCOPED_BODY_DEADLINE_SECONDS), _observe_timeout_at(armed) as arms:
            resp = await self._finish_withheld(
                self._settings(csrf, _withheld(changed[:mid], received, release, closed, changed[mid:])),
                armed, received, release, closed)
        self._refusal(resp, 400, "request_body_timeout")
        self._single_scoped_arm(arms, SCOPED_BODY_DEADLINE_SECONDS)
        self.assertEqual(await self._page_json(), before)
    async def test_slow_and_drip_public_sessions_timeout_without_session(self):
        for name in ("slow", "drip"):
            with self.subTest(name=name):
                received, release, closed, armed = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
                progressed = []
                chunks = (b"{", b" ", b" ", b" ")
                if name == "slow":
                    body = _withheld(b"{", received, release, closed, b"}")
                else:
                    body = _dripping(chunks, progressed, received, release, closed, b"}")
                with _scoped_attr("JSON_BODY_TIMEOUT_SECONDS", SCOPED_BODY_DEADLINE_SECONDS), _observe_timeout_at(armed) as arms:
                    resp = await self._finish_withheld(self._post_session(body), armed, received, release, closed)
                self._refusal(resp, 400, "request_body_timeout")
                _, deadline = self._single_scoped_arm(arms, SCOPED_BODY_DEADLINE_SECONDS)
                if name == "drip":
                    # Genuine chunks flowed before the one original
                    # deadline (explicit clock allowance), yet the
                    # withheld completion still timed out: total, not
                    # inactivity. No minimum firing instant is asserted.
                    self.assertEqual(len(progressed), len(chunks))
                    self.assertLess(progressed[-1], deadline - CLOCK_ALLOWANCE_SECONDS)
        await self._new_session()
    async def test_slow_upload_times_out_then_timely_upload_succeeds(self):
        csrf = await self._session_settings()
        received, release, closed, armed = asyncio.Event(), asyncio.Event(), asyncio.Event(), asyncio.Event()
        with _scoped_attr("UPLOAD_BODY_TIMEOUT_SECONDS", SCOPED_BODY_DEADLINE_SECONDS), _observe_timeout_at(armed) as arms:
            resp = await self._finish_withheld(
                self._upload(csrf, _withheld(_PNG[:16], received, release, closed, _PNG[16:])),
                armed, received, release, closed)
        self._refusal(resp, 400, "request_body_timeout")
        self._single_scoped_arm(arms, SCOPED_BODY_DEADLINE_SECONDS)
        page = await self.client.get("/api/session/page")
        self.assertEqual(page.status_code, 404)
        resp = await self._upload(csrf, _PNG)
        self.assertEqual(resp.status_code, 201); self.assertEqual(resp.json()["grade"], PLAIN["grade"])
    async def test_two_held_public_readers_make_third_capacity_refusal(self):
        # The scoped 2s value only keeps both readers occupied; it
        # is not a measured deadline. Every 1s wait below bounds
        # event readiness only, never production throughput.
        with _scoped_attr("JSON_BODY_TIMEOUT_SECONDS", OCCUPANCY_HOLD_SECONDS):
            started = (asyncio.Event(), asyncio.Event()); release = asyncio.Event()
            tasks = [asyncio.create_task(self._post_session(_held(event, release))) for event in started]
            try:
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), timeout=READY_BOUND_SECONDS)
                third = await asyncio.wait_for(self._post_session(b"{}"), timeout=READY_BOUND_SECONDS)
                release.set()
                held = await asyncio.wait_for(asyncio.gather(*tasks), timeout=READY_BOUND_SECONDS)
            finally:
                release.set()
                for task in tasks:
                    if not task.done(): task.cancel()
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=READY_BOUND_SECONDS)
        self._refusal(third, 429, "body_reader_capacity_exceeded")
        for resp in held: self._created(resp)
        await self._new_session()
    async def test_cancelled_public_stream_publishes_nothing_and_frees_reader(self):
        # The scoped 2s value only keeps the reader held until the
        # test cancels it; it is not a measured deadline. Every 1s
        # wait below bounds event readiness only.
        with _scoped_attr("JSON_BODY_TIMEOUT_SECONDS", OCCUPANCY_HOLD_SECONDS):
            started = asyncio.Event(); release = asyncio.Event()
            task = asyncio.create_task(self._post_session(_held(started, release)))
            try:
                await asyncio.wait_for(started.wait(), timeout=READY_BOUND_SECONDS)
                task.cancel()
                result = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=READY_BOUND_SECONDS)
            finally:
                release.set()
                if not task.done(): task.cancel()
                await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), timeout=READY_BOUND_SECONDS)
        self.assertEqual(len(result), 1); self.assertIsInstance(result[0], asyncio.CancelledError)
        self.assertTrue(task.cancelled())
        await self._new_session()
    async def test_malformed_and_oversize_session_bodies_keep_statuses(self):
        cases = ((b"{", 400), (b"x" * (http_app.MAX_SESSION_JSON_BYTES + 1), 413))
        for body, status in cases:
            with self.subTest(status=status):
                resp = await self._post_session(body)
                self.assertEqual(resp.status_code, status); self.assertNotIn("set-cookie", resp.headers)
        await self._new_session()
