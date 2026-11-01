"""Focused HTTP operation-deadline tests (post-implementation).

Safety coverage only for the accepted HTTP/provider/lifetime
integration; not retroactive test-first proof and not a redesign.
Production is untouched. Every case uses an independent app,
session, and genuine authenticated ASGI HTTP through the internal
``create_app(provider=...)`` factory with an ordinary synthetic
one-argument provider (never Meta transport). The provider result
is the valid four-field ``{raw, model_name, response_id, outcome}``
envelope reused from ``test_http_teacher_flow`` as ``fixtures``.

Witness design (trace-only holds, no replacement of production):
- D1 holds the genuine ``invoke_provider`` entry at its trace
  ``call`` event, before its original ``context.remaining()``
  check, capturing the exact context. Executor, provider wrapper,
  clock, and context deadline are never replaced or mutated.
- D2 holds only the selected success ``publish_locked`` call
  (``final_context is not None``) before its real registry-lock
  acquisition. The test owns the actual registry lock from the
  app closure, then releases the phase hold so the original code
  blocks on that lock. Reaching acquisition is acknowledged by a
  trace ``line`` event for the ``with registry_lock`` line chosen
  from the staged function's AST. Session-to-registry lock order
  is preserved; no session read is attempted while the registry
  lock is held. ``publish_locked``, ``context.remaining``, lock
  methods, validation, and prepared results are never replaced.
- D3 holds one synthetic provider pending for at least 31 seconds
  from its acknowledged entry under the unmodified default
  policy, then completes successfully.

``threading.settrace`` is installed before the test client spawns
any thread (it only affects threads started afterwards), filters
by event and code identity before any filesystem work (the trace
path performs none), chains and restores any prior threading
trace hook, and never touches the profile hooks. Holds are one-shot, armed for one expected
request, bounded by finite ceilings, and released in ``finally``
before request threads are joined and the client lifespan exits.
No thread is a daemon cleanup path. The deadline witness is the
captured context's own monotonic deadline, never controller
dispatch or submit time. If a held phase is not reached while
the context is live, the fixture fails and that schedule is not
declared covered.
"""
import ast
import copy
import inspect
import pathlib
import sys
import textwrap
import threading
import time
import unittest
from urllib.parse import quote

_TESTS_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR))
sys.path.insert(0, str(_TESTS_DIR.parent / "src"))

import test_http_teacher_flow as fixtures  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

import teacher_demo.http_app as http_app_module  # noqa: E402
from teacher_demo.http_app import create_app  # noqa: E402
from teacher_demo.http_teacher_helpers import (  # noqa: E402
    snapshot_payload,
)

# Fixture parameters fixed before each operation. Short positive
# operation budgets for D1/D2 only; D3 keeps the shipped default.
_D1_PROVIDER_TIMEOUT_SECONDS = 6.0
_D2_PROVIDER_TIMEOUT_SECONDS = 8.0
_ACK_TIMEOUT_SECONDS = 10.0
_HOLD_CEILING_SECONDS = 20.0
_MIN_REMAINING_AT_HOLD_SECONDS = 1.0
_JOIN_TIMEOUT_SECONDS = 15.0
_SETTLE_TIMEOUT_SECONDS = 5.0
_LOCK_ACQUIRE_TIMEOUT_SECONDS = 5.0
_AT_LOCK_TIMEOUT_SECONDS = 5.0
_POLL_SECONDS = 0.02
_D3_PENDING_SECONDS = 31.0
_D3_ENTRY_TIMEOUT_SECONDS = 10.0
_D3_GATE_CEILING_SECONDS = 75.0
_D3_JOIN_TIMEOUT_SECONDS = 20.0


class _SyntheticSourceProvider:
    """Ordinary one-argument synthetic provider; never a default.

    Records each frozen request for lineage checks in the test
    thread (no assertions hidden inside the callable). When a
    gate event is supplied (D3), entry is acknowledged first and
    the body then waits for the test-owned gate within a finite
    ceiling so a failed test can never strand the worker.
    """

    def __init__(self, gate=None, gate_ceiling=None):
        self._lock = threading.Lock()
        self._requests = []
        self._gate = gate
        self._gate_ceiling = gate_ceiling
        self.entered = threading.Event()
        self.entered_at = None
        self.returned_at = None

    def __call__(self, request):
        self.entered_at = time.monotonic()
        with self._lock:
            self._requests.append(copy.deepcopy(dict(request)))
        self.entered.set()
        if self._gate is not None:
            self._gate.wait(timeout=self._gate_ceiling)
        self.returned_at = time.monotonic()
        return copy.deepcopy(fixtures.SOURCE_RESULT)

    def call_count(self):
        with self._lock:
            return len(self._requests)

    def recorded_requests(self):
        with self._lock:
            return copy.deepcopy(self._requests)


