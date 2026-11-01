"""Bounded failure-diagnostics regression tests.

These five ordinary ``unittest`` methods pin one optional closed
diagnostic projection for the three originating ``incomplete``
branches of the provider path (non-null ``incomplete_details``,
envelope status ``incomplete``, and an explicit unfinished ordinary
message status inside a completed envelope), carried through the
actual stream collector, child frame, supervisor, and ``MetaProvider``
error. Every existing outcome, precedence, refusal, four-field
success, settlement requirement, and secret boundary is asserted to
stay unchanged.

Declared seams only; no network, socket, real child process, HTTP,
browser, model, or credential-store access occurs in this file:

* the parser and stream collector are exercised directly in memory;
* the child is the actual ``meta_transport._child_main`` over binary
  in-memory stdin/stdout with only ``meta_http.send_payload``
  replaced;
* the supervisor is the actual ``run_payload``/``_run_operation``
  with only ``meta_transport._create_child_process`` replaced by an
  owned inert fake process;
* the provider is the actual ``MetaProvider.__call__`` with only
  ``meta_transport.run_payload`` replaced.

Note: method four enters the real ``asyncio.run`` owner.
CPython's Windows event loop may create an internal local
self-pipe/socketpair; that is a local runtime requirement of the
event loop itself, not network use by these tests.

All data, credentials, and sentinels below are synthetic. Fixtures are
built inline. No diagnostic module
is imported: while the feature is absent each method fails on an
ordinary assertion (a missing ``diagnostic``), never on import or
collection.
"""
from __future__ import annotations

import asyncio
import copy
import io
import json
import sys
import threading
import time
import traceback
import types
import unittest
from pathlib import Path

# Staged src layout, as in tests/test_meta_provider.py.
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from teacher_demo import (  # noqa: E402
    meta_http,
    meta_protocol,
    meta_provider,
    meta_stream,
    meta_transport,
)
from teacher_demo.question_protocol_v2 import question_prompt_v2  # noqa: E402

_REFUSED = "meta protocol response refused"
_HOSTILE = "HOSTILE-DIAGNOSTIC-SENTINEL-9f27c1"
_REASONING_SENTINEL = "PRIVATE-REASONING-SENTINEL-51bd08"
_VENDOR_REASON = "synthetic_vendor_reason_xyz"
_STREAM_CREDENTIAL = "SYNTHETIC-STREAM-CREDENTIAL-4d2f9a"
_CHILD_CREDENTIAL = "SyntheticChildCredential001"
_SUPERVISOR_CREDENTIAL = "SYNTHETIC-SUPERVISOR-CREDENTIAL-001"
_PROVIDER_CREDENTIAL = "SYNTHETIC-PROVIDER-CREDENTIAL-001"
_MAX_SAFE = (1 << 53) - 1
_OMIT = object()

_GRADE = "الصف الرابع الابتدائي"
_OBJECTIVE = "أن يقرأ التلميذ النص قراءة صحيحة ويستخرج الفكرة الرئيسة"
_ARABIC_SENTENCE = "ذهب التلاميذ إلى الحديقة في الصباح وقرأوا القصة"
# A heavily marked synthetic word, spelled by codepoint so the exact
# combining sequence is unambiguous: noon, fatha, sad, dammatan,
# shadda.
_MARKED_WORD = "نَصٌّ"
_SUPPLEMENTARY = "\U0001d11e"


def _healthy_raw():
    """Return one fresh synthetic ordinary-JSON fixture value."""
    return {
        "synthetic_only": "declared synthetic fixture data",
        "sentence": _ARABIC_SENTENCE,
        "marked": _MARKED_WORD,
        "symbol": _SUPPLEMENTARY,
        "count": 2,
    }


def _inner_raw_text():
    return json.dumps(_healthy_raw(), ensure_ascii=False)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _compact_bytes(value):
    text = json.dumps(
        value, ensure_ascii=False, allow_nan=False,
        separators=(",", ":"),
    )
    return text.encode("utf-8")


def _compact_frame_bytes(value):
    return _compact_bytes(value) + b"\n"


def _message_item(status, parts=None, role="assistant"):
    """One message output item; the status key is always explicit."""
    if parts is None:
        parts = [{"type": "output_text", "text": _inner_raw_text()}]
    return {
        "type": "message",
        "role": role,
        "status": status,
        "content": list(parts),
    }


def _reasoning_item():
    return {
        "type": "reasoning",
        "summary": [
            {"type": "summary_text", "text": _REASONING_SENTINEL}
        ],
        "encrypted_content": _REASONING_SENTINEL,
        "status": "in_progress",
    }


def _envelope(
    *,
    response_id="resp_diag_001",
    status="completed",
    error=None,
    incomplete_details=None,
    output=None,
    usage=_OMIT,
    model=None,
    extra=None,
):
    """Build one synthetic Responses envelope as fixture data only.

    ``status``, ``error`` and ``incomplete_details`` omit their key
    when passed ``_OMIT``. ``output=None`` builds the default
    completed message; ``output=_OMIT`` omits the key. ``usage`` is
    included only when supplied.
    """
    envelope = {"id": response_id, "object": "response"}
    envelope["model"] = meta_protocol.MODEL if model is None else model
    if status is not _OMIT:
        envelope["status"] = status
    if error is not _OMIT:
        envelope["error"] = error
    if incomplete_details is not _OMIT:
        envelope["incomplete_details"] = incomplete_details
    if output is None:
        envelope["output"] = [_message_item("completed")]
    elif output is not _OMIT:
        envelope["output"] = output
    if usage is not _OMIT:
        envelope["usage"] = usage
    if extra:
        envelope.update(extra)
    return envelope


def _expected_usage(
    input_tokens=None,
    output_tokens=None,
    total_tokens=None,
    reasoning_tokens=None,
):
    """Assemble one expected usage projection (data only)."""
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "reasoning_tokens": reasoning_tokens,
    }


def _expected_diagnostic(
    classifier,
    response_status,
    message_status,
    response_id,
    reason,
    usage,
):
    """Assemble one expected closed diagnostic (data only)."""
    return {
        "version": 1,
        "classifier": classifier,
        "response_status": response_status,
        "message_status": message_status,
        "response_id": response_id,
        "reason": reason,
        "usage": usage,
    }


def _question_request():
    """One genuine question request under the staged v2 protocol."""
    regions = {
        "synthetic-region-1": _ARABIC_SENTENCE,
        "synthetic-region-2": _OBJECTIVE,
    }
    slots = [
        {"slot_id": "synthetic-slot-1", "level": "remember"},
        {"slot_id": "synthetic-slot-2", "level": "analyze"},
    ]
    prompt = question_prompt_v2(
        regions, slots, grade=_GRADE, objective=_OBJECTIVE
    )
    return {
        "kind": "question_generation",
        "request_id": "synthetic-diagnostics-request-001",
        "source_id": "synthetic-diagnostics-source-001",
        "prompt": prompt,
        "reviewed_regions": regions,
        "requested_slots": slots,
        "grade": _GRADE,
        "objective": _OBJECTIVE,
    }


def _genuine_payload(max_output_tokens=512):
    return meta_protocol.build_payload(
        _question_request(), max_output_tokens=max_output_tokens
    )


def _genuine_refusal(envelope):
    """Return the genuine parser refusal for one envelope fixture."""
    try:
        meta_protocol.parse_response(_json_bytes(envelope))
    except meta_protocol.MetaProtocolError as caught:
        return caught
    raise AssertionError("fixture envelope was not refused")


def _sse_terminal(event_type, envelope, ensure_ascii=False):
    event = {"type": event_type, "response": envelope}
    data = json.dumps(
        event, ensure_ascii=ensure_ascii, separators=(",", ":")
    )
    return b"data: " + data.encode("utf-8") + b"\n\n"


def _sse_done():
    return b"data: [DONE]\n\n"


def _pieces(data, size):
    return [
        data[index:index + size]
        for index in range(0, len(data), size)
    ]


def _rendered(error):
    return "".join(traceback.format_exception(error))


class _Swap:
    """Replace one attribute and always restore the original."""

    def __init__(self, owner, name, replacement):
        self._owner = owner
        self._name = name
        self._replacement = replacement
        self._original = None

    def __enter__(self):
        self._original = getattr(self._owner, self._name)
        setattr(self._owner, self._name, self._replacement)
        return self._replacement

    def __exit__(self, exc_type, exc, tb):
        setattr(self._owner, self._name, self._original)
        return False


class _RecordingSink:
    """Binary stdout sink recording each separate write."""

    def __init__(self):
        self.writes = []
        self.flushes = 0

    def write(self, data):
        if isinstance(data, (bytearray, memoryview)):
            data = bytes(data)
        self.writes.append(data)
        return len(data)

    def flush(self):
        self.flushes += 1

    def getvalue(self):
        return b"".join(self.writes)


class _FailingSink:
    """Binary stdout sink whose write always fails."""

    def __init__(self):
        self.attempts = 0

    def write(self, data):
        self.attempts += 1
        raise OSError("synthetic stdout write failure")

    def flush(self):
        return None


class _FakeStdio:
    """Minimal stdio stand-in exposing one binary buffer."""

    def __init__(self, buffer):
        self.buffer = buffer
        self.text_writes = []

    def write(self, text):
        self.text_writes.append(text)
        return len(text) if isinstance(text, str) else 0

    def flush(self):
        return None


class _FakeReader:
    """Inert async reader: queued byte chunks, then steady EOF."""

    def __init__(self, chunks):
        self._chunks = [bytes(chunk) for chunk in chunks]

    async def read(self, n=-1):
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0)
        if n is not None and 0 <= n < len(chunk):
            self._chunks.insert(0, chunk[n:])
            return chunk[:n]
        return chunk


