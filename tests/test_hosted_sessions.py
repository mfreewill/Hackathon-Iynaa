"""Bounded hosted-session turnover tests (test-only).

Real FastAPI/TestClient app, tiny synthetic images and one tiny
in-process synthetic source provider. No live model, no external
network. The hosted interface under test is the proposed internal
``create_app(..., secure_cookie=False, session_idle_seconds=None)``
plus the module-local ``http_app._session_now()`` session clock.
Tests patch only that clock for lease time; real ``time.monotonic``
operation deadlines (``ProviderCallContext`` included) stay real.

If the factory parameters or ``_session_now`` are absent, every
hosted test fails first with an explicit missing-feature message
before invoking ``create_app`` with the new arguments. An import or
signature ``TypeError`` is never the behavioural RED.
"""
import asyncio
import contextlib
import copy
import hashlib
import inspect
import io
import json
import math
import pathlib
import secrets
import sys
import threading
import time
import unittest
import urllib.parse
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from teacher_demo import http_app as http_app_module  # noqa: E402
from teacher_demo.http_app import create_app  # noqa: E402
from teacher_demo.http_teacher_publication import (  # noqa: E402
    accounted_total,
)
from teacher_demo.meta_provider import ProviderCallContext  # noqa: E402

LOCAL_BASE = "http://127.0.0.1:8000"
HOSTED_BASE = "https://hosted.example:8443"
HOSTED_HOSTS = ("hosted.example:8443",)
LEASE = 60.0
GRADE = "Grade 5"
OBJECTIVE = "Read the page"
RIGHTS_REF = "synthetic-fixture-rights-note"
RAW_TEXT = "Water flows in the river."
MODEL_NAME = "synthetic-test-provider (not Meta)"
SETTINGS = {
    "grade": GRADE, "objective": OBJECTIVE, "rights_ref": RIGHTS_REF}


class _FakeSessionClock:
    """Deterministic session-policy clock; advances only explicitly."""

    def __init__(self, start=1000.0):
        self.now = float(start)
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            return self.now

    def advance(self, seconds):
        with self._lock:
            self.now += float(seconds)
            return self.now


def _missing_hosted_interface():
    params = inspect.signature(create_app).parameters
    missing = [
        name for name in ("secure_cookie", "session_idle_seconds")
        if name not in params]
    if not callable(getattr(http_app_module, "_session_now", None)):
        missing.append("_session_now")
    return missing


def _require_hosted_interface():
    missing = _missing_hosted_interface()
    if missing:
        raise AssertionError(
            "missing hosted interface (RED): "
            + ", ".join(missing)
            + "; expected create_app(secure_cookie=False, "
            "session_idle_seconds=None) and http_app._session_now()")


def synthetic_png(rgb=(200, 30, 40)):
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (*rgb, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _source_result(text=RAW_TEXT, response_id="synthetic-source-1"):
    return {
        "raw": {
            "sections": [{
                "section_id": "synthetic-section-1",
                "kind": "body", "text": text,
                "review_reasons": ["synthetic-flag"]}],
            "page_continues": False, "review_notes": [],
        },
        "model_name": MODEL_NAME,
        "response_id": response_id,
        "outcome": "ok",
    }


class _HeldSourceProvider:
    """Tiny synthetic source provider with per-call hold gates.

    One-positional-argument callable, exactly like the accepted
    test providers. Blocked calls wait only on explicit test
    releases (bounded), never on lease time.
    """

    def __init__(self, *, block_calls=()):
        self.block_calls = set(block_calls)
        self.requests = []
        self.results = {}
        self.on_result = None
        self._lock = threading.Lock()
        self._calls = 0
        self.entered = [threading.Event() for _ in range(6)]
        self.release = [threading.Event() for _ in range(6)]
        self.returned = [threading.Event() for _ in range(6)]
        # Per-call ceiling-hit witness: set only when a blocked
        # call's finite hold wait times out unreleased.
        self.expired = [threading.Event() for _ in range(6)]
        self.ceiling_hit = self.expired

    def release_all(self):
        for event in self.release:
            event.set()

    def hold_active(self, index=0):
        """True while call index entered, unreleased, unreturned."""
        return (self.entered[index].is_set()
                and not self.release[index].is_set()
                and not self.returned[index].is_set())

    def __call__(self, request):
        with self._lock:
            index = self._calls
            self._calls += 1
            self.requests.append(copy.deepcopy(dict(request)))
        if index < len(self.entered):
            self.entered[index].set()
        if index in self.block_calls:
            if not self.release[index].wait(timeout=10):
                self.expired[index].set()
                raise AssertionError(
                    "test instrumentation failure: held provider "
                    f"call {index} hit its 10s ceiling unreleased")
        result = _source_result(
            response_id=f"synthetic-source-{index + 1}")
        with self._lock:
            self.results[request["request_id"]] = (
                copy.deepcopy(result))
        if self.on_result is not None:
            self.on_result()
        if index < len(self.returned):
            self.returned[index].set()
        return copy.deepcopy(result)


def _operation_command(source_id):
    return {"request_id": secrets.token_hex(16),
            "source_id": source_id}


def _closure_values(app):
    """Test-only observation of create_app closure authorities."""
    values = {}
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        code = getattr(endpoint, "__code__", None)
        closure = getattr(endpoint, "__closure__", None)
        if code is None or closure is None:
            continue
        for name, cell in zip(code.co_freevars, closure):
            try:
                values.setdefault(name, cell.cell_contents)
            except ValueError:
                continue
    return values


def _cookie_segments(set_cookie):
    return [part.strip().lower()
            for part in set_cookie.split(";") if part.strip()]


class _RawAsgiCall:
    """One genuine ASGI HTTP call driven in an owned thread.

    The full app (middleware included) sees a real ``http`` scope.
    Request-body chunks are delivered only when the test releases
    them, and the inner app task can be cancelled by the test.
    Only resources created here are released/joined here.
    """

    def __init__(self, app, *, base_url, method, path,
                 query_string=b"", headers=None, chunks=(),
                 hold_before_last=False):
        self._app = app
        self._base_url = base_url
        self._method = method
        self._path = path
        self._query_string = query_string
        self._headers = dict(headers or {})
        self._chunks = list(chunks)
        self._hold_before_last = hold_before_last
        self.body_waiting = threading.Event()
        self.release_body = threading.Event()
        if not hold_before_last:
            self.release_body.set()
        self.cancel_acknowledged = threading.Event()
        self.hold_timed_out = threading.Event()
        self.response_complete = threading.Event()
        self._task_ready = threading.Event()
        self._loop = None
        self._task = None
        self._receive_done = None
        self.result = {}
        self._messages = []
        self._receive_index = 0
        self._thread = threading.Thread(
            target=self._run_thread, daemon=True)

    def start(self):
        self._thread.start()

    def cancel(self, testcase):
        testcase.assertTrue(
            self._task_ready.wait(timeout=5),
            "raw ASGI task never started")

        def _cancel():
            # Record first, inside the loop, so the test can
            # witness that the actual task was cancelled.
            self.result["cancel_requested"] = True
            self.cancel_acknowledged.set()
            self._task.cancel()

        self._loop.call_soon_threadsafe(_cancel)
        testcase.assertTrue(
            self.cancel_acknowledged.wait(timeout=5),
            "raw ASGI task cancellation not acknowledged")

    def finish(self, testcase):
        self.release_body.set()
        # Response completion is awaited via the actual app
        # task in _amain; here the owned thread is joined
        # boundedly and any original failure is reported.
        self._thread.join(timeout=10)
        testcase.assertFalse(
            self._thread.is_alive(), "raw ASGI call hung")
        testcase.assertNotIn(
            "error", self.result, self.result.get("error"))
        status = None
        body = b""
        for message in self._messages:
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                body += message.get("body", b"")
        return status, body

    def _run_thread(self):
        try:
            asyncio.run(self._amain())
        except BaseException as exc:  # surfaced by finish()
            self.result.setdefault("error", repr(exc))

    async def _amain(self):
        self._loop = asyncio.get_running_loop()
        self._receive_done = asyncio.Event()
        scope = self._scope()
        self._task = asyncio.create_task(
            self._app(scope, self._receive, self._send))
        self._task_ready.set()
        try:
            await self._task
        except asyncio.CancelledError:
            self.result["cancelled"] = True
        finally:
            self._receive_done.set()

    def _scope(self):
        parsed = urllib.parse.urlparse(self._base_url)
        port = parsed.port or (443 if parsed.scheme == "https"
                                else 80)
        headers = [(b"host", parsed.netloc.encode("latin-1"))]
        for name, value in self._headers.items():
            headers.append((
                name.lower().encode("latin-1"),
                value.encode("latin-1")))
        return {
            "type": "http", "http_version": "1.1",
            "method": self._method, "scheme": parsed.scheme,
            "path": self._path,
            "raw_path": self._path.encode("latin-1"),
            "query_string": self._query_string,
            "root_path": "", "headers": headers,
            "client": ("testclient", 50000),
            "server": (parsed.hostname, port),
        }

    async def _receive(self):
        index = self._receive_index
        self._receive_index += 1
        if index < len(self._chunks):
            if (self._hold_before_last
                    and index == len(self._chunks) - 1
                    and len(self._chunks) > 1):
                self.body_waiting.set()
                released = await asyncio.to_thread(
                    self.release_body.wait, 10)
                if not released:
                    self.hold_timed_out.set()
                    self.result.setdefault(
                        "error",
                        "raw ASGI body hold ceiling expired "
                        "before release")
                    raise RuntimeError(
                        "raw ASGI body hold ceiling expired "
                        "before release")
            return {
                "type": "http.request",
                "body": self._chunks[index],
                "more_body": index < len(self._chunks) - 1,
            }
        # Normal EOF after the final more_body=False chunk:
        # park until the response is fully sent rather than
        # signalling a premature disconnect.
        await self._receive_done.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message):
        self._messages.append(dict(message))
        if (message.get("type") == "http.response.body"
                and not message.get("more_body", False)):
            self.response_complete.set()
            self._receive_done.set()