# One stable threading trace entry point for every hold in this
# module. A worker thread keeps the trace function it was first
# bootstrapped with, and threadpool workers can outlive one test
# client, so a per-case bound tracer could go stale on a reused
# worker. The stable function instead dispatches to whichever
# single hold is currently active (or to the prior hook), which
# makes every worker first started under any case in this module
# observe later cases too. Workers started before this module's
# first install in the same process have no such hook; a hold
# that never activates then fails the fixture explicitly.
_TRACE_DISPATCH = {"active": None, "prior": None}


def _stable_thread_trace(frame, event, arg):
    hold = _TRACE_DISPATCH["active"]
    if hold is not None:
        return hold._dispatch(frame, event, arg)
    prior = _TRACE_DISPATCH["prior"]
    if prior is not None:
        return prior(frame, event, arg)
    return None


class _TraceHold:
    """One-shot trace hold on one genuine nested function.

    ``mode="invoke"`` selects the ``invoke_provider`` call whose
    frozen ``request_id`` matches the expected request and holds
    it at the ``call`` event, before the first line runs.
    ``mode="publish"`` selects the ``publish_locked`` call with
    ``final_context is not None`` whose prepared
    ``provider_results`` contains the expected request, holds it
    before the body runs, then line-traces that one frame so the
    precomputed registry-lock line acknowledges acquisition.
    Non-target calls chain to any prior threading trace hook.
    """

    def __init__(self, target_function, *, mode, lock_line=None):
        self._code = target_function.__code__
        self._mode = mode
        self._lock_line = lock_line
        self._lock = threading.Lock()
        self._armed = False
        self._claimed = False
        self._expected_request_id = None
        self._prior_trace = None
        self._installed = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.at_lock = threading.Event()
        self.captured = {}
        self.hold_timed_out = False

    def install(self):
        # threading has no public trace getter; read the hook
        # slot best-effort so an existing hook is chained here
        # and restored afterwards. Profile hooks are untouched.
        # The installed function is the one stable dispatcher, so
        # workers bootstrapped under an earlier case in this
        # module keep dispatching to the currently active hold.
        current = getattr(threading, "_trace_hook", None)
        if current is _stable_thread_trace:
            self._prior_trace = _TRACE_DISPATCH["prior"]
        else:
            self._prior_trace = current
        _TRACE_DISPATCH["prior"] = self._prior_trace
        _TRACE_DISPATCH["active"] = self
        threading.settrace(_stable_thread_trace)
        self._installed = True

    def restore(self):
        if self._installed:
            if _TRACE_DISPATCH["active"] is self:
                _TRACE_DISPATCH["active"] = None
            threading.settrace(self._prior_trace)
            self._installed = False

    def expect(self, request_id):
        with self._lock:
            self._expected_request_id = request_id

    def arm(self):
        with self._lock:
            self._armed = True

    def disarm(self):
        with self._lock:
            self._armed = False
        self.release.set()

    def _prior_call(self, frame, event, arg):
        prior = self._prior_trace
        if prior is not None and prior is not _stable_thread_trace:
            return prior(frame, event, arg)
        return None

    def _select(self, frame, expected_request_id):
        try:
            locals_ = frame.f_locals
            if self._mode == "invoke":
                context = locals_.get("context")
                frozen = locals_.get("frozen")
                if context is None or frozen is None:
                    return False, {}
                request_id = frozen["request_id"]
                if request_id != expected_request_id:
                    return False, {}
                return True, {
                    "context": context,
                    "deadline": getattr(context, "deadline", None),
                    "cancel_event": getattr(
                        context, "cancel_event", None),
                    "request_id": request_id,
                    "frozen": frozen,
                }
            final_context = locals_.get("final_context")
            if final_context is None:
                return False, {}
            prepared = locals_.get("provider_results")
            if not isinstance(prepared, dict):
                return False, {}
            if expected_request_id not in prepared:
                return False, {}
            return True, {
                "context": final_context,
                "deadline": getattr(final_context, "deadline", None),
                "cancel_event": getattr(
                    final_context, "cancel_event", None),
                "request_id": expected_request_id,
                "state": locals_.get("state"),
                "workspace": locals_.get("workspace"),
                "provider_results": prepared,
            }
        except Exception:
            return False, {}

    def _dispatch(self, frame, event, arg):
        # Filter by event and code identity before any other work.
        if event != "call" or frame.f_code is not self._code:
            return self._prior_call(frame, event, arg)
        with self._lock:
            active = self._armed and not self._claimed
            expected = self._expected_request_id
        if not active:
            return self._prior_call(frame, event, arg)
        selected, captured = self._select(frame, expected)
        if not selected:
            return self._prior_call(frame, event, arg)
        with self._lock:
            if not self._armed or self._claimed:
                return self._prior_call(frame, event, arg)
            self._claimed = True
            self.captured = captured
        prior_local = self._prior_call(frame, event, arg)
        self.entered.set()
        if not self.release.wait(timeout=_HOLD_CEILING_SECONDS):
            self.hold_timed_out = True
        if self._mode == "publish":
            return self._make_local(prior_local)
        return prior_local

    def _make_local(self, prior_local):
        lock_line = self._lock_line
        at_lock = self.at_lock
        chain = {"prior": prior_local}

        def _local(frame, event, arg):
            if event == "line" and frame.f_lineno == lock_line:
                at_lock.set()
            prior = chain["prior"]
            if prior is not None:
                chain["prior"] = prior(frame, event, arg)
            if event == "return":
                return None
            return _local

        return _local


