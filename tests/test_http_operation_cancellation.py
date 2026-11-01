"""Focused HTTP cancellation/currentness tests (source only).

Coverage for the teacher demo. These
tests use the real ``http_app.create_app(provider=...)`` surface and
authenticated HTTP commands ``{request_id, source_id}`` (cancel is
``{source_id}``) with prechosen 32-lower-hex request IDs and source
IDs taken from actual Start responses. The provider is an ordinary
synthetic in-process callable; there is no real Meta transport.

Permitted test seams:

- a context-aware synthetic provider that records the exact
  ``ProviderCallContext`` it was invoked with and blocks on finite
  test-owned release events (it never writes domain state);
- read-only observation of session accounting through the app's own
  closure state, always under session -> registry lock order;
- C2 only: a one-shot ``http_app.JSONResponse`` wrapper that raises
  a fixed synthetic error at one writer's genuine
  response-preparation boundary, restored in ``finally``;
- C3 only: a finite trace-only observer (``threading.settrace``)
  that holds one genuine worker boundary, plus direct cancellation
  of the real authenticated route handler as an ``asyncio`` Task.
  That is direct handler-task cancellation, not an ASGI disconnect
  simulation.

Cases whose ready witness never arrives fail as
``instrumentation-unresolved``; they are not product verdicts.
Per-case outcome evidence is kept on
``HttpOperationCancellationTests.EVIDENCE``.
"""
import asyncio
import copy
import inspect
import io
import json
import pathlib
import re
import secrets
import sys
import threading
import time
import unittest
from urllib.parse import quote

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[0] / "src"))

import test_http_teacher_flow as fixtures  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402
from starlette.requests import Request  # noqa: E402

from teacher_demo import http_app  # noqa: E402
from teacher_demo.http_app import create_app  # noqa: E402

BASE_URL = fixtures.BASE_URL
CHANGED_GRADE = "  الصَّفُّ ٦  "
CHANGED_OBJECTIVE = "  هَدَفٌ جَدِيدٌ: تَحْلِيلُ النَّصِّ ٤٥٦  "
OLD_TEXT = "  نَصُّ الْمَصْدَرِ الْقَدِيمِ ١  "
NEW_TEXT = "  نَصُّ الْمَصْدَرِ الْجَدِيدِ ٢  "
SYNTHETIC_FAULT = "synthetic response-construction failure (test only)"
ENTER_TIMEOUT = 10.0
READY_TIMEOUT = 15.0
JOIN_TIMEOUT = 15.0
HOLD_CEILING = 30.0
ZERO_TIMEOUT = 10.0
_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _png_bytes(color, size=(6, 4)):
    buffer = io.BytesIO()
    with Image.new("RGBA", size, color) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _source_result(text, section_id, response_id):
    return {
        "raw": {
            "sections": [{
                "section_id": section_id,
                "kind": "body",
                "text": text,
                "review_reasons": ["synthetic-flag"],
            }],
            "page_continues": False,
            "review_notes": ["synthetic note"],
        },
        "model_name": fixtures.SYNTHETIC_MODEL,
        "response_id": response_id,
        "outcome": "ok",
    }


def _fresh_request_id():
    request_id = secrets.token_hex(16)
    assert _HEX32.match(request_id), request_id
    return request_id


class _CallHold:
    """One provider invocation: entered/release barriers + records."""

    __slots__ = ("entered", "release", "request", "context", "result")

    def __init__(self, request, context, result):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.request = request
        self.context = context
        self.result = result

    @property
    def event(self):
        return None if self.context is None else self.context.cancel_event


