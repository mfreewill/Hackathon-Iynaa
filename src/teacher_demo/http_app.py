"""Session-bound HTTP upload/preview and teacher journey.

Local, in-memory, same-origin only. Accepted page decoding and
the public ``TeacherWorkspace`` domain remain the authorities;
this module adds session/setup/Start, provider-backed source and
question requests through an internally injected callable,
snapshot/raw reads, one combined region-review command, explicit
approval, question edit/reject and region display-move commands,
and approved preview. The fixed same-origin setup/source browser
client is registered from ``http_client``; no live transport or
provider selection happens here.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool as _base_run_in_threadpool

from .adapter import RequestStateError, TeacherDemoError, TeacherWorkspace
from .http_client import register_client
from .http_teacher_helpers import (
    deep_freeze,
    detached_json_value,
    snapshot_payload,
)
from .http_teacher_publication import (
    accounted_total,
    admission_allows,
    callable_payload_cost,
    envelope_cost,
    is_noop_publication,
    measure_terminal_reserve,
    retain_terminal_reserves,
    session_graph_cost,
)
from .hosted_usage import UsageDenied
from .meta_protocol import MetaProtocolError
from .meta_provider import MetaProvider, ProviderCallContext
from .page_decoder import MAX_ORIGINAL_BYTES, PageDecodeError, decode_page
from .question_protocol_v2 import BLOOM_LEVELS

SESSION_COOKIE = "teacher_session"
MAX_SESSIONS = 8
MAX_INFLIGHT_UPLOADS = 2
JSON_BODY_TIMEOUT_SECONDS = 30.0
UPLOAD_BODY_TIMEOUT_SECONDS = 300.0
MAX_CONCURRENT_BODY_READERS = 16
MAX_SESSION_JSON_BYTES = 1_024
MAX_SETTINGS_JSON_BYTES = 16_384
MAX_COMMAND_JSON_BYTES = 16_384
MAX_PROVIDER_RESULT_BYTES = 1 * 1024 * 1024
MAX_PROVIDER_CONCURRENCY = 2
# Internal finite operation policy for one exact provider action
# (admission through final publication), not a latency guarantee.
PROVIDER_TIMEOUT_SECONDS = 600.0
MAX_RETAINED_BYTES_PER_SESSION = 112 * 1024 * 1024
MAX_TOTAL_RETAINED_BYTES = 256 * 1024 * 1024
_PROVIDER_OUTCOMES = frozenset((
    "ok", "refusal", "empty", "invalid_schema", "error", "timeout",
    "rate_limited", "unknown", "incomplete", "cancelled"))
# The exact TeacherWorkspace.edit_question input names. The first
# five and bloom_level are string-valued; the remaining values use
# the domain's structured shapes and are validated by the domain.
_QUESTION_EDIT_STRING_FIELDS = frozenset((
    "question", "answer_guidance", "cognitive_explanation", "grade",
    "objective", "bloom_level"))
_QUESTION_EDIT_FIELDS = _QUESTION_EDIT_STRING_FIELDS | frozenset((
    "hypothetical_assumptions", "evaluate_criteria", "create_rubric"))

_DOWNLOAD_NAMES = {
    "image/png": "original.png",
    "image/jpeg": "original.jpg",
    "application/pdf": "original.pdf",
}
_LOWER_HEX = frozenset("0123456789abcdef")


class _AdmissionCancelled(Exception):
    """Handler left during begin, before any admission published."""


class _SourceMismatch(Exception):
    """Supplied source is not the exact current source."""


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object name")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise ValueError("invalid JSON constant")


def _provider_accepts_context(callable_):
    """One-time adaptation from bindable shape; never a retry.

    Binding is tested with sentinels only, without invocation.
    A distinct context keyword must be bindable alongside one
    positional request. A one-positional callable keeps that
    exact call whatever its sole parameter happens to be named,
    including ``context``.
    """
    if isinstance(callable_, MetaProvider):
        return True
    try:
        signature = inspect.signature(callable_)
    except Exception:
        return False
    try:
        signature.bind(object(), context=object())
    except Exception:
        return False
    return True


def _meta_error_outcome(error):
    """Fixed outcome for a typed error; non-strings are unknown."""
    outcome = getattr(error, "outcome", None)
    if not isinstance(outcome, str):
        return "unknown"
    if outcome == "ok" or outcome not in _PROVIDER_OUTCOMES:
        return "unknown"
    return outcome


import math
from contextvars import ContextVar


_SESSION_PINNED_STATE_KEY = "hosted_session_pinned_state"
_HOSTED_THREADPOOL_DRAIN: ContextVar[bool] = ContextVar(
    "hosted_threadpool_drain", default=False)


def _session_now():
    return time.monotonic()


async def run_in_threadpool(fn, *args, **kwargs):
    if not _HOSTED_THREADPOOL_DRAIN.get():
        return await _base_run_in_threadpool(fn, *args, **kwargs)
    task = asyncio.create_task(
        _base_run_in_threadpool(fn, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Drain the one shielded threadpool task to completion on
        # this and any repeated cancellation, consume its outcome,
        # and only then let the cancellation propagate. The thread
        # work is never detached from this wrapper.
        while not task.done():
            try:
                await asyncio.shield(asyncio.wait({task}))
            except asyncio.CancelledError:
                continue
        if not task.cancelled():
            task.exception()
        raise


@dataclass
class _SessionState:
    csrf_token: str
    lock: threading.Lock
    pending: tuple | None = None
    workspace: TeacherWorkspace | None = None
    decoded: object = None
    source_generation: int = 0
    # request_id -> (kind, detached envelope, envelope logical cost)
    provider_results: dict = field(default_factory=dict)
    # request_id -> retained | omitted_capacity | not_available
    raw_status: dict = field(default_factory=dict)
    # Derived accounting retained with the published payloads:
    # graph cost of the fields above, protected terminal headroom
    # per working request, and callable-bound payload per request
    # still executable by a worker. Lock/executor/epoch are
    # authority metadata, never measured payload.
    terminal_reserves: dict = field(default_factory=dict)
    # Callable-bound charges use generation-qualified keys so a
    # reused public id on a new source never collides with an old
    # still-running callable from the replaced source.
    worker_reservations: dict = field(default_factory=dict)
    # (source_generation, request_id) -> ProviderCallContext for
    # admitted work whose HTTP owner is still active. Authority
    # metadata only: never measured payload, never provider data.
    provider_contexts: dict = field(default_factory=dict)
    graph_cost: int = 0
    publication_epoch: int = 0
    # Hosted idle-lease authority metadata, owned under the
    # registry lock and never measured payload: monotonic last
    # activity plus the count of pinned in-flight HTTP owners.
    last_seen: float = field(default_factory=_session_now)
    active_http: int = 0


def create_app(
    *,
    allowed_hosts=("127.0.0.1:8000",),
    allowed_origin="http://127.0.0.1:8000",
    provider=None,
    secure_cookie=False,
    session_idle_seconds=None,
) -> FastAPI:
    """Create the local app; security configuration is internal only.

    ``provider`` is an internal, caller-injected synchronous
    callable for source/question work. It is not selectable over
    HTTP, and the default (``None``) is credential-free and
    disconnected (analysis/generation refuse as unavailable).
    """
    if type(secure_cookie) is not bool:
        raise ValueError("secure_cookie must be a bool")
    if session_idle_seconds is not None:
        if (
            type(session_idle_seconds) not in (int, float)
            or not math.isfinite(session_idle_seconds)
            or session_idle_seconds <= 0
        ):
            raise ValueError(
                "session_idle_seconds must be positive and finite or None")
    hosts = frozenset(allowed_hosts)
    sessions: dict[str, _SessionState] = {}
    # Single authority for session admission, in-flight/provider
    # admission, and global retained-budget publication.
    registry_lock = threading.Lock()
    inflight = {"count": 0, "provider": 0}
    body_readers = {"count": 0}
    provider_executor = ThreadPoolExecutor(
        max_workers=MAX_PROVIDER_CONCURRENCY,
        thread_name_prefix="teacher-provider")
    # One operation owner for the whole app. Overlapping ASGI
    # lifespans share one lifetime generation; only the last
    # close captures that generation and its pool. Operations
    # register at slot reservation (before admission can
    # publish) and settle only when handler work and any
    # submitted callable are both finished. A fresh startup
    # during old cleanup opens an isolated next generation, so
    # old cleanup can never signal or join new operations.
    executor_state = {"lifespans": 0, "closed": False,
                      "generation": 0, "next_operation": 0}
    closed_generations = set()
    operations = {}
    _idle_header_value = (
        None if session_idle_seconds is None
        else str(session_idle_seconds))

    def purge_expired():
        """Drop idle-expired sessions that no owner still holds.

        Candidates are snapshotted under the registry and then
        rechecked state-lock-first (never waiting on a state lock
        while holding the registry), so busy, renewed, replaced,
        HTTP-pinned, operation-owned, or otherwise reserved
        sessions are skipped rather than force-deleted.
        """
        if session_idle_seconds is None:
            return
        now = _session_now()
        with registry_lock:
            candidates = [
                (session_id, state)
                for session_id, state in sessions.items()
                if now - state.last_seen >= session_idle_seconds
            ]
        for session_id, state in candidates:
            if not state.lock.acquire(blocking=False):
                continue
            try:
                workspace = state.workspace
                if workspace is not None:
                    requests = workspace.snapshot().requests
                    entries = (
                        requests.values()
                        if isinstance(requests, dict) else requests)
                    working = False
                    for entry in entries:
                        if isinstance(entry, dict):
                            status = entry.get(
                                "status", entry.get("state"))
                        elif isinstance(entry, str):
                            status = entry
                        else:
                            status = getattr(entry, "status", None)
                            if status is None:
                                status = getattr(entry, "state", None)
                        if status == "working":
                            working = True
                            break
                    if working:
                        continue
                with registry_lock:
                    if sessions.get(session_id) is not state:
                        continue
                    if (
                        _session_now() - state.last_seen
                        < session_idle_seconds
                    ):
                        continue
                    if state.active_http:
                        continue
                    if (
                        state.worker_reservations
                        or state.provider_contexts
                        or state.terminal_reserves
                    ):
                        continue
                    if any(
                        operation.get("state") is state
                        for operation in operations.values()
                    ):
                        continue
                    del sessions[session_id]
            finally:
                state.lock.release()

    def shutdown_provider_executor(generation, executor):
        # Runs in a threadpool thread, never on an event loop and
        # holding no lock while waiting: handler admission,
        # result/terminal work and done callbacks still need
        # event loops plus the registry/session locks. The
        # generation was closed at capture, so this snapshot
        # cannot gain new operations from a reopened lifetime.
        with registry_lock:
            owned = [operation for operation in operations.values()
                     if operation["generation"] == generation]
        signal_contexts(
            [operation["context"] for operation in owned])
        for operation in owned:
            operation["settled"].wait()
        executor.shutdown(wait=True)
        with registry_lock:
            closed_generations.discard(generation)

    @asynccontextmanager
    async def app_lifespan(_app):
        nonlocal provider_executor
        with registry_lock:
            if (executor_state["closed"]
                    and executor_state["lifespans"] == 0):
                executor_state["generation"] += 1
                provider_executor = ThreadPoolExecutor(
                    max_workers=MAX_PROVIDER_CONCURRENCY,
                    thread_name_prefix="teacher-provider")
                executor_state["closed"] = False
            executor_state["lifespans"] += 1
        try:
            yield
        finally:
            with registry_lock:
                executor_state["lifespans"] -= 1
                final = executor_state["lifespans"] <= 0
                if final:
                    executor_state["lifespans"] = 0
                    executor_state["closed"] = True
                    generation = executor_state["generation"]
                    closed_generations.add(generation)
                    closing = provider_executor
            if final:
                # Drained even if shutdown itself is cancelled:
                # cancellation propagates only after the owned
                # generation cleanup has actually settled.
                await run_owned(
                    shutdown_provider_executor, generation, closing)

    app = FastAPI(
        title="Teacher workspace",
        docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=app_lifespan)
    app.state.teacher_provider = provider
    # Adapted once here: context-aware callables (the real
    # MetaProvider included) get context=, ordinary injected
    # callables keep their exact one-argument call. Worker entry
    # rechecks the shared context, so a queued callable whose
    # operation closed or expired is never invoked late.
    provider_with_context = (
        provider is not None and _provider_accepts_context(provider))

    def invoke_provider(frozen, context):
        context.remaining()
        if provider_with_context:
            return provider(frozen, context=context)
        return provider(frozen)

    def context_terminal(context):
        """Fixed (outcome, status) if closed/expired, else None."""
        try:
            context.remaining()
        except MetaProtocolError as error:
            if getattr(error, "outcome", None) == "cancelled":
                return ("cancelled", 409)
            if getattr(error, "outcome", None) == "timeout":
                return ("timeout", 504)
            return ("unknown", 502)
        return None

    def signal_contexts(contexts):
        for context in contexts:
            context.cancel_event.set()

    def operation_contexts(state, op_keys):
        with registry_lock:
            return [state.provider_contexts[key] for key in op_keys
                    if key in state.provider_contexts]

    def signal_superseded(state, before_snapshot, after_snapshot,
                          *, generation, exclude=None):
        """Signal ops a committed transaction just closed.

        Called only after a successful publication. A request
        working before and missing or no longer working after
        was cancelled or superseded by that commit; a refused
        or no-op candidate never reaches a state change here.
        """
        if before_snapshot is None or after_snapshot is None:
            return
        after_states = {item.request_id: item.state
                        for item in after_snapshot.requests}
        keys = [(generation, item.request_id)
                for item in before_snapshot.requests
                if item.state == "working"
                and item.request_id != exclude
                and after_states.get(item.request_id) != "working"]
        signal_contexts(operation_contexts(state, keys))

    def settle_operation_locked(operation):
        """Finish registration once both owned sides are done."""
        if operation is None:
            return
        if (operation["handler_done"]
                and operation["callable_returned"]
                and operations.pop(operation["id"], None)
                is operation):
            operation["settled"].set()

    def mark_handler_done(operation):
        if operation is None:
            return
        with registry_lock:
            operation["handler_done"] = True
            settle_operation_locked(operation)

    def pop_context_locked(state, binding):
        """Remove this exact context; registry lock must be held."""
        if state.provider_contexts.get(
                binding["op_key"]) is binding["context"]:
            state.provider_contexts.pop(binding["op_key"], None)

    def leave_operation(state, binding):
        """Mark the HTTP waiter gone; keep a live callable's context.

        The registration is dropped only once both sides of the
        owned work have finished: the waiter has left and the
        callable has actually returned (or was never submitted,
        which its release records as returned). Shutdown can
        therefore still signal a callable whose waiter left.
        """
        if binding is None:
            return
        with registry_lock:
            binding["owner_left"] = True
            operation = binding.get("operation")
            if operation is not None:
                operation["handler_done"] = True
            if binding["callable_returned"]:
                pop_context_locked(state, binding)
            settle_operation_locked(operation)

    def refuse(status, detail):
        raise HTTPException(status_code=status, detail=detail)

    def _body_close_headers(request):
        scope = getattr(request, "scope", None)
        version = (
            scope.get("http_version", "")
            if isinstance(scope, dict) else "")
        if not isinstance(version, str):
            version = ""
        if version.startswith("1.") or not version:
            return {"Connection": "close"}
        return None

    def _refuse_body(request, status, detail):
        headers = _body_close_headers(request)
        if headers is None:
            raise HTTPException(status_code=status, detail=detail)
        raise HTTPException(
            status_code=status, detail=detail, headers=headers)

    def check_host(request):
        if request.headers.get("host") not in hosts:
            refuse(400, "invalid host")

    @app.middleware("http")
    async def enforce_host(request, call_next):
        if request.headers.get("host") not in hosts:
            return JSONResponse(
                status_code=400, content={"detail": "invalid host"})
        return await call_next(request)

    class _HostedSessionMiddleware:
        """Pin the cookie session for the full inner ASGI call."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") != "http":
                await self.app(scope, receive, send)
                return
            request = Request(scope)
            if request.headers.get("host") not in hosts:
                response = JSONResponse(
                    status_code=400, content={"detail": "invalid host"})
                await response(scope, receive, send)
                return
            path = scope.get("path", "")
            if (
                path != "/api/session"
                and not path.startswith("/api/session/")
            ):
                await self.app(scope, receive, send)
                return
            session_id = request.cookies.get(SESSION_COOKIE, "")
            with registry_lock:
                state = sessions.get(session_id)
                if state is not None:
                    state.active_http += 1
                    state.last_seen = _session_now()
            if state is None:
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "session required"})
                await response(scope, receive, send)
                return
            scope_state = scope.setdefault("state", {})
            scope_state[_SESSION_PINNED_STATE_KEY] = state

            async def send_with_idle_header(message):
                if (
                    message.get("type") == "http.response.start"
                    and _idle_header_value is not None
                ):
                    headers = [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() != b"x-session-idle-seconds"
                    ]
                    headers.append((
                        b"x-session-idle-seconds",
                        _idle_header_value.encode("latin-1")))
                    message = {**message, "headers": headers}
                await send(message)

            token = _HOSTED_THREADPOOL_DRAIN.set(True)
            try:
                await self.app(scope, receive, send_with_idle_header)
            finally:
                _HOSTED_THREADPOOL_DRAIN.reset(token)
                if scope_state.get(_SESSION_PINNED_STATE_KEY) is state:
                    del scope_state[_SESSION_PINNED_STATE_KEY]
                with registry_lock:
                    state.active_http -= 1
                    state.last_seen = _session_now()

    if session_idle_seconds is not None:
        app.add_middleware(_HostedSessionMiddleware)

    def check_origin(request):
        if request.headers.get("origin") != allowed_origin:
            refuse(403, "forbidden origin")

    def session_for(request):
        check_host(request)
        session_id = request.cookies.get(SESSION_COOKIE, "")
        if session_idle_seconds is None:
            with registry_lock:
                state = sessions.get(session_id)
            if state is None:
                refuse(401, "session required")
            return state
        scope_state = request.scope.get("state")
        pinned = (
            scope_state.get(_SESSION_PINNED_STATE_KEY)
            if isinstance(scope_state, dict) else None)
        with registry_lock:
            current = sessions.get(session_id)
        if pinned is None or current is not pinned:
            refuse(401, "session required")
        return pinned

    def check_mutation(request, state):
        check_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        try:
            supplied_bytes = supplied.encode("ascii")
        except (AttributeError, UnicodeEncodeError):
            refuse(403, "forbidden request")
        if not secrets.compare_digest(
                supplied_bytes, state.csrf_token.encode("ascii")):
            refuse(403, "forbidden request")

    def require_utf8_text(value):
        # Admission requires a strict UTF-8 representation (JSON
        # output renders as strict UTF-8); valid supplementary
        # Unicode passes, lone surrogates are refused untouched.
        if not isinstance(value, str):
            return False
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return False
        return True

    def prepared_json(status, payload):
        # JSONResponse renders (strict UTF-8) at construction, so
        # callers can fully build the actual response before any
        # state publication and return that same response after it.
        return JSONResponse(
            status_code=status, content=payload,
            headers={"Cache-Control": "no-store"})

    async def read_bounded(request, limit, timeout_seconds):
        with registry_lock:
            if body_readers["count"] >= MAX_CONCURRENT_BODY_READERS:
                _refuse_body(request, 429, {
                    "code": "body_reader_capacity_exceeded",
                    "admission": "not_started",
                    "message": "body reader capacity exceeded"})
            body_readers["count"] += 1
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_seconds
            chunks = []
            total = 0
            timeout = asyncio.timeout_at(deadline)
            try:
                async with timeout:
                    async for chunk in request.stream():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > limit:
                            _refuse_body(
                                request, 413, "request too large")
                        chunks.append(chunk)
            except TimeoutError:
                expired_attr = getattr(timeout, "expired", None)
                if callable(expired_attr):
                    expired = expired_attr()
                elif expired_attr is not None:
                    expired = bool(expired_attr)
                else:
                    expired = False
                if not expired and loop.time() < deadline:
                    raise
                _refuse_body(request, 400, {
                    "code": "request_body_timeout",
                    "admission": "not_started",
                    "message": "request body timeout"})
            if loop.time() >= deadline:
                _refuse_body(request, 400, {
                    "code": "request_body_timeout",
                    "admission": "not_started",
                    "message": "request body timeout"})
            return b"".join(chunks)
        finally:
            with registry_lock:
                body_readers["count"] -= 1

    async def read_json_object(request, limit):
        content_type = request.headers.get("content-type", "")
        if content_type.split(";")[0].strip() != "application/json":
            refuse(415, "unsupported media type")
        raw = await read_bounded(
            request, limit, JSON_BODY_TIMEOUT_SECONDS)
        try:
            value = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_json_constant)
        except Exception:
            refuse(400, "invalid request")
        if not isinstance(value, dict):
            refuse(400, "invalid request")
        return value

    def accounted_bytes(state):
        # Stored components only; safe under the registry lock with
        # no live graph traversal and no other session's lock.
        return accounted_total(
            state.graph_cost, state.worker_reservations,
            state.terminal_reserves)

    def graph_cost_for(state, pending, decoded, workspace,
                       provider_results, raw_status):
        # An unmeasurable retained graph refuses admission; it is
        # never silently counted as zero.
        try:
            return session_graph_cost(
                state.csrf_token, pending, decoded, workspace,
                provider_results, raw_status)
        except ValueError:
            refuse(413, "request too large")

    def publish_locked(state, *, pending, decoded, workspace,
                       provider_results, raw_status,
                       terminal_reserves, graph_cost,
                       new_worker=None, new_source=False,
                       new_context=None, final_context=None):
        """The one retained-state publication authority.

        Requires ``state.lock`` held. The fully prepared candidate
        payloads, their derived ``graph_cost``, and the actual
        response were all built before this call. Under
        session->registry order this makes the global capacity
        decision and the payload/cost/raw/reservation swap as one
        commit, or changes nothing and returns ``False``. There
        is no capacity-bypassing path: a candidate that grows the
        accounted total publishes only within the per-session and
        global caps, and a non-growing candidate (including a
        reserve-consuming terminal finish) always publishes.
        Semantic no-op candidates never reach this authority (see
        ``is_noop_publication``), so every call here is a genuine
        retained publication and advances the epoch. Registry
        sections touch stored integers only; no expensive work
        runs under the registry lock.
        """
        with registry_lock:
            worker_after = dict(state.worker_reservations)
            if new_worker is not None:
                worker_after[new_worker[0]] = new_worker[1]
            worker_sum = sum(worker_after.values())
            reserve_sum = sum(terminal_reserves.values())
            mine_after = graph_cost + worker_sum + reserve_sum
            if mine_after > accounted_bytes(state):
                others = sum(
                    accounted_bytes(other)
                    for other in sessions.values()
                    if other is not state)
                if not admission_allows(
                        graph_cost, worker_sum, reserve_sum, others,
                        MAX_RETAINED_BYTES_PER_SESSION,
                        MAX_TOTAL_RETAINED_BYTES):
                    return False
            # Final expiry/cancellation check with session and
            # registry locks both held, immediately before the
            # swap: no success can commit after closure/expiry.
            if final_context is not None:
                final_context.remaining()
            state.pending = pending
            state.decoded = decoded
            state.workspace = workspace
            state.provider_results = provider_results
            state.raw_status = raw_status
            state.terminal_reserves = terminal_reserves
            state.worker_reservations = worker_after
            if new_context is not None:
                state.provider_contexts[new_context[0]] = new_context[1]
            state.graph_cost = graph_cost
            state.publication_epoch += 1
            if new_source:
                state.source_generation += 1
            return True

    def metadata_for(decoded, workspace):
        page = decoded.decoded_source.page
        snapshot = workspace.snapshot()
        return {
            "source_id": page.source_id,
            "source_sha256": page.source_sha256,
            "render_sha256": page.render_sha256,
            "media_type": decoded.media_type,
            "page_count": decoded.page_count,
            "page_index": page.page_index,
            "width": page.width,
            "height": page.height,
            "decoder": decoded.decoder,
            "render_dpi": decoded.render_dpi,
            "grade": snapshot.grade,
            "objective": snapshot.objective,
            "rights_ref": page.rights_ref,
            "rights_is_clearance": page.rights_is_clearance,
            "original_url": "/api/session/page/original",
            "preview_url": "/api/session/page/preview.png",
        }

    @app.post("/api/sessions", status_code=201)
    async def create_session(request: Request):
        check_host(request)
        check_origin(request)
        body = await read_json_object(request, MAX_SESSION_JSON_BYTES)
        if body != {}:
            refuse(400, "invalid request")
        session_id = secrets.token_urlsafe(32)
        state = _SessionState(
            csrf_token=secrets.token_urlsafe(32),
            lock=threading.Lock(), last_seen=_session_now())
        try:
            initial_cost = session_graph_cost(
                state.csrf_token, None, None, None, {}, {})
        except ValueError:
            refuse(413, "request too large")
        # Build (render) the actual response, cookie included,
        # before publishing the session into the registry.
        payload = {"csrf_token": state.csrf_token}
        if session_idle_seconds is not None:
            payload["session_idle_seconds"] = session_idle_seconds
        response = prepared_json(201, payload)
        if session_idle_seconds is not None:
            response.headers["X-Session-Idle-Seconds"] = (
                str(session_idle_seconds))
        response.set_cookie(
            SESSION_COOKIE, session_id, httponly=True,
            secure=secure_cookie, samesite="strict", path="/api")
        if session_idle_seconds is not None:
            purge_expired()
        with registry_lock:
            if len(sessions) >= MAX_SESSIONS:
                refuse(503, "session limit reached")
            others = sum(
                accounted_bytes(other) for other in sessions.values())
            if others + initial_cost > MAX_TOTAL_RETAINED_BYTES:
                refuse(413, "request too large")
            state.graph_cost = initial_cost
            sessions[session_id] = state
        return response

    def commit_settings(state, grade, objective, rights_ref):
        # Same prepare/admit/publish path as every retained write:
        # the caller validated the whole command and rendered the
        # exact response; here a copy-on-write candidate receives
        # the public domain mutation, is measured as a full session
        # graph, and publishes (or refuses) through the authority.
        # An identical three-field command is a semantic no-op:
        # the stored pending tuple, workspace, and publication
        # epoch keep their identity, so setup/provider currency
        # captured against them stays valid. The caller still
        # returns its rendered 200 response.
        with state.lock:
            before_snapshot = (
                state.workspace.snapshot()
                if state.workspace is not None else None)
            candidate = (
                copy.deepcopy(state.workspace)
                if state.workspace is not None else None)
            if candidate is not None:
                candidate.set_grade_objective(grade, objective)
            after_snapshot = (
                candidate.snapshot()
                if candidate is not None else None)
            pending_values = (grade, objective, rights_ref)
            pending_after = (
                state.pending if state.pending == pending_values
                else pending_values)
            reserves_after = retain_terminal_reserves(
                state.terminal_reserves, after_snapshot,
                state.raw_status)
            graph_after = graph_cost_for(
                state, pending_after, state.decoded, candidate,
                state.provider_results, state.raw_status)
            if is_noop_publication(
                    before_snapshot=before_snapshot,
                    after_snapshot=after_snapshot,
                    before_pending=state.pending,
                    after_pending=pending_values,
                    before_graph_cost=state.graph_cost,
                    after_graph_cost=graph_after,
                    before_reserves=state.terminal_reserves,
                    after_reserves=reserves_after):
                return
            if not publish_locked(
                    state, pending=pending_after,
                    decoded=state.decoded, workspace=candidate,
                    provider_results=state.provider_results,
                    raw_status=state.raw_status,
                    terminal_reserves=reserves_after,
                    graph_cost=graph_after):
                refuse(413, "request too large")
            signal_superseded(
                state, before_snapshot, after_snapshot,
                generation=state.source_generation)

    @app.put("/api/session/settings")
    async def put_settings(request: Request):
        state = session_for(request)
        check_mutation(request, state)
        body = await read_json_object(
            request, MAX_SETTINGS_JSON_BYTES)
        if set(body) != {"grade", "objective", "rights_ref"}:
            refuse(400, "invalid request")
        grade = body["grade"]
        objective = body["objective"]
        rights_ref = body["rights_ref"]
        if (not require_utf8_text(grade) or not grade.strip()
                or not require_utf8_text(objective)
                or not objective.strip()
                or not require_utf8_text(rights_ref)):
            refuse(400, "invalid request")
        # The committed snapshot carries these exact strings, so
        # the actual response can be rendered before any commit.
        response = prepared_json(200, {
            "grade": grade, "objective": objective,
            "rights_ref": rights_ref})
        try:
            await run_in_threadpool(
                commit_settings, state, grade, objective, rights_ref)
        except TeacherDemoError:
            refuse(400, "invalid request")
        return response

    def start_candidate(state, data, page_index):
        # Snapshot the setup this candidate is built from.
        with state.lock:
            pending = state.pending
        if pending is None:
            refuse(400, "setup required")
        grade, objective, rights_ref = pending
        if not all(require_utf8_text(value)
                   for value in (grade, objective, rights_ref)):
            refuse(400, "invalid request")
        # Expensive preparation holds neither lock: real decode,
        # fresh workspace, metadata, and the rendered response.
        decoded = decode_page(data, page_index, rights_ref=rights_ref)
        workspace = TeacherWorkspace(
            decoded.decoded_source,
            grade=grade, objective=objective)
        response = prepared_json(201, metadata_for(decoded, workspace))
        # Full candidate graph: setup, decoded original/PNG payload
        # and metadata, and the fresh workspace. Complete state
        # accounting can correctly exceed the page bytes alone.
        graph_after = graph_cost_for(
            state, pending, decoded, workspace, {}, {})
        # Single serialized publication through the authority.
        # Setup currency is tuple identity, so a settings
        # change-away-and-back during decoding still refuses; a
        # valid replacement then deliberately resets content,
        # results, and reserves. Callable-bound reservations of
        # the old source survive until their workers complete.
        with state.lock:
            if state.pending is not pending:
                refuse(409, "setup changed")
            if not publish_locked(
                    state, pending=pending, decoded=decoded,
                    workspace=workspace, provider_results={},
                    raw_status={}, terminal_reserves={},
                    graph_cost=graph_after, new_source=True):
                refuse(413, "request too large")
            # Committed replacement signals every operation bound
            # to an older generation. Entries stay registered
            # until their owners finish; charges likewise survive.
            with registry_lock:
                replaced = [
                    context for (generation, _request_id), context
                    in state.provider_contexts.items()
                    if generation != state.source_generation]
            signal_contexts(replaced)
        return response

    @app.post("/api/session/start", status_code=201)
    async def start_page(request: Request):
        state = session_for(request)
        check_mutation(request, state)
        content_type = request.headers.get("content-type", "")
        if content_type.split(";")[0].strip() != "application/octet-stream":
            refuse(415, "unsupported media type")
        raw_index = request.query_params.get("page_index", "0")
        if (not raw_index.isascii() or not raw_index.isdigit()
                or len(raw_index) > 9):
            refuse(400, "invalid page index")
        page_index = int(raw_index)
        if state.pending is None:
            refuse(400, "setup required")
        with registry_lock:
            if inflight["count"] >= MAX_INFLIGHT_UPLOADS:
                refuse(429, "too many uploads")
            inflight["count"] += 1
        try:
            data = await read_bounded(
                request, MAX_ORIGINAL_BYTES,
                UPLOAD_BODY_TIMEOUT_SECONDS)
            try:
                return await run_in_threadpool(
                    start_candidate, state, data, page_index)
            except HTTPException:
                raise
            except PageDecodeError as exc:
                status = (
                    413 if exc.code in (
                        "input_too_large", "image_too_large")
                    else 415 if exc.code == "unsupported_format"
                    else 400)
                raise HTTPException(
                    status_code=status,
                    detail={"message": "page could not be decoded",
                            "code": exc.code}) from None
            except TeacherDemoError:
                refuse(400, "invalid request")
        finally:
            with registry_lock:
                inflight["count"] -= 1

    def current_page(state):
        with state.lock:
            if state.decoded is None or state.workspace is None:
                return None
            return state.decoded, metadata_for(
                state.decoded, state.workspace)

    @app.get("/api/session/page")
    async def get_page(request: Request):
        state = session_for(request)
        current = await run_in_threadpool(current_page, state)
        if current is None:
            refuse(404, "page not found")
        return current[1]

    async def download(request, preview):
        state = session_for(request)
        current = await run_in_threadpool(current_page, state)
        if current is None:
            refuse(404, "page not found")
        decoded = current[0]
        if preview:
            content, media_type, name = (
                decoded.png_bytes, "image/png", "preview.png")
        else:
            content = decoded.original_bytes
            media_type = decoded.media_type
            name = _DOWNLOAD_NAMES.get(media_type, "original.bin")
        return Response(
            content=content, media_type=media_type,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{name}"',
            })

    @app.get("/api/session/page/original")
    async def get_original(request: Request):
        return await download(request, preview=False)

    @app.get("/api/session/page/preview.png")
    async def get_preview(request: Request):
        return await download(request, preview=True)

    # -- teacher journey: shared transaction helpers ----------------
    def snapshot_json(state):
        return snapshot_payload(state.workspace.snapshot())

    def transact_snapshot(state, apply_fn, status=200, extra=None):
        """Copy-on-write domain command through the authority.

        The candidate receives every public domain call, its full
        session graph is measured, and the actual response is
        rendered before publication, so a domain,
        representability, capacity, or rendering failure preserves
        the exact prior state. Protected terminal reserves are
        part of the admitted total, so an edit cannot consume a
        working request's terminal room or a closed request's
        remaining retention-status room; a cancellation candidate
        converts its own full reserve to that closure room. A
        same-value command whose candidate is a semantic no-op
        (identical snapshot, graph cost, and reserves) returns
        its rendered response without a publication, preserving
        workspace and epoch identity for in-flight currency.
        """
        with state.lock:
            if state.workspace is None:
                raise LookupError("page not found")
            before_snapshot = state.workspace.snapshot()
            candidate = copy.deepcopy(state.workspace)
            apply_fn(candidate)
            snap_obj = candidate.snapshot()
            payload = {"snapshot": snapshot_payload(snap_obj)}
            if extra:
                payload.update(extra)
            response = prepared_json(status, payload)
            reserves_after = retain_terminal_reserves(
                state.terminal_reserves, snap_obj, state.raw_status)
            graph_after = graph_cost_for(
                state, state.pending, state.decoded, candidate,
                state.provider_results, state.raw_status)
            if is_noop_publication(
                    before_snapshot=before_snapshot,
                    after_snapshot=snap_obj,
                    before_pending=state.pending,
                    after_pending=state.pending,
                    before_graph_cost=state.graph_cost,
                    after_graph_cost=graph_after,
                    before_reserves=state.terminal_reserves,
                    after_reserves=reserves_after):
                return response
            if not publish_locked(
                    state, pending=state.pending,
                    decoded=state.decoded, workspace=candidate,
                    provider_results=state.provider_results,
                    raw_status=state.raw_status,
                    terminal_reserves=reserves_after,
                    graph_cost=graph_after):
                refuse(413, "request too large")
            signal_superseded(
                state, before_snapshot, snap_obj,
                generation=state.source_generation)
            return response

    def require_workspace(state):
        if state.workspace is None or state.decoded is None:
            refuse(404, "page not found")

    def check_operation_identity(state, request_id, source_id):
        """Exact current-source and duplicate gate (worker thread)."""
        with state.lock:
            require_workspace(state)
            page = state.decoded.decoded_source.page
            if page.source_id != source_id:
                refuse(409, "source changed")
            if request_view(state.workspace, request_id) is not None:
                refuse(409, "duplicate request")

    def begin_provider_request(state, kind, request_id, source_id,
                               context):
        """Publish one working domain request; return its binding.

        Runs in a worker thread. The begin is itself copy-on-write:
        candidate begins, its lifecycle snapshot is proved
        serializable, provider request data is built from the
        candidate, and only then does it become authoritative.
        """
        with state.lock:
            require_workspace(state)
            page = state.decoded.decoded_source.page
            if page.source_id != source_id:
                refuse(409, "source changed")
            if request_view(state.workspace, request_id) is not None:
                refuse(409, "duplicate request")
            before_snapshot = state.workspace.snapshot()
            candidate = copy.deepcopy(state.workspace)
            if kind == "source":
                candidate.begin_source_request(request_id)
                snapshot = candidate.snapshot()
                request_plain = {
                    "kind": "source_analysis",
                    "request_id": request_id,
                    "source_id": page.source_id,
                    "source_sha256": page.source_sha256,
                    "render_sha256": page.render_sha256,
                    # Describes the sent png_bytes, never the
                    # original upload type (kept as page metadata).
                    "media_type": "image/png",
                    "png_bytes": state.decoded.png_bytes,
                    "grade": snapshot.grade,
                    "objective": snapshot.objective,
                }
            else:
                slots = tuple(
                    {"slot_id": f"slot-{level}", "level": level}
                    for level in BLOOM_LEVELS)
                candidate.begin_question_request(
                    request_id, requested_slots=list(slots))
                prompt = candidate.question_request_prompt(request_id)
                snapshot = candidate.snapshot()
                reviewed = {
                    region.region_id: region.current_text
                    for region in snapshot.regions
                    if region.inclusion == "included"
                    and region.current_text.strip()}
                request_plain = {
                    "kind": "question_generation",
                    "request_id": request_id,
                    "source_id": page.source_id,
                    "prompt": prompt,
                    "reviewed_regions": reviewed,
                    "requested_slots": slots,
                    "grade": snapshot.grade,
                    "objective": snapshot.objective,
                }
            prepared_json(200, {"snapshot": snapshot_payload(
                candidate.snapshot())})
            binding = {
                "kind": kind,
                "request_id": request_id,
                "source_id": source_id,
                "op_key": (state.source_generation, request_id),
                "context": context,
                "callable_returned": False,
                "charge_released": False,
                "owner_left": False,
                "source_generation": state.source_generation,
                "source_revision": candidate.source_revision,
                "settings_revision": candidate.settings_revision,
            }
            frozen = deep_freeze(request_plain)
            # Admission, before any invocation: room for the
            # working request and its bound data, its protected
            # no-content terminal reserve, and the callable-bound
            # payload. The maximum successful response is not
            # reserved. Repeated retained history therefore
            # eventually refuses here instead of accumulating.
            capacity_detail = {
                "code": "retained_capacity_exceeded",
                "admission": "not_started",
            }
            try:
                frozen_cost = callable_payload_cost(frozen)
            except ValueError:
                refuse(413, capacity_detail)
            reserves_after = retain_terminal_reserves(
                state.terminal_reserves, snapshot, state.raw_status)
            reserves_after[request_id] = measure_terminal_reserve(
                candidate, request_id, kind)
            graph_after = graph_cost_for(
                state, state.pending, state.decoded, candidate,
                state.provider_results, state.raw_status)
            if context.cancel_event.is_set():
                raise _AdmissionCancelled
            if not publish_locked(
                    state, pending=state.pending,
                    decoded=state.decoded, workspace=candidate,
                    provider_results=state.provider_results,
                    raw_status=state.raw_status,
                    terminal_reserves=reserves_after,
                    graph_cost=graph_after,
                    new_worker=(binding["op_key"], frozen_cost),
                    new_context=(binding["op_key"], context)):
                refuse(413, capacity_detail)
            signal_superseded(
                state, before_snapshot, snapshot,
                generation=state.source_generation,
                exclude=request_id)
            return binding, frozen

    def request_view(workspace, request_id):
        for item in workspace.snapshot().requests:
            if item.request_id == request_id:
                return item
        return None

    def finish_on(candidate, binding, outcome):
        if binding["kind"] == "source":
            candidate.finish_source_request(
                binding["request_id"], outcome)
        else:
            candidate.finish_question_request(
                binding["request_id"], outcome)

    def terminalize_request(state, binding, outcome, status, code,
                            envelope=None, omit_raw=False):
        """Close a request without admitting content (worker thread).

        Uses current request/source authority: a source-generation
        mismatch stores and finishes nothing on the new source.
        Otherwise the finish happens on a copy-on-write candidate
        of the current workspace, so concurrent teacher decisions
        are preserved. With-raw and without-raw candidate graphs
        and their actual terminal responses are all prepared
        before publication; the authority then atomically picks
        the first that fits. Bounded raw is retained only when it
        fits, and every response states ``raw_retention``
        explicitly (``retained``, ``omitted_capacity``, or
        ``not_available``); raw is never truncated or claimed.
        The finished candidate consumes this request's protected
        terminal reserve, so truthful completion is not blocked
        by other edits. For an already-closed request, the
        closure slack retained at cancel/supersede time protects
        this later retention-status publication in the same way.
        There is no bypass: if no variant fits, nothing is
        published and the response says so against the actual
        stored snapshot, never claiming omitted raw was retained
        and never growing retained state past the caps.
        Callable-bound reservations are untouched here and
        release only at actual worker completion.
        """
        request_id = binding["request_id"]
        with state.lock:
            if (state.source_generation
                    != binding["source_generation"]
                    or state.workspace is None):
                snapshot = (snapshot_json(state)
                            if state.workspace is not None else None)
                return prepared_json(409, {
                    "request_id": request_id,
                    "snapshot": snapshot,
                    "raw_retention": "not_available",
                    "detail": {"code": "source_changed",
                               "message": "source changed"}})
            candidate = copy.deepcopy(state.workspace)
            view = request_view(candidate, request_id)
            if view is None:
                return prepared_json(409, {
                    "request_id": request_id,
                    "snapshot": snapshot_payload(candidate.snapshot()),
                    "raw_retention": "not_available",
                    "detail": {"code": "request_closed",
                               "message": "provider request did not "
                                          "complete successfully"}})
            if (view.state != "working"
                    and request_id in state.raw_status):
                # A retention decision already committed for this
                # closed request (success included). A late
                # cancellation/timeout notification preserves it
                # and its retained raw exactly; nothing publishes.
                return prepared_json(409, {
                    "request_id": request_id,
                    "snapshot": snapshot_payload(
                        candidate.snapshot()),
                    "raw_retention": state.raw_status[request_id],
                    "detail": {"code": "request_closed",
                               "message": "provider request did not "
                                          "complete successfully"}})
            was_working = view.state == "working"
            if was_working:
                finish_on(candidate, binding, outcome)
            else:
                status, code = 409, "request_closed"
            snap_obj = candidate.snapshot()
            snap = snapshot_payload(snap_obj)
            env_cost = envelope_cost(envelope) if envelope else 0

            def build(retention, store_raw):
                results_after = dict(state.provider_results)
                if store_raw:
                    results_after[request_id] = (
                        binding["kind"], envelope, env_cost)
                status_after = dict(state.raw_status)
                status_after[request_id] = retention
                reserves_variant = retain_terminal_reserves(
                    state.terminal_reserves, snap_obj, status_after)
                response = prepared_json(status, {
                    "request_id": request_id,
                    "snapshot": snap,
                    "raw_retention": retention,
                    "detail": {"code": code,
                               "message": "provider request did not "
                                          "complete successfully"}})
                graph_after = graph_cost_for(
                    state, state.pending, state.decoded, candidate,
                    results_after, status_after)
                return (results_after, status_after, reserves_variant,
                        graph_after, response)

            variants = []
            if envelope and not omit_raw:
                variants.append(build("retained", True))
            variants.append(build(
                "omitted_capacity" if envelope else "not_available",
                False))
            for (results_after, status_after, reserves_variant,
                    graph_after, response) in variants:
                if publish_locked(
                        state, pending=state.pending,
                        decoded=state.decoded, workspace=candidate,
                        provider_results=results_after,
                        raw_status=status_after,
                        terminal_reserves=reserves_variant,
                        graph_cost=graph_after):
                    return response
            # No variant fit within the caps. Protected reserves
            # make the without-raw variant admissible for both a
            # live finish and a closed request's later status, so
            # this is a defensive truthful refusal, never a forced
            # publication: stored state is untouched, the snapshot
            # is the actual stored one, and omitted raw is never
            # claimed as retained. A still-working request remains
            # cancellable through the same protected reserve.
            if not was_working:
                return variants[-1][-1]
            return prepared_json(413, {
                "request_id": request_id,
                "snapshot": snapshot_payload(
                    state.workspace.snapshot()),
                "raw_retention": (
                    "omitted_capacity" if envelope
                    else "not_available"),
                "detail": {"code": "retained_budget_exceeded",
                           "message": "provider request did not "
                                      "complete successfully"}})

    def publish_provider_result(state, binding, envelope):
        """Admit a validated ok envelope, or a terminal sentinel."""
        raw = envelope["raw"]
        request_id = binding["request_id"]
        try:
            env_cost = envelope_cost(envelope)
        except ValueError:
            return ("terminal", "error", 413,
                    "retained_budget_exceeded")
        context = binding["context"]
        closed = context_terminal(context)
        if closed is not None:
            return ("terminal", closed[0], closed[1], closed[0])
        with state.lock:
            if (state.source_generation
                    != binding["source_generation"]
                    or state.workspace is None):
                return ("terminal", "stale", 409, "source_changed")
            workspace = state.workspace
            initial_snapshot = workspace.snapshot()
            view = request_view(workspace, request_id)
            if view is None or view.kind != binding["kind"]:
                return ("terminal", "stale", 409, "source_changed")
            if view.state != "working":
                return ("terminal", view.outcome or "cancelled",
                        409, "request_closed")
            if (workspace.source_revision
                    != binding["source_revision"]
                    or workspace.settings_revision
                    != binding["settings_revision"]
                    or view.bound_source_revision
                    != binding["source_revision"]
                    or view.bound_settings_revision
                    != binding["settings_revision"]):
                return ("terminal", "stale", 409, "request_stale")
            epoch0 = state.publication_epoch
            candidate = copy.deepcopy(workspace)
            results0 = dict(state.provider_results)
            raw_status0 = dict(state.raw_status)
            reserves0 = dict(state.terminal_reserves)
            pending0 = state.pending
            decoded0 = state.decoded
        try:
            if binding["kind"] == "source":
                candidate.admit_source_proposal(
                    binding["request_id"], raw,
                    model_name=envelope["model_name"])
            else:
                candidate.admit_question_proposal(
                    binding["request_id"], raw,
                    model_name=envelope["model_name"])
            finish_on(candidate, binding, "ok")
        except RequestStateError:
            return ("terminal", "stale", 409, "request_stale")
        except TeacherDemoError:
            return ("terminal", "invalid_schema", 502,
                    "invalid_schema")
        # Actual success response rendered before any commit; a
        # rendering failure here is a precommit processing failure
        # for the caller to terminalize without content.
        snap_obj = candidate.snapshot()
        response = prepared_json(201, {
            "request_id": request_id,
            "snapshot": snapshot_payload(snap_obj),
            "raw_retention": "retained"})
        results_after = dict(results0)
        results_after[request_id] = (
            binding["kind"], envelope, env_cost)
        raw_status_after = dict(raw_status0)
        raw_status_after[request_id] = "retained"
        reserves_after = retain_terminal_reserves(
            reserves0, snap_obj, raw_status_after)
        graph_after = graph_cost_for(
            state, pending0, decoded0, candidate,
            results_after, raw_status_after)
        closed = context_terminal(context)
        if closed is not None:
            return ("terminal", closed[0], closed[1], closed[0])
        with state.lock:
            # Recheck by publication epoch plus source/settings
            # revision identity: any intervening retained
            # publication (including change-away-and-back) makes
            # this detached candidate stale, so it can never
            # overwrite a concurrent decision. Snapshot equality
            # or workspace identity alone would not prove this.
            if (state.source_generation
                    != binding["source_generation"]
                    or state.workspace is None
                    or state.publication_epoch != epoch0
                    or state.workspace.source_revision
                    != binding["source_revision"]
                    or state.workspace.settings_revision
                    != binding["settings_revision"]):
                return ("terminal", "stale", 409, "request_stale")
            try:
                published = publish_locked(
                    state, pending=pending0, decoded=decoded0,
                    workspace=candidate,
                    provider_results=results_after,
                    raw_status=raw_status_after,
                    terminal_reserves=reserves_after,
                    graph_cost=graph_after,
                    final_context=context)
            except MetaProtocolError:
                closed = context_terminal(context) or (
                    "unknown", 502)
                return ("terminal", closed[0], closed[1], closed[0])
            if not published:
                return ("terminal", "error", 413,
                        "retained_budget_exceeded")
            signal_superseded(
                state, initial_snapshot, snap_obj,
                generation=binding["source_generation"],
                exclude=request_id)
        return ("response", response)

    def process_provider_result(state, binding, result):
        # One accepted request lifecycle: detach, validate, admit,
        # and render are precommit stages of the same boundary.
        # Any ordinary failure among them terminalizes truthfully
        # without publishing content, preserving prior state and
        # retaining bounded raw only when capacity permits. The
        # request is never left working by a processing failure.
        try:
            return _process_provider_result(state, binding, result)
        except HTTPException:
            raise
        except Exception:
            return terminalize_request(
                state, binding, "error", 502, "provider_error",
                envelope=_detached_or_none(result))

    def _detached_or_none(result):
        try:
            return detached_json_value(
                result, MAX_PROVIDER_RESULT_BYTES)
        except Exception:
            return None

    def _process_provider_result(state, binding, result):
        try:
            envelope = detached_json_value(
                result, MAX_PROVIDER_RESULT_BYTES)
        except ValueError:
            return terminalize_request(
                state, binding, "invalid_schema", 502,
                "invalid_schema")
        if (not isinstance(envelope, dict)
                or set(envelope) != {
                    "raw", "model_name", "response_id", "outcome"}
                or not require_utf8_text(envelope.get("model_name"))
                or not envelope.get("model_name", "").strip()
                or not require_utf8_text(envelope.get("response_id"))
                or not envelope.get("response_id", "").strip()
                or not isinstance(envelope.get("outcome"), str)):
            return terminalize_request(
                state, binding, "invalid_schema", 502,
                "invalid_schema", envelope=envelope)
        outcome = envelope["outcome"]
        if outcome not in _PROVIDER_OUTCOMES:
            return terminalize_request(
                state, binding, "unknown", 502, "unknown",
                envelope=envelope)
        if outcome != "ok":
            status = (504 if outcome == "timeout"
                      else 503 if outcome == "rate_limited" else 502)
            return terminalize_request(
                state, binding, outcome, status, outcome,
                envelope=envelope)
        raw = envelope["raw"]
        is_empty = (
            not isinstance(raw, dict) or not raw
            or (binding["kind"] == "source"
                and raw.get("sections") in (None, []))
            or (binding["kind"] == "questions"
                and raw.get("items") in (None, [])))
        if is_empty:
            return terminalize_request(
                state, binding, "empty", 502, "empty",
                envelope=envelope)
        if not isinstance(raw, dict):
            return terminalize_request(
                state, binding, "invalid_schema", 502,
                "invalid_schema", envelope=envelope)
        published = publish_provider_result(state, binding, envelope)
        if published[0] == "response":
            return published[1]
        _, term_outcome, status, code = published
        return terminalize_request(
            state, binding, term_outcome, status, code,
            envelope=envelope,
            omit_raw=code == "retained_budget_exceeded")

    def reserve_operation(context, state):
        """Reserve a slot and register its full operation at once.

        Closure, capacity and generation/executor binding are
        decided under the one registry lock, so an operation
        cannot register into a lifetime that has closed. The
        registration now covers admission before any binding
        or provider context publication exists. The owning
        session state is registered with the operation so its
        session cannot idle-expire while the operation lives.
        """
        with registry_lock:
            if executor_state["closed"]:
                return None
            if inflight["provider"] >= MAX_PROVIDER_CONCURRENCY:
                return None
            inflight["provider"] += 1
            executor_state["next_operation"] += 1
            operation = {
                "id": executor_state["next_operation"],
                "generation": executor_state["generation"],
                "executor": provider_executor,
                "context": context,
                "state": state,
                "settled": threading.Event(),
                "handler_done": False,
                "callable_returned": False,
                "charge_released": False,
            }
            operations[operation["id"]] = operation
            return operation

    def release_reserved_slot(operation):
        """Release a never-submitted slot exactly once."""
        with registry_lock:
            if operation["charge_released"]:
                return
            operation["charge_released"] = True
            operation["callable_returned"] = True
            inflight["provider"] -= 1
            settle_operation_locked(operation)

    def submit_operation(operation, frozen, context):
        """Submit only if this generation is still dispatchable.

        Check and executor submit are one registry section, in
        the same order as final-close capture: a callable is
        either submitted before its generation closes (and is
        then awaited by that close) or is never submitted.
        """
        with registry_lock:
            if operation["generation"] in closed_generations:
                return None
            return operation["executor"].submit(
                invoke_provider, frozen, context)

    def release_callable(state, binding, _future=None):
        # Actual worker completion (or a submit the executor
        # refused) is the only release point for callable-bound
        # data: ending, cancelling, timing out, or replacing the
        # source of a request never frees what a still-running
        # worker can hold. Same global authority as publication.
        # The qualified key keeps an old completion from erasing
        # a reused id's newer reservation on a replaced source.
        # Idempotent under the registry, so the synchronous
        # ownership finalizer and the done callback can never
        # release the same charge twice. Synchronous only: it is
        # safe to call from finally during cancellation.
        with registry_lock:
            if not binding["charge_released"]:
                binding["charge_released"] = True
                binding["callable_returned"] = True
                operation = binding.get("operation")
                if operation is not None:
                    operation["charge_released"] = True
                    operation["callable_returned"] = True
                inflight["provider"] -= 1
                state.worker_reservations.pop(binding["op_key"], None)
                if binding["owner_left"]:
                    pop_context_locked(state, binding)
                settle_operation_locked(operation)
        if _future is not None:
            try:
                _future.exception()
            except Exception:
                pass

    async def run_owned(fn, *args, on_cancel=None):
        """Await threadpool work the handler owns to completion.

        Thread work cannot be cancelled with the handler. On
        handler cancellation, optionally signal the shared
        context, drain the work to its one terminal/publication
        decision, then re-raise. Nothing is abandoned to publish
        or invoke later without this owner observing completion.
        """
        task = asyncio.create_task(run_in_threadpool(fn, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if on_cancel is not None:
                on_cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            raise

    async def run_provider_request(request, kind):
        state = session_for(request)
        check_mutation(request, state)
        body = await read_json_object(request, MAX_COMMAND_JSON_BYTES)
        if set(body) != {"request_id", "source_id"}:
            refuse(400, "invalid request")
        request_id = body["request_id"]
        source_id = body["source_id"]
        if (not isinstance(request_id, str)
                or len(request_id) != 32
                or any(char not in _LOWER_HEX for char in request_id)
                or not require_utf8_text(source_id)):
            refuse(400, "invalid request")
        if provider is None:
            raise HTTPException(
                status_code=503,
                detail={"code": "provider_unavailable",
                        "message": "provider unavailable"})
        await run_in_threadpool(
            check_operation_identity, state, request_id, source_id)
        # One deadline from before admission/queue entry through
        # dispatch, provider wait, preparation and publication.
        context = ProviderCallContext(PROVIDER_TIMEOUT_SECONDS)
        operation = reserve_operation(context, state)
        if operation is None:
            refuse(429, "too many provider requests")
        binding = None
        # One synchronous ownership finalizer for the reserved
        # slot/charge. Repeated cancellation can interrupt every
        # await below; it cannot skip this finally-only, await-
        # free release. Transfer to the future done callback is
        # the single point where release moves to actual return.
        charge = {"binding": None, "transferred": False,
                  "released": False}

        def finalize_charge():
            owned = charge["binding"]
            if owned is not None:
                leave_operation(state, owned)
            else:
                mark_handler_done(operation)
            if charge["released"] or charge["transferred"]:
                return
            charge["released"] = True
            if owned is not None:
                release_callable(state, owned)
            else:
                release_reserved_slot(operation)

        async def terminalize_and_signal(outcome, status, code):
            try:
                return await run_owned(
                    terminalize_request, state, binding,
                    outcome, status, code)
            finally:
                context.cancel_event.set()

        try:
            begin_task = asyncio.create_task(run_in_threadpool(
                begin_provider_request, state, kind, request_id,
                source_id, context))
            try:
                binding, frozen = await asyncio.shield(begin_task)
                binding["operation"] = operation
                charge["binding"] = binding
            except asyncio.CancelledError:
                context.cancel_event.set()
                while not begin_task.done():
                    try:
                        await asyncio.shield(begin_task)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if begin_task.done() and not begin_task.cancelled():
                    try:
                        binding, frozen = begin_task.result()
                        binding["operation"] = operation
                        charge["binding"] = binding
                    except Exception:
                        binding = None
                if binding is not None:
                    try:
                        try:
                            await run_owned(
                                terminalize_request, state, binding,
                                "cancelled", 409, "cancelled")
                        except Exception:
                            pass
                    finally:
                        finalize_charge()
                raise
            except _AdmissionCancelled:
                refuse(409, "cancelled")
            except LookupError:
                refuse(404, "page not found")
            except RequestStateError:
                refuse(409, "generation not ready")
            except TeacherDemoError:
                refuse(400, "invalid request")
            # Capacity refusal inside begin (HTTPException 413)
            # already propagated above, before any invocation.
            closed = context_terminal(context)
            if closed is not None:
                return await run_owned(
                    terminalize_request, state, binding,
                    closed[0], closed[1], closed[0])
            try:
                future = submit_operation(operation, frozen, context)
            except Exception:
                return await run_owned(
                    terminalize_request, state, binding, "error",
                    502, "provider_error")
            if future is None:
                return await run_owned(
                    terminalize_request, state, binding,
                    "cancelled", 409, "cancelled")
            future.add_done_callback(
                lambda done, owned=binding: release_callable(
                    state, owned, done))
            charge["transferred"] = True  # callback owns release
            remaining = context.deadline - time.monotonic()
            if remaining <= 0:
                return await terminalize_and_signal(
                    "timeout", 504, "timeout")
            loop = asyncio.get_running_loop()
            try:
                result = await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(
                        future, loop=loop)),
                    timeout=remaining)
            except asyncio.TimeoutError:
                return await terminalize_and_signal(
                    "timeout", 504, "timeout")
            except asyncio.CancelledError:
                try:
                    await terminalize_and_signal(
                        "cancelled", 409, "cancelled")
                except Exception:
                    pass
                raise
            except UsageDenied as error:
                code = error.code
                if not isinstance(code, str) or code not in {
                        "usage_exhausted", "usage_window_closed",
                        "usage_rate_limited",
                        "usage_accounting_unavailable"}:
                    code = "usage_accounting_unavailable"
                return await run_owned(
                    terminalize_request, state, binding, "error",
                    503, code)
            except MetaProtocolError as error:
                outcome = _meta_error_outcome(error)
                status = (504 if outcome == "timeout"
                          else 409 if outcome == "cancelled" else 502)
                return await run_owned(
                    terminalize_request, state, binding, outcome,
                    status, outcome)
            except Exception:
                return await run_owned(
                    terminalize_request, state, binding, "error",
                    502, "provider_error")
            return await run_owned(
                process_provider_result, state, binding, result,
                on_cancel=context.cancel_event.set)
        finally:
            finalize_charge()

    @app.post("/api/session/source-analysis", status_code=201)
    async def source_analysis(request: Request):
        return await run_provider_request(request, "source")

    @app.post("/api/session/questions/generate", status_code=201)
    async def generate_questions(request: Request):
        return await run_provider_request(request, "questions")

    def read_snapshot(state):
        with state.lock:
            require_workspace(state)
            return snapshot_json(state)

    @app.get("/api/session/snapshot")
    async def get_snapshot(request: Request):
        state = session_for(request)
        try:
            payload = await run_in_threadpool(read_snapshot, state)
        except HTTPException:
            raise
        return prepared_json(200, payload)

    @app.get("/api/session/preview")
    async def get_teacher_preview(request: Request):
        state = session_for(request)
        payload = await run_in_threadpool(read_snapshot, state)
        return prepared_json(200, {"items": payload["preview_items"]})

    def read_provider_result(state, request_id):
        with state.lock:
            require_workspace(state)
            view = request_view(state.workspace, request_id)
            stored = state.provider_results.get(request_id)
            if view is None or stored is None:
                return None
            kind, envelope, _cost = stored
            return {"request_id": request_id, "kind": kind,
                    "raw_retention": "retained",
                    "provider_result": copy.deepcopy(envelope)}

    @app.get("/api/session/requests/{request_id}/raw")
    async def get_request_raw(request: Request, request_id: str):
        state = session_for(request)
        payload = await run_in_threadpool(
            read_provider_result, state, request_id)
        if payload is None:
            refuse(404, "request not found")
        return prepared_json(200, payload)

    def read_request_status(state, request_id, source_id):
        with state.lock:
            require_workspace(state)
            payload = snapshot_payload(state.workspace.snapshot())
            # Wrong source is decided before any request lookup,
            # so another current request is never substituted.
            if payload["source_id"] != source_id:
                raise _SourceMismatch
            for item in payload["requests"]:
                if item["request_id"] == request_id:
                    return {"request_id": request_id,
                            "source_id": payload["source_id"],
                            "request": item}
            return None

    @app.get("/api/session/requests/{request_id}/status")
    async def get_request_status(request: Request, request_id: str):
        state = session_for(request)
        source_id = request.query_params.get("source_id")
        if source_id is None:
            refuse(400, "invalid request")
        try:
            payload = await run_in_threadpool(
                read_request_status, state, request_id, source_id)
        except _SourceMismatch:
            refuse(409, "source changed")
        if payload is None:
            refuse(404, "request not found")
        return prepared_json(200, payload)

    @app.put("/api/session/regions/{region_id}")
    async def put_region(request: Request, region_id: str):
        state = session_for(request)
        check_mutation(request, state)
        body = await read_json_object(request, MAX_COMMAND_JSON_BYTES)
        if set(body) != {"inclusion", "current_text", "current_label"}:
            refuse(400, "invalid request")
        inclusion = body["inclusion"]
        current_text = body["current_text"]
        current_label = body["current_label"]
        if (inclusion not in ("included", "excluded", "unresolved")
                or not require_utf8_text(current_text)
                or not require_utf8_text(current_label)
                or not current_label.strip()):
            refuse(400, "invalid request")

        def apply(candidate):
            if not any(region.region_id == region_id
                       for region in candidate.snapshot().regions):
                raise LookupError("region not found")
            candidate.edit_region_text(region_id, current_text)
            candidate.edit_region_label(region_id, current_label)
            candidate.set_region_inclusion(region_id, inclusion)

        try:
            return await run_in_threadpool(
                transact_snapshot, state, apply)
        except LookupError:
            refuse(404, "region not found")
        except TeacherDemoError:
            refuse(400, "invalid request")

    @app.post("/api/session/questions/{question_id}/approve")
    async def approve(request: Request, question_id: str):
        state = session_for(request)
        check_mutation(request, state)
        body = await read_json_object(request, MAX_COMMAND_JSON_BYTES)
        if body != {}:
            refuse(400, "invalid request")

        def apply(candidate):
            if not any(item.question_id == question_id
                       for item in candidate.snapshot().questions):
                raise LookupError("question not found")
            candidate.approve_question(question_id)

        try:
            return await run_in_threadpool(
                transact_snapshot, state, apply)
        except LookupError:
            refuse(404, "question not found")
        except TeacherDemoError:
            raise HTTPException(
                status_code=409,
                detail={"code": "approval_blocked",
                        "message": "approval is blocked"}) from None

    @app.patch("/api/session/questions/{question_id}")
    async def edit_question(request: Request, question_id: str):
        """Edit current question fields: ``PATCH`` with any subset
        (including the empty no-op) of the domain edit keys.

        Returns 200 ``{snapshot}``. Unrecognized keys are refused,
        never filtered away; legitimate values are forwarded to
        ``edit_question`` exactly, with no text normalization and
        no change to source-support semantics.
        """
        state = session_for(request)
        check_mutation(request, state)
        body = await read_json_object(request, MAX_COMMAND_JSON_BYTES)
        try:
            # Representation admission on a detached copy: strings
            # or keys without a strict UTF-8 representation (for
            # example escaped lone surrogates, at any nesting
            # depth) are refused here, before any candidate exists,
            # while valid padded/supplementary text passes exact.
            changes = detached_json_value(
                body, MAX_COMMAND_JSON_BYTES)
        except ValueError:
            refuse(400, "invalid request")
        if not set(changes) <= _QUESTION_EDIT_FIELDS:
            refuse(400, "invalid request")
        if any(name in changes
               and not isinstance(changes[name], str)
               for name in _QUESTION_EDIT_STRING_FIELDS):
            refuse(400, "invalid request")

        def apply(candidate):
            if not any(item.question_id == question_id
                       for item in candidate.snapshot().questions):
                raise LookupError("question not found")
            candidate.edit_question(question_id, **changes)

        try:
            return await run_in_threadpool(
                transact_snapshot, state, apply)
        except LookupError:
            refuse(404, "question not found")
        except TeacherDemoError:
            refuse(400, "invalid request")

    @app.post("/api/session/questions/{question_id}/reject")
    async def reject(request: Request, question_id: str):
        state = session_for(request)
        check_mutation(request, state)
        body = await read_json_object(request, MAX_COMMAND_JSON_BYTES)
        if body != {}:
            refuse(400, "invalid request")

        def apply(candidate):
            if not any(item.question_id == question_id
                       for item in candidate.snapshot().questions):
                raise LookupError("question not found")
            candidate.reject_question(question_id)

        try:
            return await run_in_threadpool(
                transact_snapshot, state, apply)
        except LookupError:
            refuse(404, "question not found")
        except TeacherDemoError:
            refuse(400, "invalid request")

    async def run_region_move(request, region_id, *, up):
        state = session_for(request)
        check_mutation(request, state)
        body = await read_json_object(request, MAX_COMMAND_JSON_BYTES)
        if body != {}:
            refuse(400, "invalid request")

        def apply(candidate):
            if not any(region.region_id == region_id
                       for region in candidate.snapshot().regions):
                raise LookupError("region not found")
            if up:
                candidate.move_region_up(region_id)
            else:
                candidate.move_region_down(region_id)

        try:
            return await run_in_threadpool(
                transact_snapshot, state, apply)
        except LookupError:
            refuse(404, "region not found")
        except TeacherDemoError:
            refuse(400, "invalid request")

    @app.post("/api/session/regions/{region_id}/move-up")
    async def move_region_up(request: Request, region_id: str):
        return await run_region_move(request, region_id, up=True)

    @app.post("/api/session/regions/{region_id}/move-down")
    async def move_region_down(request: Request, region_id: str):
        return await run_region_move(request, region_id, up=False)

    @app.post("/api/session/requests/{request_id}/cancel")
    async def cancel_request(request: Request, request_id: str):
        """Cancel one working request with exactly ``{source_id}``.

        Returns 200 ``{request_id, source_id, snapshot}`` only
        after the cancellation commits, which is also when the
        matching provider event is signalled. Closing admission
        does not stop an already-running callable; its late
        result is refused at the request-lifecycle boundary.
        """
        state = session_for(request)
        check_mutation(request, state)
        body = await read_json_object(request, MAX_COMMAND_JSON_BYTES)
        if set(body) != {"source_id"}:
            refuse(400, "invalid request")
        source_id = body["source_id"]
        if not require_utf8_text(source_id):
            refuse(400, "invalid request")

        def apply(candidate):
            if candidate.snapshot().source_id != source_id:
                raise _SourceMismatch
            view = request_view(candidate, request_id)
            if view is None:
                raise LookupError("request not found")
            if view.kind == "source":
                candidate.cancel_source_request(request_id)
            else:
                candidate.cancel_question_request(request_id)

        try:
            return await run_in_threadpool(
                transact_snapshot, state, apply, 200,
                {"request_id": request_id, "source_id": source_id})
        except _SourceMismatch:
            refuse(409, "source changed")
        except LookupError:
            refuse(404, "request not found")
        except (RequestStateError, TeacherDemoError):
            refuse(409, "request is closed")

    register_client(app)
    return app