def _collect_closure(tc, app):
    """Collect one app's nested-function closure namespace.

    Walks function-typed closure cells transitively from route
    endpoints, staying inside ``teacher_demo.http_app`` so library
    closures are never followed. Needed names must denote one
    identical object everywhere they occur; a missing or
    conflicting name is a missing witness and fails the fixture.
    """
    queue = []
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if inspect.isfunction(endpoint):
            queue.append(endpoint)
    collected = {}
    required = {
        "sessions", "registry_lock", "operations",
        "invoke_provider", "publish_locked",
    }
    seen = set()
    while queue:
        func = queue.pop()
        if id(func) in seen:
            continue
        seen.add(id(func))
        if getattr(func, "__module__", None) != "teacher_demo.http_app":
            continue
        code = getattr(func, "__code__", None)
        cells = getattr(func, "__closure__", None)
        if code is None or not cells:
            continue
        for name, cell in zip(code.co_freevars, cells):
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if name in collected:
                if name in required and collected[name] is not value:
                    tc.fail(
                        "fixture: app closure name "
                        f"{name!r} is not one identical object; "
                        "deadline witness interface is unavailable")
            else:
                collected[name] = value
            if inspect.isfunction(value):
                queue.append(value)
    return collected


def _required_closure_value(tc, collected, name):
    if name not in collected:
        tc.fail(
            f"fixture: app closure does not expose {name!r}; "
            "deadline witness interface is unavailable, so this "
            "schedule is not covered")
    return collected[name]


def _parsed_function(tc, func, label):
    try:
        lines, start = inspect.getsourcelines(func)
        tree = ast.parse(textwrap.dedent("".join(lines)))
    except Exception as exc:
        tc.fail(
            f"fixture: staged {label} source/AST is unavailable "
            f"({exc!r}); deadline witness cannot be selected")
    if not tree.body or not isinstance(
            tree.body[0], ast.FunctionDef):
        tc.fail(
            f"fixture: staged {label} source is not one function; "
            "deadline witness cannot be selected")
    return tree.body[0], start


def _assert_invoke_shape(tc, invoke_function):
    """Provenance: entry hold precedes the real context check."""
    func_def, _start = _parsed_function(
        tc, invoke_function, "invoke_provider")
    first = func_def.body[0] if func_def.body else None
    call = first.value if isinstance(first, ast.Expr) else None
    attr = call.func if isinstance(call, ast.Call) else None
    if not isinstance(attr, ast.Attribute) or attr.attr != "remaining":
        tc.fail(
            "fixture: staged invoke_provider does not begin with "
            "the context remaining() check; an entry hold cannot "
            "prove delayed-entry expiry on this interface")


def _registry_lock_line(tc, publish_function):
    """Absolute line of ``with registry_lock`` from staged AST."""
    func_def, start = _parsed_function(
        tc, publish_function, "publish_locked")
    for node in ast.walk(func_def):
        if not isinstance(node, ast.With):
            continue
        for item in node.items:
            expr = item.context_expr
            if isinstance(expr, ast.Name) and expr.id == "registry_lock":
                return start + node.lineno - 1
    tc.fail(
        "fixture: staged publish_locked has no "
        "'with registry_lock' statement; final-lock witness "
        "cannot be selected")


def _bootstrap(tc, client):
    """Real authenticated session, settings, and Start journey."""
    created = client.post(
        "/api/sessions", json={},
        headers={"Origin": fixtures.BASE_URL})
    tc.assertEqual(created.status_code, 201, created.text)
    csrf = created.json()["csrf_token"]
    headers = {"Origin": fixtures.BASE_URL, "X-CSRF-Token": csrf}
    settings = client.put(
        "/api/session/settings",
        json={"grade": fixtures.GRADE,
              "objective": fixtures.OBJECTIVE,
              "rights_ref": fixtures.RIGHTS_REF},
        headers=headers)
    tc.assertEqual(settings.status_code, 200, settings.text)
    original = fixtures.synthetic_png()
    started = client.post(
        "/api/session/start", params={"page_index": 0},
        content=original,
        headers={**headers,
                 "Content-Type": "application/octet-stream"})
    tc.assertEqual(started.status_code, 201, started.text)
    return headers, started.json(), original


def _post_action(client, url, command, headers, box):
    try:
        box["response"] = client.post(
            url, json=command, headers=headers)
    except BaseException as exc:
        box["error"] = exc


def _start_action_thread(client, command, headers, box, name):
    thread = threading.Thread(
        target=_post_action,
        args=(client, "/api/session/source-analysis",
              command, headers, box),
        name=name, daemon=False)
    thread.start()
    return thread


def _join_or_fail(tc, thread, timeout, label):
    thread.join(timeout=timeout)
    if thread.is_alive():
        tc.fail(
            f"fixture: {label} did not finish within "
            f"the finite {timeout!r}s ceiling")


