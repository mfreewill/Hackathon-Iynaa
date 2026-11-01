"""Tests for the bounded Meta terminal-stream collector.

The collector under test is ``teacher_demo.meta_stream``. Every test first
asserts through ``find_spec`` that the module exists, so a missing
collector fails on the collector itself rather than on an
unrelated downstream dependency. ``teacher_demo.meta_protocol``
supplies the genuine
downstream parser, model literal and public error type. Fixtures
below only build synthetic Responses envelopes and SSE bytes;
nothing here reimplements collector framing or parsing. No
network, subprocess, credential store or model call is used.
"""
from __future__ import annotations

import copy
import importlib
import importlib.util
import inspect
import json
import unittest
from contextlib import contextmanager
from pathlib import Path

_FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "meta_stream_inputs.json"
)
_REFUSED_MESSAGE = "meta protocol response refused"
_INVALID_CREDENTIAL_MESSAGE = "meta stream credential is invalid"
_ALLOWED_OUTCOMES = frozenset((
    "invalid_schema",
    "unknown",
    "error",
    "incomplete",
    "cancelled",
    "empty",
    "refusal",
))
_DEFAULT = object()
_OMIT = object()


def _dumps(value, ensure_ascii=False):
    return json.dumps(value, ensure_ascii=ensure_ascii)