class _FakeWriter:
    """Inert async stdin writer recording its received bytes."""

    def __init__(self, fail_write=False):
        self.data = bytearray()
        self.closed = False
        self.writes = 0
        self._fail_write = fail_write

    def write(self, data):
        self.writes += 1
        if self._fail_write:
            raise OSError("synthetic stdin write failure")
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    def is_closing(self):
        return self.closed


class _FakeProcess:
    """Fully owned inert stand-in at the process-creation seam.

    Wait modes: ``settle_zero`` and ``settle_one`` complete at once
    with that return code, ``raise`` always fails, and ``block``
    stays pending until ``terminate``/``kill`` during owned
    cleanup (optionally setting the supplied cancel event when the
    first wait begins, to model a mid-flight cancellation).
    """

    def __init__(
        self,
        stdout_chunks,
        stderr_chunks,
        wait_mode="settle_zero",
        *,
        writer=None,
        cancel_event=None,
    ):
        self.stdin = writer if writer is not None else _FakeWriter()
        self.stdout = _FakeReader(stdout_chunks)
        self.stderr = _FakeReader(stderr_chunks)
        self.returncode = None
        self.wait_calls = 0
        self.terminate_calls = 0
        self.kill_calls = 0
        self._wait_mode = wait_mode
        self._cancel_event = cancel_event
        self._exited = False
        self._exit_event = None

    async def wait(self):
        self.wait_calls += 1
        if self._wait_mode == "raise":
            raise OSError("synthetic wait failure")
        if self._wait_mode == "settle_zero":
            self.returncode = 0
            return 0
        if self._wait_mode == "settle_one":
            self.returncode = 1
            return 1
        if self._cancel_event is not None and self.wait_calls == 1:
            self._cancel_event.set()
        if self._exited:
            return self.returncode
        if self._exit_event is None:
            self._exit_event = asyncio.Event()
        await self._exit_event.wait()
        return self.returncode

    def _finish(self, returncode):
        if self.returncode is None:
            self.returncode = returncode
        self._exited = True
        if self._exit_event is not None:
            self._exit_event.set()

    def terminate(self):
        self.terminate_calls += 1
        self._finish(-15)

    def kill(self):
        self.kill_calls += 1
        self._finish(-9)