def _join_best_effort(thread, timeout):
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)


def _response_or_fail(tc, box, label):
    if "error" in box:
        tc.fail(f"{label} raised in its test thread: {box['error']!r}")
    if "response" not in box:
        tc.fail(f"{label} produced no HTTP response")
    return box["response"]


def _sole_state(tc, sessions, registry_lock):
    with registry_lock:
        values = list(sessions.values())
    tc.assertEqual(
        len(values), 1, "expected exactly one session in this app")
    return values[0]


def _accounting_snapshot(registry_lock, state, operations):
    """Detached accounting read under the registry authority.

    Caller must not already hold ``registry_lock`` (it is a plain
    lock). Callable charges are keyed
    ``(source_generation, request_id)``.
    """
    with registry_lock:
        return {
            "source_generation": state.source_generation,
            "worker_reservations": dict(state.worker_reservations),
            "provider_contexts": dict(state.provider_contexts),
            "operations": [
                (operation.get("context"),
                 operation["settled"].is_set())
                for operation in operations.values()
            ],
        }


def _charges_for(snapshot, request_id):
    return {
        key: cost
        for key, cost in snapshot["worker_reservations"].items()
        if key[1] == request_id
    }


def _contexts_for(snapshot, request_id):
    return {
        key: context
        for key, context in snapshot["provider_contexts"].items()
        if key[1] == request_id
    }


def _wait_settled(tc, registry_lock, state, operations, *,
                  request_id, captured_context=None):
    """Poll until actual-return accounting settles (finite).

    A logical terminal alone is not settlement: the callable
    charge, the registered context, and any operation for the
    captured context must all be gone or settled.
    """
    waiter = threading.Event()
    limit = time.monotonic() + _SETTLE_TIMEOUT_SECONDS
    while True:
        snapshot = _accounting_snapshot(
            registry_lock, state, operations)
        pending_ops = [
            settled for context, settled in snapshot["operations"]
            if captured_context is not None
            and context is captured_context and not settled
        ]
        if (not _charges_for(snapshot, request_id)
                and not _contexts_for(snapshot, request_id)
                and not pending_ops):
            return snapshot
        if time.monotonic() >= limit:
            tc.fail(
                "accounting did not settle after real work "
                f"returned for request {request_id!r}: "
                f"charges={_charges_for(snapshot, request_id)!r} "
                "contexts="
                f"{sorted(_contexts_for(snapshot, request_id))!r} "
                f"pending_operations={len(pending_ops)}")
        waiter.wait(timeout=_POLL_SECONDS)


def _get_snapshot(tc, client):
    fetched = client.get("/api/session/snapshot")
    tc.assertEqual(fetched.status_code, 200, fetched.text)
    return fetched.json()


def _get_original(tc, client):
    fetched = client.get("/api/session/page/original")
    tc.assertEqual(fetched.status_code, 200, fetched.text)
    return fetched.content


def _find_request(tc, snapshot, request_id):
    for item in snapshot["requests"]:
        if item["request_id"] == request_id:
            return item
    tc.fail(f"request {request_id!r} missing from snapshot")


def _assert_source_success(tc, client, response, *, request_id,
                           source_id, page, provider):
    tc.assertEqual(response.status_code, 201, response.text)
    body = response.json()
    tc.assertEqual(body["request_id"], request_id)
    tc.assertEqual(body.get("raw_retention"), "retained")
    snapshot = body["snapshot"]
    tc.assertEqual(snapshot["source_id"], source_id)
    tc.assertEqual(snapshot["grade"], fixtures.GRADE)
    tc.assertEqual(snapshot["objective"], fixtures.OBJECTIVE)
    tc.assertTrue(snapshot["source_analysis_terminal"])
    view = _find_request(tc, snapshot, request_id)
    tc.assertEqual(view["kind"], "source")
    tc.assertEqual(view["state"], "terminal")
    tc.assertEqual(view["outcome"], "ok")
    tc.assertEqual(len(snapshot["regions"]), 1)
    region = snapshot["regions"][0]
    tc.assertEqual(region["request_id"], request_id)
    tc.assertEqual(region["external_section_id"],
                   "synthetic-section-1")
    tc.assertEqual(region["raw_text"], fixtures.RAW_TEXT)
    tc.assertEqual(region["current_text"], fixtures.RAW_TEXT)
    tc.assertEqual(snapshot["questions"], [])
    tc.assertEqual(_get_snapshot(tc, client), snapshot)
    raw = client.get(
        "/api/session/requests/"
        f"{quote(request_id, safe='')}/raw")
    tc.assertEqual(raw.status_code, 200, raw.text)
    tc.assertEqual(raw.json()["kind"], "source")
    tc.assertEqual(raw.json()["provider_result"],
                   fixtures.SOURCE_RESULT)
    sent = provider.recorded_requests()
    tc.assertEqual(len(sent), 1)
    tc.assertEqual(sent[0]["kind"], "source_analysis")
    tc.assertEqual(sent[0]["request_id"], request_id)
    tc.assertEqual(sent[0]["source_id"], source_id)
    tc.assertEqual(sent[0]["source_sha256"], page["source_sha256"])
    tc.assertEqual(sent[0]["render_sha256"], page["render_sha256"])
    tc.assertEqual(sent[0]["grade"], fixtures.GRADE)
    tc.assertEqual(sent[0]["objective"], fixtures.OBJECTIVE)


