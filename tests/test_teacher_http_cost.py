"""Focused tests for ``teacher_demo.http_teacher_cost``.

Synthetic fixtures only. The metric under test is a deterministic
logical-byte cost of the retained object graph (not snapshot size,
repr, pickle, or heap size). Real domain state comes from the staged
pure fixture in ``test_question_request_prompt``.
"""
import copy
import math
import pathlib
import sys
import unittest
from dataclasses import dataclass, field
from types import MappingProxyType

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from source_contract import SourcePage, Workspace as CoreWorkspace  # noqa: E402
from teacher_demo import TeacherWorkspace  # noqa: E402
from teacher_demo.http_teacher_cost import logical_payload_bytes as cost  # noqa: E402
from test_question_request_prompt import (  # noqa: E402
    GRADE, OBJECTIVE, T2, make_page, raw_source, ready_workspace)


@dataclass
class Holder:
    name: str
    payload: dict
    tags: list = field(default_factory=list)


def page_with_rights(rights_ref):
    return SourcePage(
        source_id="synthetic-teacher-page-1",
        source_sha256="a" * 64, page_index=0, render_sha256="b" * 64,
        width=1000, height=1400, rights_ref=rights_ref,
        coordinate_convention="normalized_0_1000_top_left")


class ScalarCostTests(unittest.TestCase):
    def test_scalars_positive_int_and_repeatable(self):
        for value in (None, True, False, 0, 1, -5, 1.5, "", b""):
            result = cost(value)
            self.assertIsInstance(result, int)
            self.assertGreater(result, 0)
            self.assertEqual(result, cost(value))

    def test_exact_utf8_deltas_unchanged_shape(self):
        pairs = [
            ("hello world", "hello"),
            ("a b", "ab"),  # whitespace counts, never normalized away
            ("\u0646\u064e", "\u0646"),  # tashkeel counts
            ("\u0661\u0662\u0663\u0664", "\u0661\u0662\u0663"),  # numerals
            ("x\U00010348", "x"),  # supplementary codepoint: 4 UTF-8 bytes
            ("  ", ""),
            (T2 + "x", T2),
        ]
        for bigger, smaller in pairs:
            expected = (len(bigger.encode("utf-8"))
                        - len(smaller.encode("utf-8")))
            self.assertEqual(cost(bigger) - cost(smaller), expected)

    def test_exact_raw_byte_deltas_unchanged_shape(self):
        self.assertEqual(cost(b"\x00\xff" * 10) - cost(b"\x00\xff" * 4), 12)
        self.assertEqual(cost([b"abcd"]) - cost([b"a"]), 3)
        self.assertEqual(cost({"k": b"abcd"}) - cost({"k": b"a"}), 3)

    def test_arbitrary_size_ints_without_decimal_string(self):
        big = 2 ** 10000
        self.assertGreater(cost(big), cost(2 ** 10))
        self.assertGreater(cost(-big), 0)
        self.assertEqual(cost(10 ** 5000), cost(10 ** 5000))
        self.assertGreater(cost(10 ** 5000), cost(10 ** 50))

    def test_nonfinite_floats_and_surrogates_refused_value_free(self):
        for bad in (math.inf, -math.inf, math.nan):
            with self.assertRaises(ValueError):
                cost(bad)
        secret = "ok-\ud800-TOPSECRET"
        with self.assertRaises(ValueError) as caught:
            cost(secret)
        self.assertIs(type(caught.exception), ValueError)
        self.assertNotIn("TOPSECRET", str(caught.exception))

    def test_unsupported_error_does_not_echo_caller_content(self):
        with self.assertRaises(ValueError) as caught:
            cost({"TOPSECRETKEY": object()})
        self.assertNotIn("TOPSECRETKEY", str(caught.exception))


class ContainerCostTests(unittest.TestCase):
    def test_mapping_and_set_ordering_independence(self):
        first = {"a": 1, "b": [1, 2], "c": "x"}
        second = {}
        second["c"] = "x"
        second["b"] = [1, 2]
        second["a"] = 1
        self.assertEqual(cost(first), cost(second))
        set_one = {1, 2, 3, "x", (1, 2)}
        set_two = set()
        for item in ((1, 2), "x", 3, 2, 1):
            set_two.add(item)
        self.assertEqual(cost(set_one), cost(set_two))
        self.assertEqual(cost(frozenset(set_one)), cost(set_one))
        self.assertEqual(cost(MappingProxyType({"a": 1})), cost({"a": 1}))

    def test_added_nested_and_raw_payload_increases_cost(self):
        base = {"a": [1, {"b": b"x"}]}
        bigger = {"a": [1, {"b": b"x", "c": "extra retained text"}]}
        self.assertGreater(cost(bigger), cost(base))
        self.assertLess(cost([]), cost([[]]))
        self.assertLess(cost([[]]), cost([[b"payload"]]))

    def test_shared_child_counted_per_reference(self):
        shared = ["shared-payload"]
        self.assertEqual(cost([shared, shared]),
                         cost([shared, copy.deepcopy(shared)]))
        self.assertGreater(cost([shared, shared]), cost([shared]))

    def test_deepcopy_stability_and_no_mutation_plain_graph(self):
        graph = {"k": ["text", {"n": (1, 2.5, None)}], "s": {1, 2}}
        before = copy.deepcopy(graph)
        result = cost(graph)
        self.assertEqual(graph, before)
        self.assertEqual(result, cost(copy.deepcopy(graph)))

    def test_cycles_refused(self):
        ring = []
        ring.append(ring)
        with self.assertRaises(ValueError):
            cost(ring)
        mapping = {}
        mapping["self"] = mapping
        with self.assertRaises(ValueError):
            cost(mapping)
        left, right = [], []
        left.append(right)
        right.append(left)
        with self.assertRaises(ValueError):
            cost(left)

    def test_unsupported_values_refused(self):
        for bad in (object(), object, int, (lambda: 1), 1 + 2j,
                    bytearray(b"x")):
            with self.assertRaises(ValueError):
                cost(bad)

    def test_over_limit_depth_refused(self):
        root = []
        cursor = root
        for _ in range(5000):
            nxt = []
            cursor.append(nxt)
            cursor = nxt
        with self.assertRaises(ValueError):
            cost(root)