class HostedSessionsTests(unittest.TestCase):
    # -- shared observation / staging helpers ---------------------
    def setUp(self):
        self._require()

    def _require(self):
        _require_hosted_interface()

    def _sessions(self, app):
        return _closure_values(app)["sessions"]

    def _registry_lock(self, app):
        return _closure_values(app)["registry_lock"]

    def _inflight(self, app):
        return _closure_values(app)["inflight"]

    def _state_for(self, app, csrf):
        return next(state for state in self._sessions(app).values()
                    if state.csrf_token == csrf)

    def _state_ids(self, app):
        return {id(state) for state in self._sessions(app).values()}

    def _accounted(self, app, state):
        with self._registry_lock(app):
            return accounted_total(
                state.graph_cost, dict(state.worker_reservations),
                dict(state.terminal_reserves))

    def _total_accounted(self, app):
        return sum(self._accounted(app, state)
                   for state in list(self._sessions(app).values()))

    def _wait_until(self, predicate, message, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail(message)

    def _wait_worker_drained(self, app, state, op_key):
        inflight = self._inflight(app)

        def drained():
            with self._registry_lock(app):
                return (op_key not in state.worker_reservations
                        and inflight["provider"] == 0)

        self._wait_until(
            drained, "provider worker did not drain after return")

    def _hosted_app(self, *, provider=None, idle=LEASE,
                    secure=False, https=False):
        self._require()
        if https:
            return create_app(
                allowed_hosts=HOSTED_HOSTS,
                allowed_origin=HOSTED_BASE, provider=provider,
                secure_cookie=secure, session_idle_seconds=idle)
        return create_app(
            provider=provider, secure_cookie=secure,
            session_idle_seconds=idle)

    def _open_session(self, stack, app, base_url=LOCAL_BASE, *,
                      settings=False, start=False, image=None):
        client = stack.enter_context(
            TestClient(app, base_url=base_url))
        created = client.post(
            "/api/sessions", json={},
            headers={"Origin": base_url})
        self.assertEqual(created.status_code, 201, created.text)
        csrf = created.json()["csrf_token"]
        cookie = created.headers["set-cookie"].split(";", 1)[0]
        handle = SimpleNamespace(
            client=client, csrf=csrf, cookie=cookie,
            created=created,
            headers={"Origin": base_url, "X-CSRF-Token": csrf},
            page=None, state=self._state_for(app, csrf))
        if settings or start:
            self._put_settings(handle)
        if start:
            started = client.post(
                "/api/session/start", params={"page_index": 0},
                content=image if image is not None else synthetic_png(),
                headers={**handle.headers,
                         "Content-Type": "application/octet-stream"})
            self.assertEqual(started.status_code, 201, started.text)
            handle.page = started.json()
        return handle

    def _put_settings(self, handle, *, headers=None):
        response = handle.client.put(
            "/api/session/settings", json=dict(SETTINGS),
            headers=handle.headers if headers is None else headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def _renew(self, handles):
        for handle in handles:
            self._put_settings(handle)

    def _snapshot(self, handle):
        response = handle.client.get("/api/session/snapshot")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _request_from(self, snapshot, request_id):
        return next(item for item in snapshot["requests"]
                    if item["request_id"] == request_id)

    def _attempt_session(self, app, base_url=LOCAL_BASE):
        with TestClient(app, base_url=base_url) as client:
            return client.post(
                "/api/sessions", json={},
                headers={"Origin": base_url})

    def _assert_refused_limit(self, response):
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(
            response.json(), {"detail": "session limit reached"})

    def _assert_session_required(self, response):
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(
            response.json(), {"detail": "session required"})
        set_cookie = response.headers.get("set-cookie", "")
        self.assertNotIn("teacher_session=", set_cookie)

    def _stale_get(self, app, handle, path, base_url=LOCAL_BASE):
        with TestClient(app, base_url=base_url) as client:
            return client.get(
                path, headers={"Cookie": handle.cookie})

    def _fill_eight(self, stack, app, victim, keeper_count=7,
                    base_url=LOCAL_BASE):
        keepers = [
            self._open_session(
                stack, app, base_url, settings=True)
            for _ in range(keeper_count)]
        self.assertEqual(len(self._sessions(app)), 8)
        return keepers

    def _reclaim_victim(self, app, clock, victim, keepers,
                        base_url=LOCAL_BASE):
        """Opposite control: once truly idle, victim is reclaimed."""
        keeper_ids = {id(handle.state) for handle in keepers}
        clock.advance(LEASE + 30)
        self._renew(keepers)
        admitted = self._attempt_session(app, base_url)
        self.assertEqual(admitted.status_code, 201, admitted.text)
        self.assertEqual(len(self._sessions(app)), 8)
        self.assertNotIn(id(victim.state), self._state_ids(app))
        self.assertTrue(
            keeper_ids <= self._state_ids(app),
            "reclaim evicted a keeper instead of the idle victim")
        self._assert_session_required(
            self._stale_get(app, victim, "/api/session/page",
                            base_url))
        return admitted

    def _post_async(self, app, path, headers, *, body=None,
                    content=None, base_url=LOCAL_BASE):
        result = {}
        payload = {} if body is None else body

        def run():
            try:
                with TestClient(app, base_url=base_url) as client:
                    if content is None:
                        result["response"] = client.post(
                            path, json=payload, headers=headers)
                    else:
                        result["response"] = client.post(
                            path, params={"page_index": 0},
                            content=content, headers=headers)
            except BaseException as exc:
                result["error"] = repr(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, result

    def _get_async(self, app, path, headers, base_url=LOCAL_BASE):
        result = {}

        def run():
            try:
                with TestClient(app, base_url=base_url) as client:
                    result["response"] = client.get(
                        path, headers=headers)
            except BaseException as exc:
                result["error"] = repr(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, result

    def _finish_async(self, thread, result):
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive(), "async HTTP call hung")
        self.assertNotIn("error", result, result.get("error"))
        return result["response"]

    def _attempt_async(self, app, base_url=LOCAL_BASE):
        result = {}

        def run():
            try:
                result["response"] = self._attempt_session(
                    app, base_url)
            except BaseException as exc:
                result["error"] = repr(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, result

    def _assert_ninth_refused_while_held(self, app, release_holds,
                                         base_url=LOCAL_BASE,
                                         active_hold=None):
        """Ninth admission must settle with exact 503 while held.

        A reply slower than the observation window is not by
        itself a lock-order failure. If the attempt is still
        pending, the pending branch is recorded, the
        caller-supplied ``active_hold`` witness (when given)
        must still show the victim actively held/preserved,
        the owned hold is then released and the attempt joined
        boundedly. The actual final response is asserted exact
        503 in every branch (settlement renews); there is no
        unconditional pass.
        """
        if active_hold is not None:
            self.assertTrue(
                active_hold(),
                "hold not active before ninth admission attempt")
        thread, result = self._attempt_async(app, base_url)
        try:
            thread.join(timeout=5)
            if thread.is_alive():
                result["admission_pending"] = True
                if active_hold is not None:
                    self.assertTrue(
                        active_hold(),
                        "hold not active while ninth admission "
                        "was pending")
                release_holds()
                thread.join(timeout=10)
                self.assertFalse(
                    thread.is_alive(),
                    "ninth admission never settled after hold "
                    "release")
            elif active_hold is not None:
                self.assertTrue(
                    active_hold(),
                    "hold not active after ninth admission "
                    "settled")
            self.assertNotIn("error", result, result.get("error"))
            self._assert_refused_limit(result["response"])
        finally:
            if thread.is_alive():
                release_holds()
                thread.join(timeout=10)

    # -- proposed interface: explicit first RED -------------------
    def test_missing_hosted_interface_is_first_red(self):
        missing = _missing_hosted_interface()
        self.assertEqual(
            missing, [],
            "missing hosted interface (RED): "
            + ", ".join(missing)
            + "; expected create_app(secure_cookie=False, "
            "session_idle_seconds=None) and http_app._session_now()")
        params = inspect.signature(create_app).parameters
        self.assertIs(params["secure_cookie"].default, False)
        self.assertIsNone(
            params["session_idle_seconds"].default)
        # Local defaults stay exactly as before.
        self.assertEqual(
            params["allowed_hosts"].default, ("127.0.0.1:8000",))
        self.assertEqual(
            params["allowed_origin"].default,
            "http://127.0.0.1:8000")
        self.assertIsNone(params["provider"].default)
        self.assertTrue(
            callable(http_app_module._session_now))
        value = http_app_module._session_now()
        self.assertIsInstance(value, (int, float))
        self.assertNotIsInstance(value, bool)
        self.assertTrue(math.isfinite(value))

    def test_factory_rejects_bad_secure_cookie_before_construction(
            self):
        self._require()
        real_fastapi = http_app_module.FastAPI
        calls = {"count": 0}

        def counting_fastapi(*args, **kwargs):
            calls["count"] += 1
            return real_fastapi(*args, **kwargs)

        bad_values = (1, 0, "true", "false", None, 1.0, object())
        with mock.patch.object(
                http_app_module, "FastAPI", counting_fastapi):
            for bad in bad_values:
                with self.subTest(secure_cookie=repr(bad)):
                    calls["count"] = 0
                    with self.assertRaises((TypeError, ValueError)):
                        create_app(secure_cookie=bad)
                    self.assertEqual(
                        calls["count"], 0,
                        "rejection must precede app construction")
            for good in (False, True):
                with self.subTest(secure_cookie=good):
                    calls["count"] = 0
                    app = create_app(secure_cookie=good)
                    self.assertIsNotNone(app)
                    self.assertEqual(calls["count"], 1)

    def test_factory_rejects_bad_idle_before_construction(self):
        self._require()
        real_fastapi = http_app_module.FastAPI
        calls = {"count": 0}

        def counting_fastapi(*args, **kwargs):
            calls["count"] += 1
            return real_fastapi(*args, **kwargs)

        bad_values = (
            True, False, 0, -1, 0.0, -0.5, float("nan"),
            float("inf"), float("-inf"), "1800", object(), [])
        with mock.patch.object(
                http_app_module, "FastAPI", counting_fastapi):
            for bad in bad_values:
                with self.subTest(session_idle_seconds=repr(bad)):
                    calls["count"] = 0
                    with self.assertRaises((TypeError, ValueError)):
                        create_app(session_idle_seconds=bad)
                    self.assertEqual(
                        calls["count"], 0,
                        "rejection must precede app construction")
            for good in (None, 1800, 1800.0, 60, 90.5):
                with self.subTest(session_idle_seconds=good):
                    calls["count"] = 0
                    app = create_app(session_idle_seconds=good)
                    self.assertIsNotNone(app)
                    self.assertEqual(calls["count"], 1)

    def test_session_clock_patch_is_scoped_to_session_policy(self):
        self._require()
        real_monotonic = time.monotonic
        first = http_app_module._session_now()
        second = http_app_module._session_now()
        self.assertGreaterEqual(second, first)
        self.assertAlmostEqual(first, real_monotonic(), delta=5.0)
        clock = _FakeSessionClock(start=5_000_000_000_000.0)
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            self.assertIs(time.monotonic, real_monotonic)
            self.assertEqual(
                http_app_module._session_now(), 5_000_000_000_000.0)
            self.assertLess(time.monotonic(), 5_000_000_000_000.0)
            context = ProviderCallContext(30)
            remaining = context.remaining()
            self.assertGreater(remaining, 0)
            self.assertLessEqual(remaining, 30)
            clock.advance(10 ** 9)
            later = ProviderCallContext(30)
            self.assertGreater(later.remaining(), 0)
        self.assertIs(time.monotonic, real_monotonic)

    # -- cookie flags, canonical Host/Origin --------------------
    def _assert_cookie_flags(self, response, *, secure_expected):
        segments = _cookie_segments(
            response.headers.get("set-cookie", ""))
        self.assertTrue(segments, "missing Set-Cookie header")
        self.assertTrue(
            segments[0].startswith("teacher_session="), segments)
        self.assertIn("httponly", segments)
        self.assertIn("samesite=strict", segments)
        self.assertIn("path=/api", segments)
        self.assertFalse(
            any(segment.startswith("domain=")
                for segment in segments),
            f"cookie must stay host-only: {segments}")
        self.assertEqual("secure" in segments, secure_expected,
                         segments)

    def test_cookie_secure_exactly_when_configured(self):
        self._require()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            local_app = create_app()
            with TestClient(
                    local_app, base_url=LOCAL_BASE) as client:
                created = client.post(
                    "/api/sessions", json={},
                    headers={"Origin": LOCAL_BASE})
                self.assertEqual(
                    created.status_code, 201, created.text)
                self._assert_cookie_flags(
                    created, secure_expected=False)
            for secure in (True, False):
                with self.subTest(secure_cookie=secure):
                    app = self._hosted_app(
                        secure=secure, https=True)
                    with TestClient(
                            app, base_url=HOSTED_BASE) as client:
                        created = client.post(
                            "/api/sessions", json={},
                            headers={"Origin": HOSTED_BASE})
                        self.assertEqual(
                            created.status_code, 201, created.text)
                        self._assert_cookie_flags(
                            created, secure_expected=secure)

    def test_https_host_origin_and_csrf_exact_match(self):
        self._require()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app(secure=True, https=True)
            with contextlib.ExitStack() as stack:
                handle = self._open_session(
                    stack, app, HOSTED_BASE, settings=True)
                self.assertEqual(len(self._sessions(app)), 1)
                with TestClient(
                        app, base_url=HOSTED_BASE) as client:
                    wrong_origin = client.post(
                        "/api/sessions", json={},
                        headers={
                            "Origin": "https://evil.example:8443"})
                    self.assertEqual(
                        wrong_origin.status_code, 403,
                        wrong_origin.text)
                    self.assertEqual(
                        wrong_origin.json(),
                        {"detail": "forbidden origin"})
                    self.assertEqual(len(self._sessions(app)), 1)
                with TestClient(
                        app,
                        base_url="https://evil.example:8443",
                        ) as client:
                    wrong_host = client.post(
                        "/api/sessions", json={},
                        headers={"Origin": HOSTED_BASE})
                    self.assertEqual(
                        wrong_host.status_code, 400, wrong_host.text)
                    self.assertEqual(
                        wrong_host.json(),
                        {"detail": "invalid host"})
                    self.assertEqual(len(self._sessions(app)), 1)
                pending_before = handle.state.pending
                wrong_csrf = handle.client.put(
                    "/api/session/settings", json=dict(SETTINGS),
                    headers={**handle.headers,
                             "X-CSRF-Token": "wrong-token"})
                self.assertEqual(
                    wrong_csrf.status_code, 403, wrong_csrf.text)
                self.assertEqual(
                    wrong_csrf.json(),
                    {"detail": "forbidden request"})
                self.assertIs(handle.state.pending, pending_before)
                # Healthy opposite: canonical values still succeed.
                self._put_settings(handle)

    # -- healthy lease behaviour --------------------------------
    def test_eight_fresh_sessions_refuse_ninth_exactly(self):
        self._require()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                handles = [
                    self._open_session(stack, app, settings=True)
                    for _ in range(8)]
                before_ids = self._state_ids(app)
                self.assertEqual(len(before_ids), 8)
                self.assertEqual(
                    len({handle.csrf for handle in handles}), 8)
                self._assert_refused_limit(
                    self._attempt_session(app))
                self.assertEqual(self._state_ids(app), before_ids)
                # Healthy opposite: every refused-out session
                # itself was admitted and still works.
                for handle in handles:
                    self._put_settings(handle)

    def test_idle_victim_reclaimed_only_at_ninth_admission(self):
        self._require()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                victim = self._open_session(
                    stack, app, settings=True)
                keepers = self._fill_eight(stack, app, victim)
                clock.advance(LEASE + 30)
                self._renew(keepers)
                # No background eviction: expiry alone and other
                # sessions' activity leave the registry untouched.
                self.assertIn(
                    id(victim.state), self._state_ids(app))
                self.assertEqual(len(self._sessions(app)), 8)
                admitted = self._attempt_session(app)
                self.assertEqual(
                    admitted.status_code, 201, admitted.text)
                self.assertNotIn(
                    id(victim.state), self._state_ids(app))
                for keeper in keepers:
                    self.assertIn(
                        id(keeper.state), self._state_ids(app))
                self.assertEqual(len(self._sessions(app)), 8)
                self._assert_session_required(
                    self._stale_get(
                        app, victim, "/api/session/page"))
                self._assert_session_required(
                    self._stale_get(
                        app, victim, "/api/session/snapshot"))
                # Stale reads create nothing.
                self.assertEqual(len(self._sessions(app)), 8)
                with TestClient(
                        app, base_url=LOCAL_BASE) as client:
                    anonymous = client.get("/api/session/page")
                self._assert_session_required(anonymous)
                self.assertEqual(len(self._sessions(app)), 8)

    def test_activity_renews_only_its_own_session(self):
        self._require()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                victim = self._open_session(
                    stack, app, start=True,
                    image=synthetic_png((200, 30, 40)))
                keeper = self._open_session(
                    stack, app, start=True,
                    image=synthetic_png((20, 80, 160)))
                fillers = [
                    self._open_session(stack, app, settings=True)
                    for _ in range(6)]
                self.assertEqual(len(self._sessions(app)), 8)
                victim_workspace = victim.state.workspace
                victim_generation = victim.state.source_generation
                victim_epoch = victim.state.publication_epoch
                clock.advance(30)
                fetched = keeper.client.get("/api/session/page")
                self.assertEqual(
                    fetched.status_code, 200, fetched.text)
                self.assertEqual(
                    fetched.json()["source_id"],
                    keeper.page["source_id"])
                self._renew(fillers)
                # Keeper/filler HTTP touches neither victim
                # content nor victim currentness counters.
                self.assertIs(
                    victim.state.workspace, victim_workspace)
                self.assertEqual(
                    victim.state.source_generation,
                    victim_generation)
                self.assertEqual(
                    victim.state.publication_epoch, victim_epoch)
                clock.advance(35)
                admitted = self._attempt_session(app)
                self.assertEqual(
                    admitted.status_code, 201, admitted.text)
                self.assertNotIn(
                    id(victim.state), self._state_ids(app))
                self.assertIn(
                    id(keeper.state), self._state_ids(app))
                still = keeper.client.get("/api/session/page")
                self.assertEqual(still.status_code, 200, still.text)
                self.assertEqual(
                    still.json()["source_id"],
                    keeper.page["source_id"])

    def test_invalid_command_renews_session_without_content_change(self):
        self._require()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                victim = self._open_session(
                    stack, app, start=True)
                keepers = self._fill_eight(stack, app, victim)
                pending_before = victim.state.pending
                workspace_before = victim.state.workspace
                publication_epoch_before = (
                    victim.state.publication_epoch)
                source_generation_before = (
                    victim.state.source_generation)
                accounted_before = self._accounted(
                    app, victim.state)
                state_ids_before = self._state_ids(app)
                snapshot_before = self._snapshot(victim)
                clock.advance(30)
                # Valid cookie/origin/CSRF, malformed settings
                # object: exact 400, no content change, but the
                # authenticated HTTP activity renews the victim.
                malformed = dict(SETTINGS)
                malformed[next(iter(malformed))] = ["malformed"]
                malformed["__malformed__"] = True
                invalid = victim.client.put(
                    "/api/session/settings", json=malformed,
                    headers=victim.headers)
                self.assertEqual(
                    invalid.status_code, 400, invalid.text)
                self.assertEqual(
                    invalid.json(),
                    {"detail": "invalid request"})
                self.assertIs(victim.state.pending, pending_before)
                self.assertIs(
                    victim.state.workspace, workspace_before)
                self.assertEqual(
                    victim.state.publication_epoch,
                    publication_epoch_before)
                self.assertEqual(
                    victim.state.source_generation,
                    source_generation_before)
                self.assertEqual(
                    self._accounted(app, victim.state),
                    accounted_before)
                self.assertEqual(
                    self._state_ids(app), state_ids_before)
                self._renew(keepers)
                clock.advance(35)
                # Renewed at +30, the victim is not idle at +65:
                # the ninth admission is still refused exact 503.
                self._assert_refused_limit(
                    self._attempt_session(app))
                self.assertIn(
                    id(victim.state), self._state_ids(app))
                snapshot_after = self._snapshot(victim)
                # Only lease metadata changed; source output is
                # unchanged, so whole snapshots must match.
                self.assertEqual(snapshot_after, snapshot_before)
                # A later full idle period still reclaims it.
                self._reclaim_victim(app, clock, victim, keepers)

    def test_local_mode_never_expires(self):
        self._require()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = create_app()
            with contextlib.ExitStack() as stack:
                handles = [
                    self._open_session(stack, app, settings=True)
                    for _ in range(8)]
                before_ids = self._state_ids(app)
                clock.advance(10_000_000)
                self._assert_refused_limit(
                    self._attempt_session(app))
                self.assertEqual(self._state_ids(app), before_ids)
                self._put_settings(handles[0])
            # Secure cookie alone (idle still None) expires
            # nothing either: the two factory knobs are orthogonal.
            hosted = self._hosted_app(
                idle=None, secure=True, https=True)
            with contextlib.ExitStack() as stack:
                handle = self._open_session(
                    stack, hosted, HOSTED_BASE, settings=True)
                clock.advance(10_000_000)
                self._put_settings(handle)
                self.assertIn(
                    id(handle.state), self._state_ids(hosted))

    def test_new_session_inherits_no_reclaimed_content(self):
        self._require()
        provider = _HeldSourceProvider()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app(provider=provider)
            with contextlib.ExitStack() as stack:
                victim = self._open_session(stack, app, start=True)
                analyzed = victim.client.post(
                    "/api/session/source-analysis",
                    json=_operation_command(
                        victim.page["source_id"]),
                    headers=victim.headers)
                self.assertEqual(
                    analyzed.status_code, 201, analyzed.text)
                self.assertEqual(
                    analyzed.json()["snapshot"]["regions"][0]
                    ["raw_text"], RAW_TEXT)
                keepers = self._fill_eight(stack, app, victim)
                clock.advance(LEASE + 30)
                self._renew(keepers)
                ninth_client = stack.enter_context(
                    TestClient(app, base_url=LOCAL_BASE))
                created = ninth_client.post(
                    "/api/sessions", json={},
                    headers={"Origin": LOCAL_BASE})
                self.assertEqual(
                    created.status_code, 201, created.text)
                ninth_csrf = created.json()["csrf_token"]
                self.assertNotEqual(ninth_csrf, victim.csrf)
                ninth_state = self._state_for(app, ninth_csrf)
                self.assertIsNone(ninth_state.workspace)
                self.assertIsNone(ninth_state.pending)
                self.assertEqual(
                    dict(ninth_state.provider_results), {})
                page = ninth_client.get("/api/session/page")
                self.assertEqual(page.status_code, 404, page.text)
                self.assertEqual(
                    page.json(), {"detail": "page not found"})
                snapshot = ninth_client.get(
                    "/api/session/snapshot")
                self.assertEqual(
                    snapshot.status_code, 404, snapshot.text)

    def test_metadata_read_renewal_preserves_epochs(self):
        self._require()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                victim = self._open_session(stack, app, start=True)
                keepers = self._fill_eight(stack, app, victim)
                before_snapshot = self._snapshot(victim)
                workspace_before = victim.state.workspace
                pending_before = victim.state.pending
                generation_before = victim.state.source_generation
                epoch_before = victim.state.publication_epoch
                clock.advance(30)
                page = victim.client.get("/api/session/page")
                self.assertEqual(page.status_code, 200, page.text)
                after_snapshot = self._snapshot(victim)
                self.assertEqual(after_snapshot, before_snapshot)
                self.assertIs(
                    victim.state.workspace, workspace_before)
                self.assertIs(victim.state.pending, pending_before)
                self.assertEqual(
                    victim.state.source_generation,
                    generation_before)
                self.assertEqual(
                    victim.state.publication_epoch, epoch_before)
                self._renew(keepers)
                clock.advance(45)
                # Reads renewed: nobody is idle-eligible yet.
                self._assert_refused_limit(
                    self._attempt_session(app))
                self.assertIn(
                    id(victim.state), self._state_ids(app))
                clock.advance(30)
                self._renew(keepers)
                admitted = self._attempt_session(app)
                self.assertEqual(
                    admitted.status_code, 201, admitted.text)
                self.assertNotIn(
                    id(victim.state), self._state_ids(app))

    def test_second_tab_post_never_reuses_cookie_session(self):
        self._require()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with TestClient(app, base_url=LOCAL_BASE) as client:
                first = client.post(
                    "/api/sessions", json={},
                    headers={"Origin": LOCAL_BASE})
                self.assertEqual(first.status_code, 201, first.text)
                first_csrf = first.json()["csrf_token"]
                first_cookie = (
                    first.headers["set-cookie"].split(";", 1)[0])
                first_headers = {
                    "Origin": LOCAL_BASE,
                    "X-CSRF-Token": first_csrf}
                settings = client.put(
                    "/api/session/settings", json=dict(SETTINGS),
                    headers=first_headers)
                self.assertEqual(
                    settings.status_code, 200, settings.text)
                # Same browser jar (existing cookie sent): the
                # second POST must mint a fresh session/token,
                # never hand back the first tab's CSRF session.
                second = client.post(
                    "/api/sessions", json={},
                    headers={"Origin": LOCAL_BASE})
                self.assertEqual(
                    second.status_code, 201, second.text)
                second_csrf = second.json()["csrf_token"]
                second_cookie = (
                    second.headers["set-cookie"].split(";", 1)[0])
                self.assertNotEqual(second_csrf, first_csrf)
                self.assertNotEqual(second_cookie, first_cookie)
                self.assertEqual(len(self._sessions(app)), 2)
                # First tab now holds a stale token for the
                # overwritten cookie: the intentional refusal.
                stale_tab = client.put(
                    "/api/session/settings", json=dict(SETTINGS),
                    headers=first_headers)
                self.assertEqual(
                    stale_tab.status_code, 403, stale_tab.text)
                self.assertEqual(
                    stale_tab.json(),
                    {"detail": "forbidden request"})
                # Neither session was destroyed or shared: each
                # exact cookie+token pair still works on its own.
                second_headers = {
                    "Origin": LOCAL_BASE,
                    "X-CSRF-Token": second_csrf}
                ok_second = client.put(
                    "/api/session/settings", json=dict(SETTINGS),
                    headers=second_headers)
                self.assertEqual(
                    ok_second.status_code, 200, ok_second.text)
            with TestClient(app, base_url=LOCAL_BASE) as client:
                ok_first = client.put(
                    "/api/session/settings", json=dict(SETTINGS),
                    headers={**first_headers,
                             "Cookie": first_cookie})
                self.assertEqual(
                    ok_first.status_code, 200, ok_first.text)
            self.assertEqual(len(self._sessions(app)), 2)

    def test_logical_bytes_follow_actual_registry(self):
        provider = _HeldSourceProvider()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app(provider=provider)
            with contextlib.ExitStack() as stack:
                victim = self._open_session(stack, app, start=True)
                analyzed = victim.client.post(
                    "/api/session/source-analysis",
                    json=_operation_command(
                        victim.page["source_id"]),
                    headers=victim.headers)
                self.assertEqual(
                    analyzed.status_code, 201, analyzed.text)
                keepers = self._fill_eight(stack, app, victim)
                clock.advance(LEASE + 30)
                self._renew(keepers)
                victim_before = self._accounted(app, victim.state)
                keeper_before = {
                    id(handle.state): self._accounted(
                        app, handle.state)
                    for handle in keepers}
                total_before = self._total_accounted(app)
                self.assertEqual(
                    total_before,
                    victim_before + sum(keeper_before.values()))
                self.assertGreater(
                    victim_before,
                    max(keeper_before.values()),
                    "started+analysed victim must carry its "
                    "retained graph charge")
                admitted = self._attempt_session(app)
                self.assertEqual(
                    admitted.status_code, 201, admitted.text)
                ninth_state = self._state_for(
                    app, admitted.json()["csrf_token"])
                ninth_accounted = self._accounted(
                    app, ninth_state)
                self.assertGreater(ninth_accounted, 0)
                self.assertEqual(
                    dict(ninth_state.worker_reservations), {})
                self.assertEqual(
                    self._total_accounted(app),
                    total_before - victim_before + ninth_accounted)
                for handle in keepers:
                    self.assertEqual(
                        self._accounted(app, handle.state),
                        keeper_before[id(handle.state)],
                        "reclaim must not re-charge survivors")

    # -- held provider: healthy release under the lease ---------
    def test_held_provider_releases_with_correct_result(self):
        provider = _HeldSourceProvider(block_calls={0})
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app(provider=provider)
            with contextlib.ExitStack() as stack:
                victim = self._open_session(stack, app, start=True)
                command = _operation_command(
                    victim.page["source_id"])
                request_id = command["request_id"]
                generation = victim.state.source_generation
                op_key = (generation, request_id)
                headers = {**victim.headers,
                           "Cookie": victim.cookie}
                thread = None
                result = {}
                response = None
                body_ok = False

                def _release_and_join():
                    provider.release_all()
                    if thread is None:
                        return None
                    return self._finish_async(thread, result)

                try:
                    thread, result = self._post_async(
                        app, "/api/session/source-analysis", headers,
                        body=command)
                    self.assertTrue(
                        provider.entered[0].wait(timeout=5),
                        "provider boundary never reached")
                    self.assertTrue(
                        provider.hold_active(0),
                        "provider hold not active before fake "
                        "clock shift")
                    self.assertFalse(
                        provider.expired[0].is_set(),
                        "provider hold ceiling fired before fake "
                        "clock shift")
                    working = self._request_from(
                        self._snapshot(victim), request_id)
                    self.assertEqual(working["state"], "working")
                    # Fake lease time races ahead; the real
                    # operation deadline must not move with it.
                    clock.advance(3600)
                    self.assertTrue(
                        provider.hold_active(0),
                        "provider hold not active after fake "
                        "clock shift")
                    self.assertFalse(
                        provider.expired[0].is_set(),
                        "provider hold ceiling fired after fake "
                        "clock shift")
                    body_ok = True
                finally:
                    # Release and join our own HTTP thread while
                    # the clock mock and clients are still active.
                    # On failure, cleanup must not mask the first
                    # assertion; keep the response only when the
                    # body completed without error.
                    if body_ok:
                        response = _release_and_join()
                    else:
                        try:
                            _release_and_join()
                        except BaseException:
                            pass
                self.assertFalse(
                    provider.expired[0].is_set(),
                    "provider hold ceiling fired")
                self.assertEqual(
                    response.status_code, 201, response.text)
                self.assertEqual(
                    response.json()["request_id"], request_id)
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(
                    provider.requests[0]["kind"],
                    "source_analysis")
                self.assertEqual(
                    provider.requests[0]["source_id"],
                    victim.page["source_id"])
                snapshot = response.json()["snapshot"]
                self.assertEqual(
                    snapshot["regions"][0]["raw_text"], RAW_TEXT)
                self.assertEqual(
                    self._request_from(snapshot, request_id)
                    ["outcome"], "ok")
                raw = victim.client.get(
                    "/api/session/requests/"
                    + quote(request_id, safe="") + "/raw")
                self.assertEqual(raw.status_code, 200, raw.text)
                self.assertEqual(
                    raw.json()["provider_result"],
                    provider.results[request_id])
                self._wait_worker_drained(
                    app, victim.state, op_key)

    # -- races: owned work is never evictable -------------------
    def test_long_provider_call_blocks_eviction_then_settles(self):
        provider = _HeldSourceProvider(block_calls={0})
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app(provider=provider)
            with contextlib.ExitStack() as stack:
                victim = self._open_session(stack, app, start=True)
                keepers = self._fill_eight(stack, app, victim)
                command = _operation_command(
                    victim.page["source_id"])
                request_id = command["request_id"]
                generation = victim.state.source_generation
                op_key = (generation, request_id)
                headers = {**victim.headers,
                           "Cookie": victim.cookie}
                thread = None
                result = {}
                response = None
                body_ok = False

                def _release_and_join():
                    provider.release_all()
                    if thread is None:
                        return None
                    return self._finish_async(thread, result)

                try:
                    thread, result = self._post_async(
                        app, "/api/session/source-analysis", headers,
                        body=command)
                    self.assertTrue(
                        provider.entered[0].wait(timeout=5),
                        "provider boundary never reached")
                    self._request_from(
                        self._snapshot(victim), request_id)
                    self.assertIn(
                        op_key, dict(
                            victim.state.worker_reservations))
                    held_accounted = self._accounted(
                        app, victim.state)
                    self.assertGreater(
                        held_accounted, victim.state.graph_cost,
                        "callable-bound charge must stay accounted "
                        "while the worker is held")
                    self.assertTrue(
                        provider.hold_active(0),
                        "provider hold not active before fake "
                        "clock shift")
                    self.assertFalse(
                        provider.expired[0].is_set(),
                        "provider hold ceiling fired before fake "
                        "clock shift")
                    clock.advance(3600)
                    self._renew(keepers)
                    self.assertTrue(
                        provider.hold_active(0),
                        "provider hold not active after fake "
                        "clock shift")
                    self.assertFalse(
                        provider.expired[0].is_set(),
                        "provider hold ceiling fired after fake "
                        "clock shift")
                    self._assert_ninth_refused_while_held(
                        app, provider.release_all,
                        active_hold=lambda: provider.hold_active(0))
                    # The helper may deliberately release the
                    # hold on its pending branch; require only
                    # that the ceiling never fired.
                    self.assertFalse(
                        provider.expired[0].is_set(),
                        "provider hold ceiling fired at ninth "
                        "admission")
                    self.assertIn(
                        id(victim.state), self._state_ids(app))
                    body_ok = True
                finally:
                    # Release and join our own HTTP thread while
                    # the clock mock and clients are still active.
                    # On failure, cleanup must not mask the first
                    # assertion; keep the response only when the
                    # body completed without error.
                    if body_ok:
                        response = _release_and_join()
                    else:
                        try:
                            _release_and_join()
                        except BaseException:
                            pass
                self.assertFalse(
                    provider.expired[0].is_set(),
                    "provider hold ceiling fired")
                # Ordinary release, same correct result, no fake
                # lease timeout of the real operation.
                self.assertEqual(
                    response.status_code, 201, response.text)
                self.assertEqual(
                    response.json()["snapshot"]["regions"][0]
                    ["raw_text"], RAW_TEXT)
                self._wait_worker_drained(
                    app, victim.state, op_key)
                # Settlement renewed at the advanced clock: a
                # further sub-lease advance admits nobody.
                self._renew(keepers)
                clock.advance(LEASE - 15)
                self._assert_refused_limit(
                    self._attempt_session(app))
                self.assertIn(
                    id(victim.state), self._state_ids(app))
                self._reclaim_victim(app, clock, victim, keepers)

    def test_blocked_callable_blocks_eviction_after_close(self):
        # A terminal domain state (cancelled / timed out) or a
        # replaced source is not idleness while the callable
        # itself has not returned.
        for mode in ("cancel", "timeout", "source_replace"):
            with self.subTest(mode=mode):
                self._run_blocked_callable_case(mode)

    def _run_blocked_callable_case(self, mode):
        provider = _HeldSourceProvider(block_calls={0})
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app(provider=provider)
            with contextlib.ExitStack() as stack:
                victim = self._open_session(stack, app, start=True)
                keepers = self._fill_eight(stack, app, victim)
                command = _operation_command(
                    victim.page["source_id"])
                request_id = command["request_id"]
                generation = victim.state.source_generation
                op_key = (generation, request_id)
                headers = {**victim.headers,
                           "Cookie": victim.cookie}
                thread = None
                result = None
                late = None
                body_ok = False

                def _release_and_join():
                    provider.release_all()
                    if thread is None:
                        return None
                    return self._finish_async(thread, result)

                try:
                    if mode == "timeout":
                        with mock.patch.object(
                                http_app_module,
                                "PROVIDER_TIMEOUT_SECONDS", 0.25):
                            timed_out = victim.client.post(
                                "/api/session/source-analysis",
                                json=command, headers=victim.headers)
                        self.assertEqual(
                            timed_out.status_code, 504,
                            timed_out.text)
                    else:
                        thread, result = self._post_async(
                            app, "/api/session/source-analysis",
                            headers, body=command)
                    self.assertTrue(
                        provider.entered[0].wait(timeout=5),
                        "provider boundary never reached")
                    self.assertTrue(
                        provider.hold_active(0),
                        "provider hold not active at provider "
                        "boundary")
                    self.assertFalse(
                        provider.expired[0].is_set(),
                        "provider hold ceiling fired at provider "
                        "boundary")
                    if mode == "cancel":
                        self._request_from(
                            self._snapshot(victim), request_id)
                        cancelled = victim.client.post(
                            "/api/session/requests/"
                            + quote(request_id, safe="") + "/cancel",
                            json={"source_id":
                                  victim.page["source_id"]},
                            headers=victim.headers)
                        self.assertEqual(
                            cancelled.status_code, 200,
                            cancelled.text)
                        closed = self._request_from(
                            self._snapshot(victim), request_id)
                        self.assertEqual(
                            closed["outcome"], "cancelled")
                        self.assertNotEqual(
                            closed["state"], "working")
                    if mode == "timeout":
                        closed = self._request_from(
                            self._snapshot(victim), request_id)
                        self.assertEqual(closed["outcome"], "timeout")
                        self.assertEqual(closed["state"], "terminal")
                    if mode == "source_replace":
                        self._request_from(
                            self._snapshot(victim), request_id)
                        restarted = victim.client.post(
                            "/api/session/start",
                            params={"page_index": 0},
                            content=synthetic_png((30, 90, 150)),
                            headers={**victim.headers,
                                     "Content-Type":
                                     "application/octet-stream"})
                        self.assertEqual(
                            restarted.status_code, 201,
                            restarted.text)
                        fresh = self._snapshot(victim)
                        self.assertEqual(fresh["requests"], [])
                    # Callable still blocked: charge and owner
                    # survive cancel/timeout/replacement alike.
                    # The actual callable has not returned even
                    # though the logical request is terminal.
                    self.assertTrue(
                        provider.hold_active(0),
                        "provider hold not active before fake "
                        "clock shift")
                    self.assertFalse(
                        provider.expired[0].is_set(),
                        "provider hold ceiling fired before fake "
                        "clock shift")
                    self.assertIn(
                        op_key, dict(
                            victim.state.worker_reservations))
                    self.assertEqual(
                        self._inflight(app)["provider"], 1)
                    clock.advance(3600)
                    self._renew(keepers)
                    self.assertTrue(
                        provider.hold_active(0),
                        "provider hold not active after fake "
                        "clock shift")
                    self.assertFalse(
                        provider.expired[0].is_set(),
                        "provider hold ceiling fired after fake "
                        "clock shift")
                    self._assert_ninth_refused_while_held(
                        app, provider.release_all,
                        active_hold=lambda: provider.hold_active(0))
                    # The helper may deliberately release the
                    # hold on its pending branch; require only
                    # that the ceiling never fired.
                    self.assertFalse(
                        provider.expired[0].is_set(),
                        "provider hold ceiling fired at ninth "
                        "admission")
                    self.assertIn(
                        id(victim.state), self._state_ids(app))
                    body_ok = True
                finally:
                    # Release and join our own HTTP thread while
                    # the clock mock and clients are still active.
                    # On failure, cleanup must not mask the first
                    # assertion; keep the late response only when
                    # the body completed without error.
                    if body_ok:
                        late = _release_and_join()
                    else:
                        try:
                            _release_and_join()
                        except BaseException:
                            pass
                self.assertFalse(
                    provider.expired[0].is_set(),
                    "provider hold ceiling fired")
                if thread is not None:
                    self.assertEqual(
                        late.status_code, 409, late.text)
                    expected_code = (
                        "source_changed"
                        if mode == "source_replace"
                        else "request_closed")
                    self.assertEqual(
                        late.json()["detail"]["code"], expected_code)
                self._wait_worker_drained(
                    app, victim.state, op_key)
                if mode != "source_replace":
                    self.assertEqual(
                        self._snapshot(victim)["regions"], [])
                self._reclaim_victim(app, clock, victim, keepers)

    def test_slow_upload_body_blocks_eviction(self):
        # Genuine ASGI path (middleware included): the second
        # body chunk is withheld while fake lease time advances.
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                # Any open client keeps the app lifespan running
                # while the raw call shares the same app.
                victim = self._open_session(
                    stack, app, settings=True)
                keepers = self._fill_eight(stack, app, victim)
                image = synthetic_png()
                raw = _RawAsgiCall(
                    app, base_url=LOCAL_BASE, method="POST",
                    path="/api/session/start",
                    query_string=b"page_index=0",
                    headers={
                        "Origin": LOCAL_BASE,
                        "Cookie": victim.cookie,
                        "X-CSRF-Token": victim.csrf,
                        "Content-Type": "application/octet-stream"},
                    chunks=[image[:10], image[10:]],
                    hold_before_last=True)
                raw.start()

                def assert_body_hold_alive():
                    # Method-local witness only. The provided
                    # _RawAsgiCall interface exposes no expired
                    # flag, so do not invent one: reached
                    # boundary + unreleased body + live owned
                    # thread + no recorded error is the
                    # not-expired evidence available here.
                    # raw.finish() below propagates any hold
                    # timeout failure from the corrected class.
                    self.assertTrue(
                        raw.body_waiting.is_set(),
                        "streamed-body boundary not reached")
                    self.assertFalse(
                        raw.release_body.is_set(),
                        "streamed body released before eviction "
                        "was observed")
                    self.assertTrue(
                        raw._thread.is_alive(),
                        "streamed-body hold expired or finished "
                        "before eviction was observed")
                    self.assertNotIn(
                        "error", raw.result,
                        raw.result.get("error"))
                    return True

                try:
                    self.assertTrue(
                        raw.body_waiting.wait(timeout=5),
                        "streamed-body boundary never reached")
                    assert_body_hold_alive()
                    self.assertEqual(
                        self._inflight(app)["count"], 1)
                    clock.advance(3600)
                    self._renew(keepers)
                    self._assert_ninth_refused_while_held(
                        app, raw.release_body.set,
                        active_hold=lambda: assert_body_hold_alive())
                    # The helper may have released the body, so
                    # the thread may legitimately have finished;
                    # only a recorded error (the expiry signal
                    # this interface exposes) is checkable here.
                    # finish() in finally is authoritative.
                    self.assertNotIn(
                        "error", raw.result,
                        raw.result.get("error"))
                    self.assertIn(
                        id(victim.state), self._state_ids(app))
                finally:
                    # release_body.set() + bounded join inside
                    # the clock scope, even on failure.
                    status, body = raw.finish(self)
                self.assertEqual(status, 201, body)
                metadata = json.loads(body.decode("utf-8"))
                self.assertEqual(
                    metadata["source_sha256"],
                    hashlib.sha256(image).hexdigest())
                victim.page = metadata
                fetched = victim.client.get("/api/session/page")
                self.assertEqual(
                    fetched.status_code, 200, fetched.text)
                self.assertEqual(fetched.json(), metadata)
                self._wait_until(
                    lambda: self._inflight(app)["count"] == 0,
                    "upload slot did not settle after body done")
                self._reclaim_victim(app, clock, victim, keepers)

    def test_decode_thread_survives_request_cancellation(self):
        # Hold only the observed named decoder (the real decoder
        # still runs, after the hold). Cancelling the HTTP task
        # settles the handler side but must not free the session
        # until the decode thread itself returns.
        real_decode = http_app_module.decode_page
        entered = threading.Event()
        release = threading.Event()
        returned = threading.Event()
        hold_timed_out = threading.Event()

        def held_decode(data, page_index, rights_ref=None):
            entered.set()
            if not release.wait(timeout=10):
                hold_timed_out.set()
            decoded = real_decode(
                data, page_index, rights_ref=rights_ref)
            returned.set()
            return decoded

        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                victim = self._open_session(
                    stack, app, settings=True)
                keepers = self._fill_eight(stack, app, victim)
                image = synthetic_png()
                with mock.patch.object(
                        http_app_module, "decode_page", held_decode):
                    raw = _RawAsgiCall(
                        app, base_url=LOCAL_BASE, method="POST",
                        path="/api/session/start",
                        query_string=b"page_index=0",
                        headers={
                            "Origin": LOCAL_BASE,
                            "Cookie": victim.cookie,
                            "X-CSRF-Token": victim.csrf,
                            "Content-Type":
                            "application/octet-stream"},
                        chunks=[image])
                    raw.start()
                    try:
                        self.assertTrue(
                            entered.wait(timeout=5),
                            "decode boundary never reached")
                        raw.cancel(self)
                        # Cancellation of the actual raw task was
                        # acknowledged in-loop; the decode thread
                        # is still held and may only drain once
                        # explicitly released below.
                        self.assertTrue(
                            raw.cancel_acknowledged.is_set(),
                            "raw ASGI task cancellation was not "
                            "acknowledged")
                        self.assertTrue(entered.is_set())
                        self.assertFalse(returned.is_set())
                        clock.advance(3600)
                        self._renew(keepers)

                        def victim_held():
                            return (
                                entered.is_set()
                                and not returned.is_set()
                                and id(victim.state)
                                in self._state_ids(app))

                        self._assert_ninth_refused_while_held(
                            app, release.set,
                            active_hold=victim_held)
                        self.assertIn(
                            id(victim.state), self._state_ids(app))
                    finally:
                        release.set()
                        status, body = raw.finish(self)
                    self.assertFalse(
                        hold_timed_out.is_set(),
                        "decode hold ceiling expired before "
                        "explicit release")
                    self.assertTrue(
                        returned.wait(timeout=5),
                        "decode thread never returned")
                    # Cancelled upload: no 201 was delivered on
                    # the cancelled call in either handling.
                    self.assertNotEqual(status, 201, body)
                    if "cancelled" not in raw.result:
                        self.assertIsNotNone(
                            status,
                            "raw call ended with neither a "
                            "response nor a cancellation record")
                # Eligibility starts only now. The detached
                # thread either published truthfully or published
                # nothing; both keep the (live) session usable,
                # never 401 before reclaim.
                page = victim.client.get("/api/session/page")
                self.assertIn(
                    page.status_code, (200, 404), page.text)
                self._reclaim_victim(app, clock, victim, keepers)

    def test_pre_binding_admission_blocks_eviction(self):
        # Hold only the observed named freeze (real freeze still
        # runs first). At that point the operation slot is
        # reserved but no binding is published yet: no worker
        # charge, no provider context, provider not invoked.
        real_freeze = http_app_module.deep_freeze
        fired = {"value": False}
        entered = threading.Event()
        release = threading.Event()
        expired = threading.Event()
        returned = threading.Event()

        def held_freeze(value):
            frozen = real_freeze(value)
            if not fired["value"]:
                # One shot only: later freezes (keeper renew /
                # settings included) must pass through unheld.
                fired["value"] = True
                entered.set()
                if not release.wait(timeout=10):
                    expired.set()
                    raise AssertionError(
                        "pre-binding hold expired before the "
                        "test released it")
                returned.set()
            return frozen

        def assert_hold_alive():
            self.assertTrue(
                entered.is_set(),
                "pre-binding hold never entered")
            self.assertFalse(
                expired.is_set(),
                "pre-binding hold expired before eviction "
                "was observed")
            self.assertFalse(
                returned.is_set(),
                "pre-binding hold returned before eviction "
                "was observed")
            return True

        provider = _HeldSourceProvider()
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app(provider=provider)
            with contextlib.ExitStack() as stack:
                victim = self._open_session(stack, app, start=True)
                keepers = self._fill_eight(stack, app, victim)
                command = _operation_command(
                    victim.page["source_id"])
                request_id = command["request_id"]
                generation = victim.state.source_generation
                op_key = (generation, request_id)
                headers = {**victim.headers,
                           "Cookie": victim.cookie}
                with mock.patch.object(
                        http_app_module, "deep_freeze", held_freeze):
                    thread, result = self._post_async(
                        app, "/api/session/source-analysis",
                        headers, body=command)
                    try:
                        self.assertTrue(
                            entered.wait(timeout=5),
                            "pre-binding boundary never reached")
                        with self._registry_lock(app):
                            workers = dict(
                                victim.state.worker_reservations)
                            contexts = dict(
                                victim.state.provider_contexts)
                        self.assertNotIn(op_key, workers)
                        self.assertNotIn(op_key, contexts)
                        self.assertEqual(provider.requests, [])
                        self.assertEqual(
                            self._inflight(app)["provider"], 1)
                        assert_hold_alive()
                        clock.advance(3600)
                        self._renew(keepers)
                        self._assert_ninth_refused_while_held(
                            app, release.set,
                            active_hold=lambda: assert_hold_alive())
                        # The helper may have released the hold;
                        # only expiry is still checkable here.
                        self.assertFalse(
                            expired.is_set(),
                            "pre-binding hold expired during the "
                            "ninth-session attempt")
                        self.assertIn(
                            id(victim.state), self._state_ids(app))
                    finally:
                        # Release + bounded join inside the mock
                        # and clock scopes, even on failure.
                        release.set()
                        response = self._finish_async(
                            thread, result)
                self.assertEqual(
                    response.status_code, 201, response.text)
                self.assertEqual(len(provider.requests), 1)
                self.assertEqual(
                    response.json()["snapshot"]["regions"][0]
                    ["raw_text"], RAW_TEXT)
                self._wait_worker_drained(
                    app, victim.state, op_key)
                self._reclaim_victim(app, clock, victim, keepers)

    def test_detached_preparation_blocks_eviction(self):
        # Provider has returned; detached candidate preparation
        # is held at the observed named graph-cost measure (real
        # measure still runs first), discriminated by the victim
        # CSRF token so keepers pass through. Nothing is
        # published yet, and no eviction may intervene.
        real_cost = http_app_module.session_graph_cost
        armed = {"value": False}
        fired = {"value": False}
        entered = threading.Event()
        release = threading.Event()
        expired = threading.Event()
        returned = threading.Event()

        def held_cost(csrf_token, pending, decoded, workspace,
                      provider_results, raw_status):
            cost = real_cost(
                csrf_token, pending, decoded, workspace,
                provider_results, raw_status)
            if (armed["value"] and not fired["value"]
                    and csrf_token == held_cost.victim_csrf):
                # One shot, victim only: keeper renew/settings
                # measures must pass through unheld.
                fired["value"] = True
                entered.set()
                if not release.wait(timeout=10):
                    expired.set()
                    raise AssertionError(
                        "detached-preparation hold expired "
                        "before the test released it")
                returned.set()
            return cost

        def assert_hold_alive():
            self.assertTrue(
                entered.is_set(),
                "detached-preparation hold never entered")
            self.assertFalse(
                expired.is_set(),
                "detached-preparation hold expired before "
                "eviction was observed")
            self.assertFalse(
                returned.is_set(),
                "detached-preparation hold returned before "
                "eviction was observed")
            return True

        held_cost.victim_csrf = None
        provider = _HeldSourceProvider()
        provider.on_result = (
            lambda: armed.__setitem__("value", True))
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app(provider=provider)
            with contextlib.ExitStack() as stack:
                victim = self._open_session(stack, app, start=True)
                held_cost.victim_csrf = victim.csrf
                keepers = self._fill_eight(stack, app, victim)
                command = _operation_command(
                    victim.page["source_id"])
                request_id = command["request_id"]
                generation = victim.state.source_generation
                op_key = (generation, request_id)
                headers = {**victim.headers,
                           "Cookie": victim.cookie}
                with mock.patch.object(
                        http_app_module, "session_graph_cost",
                        held_cost):
                    thread, result = self._post_async(
                        app, "/api/session/source-analysis",
                        headers, body=command)
                    try:
                        self.assertTrue(
                            entered.wait(timeout=5),
                            "detached-preparation boundary never "
                            "reached")
                        during = self._snapshot(victim)
                        self.assertEqual(
                            self._request_from(during, request_id)
                            ["state"], "working")
                        self.assertEqual(during["regions"], [])
                        assert_hold_alive()
                        clock.advance(3600)
                        self._renew(keepers)
                        self._assert_ninth_refused_while_held(
                            app, release.set,
                            active_hold=lambda: assert_hold_alive())
                        # The helper may have released the hold;
                        # only expiry is still checkable here.
                        self.assertFalse(
                            expired.is_set(),
                            "detached-preparation hold expired "
                            "during the ninth-session attempt")
                        self.assertIn(
                            id(victim.state), self._state_ids(app))
                    finally:
                        # Release + bounded join inside the mock
                        # and clock scopes, even on failure.
                        release.set()
                        response = self._finish_async(
                            thread, result)
                # The held publisher wrote through the live
                # session exactly once, with the correct result.
                self.assertEqual(
                    response.status_code, 201, response.text)
                self.assertEqual(
                    response.json()["snapshot"]["regions"][0]
                    ["raw_text"], RAW_TEXT)
                self._wait_worker_drained(
                    app, victim.state, op_key)
                self._reclaim_victim(app, clock, victim, keepers)

    def test_plain_snapshot_read_pin_blocks_eviction(self):
        # Every /api/session consumer pins, including a plain
        # read whose raw session reference runs in a threadpool
        # thread. The payload builder is NOT the seam: it runs
        # inside state.lock, so holding it would block eviction
        # by lock contention alone and could not prove the
        # unlocked thread pin. Instead wrap the genuine
        # threadpool runner so that, one shot, the exact
        # read_snapshot function is replaced in the worker by a
        # sync wrapper that runs it to completion first (its
        # state.lock is therefore released) and only then holds,
        # still inside the worker, before the handler settles.
        # The product decision and the ASGI path are untouched.
        genuine_read = getattr(
            http_app_module, "read_snapshot", None)
        fired = {"value": False}
        entered = threading.Event()
        release = threading.Event()
        expired = threading.Event()
        returned = threading.Event()

        def is_read_snapshot(fn):
            candidate = getattr(
                fn, "__wrapped__", getattr(fn, "func", fn))
            if (genuine_read is not None
                    and candidate is genuine_read):
                return True
            code = getattr(candidate, "__code__", None)
            return (
                getattr(candidate, "__name__", None)
                == "read_snapshot"
                and getattr(candidate, "__module__", None)
                == http_app_module.__name__
                and code is not None
                and code.co_name == "read_snapshot")

        def wrap_runner(real_run):
            async def held_run(*args, **kwargs):
                if (args and not fired["value"]
                        and is_read_snapshot(args[0])):
                    # One shot per schedule: keeper renew /
                    # settings and every other function pass
                    # through the genuine runner unheld.
                    fired["value"] = True
                    genuine_fn = args[0]

                    def held_read(*fn_args, **fn_kwargs):
                        result = genuine_fn(
                            *fn_args, **fn_kwargs)
                        entered.set()
                        if not release.wait(timeout=10):
                            expired.set()
                            raise AssertionError(
                                "plain-read hold expired "
                                "before the test released it")
                        returned.set()
                        return result

                    held_read.__name__ = "read_snapshot"
                    held_read.__wrapped__ = genuine_fn
                    return await real_run(
                        held_read, *args[1:], **kwargs)
                return await real_run(*args, **kwargs)

            return held_run

        def assert_hold_alive():
            self.assertTrue(
                entered.is_set(),
                "plain-read hold never entered")
            self.assertFalse(
                expired.is_set(),
                "plain-read hold expired before eviction "
                "was observed")
            self.assertFalse(
                returned.is_set(),
                "plain-read hold returned before eviction "
                "was observed")
            return True

        runner_names = [
            name for name in ("_base_run_in_threadpool",
                              "run_in_threadpool")
            if hasattr(http_app_module, name)]
        self.assertTrue(
            runner_names,
            "http_app exposes no threadpool runner seam "
            "(_base_run_in_threadpool / run_in_threadpool)")

        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                victim = self._open_session(stack, app, start=True)
                keepers = self._fill_eight(stack, app, victim)
                headers = {"Cookie": victim.cookie}
                with contextlib.ExitStack() as patches:
                    for runner_name in runner_names:
                        patches.enter_context(mock.patch.object(
                            http_app_module, runner_name,
                            wrap_runner(getattr(
                                http_app_module, runner_name))))
                    # Healthy schedule: no cancellation.
                    thread, result = self._get_async(
                        app, "/api/session/snapshot", headers)
                    try:
                        self.assertTrue(
                            entered.wait(timeout=5),
                            "snapshot-read boundary never reached")
                        # read_snapshot has returned, so its
                        # state.lock must be free. This acquire,
                        # not any elapsed wait, is the proof.
                        self.assertTrue(
                            victim.state.lock.acquire(False),
                            "snapshot lock still held in the "
                            "worker; unlocked thread pin not "
                            "proven")
                        victim.state.lock.release()
                        assert_hold_alive()
                        clock.advance(3600)
                        self._renew(keepers)
                        self._assert_ninth_refused_while_held(
                            app, release.set,
                            active_hold=lambda: assert_hold_alive())
                        # The helper may have released the hold;
                        # only expiry is still checkable here.
                        self.assertFalse(
                            expired.is_set(),
                            "plain-read hold expired during the "
                            "ninth-session attempt")
                        self.assertIn(
                            id(victim.state), self._state_ids(app))
                    finally:
                        # Release + bounded join inside the
                        # runner and clock scopes, even on
                        # failure.
                        release.set()
                        response = self._finish_async(
                            thread, result)
                    self.assertEqual(
                        response.status_code, 200, response.text)
                    self.assertEqual(
                        response.json()["source_id"],
                        victim.page["source_id"])
                    # Cancellation schedule (same method, no new
                    # test): re-arm the one-shot hold, cancel the
                    # actual ASGI request while the worker is
                    # held, and require the same refusal. The
                    # cancelled call may then either drain to a
                    # 200 or settle as an independently counted
                    # thread; neither may evict prematurely.
                    fired["value"] = False
                    entered.clear()
                    release.clear()
                    expired.clear()
                    returned.clear()
                    raw = _RawAsgiCall(
                        app, base_url=LOCAL_BASE, method="GET",
                        path="/api/session/snapshot",
                        headers={"Origin": LOCAL_BASE,
                                 "Cookie": victim.cookie})
                    raw.start()
                    try:
                        self.assertTrue(
                            entered.wait(timeout=5),
                            "cancelled snapshot-read boundary "
                            "never reached")
                        self.assertTrue(
                            victim.state.lock.acquire(False),
                            "snapshot lock still held in the "
                            "cancelled-read worker")
                        victim.state.lock.release()
                        assert_hold_alive()
                        raw.cancel(self)
                        # The worker thread cannot be cancelled
                        # away: the hold must still be live.
                        assert_hold_alive()
                        clock.advance(3600)
                        self._renew(keepers)
                        self._assert_ninth_refused_while_held(
                            app, release.set,
                            active_hold=lambda: assert_hold_alive())
                        self.assertFalse(
                            expired.is_set(),
                            "cancelled plain-read hold expired "
                            "during the ninth-session attempt")
                        self.assertIn(
                            id(victim.state), self._state_ids(app))
                    finally:
                        release.set()
                        status, body = raw.finish(self)
                    self.assertTrue(
                        returned.wait(timeout=5),
                        "cancelled snapshot-read worker never "
                        "returned after release")
                    if status is not None:
                        self.assertEqual(status, 200, body)
                        self.assertEqual(
                            json.loads(
                                body.decode("utf-8"))["source_id"],
                            victim.page["source_id"])
                    else:
                        self.assertTrue(
                            raw.result.get("cancelled"),
                            "cancelled snapshot read neither "
                            "drained to a response nor "
                            "acknowledged cancellation")
                    self._wait_until(
                        lambda: self._inflight(app)["count"] == 0,
                        "cancelled snapshot read did not settle")
                self._reclaim_victim(app, clock, victim, keepers)

    def test_concurrent_admissions_never_over_cap(self):
        clock = _FakeSessionClock()
        with mock.patch.object(
                http_app_module, "_session_now", clock):
            app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                victims = [
                    self._open_session(stack, app, settings=True)
                    for _ in range(2)]
                keepers = [
                    self._open_session(stack, app, settings=True)
                    for _ in range(6)]
                self.assertEqual(len(self._sessions(app)), 8)
                clock.advance(LEASE + 30)
                self._renew(keepers)
                outcomes = self._race_admissions(app, racers=4)
                statuses = sorted(
                    outcome.status_code for outcome in outcomes)
                self.assertEqual(statuses, [201, 201, 503, 503])
                for outcome in outcomes:
                    if outcome.status_code == 503:
                        self.assertEqual(
                            outcome.json(),
                            {"detail": "session limit reached"})
                winners = [
                    outcome.json()["csrf_token"]
                    for outcome in outcomes
                    if outcome.status_code == 201]
                self.assertEqual(len(set(winners)), 2)
                self.assertEqual(len(self._sessions(app)), 8)
                for keeper in keepers:
                    self.assertIn(
                        id(keeper.state), self._state_ids(app))
                for victim in victims:
                    self.assertNotIn(
                        id(victim.state), self._state_ids(app))
            # Opposite control: with nobody eligible, concurrent
            # admissions all refuse and evict nobody.
            fresh_app = self._hosted_app()
            with contextlib.ExitStack() as stack:
                handles = [
                    self._open_session(
                        stack, fresh_app, settings=True)
                    for _ in range(8)]
                before_ids = self._state_ids(fresh_app)
                outcomes = self._race_admissions(
                    fresh_app, racers=4)
                self.assertEqual(
                    sorted(o.status_code for o in outcomes),
                    [503, 503, 503, 503])
                self.assertEqual(
                    self._state_ids(fresh_app), before_ids)
                for handle in handles:
                    self._put_settings(handle)

    def _race_admissions(self, app, *, racers):
        barrier = threading.Barrier(racers)
        results = [None] * racers
        errors = [None] * racers

        def run(index):
            try:
                with TestClient(
                        app, base_url=LOCAL_BASE) as client:
                    barrier.wait(timeout=10)
                    results[index] = client.post(
                        "/api/sessions", json={},
                        headers={"Origin": LOCAL_BASE})
            except BaseException as exc:
                errors[index] = repr(exc)

        threads = [
            threading.Thread(target=run, args=(index,),
                             daemon=True)
            for index in range(racers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(
                thread.is_alive(), "concurrent admission hung")
        self.assertEqual(errors, [None] * racers)
        return results


if __name__ == "__main__":
    unittest.main()