def _assert_timeout_no_content(tc, snapshot, *, request_id,
                               source_id):
    tc.assertEqual(snapshot["source_id"], source_id)
    tc.assertEqual(snapshot["regions"], [])
    tc.assertEqual(snapshot["questions"], [])
    tc.assertEqual(snapshot["preview_items"], [])
    view = _find_request(tc, snapshot, request_id)
    tc.assertEqual(view["kind"], "source")
    tc.assertEqual(view["state"], "terminal")
    tc.assertEqual(view["outcome"], "timeout")


def _assert_final_timeout_state(tc, client, *, request_id,
                                source_id, expect_raw_absent):
    snapshot = _get_snapshot(tc, client)
    _assert_timeout_no_content(
        tc, snapshot, request_id=request_id, source_id=source_id)
    status = client.get(
        "/api/session/requests/"
        f"{quote(request_id, safe='')}/status",
        params={"source_id": source_id})
    tc.assertEqual(status.status_code, 200, status.text)
    tc.assertEqual(status.json()["request"]["state"], "terminal")
    tc.assertEqual(status.json()["request"]["outcome"], "timeout")
    raw = client.get(
        "/api/session/requests/"
        f"{quote(request_id, safe='')}/raw")
    if expect_raw_absent:
        # No provider envelope ever existed on this path, so the
        # actual no-content result retains no raw payload.
        tc.assertEqual(raw.status_code, 404, raw.text)
    elif raw.status_code == 200:
        # Bounded raw may be retained without admitting content;
        # retention must still be the exact synthetic envelope.
        tc.assertEqual(raw.json()["provider_result"],
                       fixtures.SOURCE_RESULT)
    else:
        tc.assertEqual(raw.status_code, 404, raw.text)
    return snapshot


def _wait_until_past(deadline):
    waiter = threading.Event()
    while time.monotonic() <= deadline:
        remaining = deadline - time.monotonic()
        waiter.wait(timeout=min(_POLL_SECONDS, max(remaining, 0.0)))


