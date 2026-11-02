"""Executable test oracle for ``teacher_demo.meta_protocol``.

Fixture:
``tests/fixtures/meta_protocol_inputs.json``. Stdlib/unittest only.

Every test
obtains the module through ``_meta()``, which asserts via
``importlib.util.find_spec`` before importing, so a missing module
reports 12 missing-behavior assertion failures rather than
import/collection errors. No stub or substitute implementation is
provided here, and no test mirrors the implementation.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import importlib
import importlib.util
import json
import unittest
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

EXPECTED_MODEL = "muse-spark-1.3"
EXPECTED_EFFORT = "max"
EXPECTED_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
SENTINEL = "HOSTILE-SENTINEL-DO-NOT-ECHO-7xQ"
REASONING_SENTINEL = "PRIVATE-REASONING-SENTINEL-3kZ"
USAGE_SENTINEL = "PROVIDER-USAGE-SENTINEL-5mN"

_FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "meta_protocol_inputs.json"
)


def _load_fixture():
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _freeze(value):
    """Deep-freeze as the HTTP seam does: proxies and tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _snapshot(value):
    """Plain comparable snapshot used only for immutability checks."""
    if isinstance(value, Mapping):
        return {key: _snapshot(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_snapshot(item) for item in value)
    if isinstance(value, list):
        return [_snapshot(item) for item in value]
    return value


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _inner_text(obj):
    return json.dumps(obj, ensure_ascii=False)


def _text_part(text):
    return {"type": "output_text", "text": text}


def _message(parts, role="assistant", status="completed"):
    message = {"type": "message", "content": list(parts)}
    if role is not None:
        message["role"] = role
    if status is not None:
        message["status"] = status
    return message


def _reasoning_item(marker):
    return {
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": marker}],
        "encrypted_content": marker,
    }


def _completed_dict(inner, response_id="resp_ok_001", output=None):
    if output is None:
        output = [_message([_text_part(inner)])]
    return {
        "id": response_id,
        "object": "response",
        "model": EXPECTED_MODEL,
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": output,
    }


def _completed_bytes(inner, response_id="resp_ok_001", output=None):
    envelope = _completed_dict(inner, response_id=response_id, output=output)
    return _json_bytes(envelope)

class MetaProtocolTest(unittest.TestCase):
    """The 12 bounded oracle methods named by the task contract."""

    def _meta(self):
        try:
            spec = importlib.util.find_spec("teacher_demo.meta_protocol")
        except (ModuleNotFoundError, ValueError):
            spec = None
        self.assertIsNotNone(
            spec, "teacher_demo.meta_protocol is missing"
        )
        return importlib.import_module("teacher_demo.meta_protocol")

    def _source_parts(self):
        fixture = _load_fixture()
        png = base64.b64decode(fixture["png_base64"])
        request = {
            "kind": "source_analysis",
            "request_id": "req_src_001",
            "source_id": "src_001",
            "source_sha256": hashlib.sha256(
                b"synthetic-original-source-bytes"
            ).hexdigest(),
            "render_sha256": fixture["png_sha256"],
            "media_type": "image/png",
            "png_bytes": png,
            "grade": fixture["grade"],
            "objective": fixture["objective"],
        }
        return fixture, png, request

    def _question_parts(self):
        from teacher_demo.question_protocol_v2 import question_prompt_v2

        fixture = _load_fixture()
        grade = fixture["grade"]
        objective = fixture["objective"]
        regions = {"r1": objective, "r2": grade}
        slots = [
            {"slot_id": "q1", "level": "remember"},
            {"slot_id": "q2", "level": "analyze"},
        ]
        prompt = question_prompt_v2(
            regions, slots, grade=grade, objective=objective
        )
        request = {
            "kind": "question_generation",
            "request_id": "req_q_001",
            "source_id": "src_001",
            "prompt": prompt,
            "reviewed_regions": regions,
            "requested_slots": slots,
            "grade": grade,
            "objective": objective,
        }
        return fixture, request, regions, slots, prompt

    def _assert_build_error(self, meta, request, tokens=1024,
                            forbidden=None):
        before = _snapshot(request)
        with self.assertRaises(ValueError) as caught:
            meta.build_payload(request, max_output_tokens=tokens)
        if forbidden is not None:
            self.assertNotIn(forbidden, str(caught.exception))
        self.assertEqual(before, _snapshot(request))
        return caught.exception

    def _assert_parse_error(self, meta, data, outcome, forbidden=None):
        with self.assertRaises(meta.MetaProtocolError) as caught:
            meta.parse_response(data)
        self.assertIsInstance(caught.exception, ValueError)
        self.assertEqual(
            outcome, getattr(caught.exception, "outcome", None)
        )
        if forbidden is not None:
            self.assertNotIn(forbidden, str(caught.exception))
        return caught.exception

    def _valid_source_raw(self):
        fixture = _load_fixture()
        return {
            "sections": [
                {
                    "section_id": "s1",
                    "kind": "body",
                    "text": fixture["objective"],
                    "review_reasons": [],
                }
            ],
            "page_continues": False,
            "review_notes": [fixture["grade"]],
        }

    def test_source_payload_and_completed_response(self):
        meta = self._meta()
        from teacher_demo.source_protocol import (
            SOURCE_SCHEMA,
            validate_source_proposal,
        )

        fixture, png, request = self._source_parts()
        # Fixture precondition: genuine PNG whose hash is as staged.
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(
            hashlib.sha256(png).hexdigest(), fixture["png_sha256"]
        )
        self.assertNotEqual(
            request["source_sha256"], request["render_sha256"]
        )
        self.assertEqual(EXPECTED_MODEL, meta.MODEL)
        self.assertEqual(EXPECTED_EFFORT, meta.EFFORT)
        self.assertEqual(EXPECTED_MAX_RESPONSE_BYTES,
                         meta.MAX_RESPONSE_BYTES)
        self.assertEqual(fixture["source_prompt"], meta.SOURCE_PROMPT)

        before = _snapshot(request)
        payload = meta.build_payload(request, max_output_tokens=1024)
        self.assertEqual(before, _snapshot(request))

        self.assertIsInstance(payload, dict)
        self.assertEqual(
            {"model", "reasoning", "max_output_tokens", "store",
             "stream", "text", "input"},
            set(payload.keys()),
        )
        self.assertEqual(EXPECTED_MODEL, payload["model"])
        self.assertEqual({"effort": EXPECTED_EFFORT},
                         payload["reasoning"])
        self.assertEqual(1024, payload["max_output_tokens"])
        self.assertIs(False, payload["store"])
        self.assertIs(False, payload["stream"])
        # Payload must be plain JSON-compatible data.
        self.assertEqual(
            payload,
            json.loads(json.dumps(payload, ensure_ascii=False)),
        )

        self.assertEqual({"format"}, set(payload["text"].keys()))
        fmt = payload["text"]["format"]
        self.assertEqual(
            {"type", "name", "schema", "strict"}, set(fmt.keys())
        )
        self.assertEqual("json_schema", fmt["type"])
        self.assertEqual("teacher_source_v1", fmt["name"])
        self.assertIs(True, fmt["strict"])
        self.assertEqual(SOURCE_SCHEMA, fmt["schema"])
        self.assertIsNot(SOURCE_SCHEMA, fmt["schema"])

        self.assertEqual(1, len(payload["input"]))
        message = payload["input"][0]
        self.assertEqual(
            {"type", "role", "content"}, set(message.keys())
        )
        self.assertEqual("message", message["type"])
        self.assertEqual("user", message["role"])
        self.assertEqual(2, len(message["content"]))
        text_part, image_part = message["content"]
        self.assertEqual({"type", "text"}, set(text_part.keys()))
        self.assertEqual("input_text", text_part["type"])
        self.assertEqual(fixture["source_prompt"], text_part["text"])
        self.assertEqual(
            {"type", "image_url"}, set(image_part.keys())
        )
        self.assertEqual("input_image", image_part["type"])
        prefix = "data:image/png;base64,"
        self.assertTrue(image_part["image_url"].startswith(prefix))
        decoded = base64.b64decode(image_part["image_url"][len(prefix):])
        self.assertEqual(png, decoded)

        raw = self._valid_source_raw()
        expected_raw = copy.deepcopy(raw)
        data = _completed_bytes(
            _inner_text(raw), response_id="resp_source_001"
        )
        result = meta.parse_response(data)
        self.assertEqual(
            {"raw", "model_name", "response_id", "outcome"},
            set(result.keys()),
        )
        self.assertEqual("ok", result["outcome"])
        self.assertEqual(EXPECTED_MODEL, result["model_name"])
        self.assertEqual("resp_source_001", result["response_id"])
        self.assertEqual(expected_raw, result["raw"])
        self.assertEqual(
            expected_raw, validate_source_proposal(result["raw"])
        )

        # Mutating returned trees must not leak into fresh calls.
        payload["input"][0]["content"][0]["text"] = "MUTATED"
        result["raw"]["sections"][0]["text"] = "MUTATED"
        result["raw"]["review_notes"].append("MUTATED")
        self.assertEqual(before, _snapshot(request))
        payload2 = meta.build_payload(request, max_output_tokens=1024)
        self.assertEqual(
            fixture["source_prompt"],
            payload2["input"][0]["content"][0]["text"],
        )
        result2 = meta.parse_response(data)
        self.assertEqual(expected_raw, result2["raw"])

    def test_question_payload_preserves_frozen_reviewed_text(self):
        meta = self._meta()
        from teacher_demo.question_protocol_v2 import (
            QUESTION_SCHEMA_V2,
            question_prompt_v2,
        )

        fixture, request, regions, slots, prompt = self._question_parts()
        frozen = _freeze(request)
        self.assertIsInstance(frozen, MappingProxyType)
        self.assertIsInstance(
            frozen["reviewed_regions"], MappingProxyType
        )
        self.assertIsInstance(frozen["requested_slots"], tuple)
        self.assertIsInstance(
            frozen["requested_slots"][0], MappingProxyType
        )
        # Precondition: frozen shapes rebuild the identical prompt.
        rebuilt = question_prompt_v2(
            frozen["reviewed_regions"],
            frozen["requested_slots"],
            grade=frozen["grade"],
            objective=frozen["objective"],
        )
        self.assertEqual(prompt, rebuilt)
        self.assertEqual(prompt, frozen["prompt"])

        before = _snapshot(frozen)
        payload = meta.build_payload(frozen, max_output_tokens=2048)
        self.assertEqual(before, _snapshot(frozen))

        self.assertEqual(
            {"model", "reasoning", "max_output_tokens", "store",
             "stream", "text", "input"},
            set(payload.keys()),
        )
        self.assertEqual(EXPECTED_MODEL, payload["model"])
        self.assertEqual({"effort": "high"},
                         payload["reasoning"])
        self.assertEqual(2048, payload["max_output_tokens"])
        self.assertIs(False, payload["store"])
        self.assertIs(False, payload["stream"])

        self.assertEqual({"format"}, set(payload["text"].keys()))
        fmt = payload["text"]["format"]
        self.assertEqual(
            {"type", "name", "schema", "strict"}, set(fmt.keys())
        )
        self.assertEqual("json_schema", fmt["type"])
        self.assertEqual("teacher_question_v2", fmt["name"])
        self.assertIs(True, fmt["strict"])
        self.assertEqual(QUESTION_SCHEMA_V2, fmt["schema"])
        self.assertIsNot(QUESTION_SCHEMA_V2, fmt["schema"])

        self.assertEqual(1, len(payload["input"]))
        message = payload["input"][0]
        self.assertEqual(
            {"type", "role", "content"}, set(message.keys())
        )
        self.assertEqual("message", message["type"])
        self.assertEqual("user", message["role"])
        # Exactly one text part, hence no image part at all.
        self.assertEqual(
            ["input_text"],
            [part["type"] for part in message["content"]],
        )
        part = message["content"][0]
        self.assertEqual({"type", "text"}, set(part.keys()))
        self.assertEqual(prompt, part["text"])

        # Exact retention: padded grade, marked objective, mixed
        # numerals and supplementary codepoints, reviewed texts.
        for exact in (fixture["grade"], fixture["objective"],
                      regions["r1"], regions["r2"], "q1", "q2"):
            with self.subTest(exact=exact):
                self.assertIn(exact, part["text"])
        self.assertEqual(before, _snapshot(frozen))

    def test_request_inputs_fail_value_free(self):
        meta = self._meta()
        from teacher_demo.question_protocol_v2 import question_prompt_v2

        fixture, png, source_base = self._source_parts()
        _, question_base, regions, slots, prompt = self._question_parts()

        for key in ("api_key", "base_url", "model", "schema",
                    "headers", "tools", "provider", "url"):
            with self.subTest(case="extra-authority", key=key):
                req = copy.deepcopy(source_base)
                req[key] = SENTINEL
                self._assert_build_error(
                    meta, req, forbidden=SENTINEL
                )
        with self.subTest(case="extra-authority-question"):
            req = copy.deepcopy(question_base)
            req["teacher_approved"] = SENTINEL
            self._assert_build_error(meta, req, forbidden=SENTINEL)

        with self.subTest(case="missing-key"):
            req = copy.deepcopy(source_base)
            del req["objective"]
            self._assert_build_error(meta, req)
        with self.subTest(case="non-mapping-request"):
            self._assert_build_error(
                meta, SENTINEL, forbidden=SENTINEL
            )
        with self.subTest(case="blank-identifier"):
            req = copy.deepcopy(source_base)
            req["request_id"] = "   "
            self._assert_build_error(meta, req)
        with self.subTest(case="lone-surrogate-string"):
            req = copy.deepcopy(source_base)
            req["objective"] = "goal " + chr(0xD800) + SENTINEL
            self._assert_build_error(meta, req, forbidden=SENTINEL)

        for kind in ("question_generation", SENTINEL, "", None, 7):
            with self.subTest(case="wrong-kind", kind=repr(kind)):
                req = copy.deepcopy(source_base)
                req["kind"] = kind
                self._assert_build_error(
                    meta, req,
                    forbidden=SENTINEL if kind == SENTINEL else None,
                )
        with self.subTest(case="wrong-kind-question"):
            req = copy.deepcopy(question_base)
            req["kind"] = "source_analysis"
            self._assert_build_error(meta, req)

        for tokens in (True, False, 16.5, "1024", None, 0, 15, -3,
                       SENTINEL):
            with self.subTest(case="bad-budget", tokens=repr(tokens)):
                req = copy.deepcopy(source_base)
                self._assert_build_error(
                    meta, req, tokens=tokens,
                    forbidden=SENTINEL if tokens == SENTINEL else None,
                )
        with self.subTest(case="budget-floor-control"):
            payload = meta.build_payload(
                copy.deepcopy(source_base), max_output_tokens=16
            )
            self.assertEqual(16, payload["max_output_tokens"])

        png_cases = []
        req = copy.deepcopy(source_base)
        req["media_type"] = "image/jpeg"
        png_cases.append(("media-jpeg", req, None))
        req = copy.deepcopy(source_base)
        req["media_type"] = SENTINEL
        png_cases.append(("media-hostile", req, SENTINEL))
        req = copy.deepcopy(source_base)
        req["render_sha256"] = "0" * 64
        png_cases.append(("hash-mismatch", req, None))
        req = copy.deepcopy(source_base)
        req["render_sha256"] = fixture["png_sha256"].upper()
        png_cases.append(("hash-uppercase", req, None))
        req = copy.deepcopy(source_base)
        req["render_sha256"] = SENTINEL
        png_cases.append(("hash-hostile", req, SENTINEL))
        req = copy.deepcopy(source_base)
        req["source_sha256"] = "ABC123"
        png_cases.append(("source-hash-malformed", req, None))
        req = copy.deepcopy(source_base)
        req["png_bytes"] = b""
        png_cases.append(("bytes-empty", req, None))
        req = copy.deepcopy(source_base)
        bad_sig = bytes([png[0] ^ 1]) + png[1:]
        req["png_bytes"] = bad_sig
        req["render_sha256"] = hashlib.sha256(bad_sig).hexdigest()
        png_cases.append(("bytes-bad-signature", req, None))
        req = copy.deepcopy(source_base)
        req["png_bytes"] = fixture["png_base64"]
        png_cases.append(("bytes-not-bytes", req, None))
        for label, req, forbidden in png_cases:
            with self.subTest(case=label):
                self._assert_build_error(
                    meta, req, forbidden=forbidden
                )

        with self.subTest(case="prompt-trailing-space"):
            req = copy.deepcopy(question_base)
            req["prompt"] = prompt + " "
            self._assert_build_error(meta, req)
        with self.subTest(case="prompt-hostile-append"):
            req = copy.deepcopy(question_base)
            req["prompt"] = prompt + SENTINEL
            self._assert_build_error(meta, req, forbidden=SENTINEL)
        with self.subTest(case="prompt-grade-mismatch"):
            req = copy.deepcopy(question_base)
            req["grade"] = req["grade"] + "x"
            self._assert_build_error(meta, req)
        for blank in ("", "   "):
            with self.subTest(case="blank-reviewed", blank=repr(blank)):
                blank_regions = {"r1": blank, "r2": regions["r2"]}
                req = copy.deepcopy(question_base)
                req["reviewed_regions"] = blank_regions
                req["prompt"] = question_prompt_v2(
                    blank_regions, slots,
                    grade=req["grade"], objective=req["objective"],
                )
                self._assert_build_error(meta, req)

    def test_payload_schema_is_detached(self):
        meta = self._meta()
        from teacher_demo.question_protocol_v2 import QUESTION_SCHEMA_V2
        from teacher_demo.source_protocol import SOURCE_SCHEMA

        source_schema_before = copy.deepcopy(SOURCE_SCHEMA)
        question_schema_before = copy.deepcopy(QUESTION_SCHEMA_V2)
        fixture, png, source_req = self._source_parts()
        _, question_req, _, _, prompt = self._question_parts()

        with self.subTest(case="source-schema"):
            first = meta.build_payload(
                source_req, max_output_tokens=64
            )
            schema = first["text"]["format"]["schema"]
            schema["properties"]["sections"]["type"] = "MUTATED"
            schema["required"].append("MUTATED")
            first["text"]["format"]["name"] = "MUTATED"
            first["input"][0]["content"][0]["text"] = "MUTATED"
            first["input"].append({"type": "message"})
            first["reasoning"]["effort"] = "MUTATED"
            second = meta.build_payload(
                source_req, max_output_tokens=64
            )
            self.assertEqual(
                source_schema_before,
                second["text"]["format"]["schema"],
            )
            self.assertEqual(
                "teacher_source_v1", second["text"]["format"]["name"]
            )
            self.assertEqual(
                fixture["source_prompt"],
                second["input"][0]["content"][0]["text"],
            )
            self.assertEqual(1, len(second["input"]))
            self.assertEqual(
                {"effort": EXPECTED_EFFORT}, second["reasoning"]
            )
            self.assertIsNot(
                first["text"]["format"]["schema"],
                second["text"]["format"]["schema"],
            )
            self.assertIsNot(
                first["text"]["format"]["schema"]["properties"],
                second["text"]["format"]["schema"]["properties"],
            )
            self.assertEqual(source_schema_before, SOURCE_SCHEMA)

        with self.subTest(case="question-schema"):
            first = meta.build_payload(
                question_req, max_output_tokens=64
            )
            schema = first["text"]["format"]["schema"]
            schema["properties"]["items"]["type"] = "MUTATED"
            schema["required"].append("MUTATED")
            first["input"][0]["content"][0]["text"] = "MUTATED"
            second = meta.build_payload(
                question_req, max_output_tokens=64
            )
            self.assertEqual(
                question_schema_before,
                second["text"]["format"]["schema"],
            )
            self.assertEqual(
                prompt, second["input"][0]["content"][0]["text"]
            )
            self.assertIsNot(
                first["text"]["format"]["schema"],
                second["text"]["format"]["schema"],
            )
            self.assertEqual(
                question_schema_before, QUESTION_SCHEMA_V2
            )

    def test_ordinary_text_parts_preserve_exact_json_strings(self):
        meta = self._meta()
        fixture = _load_fixture()
        raw = {
            "note": fixture["objective"],
            "padded": fixture["grade"],
            "count": 12,
            "nested": {
                "items": [fixture["objective"], fixture["grade"], ""]
            },
        }
        expected = copy.deepcopy(raw)
        full = _inner_text(raw)
        cut1 = len(full) // 3
        cut2 = (2 * len(full)) // 3
        # Empty individual parts are allowed; order is preserved
        # across messages with reasoning interleaved and ignored.
        output = [
            _reasoning_item(REASONING_SENTINEL),
            _message([
                _text_part(""),
                _text_part("  " + full[:cut1]),
                _text_part(""),
            ]),
            _reasoning_item(REASONING_SENTINEL),
            _message([
                _text_part(full[cut1:cut2]),
                _text_part(""),
                _text_part(full[cut2:] + "\n"),
            ]),
        ]
        data = _completed_bytes(
            full, response_id="resp_parts_001", output=output
        )
        result = meta.parse_response(data)
        self.assertEqual(
            {"raw", "model_name", "response_id", "outcome"},
            set(result.keys()),
        )
        self.assertEqual("ok", result["outcome"])
        self.assertEqual(EXPECTED_MODEL, result["model_name"])
        self.assertEqual("resp_parts_001", result["response_id"])
        self.assertEqual(expected, result["raw"])
        self.assertEqual(
            fixture["objective"], result["raw"]["note"]
        )
        self.assertEqual(fixture["grade"], result["raw"]["padded"])
        self.assertNotIn(
            REASONING_SENTINEL,
            json.dumps(result, ensure_ascii=False),
        )

    def test_model_and_response_identity_required(self):
        meta = self._meta()
        inner = _inner_text({"note": "plausible"})
        base = _completed_dict(inner)

        cases = []
        env = copy.deepcopy(base)
        env["model"] = "some-other-model"
        cases.append(("wrong-model", env, None))
        env = copy.deepcopy(base)
        env["model"] = SENTINEL
        cases.append(("hostile-model", env, SENTINEL))
        env = copy.deepcopy(base)
        del env["model"]
        cases.append(("missing-model", env, None))
        env = copy.deepcopy(base)
        env["model"] = "   "
        cases.append(("blank-model", env, None))
        env = copy.deepcopy(base)
        env["model"] = EXPECTED_MODEL + " "
        cases.append(("padded-model", env, None))
        env = copy.deepcopy(base)
        env["model"] = EXPECTED_MODEL.upper()
        cases.append(("uppercase-model", env, None))
        env = copy.deepcopy(base)
        env["model"] = 123
        cases.append(("nonstring-model", env, None))
        env = copy.deepcopy(base)
        del env["id"]
        cases.append(("missing-id", env, None))
        env = copy.deepcopy(base)
        env["id"] = ""
        cases.append(("empty-id", env, None))
        env = copy.deepcopy(base)
        env["id"] = "   "
        cases.append(("blank-id", env, None))
        env = copy.deepcopy(base)
        env["id"] = None
        cases.append(("null-id", env, None))
        env = copy.deepcopy(base)
        env["id"] = 42
        cases.append(("nonstring-id", env, None))
        env = copy.deepcopy(base)
        del env["id"]
        del env["model"]
        env["response_id"] = "resp_ok_001"
        env["model_name"] = EXPECTED_MODEL
        cases.append(("alias-identity", env, None))
        env = copy.deepcopy(base)
        env["object"] = "chat.completion"
        cases.append(("wrong-object", env, None))
        # Invalid identity wins even over a non-success lifecycle.
        env = copy.deepcopy(base)
        env["model"] = SENTINEL
        env["status"] = "failed"
        env["error"] = {"code": "x", "message": "y"}
        cases.append(("bad-identity-plus-failed", env, SENTINEL))

        for label, env, forbidden in cases:
            with self.subTest(case=label):
                self._assert_parse_error(
                    meta, _json_bytes(env), "invalid_schema",
                    forbidden=forbidden,
                )

        with self.subTest(case="top-level-output-text-shortcut"):
            env = copy.deepcopy(base)
            env["output"] = []
            env["output_text"] = inner
            self._assert_parse_error(
                meta, _json_bytes(env), "empty"
            )
        with self.subTest(case="missing-object-control"):
            env = copy.deepcopy(base)
            del env["object"]
            result = meta.parse_response(_json_bytes(env))
            self.assertEqual("ok", result["outcome"])
            self.assertEqual({"note": "plausible"}, result["raw"])

    def test_non_success_lifecycle_never_uses_plausible_content(self):
        meta = self._meta()
        inner = _inner_text(self._valid_source_raw())
        base = _completed_dict(inner)

        cases = []
        for status, outcome in (
            ("incomplete", "incomplete"),
            ("failed", "error"),
            ("queued", "unknown"),
            ("in_progress", "unknown"),
            ("some_future_status", "unknown"),
            ("cancelled", "cancelled"),
            ("", "unknown"),
        ):
            env = copy.deepcopy(base)
            env["status"] = status
            cases.append(
                ("status-" + (status or "empty"), env, outcome, None)
            )
        env = copy.deepcopy(base)
        del env["status"]
        cases.append(("status-missing", env, "unknown", None))
        env = copy.deepcopy(base)
        env["status"] = None
        cases.append(("status-null", env, "unknown", None))
        env = copy.deepcopy(base)
        env["error"] = {"code": "server_error", "message": SENTINEL}
        cases.append(
            ("completed-plus-error", env, "error", SENTINEL)
        )
        env = copy.deepcopy(base)
        env["incomplete_details"] = {"reason": "max_output_tokens"}
        cases.append(
            ("completed-plus-incomplete", env, "incomplete", None)
        )

        for label, env, outcome, forbidden in cases:
            with self.subTest(case=label):
                # Every fixture keeps valid id/model and the same
                # plausible ordinary JSON content as the success.
                self.assertEqual("resp_ok_001", env["id"])
                self.assertEqual(EXPECTED_MODEL, env["model"])
                self._assert_parse_error(
                    meta, _json_bytes(env), outcome,
                    forbidden=forbidden,
                )

    def test_refusal_empty_and_invalid_parts_are_distinct(self):
        meta = self._meta()
        inner = _inner_text({"note": "plausible"})
        refusal_part = {"type": "refusal", "refusal": SENTINEL}

        refusal_cases = [
            ("refusal-only", [_message([refusal_part])]),
            ("refusal-after-valid",
             [_message([_text_part(inner), refusal_part])]),
            ("refusal-before-valid",
             [_message([refusal_part, _text_part(inner)])]),
            ("refusal-second-message",
             [_message([_text_part(inner)]),
              _message([refusal_part])]),
        ]
        for label, output in refusal_cases:
            with self.subTest(case=label):
                data = _completed_bytes(inner, output=output)
                self._assert_parse_error(
                    meta, data, "refusal", forbidden=SENTINEL
                )

        empty_cases = [
            ("empty-output", []),
            ("reasoning-only", [_reasoning_item(inner)]),
            ("empty-content", [_message([])]),
            ("empty-text", [_message([_text_part("")])]),
            ("whitespace-text",
             [_message([_text_part("   \n\t ")])]),
            ("empty-parts-across-messages",
             [_message([_text_part("")]),
              _message([_text_part("")])]),
            ("reasoning-plus-empty",
             [_reasoning_item(REASONING_SENTINEL),
              _message([_text_part("")])]),
        ]
        for label, output in empty_cases:
            with self.subTest(case=label):
                data = _completed_bytes(inner, output=output)
                self._assert_parse_error(meta, data, "empty")

        invalid_outputs = [
            ("non-assistant-role",
             [_message([_text_part(inner)], role="user")]),
            ("function-call-item",
             [{"type": "function_call", "name": "act",
               "arguments": inner}]),
            ("unknown-item-type",
             [{"type": "future_item", "text": inner}]),
            ("unknown-content-with-valid",
             [_message([_text_part(inner),
                        {"type": "mystery_part", "text": "x"}])]),
            ("non-mapping-item", ["not-a-mapping"]),
            ("content-not-a-list",
             [{"type": "message", "role": "assistant",
               "status": "completed", "content": inner}]),
            ("non-mapping-part", [_message(["not-a-mapping"])]),
            ("non-string-text",
             [_message([{"type": "output_text", "text": 123}])]),
            ("valid-plus-malformed-message",
             [_message([_text_part(inner)]),
              {"type": "message", "role": "assistant",
               "content": "not-a-list"}]),
            ("missing-output-type", [{"role": "assistant",
                                      "content": []}]),
        ]
        for label, output in invalid_outputs:
            with self.subTest(case=label):
                data = _completed_bytes(inner, output=output)
                self._assert_parse_error(
                    meta, data, "invalid_schema"
                )

        with self.subTest(case="output-not-a-list"):
            env = _completed_dict(inner)
            env["output"] = inner
            self._assert_parse_error(
                meta, _json_bytes(env), "invalid_schema"
            )
        with self.subTest(case="message-not-completed"):
            output = [_message([_text_part(inner)],
                                status="in_progress")]
            data = _completed_bytes(inner, output=output)
            self._assert_parse_error(meta, data, "incomplete")
        with self.subTest(case="absent-role-status-control"):
            output = [_message([_text_part(inner)],
                                role=None, status=None)]
            data = _completed_bytes(inner, output=output)
            result = meta.parse_response(data)
            self.assertEqual("ok", result["outcome"])
            self.assertEqual({"note": "plausible"}, result["raw"])

    def test_strict_response_json_rejects_ambiguous_or_nonfinite_values(self):
        meta = self._meta()
        inner = '{"a": 1}'
        text_json = json.dumps(inner)
        head = (
            '"object":"response","model":"' + EXPECTED_MODEL + '",'
            '"status":"completed","error":null,'
            '"incomplete_details":null,'
        )
        tail = (
            '"output":[{"type":"message","role":"assistant",'
            '"status":"completed","content":['
            '{"type":"output_text","text":' + text_json + '}]}]}'
        )
        backslash = chr(92)

        outer_cases = [
            ("duplicate-outer-id",
             ('{"id":"resp_a","id":"resp_b",' + head + tail).encode()),
            ("duplicate-outer-model",
             ('{"id":"resp_a","model":"x",' + head + tail).encode()),
            ("duplicate-nested-usage-key",
             ('{"id":"resp_a",' + head
              + '"usage":{"t":1,"t":2},' + tail).encode()),
            ("duplicate-content-type-key",
             ('{"id":"resp_a",' + head
              + '"output":[{"type":"message","content":['
              + '{"type":"output_text","type":"output_text","text":'
              + text_json + '}]}]}').encode()),
            ("outer-nan",
             ('{"id":"resp_a",' + head
              + '"temperature":NaN,' + tail).encode()),
            ("outer-lone-surrogate",
             ('{"id":"resp' + backslash + 'ud800x",' + head
              + tail).encode()),
            ("outer-trailing-value",
             _completed_bytes(inner) + b" {}"),
            ("outer-malformed-utf8",
             _completed_bytes(inner).replace(
                 b"resp_ok_001", b"resp_\xff", 1
             )),
            ("outer-truncated", b'{"id":'),
            ("outer-empty", b""),
            ("outer-whitespace", b"   "),
        ]
        for label, data in outer_cases:
            with self.subTest(case=label):
                self._assert_parse_error(
                    meta, data, "invalid_schema"
                )
        for label, data in (
            ("outer-array", b"[1, 2]"),
            ("outer-string", b'"hello"'),
            ("outer-number", b"123"),
            ("outer-null", b"null"),
            ("outer-bool", b"true"),
        ):
            with self.subTest(case=label):
                self._assert_parse_error(
                    meta, data, "invalid_schema"
                )

        inner_cases = [
            ("duplicate-raw-key", '{"x": 1, "x": 2}', None),
            ("duplicate-nested-raw-key",
             '{"x": {"y": 1, "y": 2}}', None),
            ("inner-nan", '{"x": NaN}', None),
            ("inner-infinity", '{"x": Infinity}', None),
            ("inner-negative-infinity", '{"x": -Infinity}', None),
            ("inner-lone-surrogate",
             '{"x": "' + backslash + 'ud800"}', None),
            ("inner-trailing-value", '{"x": 1} {"y": 2}', None),
            ("inner-trailing-garbage", '{"x": 1}xyz', None),
            ("inner-malformed", '{"x": }', None),
            ("inner-malformed-hostile",
             '{"x": "' + SENTINEL + '",}', SENTINEL),
            ("raw-array", "[1, 2]", None),
            ("raw-string", '"hello"', None),
            ("raw-number", "123", None),
            ("raw-null", "null", None),
            ("raw-bool", "true", None),
            ("raw-fenced", "```json\n" + inner + "\n```", None),
            ("raw-fenced-plain", "```\n" + inner + "\n```", None),
            ("raw-prefixed-prose", "Here: " + inner, None),
        ]
        for label, text, forbidden in inner_cases:
            with self.subTest(case=label):
                self._assert_parse_error(
                    meta, _completed_bytes(text), "invalid_schema",
                    forbidden=forbidden,
                )

        with self.subTest(case="non-bytes-input"):
            text_data = _completed_bytes(inner).decode("utf-8")
            self._assert_parse_error(
                meta, text_data, "invalid_schema"
            )

    def test_response_bytes_bound_and_value_free_errors(self):
        meta = self._meta()
        self.assertEqual(
            EXPECTED_MAX_RESPONSE_BYTES, meta.MAX_RESPONSE_BYTES
        )
        self.assertEqual(4 * 1024 * 1024, meta.MAX_RESPONSE_BYTES)
        inner = _inner_text({"note": "bounded"})

        with self.subTest(case="healthy-bounded"):
            result = meta.parse_response(_completed_bytes(inner))
            self.assertEqual("ok", result["outcome"])
            self.assertEqual({"note": "bounded"}, result["raw"])

        with self.subTest(case="exactly-at-bound"):
            healthy = _completed_bytes(inner)
            exact = healthy + b" " * (
                EXPECTED_MAX_RESPONSE_BYTES - len(healthy)
            )
            self.assertEqual(EXPECTED_MAX_RESPONSE_BYTES, len(exact))
            result = meta.parse_response(exact)
            self.assertEqual("ok", result["outcome"])
            self.assertEqual({"note": "bounded"}, result["raw"])

        with self.subTest(case="oversize"):
            env = _completed_dict(inner)
            repeats = (EXPECTED_MAX_RESPONSE_BYTES // len(SENTINEL)) + 100
            env["pad"] = SENTINEL * repeats
            data = _json_bytes(env)
            self.assertGreater(len(data), EXPECTED_MAX_RESPONSE_BYTES)
            self._assert_parse_error(
                meta, data, "invalid_schema", forbidden=SENTINEL
            )

        with self.subTest(case="deep-outer-nesting"):
            data = b"[" * 3000 + b"]" * 3000
            self.assertLess(len(data), EXPECTED_MAX_RESPONSE_BYTES)
            self._assert_parse_error(meta, data, "invalid_schema")

        with self.subTest(case="deep-inner-nesting"):
            deep = '{"a":' * 3000 + "1" + "}" * 3000
            data = _completed_bytes(deep)
            self.assertLess(len(data), EXPECTED_MAX_RESPONSE_BYTES)
            self._assert_parse_error(meta, data, "invalid_schema")

    def test_missing_or_extra_application_fields_are_not_silently_repaired(self):
        meta = self._meta()
        from teacher_demo.question_protocol_v2 import (
            validate_question_proposal_v2,
        )
        from teacher_demo.source_protocol import validate_source_proposal

        fixture = _load_fixture()
        valid_source = self._valid_source_raw()

        source_cases = [
            ("missing-sections",
             {"page_continues": False, "review_notes": []}),
            ("empty-sections",
             {"sections": [], "page_continues": False,
              "review_notes": []}),
            ("missing-section-field",
             {"sections": [{"section_id": "s1", "kind": "body",
                            "text": fixture["objective"]}],
              "page_continues": False, "review_notes": []}),
        ]
        extra = copy.deepcopy(valid_source)
        extra["teacher_approved"] = True
        source_cases.append(("extra-approval-claim", extra))

        for label, raw in source_cases:
            with self.subTest(case="source-" + label):
                sent = copy.deepcopy(raw)
                data = _completed_bytes(_inner_text(raw))
                result = meta.parse_response(data)
                # Transport parsing succeeds and repairs nothing.
                self.assertEqual("ok", result["outcome"])
                self.assertEqual(sent, result["raw"])
                self.assertEqual(
                    set(sent.keys()), set(result["raw"].keys())
                )
                with self.assertRaises(ValueError):
                    validate_source_proposal(result["raw"])

        with self.subTest(case="source-valid-control"):
            data = _completed_bytes(_inner_text(valid_source))
            result = meta.parse_response(data)
            self.assertEqual("ok", result["outcome"])
            self.assertEqual(
                valid_source,
                validate_source_proposal(result["raw"]),
            )

        _, _, regions, slots, _ = self._question_parts()
        question_cases = [
            ("missing-items", {}),
            ("empty-items", {"items": []}),
            ("extra-approval-claim",
             {"items": [], "teacher_approved": True}),
            ("extra-field-only", {"approved": True}),
        ]
        for label, raw in question_cases:
            with self.subTest(case="question-" + label):
                sent = copy.deepcopy(raw)
                data = _completed_bytes(_inner_text(raw))
                result = meta.parse_response(data)
                self.assertEqual("ok", result["outcome"])
                self.assertEqual(sent, result["raw"])
                self.assertEqual(
                    set(sent.keys()), set(result["raw"].keys())
                )
                validation = validate_question_proposal_v2(
                    result["raw"], regions, slots
                )
                self.assertFalse(validation.ok)
                self.assertTrue(validation.issues)
                self.assertFalse(validation.cognitive_level_certified)
                self.assertFalse(
                    validation.factual_entailment_certified
                )

    def test_returned_response_has_independent_ownership(self):
        meta = self._meta()
        raw = {"outer": {"items": [1, {"deep": "value"}],
                         "note": "keep"}}
        expected = copy.deepcopy(raw)
        env = _completed_dict(
            _inner_text(raw),
            response_id="resp_own_001",
            output=[
                _reasoning_item(REASONING_SENTINEL),
                _message([_text_part(_inner_text(raw))]),
            ],
        )
        env["usage"] = {"input_tokens": 3, "note": USAGE_SENTINEL}
        env["headers"] = {"x-provider": USAGE_SENTINEL}
        env["teacher_approved"] = True
        env["model_name"] = "alias-that-must-be-ignored"
        data = _json_bytes(env)

        first = meta.parse_response(data)
        second = meta.parse_response(data)
        for result in (first, second):
            self.assertEqual(
                {"raw", "model_name", "response_id", "outcome"},
                set(result.keys()),
            )
            self.assertEqual("ok", result["outcome"])
            self.assertEqual(EXPECTED_MODEL, result["model_name"])
            self.assertEqual("resp_own_001", result["response_id"])
            self.assertEqual(expected, result["raw"])
            rendered = json.dumps(result, ensure_ascii=False)
            self.assertNotIn(REASONING_SENTINEL, rendered)
            self.assertNotIn(USAGE_SENTINEL, rendered)
            self.assertNotIn("teacher_approved", result["raw"])
            self.assertNotIn("alias-that-must-be-ignored", rendered)

        self.assertIsNot(first, second)
        self.assertIsNot(first["raw"], second["raw"])
        self.assertIsNot(
            first["raw"]["outer"], second["raw"]["outer"]
        )
        self.assertIsNot(
            first["raw"]["outer"]["items"],
            second["raw"]["outer"]["items"],
        )

        first["raw"]["outer"]["items"].append("MUTATED")
        first["raw"]["outer"]["items"][1]["deep"] = "MUTATED"
        first["raw"]["new_key"] = "MUTATED"
        first["outcome"] = "MUTATED"
        self.assertEqual(expected, second["raw"])
        self.assertEqual("ok", second["outcome"])
        third = meta.parse_response(data)
        self.assertEqual(expected, third["raw"])
        self.assertEqual("ok", third["outcome"])


if __name__ == "__main__":
    unittest.main()









