"""Stage-specific reasoning-effort tests for the Meta seam.

Approved application setting under test: source reading stays at
reasoning effort ``max``, question generation moves to ``high``,
the model stays ``muse-spark-1.3``, and both stages run here at
the exact ``max_output_tokens`` ceiling of 131072 (reasoning plus
visible output). Every expected effort/model/format/token value
below is a test-owned literal, never read back from the product
constants being tested. This file asserts configuration only; it
claims nothing about speed or quality.

Subjects are the actual existing ``meta_protocol.build_payload``,
``meta_provider.MetaProvider`` and ``meta_http._prepare_body`` /
``meta_http.send_payload``. Request fixtures come from the real
``_source_parts`` / ``_question_parts`` helpers on an instantiated
``test_meta_protocol.MetaProtocolTest``, and the wire exchange
comes from ``_exchange`` on an instantiated
``test_meta_http.MetaHttpTest`` (genuine loopback TLS, never a
mocked HTTP response). Both sibling test modules are imported as
modules only, so their TestCase classes never enter this module's
globals and are not collected twice. Credentials are synthetic
fixture values and are asserted absent from payloads, bodies and
error text.

Intended first RED on the pre-change product:
``test_build_payload_stage_efforts_at_ceiling`` fails its
``question_generation`` subtest, where the builder still emits
``{"effort": "max"}`` instead of the literal ``{"effort": "high"}``.
"""
from __future__ import annotations

import base64
import copy
import inspect
import json
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1] / "src"))
sys.path.insert(0, str(_HERE.parent))

import test_meta_http  # noqa: E402
import test_meta_protocol  # noqa: E402
from teacher_demo import meta_http, meta_protocol, meta_transport  # noqa: E402
from teacher_demo.meta_provider import (  # noqa: E402
    MetaProvider,
    ProviderCallContext,
)
from teacher_demo.question_protocol_v2 import QUESTION_SCHEMA_V2  # noqa: E402
from teacher_demo.source_protocol import SOURCE_SCHEMA  # noqa: E402

EXPECTED_MODEL = "muse-spark-1.3"
EXPECTED_LEGACY_EFFORT = "max"
CEILING = 131072
SYNTHETIC_CREDENTIAL = "SYNTHETIC-STAGE-REASONING-NOT-REAL-0123456789"
SENTINEL = "STAGE-REASONING-SENTINEL-DO-NOT-ECHO-9f2"
INVALID_HTTP_MESSAGE = "meta http request is invalid"
EXPECTED_EFFORT_BY_FORMAT = {
    "teacher_source_v1": "max",
    "teacher_question_v2": "high",
}
_PAYLOAD_KEYS = {
    "model", "reasoning", "max_output_tokens", "store", "stream",
    "text", "input",
}


def _protocol_helper():
    return test_meta_protocol.MetaProtocolTest(
        "test_source_payload_and_completed_response"
    )


def _http_helper():
    return test_meta_http.MetaHttpTest(
        "test_one_verified_post_preserves_payload_and_result"
    )