def _run_delayed_entry_case(tc, case):
    provider = _SyntheticSourceProvider()
    app = create_app(provider=provider)
    collected = _collect_closure(tc, app)
    invoke_function = _required_closure_value(
        tc, collected, "invoke_provider")
    _required_closure_value(tc, collected, "publish_locked")
    sessions = _required_closure_value(tc, collected, "sessions")
    registry_lock = _required_closure_value(
        tc, collected, "registry_lock")
    operations = _required_closure_value(
        tc, collected, "operations")
    _assert_invoke_shape(tc, invoke_function)
    hold = _TraceHold(invoke_function, mode="invoke")
    original_timeout = http_app_module.PROVIDER_TIMEOUT_SECONDS
    request_thread = None
    hold.install()
    try:
        http_app_module.PROVIDER_TIMEOUT_SECONDS = (
            _D1_PROVIDER_TIMEOUT_SECONDS)
        with TestClient(
                app, base_url=fixtures.BASE_URL) as client:
            headers, page, _original = _bootstrap(tc, client)
            state = _sole_state(tc, sessions, registry_lock)
            command = fixtures.operation_command(page["source_id"])
            request_id = command["request_id"]
            tc.assertEqual(len(request_id), 32)
            tc.assertEqual(set(command), {"request_id", "source_id"})
            hold.expect(request_id)
            hold.arm()
            box = {}
            try:
                request_thread = _start_action_thread(
                    client, command, headers, box,
                    f"deadline-d1-{case}")
                if not hold.entered.wait(
                        timeout=_ACK_TIMEOUT_SECONDS):
                    tc.fail(
                        "fixture: genuine invoke_provider entry "
                        "was never held for this request; "
                        "nonactivation means the delayed-entry "
                        "schedule is not covered")
                captured = hold.captured
                context = captured["context"]
                deadline = captured["deadline"]
                tc.assertIsNotNone(
                    deadline,
                    "captured context exposes no monotonic deadline")
                tc.assertIs(
                    captured["cancel_event"], context.cancel_event)
                tc.assertEqual(captured["request_id"], request_id)
                tc.assertEqual(
                    dict(captured["frozen"])["source_id"],
                    page["source_id"])
                remaining_at_hold = deadline - time.monotonic()
                if remaining_at_hold <= _MIN_REMAINING_AT_HOLD_SECONDS:
                    tc.fail(
                        "fixture: invoke_provider readiness was "
                        "not reached before expiry (remaining "
                        f"{remaining_at_hold!r}s at the held "
                        "phase); this schedule is not covered")
                tc.assertEqual(
                    provider.call_count(), 0,
                    "provider body ran while worker entry was held")
                generation = _accounting_snapshot(
                    registry_lock, state,
                    operations)["source_generation"]
                op_key = (generation, request_id)
                if case == "within_budget":
                    hold.release.set()
                    _join_or_fail(
                        tc, request_thread, _JOIN_TIMEOUT_SECONDS,
                        "within-budget source-analysis HTTP request")
                    response = _response_or_fail(
                        tc, box, "within-budget source-analysis")
                    tc.assertFalse(
                        hold.hold_timed_out,
                        "entry hold expired instead of releasing "
                        "before the context deadline")
                    _assert_source_success(
                        tc, client, response,
                        request_id=request_id,
                        source_id=page["source_id"], page=page,
                        provider=provider)
                    tc.assertEqual(provider.call_count(), 1)
                    _wait_settled(
                        tc, registry_lock, state, operations,
                        request_id=request_id,
                        captured_context=context)
                else:
                    tc.assertEqual(case, "expired_before_entry")
                    _join_or_fail(
                        tc, request_thread,
                        remaining_at_hold + _JOIN_TIMEOUT_SECONDS,
                        "expired-before-entry source-analysis "
                        "HTTP request")
                    tc.assertGreaterEqual(
                        time.monotonic(), deadline,
                        "HTTP closed before the captured "
                        "monotonic deadline was reached")
                    tc.assertFalse(
                        hold.release.is_set(),
                        "entry was released before the deadline "
                        "crossing this case must witness")
                    response = _response_or_fail(
                        tc, box, "expired-before-entry "
                        "source-analysis")
                    tc.assertEqual(
                        response.status_code, 504, response.text)
                    body = response.json()
                    tc.assertEqual(body["request_id"], request_id)
                    tc.assertEqual(
                        body.get("raw_retention"), "not_available")
                    tc.assertEqual(
                        body["detail"]["code"], "timeout")
                    _assert_timeout_no_content(
                        tc, body["snapshot"],
                        request_id=request_id,
                        source_id=page["source_id"])
                    tc.assertEqual(provider.call_count(), 0)
                    interim = _accounting_snapshot(
                        registry_lock, state, operations)
                    tc.assertIn(
                        op_key, interim["worker_reservations"],
                        "callable charge was released merely "
                        "because the HTTP waiter timed out")
                    tc.assertGreater(
                        interim["worker_reservations"][op_key], 0)
                    tc.assertIs(
                        interim["provider_contexts"].get(op_key),
                        context,
                        "live callable context was dropped "
                        "before actual worker return")
                    tc.assertTrue(
                        any(op_context is context and not settled
                            for op_context, settled
                            in interim["operations"]),
                        "ownership settled before the held "
                        "callable actually returned")
                    hold.release.set()
                    _wait_settled(
                        tc, registry_lock, state, operations,
                        request_id=request_id,
                        captured_context=context)
                    tc.assertEqual(
                        provider.call_count(), 0,
                        "provider body ran after expiry; late "
                        "callable cleanup became late success")
                    _assert_final_timeout_state(
                        tc, client, request_id=request_id,
                        source_id=page["source_id"],
                        expect_raw_absent=True)
            finally:
                hold.release.set()
                hold.disarm()
                _join_best_effort(
                    request_thread, _JOIN_TIMEOUT_SECONDS)
    finally:
        hold.release.set()
        hold.disarm()
        hold.restore()
        http_app_module.PROVIDER_TIMEOUT_SECONDS = original_timeout