class FailureDiagnosticsTest(unittest.TestCase):
    """Five bounded diagnostic regression methods (see module doc)."""

    # -- closed-shape assertion helpers ------------------------------

    def _assert_fixed(self, exc, outcome):
        self.assertIs(type(exc), meta_protocol.MetaProtocolError)
        self.assertEqual(getattr(exc, "outcome", None), outcome)
        self.assertEqual(str(exc), _REFUSED)
        self.assertEqual(exc.args, (_REFUSED,))
        # Cause/context slots live outside vars(); check them here
        # so every fixed-error assertion sees the whole chain.
        self.assertIsNone(
            exc.__cause__, "fixed error retained an exception cause"
        )
        self.assertIsNone(
            exc.__context__,
            "fixed error retained an exception context",
        )

    def _assert_absent(self, needle, text):
        self.assertTrue(
            needle not in text, "forbidden synthetic value escaped"
        )

    def _assert_diagnostic(self, exc, expected):
        """Assert the exact closed diagnostic on an incomplete error."""
        self._assert_fixed(exc, "incomplete")
        diagnostic = getattr(exc, "diagnostic", None)
        self.assertIs(
            type(diagnostic), dict,
            "diagnosed incomplete error must expose one plain "
            "diagnostic dict and nothing else",
        )
        self.assertEqual(
            set(vars(exc)), {"outcome", "diagnostic"}
        )
        self.assertIs(type(diagnostic.get("version")), int)
        usage = diagnostic.get("usage")
        self.assertIs(type(usage), dict)
        for key, value in expected["usage"].items():
            if value is not None:
                self.assertIs(type(usage.get(key)), int)
        self.assertEqual(expected, diagnostic)
        for ensure_ascii in (False, True):
            serialized = json.dumps(
                diagnostic, ensure_ascii=ensure_ascii
            )
            self._assert_absent(_HOSTILE, serialized)
            self._assert_absent(_REASONING_SENTINEL, serialized)
        return diagnostic

    def _assert_no_diagnostic(self, exc, outcome):
        self._assert_fixed(exc, outcome)
        self.assertEqual(set(vars(exc)), {"outcome"})
        self.assertTrue(
            "diagnostic" not in vars(exc),
            "undiagnosed error gained a diagnostic field",
        )

    def _caught_parse(self, data):
        with self.assertRaises(
            meta_protocol.MetaProtocolError
        ) as caught:
            meta_protocol.parse_response(data)
        return caught.exception

    def _caught_collect(self, chunks, credential):
        with self.assertRaises(
            meta_protocol.MetaProtocolError
        ) as caught:
            meta_stream.collect_response(
                chunks, credential=credential
            )
        return caught.exception

    # -- decoder result reading (closed carriage shapes only) -------

    def _failure_carriage_parts(self, decoded):
        """Return closed failure-carriage parts, or None.

        Return ``(outcome, diagnostic, has_diagnostic)`` only for
        these finite forms: a flat outcome with an optional third
        diagnostic member, an inner outcome sequence of size one or
        two, or an inner dictionary containing only one outcome key
        (``failure`` or ``outcome``) plus an optional
        ``diagnostic`` key. Outer/inner sequences may be tuples or
        lists as the prior accessor allowed. Any other size, member
        or key is unrecognized rather than searched recursively.
        """
        if not isinstance(decoded, (tuple, list)):
            return None
        if len(decoded) not in (2, 3):
            return None
        if type(decoded[0]) is not str or decoded[0] != "failure":
            return None
        if len(decoded) == 3:
            if type(decoded[1]) is not str:
                return None
            return decoded[1], decoded[2], True
        value = decoded[1]
        if type(value) is str:
            return value, None, False
        if isinstance(value, (tuple, list)):
            if len(value) not in (1, 2):
                return None
            if type(value[0]) is not str:
                return None
            if len(value) == 1:
                return value[0], None, False
            return value[0], value[1], True
        if isinstance(value, dict):
            keys = set(value)
            if "failure" in keys and "outcome" not in keys:
                outcome_key = "failure"
            elif "outcome" in keys and "failure" not in keys:
                outcome_key = "outcome"
            else:
                return None
            if keys not in (
                {outcome_key}, {outcome_key, "diagnostic"}
            ):
                return None
            outcome = value[outcome_key]
            if type(outcome) is not str:
                return None
            if "diagnostic" in value:
                return outcome, value["diagnostic"], True
            return outcome, None, False
        return None

    def _decoded_outcome(self, decoded):
        parts = self._failure_carriage_parts(decoded)
        return parts[0] if parts is not None else None

    def _decoded_diagnostic(self, decoded):
        """Return the diagnostic in a closed carriage, or None."""
        parts = self._failure_carriage_parts(decoded)
        if parts is None or not parts[2]:
            return None
        return parts[1]

    def _assert_carriage_has_no_sensitive_markers(self, decoded):
        markers = (
            _HOSTILE,
            _REASONING_SENTINEL,
            _STREAM_CREDENTIAL,
            _CHILD_CREDENTIAL,
            _SUPERVISOR_CREDENTIAL,
            _PROVIDER_CREDENTIAL,
        )
        stack = [decoded]
        seen = set()
        while stack:
            item = stack.pop()
            if isinstance(item, (dict, list, tuple)):
                marker_id = id(item)
                if marker_id in seen:
                    continue
                seen.add(marker_id)
                if isinstance(item, dict):
                    stack.extend(item.keys())
                    stack.extend(item.values())
                else:
                    stack.extend(item)
                continue
            if isinstance(item, bytes):
                text = item.decode("utf-8", errors="ignore")
            elif isinstance(item, str):
                text = item
            else:
                text = repr(item)
            for marker in markers:
                self._assert_absent(marker, text)

    def _assert_emitted_diagnosed(self, emitted, expected):
        """Validate raw diagnosed wire bytes without product code.

        The bytes must be exactly one strict JSON object and one
        final newline. The object must be exactly
        ``{"failure": "incomplete", "diagnostic": expected}``.
        Duplicate keys and non-finite constants are rejected here,
        before any product decoder could clean the emitted bytes.
        """
        self.assertIs(type(emitted), bytes)
        self.assertTrue(
            emitted.endswith(b"\n")
            and not emitted.endswith(b"\n\n"),
            "diagnosed wire bytes lack exactly one final newline",
        )
        body = emitted[:-1]
        self.assertTrue(
            body.startswith(b"{") and body.endswith(b"}"),
            "diagnosed wire bytes are not exactly one JSON object",
        )

        def strict_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON object key")
                result[key] = value
            return result

        def reject_constant(name):
            raise ValueError("non-finite JSON constant")

        def strict_float(text):
            value = float(text)
            if value != value or value in (
                float("inf"), float("-inf")
            ):
                raise ValueError("non-finite JSON number")
            return value

        try:
            frame = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=strict_object,
                parse_constant=reject_constant,
                parse_float=strict_float,
            )
        except Exception:
            self.fail("diagnosed wire bytes are not strict JSON")
        self.assertIs(type(frame), dict)
        self.assertEqual(set(frame), {"failure", "diagnostic"})
        self.assertIs(type(frame["failure"]), str)
        self.assertEqual(frame["failure"], "incomplete")
        self._assert_decoded_diagnosed(
            ("failure", "incomplete", frame["diagnostic"]), expected
        )
        self.assertEqual(
            frame,
            {"failure": "incomplete", "diagnostic": expected},
        )
        return frame

    def _assert_decoded_diagnosed(self, decoded, expected):
        self._assert_carriage_has_no_sensitive_markers(decoded)
        parts = self._failure_carriage_parts(decoded)
        self.assertIsNotNone(
            parts,
            "diagnosed frame was not one closed failure carriage",
        )
        outcome, diagnostic, has_diagnostic = parts
        self.assertEqual(outcome, "incomplete")
        self.assertTrue(
            has_diagnostic,
            "decoded diagnosed frame carries no diagnostic",
        )
        self.assertIs(
            type(diagnostic), dict,
            "decoded diagnosed frame carries no diagnostic dict",
        )
        self.assertEqual(
            set(diagnostic),
            {
                "version", "classifier", "response_status",
                "message_status", "response_id", "reason", "usage",
            },
        )
        self.assertIs(type(diagnostic.get("version")), int)
        usage = diagnostic.get("usage")
        self.assertIs(type(usage), dict)
        self.assertEqual(
            set(usage),
            {
                "input_tokens", "output_tokens", "total_tokens",
                "reasoning_tokens",
            },
        )
        for value in usage.values():
            if value is not None:
                self.assertIs(type(value), int)
        self.assertEqual(expected, diagnostic)

    def _assert_decoded_plain_incomplete(self, decoded):
        self._assert_carriage_has_no_sensitive_markers(decoded)
        self.assertIs(
            type(decoded), tuple,
            "legacy frame was not the exact original tuple",
        )
        self.assertEqual(
            len(decoded), 2,
            "legacy frame gained hidden carriage members",
        )
        self.assertIs(type(decoded[0]), str)
        self.assertIs(type(decoded[1]), str)
        self.assertEqual(decoded, ("failure", "incomplete"))

    def _assert_decoder_oracle_self_checks(self):
        """Check the decoder oracles on inert carriages only."""
        expected = _expected_diagnostic(
            "response_status", "incomplete", None,
            "resp_oracle_001", "absent",
            _expected_usage(10, 20, 30, 5),
        )
        valid_carriages = [
            ("failure", "incomplete", copy.deepcopy(expected)),
            (
                "failure",
                ("incomplete", copy.deepcopy(expected)),
            ),
            (
                "failure",
                {
                    "failure": "incomplete",
                    "diagnostic": copy.deepcopy(expected),
                },
            ),
            (
                "failure",
                {
                    "outcome": "incomplete",
                    "diagnostic": copy.deepcopy(expected),
                },
            ),
        ]
        for carriage in valid_carriages:
            self._assert_decoded_diagnosed(carriage, expected)
        self._assert_decoded_plain_incomplete(
            ("failure", "incomplete")
        )

        with self.assertRaises(AssertionError):
            self._assert_decoded_diagnosed(
                (
                    "failure", "incomplete",
                    copy.deepcopy(expected), {"debug": _HOSTILE},
                ),
                expected,
            )
        with self.assertRaises(AssertionError):
            self._assert_decoded_plain_incomplete(
                ("failure", "incomplete", {"debug": _HOSTILE})
            )
        float_diagnostic = copy.deepcopy(expected)
        float_diagnostic["usage"]["input_tokens"] = 10.0
        with self.assertRaises(AssertionError):
            self._assert_decoded_diagnosed(
                ("failure", "incomplete", float_diagnostic),
                expected,
            )

        valid_frame = {
            "failure": "incomplete",
            "diagnostic": copy.deepcopy(expected),
        }
        valid_bytes = _compact_frame_bytes(valid_frame)
        self.assertEqual(
            self._assert_emitted_diagnosed(valid_bytes, expected),
            valid_frame,
        )
        prompt_frame = copy.deepcopy(valid_frame)
        prompt_frame["diagnostic"]["prompt"] = _ARABIC_SENTENCE
        bool_version_frame = copy.deepcopy(valid_frame)
        bool_version_frame["diagnostic"]["version"] = True
        duplicate_text = valid_bytes.decode("utf-8").replace(
            '{"failure":"incomplete",',
            '{"failure":"error","failure":"incomplete",',
            1,
        )
        duplicate_bytes = duplicate_text.encode("utf-8")
        self.assertNotEqual(duplicate_bytes, valid_bytes)
        self.assertEqual(json.loads(duplicate_text), valid_frame)
        raw_cases = [
            (
                "extra-prompt-field",
                _compact_frame_bytes(prompt_frame),
            ),
            (
                "float-counter",
                _compact_frame_bytes({
                    "failure": "incomplete",
                    "diagnostic": float_diagnostic,
                }),
            ),
            (
                "bool-version",
                _compact_frame_bytes(bool_version_frame),
            ),
            ("duplicate-key", duplicate_bytes),
        ]
        for label, raw_bytes in raw_cases:
            with self.subTest(case="raw-wire-" + label):
                # A clean separate carriage cannot redeem bad raw
                # bytes that have already escaped the child.
                self._assert_decoded_diagnosed(
                    (
                        "failure", "incomplete",
                        copy.deepcopy(expected),
                    ),
                    expected,
                )
                with self.assertRaises(AssertionError):
                    self._assert_emitted_diagnosed(
                        raw_bytes, expected
                    )

    # -- declared external-seam runners -------------------------------

    def _run_child(self, input_bytes, stub, sink=None):
        """Run the actual child in-process over binary fake stdio.

        Only ``meta_http.send_payload`` is replaced. Returns
        ``(sink, calls, stderr)`` with every seam restored.
        """
        if sink is None:
            sink = _RecordingSink()
        calls = []

        def send(payload, *, credential, timeout_seconds):
            calls.append({
                "payload": payload,
                "credential": credential,
                "timeout_seconds": timeout_seconds,
            })
            return stub(
                payload,
                credential=credential,
                timeout_seconds=timeout_seconds,
            )

        stderr = _FakeStdio(_RecordingSink())
        saved = (sys.stdin, sys.stdout, sys.stderr)
        with _Swap(meta_http, "send_payload", send):
            sys.stdin = _FakeStdio(io.BytesIO(input_bytes))
            sys.stdout = _FakeStdio(sink)
            sys.stderr = stderr
            try:
                meta_transport._child_main()
            finally:
                sys.stdin, sys.stdout, sys.stderr = saved
        return sink, calls, stderr

    def _assert_child_silent(self, stderr):
        self.assertEqual(stderr.buffer.getvalue(), b"")
        self.assertEqual(stderr.text_writes, [])

    def _emitted_frame(self, sink):
        """Read back the one emitted child frame as fixture data."""
        self.assertEqual(len(sink.writes), 1)
        emitted = sink.getvalue()
        self.assertTrue(emitted.endswith(b"\n"))
        self.assertFalse(emitted.endswith(b"\n\n"))
        return emitted, json.loads(emitted[:-1].decode("utf-8"))

    def _supervise(
        self, process, payload, *, total=5.0, cancel_event=None,
        credential=_SUPERVISOR_CREDENTIAL,
    ):
        """Run the actual supervisor over one fake created process."""
        record = []

        async def create():
            record.append(process)
            return process

        with _Swap(
            meta_transport, "_create_child_process", create
        ):
            try:
                result = meta_transport.run_payload(
                    payload,
                    credential=credential,
                    timeout_seconds=2.5,
                    total_timeout_seconds=total,
                    cancel_event=cancel_event,
                )
            except meta_protocol.MetaProtocolError as caught:
                return "error", caught, record
        return "result", result, record

    def _call_provider(self, provider, request, context, transport):
        with _Swap(meta_transport, "run_payload", transport):
            try:
                result = provider(request, context=context)
            except meta_protocol.MetaProtocolError as caught:
                return "error", caught
        return "result", result

    # -- 1: actual parser ---------------------------------------------

    def test_parser_projects_three_incomplete_branches(self):
        null_usage = _expected_usage()
        full_provider_usage = {
            "input_tokens": 111,
            "output_tokens": 222,
            "total_tokens": 333,
            "output_tokens_details": {"reasoning_tokens": 44},
            "input_tokens_details": {"cached_tokens": 5},
        }
        full_usage = _expected_usage(111, 222, 333, 44)
        default_id = "resp_diag_001"

        def diag(classifier, response_status, message_status,
                 response_id, reason, usage):
            return _expected_diagnostic(
                classifier, response_status, message_status,
                response_id, reason, usage,
            )

        diagnosed = [
            (
                "p1-max-tokens-full-usage",
                _envelope(
                    incomplete_details={
                        "reason": "max_output_tokens"
                    },
                    usage=full_provider_usage,
                ),
                diag(
                    "incomplete_details", "completed", None,
                    default_id, "max_output_tokens", full_usage,
                ),
                (),
            ),
            (
                "p1-beats-failed-status",
                _envelope(
                    status="failed",
                    incomplete_details={
                        "reason": "content_filter"
                    },
                    output=[],
                ),
                diag(
                    "incomplete_details", "failed", None,
                    default_id, "content_filter", null_usage,
                ),
                (),
            ),
            (
                "p1-empty-details-absent-status",
                _envelope(status=_OMIT, incomplete_details={}),
                diag(
                    "incomplete_details", "absent", None,
                    default_id, "absent", null_usage,
                ),
                (),
            ),
            (
                "p1-before-incomplete-status",
                _envelope(
                    status="incomplete",
                    incomplete_details={"reason": None},
                    output=[],
                ),
                diag(
                    "incomplete_details", "incomplete", None,
                    default_id, "absent", null_usage,
                ),
                (),
            ),
            (
                "p1-reason-missing-hostile-extra",
                _envelope(
                    status="queued",
                    incomplete_details={"note": _HOSTILE},
                    output=[],
                ),
                diag(
                    "incomplete_details", "queued", None,
                    default_id, "absent", null_usage,
                ),
                (_HOSTILE,),
            ),
            (
                "p1-reason-other-string",
                _envelope(
                    status="cancelled",
                    incomplete_details={"reason": _VENDOR_REASON},
                    output=[],
                ),
                diag(
                    "incomplete_details", "cancelled", None,
                    default_id, "other", null_usage,
                ),
                (_VENDOR_REASON,),
            ),
            (
                "p1-reason-non-string",
                _envelope(
                    status="in_progress",
                    incomplete_details={"reason": 7},
                    output=[],
                ),
                diag(
                    "incomplete_details", "in_progress", None,
                    default_id, "malformed", null_usage,
                ),
                (),
            ),
            (
                "p1-details-bare-string",
                _envelope(
                    status="some_future_status",
                    incomplete_details="max_output_tokens",
                    output=[],
                ),
                diag(
                    "incomplete_details", "other", None,
                    default_id, "malformed", null_usage,
                ),
                (),
            ),
            (
                "p1-details-false-null-status",
                _envelope(
                    status=None,
                    incomplete_details=False,
                    output=[],
                ),
                diag(
                    "incomplete_details", "other", None,
                    default_id, "malformed", null_usage,
                ),
                (),
            ),
            (
                "p1-details-zero-numeric-status",
                _envelope(
                    status=7, incomplete_details=0, output=[],
                ),
                diag(
                    "incomplete_details", "other", None,
                    default_id, "malformed", null_usage,
                ),
                (),
            ),
            (
                "p1-details-list-bool-status",
                _envelope(
                    status=True, incomplete_details=[], output=[],
                ),
                diag(
                    "incomplete_details", "other", None,
                    default_id, "malformed", null_usage,
                ),
                (),
            ),
            (
                "p2-status-only-no-output",
                _envelope(
                    status="incomplete",
                    incomplete_details=_OMIT,
                    output=_OMIT,
                    usage={
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "total_tokens": 30,
                        "output_tokens_details": {
                            "reasoning_tokens": 5
                        },
                    },
                ),
                diag(
                    "response_status", "incomplete", None,
                    default_id, "absent",
                    _expected_usage(10, 20, 30, 5),
                ),
                (),
            ),
        ]
        for label, envelope, expected, forbidden in diagnosed:
            with self.subTest(case=label):
                exc = self._caught_parse(_json_bytes(envelope))
                diagnostic = self._assert_diagnostic(exc, expected)
                serialized = json.dumps(
                    diagnostic, ensure_ascii=False
                )
                for value in forbidden:
                    self._assert_absent(value, serialized)
                    self._assert_absent(value, str(exc))

        usage_cases = [
            ("usage-absent", _OMIT, null_usage),
            ("usage-null", None, null_usage),
            ("usage-hostile-string", _HOSTILE, null_usage),
            ("usage-list", [], null_usage),
            (
                "usage-independent-fields",
                {
                    "input_tokens": 0,
                    "output_tokens": True,
                    "total_tokens": (1 << 53),
                    "reasoning_tokens": 999,
                    "output_tokens_details": {
                        "reasoning_tokens": _MAX_SAFE
                    },
                },
                _expected_usage(0, None, None, _MAX_SAFE),
            ),
            (
                "usage-float-negative-string",
                {
                    "input_tokens": 3.0,
                    "output_tokens": -1,
                    "total_tokens": "9",
                    "output_tokens_details": {
                        "reasoning_tokens": 12
                    },
                },
                _expected_usage(None, None, None, 12),
            ),
            (
                "usage-details-not-dict",
                {
                    "input_tokens": _MAX_SAFE,
                    "output_tokens_details": "nope",
                },
                _expected_usage(_MAX_SAFE, None, None, None),
            ),
            (
                "usage-reasoning-bool",
                {
                    "output_tokens": 5,
                    "output_tokens_details": {
                        "reasoning_tokens": False
                    },
                },
                _expected_usage(None, 5, None, None),
            ),
        ]
        for label, provider_usage, expected_usage in usage_cases:
            with self.subTest(case=label):
                envelope = _envelope(
                    incomplete_details={
                        "reason": "max_output_tokens"
                    },
                    usage=provider_usage,
                    output=[],
                )
                exc = self._caught_parse(_json_bytes(envelope))
                self._assert_diagnostic(
                    exc,
                    diag(
                        "incomplete_details", "completed", None,
                        default_id, "max_output_tokens",
                        expected_usage,
                    ),
                )

        id_cases = [
            ("id-short", "resp_a", "resp_a"),
            ("id-mixed", "resp_Ab-9_z", "resp_Ab-9_z"),
            ("id-max-128", "resp_" + "a" * 128, "resp_" + "a" * 128),
            ("id-over-129", "resp_" + "a" * 129, None),
            ("id-prefix-only", "resp_", None),
            ("id-no-prefix", "synthetic-response-1", None),
            ("id-upper-prefix", "RESP_abc", None),
            ("id-space", "resp_ab c", None),
            ("id-non-ascii", "resp_é", None),
            ("id-newline", "resp_abc\n", None),
            ("id-padded", " resp_abc ", None),
        ]
        for label, candidate, expected_id in id_cases:
            with self.subTest(case=label):
                envelope = _envelope(
                    response_id=candidate,
                    status="incomplete",
                    output=[],
                )
                exc = self._caught_parse(_json_bytes(envelope))
                self._assert_diagnostic(
                    exc,
                    diag(
                        "response_status", "incomplete", None,
                        expected_id, "absent", null_usage,
                    ),
                )

        p3_cases = [
            (
                "p3-in-progress",
                [_message_item("in_progress")],
                "in_progress",
                {"input_tokens": 7},
                _expected_usage(7, None, None, None),
            ),
            (
                "p3-explicit-null-status",
                [_message_item(None)],
                "other",
                _OMIT,
                null_usage,
            ),
            (
                "p3-incomplete-status",
                [_message_item("incomplete")],
                "incomplete",
                _OMIT,
                null_usage,
            ),
            (
                "p3-numeric-status",
                [_message_item(7)],
                "other",
                _OMIT,
                null_usage,
            ),
            (
                "p3-queued-status",
                [_message_item("queued")],
                "queued",
                _OMIT,
                null_usage,
            ),
            (
                "p3-second-message",
                [
                    _message_item("completed"),
                    _message_item("in_progress"),
                ],
                "in_progress",
                _OMIT,
                null_usage,
            ),
            (
                "p3-after-reasoning-hostile-usage",
                [_reasoning_item(), _message_item("failed")],
                "failed",
                _HOSTILE,
                null_usage,
            ),
        ]
        for (
            label, output, message_status, provider_usage,
            expected_usage,
        ) in p3_cases:
            with self.subTest(case=label):
                envelope = _envelope(
                    output=output, usage=provider_usage
                )
                exc = self._caught_parse(_json_bytes(envelope))
                self._assert_diagnostic(
                    exc,
                    diag(
                        "message_status", "completed",
                        message_status, default_id, "absent",
                        expected_usage,
                    ),
                )

        refusal_message = _message_item(
            "completed",
            parts=[{"type": "refusal", "refusal": _HOSTILE}],
        )
        controls = [
            (
                "error-beats-incomplete-details",
                _envelope(
                    status="incomplete",
                    error={"message": _HOSTILE},
                    incomplete_details={
                        "reason": "max_output_tokens"
                    },
                    output=[],
                ),
                "error",
                (_HOSTILE,),
            ),
            (
                "failed-status-with-usage",
                _envelope(
                    status="failed",
                    usage=full_provider_usage,
                    output=[],
                ),
                "error",
                (),
            ),
            (
                "cancelled-status",
                _envelope(status="cancelled", output=[]),
                "cancelled",
                (),
            ),
            (
                "queued-status",
                _envelope(status="queued", output=[]),
                "unknown",
                (),
            ),
            (
                "missing-status-no-details",
                _envelope(status=_OMIT, output=[]),
                "unknown",
                (),
            ),
            (
                "wrong-model-before-classification",
                _envelope(
                    model="synthetic-wrong-model",
                    incomplete_details={
                        "reason": "max_output_tokens"
                    },
                    output=[],
                ),
                "invalid_schema",
                (),
            ),
            (
                "blank-id-before-classification",
                _envelope(
                    response_id="   ", status="incomplete",
                    output=[],
                ),
                "invalid_schema",
                (),
            ),
            (
                "refusal-unchanged",
                _envelope(output=[refusal_message]),
                "refusal",
                (_HOSTILE,),
            ),
            (
                "refusal-before-unfinished-message",
                _envelope(
                    output=[
                        refusal_message,
                        _message_item("in_progress"),
                    ]
                ),
                "refusal",
                (_HOSTILE,),
            ),
            (
                "empty-output",
                _envelope(output=[]),
                "empty",
                (),
            ),
        ]
        for label, envelope, outcome, forbidden in controls:
            with self.subTest(case=label):
                exc = self._caught_parse(_json_bytes(envelope))
                self._assert_no_diagnostic(exc, outcome)
                for value in forbidden:
                    self._assert_absent(value, str(exc))

        with self.subTest(case="healthy-success-exact"):
            envelope = _envelope(
                usage=full_provider_usage,
                extra={"provider_extra": _HOSTILE},
            )
            data = _json_bytes(envelope)
            result = meta_protocol.parse_response(data)
            self.assertEqual(
                result,
                {
                    "raw": _healthy_raw(),
                    "model_name": meta_protocol.MODEL,
                    "response_id": default_id,
                    "outcome": "ok",
                },
            )
            marked = result["raw"]["marked"]
            self.assertEqual(
                [ord(char) for char in marked],
                [ord(char) for char in _MARKED_WORD],
            )
            self.assertEqual(
                result["raw"]["sentence"].encode("utf-8"),
                _ARABIC_SENTENCE.encode("utf-8"),
            )
            result["raw"]["marked"] = "MUTATED"
            fresh = meta_protocol.parse_response(data)
            self.assertEqual(fresh["raw"], _healthy_raw())

        with self.subTest(case="success-absent-role-and-status"):
            envelope = _envelope(
                output=[{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": _inner_raw_text(),
                    }],
                }]
            )
            result = meta_protocol.parse_response(
                _json_bytes(envelope)
            )
            self.assertEqual(result["outcome"], "ok")
            self.assertEqual(result["raw"], _healthy_raw())

        with self.subTest(case="success-unrepresentable-id-kept"):
            unicode_id = "resp_ok_é" + _SUPPLEMENTARY
            envelope = _envelope(response_id=unicode_id)
            result = meta_protocol.parse_response(
                _json_bytes(envelope)
            )
            self.assertEqual(result["response_id"], unicode_id)
            self.assertEqual(
                set(result.keys()),
                {"raw", "model_name", "response_id", "outcome"},
            )

        with self.subTest(case="diagnostic-newly-owned-per-parse"):
            envelope = _envelope(
                incomplete_details={"reason": "max_output_tokens"},
                usage=full_provider_usage,
            )
            data = _json_bytes(envelope)
            expected = diag(
                "incomplete_details", "completed", None,
                default_id, "max_output_tokens", full_usage,
            )
            first = self._assert_diagnostic(
                self._caught_parse(data), expected
            )
            second = self._assert_diagnostic(
                self._caught_parse(data), expected
            )
            self.assertIsNot(first, second)
            self.assertIsNot(first["usage"], second["usage"])
            first["classifier"] = "mutated"
            first["usage"]["input_tokens"] = -1
            self.assertEqual(second, expected)

        with self.subTest(case="manual-error-has-no-diagnostic"):
            manual = meta_protocol.MetaProtocolError("incomplete")
            self._assert_no_diagnostic(manual, "incomplete")

    # -- 2: actual stream collector ------------------------------------

    def test_stream_carries_projection_and_drops_credential_collisions(
        self,
    ):
        credential = _STREAM_CREDENTIAL
        null_usage = _expected_usage()

        with self.subTest(case="stream-p2-projection-chunked"):
            envelope = _envelope(
                response_id="resp_stream_001",
                status="incomplete",
                output=[],
                usage={
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                    "output_tokens_details": {"reasoning_tokens": 5},
                },
            )
            stream = (
                _sse_terminal("response.incomplete", envelope)
                + _sse_done()
            )
            exc = self._caught_collect(
                _pieces(stream, 7), credential
            )
            self._assert_diagnostic(
                exc,
                _expected_diagnostic(
                    "response_status", "incomplete", None,
                    "resp_stream_001", "absent",
                    _expected_usage(10, 20, 30, 5),
                ),
            )

        with self.subTest(case="stream-p1-projection"):
            envelope = _envelope(
                response_id="resp_stream_002",
                incomplete_details={"reason": "content_filter"},
            )
            stream = _sse_terminal("response.completed", envelope)
            exc = self._caught_collect([stream], credential)
            self._assert_diagnostic(
                exc,
                _expected_diagnostic(
                    "incomplete_details", "completed", None,
                    "resp_stream_002", "content_filter", null_usage,
                ),
            )

        with self.subTest(case="stream-p3-projection-secret-fields"):
            envelope = _envelope(
                response_id="resp_stream_003",
                output=[
                    _reasoning_item(),
                    _message_item("in_progress"),
                ],
                usage={
                    "input_tokens": 3,
                    "output_tokens_details": {"reasoning_tokens": 2},
                },
                extra={"provider_debug": _HOSTILE},
            )
            stream = _sse_terminal("response.completed", envelope)
            exc = self._caught_collect([stream], credential)
            self._assert_diagnostic(
                exc,
                _expected_diagnostic(
                    "message_status", "completed", "in_progress",
                    "resp_stream_003", "absent",
                    _expected_usage(3, None, None, 2),
                ),
            )

        with self.subTest(case="credential-in-response-id-plain"):
            envelope = _envelope(
                response_id="resp_" + credential,
                status="incomplete",
                output=[],
            )
            stream = _sse_terminal("response.incomplete", envelope)
            exc = self._caught_collect([stream], credential)
            self._assert_no_diagnostic(exc, "error")
            self._assert_absent(credential, str(exc))

        with self.subTest(case="credential-in-response-id-escaped"):
            envelope = _envelope(
                response_id="resp_" + credential,
                status="incomplete",
                output=[],
            )
            event = {"type": "response.incomplete",
                     "response": envelope}
            data_text = json.dumps(
                event, ensure_ascii=False, separators=(",", ":")
            )
            escaped = data_text.replace(
                credential, "\\u0053" + credential[1:], 1
            )
            self.assertTrue(
                credential not in escaped,
                "escaped fixture still contains the credential",
            )
            decoded = json.loads(escaped)
            self.assertTrue(
                credential in decoded["response"]["id"],
                "escaped fixture does not decode to the credential",
            )
            stream = (
                b"data: " + escaped.encode("utf-8") + b"\n\n"
            )
            exc = self._caught_collect([stream], credential)
            self._assert_no_diagnostic(exc, "error")

        for key_credential in ("response_status", "classifier"):
            with self.subTest(
                case="diagnostic-key-credential-drops-diagnostic",
                key=key_credential,
            ):
                envelope = _envelope(
                    response_id="resp_stream_004",
                    status="incomplete",
                    output=[],
                )
                stream = _sse_terminal(
                    "response.incomplete", envelope
                )
                self.assertTrue(
                    key_credential
                    not in stream.decode("utf-8"),
                    "key-collision fixture already carries the "
                    "synthetic credential in its stream text",
                )
                exc = self._caught_collect(
                    [stream], key_credential
                )
                self._assert_no_diagnostic(exc, "incomplete")

        with self.subTest(
            case="fixed-error-literal-credential-preserved"
        ):
            # The public fixed error text is not copied credential
            # data. This pure stream seam accepts the nonblank
            # UTF-8 value even though the later HTTP seam is stricter.
            envelope = _envelope(
                response_id="resp_stream_fixed_literal",
                status="incomplete",
                output=[],
                usage={
                    "input_tokens": 12,
                    "output_tokens": 34,
                    "total_tokens": 46,
                    "output_tokens_details": {"reasoning_tokens": 7},
                },
            )
            stream = _sse_terminal("response.incomplete", envelope)
            self.assertTrue(
                _REFUSED not in stream.decode("utf-8"),
                "fixed-literal fixture event copies the credential",
            )
            exc = self._caught_collect([stream], _REFUSED)
            diagnostic = self._assert_diagnostic(
                exc,
                _expected_diagnostic(
                    "response_status", "incomplete", None,
                    "resp_stream_fixed_literal", "absent",
                    _expected_usage(12, 34, 46, 7),
                ),
            )
            self.assertEqual(str(exc), _REFUSED)
            self.assertEqual(exc.args, (_REFUSED,))
            self._assert_absent(
                _REFUSED,
                json.dumps(diagnostic, ensure_ascii=False),
            )

        terminal_frame = _sse_terminal(
            "response.incomplete",
            _envelope(
                response_id="resp_stream_005",
                status="incomplete",
                output=[],
            ),
        )

        def forged_error():
            error = meta_protocol.MetaProtocolError("incomplete")
            forged = _expected_diagnostic(
                "response_status", "incomplete", None,
                "resp_forged_001", "absent", null_usage,
            )
            forged["debug"] = _HOSTILE
            error.diagnostic = forged
            return error

        iterator_cases = [
            ("forged-protocol-error", forged_error(), (_HOSTILE,)),
            (
                "genuine-diagnosed-raise",
                _genuine_refusal(
                    _envelope(
                        status="incomplete", output=[],
                    )
                ),
                (),
            ),
            ("oserror-with-credential", OSError(credential), ()),
        ]
        for label, failure, forbidden in iterator_cases:
            with self.subTest(case="iterator-" + label):
                state = {"yielded": 0, "raised": False}

                def chunks(failure=failure, state=state):
                    state["yielded"] += 1
                    yield terminal_frame
                    state["raised"] = True
                    raise failure

                exc = self._caught_collect(chunks(), credential)
                self.assertEqual(
                    state, {"yielded": 1, "raised": True}
                )
                rendered = _rendered(exc)
                self._assert_absent(credential, rendered)
                for value in forbidden:
                    self._assert_absent(value, rendered)
                self._assert_no_diagnostic(exc, "unknown")
                self._assert_absent(credential, str(exc))
                for value in forbidden:
                    self._assert_absent(value, str(exc))

        with self.subTest(case="healthy-stream-success-exact"):
            envelope = _envelope(
                response_id="resp_stream_ok",
                usage={
                    "input_tokens": 1,
                    "output_tokens": 2,
                    "total_tokens": 3,
                    "output_tokens_details": {"reasoning_tokens": 1},
                },
            )
            stream = (
                _sse_terminal("response.completed", envelope)
                + _sse_done()
            )
            result = meta_stream.collect_response(
                _pieces(stream, 11), credential=credential
            )
            self.assertEqual(
                result,
                {
                    "raw": _healthy_raw(),
                    "model_name": meta_protocol.MODEL,
                    "response_id": "resp_stream_ok",
                    "outcome": "ok",
                },
            )
            for ensure_ascii in (False, True):
                serialized = json.dumps(
                    result, ensure_ascii=ensure_ascii
                )
                self._assert_absent(credential, serialized)

        with self.subTest(case="success-ordinary-secret-unchanged"):
            ordinary = json.dumps(
                {"note": credential}, ensure_ascii=False
            )
            envelope = _envelope(
                response_id="resp_stream_secret",
                output=[_message_item(
                    "completed",
                    parts=[{
                        "type": "output_text", "text": ordinary,
                    }],
                )],
            )
            stream = _sse_terminal("response.completed", envelope)
            exc = self._caught_collect([stream], credential)
            self._assert_no_diagnostic(exc, "error")

        with self.subTest(case="stream-refusal-unchanged"):
            envelope = _envelope(
                response_id="resp_stream_refusal",
                output=[_message_item(
                    "completed",
                    parts=[{
                        "type": "refusal", "refusal": _HOSTILE,
                    }],
                )],
            )
            stream = _sse_terminal("response.completed", envelope)
            exc = self._caught_collect([stream], credential)
            self._assert_no_diagnostic(exc, "refusal")
            self._assert_absent(_HOSTILE, str(exc))

        with self.subTest(case="stream-failed-unchanged"):
            envelope = _envelope(
                response_id="resp_stream_failed",
                status="failed",
                output=[],
            )
            stream = _sse_terminal("response.failed", envelope)
            exc = self._caught_collect([stream], credential)
            self._assert_no_diagnostic(exc, "error")

        with self.subTest(case="stream-missing-terminal"):
            exc = self._caught_collect([_sse_done()], credential)
            self._assert_no_diagnostic(exc, "unknown")

        with self.subTest(case="stream-terminal-mismatch"):
            envelope = _envelope(
                response_id="resp_stream_mismatch",
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
                output=[],
            )
            stream = _sse_terminal("response.completed", envelope)
            exc = self._caught_collect([stream], credential)
            self._assert_no_diagnostic(exc, "invalid_schema")

    # -- 3: actual child frame ---------------------------------------

    def test_child_frame_transports_only_safe_optional_diagnostic(self):
        credential = _CHILD_CREDENTIAL
        envelope = _envelope(
            response_id="resp_child_001",
            status="incomplete",
            output=[],
            usage={
                "input_tokens": 10,
                "output_tokens": 20,
                "total_tokens": 30,
                "output_tokens_details": {"reasoning_tokens": 5},
            },
        )
        expected = _expected_diagnostic(
            "response_status", "incomplete", None,
            "resp_child_001", "absent",
            _expected_usage(10, 20, 30, 5),
        )
        with self.subTest(case="decoder-oracle-self-checks"):
            self._assert_decoder_oracle_self_checks()
        # Prove the genuine parser projection before constructing
        # any child input bytes for this method.
        genuine = _genuine_refusal(envelope)
        genuine_diagnostic = self._assert_diagnostic(
            genuine, expected
        )

        payload = _genuine_payload()
        wire_payload = json.loads(
            _compact_bytes(payload).decode("utf-8")
        )
        legacy_frame = b'{"failure":"incomplete"}\n'
        self.assertEqual(
            meta_transport._failure_frame_bytes("incomplete"),
            legacy_frame,
        )

        def child_input(child_credential=credential):
            return _compact_bytes({
                "payload": payload,
                "credential": child_credential,
                "timeout_seconds": 2.5,
            })

        def raise_error(error):
            def stub(sent_payload, *, credential, timeout_seconds):
                raise error

            return stub

        def assert_one_call(calls, child_credential=credential):
            self.assertEqual(calls, [{
                "payload": wire_payload,
                "credential": child_credential,
                "timeout_seconds": 2.5,
            }])

        safe_emitted = None
        safe_frame = None
        with self.subTest(case="child-safe-diagnosed-frame"):
            sink, calls, stderr = self._run_child(
                child_input(), raise_error(genuine)
            )
            self._assert_child_silent(stderr)
            assert_one_call(calls)
            self.assertEqual(sink.flushes, 1)
            safe_emitted, safe_frame = self._emitted_frame(sink)
            # The raw bytes have already escaped; validate them
            # before any product decoder could clean them.
            safe_frame = self._assert_emitted_diagnosed(
                safe_emitted, expected
            )
            self._assert_absent(
                credential, safe_emitted.decode("utf-8")
            )
            self._assert_absent(
                _HOSTILE, safe_emitted.decode("utf-8")
            )
            decoded = meta_transport._decode_child_frame(
                safe_emitted, credential
            )
            self._assert_decoded_diagnosed(decoded, expected)
            self.assertIsNot(
                self._decoded_diagnostic(decoded),
                genuine_diagnostic,
            )

        with self.subTest(case="child-legacy-manual-incomplete"):
            manual = meta_protocol.MetaProtocolError("incomplete")
            self._assert_no_diagnostic(manual, "incomplete")
            sink, calls, stderr = self._run_child(
                child_input(), raise_error(manual)
            )
            self._assert_child_silent(stderr)
            assert_one_call(calls)
            self.assertEqual(sink.flushes, 1)
            emitted, frame = self._emitted_frame(sink)
            self.assertEqual(emitted, legacy_frame)
            self.assertEqual(frame, {"failure": "incomplete"})
            self._assert_decoded_plain_incomplete(
                meta_transport._decode_child_frame(
                    emitted, credential
                )
            )

        with self.subTest(case="child-success-frame-unchanged"):
            success = {
                "raw": _healthy_raw(),
                "model_name": meta_protocol.MODEL,
                "response_id": "resp_child_ok",
                "outcome": "ok",
            }

            def return_success(
                sent_payload, *, credential, timeout_seconds
            ):
                return copy.deepcopy(success)

            sink, calls, stderr = self._run_child(
                child_input(), return_success
            )
            self._assert_child_silent(stderr)
            assert_one_call(calls)
            self.assertEqual(sink.flushes, 1)
            emitted, frame = self._emitted_frame(sink)
            self.assertEqual(frame, {"result": success})
            self.assertEqual(
                meta_transport._decode_child_frame(
                    emitted, credential
                ),
                ("result", success),
            )

        extra = copy.deepcopy(expected)
        extra["debug"] = _HOSTILE
        bad_version = copy.deepcopy(expected)
        bad_version["version"] = True
        bad_usage = copy.deepcopy(expected)
        bad_usage["usage"] = {"input_tokens": True}
        bad_classifier = copy.deepcopy(expected)
        bad_classifier["classifier"] = _HOSTILE
        secret_id = copy.deepcopy(expected)
        secret_id["response_id"] = "resp_" + credential
        forged_cases = [
            ("extra-key", extra),
            ("bool-version", bad_version),
            ("malformed-usage", bad_usage),
            ("hostile-classifier", bad_classifier),
            ("secret-response-id", secret_id),
            ("non-dict", [_HOSTILE]),
        ]
        for label, forged in forged_cases:
            with self.subTest(case="child-forged-" + label):
                forged_error = meta_protocol.MetaProtocolError(
                    "incomplete"
                )
                forged_error.diagnostic = copy.deepcopy(forged)
                sink, calls, stderr = self._run_child(
                    child_input(), raise_error(forged_error)
                )
                self._assert_child_silent(stderr)
                assert_one_call(calls)
                emitted, frame = self._emitted_frame(sink)
                # Omission, not a changed legitimate outcome: the
                # bytes stay the exact legacy one-field frame.
                self.assertEqual(emitted, legacy_frame)
                self.assertEqual(frame, {"failure": "incomplete"})
                emitted_text = emitted.decode("utf-8")
                self._assert_absent(credential, emitted_text)
                self._assert_absent(_HOSTILE, emitted_text)
                self._assert_decoded_plain_incomplete(
                    meta_transport._decode_child_frame(
                        emitted, credential
                    )
                )

        for colliding in ("response_status", "resp_child_001"):
            with self.subTest(
                case="child-diagnostic-credential-collision",
                colliding=colliding,
            ):
                # A key-name or response-ID value collision drops
                # only the diagnostic before anything is emitted.
                sink, calls, stderr = self._run_child(
                    child_input(colliding),
                    raise_error(_genuine_refusal(envelope)),
                )
                self._assert_child_silent(stderr)
                assert_one_call(calls, colliding)
                emitted, frame = self._emitted_frame(sink)
                self.assertEqual(emitted, legacy_frame)
                self.assertEqual(frame, {"failure": "incomplete"})
                self._assert_decoded_plain_incomplete(
                    meta_transport._decode_child_frame(
                        emitted, colliding
                    )
                )

        def mutated_wire(path, value=_OMIT):
            frame = copy.deepcopy(safe_frame)
            target = frame
            for part in path[:-1]:
                target = target[part]
            if value is _OMIT:
                del target[path[-1]]
            else:
                target[path[-1]] = value
            return _compact_frame_bytes(frame)

        usage_input = ("diagnostic", "usage", "input_tokens")
        malformed_wire_cases = [
            (
                "extra-prompt-field",
                mutated_wire(
                    ("diagnostic", "prompt"), _ARABIC_SENTENCE
                ),
            ),
            (
                "bool-version",
                mutated_wire(("diagnostic", "version"), True),
            ),
            (
                "float-version",
                mutated_wire(("diagnostic", "version"), 1.0),
            ),
            ("float-usage", mutated_wire(usage_input, 10.0)),
            ("bool-usage", mutated_wire(usage_input, True)),
            ("negative-usage", mutated_wire(usage_input, -1)),
            (
                "over-max-usage",
                mutated_wire(usage_input, _MAX_SAFE + 1),
            ),
            (
                "extra-usage-field",
                mutated_wire(
                    ("diagnostic", "usage", "cached_tokens"), 1
                ),
            ),
            (
                "missing-usage-field",
                mutated_wire(
                    ("diagnostic", "usage", "total_tokens")
                ),
            ),
            (
                "invalid-classifier",
                mutated_wire(
                    ("diagnostic", "classifier"),
                    "synthetic_invalid_classifier",
                ),
            ),
            (
                "invalid-reason",
                mutated_wire(("diagnostic", "reason"), _VENDOR_REASON),
            ),
            (
                "invalid-response-id",
                mutated_wire(
                    ("diagnostic", "response_id"), "resp_bad id"
                ),
            ),
            (
                "diagnostic-on-error",
                mutated_wire(("failure",), "error"),
            ),
        ]
        for label, data in malformed_wire_cases:
            with self.subTest(
                case="child-malformed-diagnosed-wire-" + label
            ):
                # Each fixture changes one field in the actual safe
                # object and must be refused, never repaired.
                self.assertEqual(
                    meta_transport._decode_child_frame(
                        data, credential
                    ),
                    (None, None),
                )

        extra_frame = copy.deepcopy(safe_frame)
        extra_frame["debug"] = _HOSTILE
        tampered_cases = [
            (
                "extra-top-level",
                _compact_frame_bytes(extra_frame),
                credential,
            ),
            ("second-frame", safe_emitted + safe_emitted, credential),
            ("missing-newline", safe_emitted[:-1], credential),
            ("trailing-byte", safe_emitted + b"x", credential),
            ("secret-response-id", safe_emitted, "resp_child_001"),
        ]
        for label, data, decode_credential in tampered_cases:
            with self.subTest(case="child-decoder-" + label):
                # Untrusted wire bytes are refused as a frame, not
                # salvaged into a diagnosed or plain outcome.
                self.assertEqual(
                    meta_transport._decode_child_frame(
                        data, decode_credential
                    ),
                    (None, None),
                )

        with self.subTest(case="child-failing-stdout-one-attempt"):
            failing = _FailingSink()
            sink, calls, stderr = self._run_child(
                child_input(),
                raise_error(_genuine_refusal(envelope)),
                sink=failing,
            )
            self._assert_child_silent(stderr)
            assert_one_call(calls)
            self.assertIs(sink, failing)
            self.assertEqual(failing.attempts, 1)

    # -- 4: actual supervisor -----------------------------------------

    def test_supervisor_preserves_diagnostic_only_after_settlement(
        self,
    ):
        credential = _SUPERVISOR_CREDENTIAL
        envelope = _envelope(
            response_id="resp_super_001",
            incomplete_details={"reason": "max_output_tokens"},
            usage={
                "input_tokens": 111,
                "output_tokens": 222,
                "total_tokens": 333,
                "output_tokens_details": {"reasoning_tokens": 44},
            },
        )
        expected = _expected_diagnostic(
            "incomplete_details", "completed", None,
            "resp_super_001", "max_output_tokens",
            _expected_usage(111, 222, 333, 44),
        )
        payload = _genuine_payload()
        wire_payload = json.loads(
            _compact_bytes(payload).decode("utf-8")
        )
        child_input = _compact_bytes({
            "payload": payload,
            "credential": credential,
            "timeout_seconds": 2.5,
        })
        success = {
            "raw": _healthy_raw(),
            "model_name": meta_protocol.MODEL,
            "response_id": "resp_super_ok",
            "outcome": "ok",
        }

        def raise_genuine(sent_payload, *, credential, timeout_seconds):
            raise _genuine_refusal(envelope)

        def raise_manual(sent_payload, *, credential, timeout_seconds):
            raise meta_protocol.MetaProtocolError("incomplete")

        def return_success(sent_payload, *, credential, timeout_seconds):
            return copy.deepcopy(success)

        def frame_from_child(stub):
            sink, calls, stderr = self._run_child(child_input, stub)
            self._assert_child_silent(stderr)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["credential"], credential)
            emitted, _frame = self._emitted_frame(sink)
            return emitted

        # Every supervisor frame below is output of the actual
        # child under the declared HTTP seam, never hand-built.
        diagnosed_frame = frame_from_child(raise_genuine)
        self._assert_decoded_diagnosed(
            meta_transport._decode_child_frame(
                diagnosed_frame, credential
            ),
            expected,
        )
        legacy_frame = frame_from_child(raise_manual)
        self.assertEqual(legacy_frame, b'{"failure":"incomplete"}\n')
        success_frame = frame_from_child(return_success)
        self.assertEqual(
            meta_transport._decode_child_frame(
                success_frame, credential
            ),
            ("result", success),
        )

        with self.subTest(case="supervisor-settled-diagnosed"):
            process = _FakeProcess(
                _pieces(diagnosed_frame, 13), [], "settle_zero"
            )
            kind, exc, record = self._supervise(process, payload)
            self.assertEqual(kind, "error")
            self.assertEqual(record, [process])
            self._assert_diagnostic(exc, expected)
            self._assert_absent(credential, _rendered(exc))
            self.assertEqual(
                json.loads(
                    bytes(process.stdin.data).decode("utf-8")
                ),
                {
                    "payload": wire_payload,
                    "credential": credential,
                    "timeout_seconds": 2.5,
                },
            )
            self.assertTrue(process.stdin.closed)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(process.wait_calls, 1)
            self.assertEqual(process.terminate_calls, 0)
            self.assertEqual(process.kill_calls, 0)

        with self.subTest(case="supervisor-settled-legacy"):
            process = _FakeProcess(
                [legacy_frame], [], "settle_zero"
            )
            kind, exc, record = self._supervise(process, payload)
            self.assertEqual(kind, "error")
            self.assertEqual(record, [process])
            self._assert_no_diagnostic(exc, "incomplete")
            self.assertEqual(process.returncode, 0)

        with self.subTest(case="supervisor-settled-success"):
            process = _FakeProcess(
                [success_frame], [], "settle_zero"
            )
            kind, result, record = self._supervise(process, payload)
            self.assertEqual(kind, "result")
            self.assertEqual(record, [process])
            self.assertEqual(result, success)
            self.assertEqual(process.returncode, 0)

        failing_writer = _FakeWriter(fail_write=True)
        negative_cases = [
            (
                "nonzero-exit",
                _FakeProcess(
                    [diagnosed_frame], [], "settle_one"
                ),
                {},
            ),
            (
                "stderr-nonempty",
                _FakeProcess(
                    [diagnosed_frame],
                    [b"synthetic stderr\n"],
                    "settle_zero",
                ),
                {},
            ),
            (
                "failed-write",
                _FakeProcess(
                    [diagnosed_frame], [], "settle_zero",
                    writer=failing_writer,
                ),
                {},
            ),
            (
                "failed-wait",
                _FakeProcess([diagnosed_frame], [], "raise"),
                {},
            ),
            (
                "truncated-at-eof",
                _FakeProcess(
                    [diagnosed_frame[:-1]], [], "settle_zero"
                ),
                {},
            ),
            (
                "no-frame-at-eof",
                _FakeProcess([], [], "settle_zero"),
                {},
            ),
            (
                "secret-response-id-on-wire",
                _FakeProcess(
                    [diagnosed_frame], [], "settle_zero"
                ),
                {"credential": "resp_super_001"},
            ),
        ]
        for label, process, kwargs in negative_cases:
            with self.subTest(case="supervisor-" + label):
                # A decoded or partly decoded diagnosed frame is
                # never enough on its own: each of these stays the
                # pre-existing fixed unknown with no diagnostic.
                kind, exc, record = self._supervise(
                    process, _genuine_payload(), **kwargs
                )
                self.assertEqual(kind, "error")
                self.assertEqual(record, [process])
                self._assert_no_diagnostic(exc, "unknown")
                self.assertIsNotNone(process.returncode)
        self.assertEqual(failing_writer.writes, 1)
        self.assertTrue(failing_writer.closed)

        with self.subTest(case="supervisor-cancelled-while-waiting"):
            cancel_event = threading.Event()
            process = _FakeProcess(
                [diagnosed_frame], [], "block",
                cancel_event=cancel_event,
            )
            kind, exc, record = self._supervise(
                process, payload, cancel_event=cancel_event
            )
            self.assertEqual(kind, "error")
            self.assertEqual(record, [process])
            self._assert_no_diagnostic(exc, "cancelled")
            self.assertTrue(cancel_event.is_set())
            self.assertGreaterEqual(process.terminate_calls, 1)
            self.assertEqual(process.returncode, -15)

        with self.subTest(case="supervisor-deadline-while-waiting"):
            # EOF and a decodable diagnosed frame arrive, but the
            # owned wait never settles before the total deadline.
            process = _FakeProcess(
                [diagnosed_frame], [], "block"
            )
            kind, exc, record = self._supervise(
                process, payload, total=0.5
            )
            self.assertEqual(kind, "error")
            self.assertEqual(record, [process])
            self._assert_no_diagnostic(exc, "timeout")
            self.assertGreaterEqual(process.terminate_calls, 1)
            self.assertEqual(process.returncode, -15)

    # -- 5: actual MetaProvider ---------------------------------------

    def test_provider_preserves_safe_diagnostic_and_legacy_errors(self):
        credential = _PROVIDER_CREDENTIAL
        provider = meta_provider.MetaProvider(
            credential=credential,
            max_output_tokens=512,
            timeout_seconds=2.5,
        )
        envelope = _envelope(
            response_id="resp_provider_001",
            output=[
                _reasoning_item(),
                _message_item("in_progress"),
            ],
            usage={
                "input_tokens": 7,
                "output_tokens_details": {"reasoning_tokens": 2},
            },
        )
        expected = _expected_diagnostic(
            "message_status", "completed", "in_progress",
            "resp_provider_001", "absent",
            _expected_usage(7, None, None, 2),
        )
        success = {
            "raw": _healthy_raw(),
            "model_name": meta_protocol.MODEL,
            "response_id": "resp_provider_ok",
            "outcome": "ok",
        }

        def fresh_context():
            return meta_provider.ProviderCallContext(10.0)

        def make_transport(calls, action):
            def transport(
                payload, *, credential, timeout_seconds,
                total_timeout_seconds, cancel_event,
            ):
                calls.append({
                    "payload": payload,
                    "credential": credential,
                    "timeout_seconds": timeout_seconds,
                    "total_timeout_seconds": total_timeout_seconds,
                    "cancel_event": cancel_event,
                })
                return action()

            return transport

        def transport_raising(calls, error_factory):
            def action():
                raise error_factory()

            return make_transport(calls, action)

        def transport_returning(calls, result):
            return make_transport(calls, lambda: result)

        with self.subTest(case="provider-safe-fresh-owned"):
            sources = []
            calls = []

            def fresh_genuine():
                source = _genuine_refusal(envelope)
                sources.append(source)
                return source

            transport = transport_raising(calls, fresh_genuine)
            first_context = fresh_context()
            kind, first = self._call_provider(
                provider, _question_request(), first_context,
                transport,
            )
            self.assertEqual(kind, "error")
            self.assertEqual(len(sources), 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["payload"], _genuine_payload())
            self.assertEqual(calls[0]["credential"], credential)
            self.assertEqual(calls[0]["timeout_seconds"], 2.5)
            self.assertGreater(
                calls[0]["total_timeout_seconds"], 0
            )
            self.assertLessEqual(
                calls[0]["total_timeout_seconds"], 10.0
            )
            self.assertIs(
                calls[0]["cancel_event"],
                first_context.cancel_event,
            )
            first_diagnostic = self._assert_diagnostic(
                first, expected
            )
            source_diagnostic = self._assert_diagnostic(
                sources[0], expected
            )
            self.assertIsNot(first, sources[0])
            self.assertIsNot(first_diagnostic, source_diagnostic)
            self.assertIsNot(
                first_diagnostic["usage"],
                source_diagnostic["usage"],
            )
            # Mutating the caught/source dictionary afterwards must
            # not reach into the provider's own fresh exception.
            source_diagnostic["classifier"] = "mutated"
            source_diagnostic["usage"]["input_tokens"] = -1
            source_diagnostic["debug"] = _HOSTILE
            self._assert_diagnostic(first, expected)

            second_context = fresh_context()
            kind, second = self._call_provider(
                provider, _question_request(), second_context,
                transport,
            )
            self.assertEqual(kind, "error")
            self.assertEqual(len(sources), 2)
            second_diagnostic = self._assert_diagnostic(
                second, expected
            )
            self.assertIsNot(second, sources[1])
            self.assertIsNot(second_diagnostic, first_diagnostic)
            self.assertIsNot(
                second_diagnostic["usage"],
                first_diagnostic["usage"],
            )
            first_diagnostic["classifier"] = "mutated-again"
            first_diagnostic["usage"]["input_tokens"] = -9
            self._assert_diagnostic(second, expected)

        extra = copy.deepcopy(expected)
        extra["debug"] = _HOSTILE
        bad_version = copy.deepcopy(expected)
        bad_version["version"] = True
        bad_usage = copy.deepcopy(expected)
        bad_usage["usage"] = {"input_tokens": True}
        bad_classifier = copy.deepcopy(expected)
        bad_classifier["classifier"] = _HOSTILE
        secret_id = copy.deepcopy(expected)
        secret_id["response_id"] = "resp_" + credential
        forged_cases = [
            ("extra-key", "incomplete", extra, "incomplete"),
            ("bool-version", "incomplete", bad_version, "incomplete"),
            (
                "malformed-usage", "incomplete", bad_usage,
                "incomplete",
            ),
            (
                "hostile-classifier", "incomplete", bad_classifier,
                "incomplete",
            ),
            (
                "secret-response-id", "incomplete", secret_id,
                "incomplete",
            ),
            ("non-dict", "incomplete", [_HOSTILE], "incomplete"),
            (
                "valid-diagnostic-on-error", "error",
                copy.deepcopy(expected), "error",
            ),
        ]
        for label, raised, value, outcome in forged_cases:
            with self.subTest(case="provider-forged-" + label):
                sources = []

                def factory(
                    raised=raised, value=value, sources=sources
                ):
                    error = meta_protocol.MetaProtocolError(raised)
                    error.diagnostic = copy.deepcopy(value)
                    sources.append(error)
                    return error

                calls = []
                kind, exc = self._call_provider(
                    provider, _question_request(), fresh_context(),
                    transport_raising(calls, factory),
                )
                # Untrusted metadata is omitted; only the fixed
                # legitimate outcome crosses the provider boundary.
                self.assertEqual(kind, "error")
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(sources), 1)
                self.assertIsNot(exc, sources[0])
                self._assert_no_diagnostic(exc, outcome)
                rendered = _rendered(exc)
                self._assert_absent(_HOSTILE, rendered)
                self._assert_absent(credential, rendered)
                self._assert_absent(_REASONING_SENTINEL, rendered)

        with self.subTest(
            case="provider-diagnostic-key-credential-omitted"
        ):
            key_provider = meta_provider.MetaProvider(
                credential="response_status",
                max_output_tokens=512,
                timeout_seconds=2.5,
            )
            sources = []

            def genuine_factory():
                source = _genuine_refusal(envelope)
                sources.append(source)
                return source

            calls = []
            kind, exc = self._call_provider(
                key_provider, _question_request(), fresh_context(),
                transport_raising(calls, genuine_factory),
            )
            self.assertEqual(kind, "error")
            self.assertEqual(calls[0]["credential"], "response_status")
            self.assertEqual(len(sources), 1)
            self.assertIsNot(exc, sources[0])
            self._assert_no_diagnostic(exc, "incomplete")

        for outcome in (
            "incomplete", "error", "unknown", "refusal",
            "cancelled", "timeout",
        ):
            with self.subTest(
                case="provider-legacy-manual", outcome=outcome
            ):
                sources = []

                def factory(outcome=outcome, sources=sources):
                    error = meta_protocol.MetaProtocolError(outcome)
                    sources.append(error)
                    return error

                calls = []
                kind, exc = self._call_provider(
                    provider, _question_request(), fresh_context(),
                    transport_raising(calls, factory),
                )
                self.assertEqual(kind, "error")
                self.assertEqual(len(calls), 1)
                self.assertEqual(len(sources), 1)
                self.assertIsNot(exc, sources[0])
                # Legacy manual errors expose only ``outcome``.
                self._assert_no_diagnostic(exc, outcome)

        with self.subTest(case="provider-ordinary-unknown"):
            def unknown_factory():
                return OSError(_HOSTILE + " " + credential)

            calls = []
            kind, exc = self._call_provider(
                provider, _question_request(), fresh_context(),
                transport_raising(calls, unknown_factory),
            )
            self.assertEqual(kind, "error")
            self.assertEqual(len(calls), 1)
            self._assert_no_diagnostic(exc, "unknown")
            rendered = _rendered(exc)
            self._assert_absent(_HOSTILE, rendered)
            self._assert_absent(credential, rendered)

        with self.subTest(case="provider-success-exact"):
            calls = []
            kind, result = self._call_provider(
                provider, _question_request(), fresh_context(),
                transport_returning(calls, success),
            )
            self.assertEqual(kind, "result")
            self.assertIs(result, success)
            self.assertEqual(
                result,
                {
                    "raw": _healthy_raw(),
                    "model_name": meta_protocol.MODEL,
                    "response_id": "resp_provider_ok",
                    "outcome": "ok",
                },
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["payload"], _genuine_payload())

        with self.subTest(case="provider-context-cancelled-before"):
            cancel_event = threading.Event()
            cancel_event.set()
            context = meta_provider.ProviderCallContext(
                10.0, cancel_event=cancel_event
            )
            calls = []
            kind, exc = self._call_provider(
                provider, _question_request(), context,
                transport_returning(calls, success),
            )
            self.assertEqual(kind, "error")
            self._assert_no_diagnostic(exc, "cancelled")
            self.assertEqual(calls, [])

        with self.subTest(case="provider-context-deadline-before"):
            context = fresh_context()
            context.deadline = time.monotonic() - 0.001
            calls = []
            kind, exc = self._call_provider(
                provider, _question_request(), context,
                transport_returning(calls, success),
            )
            self.assertEqual(kind, "error")
            self._assert_no_diagnostic(exc, "timeout")
            self.assertEqual(calls, [])

        with self.subTest(case="provider-context-cancelled-during"):
            active_context = fresh_context()

            def cancel_then_succeed(active=active_context):
                active.cancel_event.set()
                return success

            calls = []
            kind, exc = self._call_provider(
                provider, _question_request(), active_context,
                make_transport(calls, cancel_then_succeed),
            )
            self.assertEqual(kind, "error")
            self._assert_no_diagnostic(exc, "cancelled")
            self.assertEqual(len(calls), 1)
            self.assertIs(
                calls[0]["cancel_event"],
                active_context.cancel_event,
            )

        with self.subTest(case="provider-context-deadline-during"):
            active_context = fresh_context()

            def expire_then_succeed(active=active_context):
                active.deadline = time.monotonic() - 0.001
                return success

            calls = []
            kind, exc = self._call_provider(
                provider, _question_request(), active_context,
                make_transport(calls, expire_then_succeed),
            )
            self.assertEqual(kind, "error")
            self._assert_no_diagnostic(exc, "timeout")
            self.assertEqual(len(calls), 1)
