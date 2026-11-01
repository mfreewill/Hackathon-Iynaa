"""Iterator-boundary regressions for the Meta stream collector."""
import json
import unittest
from pathlib import Path

from teacher_demo import meta_protocol
from teacher_demo.meta_stream import collect_response

_FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "meta_stream_inputs.json")
    .read_text(encoding="utf-8")
)
_CREDENTIAL = _FIXTURE["synthetic_credential"]
_RAW = _FIXTURE["raw"]
_RESPONSE_ID = _FIXTURE["response_id"]
_REFUSED = "meta protocol response refused"
_DONE = b"data: [DONE]\n\n"


def _envelope(status, output):
    return {"id": _RESPONSE_ID, "object": "response",
            "model": meta_protocol.MODEL, "status": status,
            "error": None, "incomplete_details": None, "output": output}


def _message(parts):
    return [{"type": "message", "role": "assistant",
             "status": "completed", "content": parts}]


def _completed(text=None):
    if text is None:
        text = json.dumps(_RAW, ensure_ascii=False)
    return _envelope("completed",
                     _message([{"type": "output_text", "text": text}]))


def _terminal(event_type, envelope):
    event = {"type": event_type, "response": envelope}
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return b"data: " + data.encode("utf-8") + b"\n\n"


_COMPLETED_FRAME = _terminal("response.completed", _completed())


class _IterRaise:
    """Iterable whose iter() raises the supplied error."""

    def __init__(self, error):
        self.error = error
        self.iter_calls = 0

    def __iter__(self):
        self.iter_calls += 1
        raise self.error


def _raising(prefix, error, state):
    for chunk in prefix:
        state["yielded"] += 1
        yield chunk
    state["raised"] = True
    raise error


