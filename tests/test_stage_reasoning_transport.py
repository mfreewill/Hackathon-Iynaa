"""Stage-effort gate and loopback wire tests for ``meta_http``.

Test-owned expectations: source payloads (``teacher_source_v1``)
serialize with effort ``max``, question payloads
(``teacher_question_v2``) with effort ``high``, both at 131072
tokens with ``stream=true`` on the wire. Sibling modules supply
genuine builder inputs and the real loopback TLS exchange.
"""
import copy
import http.client
import json
import unittest

import test_meta_http as httpfx
import test_meta_protocol as protofx
from teacher_demo import meta_http, meta_protocol

_TOKENS = 131072
_MODEL = "muse-spark-1.3"
_INVALID = "meta http request is invalid"
_SOURCE_NAME = "teacher_source_v1"
_QUESTION_NAME = "teacher_question_v2"
_PROTO_CASE = "test_source_payload_and_completed_response"
_HTTP_CASE = "test_one_verified_post_preserves_payload_and_result"


def _source_payload():
    helper = protofx.MetaProtocolTest(_PROTO_CASE)
    _fixture, _png, request = helper._source_parts()
    return meta_protocol.build_payload(request, max_output_tokens=_TOKENS)


def _question_payload():
    helper = protofx.MetaProtocolTest(_PROTO_CASE)
    _fixture, request, _regions, _slots, _prompt = (
        helper._question_parts()
    )
    return meta_protocol.build_payload(request, max_output_tokens=_TOKENS)


def _with_reasoning(value):
    return lambda p: p.__setitem__("reasoning", value)


def _with_format_field(key, value):
    return lambda p: p["text"]["format"].__setitem__(key, value)


class StageReasoningTransportTest(unittest.TestCase):
    def _assert_wire(self, wire, before, effort, format_name):
        self.assertEqual({"effort": effort}, wire["reasoning"])
        self.assertEqual(_TOKENS, wire["max_output_tokens"])
        self.assertEqual(_MODEL, wire["model"])
        self.assertIs(True, wire["stream"])
        self.assertIs(False, wire["store"])
        self.assertEqual(format_name, wire["text"]["format"]["name"])
        expected = copy.deepcopy(before)
        expected["stream"] = True
        self.assertEqual(expected, wire)

    def _assert_refused(self, candidate, credential):
        before = copy.deepcopy(candidate)
        try:
            meta_http.send_payload(
                candidate, credential=credential, timeout_seconds=5
            )
        except Exception as caught:
            exc = caught
        else:
            self.fail("expected the fixed request refusal")
        self.assertIs(type(exc), ValueError)
        self.assertEqual((_INVALID,), exc.args)
        self.assertEqual(before, candidate)

    def test_stage_gate_serializes_only_matching_effort(self):
        credential = httpfx._credential()
        source = _source_payload()
        question = _question_payload()
        # Direct gate positive, independent of the builder change:
        # force the expected question effort into the genuine
        # question payload before exercising the transport gate.
        question["reasoning"] = {"effort": "high"}
        positives = [
            ("source-max", source, "max", _SOURCE_NAME),
            ("question-high", question, "high", _QUESTION_NAME),
        ]
        for label, payload, effort, name in positives:
            with self.subTest(case=label):
                before = copy.deepcopy(payload)
                body = meta_http._prepare_body(payload, credential, 5)
                self.assertIsInstance(body, bytes)
                wire = json.loads(body.decode("utf-8"))
                self._assert_wire(wire, before, effort, name)
                self.assertEqual(before, payload)

        cases = []

        def add(label, base, mutate):
            candidate = copy.deepcopy(base)
            mutate(candidate)
            cases.append((label, candidate))

        add("source-high", source, _with_reasoning({"effort": "high"}))
        add("source-low", source, _with_reasoning({"effort": "low"}))
        add("question-max", question, _with_reasoning({"effort": "max"}))
        add("question-low", question, _with_reasoning({"effort": "low"}))
        add("reasoning-missing", source, lambda p: p.pop("reasoning"))
        add("reasoning-empty", question, _with_reasoning({}))
        add(
            "reasoning-extra",
            question,
            _with_reasoning({"effort": "high", "extra": 1}),
        )
        add("reasoning-not-object", source, _with_reasoning("max"))
        # Format negatives keep the old-approved max effort of the
        # genuine source payload, so only the new format gate can
        # refuse them.
        add("name-missing", source, lambda p: p["text"]["format"].pop("name"))
        add(
            "name-unknown",
            source,
            _with_format_field("name", "teacher_unknown_v9"),
        )
        add("name-list", source, _with_format_field("name", [_SOURCE_NAME]))
        add("format-type-wrong", source, _with_format_field("type", "text"))
        add(
            "format-not-object",
            source,
            lambda p: p["text"].__setitem__("format", "json_schema"),
        )
        add("format-missing", source, lambda p: p.__setitem__("text", {}))
        add("text-not-object", source, lambda p: p.__setitem__("text", []))

        constructions = []
        real_connection = http.client.HTTPSConnection

        def trap(*args, **kwargs):
            constructions.append((args, kwargs))
            raise AssertionError("connection must not be constructed")

        http.client.HTTPSConnection = trap
        try:
            for label, candidate in cases:
                with self.subTest(case=label):
                    self._assert_refused(candidate, credential)
        finally:
            http.client.HTTPSConnection = real_connection
        self.assertEqual([], constructions)

    def test_stage_payloads_survive_loopback_wire(self):
        helper = httpfx.MetaHttpTest(_HTTP_CASE)
        credential = httpfx._credential()
        stages = [
            ("source", _source_payload(), "max", _SOURCE_NAME),
            ("question", _question_payload(), "high", _QUESTION_NAME),
        ]
        for label, payload, effort, name in stages:
            with self.subTest(stage=label):
                before = copy.deepcopy(payload)
                body = httpfx._healthy_sse()
                expected_result = httpfx._collector_result(body, credential)
                exchange = helper._exchange(
                    [httpfx._length_spec(body)],
                    payload=payload,
                    credential=credential,
                    timeout_seconds=5,
                )
                self.assertIsNone(exchange["exc"], "expected success")
                self.assertEqual(expected_result, exchange["result"])
                helper._assert_one_post(exchange)
                helper._assert_closed(exchange["captured"])
                wire_body = exchange["server"]["requests"][0]["body"]
                wire = json.loads(wire_body.decode("utf-8"))
                self._assert_wire(wire, before, effort, name)
                self.assertEqual(before, payload)


if __name__ == "__main__":
    unittest.main()
