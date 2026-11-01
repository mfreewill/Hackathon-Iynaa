"""Owned one-call Meta transport supervisor and child entry point.

This module owns both sides of one short-lived transport call.
Ordinary import is inert: no process is started, no network call is
made, and no credential is read from arguments, the environment or
any file.

The synchronous ``run_payload`` supervisor validates and prepares
one private child input under a monotonic total deadline, then runs
one per-call asynchronous lifecycle for exactly one direct child of
the current interpreter. That child is this same module, launched
with literal ``-I -B`` and an explicit OS-bootstrap-only
environment; it receives its input only on stdin. The supervisor
starts bounded output readers before stdin drain, independently
validates the complete child frame, and admits a result only after
EOF, empty stderr, zero exit and owned cleanup have settled.

When run as a script (including under ``python -I -B`` with the
absolute path of this file), the module first resolves its own
package source parent for imports and then calls ``_child_main()``.
The child reads exactly one strict JSON input object from binary
stdin, validates it before any send, calls the accepted
``meta_http.send_payload`` at most once, constructs and validates
one complete closed output frame before its first stdout write, and
emits that frame exactly once on binary stdout. It never retries,
never writes diagnostic text, and never emits a second frame after an
output-write failure.
"""
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    _SOURCE_PARENT = str(Path(__file__).resolve().parent.parent)
    if _SOURCE_PARENT not in sys.path:
        sys.path.insert(0, _SOURCE_PARENT)

from teacher_demo import meta_http, meta_protocol, meta_stream

MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
IO_CHUNK_BYTES = 64 * 1024

__all__ = [
    "IO_CHUNK_BYTES",
    "MAX_INPUT_BYTES",
    "MAX_OUTPUT_BYTES",
    "MAX_STDERR_BYTES",
    "run_payload",
]

_INPUT_FIELDS = frozenset(("payload", "credential", "timeout_seconds"))
_RESULT_FIELDS = frozenset((
    "raw",
    "model_name",
    "response_id",
    "outcome",
))
_SUCCESS_OUTCOME = "ok"

_FAILURE_ERROR = "error"
_FAILURE_UNKNOWN = "unknown"
_FAILURE_INVALID_SCHEMA = "invalid_schema"
_FAILURE_INCOMPLETE = "incomplete"
_FAILURE_REFUSAL = "refusal"
_FAILURE_EMPTY = "empty"
_FAILURE_CANCELLED = "cancelled"

_FAILURE_BY_OUTCOME = {
    _FAILURE_ERROR: _FAILURE_ERROR,
    _FAILURE_UNKNOWN: _FAILURE_UNKNOWN,
    _FAILURE_INVALID_SCHEMA: _FAILURE_INVALID_SCHEMA,
    _FAILURE_INCOMPLETE: _FAILURE_INCOMPLETE,
    _FAILURE_REFUSAL: _FAILURE_REFUSAL,
    _FAILURE_EMPTY: _FAILURE_EMPTY,
    _FAILURE_CANCELLED: _FAILURE_CANCELLED,
}

_UTF8_BOM = b"\xef\xbb\xbf"


def _failure_frame_bytes(outcome):
    """Return the complete fixed failure frame for ``outcome``."""
    if isinstance(outcome, str):
        failure = _FAILURE_BY_OUTCOME.get(outcome, _FAILURE_UNKNOWN)
    else:
        failure = _FAILURE_UNKNOWN
    text = json.dumps(
        {"failure": failure},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )
    return text.encode("utf-8") + b"\n"