class MetaStreamIterationTest(unittest.TestCase):

    def _assert_refused(self, ctx, outcome, expected_diagnostic=None):
        err = ctx.exception
        self.assertIs(type(err), meta_protocol.MetaProtocolError)
        self.assertEqual(str(err), _REFUSED)
        self.assertEqual(err.args, (_REFUSED,))
        if expected_diagnostic is None:
            # Value-free check so a failure never echoes an input outcome.
            self.assertTrue(err.__dict__ == {"outcome": outcome},
                            "non-canonical outcome")
            return
        # Diagnosed branch stays value-free for the same reason.
        self.assertTrue(outcome == "incomplete",
                        "diagnostic on non-incomplete outcome")
        self.assertTrue(err.__dict__.get("outcome") == outcome,
                        "non-canonical outcome")
        self.assertTrue(
            set(err.__dict__) == {"outcome", "diagnostic"},
            "non-canonical diagnosed error fields")
        diagnostic = err.__dict__["diagnostic"]
        self.assertIs(type(diagnostic), dict)
        self.assertTrue(
            _CREDENTIAL not in json.dumps(
                diagnostic, ensure_ascii=False, default=str),
            "diagnostic retained the credential")
        self.assertTrue(
            set(diagnostic) == {"version", "classifier",
                                "response_status", "message_status",
                                "response_id", "reason", "usage"},
            "non-canonical diagnostic fields")
        usage = diagnostic.get("usage")
        self.assertIs(type(usage), dict)
        self.assertTrue(
            set(usage) == {"input_tokens", "output_tokens",
                           "total_tokens", "reasoning_tokens"},
            "non-canonical diagnostic usage fields")
        self.assertIs(type(diagnostic.get("version")), int)
        for value in usage.values():
            self.assertTrue(value is None or type(value) is int,
                            "non-canonical diagnostic usage counter")
        self.assertIsNone(err.__cause__)
        self.assertIsNone(err.__context__)
        self.assertTrue(diagnostic == expected_diagnostic,
                        "non-canonical diagnostic")

    def test_iterator_errors_are_canonical_unknown(self):
        prefixes = [
            ("before_bytes", ()),
            ("after_terminal", (_COMPLETED_FRAME,)),
            ("after_done", (_COMPLETED_FRAME, _DONE)),
        ]
        outcomes = [
            ("error", "error"),
            ("incomplete", "incomplete"),
            ("credential", _CREDENTIAL),
            ("ok", "ok"),
        ]
        for label, outcome in outcomes:
            tag = "protocol:" + label
            with self.subTest(position="iter_creation", error=tag):
                error = meta_protocol.MetaProtocolError(outcome)
                chunks = _IterRaise(error)
                with self.assertRaises(meta_protocol.MetaProtocolError) as ctx:
                    collect_response(chunks, credential=_CREDENTIAL)
                self.assertEqual(chunks.iter_calls, 1)
                self._assert_refused(ctx, "unknown")
            for position, prefix in prefixes:
                with self.subTest(position=position, error=tag):
                    error = meta_protocol.MetaProtocolError(outcome)
                    state = {"yielded": 0, "raised": False}
                    with self.assertRaises(
                            meta_protocol.MetaProtocolError) as ctx:
                        collect_response(_raising(prefix, error, state),
                                         credential=_CREDENTIAL)
                    self.assertEqual(state, {"yielded": len(prefix),
                                             "raised": True})
                    self._assert_refused(ctx, "unknown")
        with self.subTest(position="iter_creation", error="oserror"):
            chunks = _IterRaise(OSError(_CREDENTIAL))
            with self.assertRaises(meta_protocol.MetaProtocolError) as ctx:
                collect_response(chunks, credential=_CREDENTIAL)
            self.assertEqual(chunks.iter_calls, 1)
            self._assert_refused(ctx, "unknown")
        for position, prefix in prefixes:
            with self.subTest(position=position, error="oserror"):
                state = {"yielded": 0, "raised": False}
                with self.assertRaises(meta_protocol.MetaProtocolError) as ctx:
                    collect_response(
                        _raising(prefix, OSError(_CREDENTIAL), state),
                        credential=_CREDENTIAL)
                self.assertEqual(state, {"yielded": len(prefix),
                                         "raised": True})
                self._assert_refused(ctx, "unknown")

    def test_collector_owned_outcomes_are_preserved(self):
        expected = {"raw": _RAW, "model_name": meta_protocol.MODEL,
                    "response_id": _RESPONSE_ID, "outcome": "ok"}
        successes = [
            ("terminal_eof", [_COMPLETED_FRAME]),
            ("terminal_done_eof", [_COMPLETED_FRAME, _DONE]),
        ]
        for label, chunks in successes:
            with self.subTest(case=label):
                result = collect_response(chunks, credential=_CREDENTIAL)
                self.assertEqual(result, expected)
        failures = [
            ("failed",
             [_terminal("response.failed", _envelope("failed", []))],
             "error"),
            ("incomplete",
             [_terminal("response.incomplete",
                        _envelope("incomplete", []))],
             "incomplete"),
            ("refusal",
             [_terminal("response.completed",
                        _envelope("completed", _message(
                            [{"type": "refusal", "refusal": "refused"}])))],
             "refusal"),
            ("empty",
             [_terminal("response.completed", _completed(""))],
             "empty"),
            ("malformed_json",
             [_terminal("response.completed", _completed("{malformed"))],
             "invalid_schema"),
            ("malformed_utf8", [b"data: \xff\n\n"], "invalid_schema"),
        ]
        for label, chunks, outcome in failures:
            with self.subTest(case=label):
                with self.assertRaises(meta_protocol.MetaProtocolError) as ctx:
                    collect_response(chunks, credential=_CREDENTIAL)
                if label == "incomplete":
                    self._assert_refused(
                        ctx, outcome,
                        expected_diagnostic={
                            "version": 1,
                            "classifier": "response_status",
                            "response_status": "incomplete",
                            "message_status": None,
                            "response_id": None,
                            "reason": "absent",
                            "usage": {
                                "input_tokens": None,
                                "output_tokens": None,
                                "total_tokens": None,
                                "reasoning_tokens": None,
                            },
                        })
                else:
                    self._assert_refused(ctx, outcome)


if __name__ == "__main__":
    unittest.main()