class StageReasoningTest(unittest.TestCase):
    """Source ``max`` / question ``high`` at the 131072 ceiling."""

    def _cases(self):
        helper = _protocol_helper()
        fixture, png, source_request = helper._source_parts()
        _fixture, question_request, _regions, _slots, prompt = (
            helper._question_parts()
        )
        cases = (
            (
                "source_analysis",
                test_meta_protocol._freeze(source_request),
                "teacher_source_v1",
                "max",
                SOURCE_SCHEMA,
            ),
            (
                "question_generation",
                test_meta_protocol._freeze(question_request),
                "teacher_question_v2",
                "high",
                QUESTION_SCHEMA_V2,
            ),
        )
        return fixture, png, prompt, cases

    def _build(self, request):
        return meta_protocol.build_payload(
            request, max_output_tokens=CEILING
        )

    def test_build_payload_stage_efforts_at_ceiling(self):
        fixture, png, prompt, cases = self._cases()
        # Legacy surface preserved: model and EFFORT stay literal.
        self.assertEqual(EXPECTED_MODEL, meta_protocol.MODEL)
        self.assertEqual(EXPECTED_LEGACY_EFFORT, meta_protocol.EFFORT)

        for kind, frozen, format_name, effort, schema in cases:
            with self.subTest(kind=kind):
                before = test_meta_protocol._snapshot(frozen)
                payload = self._build(frozen)
                self.assertEqual(
                    before, test_meta_protocol._snapshot(frozen)
                )
                self.assertEqual(_PAYLOAD_KEYS, set(payload.keys()))
                self.assertEqual(EXPECTED_MODEL, payload["model"])
                self.assertEqual({"effort": effort}, payload["reasoning"])
                self.assertIs(int, type(payload["max_output_tokens"]))
                self.assertEqual(CEILING, payload["max_output_tokens"])
                self.assertIs(False, payload["store"])
                self.assertIs(False, payload["stream"])
                self.assertEqual(
                    payload,
                    json.loads(json.dumps(payload, ensure_ascii=False)),
                )
                self.assertEqual({"format"}, set(payload["text"].keys()))
                fmt = payload["text"]["format"]
                self.assertEqual(
                    {"type", "name", "schema", "strict"},
                    set(fmt.keys()),
                )
                self.assertEqual("json_schema", fmt["type"])
                self.assertEqual(format_name, fmt["name"])
                self.assertIs(True, fmt["strict"])
                self.assertEqual(schema, fmt["schema"])
                self.assertIsNot(schema, fmt["schema"])
                self.assertEqual(1, len(payload["input"]))
                message = payload["input"][0]
                self.assertEqual(
                    {"type", "role", "content"}, set(message.keys())
                )
                self.assertEqual("message", message["type"])
                self.assertEqual("user", message["role"])
                content = message["content"]
                if kind == "source_analysis":
                    self.assertEqual(2, len(content))
                    self.assertEqual(
                        {"type": "input_text",
                         "text": fixture["source_prompt"]},
                        content[0],
                    )
                    self.assertEqual(
                        {"type", "image_url"}, set(content[1].keys())
                    )
                    self.assertEqual("input_image", content[1]["type"])
                    prefix = "data:image/png;base64,"
                    self.assertTrue(
                        content[1]["image_url"].startswith(prefix)
                    )
                    decoded = base64.b64decode(
                        content[1]["image_url"][len(prefix):]
                    )
                    self.assertEqual(png, decoded)
                else:
                    self.assertEqual(
                        [{"type": "input_text", "text": prompt}], content
                    )

        # No configurable endpoint/model/effort surface is gained.
        self.assertEqual(
            ["request", "max_output_tokens"],
            list(inspect.signature(
                meta_protocol.build_payload).parameters),
        )
        self.assertEqual(
            ["self", "credential", "max_output_tokens",
             "timeout_seconds"],
            list(inspect.signature(MetaProvider.__init__).parameters),
        )
        self.assertEqual(
            ["payload", "credential", "timeout_seconds"],
            list(inspect.signature(meta_http._prepare_body).parameters),
        )
        self.assertEqual(
            ["payload", "credential", "timeout_seconds"],
            list(inspect.signature(meta_http.send_payload).parameters),
        )
        with self.assertRaises(TypeError):
            MetaProvider(
                credential=SYNTHETIC_CREDENTIAL,
                max_output_tokens=CEILING,
                timeout_seconds=2.5,
                effort="high",
            )
        for kind, frozen, _name, _effort, _schema in cases:
            with self.subTest(case="builder-effort-kwarg", kind=kind):
                with self.assertRaises(TypeError):
                    meta_protocol.build_payload(
                        frozen, max_output_tokens=CEILING, effort="high"
                    )
            for key in ("effort", "reasoning", "model", "endpoint"):
                with self.subTest(case="request-config-key", kind=kind,
                                  key=key):
                    plain = test_meta_protocol._snapshot(frozen)
                    plain[key] = SENTINEL
                    with self.assertRaises(ValueError) as caught:
                        self._build(plain)
                    self.assertIs(ValueError, type(caught.exception))
                    self.assertNotIn(SENTINEL, str(caught.exception))

    def test_repeated_alternating_and_concurrent_builds_do_not_leak(self):
        _fixture, _png, _prompt, cases = self._cases()
        by_kind = {case[0]: case for case in cases}
        sequence = [
            by_kind["question_generation"],
            by_kind["source_analysis"],
            by_kind["question_generation"],
            by_kind["source_analysis"],
            by_kind["source_analysis"],
            by_kind["question_generation"],
        ]
        payloads = [self._build(case[1]) for case in sequence]
        for case, payload in zip(sequence, payloads):
            kind, _frozen, format_name, effort, _schema = case
            with self.subTest(case="alternating", kind=kind):
                self.assertEqual({"effort": effort}, payload["reasoning"])
                self.assertEqual(
                    format_name, payload["text"]["format"]["name"]
                )
                self.assertEqual(CEILING, payload["max_output_tokens"])
        self.assertIsNot(
            payloads[0]["reasoning"], payloads[2]["reasoning"]
        )

        # Mutating one returned payload must not stain either stage.
        payloads[0]["reasoning"]["effort"] = "max"
        payloads[0]["text"]["format"]["schema"].clear()
        for kind, frozen, _name, effort, schema in cases:
            with self.subTest(case="after-mutation", kind=kind):
                rebuilt = self._build(frozen)
                self.assertEqual({"effort": effort}, rebuilt["reasoning"])
                self.assertEqual(
                    schema, rebuilt["text"]["format"]["schema"]
                )

        jobs = [by_kind["question_generation"],
                by_kind["source_analysis"]] * 4
        with ThreadPoolExecutor(max_workers=4) as executor:
            concurrent = list(
                executor.map(lambda case: self._build(case[1]), jobs)
            )
        for case, payload in zip(jobs, concurrent):
            kind, _frozen, format_name, effort, _schema = case
            with self.subTest(case="concurrent", kind=kind):
                self.assertEqual({"effort": effort}, payload["reasoning"])
                self.assertEqual(
                    format_name, payload["text"]["format"]["name"]
                )
                self.assertEqual(EXPECTED_MODEL, payload["model"])
                self.assertEqual(CEILING, payload["max_output_tokens"])

    def test_provider_sends_stage_efforts_at_ceiling(self):
        _fixture, _png, _prompt, cases = self._cases()
        by_kind = {case[0]: case for case in cases}
        request_by_format = {case[2]: case[1] for case in cases}
        results_by_format = {
            format_name: {
                "raw": {
                    "synthetic_only": (
                        "declared synthetic transport double, "
                        "not Meta output"
                    ),
                    "format": format_name,
                },
                "model_name": EXPECTED_MODEL,
                "response_id": "synthetic-stage-" + format_name,
                "outcome": "ok",
            }
            for format_name in EXPECTED_EFFORT_BY_FORMAT
        }
        provider = MetaProvider(
            credential=SYNTHETIC_CREDENTIAL,
            max_output_tokens=CEILING,
            timeout_seconds=2.5,
        )
        self.assertNotIn(SYNTHETIC_CREDENTIAL, repr(provider))
        calls = []
        lock = threading.Lock()

        def synthetic(payload, *, credential, timeout_seconds,
                      total_timeout_seconds, cancel_event=None):
            record = {
                "payload": copy.deepcopy(payload),
                "credential": credential,
                "timeout_seconds": timeout_seconds,
                "total_timeout_seconds": total_timeout_seconds,
                "cancel_event": cancel_event,
            }
            with lock:
                calls.append(record)
            return results_by_format[payload["text"]["format"]["name"]]

        def invoke(case):
            context = ProviderCallContext(5.0)
            returned = provider(case[1], context=context)
            return case, context, returned

        original = meta_transport.run_payload
        outcomes = []
        try:
            meta_transport.run_payload = synthetic
            sequential = [
                by_kind["question_generation"],
                by_kind["source_analysis"],
                by_kind["question_generation"],
            ]
            outcomes.extend(invoke(case) for case in sequential)
            concurrent_cases = [
                by_kind["question_generation"],
                by_kind["source_analysis"],
                by_kind["question_generation"],
                by_kind["source_analysis"],
            ]
            with ThreadPoolExecutor(max_workers=4) as executor:
                outcomes.extend(executor.map(invoke, concurrent_cases))
        finally:
            meta_transport.run_payload = original
        self.assertIs(original, meta_transport.run_payload)

        for case, _context, returned in outcomes:
            with self.subTest(case="returned", kind=case[0]):
                self.assertIs(results_by_format[case[2]], returned)
        self.assertEqual(7, len(calls))
        counts = {"teacher_source_v1": 0, "teacher_question_v2": 0}
        for call in calls:
            payload = call["payload"]
            format_name = payload["text"]["format"]["name"]
            with self.subTest(case="transport-call",
                              format_name=format_name):
                self.assertIn(format_name, EXPECTED_EFFORT_BY_FORMAT)
                counts[format_name] += 1
                self.assertEqual(
                    self._build(request_by_format[format_name]), payload
                )
                self.assertEqual(
                    {"effort": EXPECTED_EFFORT_BY_FORMAT[format_name]},
                    payload["reasoning"],
                )
                self.assertEqual(EXPECTED_MODEL, payload["model"])
                self.assertEqual(CEILING, payload["max_output_tokens"])
                self.assertIs(False, payload["store"])
                self.assertIs(False, payload["stream"])
                self.assertEqual(SYNTHETIC_CREDENTIAL, call["credential"])
                self.assertNotIn(
                    SYNTHETIC_CREDENTIAL,
                    json.dumps(payload, ensure_ascii=False),
                )
                self.assertEqual(2.5, call["timeout_seconds"])
                self.assertGreater(call["total_timeout_seconds"], 0)
                self.assertLessEqual(call["total_timeout_seconds"], 5.0)
                self.assertIsInstance(
                    call["cancel_event"], threading.Event
                )
                self.assertFalse(call["cancel_event"].is_set())
        self.assertEqual(
            {"teacher_source_v1": 3, "teacher_question_v2": 4}, counts
        )
