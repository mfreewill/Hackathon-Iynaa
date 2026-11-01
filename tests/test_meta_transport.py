"""Owned-transport tests for ``teacher_demo.meta_transport``.

Every test method first asserts module availability with a
real ``importlib.util.find_spec`` before importing it, starting any
fixture server, or launching any child, so a missing module
fails directly and unrelated package import errors are never
converted into missing-module evidence.

Declared seams (loopback only):

* Real-child cases run the actual product ``__main__`` branch and
  ``_child_main`` through ``tests/meta_transport_child_fixture.py``
  mode ``real_child``: the fixture installs a per-process
  IPv4-loopback-only socket guard and the existing
  ``test_meta_http._client_seams`` local-TLS/destination/trust
  seams, then ``runpy`` executes the real script. The real
  ``meta_http.send_payload`` performs the single POST against the
  reused ``test_meta_http._running_server`` TLS fixture. This is
  the real product script branch/HTTP helper/process execution
  with declared test bootstrap and destination/trust seams, not
  an unmodified live-provider invocation.
* The test wraps the actual ``asyncio.create_subprocess_exec``:
  it records and validates the production interpreter, ``-I -B``
  flags, entry point, environment, and cwd BEFORE substituting
  only the fixed fixture script and fixture-only arguments. No
  credential or payload is ever placed in fixture argv/env/files;
  the private input still travels on the child's stdin.
* Supervisor-negative cases use the fixture's explicitly
  synthetic ``syn_*`` process modes: known synthetic frames
  derived from the existing safe fixtures, never real Meta
  responses and never a replacement HTTP helper.
* Direct ``_child_main`` cases invoke the real entry point
  in-process under a per-process socket guard (refuse-all for
  malformed input; loopback plus ``_client_seams`` for helper
  classifications). Malformed-stdin process cases additionally
  run the real script through fixture mode ``real_guarded``,
  which refuses every network operation. No live-capable child
  is ever launched unguarded.
* Small byte-bound cases patch only the module's runtime bound
  constants at these declared test-only setup seams (the contract
  requires bounds be read at use time); product parsing, sending,
  output, and decoding are never replaced.

``test_meta_http`` is reused as a MODULE (its private fixture
helpers only), never as a ``TestCase`` alias, so discovery of this
file finds exactly the six methods below. Standard library and
``unittest`` only.
"""
import asyncio
import concurrent.futures
import contextlib
import copy
import hashlib
import hmac
import http.client
import importlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import MappingProxyType

_TESTS_DIR = Path(__file__).resolve().parent
_FIXTURE_SCRIPT = _TESTS_DIR / "meta_transport_child_fixture.py"
_SRC_DIR = _TESTS_DIR.parent / "src"
_TRANSPORT_INVALID = "meta transport request is invalid"
_REFUSED_MSG = "meta protocol response refused"
_SENTINEL = object()
_CLOSED_FAILURES = (
    "error", "unknown", "invalid_schema", "incomplete",
    "refusal", "empty", "cancelled",
)
_FORBIDDEN_ENV = (
    "META_API_KEY", "OPENAI_API_KEY", "SSLKEYLOGFILE", "PYTHONPATH",
    "PYTHONHOME", "NETRC", "HTTP_PROXY", "HTTPS_PROXY",
)


def _load_fixture_module():
    """Reuse the child fixture module for its shared guard only.

    Importing it installs no hook and starts no process; the audit
    floor is activated only when a guard scope is entered.
    """
    name = "meta_transport_child_fixture"
    existing = sys.modules.get(name)
    if existing is not None:
        try:
            if Path(existing.__file__).resolve() == (
                _FIXTURE_SCRIPT.resolve()
            ):
                return existing
        except Exception:
            pass
    spec = importlib.util.spec_from_file_location(
        name, str(_FIXTURE_SCRIPT)
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The shared fixture is acquired lazily, only when a
# ``_DirectNetGuard`` is first constructed after the protected
# ``_http = _load_http_module()`` initialization. Importing this
# test module therefore never imports the fixture, whose import
# could change how the HTTP helper module name resolves. Exactly
# one genuine fixture module instance is cached and shared.
_fixture_module = None
_fixture_module_lock = threading.Lock()


def _shared_fixture_module():
    global _fixture_module
    if _fixture_module is None:
        with _fixture_module_lock:
            if _fixture_module is None:
                _fixture_module = _load_fixture_module()
    return _fixture_module


def _ensure_record_fields(record):
    record.setdefault("attempts", 0)
    record.setdefault("spawns", [])
    record.setdefault("invocation_seq", 0)
    record.setdefault("active_invocation_ids", [])
    record.setdefault("invocation_spawn_starts", {})
    record.setdefault("invocation_spawn_ends", {})
    record.setdefault("invocation_attempt_starts", {})
    record.setdefault("completed_invocation_ids", [])
    record.setdefault("product_return_observations", [])
    record.setdefault("schedule_releases", [])
    record.setdefault("emergency_rescues", [])
    record.setdefault("teardown_failures", [])
    record.setdefault("_lock", threading.Lock())
    return record


# -- A1: one shared pre-launch production-spawn validator -------------
# The same pure validator runs inside the wrapped
# ``asyncio.create_subprocess_exec`` before any barrier, task or
# process exists, and ``_assert_production_spawn`` reuses it later.
# Invalid proposals are refused before side effects; they are never
# sanitized into a launchable call. Error text is fixed and carries
# no proposal values.

_SPAWN_INVALID = "invalid production spawn proposal"
_EXPECTED_PRODUCT_SCRIPT = (
    _SRC_DIR / "teacher_demo" / "meta_transport.py"
)
_EXPECTED_PRODUCT_DIR = _EXPECTED_PRODUCT_SCRIPT.parent
_REQUIRED_SPAWN_KWARGS = frozenset((
    "stdin", "stdout", "stderr", "env", "cwd",
))
_OPTIONAL_SPAWN_KWARGS = frozenset((
    "limit", "close_fds", "bufsize", "shell",
))
_BOOTSTRAP_ENV_KEYS = (
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH",
    "PATHEXT", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)
_FIXED_PYTHON_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONNOUSERSITE": "1",
    "PYTHONOPTIMIZE": "0",
}


def _spawn_refused():
    raise ValueError(_SPAWN_INVALID)


def _current_bootstrap_entries():
    """Map upper-cased bootstrap name to its live ``(key, value)``."""
    entries = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in _BOOTSTRAP_ENV_KEYS and upper not in entries:
            entries[upper] = (key, value)
    return entries


def _safe_bootstrap_env():
    """Fresh OS-bootstrap-only env rebuilt from the live process."""
    entries = _current_bootstrap_entries()
    env = {}
    for upper in _BOOTSTRAP_ENV_KEYS:
        if upper in entries:
            key, value = entries[upper]
            env[key] = value
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _validate_bootstrap_env(env):
    if not isinstance(env, dict):
        _spawn_refused()
    inherited = {
        name: value
        for name, (_key, value) in _current_bootstrap_entries().items()
    }
    seen = set()
    for key, value in env.items():
        if not isinstance(key, str) or not isinstance(value, str):
            _spawn_refused()
        upper = key.upper()
        if upper in seen:
            _spawn_refused()
        seen.add(upper)
        if upper in _BOOTSTRAP_ENV_KEYS:
            if upper not in inherited or value != inherited[upper]:
                _spawn_refused()
        elif upper in _FIXED_PYTHON_ENV:
            if value != _FIXED_PYTHON_ENV[upper]:
                _spawn_refused()
        else:
            _spawn_refused()


def _validated_path(value, expected, require_absolute=True):
    if isinstance(value, os.PathLike):
        value = os.fspath(value)
    if not isinstance(value, str) or isinstance(value, bytes):
        _spawn_refused()
    candidate = Path(value)
    if require_absolute and not candidate.is_absolute():
        _spawn_refused()
    try:
        if candidate.resolve() != Path(expected).resolve():
            _spawn_refused()
    except ValueError:
        raise
    except Exception:
        _spawn_refused()
    return candidate


def _validate_production_spawn(args, kwargs, module=None):
    """Validate one production proposal; return a fresh fixture env.

    Pure: it inspects only its arguments, live bootstrap values and
    module path metadata. It imports nothing, records nothing and
    launches nothing.
    """
    if not isinstance(args, (tuple, list)) or len(args) != 4:
        _spawn_refused()
    if not isinstance(kwargs, dict):
        _spawn_refused()
    if module is not None:
        module_file = getattr(module, "__file__", None)
        if module_file is None and isinstance(
            module, (str, os.PathLike)
        ):
            module_file = module
        _validated_path(
            module_file, _EXPECTED_PRODUCT_SCRIPT,
            require_absolute=False,
        )
    _validated_path(args[0], sys.executable)
    if args[1] != "-I" or args[2] != "-B":
        _spawn_refused()
    _validated_path(args[3], _EXPECTED_PRODUCT_SCRIPT)
    keys = set()
    for key in kwargs:
        if not isinstance(key, str):
            _spawn_refused()
        keys.add(key)
    if not _REQUIRED_SPAWN_KWARGS.issubset(keys):
        _spawn_refused()
    if not keys.issubset(
        _REQUIRED_SPAWN_KWARGS | _OPTIONAL_SPAWN_KWARGS
    ):
        _spawn_refused()
    for name in ("stdin", "stdout", "stderr"):
        if kwargs[name] != subprocess.PIPE or isinstance(
            kwargs[name], bool
        ):
            _spawn_refused()
    _validated_path(kwargs["cwd"], _EXPECTED_PRODUCT_DIR)
    _validate_bootstrap_env(kwargs["env"])
    if "limit" in kwargs:
        if type(kwargs["limit"]) is not int or kwargs["limit"] <= 0:
            _spawn_refused()
    if "close_fds" in kwargs and kwargs["close_fds"] is not True:
        _spawn_refused()
    if "bufsize" in kwargs:
        if type(kwargs["bufsize"]) is not int or kwargs["bufsize"] != 0:
            _spawn_refused()
    if "shell" in kwargs and kwargs["shell"] is not False:
        _spawn_refused()
    return _safe_bootstrap_env()


# -- A3: immutable product-return observations ------------------------
# Snapshots are taken immediately when the real callable returns,
# before any emergency rescue. Only status, counts and digests are
# recorded. Later rescue can never rewrite a snapshot. Each
# snapshot covers exactly one invocation: the attempts and spawns
# recorded against that invocation at the shared creation seam.

_FIXTURE_SETTLE_FAILURE = "meta transport fixture cleanup did not settle"
_CLEANUP_TIMEOUT = 10.0


