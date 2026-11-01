"""Socket-free pure tests for teacher HTTP boundary helpers."""
import copy
import dataclasses
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from teacher_demo.http_teacher_helpers import (  # noqa: E402
    canonical_size,
    deep_freeze,
    detached_json_value,
    snapshot_payload,
)


@dataclasses.dataclass(frozen=True)
class FakeSnapshot:
    text: str = "  اَلنَّصُّ ١٢٣  "
    pair: tuple = ("a", 1)


class TeacherHttpHelperTests(unittest.TestCase):
    def test_snapshot_payload_lists_and_exact_unicode(self):
        payload = snapshot_payload(FakeSnapshot())
        self.assertEqual(payload, {
            "text": "  اَلنَّصُّ ١٢٣  ", "pair": ["a", 1]})

    def test_deep_freeze_is_recursive_and_deepcopy_safe(self):
        frozen = deep_freeze({"outer": {"items": [1, {"x": "y"}]}})
        with self.assertRaises(TypeError):
            frozen["outer"] = {}
        with self.assertRaises(TypeError):
            frozen["outer"]["items"][1]["x"] = "z"
        cloned = copy.deepcopy(dict(frozen))
        self.assertEqual(cloned["outer"]["items"][1]["x"], "y")

    def test_detached_value_is_bounded_and_independent(self):
        original = {"raw": {"items": [{"text": "  نَصٌّ  "}]}}
        copied = detached_json_value(original, 10_000)
        original["raw"]["items"][0]["text"] = "changed"
        self.assertEqual(
            copied["raw"]["items"][0]["text"], "  نَصٌّ  ")
        self.assertGreater(canonical_size(copied), 0)

    def test_detached_value_rejects_unsafe_shapes(self):
        cyclic = []
        cyclic.append(cyclic)
        for bad in ({"x": "\ud800"}, {"x": object()}, cyclic,
                    {"x": float("inf")}, {1: "x"}):
            with self.subTest(kind=type(bad).__name__):
                with self.assertRaises(ValueError):
                    detached_json_value(bad, 10_000)
        with self.assertRaises(ValueError):
            detached_json_value({"x": "1234567890"}, 4)


if __name__ == "__main__":
    unittest.main()