class DataclassCostTests(unittest.TestCase):
    def test_all_stored_children_count_exact_delta(self):
        small = Holder("n", {"k": [b"a"]}, [])
        large = Holder("n", {"k": [b"abcd"]}, [])
        self.assertEqual(cost(large) - cost(small), 3)

    def test_caller_owned_nested_containers_are_followed(self):
        inner = {"k": [1]}
        holder = Holder("n", inner, [])
        before = cost(holder)
        inner["k"].append("more retained text")
        self.assertGreater(cost(holder), before)
        before = cost(holder)
        holder.tags.append("another retained tag")
        self.assertGreater(cost(holder), before)

    def test_domain_dataclass_exact_delta(self):
        small = page_with_rights("synthetic-fixture-rights-note")
        large = page_with_rights("synthetic-fixture-rights-note-extra")
        self.assertEqual(cost(large) - cost(small), len("-extra".encode()))
        self.assertGreater(cost(make_page()), 0)


class WorkspaceCostTests(unittest.TestCase):
    def test_workspace_repeat_deepcopy_and_no_mutation(self):
        ws, _ = ready_workspace()
        snap = ws.snapshot()
        result = cost(ws)
        self.assertGreater(result, 0)
        self.assertEqual(result, cost(ws))
        self.assertEqual(ws.snapshot(), snap)
        self.assertEqual(result, cost(copy.deepcopy(ws)))

    def test_core_workspace_counted_directly(self):
        ws, _ = ready_workspace()
        core_result = cost(ws.core_workspace)
        self.assertGreater(core_result, 0)
        self.assertEqual(core_result, cost(copy.deepcopy(ws.core_workspace)))
        self.assertGreater(cost(ws), core_result)
        small = CoreWorkspace(page_with_rights("synthetic-fixture-rights-note"))
        large = CoreWorkspace(
            page_with_rights("synthetic-fixture-rights-note-extra"))
        self.assertEqual(cost(large) - cost(small), len("-extra".encode()))

    def test_raw_source_proposal_retention_increases_cost(self):
        ws = TeacherWorkspace(make_page(), grade=GRADE, objective=OBJECTIVE)
        before = cost(ws)
        rid = ws.begin_source_request("src-cost-1")
        ws.admit_source_proposal(rid, raw_source())
        ws.finish_source_request(rid, "ok")
        self.assertGreater(cost(ws), before)

    def test_question_request_bound_data_counts_while_invisible(self):
        ws, _ = ready_workspace()
        self.assertEqual(ws.snapshot().questions, ())
        before = cost(ws)
        core_before = cost(ws.core_workspace)
        ws.begin_question_request("q-bound-cost")
        self.assertEqual(ws.snapshot().questions, ())
        self.assertGreater(cost(ws), before)
        self.assertEqual(cost(ws.core_workspace), core_before)

    def test_repeated_failed_request_history_increases_cost(self):
        ws, _ = ready_workspace()
        previous = cost(ws)
        for index in range(3):
            qid = ws.begin_question_request(f"q-failed-{index}")
            ws.finish_question_request(qid, "error")
            current = cost(ws)
            self.assertGreater(current, previous)
            previous = current
        self.assertEqual(ws.snapshot().questions, ())


class LimitAndShapeTests(unittest.TestCase):
    def test_node_limit_refused(self):
        graph = [[0] * 1000 for _ in range(2000)]
        with self.assertRaises(ValueError):
            cost(graph)

    def test_shared_diamond_bounded_not_exponential_hang(self):
        node = ["x"]
        for _ in range(30):
            node = [node, node]
        with self.assertRaises(ValueError):
            cost(node)

    def test_dataclass_cycle_refused(self):
        holder = Holder("n", {}, [])
        holder.payload["self"] = holder
        with self.assertRaises(ValueError):
            cost(holder)

    def test_ordered_dict_mapping_counted_like_dict(self):
        from collections import OrderedDict
        ordered = OrderedDict([("a", 1), ("b", 2)])
        self.assertEqual(cost(ordered), cost({"a": 1, "b": 2}))

    def test_slots_dataclass_children_count(self):
        if sys.version_info < (3, 10):
            self.skipTest("slots dataclasses need Python 3.10+")

        @dataclass(slots=True)
        class SlotHolder:
            name: str
            payload: dict

        small = SlotHolder("n", {"k": b"a"})
        large = SlotHolder("n", {"k": b"abcd"})
        self.assertEqual(cost(large) - cost(small), 3)


if __name__ == "__main__":
    unittest.main()