def _freeze_observation(value):
    if isinstance(value, dict):
        return MappingProxyType({
            key: _freeze_observation(item)
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_observation(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(value)
    return value


def _task_snapshot(task):
    if task is None:
        return {"present": False, "done": None, "cancelled": None}
    try:
        done = bool(task.done())
    except Exception:
        done = None
    try:
        cancelled = bool(task.cancelled())
    except Exception:
        cancelled = None
    return {"present": True, "done": done, "cancelled": cancelled}


def _pipe_snapshot(proc):
    states = {}
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(proc, name, None) if proc is not None else None
        state = {"present": stream is not None}
        if stream is not None:
            for probe in ("is_closing", "at_eof"):
                method = getattr(stream, probe, None)
                if callable(method):
                    try:
                        state[probe] = bool(method())
                    except Exception:
                        state[probe] = None
        states[name] = state
    return states


# -- Owned public-IO observation -------------------------------------
# The fixture observes the actual public asyncio operations on
# each acquired child's streams, following their real lifetime.
# Each public method is itself a coroutine operation: neither
# the underlying operation nor its value-free pending record is
# constructed until the method actually begins executing, so a
# coroutine that is never advanced, closed before its first
# step, or cancelled before its first step launches no extra
# work, orphans no underlying coroutine, and leaves no fictional
# pending IO. While an operation is genuinely pending, its
# record retains exactly the executing task needed for owned
# cancellation/join. On every terminal exit the scalar state is
# recorded before the faithful result, exception type/data or
# cancellation propagates unchanged, and the record releases
# its strong reference to that task, so terminal history never
# retains returned bytes, partial results or tracebacks
# through it. No stream bytes are read or retained, no unrelated
# tasks are scanned, and completion is never inferred from EOF
# alone.


def _io_operations(spawn):
    return spawn.setdefault("io_operations", [])


def _io_begin(spawn, kind):
    """Register one public operation at its first execution step."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    operation = {"kind": kind, "state": "pending", "task": task}
    _io_operations(spawn).append(operation)
    return operation


async def _io_track(operation, awaitable):
    try:
        result = await awaitable
    except asyncio.CancelledError:
        operation["state"] = "cancelled"
        raise
    except GeneratorExit:
        operation["state"] = "cancelled"
        raise
    except BaseException:
        operation["state"] = "failed"
        raise
    else:
        operation["state"] = "completed"
        return result
    finally:
        # The operation is terminal (or its coroutine is being
        # closed): keep only the scalar kind/state, never the
        # task that could hold its result or exception.
        operation["task"] = None


def _io_snapshot(spawn):
    entries = []
    for operation in list(spawn.get("io_operations", ()) or ()):
        if isinstance(operation, dict):
            entry = {
                "kind": operation.get("kind"),
                "state": operation.get("state"),
            }
            entry.update(_task_snapshot(operation.get("task")))
            entries.append(entry)
        else:
            entries.append({
                "kind": None, "state": None, "present": True,
                "done": None, "cancelled": None,
            })
    return entries


def _real_stream(stream):
    try:
        return object.__getattribute__(stream, "_real")
    except AttributeError:
        return stream


class _ObservedReader:
    """Faithful delegate over one real child stdout/stderr reader.

    Only operation identities and states are recorded on the
    owning spawn. Returned bytes go straight to the caller and
    are never retained by this wrapper.
    """

    def __init__(self, real, spawn, stream_name):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_spawn", spawn)
        object.__setattr__(self, "_stream_name", stream_name)

    async def read(self, n=-1):
        real = object.__getattribute__(self, "_real")
        operation = _io_begin(
            object.__getattribute__(self, "_spawn"),
            object.__getattribute__(self, "_stream_name") + "_read",
        )
        return await _io_track(operation, real.read(n))

    async def readline(self):
        real = object.__getattribute__(self, "_real")
        operation = _io_begin(
            object.__getattribute__(self, "_spawn"),
            object.__getattribute__(
                self, "_stream_name"
            ) + "_readline",
        )
        return await _io_track(operation, real.readline())

    async def readexactly(self, n):
        real = object.__getattribute__(self, "_real")
        operation = _io_begin(
            object.__getattribute__(self, "_spawn"),
            object.__getattribute__(
                self, "_stream_name"
            ) + "_readexactly",
        )
        return await _io_track(operation, real.readexactly(n))

    async def readuntil(self, separator=b"\n"):
        real = object.__getattribute__(self, "_real")
        operation = _io_begin(
            object.__getattribute__(self, "_spawn"),
            object.__getattribute__(
                self, "_stream_name"
            ) + "_readuntil",
        )
        return await _io_track(operation, real.readuntil(separator))

    def __aiter__(self):
        return self

    async def __anext__(self):
        line = await self.readline()
        if line == b"":
            raise StopAsyncIteration
        return line

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)


def _record_cleanup_outcome(spawn, succeeded):
    """Record the one cleanup-attempt outcome for ``spawn``.

    This is a separate required fact from live resource
    settlement. A failed outcome is sticky: a later exit, EOF,
    cleanup call or invocation can never turn it into success.
    """
    if spawn.get("cleanup_succeeded") is False:
        return False
    spawn["cleanup_succeeded"] = bool(succeeded)
    return bool(succeeded)


def _spawn_cleanup_ok(spawn):
    return spawn.get("cleanup_succeeded") is not False


def _spawns_cleanup_ok(spawns):
    return all(_spawn_cleanup_ok(spawn) for spawn in spawns)


def _live_owned_settled(spawn):
    """Both live facts: resources settled and cleanup not failed."""
    return _live_spawn_settled(spawn) and _spawn_cleanup_ok(spawn)


def _spawn_snapshot(spawn):
    proc = spawn.get("proc")
    digest = spawn.get("stdin_sha")
    if isinstance(digest, str):
        digest_text = digest
    else:
        try:
            digest_text = (
                digest.hexdigest() if digest is not None else None
            )
        except Exception:
            digest_text = None
    return {
        "invocation_id": spawn.get("invocation_id"),
        "spawn_index": spawn.get("spawn_index"),
        "proc_present": proc is not None,
        "pid": getattr(proc, "pid", None) if proc is not None else None,
        "returncode": (
            getattr(proc, "returncode", None)
            if proc is not None else None
        ),
        "stdin_bytes": spawn.get("stdin_bytes", 0),
        "stdin_sha256": digest_text,
        "creation_task": _task_snapshot(spawn.get("creation_task")),
        "inner_task": _task_snapshot(
            spawn.get("inner_task") or spawn.get("inner")
        ),
        "rescue_task": _task_snapshot(spawn.get("rescue_future")),
        "io_operations": _io_snapshot(spawn),
        "cleanup_succeeded": spawn.get("cleanup_succeeded"),
        "pipes": _pipe_snapshot(proc),
    }


def _spawn_unsettled_reason(snapshot):
    """One owned-resource settlement predicate, as a fixed reason.

    Returns ``None`` only when the snapshot is fully settled. The
    same predicate governs immutable product-return assertions and
    live rescue-completion checks (through a fresh live snapshot);
    a live check never refreshes an already frozen verdict. Child
    exit alone is not closure: any pending retained creation,
    input/output or rescue work is unsettled, an acquired child
    must have exited, its stdin must be closing and its
    stdout/stderr must be at EOF. An unavailable or failing
    relevant probe is never success. A spawn with no acquired
    child is settled only as failed-settled acquisition, when its
    retained creation work is done.
    """
    for label in ("creation_task", "inner_task", "rescue_task"):
        task = snapshot.get(label) or {}
        if task.get("present") and task.get("done") is not True:
            return "owned creation was unresolved at return"
    for operation in snapshot.get("io_operations", ()) or ():
        state = operation.get("state")
        if state is not None:
            pending = state not in (
                "completed", "failed", "cancelled",
            )
        elif "pending" in operation:
            pending = bool(operation.get("pending"))
        else:
            pending = bool(
                operation.get("present")
                and operation.get("done") is not True
            )
        if pending:
            return (
                "owned input/output operation was pending "
                "at product return"
            )
    if not snapshot.get("proc_present"):
        return None
    if snapshot.get("returncode") is None:
        return "owned child had not exited at product return"
    pipes = snapshot.get("pipes") or {}
    stdin = pipes.get("stdin") or {}
    if not stdin.get("present") or stdin.get("is_closing") is not True:
        return "owned child stdin was not closing at product return"
    for name in ("stdout", "stderr"):
        stream = pipes.get(name) or {}
        if not stream.get("present") or (
            stream.get("at_eof") is not True
        ):
            return "owned child output was not drained at product return"
    return None


def _spawn_settled(snapshot):
    return _spawn_unsettled_reason(snapshot) is None


def _live_spawn_settled(spawn):
    try:
        return _spawn_settled(_spawn_snapshot(spawn))
    except Exception:
        return False


def _events_for(record, key, invocation_id):
    selected = []
    for event in record.get(key, ()):
        if invocation_id in event.get("invocation_ids", ()):
            selected.append({
                "pending": bool(event.get("pending")),
                "sequence": event.get("sequence"),
            })
    return selected


def _invocation_spawns(record, invocation_id):
    """Live spawns owned by one invocation, in creation order."""
    spawns = list(record.get("spawns", ()))
    starts = record.get("invocation_spawn_starts") or {}
    if invocation_id in starts:
        ends = record.get("invocation_spawn_ends") or {}
        end = ends.get(invocation_id, len(spawns))
        return spawns[starts[invocation_id]:end]
    tagged = [
        spawn for spawn in spawns
        if spawn.get("invocation_id") == invocation_id
    ]
    if tagged:
        return tagged
    covered = sum(
        len(observation.get("spawns", ()))
        for observation in record.get(
            "product_return_observations", ()
        )
        if observation.get("invocation_id") != invocation_id
    )
    return spawns[covered:]


def _failed_spawn_snapshot():
    return {
        "invocation_id": None,
        "spawn_index": None,
        "proc_present": False,
        "pid": None,
        "returncode": None,
        "stdin_bytes": 0,
        "stdin_sha256": None,
        "creation_task": {
            "present": True, "done": None, "cancelled": None,
        },
        "inner_task": {
            "present": False, "done": None, "cancelled": None,
        },
        "rescue_task": {
            "present": False, "done": None, "cancelled": None,
        },
        "io_operations": (),
        "cleanup_succeeded": None,
        "pipes": {},
    }


def _record_product_return(record, invocation_id):
    """Freeze one invocation's ownership state at callable return.

    Exactly once per invocation: a second recording returns the
    earlier observation unchanged and never replaces it. Only the
    attempts and spawns owned by ``invocation_id`` are covered, so
    a later invocation can neither resnapshot nor heal earlier
    evidence. No credential, stdin bytes or exception payload is
    retained.
    """
    _ensure_record_fields(record)
    with record["_lock"]:
        for existing in record["product_return_observations"]:
            if existing.get("invocation_id") == invocation_id:
                return existing
        failed = False
        snapshots = []
        for spawn in _invocation_spawns(record, invocation_id):
            try:
                snapshots.append(_spawn_snapshot(spawn))
            except Exception:
                failed = True
                snapshots.append(_failed_spawn_snapshot())
        attempt_starts = record["invocation_attempt_starts"]
        if invocation_id in attempt_starts:
            attempts = (
                record["attempts"] - attempt_starts[invocation_id]
            )
        else:
            attempts = len(snapshots)
        schedule = _events_for(
            record, "schedule_releases", invocation_id
        )
        emergency = _events_for(
            record, "emergency_rescues", invocation_id
        )
        observation = _freeze_observation({
            "invocation_id": invocation_id,
            "attempts": attempts,
            "spawns": snapshots,
            "schedule_releases": schedule,
            "pending_schedule_releases": sum(
                1 for item in schedule if item["pending"]
            ),
            "emergency_rescues": emergency,
            "rescued_before_return": bool(emergency),
            "observation_failed": failed,
        })
        record["product_return_observations"].append(observation)
        return observation


def _begin_invocation(record):
    _ensure_record_fields(record)
    with record["_lock"]:
        if record["active_invocation_ids"]:
            # One stable owner per shared record. Serial use only:
            # ambiguity is rejected before any work is acquired
            # rather than overwriting the current owner.
            raise AssertionError(_FIXTURE_SETTLE_FAILURE)
        record["invocation_seq"] += 1
        invocation_id = record["invocation_seq"]
        record["active_invocation_ids"].append(invocation_id)
        record["invocation_spawn_starts"][invocation_id] = len(
            record["spawns"]
        )
        record["invocation_attempt_starts"][invocation_id] = (
            record["attempts"]
        )
        return invocation_id


def _end_invocation(record, invocation_id):
    _ensure_record_fields(record)
    with record["_lock"]:
        active = record["active_invocation_ids"]
        removed = False
        for index in range(len(active) - 1, -1, -1):
            if active[index] == invocation_id:
                del active[index]
                removed = True
                break
        if removed:
            if invocation_id in record["invocation_spawn_starts"]:
                record["invocation_spawn_ends"][invocation_id] = len(
                    record["spawns"]
                )
            completed = record["completed_invocation_ids"]
            if invocation_id not in completed:
                completed.append(invocation_id)


async def _settle_spawn_for_rescue(spawn, deadline=None):
    """Perform the one cleanup attempt for a spawn; rescue only.

    The return value, also recorded on the spawn through
    ``_record_cleanup_outcome``, is the cleanup-attempt outcome:
    whether this attempt's own work -- stopping the owned child,
    joining retained creation, cancelling/joining retained
    public input/output operations, closing stdin, draining
    remaining stdout/stderr without retaining bytes, and
    awaiting exit -- completed without failure inside one total
    deadline. It is a separate required fact from live resource
    settlement, which outer callers verify with the common
    predicate after any worker join. The executing cleanup
    therefore never waits on or condemns itself (its own task
    and future are not part of its outcome), while every other
    unfinished owned operation still matters. The retained
    creation is awaited behind a shield and is never cancelled
    and forgotten; retained IO operations are cancelled and
    joined before any rescue read, so rescue can never collide
    with a pre-existing read it knows about. A failed outcome
    is sticky through ``_record_cleanup_outcome``.
    """
    if deadline is None:
        deadline = time.monotonic() + _CLEANUP_TIMEOUT

    def _remaining():
        return deadline - time.monotonic()

    work_failed = False
    current_task = asyncio.current_task()
    inner = spawn.get("inner_task") or spawn.get("inner")
    proc = spawn.get("proc")
    if proc is not None and proc.returncode is None:
        try:
            proc.kill()
        except Exception:
            pass
    if inner is not None:
        try:
            inner_done = bool(inner.done())
        except Exception:
            inner_done = False
        if not inner_done:
            if _remaining() <= 0:
                return _record_cleanup_outcome(spawn, False)
            try:
                await asyncio.wait_for(
                    asyncio.shield(inner), timeout=_remaining()
                )
            except Exception:
                try:
                    inner_done = bool(inner.done())
                except Exception:
                    inner_done = False
                if not inner_done:
                    # Unavoidable creation stays retained and
                    # pending; it is never cancelled and forgotten.
                    return _record_cleanup_outcome(spawn, False)
    proc = spawn.get("proc", proc)
    if proc is None:
        return _record_cleanup_outcome(spawn, not work_failed)
    if proc.returncode is None:
        try:
            proc.kill()
        except Exception:
            pass
    # Cancel and join the actually retained owned public
    # input/output operations before assigning any rescue read.
    # Only the executing cleanup's own task is excluded; it must
    # never wait on or condemn itself.
    io_tasks = []
    for operation in list(spawn.get("io_operations", ()) or ()):
        if not isinstance(operation, dict):
            work_failed = True
            continue
        if operation.get("state") != "pending":
            continue
        task = operation.get("task")
        if task is current_task:
            continue
        if task is None:
            work_failed = True
            continue
        try:
            task_done = bool(task.done())
        except Exception:
            task_done = False
        if not task_done:
            try:
                task.cancel()
            except Exception:
                work_failed = True
            io_tasks.append(task)
    if io_tasks:
        if _remaining() <= 0:
            work_failed = True
        else:
            try:
                await asyncio.wait(
                    set(io_tasks), timeout=_remaining()
                )
            except Exception:
                work_failed = True
        for task in io_tasks:
            try:
                if not task.done():
                    work_failed = True
                else:
                    # Join fully: retrieve any failure so the
                    # retained operation is finished business.
                    cancelled = getattr(task, "cancelled", None)
                    retrieve = getattr(task, "exception", None)
                    if (
                        callable(cancelled)
                        and not cancelled()
                        and callable(retrieve)
                    ):
                        retrieve()
            except Exception:
                work_failed = True
    output_pending = False
    for operation in list(spawn.get("io_operations", ()) or ()):
        if (
            isinstance(operation, dict)
            and operation.get("state") == "pending"
            and operation.get("task") is not current_task
        ):
            work_failed = True
            kind = operation.get("kind") or ""
            if kind.startswith(("stdout_", "stderr_")):
                output_pending = True
    stdin = _real_stream(getattr(proc, "stdin", None))
    if stdin is not None:
        closing = False
        is_closing = getattr(stdin, "is_closing", None)
        if callable(is_closing):
            try:
                closing = bool(is_closing())
            except Exception:
                closing = False
                work_failed = True
        if not closing:
            try:
                stdin.close()
            except Exception:
                work_failed = True
    # Drain on the real readers: rescue reads are this cleanup
    # attempt's own work, not new product operations. If a
    # retained output operation is still pending above, no
    # rescue read is assigned at all, so it cannot collide.
    if output_pending:
        work_failed = True
    else:
        for name in ("stdout", "stderr"):
            stream = _real_stream(getattr(proc, name, None))
            read = getattr(stream, "read", None)
            if not callable(read):
                work_failed = True
                continue
            try:
                while True:
                    if _remaining() <= 0:
                        work_failed = True
                        break
                    chunk = await asyncio.wait_for(
                        read(65536), timeout=_remaining()
                    )
                    if not chunk:
                        break
            except Exception:
                work_failed = True
    if proc.returncode is None:
        if _remaining() <= 0:
            work_failed = True
        else:
            try:
                await asyncio.wait_for(
                    proc.wait(), timeout=_remaining()
                )
            except Exception:
                if proc.returncode is None:
                    work_failed = True
    return _record_cleanup_outcome(spawn, not work_failed)


def _rescue_spawns(plan, spawns, deadline=None):
    """Emergency-settle owned ``spawns`` under one deadline.

    Rescue only: it supplies no product-return evidence and never
    rewrites a frozen observation. The return value consumes
    both required facts for every spawn: the one recorded
    cleanup-attempt outcome and live resource settlement. A
    spawn whose cleanup attempt already has an outcome is never
    re-attempted, and a failed outcome is sticky: a later exit,
    EOF, cleanup call or invocation cannot heal it. A cleanup
    future's result and exception are always consumed here; a
    timed-out future is retained on its spawn, recorded failed,
    and never cancelled or forgotten. Already settled spawns
    need no emergency release. Otherwise only the plan's own
    barriers are emergency-released, then each unsettled spawn
    gets its single cleanup attempt on its owning live loop.
    Ownership left on a stopped or closed loop is unverified:
    this fixture cannot revive that loop, join its unobserved
    private tasks, or drain its transports from an unrelated
    loop. Only safe owned emergency disposal (stopping the
    acquired child) is attempted there and final containment
    stays with the independent outer process-tree ceiling.
    """
    spawns = list(spawns)
    if all(_live_owned_settled(spawn) for spawn in spawns):
        return True
    if deadline is None:
        deadline = time.monotonic() + _CLEANUP_TIMEOUT
    _release_plan(plan, emergency=True)
    for spawn in spawns:
        if _live_owned_settled(spawn):
            continue
        if spawn.get("cleanup_succeeded") is not None:
            # The single cleanup attempt already happened: a
            # failed outcome stays failed and a succeeded one is
            # not spent again. Live resources are judged at the
            # common return below.
            continue
        if _live_spawn_settled(spawn):
            continue
        loop = spawn.get("event_loop")
        scheduled = False
        if loop is not None:
            try:
                running = (
                    loop.is_running() and not loop.is_closed()
                )
            except Exception:
                running = False
            if running:
                try:
                    running_here = (
                        asyncio.get_running_loop() is loop
                    )
                except RuntimeError:
                    running_here = False
                remaining = deadline - time.monotonic()
                if not running_here and remaining > 0:
                    future = spawn.get("rescue_future")
                    if future is None:
                        try:
                            future = (
                                asyncio.run_coroutine_threadsafe(
                                    _settle_spawn_for_rescue(
                                        spawn, deadline=deadline
                                    ),
                                    loop,
                                )
                            )
                        except Exception:
                            future = None
                    if future is not None:
                        scheduled = True
                        spawn["rescue_future"] = future
                        # Consume the cleanup attempt itself: its
                        # boolean result and any exception are
                        # required evidence, separate from live
                        # resource settlement below. A timeout
                        # records the attempt failed and leaves
                        # the future retained, never cancelled
                        # or forgotten.
                        try:
                            attempt = future.result(
                                timeout=remaining
                            )
                        except Exception:
                            _record_cleanup_outcome(spawn, False)
                        else:
                            _record_cleanup_outcome(
                                spawn, attempt is True
                            )
        if scheduled:
            continue
        proc = spawn.get("proc")
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        # Dead, closed or unschedulable loop: disposal above is
        # all that is safe. The single attempt outcome is the
        # live resource verdict after disposal; anything else
        # stays explicitly unverified and failed.
        _record_cleanup_outcome(
            spawn, _live_spawn_settled(spawn)
        )
    return all(_live_owned_settled(spawn) for spawn in spawns)
class _FaultingStdoutBuffer:
    """Binary side of :class:`_FaultingStdoutSink`."""

    def __init__(self, sink):
        self._sink = sink

    def write(self, data):
        return self._sink._write_bytes(bytes(data))

    def flush(self):
        return None

    def getvalue(self):
        return self._sink.accepted_bytes()

    def writable(self):
        return True


class _FaultingStdoutSink:
    """Public stdout sink whose writes fail with one synthetic error.

    Every write attempt is counted. The first attempt either
    accepts no bytes or accepts a strict nonempty prefix of the
    intended output, then raises the same fixed ``OSError`` as
    every later attempt; later attempts accept nothing more.
    ``flush`` is inert and is not a write attempt. No successful
    full frame can be delivered through this sink.
    """

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, accept_prefix=False):
        self._accept_prefix = bool(accept_prefix)
        self._accepted = io.BytesIO()
        self.buffer = _FaultingStdoutBuffer(self)
        self.write_attempts = 0
        self.first_write_size = None

    def _write_bytes(self, data):
        self.write_attempts += 1
        if self.first_write_size is None:
            self.first_write_size = len(data)
        if self.write_attempts == 1 and self._accept_prefix and data:
            if len(data) > 1:
                prefix_size = max(1, len(data) // 2)
            else:
                prefix_size = 1
            self._accepted.write(data[:prefix_size])
        raise OSError("synthetic stdout write failure")

    def write(self, data):
        if isinstance(data, str):
            encoded = data.encode("utf-8")
        else:
            encoded = bytes(data)
        return self._write_bytes(encoded)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        return None

    def isatty(self):
        return False

    def accepted_bytes(self):
        return self._accepted.getvalue()


class _ObservedSupervisorStop:
    """Observe genuine public child-stop calls without changing them.

    Wraps the public ``terminate``/``kill``/``send_signal``
    operations of ``asyncio.subprocess.Process`` for one narrow
    invocation. Each wrapper delegates to the captured original
    with unchanged arguments and preserves its exact result or
    exception; only a successful stopping delegation against a
    still-running process is noted. A stop is
    counted only while the record still shows the product-return
    observation count captured on entry, so a stop made by
    post-return emergency rescue is never counted as supervisor
    evidence. All patched attributes are restored on exit.
    """

    def __init__(self, record):
        self._record = record
        self._stopped = []
        self._saved = []
        self._baseline = None

    def __enter__(self):
        self._baseline = self._observation_count()
        subprocess_module = getattr(asyncio, "subprocess", None)
        process_class = getattr(subprocess_module, "Process", None)
        if process_class is None:
            return self
        observer = self

        def _make_wrapper(name, original):
            def _wrapped(proc, *args, **kwargs):
                try:
                    was_running = proc.returncode is None
                except Exception:
                    was_running = False
                is_stop = was_running
                if name == "send_signal":
                    signal_value = (
                        args[0] if args else kwargs.get("sig")
                    )
                    is_stop = was_running and signal_value != 0
                result = original(proc, *args, **kwargs)
                if is_stop:
                    observer._note(proc)
                return result

            return _wrapped

        try:
            for name in ("terminate", "kill", "send_signal"):
                original = getattr(process_class, name, None)
                if original is None:
                    continue
                setattr(
                    process_class, name,
                    _make_wrapper(name, original),
                )
                self._saved.append((process_class, name, original))
        except BaseException:
            self._restore()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._restore()
        return False

    def _restore(self):
        try:
            for process_class, name, original in reversed(
                self._saved
            ):
                setattr(process_class, name, original)
        finally:
            self._saved = []

    def _observation_count(self):
        try:
            observations = self._record.get(
                "product_return_observations", ()
            )
            return len(list(observations or ()))
        except Exception:
            return None

    def _note(self, proc):
        try:
            if self._baseline is None:
                return
            if self._observation_count() != self._baseline:
                return
            if any(item is proc for item in self._stopped):
                return
            self._stopped.append(proc)
        except Exception:
            return

    def stopped(self, proc):
        return proc is not None and any(
            item is proc for item in self._stopped
        )


class _MissingCwdCreationBoundary:
    """Force one genuine OS creation failure at the fixed boundary.

    Installed outside ``_spawn_harness`` so the harness captures
    this object as its real creation callable while its original
    product pre-launch validator still validates the production
    proposal first. The wrapper then validates the exact final
    fixture launch the harness built -- fixed current Python,
    fixed fixture script in ``syn_empty`` mode, three PIPEs and
    the bootstrap environment -- before anything reaches the OS.
    Only a validated launch is delegated, exactly once counted,
    to the captured original ``asyncio.create_subprocess_exec``
    with its sole ``cwd`` replaced by a test-owned nonexistent
    directory, so the actual OS call itself fails. No exception
    is fabricated for a validated launch.
    """

    def __init__(self, missing_cwd):
        self._missing_cwd = str(missing_cwd)
        self._real_exec = None
        self.os_attempts = 0
        self.launch_valid = False
        self.validation_failed = False

    def __enter__(self):
        real_exec = asyncio.create_subprocess_exec
        self._real_exec = real_exec
        boundary = self

        async def _exec(*args, **kwargs):
            if not boundary._validate_launch(args, kwargs):
                boundary.validation_failed = True
                raise AssertionError(
                    "OS creation failure boundary received an "
                    "unapproved launch"
                )
            boundary.launch_valid = True
            attempted = dict(kwargs)
            attempted["cwd"] = boundary._missing_cwd
            boundary.os_attempts += 1
            return await real_exec(*args, **attempted)

        asyncio.create_subprocess_exec = _exec
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._real_exec is not None:
            asyncio.create_subprocess_exec = self._real_exec
            self._real_exec = None
        return False

    def _validate_launch(self, args, kwargs):
        try:
            expected_argv = (
                sys.executable, "-I", "-B",
                str(_FIXTURE_SCRIPT), "syn_empty",
            )
            if tuple(args) != expected_argv:
                return False
            if kwargs.get("stdin") != asyncio.subprocess.PIPE:
                return False
            if kwargs.get("stdout") != asyncio.subprocess.PIPE:
                return False
            if kwargs.get("stderr") != asyncio.subprocess.PIPE:
                return False
            if kwargs.get("cwd") != str(_EXPECTED_PRODUCT_DIR):
                return False
            allowed_keys = {
                "stdin", "stdout", "stderr", "env", "cwd",
                "limit", "close_fds", "bufsize", "shell",
            }
            if not set(kwargs).issubset(allowed_keys):
                return False
            if kwargs.get("shell", False):
                return False
            env = kwargs.get("env")
            if env is None:
                return False
            _validate_bootstrap_env(dict(env))
        except Exception:
            return False
        return True
_B2_UNRESOLVED = "instrumentation unresolved: required B2 seam not reached"
_B2_HOLD_CEILING_SECONDS = 15.0


def _b2_prepare_body_function():
    """Return the accepted helper module and its real ``_prepare_body``.

    Resolved only when a B2 runtime context is entered, never at
    import time.  ``(None, None)`` means the supported seam is
    unavailable and the owning case must fail instrumentation-
    unresolved rather than pass.
    """
    try:
        http_module = importlib.import_module("teacher_demo.meta_http")
    except Exception:
        return None, None
    prepare = getattr(http_module, "_prepare_body", None)
    if prepare is None or getattr(prepare, "__code__", None) is None:
        return None, None
    return http_module, prepare


def _b2_expected_request_body(payload, credential, timeout_seconds):
    """Exact POST bytes the accepted helper prepares for ``payload``.

    Derived from the genuine accepted ``_prepare_body`` on a deep
    copy, outside any hold.  ``None`` means the expected body could
    not be derived honestly and the owning case must fail
    instrumentation-unresolved before any worker or server starts.
    """
    _http_module, prepare = _b2_prepare_body_function()
    if prepare is None:
        return None
    try:
        prepared = prepare(
            copy.deepcopy(payload), credential, timeout_seconds
        )
    except Exception:
        return None
    if isinstance(prepared, bytes):
        return prepared
    return None


class _B2PreparationHold:
    """Hold one successful return of the real parent ``_prepare_body``.

    The original function is delegated unchanged (genuine
    validation/copy/serialization run first); only delivery of its
    first successful result to the parent worker is held, finitely.
    The canonical helper attribute and every product-module alias
    bound to the identical function/code object are patched, so an
    imported alias cannot silently bypass the instrument.  Only
    scalar evidence is retained; arguments and results are never
    stored.  All patches are restored on exit, which the owning
    case performs only after its worker has settled.
    """

    def __init__(self, product_module):
        self._product_module = product_module
        self.ready = threading.Event()
        self.release = threading.Event()
        self.available = False
        self.calls = 0
        self.held = 0
        self.thread_ident = None
        self.code_identity_matched = False
        self.alias_names = ()
        self.ceiling_expired = False
        self._saved = []
        self._original = None

    def __enter__(self):
        http_module, original = _b2_prepare_body_function()
        if original is None:
            return self
        self.available = True
        self._original = original
        original_code = original.__code__
        observer = self

        def _wrapper(*args, **kwargs):
            result = original(*args, **kwargs)
            observer.calls += 1
            if observer.held == 0:
                observer.held = 1
                observer.thread_ident = threading.get_ident()
                observer.code_identity_matched = (
                    getattr(original, "__code__", None) is original_code
                )
                observer.ready.set()
                if not observer.release.wait(
                    timeout=_B2_HOLD_CEILING_SECONDS
                ):
                    observer.ceiling_expired = True
            return result

        self._saved.append((http_module, "_prepare_body", original))
        http_module._prepare_body = _wrapper
        aliases = []
        module = self._product_module
        if module is not None:
            for name, value in list(vars(module).items()):
                if value is original or (
                    getattr(value, "__code__", None) is original_code
                    and callable(value)
                ):
                    self._saved.append((module, name, value))
                    setattr(module, name, _wrapper)
                    aliases.append(name)
        self.alias_names = tuple(sorted(aliases))
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release.set()
        for owner, name, value in reversed(self._saved):
            try:
                setattr(owner, name, value)
            except Exception:
                pass
        self._saved = []
        self._original = None
        self._product_module = None
        return False


class _B2DecodeHold:
    """Hold one successful stdlib decode of one exact expected text.

    ``json.loads`` and ``json.JSONDecoder.decode`` are delegated
    with unchanged arguments, hooks and results; only the first
    successful return whose input is exactly the precomputed
    complete synthetic-result JSON text (never input JSON, fixture
    loading or nested content) is held, finitely, in the calling
    worker thread.  The decoded value is never retained.
    """

    def __init__(self, expected_text):
        self._expected_text = expected_text
        self._expected_bytes = expected_text.encode("utf-8")
        self.ready = threading.Event()
        self.release = threading.Event()
        self.matched = 0
        self.thread_ident = None
        self.ceiling_expired = False
        self._saved_loads = None
        self._saved_decode = None

    def _maybe_hold(self, value):
        if self.matched:
            return
        if isinstance(value, str):
            if value != self._expected_text:
                return
        elif isinstance(value, (bytes, bytearray, memoryview)):
            if bytes(value) != self._expected_bytes:
                return
        else:
            return
        self.matched = 1
        self.thread_ident = threading.get_ident()
        self.ready.set()
        if not self.release.wait(timeout=_B2_HOLD_CEILING_SECONDS):
            self.ceiling_expired = True

    def __enter__(self):
        observer = self
        original_loads = json.loads
        original_decode = json.JSONDecoder.decode
        self._saved_loads = original_loads
        self._saved_decode = original_decode

        def _loads(value, *args, **kwargs):
            result = original_loads(value, *args, **kwargs)
            observer._maybe_hold(value)
            return result

        def _decode(decoder, value, *args, **kwargs):
            result = original_decode(decoder, value, *args, **kwargs)
            observer._maybe_hold(value)
            return result

        json.loads = _loads
        json.JSONDecoder.decode = _decode
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release.set()
        if self._saved_loads is not None:
            json.loads = self._saved_loads
        if self._saved_decode is not None:
            json.JSONDecoder.decode = self._saved_decode
        self._saved_loads = None
        self._saved_decode = None
        self._expected_text = None
        self._expected_bytes = None
        return False


def _b2_spawn_for_proc(record, proc):
    for spawn in record.get("spawns", ()):
        if spawn.get("proc") is proc:
            return spawn
    return None


class _B2TerminalWaitHold:
    """Asynchronously hold one independently witnessed terminal wait.

    Every public ``Process.wait`` is delegated to the original
    first.  Only a successful return for the one acquired process
    in ``record`` is held, and only after zero returncode, closing
    stdin, EOF on both streams and no pending observed IO
    operation on that spawn.  An earlier wait that precedes
    remaining IO passes straight through.  The hold is tracked
    here, separately from the spawn's IO records, and its own
    pending state is never required to be terminal before the
    held wait may return.  Cancellation of the held return
    propagates faithfully; nothing is shielded for the product.
    """

    def __init__(self, record):
        self._record = record
        self.gate = concurrent.futures.Future()
        self.ready = threading.Event()
        self.wait_calls = 0
        self.passthroughs = 0
        self.selected = False
        self.hold_state = "none"
        self.witness = {
            "returncode_zero": False,
            "stdin_closing": False,
            "stdout_eof": False,
            "stderr_eof": False,
            "io_terminal": False,
        }
        self._hold_task = None
        self._saved_wait = None

    def release(self):
        if not self.gate.done():
            try:
                self.gate.set_result(None)
            except Exception:
                pass

    def _is_terminal(self, proc, result):
        if result != 0 or proc.returncode != 0:
            return False
        spawn = _b2_spawn_for_proc(self._record, proc)
        if spawn is None:
            return False
        try:
            stdin_closing = bool(
                proc.stdin is not None and proc.stdin.is_closing()
            )
            stdout_eof = bool(
                proc.stdout is not None and proc.stdout.at_eof()
            )
            stderr_eof = bool(
                proc.stderr is not None and proc.stderr.at_eof()
            )
        except Exception:
            return False
        io_terminal = all(
            operation.get("state") != "pending"
            for operation in spawn.get("io_operations", ())
        )
        self.witness = {
            "returncode_zero": True,
            "stdin_closing": stdin_closing,
            "stdout_eof": stdout_eof,
            "stderr_eof": stderr_eof,
            "io_terminal": io_terminal,
        }
        return (
            stdin_closing and stdout_eof and stderr_eof and io_terminal
        )

    def __enter__(self):
        observer = self
        process_class = asyncio.subprocess.Process
        original_wait = process_class.wait
        self._saved_wait = original_wait

        async def _wait(proc):
            result = await original_wait(proc)
            observer.wait_calls += 1
            if observer.selected or not observer._is_terminal(
                proc, result
            ):
                observer.passthroughs += 1
                return result
            observer.selected = True
            observer.hold_state = "pending"
            try:
                observer._hold_task = asyncio.current_task()
            except RuntimeError:
                observer._hold_task = None
            observer.ready.set()
            try:
                await asyncio.wait_for(
                    asyncio.wrap_future(observer.gate),
                    timeout=_B2_HOLD_CEILING_SECONDS,
                )
            except asyncio.CancelledError:
                observer.hold_state = "cancelled"
                raise
            except TimeoutError:
                # Finite emergency ceiling only; the owning case
                # asserts this state never counts as its evidence.
                observer.hold_state = "ceiling"
                return result
            except BaseException:
                observer.hold_state = "failed"
                raise
            else:
                observer.hold_state = "released"
                return result
            finally:
                observer._hold_task = None

        process_class.wait = _wait
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        if self._saved_wait is not None:
            asyncio.subprocess.Process.wait = self._saved_wait
        self._saved_wait = None
        self._hold_task = None
        self._record = None
        return False


class _B2CreationGateAck:
    """Acknowledge actual suspension of harness ``_create`` on one gate.

    ``asyncio.wrap_future`` is delegated unchanged for every call.
    For the one exact gate future, a ``call_soon`` callback is
    queued on the running loop while the original ``_create`` is
    evaluating its ``await wrap_future(gate)`` expression; that
    callback can only run after the creating task has yielded to
    the loop on the returned pending future.  Wrapper entry,
    attempt counts and thread liveness are not used as readiness.
    """

    def __init__(self, gate):
        self._gate = gate
        self.ready = threading.Event()
        self.wrap_calls = 0
        self.acknowledged = False
        self.wrapped_pending_at_ack = False
        self.ack_unavailable = False
        self._wrapped = None
        self._saved_wrap_future = None

    def _acknowledge(self):
        wrapped = self._wrapped
        self.wrapped_pending_at_ack = bool(
            wrapped is not None and not wrapped.done()
        )
        self.acknowledged = bool(
            self.wrapped_pending_at_ack and not self._gate.done()
        )
        self.ready.set()

    def __enter__(self):
        observer = self
        original_wrap_future = asyncio.wrap_future
        self._saved_wrap_future = original_wrap_future

        def _wrap_future(future, *args, **kwargs):
            wrapped = original_wrap_future(future, *args, **kwargs)
            if future is observer._gate and observer.wrap_calls == 0:
                observer.wrap_calls = 1
                observer._wrapped = wrapped
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    observer.ack_unavailable = True
                else:
                    loop.call_soon(observer._acknowledge)
            return wrapped

        asyncio.wrap_future = _wrap_future
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._saved_wrap_future is not None:
            asyncio.wrap_future = self._saved_wrap_future
        self._saved_wrap_future = None
        self._wrapped = None
        self._gate = None
        return False


class _B2RootTaskObserver:
    """Capture the actual operation-root Task through the public bridge.

    ``BaseEventLoop.run_until_complete`` and ``create_task`` are
    delegated exactly once per call and never given cleanup or
    cancellation logic.  A Task argument is captured directly; a
    coroutine argument is correlated with the original
    ``create_task`` return by exact argument and loop identity.
    Only the first capture from a non-owner thread counts, so
    later Runner shutdown, rescue loops and other workers cannot
    replace the root.  The direct caller shape is not assumed, so
    an added Proactor ``run_forever`` frame is irrelevant.

    ``asyncio.shield``/``gather`` (and exact product-module
    aliases of those originals) are delegated while their
    input-to-returned-future identities are recorded, solely so a
    suspended root waiter's dependency on an owned task can be
    established from identities rather than calls or counts.
    Task/future references live only inside this context.

    While the one candidate bridge is delegated, a worker-thread
    trace counts only ``CancelledError`` exception events in the
    exact current root-coroutine frame while the current Task is
    that root.  Request scheduling, owning-loop callback
    execution, ``Task.cancel()`` acceptance and observed delivery
    are separate scalar phase facts; at most one delivery
    milestone is counted per accepted executed phase, with the
    raw root exception count kept apart.  No frame, exception,
    traceback, message or result value is retained.
    """

    def __init__(self, product_module=None):
        self._product_module = product_module
        self._owner_thread_ident = threading.get_ident()
        self.root_ready = threading.Event()
        self.first_delivery_ready = threading.Event()
        self.second_delivery_ready = threading.Event()
        self.captured_via = "none"
        self.capture_thread_ident = None
        self.bridge_calls = 0
        self.cancel_requests = 0
        self.requests_scheduled = 0
        self.requests_executed = 0
        self.requests_accepted = 0
        self.requests_refused = 0
        self.requests_completed = 0
        self.request_phase = 0
        self.root_cancel_events_raw = 0
        self.first_delivery_link_failures = 0
        self.trace_state = "not_installed"
        self.rechecked_join_route = "none"
        # _executed_mask/requests_executed record owning-loop
        # callback entry only.  _completed_mask/
        # requests_completed are published last, after the
        # accepted/refused outcome and any rechecked route.
        self._executed_mask = 0
        self._accepted_mask = 0
        self._refused_mask = 0
        self._delivered_mask = 0
        self._completed_mask = 0
        self._first_delivery_link = None
        self._root = None
        self._loop = None
        self._pending_coro = None
        self._pending_loop = None
        self._noted_waiter = None
        self._relations = {}
        self._saved = []

    def _capture_root(self, task, loop, via):
        self._root = task
        self._loop = loop
        self.captured_via = via
        self.capture_thread_ident = threading.get_ident()
        self.root_ready.set()

    def __enter__(self):
        observer = self
        loop_class = asyncio.BaseEventLoop
        original_run = loop_class.run_until_complete
        original_create_task = loop_class.create_task
        original_shield = asyncio.shield
        original_gather = asyncio.gather

        def _run_until_complete(loop, future):
            candidate = (
                observer._root is None
                and threading.get_ident()
                != observer._owner_thread_ident
            )
            if not candidate:
                return original_run(loop, future)
            observer.bridge_calls += 1
            if isinstance(future, asyncio.Task):
                observer._capture_root(future, loop, "task")
            elif asyncio.iscoroutine(future):
                if observer._pending_coro is None:
                    observer._pending_coro = future
                    observer._pending_loop = loop
            gettrace = getattr(sys, "gettrace", None)
            settrace = getattr(sys, "settrace", None)
            previous_trace = None
            trace_installed = False
            if gettrace is not None and settrace is not None:
                try:
                    previous_trace = gettrace()
                    settrace(observer._trace)
                    trace_installed = True
                    observer.trace_state = "installed"
                except Exception:
                    observer.trace_state = "unavailable"
            else:
                observer.trace_state = "unavailable"
            try:
                return original_run(loop, future)
            finally:
                if trace_installed:
                    try:
                        settrace(previous_trace)
                    except Exception:
                        pass
                if observer._pending_coro is future:
                    observer._pending_coro = None
                    observer._pending_loop = None

        def _create_task(loop, coro, *args, **kwargs):
            task = original_create_task(loop, coro, *args, **kwargs)
            if (
                observer._root is None
                and observer._pending_coro is coro
                and observer._pending_loop is loop
            ):
                observer._capture_root(task, loop, "create_task")
            return task

        def _shield(awaitable, *args, **kwargs):
            returned = original_shield(awaitable, *args, **kwargs)
            observer._relations[id(returned)] = (
                returned,
                (awaitable,),
                "shield",
            )
            return returned

        def _gather(*awaitables, **kwargs):
            returned = original_gather(*awaitables, **kwargs)
            observer._relations[id(returned)] = (
                returned,
                tuple(awaitables),
                "gather",
                kwargs.get("return_exceptions", False),
            )
            return returned

        self._saved = [
            (loop_class, "run_until_complete", original_run),
            (loop_class, "create_task", original_create_task),
            (asyncio, "shield", original_shield),
            (asyncio, "gather", original_gather),
        ]
        loop_class.run_until_complete = _run_until_complete
        loop_class.create_task = _create_task
        asyncio.shield = _shield
        asyncio.gather = _gather
        module = self._product_module
        if module is not None:
            for name, value in list(vars(module).items()):
                if value is original_shield:
                    self._saved.append((module, name, value))
                    setattr(module, name, _shield)
                elif value is original_gather:
                    self._saved.append((module, name, value))
                    setattr(module, name, _gather)
        return self

    def _root_frame(self):
        root = self._root
        if root is None:
            return None
        get_coro = getattr(root, "get_coro", None)
        if get_coro is None:
            return None
        try:
            coro = get_coro()
        except Exception:
            return None
        for name in ("cr_frame", "gi_frame", "ag_frame"):
            frame = getattr(coro, name, None)
            if frame is not None:
                return frame
        return None

    def _record_root_cancel_event(self):
        self.root_cancel_events_raw += 1
        phase = self.request_phase
        bit = self._phase_bit(phase)
        if not bit:
            return
        if not (self._executed_mask & bit):
            return
        if not (self._accepted_mask & bit):
            return
        if self._delivered_mask & bit:
            return
        if phase == 1:
            link = self._first_delivery_link
            if link is not None:
                # Arm synchronously in this actual first
                # root-frame observation.  Publish the
                # completed phase-1 milestone/event only
                # after that required arming succeeds; a
                # failed link leaves completion unproved.
                try:
                    link.set()
                except Exception:
                    self.first_delivery_link_failures += 1
                    return
            self._delivered_mask |= bit
            self.first_delivery_ready.set()
        elif phase == 2:
            self._delivered_mask |= bit
            self.second_delivery_ready.set()
        else:
            self._delivered_mask |= bit

    def _trace(self, frame, event, arg):
        if event == "exception" and arg is not None:
            try:
                exc_type = arg[0]
            except Exception:
                exc_type = None
            if (
                isinstance(exc_type, type)
                and issubclass(exc_type, asyncio.CancelledError)
                and frame is self._root_frame()
            ):
                try:
                    current = asyncio.current_task()
                except Exception:
                    current = None
                if current is not None and current is self._root:
                    self._record_root_cancel_event()
        return self._trace

    def arm_on_first_delivery(self, event):
        """Set ``event`` synchronously at the first delivery."""
        self._first_delivery_link = event
        if event is not None and self.phase_delivered(1):
            try:
                event.set()
            except Exception:
                pass

    def _phase_bit(self, phase):
        try:
            phase = int(phase)
        except Exception:
            return 0
        if phase < 1 or phase > 30:
            return 0
        return 1 << phase

    def request_executed(self, phase):
        """Completed acknowledgment for ``phase``.

        True only after the owning-loop callback has published
        its final accepted/refused outcome and any rechecked
        route.  Callback entry alone (``_executed_mask`` and
        ``requests_executed``) is a separate diagnostic fact
        and does not satisfy this consumer boundary; a queued
        or unresolved callback therefore remains False here.
        """
        return bool(self._completed_mask & self._phase_bit(phase))

    def request_accepted(self, phase):
        return bool(self._accepted_mask & self._phase_bit(phase))

    def request_refused(self, phase):
        return bool(self._refused_mask & self._phase_bit(phase))

    def phase_delivered(self, phase):
        return bool(self._delivered_mask & self._phase_bit(phase))

    def has_root(self):
        return self._root is not None

    def root_is(self, task):
        return task is not None and task is self._root

    def root_done(self):
        root = self._root
        if root is None:
            return None
        try:
            return bool(root.done())
        except Exception:
            return None

    def root_cancelling(self):
        root = self._root
        cancelling = getattr(root, "cancelling", None)
        if root is None or cancelling is None:
            return None
        try:
            return int(cancelling())
        except Exception:
            return None

    def _current_waiter(self):
        root = self._root
        if root is None:
            return None
        try:
            return getattr(root, "_fut_waiter", None)
        except Exception:
            return None

    def note_current_waiter(self):
        self._noted_waiter = self._current_waiter()
        return self._noted_waiter is not None

    def _still_pending(self, item):
        try:
            return not item.done()
        except Exception:
            return False

    def _witness_from(self, current, targets, route, seen):
        if current is None or id(current) in seen:
            return None
        for target in targets:
            if current is target:
                if self._still_pending(current):
                    return (current, route or "direct")
                return None
        if not self._still_pending(current):
            return None
        seen = seen | {id(current)}
        relation = None
        try:
            candidate = self._relations.get(id(current))
        except Exception:
            candidate = None
        if candidate is not None:
            try:
                if candidate[0] is current:
                    relation = candidate
            except Exception:
                relation = None
        if relation is not None:
            # A recorded original shield/gather is the one
            # wrapper allowed on this path; a second such
            # wrapper (for example gather around shield, or
            # shield around gather) is unsupported, however
            # the outer wrapper is named.
            if route is not None:
                return None
            try:
                items = relation[1]
                kind = relation[2]
            except Exception:
                return None
            if kind not in ("shield", "gather"):
                return None
            try:
                branches = list(items)
            except Exception:
                return None
            if kind == "gather":
                try:
                    children = getattr(
                        current, "_children", None
                    )
                except Exception:
                    children = None
                if isinstance(children, (list, tuple)):
                    for child in children:
                        if all(
                            child is not branch
                            for branch in branches
                        ):
                            branches.append(child)
            for branch in branches:
                witness = self._witness_from(
                    branch, targets, kind, seen
                )
                if witness is not None:
                    return witness
            return None
        if isinstance(current, asyncio.Task):
            try:
                waiter = getattr(current, "_fut_waiter", None)
            except Exception:
                return None
            return self._witness_from(
                waiter, targets, route, seen
            )
        return None

    def _join_witness(self, targets):
        """One current pending owned target and route, or None.

        The root's current waiter is followed through
        still-pending Task waiters and at most one recorded
        original shield/gather wrapper (using, for that
        original gather, its actual children as well as its
        recorded inputs).  Success requires the reached
        target itself, and every intermediate on that one
        path, to still be pending.  Cycles, terminal
        historical relations, unrecorded or unproved links
        and any second wrapper yield ``None``.  The returned
        identity is only a transient witness for the caller
        of this observation; it is not retained here.
        """
        if self._root is None or self.root_done() is not False:
            return None
        try:
            target_tuple = tuple(
                target
                for target in targets
                if target is not None
            )
        except Exception:
            return None
        if not target_tuple:
            return None
        waiter = self._current_waiter()
        if waiter is None:
            return None
        return self._witness_from(
            waiter, target_tuple, None, frozenset()
        )

    def suspended_on_join(self, targets):
        """True for an identity-proven owned join after delivery 1.

        Requires the actual first-phase delivery milestone
        and the single current witness used by ``join_route``:
        one still-pending owned target reached by a currently
        pending waiter path within the supported boundary.
        A waiter identity merely differing from a noted waiter
        is neither required nor treated as sufficient.
        Anything unavailable reads as False so the owning case
        fails instrumentation-unresolved on timeout.
        """
        if self._root is None or not self.phase_delivered(1):
            return False
        return self._join_witness(targets) is not None

    def join_route(self, targets):
        """Return direct/shield/gather for the current owned join.

        This is the route of the same single pending-target
        witness that governs ``suspended_on_join``: an
        existing-Task direct chain or one recorded original
        shield/gather relation (including that original
        gathering future's own children).  Any other waiter
        graph is ``"unavailable"``.
        """
        witness = self._join_witness(targets)
        if witness is None:
            return "unavailable"
        return witness[1]

    def cancel_root(self, targets=None, recheck=None):
        """Schedule one root cancellation on its owning loop.

        Scheduling alone proves nothing: callback execution,
        acceptance/refusal and later delivery are recorded as
        separate phase facts.  When ``targets`` are given (a
        post-first-delivery request), the callback rechecks on
        that same loop the same root/loop, delivery 1 and the
        still-pending identity-proven owned join immediately
        before cancelling, plus the optional scalar ``recheck``;
        a disappeared condition records refusal and issues no
        blind cancellation.  The phase is set immediately before
        the real ``root.cancel()`` call in that callback.
        Callback entry is recorded separately; completion is
        published last, only after the final accepted/refused
        outcome and any rechecked route are available, so
        ``request_executed(phase)`` is a completed
        acknowledgment rather than a callback-entry signal.
        """
        root = self._root
        loop = self._loop
        if root is None or loop is None:
            return False
        try:
            if loop.is_closed():
                return False
        except Exception:
            return False
        phase = self.cancel_requests + 1
        bit = self._phase_bit(phase)
        if not bit:
            return False
        observer = self
        target_tuple = None
        if targets is not None:
            target_tuple = tuple(targets)

        def _deliver():
            observer._executed_mask |= bit
            observer.requests_executed += 1
            allowed = True
            try:
                running = asyncio.get_running_loop()
            except Exception:
                running = None
            if (
                running is not loop
                or observer._root is not root
                or observer._loop is not loop
            ):
                allowed = False
            if allowed:
                try:
                    if root.done():
                        allowed = False
                except Exception:
                    allowed = False
            if allowed and target_tuple is not None:
                if phase < 2 or not observer.phase_delivered(1):
                    allowed = False
                else:
                    witness = observer._join_witness(target_tuple)
                    if witness is None:
                        allowed = False
                    else:
                        observer.rechecked_join_route = witness[1]
            if allowed and recheck is not None:
                try:
                    allowed = bool(recheck())
                except Exception:
                    allowed = False
            if not allowed:
                observer._refused_mask |= bit
                observer.requests_refused += 1
                observer.requests_completed += 1
                observer._completed_mask |= bit
                return
            observer.request_phase = phase
            try:
                accepted = bool(root.cancel())
            except Exception:
                return
            if accepted:
                observer._accepted_mask |= bit
                observer.requests_accepted += 1
            else:
                observer._refused_mask |= bit
                observer.requests_refused += 1
            observer.requests_completed += 1
            observer._completed_mask |= bit

        self.cancel_requests = phase
        self.requests_scheduled = phase
        try:
            loop.call_soon_threadsafe(_deliver)
        except Exception:
            self.cancel_requests = phase - 1
            self.requests_scheduled = phase - 1
            return False
        return True

    def __exit__(self, exc_type, exc, tb):
        for owner, name, value in reversed(self._saved):
            try:
                setattr(owner, name, value)
            except Exception:
                pass
        self._saved = []
        self._relations = {}
        self._root = None
        self._loop = None
        self._pending_coro = None
        self._pending_loop = None
        self._noted_waiter = None
        self._first_delivery_link = None
        self._product_module = None
        return False


class _B2ReaderTerminalHold:
    """Finitely delay terminal propagation of one owned reader task.

    The real ``StreamReader`` operations delegate unchanged.
    Exactly one pending observed stdout/stderr operation on the
    recorded spawn, executing in a non-root task, has its genuine
    ``CancelledError`` held before re-raising it unchanged, so its
    A3 operation record stays pending while the product joins it.
    Ordinarily this requires the instrument to be armed at the
    first actual root delivery.  Narrowly earlier, before that
    delivery, it also requires an acknowledged accepted first
    root-cancel request, the root currently waiting on the
    recorded original ``gather(return_exceptions=False)``, the
    current reader task an actual child of that gather, and
    another distinct unfinished pending observed stdout/stderr
    task from the same spawn also in that gather; that peer stays
    free to propagate the first cancellation to the root.  Every
    unproved relation and ``return_exceptions=True`` is
    ineligible.  The fixture protects only this hold itself
    against further cancellation; root cancellation is never
    caught or shielded here, no result is substituted, no root
    resistance is supplied, and no ownership record is written.
    If no such separate owned task exists on either path,
    ``ready`` never sets and the owning case fails unresolved.
    """

    _METHODS = ("read", "readline", "readexactly", "readuntil")

    def __init__(self, record, root_observer):
        self._record = record
        self._root_observer = root_observer
        self.gate = concurrent.futures.Future()
        self.ready = threading.Event()
        self.armed = threading.Event()
        self.selected = False
        self.selected_kind = "none"
        self.hold_state = "none"
        self.extra_cancels = 0
        self._selected_task = None
        self._saved = []

    def release(self):
        if not self.gate.done():
            try:
                self.gate.set_result(None)
            except Exception:
                pass

    def selected_task(self):
        return self._selected_task

    def hold_pending(self):
        task = self._selected_task
        if not self.selected or task is None:
            return False
        if self.hold_state != "pending" or self.gate.done():
            return False
        try:
            return not task.done()
        except Exception:
            return False

    def _select(self, reader):
        if self.selected:
            return False
        armed = self.armed.is_set()
        try:
            task = asyncio.current_task()
        except RuntimeError:
            return False
        if task is None or self._root_observer.root_is(task):
            return False
        early_children = None
        if not armed:
            observer = self._root_observer
            try:
                if observer is None:
                    return False
                if not observer.request_accepted(1):
                    return False
                if not observer.request_executed(1):
                    return False
                if observer.phase_delivered(1):
                    return False
                if observer.root_done() is not False:
                    return False
                waiter = observer._current_waiter()
                if waiter is None:
                    return False
                if waiter.done():
                    return False
                relation = observer._relations.get(id(waiter))
                if relation is None:
                    return False
                if relation[0] is not waiter:
                    return False
                if relation[2] != "gather":
                    return False
                if len(relation) < 4:
                    return False
                if relation[3] is not False:
                    return False
                children = getattr(waiter, "_children", None)
                if not isinstance(children, (list, tuple)):
                    return False
                if not any(
                    child is task for child in children
                ):
                    return False
                if task.done():
                    return False
                early_children = tuple(children)
            except Exception:
                return False
        for spawn in self._record.get("spawns", ()):
            proc = spawn.get("proc")
            if proc is None:
                continue
            streams = (
                ("stdout", getattr(proc, "stdout", None)),
                ("stderr", getattr(proc, "stderr", None)),
            )
            for stream_name, stream in streams:
                if stream is None or _real_stream(stream) is not reader:
                    continue
                for operation in spawn.get("io_operations", ()):
                    if (
                        operation.get("state") == "pending"
                        and operation.get("task") is task
                        and str(operation.get("kind", "")).startswith(
                            stream_name + "_"
                        )
                    ):
                        if not armed:
                            peer_free = False
                            for peer in spawn.get(
                                "io_operations", ()
                            ):
                                peer_task = peer.get("task")
                                if peer_task is None:
                                    continue
                                if peer_task is task:
                                    continue
                                if not isinstance(
                                    peer_task, asyncio.Task
                                ):
                                    continue
                                if self._root_observer.root_is(
                                    peer_task
                                ):
                                    continue
                                if peer.get("state") != "pending":
                                    continue
                                peer_kind = str(
                                    peer.get("kind", "")
                                )
                                if not (
                                    peer_kind.startswith(
                                        "stdout_"
                                    )
                                    or peer_kind.startswith(
                                        "stderr_"
                                    )
                                ):
                                    continue
                                if not any(
                                    child is peer_task
                                    for child in early_children
                                ):
                                    continue
                                try:
                                    if peer_task.done():
                                        continue
                                except Exception:
                                    continue
                                peer_free = True
                                break
                            if not peer_free:
                                continue
                        self.selected = True
                        self.selected_kind = str(operation["kind"])
                        self._selected_task = task
                        return True
        return False

    async def _hold(self):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _B2_HOLD_CEILING_SECONDS
        self.hold_state = "pending"
        while not self.gate.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                self.hold_state = "ceiling"
                return
            try:
                await asyncio.wait_for(
                    asyncio.shield(asyncio.wrap_future(self.gate)),
                    timeout=remaining,
                )
            except asyncio.CancelledError:
                # Only this fixture hold is kept pending; the
                # reader's original cancellation is re-raised by
                # the caller once the hold releases.
                self.extra_cancels += 1
                continue
            except TimeoutError:
                self.hold_state = "ceiling"
                return
        self.hold_state = "released"

    def __enter__(self):
        observer = self
        reader_class = asyncio.StreamReader

        def _make_wrapper(original):
            async def _wrapper(reader, *args, **kwargs):
                try:
                    return await original(reader, *args, **kwargs)
                except asyncio.CancelledError:
                    if observer._select(reader):
                        observer.hold_state = "pending"
                        observer.ready.set()
                        await observer._hold()
                    raise

            return _wrapper

        for name in self._METHODS:
            original = getattr(reader_class, name)
            self._saved.append((name, original))
            setattr(reader_class, name, _make_wrapper(original))
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        reader_class = asyncio.StreamReader
        for name, original in reversed(self._saved):
            try:
                setattr(reader_class, name, original)
            except Exception:
                pass
        self._saved = []
        self._selected_task = None
        self._record = None
        self._root_observer = None
        return False


class _B2HeldTlsResponse:
    """Hold one genuine fixture-server response after request receipt.

    The actual ``_TlsFixtureServer._write_response`` is delegated
    unchanged, but for the one exact server/spec whose sole
    accepted request body matches the expected POST bytes,
    delegation is held finitely after the request was read.  A
    later write failure against a product-killed peer is recorded
    only as a scalar state, never converted to success.  The
    class attribute is resolved through ``_http`` only when this
    context is entered at runtime.
    """

    def __init__(self, server, spec, expected_body):
        self._server = server
        self._spec = spec
        self._expected_body = expected_body
        self.ready = threading.Event()
        self.release = threading.Event()
        self.matched = 0
        self.delegate_state = "not_run"
        self.ceiling_expired = False
        self._saved_write = None
        self._server_class = None

    def __enter__(self):
        observer = self
        server_class = _http._TlsFixtureServer
        original_write = server_class._write_response
        self._server_class = server_class
        self._saved_write = original_write

        def _write_response(server, tls, spec):
            if (
                server is observer._server
                and spec is observer._spec
                and observer.matched == 0
            ):
                snapshot = server.snapshot()
                requests = snapshot.get("requests", ())
                if (
                    snapshot.get("accepts") == 1
                    and len(requests) == 1
                    and requests[0].get("body") == observer._expected_body
                    and str(requests[0].get("line", "")).startswith(
                        "POST "
                    )
                ):
                    observer.matched = 1
                    observer.ready.set()
                    if not observer.release.wait(
                        timeout=_B2_HOLD_CEILING_SECONDS + 5.0
                    ):
                        observer.ceiling_expired = True
            try:
                original_write(server, tls, spec)
            except BaseException:
                if observer.matched:
                    observer.delegate_state = "failed"
                raise
            else:
                if observer.matched:
                    observer.delegate_state = "completed"

        server_class._write_response = _write_response
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release.set()
        if self._server_class is not None and self._saved_write is not None:
            self._server_class._write_response = self._saved_write
        self._saved_write = None
        self._server_class = None
        self._server = None
        self._spec = None
        self._expected_body = None
        return False


def _b2_large_valid_payload(payload, credential, timeout_seconds,
                            target_bytes):
    """A deep-copied genuine payload enlarged in its longest text leaf.

    The longest existing ordinary-text value is repeated, so no
    new field, control character or invalid shape is introduced,
    until the existing test input serialization reaches
    ``target_bytes``.  Callers choose a target with margin below
    the product input cap and the accepted preparation cap.
    ``None`` means no usable text leaf existed.
    """
    enlarged = copy.deepcopy(payload)
    best = {"length": 0, "parent": None, "key": None, "text": None}

    def _visit(parent, key, value):
        if isinstance(value, str):
            if len(value) > best["length"]:
                best["length"] = len(value)
                best["parent"] = parent
                best["key"] = key
                best["text"] = value
        elif isinstance(value, dict):
            for child_key, child in value.items():
                _visit(value, child_key, child)
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                _visit(value, index, child)

    _visit(None, None, enlarged)
    parent = best["parent"]
    if parent is None or not best["text"]:
        return None
    key = best["key"]
    text = best["text"]
    while len(_make_input_bytes(
        enlarged, credential, timeout_seconds
    )) < target_bytes:
        text = text + best["text"]
        parent[key] = text
    return enlarged


def _b2_wait_stdin_pressure(record, timeout=5.0):
    """Wait for real stdin backpressure, probed on the owning loop.

    Each probe runs through ``call_soon_threadsafe`` on the spawn's
    own loop and reports only scalars.  The unwrapped ``proc.stdin``
    writer is the real ``StreamWriter``: its public ``transport``
    is the stdin pipe transport and ``transport.get_protocol()``
    is only the pipe protocol, while the writer's ``_protocol``
    is the high-level flow-control owner.  One callback verifies
    that exact writer, pipe transport, pipe protocol and
    high-level protocol relationship, then contemporaneously
    requires a live acquired child, a pending recorded
    ``stdin_drain`` operation and its Task, a positive public
    write-buffer size, the high-level protocol paused, and a
    still-pending waiter from its ``_drain_waiters`` deque on
    which that exact Task is suspended.  Nothing is paused,
    gated or written by the probe, and no reader, writer, Task
    or Future is retained in the returned evidence.  Unavailable
    or unproved relationships leave every scalar false.  The
    final scalar dict is returned either way.
    """
    result = {
        "available": False,
        "live_child": False,
        "drain_pending": False,
        "buffer_positive": False,
        "protocol_paused": False,
        "drain_waiter_pending": False,
    }
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        spawns = record.get("spawns", ())
        if not spawns:
            time.sleep(0.01)
            continue
        spawn = spawns[0]
        loop = spawn.get("event_loop")
        if loop is None:
            break
        done = threading.Event()
        box = {}

        def _probe(spawn=spawn, loop=loop, box=box, done=done):
            probe = {
                "available": False,
                "live_child": False,
                "drain_pending": False,
                "buffer_positive": False,
                "protocol_paused": False,
                "drain_waiter_pending": False,
            }
            try:
                proc = spawn.get("proc")
                if proc is None:
                    return
                if getattr(proc, "stdin", None) is None:
                    return
                writer = _real_stream(proc.stdin)
                transport = getattr(writer, "transport", None)
                protocol = getattr(writer, "_protocol", None)
                if transport is None or protocol is None:
                    return
                pipe_protocol = transport.get_protocol()
                subprocess_transport = getattr(
                    proc, "_transport", None
                )
                if pipe_protocol is None:
                    return
                if subprocess_transport is None:
                    return
                waiters = getattr(protocol, "_drain_waiters", None)
                if waiters is None:
                    return
                pipe_transport = (
                    subprocess_transport.get_pipe_transport(0)
                )
                relationships = bool(
                    protocol is not pipe_protocol
                    and getattr(writer, "_transport", None)
                    is transport
                    and getattr(protocol, "stdin", None) is writer
                    and getattr(proc, "_protocol", None)
                    is protocol
                    and getattr(
                        subprocess_transport, "_protocol", None
                    )
                    is protocol
                    and pipe_transport is transport
                    and getattr(pipe_protocol, "pipe", None)
                    is transport
                    and getattr(pipe_protocol, "proc", None)
                    is subprocess_transport
                    and getattr(pipe_protocol, "fd", None) == 0
                    and getattr(writer, "_loop", None) is loop
                    and getattr(protocol, "_loop", None) is loop
                    and spawn.get("event_loop") is loop
                )
                if not relationships:
                    return
                drains = [
                    operation
                    for operation in spawn.get("io_operations", ())
                    if operation.get("kind") == "stdin_drain"
                    and operation.get("state") == "pending"
                    and operation.get("task") is not None
                    and not operation["task"].done()
                ]
                pending_waiters = [
                    waiter
                    for waiter in list(waiters)
                    if waiter is not None and not waiter.done()
                ]
                suspended = any(
                    getattr(operation["task"], "_fut_waiter", None)
                    is waiter
                    for operation in drains
                    for waiter in pending_waiters
                )
                buffer_size = transport.get_write_buffer_size()
                probe.update({
                    "available": True,
                    "live_child": proc.returncode is None,
                    "drain_pending": bool(drains),
                    "buffer_positive": bool(buffer_size > 0),
                    "protocol_paused": bool(
                        getattr(protocol, "_paused", False) is True
                    ),
                    "drain_waiter_pending": bool(suspended),
                })
            except Exception:
                probe = {
                    "available": False,
                    "live_child": False,
                    "drain_pending": False,
                    "buffer_positive": False,
                    "protocol_paused": False,
                    "drain_waiter_pending": False,
                }
            finally:
                box.update(probe)
                done.set()

        try:
            loop.call_soon_threadsafe(_probe)
        except RuntimeError:
            break
        if not done.wait(timeout=0.5):
            continue
        if box:
            result = dict(box)
        if all(result.values()):
            return result
        time.sleep(0.02)
    return result


def _b2_io_progress_scalars(record, stream_name):
    """Scalar public-output progress for one stream of the first spawn."""
    progress = {"completed": 0, "terminal": 0, "pending": 0}
    spawns = record.get("spawns", ())
    if not spawns:
        return progress
    for operation in spawns[0].get("io_operations", ()):
        if not str(operation.get("kind", "")).startswith(
            stream_name + "_"
        ):
            continue
        state = operation.get("state")
        if state == "completed":
            progress["completed"] += 1
            progress["terminal"] += 1
        elif state == "pending":
            progress["pending"] += 1
        else:
            progress["terminal"] += 1
    return progress






def _b2_output_read_wrapper(observer, method_name, original):
    async def _wrapper(reader, *args, **kwargs):
        return await observer._delegate(
            method_name, original, reader, args, kwargs
        )

    return _wrapper


class _B2OutputByteObserver:
    """Finite value-free byte counter for one held-output case.

    Installed explicitly before the original invocation starts,
    this context observes genuine public reader operations on
    the exact acquired stream named at construction.  Each
    original call is delegated once with unchanged arguments;
    returned bytes, exceptions and cancellation pass through
    unchanged to their caller.  Only scalar lengths are kept:
    nonempty returned bytes are counted, empty EOF returns,
    cancellation and errors carrying no data are excluded, and
    actual bytes carried as ``partial`` by the known
    ``asyncio.IncompleteReadError`` are recorded separately
    from ordinary returns.  No body, result, exception,
    reader, Task or Future is retained after a terminal
    observation.  Nested public methods on the same reader
    and Task, such as ``readline`` delegating to
    ``readuntil``, delegate without a second count.  Counting applies only while the recorded operation
    for the selected stream is pending on the current Task,
    on the spawn's owning loop, during the sole active
    original invocation, before any product-return
    observation or emergency rescue for that invocation.
    Collaborators are resolved at context entry.  Exit, after
    its owner has settled, restores the public methods and
    clears every transient reference.
    """

    def __init__(self, record, stream_name):
        self._record = record
        self._stream_name = stream_name
        self._returned_bytes = 0
        self._nonempty_returns = 0
        self._exception_partial_bytes = 0
        self._entered = False
        self._stopped = False
        self._depth = {}
        self._saved = {}
        self._reader_class = None
        self._unwrap = None

    def __enter__(self):
        if self._entered:
            raise RuntimeError("output observer already active")
        if self._stream_name not in ("stdout", "stderr"):
            raise ValueError("output observer needs one stream")
        namespace = globals()
        asyncio_module = namespace.get("asyncio")
        unwrap = namespace.get("_real_stream")
        if asyncio_module is None or unwrap is None:
            raise RuntimeError("output observer unavailable")
        reader_class = getattr(
            asyncio_module, "StreamReader", None
        )
        if reader_class is None:
            streams_module = getattr(
                asyncio_module, "streams", None
            )
            reader_class = getattr(
                streams_module, "StreamReader", None
            )
        if reader_class is None:
            raise RuntimeError("output observer unavailable")
        methods = ("read", "readline", "readexactly", "readuntil")
        self._reader_class = reader_class
        self._unwrap = unwrap
        try:
            for name in methods:
                original = vars(reader_class).get(name)
                if original is None:
                    original = getattr(reader_class, name, None)
                if original is None:
                    raise RuntimeError("output observer unavailable")
                self._saved[name] = original
            for name in methods:
                setattr(
                    reader_class,
                    name,
                    _b2_output_read_wrapper(
                        self, name, self._saved[name]
                    ),
                )
        except BaseException:
            for name, original in list(self._saved.items()):
                try:
                    setattr(reader_class, name, original)
                except Exception:
                    pass
            self._saved.clear()
            self._reader_class = None
            self._unwrap = None
            raise
        self._entered = True
        self._stopped = False
        return self

    def __exit__(self, exc_type, exc, traceback):
        self._stopped = True
        reader_class = self._reader_class
        if reader_class is not None:
            for name, original in list(self._saved.items()):
                try:
                    setattr(reader_class, name, original)
                except Exception:
                    pass
        self._saved.clear()
        self._depth.clear()
        self._reader_class = None
        self._unwrap = None
        self._record = None
        self._entered = False
        return False

    def _gate(self, method_name, reader, task):
        try:
            if not self._entered or self._stopped:
                return False
            if task is None or task.done():
                return False
            record = self._record
            unwrap = self._unwrap
            if not isinstance(record, dict) or unwrap is None:
                return False
            loop = asyncio.get_running_loop()
            stream_name = self._stream_name
            expected_kind = stream_name + "_" + method_name
            for spawn in record.get("spawns", ()):
                if not isinstance(spawn, dict):
                    continue
                if spawn.get("event_loop") is not loop:
                    continue
                proc = spawn.get("proc")
                if proc is None:
                    continue
                public_stream = getattr(proc, stream_name, None)
                if public_stream is None:
                    continue
                if unwrap(public_stream) is not reader:
                    continue
                invocation_id = spawn.get("invocation_id")
                active = list(
                    record.get("active_invocation_ids", ())
                )
                if active != [invocation_id]:
                    return False
                for observation in record.get(
                    "product_return_observations", ()
                ):
                    if observation.get(
                        "invocation_id"
                    ) == invocation_id:
                        return False
                for rescue in record.get(
                    "emergency_rescues", ()
                ):
                    if invocation_id in rescue.get(
                        "invocation_ids", ()
                    ):
                        return False
                for operation in spawn.get("io_operations", ()):
                    if operation.get("kind") != expected_kind:
                        continue
                    if operation.get("state") != "pending":
                        continue
                    if operation.get("task") is task:
                        return True
                return False
            return False
        except Exception:
            return False

    async def _delegate(
        self, method_name, original, reader, args, kwargs
    ):
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is None:
            return await original(reader, *args, **kwargs)
        key = (id(reader), id(task))
        if self._depth.get(key, 0):
            return await original(reader, *args, **kwargs)
        counted = self._gate(method_name, reader, task)
        self._depth[key] = 1
        try:
            result = await original(reader, *args, **kwargs)
        except asyncio.IncompleteReadError as exc:
            if counted and self._gate(method_name, reader, task):
                partial = getattr(exc, "partial", None)
                if isinstance(
                    partial, (bytes, bytearray, memoryview)
                ):
                    self._exception_partial_bytes += len(partial)
            raise
        else:
            if counted and self._gate(method_name, reader, task):
                if isinstance(
                    result, (bytes, bytearray, memoryview)
                ):
                    size = len(result)
                    if size > 0:
                        self._returned_bytes += size
                        self._nonempty_returns += 1
            return result
        finally:
            self._depth.pop(key, None)

    def snapshot(self):
        self._stopped = True
        return {
            "stream": self._stream_name,
            "returned_bytes": int(self._returned_bytes),
            "nonempty_returns": int(self._nonempty_returns),
            "exception_partial_bytes": int(
                self._exception_partial_bytes
            ),
        }
def _load_http_module():
    """Reuse ``test_meta_http`` as a module, never its TestCase."""
    try:
        return importlib.import_module("test_meta_http")
    except ImportError:
        spec = importlib.util.spec_from_file_location(
            "test_meta_http_reused",
            str(_TESTS_DIR / "test_meta_http.py"),
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module


_http = _load_http_module()


def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _import_transport():
    """Import the target under a refuse-all socket guard."""
    with _DirectNetGuard("refuse-all"):
        return importlib.import_module("teacher_demo.meta_transport")


def _expected_result():
    return _http._collector_result(
        _http._healthy_sse(), _http._credential()
    )


def _synthetic_success_frame():
    """Bytes the fixture's ``syn_valid_success`` mode emits."""
    text = json.dumps(
        {"result": _expected_result()},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return text.encode("utf-8") + b"\n"


def _make_input_bytes(payload, credential, timeout_seconds):
    text = json.dumps(
        {
            "payload": payload,
            "credential": credential,
            "timeout_seconds": timeout_seconds,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return text.encode("utf-8")


@contextlib.contextmanager
def _patched(module, name, value):
    """Test-only runtime-bound override seam; always restored."""
    saved = getattr(module, name, _SENTINEL)
    setattr(module, name, value)
    try:
        yield
    finally:
        if saved is _SENTINEL:
            try:
                delattr(module, name)
            except AttributeError:
                pass
        else:
            setattr(module, name, saved)


class _DirectNetGuard:
    """Scoped policy on the shared audit authority in the fixture.

    Entering activates the process-wide numeric IPv4
    stream-loopback floor (all threads); ``refuse-all`` narrows
    only the entering thread/task. Exiting restores that scoped
    policy; the once-registered audit hook is never removed or
    re-added. ``attempts`` holds refused event names only.
    """

    def __init__(self, mode):
        # Acquire the one genuine shared fixture lazily, here and
        # only here. Construction always happens after the
        # protected ``_http`` initialization, so fixture import
        # can never change which HTTP helper module resolved.
        fixture = _shared_fixture_module()
        self._scope = fixture._network_guard(mode)
        self.mode = self._scope.mode

    @property
    def attempts(self):
        return self._scope.attempts

    @property
    def refusal_count(self):
        return self._scope.refusal_count

    def __enter__(self):
        self._scope.__enter__()
        return self

    def __exit__(self, *exc_info):
        return self._scope.__exit__(*exc_info)


def _new_record():
    return {
        "attempts": 0,
        "spawns": [],
        "invocation_seq": 0,
        "active_invocation_ids": [],
        "invocation_spawn_starts": {},
        "invocation_spawn_ends": {},
        "invocation_attempt_starts": {},
        "completed_invocation_ids": [],
        "product_return_observations": [],
        "schedule_releases": [],
        "emergency_rescues": [],
        "teardown_failures": [],
        "_lock": threading.Lock(),
    }


def _fixture_env(production_env):
    """Bootstrap-only env for the substituted fixture child.

    Any supplied production env is validated against the shared
    policy, but the fixture env itself is always rebuilt fresh
    from the live process rather than copied from the proposal.
    """
    if production_env is not None:
        _validate_bootstrap_env(production_env)
    return _safe_bootstrap_env()


class _RecordingStdin:
    """Counting delegate over the real child stdin writer.

    Records only byte counts and a digest of what the supervisor
    actually wrote to the acquired child; the bytes themselves
    (which carry the credential by design, on stdin only) are
    never retained or printed. The async public operations
    (``drain`` and ``wait_closed``) are coroutine operations
    themselves: nothing is registered or launched before they
    actually begin executing; while pending they retain only
    the executing task their owned cancellation/join needs,
    and on any terminal exit they keep only the scalar state,
    with faithful result, error and cancellation propagation.
    Every other attribute delegates unchanged.
    """

    def __init__(self, real, spawn):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_spawn", spawn)

    def write(self, data):
        real = object.__getattribute__(self, "_real")
        spawn = object.__getattribute__(self, "_spawn")
        # Observation failure must propagate; it is never recorded
        # as a successful zero-byte write.
        chunk = bytes(data)
        spawn["stdin_bytes"] += len(chunk)
        spawn["stdin_sha"].update(chunk)
        return real.write(data)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    async def drain(self):
        real = object.__getattribute__(self, "_real")
        spawn = object.__getattribute__(self, "_spawn")
        operation = _io_begin(spawn, "stdin_drain")
        return await _io_track(operation, real.drain())

    async def wait_closed(self):
        real = object.__getattribute__(self, "_real")
        spawn = object.__getattribute__(self, "_spawn")
        operation = _io_begin(spawn, "stdin_wait_closed")
        return await _io_track(operation, real.wait_closed())

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)


@contextlib.contextmanager
def _spawn_harness(plan):
    """Wrap the real ``asyncio.create_subprocess_exec`` boundary.

    Pins the intended production interpreter/flags/script/env/cwd
    from the recorded call, then substitutes only the fixed
    fixture script (``sys.executable -I -B <fixture> <mode> ...``).
    Optional barrier gates hold creation before launch or after
    launch/before handle delivery behind a shielded inner task,
    so a supervisor timeout or cancellation cannot destroy the
    eventual creation the test then requires to be reaped.
    """
    record = _ensure_record_fields(plan["record"])
    plan["record"] = record
    module = plan.get("module")
    real_exec = asyncio.create_subprocess_exec
    saved_module_attr = _SENTINEL
    patch_module_attr = (
        module is not None and "create_subprocess_exec" in vars(module)
    )
    if patch_module_attr:
        saved_module_attr = vars(module)["create_subprocess_exec"]

    async def _wrapped(*args, **kwargs):
        # Refuse an invalid production proposal before any record
        # mutation, barrier, creation task or process exists.
        fixture_env = _validate_production_spawn(
            args, kwargs, module=module
        )
        try:
            creation_task = asyncio.current_task()
        except RuntimeError:
            creation_task = None
        try:
            event_loop = asyncio.get_running_loop()
        except RuntimeError:
            event_loop = None
        with record["_lock"]:
            active = tuple(record["active_invocation_ids"])
            if len(active) != 1:
                # No stable sole owner at the creation seam. An
                # unexpected acquisition outside an invocation (or
                # an ambiguous shared record) is refused before
                # any attempt, spawn or process exists; ownership
                # is never assigned from whichever call is latest.
                raise AssertionError(_FIXTURE_SETTLE_FAILURE)
            spawn = {
                "argv": tuple(args),
                "kwargs": dict(kwargs),
                "kwargs_keys": tuple(sorted(kwargs.keys())),
                "env": dict(kwargs["env"]),
                "cwd": kwargs.get("cwd"),
                "stdin": kwargs.get("stdin"),
                "stdout": kwargs.get("stdout"),
                "stderr": kwargs.get("stderr"),
                "proc": None,
                "stdin_bytes": 0,
                "stdin_sha": hashlib.sha256(),
                "creation_task": creation_task,
                "inner_task": None,
                "inner": None,
                "rescue_future": None,
                "io_operations": [],
                "cleanup_succeeded": None,
                "event_loop": event_loop,
                "invocation_id": active[0],
                "spawn_index": len(record["spawns"]),
            }
            record["attempts"] += 1
            record["spawns"].append(spawn)
        if plan.get("fail"):
            raise FileNotFoundError("synthetic spawn refused")
        fixture_argv = [
            sys.executable, "-I", "-B", str(_FIXTURE_SCRIPT),
            plan["mode"],
        ]
        fixture_argv.extend(
            str(item) for item in plan.get("mode_args", ())
        )
        fixture_kwargs = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "env": fixture_env,
            "cwd": str(_EXPECTED_PRODUCT_DIR),
        }
        for optional in ("limit", "close_fds", "bufsize", "shell"):
            if optional in kwargs:
                fixture_kwargs[optional] = kwargs[optional]
        gate_pre = plan.get("barrier_pre")
        gate_post = plan.get("barrier_post")

        async def _create():
            if gate_pre is not None:
                await asyncio.wrap_future(gate_pre)
            proc = await real_exec(*fixture_argv, **fixture_kwargs)
            spawn["proc"] = proc
            if getattr(proc, "stdin", None) is not None:
                proc.stdin = _RecordingStdin(proc.stdin, spawn)
            # Bind actual public-output ownership at acquisition,
            # before the handle can be delivered: from here on
            # every public read operation on these streams is
            # recorded against this spawn, and rescue reads on
            # the real readers only after those records are
            # cancelled/joined.
            if getattr(proc, "stdout", None) is not None:
                proc.stdout = _ObservedReader(
                    proc.stdout, spawn, "stdout"
                )
            if getattr(proc, "stderr", None) is not None:
                proc.stderr = _ObservedReader(
                    proc.stderr, spawn, "stderr"
                )
            if gate_post is not None:
                await asyncio.wrap_future(gate_post)
            return proc

        inner = asyncio.ensure_future(_create())
        spawn["inner_task"] = inner
        spawn["inner"] = inner
        return await asyncio.shield(inner)

    try:
        asyncio.create_subprocess_exec = _wrapped
        if patch_module_attr:
            module.create_subprocess_exec = _wrapped
        yield record
    finally:
        asyncio.create_subprocess_exec = real_exec
        if patch_module_attr:
            module.create_subprocess_exec = saved_module_attr


def _release_plan(plan, *, emergency=False):
    """Release every barrier for ``plan``; safe to call repeatedly.

    The release is classified before any gate opens: a release
    while a callable is still pending is a deliberate schedule
    release unless marked as emergency rescue. That ordering lets
    the product-return snapshot include releases it needed, while
    an emergency release made before return permanently marks the
    pending invocation as rescued.
    """
    record = plan.get("record") if isinstance(plan, dict) else None
    if isinstance(record, dict):
        _ensure_record_fields(record)
        with record["_lock"]:
            active = tuple(record["active_invocation_ids"])
            key = (
                "emergency_rescues" if emergency
                else "schedule_releases"
            )
            record[key].append({
                "invocation_ids": active,
                "pending": bool(active),
                "sequence": len(record[key]) + 1,
            })
    for key in ("barrier_pre", "barrier_post"):
        gate = plan.get(key)
        if gate is not None:
            try:
                if not gate.done():
                    gate.set_result(None)
            except Exception:
                pass
    release_file = plan.get("release_file")
    if release_file is not None:
        try:
            Path(release_file).write_text("release", encoding="ascii")
        except Exception:
            pass


class _RunHandle:
    """One threaded ``run_payload`` call under an entered harness."""

    def __init__(self, plan, box, thread, harness_cm,
                 invocation_id=None):
        self.plan = plan
        self.box = box
        self.thread = thread
        self.invocation_id = invocation_id
        self.rescue_error = None
        self._cm = harness_cm
        self._closed = False
        self._harness_exited = False

    def finish(self, timeout=25.0):
        self.thread.join(timeout)
        return not self.thread.is_alive()

    def _capture_valid(self):
        """Whether this invocation made its valid original capture.

        Requires exactly one non-failed original observation
        for this invocation on the shared record, consistent
        with any observation the worker retained in the box. A
        legitimately captured empty population
        (invalid/pre-cancelled/expired, settled failed creation)
        is valid; a missing or failed capture is not, even for a
        finished no-child call with an expected exception. This
        inspects only capture identity and status, never the
        callable result or exception payload.
        """
        invocation_id = self.invocation_id
        if invocation_id is None:
            invocation_id = self.box.get("invocation_id")
        if invocation_id is None:
            return False
        record = self.plan.get("record", {})
        recorded = [
            observation
            for observation in record.get(
                "product_return_observations", ()
            )
            if observation.get("invocation_id") == invocation_id
        ]
        if len(recorded) != 1:
            return False
        if recorded[0].get("observation_failed", True):
            return False
        boxed = self.box.get("observation")
        if boxed is None:
            return True
        if boxed.get("invocation_id") != invocation_id:
            return False
        if boxed.get("observation_failed", True):
            return False
        return recorded[0] is boxed or recorded[0] == boxed

    def _record_unusable(self):
        """Stick the failed/unusable state, value-free."""
        if self.rescue_error is None:
            self.rescue_error = AssertionError(
                _FIXTURE_SETTLE_FAILURE
            )
        invocation_id = self.invocation_id
        if invocation_id is None:
            invocation_id = self.box.get("invocation_id")
        if invocation_id is not None:
            try:
                record = _ensure_record_fields(
                    self.plan.get("record", {})
                )
                with record["_lock"]:
                    failures = record["teardown_failures"]
                    if invocation_id not in failures:
                        failures.append(invocation_id)
            except BaseException:
                pass

    def outcome(self):
        # Final consumption, not a peek at the box: a recorded
        # failure, or a finished worker whose own original
        # capture is missing or failed, is unusable even when an
        # expected callable exception was retained separately.
        if self.rescue_error is not None:
            raise AssertionError(_FIXTURE_SETTLE_FAILURE) from None
        if not self.thread.is_alive() and not self._capture_valid():
            self._record_unusable()
            raise AssertionError(_FIXTURE_SETTLE_FAILURE) from None
        return self.box.get("result"), self.box.get("exc")

    def _unsettled(self):
        if self.thread.is_alive():
            return True
        record = self.plan.get("record", {})
        for spawn in record.get("spawns", ()):
            # Both live facts, on a fresh snapshot: resource
            # settlement through the same common predicate as
            # the immutable return assertions (a set returncode
            # alone is not settlement), and the recorded cleanup
            # outcome, which no later exit or EOF can heal.
            if not _live_owned_settled(spawn):
                return True
        return False

    def _force_settle_owned(self, deadline=None):
        record = self.plan.get("record", {})
        return _rescue_spawns(
            self.plan,
            list(record.get("spawns", ())),
            deadline=deadline,
        )

    def _exit_harness(self):
        if not self._harness_exited:
            self._harness_exited = True
            self._cm.__exit__(None, None, None)

    def close(self):
        """Emergency rescue only; it never supplies return evidence.

        Finalization also requires this invocation's own valid
        exactly-once frozen capture from the worker boundary,
        including no-child calls; rescue itself only releases
        owned barriers, settles owned spawns on their owning loop
        where possible and joins the worker before patches are
        restored. Its verdict consumes that capture validity,
        the recorded cleanup outcome and post-join resource
        settlement. One bounded deadline covers every release,
        join, drain and wait. The callable's own
        result/exception in
        ``box`` is retained; a rescue failure raised here never
        replaces an original failure already propagating through a
        caller. An unsettled worker is a failed/unusable fixture,
        never a reusable one.
        """
        if self._closed:
            # A repeated close never turns a recorded failure
            # into success: raise it again unless doing so would
            # replace an exception now unwinding through the
            # caller, which keeps the sticky unusable state.
            if (
                self.rescue_error is not None
                and sys.exc_info()[0] is None
            ):
                raise AssertionError(
                    _FIXTURE_SETTLE_FAILURE
                ) from None
            return
        self._closed = True
        original_active = sys.exc_info()[0] is not None
        failure = None
        settled = True
        force_result = None
        deadline = time.monotonic() + _CLEANUP_TIMEOUT

        def _remaining():
            return max(0.0, deadline - time.monotonic())

        try:
            if self._unsettled():
                _release_plan(self.plan, emergency=True)
                if self.thread.is_alive():
                    # A bounded grace from the same single
                    # deadline, so a worker released by the
                    # emergency barrier opening can return on its
                    # own, while enough of the deadline remains
                    # for owned force-settlement and final join.
                    self.thread.join(
                        min(_remaining(), _CLEANUP_TIMEOUT / 2.0)
                    )
                if self._unsettled():
                    force_result = self._force_settle_owned(
                        deadline=deadline
                    )
                if self.thread.is_alive():
                    self.thread.join(_remaining())
                # Consume the same two facts as the synchronous
                # caller: the recorded cleanup outcome and, after
                # the final join, live resource settlement. A
                # false force result explained only by resources
                # still settling at that instant is re-judged
                # after the join, through the outcome records; a
                # recorded failed cleanup can never be healed by
                # that later exit, EOF or join.
                cleanup_ok = _spawns_cleanup_ok(
                    list(
                        self.plan.get("record", {}).get(
                            "spawns", ()
                        )
                    )
                )
                settled = (
                    not self._unsettled()
                    and cleanup_ok
                    and (force_result is not False or cleanup_ok)
                )
                if not settled:
                    failure = AssertionError(
                        _FIXTURE_SETTLE_FAILURE
                    )
            if not self.thread.is_alive() and (
                not self._capture_valid()
            ):
                # This invocation's own valid exactly-once
                # original capture is a third required fact for
                # finalization, including no-child calls: a
                # finished worker with settled resources and an
                # expected stored exception is still unusable
                # when that capture is missing or failed.
                settled = False
                if failure is None:
                    failure = AssertionError(
                        _FIXTURE_SETTLE_FAILURE
                    )
        except BaseException:
            try:
                settled = not self._unsettled()
            except BaseException:
                settled = False
            failure = AssertionError(_FIXTURE_SETTLE_FAILURE)
        finally:
            # Restore patches only once no owned worker can still
            # execute under them; an unsettled live worker is left
            # to the independent outer ceiling, not returned as a
            # usable fixture.
            if settled or not self.thread.is_alive():
                try:
                    self._exit_harness()
                except BaseException:
                    if failure is None:
                        failure = AssertionError(
                            _FIXTURE_SETTLE_FAILURE
                        )
        self.rescue_error = failure
        if failure is not None:
            self._record_unusable()
        if failure is not None and not original_active:
            raise failure


class _CountingReader:
    """Binary stdin stand-in counting bytes actually served."""

    def __init__(self, data):
        self._bio = io.BytesIO(data)
        self.served = 0
        self.read_calls = 0

    def read(self, size=-1):
        self.read_calls += 1
        if size is None:
            chunk = self._bio.read()
        else:
            chunk = self._bio.read(size)
        self.served += len(chunk)
        return chunk

    def read1(self, size=-1):
        return self.read(size)

    def readline(self, size=-1):
        chunk = self._bio.readline(size)
        self.served += len(chunk)
        return chunk

    def readable(self):
        return True

    def seekable(self):
        return False


class _FakeIn:
    def __init__(self, reader):
        self.buffer = reader


class _FakeOut:
    def __init__(self):
        self.buffer = io.BytesIO()

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        return self.buffer.write(data)

    def flush(self):
        return None


class MetaTransportTest(unittest.TestCase):
    # -- shared fixture helpers (never product logic) ----------------

    def _plan(self, module, mode, mode_args=(), **extra):
        plan = {
            "record": _new_record(),
            "module": module,
            "mode": mode,
            "mode_args": tuple(mode_args),
        }
        plan.update(extra)
        return plan

    def _barrier_files(self, plan=None):
        handle = tempfile.TemporaryDirectory()
        self.addCleanup(handle.cleanup)
        base = Path(handle.name)
        ready = base / "ready"
        release = base / "release"
        if plan is not None:
            plan["release_file"] = release
        return ready, release

    def _invoke(self, module, plan, payload, **call_kwargs):
        result = None
        exc = None
        record = _ensure_record_fields(plan["record"])
        plan["record"] = record
        invocation_id = _begin_invocation(record)
        observation = None
        callable_entered = False
        teardown_failure = None
        try:
            with _spawn_harness(plan), _DirectNetGuard("loopback"):
                try:
                    callable_entered = True
                    try:
                        result = module.run_payload(
                            payload, **call_kwargs
                        )
                    except BaseException as caught:  # for assertion
                        exc = caught
                    # Freeze ownership evidence at the actual
                    # return, before either context exits and
                    # before any rescue could change the observed
                    # state. If this capture is itself
                    # interrupted, ``observation`` stays ``None``
                    # and the covering finally below leaves the
                    # invocation explicitly unverified while the
                    # original interruption keeps propagating.
                    observation = _record_product_return(
                        record, invocation_id
                    )
                finally:
                    # Covering owned teardown: it runs on every
                    # exit path -- normal return, captured
                    # callable exception, and capture
                    # interruption -- while the harness is still
                    # entered. It consumes both facts: the
                    # recorded cleanup outcome (inside the common
                    # rescue verdict) and live resource
                    # settlement. It never supplies or improves
                    # product-return evidence, and remaining
                    # ownership on a loop the callable already
                    # stopped or closed stays unverified, never
                    # revived here. No failure raised in this
                    # block may replace an exception already
                    # propagating through it, so the verdict is
                    # only recorded here and acted on below, when
                    # nothing original is in flight.
                    try:
                        spawns = _invocation_spawns(
                            record, invocation_id
                        )
                        settled = _rescue_spawns(plan, spawns)
                    except Exception:
                        settled = False
                    captured = (
                        observation is not None
                        and not observation.get(
                            "observation_failed", True
                        )
                    )
                    if not settled or (
                        callable_entered and not captured
                    ):
                        with record["_lock"]:
                            failures = record["teardown_failures"]
                            if invocation_id not in failures:
                                failures.append(invocation_id)
                        teardown_failure = AssertionError(
                            _FIXTURE_SETTLE_FAILURE
                        )
        finally:
            _end_invocation(record, invocation_id)
        if teardown_failure is not None:
            # Unconditional: even an expected callable exception
            # kept separately in ``exc`` above cannot make a
            # failed or unverified teardown usable, so the fixed
            # value-free fixture failure propagates instead of a
            # ``(result, exc)`` pair that would conceal it from
            # consumers that never call ``_assert_reaped``.
            raise teardown_failure
        return result, exc

    def _start(self, module, plan, payload, name="meta-transport-call",
               **call_kwargs):
        record = _ensure_record_fields(plan["record"])
        plan["record"] = record
        # Establish the stable sole owner before acquiring any
        # harness or work; a record already in use is refused
        # here, before patches are installed.
        invocation_id = _begin_invocation(record)
        harness_cm = _spawn_harness(plan)
        try:
            harness_cm.__enter__()
        except BaseException:
            _end_invocation(record, invocation_id)
            raise
        box = {}
        box["invocation_id"] = invocation_id

        def target():
            try:
                with _DirectNetGuard("loopback"):
                    try:
                        box["result"] = module.run_payload(
                            payload, **call_kwargs
                        )
                    except BaseException as caught:
                        box["exc"] = caught
                    # Recorded here, in the worker, immediately at
                    # return: no later handle rescue can move or
                    # heal this observation.
                    box["observation"] = _record_product_return(
                        record, invocation_id
                    )
            except BaseException as caught:
                box.setdefault("exc", caught)
            finally:
                _end_invocation(record, invocation_id)

        thread = threading.Thread(target=target, name=name, daemon=True)
        try:
            thread.start()
        except BaseException:
            _end_invocation(record, invocation_id)
            harness_cm.__exit__(None, None, None)
            raise
        return _RunHandle(
            plan, box, thread, harness_cm,
            invocation_id=invocation_id,
        )

    def _assert_protocol_error(self, exc, outcome):
        from teacher_demo import meta_protocol

        self.assertTrue(
            isinstance(exc, meta_protocol.MetaProtocolError),
            "expected a typed protocol refusal",
        )
        self.assertTrue(
            getattr(exc, "outcome", None) == outcome,
            "protocol outcome mismatch",
        )
        self.assertTrue(
            exc.args == (_REFUSED_MSG,), "protocol refusal text mismatch"
        )
        self.assertTrue(
            set(vars(exc)) == {"outcome"},
            "protocol refusal fields are not closed",
        )

    def _assert_transport_invalid(self, exc):
        self.assertTrue(
            type(exc) is ValueError,
            "expected a plain ValueError refusal",
        )
        self.assertTrue(
            exc.args == (_TRANSPORT_INVALID,),
            "transport refusal text mismatch",
        )
        self.assertTrue(
            vars(exc) == {},
            "transport refusal carries instance data",
        )

    def _assert_no_marker(self, exc, markers):
        text = (
            str(exc) + " " + repr(getattr(exc, "args", ()))
            + " " + repr(vars(exc))
        )
        for marker in markers:
            self.assertTrue(
                marker not in text,
                "diagnostic or secret data leaked into failure",
            )

    def _assert_production_spawn(self, spawn, module, credential,
                                 extra_forbidden=()):
        # Late assertion reuses the one pre-launch validator; it
        # keeps no second, divergent copy of the spawn rules.
        kwargs = spawn.get("kwargs")
        if not isinstance(kwargs, dict):
            kwargs = {
                "stdin": spawn.get("stdin"),
                "stdout": spawn.get("stdout"),
                "stderr": spawn.get("stderr"),
                "env": spawn.get("env"),
                "cwd": spawn.get("cwd"),
            }
        try:
            _validate_production_spawn(
                spawn.get("argv", ()), dict(kwargs), module=module
            )
        except Exception:
            self.fail("production child spawn proposal is not closed")
        argv = [str(item) for item in spawn["argv"]]
        for item in argv:
            self.assertTrue(
                credential not in item,
                "credential present in production child argv",
            )
        env = spawn["env"]
        keys_upper = {str(key).upper() for key in env}
        for bad in tuple(_FORBIDDEN_ENV) + tuple(extra_forbidden):
            self.assertTrue(
                bad.upper() not in keys_upper,
                "ambient authority reached child env: " + bad,
            )
        for key, value in env.items():
            self.assertTrue(
                credential not in str(key)
                and credential not in str(value),
                "credential present in production child env",
            )

    def _assert_reaped(self, record, expected_codes=None):
        # Consumes only the immutable observations frozen at
        # actual callable returns, across the whole history in
        # original invocation/creation order. It never polls or
        # refreshes live process state and never consults a latest
        # observation, so a later healthy call -- or a child later
        # killed by emergency rescue -- cannot heal earlier
        # evidence or become product ownership evidence.
        record = _ensure_record_fields(record)
        spawns = record["spawns"]
        self.assertTrue(spawns, "no child spawn was recorded")
        observations = list(
            record.get("product_return_observations", ())
        )
        self.assertTrue(
            observations,
            "product-return ownership was not observed",
        )
        self.assertTrue(
            not record.get("active_invocation_ids"),
            "an invocation is still in progress",
        )
        self.assertTrue(
            not record.get("teardown_failures"),
            _FIXTURE_SETTLE_FAILURE,
        )
        invocation_ids = [
            observation.get("invocation_id")
            for observation in observations
        ]
        self.assertEqual(
            len(invocation_ids), len(set(invocation_ids)),
            "an invocation was observed more than once",
        )
        for completed_id in record.get(
            "completed_invocation_ids", ()
        ):
            self.assertTrue(
                completed_id in invocation_ids,
                "an invocation has no product-return observation",
            )
        observed = []
        for observation in observations:
            self.assertTrue(
                not observation["observation_failed"],
                "product-return ownership observation failed",
            )
            self.assertTrue(
                not observation["rescued_before_return"],
                "ownership evidence was supplied by emergency rescue",
            )
            observed.extend(observation["spawns"])
        self.assertEqual(
            len(observed), len(spawns),
            "a spawn is missing from product-return evidence",
        )
        if all(
            snapshot.get("spawn_index") is not None
            for snapshot in observed
        ):
            self.assertEqual(
                [
                    snapshot["spawn_index"]
                    for snapshot in observed
                ],
                list(range(len(spawns))),
                "product-return evidence does not cover every "
                "spawn exactly once in creation order",
            )
        codes = []
        for snapshot in observed:
            reason = _spawn_unsettled_reason(snapshot)
            self.assertTrue(reason is None, reason)
            if snapshot["proc_present"]:
                codes.append(snapshot["returncode"])
            # With no acquired child, only failed-settled
            # acquisition (retained creation done, checked by the
            # common predicate above) has no code to reap; a
            # pending creation was already refused there. The
            # non-empty population check above still rejects call
            # sites that expected an acquired child but recorded
            # no spawn at all, and ``expected_codes`` still
            # rejects a history with no acquired code to match.
        if expected_codes is not None:
            self.assertEqual(codes, list(expected_codes))
        return codes

    def _assert_bearer(self, request, credential):
        values = [
            value for name, value in request["headers"]
            if name.lower() == "authorization"
        ]
        self.assertEqual(len(values), 1)
        self.assertTrue(
            hmac.compare_digest(values[0], "Bearer " + credential),
            "authorization Bearer [REDACTED]",
        )

    def _call_kwargs(self, credential, socket_timeout=5.0, total=20.0,
                     cancel_event=_SENTINEL):
        kwargs = {
            "credential": credential,
            "timeout_seconds": socket_timeout,
            "total_timeout_seconds": total,
        }
        if cancel_event is not _SENTINEL:
            kwargs["cancel_event"] = cancel_event
        return kwargs

    def _direct_child(self, module, input_bytes, guard_mode="refuse-all",
                      specs=None, bounds=None, stdout_sink=None):
        """Run the real ``_child_main`` in-process under a guard."""
        reader = _CountingReader(input_bytes)
        fake_out = _FakeOut() if stdout_sink is None else stdout_sink
        fake_err = _FakeOut()
        out = {
            "reader": reader, "exc": None, "exit_code": None,
            "server": None, "attempts": None,
        }
        with contextlib.ExitStack() as stack:
            guard = stack.enter_context(_DirectNetGuard(guard_mode))
            server = None
            if specs is not None:
                server = stack.enter_context(
                    _http._running_server(specs)
                )
                stack.enter_context(_http._client_seams(server.port, True))
            for name, value in (bounds or {}).items():
                stack.enter_context(_patched(module, name, value))
            saved = (sys.stdin, sys.stdout, sys.stderr)
            sys.stdin = _FakeIn(reader)
            sys.stdout = fake_out
            sys.stderr = fake_err
            try:
                module._child_main()
            except SystemExit as caught:
                out["exit_code"] = caught.code
            except BaseException as caught:  # returned for assertion
                out["exc"] = caught
            finally:
                sys.stdin, sys.stdout, sys.stderr = saved
            if server is not None:
                _wait_until(
                    lambda: server.snapshot()["handled"] >= 1,
                    timeout=5.0,
                )
                out["server"] = server.snapshot()
            out["attempts"] = list(guard.attempts)
        out["stdout"] = fake_out.buffer.getvalue()
        out["stderr"] = fake_err.buffer.getvalue()
        return out

    def _parse_frame(self, data):
        """Strictly parse one newline-terminated child frame."""
        from teacher_demo import meta_protocol

        if not data.endswith(b"\n") or data.endswith(b"\n\n"):
            return None
        # One complete object exactly fills the bytes before the
        # final LF: no byte (including JSON whitespace) may sit
        # before its opening or after its closing boundary.
        # Whitespace within the object remains legal and is not
        # trimmed; strict loading then validates the whole text.
        body = data[:-1]
        if not body.startswith(b"{") or not body.endswith(b"}"):
            return None
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return None
        try:
            return meta_protocol._strict_json_loads(text)
        except Exception:
            return None

    def _assert_frame(self, out, expected):
        self.assertTrue(
            out["exc"] is None, "direct child raised unexpectedly"
        )
        self.assertTrue(
            out["exit_code"] in (None, 0),
            "handled child frame must exit zero",
        )
        self.assertEqual(
            out["stderr"], b"", "child wrote diagnostics to stderr"
        )
        # Legal internal JSON whitespace may itself contain
        # newlines; only the terminal newline is frame structure.
        # ``_parse_frame`` below still requires exactly one
        # complete strict object before that terminal newline.
        self.assertTrue(
            out["stdout"].endswith(b"\n")
            and not out["stdout"].endswith(b"\n\n"),
            "child output does not end with exactly one "
            "terminal newline",
        )
        frame = self._parse_frame(out["stdout"])
        self.assertTrue(frame is not None, "child frame is not strict")
        self.assertEqual(frame, expected)

    def _run_guarded_process(self, input_bytes):
        """Real script as a process under the guard-bootstrap mode.

        Uses the same fresh OS-bootstrap-only environment policy
        as the shared production-spawn validator; it is not a
        second, unvalidated subprocess door.
        """
        env = _safe_bootstrap_env()
        _validate_bootstrap_env(env)
        completed = subprocess.run(
            [
                sys.executable, "-I", "-B", str(_FIXTURE_SCRIPT),
                "real_guarded",
            ],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            env=env,
            cwd=str(_TESTS_DIR),
            shell=False,
        )
        return completed

    # -- 1: real child roundtrip, one send ---------------------------

    def test_real_child_roundtrip_preserves_safe_result_and_one_send(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_transport")
        )
        module = _import_transport()
        self.assertEqual(module.MAX_INPUT_BYTES, 128 * 1024 * 1024)
        self.assertEqual(module.MAX_OUTPUT_BYTES, 4 * 1024 * 1024)
        self.assertEqual(module.MAX_STDERR_BYTES, 64 * 1024)
        self.assertEqual(module.IO_CHUNK_BYTES, 64 * 1024)

        payload = _http._genuine_payload()
        caller_snapshot = copy.deepcopy(payload)
        credential = _http._credential()
        body = _http._healthy_sse()
        expected = _http._collector_result(body, credential)
        specs = [
            _http._length_spec(body, fragment=3),
            _http._length_spec(body, fragment=3),
        ]
        with _http._running_server(specs) as server:
            plan = self._plan(
                module, "real_child", (str(server.port),)
            )
            result, exc = self._invoke(
                module, plan, payload,
                **self._call_kwargs(credential),
            )
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(
                set(result),
                {"raw", "model_name", "response_id", "outcome"},
            )
            self.assertEqual(result, expected)
            stream = _http._load_fixture_json("meta_stream_inputs.json")
            self.assertEqual(result["raw"], stream["raw"])
            self.assertEqual(
                result["raw"]["sections"][0]["text"],
                "  نَصّ عربي ١٢ / 12 𝄞\nثانٍ  ",
            )
            self.assertEqual(result["response_id"], stream["response_id"])
            self.assertEqual(payload, caller_snapshot)
            self.assertIs(payload["stream"], False)
            for ensure_ascii in (False, True):
                self.assertTrue(
                    credential
                    not in json.dumps(result, ensure_ascii=ensure_ascii),
                    "credential present in IPC result",
                )
            _wait_until(
                lambda: server.snapshot()["handled"] >= 1, timeout=5.0
            )
            snapshot = server.snapshot()
            self.assertEqual(snapshot["accepts"], 1)
            self.assertEqual(len(snapshot["requests"]), 1)
            request = snapshot["requests"][0]
            self.assertTrue(
                request["line"].startswith("POST /v1/responses HTTP/"),
                "wire request line is not the single expected POST",
            )
            self._assert_bearer(request, credential)
            outgoing = json.loads(request["body"].decode("utf-8"))
            expected_outgoing = copy.deepcopy(caller_snapshot)
            expected_outgoing["stream"] = True
            self.assertEqual(outgoing, expected_outgoing)
            self.assertEqual(
                len(request["body"]),
                len(json.dumps(
                    expected_outgoing, ensure_ascii=False,
                    allow_nan=False, separators=(",", ":"),
                ).encode("utf-8")),
            )

            record = plan["record"]
            self.assertEqual(record["attempts"], 1)
            spawn = record["spawns"][0]
            self._assert_production_spawn(spawn, module, credential)
            self.assertTrue(
                spawn["stdin_bytes"] > 0, "no private input was sent"
            )
            self._assert_reaped(record, expected_codes=[0])

            # A second identical call uses a fresh owned child and
            # sends exactly one further POST; results stay equal
            # but independently owned.
            result2, exc2 = self._invoke(
                module, plan, payload,
                **self._call_kwargs(credential),
            )
            self.assertTrue(exc2 is None, "expected success")
            self.assertEqual(result2, expected)
            self.assertIsNot(result2, result)
            _wait_until(
                lambda: server.snapshot()["handled"] >= 2, timeout=5.0
            )
            snapshot = server.snapshot()
            self.assertEqual(snapshot["accepts"], 2)
            self.assertEqual(len(snapshot["requests"]), 2)
            self.assertEqual(record["attempts"], 2)
            self._assert_reaped(record, expected_codes=[0, 0])
            self.assertEqual(payload, caller_snapshot)

    # -- 2: child input strictness and closed output -----------------

    def test_child_input_and_failure_output_are_closed(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_transport")
        )
        module = _import_transport()
        payload = _http._genuine_payload()
        credential = _http._credential()
        valid_input = _make_input_bytes(payload, credential, 5)
        invalid_frame = {"failure": "invalid_schema"}

        payload_text = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        )
        structural = [
            ("empty stdin", b""),
            ("whitespace only", b"  \n"),
            ("invalid utf-8", b'{"payload":{},"credential":"x\xff",'
                              b'"timeout_seconds":5}'),
            ("malformed json", b"{not json}"),
            ("top-level array", b"[]"),
            ("top-level number", b"3"),
            ("top-level null", b"null"),
            ("byte order mark", b"\xef\xbb\xbf" + valid_input),
            ("second object", valid_input + b" {}"),
            ("duplicate payload key",
             ('{"payload":' + payload_text + ',"payload":' + payload_text
              + ',"credential":"x","timeout_seconds":5}').encode("utf-8")),
            ("missing payload",
             b'{"credential":"x","timeout_seconds":5}'),
            ("missing credential",
             ('{"payload":' + payload_text
              + ',"timeout_seconds":5}').encode("utf-8")),
            ("missing timeout",
             ('{"payload":' + payload_text
              + ',"credential":"x"}').encode("utf-8")),
            ("extra key", valid_input[:-1] + b',"extra":1}'),
            ("nonfinite infinity timeout",
             ('{"payload":' + payload_text
              + ',"credential":"x","timeout_seconds":Infinity}'
              ).encode("utf-8")),
            ("nonfinite nan timeout",
             ('{"payload":' + payload_text
              + ',"credential":"x","timeout_seconds":NaN}'
              ).encode("utf-8")),
            ("lone surrogate credential",
             ('{"payload":' + payload_text
              + ',"credential":"x\\ud800","timeout_seconds":5}'
              ).encode("utf-8")),
        ]
        for label, data in structural:
            with self.subTest(case="structural: " + label):
                out = self._direct_child(module, data)
                self._assert_frame(out, invalid_frame)
                self.assertEqual(
                    out["attempts"], [],
                    "malformed child input reached the network",
                )

        def mutated_input(mutator):
            candidate = copy.deepcopy(payload)
            mutator(candidate)
            return _make_input_bytes(candidate, credential, 5)

        semantic = [
            ("wrong model", mutated_input(
                lambda p: p.__setitem__("model", "other"))),
            ("stream true", mutated_input(
                lambda p: p.__setitem__("stream", True))),
            ("extra payload field", mutated_input(
                lambda p: p.__setitem__("extra", 1))),
            ("payload array", _make_input_bytes([], credential, 5)),
            ("empty credential", _make_input_bytes(payload, "", 5)),
            ("space credential",
             _make_input_bytes(payload, "has space", 5)),
            ("numeric credential",
             _make_input_bytes(payload, 123, 5)),
            ("zero timeout", _make_input_bytes(payload, credential, 0)),
            ("bool timeout",
             _make_input_bytes(payload, credential, True)),
            ("text timeout",
             _make_input_bytes(payload, credential, "5")),
        ]
        for label, data in semantic:
            with self.subTest(case="helper-invalid: " + label):
                out = self._direct_child(module, data)
                self._assert_frame(out, invalid_frame)
                self.assertEqual(
                    out["attempts"], [],
                    "helper-invalid child input reached the network",
                )

        with self.subTest(case="input bound: one byte under refuses"):
            out = self._direct_child(
                module, valid_input,
                bounds={"MAX_INPUT_BYTES": len(valid_input) - 1},
            )
            self._assert_frame(out, invalid_frame)
            self.assertEqual(out["attempts"], [])

        with self.subTest(case="input bound: exact size admitted"):
            out = self._direct_child(
                module, valid_input,
                bounds={"MAX_INPUT_BYTES": len(valid_input)},
            )
            # The refuse-all guard turns the one real send attempt
            # into the helper's fixed unknown outcome.
            self._assert_frame(out, {"failure": "unknown"})
            self.assertTrue(
                bool(out["attempts"]),
                "exact-bound input never attempted its one send",
            )

        with self.subTest(case="input bound: at most bound+1 read"):
            padded = b" " * (4096 - len(valid_input)) + valid_input
            out = self._direct_child(
                module, padded, bounds={"MAX_INPUT_BYTES": 64},
            )
            self._assert_frame(out, invalid_frame)
            self.assertTrue(
                out["reader"].served <= 65,
                "child read beyond bound+1 input bytes",
            )
            self.assertEqual(out["attempts"], [])

        guarded_cases = [
            ("guarded process: malformed", b"{not json}",
             invalid_frame),
            ("guarded process: duplicate key", structural[9][1],
             invalid_frame),
            ("guarded process: valid input, network refused",
             valid_input, {"failure": "unknown"}),
        ]
        for label, data, expected_frame in guarded_cases:
            with self.subTest(case=label):
                completed = self._run_guarded_process(data)
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stderr, b"")
                self.assertTrue(
                    completed.stdout.endswith(b"\n")
                    and not completed.stdout.endswith(b"\n\n"),
                    "guarded output does not end with exactly one "
                    "terminal newline",
                )
                self.assertEqual(
                    self._parse_frame(completed.stdout), expected_frame
                )

        healthy = _http._healthy_sse()
        expected_result = _http._collector_result(healthy, credential)
        credential_text = json.dumps(
            {"note": credential}, ensure_ascii=False
        )
        classification = [
            ("failed terminal", _http._sse_event(
                "response.failed",
                _http._envelope(
                    status="failed",
                    error={"message": "synthetic failure"}))),
            ("incomplete terminal", _http._sse_event(
                "response.incomplete",
                _http._envelope(
                    status="incomplete",
                    incomplete_details={"reason": "synthetic"}))),
            ("refusal", _http._sse_event(
                "response.completed",
                _http._envelope(content_parts=[
                    {"type": "refusal",
                     "refusal": "synthetic refusal"}]))),
            ("empty", _http._sse_event(
                "response.completed",
                _http._envelope(ordinary_text="   "))),
            ("malformed event", b"data: {not-json}\n\n"),
            ("credential bearing", _http._sse_event(
                "response.completed",
                _http._envelope(ordinary_text=credential_text))),
        ]
        for label, case_body in classification:
            with self.subTest(case="helper outcome: " + label):
                outcome, _unused = _http._collector_outcome(
                    case_body, credential
                )
                self.assertTrue(
                    outcome != "ok" and outcome in _CLOSED_FAILURES,
                    "collector fixture must itself refuse closedly",
                )
                out = self._direct_child(
                    module, valid_input, guard_mode="loopback",
                    specs=[_http._length_spec(case_body)],
                )
                # Helper outcome: incomplete terminal.
                if outcome == "incomplete":
                    self._assert_frame(
                        out,
                        {
                            "failure": outcome,
                            "diagnostic": {
                                "version": 1,
                                "classifier": "incomplete_details",
                                "response_status": "incomplete",
                                "message_status": None,
                                "response_id": None,
                                "reason": "other",
                                "usage": {
                                    "input_tokens": None,
                                    "output_tokens": None,
                                    "total_tokens": None,
                                    "reasoning_tokens": None,
                                },
                            },
                        },
                    )
                    _diagnosed_frame = self._parse_frame(out["stdout"])
                    self.assertTrue(
                        _diagnosed_frame is not None,
                        "child frame is not strict",
                    )
                    _diagnostic = _diagnosed_frame.get("diagnostic")
                    self.assertIs(type(_diagnostic), dict)
                    self.assertIs(type(_diagnostic.get("version")), int)
                    _usage = _diagnostic.get("usage")
                    self.assertIs(type(_usage), dict)
                    for _value in _usage.values():
                        self.assertTrue(
                            _value is None or type(_value) is int,
                            "diagnostic usage counter is not an "
                            "exact integer",
                        )
                else:
                    self._assert_frame(out, {"failure": outcome})
                self.assertEqual(
                    len(out["server"]["requests"]), 1,
                    "helper outcome case did not make its one send",
                )

        with self.subTest(case="helper outcome: http 500 is error"):
            out = self._direct_child(
                module, valid_input, guard_mode="loopback",
                specs=[_http._spec(
                    500, [("Content-Length", "0")], b""
                )],
            )
            self._assert_frame(out, {"failure": "error"})
            self.assertEqual(len(out["server"]["requests"]), 1)

        with self.subTest(case="helper outcome: dropped is unknown"):
            out = self._direct_child(
                module, valid_input, guard_mode="loopback",
                specs=[_http._spec(action="drop")],
            )
            self._assert_frame(out, {"failure": "unknown"})
            self.assertEqual(len(out["server"]["requests"]), 1)

        healthy_length = None
        with self.subTest(case="healthy direct control"):
            out = self._direct_child(
                module, valid_input, guard_mode="loopback",
                specs=[_http._length_spec(healthy)],
            )
            self._assert_frame(out, {"result": expected_result})
            self.assertEqual(len(out["server"]["requests"]), 1)
            self._assert_bearer(out["server"]["requests"][0], credential)
            healthy_length = len(out["stdout"])

        with self.subTest(case="output bound: exact size admitted"):
            self.assertTrue(healthy_length is not None)
            out = self._direct_child(
                module, valid_input, guard_mode="loopback",
                specs=[_http._length_spec(healthy)],
                bounds={"MAX_OUTPUT_BYTES": healthy_length},
            )
            self._assert_frame(out, {"result": expected_result})

        with self.subTest(case="output bound: oversized becomes error"):
            out = self._direct_child(
                module, valid_input, guard_mode="loopback",
                specs=[_http._length_spec(healthy)],
                bounds={"MAX_OUTPUT_BYTES": healthy_length - 1},
            )
            self._assert_frame(out, {"failure": "error"})
            self.assertEqual(
                len(out["server"]["requests"]), 1,
                "oversized success still performed its one send",
            )

        # The NEW enclosing key ``result`` is itself a credential
        # witness: these synthetic credentials pass the prior
        # collector but must be withheld by the enclosing frame.
        for colliding in ("result", '"result"'):
            with self.subTest(case="enclosing-key collision refuses"):
                prior = _http._collector_result(healthy, colliding)
                self.assertEqual(prior, expected_result)
                out = self._direct_child(
                    module,
                    _make_input_bytes(payload, colliding, 5),
                    guard_mode="loopback",
                    specs=[_http._length_spec(healthy)],
                )
                self._assert_frame(out, {"failure": "error"})
                self.assertEqual(len(out["server"]["requests"]), 1)
                self._assert_bearer(
                    out["server"]["requests"][0], colliding
                )

        for control in ("Result", '"Result"'):
            with self.subTest(case="case-different control succeeds"):
                prior = _http._collector_result(healthy, control)
                self.assertEqual(prior, expected_result)
                out = self._direct_child(
                    module,
                    _make_input_bytes(payload, control, 5),
                    guard_mode="loopback",
                    specs=[_http._length_spec(healthy)],
                )
                self._assert_frame(out, {"result": expected_result})
                self.assertEqual(len(out["server"]["requests"]), 1)

        with self.subTest(case="fixed failure collision is permitted"):
            out = self._direct_child(
                module,
                _make_input_bytes(payload, "error", 5),
                guard_mode="loopback",
                specs=[_http._spec(
                    500, [("Content-Length", "0")], b""
                )],
            )
            self._assert_frame(out, {"failure": "error"})
            self.assertTrue(
                b"error" in out["stdout"],
                "fixed failure literal was not emitted as-is",
            )

        # The genuine child produces its genuine local result and
        # attempts its one output against a broken public stdout
        # sink. The unhandled-output boundary has no forced
        # exit-code or no-exception rule, so neither ``exc`` nor
        # ``exit_code`` is asserted here; ``_direct_child`` still
        # restores stdout/stderr in its finally. What must hold
        # is exactly one attempted first output (no second frame
        # or write retry) and no raw traceback on stderr. The
        # broken sink need not deliver a successful full frame.
        for accepts_prefix, fault_label in (
            (False, "write fails before accepting any bytes"),
            (True, "write fails after accepting a prefix"),
        ):
            with self.subTest(case="stdout fault: " + fault_label):
                sink = _FaultingStdoutSink(
                    accept_prefix=accepts_prefix
                )
                out = self._direct_child(
                    module, valid_input, guard_mode="loopback",
                    specs=[_http._length_spec(healthy)],
                    stdout_sink=sink,
                )
                self.assertEqual(
                    len(out["server"]["requests"]), 1,
                    "write-fault case did not make its one send",
                )
                self.assertEqual(
                    sink.write_attempts, 1,
                    "broken stdout received a second frame or "
                    "write retry",
                )
                accepted = sink.accepted_bytes()
                self.assertEqual(out["stdout"], accepted)
                if accepts_prefix:
                    self.assertTrue(
                        bool(accepted),
                        "prefix fault accepted no bytes",
                    )
                    self.assertTrue(
                        self._parse_frame(accepted) is None,
                        "accepted prefix was a complete frame",
                    )
                else:
                    self.assertEqual(
                        accepted, b"",
                        "before-commit fault accepted bytes",
                    )
                self.assertTrue(
                    b"Traceback" not in out["stderr"],
                    "write fault leaked a raw traceback",
                )

    # -- 3: supervisor waits for EOF/exit and validates output -------

    def test_supervisor_waits_for_eof_exit_and_validates_output(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_transport")
        )
        module = _import_transport()
        payload = _http._genuine_payload()
        credential = _http._credential()
        expected = _expected_result()

        with self.subTest(case="synthetic control: correct frame"):
            plan = self._plan(module, "syn_valid_success")
            result, exc = self._invoke(
                module, plan, payload,
                **self._call_kwargs(credential),
            )
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_production_spawn(
                plan["record"]["spawns"][0], module, credential
            )
            self._assert_reaped(
                plan["record"], expected_codes=[0]
            )

        for outcome in _CLOSED_FAILURES:
            with self.subTest(case="failure frame: " + outcome):
                plan = self._plan(
                    module, "syn_failure", (outcome,)
                )
                result, exc = self._invoke(
                    module, plan, payload,
                    **self._call_kwargs(credential),
                )
                self.assertTrue(result is None)
                self._assert_protocol_error(exc, outcome)
                self.assertEqual(plan["record"]["attempts"], 1)
                self._assert_reaped(
                    plan["record"], expected_codes=[0]
                )

        with self.subTest(case="failure collision admitted as failure"):
            plan = self._plan(module, "syn_failure", ("error",))
            result, exc = self._invoke(
                module, plan, payload,
                **self._call_kwargs("error"),
            )
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "error")
            self._assert_reaped(plan["record"], expected_codes=[0])

        def assert_reaped_natural_or_stopped(
            record, natural_code, stop_observer
        ):
            # Original pre-rescue ownership is asserted first,
            # exactly as in the fixed-code path; only the settled
            # status may then be the natural code or the status
            # of the same process the supervisor genuinely
            # stopped before return. An arbitrary crash status
            # with no such observed stop still fails.
            codes = self._assert_reaped(record)
            self.assertEqual(
                len(codes), 1,
                "early-refusal case settled no single child",
            )
            if codes == [natural_code]:
                return
            spawns = record["spawns"]
            self.assertEqual(
                len(spawns), 1,
                "early-refusal case settled no single child",
            )
            self.assertTrue(
                stop_observer is not None
                and stop_observer.stopped(spawns[0]["proc"]),
                "non-natural early-refusal exit was not a "
                "genuine supervisor stop of the acquired child",
            )

        # The enclosing ``result`` key is itself supervisor-side
        # authority: credentials colliding with the decoded key
        # or its exact serialized form must be refused as fixed
        # ``unknown`` (not the child-side ``error``) even though
        # the genuine fixture frame is otherwise valid. On these
        # negative cases the supervisor may stop the same
        # still-running child after detecting the collision, so
        # natural ``0`` or its genuinely observed stop settles
        # the exit check. The case-different controls are
        # healthy because the scan is case-sensitive.
        for colliding in ("result", '"result"'):
            with self.subTest(case="envelope-key collision refuses"):
                plan = self._plan(module, "syn_valid_success")
                with _ObservedSupervisorStop(
                    plan["record"]
                ) as stop_observer:
                    result, exc = self._invoke(
                        module, plan, payload,
                        **self._call_kwargs(colliding),
                    )
                self.assertTrue(
                    result is None,
                    "envelope-colliding credential became success",
                )
                self._assert_protocol_error(exc, "unknown")
                self.assertEqual(plan["record"]["attempts"], 1)
                assert_reaped_natural_or_stopped(
                    plan["record"], 0, stop_observer
                )

        for control in ("Result", '"Result"'):
            with self.subTest(
                case="envelope case-different control succeeds"
            ):
                plan = self._plan(module, "syn_valid_success")
                result, exc = self._invoke(
                    module, plan, payload,
                    **self._call_kwargs(control),
                )
                self.assertTrue(exc is None, "expected success")
                self.assertEqual(result, expected)
                self.assertEqual(plan["record"]["attempts"], 1)
                self._assert_reaped(
                    plan["record"], expected_codes=[0]
                )

        # On these already-enumerated early-refusal modes a
        # conforming supervisor may stop the still-running child
        # once invalid framing or nonempty stderr is detected, so
        # either the known natural status or a genuinely observed
        # supervisor stop of that same acquired process settles
        # the case. Healthy/valid frames, the EOF-dependent
        # empty/missing-newline controls and the intentional
        # valid-frame-then-nonzero witnesses are not listed and
        # keep their exact exit pins below.
        early_refusal_modes = {
            "syn_malformed",
            "syn_toplevel_array",
            "syn_double_newline",
            "syn_prefix_byte",
            "syn_trailing_byte",
            "syn_duplicate_frame",
            "syn_partial_frame",
            "syn_wrong_model",
            "syn_blank_id",
            "syn_missing_id",
            "syn_raw_nondict",
            "syn_outcome_not_ok",
            "syn_extra_result_field",
            "syn_missing_raw",
            "syn_extra_enclosing",
            "syn_both_keys",
            "syn_failure_timeout_literal",
            "syn_failure_bad_literal",
            "syn_dup_key",
            "syn_nonfinite",
            "syn_surrogate",
            "syn_invalid_utf8",
            "syn_credential_success",
            "syn_credential_stderr",
            "syn_stderr_marker",
            "syn_barrier_late_stdout",
            "syn_barrier_late_stderr",
        }

        negatives = [
            ("empty stdout", "syn_empty", 0),
            ("malformed json", "syn_malformed", 0),
            ("top-level array", "syn_toplevel_array", 0),
            ("missing final newline", "syn_missing_newline", 0),
            ("double newline", "syn_double_newline", 0),
            ("prefix byte", "syn_prefix_byte", 0),
            ("trailing byte", "syn_trailing_byte", 0),
            ("duplicate frame", "syn_duplicate_frame", 0),
            ("partial frame", "syn_partial_frame", 0),
            ("wrong model", "syn_wrong_model", 0),
            ("blank response id", "syn_blank_id", 0),
            ("missing response id", "syn_missing_id", 0),
            ("raw not an object", "syn_raw_nondict", 0),
            ("outcome not ok", "syn_outcome_not_ok", 0),
            ("extra result field", "syn_extra_result_field", 0),
            ("missing raw", "syn_missing_raw", 0),
            ("extra enclosing key", "syn_extra_enclosing", 0),
            ("result and failure keys", "syn_both_keys", 0),
            ("child timeout literal", "syn_failure_timeout_literal", 0),
            ("unknown failure literal", "syn_failure_bad_literal", 0),
            ("duplicate json key", "syn_dup_key", 0),
            ("nonfinite frame value", "syn_nonfinite", 0),
            ("lone surrogate frame value", "syn_surrogate", 0),
            ("invalid utf-8 stdout", "syn_invalid_utf8", 0),
            ("credential in success", "syn_credential_success", 0),
            ("credential in stderr", "syn_credential_stderr", 0),
            ("diagnostic stderr", "syn_stderr_marker", 0),
            ("nonzero exit after frame", "syn_nonzero_after_frame", 3),
        ]
        for label, mode, code in negatives:
            with self.subTest(case="supervisor refuses: " + label):
                plan = self._plan(module, mode)
                with contextlib.ExitStack() as stack:
                    stop_observer = None
                    if mode in early_refusal_modes:
                        stop_observer = stack.enter_context(
                            _ObservedSupervisorStop(plan["record"])
                        )
                    result, exc = self._invoke(
                        module, plan, payload,
                        **self._call_kwargs(credential),
                    )
                self.assertTrue(
                    result is None,
                    "plausible child output became an early success",
                )
                self._assert_protocol_error(exc, "unknown")
                self._assert_no_marker(exc, (
                    credential, "SYNTHETIC-DIAGNOSTIC-MARKER",
                ))
                self.assertEqual(
                    plan["record"]["attempts"], 1,
                    "supervisor retried or spawned a second child",
                )
                if stop_observer is None:
                    self._assert_reaped(
                        plan["record"], expected_codes=[code]
                    )
                else:
                    assert_reaped_natural_or_stopped(
                        plan["record"], code, stop_observer
                    )

        barrier_cases = [
            ("late nonzero exit", "syn_barrier_late_exit", 3, False),
            ("late stdout byte", "syn_barrier_late_stdout", 0, False),
            ("late stderr byte", "syn_barrier_late_stderr", 0, False),
            ("delayed clean exit", "syn_barrier_clean_success", 0, True),
        ]
        for label, mode, code, succeeds in barrier_cases:
            with self.subTest(case="waits for eof/exit: " + label):
                plan = self._plan(module, mode)
                ready, release = self._barrier_files(plan)
                plan["mode_args"] = (str(ready), str(release))
                with contextlib.ExitStack() as stack:
                    stop_observer = None
                    if mode in early_refusal_modes:
                        stop_observer = stack.enter_context(
                            _ObservedSupervisorStop(plan["record"])
                        )
                    handle = self._start(
                        module, plan, payload,
                        **self._call_kwargs(credential, total=20.0),
                    )
                    try:
                        self.assertTrue(
                            _wait_until(ready.exists, timeout=10.0),
                            "synthetic child never reached its "
                            "barrier",
                        )
                        # A complete plausible frame is already on
                        # the pipe; a supervisor returning early
                        # would have finished during this bounded
                        # observation.
                        time.sleep(0.4)
                        self.assertTrue(
                            handle.thread.is_alive(),
                            "supervisor returned before EOF and "
                            "exit",
                        )
                    finally:
                        _release_plan(plan)
                        finished = handle.finish(timeout=20.0)
                        handle.close()
                self.assertTrue(
                    finished,
                    "run_payload did not return within outer ceiling",
                )
                result, exc = handle.outcome()
                if succeeds:
                    self.assertTrue(exc is None, "expected success")
                    self.assertEqual(result, expected)
                else:
                    self.assertTrue(result is None)
                    self._assert_protocol_error(exc, "unknown")
                    self._assert_no_marker(
                        exc, ("SYNTHETIC-DIAGNOSTIC-MARKER",)
                    )
                self.assertEqual(plan["record"]["attempts"], 1)
                if stop_observer is None:
                    self._assert_reaped(
                        plan["record"], expected_codes=[code]
                    )
                else:
                    assert_reaped_natural_or_stopped(
                        plan["record"], code, stop_observer
                    )

    # -- 4: IO bounds, deadline, active cancellation -----------------

    def test_io_bounds_deadline_and_active_cancellation_reap(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_transport")
        )
        module = _import_transport()
        payload = _http._genuine_payload()
        credential = _http._credential()
        expected = _expected_result()
        frame_length = len(_synthetic_success_frame())

        with self.subTest(case="stdout bound: exact frame admitted"):
            plan = self._plan(module, "syn_valid_success")
            with _patched(module, "MAX_OUTPUT_BYTES", frame_length):
                result, exc = self._invoke(
                    module, plan, payload,
                    **self._call_kwargs(credential),
                )
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self._assert_reaped(plan["record"], expected_codes=[0])

        with self.subTest(case="stdout bound: one byte over refused"):
            plan = self._plan(module, "syn_valid_success")
            with _patched(
                module, "MAX_OUTPUT_BYTES", frame_length - 1
            ):
                result, exc = self._invoke(
                    module, plan, payload,
                    **self._call_kwargs(credential),
                )
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "unknown")
            self.assertEqual(plan["record"]["attempts"], 1)
            # Reaped either way; whether overflow detection races
            # the child's own clean exit is not prescribed.
            self._assert_reaped(plan["record"])

        flood_cases = [
            ("stdout flood", "syn_stdout_flood",
             {"MAX_OUTPUT_BYTES": 1024}),
            ("valid frame then stdout flood", "syn_valid_then_flood",
             {"MAX_OUTPUT_BYTES": 1024}),
            ("stderr flood, stdout clean", "syn_stderr_flood",
             {"MAX_STDERR_BYTES": 256}),
        ]
        for label, mode, bounds in flood_cases:
            # Original limited controls: finite floods only; the
            # held-EOF over-cap obligations are the separate B2
            # cases appended below, not these floods.
            with self.subTest(
                case="original limited finite-flood bound: " + label
            ):
                plan = self._plan(module, mode)
                with contextlib.ExitStack() as stack:
                    for bound_name, bound_value in bounds.items():
                        stack.enter_context(
                            _patched(module, bound_name, bound_value)
                        )
                    handle = self._start(
                        module, plan, payload,
                        **self._call_kwargs(credential, total=15.0),
                    )
                    try:
                        finished = handle.finish(timeout=20.0)
                    finally:
                        handle.close()
                self.assertTrue(
                    finished,
                    "flooded child deadlocked or outlived its owner",
                )
                result, exc = handle.outcome()
                self.assertTrue(result is None)
                self._assert_protocol_error(exc, "unknown")
                self.assertEqual(plan["record"]["attempts"], 1)
                self._assert_reaped(plan["record"])

        with self.subTest(case="trickle within budget succeeds"):
            plan = self._plan(module, "syn_trickle_success")
            result, exc = self._invoke(
                module, plan, payload,
                **self._call_kwargs(credential, total=20.0),
            )
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self._assert_reaped(plan["record"], expected_codes=[0])

        deadline_cases = [
            ("stall after input", "syn_stall", 1.0),
            # Original limited control: small input only, so no
            # real drain backpressure is claimed from this row.
            ("stdin never read, small input (original limited control)",
             "syn_stdin_never_read", 1.0),
            ("trickle then stall", "syn_trickle_then_stall", 2.0),
        ]
        for label, mode, total in deadline_cases:
            with self.subTest(case="deadline closes: " + label):
                plan = self._plan(module, mode)
                ready, release = self._barrier_files(plan)
                plan["mode_args"] = (str(ready), str(release))
                started = time.monotonic()
                handle = self._start(
                    module, plan, payload,
                    **self._call_kwargs(credential, total=total),
                )
                finished = False
                try:
                    self.assertTrue(
                        _wait_until(ready.exists, timeout=10.0),
                        "synthetic child never became active",
                    )
                    finished = handle.finish(timeout=20.0)
                finally:
                    _release_plan(plan)
                    handle.close()
                elapsed = time.monotonic() - started
                self.assertTrue(
                    finished,
                    "deadline case did not return within outer ceiling",
                )
                self.assertTrue(
                    elapsed < 20.0, "deadline cleanup was unbounded"
                )
                result, exc = handle.outcome()
                self.assertTrue(
                    result is None,
                    "stalled child produced an early success",
                )
                self._assert_protocol_error(exc, "timeout")
                self.assertEqual(plan["record"]["attempts"], 1)
                spawn = plan["record"]["spawns"][0]
                self.assertTrue(
                    spawn["stdin_bytes"] > 0,
                    "exactly one input was not attempted",
                )
                self._assert_reaped(plan["record"])

        with self.subTest(case="cancel while synthetic child active"):
            plan = self._plan(module, "syn_stall")
            ready, release = self._barrier_files(plan)
            plan["mode_args"] = (str(ready), str(release))
            cancel_event = threading.Event()
            handle = self._start(
                module, plan, payload,
                **self._call_kwargs(
                    credential, total=20.0, cancel_event=cancel_event
                ),
            )
            finished = False
            try:
                self.assertTrue(
                    _wait_until(ready.exists, timeout=10.0),
                    "synthetic child never became active",
                )
                cancel_event.set()
                finished = handle.finish(timeout=20.0)
            finally:
                cancel_event.set()
                _release_plan(plan)
                handle.close()
            self.assertTrue(
                finished,
                "cancellation did not return within outer ceiling",
            )
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "cancelled")
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"])

        # B2 controlled replacements for the two former
        # handle-polling/short-deadline probability cases.  The
        # trigger is delivered only after the fixture server has
        # read the exact genuine POST and is held before its
        # genuine response write, with a live child and a pending
        # callable.  Socket inactivity (15s) stays beyond the
        # operation deadline/hold so it cannot win first.
        with self.subTest(
            case="REAL_ACTIVE_CANCEL_CONTROLLED: held real response"
        ):
            expected_body = _b2_expected_request_body(
                payload, credential, 15.0
            )
            if expected_body is None:
                self.fail(
                    _B2_UNRESOLVED
                    + ": expected POST body was not derivable "
                    "from the accepted preparation helper"
                )
            body = _http._healthy_sse()
            spec = _http._length_spec(body)
            with _http._running_server([spec]) as server:
                with _B2HeldTlsResponse(
                    server, spec, expected_body
                ) as held:
                    plan = self._plan(
                        module, "real_child", (str(server.port),)
                    )
                    cancel_event = threading.Event()
                    call_kwargs = self._call_kwargs(
                        credential, socket_timeout=15.0,
                        total=20.0, cancel_event=cancel_event,
                    )
                    started = time.monotonic()
                    handle = self._start(
                        module, plan, payload, **call_kwargs
                    )
                    finished = False
                    try:
                        self.assertTrue(
                            held.ready.wait(timeout=15.0),
                            _B2_UNRESOLVED
                            + ": held fixture response was not "
                            "reached after the exact request",
                        )
                        snapshot = server.snapshot()
                        self.assertEqual(snapshot["accepts"], 1)
                        self.assertEqual(len(snapshot["requests"]), 1)
                        self.assertEqual(
                            snapshot["requests"][0]["body"],
                            expected_body,
                        )
                        self.assertTrue(
                            snapshot["requests"][0][
                                "line"
                            ].startswith("POST ")
                        )
                        self.assertEqual(plan["record"]["attempts"], 1)
                        spawn = plan["record"]["spawns"][0]
                        self.assertTrue(
                            spawn["proc"] is not None
                            and spawn["proc"].returncode is None,
                            "real child was not live at readiness",
                        )
                        self.assertTrue(
                            handle.thread.is_alive(),
                            "callable was not pending at readiness",
                        )
                        self.assertTrue(
                            time.monotonic() - started < 20.0,
                            "readiness arrived only after expiry",
                        )
                        cancel_event.set()
                        # The response stays held while the
                        # product settles.
                        finished = handle.finish(timeout=25.0)
                    finally:
                        cancel_event.set()
                        held.release.set()
                        _release_plan(plan)
                        handle.close()
                    self.assertTrue(
                        _wait_until(
                            lambda: server.snapshot()["handled"] >= 1,
                            timeout=5.0,
                        ),
                        "held fixture connection never ended",
                    )
                # Leaving the server context performs the
                # shutdown/thread join after the hold release;
                # ``handled`` alone is not used as closure proof.
            self.assertTrue(
                finished,
                "controlled real-active case exceeded ceiling",
            )
            self.assertEqual(held.matched, 1)
            self.assertTrue(
                held.delegate_state in ("completed", "failed"),
                "genuine response write was never delegated",
            )
            self.assertFalse(held.ceiling_expired)
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "cancelled")
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"])
        with self.subTest(
            case="REAL_ACTIVE_DEADLINE_CONTROLLED: held real response"
        ):
            expected_body = _b2_expected_request_body(
                payload, credential, 15.0
            )
            if expected_body is None:
                self.fail(
                    _B2_UNRESOLVED
                    + ": expected POST body was not derivable "
                    "from the accepted preparation helper"
                )
            body = _http._healthy_sse()
            spec = _http._length_spec(body)
            with _http._running_server([spec]) as server:
                with _B2HeldTlsResponse(
                    server, spec, expected_body
                ) as held:
                    plan = self._plan(
                        module, "real_child", (str(server.port),)
                    )
                    call_kwargs = self._call_kwargs(
                        credential, socket_timeout=15.0, total=8.0,
                    )
                    started = time.monotonic()
                    handle = self._start(
                        module, plan, payload, **call_kwargs
                    )
                    finished = False
                    try:
                        self.assertTrue(
                            held.ready.wait(timeout=6.0),
                            _B2_UNRESOLVED
                            + ": held fixture response was not "
                            "reached after the exact request",
                        )
                        snapshot = server.snapshot()
                        self.assertEqual(snapshot["accepts"], 1)
                        self.assertEqual(len(snapshot["requests"]), 1)
                        self.assertEqual(
                            snapshot["requests"][0]["body"],
                            expected_body,
                        )
                        self.assertTrue(
                            snapshot["requests"][0][
                                "line"
                            ].startswith("POST ")
                        )
                        self.assertEqual(plan["record"]["attempts"], 1)
                        spawn = plan["record"]["spawns"][0]
                        self.assertTrue(
                            spawn["proc"] is not None
                            and spawn["proc"].returncode is None,
                            "real child was not live at readiness",
                        )
                        self.assertTrue(
                            handle.thread.is_alive(),
                            "callable was not pending at readiness",
                        )
                        self.assertTrue(
                            time.monotonic() - started < 8.0,
                            "readiness arrived only after expiry",
                        )
                        # The response stays held while the
                        # product settles: the original budget
                        # crosses inside this finish wait and is
                        # never reset.
                        finished = handle.finish(timeout=25.0)
                    finally:
                        held.release.set()
                        _release_plan(plan)
                        handle.close()
                    self.assertTrue(
                        _wait_until(
                            lambda: server.snapshot()["handled"] >= 1,
                            timeout=5.0,
                        ),
                        "held fixture connection never ended",
                    )
                # Leaving the server context performs the
                # shutdown/thread join after the hold release;
                # ``handled`` alone is not used as closure proof.
            self.assertTrue(
                finished,
                "controlled real-active case exceeded ceiling",
            )
            self.assertEqual(held.matched, 1)
            self.assertTrue(
                held.delegate_state in ("completed", "failed"),
                "genuine response write was never delegated",
            )
            self.assertFalse(held.ceiling_expired)
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "timeout")
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"])

        with self.subTest(
            case="REAL_ACTIVE healthy opposite: held response "
            "released within budget succeeds once"
        ):
            expected_body = _b2_expected_request_body(
                payload, credential, 15.0
            )
            if expected_body is None:
                self.fail(
                    _B2_UNRESOLVED
                    + ": expected POST body was not derivable from "
                    "the accepted preparation helper"
                )
            body = _http._healthy_sse()
            spec = _http._length_spec(body)
            with _http._running_server([spec]) as server:
                with _B2HeldTlsResponse(
                    server, spec, expected_body
                ) as held:
                    plan = self._plan(
                        module, "real_child", (str(server.port),)
                    )
                    handle = self._start(
                        module, plan, payload,
                        **self._call_kwargs(
                            credential, socket_timeout=15.0, total=20.0
                        ),
                    )
                    finished = False
                    try:
                        self.assertTrue(
                            held.ready.wait(timeout=15.0),
                            _B2_UNRESOLVED
                            + ": held fixture response not reached",
                        )
                        held.release.set()
                        finished = handle.finish(timeout=25.0)
                    finally:
                        held.release.set()
                        _release_plan(plan)
                        handle.close()
                    self.assertTrue(
                        _wait_until(
                            lambda: server.snapshot()["handled"] >= 1,
                            timeout=5.0,
                        )
                    )
            self.assertTrue(finished)
            self.assertEqual(held.delegate_state, "completed")
            snapshot = server.snapshot()
            self.assertEqual(snapshot["accepts"], 1)
            self.assertEqual(len(snapshot["requests"]), 1)
            self.assertEqual(
                snapshot["requests"][0]["body"], expected_body
            )
            result, exc = handle.outcome()
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"], expected_codes=[0])

        with self.subTest(
            case="REAL_ACTIVE healthy opposite: completion "
            "before cancel stays success"
        ):
            body = _http._healthy_sse()
            with _http._running_server(
                [_http._length_spec(body)]
            ) as server:
                plan = self._plan(
                    module, "real_child", (str(server.port),)
                )
                cancel_event = threading.Event()
                handle = self._start(
                    module, plan, payload,
                    **self._call_kwargs(
                        credential, total=20.0,
                        cancel_event=cancel_event,
                    ),
                )
                finished = False
                try:
                    finished = handle.finish(timeout=25.0)
                    # Set only after the joined worker proves the
                    # callable itself has actually completed.
                    cancel_event.set()
                finally:
                    cancel_event.set()
                    _release_plan(plan)
                    handle.close()
            self.assertTrue(finished)
            result, exc = handle.outcome()
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"], expected_codes=[0])

        # BOUNDARY_DECODE_*: the exact complete compact synthetic
        # output text is precomputed outside the observer.  The
        # genuine stdlib decoder runs first; only its successful
        # return is held.  Settlement is required at callable
        # return, not before decoding starts, and the test never
        # waits for loop-owned IO while the held decode blocks
        # that same loop: it only checks the worker is still held,
        # then releases in finally before finishing.
        decode_text = _synthetic_success_frame()[:-1].decode("utf-8")
        decode_cases = (
            ("BOUNDARY_DECODE_CANCEL", "cancel", 20.0, "cancelled"),
            ("BOUNDARY_DECODE_DEADLINE", "deadline", 8.0, "timeout"),
        )
        for case_id, trigger, total, outcome_name in decode_cases:
            with self.subTest(case=case_id + ": held genuine decode"):
                plan = self._plan(module, "syn_valid_success")
                cancel_event = threading.Event()
                if trigger == "cancel":
                    call_kwargs = self._call_kwargs(
                        credential, total=total,
                        cancel_event=cancel_event,
                    )
                    ready_timeout = 15.0
                else:
                    call_kwargs = self._call_kwargs(
                        credential, total=total
                    )
                    ready_timeout = 6.0
                with _B2DecodeHold(decode_text) as decode_hold:
                    started = time.monotonic()
                    handle = self._start(
                        module, plan, payload, **call_kwargs
                    )
                    finished = False
                    try:
                        self.assertTrue(
                            decode_hold.ready.wait(
                                timeout=ready_timeout
                            ),
                            _B2_UNRESOLVED
                            + ": exact synthetic-result decode was "
                            "not reached through stdlib decoding",
                        )
                        self.assertEqual(
                            decode_hold.thread_ident,
                            handle.thread.ident,
                        )
                        self.assertTrue(
                            time.monotonic() - started < total,
                            "decode readiness arrived only after "
                            "the original deadline",
                        )
                        if trigger == "cancel":
                            cancel_event.set()
                        else:
                            observed = time.monotonic()
                            bound = observed + total
                            remaining = bound - time.monotonic()
                            if remaining > 0:
                                time.sleep(remaining)
                            self.assertTrue(
                                time.monotonic() >= bound,
                                "conservative deadline bound "
                                "was not crossed",
                            )
                        self.assertTrue(
                            handle.thread.is_alive(),
                            "callable returned while its genuine "
                            "decode return was still held",
                        )
                    finally:
                        cancel_event.set()
                        decode_hold.release.set()
                        _release_plan(plan)
                        finished = handle.finish(timeout=20.0)
                        handle.close()
                self.assertTrue(finished)
                self.assertEqual(decode_hold.matched, 1)
                self.assertFalse(decode_hold.ceiling_expired)
                result, exc = handle.outcome()
                self.assertTrue(
                    result is None,
                    "held decode admitted success after closure",
                )
                self._assert_protocol_error(exc, outcome_name)
                self.assertEqual(plan["record"]["attempts"], 1)
                self._assert_reaped(plan["record"])

        with self.subTest(
            case="BOUNDARY_DECODE healthy opposite: compact held "
            "decode released within budget succeeds"
        ):
            plan = self._plan(module, "syn_valid_success")
            with _B2DecodeHold(decode_text) as decode_hold:
                handle = self._start(
                    module, plan, payload,
                    **self._call_kwargs(credential, total=20.0),
                )
                finished = False
                try:
                    self.assertTrue(
                        decode_hold.ready.wait(timeout=15.0),
                        _B2_UNRESOLVED
                        + ": exact synthetic-result decode not "
                        "reached through stdlib decoding",
                    )
                    decode_hold.release.set()
                    finished = handle.finish(timeout=20.0)
                finally:
                    decode_hold.release.set()
                    _release_plan(plan)
                    handle.close()
            self.assertTrue(finished)
            self.assertEqual(decode_hold.matched, 1)
            result, exc = handle.outcome()
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"], expected_codes=[0])
        # The ordinary unheld valid-result control for decoding is
        # the exact-frame success at the head of this method.

        # BOUNDARY_CLEANUP_* under the declared first-implementation
        # profile: only a terminal Process.wait return witnessed
        # after zero exit, closing stdin, both EOFs and terminal
        # other IO is held.  A missing witness is unresolved, not
        # a passing alternate implementation.
        cleanup_cases = (
            ("BOUNDARY_CLEANUP_CANCEL", "cancel", 20.0, "cancelled"),
            ("BOUNDARY_CLEANUP_DEADLINE", "deadline", 8.0, "timeout"),
        )
        for case_id, trigger, total, outcome_name in cleanup_cases:
            with self.subTest(case=case_id + ": held terminal wait"):
                plan = self._plan(module, "syn_valid_success")
                cancel_event = threading.Event()
                if trigger == "cancel":
                    call_kwargs = self._call_kwargs(
                        credential, total=total,
                        cancel_event=cancel_event,
                    )
                    ready_timeout = 15.0
                else:
                    call_kwargs = self._call_kwargs(
                        credential, total=total
                    )
                    ready_timeout = 6.0
                wait_hold = _B2TerminalWaitHold(plan["record"])
                with wait_hold:
                    started = time.monotonic()
                    handle = self._start(
                        module, plan, payload, **call_kwargs
                    )
                    finished = False
                    try:
                        self.assertTrue(
                            wait_hold.ready.wait(
                                timeout=ready_timeout
                            ),
                            _B2_UNRESOLVED
                            + ": terminal Process.wait witness was "
                            "not reached under the declared profile",
                        )
                        self.assertTrue(
                            all(wait_hold.witness.values())
                        )
                        self.assertEqual(
                            wait_hold.hold_state, "pending"
                        )
                        self.assertTrue(handle.thread.is_alive())
                        self.assertTrue(
                            time.monotonic() - started < total,
                            "terminal witness arrived only after "
                            "the original deadline",
                        )
                        if trigger == "cancel":
                            cancel_event.set()
                        else:
                            observed = time.monotonic()
                            bound = observed + total
                            remaining = bound - time.monotonic()
                            if remaining > 0:
                                time.sleep(remaining)
                            self.assertTrue(
                                time.monotonic() >= bound,
                                "conservative deadline bound "
                                "was not crossed",
                            )
                    finally:
                        cancel_event.set()
                        wait_hold.release()
                        _release_plan(plan)
                        finished = handle.finish(timeout=20.0)
                        handle.close()
                self.assertTrue(finished)
                self.assertTrue(wait_hold.selected)
                self.assertTrue(
                    wait_hold.hold_state
                    in ("released", "cancelled"),
                    "terminal wait hold was not joined before "
                    "the original return capture",
                )
                result, exc = handle.outcome()
                self.assertTrue(
                    result is None,
                    "held terminal cleanup admitted success "
                    "after closure",
                )
                self._assert_protocol_error(exc, outcome_name)
                self.assertEqual(plan["record"]["attempts"], 1)
                self._assert_reaped(
                    plan["record"], expected_codes=[0]
                )

        with self.subTest(
            case="BOUNDARY_CLEANUP healthy opposite: terminal "
            "wait released within budget keeps exact success"
        ):
            plan = self._plan(module, "syn_valid_success")
            wait_hold = _B2TerminalWaitHold(plan["record"])
            with wait_hold:
                handle = self._start(
                    module, plan, payload,
                    **self._call_kwargs(credential, total=20.0),
                )
                finished = False
                try:
                    self.assertTrue(
                        wait_hold.ready.wait(timeout=15.0),
                        _B2_UNRESOLVED
                        + ": terminal Process.wait witness not "
                        "reached under the declared profile",
                    )
                    wait_hold.release()
                    finished = handle.finish(timeout=20.0)
                finally:
                    wait_hold.release()
                    _release_plan(plan)
                    handle.close()
            self.assertTrue(finished)
            self.assertEqual(wait_hold.hold_state, "released")
            result, exc = handle.outcome()
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"], expected_codes=[0])

        with self.subTest(
            case="REPEATED_CANCEL_IO_CLEANUP: second root "
            "cancellation during held owned-IO join"
        ):
            plan = self._plan(module, "syn_stall")
            ready, release = self._barrier_files(plan)
            plan["mode_args"] = (str(ready), str(release))
            root_observer = _B2RootTaskObserver(module)
            reader_hold = _B2ReaderTerminalHold(
                plan["record"], root_observer
            )
            root_observer.arm_on_first_delivery(reader_hold.armed)
            selected_task = None
            with root_observer:
                with reader_hold:
                    handle = self._start(
                        module, plan, payload,
                        **self._call_kwargs(credential, total=20.0),
                    )
                    finished = False
                    try:
                        self.assertTrue(
                            root_observer.root_ready.wait(
                                timeout=10.0
                            ),
                            _B2_UNRESOLVED
                            + ": operation root was not captured "
                            "through the public event-loop bridge",
                        )
                        self.assertEqual(
                            root_observer.capture_thread_ident,
                            handle.thread.ident,
                        )
                        self.assertFalse(
                            reader_hold.armed.is_set(),
                            "reader hold was armed before the "
                            "first actual root delivery",
                        )
                        self.assertTrue(
                            _wait_until(ready.exists, timeout=10.0),
                            "synthetic child never became active",
                        )
                        self.assertTrue(
                            _wait_until(
                                lambda: (
                                    _b2_io_progress_scalars(
                                        plan["record"], "stdout"
                                    )["pending"]
                                    + _b2_io_progress_scalars(
                                        plan["record"], "stderr"
                                    )["pending"]
                                )
                                > 0,
                                timeout=10.0,
                            ),
                            _B2_UNRESOLVED
                            + ": no pending observed reader "
                            "operation existed before cancellation",
                        )
                        self.assertTrue(root_observer.cancel_root())
                        self.assertTrue(
                            _wait_until(
                                lambda: (
                                    root_observer.request_executed(1)
                                ),
                                timeout=10.0,
                            ),
                            _B2_UNRESOLVED
                            + ": first cancellation callback did "
                            "not execute on the owning loop",
                        )
                        self.assertTrue(
                            root_observer.request_accepted(1),
                            _B2_UNRESOLVED
                            + ": first root cancellation was not "
                            "accepted on the owning loop",
                        )
                        self.assertTrue(
                            _wait_until(
                                lambda: (
                                    root_observer.phase_delivered(1)
                                ),
                                timeout=10.0,
                            ),
                            _B2_UNRESOLVED
                            + ": first actual root-frame "
                            "cancellation delivery was not observed",
                        )
                        self.assertTrue(
                            reader_hold.armed.is_set(),
                            "reader hold was not armed at the "
                            "first actual root delivery",
                        )
                        self.assertTrue(
                            reader_hold.ready.wait(timeout=10.0),
                            _B2_UNRESOLVED
                            + ": no eligible separate owned reader "
                            "reached its held cancellation "
                            "terminal after first delivery",
                        )
                        self.assertTrue(
                            _wait_until(
                                lambda: (
                                    reader_hold.selected_task()
                                    is not None
                                    and root_observer.suspended_on_join(
                                        (reader_hold.selected_task(),)
                                    )
                                ),
                                timeout=10.0,
                            ),
                            _B2_UNRESOLVED
                            + ": root was not witnessed suspended "
                            "on a join depending on the held "
                            "owned reader task after first delivery",
                        )
                        selected_task = reader_hold.selected_task()
                        self.assertTrue(selected_task is not None)
                        self.assertTrue(
                            _wait_until(
                                reader_hold.hold_pending,
                                timeout=10.0,
                            ),
                            _B2_UNRESOLVED
                            + ": selected reader terminal was not "
                            "held pending at the owned join",
                        )
                        selected_stream = (
                            reader_hold.selected_kind.split("_", 1)[0]
                        )
                        self.assertTrue(
                            _b2_io_progress_scalars(
                                plan["record"], selected_stream
                            )["pending"]
                            >= 1,
                            "held reader operation did not remain "
                            "pending in its A3 record",
                        )
                        self.assertTrue(handle.thread.is_alive())
                        join_route = root_observer.join_route(
                            (selected_task,)
                        )
                        self.assertTrue(
                            join_route
                            in ("direct", "shield", "gather"),
                            _B2_UNRESOLVED
                            + ": observed owned-join route was "
                            "unavailable for the held reader",
                        )
                        self.assertTrue(
                            root_observer.cancel_root(
                                targets=(selected_task,),
                                recheck=reader_hold.hold_pending,
                            )
                        )
                        self.assertTrue(
                            _wait_until(
                                lambda: (
                                    root_observer.request_executed(2)
                                ),
                                timeout=10.0,
                            ),
                            _B2_UNRESOLVED
                            + ": second cancellation callback "
                            "did not execute on the owning loop",
                        )
                        self.assertTrue(
                            root_observer.request_accepted(2),
                            _B2_UNRESOLVED
                            + ": second root cancellation was "
                            "refused or unavailable at the "
                            "owning-loop recheck",
                        )
                        join_route = (
                            root_observer.rechecked_join_route
                        )
                        if join_route == "shield":
                            self.assertTrue(
                                _wait_until(
                                    lambda: (
                                        root_observer.phase_delivered(
                                            2
                                        )
                                    ),
                                    timeout=10.0,
                                ),
                                _B2_UNRESOLVED
                                + ": shield-route second actual "
                                "root delivery was not observed "
                                "while the reader remained held",
                            )
                            self.assertTrue(
                                reader_hold.hold_pending()
                            )
                            self.assertFalse(
                                _wait_until(
                                    lambda: (
                                        not handle.thread.is_alive()
                                    ),
                                    timeout=0.75,
                                ),
                                "second cancellation returned "
                                "before the held owned IO join "
                                "settled",
                            )
                        else:
                            reader_hold.release()
                            self.assertTrue(
                                _wait_until(
                                    lambda: (
                                        root_observer.phase_delivered(
                                            2
                                        )
                                    ),
                                    timeout=10.0,
                                ),
                                _B2_UNRESOLVED
                                + ": direct/gather-route later "
                                "actual root delivery was not "
                                "observed after the accepted "
                                "second request and schedule "
                                "release",
                            )
                    finally:
                        reader_hold.release()
                        _release_plan(plan)
                        finished = handle.finish(timeout=20.0)
                        handle.close()
            self.assertTrue(finished)
            self.assertEqual(root_observer.requests_executed, 2)
            self.assertEqual(root_observer.requests_accepted, 2)
            self.assertTrue(root_observer.phase_delivered(1))
            self.assertTrue(root_observer.phase_delivered(2))
            self.assertTrue(selected_task is not None)
            self.assertTrue(selected_task.done())
            self.assertTrue(reader_hold.selected)
            self.assertEqual(reader_hold.hold_state, "released")
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "cancelled")
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"])

        with self.subTest(
            case="REPEATED_CANCEL_IO healthy opposite: no root "
            "cancellation and unarmed hold keep ordinary success"
        ):
            plan = self._plan(module, "syn_valid_success")
            root_observer = _B2RootTaskObserver(module)
            reader_hold = _B2ReaderTerminalHold(
                plan["record"], root_observer
            )
            root_observer.arm_on_first_delivery(reader_hold.armed)
            with root_observer:
                with reader_hold:
                    handle = self._start(
                        module, plan, payload,
                        **self._call_kwargs(credential, total=20.0),
                    )
                    finished = False
                    try:
                        self.assertTrue(
                            root_observer.root_ready.wait(
                                timeout=10.0
                            )
                        )
                        self.assertEqual(
                            root_observer.capture_thread_ident,
                            handle.thread.ident,
                        )
                        finished = handle.finish(timeout=20.0)
                    finally:
                        reader_hold.release()
                        _release_plan(plan)
                        handle.close()
            self.assertTrue(finished)
            self.assertEqual(root_observer.cancel_requests, 0)
            self.assertEqual(root_observer.requests_executed, 0)
            self.assertEqual(root_observer.requests_accepted, 0)
            self.assertFalse(root_observer.phase_delivered(1))
            self.assertFalse(root_observer.phase_delivered(2))
            self.assertFalse(reader_hold.armed.is_set())
            self.assertFalse(reader_hold.selected)
            result, exc = handle.outcome()
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"], expected_codes=[0])

        # True flow control uses one enlarged copy of the genuine
        # payload.  384 KiB of encoded input leaves wide margin
        # below the 128 MiB product input cap and below the
        # accepted HTTP preparation cap, while exceeding pipe and
        # transport write buffers; no limit is enlarged and no
        # drain call is gated or transport paused by the test.
        large_payload = _b2_large_valid_payload(
            payload, credential, 5.0, 384 * 1024
        )

        with self.subTest(
            case="STDIN_BACKPRESSURE_DEADLINE: observed pending "
            "drain crosses the original deadline"
        ):
            if large_payload is None:
                self.fail(
                    _B2_UNRESOLVED
                    + ": genuine payload had no enlargeable text"
                )
            plan = self._plan(module, "syn_stdin_never_read")
            ready, release = self._barrier_files(plan)
            plan["mode_args"] = (str(ready), str(release))
            started = time.monotonic()
            handle = self._start(
                module, plan, large_payload,
                **self._call_kwargs(credential, total=8.0),
            )
            finished = False
            try:
                self.assertTrue(
                    _wait_until(ready.exists, timeout=5.0),
                    "no-read child never became active",
                )
                pressure = _b2_wait_stdin_pressure(
                    plan["record"], timeout=5.0
                )
                self.assertTrue(
                    all(pressure.values()),
                    _B2_UNRESOLVED
                    + ": live child, pending observed drain, "
                    "positive transport buffer, paused protocol "
                    "and pending drain waiter were not all "
                    "witnessed on the owning loop",
                )
                self.assertTrue(
                    time.monotonic() - started < 8.0,
                    "backpressure was witnessed only after the "
                    "original deadline",
                )
                finished = handle.finish(timeout=20.0)
            finally:
                _release_plan(plan)
                handle.close()
            self.assertTrue(finished)
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "timeout")
            self.assertEqual(plan["record"]["attempts"], 1)
            spawn = plan["record"]["spawns"][0]
            self.assertTrue(spawn["stdin_bytes"] > 0)
            self._assert_reaped(plan["record"])

        with self.subTest(
            case="STDIN_BACKPRESSURE healthy opposite: same "
            "large payload consumed by valid success"
        ):
            if large_payload is None:
                self.fail(
                    _B2_UNRESOLVED
                    + ": genuine payload had no enlargeable text"
                )
            plan = self._plan(module, "syn_valid_success")
            with _patched(module, "MAX_OUTPUT_BYTES", frame_length):
                result, exc = self._invoke(
                    module, plan, large_payload,
                    **self._call_kwargs(credential, total=20.0),
                )
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"], expected_codes=[0])

        with self.subTest(
            case="STDOUT_OVERCAP_WITH_STDIN_PENDING: over-cap "
            "unfinished valid JSON settles before EOF release"
        ):
            if large_payload is None:
                self.fail(
                    _B2_UNRESOLVED
                    + ": genuine payload had no enlargeable text"
                )
            plan = self._plan(module, "syn_stdout_pending_hold")
            ready, release = self._barrier_files(plan)
            output_go = ready.with_name("output-go")
            plan["mode_args"] = (
                str(ready), str(output_go), str(release)
            )
            output_snapshot = None
            with _patched(module, "MAX_OUTPUT_BYTES", 1024):
                with _B2OutputByteObserver(
                    plan["record"], "stdout"
                ) as output_observer:
                    started = time.monotonic()
                    handle = self._start(
                        module, plan, large_payload,
                        **self._call_kwargs(
                            credential, total=20.0
                        ),
                    )
                    finished = False
                    settled_before_release = False
                    try:
                        self.assertTrue(
                            _wait_until(
                                ready.exists, timeout=10.0
                            ),
                            "held-output child never "
                            "signalled ready",
                        )
                        pressure = _b2_wait_stdin_pressure(
                            plan["record"], timeout=5.0
                        )
                        self.assertTrue(
                            all(pressure.values()),
                            _B2_UNRESOLVED
                            + ": same-child stdin "
                            "backpressure was not witnessed "
                            "before opening output-go",
                        )
                        output_go.write_text(
                            "go", encoding="ascii"
                        )
                        finished = handle.finish(timeout=10.0)
                        settled_before_release = bool(
                            finished and not release.exists()
                        )
                        self.assertTrue(
                            time.monotonic() - started < 20.0,
                            "over-cap settlement needed "
                            "the deadline",
                        )
                        if finished:
                            output_snapshot = (
                                output_observer.snapshot()
                            )
                    finally:
                        try:
                            output_go.write_text(
                                "go", encoding="ascii"
                            )
                        except Exception:
                            pass
                        _release_plan(plan)
                        handle.close()
            self.assertTrue(
                finished,
                "over-cap output with pending stdin did not "
                "settle before EOF release/deadline",
            )
            self.assertTrue(settled_before_release)
            self.assertTrue(
                output_snapshot is not None,
                "no stdout byte snapshot was captured after "
                "successful finish and before release/close; "
                "cleanup cannot supply this witness",
            )
            self.assertEqual(output_snapshot["stream"], "stdout")
            self.assertTrue(
                output_snapshot["returned_bytes"] > 1024,
                "actual returned stdout bytes did not exceed "
                "the output cap; empty EOF, failed or "
                "cancelled reads and planned bytes are not "
                "progress evidence",
            )
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "unknown")
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"])

        with self.subTest(
            case="STDOUT_OVERCAP healthy opposite: within-cap "
            "no-pressure ordinary success"
        ):
            plan = self._plan(module, "syn_valid_success")
            with _patched(module, "MAX_OUTPUT_BYTES", frame_length):
                result, exc = self._invoke(
                    module, plan, payload,
                    **self._call_kwargs(credential, total=20.0),
                )
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"], expected_codes=[0])

        with self.subTest(
            case="STDERR_OVERCAP_HOLD_EOF: over-cap stderr "
            "settles before EOF release"
        ):
            plan = self._plan(module, "syn_stderr_pending_hold")
            ready, release = self._barrier_files(plan)
            plan["mode_args"] = (str(ready), str(release))
            output_snapshot = None
            with _patched(module, "MAX_STDERR_BYTES", 256):
                with _B2OutputByteObserver(
                    plan["record"], "stderr"
                ) as output_observer:
                    started = time.monotonic()
                    handle = self._start(
                        module, plan, payload,
                        **self._call_kwargs(
                            credential, total=20.0
                        ),
                    )
                    finished = False
                    settled_before_release = False
                    try:
                        # The mode writes ready on entry, before
                        # any emission, so an arbitrary bootstrap
                        # crash cannot be mistaken for this known
                        # mode.
                        self.assertTrue(
                            _wait_until(
                                ready.exists, timeout=10.0
                            ),
                            "held-stderr mode never "
                            "signalled entry",
                        )
                        finished = handle.finish(timeout=10.0)
                        settled_before_release = bool(
                            finished and not release.exists()
                        )
                        self.assertTrue(
                            time.monotonic() - started < 20.0,
                            "stderr over-cap settlement "
                            "needed the operation deadline",
                        )
                        if finished:
                            output_snapshot = (
                                output_observer.snapshot()
                            )
                    finally:
                        _release_plan(plan)
                        handle.close()
            self.assertTrue(
                finished,
                "stderr over-cap did not settle before EOF "
                "release/deadline",
            )
            self.assertTrue(settled_before_release)
            self.assertTrue(
                output_snapshot is not None,
                "no stderr byte snapshot was captured after "
                "successful finish and before release/close; "
                "cleanup cannot supply this witness",
            )
            self.assertEqual(output_snapshot["stream"], "stderr")
            self.assertTrue(
                output_snapshot["returned_bytes"] >= 1,
                "no actual stderr byte was received from "
                "this acquired stream; mode readiness alone "
                "and stdout progress are not stderr evidence",
            )
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "unknown")
            self.assertEqual(plan["record"]["attempts"], 1)
            spawn = plan["record"]["spawns"][0]
            self.assertTrue(spawn["stdin_bytes"] > 0)
            # Either first-byte refusal or over-cap refusal may
            # stop this same child; no planned-write completion
            # or natural status is demanded after that stop.
            self._assert_reaped(plan["record"])

        with self.subTest(
            case="STDERR_OVERCAP healthy opposite: clean stderr "
            "within the same cap succeeds"
        ):
            plan = self._plan(module, "syn_valid_success")
            with _patched(module, "MAX_STDERR_BYTES", 256):
                result, exc = self._invoke(
                    module, plan, payload,
                    **self._call_kwargs(credential, total=20.0),
                )
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"], expected_codes=[0])


    # -- 5: pending spawn ownership ------------------------------------

    def test_pending_spawn_ownership_survives_deadline_and_cancel(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_transport")
        )
        module = _import_transport()
        payload = _http._genuine_payload()
        credential = _http._credential()
        expected = _expected_result()

        # SEAM_REACHED_BEFORE_TRIGGER: readiness is the same-loop
        # acknowledgement that the original harness ``_create`` is
        # actually suspended on the exact selected gate, never an
        # attempt count plus a fixed sleep.  A bounded wait for
        # expiry is used only after that acknowledgement.
        for seam in ("before launch", "after launch before delivery"):
            for trigger in ("deadline", "cancel"):
                label = seam + " + " + trigger
                with self.subTest(
                    case="SEAM_REACHED_BEFORE_TRIGGER: " + label
                ):
                    gate = concurrent.futures.Future()
                    plan = self._plan(module, "syn_stall")
                    ready, release = self._barrier_files(plan)
                    plan["mode_args"] = (str(ready), str(release))
                    if seam == "before launch":
                        plan["barrier_pre"] = gate
                    else:
                        plan["barrier_post"] = gate
                    cancel_event = threading.Event()
                    if trigger == "deadline":
                        call_kwargs = self._call_kwargs(
                            credential, total=5.0
                        )
                        expected_outcome = "timeout"
                        ack_timeout = 4.0
                        total_budget = 5.0
                    else:
                        call_kwargs = self._call_kwargs(
                            credential, total=20.0,
                            cancel_event=cancel_event,
                        )
                        expected_outcome = "cancelled"
                        ack_timeout = 10.0
                        total_budget = 20.0
                    ack = _B2CreationGateAck(gate)
                    with ack:
                        started = time.monotonic()
                        handle = self._start(
                            module, plan, payload, **call_kwargs
                        )
                        finished = False
                        try:
                            self.assertTrue(
                                ack.ready.wait(timeout=ack_timeout),
                                _B2_UNRESOLVED
                                + ": creation gate suspension was "
                                "not acknowledged on its own loop",
                            )
                            self.assertTrue(ack.acknowledged)
                            self.assertTrue(
                                ack.wrapped_pending_at_ack
                            )
                            self.assertFalse(gate.done())
                            self.assertEqual(
                                plan["record"]["attempts"], 1
                            )
                            spawn = plan["record"]["spawns"][0]
                            if seam == "before launch":
                                self.assertTrue(
                                    spawn["proc"] is None,
                                    "pre-launch gate already had "
                                    "an acquired handle",
                                )
                            else:
                                self.assertTrue(
                                    spawn["proc"] is not None,
                                    "post-launch gate had no "
                                    "acquired handle",
                                )
                                self.assertTrue(
                                    isinstance(
                                        spawn["proc"].stdin,
                                        _RecordingStdin,
                                    )
                                    and isinstance(
                                        spawn["proc"].stdout,
                                        _ObservedReader,
                                    )
                                    and isinstance(
                                        spawn["proc"].stderr,
                                        _ObservedReader,
                                    ),
                                    "post-launch stream wrappers "
                                    "did not exist at the gate",
                                )
                            self.assertTrue(
                                time.monotonic() - started
                                < total_budget,
                                "trigger was not delivered while "
                                "the original budget was live",
                            )
                            if trigger == "cancel":
                                cancel_event.set()
                                self.assertFalse(
                                    _wait_until(
                                        lambda: not handle.thread.is_alive(),
                                        timeout=0.75,
                                    ),
                                    "supervisor abandoned the "
                                    "acknowledged pending creation",
                                )
                            else:
                                observed = time.monotonic()
                                bound = observed + total_budget
                                remaining = bound - time.monotonic()
                                if remaining > 0:
                                    time.sleep(remaining)
                                self.assertTrue(
                                    time.monotonic() >= bound,
                                    "conservative deadline bound "
                                    "was not crossed",
                                )
                                self.assertTrue(
                                    handle.thread.is_alive(),
                                    "supervisor abandoned the "
                                    "acknowledged pending creation",
                                )
                        finally:
                            cancel_event.set()
                            _release_plan(plan)
                            finished = handle.finish(timeout=20.0)
                            handle.close()
                    self.assertTrue(
                        finished,
                        "pending-spawn cleanup exceeded outer ceiling",
                    )
                    result, exc = handle.outcome()
                    self.assertTrue(
                        result is None,
                        "late child overrode the closure outcome",
                    )
                    self._assert_protocol_error(exc, expected_outcome)
                    self.assertEqual(
                        plan["record"]["attempts"], 1,
                        "pending-spawn closure created a second child",
                    )
                    spawn = plan["record"]["spawns"][0]
                    self.assertTrue(
                        spawn["proc"] is not None,
                        "eventual child handle was not retained",
                    )
                    self.assertEqual(
                        spawn["stdin_bytes"], 0,
                        "input was sent after closure was selected",
                    )
                    self._assert_reaped(plan["record"])

        # REPEATED_CANCEL_PENDING_CREATION: after the same acknowledged
        # gate suspension, a second actual root cancellation is
        # delivered while the original creation work is joined.
        for seam in ("before launch", "after launch before delivery"):
            with self.subTest(
                case="REPEATED_CANCEL_PENDING_CREATION: second root "
                "cancellation during pending owned creation join ("
                + seam
                + ")"
            ):
                gate = concurrent.futures.Future()
                plan = self._plan(module, "syn_stall")
                ready, release = self._barrier_files(plan)
                plan["mode_args"] = (str(ready), str(release))
                if seam == "before launch":
                    plan["barrier_pre"] = gate
                else:
                    plan["barrier_post"] = gate
                ack = _B2CreationGateAck(gate)
                root_observer = _B2RootTaskObserver(module)
                creation_targets = ()
                inner_task = None
                with ack:
                    with root_observer:
                        started = time.monotonic()
                        handle = self._start(
                            module, plan, payload,
                            **self._call_kwargs(
                                credential, total=20.0
                            ),
                        )
                        finished = False
                        try:
                            self.assertTrue(
                                root_observer.root_ready.wait(
                                    timeout=10.0
                                ),
                                _B2_UNRESOLVED
                                + ": operation root was not "
                                "captured through the public "
                                "event-loop bridge",
                            )
                            self.assertEqual(
                                root_observer.capture_thread_ident,
                                handle.thread.ident,
                            )
                            self.assertTrue(
                                ack.ready.wait(timeout=10.0),
                                _B2_UNRESOLVED
                                + ": creation gate suspension was "
                                "not acknowledged on its own loop",
                            )
                            self.assertTrue(ack.acknowledged)
                            self.assertTrue(
                                ack.wrapped_pending_at_ack
                            )
                            self.assertFalse(gate.done())
                            self.assertEqual(
                                plan["record"]["attempts"], 1
                            )
                            spawn = plan["record"]["spawns"][0]
                            if seam == "before launch":
                                self.assertTrue(
                                    spawn["proc"] is None,
                                    "pre-launch gate already had "
                                    "an acquired handle",
                                )
                            else:
                                self.assertTrue(
                                    spawn["proc"] is not None,
                                    "post-launch gate had no "
                                    "acquired handle",
                                )
                                self.assertTrue(
                                    isinstance(
                                        spawn["proc"].stdin,
                                        _RecordingStdin,
                                    )
                                    and isinstance(
                                        spawn["proc"].stdout,
                                        _ObservedReader,
                                    )
                                    and isinstance(
                                        spawn["proc"].stderr,
                                        _ObservedReader,
                                    ),
                                    "post-launch stream wrappers "
                                    "did not exist at the gate",
                                )
                            self.assertTrue(
                                time.monotonic() - started < 20.0,
                                "first cancellation was not "
                                "delivered while the original "
                                "budget was live",
                            )
                            inner_task = spawn.get("inner_task")
                            if inner_task is None:
                                inner_task = spawn.get("inner")
                            creation_task = spawn.get(
                                "creation_task"
                            )
                            target_list = []
                            if inner_task is not None:
                                target_list.append(inner_task)
                            if (
                                creation_task is not None
                                and not root_observer.root_is(
                                    creation_task
                                )
                            ):
                                target_list.append(creation_task)
                            creation_targets = tuple(target_list)
                            self.assertTrue(
                                creation_targets,
                                _B2_UNRESOLVED
                                + ": genuine owned creation task "
                                "identity was not recorded",
                            )
                            self.assertTrue(
                                root_observer.cancel_root()
                            )
                            self.assertTrue(
                                _wait_until(
                                    lambda: (
                                        root_observer
                                        .request_executed(1)
                                    ),
                                    timeout=10.0,
                                ),
                                _B2_UNRESOLVED
                                + ": first cancellation callback "
                                "did not execute on the owning "
                                "loop",
                            )
                            self.assertTrue(
                                root_observer.request_accepted(1),
                                _B2_UNRESOLVED
                                + ": first root cancellation was "
                                "not accepted on the owning loop",
                            )
                            self.assertTrue(
                                _wait_until(
                                    lambda: (
                                        root_observer
                                        .phase_delivered(1)
                                    ),
                                    timeout=10.0,
                                ),
                                _B2_UNRESOLVED
                                + ": first actual root-frame "
                                "cancellation delivery was not "
                                "observed at the pending creation",
                            )
                            self.assertTrue(
                                _wait_until(
                                    lambda: (
                                        root_observer
                                        .suspended_on_join(
                                            creation_targets
                                        )
                                    ),
                                    timeout=10.0,
                                ),
                                _B2_UNRESOLVED
                                + ": root was not witnessed "
                                "suspended on the genuine pending "
                                "owned creation join after first "
                                "delivery",
                            )
                            self.assertFalse(gate.done())
                            self.assertTrue(
                                handle.thread.is_alive()
                            )
                            creation_route = (
                                root_observer.join_route(
                                    creation_targets
                                )
                            )
                            self.assertTrue(
                                creation_route
                                in ("direct", "shield", "gather"),
                                _B2_UNRESOLVED
                                + ": observed owned creation join "
                                "route was unavailable",
                            )
                            self.assertTrue(
                                root_observer.cancel_root(
                                    targets=creation_targets,
                                    recheck=(
                                        lambda: not gate.done()
                                    ),
                                )
                            )
                            self.assertTrue(
                                _wait_until(
                                    lambda: (
                                        root_observer
                                        .request_executed(2)
                                    ),
                                    timeout=10.0,
                                ),
                                _B2_UNRESOLVED
                                + ": second cancellation callback "
                                "did not execute on the owning "
                                "loop",
                            )
                            self.assertTrue(
                                root_observer.request_accepted(2),
                                _B2_UNRESOLVED
                                + ": second root cancellation was "
                                "refused or unavailable at the "
                                "owning-loop recheck",
                            )
                            creation_route = (
                                root_observer.rechecked_join_route
                            )
                            if creation_route == "shield":
                                self.assertTrue(
                                    _wait_until(
                                        lambda: (
                                            root_observer
                                            .phase_delivered(2)
                                        ),
                                        timeout=10.0,
                                    ),
                                    _B2_UNRESOLVED
                                    + ": shield-route second "
                                    "actual root delivery was not "
                                    "observed while creation "
                                    "remained pending",
                                )
                                self.assertFalse(gate.done())
                                if inner_task is not None:
                                    self.assertFalse(
                                        inner_task.done()
                                    )
                                self.assertFalse(
                                    _wait_until(
                                        lambda: (
                                            not handle.thread
                                            .is_alive()
                                        ),
                                        timeout=0.75,
                                    ),
                                    "supervisor abandoned the "
                                    "acknowledged pending "
                                    "creation after the second "
                                    "cancellation",
                                )
                            else:
                                if not gate.done():
                                    gate.set_result(None)
                                self.assertTrue(
                                    _wait_until(
                                        lambda: (
                                            root_observer
                                            .phase_delivered(2)
                                        ),
                                        timeout=10.0,
                                    ),
                                    _B2_UNRESOLVED
                                    + ": direct/gather-route "
                                    "later actual root delivery "
                                    "was not observed after the "
                                    "accepted second request "
                                    "and gate release",
                                )
                        finally:
                            if not gate.done():
                                try:
                                    gate.set_result(None)
                                except Exception:
                                    pass
                            _release_plan(plan)
                            finished = handle.finish(timeout=20.0)
                            handle.close()
                self.assertTrue(
                    finished,
                    "repeated pending-creation cleanup exceeded "
                    "the outer ceiling",
                )
                self.assertEqual(
                    root_observer.requests_executed, 2
                )
                self.assertEqual(
                    root_observer.requests_accepted, 2
                )
                self.assertTrue(root_observer.phase_delivered(1))
                self.assertTrue(root_observer.phase_delivered(2))
                if inner_task is not None:
                    self.assertTrue(inner_task.done())
                result, exc = handle.outcome()
                self.assertTrue(
                    result is None,
                    "late child overrode the closure outcome",
                )
                self._assert_protocol_error(exc, "cancelled")
                self.assertEqual(
                    plan["record"]["attempts"], 1,
                    "pending-spawn closure created a second child",
                )
                spawn = plan["record"]["spawns"][0]
                self.assertTrue(
                    spawn["proc"] is not None,
                    "eventual child handle was not retained",
                )
                self.assertEqual(
                    spawn["stdin_bytes"], 0,
                    "input was sent after closure was selected",
                )
                self._assert_reaped(plan["record"])

        for seam in ("before launch", "after launch before delivery"):
            with self.subTest(
                case="PENDING_CREATION_HEALTHY_RELEASE: "
                "within-budget gate release keeps ordinary success ("
                + seam
                + ")"
            ):
                gate = concurrent.futures.Future()
                plan = self._plan(module, "syn_valid_success")
                if seam == "before launch":
                    plan["barrier_pre"] = gate
                else:
                    plan["barrier_post"] = gate
                ack = _B2CreationGateAck(gate)
                with ack:
                    started = time.monotonic()
                    handle = self._start(
                        module, plan, payload,
                        **self._call_kwargs(credential, total=20.0),
                    )
                    finished = False
                    try:
                        self.assertTrue(
                            ack.ready.wait(timeout=10.0),
                            _B2_UNRESOLVED
                            + ": creation gate suspension was "
                            "not acknowledged on its own loop",
                        )
                        self.assertTrue(ack.acknowledged)
                        self.assertTrue(
                            ack.wrapped_pending_at_ack
                        )
                        self.assertFalse(gate.done())
                        self.assertEqual(
                            plan["record"]["attempts"], 1
                        )
                        spawn = plan["record"]["spawns"][0]
                        if seam == "before launch":
                            self.assertTrue(
                                spawn["proc"] is None,
                                "pre-launch gate already had an "
                                "acquired handle",
                            )
                        else:
                            self.assertTrue(
                                spawn["proc"] is not None,
                                "post-launch gate had no "
                                "acquired handle",
                            )
                            self.assertTrue(
                                isinstance(
                                    spawn["proc"].stdin,
                                    _RecordingStdin,
                                )
                                and isinstance(
                                    spawn["proc"].stdout,
                                    _ObservedReader,
                                )
                                and isinstance(
                                    spawn["proc"].stderr,
                                    _ObservedReader,
                                ),
                                "post-launch stream wrappers "
                                "did not exist at the gate",
                            )
                        self.assertTrue(
                            time.monotonic() - started < 20.0,
                            "healthy gate release was not "
                            "within the original budget",
                        )
                        if not gate.done():
                            gate.set_result(None)
                    finally:
                        if not gate.done():
                            try:
                                gate.set_result(None)
                            except Exception:
                                pass
                        _release_plan(plan)
                        finished = handle.finish(timeout=20.0)
                        handle.close()
                self.assertTrue(finished)
                result, exc = handle.outcome()
                self.assertTrue(exc is None, "expected success")
                self.assertEqual(result, _expected_result())
                self.assertEqual(plan["record"]["attempts"], 1)
                spawn = plan["record"]["spawns"][0]
                self.assertTrue(
                    spawn["proc"] is not None,
                    "healthy child handle was not retained",
                )
                self.assertTrue(
                    spawn["stdin_bytes"] > 0,
                    "healthy child received no ordinary input",
                )
                self._assert_reaped(
                    plan["record"], expected_codes=[0]
                )

        with self.subTest(
            case="injected synthetic creation exception is unknown"
        ):
            plan = self._plan(module, "syn_valid_success", fail=True)
            result, exc = self._invoke(
                module, plan, payload,
                **self._call_kwargs(credential),
            )
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "unknown")
            self.assertEqual(plan["record"]["attempts"], 1)
            self.assertTrue(
                plan["record"]["spawns"][0]["proc"] is None,
                "failed spawn fabricated a child",
            )

        with self.subTest(case="actual OS creation failure is unknown"):
            with tempfile.TemporaryDirectory() as owned_dir:
                missing_cwd = str(Path(owned_dir) / "absent-cwd")
                self.assertTrue(
                    not Path(missing_cwd).exists(),
                    "OS-failure cwd unexpectedly exists",
                )
                plan = self._plan(module, "syn_empty")
                with _MissingCwdCreationBoundary(
                    missing_cwd
                ) as boundary:
                    result, exc = self._invoke(
                        module, plan, payload,
                        **self._call_kwargs(credential),
                    )
                self.assertTrue(result is None)
                self._assert_protocol_error(exc, "unknown")
                self.assertTrue(
                    boundary.launch_valid
                    and not boundary.validation_failed,
                    "exact final fixture launch was not validated "
                    "before the OS call",
                )
                self.assertEqual(
                    boundary.os_attempts, 1,
                    "genuine OS creation was not attempted once",
                )
                self.assertEqual(plan["record"]["attempts"], 1)
                self.assertTrue(
                    plan["record"]["spawns"][0]["proc"] is None,
                    "OS creation failure fabricated a child",
                )
                self._assert_reaped(plan["record"])

        with self.subTest(case="deadline before spawn makes no child"):
            payload_before = copy.deepcopy(payload)
            plan = self._plan(module, "syn_valid_success")
            prep_hold = _B2PreparationHold(module)
            with prep_hold:
                self.assertTrue(
                    prep_hold.available,
                    _B2_UNRESOLVED
                    + ": accepted preparation seam was "
                    "unavailable",
                )
                started = time.monotonic()
                handle = self._start(
                    module, plan, payload,
                    **self._call_kwargs(credential, total=5.0),
                )
                finished = False
                try:
                    self.assertTrue(
                        prep_hold.ready.wait(timeout=4.0),
                        _B2_UNRESOLVED
                        + ": successful preparation hold was "
                        "not reached within the original budget",
                    )
                    self.assertEqual(
                        prep_hold.thread_ident,
                        handle.thread.ident,
                    )
                    self.assertTrue(
                        prep_hold.code_identity_matched
                    )
                    self.assertEqual(prep_hold.calls, 1)
                    self.assertEqual(prep_hold.held, 1)
                    self.assertTrue(
                        time.monotonic() - started < 5.0,
                        "preparation readiness did not precede "
                        "the original deadline",
                    )
                    # The product deadline starts in the
                    # worker no later than this readiness
                    # observation, so the observation plus
                    # the unchanged original budget is beyond
                    # that deadline.  This selects only a safe
                    # release time; it does not reset or
                    # change the product deadline.
                    ready_observed = time.monotonic()
                    release_not_before = ready_observed + 5.0
                    remaining = (
                        release_not_before - time.monotonic()
                    )
                    if remaining > 0:
                        time.sleep(remaining)
                    self.assertTrue(
                        time.monotonic() >= release_not_before,
                        "the conservative bound beyond the "
                        "original deadline was not crossed "
                        "before release",
                    )
                    self.assertTrue(handle.thread.is_alive())
                finally:
                    prep_hold.release.set()
                    _release_plan(plan)
                    finished = handle.finish(timeout=20.0)
                    handle.close()
            self.assertTrue(finished)
            self.assertFalse(prep_hold.ceiling_expired)
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "timeout")
            self.assertEqual(
                plan["record"]["attempts"], 0,
                "elapsed budget still spawned a child",
            )
            self.assertEqual(len(plan["record"]["spawns"]), 0)
            self.assertEqual(payload, payload_before)

        with self.subTest(case="ordinary within-budget real success"):
            body = _http._healthy_sse()
            with _http._running_server(
                [_http._length_spec(body)]
            ) as server:
                plan = self._plan(
                    module, "real_child", (str(server.port),)
                )
                result, exc = self._invoke(
                    module, plan, payload,
                    **self._call_kwargs(credential),
                )
                self.assertTrue(exc is None, "expected success")
                self.assertEqual(result, expected)
                _wait_until(
                    lambda: server.snapshot()["handled"] >= 1,
                    timeout=5.0,
                )
                self.assertEqual(
                    len(server.snapshot()["requests"]), 1
                )
            self.assertEqual(plan["record"]["attempts"], 1)
            self._assert_reaped(plan["record"], expected_codes=[0])

    # -- 6: parent admission and worker-thread bridge ------------------

    def test_parent_admission_and_worker_thread_bridge(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_transport")
        )
        module = _import_transport()
        payload = _http._genuine_payload()
        credential = _http._credential()
        expected = _expected_result()

        def same_payload(left, right):
            return left == right or repr(left) == repr(right)

        payload_cases = []

        def add(label, mutator):
            candidate = copy.deepcopy(payload)
            mutator(candidate)
            payload_cases.append((label, candidate))

        add("wrong model", lambda p: p.__setitem__("model", "other"))
        add("extra field", lambda p: p.__setitem__("extra", 1))
        add("missing input", lambda p: p.pop("input"))
        add("stream true", lambda p: p.__setitem__("stream", True))
        add("store numeric zero", lambda p: p.__setitem__("store", 0))
        add("tokens bool",
            lambda p: p.__setitem__("max_output_tokens", True))
        add("tokens too small",
            lambda p: p.__setitem__("max_output_tokens", 15))
        add("nonfinite nested",
            lambda p: p["input"][0]["content"][0].__setitem__(
                "text", float("nan")))
        add("lone surrogate text",
            lambda p: p["input"][0]["content"][0].__setitem__(
                "text", "bad\ud800text"))
        payload_cases.append(("payload none", None))
        payload_cases.append(("payload list", []))
        payload_cases.append(("payload text", "{}"))
        payload_cases.append(("payload bytes", b"{}"))
        circular = copy.deepcopy(payload)
        circular["input"].append(circular)
        payload_cases.append(("circular payload", circular))

        credential_cases = [
            ("empty credential", ""),
            ("space credential", "has space"),
            ("non-ascii credential", "café"),
            ("del credential", "bad\x7fvalue"),
            ("none credential", None),
            ("bytes credential", b"synthetic"),
            ("int credential", 123),
        ]
        socket_cases = [
            ("bool socket timeout", True),
            ("zero socket timeout", 0),
            ("negative socket timeout", -1),
            ("nan socket timeout", float("nan")),
            ("inf socket timeout", float("inf")),
            ("text socket timeout", "5"),
            ("none socket timeout", None),
        ]
        total_cases = [
            ("bool total timeout", True),
            ("false total timeout", False),
            ("zero total timeout", 0),
            ("negative total timeout", -0.5),
            ("nan total timeout", float("nan")),
            ("inf total timeout", float("inf")),
            ("text total timeout", "5"),
            ("none total timeout", None),
        ]
        cancel_cases = [
            ("plain object cancel", object()),
            ("lock cancel", threading.Lock()),
            ("int cancel", 1),
            ("text cancel", "event"),
        ]

        with self.subTest(case="invalid admission refuses before spawn"):
            plan = self._plan(module, "syn_valid_success")
            with _spawn_harness(plan), _DirectNetGuard("loopback"):
                def attempt(candidate_payload, cred, sock, total,
                            cancel_marker=_SENTINEL):
                    kwargs = {
                        "credential": cred,
                        "timeout_seconds": sock,
                        "total_timeout_seconds": total,
                    }
                    if cancel_marker is not _SENTINEL:
                        kwargs["cancel_event"] = cancel_marker
                    try:
                        module.run_payload(candidate_payload, **kwargs)
                    except BaseException as caught:
                        return caught
                    return None

                for label, candidate in payload_cases:
                    with self.subTest(case="invalid: " + label):
                        snapshot = copy.deepcopy(candidate) \
                            if label != "circular payload" else None
                        exc = attempt(candidate, credential, 5.0, 20.0)
                        self._assert_transport_invalid(exc)
                        self._assert_no_marker(exc, (credential,))
                        if snapshot is not None:
                            self.assertTrue(
                                same_payload(candidate, snapshot),
                                "refused call mutated the caller payload",
                            )
                for label, candidate in credential_cases:
                    with self.subTest(case="invalid: " + label):
                        exc = attempt(payload, candidate, 5.0, 20.0)
                        self._assert_transport_invalid(exc)
                for label, candidate in socket_cases:
                    with self.subTest(case="invalid: " + label):
                        exc = attempt(
                            payload, credential, candidate, 20.0
                        )
                        self._assert_transport_invalid(exc)
                for label, candidate in total_cases:
                    with self.subTest(case="invalid: " + label):
                        exc = attempt(
                            payload, credential, 5.0, candidate
                        )
                        self._assert_transport_invalid(exc)
                for label, candidate in cancel_cases:
                    with self.subTest(case="invalid: " + label):
                        exc = attempt(
                            payload, credential, 5.0, 20.0,
                            cancel_marker=candidate,
                        )
                        self._assert_transport_invalid(exc)
            self.assertEqual(
                plan["record"]["attempts"], 0,
                "refused admission still created a child",
            )

        with self.subTest(case="input byte bound refuses before spawn"):
            plan = self._plan(module, "syn_valid_success")
            result, exc = self._invoke(
                module, plan, payload,
                **self._call_kwargs(
                    credential, cancel_event=threading.Event()
                ),
            )
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            input_length = plan["record"]["spawns"][0]["stdin_bytes"]
            self.assertTrue(
                input_length > 100,
                "control call sent no measurable private input",
            )
            exact_plan = self._plan(module, "syn_valid_success")
            with _patched(module, "MAX_INPUT_BYTES", input_length):
                result2, exc2 = self._invoke(
                    module, exact_plan, payload,
                    **self._call_kwargs(credential),
                )
            self.assertTrue(exc2 is None, "expected success")
            self.assertEqual(result2, expected)
            self.assertEqual(
                exact_plan["record"]["spawns"][0]["stdin_bytes"],
                input_length,
            )
            over_plan = self._plan(module, "syn_valid_success")
            with _patched(module, "MAX_INPUT_BYTES", input_length - 1):
                result3, exc3 = self._invoke(
                    module, over_plan, payload,
                    **self._call_kwargs(credential),
                )
            self.assertTrue(result3 is None)
            self._assert_transport_invalid(exc3)
            self.assertEqual(
                over_plan["record"]["attempts"], 0,
                "input overflow still created a child",
            )

        with self.subTest(case="pre-set cancel returns before spawn"):
            plan = self._plan(module, "syn_valid_success")
            preset = threading.Event()
            preset.set()
            snapshot = copy.deepcopy(payload)
            result, exc = self._invoke(
                module, plan, payload,
                **self._call_kwargs(credential, cancel_event=preset),
            )
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "cancelled")
            self.assertEqual(plan["record"]["attempts"], 0)
            self.assertEqual(payload, snapshot)

        with self.subTest(case="worker thread, no ambient authority"):
            body = _http._healthy_sse()
            with tempfile.TemporaryDirectory() as directory:
                keylog_path = Path(directory) / "keylog-sentinel.log"
                sentinel = b"SYNTHETIC-SENTINEL-DO-NOT-TOUCH\n"
                keylog_path.write_bytes(sentinel)
                updates = {
                    "SSLKEYLOGFILE": str(keylog_path),
                    "META_API_KEY": "AMBIENT-SYNTHETIC-NOT-REAL",
                    "OPENAI_API_KEY": "AMBIENT-SYNTHETIC-NOT-REAL",
                    "http_proxy": "http://127.0.0.1:1",
                    "https_proxy": "http://127.0.0.1:1",
                    "PYTHONPATH": directory,
                    "TRANSPORT_TEST_CANARY": "1",
                }
                with _http._running_server(
                    [_http._length_spec(body)]
                ) as server:
                    plan = self._plan(
                        module, "real_child", (str(server.port),)
                    )
                    with _http._temporary_env(updates):
                        handle = self._start(
                            module, plan, payload,
                            name="transport-worker-under-test",
                            **self._call_kwargs(credential),
                        )
                        try:
                            self.assertIsNot(
                                handle.thread, threading.main_thread()
                            )
                            self.assertEqual(
                                handle.thread.name,
                                "transport-worker-under-test",
                            )
                            finished = handle.finish(timeout=25.0)
                        finally:
                            _release_plan(plan)
                            handle.close()
                    self.assertTrue(
                        finished,
                        "worker-thread call exceeded outer ceiling",
                    )
                    result, exc = handle.outcome()
                    self.assertTrue(exc is None, "expected success")
                    self.assertEqual(result, expected)
                    _wait_until(
                        lambda: server.snapshot()["handled"] >= 1,
                        timeout=5.0,
                    )
                    requests = server.snapshot()["requests"]
                    self.assertEqual(len(requests), 1)
                    self._assert_bearer(requests[0], credential)
                spawn = plan["record"]["spawns"][0]
                self._assert_production_spawn(
                    spawn, module, credential,
                    extra_forbidden=("TRANSPORT_TEST_CANARY",),
                )
                self._assert_reaped(plan["record"], expected_codes=[0])
                self.assertEqual(keylog_path.read_bytes(), sentinel)

        with self.subTest(case="no mutable state shared between calls"):
            first_plan = self._plan(module, "syn_valid_success")
            first, first_exc = self._invoke(
                module, first_plan, payload,
                **self._call_kwargs(credential),
            )
            self.assertTrue(first_exc is None, "expected success")
            first["raw"]["sections"][0]["text"] = "MUTATED-BY-CALLER"
            second_plan = self._plan(module, "syn_valid_success")
            second, second_exc = self._invoke(
                module, second_plan, payload,
                **self._call_kwargs(credential),
            )
            self.assertTrue(second_exc is None, "expected success")
            self.assertIsNot(first, second)
            self.assertEqual(second, expected)
            self.assertEqual(
                second["raw"]["sections"][0]["text"],
                "  نَصّ عربي ١٢ / 12 𝄞\nثانٍ  ",
            )
            fail_plan_a = self._plan(module, "syn_failure", ("error",))
            _ra, exc_a = self._invoke(
                module, fail_plan_a, payload,
                **self._call_kwargs(credential),
            )
            fail_plan_b = self._plan(module, "syn_failure", ("error",))
            _rb, exc_b = self._invoke(
                module, fail_plan_b, payload,
                **self._call_kwargs(credential),
            )
            self._assert_protocol_error(exc_a, "error")
            self._assert_protocol_error(exc_b, "error")
            self.assertIsNot(
                exc_a, exc_b,
                "failure outcome instance was shared between calls",
            )


        with self.subTest(
            case="BOUNDARY_PREP_CANCEL: cancel set while "
            "successful preparation is held"
        ):
            payload_before = copy.deepcopy(payload)
            plan = self._plan(module, "syn_valid_success")
            cancel_event = threading.Event()
            prep_hold = _B2PreparationHold(module)
            with prep_hold:
                self.assertTrue(
                    prep_hold.available,
                    _B2_UNRESOLVED
                    + ": accepted preparation seam was "
                    "unavailable",
                )
                started = time.monotonic()
                handle = self._start(
                    module, plan, payload,
                    **self._call_kwargs(
                        credential,
                        total=20.0,
                        cancel_event=cancel_event,
                    ),
                )
                finished = False
                try:
                    self.assertTrue(
                        prep_hold.ready.wait(timeout=10.0),
                        _B2_UNRESOLVED
                        + ": successful preparation hold was "
                        "not reached on the worker",
                    )
                    self.assertEqual(
                        prep_hold.thread_ident,
                        handle.thread.ident,
                    )
                    self.assertTrue(
                        prep_hold.code_identity_matched
                    )
                    self.assertEqual(prep_hold.calls, 1)
                    self.assertEqual(prep_hold.held, 1)
                    self.assertTrue(
                        time.monotonic() - started < 20.0,
                        "preparation cancel was not set while "
                        "the original budget was live",
                    )
                    self.assertFalse(cancel_event.is_set())
                    cancel_event.set()
                finally:
                    prep_hold.release.set()
                    _release_plan(plan)
                    finished = handle.finish(timeout=20.0)
                    handle.close()
            self.assertTrue(finished)
            self.assertFalse(prep_hold.ceiling_expired)
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "cancelled")
            self.assertEqual(plan["record"]["attempts"], 0)
            self.assertEqual(len(plan["record"]["spawns"]), 0)
            self.assertEqual(payload, payload_before)

        with self.subTest(
            case="BOUNDARY_PREP_DEADLINE: original deadline "
            "crosses while successful preparation is held"
        ):
            payload_before = copy.deepcopy(payload)
            plan = self._plan(module, "syn_valid_success")
            prep_hold = _B2PreparationHold(module)
            with prep_hold:
                self.assertTrue(
                    prep_hold.available,
                    _B2_UNRESOLVED
                    + ": accepted preparation seam was "
                    "unavailable",
                )
                started = time.monotonic()
                handle = self._start(
                    module, plan, payload,
                    **self._call_kwargs(credential, total=5.0),
                )
                finished = False
                try:
                    self.assertTrue(
                        prep_hold.ready.wait(timeout=4.0),
                        _B2_UNRESOLVED
                        + ": successful preparation hold was "
                        "not reached within the original budget",
                    )
                    self.assertEqual(
                        prep_hold.thread_ident,
                        handle.thread.ident,
                    )
                    self.assertTrue(
                        prep_hold.code_identity_matched
                    )
                    self.assertEqual(prep_hold.calls, 1)
                    self.assertEqual(prep_hold.held, 1)
                    self.assertTrue(
                        time.monotonic() - started < 5.0,
                        "preparation readiness did not precede "
                        "the original deadline",
                    )
                    # The product deadline starts in the
                    # worker no later than this readiness
                    # observation, so the observation plus
                    # the unchanged original budget is beyond
                    # that deadline.  This selects only a safe
                    # release time; it does not reset or
                    # change the product deadline.
                    ready_observed = time.monotonic()
                    release_not_before = ready_observed + 5.0
                    remaining = (
                        release_not_before - time.monotonic()
                    )
                    if remaining > 0:
                        time.sleep(remaining)
                    self.assertTrue(
                        time.monotonic() >= release_not_before,
                        "the conservative bound beyond the "
                        "original deadline was not crossed "
                        "before release",
                    )
                    self.assertTrue(handle.thread.is_alive())
                finally:
                    prep_hold.release.set()
                    _release_plan(plan)
                    finished = handle.finish(timeout=20.0)
                    handle.close()
            self.assertTrue(finished)
            self.assertFalse(prep_hold.ceiling_expired)
            result, exc = handle.outcome()
            self.assertTrue(result is None)
            self._assert_protocol_error(exc, "timeout")
            self.assertEqual(plan["record"]["attempts"], 0)
            self.assertEqual(len(plan["record"]["spawns"]), 0)
            self.assertEqual(payload, payload_before)

        with self.subTest(
            case="BOUNDARY_PREP healthy opposite: within-budget "
            "preparation release keeps ordinary success"
        ):
            payload_before = copy.deepcopy(payload)
            plan = self._plan(module, "syn_valid_success")
            prep_hold = _B2PreparationHold(module)
            with prep_hold:
                self.assertTrue(
                    prep_hold.available,
                    _B2_UNRESOLVED
                    + ": accepted preparation seam was "
                    "unavailable",
                )
                started = time.monotonic()
                handle = self._start(
                    module, plan, payload,
                    **self._call_kwargs(credential, total=20.0),
                )
                finished = False
                try:
                    self.assertTrue(
                        prep_hold.ready.wait(timeout=10.0),
                        _B2_UNRESOLVED
                        + ": successful preparation hold was "
                        "not reached on the worker",
                    )
                    self.assertEqual(
                        prep_hold.thread_ident,
                        handle.thread.ident,
                    )
                    self.assertTrue(
                        prep_hold.code_identity_matched
                    )
                    self.assertEqual(prep_hold.calls, 1)
                    self.assertEqual(prep_hold.held, 1)
                    self.assertTrue(
                        time.monotonic() - started < 20.0,
                        "healthy preparation release was not "
                        "within the original budget",
                    )
                    prep_hold.release.set()
                finally:
                    prep_hold.release.set()
                    _release_plan(plan)
                    finished = handle.finish(timeout=20.0)
                    handle.close()
            self.assertTrue(finished)
            self.assertFalse(prep_hold.ceiling_expired)
            result, exc = handle.outcome()
            self.assertTrue(exc is None, "expected success")
            self.assertEqual(result, expected)
            self.assertEqual(plan["record"]["attempts"], 1)
            self.assertEqual(payload, payload_before)
            self._assert_reaped(
                plan["record"], expected_codes=[0]
            )
if __name__ == "__main__":
    unittest.main()