class _BlockingProvider:
    """Ordinary synthetic provider with finite per-call holds.

    Accepts the production ``context=`` adaptation so tests can
    record the exact invocation context/event. It blocks only on
    its own release event (finite ceiling), then returns the queued
    synthetic envelope unchanged; it never inspects or writes any
    domain/accounting state.
    """

    def __init__(self, results):
        self._results = list(results)
        self._lock = threading.Lock()
        self._released_all = False
        self.calls = []

    def __call__(self, request, *, context=None):
        with self._lock:
            index = len(self.calls)
            result = self._results[min(index, len(self._results) - 1)]
            hold = _CallHold(copy.deepcopy(dict(request)), context, result)
            if self._released_all:
                hold.release.set()
            self.calls.append(hold)
        hold.entered.set()
        if not hold.release.wait(timeout=HOLD_CEILING):
            raise RuntimeError("synthetic provider hold ceiling reached")
        return copy.deepcopy(hold.result)

    def release_all(self):
        with self._lock:
            self._released_all = True
            calls = list(self.calls)
        for hold in calls:
            hold.release.set()

    def call_or_none(self, index):
        with self._lock:
            return self.calls[index] if index < len(self.calls) else None

    def wait_call(self, index, timeout=ENTER_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hold = self.call_or_none(index)
            if hold is not None and hold.entered.is_set():
                return hold
            time.sleep(0.005)
        return None

    @property
    def call_count(self):
        with self._lock:
            return len(self.calls)


class _TraceHold:
    """Finite trace-only hold at one genuine worker boundary.

    Matching is by exact code-object identity first; only then, for
    return holds, by committed-success return shape. The product
    helper is never called or replaced by this observer.
    """

    def __init__(self, code, mode, previous_trace=None):
        assert mode in ("entry", "return_success")
        self._code = code
        self._mode = mode
        self._previous = previous_trace
        self.ready = threading.Event()
        self.release = threading.Event()
        self.disarmed = False
        self._fired = False
        self.captured_request_id = None
        self.captured_return = None
        self.hold_started_at = None
        self.hold_active = False
        self.hold_ceiling_hit = False
        self._c3_witness = None

    def _capture_request_id(self, frame):
        binding = frame.f_locals.get("binding")
        if isinstance(binding, dict):
            self.captured_request_id = binding.get("request_id")

    def _hold(self, frame, returned=None):
        self._capture_request_id(frame)
        self.captured_return = returned
        self.hold_started_at = time.monotonic()
        self.hold_active = True
        self.ready.set()
        released = self.release.wait(timeout=HOLD_CEILING)
        self.hold_ceiling_hit = not released
        self.hold_active = False

    @staticmethod
    def _is_committed_success(value):
        return (isinstance(value, tuple) and len(value) == 2
                and value[0] == "response"
                and getattr(value[1], "status_code", None) == 201)

    def global_trace(self, frame, event, arg):
        if frame.f_code is not self._code:
            witness = getattr(self, "_c3_witness", None)
            if witness is not None and frame.f_code is witness.code:
                return witness.local_trace
            if self._previous is not None:
                return self._previous(frame, event, arg)
            return None
        if self.disarmed or self._fired:
            return None
        if self._mode == "entry":
            if event == "call":
                self._fired = True
                self._hold(frame)
            return None
        return self._local_trace

    def _local_trace(self, frame, event, arg):
        if event == "return" and not self.disarmed and not self._fired:
            if self._is_committed_success(arg):
                self._fired = True
                self._hold(frame, returned=(arg[0], arg[1].status_code))
        return self._local_trace


class _C3CancellationWitness:
    """Record scheduled, accepted and delivered Task cancellations.

    Requests run in a test-owned callback on the exact Task's loop and
    publish an acknowledgement only after the original bound
    ``Task.cancel`` method returns. Delivery is recorded separately
    when tracing shows ``CancelledError`` at the corresponding shield
    await in the genuine ``run_owned`` frame of that exact Task.
    """

    def __init__(self, run_owned):
        self._function = run_owned
        self.code = getattr(run_owned, "__code__", None)
        self.instrumentation_error = None
        self._task = None
        self._cancel = None
        self._lock = threading.Lock()
        self._phase = 0
        self._scheduled = {}
        self._acks = {}
        self._deliveries = {}
        self._errors = []
        self._unexpected = []
        self._rejected = []
        self._ack_events = {1: threading.Event(), 2: threading.Event()}
        self._delivery_events = {
            1: threading.Event(), 2: threading.Event()}
        self._last_lines = {}
        self._initial_line = None
        self._drain_line = None
        if self.code is None:
            self.instrumentation_error = "run_owned code not locatable"
            return
        try:
            source_lines, first_line = inspect.getsourcelines(run_owned)
        except (OSError, TypeError) as error:
            self.instrumentation_error = (
                "run_owned shield sites not locatable: " + repr(error))
            return
        sites = [first_line + offset
                 for offset, line in enumerate(source_lines)
                 if "await asyncio.shield(task)" in line]
        if len(sites) != 2:
            self.instrumentation_error = (
                "run_owned shield sites not locatable: " + repr(sites))
            return
        self._initial_line, self._drain_line = sites

    def bind_task(self, task):
        with self._lock:
            if self._task is not None and self._task is not task:
                self._errors.append("route Task identity changed")
                return
            self._task = task
            self._cancel = task.cancel

    def begin_request(self, sequence):
        with self._lock:
            if sequence not in (1, 2):
                self._errors.append(
                    "invalid cancellation sequence " + repr(sequence))
                return
            if sequence in self._scheduled:
                self._errors.append(
                    "duplicate cancellation schedule " + repr(sequence))
                return
            if sequence == 2 and 1 not in self._deliveries:
                self._errors.append(
                    "second cancellation scheduled before first delivery")
                return
            self._phase = sequence
            self._scheduled[sequence] = time.monotonic()

    def request_cancel(self, sequence):
        """Run on the owning loop; acknowledge after ``cancel()``."""
        acknowledgement = None
        try:
            task = self._task
            cancel = self._cancel
            if task is None or cancel is None:
                raise RuntimeError("route Task was not bound")
            if asyncio.get_running_loop() is not task.get_loop():
                raise RuntimeError("cancellation ran on the wrong loop")
            before = task.cancelling()
            returned = cancel()
            after = task.cancelling()
            acknowledgement = {
                "task_id": id(task),
                "returned": returned,
                "before": before,
                "after": after,
                "at": time.monotonic(),
            }
        except BaseException as error:
            acknowledgement = {"error": repr(error)}
        with self._lock:
            self._acks[sequence] = acknowledgement
            if "error" in acknowledgement:
                self._errors.append(
                    "cancellation request failed: "
                    + acknowledgement["error"])
        self._ack_events[sequence].set()

    def wait_ack(self, sequence, timeout):
        return self._ack_events[sequence].wait(timeout=timeout)

    def wait_delivery(self, sequence, timeout):
        return self._delivery_events[sequence].wait(timeout=timeout)

    def snapshot(self):
        with self._lock:
            return {
                "scheduled": dict(self._scheduled),
                "acks": {key: dict(value)
                         for key, value in self._acks.items()},
                "deliveries": {key: dict(value)
                               for key, value in
                               self._deliveries.items()},
                "errors": list(self._errors),
                "unexpected": list(self._unexpected),
                "rejected": list(self._rejected),
            }

    @staticmethod
    def _frame_in_task(task, frame):
        try:
            coroutine = task.get_coro()
        except Exception:
            return False
        seen = set()
        while coroutine is not None and id(coroutine) not in seen:
            seen.add(id(coroutine))
            if (getattr(coroutine, "cr_frame", None) is frame
                    or getattr(coroutine, "gi_frame", None) is frame):
                return True
            coroutine = (getattr(coroutine, "cr_await", None)
                         or getattr(coroutine, "gi_yieldfrom", None))
        return False

    def local_trace(self, frame, event, arg):
        if event == "line":
            with self._lock:
                self._last_lines[id(frame)] = frame.f_lineno
        elif event == "exception":
            self._observe_exception(frame, arg)
        elif event == "return":
            with self._lock:
                self._last_lines.pop(id(frame), None)
        return self.local_trace

    def _observe_exception(self, frame, arg):
        if not isinstance(arg, tuple) or len(arg) < 2:
            return
        exception_type, exception = arg[0], arg[1]
        if not (isinstance(exception_type, type)
                and issubclass(exception_type, asyncio.CancelledError)):
            return
        with self._lock:
            sequence = self._phase
            task = self._task
            acknowledged = sequence in self._acks
            already_delivered = sequence in self._deliveries
            last_line = self._last_lines.get(id(frame))
            expected_line = (
                self._initial_line if sequence == 1
                else self._drain_line if sequence == 2 else None)
            first_delivery = self._deliveries.get(1)
        if already_delivered:
            return
        try:
            current_task = asyncio.current_task()
        except RuntimeError:
            current_task = None
        exact_task = task is not None and current_task is task
        exact_frame = bool(
            exact_task and self._frame_in_task(task, frame))
        observed = {
            "task_id": id(task) if task is not None else None,
            "current_task_id": (
                id(current_task) if current_task is not None else None),
            "frame_id": id(frame),
            "code_name": frame.f_code.co_name,
            "expected_line": expected_line,
            "line": frame.f_lineno,
            "last_line": last_line,
            "exception": exception,
            "at": time.monotonic(),
        }
        if sequence not in (1, 2):
            if exact_task and exact_frame:
                with self._lock:
                    self._unexpected.append(observed)
            return
        reasons = []
        if not exact_task:
            reasons.append("current Task is not the original route Task")
        if not exact_frame:
            reasons.append("frame is outside the original route Task")
        if not acknowledged:
            reasons.append("delivery preceded cancellation acknowledgement")
        if expected_line not in (frame.f_lineno, last_line):
            reasons.append("delivery site is not the expected shield await")
        if (sequence == 2 and first_delivery is not None
                and (observed["frame_id"]
                     != first_delivery["frame_id"]
                     or exception is first_delivery["exception"])):
            reasons.append("second delivery is not a distinct drain delivery")
        if reasons:
            with self._lock:
                rejected = dict(observed)
                rejected["reasons"] = reasons
                self._rejected.append(rejected)
            return
        with self._lock:
            if sequence in self._deliveries:
                return
            self._deliveries[sequence] = observed
        self._delivery_events[sequence].set()


def _closure_values(app):
    """Read-only map of create_app closure cells, by variable name.

    Function values are also keyed as ``__fn__:<name>`` so tests can
    match exact product code objects without replacing anything.
    """
    found = {}
    seen = set()
    queue = []
    for route in getattr(app, "routes", []):
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None:
            queue.append(endpoint)
    while queue:
        function = queue.pop(0)
        if id(function) in seen or not inspect.isfunction(function):
            continue
        seen.add(id(function))
        code = getattr(function, "__code__", None)
        cells = getattr(function, "__closure__", None)
        if code is None or not cells:
            continue
        for name, cell in zip(code.co_freevars, cells):
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            found.setdefault(name, value)
            if inspect.isfunction(value):
                found.setdefault("__fn__:" + value.__name__, value)
                queue.append(value)
    return found


def _find_endpoint(app, path):
    for route in getattr(app, "routes", []):
        if getattr(route, "path", None) == path:
            return getattr(route, "endpoint", None)
    return None


class HttpOperationCancellationTests(unittest.TestCase):
    EVIDENCE = {}

    # -- small in-file helpers ------------------------------------
    def _record(self, key, **facts):
        self.EVIDENCE[key] = facts

    def _unresolved(self, message):
        self.fail("instrumentation-unresolved: " + message)

    def _setup_session(self, client):
        created = client.post(
            "/api/sessions", json={}, headers={"Origin": BASE_URL})
        self.assertEqual(created.status_code, 201, created.text)
        csrf = created.json()["csrf_token"]
        cookie = created.headers.get("set-cookie", "")
        self.assertTrue(cookie.startswith("teacher_session="), cookie)
        session_id = cookie.split(";", 1)[0].split("=", 1)[1]
        headers = {"Origin": BASE_URL, "X-CSRF-Token": csrf}
        settings = client.put(
            "/api/session/settings",
            json={"grade": fixtures.GRADE,
                  "objective": fixtures.OBJECTIVE,
                  "rights_ref": fixtures.RIGHTS_REF}, headers=headers)
        self.assertEqual(settings.status_code, 200, settings.text)
        return csrf, session_id, headers

    def _start(self, client, headers, data):
        started = client.post(
            "/api/session/start", params={"page_index": 0},
            content=data,
            headers={**headers,
                     "Content-Type": "application/octet-stream"})
        self.assertEqual(started.status_code, 201, started.text)
        return started.json()

    def _snapshot(self, client):
        fetched = client.get("/api/session/snapshot")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        return fetched.json()

    def _original_bytes(self, client):
        fetched = client.get("/api/session/page/original")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        return fetched.content

    def _raw(self, client, request_id):
        return client.get(
            "/api/session/requests/"
            + quote(request_id, safe="") + "/raw")

    @staticmethod
    def _request_from(snapshot, request_id):
        for item in snapshot["requests"]:
            if item["request_id"] == request_id:
                return item
        return None

    def _observe(self, app, session_id):
        """Snapshot related state under session -> registry order."""
        values = _closure_values(app)
        sessions = values.get("sessions")
        registry_lock = values.get("registry_lock")
        if sessions is None or registry_lock is None:
            self._unresolved("session/registry state not locatable")
        with registry_lock:
            state = sessions.get(session_id)
        if state is None:
            self._unresolved("own session state not locatable")
        operations = values.get("operations") or {}
        inflight = values.get("inflight") or {}
        with state.lock:
            workspace_snapshot = (
                state.workspace.snapshot()
                if state.workspace is not None else None)
            with registry_lock:
                observed = {
                    "generation": state.source_generation,
                    "worker_keys": sorted(state.worker_reservations),
                    "contexts": dict(state.provider_contexts),
                    "operations": len(operations),
                    "inflight_provider": inflight.get("provider"),
                    "pending": state.pending,
                    "raw_status": dict(state.raw_status),
                    "result_keys": sorted(state.provider_results),
                    "source_id": (
                        state.decoded.decoded_source.page.source_id
                        if state.decoded is not None else None),
                    "snapshot": workspace_snapshot,
                    "requests": {
                        item.request_id: item
                        for item in (
                            workspace_snapshot.requests
                            if workspace_snapshot is not None else ())},
                }
        return observed

    def _assert_zero_ownership(self, app, session_id, label):
        deadline = time.monotonic() + ZERO_TIMEOUT
        observed = None
        while time.monotonic() < deadline:
            observed = self._observe(app, session_id)
            if (not observed["worker_keys"]
                    and not observed["contexts"]
                    and observed["operations"] == 0
                    and observed["inflight_provider"] == 0):
                return observed
            time.sleep(0.01)
        self.fail(
            f"{label}: ownership did not clear: "
            f"workers={observed['worker_keys']} "
            f"contexts={sorted(observed['contexts'])} "
            f"operations={observed['operations']} "
            f"inflight={observed['inflight_provider']}")

    def _background_post(self, app, session_id, csrf, path, body):
        holder = {"response": None, "error": None}
        headers = {
            "Origin": BASE_URL, "X-CSRF-Token": csrf,
            "Cookie": "teacher_session=" + session_id}

        def run():
            try:
                client = TestClient(app, base_url=BASE_URL)
                holder["response"] = client.post(
                    path, json=body, headers=headers)
            except Exception as error:  # surfaced by _join below
                holder["error"] = error

        thread = threading.Thread(
            target=run, name="cancellation-test-client", daemon=True)
        thread.start()
        return thread, holder

    def _join(self, thread, holder, label):
        thread.join(timeout=JOIN_TIMEOUT)
        self.assertFalse(
            thread.is_alive(),
            f"instrumentation-unresolved: {label} thread did not join")
        self.assertIsNone(holder["error"], repr(holder["error"]))
        self.assertIsNotNone(holder["response"], label)
        return holder["response"]

    def _wait_provider_call(self, provider, index, label):
        hold = provider.wait_call(index)
        if hold is None:
            self._unresolved(f"provider call {index} never entered: {label}")
        self.assertIsNotNone(hold.context, label)
        self.assertIsNotNone(hold.event, label)
        return hold

    def _assert_raw_if_retained(self, client, request_id, envelope):
        fetched = self._raw(client, request_id)
        if fetched.status_code == 404:
            return "not_retained"
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["provider_result"], envelope)
        return "retained_exact"

    # -- C1: same public ID across genuine source replacement ----
    def test_reused_id_keeps_new_generation_owned(self):
        old_result = _source_result(
            OLD_TEXT, "old-section-1", "synthetic-old-response-1")
        new_result = _source_result(
            NEW_TEXT, "new-section-1", "synthetic-new-response-1")
        provider = _BlockingProvider([old_result, new_result])
        app = create_app(provider=provider)
        original_old = _png_bytes((200, 30, 40, 128))
        original_new = _png_bytes((30, 40, 200, 255), size=(5, 7))
        self.assertNotEqual(original_old, original_new)
        request_id = _fresh_request_id()
        threads = []
        with TestClient(app, base_url=BASE_URL) as client:
            try:
                csrf, session_id, headers = self._setup_session(client)
                page_old = self._start(client, headers, original_old)
                source_old = page_old["source_id"]
                thread_old, holder_old = self._background_post(
                    app, session_id, csrf,
                    "/api/session/source-analysis",
                    {"request_id": request_id, "source_id": source_old})
                threads.append(thread_old)
                call_old = self._wait_provider_call(provider, 0, "C1 old")
                self.assertEqual(call_old.request["kind"],
                                 "source_analysis")
                self.assertEqual(call_old.request["request_id"], request_id)
                self.assertEqual(call_old.request["source_id"], source_old)
                self.assertEqual(call_old.request["source_sha256"],
                                 page_old["source_sha256"])
                self.assertFalse(call_old.event.is_set())
                snapshot = self._snapshot(client)
                entry = self._request_from(snapshot, request_id)
                self.assertIsNotNone(entry, snapshot)
                self.assertEqual(entry["kind"], "source")
                self.assertEqual(entry["state"], "working")
                self.assertEqual(self._original_bytes(client), original_old)
                observed = self._observe(app, session_id)
                generation_old = observed["generation"]
                key_old = (generation_old, request_id)
                self.assertEqual(observed["worker_keys"], [key_old])
                self.assertIs(observed["contexts"].get(key_old),
                              call_old.context)
                self.assertEqual(observed["operations"], 1)
                self.assertEqual(observed["inflight_provider"], 1)

                page_new = self._start(client, headers, original_new)
                source_new = page_new["source_id"]
                self.assertNotEqual(source_new, source_old, page_new)
                self.assertNotEqual(page_new["source_sha256"],
                                    page_old["source_sha256"])
                self.assertTrue(call_old.event.is_set())
                observed = self._observe(app, session_id)
                generation_new = observed["generation"]
                self.assertGreater(generation_new, generation_old)
                self.assertIn(key_old, observed["worker_keys"])
                self.assertIs(observed["contexts"].get(key_old),
                              call_old.context)
                self.assertEqual(observed["inflight_provider"], 1)
                snapshot = self._snapshot(client)
                self.assertEqual(snapshot["source_id"], source_new)
                self.assertEqual(snapshot["requests"], [])
                self.assertEqual(snapshot["regions"], [])
                self.assertEqual(self._original_bytes(client), original_new)

                thread_new, holder_new = self._background_post(
                    app, session_id, csrf,
                    "/api/session/source-analysis",
                    {"request_id": request_id, "source_id": source_new})
                threads.append(thread_new)
                deadline = time.monotonic() + ENTER_TIMEOUT
                call_new = None
                while time.monotonic() < deadline:
                    candidate = provider.call_or_none(1)
                    if candidate is not None and candidate.entered.is_set():
                        call_new = candidate
                        break
                    if not thread_new.is_alive():
                        break
                    time.sleep(0.005)
                if call_new is None:
                    # Explicit-refusal branch, kept only if the live
                    # authority refuses reuse while old work is active:
                    # refusal must precede any second invocation, and a
                    # fresh current ID must then succeed. Silent ID
                    # substitution is never accepted.
                    refused = self._join(thread_new, holder_new, "C1 reuse")
                    self.assertNotEqual(refused.status_code, 201,
                                        refused.text)
                    self.assertEqual(provider.call_count, 1)
                    fresh_id = _fresh_request_id()
                    self.assertNotEqual(fresh_id, request_id)
                    thread_fresh, holder_fresh = self._background_post(
                        app, session_id, csrf,
                        "/api/session/source-analysis",
                        {"request_id": fresh_id, "source_id": source_new})
                    threads.append(thread_fresh)
                    call_fresh = self._wait_provider_call(
                        provider, 1, "C1 fresh-id fallback")
                    call_old.release.set()
                    late_old = self._join(thread_old, holder_old, "C1 old")
                    self.assertNotEqual(late_old.status_code, 201,
                                        late_old.text)
                    call_fresh.release.set()
                    fresh_done = self._join(
                        thread_fresh, holder_fresh, "C1 fresh")
                    self.assertEqual(fresh_done.status_code, 201,
                                     fresh_done.text)
                    self.assertEqual(fresh_done.json()["request_id"],
                                     fresh_id)
                    self._assert_zero_ownership(app, session_id, "C1 fresh")
                    self._record("C1", branch="refused-reuse",
                                 refusal_status=refused.status_code,
                                 fresh_status=fresh_done.status_code)
                    return

                # Supported branch in current production: the reused
                # public ID is a distinct generation-qualified charge.
                self.assertIsNot(call_new.context, call_old.context)
                self.assertIsNot(call_new.event, call_old.event)
                self.assertFalse(call_new.event.is_set())
                self.assertEqual(call_new.request["request_id"], request_id)
                self.assertEqual(call_new.request["source_id"], source_new)
                self.assertEqual(call_new.request["source_sha256"],
                                 page_new["source_sha256"])
                key_new = (generation_new, request_id)
                self.assertNotEqual(key_new, key_old)
                observed = self._observe(app, session_id)
                self.assertEqual(observed["worker_keys"],
                                 sorted([key_old, key_new]))
                self.assertIs(observed["contexts"].get(key_old),
                              call_old.context)
                self.assertIs(observed["contexts"].get(key_new),
                              call_new.context)
                self.assertEqual(observed["operations"], 2)
                self.assertEqual(observed["inflight_provider"], 2)
                entry = self._request_from(
                    self._snapshot(client), request_id)
                self.assertIsNotNone(entry)
                self.assertEqual(entry["state"], "working")

                call_old.release.set()
                late_old = self._join(thread_old, holder_old, "C1 old")
                self.assertEqual(late_old.status_code, 409, late_old.text)
                self.assertEqual(
                    late_old.json()["detail"]["code"], "source_changed",
                    late_old.text)
                snapshot = self._snapshot(client)
                self.assertEqual(snapshot["source_id"], source_new)
                self.assertEqual(snapshot["regions"], [])
                entry = self._request_from(snapshot, request_id)
                self.assertIsNotNone(entry, snapshot)
                self.assertEqual(entry["state"], "working")
                self.assertFalse(call_new.event.is_set())
                observed = self._observe(app, session_id)
                self.assertEqual(observed["worker_keys"], [key_new])
                self.assertEqual(sorted(observed["contexts"]), [key_new])
                self.assertIs(observed["contexts"].get(key_new),
                              call_new.context)
                self.assertEqual(observed["operations"], 1)
                self.assertEqual(observed["inflight_provider"], 1)
                self.assertEqual(self._original_bytes(client), original_new)

                call_new.release.set()
                done_new = self._join(thread_new, holder_new, "C1 new")
                self.assertEqual(done_new.status_code, 201, done_new.text)
                self.assertEqual(done_new.json()["request_id"], request_id)
                snapshot = done_new.json()["snapshot"]
                self.assertEqual(snapshot, self._snapshot(client))
                self.assertEqual(snapshot["source_id"], source_new)
                entry = self._request_from(snapshot, request_id)
                self.assertEqual(entry["kind"], "source")
                self.assertEqual(entry["state"], "terminal")
                self.assertEqual(entry["outcome"], "ok")
                self.assertEqual(len(snapshot["regions"]), 1)
                region = snapshot["regions"][0]
                self.assertEqual(region["request_id"], request_id)
                self.assertEqual(region["external_section_id"],
                                 "new-section-1")
                self.assertEqual(region["raw_text"], NEW_TEXT)
                self.assertNotEqual(region["raw_text"], OLD_TEXT)
                raw_new = self._raw(client, request_id)
                self.assertEqual(raw_new.status_code, 200, raw_new.text)
                self.assertEqual(raw_new.json()["kind"], "source")
                self.assertEqual(raw_new.json()["provider_result"],
                                 new_result)
                self.assertEqual(provider.call_count, 2)
                self.assertEqual(self._original_bytes(client), original_new)
                self._assert_zero_ownership(app, session_id, "C1 final")
                self._record(
                    "C1", branch="distinct-generation",
                    generation_old=generation_old,
                    generation_new=generation_new,
                    late_old_status=late_old.status_code,
                    late_old_code=late_old.json()["detail"]["code"],
                    new_status=done_new.status_code,
                    provider_calls=provider.call_count)
            finally:
                provider.release_all()
                for thread in threads:
                    thread.join(timeout=JOIN_TIMEOUT)

    # -- C2: refused mutations preserve work; commits differ -----
    def _do_writer(self, client, headers, writer, *, request_id,
                   source_id, replacement):
        if writer == "cancel":
            return client.post(
                "/api/session/requests/"
                + quote(request_id, safe="") + "/cancel",
                json={"source_id": source_id}, headers=headers)
        if writer == "settings":
            return client.put(
                "/api/session/settings",
                json={"grade": CHANGED_GRADE,
                      "objective": CHANGED_OBJECTIVE,
                      "rights_ref": fixtures.RIGHTS_REF},
                headers=headers)
        self.assertEqual(writer, "replace_source")
        return client.post(
            "/api/session/start", params={"page_index": 0},
            content=replacement,
            headers={**headers,
                     "Content-Type": "application/octet-stream"})

    def _faulty_writer(self, predicate, do_request):
        """Run one writer with a one-shot JSONResponse fault seam.

        Only ``http_app.JSONResponse`` is wrapped, only while the
        provider remains held, and only the first construction
        matching this writer's genuine success-response shape
        raises the fixed synthetic error. Restored in ``finally``.
        """
        original = http_app.JSONResponse
        fired = []

        def flaky(*args, **kwargs):
            if not fired:
                status = kwargs.get("status_code")
                content = kwargs.get("content")
                if args and content is None:
                    content = args[0]
                if predicate(status, content):
                    fired.append(status)
                    raise RuntimeError(SYNTHETIC_FAULT)
            return original(*args, **kwargs)

        http_app.JSONResponse = flaky
        try:
            response = do_request()
        finally:
            http_app.JSONResponse = original
        return response, bool(fired)

    def test_refused_closures_leave_active_operation_live(self):
        for writer in ("cancel", "settings", "replace_source"):
            for case in ("refused", "committed"):
                with self.subTest(writer=writer, case=case):
                    self._run_c2_case(writer, case)

    def _run_c2_case(self, writer, case):
        # A real grade/objective change, never the baseline no-op.
        self.assertNotEqual(CHANGED_GRADE, fixtures.GRADE)
        self.assertNotEqual(CHANGED_OBJECTIVE, fixtures.OBJECTIVE)
        label = f"C2/{writer}/{case}"
        envelope = copy.deepcopy(fixtures.SOURCE_RESULT)
        provider = _BlockingProvider([envelope])
        app = create_app(provider=provider)
        seed = abs(hash((writer, case))) % 150
        original = _png_bytes((40 + seed, 60, 80, 255))
        replacement = _png_bytes((90, 20 + seed, 160, 255), size=(7, 5))
        request_id = _fresh_request_id()
        raise_errors = case == "committed"
        with TestClient(app, base_url=BASE_URL,
                        raise_server_exceptions=raise_errors) as client:
            thread = None
            holder = None
            try:
                csrf, session_id, headers = self._setup_session(client)
                page = self._start(client, headers, original)
                source_id = page["source_id"]
                thread, holder = self._background_post(
                    app, session_id, csrf,
                    "/api/session/source-analysis",
                    {"request_id": request_id, "source_id": source_id})
                call = self._wait_provider_call(provider, 0, label)
                self.assertEqual(call.request["request_id"], request_id)
                self.assertEqual(call.request["source_id"], source_id)
                self.assertFalse(call.event.is_set())
                baseline_snapshot = self._snapshot(client)
                baseline_entry = self._request_from(
                    baseline_snapshot, request_id)
                self.assertIsNotNone(baseline_entry, baseline_snapshot)
                self.assertEqual(baseline_entry["state"], "working")
                self.assertEqual(baseline_snapshot["regions"], [])
                self.assertEqual(self._original_bytes(client), original)
                baseline = self._observe(app, session_id)
                key = (baseline["generation"], request_id)
                self.assertEqual(baseline["worker_keys"], [key])
                self.assertIs(baseline["contexts"].get(key), call.context)
                bound_view = baseline["requests"][request_id]
                bound_settings = bound_view.bound_settings_revision
                baseline_revisions = (
                    baseline["snapshot"].source_revision,
                    baseline["snapshot"].settings_revision)
                if case == "refused":
                    self._c2_refused(
                        client, headers, writer, label=label,
                        request_id=request_id, source_id=source_id,
                        replacement=replacement, call=call,
                        baseline_snapshot=baseline_snapshot,
                        baseline=baseline, key=key, original=original,
                        app=app, session_id=session_id)
                else:
                    self._c2_committed(
                        client, headers, writer, label=label,
                        request_id=request_id, source_id=source_id,
                        replacement=replacement, call=call,
                        baseline_snapshot=baseline_snapshot,
                        baseline=baseline, baseline_revisions=baseline_revisions,
                        bound_settings=bound_settings, key=key,
                        original=original, app=app,
                        session_id=session_id, envelope=envelope)
                # Same admitted operation finishes exactly once.
                call.release.set()
                finished = self._join(thread, holder, label)
                thread = None
                self._c2_finish(
                    client, writer, case, finished, label=label,
                    request_id=request_id, source_id=source_id,
                    original=original, replacement=replacement,
                    envelope=envelope, app=app, session_id=session_id,
                    provider=provider)
            finally:
                provider.release_all()
                if thread is not None:
                    thread.join(timeout=JOIN_TIMEOUT)

    def _c2_refused(self, client, headers, writer, *, label, request_id,
                    source_id, replacement, call, baseline_snapshot,
                    baseline, key, original, app, session_id):
        if writer == "cancel":
            def predicate(status, content):
                return (status == 200 and isinstance(content, dict)
                        and content.get("request_id") == request_id
                        and "snapshot" in content)
        elif writer == "settings":
            def predicate(status, content):
                return (status == 200 and isinstance(content, dict)
                        and content.get("grade") == CHANGED_GRADE
                        and content.get("objective") == CHANGED_OBJECTIVE)
        else:
            def predicate(status, content):
                return (status == 201 and isinstance(content, dict)
                        and "source_id" in content
                        and "source_sha256" in content)
        response, fired = self._faulty_writer(
            predicate,
            lambda: self._do_writer(
                client, headers, writer, request_id=request_id,
                source_id=source_id, replacement=replacement))
        self.assertTrue(fired, label + ": synthetic fault never fired")
        self.assertEqual(response.status_code, 500, response.text)
        # No mutation or signal may have occurred.
        self.assertFalse(call.event.is_set(), label)
        self.assertEqual(self._snapshot(client), baseline_snapshot, label)
        self.assertEqual(self._original_bytes(client), original, label)
        observed = self._observe(app, session_id)
        self.assertEqual(observed["generation"], baseline["generation"])
        self.assertEqual(observed["worker_keys"], [key])
        self.assertIs(observed["contexts"].get(key), call.context)
        self.assertEqual(observed["pending"], baseline["pending"])
        self.assertEqual(observed["source_id"], source_id)
        self.assertEqual(observed["snapshot"].grade, fixtures.GRADE)
        self.assertEqual(observed["snapshot"].objective,
                         fixtures.OBJECTIVE)
        self.assertEqual(
            (observed["snapshot"].source_revision,
             observed["snapshot"].settings_revision),
            (baseline["snapshot"].source_revision,
             baseline["snapshot"].settings_revision))
        self.assertEqual(observed["requests"][request_id].state, "working")
        self._record(label, fault_fired=fired,
                     writer_status=response.status_code,
                     event_set=False)

    def _c2_committed(self, client, headers, writer, *, label, request_id,
                      source_id, replacement, call, baseline_snapshot,
                      baseline, baseline_revisions, bound_settings, key,
                      original, app, session_id, envelope):
        response = self._do_writer(
            client, headers, writer, request_id=request_id,
            source_id=source_id, replacement=replacement)
        observed = self._observe(app, session_id)
        if writer == "cancel":
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["request_id"], request_id)
            self.assertEqual(response.json()["source_id"], source_id)
            ack = self._request_from(
                response.json()["snapshot"], request_id)
            self.assertEqual(ack["state"], "terminal")
            self.assertEqual(ack["outcome"], "cancelled")
            self.assertTrue(call.event.is_set(), label)
            self.assertEqual(observed["worker_keys"], [key])
            self.assertEqual(self._snapshot(client)["regions"], [])
            self.assertEqual(self._original_bytes(client), original)
        elif writer == "settings":
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), {
                "grade": CHANGED_GRADE, "objective": CHANGED_OBJECTIVE,
                "rights_ref": fixtures.RIGHTS_REF})
            snapshot = self._snapshot(client)
            self.assertEqual(snapshot["grade"], CHANGED_GRADE)
            self.assertEqual(snapshot["objective"], CHANGED_OBJECTIVE)
            self.assertEqual(snapshot["settings_revision"],
                             baseline_revisions[1] + 1)
            self.assertEqual(snapshot["source_revision"],
                             baseline_revisions[0])
            entry = self._request_from(snapshot, request_id)
            self.assertEqual(entry["state"], "working")
            self.assertEqual(
                observed["requests"][request_id].bound_settings_revision,
                bound_settings)
            self.assertNotEqual(bound_settings,
                                snapshot["settings_revision"])
            # Oracle correction: a settings commit makes the request
            # revision-stale; it does not signal its event or close it.
            self.assertFalse(call.event.is_set(), label)
            self.assertEqual(observed["worker_keys"], [key])
            self.assertEqual(self._original_bytes(client), original)
        else:
            self.assertEqual(response.status_code, 201, response.text)
            new_source = response.json()["source_id"]
            self.assertNotEqual(new_source, source_id, response.text)
            self.assertGreater(observed["generation"],
                               baseline["generation"])
            self.assertEqual(observed["source_id"], new_source)
            self.assertTrue(call.event.is_set(), label)
            self.assertEqual(observed["worker_keys"], [key])
            self.assertIs(observed["contexts"].get(key), call.context)
            snapshot = self._snapshot(client)
            self.assertEqual(snapshot["source_id"], new_source)
            self.assertEqual(snapshot["requests"], [])
            self.assertEqual(snapshot["regions"], [])
            self.assertEqual(self._original_bytes(client), replacement)
        self._record(label, writer_status=response.status_code,
                     event_set=bool(call.event.is_set()),
                     generation=observed["generation"])

    def _c2_finish(self, client, writer, case, finished, *, label,
                   request_id, source_id, original, replacement, envelope,
                   app, session_id, provider):
        self.assertEqual(provider.call_count, 1, label)
        snapshot = self._snapshot(client)
        if case == "refused":
            # The same admitted operation completes normally.
            self.assertEqual(finished.status_code, 201, finished.text)
            self.assertEqual(finished.json()["request_id"], request_id)
            self.assertEqual(finished.json()["snapshot"], snapshot)
            self.assertEqual(snapshot["source_id"], source_id)
            self.assertEqual(snapshot["grade"], fixtures.GRADE)
            self.assertEqual(snapshot["objective"], fixtures.OBJECTIVE)
            entry = self._request_from(snapshot, request_id)
            self.assertEqual(entry["state"], "terminal")
            self.assertEqual(entry["outcome"], "ok")
            self.assertEqual(len(snapshot["regions"]), 1)
            self.assertEqual(snapshot["regions"][0]["raw_text"],
                             fixtures.RAW_TEXT)
            self.assertEqual(snapshot["regions"][0]["request_id"],
                             request_id)
            raw_state = self._assert_raw_if_retained(
                client, request_id, envelope)
            self.assertEqual(raw_state, "retained_exact")
            self.assertEqual(self._original_bytes(client), original)
        elif writer == "cancel":
            self.assertEqual(finished.status_code, 409, finished.text)
            self.assertEqual(finished.json()["detail"]["code"],
                             "request_closed", finished.text)
            entry = self._request_from(snapshot, request_id)
            self.assertEqual(entry["state"], "terminal")
            self.assertEqual(entry["outcome"], "cancelled")
            self.assertEqual(snapshot["regions"], [])
            self.assertEqual(snapshot["source_id"], source_id)
            self._assert_raw_if_retained(client, request_id, envelope)
            self.assertEqual(self._original_bytes(client), original)
        elif writer == "settings":
            self.assertEqual(finished.status_code, 409, finished.text)
            self.assertEqual(finished.json()["detail"]["code"],
                             "request_stale", finished.text)
            entry = self._request_from(snapshot, request_id)
            self.assertEqual(entry["state"], "terminal")
            self.assertEqual(entry["outcome"], "stale")
            self.assertEqual(snapshot["regions"], [])
            self.assertEqual(snapshot["grade"], CHANGED_GRADE)
            self.assertEqual(snapshot["objective"], CHANGED_OBJECTIVE)
            self.assertEqual(snapshot["source_id"], source_id)
            self._assert_raw_if_retained(client, request_id, envelope)
            self.assertEqual(self._original_bytes(client), original)
        else:
            self.assertEqual(finished.status_code, 409, finished.text)
            self.assertEqual(finished.json()["detail"]["code"],
                             "source_changed", finished.text)
            self.assertNotEqual(snapshot["source_id"], source_id)
            self.assertEqual(snapshot["requests"], [])
            self.assertEqual(snapshot["regions"], [])
            self.assertEqual(self._original_bytes(client), replacement)
        self._assert_zero_ownership(app, session_id, label + " final")
        prior = dict(self.EVIDENCE.get(label, {}))
        prior.update(finished_status=finished.status_code,
                     provider_calls=provider.call_count)
        self._record(label, **prior)

    # -- C3: handler cancellation during/after result work -------
    def test_result_phase_handler_cancellation_preserves_truth(self):
        # Each subcase directly cancels the real authenticated route
        # handler as an asyncio Task; this is not an ASGI disconnect
        # simulation. Setup and readback stay on real HTTP.
        for phase in ("before_commit", "after_commit"):
            for cancels in (0, 1, 2):
                with self.subTest(phase=phase, cancels=cancels):
                    self._run_c3_case(phase, cancels)

    def _direct_request(self, session_id, csrf, request_id, source_id):
        body = json.dumps(
            {"request_id": request_id, "source_id": source_id}).encode(
                "utf-8")
        headers = [
            (b"host", b"127.0.0.1:8000"),
            (b"origin", BASE_URL.encode("utf-8")),
            (b"x-csrf-token", csrf.encode("utf-8")),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cookie", ("teacher_session=" + session_id).encode("utf-8")),
        ]
        scope = {
            "type": "http", "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": "/api/session/source-analysis",
            "raw_path": b"/api/session/source-analysis",
            "query_string": b"", "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
        }
        sent = {"done": False}

        async def receive():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": body,
                        "more_body": False}
            return {"type": "http.disconnect"}

        return Request(scope, receive)

    def _run_c3_case(self, phase, cancels):
        label = f"C3/{phase}/cancels={cancels}"
        envelope = copy.deepcopy(fixtures.SOURCE_RESULT)
        provider = _BlockingProvider([envelope])
        provider.release_all()  # the valid result returns immediately
        app = create_app(provider=provider)
        values = _closure_values(app)
        if phase == "before_commit":
            target = values.get("__fn__:process_provider_result")
            mode = "entry"
        else:
            target = values.get("__fn__:publish_provider_result")
            mode = "return_success"
        if target is None:
            self._unresolved(label + ": worker seam function not locatable")
        endpoint = _find_endpoint(app, "/api/session/source-analysis")
        if endpoint is None:
            self._unresolved(label + ": source-analysis endpoint missing")
        run_owned_fn = values.get("__fn__:run_owned")
        if run_owned_fn is None:
            self._unresolved(label + ": run_owned function not locatable")
        previous_thread_trace = (
            threading.gettrace() if hasattr(threading, "gettrace") else None)
        previous_profile = sys.getprofile()
        hold = _TraceHold(target.__code__, mode, previous_thread_trace)
        c3_witness = _C3CancellationWitness(run_owned_fn)
        hold._c3_witness = c3_witness
        if c3_witness.instrumentation_error is not None:
            self._unresolved(
                label + ": " + c3_witness.instrumentation_error)
        original = _png_bytes((70, 110, 30, 255))
        request_id = _fresh_request_id()
        holder = {"started": threading.Event(), "loop": None, "task": None,
                  "response": None, "cancelled": False, "error": None}
        # Installed before the client/app threads and the handler
        # thread start, so worker threads inherit the observer. The
        # previous trace is chained for other frames and restored in
        # finally; the parent profile hook is never replaced.
        threading.settrace(hold.global_trace)
        try:
            with TestClient(app, base_url=BASE_URL) as client:
                csrf, session_id, headers = self._setup_session(client)
                page = self._start(client, headers, original)
                source_id = page["source_id"]
                request_obj = self._direct_request(
                    session_id, csrf, request_id, source_id)

                async def amain():
                    task = asyncio.create_task(endpoint(request_obj))
                    holder["loop"] = asyncio.get_running_loop()
                    holder["task"] = task
                    holder["started"].set()
                    try:
                        holder["response"] = await task
                    except asyncio.CancelledError:
                        holder["cancelled"] = True
                    holder["task_cancelled"] = task.cancelled()

                def run_handler():
                    sys.settrace(hold.global_trace)
                    try:
                        asyncio.run(amain())
                    except Exception as error:
                        holder["error"] = error

                thread = threading.Thread(
                    target=run_handler, name="cancellation-handler",
                    daemon=True)
                thread.start()
                try:
                    if not holder["started"].wait(timeout=READY_TIMEOUT):
                        self._unresolved(label + ": handler never started")
                    handler_loop = holder["loop"]
                    handler_task = holder["task"]
                    if handler_loop is None or handler_task is None:
                        self._unresolved(
                            label + ": original route Task not locatable")
                    c3_witness.bind_task(handler_task)
                    if not hold.ready.wait(timeout=READY_TIMEOUT):
                        self._unresolved(
                            label + ": worker seam never reached")
                    self.assertEqual(hold.captured_request_id, request_id,
                                     label)
                    call = self._wait_provider_call(provider, 0, label)
                    self.assertEqual(provider.call_count, 1, label)

                    def require_ack(sequence):
                        facts = c3_witness.snapshot()
                        ack = facts["acks"].get(sequence)
                        if (facts["errors"] or ack is None
                                or ack.get("task_id") != id(handler_task)
                                or ack.get("returned") is not True
                                or ack.get("after")
                                != ack.get("before", -1) + 1):
                            self._unresolved(
                                label + ": cancellation "
                                + str(sequence)
                                + " was not affirmatively accepted: "
                                + repr(facts))

                    def require_before_release():
                        facts = c3_witness.snapshot()
                        expected = list(range(1, cancels + 1))
                        if (sorted(facts["scheduled"]) != expected
                                or sorted(facts["acks"]) != expected
                                or sorted(facts["deliveries"]) != expected
                                or facts["errors"] or facts["unexpected"]):
                            self._unresolved(
                                label + ": cancellation witnesses do "
                                "not match the requested case: "
                                + repr(facts))
                        deliveries = facts["deliveries"]
                        for sequence in expected:
                            delivery = deliveries[sequence]
                            if (delivery["task_id"] != id(handler_task)
                                    or delivery["current_task_id"]
                                    != id(handler_task)
                                    or delivery["code_name"] != "run_owned"
                                    or delivery["expected_line"] not in (
                                        delivery["line"],
                                        delivery["last_line"])):
                                self._unresolved(
                                    label + ": cancellation "
                                    + str(sequence)
                                    + " delivery identity/site mismatch: "
                                    + repr(facts))
                        if cancels == 2 and (
                                deliveries[1]["frame_id"]
                                != deliveries[2]["frame_id"]
                                or deliveries[1]["exception"]
                                is deliveries[2]["exception"]):
                            self._unresolved(
                                label + ": second delivery is not a "
                                "distinct drain delivery: " + repr(facts))
                        if (holder["loop"] is not handler_loop
                                or holder["task"] is not handler_task
                                or handler_task.done()
                                or not thread.is_alive()):
                            self._unresolved(
                                label + ": original handler is not "
                                "still pending before release")
                        if (not hold.ready.is_set() or not hold.hold_active
                                or hold.release.is_set() or hold.disarmed
                                or hold.hold_ceiling_hit
                                or hold.hold_started_at is None
                                or time.monotonic() - hold.hold_started_at
                                >= HOLD_CEILING):
                            self._unresolved(
                                label + ": worker hold is not active "
                                "and unreleased before release")
                        sessions = values.get("sessions")
                        registry_lock = values.get("registry_lock")
                        operations_registry = (
                            values.get("operations") or {})
                        if sessions is None or registry_lock is None:
                            self._unresolved(
                                label + ": session/registry state not "
                                "locatable before release")
                        with registry_lock:
                            state = sessions.get(session_id)
                        if state is None:
                            self._unresolved(
                                label + ": own session state not "
                                "locatable before release")
                        with state.lock:
                            with registry_lock:
                                contexts = dict(state.provider_contexts)
                                registered = list(
                                    operations_registry.values())
                        original_context = call.context
                        identity_keys = [
                            key for key, context in contexts.items()
                            if context is original_context]
                        identity_key = (
                            identity_keys[0]
                            if len(identity_keys) == 1 else None)
                        matches = [
                            operation for operation in registered
                            if isinstance(operation, dict)
                            and operation.get("context")
                            is original_context]
                        if (original_context is None
                                or identity_key is None
                                or not isinstance(identity_key, tuple)
                                or len(identity_key) != 2
                                or not isinstance(identity_key[0], int)
                                or identity_key[0]
                                != state.source_generation
                                or identity_key[1] != request_id
                                or getattr(original_context,
                                           "cancel_event", None)
                                is not call.event
                                or len(matches) != 1
                                or matches[0].get("settled") is None
                                or matches[0]["settled"].is_set()):
                            self._unresolved(
                                label + ": registered operation for "
                                "the invocation context is not present "
                                "and unsettled before release")

                    committed_snapshot = self._c3_at_ready(
                        client, phase, label=label, request_id=request_id,
                        source_id=source_id, envelope=envelope,
                        original=original, hold=hold, call=call)
                    if cancels:
                        loop = holder["loop"]
                        task = holder["task"]
                        self.assertIsNotNone(loop, label)
                        self.assertIsNotNone(task, label)
                        if (loop is not handler_loop
                                or task is not handler_task):
                            self._unresolved(
                                label + ": original route Task changed "
                                "before cancellation")
                        c3_witness.begin_request(1)
                        loop.call_soon_threadsafe(
                            c3_witness.request_cancel, 1)
                        if not c3_witness.wait_ack(1, ENTER_TIMEOUT):
                            self._unresolved(
                                label + ": first cancellation was not "
                                "acknowledged on the owning loop")
                        require_ack(1)
                        if not c3_witness.wait_delivery(
                                1, ENTER_TIMEOUT):
                            self._unresolved(
                                label + ": first cancellation was not "
                                "delivered at the initial shield await: "
                                + repr(c3_witness.snapshot()))
                        # Affirmative readiness that owned cleanup has
                        # begun (the shared context closed) while the
                        # worker is still held; a second cancel then
                        # interrupts the drain, not empty air.
                        self.assertTrue(
                            call.event.wait(timeout=ENTER_TIMEOUT),
                            label + ": cancellation never closed context")
                        if cancels == 2:
                            c3_witness.begin_request(2)
                            loop.call_soon_threadsafe(
                                c3_witness.request_cancel, 2)
                            if not c3_witness.wait_ack(2, ENTER_TIMEOUT):
                                self._unresolved(
                                    label + ": second cancellation was "
                                    "not acknowledged on the owning loop")
                            require_ack(2)
                            if not c3_witness.wait_delivery(
                                    2, ENTER_TIMEOUT):
                                self._unresolved(
                                    label + ": second cancellation was "
                                    "not delivered at the drain shield "
                                    "await: "
                                    + repr(c3_witness.snapshot()))
                    require_before_release()
                finally:
                    # Release worker processing before joining, so the
                    # owned handler cleanup can complete and only then
                    # propagate cancellation.
                    hold.release.set()
                    hold.disarmed = True
                    thread.join(timeout=JOIN_TIMEOUT)
                self.assertFalse(
                    thread.is_alive(),
                    "instrumentation-unresolved: " + label
                    + " handler thread did not join")
                self.assertIsNone(holder["error"], repr(holder["error"]))
                self._c3_finish(
                    client, phase, cancels, holder,
                    committed_snapshot=committed_snapshot, label=label,
                    request_id=request_id, source_id=source_id,
                    envelope=envelope, original=original, call=call,
                    app=app, session_id=session_id, provider=provider)
        finally:
            hold.release.set()
            hold.disarmed = True
            threading.settrace(previous_thread_trace)
            # sys.setprofile was never called; previous_profile is
            # recorded only to keep that boundary explicit.
            _ = previous_profile

    def _c3_at_ready(self, client, phase, *, label, request_id, source_id,
                     envelope, original, hold, call):
        """Prove the pre/post-commit fact at the held seam."""
        snapshot = self._snapshot(client)
        entry = self._request_from(snapshot, request_id)
        self.assertIsNotNone(entry, label)
        self.assertEqual(self._original_bytes(client), original, label)
        if phase == "before_commit":
            # process_provider_result entry: nothing committed yet.
            self.assertEqual(entry["state"], "working", label)
            self.assertEqual(snapshot["regions"], [], label)
            self.assertFalse(call.event.is_set(), label)
            return None
        # publish_provider_result success return: the authoritative
        # request/raw/content is already committed and the outer
        # seam holds no session lock, so this readback is safe.
        self.assertEqual(hold.captured_return, ("response", 201), label)
        self.assertEqual(entry["state"], "terminal", label)
        self.assertEqual(entry["outcome"], "ok", label)
        self.assertEqual(len(snapshot["regions"]), 1, label)
        self.assertEqual(snapshot["regions"][0]["raw_text"],
                         fixtures.RAW_TEXT, label)
        raw = self._raw(client, request_id)
        self.assertEqual(raw.status_code, 200, raw.text)
        self.assertEqual(raw.json()["provider_result"], envelope)
        return snapshot

    def _c3_finish(self, client, phase, cancels, holder, *,
                   committed_snapshot, label, request_id, source_id,
                   envelope, original, call, app, session_id, provider):
        self.assertEqual(provider.call_count, 1, label)
        snapshot = self._snapshot(client)
        entry = self._request_from(snapshot, request_id)
        self.assertIsNotNone(entry, label)
        self.assertEqual(snapshot["source_id"], source_id, label)
        self.assertEqual(
            sum(1 for item in snapshot["requests"]
                if item["request_id"] == request_id), 1, label)
        self.assertEqual(self._original_bytes(client), original, label)
        if cancels == 0:
            self.assertFalse(holder["cancelled"], label)
            response = holder["response"]
            self.assertIsNotNone(response, label)
            self.assertEqual(response.status_code, 201, label)
            body = json.loads(response.body.decode("utf-8"))
            self.assertEqual(body["request_id"], request_id)
            self.assertEqual(body["snapshot"], snapshot)
            self.assertEqual(entry["state"], "terminal")
            self.assertEqual(entry["outcome"], "ok")
            self.assertEqual(len(snapshot["regions"]), 1)
            self.assertEqual(
                self._assert_raw_if_retained(client, request_id, envelope),
                "retained_exact")
            outcome = "ok"
        elif phase == "before_commit":
            self.assertTrue(holder["cancelled"], label)
            self.assertTrue(holder.get("task_cancelled"), label)
            self.assertIsNone(holder["response"], label)
            # The identical invocation context closed.
            self.assertTrue(call.event.is_set(), label)
            status = client.get(
                "/api/session/requests/"
                + quote(request_id, safe="") + "/status",
                params={"source_id": source_id})
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.json()["request"]["outcome"],
                             "cancelled", status.text)
            self.assertEqual(entry["state"], "terminal")
            self.assertEqual(entry["outcome"], "cancelled")
            self.assertEqual(snapshot["regions"], [])
            self._assert_raw_if_retained(client, request_id, envelope)
            outcome = "cancelled"
        else:
            # Cancellation propagated to the awaiting handler, but the
            # already-committed success is retained exactly.
            self.assertTrue(holder["cancelled"], label)
            self.assertTrue(holder.get("task_cancelled"), label)
            self.assertEqual(snapshot, committed_snapshot, label)
            self.assertEqual(entry["state"], "terminal")
            self.assertEqual(entry["outcome"], "ok")
            self.assertEqual(len(snapshot["regions"]), 1)
            self.assertEqual(snapshot["regions"][0]["raw_text"],
                             fixtures.RAW_TEXT)
            self.assertEqual(
                self._assert_raw_if_retained(client, request_id, envelope),
                "retained_exact")
            status = client.get(
                "/api/session/requests/"
                + quote(request_id, safe="") + "/status",
                params={"source_id": source_id})
            self.assertEqual(status.status_code, 200, status.text)
            status_body = status.json()
            self.assertEqual(status_body["request_id"], request_id)
            self.assertEqual(status_body["source_id"], source_id)
            self.assertEqual(status_body["request"]["state"], "terminal")
            self.assertEqual(status_body["request"]["outcome"], "ok")
            self.assertEqual(status_body["request"], entry)
            outcome = "ok"
        self._assert_zero_ownership(app, session_id, label + " final")
        self._record(label, phase=phase, cancels=cancels, outcome=outcome,
                     handler_cancelled=bool(holder["cancelled"]),
                     provider_calls=provider.call_count,
                     event_set=bool(call.event.is_set()))


if __name__ == "__main__":
    unittest.main()
