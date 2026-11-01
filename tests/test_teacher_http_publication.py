"""Socket-free tests for ``teacher_demo.http_teacher_publication``.

Pure retained-cost accounting for the HTTP publication authority:
session graph composition, admission arithmetic, terminal reserves,
and callable-bound payload costs. No HTTP, sockets, or provider.
Real domain state comes from the staged pure fixture.
"""
import copy
import pathlib
import sys
import unittest
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from teacher_demo.http_teacher_cost import logical_payload_bytes  # noqa: E402
from teacher_demo.http_teacher_helpers import deep_freeze  # noqa: E402
from teacher_demo.http_teacher_publication import (  # noqa: E402
    TERMINAL_SLACK_BYTES,
    accounted_total,
    admission_allows,
    callable_payload_cost,
    envelope_cost,
    measure_terminal_reserve,
    prune_terminal_reserves,
    session_graph_cost,
    working_request_ids,
)
from test_question_request_prompt import ready_workspace  # noqa: E402


@dataclass(frozen=True)
class FakeDecoded:
    original_bytes: bytes
    png_bytes: bytes
    media_type: str
    page_count: int = 1


def session_cost(workspace, pending=("g", "o", "r"), results=None,
                 raw_status=None):
    return session_graph_cost(
        "tok", pending,
        FakeDecoded(b"original-bytes", b"png-bytes", "image/jpeg"),
        workspace, results or {}, raw_status or {})


class SessionGraphCostTests(unittest.TestCase):
    def test_positive_and_exact_pending_delta(self):
        ws, _ = ready_workspace()
        base = session_cost(ws)
        self.assertGreater(base, 0)
        bigger = session_cost(ws, pending=("gxxxx", "o", "r"))
        self.assertEqual(bigger - base, 4)

    def test_counts_invisible_bound_data_and_failed_history(self):
        ws, _ = ready_workspace()
        self.assertEqual(ws.snapshot().questions, ())
        previous = session_cost(ws)
        qid = ws.begin_question_request("q-pub-0")
        self.assertGreater(session_cost(ws), previous)
        ws.finish_question_request(qid, "error")
        previous = session_cost(ws)
        self.assertEqual(ws.snapshot().questions, ())
        for index in (1, 2):
            qid = ws.begin_question_request(f"q-pub-{index}")
            ws.finish_question_request(qid, "error")
            current = session_cost(ws)
            self.assertGreater(current, previous)
            previous = current

    def test_counts_raw_results_and_refuses_unsupported_payload(self):
        ws, _ = ready_workspace()
        envelope = {"raw": {"sections": []}, "outcome": "ok"}
        with_raw = session_cost(
            ws, results={"r1": ("source", envelope, 123)})
        self.assertGreater(with_raw, session_cost(ws))
        with self.assertRaises(ValueError):
            session_cost(ws, results={"r1": ("source", object(), 0)})

    def test_empty_session_graph_still_positive(self):
        result = session_graph_cost("tok", None, None, None, {}, {})
        self.assertGreater(result, 0)
        self.assertEqual(result, session_graph_cost(
            "tok", None, None, None, {}, {}))


class AdmissionArithmeticTests(unittest.TestCase):
    def test_exact_boundaries(self):
        self.assertTrue(admission_allows(
            100, 10, 5, 1000, per_session_cap=115, total_cap=1115))
        self.assertFalse(admission_allows(
            100, 10, 5, 1000, per_session_cap=114, total_cap=1115))
        self.assertFalse(admission_allows(
            100, 10, 5, 1000, per_session_cap=115, total_cap=1114))

    def test_accounted_total_sums_all_components(self):
        self.assertEqual(
            accounted_total(100, {"a": 3, "b": 4}, {"x": 5}), 112)
        self.assertEqual(accounted_total(0, {}, {}), 0)

    def test_prune_reserves_keeps_only_working_without_mutation(self):
        reserves = {"a": 1, "b": 2, "c": 3}
        self.assertEqual(
            prune_terminal_reserves(reserves, ("b",)), {"b": 2})
        self.assertEqual(reserves, {"a": 1, "b": 2, "c": 3})


class TerminalReserveTests(unittest.TestCase):
    def test_working_ids_track_lifecycle(self):
        ws, _ = ready_workspace()
        qid = ws.begin_question_request("q-reserve")
        self.assertIn(qid, working_request_ids(ws.snapshot()))
        ws.finish_question_request(qid, "error")
        self.assertNotIn(qid, working_request_ids(ws.snapshot()))

    def test_reserve_covers_actual_finish_delta_plus_slack_only(self):
        ws, _ = ready_workspace()
        qid = ws.begin_question_request("q-reserve-cover")
        before = ws.snapshot()
        reserve = measure_terminal_reserve(ws, qid, "questions")
        self.assertEqual(ws.snapshot(), before)  # probe never mutates
        self.assertIn(qid, working_request_ids(ws.snapshot()))
        finished = copy.deepcopy(ws)
        finished.finish_question_request(qid, "invalid_schema")
        delta = (logical_payload_bytes(finished)
                 - logical_payload_bytes(ws))
        self.assertEqual(reserve, max(0, delta) + TERMINAL_SLACK_BYTES)
        self.assertGreater(reserve, 0)


class PayloadCostTests(unittest.TestCase):
    def test_envelope_cost_exact_delta(self):
        small = {"raw": {"items": []}, "outcome": "ok"}
        large = {"raw": {"items": []}, "outcome": "okay!"}
        self.assertEqual(envelope_cost(large) - envelope_cost(small), 3)

    def test_callable_cost_counts_png_and_matches_frozen_form(self):
        plain = {"kind": "source_analysis", "png_bytes": b"a" * 10,
                 "reviewed": {"r": "text"}}
        smaller = {"kind": "source_analysis", "png_bytes": b"a" * 4,
                   "reviewed": {"r": "text"}}
        self.assertEqual(
            callable_payload_cost(plain)
            - callable_payload_cost(smaller), 6)
        self.assertEqual(
            callable_payload_cost(deep_freeze(plain)),
            callable_payload_cost(plain))


class ParentRunSourceSyntaxTests(unittest.TestCase):
    def test_http_sources_parse_without_executing_them(self):
        import ast
        root = pathlib.Path(__file__).resolve().parents[1]
        for rel in ("src/teacher_demo/http_app.py",
                    "tests/test_http_teacher_safety.py",
                    "tests/test_http_teacher_publication.py"):
            source = (root / rel).read_text(encoding="utf-8")
            ast.parse(source, filename=rel)


if __name__ == "__main__":
    unittest.main()