class MetaStreamTest(unittest.TestCase):
    """Six independent oracles for ``collect_response``."""

    # -- independent setup helpers ---------------------------------

    def _fresh_inputs(self):
        text = _FIXTURE_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
        self.assertIsInstance(data, dict)
        credential = data["synthetic_credential"]
        raw = data["raw"]
        response_id = data["response_id"]
        self.assertIsInstance(credential, str)
        self.assertTrue(
            bool(credential.strip()),
            "fixture credential precondition failed",
        )
        self.assertIsInstance(raw, dict)
        self.assertIsInstance(response_id, str)
        self.assertTrue(bool(response_id.strip()))
        return credential, copy.deepcopy(raw), response_id

    def _collector(self):
        try:
            spec = importlib.util.find_spec(
                "teacher_demo.meta_stream"
            )
        except (ImportError, ValueError, AttributeError):
            spec = None
        self.assertIsNotNone(
            spec, "teacher_demo.meta_stream is missing"
        )
        stream_module = importlib.import_module(
            "teacher_demo.meta_stream"
        )
        protocol = importlib.import_module(
            "teacher_demo.meta_protocol"
        )
        collect = getattr(stream_module, "collect_response", None)
        self.assertTrue(
            callable(collect), "collect_response is missing"
        )
        return stream_module, protocol

    def _scenario(self):
        stream_module, protocol = self._collector()
        credential, raw, response_id = self._fresh_inputs()
        return stream_module, protocol, credential, raw, response_id

    # -- synthetic fixture builders (no collector logic) -----------

    def _make_response(
        self,
        protocol,
        response_id,
        raw=None,
        *,
        ordinary_text=None,
        parts=None,
        output=None,
        status="completed",
        model=_DEFAULT,
        include_error=True,
        error_value=None,
        include_incomplete=True,
        incomplete_value=None,
        extra=None,
    ):
        if output is None:
            if parts is None:
                if ordinary_text is not None:
                    text = ordinary_text
                else:
                    text = _dumps(raw)
                parts = [text]
            output = [{
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": part}
                    for part in parts
                ],
            }]
        response = {}
        response["id"] = response_id
        response["object"] = "response"
        if model is _DEFAULT:
            response["model"] = protocol.MODEL
        elif model is not _OMIT:
            response["model"] = model
        response["status"] = status
        response["output"] = output
        if include_error:
            response["error"] = error_value
        if include_incomplete:
            response["incomplete_details"] = incomplete_value
        if extra:
            response.update(extra)
        return response

    def _frame(
        self,
        data,
        event=_DEFAULT,
        ending="\n",
        prefix=(),
        middle=(),
        ensure_ascii=False,
    ):
        if isinstance(data, str):
            data_text = data
        else:
            data_text = _dumps(data, ensure_ascii=ensure_ascii)
        if event is _DEFAULT:
            if isinstance(data, dict):
                event_value = data.get("type")
                if not isinstance(event_value, str):
                    event_value = None
            else:
                event_value = None
        else:
            event_value = event
        lines = list(prefix)
        if event_value is not None:
            lines.append("event: " + event_value)
        lines.extend(middle)
        lines.append("data: " + data_text)
        return (ending.join(lines) + ending + ending).encode(
            "utf-8"
        )

    def _terminal(
        self, response, event_type="response.completed", **kwargs
    ):
        event = {"type": event_type, "response": response}
        return self._frame(event, event=event_type, **kwargs)

    def _done(self, ending="\n"):
        return ("data: [DONE]" + ending + ending).encode(
            "utf-8"
        )

    @staticmethod
    def _pieces(data, size):
        return [
            data[index:index + size]
            for index in range(0, len(data), size)
        ]

    @staticmethod
    def _unicode_escape(text):
        return "".join(
            "\\u%04x" % ord(char) for char in text
        )

    # -- closed-shape assertion helpers ----------------------------

    def _assert_success(
        self, result, protocol, raw, response_id
    ):
        self.assertIsInstance(result, dict)
        self.assertEqual(
            set(result.keys()),
            {"raw", "model_name", "response_id", "outcome"},
        )
        self.assertEqual(result["outcome"], "ok")
        self.assertEqual(result["model_name"], protocol.MODEL)
        self.assertEqual(result["response_id"], response_id)
        self.assertEqual(result["raw"], raw)
        return result

    def _assert_refused(
        self, stream_module, protocol, chunks, credential, outcome,
        expected_diagnostic=None
    ):
        self.assertIn(outcome, _ALLOWED_OUTCOMES)
        with self.assertRaises(
            protocol.MetaProtocolError
        ) as caught:
            stream_module.collect_response(
                chunks, credential=credential
            )
        exc = caught.exception
        self.assertEqual(str(exc), _REFUSED_MESSAGE)
        self.assertEqual(exc.args, (_REFUSED_MESSAGE,))
        self.assertEqual(exc.outcome, outcome)
        public = {
            name
            for name in vars(exc)
            if not name.startswith("_")
        }
        if expected_diagnostic is None:
            self.assertEqual(public, {"outcome"})
            return
        self.assertEqual(outcome, "incomplete")
        self.assertEqual(set(vars(exc)), {"outcome", "diagnostic"})
        diagnostic = exc.diagnostic
        self.assertIs(type(diagnostic), dict)
        if isinstance(credential, str) and credential:
            self.assertTrue(
                credential
                not in json.dumps(
                    diagnostic, ensure_ascii=False, default=str
                ),
                "diagnostic retained the credential",
            )
        self.assertEqual(
            set(diagnostic),
            {
                "version",
                "classifier",
                "response_status",
                "message_status",
                "response_id",
                "reason",
                "usage",
            },
        )
        usage = diagnostic.get("usage")
        self.assertIs(type(usage), dict)
        self.assertEqual(
            set(usage),
            {
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "reasoning_tokens",
            },
        )
        self.assertIs(type(diagnostic.get("version")), int)
        for value in usage.values():
            self.assertTrue(
                value is None or type(value) is int,
                "diagnostic usage counter is not an exact integer",
            )
        self.assertIsNone(exc.__cause__)
        self.assertIsNone(exc.__context__)
        self.assertEqual(diagnostic, expected_diagnostic)

    def _assert_invalid_credential(
        self, stream_module, protocol, chunks, credential
    ):
        with self.assertRaises(ValueError) as caught:
            stream_module.collect_response(
                chunks, credential=credential
            )
        exc = caught.exception
        self.assertIs(type(exc), ValueError)
        self.assertNotIsInstance(
            exc, protocol.MetaProtocolError
        )
        self.assertEqual(
            str(exc), _INVALID_CREDENTIAL_MESSAGE
        )
        self.assertEqual(
            exc.args, (_INVALID_CREDENTIAL_MESSAGE,)
        )
        public = {
            name
            for name in vars(exc)
            if not name.startswith("_")
        }
        self.assertEqual(public, set())

    def _assert_credential_absent(self, result, credential):
        stack = [result]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                if credential in item:
                    self.fail(
                        "credential escaped in successful "
                        "result value"
                    )
            elif isinstance(item, dict):
                stack.extend(item.keys())
                stack.extend(item.values())
            elif isinstance(item, (list, tuple)):
                stack.extend(item)
        for ensure_ascii in (False, True):
            serialized = json.dumps(
                result, ensure_ascii=ensure_ascii
            )
            if credential in serialized:
                self.fail(
                    "credential escaped in serialized "
                    "successful result"
                )

    @contextmanager
    def _patched_bounds(
        self, stream_module, stream_bytes=None, frame_bytes=None
    ):
        old_stream = stream_module.MAX_STREAM_BYTES
        old_frame = stream_module.MAX_FRAME_BYTES
        try:
            if stream_bytes is not None:
                stream_module.MAX_STREAM_BYTES = stream_bytes
            if frame_bytes is not None:
                stream_module.MAX_FRAME_BYTES = frame_bytes
            yield
        finally:
            stream_module.MAX_STREAM_BYTES = old_stream
            stream_module.MAX_FRAME_BYTES = old_frame

    # 1 ------------------------------------------------------------

    def test_terminal_response_is_the_only_success_authority(self):
        stream_module, protocol, credential, raw, rid = (
            self._scenario()
        )
        signature = inspect.signature(
            stream_module.collect_response
        )
        params = list(signature.parameters.values())
        self.assertEqual(
            [param.name for param in params],
            ["chunks", "credential"],
        )
        self.assertEqual(
            params[1].kind,
            inspect.Parameter.KEYWORD_ONLY,
        )

        def collect(chunks):
            return stream_module.collect_response(
                chunks, credential=credential
            )

        with self.subTest(case="lone-terminal-no-done"):
            response = self._make_response(
                protocol, rid, copy.deepcopy(raw)
            )
            expected = protocol.parse_response(
                _dumps(response).encode("utf-8")
            )
            result = collect([self._terminal(response)])
            self._assert_success(result, protocol, raw, rid)
            self.assertEqual(result, expected)
            self._assert_credential_absent(result, credential)

        with self.subTest(case="lone-terminal-with-done"):
            response = self._make_response(
                protocol, rid, copy.deepcopy(raw)
            )
            stream = self._terminal(response) + self._done()
            result = collect([stream])
            self._assert_success(result, protocol, raw, rid)

        with self.subTest(case="created-delta-terminal"):
            other = copy.deepcopy(raw)
            other["sections"][0]["text"] = (
                "early different text"
            )
            created = {
                "type": "response.created",
                "response": {
                    "id": rid,
                    "model": protocol.MODEL,
                    "status": "in_progress",
                },
            }
            delta = {
                "type": "response.output_text.delta",
                "delta": _dumps(other),
            }
            in_progress = {
                "type": "response.in_progress",
                "response": {
                    "id": rid,
                    "model": protocol.MODEL,
                },
            }
            response = self._make_response(
                protocol, rid, copy.deepcopy(raw)
            )
            prefix = (
                self._frame(created)
                + self._frame(delta)
                + self._frame(in_progress)
            )
            for suffix_label, suffix in (
                ("no-done", b""),
                ("with-done", self._done()),
            ):
                with self.subTest(suffix=suffix_label):
                    stream = (
                        prefix
                        + self._terminal(response)
                        + suffix
                    )
                    result = collect([stream])
                    self._assert_success(
                        result, protocol, raw, rid
                    )

        with self.subTest(case="early-text-alone-never-succeeds"):
            created = {
                "type": "response.created",
                "response": {
                    "id": rid,
                    "model": protocol.MODEL,
                },
            }
            delta = {
                "type": "response.output_text.delta",
                "delta": _dumps(raw),
            }
            done_text = {
                "type": "response.output_text.done",
                "text": _dumps(raw),
            }
            done_item = {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{
                        "type": "output_text",
                        "text": _dumps(raw),
                    }],
                },
            }
            streams = {
                "delta-only": self._frame(delta),
                "created-delta": (
                    self._frame(created) + self._frame(delta)
                ),
                "text-done-only": self._frame(done_text),
                "item-done-only": self._frame(done_item),
            }
            for label, stream in streams.items():
                with self.subTest(stream=label):
                    self._assert_refused(
                        stream_module,
                        protocol,
                        [stream],
                        credential,
                        "unknown",
                    )

        with self.subTest(case="id-and-codepoints-preserved"):
            unicode_id = rid + "-\u00e9\U0001d11e"
            created = {
                "type": "response.created",
                "response": {
                    "id": unicode_id,
                    "model": protocol.MODEL,
                },
            }
            response = self._make_response(
                protocol, unicode_id, copy.deepcopy(raw)
            )
            stream = (
                self._frame(created)
                + self._terminal(response)
            )
            result = collect([stream])
            self._assert_success(
                result, protocol, raw, unicode_id
            )
            text = result["raw"]["sections"][0]["text"]
            original = raw["sections"][0]["text"]
            self.assertEqual(text, original)
            self.assertEqual(
                text.encode("utf-8"),
                original.encode("utf-8"),
            )
            self.assertEqual(
                [ord(char) for char in text],
                [ord(char) for char in original],
            )

        with self.subTest(case="chunk-splitting-including-utf8"):
            response = self._make_response(
                protocol, rid, copy.deepcopy(raw)
            )
            stream = self._terminal(response) + self._done()
            needle = "\U0001d11e".encode("utf-8")
            self.assertIn(needle, stream)
            cut = stream.index(needle)

            def chunkings():
                yield "single", [stream]
                yield "one-byte", self._pieces(stream, 1)
                yield "two-byte", self._pieces(stream, 2)
                yield "seven-byte", self._pieces(stream, 7)
                yield "inside-utf8", [
                    stream[:cut + 1],
                    stream[cut + 1:cut + 3],
                    stream[cut + 3:],
                ]
                yield "empty-chunks", [
                    b"",
                    stream[:1],
                    b"",
                    stream[1:9],
                    b"",
                    stream[9:],
                    b"",
                ]
                yield "generator", (
                    piece
                    for piece in self._pieces(stream, 3)
                )
                yield "tuple", tuple(
                    self._pieces(stream, 5)
                )

            for label, chunks in chunkings():
                with self.subTest(chunking=label):
                    result = collect(chunks)
                    self._assert_success(
                        result, protocol, raw, rid
                    )

    # 2 ------------------------------------------------------------

    def test_sse_lines_data_comments_and_optional_fields(self):
        stream_module, protocol, credential, raw, rid = (
            self._scenario()
        )

        def collect(chunks):
            return stream_module.collect_response(
                chunks, credential=credential
            )

        def expect_success(chunks, expected_raw=None):
            if expected_raw is None:
                expected_raw = raw
            result = collect(chunks)
            self._assert_success(
                result, protocol, expected_raw, rid
            )
            return result

        response = self._make_response(
            protocol, rid, copy.deepcopy(raw)
        )
        event_object = {
            "type": "response.completed",
            "response": response,
        }
        data_text = _dumps(event_object)
        data_bytes = data_text.encode("utf-8")

        with self.subTest(case="line-endings"):
            for label, ending in (
                ("lf", "\n"),
                ("crlf", "\r\n"),
                ("cr", "\r"),
            ):
                with self.subTest(ending=label):
                    stream = (
                        self._terminal(
                            response, ending=ending
                        )
                        + self._done(ending=ending)
                    )
                    expect_success([stream])
            with self.subTest(ending="mixed"):
                stream = (
                    b"event: response.completed\r\n"
                    b"data: " + data_bytes + b"\n"
                    b"\r"
                    b"data: [DONE]\r\n"
                    b"\n"
                )
                expect_success([stream])

        with self.subTest(case="split-crlf-bom-utf8"):
            crlf_stream = (
                self._terminal(response, ending="\r\n")
                + self._done(ending="\r\n")
            )
            cut = crlf_stream.index(b"\r\n")
            with self.subTest(split="crlf-across-chunks"):
                expect_success([
                    crlf_stream[:cut + 1],
                    crlf_stream[cut + 1:],
                ])
            with self.subTest(split="every-crlf-one-byte"):
                expect_success(self._pieces(crlf_stream, 1))
            plain_stream = self._terminal(response)
            bom_stream = b"\xef\xbb\xbf" + plain_stream
            with self.subTest(split="bom-single-chunk"):
                expect_success([bom_stream])
            with self.subTest(split="bom-across-chunks"):
                expect_success([
                    bom_stream[:1],
                    bom_stream[1:2],
                    bom_stream[2:],
                ])
            with self.subTest(split="bom-one-byte"):
                expect_success(self._pieces(bom_stream, 1))
            needle = "\U0001d11e".encode("utf-8")
            cut = plain_stream.index(needle)
            with self.subTest(split="inside-utf8"):
                expect_success([
                    plain_stream[:cut + 2],
                    plain_stream[cut + 2:],
                ])

        with self.subTest(case="multiline-data"):
            pretty = json.dumps(
                event_object, ensure_ascii=False, indent=2
            )
            pretty_lines = [
                "data: " + line
                for line in pretty.split("\n")
            ]
            pretty_frame = (
                "\n".join(pretty_lines) + "\n\n"
            ).encode("utf-8")
            with self.subTest(form="pretty-json-lines"):
                expect_success([pretty_frame])
            response_text = _dumps(response)
            first = '{"type":"response.completed",'
            second = '"response":' + response_text + "}"
            joined_frame = (
                "data: " + first + "\n"
                "data:\n"
                "data: " + second + "\n"
                "\n"
            ).encode("utf-8")
            with self.subTest(form="lf-joined-data-lines"):
                expect_success([joined_frame])
            for label, prefix in (
                ("data-no-space", b"data:"),
                ("data-one-space", b"data: "),
                ("data-two-spaces", b"data:  "),
            ):
                with self.subTest(form=label):
                    frame = prefix + data_bytes + b"\n\n"
                    expect_success([frame])
            for label, prefix in (
                ("event-no-space", b"event:"),
                ("event-one-space", b"event: "),
            ):
                with self.subTest(form=label):
                    frame = (
                        prefix
                        + b"response.completed\n"
                        + b"data: "
                        + data_bytes
                        + b"\n\n"
                    )
                    expect_success([frame])

        with self.subTest(
            case="comments-ignored-fields-blank-frames"
        ):
            stream = (
                b"\n\n"
                b": leading comment\n"
                b":\n"
                b"\n"
                b"id: ignored-stream-id\n"
                b"retry: 60000\n"
                b"unknown-field: a:b:c\n"
                b"\n"
                b": inside terminal frame\n"
                b"id: not-a-response-id\n"
                b"retry: 60000\n"
                b"x-custom: value:with:colons\n"
                b"event: response.completed\n"
                b"data: " + data_bytes + b"\n"
                b"\n"
                b"data: [DONE]\n"
                b"\n"
                b": harmless after done\n"
                b"\n"
                b": final comment\n"
                b"\n"
                b"\n"
            )
            expect_success([stream])

        with self.subTest(case="optional-and-repeated-event"):
            frames = {
                "no-event-line": (
                    b"data: " + data_bytes + b"\n\n"
                ),
                "blank-event": (
                    b"event:\n"
                    b"data: " + data_bytes + b"\n\n"
                ),
                "blank-event-one-space": (
                    b"event: \n"
                    b"data: " + data_bytes + b"\n\n"
                ),
                "repeated-last-correct": (
                    b"event: response.created\n"
                    b"event: response.completed\n"
                    b"data: " + data_bytes + b"\n\n"
                ),
                "repeated-last-blank": (
                    b"event: response.completed\n"
                    b"event:\n"
                    b"data: " + data_bytes + b"\n\n"
                ),
            }
            for label, frame in frames.items():
                with self.subTest(form=label):
                    expect_success([frame])
            terminal = self._terminal(response)
            done_frames = {
                "done-plain": b"data: [DONE]\n\n",
                "done-no-space": b"data:[DONE]\n\n",
                "done-crlf": b"data: [DONE]\r\n\r\n",
                "done-blank-event": (
                    b"event:\n"
                    b"data: [DONE]\n\n"
                ),
                "done-repeated-last-blank": (
                    b"event: response.completed\n"
                    b"event:\n"
                    b"data: [DONE]\n\n"
                ),
                "done-ignored-fields": (
                    b"id: ignored\n"
                    b"retry: 60000\n"
                    b"x-note: ignored\n"
                    b"data: [DONE]\n\n"
                ),
                "done-then-comments": (
                    b"data: [DONE]\n\n"
                    b": after\n\n"
                    b"\n"
                ),
            }
            for label, done_frame in done_frames.items():
                with self.subTest(form=label):
                    expect_success([terminal + done_frame])

        with self.subTest(
            case="unicode-padding-and-object-order"
        ):
            with self.subTest(form="ascii-escaped-json"):
                frame = self._frame(
                    event_object,
                    event="response.completed",
                    ensure_ascii=True,
                )
                result = expect_success([frame])
                self.assertEqual(
                    result["raw"]["sections"][0]["text"],
                    raw["sections"][0]["text"],
                )
            with self.subTest(form="reordered-object-keys"):
                reordered = {
                    "output": response["output"],
                    "incomplete_details": None,
                    "error": None,
                    "status": "completed",
                    "model": protocol.MODEL,
                    "object": "response",
                    "id": rid,
                }
                frame = self._frame(
                    {
                        "response": reordered,
                        "type": "response.completed",
                    },
                    event="response.completed",
                )
                expect_success([frame])
            with self.subTest(form="ordinary-padding"):
                padded_response = self._make_response(
                    protocol,
                    rid,
                    ordinary_text=(
                        "  \n" + _dumps(raw) + "  \n"
                    ),
                )
                expect_success(
                    [self._terminal(padded_response)]
                )
            with self.subTest(form="spaced-json"):
                spaced = json.dumps(
                    event_object,
                    ensure_ascii=False,
                    separators=(", ", " : "),
                )
                frame = (
                    "data: " + spaced + "\n\n"
                ).encode("utf-8")
                expect_success([frame])
            text = raw["sections"][0]["text"]
            self.assertTrue(text.startswith("  "))
            self.assertTrue(text.endswith("  "))
            self.assertIn("\n", text)

    # 3 ------------------------------------------------------------

    def test_terminal_lifecycle_and_identity_cannot_be_salvaged(
        self,
    ):
        stream_module, protocol, credential, raw, rid = (
            self._scenario()
        )
        other_id = rid + "-other"

        def refused(chunks, outcome, expected_diagnostic=None):
            self._assert_refused(
                stream_module,
                protocol,
                chunks,
                credential,
                outcome,
                expected_diagnostic=expected_diagnostic,
            )

        def valid_response(response_id=rid, **kwargs):
            if "raw" not in kwargs and not any(
                key in kwargs
                for key in ("ordinary_text", "parts", "output")
            ):
                kwargs["raw"] = copy.deepcopy(raw)
            return self._make_response(
                protocol, response_id, **kwargs
            )

        def created(response_id=rid, model=None):
            identity = {"id": response_id}
            identity["model"] = (
                protocol.MODEL if model is None else model
            )
            return {
                "type": "response.created",
                "response": identity,
            }

        def delta():
            return {
                "type": "response.output_text.delta",
                "delta": _dumps(raw),
            }

        with self.subTest(case="parser-terminal-outcomes"):
            failed = valid_response(
                status="failed",
                error_value={
                    "code": "synthetic_failure",
                    "message": "synthetic failure",
                },
            )
            with self.subTest(kind="failed"):
                refused(
                    [self._terminal(
                        failed, "response.failed"
                    )],
                    "error",
                )
            incomplete = valid_response(
                status="incomplete",
                incomplete_value={
                    "reason": "max_output_tokens"
                },
            )
            with self.subTest(kind="incomplete"):
                refused(
                    [self._terminal(
                        incomplete, "response.incomplete"
                    )],
                    "incomplete",
                    expected_diagnostic={
                        "version": 1,
                        "classifier": "incomplete_details",
                        "response_status": "incomplete",
                        "message_status": None,
                        "response_id": None,
                        "reason": "max_output_tokens",
                        "usage": {
                            "input_tokens": None,
                            "output_tokens": None,
                            "total_tokens": None,
                            "reasoning_tokens": None,
                        },
                    },
                )
            refusal_output = [{
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{
                    "type": "refusal",
                    "refusal": "synthetic refusal",
                }],
            }]
            with self.subTest(kind="refusal"):
                refused(
                    [self._terminal(valid_response(
                        output=refusal_output
                    ))],
                    "refusal",
                )
            with self.subTest(kind="empty-whitespace"):
                refused(
                    [self._terminal(valid_response(
                        parts=["  \n\t "]
                    ))],
                    "empty",
                )
            with self.subTest(kind="empty-no-output"):
                refused(
                    [self._terminal(valid_response(
                        output=[]
                    ))],
                    "empty",
                )
            with self.subTest(kind="completed-with-error"):
                refused(
                    [self._terminal(valid_response(
                        error_value={
                            "code": "synthetic_error"
                        }
                    ))],
                    "error",
                )
            with self.subTest(
                kind="completed-with-incomplete-details"
            ):
                refused(
                    [self._terminal(valid_response(
                        incomplete_value={
                            "reason": "synthetic"
                        }
                    ))],
                    "incomplete",
                    expected_diagnostic={
                        "version": 1,
                        "classifier": "incomplete_details",
                        "response_status": "completed",
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
                )
            message_incomplete = [{
                "type": "message",
                "role": "assistant",
                "status": "in_progress",
                "content": [{
                    "type": "output_text",
                    "text": _dumps(raw),
                }],
            }]
            with self.subTest(kind="message-incomplete"):
                refused(
                    [self._terminal(valid_response(
                        output=message_incomplete
                    ))],
                    "incomplete",
                    expected_diagnostic={
                        "version": 1,
                        "classifier": "message_status",
                        "response_status": "completed",
                        "message_status": "in_progress",
                        "response_id": None,
                        "reason": "absent",
                        "usage": {
                            "input_tokens": None,
                            "output_tokens": None,
                            "total_tokens": None,
                            "reasoning_tokens": None,
                        },
                    },
                )

        with self.subTest(case="error-event"):
            error_event = {
                "type": "error",
                "code": "synthetic_error",
                "message": "synthetic provider error",
            }
            with self.subTest(kind="error-after-valid-delta"):
                stream = (
                    self._frame(created())
                    + self._frame(delta())
                    + self._frame(
                        error_event, event="error"
                    )
                )
                refused([stream], "error")
            with self.subTest(
                kind="error-before-valid-terminal"
            ):
                stream = (
                    self._frame(
                        error_event, event="error"
                    )
                    + self._terminal(valid_response())
                )
                refused([stream], "error")

        with self.subTest(case="missing-terminal-is-unknown"):
            complete_looking = {
                "type": "response.in_progress",
                "response": valid_response(),
            }
            streams = {
                "empty-iterable": None,
                "empty-chunks": [b"", b""],
                "comments-only": [b": only\n\n"],
                "created-delta": [
                    self._frame(created())
                    + self._frame(delta())
                ],
                "done-alone": [self._done()],
                "premature-done": [
                    self._frame(created())
                    + self._frame(delta())
                    + self._done()
                ],
                "done-then-terminal": [
                    self._done()
                    + self._terminal(valid_response())
                ],
                "nonterminal-complete-response": [
                    self._frame(complete_looking)
                ],
            }
            for label, chunks in streams.items():
                with self.subTest(form=label):
                    refused(
                        [] if chunks is None else chunks,
                        "unknown",
                    )

        with self.subTest(
            case="missing-or-mismatched-response"
        ):
            with self.subTest(form="terminal-no-response"):
                refused(
                    [self._frame(
                        {"type": "response.completed"}
                    )],
                    "invalid_schema",
                )
            for label, bad in (
                ("response-null", None),
                ("response-string", "synthetic"),
                ("response-list", []),
            ):
                with self.subTest(form=label):
                    refused(
                        [self._frame({
                            "type": "response.completed",
                            "response": bad,
                        })],
                        "invalid_schema",
                    )
            for label, mutate in (
                ("id-missing", "drop-id"),
                ("id-blank", "blank-id"),
                ("id-nonstring", "int-id"),
                ("model-missing", "drop-model"),
                ("model-wrong", "wrong-model"),
                ("model-null", "null-model"),
                ("status-missing", "drop-status"),
            ):
                with self.subTest(form=label):
                    response = valid_response()
                    if mutate == "drop-id":
                        del response["id"]
                    elif mutate == "blank-id":
                        response["id"] = "   "
                    elif mutate == "int-id":
                        response["id"] = 123
                    elif mutate == "drop-model":
                        del response["model"]
                    elif mutate == "wrong-model":
                        response["model"] = "other-model"
                    elif mutate == "null-model":
                        response["model"] = None
                    elif mutate == "drop-status":
                        del response["status"]
                    refused(
                        [self._terminal(response)],
                        "invalid_schema",
                    )
            with self.subTest(form="preterminal-wrong-model"):
                stream = (
                    self._frame(
                        created(model="other-model")
                    )
                    + self._terminal(valid_response())
                )
                refused([stream], "invalid_schema")
            for label, bad_identity in (
                ("preterminal-response-string", "x"),
                ("preterminal-response-blank-id", {
                    "id": "  ",
                    "model": protocol.MODEL,
                }),
            ):
                with self.subTest(form=label):
                    event = {
                        "type": "response.created",
                        "response": bad_identity,
                    }
                    stream = (
                        self._frame(event)
                        + self._terminal(valid_response())
                    )
                    refused([stream], "invalid_schema")

        with self.subTest(case="status-mismatch"):
            combos = (
                ("completed-event-failed", "response.completed", "failed"),
                ("failed-event-completed", "response.failed", "completed"),
                ("incomplete-event-completed", "response.incomplete", "completed"),
                ("completed-event-incomplete", "response.completed", "incomplete"),
                ("completed-event-cancelled", "response.completed", "cancelled"),
            )
            for label, event_type, status in combos:
                with self.subTest(form=label):
                    response = valid_response(
                        status=status
                    )
                    refused(
                        [self._terminal(
                            response, event_type
                        )],
                        "invalid_schema",
                    )

        with self.subTest(case="conflicting-observed-ids"):
            delta_with_other = {
                "type": "response.output_text.delta",
                "delta": _dumps(raw),
                "response": {
                    "id": other_id,
                    "model": protocol.MODEL,
                },
            }
            streams = {
                "created-a-terminal-b": (
                    self._frame(created(rid))
                    + self._terminal(
                        valid_response(other_id)
                    )
                ),
                "two-preterminal-ids": (
                    self._frame(created(rid))
                    + self._frame(created(other_id))
                    + self._terminal(valid_response(rid))
                ),
                "delta-response-b-terminal-a": (
                    self._frame(delta_with_other)
                    + self._terminal(valid_response(rid))
                ),
            }
            for label, stream in streams.items():
                with self.subTest(form=label):
                    refused([stream], "unknown")

        with self.subTest(case="duplicate-and-after-terminal"):
            terminal = self._terminal(valid_response())
            failed_response = valid_response(
                status="failed",
                error_value={"code": "synthetic"},
            )
            failed_frame = self._terminal(
                failed_response, "response.failed"
            )
            custom = {"type": "response.custom"}
            streams = {
                "duplicate-identical": (
                    terminal + terminal
                ),
                "completed-then-failed": (
                    terminal + failed_frame
                ),
                "terminal-then-delta": (
                    terminal + self._frame(delta())
                ),
                "terminal-then-created": (
                    terminal + self._frame(created())
                ),
                "terminal-then-custom": (
                    terminal + self._frame(custom)
                ),
                "terminal-done-delta": (
                    terminal
                    + self._done()
                    + self._frame(delta())
                ),
                "terminal-done-terminal": (
                    terminal + self._done() + terminal
                ),
            }
            for label, stream in streams.items():
                with self.subTest(form=label):
                    refused([stream], "unknown")

        with self.subTest(case="event-json-type-mismatch"):
            event_object = {
                "type": "response.completed",
                "response": valid_response(),
            }
            data_bytes = _dumps(event_object).encode(
                "utf-8"
            )
            frames = {
                "sse-created-json-completed": (
                    b"event: response.created\n"
                    b"data: " + data_bytes + b"\n\n"
                ),
                "sse-failed-json-completed": (
                    b"event: response.failed\n"
                    b"data: " + data_bytes + b"\n\n"
                ),
                "repeated-last-mismatch": (
                    b"event: response.completed\n"
                    b"event: response.created\n"
                    b"data: " + data_bytes + b"\n\n"
                ),
            }
            for label, frame in frames.items():
                with self.subTest(form=label):
                    refused([frame], "invalid_schema")
            with self.subTest(form="preterminal-mismatch"):
                stream = (
                    self._frame(
                        created(),
                        event=(
                            "response.output_text.delta"
                        ),
                    )
                    + self._terminal(valid_response())
                )
                refused([stream], "invalid_schema")

        with self.subTest(case="dangling-and-iteration"):
            terminal_frame = self._terminal(
                valid_response()
            )
            with self.subTest(form="dangling-terminal-frame"):
                dangling = terminal_frame[:-1]
                self.assertTrue(
                    dangling.endswith(b"\n")
                )
                self.assertFalse(
                    dangling.endswith(b"\n\n")
                )
                refused([dangling], "unknown")
            with self.subTest(
                form="dangling-no-final-newline"
            ):
                refused(
                    [terminal_frame[:-2]], "unknown"
                )
            with self.subTest(form="dangling-preterminal"):
                stream = (
                    self._frame(created())
                    + self._frame(delta())[:-1]
                )
                refused([stream], "unknown")

            def failing_after(prefix):
                yield prefix
                raise RuntimeError(
                    "synthetic iteration failure"
                )

            def failing_now():
                raise RuntimeError(
                    "synthetic iteration failure"
                )
                yield b""

            with self.subTest(form="iteration-immediate"):
                refused(failing_now(), "unknown")
            with self.subTest(
                form="iteration-before-terminal"
            ):
                refused(
                    failing_after(
                        self._frame(created())
                    ),
                    "unknown",
                )
            with self.subTest(
                form="iteration-after-terminal"
            ):
                refused(
                    failing_after(terminal_frame),
                    "unknown",
                )

    # 4 ------------------------------------------------------------

    def test_strict_json_utf8_and_byte_bounds(self):
        stream_module, protocol, credential, raw, rid = (
            self._scenario()
        )
        self.assertEqual(
            stream_module.MAX_STREAM_BYTES, 16 * 1024 * 1024
        )
        self.assertEqual(
            stream_module.MAX_FRAME_BYTES, 4 * 1024 * 1024
        )
        self.assertIs(
            type(stream_module.MAX_STREAM_BYTES), int
        )
        self.assertIs(
            type(stream_module.MAX_FRAME_BYTES), int
        )
        original_stream = stream_module.MAX_STREAM_BYTES
        original_frame = stream_module.MAX_FRAME_BYTES

        def refused(chunks, outcome="invalid_schema"):
            self._assert_refused(
                stream_module,
                protocol,
                chunks,
                credential,
                outcome,
            )

        def valid_response(**kwargs):
            if "raw" not in kwargs and not any(
                key in kwargs
                for key in ("ordinary_text", "parts", "output")
            ):
                kwargs["raw"] = copy.deepcopy(raw)
            return self._make_response(
                protocol, rid, **kwargs
            )

        response = valid_response()
        terminal = self._terminal(response)

        with self.subTest(case="healthy-small-control"):
            result = stream_module.collect_response(
                [b"", terminal, b""],
                credential=credential,
            )
            self._assert_success(
                result, protocol, raw, rid
            )

        with self.subTest(case="bytes-only-chunks"):
            cases = {
                "str-chunk": [terminal.decode("utf-8")],
                "bytearray-chunk": [bytearray(terminal)],
                "memoryview-chunk": [
                    memoryview(terminal)
                ],
                "none-chunk": [terminal, None],
                "nested-list-chunk": [[terminal]],
                "dict-chunk": [{"data": terminal}],
                "bytes-as-iterable": terminal,
                "str-as-iterable": terminal.decode(
                    "utf-8"
                ),
            }
            for label, chunks in cases.items():
                with self.subTest(form=label):
                    refused(chunks)

        with self.subTest(case="malformed-utf8"):
            streams = {
                "invalid-in-comment": (
                    b": \xff\n\n" + terminal
                ),
                "invalid-in-ignored-field": (
                    b"x-note: \xc3\x28\n\n" + terminal
                ),
                "invalid-in-data": (
                    b"data: \xff\n\n" + terminal
                ),
                "truncated-sequence": (
                    b": \xe2\x82\n\n" + terminal
                ),
                "overlong-sequence": (
                    b": \xc0\xaf\n\n" + terminal
                ),
                "invalid-after-terminal": (
                    terminal + b": \xff\n\n"
                ),
            }
            for label, stream in streams.items():
                with self.subTest(form=label):
                    refused([stream])

        with self.subTest(case="strict-event-json"):
            identity_text = _dumps({
                "id": rid,
                "model": protocol.MODEL,
            })
            response_text = _dumps(response)
            raw_events = {
                "duplicate-type-key": (
                    '{"type":"response.completed",'
                    '"response":' + response_text + ","
                    '"type":"response.completed"}'
                ),
                "nan-in-ignored-field": (
                    '{"type":"response.created",'
                    '"response":' + identity_text + ","
                    '"value":NaN}'
                ),
                "infinity-in-ignored-field": (
                    '{"type":"response.created",'
                    '"response":' + identity_text + ","
                    '"value":Infinity}'
                ),
                "overflow-float": (
                    '{"type":"response.created",'
                    '"response":' + identity_text + ","
                    '"value":1e999}'
                ),
                "lone-surrogate": (
                    '{"type":"response.created",'
                    '"response":' + identity_text + ","
                    '"note":"\\ud800"}'
                ),
                "array-event": "[]",
                "string-event": '"synthetic"',
                "number-event": "123",
                "null-event": "null",
                "missing-type": (
                    '{"response":' + identity_text + "}"
                ),
                "blank-type": '{"type":"  "}',
                "numeric-type": '{"type":123}',
                "truncated-object": "{",
                "trailing-comma": '{"type":"x",}',
                "almost-done": "[DONE",
            }
            for label, raw_text in raw_events.items():
                with self.subTest(form=label):
                    frame = self._frame(
                        raw_text, event=None
                    )
                    if label == "duplicate-type-key":
                        refused([frame])
                    else:
                        refused([frame + terminal])
            with self.subTest(form="finite-and-pair-control"):
                healthy_text = (
                    '{"type":"response.created",'
                    '"response":' + identity_text + ","
                    '"value":1.5,'
                    '"note":"\\ud834\\udd1e"}'
                )
                stream = (
                    self._frame(healthy_text, event=None)
                    + terminal
                )
                result = stream_module.collect_response(
                    [stream], credential=credential
                )
                self._assert_success(
                    result, protocol, raw, rid
                )

        with self.subTest(case="ordinary-json-through-parser"):
            ordinary_cases = {
                "not-json": "not json",
                "truncated": "{",
                "array-raw": "[]",
                "string-raw": '"synthetic"',
                "duplicate-key": '{"a":1,"a":2}',
                "nan-raw": '{"x":NaN}',
                "overflow-raw": '{"x":1e999}',
                "surrogate-raw": '{"x":"\\ud800"}',
            }
            for label, text in ordinary_cases.items():
                with self.subTest(form=label):
                    bad = valid_response(
                        ordinary_text=text
                    )
                    refused([self._terminal(bad)])

        with self.subTest(case="frame-byte-bound"):
            data_bytes = _dumps({
                "type": "response.completed",
                "response": response,
            }).encode("utf-8")

            def padded_frame(pad_count):
                return (
                    b": " + b"p" * pad_count + b"\n"
                    b"id: ignored\n"
                    b"retry: 1\n"
                    b"event: response.completed\n"
                    b"data: " + data_bytes + b"\n"
                    b"\n"
                )

            exact_frame = padded_frame(len(b"padding"))
            frame_limit = len(exact_frame)
            over_frame = padded_frame(
                len(b"padding") + 1
            )
            self.assertEqual(
                len(over_frame), frame_limit + 1
            )
            with self._patched_bounds(
                stream_module,
                stream_bytes=original_stream,
                frame_bytes=frame_limit,
            ):
                with self.subTest(form="frame-exact"):
                    result = (
                        stream_module.collect_response(
                            [exact_frame],
                            credential=credential,
                        )
                    )
                    self._assert_success(
                        result, protocol, raw, rid
                    )
                with self.subTest(form="frame-over-by-one"):
                    refused([over_frame])
                with self.subTest(
                    form="frame-over-split-chunks"
                ):
                    refused(
                        self._pieces(over_frame, 1)
                    )
            self.assertEqual(
                stream_module.MAX_FRAME_BYTES,
                original_frame,
            )
            self.assertEqual(
                stream_module.MAX_STREAM_BYTES,
                original_stream,
            )

        with self.subTest(case="stream-byte-bound"):
            def comment_frame(pad_count):
                return (
                    b": " + b"c" * pad_count + b"\n\n"
                )

            done = self._done()
            exact_stream = (
                comment_frame(len(b"pad"))
                + terminal
                + done
            )
            stream_limit = len(exact_stream)
            over_stream = (
                comment_frame(len(b"pad") + 1)
                + terminal
                + done
            )
            self.assertEqual(
                len(over_stream), stream_limit + 1
            )
            with self._patched_bounds(
                stream_module,
                stream_bytes=stream_limit,
                frame_bytes=stream_limit,
            ):
                with self.subTest(form="stream-exact"):
                    result = (
                        stream_module.collect_response(
                            [exact_stream],
                            credential=credential,
                        )
                    )
                    self._assert_success(
                        result, protocol, raw, rid
                    )
                with self.subTest(
                    form="stream-exact-split"
                ):
                    result = (
                        stream_module.collect_response(
                            self._pieces(exact_stream, 3),
                            credential=credential,
                        )
                    )
                    self._assert_success(
                        result, protocol, raw, rid
                    )
                with self.subTest(form="stream-over-by-one"):
                    refused([over_stream])
                with self.subTest(
                    form="stream-over-last-chunk"
                ):
                    refused([
                        over_stream[:-1],
                        over_stream[-1:],
                    ])
            self.assertEqual(
                stream_module.MAX_STREAM_BYTES,
                original_stream,
            )

        with self.subTest(case="trailing-bytes-counted"):
            base = terminal + self._done()
            trailing = (
                b": " + b"t" * len(b"trailing") + b"\n\n"
            )
            with_trailing = base + trailing
            with self._patched_bounds(
                stream_module,
                stream_bytes=len(with_trailing),
                frame_bytes=len(with_trailing),
            ):
                result = stream_module.collect_response(
                    [with_trailing],
                    credential=credential,
                )
                self._assert_success(
                    result, protocol, raw, rid
                )
            with self._patched_bounds(
                stream_module,
                stream_bytes=len(base),
                frame_bytes=len(with_trailing),
            ):
                refused([with_trailing])
            self.assertEqual(
                stream_module.MAX_STREAM_BYTES,
                original_stream,
            )
            self.assertEqual(
                stream_module.MAX_FRAME_BYTES,
                original_frame,
            )

    # 5 ------------------------------------------------------------

    def test_synthetic_credential_cannot_escape_reconstruction(
        self,
    ):
        stream_module, protocol, credential, raw, rid = (
            self._scenario()
        )
        escaped = self._unicode_escape(credential)
        placeholder = "ESCAPED-PLACEHOLDER-TOKEN"

        def refuse_error(chunks, secret=None):
            self._assert_refused(
                stream_module,
                protocol,
                chunks,
                credential if secret is None else secret,
                "error",
            )

        def valid_response(response_id=rid, **kwargs):
            if "raw" not in kwargs and not any(
                key in kwargs
                for key in ("ordinary_text", "parts", "output")
            ):
                kwargs["raw"] = copy.deepcopy(raw)
            return self._make_response(
                protocol, response_id, **kwargs
            )

        def collect_ok(
            chunks,
            secret=None,
            expected_raw=None,
            response_id=rid,
        ):
            active = (
                credential if secret is None else secret
            )
            result = stream_module.collect_response(
                chunks, credential=active
            )
            self._assert_success(
                result,
                protocol,
                raw if expected_raw is None else expected_raw,
                response_id,
            )
            self._assert_credential_absent(
                result, active
            )
            return result

        terminal = self._terminal(valid_response())

        with self.subTest(case="plain-frame-data"):
            plain_delta = {
                "type": "response.output_text.delta",
                "delta": credential,
            }
            with self.subTest(form="plain-delta"):
                refuse_error([
                    self._frame(plain_delta) + terminal
                ])
            with self.subTest(form="plain-ignored-event"):
                event = {
                    "type": "response.custom",
                    "metadata": {"note": credential},
                }
                refuse_error([
                    self._frame(event) + terminal
                ])
            with self.subTest(form="plain-data-line"):
                stream = (
                    (
                        "data: " + credential + "\n\n"
                    ).encode("utf-8")
                    + terminal
                )
                refuse_error([stream])
            with self.subTest(form="plain-split-on-wire"):
                stream = (
                    self._frame(plain_delta) + terminal
                )
                refuse_error(self._pieces(stream, 1))

        with self.subTest(case="escaped-decoded-fields"):
            identity_text = _dumps({
                "id": rid,
                "model": protocol.MODEL,
            })
            metadata_text = (
                '{"type":"response.created",'
                '"response":' + identity_text + ","
                '"metadata":"' + escaped + '"}'
            )
            self.assertFalse(
                credential in metadata_text,
                "escaped fixture precondition failed",
            )
            with self.subTest(form="metadata-value"):
                refuse_error([
                    self._frame(
                        metadata_text, event=None
                    )
                    + terminal
                ])
            key_text = (
                '{"type":"response.custom","'
                + escaped
                + '":"clean"}'
            )
            with self.subTest(form="decoded-key"):
                refuse_error([
                    self._frame(key_text, event=None)
                    + terminal
                ])
            with self.subTest(form="reasoning"):
                output = [
                    {
                        "type": "reasoning",
                        "summary": [{
                            "type": "summary_text",
                            "text": placeholder,
                        }],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{
                            "type": "output_text",
                            "text": _dumps(raw),
                        }],
                    },
                ]
                response_text = _dumps(
                    valid_response(output=output)
                ).replace(placeholder, escaped)
                event_text = (
                    '{"type":"response.completed",'
                    '"response":' + response_text + "}"
                )
                self.assertFalse(
                    credential in event_text,
                    "escaped fixture precondition failed",
                )
                refuse_error([
                    self._frame(event_text, event=None)
                ])
            with self.subTest(form="error-note-field"):
                response_text = _dumps(
                    valid_response(
                        extra={
                            "provider_error_note": (
                                placeholder
                            )
                        }
                    )
                ).replace(placeholder, escaped)
                event_text = (
                    '{"type":"response.completed",'
                    '"response":' + response_text + "}"
                )
                refuse_error([
                    self._frame(event_text, event=None)
                ])
            with self.subTest(form="error-event-text"):
                event = {
                    "type": "error",
                    "message": credential,
                }
                refuse_error([
                    self._frame(event, event="error")
                    + terminal
                ])

        with self.subTest(case="terminal-fields"):
            with self.subTest(form="id-exact"):
                refuse_error([
                    self._terminal(
                        valid_response(credential)
                    )
                ])
            with self.subTest(form="id-contains"):
                refuse_error([
                    self._terminal(valid_response(
                        "resp-" + credential + "-tail"
                    ))
                ])
            with self.subTest(form="model-exact"):
                refuse_error([
                    self._terminal(valid_response(
                        model=credential
                    ))
                ])
            with self.subTest(form="ordinary-value"):
                raw_bad = copy.deepcopy(raw)
                raw_bad["review_notes"] = [credential]
                refuse_error([
                    self._terminal(
                        valid_response(raw=raw_bad)
                    )
                ])
            with self.subTest(form="ordinary-key"):
                raw_bad = copy.deepcopy(raw)
                raw_bad[credential] = "clean"
                refuse_error([
                    self._terminal(
                        valid_response(raw=raw_bad)
                    )
                ])

        with self.subTest(case="reconstruction-boundaries"):
            raw_bad = copy.deepcopy(raw)
            raw_bad["sections"][0]["text"] = (
                "prefix " + credential + " suffix"
            )
            inner_plain = _dumps(raw_bad)
            start = inner_plain.index(credential)
            middle = start + len(credential) // 2
            parts = [
                inner_plain[:middle],
                inner_plain[middle:],
            ]
            for part in parts:
                self.assertFalse(
                    credential in part,
                    "split fixture precondition failed",
                )
            with self.subTest(form="split-output-parts"):
                refuse_error([
                    self._terminal(
                        valid_response(parts=parts)
                    )
                ])
            with self.subTest(form="split-output-messages"):
                output = [
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{
                            "type": "output_text",
                            "text": parts[0],
                        }],
                    },
                    {
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{
                            "type": "output_text",
                            "text": parts[1],
                        }],
                    },
                ]
                refuse_error([
                    self._terminal(
                        valid_response(output=output)
                    )
                ])
            inner_escaped = inner_plain.replace(
                credential, escaped
            )
            self.assertFalse(
                credential in inner_escaped,
                "inner escape precondition failed",
            )
            with self.subTest(form="inner-json-escapes"):
                refuse_error([
                    self._terminal(valid_response(
                        ordinary_text=inner_escaped
                    ))
                ])
            with self.subTest(
                form="inner-escapes-split-parts"
            ):
                escape_start = inner_escaped.index(
                    escaped
                )
                cut = escape_start + 3
                split_parts = [
                    inner_escaped[:cut],
                    inner_escaped[cut:],
                ]
                refuse_error([
                    self._terminal(valid_response(
                        parts=split_parts
                    ))
                ])
            with self.subTest(
                form="inner-escaped-raw-key"
            ):
                raw_key = copy.deepcopy(raw)
                raw_key[credential] = "clean"
                inner_key = _dumps(raw_key).replace(
                    credential, escaped
                )
                self.assertFalse(
                    credential in inner_key,
                    "inner key precondition failed",
                )
                refuse_error([
                    self._terminal(valid_response(
                        ordinary_text=inner_key
                    ))
                ])

        with self.subTest(case="similar-but-not-equal"):
            similars = {
                "last-char-changed": (
                    credential[:-1] + "X"
                ),
                "lowercase": credential.lower(),
                "hyphen-as-space": credential.replace(
                    "-", " ", 1
                ),
                "prefix-only": credential[:10],
                "middle-changed": (
                    credential[:5]
                    + "X"
                    + credential[6:]
                ),
            }
            for label, similar in similars.items():
                with self.subTest(form=label):
                    self.assertFalse(
                        credential in similar,
                        "similar fixture precondition "
                        "failed",
                    )
                    raw_ok = copy.deepcopy(raw)
                    raw_ok["sections"][0]["text"] = (
                        raw["sections"][0]["text"]
                        + ' "'
                        + similar
                        + '" '
                    )
                    raw_ok["review_notes"] = [similar]
                    collect_ok(
                        [self._terminal(
                            valid_response(raw=raw_ok)
                        )],
                        expected_raw=raw_ok,
                    )
            with self.subTest(form="similar-split-parts"):
                raw_ok = copy.deepcopy(raw)
                raw_ok["review_notes"] = [
                    similars["last-char-changed"]
                ]
                inner = _dumps(raw_ok)
                cut = len(inner) // 2
                collect_ok(
                    [self._terminal(valid_response(
                        parts=[inner[:cut], inner[cut:]]
                    ))],
                    expected_raw=raw_ok,
                )
            with self.subTest(
                form="arabic-quotes-whitespace"
            ):
                collect_ok([terminal])

        with self.subTest(case="clean-failures-retain-kind"):
            with self.subTest(form="clean-wrong-model"):
                self._assert_refused(
                    stream_module,
                    protocol,
                    [self._terminal(valid_response(
                        model="other-model"
                    ))],
                    credential,
                    "invalid_schema",
                )
            with self.subTest(form="clean-incomplete"):
                response = valid_response(
                    status="incomplete",
                    incomplete_value={
                        "reason": "max_output_tokens"
                    },
                )
                self._assert_refused(
                    stream_module,
                    protocol,
                    [self._terminal(
                        response, "response.incomplete"
                    )],
                    credential,
                    "incomplete",
                    expected_diagnostic={
                        "version": 1,
                        "classifier": "incomplete_details",
                        "response_status": "incomplete",
                        "message_status": None,
                        "response_id": None,
                        "reason": "max_output_tokens",
                        "usage": {
                            "input_tokens": None,
                            "output_tokens": None,
                            "total_tokens": None,
                            "reasoning_tokens": None,
                        },
                    },
                )
            with self.subTest(form="clean-refusal"):
                output = [{
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{
                        "type": "refusal",
                        "refusal": "synthetic refusal",
                    }],
                }]
                self._assert_refused(
                    stream_module,
                    protocol,
                    [self._terminal(
                        valid_response(output=output)
                    )],
                    credential,
                    "refusal",
                )
            with self.subTest(form="clean-duplicate"):
                self._assert_refused(
                    stream_module,
                    protocol,
                    [terminal + terminal],
                    credential,
                    "unknown",
                )

        with self.subTest(case="invalid-credential"):
            class TrackingIterable:
                def __init__(self, seen):
                    self.seen = seen

                def __iter__(self):
                    self.seen.append(True)
                    return iter(())

            bad_values = {
                "none": None,
                "bytes": credential.encode("utf-8"),
                "bytearray": bytearray(
                    credential.encode("utf-8")
                ),
                "integer": len(credential),
                "boolean": True,
                "list": [credential],
                "empty": "",
                "whitespace": " \t\r\n",
                "surrogate-suffix": (
                    credential + chr(0xD800)
                ),
                "surrogate-only": chr(0xDFFF),
            }
            for label, bad in bad_values.items():
                with self.subTest(form=label):
                    seen = []
                    self._assert_invalid_credential(
                        stream_module,
                        protocol,
                        TrackingIterable(seen),
                        bad,
                    )
                    self.assertEqual(seen, [])

        with self.subTest(case="public-literal-collisions"):
            probe = protocol.MetaProtocolError("error")
            public_message = str(probe)
            self.assertEqual(
                public_message, _REFUSED_MESSAGE
            )
            error_outcome = probe.outcome
            self.assertEqual(error_outcome, "error")
            start = public_message.index("response")
            substring = public_message[start:]
            collisions = (
                ("full-message", public_message),
                ("error-outcome", error_outcome),
                ("public-substring", substring),
            )
            for label, secret in collisions:
                with self.subTest(form=label):
                    clean = self._make_response(
                        protocol,
                        rid,
                        copy.deepcopy(raw),
                        include_error=False,
                        include_incomplete=False,
                    )
                    clean_frame = self._terminal(clean)
                    clean_text = clean_frame.decode(
                        "utf-8"
                    )
                    self.assertFalse(
                        secret in clean_text,
                        "collision clean fixture "
                        "precondition failed",
                    )
                    with self.subTest(
                        placement="clean-success"
                    ):
                        collect_ok(
                            [clean_frame], secret=secret
                        )
                    with self.subTest(
                        placement="received-metadata"
                    ):
                        event = {
                            "type": "response.custom",
                            "note": secret,
                        }
                        refuse_error(
                            [
                                self._frame(event)
                                + clean_frame
                            ],
                            secret=secret,
                        )
                    with self.subTest(
                        placement="received-ordinary"
                    ):
                        raw_hit = copy.deepcopy(raw)
                        raw_hit["review_notes"] = [secret]
                        hit = self._make_response(
                            protocol,
                            rid,
                            raw_hit,
                            include_error=False,
                            include_incomplete=False,
                        )
                        refuse_error(
                            [self._terminal(hit)],
                            secret=secret,
                        )
                    with self.subTest(
                        placement="received-terminal-id"
                    ):
                        hit = self._make_response(
                            protocol,
                            secret,
                            copy.deepcopy(raw),
                            include_error=False,
                            include_incomplete=False,
                        )
                        refuse_error(
                            [self._terminal(hit)],
                            secret=secret,
                        )

        with self.subTest(case="final-success-only-matches"):
            clean = valid_response(
                include_error=False,
                include_incomplete=False,
            )
            event_object = {
                "type": "response.completed",
                "response": clean,
            }
            clean_frame = self._terminal(clean)
            self.assertEqual(
                clean_frame,
                self._frame(
                    event_object,
                    event="response.completed",
                ),
            )
            frame_text = clean_frame.decode("utf-8")
            data_text = _dumps(event_object)
            decoded_event = protocol._strict_json_loads(
                data_text
            )
            self.assertEqual(decoded_event, event_object)
            decoded_response = decoded_event["response"]
            self.assertEqual(decoded_response, clean)
            ordinary_text = protocol._collect_ordinary_text(
                decoded_response
            )
            parsed_raw = protocol._strict_json_loads(
                ordinary_text
            )
            self.assertEqual(parsed_raw, raw)
            expected = protocol.parse_response(
                _dumps(decoded_response).encode("utf-8")
            )
            self._assert_success(expected, protocol, raw, rid)
            self.assertEqual(expected["raw"], parsed_raw)

            def secret_in_tree(value, candidate):
                stack = [value]
                while stack:
                    item = stack.pop()
                    if isinstance(item, str):
                        if candidate in item:
                            return True
                    elif isinstance(item, dict):
                        stack.extend(item.keys())
                        stack.extend(item.values())
                    elif isinstance(item, (list, tuple)):
                        stack.extend(item)
                return False

            def assert_absent_before_final(candidate):
                self.assertNotIn(
                    candidate.encode("utf-8"), clean_frame
                )
                self.assertNotIn(candidate, frame_text)
                self.assertNotIn(candidate, data_text)
                self.assertFalse(
                    secret_in_tree(decoded_event, candidate)
                )
                self.assertFalse(
                    secret_in_tree(
                        decoded_response, candidate
                    )
                )
                self.assertNotIn(candidate, ordinary_text)
                self.assertFalse(
                    secret_in_tree(parsed_raw, candidate)
                )
                self._assert_credential_absent(
                    decoded_event, candidate
                )
                self._assert_credential_absent(
                    parsed_raw, candidate
                )

            cases = (
                ("final-tree-key", "model_name", "model_namE"),
                (
                    "final-serialized-only",
                    '"outcome"',
                    '"outcomE"',
                ),
            )
            for label, secret, healthy in cases:
                with self.subTest(form=label):
                    assert_absent_before_final(secret)
                    if label == "final-tree-key":
                        self.assertIn(secret, expected)
                        self.assertTrue(
                            secret_in_tree(expected, secret)
                        )
                    else:
                        self.assertIn("outcome", expected)
                        self.assertFalse(
                            secret_in_tree(expected, secret)
                        )
                    for ensure_ascii in (False, True):
                        serialized = json.dumps(
                            expected,
                            ensure_ascii=ensure_ascii,
                        )
                        self.assertIn(secret, serialized)
                    with self.subTest(
                        placement="final-only-refused"
                    ):
                        refuse_error(
                            [clean_frame], secret=secret
                        )
                    with self.subTest(
                        placement="case-different-success"
                    ):
                        assert_absent_before_final(healthy)
                        self._assert_credential_absent(
                            expected, healthy
                        )
                        result = collect_ok(
                            [clean_frame], secret=healthy
                        )
                        self.assertEqual(result, expected)













    def test_owned_raw_result_preserves_domain_invalid_content(self):
        stream_module, protocol, credential, raw, rid = (
            self._scenario()
        )
        fixture_snapshot = copy.deepcopy(raw)
        credential_snapshot = credential
        response_id_snapshot = rid
        model_before = protocol.MODEL
        effort_before = protocol.EFFORT
        max_response_before = protocol.MAX_RESPONSE_BYTES
        source_prompt_before = protocol.SOURCE_PROMPT
        source_schema_before = copy.deepcopy(
            protocol.SOURCE_SCHEMA
        )
        question_schema_before = copy.deepcopy(
            protocol.QUESTION_SCHEMA_V2
        )
        max_stream_before = stream_module.MAX_STREAM_BYTES
        max_frame_before = stream_module.MAX_FRAME_BYTES
        delta_sentinel = "DELTA-ONLY-TEXT-7319"
        reasoning_sentinel = "REASONING-ONLY-TEXT-7319"
        terminal_reasoning_sentinel = (
            "TERMINAL-REASONING-TEXT-7319"
        )
        mutated_sentinel = "MUTATED-FIRST-RESULT-7319"
        outside_sentinels = (
            delta_sentinel,
            reasoning_sentinel,
            terminal_reasoning_sentinel,
        )

        def collect(chunks):
            return stream_module.collect_response(
                chunks, credential=credential
            )

        def assert_ordinary_tree(value):
            if type(value) is dict:
                for key, child in value.items():
                    self.assertIs(type(key), str)
                    assert_ordinary_tree(child)
            elif type(value) is list:
                for child in value:
                    assert_ordinary_tree(child)
            elif value is None:
                return
            elif type(value) in (str, int, float, bool):
                return
            else:
                self.fail(
                    "non-ordinary value in returned tree"
                )

        def assert_sentinels_absent(value, sentinels):
            stack = [value]
            while stack:
                item = stack.pop()
                if isinstance(item, str):
                    for sentinel in sentinels:
                        if sentinel in item:
                            self.fail(
                                "outside text entered result"
                            )
                elif isinstance(item, dict):
                    stack.extend(item.keys())
                    stack.extend(item.values())
                elif isinstance(item, (list, tuple)):
                    stack.extend(item)
            for ensure_ascii in (False, True):
                serialized = json.dumps(
                    value, ensure_ascii=ensure_ascii
                )
                for sentinel in sentinels:
                    self.assertNotIn(sentinel, serialized)

        def assert_packaged_unchanged():
            self.assertEqual(protocol.MODEL, model_before)
            self.assertEqual(protocol.EFFORT, effort_before)
            self.assertEqual(
                protocol.MAX_RESPONSE_BYTES,
                max_response_before,
            )
            self.assertEqual(
                protocol.SOURCE_PROMPT,
                source_prompt_before,
            )
            self.assertEqual(
                protocol.SOURCE_SCHEMA,
                source_schema_before,
            )
            self.assertEqual(
                protocol.QUESTION_SCHEMA_V2,
                question_schema_before,
            )
            self.assertEqual(
                stream_module.MAX_STREAM_BYTES,
                max_stream_before,
            )
            self.assertEqual(
                stream_module.MAX_FRAME_BYTES,
                max_frame_before,
            )
            self.assertEqual(raw, fixture_snapshot)

        def build_domain_raw():
            value = copy.deepcopy(raw)
            section = value["sections"][0]
            section["kind"] = "not-a-domain-kind"
            section["review_reasons"] = [
                123,
                {"cue": "not a domain reason string"},
                None,
            ]
            section["extra_nested"] = {
                "approved": True,
                "approval_claim": "raw claim only",
                "values": [
                    0,
                    1,
                    1.0,
                    2.5,
                    True,
                    False,
                    None,
                    "",
                    [],
                    {},
                ],
                "decomposed": "e" + chr(0x0301),
                "ligature": chr(0xFB01),
                "digits": chr(0x0661) + chr(0x0662),
                "joined": "a" + chr(0x200D) + "b",
                "padded": "  keep" + chr(9) + "spaces  ",
                "crlf_inside": (
                    "line1" + chr(13) + chr(10) + "line2"
                ),
                "": "empty-key-value",
            }
            value["sections"].append({"section_id": "s1"})
            value["sections"].append({
                "section_id": "s-extra",
                "kind": 42,
                "text": None,
                "review_reasons": "not-a-list",
                "extra_deep": {
                    "approved": False,
                    "items": [[], {"nested": [None]}],
                },
            })
            value["page_continues"] = "not-a-bool"
            value["review_notes"] = [
                "APPROVED FOR PUBLICATION (raw claim)",
                42,
                {"teacher_approved": True},
            ]
            value["approved"] = True
            value["approval"] = {
                "approved": True,
                "teacher_approved": True,
                "approved_by": "raw-claim-only",
                "permission": "publish",
            }
            value["domain_extra_number"] = 9007199254740993
            decomposed_key = (
                "cle" + chr(0x0301) + "_extra"
            )
            value[decomposed_key] = "decomposed-key-value"
            return value

        def build_stream(expected_raw, split_parts=False):
            if split_parts:
                inner = _dumps(copy.deepcopy(expected_raw))
                cut = len(inner) // 2
                response = self._make_response(
                    protocol,
                    rid,
                    parts=[inner[:cut], inner[cut:]],
                )
            else:
                response = self._make_response(
                    protocol,
                    rid,
                    copy.deepcopy(expected_raw),
                )
            reasoning_item = {
                "type": "reasoning",
                "summary": [{
                    "type": "summary_text",
                    "text": terminal_reasoning_sentinel,
                }],
                "content": [{
                    "type": "reasoning_text",
                    "text": terminal_reasoning_sentinel,
                }],
            }
            response["output"].insert(0, reasoning_item)
            other_raw = {
                "sections": [{
                    "section_id": "delta-only",
                    "kind": "body",
                    "text": delta_sentinel,
                    "review_reasons": [],
                }],
                "page_continues": False,
                "review_notes": [],
                "approved": True,
            }
            created = {
                "type": "response.created",
                "response": {
                    "id": rid,
                    "model": protocol.MODEL,
                    "status": "in_progress",
                },
            }
            delta = {
                "type": "response.output_text.delta",
                "delta": delta_sentinel + _dumps(other_raw),
            }
            reasoning_delta = {
                "type": (
                    "response.reasoning_summary_text.delta"
                ),
                "delta": reasoning_sentinel,
            }
            text_done = {
                "type": "response.output_text.done",
                "text": _dumps(other_raw),
            }
            stream = (
                self._frame(created)
                + self._frame(delta)
                + self._frame(reasoning_delta)
                + self._frame(text_done)
                + self._terminal(response)
                + self._done()
            )
            expected_text = _dumps(expected_raw)
            for sentinel in outside_sentinels:
                self.assertIn(
                    sentinel.encode("utf-8"), stream
                )
                self.assertNotIn(sentinel, expected_text)
            return response, stream

        def collect_ok(chunks, expected_raw, response):
            expected = protocol.parse_response(
                _dumps(response).encode("utf-8")
            )
            self.assertEqual(expected["raw"], expected_raw)
            result = collect(chunks)
            self.assertIs(type(result), dict)
            self._assert_success(
                result, protocol, expected_raw, rid
            )
            self.assertEqual(result, expected)
            self.assertIs(type(result["raw"]), dict)
            assert_ordinary_tree(result)
            assert_sentinels_absent(
                result, outside_sentinels
            )
            self._assert_credential_absent(
                result, credential
            )
            self.assertNotIn("approved", result)
            self.assertNotIn("approval", result)
            self.assertNotIn("teacher_approved", result)
            return result

        with self.subTest(
            case="domain-invalid-raw-preserved-exactly"
        ):
            expected_raw = build_domain_raw()
            expected_snapshot = copy.deepcopy(expected_raw)
            response, stream = build_stream(expected_raw)
            response_snapshot = copy.deepcopy(response)
            result = collect_ok(
                [stream], expected_raw, response
            )
            self.assertEqual(
                result["raw"], expected_snapshot
            )
            self.assertEqual(expected_raw, expected_snapshot)
            self.assertEqual(response, response_snapshot)
            returned = result["raw"]
            section = returned["sections"][0]
            nested = section["extra_nested"]
            fixture_text = (
                fixture_snapshot["sections"][0]["text"]
            )
            self.assertEqual(section["text"], fixture_text)
            self.assertEqual(
                section["text"].encode("utf-8"),
                fixture_text.encode("utf-8"),
            )
            self.assertEqual(
                [ord(char) for char in section["text"]],
                [ord(char) for char in fixture_text],
            )
            self.assertTrue(section["text"].startswith("  "))
            self.assertTrue(section["text"].endswith("  "))
            self.assertIn(chr(10), section["text"])
            self.assertEqual(
                nested["decomposed"], "e" + chr(0x0301)
            )
            self.assertEqual(
                [
                    ord(char)
                    for char in nested["decomposed"]
                ],
                [0x65, 0x0301],
            )
            self.assertNotEqual(
                nested["decomposed"], chr(0x00E9)
            )
            self.assertEqual(
                [ord(char) for char in nested["ligature"]],
                [0xFB01],
            )
            self.assertNotEqual(nested["ligature"], "fi")
            self.assertEqual(
                [ord(char) for char in nested["digits"]],
                [0x0661, 0x0662],
            )
            self.assertNotEqual(nested["digits"], "12")
            self.assertEqual(
                [ord(char) for char in nested["joined"]],
                [0x61, 0x200D, 0x62],
            )
            self.assertNotEqual(nested["joined"], "ab")
            self.assertEqual(
                nested["padded"],
                "  keep" + chr(9) + "spaces  ",
            )
            self.assertNotEqual(
                nested["padded"], nested["padded"].strip()
            )
            self.assertEqual(
                nested["crlf_inside"],
                "line1" + chr(13) + chr(10) + "line2",
            )
            self.assertEqual(nested[""], "empty-key-value")
            decomposed_key = (
                "cle" + chr(0x0301) + "_extra"
            )
            precomposed_key = (
                "cl" + chr(0x00E9) + "_extra"
            )
            self.assertEqual(
                returned[decomposed_key],
                "decomposed-key-value",
            )
            self.assertNotIn(precomposed_key, returned)
            values = nested["values"]
            self.assertIs(type(values), list)
            self.assertIs(type(values[0]), int)
            self.assertEqual(values[0], 0)
            self.assertIs(type(values[1]), int)
            self.assertEqual(values[1], 1)
            self.assertIs(type(values[2]), float)
            self.assertEqual(values[2], 1.0)
            self.assertIs(type(values[3]), float)
            self.assertEqual(values[3], 2.5)
            self.assertIs(values[4], True)
            self.assertIs(values[5], False)
            self.assertIs(values[6], None)
            self.assertEqual(values[7], "")
            self.assertEqual(values[8], [])
            self.assertEqual(values[9], {})
            self.assertIs(
                type(returned["domain_extra_number"]), int
            )
            self.assertEqual(
                returned["domain_extra_number"],
                9007199254740993,
            )
            self.assertEqual(
                section["kind"], "not-a-domain-kind"
            )
            self.assertIs(
                type(section["review_reasons"][0]), int
            )
            self.assertEqual(
                returned["sections"][1],
                {"section_id": "s1"},
            )
            self.assertEqual(
                set(returned["sections"][1].keys()),
                {"section_id"},
            )
            self.assertEqual(
                [
                    item["section_id"]
                    for item in returned["sections"]
                ],
                ["s1", "s1", "s-extra"],
            )
            self.assertIs(
                type(returned["sections"][2]["kind"]), int
            )
            self.assertIs(
                returned["sections"][2]["text"], None
            )
            self.assertEqual(
                returned["sections"][2]["review_reasons"],
                "not-a-list",
            )
            self.assertIs(
                type(returned["page_continues"]), str
            )
            self.assertEqual(
                returned["page_continues"], "not-a-bool"
            )
            self.assertIs(returned["approved"], True)
            self.assertIs(
                returned["approval"]["teacher_approved"],
                True,
            )
            self.assertEqual(
                returned["review_notes"][0],
                "APPROVED FOR PUBLICATION (raw claim)",
            )
            self.assertEqual(
                set(result.keys()),
                {
                    "raw",
                    "model_name",
                    "response_id",
                    "outcome",
                },
            )
            assert_packaged_unchanged()

        with self.subTest(
            case="genuine-terminal-split-parts-only"
        ):
            expected_raw = build_domain_raw()
            expected_snapshot = copy.deepcopy(expected_raw)
            response, stream = build_stream(
                expected_raw, split_parts=True
            )
            message = response["output"][1]
            self.assertEqual(message["type"], "message")
            self.assertEqual(len(message["content"]), 2)
            result = collect_ok(
                self._pieces(stream, 5),
                expected_raw,
                response,
            )
            self.assertEqual(
                result["raw"], expected_snapshot
            )
            self.assertEqual(expected_raw, expected_snapshot)
            assert_packaged_unchanged()

        with self.subTest(
            case="independent-calls-mutation-isolation"
        ):
            expected_raw = build_domain_raw()
            expected_snapshot = copy.deepcopy(expected_raw)
            response, stream = build_stream(expected_raw)
            response_snapshot = copy.deepcopy(response)
            chunks = self._pieces(stream, 7)
            chunks_snapshot = list(chunks)
            stream_snapshot = bytes(stream)
            first = collect_ok(
                chunks, expected_raw, response
            )
            retained = collect_ok(
                chunks, expected_raw, response
            )
            retained_snapshot = copy.deepcopy(retained)
            self.assertIsNot(first, retained)
            self.assertIsNot(first["raw"], retained["raw"])
            self.assertIsNot(
                first["raw"]["sections"],
                retained["raw"]["sections"],
            )
            self.assertIsNot(
                first["raw"]["sections"][0],
                retained["raw"]["sections"][0],
            )
            self.assertIsNot(
                first["raw"]["sections"][0]["extra_nested"],
                retained["raw"]["sections"][0]["extra_nested"],
            )
            first_values = (
                first["raw"]["sections"][0]["extra_nested"][
                    "values"
                ]
            )
            retained_values = (
                retained["raw"]["sections"][0]["extra_nested"][
                    "values"
                ]
            )
            self.assertIsNot(first_values, retained_values)
            self.assertIsNot(
                first["raw"]["approval"],
                retained["raw"]["approval"],
            )
            self.assertIsNot(first["raw"], expected_raw)
            self.assertIsNot(retained["raw"], expected_raw)
            self.assertIsNot(
                first["raw"]["sections"],
                expected_raw["sections"],
            )
            self.assertIsNot(
                retained["raw"]["sections"],
                expected_raw["sections"],
            )
            first["raw"]["sections"][0]["text"] = (
                mutated_sentinel
            )
            first_values.append(mutated_sentinel)
            first["raw"]["sections"][0]["extra_nested"][
                "new_key"
            ] = {"mutated": [mutated_sentinel]}
            first["raw"]["sections"].append({
                "section_id": mutated_sentinel
            })
            first["raw"]["approval"]["teacher_approved"] = (
                False
            )
            first["raw"]["review_notes"].append(
                mutated_sentinel
            )
            del first["raw"]["domain_extra_number"]
            first["outcome"] = mutated_sentinel
            self.assertNotEqual(
                first["raw"], retained["raw"]
            )
            self.assertEqual(retained, retained_snapshot)
            self.assertEqual(
                retained["raw"], expected_snapshot
            )
            assert_sentinels_absent(
                retained, (mutated_sentinel,)
            )
            self.assertEqual(expected_raw, expected_snapshot)
            self.assertEqual(response, response_snapshot)
            self.assertEqual(chunks, chunks_snapshot)
            for got, original in zip(
                chunks, chunks_snapshot
            ):
                self.assertIs(got, original)
            self.assertEqual(
                b"".join(chunks), stream_snapshot
            )
            self.assertEqual(raw, fixture_snapshot)
            assert_packaged_unchanged()
            fresh = collect_ok(
                chunks, expected_raw, response
            )
            self.assertEqual(fresh, retained_snapshot)
            self.assertEqual(
                fresh["raw"], expected_snapshot
            )
            assert_sentinels_absent(
                fresh, (mutated_sentinel,)
            )
            self.assertIsNot(fresh, retained)
            self.assertIsNot(fresh["raw"], retained["raw"])
            self.assertIsNot(
                fresh["raw"]["sections"],
                retained["raw"]["sections"],
            )
            self.assertIsNot(fresh["raw"], first["raw"])
            self.assertEqual(retained, retained_snapshot)
            self.assertEqual(chunks, chunks_snapshot)
            self.assertEqual(response, response_snapshot)
            assert_packaged_unchanged()
            fresh_values = (
                fresh["raw"]["sections"][0]["extra_nested"][
                    "values"
                ]
            )
            fresh_values[0] = mutated_sentinel
            fresh["raw"]["sections"].clear()
            self.assertEqual(retained, retained_snapshot)
            another = collect_ok(
                chunks, expected_raw, response
            )
            self.assertEqual(
                another["raw"], expected_snapshot
            )
            assert_sentinels_absent(
                another, (mutated_sentinel,)
            )
            self.assertEqual(retained, retained_snapshot)
            self.assertEqual(chunks, chunks_snapshot)
            self.assertEqual(
                b"".join(chunks), stream_snapshot
            )
            assert_packaged_unchanged()

        with self.subTest(case="other-domain-invalid-shapes"):
            variants = {
                "empty-object": {},
                "approval-claims-only": {
                    "approved": True,
                    "teacher_approved": True,
                    "permission": "publish",
                },
                "unrelated-shape": {
                    "not_source_schema": {
                        "deep": [None, True, {"x": []}]
                    },
                    "count": 3,
                },
                "wrong-top-level-types": {
                    "sections": "not-a-list",
                    "page_continues": 7,
                    "review_notes": {"not": ["list"]},
                },
                "nulls-and-empty-containers": {
                    "sections": [],
                    "page_continues": None,
                    "review_notes": [],
                    "extra": [[], {}, [[]], {"": None}],
                },
            }
            for label, variant in variants.items():
                with self.subTest(form=label):
                    expected_raw = copy.deepcopy(variant)
                    snapshot = copy.deepcopy(variant)
                    response, stream = build_stream(
                        expected_raw
                    )
                    result = collect_ok(
                        [stream], expected_raw, response
                    )
                    self.assertEqual(
                        result["raw"], snapshot
                    )
                    self.assertEqual(
                        expected_raw, snapshot
                    )
                    self.assertEqual(variant, snapshot)

        assert_packaged_unchanged()
        credential_again, raw_again, rid_again = (
            self._fresh_inputs()
        )
        self.assertEqual(
            credential_again, credential_snapshot
        )
        self.assertEqual(raw_again, fixture_snapshot)
        self.assertEqual(rid_again, response_id_snapshot)
        self.assertEqual(raw, fixture_snapshot)