def _run_final_lock_case(tc, case):
    provider = _SyntheticSourceProvider()
    app = create_app(provider=provider)
    collected = _collect_closure(tc, app)
    publish_function = _required_closure_value(
        tc, collected, "publish_locked")
    _required_closure_value(tc, collected, "invoke_provider")
    sessions = _required_closure_value(tc, collected, "sessions")
    registry_lock = _required_closure_value(
        tc, collected, "registry_lock")
    operations = _required_closure_value(
        tc, collected, "operations")
    lock_line = _registry_lock_line(tc, publish_function)
    hold = _TraceHold(
        publish_function, mode="publish", lock_line=lock_line)
    original_timeout = http_app_module.PROVIDER_TIMEOUT_SECONDS
    request_thread = None
    registry_held = False
    hold.install()
    try:
        http_app_module.PROVIDER_TIMEOUT_SECONDS = (
            _D2_PROVIDER_TIMEOUT_SECONDS)
        with TestClient(
                app, base_url=fixtures.BASE_URL) as client:
            headers, page, _uploaded = _bootstrap(tc, client)
            state = _sole_state(tc, sessions, registry_lock)
            before_snapshot = _get_snapshot(tc, client)
            tc.assertEqual(before_snapshot["regions"], [])
            tc.assertEqual(before_snapshot["questions"], [])
            before_original_bytes = _get_original(tc, client)
            tc.assertTrue(
                before_original_bytes,
                "started page exposes no original bytes")
            command = fixtures.operation_command(page["source_id"])
            request_id = command["request_id"]
            tc.assertEqual(set(command), {"request_id", "source_id"})
            hold.expect(request_id)
            hold.arm()
            box = {}
            try:
                request_thread = _start_action_thread(
                    client, command, headers, box,
                    f"deadline-d2-{case}")
                if not hold.entered.wait(
                        timeout=_ACK_TIMEOUT_SECONDS):
                    tc.fail(
                        "fixture: selected success publish_locked "
                        "call (final_context is not None) was "
                        "never held; nonactivation means the "
                        "final-lock schedule is not covered")
                captured = hold.captured
                context = captured["context"]
                deadline = captured["deadline"]
                tc.assertIsNotNone(
                    deadline,
                    "captured final context exposes no "
                    "monotonic deadline")
                tc.assertIs(captured["state"], state)
                if time.monotonic() >= deadline:
                    tc.fail(
                        "fixture: candidate preparation was not "
                        "established before expiry; this schedule "
                        "is not covered")
                remaining_at_hold = deadline - time.monotonic()
                if remaining_at_hold <= _MIN_REMAINING_AT_HOLD_SECONDS:
                    tc.fail(
                        "fixture: selected publication reached "
                        "the held phase too close to expiry "
                        f"(remaining {remaining_at_hold!r}s); "
                        "this schedule is not covered")
                tc.assertEqual(
                    provider.call_count(), 1,
                    "real synthetic provider must return before "
                    "candidate preparation is held")
                candidate = captured["workspace"]
                tc.assertIsNotNone(
                    candidate, "prepared candidate was not captured")
                candidate_snapshot = snapshot_payload(
                    candidate.snapshot())
                tc.assertEqual(
                    candidate_snapshot["source_id"],
                    page["source_id"])
                tc.assertEqual(
                    len(candidate_snapshot["regions"]), 1)
                tc.assertEqual(
                    candidate_snapshot["regions"][0]["raw_text"],
                    fixtures.RAW_TEXT)
                candidate_view = _find_request(
                    tc, candidate_snapshot, request_id)
                tc.assertEqual(candidate_view["state"], "terminal")
                tc.assertEqual(candidate_view["outcome"], "ok")
                prepared_entry = captured[
                    "provider_results"][request_id]
                tc.assertEqual(prepared_entry[0], "source")
                tc.assertEqual(
                    prepared_entry[1], fixtures.SOURCE_RESULT)
                tc.assertLess(
                    time.monotonic(), deadline,
                    "candidate preparation witness completed "
                    "only after expiry")
                if not registry_lock.acquire(
                        timeout=_LOCK_ACQUIRE_TIMEOUT_SECONDS):
                    tc.fail(
                        "fixture: test could not own the actual "
                        "registry lock while publication was "
                        "held before acquisition")
                registry_held = True
                try:
                    hold.release.set()
                    if not hold.at_lock.wait(
                            timeout=_AT_LOCK_TIMEOUT_SECONDS):
                        tc.fail(
                            "fixture: original publish_locked "
                            "never reached its AST-selected "
                            "registry-lock acquisition line")
                    tc.assertTrue(
                        request_thread.is_alive(),
                        "HTTP returned while final publication "
                        "still waited for the registry lock")
                    if case == "commit_before_deadline":
                        if time.monotonic() >= deadline:
                            tc.fail(
                                "fixture: context expired "
                                "before the intended live commit; "
                                "commit schedule is not covered")
                    else:
                        tc.assertEqual(case, "expiry_before_commit")
                        # No session snapshot/read and no session
                        # lock attempt happens in this interval:
                        # the registry lock alone is owned until
                        # the context deadline has passed.
                        _wait_until_past(deadline)
                        tc.assertGreater(time.monotonic(), deadline)
                        tc.assertTrue(
                            request_thread.is_alive(),
                            "HTTP returned before expiry while "
                            "waiting for the registry lock")
                finally:
                    if registry_held:
                        registry_lock.release()
                        registry_held = False
                _join_or_fail(
                    tc, request_thread, _JOIN_TIMEOUT_SECONDS,
                    f"{case} source-analysis HTTP request")
                response = _response_or_fail(
                    tc, box, f"{case} source-analysis")
                tc.assertFalse(
                    hold.hold_timed_out,
                    "publication phase hold expired instead of "
                    "being released by the test")
                if case == "commit_before_deadline":
                    _assert_source_success(
                        tc, client, response,
                        request_id=request_id,
                        source_id=page["source_id"], page=page,
                        provider=provider)
                else:
                    tc.assertEqual(
                        response.status_code, 504, response.text)
                    body = response.json()
                    tc.assertEqual(body["request_id"], request_id)
                    tc.assertEqual(
                        body["detail"]["code"], "timeout")
                    tc.assertIn(
                        body.get("raw_retention"),
                        ("retained", "omitted_capacity"))
                    _assert_timeout_no_content(
                        tc, body["snapshot"],
                        request_id=request_id,
                        source_id=page["source_id"])
                    after_snapshot = _assert_final_timeout_state(
                        tc, client, request_id=request_id,
                        source_id=page["source_id"],
                        expect_raw_absent=False)
                    for field in (
                            "source_id", "grade", "objective",
                            "regions", "questions",
                            "preview_items"):
                        tc.assertEqual(
                            after_snapshot[field],
                            before_snapshot[field],
                            f"prior teacher state changed: {field}")
                    tc.assertEqual(
                        _get_original(tc, client),
                        before_original_bytes,
                        "original page bytes were not preserved")
                tc.assertEqual(provider.call_count(), 1)
                _wait_settled(
                    tc, registry_lock, state, operations,
                    request_id=request_id,
                    captured_context=context)
            finally:
                hold.release.set()
                hold.disarm()
                if registry_held:
                    registry_lock.release()
                    registry_held = False
                _join_best_effort(
                    request_thread, _JOIN_TIMEOUT_SECONDS)
    finally:
        hold.release.set()
        hold.disarm()
        if registry_held:
            registry_lock.release()
        hold.restore()
        http_app_module.PROVIDER_TIMEOUT_SECONDS = original_timeout


