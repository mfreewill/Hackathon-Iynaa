"""Behavioral tests for the future ``teacher_demo.meta_provider`` bridge.

Declared synthetic only: credentials, requests, and transport results
in this file are test fixture data, not live Meta access or output.
The single synthetic boundary is the final
``teacher_demo.meta_transport.run_payload`` attribute, replaced with
a capturing callback and restored after each use. The accepted
``meta_protocol.build_payload`` builder remains genuine; where it is
wrapped, the wrapper only observes/schedules and returns the actual
payload built by the genuine builder. All waiting uses the real
``time.monotonic`` clock with explicit witnessed-expiry assertions.
No sockets, processes, HTTP, browser, credential loading, or clock
replacement occur here.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import importlib.util
import json
import sys
import threading
import time
import traceback
import types
import unittest
from collections.abc import Mapping
from pathlib import Path

# Staged src layout: make the packaged sources importable by their
# absolute names regardless of the discovery working directory.
_SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from teacher_demo import meta_protocol, meta_transport  # noqa: E402
from teacher_demo.question_protocol_v2 import question_prompt_v2  # noqa: E402

_FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "meta_protocol_inputs.json"
)
_SYNTHETIC_CREDENTIAL = "SYNTHETIC-TEST-CREDENTIAL-NOT-REAL-0123456789"
_MAX_OUTPUT_TOKENS = 512
_SOCKET_TIMEOUT = 2.5
_HEALTHY_BUDGET = 5.0
_SHORT_BUDGET = 0.35
_HOSTILE = "HOSTILE" + "-SENTINEL-" + "7c1d9e2f"
_RECOGNIZED_OUTCOMES = (
    "error",
    "unknown",
    "invalid_schema",
    "incomplete",
    "refusal",
    "empty",
    "cancelled",
    "timeout",
)


def _load_fixture():
    data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    png_bytes = base64.b64decode(data["png_base64"], validate=True)
    return {
        "png_bytes": png_bytes,
        "png_sha256": data["png_sha256"],
        "grade": data["grade"],
        "objective": data["objective"],
    }


def _source_request(fixture):
    source_sha = hashlib.sha256(b"SYNTHETIC-ONLY-SOURCE-DIGEST").hexdigest()
    return types.MappingProxyType({
        "kind": "source_analysis",
        "request_id": "synthetic-source-request-001",
        "source_id": "synthetic-source-001",
        "source_sha256": source_sha,
        "render_sha256": fixture["png_sha256"],
        "media_type": "image/png",
        "png_bytes": fixture["png_bytes"],
        "grade": fixture["grade"],
        "objective": fixture["objective"],
    })


def _question_request(fixture):
    regions = types.MappingProxyType({
        "synthetic-region-1": (
            "نصّ صناعي مخصّص للاختبار فقط: "
            "ذهب التلاميذ إلى الحديقة في الصباح."
        ),
        "synthetic-region-2": (
            "نصّ صناعي ثانٍ للاختبار فقط: "
            "قرأت المعلّمة القصّة ثم سألت عن الفكرة الرئيسة."
        ),
    })
    slots = (
        types.MappingProxyType({
            "slot_id": "synthetic-slot-remember",
            "level": "remember",
        }),
        types.MappingProxyType({
            "slot_id": "synthetic-slot-analyze",
            "level": "analyze",
        }),
    )
    prompt = question_prompt_v2(
        regions,
        slots,
        grade=fixture["grade"],
        objective=fixture["objective"],
    )
    return types.MappingProxyType({
        "kind": "question_generation",
        "request_id": "synthetic-question-request-001",
        "source_id": "synthetic-source-001",
        "prompt": prompt,
        "reviewed_regions": regions,
        "requested_slots": slots,
        "grade": fixture["grade"],
        "objective": fixture["objective"],
    })


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_plain(item) for item in value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _synthetic_result(marker):
    return {
        "raw": {
            "synthetic_only": (
                "declared synthetic fixture data, not live Meta"
            ),
            "marker": marker,
        },
        "model_name": meta_protocol.MODEL,
        "response_id": "synthetic-response-" + marker,
        "outcome": "ok",
    }


class _ReplacedAttribute:
    """Replace one module attribute and always restore the original."""

    def __init__(self, module, name, replacement):
        self._module = module
        self._name = name
        self._replacement = replacement
        self._original = None

    def __enter__(self):
        self._original = getattr(self._module, self._name)
        setattr(self._module, self._name, self._replacement)
        return self._replacement

    def __exit__(self, exc_type, exc, tb):
        setattr(self._module, self._name, self._original)
        return False


def _record_call(calls, payload, credential, timeout_seconds,
                 total_timeout_seconds, cancel_event):
    calls.append({
        "payload": payload,
        "credential": credential,
        "timeout_seconds": timeout_seconds,
        "total_timeout_seconds": total_timeout_seconds,
        "cancel_event": cancel_event,
        "at": time.monotonic(),
    })


def _success_transport(calls, result):
    def synthetic_transport(payload, *, credential, timeout_seconds,
                            total_timeout_seconds, cancel_event=None):
        _record_call(calls, payload, credential, timeout_seconds,
                     total_timeout_seconds, cancel_event)
        return result

    return synthetic_transport


def _raising_transport(calls, error):
    def synthetic_transport(payload, *, credential, timeout_seconds,
                            total_timeout_seconds, cancel_event=None):
        _record_call(calls, payload, credential, timeout_seconds,
                     total_timeout_seconds, cancel_event)
        raise error

    return synthetic_transport


def _wait_until_deadline(deadline):
    """Wait on the real clock until ``deadline``; return observed time."""
    limit = deadline + 5.0
    observed = time.monotonic()
    while observed < deadline and observed <= limit:
        time.sleep(0.005)
        observed = time.monotonic()
    return observed


class _FakeEvent:
    """Event-shaped object that is not a real ``threading.Event``."""

    def __init__(self):
        self._set = False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def clear(self):
        self._set = False

    def wait(self, timeout=None):
        return self._set


class _FakeContext:
    """Context-shaped object that is not a real ``ProviderCallContext``."""

    def __init__(self):
        self.deadline = time.monotonic() + 5.0
        self.cancel_event = threading.Event()

    def remaining(self):
        return 1.0


class MetaProviderTest(unittest.TestCase):
    """Five behavioral RED tests for the not-yet-present provider."""

    def _assert_no_leak(self, error, forbidden):
        rendered = "".join(traceback.format_exception(error))
        texts = [str(error), repr(error.args), rendered]
        outcome = getattr(error, "outcome", None)
        if isinstance(outcome, str):
            texts.append(outcome)
        for value in forbidden:
            if not isinstance(value, str) or not value:
                continue
            for text in texts:
                self.assertNotIn(value, text)

    def _assert_plain_value_error(self, error, forbidden=()):
        self.assertIs(type(error), ValueError)
        self._assert_no_leak(error, forbidden)
        return str(error)

    def _assert_protocol_error(self, error, outcome, forbidden=()):
        self.assertIs(type(error), meta_protocol.MetaProtocolError)
        self.assertEqual(error.outcome, outcome)
        expected = str(meta_protocol.MetaProtocolError(outcome))
        self.assertEqual(str(error), expected)
        self.assertEqual(error.args, (expected,))
        self._assert_no_leak(error, forbidden)

    def _witness_expiry(self, context):
        observed = _wait_until_deadline(context.deadline)
        self.assertGreaterEqual(observed, context.deadline)
        self.assertGreaterEqual(time.monotonic(), context.deadline)
        return observed

    def test_constructor_context_and_import_are_inert(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_provider"),
            "teacher_demo.meta_provider is not implemented yet",
        )
        original_transport = meta_transport.run_payload
        transport_calls = []
        synthetic = _success_transport(
            transport_calls, _synthetic_result("inert"))
        with _ReplacedAttribute(
                meta_transport, "run_payload", synthetic):
            provider_module = importlib.import_module(
                "teacher_demo.meta_provider")
            self.assertEqual(transport_calls, [])
            context_type = provider_module.ProviderCallContext
            provider_type = provider_module.MetaProvider

            explicit_event = threading.Event()
            before = time.monotonic()
            context = context_type(
                _HEALTHY_BUDGET, cancel_event=explicit_event)
            after = time.monotonic()
            self.assertIs(context.cancel_event, explicit_event)
            self.assertIs(type(context.deadline), float)
            self.assertGreaterEqual(
                context.deadline, before + _HEALTHY_BUDGET)
            self.assertLessEqual(
                context.deadline, after + _HEALTHY_BUDGET)
            deadline_before = context.deadline
            mark_before = time.monotonic()
            remaining = context.remaining()
            mark_after = time.monotonic()
            self.assertGreater(remaining, 0)
            self.assertLessEqual(remaining, _HEALTHY_BUDGET)
            self.assertLessEqual(
                remaining,
                deadline_before - mark_before + 1e-9)
            self.assertGreaterEqual(
                remaining,
                deadline_before - mark_after - 1e-9)
            self.assertEqual(context.deadline, deadline_before)

            first_default = context_type(_HEALTHY_BUDGET)
            second_default = context_type(_HEALTHY_BUDGET)
            self.assertIsInstance(
                first_default.cancel_event, threading.Event)
            self.assertIsInstance(
                second_default.cancel_event, threading.Event)
            self.assertIsNot(
                first_default.cancel_event,
                second_default.cancel_event)
            self.assertFalse(first_default.cancel_event.is_set())
            self.assertFalse(second_default.cancel_event.is_set())
            first_default.cancel_event.set()
            self.assertFalse(second_default.cancel_event.is_set())
            int_context = context_type(2)
            self.assertGreater(int_context.remaining(), 0)
            self.assertLessEqual(int_context.remaining(), 2)
            self.assertEqual(transport_calls, [])

            context_messages = set()
            bad_budgets = (
                True, False, 0, 0.0, -1, -0.5,
                float("inf"), float("-inf"), float("nan"),
                "5", None, _HOSTILE,
            )
            for value in bad_budgets:
                with self.assertRaises(ValueError) as caught:
                    context_type(value)
                context_messages.add(
                    self._assert_plain_value_error(
                        caught.exception, (_HOSTILE,)))
            bad_events = (object(), "event", 1, True, _FakeEvent())
            for value in bad_events:
                with self.assertRaises(ValueError) as caught:
                    context_type(
                        _HEALTHY_BUDGET, cancel_event=value)
                context_messages.add(
                    self._assert_plain_value_error(
                        caught.exception, (_HOSTILE,)))
            self.assertEqual(len(context_messages), 1)
            with self.assertRaises(TypeError):
                context_type(_HEALTHY_BUDGET, threading.Event())
            with self.assertRaises(TypeError):
                context_type(
                    _HEALTHY_BUDGET, clock=time.monotonic)

            cancelled_event = threading.Event()
            cancelled_event.set()
            cancelled_context = context_type(
                _HEALTHY_BUDGET, cancel_event=cancelled_event)
            with self.assertRaises(
                    meta_protocol.MetaProtocolError) as first_caught:
                cancelled_context.remaining()
            with self.assertRaises(
                    meta_protocol.MetaProtocolError) as second_caught:
                cancelled_context.remaining()
            self._assert_protocol_error(
                first_caught.exception, "cancelled", (_HOSTILE,))
            self._assert_protocol_error(
                second_caught.exception, "cancelled", (_HOSTILE,))
            self.assertIsNot(
                first_caught.exception, second_caught.exception)

            expired_context = context_type(_SHORT_BUDGET)
            self._witness_expiry(expired_context)
            self.assertFalse(expired_context.cancel_event.is_set())
            with self.assertRaises(
                    meta_protocol.MetaProtocolError) as first_caught:
                expired_context.remaining()
            with self.assertRaises(
                    meta_protocol.MetaProtocolError) as second_caught:
                expired_context.remaining()
            self._assert_protocol_error(
                first_caught.exception, "timeout", (_HOSTILE,))
            self._assert_protocol_error(
                second_caught.exception, "timeout", (_HOSTILE,))
            self.assertIsNot(
                first_caught.exception, second_caught.exception)

            both_event = threading.Event()
            both_context = context_type(
                _SHORT_BUDGET, cancel_event=both_event)
            self._witness_expiry(both_context)
            both_event.set()
            with self.assertRaises(
                    meta_protocol.MetaProtocolError) as caught:
                both_context.remaining()
            self._assert_protocol_error(
                caught.exception, "cancelled", (_HOSTILE,))
            self.assertEqual(transport_calls, [])

            provider = provider_type(
                credential=_SYNTHETIC_CREDENTIAL,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                timeout_seconds=_SOCKET_TIMEOUT)
            self.assertNotIn(_SYNTHETIC_CREDENTIAL, repr(provider))
            self.assertNotIn(_SYNTHETIC_CREDENTIAL, str(provider))
            self.assertEqual(transport_calls, [])

            valid_config = {
                "credential": _SYNTHETIC_CREDENTIAL,
                "max_output_tokens": _MAX_OUTPUT_TOKENS,
                "timeout_seconds": _SOCKET_TIMEOUT,
            }
            invalid_configs = []
            bad_credentials = (
                "", " ", "has space", "tab\there", "new\nline",
                "ctrl\x01here", "del\x7fhere", "synthetic-é",
                "بيانات", b"synthetic-bytes", None, 123, True,
                _HOSTILE + " ", _HOSTILE + "\n",
            )
            for value in bad_credentials:
                config = dict(valid_config)
                config["credential"] = value
                invalid_configs.append(config)
            bad_tokens = (
                True, False, 0, 15, -16, 16.0, "512", None,
                float("inf"), float("nan"),
            )
            for value in bad_tokens:
                config = dict(valid_config)
                config["max_output_tokens"] = value
                invalid_configs.append(config)
            bad_timeouts = (
                True, False, 0, 0.0, -1, -0.5, "2.5", None,
                float("inf"), float("-inf"), float("nan"),
            )
            for value in bad_timeouts:
                config = dict(valid_config)
                config["timeout_seconds"] = value
                invalid_configs.append(config)
            provider_messages = set()
            for config in invalid_configs:
                with self.assertRaises(ValueError) as caught:
                    provider_type(**config)
                provider_messages.add(
                    self._assert_plain_value_error(
                        caught.exception,
                        (_SYNTHETIC_CREDENTIAL, _HOSTILE)))
            self.assertEqual(len(provider_messages), 1)
            with self.assertRaises(TypeError):
                provider_type(
                    _SYNTHETIC_CREDENTIAL,
                    max_output_tokens=_MAX_OUTPUT_TOKENS,
                    timeout_seconds=_SOCKET_TIMEOUT)
            with self.assertRaises(TypeError):
                provider_type(
                    credential=_SYNTHETIC_CREDENTIAL,
                    max_output_tokens=_MAX_OUTPUT_TOKENS,
                    timeout_seconds=_SOCKET_TIMEOUT,
                    endpoint="synthetic-endpoint")
            self.assertEqual(transport_calls, [])
        self.assertIs(meta_transport.run_payload, original_transport)

    def test_source_and_questions_use_real_builder_and_one_transport(
            self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_provider"),
            "teacher_demo.meta_provider is not implemented yet",
        )
        provider_module = importlib.import_module(
            "teacher_demo.meta_provider")
        fixture = _load_fixture()
        self.assertEqual(
            hashlib.sha256(fixture["png_bytes"]).hexdigest(),
            fixture["png_sha256"])
        source_request = _source_request(fixture)
        question_request = _question_request(fixture)
        plains_before = {
            "source": _plain(source_request),
            "questions": _plain(question_request),
        }
        expected_source = meta_protocol.build_payload(
            source_request, max_output_tokens=_MAX_OUTPUT_TOKENS)
        expected_question = meta_protocol.build_payload(
            question_request, max_output_tokens=_MAX_OUTPUT_TOKENS)
        provider = provider_module.MetaProvider(
            credential=_SYNTHETIC_CREDENTIAL,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            timeout_seconds=_SOCKET_TIMEOUT)
        repr_before = repr(provider)
        cases = (
            ("source", source_request, expected_source,
             _synthetic_result("source")),
            ("questions", question_request, expected_question,
             _synthetic_result("questions")),
        )
        for label, request, expected_payload, result in cases:
            result_snapshot = copy.deepcopy(result)
            calls = []
            synthetic = _success_transport(calls, result)
            event = threading.Event()
            context = provider_module.ProviderCallContext(
                _HEALTHY_BUDGET, cancel_event=event)
            deadline_before = context.deadline
            started = time.monotonic()
            with _ReplacedAttribute(
                    meta_transport, "run_payload", synthetic):
                returned = provider(request, context=context)
            self.assertIs(returned, result)
            self.assertEqual(returned, result_snapshot)
            self.assertEqual(context.deadline, deadline_before)
            self.assertEqual(_plain(request), plains_before[label])
            self.assertEqual(len(calls), 1)
            call = calls[0]
            self.assertEqual(call["payload"], expected_payload)
            self.assertEqual(
                call["payload"]["model"], meta_protocol.MODEL)
            expected_effort = (
                "max" if label == "source" else "high")
            self.assertEqual(
                call["payload"]["reasoning"],
                {"effort": expected_effort})
            self.assertEqual(call["payload"]["reasoning"]["effort"],
                             expected_effort)
            self.assertEqual(
                call["payload"]["max_output_tokens"],
                _MAX_OUTPUT_TOKENS)
            self.assertNotIn(
                _SYNTHETIC_CREDENTIAL,
                json.dumps(call["payload"], ensure_ascii=False))
            self.assertEqual(
                call["credential"], _SYNTHETIC_CREDENTIAL)
            self.assertEqual(
                call["timeout_seconds"], _SOCKET_TIMEOUT)
            self.assertIs(call["cancel_event"], event)
            self.assertIs(call["cancel_event"], context.cancel_event)
            total = call["total_timeout_seconds"]
            self.assertGreater(total, 0)
            self.assertLessEqual(total, _HEALTHY_BUDGET)
            self.assertGreater(total, _SOCKET_TIMEOUT)
            self.assertLessEqual(
                total, deadline_before - started + 1e-9)
            self.assertGreaterEqual(
                total, deadline_before - call["at"] - 1e-9)
            self.assertFalse(event.is_set())
        self.assertEqual(repr(provider), repr_before)
        self.assertNotIn(_SYNTHETIC_CREDENTIAL, repr(provider))

    def test_preclosed_context_never_prepares_or_sends(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_provider"),
            "teacher_demo.meta_provider is not implemented yet",
        )
        provider_module = importlib.import_module(
            "teacher_demo.meta_provider")
        fixture = _load_fixture()
        request = _source_request(fixture)
        context_type = provider_module.ProviderCallContext
        provider = provider_module.MetaProvider(
            credential=_SYNTHETIC_CREDENTIAL,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            timeout_seconds=_SOCKET_TIMEOUT)
        genuine_builder = meta_protocol.build_payload
        original_builder = genuine_builder
        original_transport = meta_transport.run_payload
        builder_calls = []
        transport_calls = []
        healthy_result = _synthetic_result("preclosed-healthy")

        def counting_builder(given_request, *, max_output_tokens):
            builder_calls.append({
                "request": given_request,
                "max_output_tokens": max_output_tokens,
            })
            return genuine_builder(
                given_request, max_output_tokens=max_output_tokens)

        synthetic = _success_transport(transport_calls, healthy_result)
        with _ReplacedAttribute(
                meta_protocol, "build_payload", counting_builder):
            with _ReplacedAttribute(
                    meta_transport, "run_payload", synthetic):
                preset_event = threading.Event()
                preset_event.set()
                preset_context = context_type(
                    _HEALTHY_BUDGET, cancel_event=preset_event)
                with self.assertRaises(
                        meta_protocol.MetaProtocolError) as caught:
                    provider(request, context=preset_context)
                self._assert_protocol_error(
                    caught.exception, "cancelled",
                    (_SYNTHETIC_CREDENTIAL, _HOSTILE))
                self.assertEqual(builder_calls, [])
                self.assertEqual(transport_calls, [])

                expired_context = context_type(_SHORT_BUDGET)
                self._witness_expiry(expired_context)
                self.assertFalse(
                    expired_context.cancel_event.is_set())
                with self.assertRaises(
                        meta_protocol.MetaProtocolError) as caught:
                    provider(request, context=expired_context)
                self._assert_protocol_error(
                    caught.exception, "timeout",
                    (_SYNTHETIC_CREDENTIAL, _HOSTILE))
                self.assertEqual(builder_calls, [])
                self.assertEqual(transport_calls, [])

                both_event = threading.Event()
                both_context = context_type(
                    _SHORT_BUDGET, cancel_event=both_event)
                self._witness_expiry(both_context)
                both_event.set()
                with self.assertRaises(
                        meta_protocol.MetaProtocolError) as caught:
                    provider(request, context=both_context)
                self._assert_protocol_error(
                    caught.exception, "cancelled",
                    (_SYNTHETIC_CREDENTIAL, _HOSTILE))
                self.assertEqual(builder_calls, [])
                self.assertEqual(transport_calls, [])

                with self.assertRaises(TypeError):
                    provider(request)
                healthy_for_positional = context_type(_HEALTHY_BUDGET)
                with self.assertRaises(TypeError):
                    provider(request, healthy_for_positional)
                self.assertEqual(builder_calls, [])
                self.assertEqual(transport_calls, [])

                invalid_messages = set()
                invalid_contexts = (
                    None, object(), "context", 1, _FakeContext())
                for value in invalid_contexts:
                    with self.assertRaises(ValueError) as caught:
                        provider(request, context=value)
                    invalid_messages.add(
                        self._assert_plain_value_error(
                            caught.exception,
                            (_SYNTHETIC_CREDENTIAL, _HOSTILE)))
                self.assertEqual(len(invalid_messages), 1)
                self.assertEqual(builder_calls, [])
                self.assertEqual(transport_calls, [])

                bad_request = types.MappingProxyType(
                    {"kind": _HOSTILE})
                healthy_context = context_type(_HEALTHY_BUDGET)
                with self.assertRaises(ValueError) as caught:
                    provider(bad_request, context=healthy_context)
                self._assert_plain_value_error(
                    caught.exception,
                    (_SYNTHETIC_CREDENTIAL, _HOSTILE))
                self.assertEqual(len(builder_calls), 1)
                self.assertEqual(
                    builder_calls[0]["max_output_tokens"],
                    _MAX_OUTPUT_TOKENS)
                self.assertEqual(transport_calls, [])

                builder_calls.clear()
                transport_calls.clear()
                opposite_context = context_type(_HEALTHY_BUDGET)
                returned = provider(
                    request, context=opposite_context)
                self.assertIs(returned, healthy_result)
                self.assertEqual(len(builder_calls), 1)
                self.assertEqual(len(transport_calls), 1)
        self.assertIs(meta_protocol.build_payload, original_builder)
        self.assertIs(meta_transport.run_payload, original_transport)

    def test_payload_preparation_cannot_reset_or_outlive_context(
            self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_provider"),
            "teacher_demo.meta_provider is not implemented yet",
        )
        provider_module = importlib.import_module(
            "teacher_demo.meta_provider")
        fixture = _load_fixture()
        request = _source_request(fixture)
        expected_payload = meta_protocol.build_payload(
            request, max_output_tokens=_MAX_OUTPUT_TOKENS)
        context_type = provider_module.ProviderCallContext
        provider = provider_module.MetaProvider(
            credential=_SYNTHETIC_CREDENTIAL,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            timeout_seconds=_SOCKET_TIMEOUT)
        genuine_builder = meta_protocol.build_payload

        cancel_event = threading.Event()
        cancel_context = context_type(
            _HEALTHY_BUDGET, cancel_event=cancel_event)
        cancel_deadline = cancel_context.deadline
        cancel_built = []
        cancel_transport_calls = []

        def cancel_after_build(given_request, *, max_output_tokens):
            payload = genuine_builder(
                given_request, max_output_tokens=max_output_tokens)
            cancel_built.append(payload)
            cancel_event.set()
            return payload

        cancel_synthetic = _success_transport(
            cancel_transport_calls,
            _synthetic_result("must-not-send-cancel"))
        with _ReplacedAttribute(
                meta_protocol, "build_payload", cancel_after_build):
            with _ReplacedAttribute(
                    meta_transport, "run_payload", cancel_synthetic):
                with self.assertRaises(
                        meta_protocol.MetaProtocolError) as caught:
                    provider(request, context=cancel_context)
        self._assert_protocol_error(
            caught.exception, "cancelled",
            (_SYNTHETIC_CREDENTIAL, _HOSTILE))
        self.assertEqual(len(cancel_built), 1)
        self.assertEqual(cancel_built[0], expected_payload)
        self.assertEqual(cancel_transport_calls, [])
        self.assertEqual(cancel_context.deadline, cancel_deadline)
        self.assertTrue(cancel_event.is_set())

        expiry_context = context_type(_SHORT_BUDGET)
        expiry_deadline = expiry_context.deadline
        expiry_built = []
        expiry_record = {}
        expiry_transport_calls = []

        def expire_after_build(given_request, *, max_output_tokens):
            payload = genuine_builder(
                given_request, max_output_tokens=max_output_tokens)
            expiry_built.append(payload)
            expiry_record["observed"] = _wait_until_deadline(
                expiry_deadline)
            return payload

        expiry_synthetic = _success_transport(
            expiry_transport_calls,
            _synthetic_result("must-not-send-expiry"))
        with _ReplacedAttribute(
                meta_protocol, "build_payload", expire_after_build):
            with _ReplacedAttribute(
                    meta_transport, "run_payload", expiry_synthetic):
                with self.assertRaises(
                        meta_protocol.MetaProtocolError) as caught:
                    provider(request, context=expiry_context)
        self._assert_protocol_error(
            caught.exception, "timeout",
            (_SYNTHETIC_CREDENTIAL, _HOSTILE))
        self.assertEqual(len(expiry_built), 1)
        self.assertEqual(expiry_built[0], expected_payload)
        self.assertGreaterEqual(
            expiry_record["observed"], expiry_deadline)
        self.assertGreaterEqual(time.monotonic(), expiry_deadline)
        self.assertFalse(expiry_context.cancel_event.is_set())
        self.assertEqual(expiry_transport_calls, [])
        self.assertEqual(expiry_context.deadline, expiry_deadline)

        healthy_context = context_type(_HEALTHY_BUDGET)
        healthy_deadline = healthy_context.deadline
        healthy_built = []
        healthy_calls = []
        healthy_result = _synthetic_result("preparation-healthy")

        def observe_build(given_request, *, max_output_tokens):
            payload = genuine_builder(
                given_request, max_output_tokens=max_output_tokens)
            healthy_built.append(payload)
            return payload

        healthy_synthetic = _success_transport(
            healthy_calls, healthy_result)
        with _ReplacedAttribute(
                meta_protocol, "build_payload", observe_build):
            with _ReplacedAttribute(
                    meta_transport, "run_payload", healthy_synthetic):
                returned = provider(
                    request, context=healthy_context)
        self.assertIs(returned, healthy_result)
        self.assertEqual(len(healthy_built), 1)
        self.assertEqual(healthy_built[0], expected_payload)
        self.assertEqual(len(healthy_calls), 1)
        self.assertEqual(healthy_calls[0]["payload"], expected_payload)
        self.assertIs(
            healthy_calls[0]["cancel_event"],
            healthy_context.cancel_event)
        self.assertGreater(
            healthy_calls[0]["total_timeout_seconds"], 0)
        self.assertLessEqual(
            healthy_calls[0]["total_timeout_seconds"], _HEALTHY_BUDGET)
        self.assertEqual(healthy_context.deadline, healthy_deadline)
        self.assertIs(meta_protocol.build_payload, genuine_builder)

    def test_transport_return_and_failures_are_closed(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_provider"),
            "teacher_demo.meta_provider is not implemented yet",
        )
        provider_module = importlib.import_module(
            "teacher_demo.meta_provider")
        fixture = _load_fixture()
        request = _source_request(fixture)
        context_type = provider_module.ProviderCallContext
        provider = provider_module.MetaProvider(
            credential=_SYNTHETIC_CREDENTIAL,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            timeout_seconds=_SOCKET_TIMEOUT)
        forbidden = (
            _SYNTHETIC_CREDENTIAL, _HOSTILE, fixture["objective"])

        cancel_event = threading.Event()
        cancel_context = context_type(
            _HEALTHY_BUDGET, cancel_event=cancel_event)
        cancel_calls = []
        cancel_result = _synthetic_result("closed-on-return-cancel")

        def set_event_then_return(payload, *, credential,
                                  timeout_seconds,
                                  total_timeout_seconds,
                                  cancel_event=None):
            _record_call(cancel_calls, payload, credential,
                         timeout_seconds, total_timeout_seconds,
                         cancel_event)
            cancel_event.set()
            return cancel_result

        with _ReplacedAttribute(
                meta_transport, "run_payload", set_event_then_return):
            with self.assertRaises(
                    meta_protocol.MetaProtocolError) as caught:
                provider(request, context=cancel_context)
        self._assert_protocol_error(
            caught.exception, "cancelled", forbidden)
        self.assertEqual(len(cancel_calls), 1)
        self.assertIs(cancel_calls[0]["cancel_event"], cancel_event)

        expiry_context = context_type(_SHORT_BUDGET)
        expiry_deadline = expiry_context.deadline
        expiry_calls = []
        expiry_record = {}
        expiry_result = _synthetic_result("closed-on-return-expiry")

        def expire_then_return(payload, *, credential,
                               timeout_seconds,
                               total_timeout_seconds,
                               cancel_event=None):
            _record_call(expiry_calls, payload, credential,
                         timeout_seconds, total_timeout_seconds,
                         cancel_event)
            expiry_record["observed"] = _wait_until_deadline(
                expiry_deadline)
            return expiry_result

        with _ReplacedAttribute(
                meta_transport, "run_payload", expire_then_return):
            with self.assertRaises(
                    meta_protocol.MetaProtocolError) as caught:
                provider(request, context=expiry_context)
        self._assert_protocol_error(
            caught.exception, "timeout", forbidden)
        self.assertEqual(len(expiry_calls), 1)
        self.assertGreaterEqual(
            expiry_record["observed"], expiry_deadline)
        self.assertGreaterEqual(time.monotonic(), expiry_deadline)
        self.assertFalse(expiry_context.cancel_event.is_set())

        both_event = threading.Event()
        both_context = context_type(
            _SHORT_BUDGET, cancel_event=both_event)
        both_deadline = both_context.deadline
        both_calls = []
        both_record = {}
        both_result = _synthetic_result("closed-on-return-both")

        def close_both_then_return(payload, *, credential,
                                   timeout_seconds,
                                   total_timeout_seconds,
                                   cancel_event=None):
            _record_call(both_calls, payload, credential,
                         timeout_seconds, total_timeout_seconds,
                         cancel_event)
            cancel_event.set()
            both_record["observed"] = _wait_until_deadline(
                both_deadline)
            return both_result

        with _ReplacedAttribute(
                meta_transport, "run_payload", close_both_then_return):
            with self.assertRaises(
                    meta_protocol.MetaProtocolError) as caught:
                provider(request, context=both_context)
        self._assert_protocol_error(
            caught.exception, "cancelled", forbidden)
        self.assertEqual(len(both_calls), 1)
        self.assertGreaterEqual(
            both_record["observed"], both_deadline)

        for outcome in _RECOGNIZED_OUTCOMES:
            with self.subTest(outcome=outcome):
                stored_error = meta_protocol.MetaProtocolError(outcome)
                calls = []
                synthetic = _raising_transport(calls, stored_error)
                fresh_context = context_type(_HEALTHY_BUDGET)
                with _ReplacedAttribute(
                        meta_transport, "run_payload", synthetic):
                    with self.assertRaises(
                            meta_protocol.MetaProtocolError) as caught:
                        provider(request, context=fresh_context)
                self._assert_protocol_error(
                    caught.exception, outcome, forbidden)
                self.assertIsNot(caught.exception, stored_error)
                self.assertEqual(len(calls), 1)
                self.assertFalse(fresh_context.cancel_event.is_set())
                self.assertLess(
                    time.monotonic(), fresh_context.deadline)

        forged_outcomes = (
            _HOSTILE, "ok", "", None, 987654321, ["forged"])
        for index, forged in enumerate(forged_outcomes):
            with self.subTest(forged_case=index):
                stored_error = meta_protocol.MetaProtocolError(forged)
                calls = []
                synthetic = _raising_transport(calls, stored_error)
                fresh_context = context_type(_HEALTHY_BUDGET)
                with _ReplacedAttribute(
                        meta_transport, "run_payload", synthetic):
                    with self.assertRaises(
                            meta_protocol.MetaProtocolError) as caught:
                        provider(request, context=fresh_context)
                self._assert_protocol_error(
                    caught.exception, "unknown", forbidden)
                self.assertIsNot(caught.exception, stored_error)
                self.assertEqual(len(calls), 1)

        hostile_text = _HOSTILE + " " + _SYNTHETIC_CREDENTIAL
        unexpected_errors = (
            RuntimeError(hostile_text),
            Exception(hostile_text, fixture["objective"]),
        )
        for index, stored_error in enumerate(unexpected_errors):
            with self.subTest(unexpected_case=index):
                calls = []
                synthetic = _raising_transport(calls, stored_error)
                fresh_context = context_type(_HEALTHY_BUDGET)
                with _ReplacedAttribute(
                        meta_transport, "run_payload", synthetic):
                    with self.assertRaises(
                            meta_protocol.MetaProtocolError) as caught:
                        provider(request, context=fresh_context)
                self._assert_protocol_error(
                    caught.exception, "unknown", forbidden)
                self.assertIsNot(caught.exception, stored_error)
                self.assertEqual(len(calls), 1)

        second_result = _synthetic_result("unaffected-second")
        second_calls = []
        second_synthetic = _success_transport(
            second_calls, second_result)
        second_context = context_type(_HEALTHY_BUDGET)
        self.assertIsNot(
            second_context.cancel_event, cancel_context.cancel_event)
        self.assertTrue(cancel_context.cancel_event.is_set())
        self.assertFalse(second_context.cancel_event.is_set())
        with _ReplacedAttribute(
                meta_transport, "run_payload", second_synthetic):
            returned = provider(request, context=second_context)
        self.assertIs(returned, second_result)
        self.assertEqual(len(second_calls), 1)
        self.assertIs(
            second_calls[0]["cancel_event"],
            second_context.cancel_event)
        self.assertFalse(second_context.cancel_event.is_set())
        self.assertTrue(cancel_context.cancel_event.is_set())


if __name__ == "__main__":
    unittest.main()