def _diagnosed_failure_frame_bytes(diagnostic, credential):
    """Build the one closed diagnosed incomplete frame, or None."""
    if not isinstance(credential, str) or not credential:
        return None
    validated = meta_protocol._validated_diagnostic(
        diagnostic, credential=credential
    )
    if validated is None:
        return None
    frame = {
        "failure": _FAILURE_INCOMPLETE,
        "diagnostic": validated,
    }
    try:
        if meta_stream._contains_secret(frame, credential):
            return None
        frame_text = json.dumps(
            frame,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        frame_bytes = frame_text.encode("utf-8") + b"\n"
        if credential in frame_text:
            return None
        limit = MAX_OUTPUT_BYTES
        if isinstance(limit, bool) or not isinstance(limit, int):
            return None
        if limit < 0 or len(frame_bytes) > limit:
            return None
        if meta_protocol._strict_json_loads(frame_text) != frame:
            return None
    except Exception:
        return None
    return frame_bytes


def _caught_failure_frame_bytes(caught, credential):
    """Build a caught error's frame, omitting unsafe diagnostics."""
    try:
        outcome = getattr(caught, "outcome", None)
    except Exception:
        outcome = None
    if outcome == _FAILURE_INCOMPLETE:
        try:
            candidate = getattr(caught, "diagnostic", None)
        except Exception:
            candidate = None
        if candidate is not None:
            diagnosed = _diagnosed_failure_frame_bytes(
                candidate, credential
            )
            if diagnosed is not None:
                return diagnosed
    return _failure_frame_bytes(outcome)


def _read_input_bytes():
    """Read binary stdin to EOF, serving at most the bound + 1."""
    limit = MAX_INPUT_BYTES
    if isinstance(limit, bool) or not isinstance(limit, int):
        return None
    if limit < 0:
        return None
    chunk_limit = IO_CHUNK_BYTES
    if isinstance(chunk_limit, bool):
        chunk_limit = limit + 1
    elif not isinstance(chunk_limit, int) or chunk_limit <= 0:
        chunk_limit = limit + 1
    stream = sys.stdin.buffer
    parts = []
    total = 0
    while total <= limit:
        remaining = limit + 1 - total
        chunk = stream.read(min(chunk_limit, remaining))
        if chunk is None:
            return None
        if isinstance(chunk, bytearray):
            chunk = bytes(chunk)
        elif isinstance(chunk, memoryview):
            chunk = chunk.tobytes()
        if not isinstance(chunk, bytes):
            return None
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
    if total > limit:
        return None
    return b"".join(parts)


def _parse_input(data):
    """Parse the one closed child input object, or return None."""
    if data.startswith(_UTF8_BOM):
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        value = meta_protocol._strict_json_loads(text)
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    if set(value.keys()) != _INPUT_FIELDS:
        return None
    return (
        value["payload"],
        value["credential"],
        value["timeout_seconds"],
    )


def _success_frame_bytes(result, credential):
    """Validate a helper result and build its complete safe frame.

    Returns the frame bytes (including the single terminal newline)
    only when the result is the accepted four-field success, every
    nested value is strict JSON data, the credential appears neither
    in the decoded enclosing frame nor in its exact serialization,
    and the frame fits the output bound. Any other case returns
    None, which the caller turns into a fixed error frame before
    any output has been written.
    """
    if not isinstance(result, dict):
        return None
    if set(result.keys()) != _RESULT_FIELDS:
        return None
    if not isinstance(result["raw"], dict):
        return None
    if result["model_name"] != meta_protocol.MODEL:
        return None
    if result["outcome"] != _SUCCESS_OUTCOME:
        return None
    if not meta_protocol._is_nonblank_text(result["response_id"]):
        return None
    if not isinstance(credential, str) or not credential:
        return None
    try:
        if not meta_http._is_json_data(result, set()):
            return None
    except Exception:
        return None
    frame = {"result": result}
    try:
        if meta_stream._contains_secret(frame, credential):
            return None
        frame_text = json.dumps(
            frame,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        frame_bytes = frame_text.encode("utf-8") + b"\n"
        if credential in frame_text:
            return None
        limit = MAX_OUTPUT_BYTES
        if isinstance(limit, bool) or not isinstance(limit, int):
            return None
        if limit < 0 or len(frame_bytes) > limit:
            return None
        if meta_protocol._strict_json_loads(frame_text) != frame:
            return None
    except Exception:
        return None
    return frame_bytes


def _emit_frame(frame_bytes):
    """Write one complete frame once; never retry or diagnose."""
    try:
        stream = sys.stdout.buffer
        stream.write(frame_bytes)
        stream.flush()
    except Exception:
        return


def _child_main():
    """Run the private binary-stdin/binary-stdout child once."""
    try:
        data = _read_input_bytes()
    except Exception:
        data = None
    if data is None:
        _emit_frame(_failure_frame_bytes(_FAILURE_INVALID_SCHEMA))
        return
    try:
        parsed = _parse_input(data)
    except Exception:
        parsed = None
    if parsed is None:
        _emit_frame(_failure_frame_bytes(_FAILURE_INVALID_SCHEMA))
        return
    payload, credential, timeout_seconds = parsed
    try:
        result = meta_http.send_payload(
            payload,
            credential=credential,
            timeout_seconds=timeout_seconds,
        )
    except meta_protocol.MetaProtocolError as caught:
        _emit_frame(
            _caught_failure_frame_bytes(caught, credential)
        )
        return
    except ValueError:
        _emit_frame(_failure_frame_bytes(_FAILURE_INVALID_SCHEMA))
        return
    except Exception:
        _emit_frame(_failure_frame_bytes(_FAILURE_UNKNOWN))
        return
    frame_bytes = _success_frame_bytes(result, credential)
    if frame_bytes is None:
        _emit_frame(_failure_frame_bytes(_FAILURE_ERROR))
        return
    _emit_frame(frame_bytes)


import asyncio
import os
import subprocess
import threading
import time

_TRANSPORT_INVALID = "meta transport request is invalid"
_TIMEOUT_OUTCOME = "timeout"
_WATCH_INTERVAL_SECONDS = 0.005
_BOOTSTRAP_ENV_KEYS = (
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
)
_FIXED_PYTHON_ENV = {
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUTF8": "1",
    "PYTHONIOENCODING": "utf-8",
    "PYTHONNOUSERSITE": "1",
    "PYTHONOPTIMIZE": "0",
}


class _SelectedOutcome(Exception):
    """Internal control flow: a fixed closure outcome was selected."""

    def __init__(self, outcome):
        super().__init__()
        self.outcome = outcome


class _SupervisorState:
    """Per-call deadline and cancellation selection state."""

    def __init__(self, deadline, cancel_event):
        self.deadline = deadline
        self.cancel_event = cancel_event
        self.selected = None
        self.loop = None
        self.root_task = None
        self._watch_handle = None

    def select(self, outcome):
        if self.selected is None:
            self.selected = outcome
            self.stop_watch()
        return self.selected

    def check(self):
        if self.selected is not None:
            return self.selected
        if self.cancel_event is not None:
            if self.cancel_event.is_set():
                return self.select(_FAILURE_CANCELLED)
        if time.monotonic() >= self.deadline:
            return self.select(_TIMEOUT_OUTCOME)
        return None

    def select_after_cancellation(self):
        selected = self.check()
        if selected is None:
            selected = self.select(_FAILURE_CANCELLED)
        return selected

    def request_closure(self, outcome):
        if self.selected is not None:
            return self.selected
        selected = self.select(outcome)
        task = self.root_task
        if task is None or task.done():
            return selected
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if current is not task:
            task.cancel()
        return selected

    def start_watch(self):
        self.loop = asyncio.get_running_loop()
        self.root_task = asyncio.current_task()
        self._schedule_watch()

    def stop_watch(self):
        handle = self._watch_handle
        self._watch_handle = None
        if handle is not None:
            handle.cancel()

    def _schedule_watch(self):
        if self.selected is not None or self.loop is None:
            return
        remaining = self.deadline - time.monotonic()
        if self.cancel_event is None and remaining == float("inf"):
            return
        if remaining <= 0:
            delay = 0.0
        else:
            delay = min(_WATCH_INTERVAL_SECONDS, remaining)
        self._watch_handle = self.loop.call_later(
            delay, self._watch_tick
        )

    def _watch_tick(self):
        self._watch_handle = None
        if self.selected is not None:
            return
        if self.check() is not None:
            task = self.root_task
            if task is not None and not task.done():
                task.cancel()
            return
        self._schedule_watch()


def _uncancel_current_task():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return
    if task is None:
        return
    uncancel = getattr(task, "uncancel", None)
    if callable(uncancel):
        try:
            uncancel()
        except Exception:
            pass


def _invalid_transport_request():
    raise ValueError(_TRANSPORT_INVALID)


def _bound_value(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0:
        return None
    return value


def _io_chunk_size(fallback):
    value = IO_CHUNK_BYTES
    if isinstance(value, bool) or not isinstance(value, int):
        return fallback
    if value <= 0:
        return fallback
    return value


def _prepare_supervisor_input(payload, credential, timeout_seconds):
    """Validate with the accepted helper and serialize child input."""
    try:
        meta_http._prepare_body(payload, credential, timeout_seconds)
    except Exception:
        raise ValueError(_TRANSPORT_INVALID) from None
    try:
        text = json.dumps(
            {
                "payload": payload,
                "credential": credential,
                "timeout_seconds": timeout_seconds,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        data = text.encode("utf-8")
    except Exception:
        raise ValueError(_TRANSPORT_INVALID) from None
    limit = _bound_value(MAX_INPUT_BYTES)
    if limit is None or len(data) > limit:
        raise ValueError(_TRANSPORT_INVALID)
    return data


def _child_environment():
    environment = {}
    for name in _BOOTSTRAP_ENV_KEYS:
        if name in os.environ:
            environment[name] = os.environ[name]
    environment.update(_FIXED_PYTHON_ENV)
    return environment


async def _create_child_process():
    script = Path(__file__).resolve()
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-B",
        str(script),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_environment(),
        cwd=str(script.parent),
    )


def _close_stream_writer(writer):
    if writer is None:
        return
    try:
        is_closing = getattr(writer, "is_closing", None)
        if callable(is_closing) and is_closing():
            return
        writer.close()
    except Exception:
        pass


def _close_child_stdin(process):
    _close_stream_writer(getattr(process, "stdin", None))


def _stop_child_process(process):
    try:
        if process.returncode is None:
            process.terminate()
    except Exception:
        pass


def _kill_child_process(process):
    try:
        if process.returncode is None:
            process.kill()
    except Exception:
        pass


async def _read_child_stream(stream, *, retain, state):
    """Read one child stream to EOF under its actual byte bound."""
    if stream is None:
        state.request_closure(_FAILURE_UNKNOWN)
        return ("error", b"" if retain else None, 0)
    collected = bytearray() if retain else None
    total = 0
    try:
        while True:
            limit = _bound_value(
                MAX_OUTPUT_BYTES if retain else MAX_STDERR_BYTES
            )
            if limit is None:
                state.request_closure(_FAILURE_UNKNOWN)
                return (
                    "error",
                    bytes(collected) if retain else None,
                    total,
                )
            remaining = limit + 1 - total
            chunk = await stream.read(
                min(_io_chunk_size(remaining), remaining)
            )
            if isinstance(chunk, bytearray):
                chunk = bytes(chunk)
            elif isinstance(chunk, memoryview):
                chunk = chunk.tobytes()
            if not isinstance(chunk, bytes):
                state.request_closure(_FAILURE_UNKNOWN)
                return (
                    "error",
                    bytes(collected) if retain else None,
                    total,
                )
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                state.request_closure(_FAILURE_UNKNOWN)
                return (
                    "overflow",
                    bytes(collected) if retain else None,
                    total,
                )
            if retain:
                collected.extend(chunk)
            else:
                state.request_closure(_FAILURE_UNKNOWN)
                return ("nonempty", None, total)
    except asyncio.CancelledError:
        raise
    except Exception:
        state.request_closure(_FAILURE_UNKNOWN)
        return ("error", bytes(collected) if retain else None, total)
    return ("eof", bytes(collected) if retain else None, total)


async def _write_child_input(process, data, state):
    """Write the one prepared input, drain it, and close stdin."""
    stream = getattr(process, "stdin", None)
    if stream is None:
        state.request_closure(_FAILURE_UNKNOWN)
        return "error"
    try:
        if state.check() is not None:
            return "skipped"
        stream.write(data)
        await stream.drain()
    except asyncio.CancelledError:
        raise
    except Exception:
        state.request_closure(_FAILURE_UNKNOWN)
        return "error"
    finally:
        _close_stream_writer(stream)
    return "ok"


def _fixed_protocol_error(outcome, diagnostic=None):
    """Build one fresh fixed protocol error for a closed outcome."""
    if outcome == _TIMEOUT_OUTCOME:
        fixed = _TIMEOUT_OUTCOME
    elif isinstance(outcome, str):
        fixed = _FAILURE_BY_OUTCOME.get(outcome, _FAILURE_UNKNOWN)
    else:
        fixed = _FAILURE_UNKNOWN
    error = meta_protocol.MetaProtocolError(fixed)
    if fixed == _FAILURE_INCOMPLETE and diagnostic is not None:
        validated = meta_protocol._validated_diagnostic(diagnostic)
        if validated is not None:
            error.diagnostic = validated
    return error


def _root_cancellation_delivered():
    """True when the current task has an undelivered-count cancel."""
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return False
    if task is None:
        return False
    cancelling = getattr(task, "cancelling", None)
    if not callable(cancelling):
        return True
    try:
        return cancelling() > 0
    except Exception:
        return False


def _retrieve_task_exception(task):
    if task is None:
        return
    try:
        if task.done() and not task.cancelled():
            task.exception()
    except Exception:
        pass


async def _drain_child_stream(stream):
    """Read one child stream to EOF, discarding every byte."""
    if stream is None:
        return
    try:
        while True:
            chunk = await stream.read(_io_chunk_size(64 * 1024))
            if isinstance(chunk, bytearray):
                chunk = bytes(chunk)
            elif isinstance(chunk, memoryview):
                chunk = chunk.tobytes()
            if not isinstance(chunk, bytes) or not chunk:
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        return


def _decode_child_frame(data, credential):
    """Independently validate one complete child output frame.

    Returns ``("result", result)`` for the one accepted success
    frame, ``("failure", outcome)`` for one legacy closed failure
    frame, ``("failure", "incomplete", diagnostic)`` for the one
    closed diagnosed failure frame, or ``(None, None)`` for
    anything else. The complete bytes are
    checked as received: exactly one JSON object (legal internal
    whitespace preserved) plus exactly one final newline, with no
    prefix, second frame or trailing byte beyond that newline.
    """
    if not isinstance(data, bytes) or not data:
        return (None, None)
    if not data.endswith(b"\n"):
        return (None, None)
    body = data[:-1]
    if not body.startswith(b"{") or not body.endswith(b"}"):
        return (None, None)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return (None, None)
    try:
        frame = meta_protocol._strict_json_loads(text)
    except Exception:
        return (None, None)
    if not isinstance(frame, dict):
        return (None, None)
    keys = set(frame.keys())
    if keys == {"failure"}:
        failure = frame["failure"]
        if isinstance(failure, str) and failure in _FAILURE_BY_OUTCOME:
            return ("failure", _FAILURE_BY_OUTCOME[failure])
        return (None, None)
    if keys == {"failure", "diagnostic"}:
        if frame["failure"] != _FAILURE_INCOMPLETE:
            return (None, None)
        if type(frame["failure"]) is not str:
            return (None, None)
        if not isinstance(credential, str) or not credential:
            return (None, None)
        try:
            if credential in text:
                return (None, None)
            if meta_stream._contains_secret(frame, credential):
                return (None, None)
        except Exception:
            return (None, None)
        diagnostic = meta_protocol._validated_diagnostic(
            frame["diagnostic"], credential=credential
        )
        if diagnostic is None:
            return (None, None)
        return ("failure", _FAILURE_INCOMPLETE, diagnostic)
    if keys != {"result"}:
        return (None, None)
    result = frame["result"]
    if not isinstance(result, dict):
        return (None, None)
    if set(result.keys()) != _RESULT_FIELDS:
        return (None, None)
    if not isinstance(result["raw"], dict):
        return (None, None)
    if result["model_name"] != meta_protocol.MODEL:
        return (None, None)
    if result["outcome"] != _SUCCESS_OUTCOME:
        return (None, None)
    if not meta_protocol._is_nonblank_text(result["response_id"]):
        return (None, None)
    if not isinstance(credential, str) or not credential:
        return (None, None)
    try:
        if credential in text:
            return (None, None)
        if meta_stream._contains_secret(frame, credential):
            return (None, None)
    except Exception:
        return (None, None)
    return ("result", result)


async def _run_operation(data, credential, state):
    """Drive one owned child lifecycle to a settled fixed outcome.

    This coroutine is the genuine per-call operation root. Every
    owned wait happens directly in this frame, with at most one
    shield or gather wrapper between the root and a still-pending
    owned Task, so repeated root cancellation is absorbed here
    while creation, IO and cleanup ownership is retained.
    """
    process = None
    creation_task = None
    writer_task = None
    stdout_task = None
    stderr_task = None
    wait_task = None
    write_result = None
    stdout_result = None
    stderr_result = None
    decoded_kind = None
    decoded_value = None
    decoded_diagnostic = None
    io_completed = False
    terminal_settled = False
    try:
        state.start_watch()
        if state.check() is None:
            creation_task = asyncio.create_task(
                _create_child_process()
            )
            while True:
                try:
                    process = await asyncio.shield(creation_task)
                    break
                except asyncio.CancelledError:
                    if _root_cancellation_delivered():
                        state.select_after_cancellation()
                        _uncancel_current_task()
                    if creation_task.done():
                        break
                except Exception:
                    break
            if process is None and creation_task.done():
                if not creation_task.cancelled():
                    if creation_task.exception() is None:
                        process = creation_task.result()
            if process is None:
                _retrieve_task_exception(creation_task)
                if state.selected is None:
                    state.select(_FAILURE_UNKNOWN)
        if process is not None and state.check() is None:
            stdout_task = asyncio.create_task(
                _read_child_stream(
                    process.stdout, retain=True, state=state
                )
            )
            stderr_task = asyncio.create_task(
                _read_child_stream(
                    process.stderr, retain=False, state=state
                )
            )
            writer_task = asyncio.create_task(
                _write_child_input(process, data, state)
            )
            try:
                gathered = await asyncio.gather(
                    writer_task, stdout_task, stderr_task
                )
                write_result = gathered[0]
                stdout_result = gathered[1]
                stderr_result = gathered[2]
                io_completed = True
            except asyncio.CancelledError:
                if _root_cancellation_delivered():
                    state.select_after_cancellation()
                    _uncancel_current_task()
                elif state.selected is None:
                    state.select(_FAILURE_UNKNOWN)
            except Exception:
                if state.selected is None:
                    state.select(_FAILURE_UNKNOWN)
        if process is not None and io_completed:
            io_ok = (
                write_result == "ok"
                and stdout_result is not None
                and stdout_result[0] == "eof"
                and stderr_result is not None
                and stderr_result[0] == "eof"
                and stderr_result[2] == 0
            )
            if io_ok and state.check() is None:
                decoded = _decode_child_frame(
                    stdout_result[1], credential
                )
                decoded_kind = decoded[0]
                decoded_value = decoded[1]
                if len(decoded) > 2:
                    decoded_diagnostic = decoded[2]
            elif not io_ok and state.selected is None:
                state.select(_FAILURE_UNKNOWN)
            wait_task = asyncio.create_task(process.wait())
            try:
                await wait_task
                terminal_settled = True
            except asyncio.CancelledError:
                if _root_cancellation_delivered():
                    state.select_after_cancellation()
                    _uncancel_current_task()
                elif state.selected is None:
                    state.select(_FAILURE_UNKNOWN)
            except Exception:
                if state.selected is None:
                    state.select(_FAILURE_UNKNOWN)
            if terminal_settled:
                if process.returncode != 0:
                    if state.selected is None:
                        state.select(_FAILURE_UNKNOWN)
                selected = state.check()
                if selected is not None:
                    raise _fixed_protocol_error(selected)
                if decoded_kind == "result":
                    return decoded_value
                if decoded_kind == "failure":
                    raise _fixed_protocol_error(
                        decoded_value,
                        diagnostic=decoded_diagnostic,
                    )
                raise _fixed_protocol_error(_FAILURE_UNKNOWN)
        if process is None:
            raise _fixed_protocol_error(state.selected)
        if state.selected is None:
            state.select(_FAILURE_UNKNOWN)
        _close_child_stdin(process)
        _stop_child_process(process)
        owned_tasks = [
            task
            for task in (
                writer_task,
                stdout_task,
                stderr_task,
                wait_task,
            )
            if task is not None
        ]
        for task in owned_tasks:
            if not task.done():
                task.cancel()
        for task in owned_tasks:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    if _root_cancellation_delivered():
                        state.select_after_cancellation()
                        _uncancel_current_task()
                except Exception:
                    break
            _retrieve_task_exception(task)
        drain_tasks = [
            asyncio.create_task(
                _drain_child_stream(
                    getattr(process, "stdout", None)
                )
            ),
            asyncio.create_task(
                _drain_child_stream(
                    getattr(process, "stderr", None)
                )
            ),
        ]
        for task in drain_tasks:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    if _root_cancellation_delivered():
                        state.select_after_cancellation()
                        _uncancel_current_task()
                except Exception:
                    break
            _retrieve_task_exception(task)
        if process.returncode is None:
            _kill_child_process(process)
        reap_task = asyncio.create_task(process.wait())
        while not reap_task.done():
            try:
                await asyncio.shield(reap_task)
            except asyncio.CancelledError:
                if _root_cancellation_delivered():
                    state.select_after_cancellation()
                    _uncancel_current_task()
            except Exception:
                break
        _retrieve_task_exception(reap_task)
        raise _fixed_protocol_error(state.selected)
    finally:
        state.stop_watch()


def run_payload(
    payload,
    *,
    credential,
    timeout_seconds,
    total_timeout_seconds,
    cancel_event=None,
):
    """Run one owned transport call and return its accepted result.

    Synchronous and per-call: the monotonic total deadline starts
    before input preparation, exactly one direct child is owned
    behind an ``asyncio.run`` operation root, and this callable
    returns or raises only after owned creation, IO, cleanup and
    reaping have settled. Invalid arguments raise one plain fixed
    ``ValueError``; every operational outcome is a fresh fixed
    ``meta_protocol.MetaProtocolError``.
    """
    started = time.monotonic()
    if isinstance(total_timeout_seconds, bool) or not isinstance(
        total_timeout_seconds, (int, float)
    ):
        raise ValueError(_TRANSPORT_INVALID)
    if not total_timeout_seconds > 0:
        raise ValueError(_TRANSPORT_INVALID)
    if total_timeout_seconds == float("inf"):
        raise ValueError(_TRANSPORT_INVALID)
    if cancel_event is not None and not isinstance(
        cancel_event, threading.Event
    ):
        raise ValueError(_TRANSPORT_INVALID)
    try:
        deadline = started + total_timeout_seconds
    except OverflowError:
        deadline = float("inf")
    state = _SupervisorState(deadline, cancel_event)
    selected = state.check()
    if selected is not None:
        raise _fixed_protocol_error(selected)
    data = _prepare_supervisor_input(
        payload, credential, timeout_seconds
    )
    selected = state.check()
    if selected is not None:
        raise _fixed_protocol_error(selected)
    failure_outcome = None
    try:
        return asyncio.run(_run_operation(data, credential, state))
    except meta_protocol.MetaProtocolError:
        raise
    except asyncio.CancelledError:
        failure_outcome = state.selected or _FAILURE_CANCELLED
    except Exception:
        failure_outcome = state.selected or _FAILURE_UNKNOWN
    raise _fixed_protocol_error(failure_outcome)


if __name__ == "__main__":
    _child_main()