def _run_thirty_second_case(tc):
    default_timeout = http_app_module.PROVIDER_TIMEOUT_SECONDS
    tc.assertGreater(
        default_timeout, _D3_PENDING_SECONDS,
        "unmodified default operation policy must exceed the "
        "31-second pending interval this case witnesses")
    gate = threading.Event()
    provider = _SyntheticSourceProvider(
        gate=gate, gate_ceiling=_D3_GATE_CEILING_SECONDS)
    app = create_app(provider=provider)
    collected = _collect_closure(tc, app)
    sessions = _required_closure_value(tc, collected, "sessions")
    registry_lock = _required_closure_value(
        tc, collected, "registry_lock")
    operations = _required_closure_value(
        tc, collected, "operations")
    request_thread = None
    with TestClient(app, base_url=fixtures.BASE_URL) as client:
        headers, page, _original = _bootstrap(tc, client)
        state = _sole_state(tc, sessions, registry_lock)
        command = fixtures.operation_command(page["source_id"])
        request_id = command["request_id"]
        tc.assertEqual(set(command), {"request_id", "source_id"})
        box = {}
        try:
            request_thread = _start_action_thread(
                client, command, headers, box, "deadline-d3")
            if not provider.entered.wait(
                    timeout=_D3_ENTRY_TIMEOUT_SECONDS):
                tc.fail(
                    "fixture: synthetic provider entry was "
                    "never acknowledged; the 31-second pending "
                    "interval cannot be measured")
            entered_at = provider.entered_at
            tc.assertIsNotNone(entered_at)
            tc.assertEqual(provider.call_count(), 1)
            tc.assertIsNone(
                provider.returned_at,
                "provider returned before its pending interval")
            _wait_until_past(entered_at + _D3_PENDING_SECONDS)
            tc.assertGreaterEqual(
                time.monotonic() - entered_at, _D3_PENDING_SECONDS)
            tc.assertIsNone(
                provider.returned_at,
                "provider returned before the test released it")
            tc.assertTrue(
                request_thread.is_alive(),
                "HTTP returned before the pending provider "
                "completed past 31 seconds")
            gate.set()
            _join_or_fail(
                tc, request_thread, _D3_JOIN_TIMEOUT_SECONDS,
                "31-second source-analysis HTTP request")
            response = _response_or_fail(
                tc, box, "31-second source-analysis")
            tc.assertIsNotNone(provider.returned_at)
            elapsed = provider.returned_at - entered_at
            tc.assertGreaterEqual(
                elapsed, _D3_PENDING_SECONDS,
                "provider pending interval measured from its "
                f"acknowledged entry was only {elapsed!r}s")
            _assert_source_success(
                tc, client, response, request_id=request_id,
                source_id=page["source_id"], page=page,
                provider=provider)
            tc.assertEqual(provider.call_count(), 1)
            _wait_settled(
                tc, registry_lock, state, operations,
                request_id=request_id)
        finally:
            gate.set()
            _join_best_effort(
                request_thread, _D3_JOIN_TIMEOUT_SECONDS)
    tc.assertEqual(
        http_app_module.PROVIDER_TIMEOUT_SECONDS, default_timeout,
        "D3 must keep the unmodified default operation policy")


class HttpOperationDeadlineTests(unittest.TestCase):
    def test_delayed_worker_entry_uses_original_deadline(self):
        with self.subTest(case="within_budget"):
            _run_delayed_entry_case(self, "within_budget")
        with self.subTest(case="expired_before_entry"):
            _run_delayed_entry_case(self, "expired_before_entry")

    def test_expiry_while_waiting_for_final_registry_lock(self):
        with self.subTest(case="commit_before_deadline"):
            _run_final_lock_case(self, "commit_before_deadline")
        with self.subTest(case="expiry_before_commit"):
            _run_final_lock_case(self, "expiry_before_commit")

    def test_success_after_old_thirty_second_boundary(self):
        _run_thirty_second_case(self)


if __name__ == "__main__":
    unittest.main()
