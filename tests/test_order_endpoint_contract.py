"""Contract: generator order endpoints must use the reviewed mapping.

Each ``required_order_relationships`` endpoint must be present in the
``reviewed_regions`` mapping with nonblank text and match exactly;
validation never mutates its inputs. Explicit synthetic fixtures only.
"""
import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from teacher_demo.question_protocol_v2 import validate_question_proposal_v2  # noqa: E402

ALPHA = "r-alpha"
BETA = "r-beta"
BLANK = "r-blank"
UNKNOWN = "r-unknown"
# Known elsewhere in the workspace, but omitted from the reviewed mapping.
WORKSPACE_ONLY = "r-workspace-only"
QUOTE = "Alpha exact sentence."


def _reviewed(extra=None):
    regions = {ALPHA: QUOTE + " More alpha text.", BETA: "Beta exact sentence."}
    if extra:
        regions.update(extra)
    return regions


def _slots():
    return [{"slot_id": "s-remember", "level": "remember"}]


def _proposal(orders):
    return {"items": [{
        "slot_id": "s-remember",
        "level": "remember",
        "status": "draft",
        "question": "What does the alpha region state?",
        "answer_guidance": "It states the alpha exact sentence.",
        "cognitive_explanation": "Recalling a stated fact fits remember.",
        "hypothetical_assumptions": [],
        "evaluate_criteria": [],
        "create_rubric": [],
        "evidence": [{"region_id": ALPHA, "quote": QUOTE}],
        "required_order_relationships": orders,
        "abstain_reason": None,
    }]}


class OrderEndpointContractTest(unittest.TestCase):
    def _run(self, orders, reviewed=None):
        proposal = _proposal(orders)
        regions = _reviewed() if reviewed is None else reviewed
        slots = _slots()
        snapshot = copy.deepcopy((proposal, regions, slots))
        result = validate_question_proposal_v2(proposal, regions, slots)
        # Raw immutability: nothing stripped, rewritten, or deduplicated.
        self.assertEqual((proposal, regions, slots), snapshot)
        return result, proposal

    def test_safety_usable_reviewed_endpoints_stay_accepted(self):
        ok_orders = [
            ("distinct known pair", [[ALPHA, BETA]]),
            ("reversed known pair", [[BETA, ALPHA]]),
            ("both directions, no cycle rule", [[ALPHA, BETA], [BETA, ALPHA]]),
            ("no order relationships", []),
        ]
        for label, orders in ok_orders:
            with self.subTest(label=label):
                result, _ = self._run(orders)
                self.assertTrue(result.ok)
        repeated = [[ALPHA, BETA], [ALPHA, BETA]]
        result, proposal = self._run([list(pair) for pair in repeated])
        self.assertTrue(result.ok)
        self.assertEqual(
            proposal["items"][0]["required_order_relationships"], repeated)
        for same in (ALPHA, BETA):
            with self.subTest(label="same endpoint refused", endpoint=same):
                result, _ = self._run([[same, same]])
                self.assertFalse(result.ok)

    def test_repair_unusable_endpoints_are_rejected(self):
        # The evidence quote stays exactly anchored throughout, so no
        # rejection below can come from quote/evidence failure.
        bad_pairs = [
            ("unknown before", [UNKNOWN, BETA]),
            ("unknown after", [ALPHA, UNKNOWN]),
            ("workspace-only before", [WORKSPACE_ONLY, BETA]),
            ("workspace-only after", [ALPHA, WORKSPACE_ONLY]),
            ("padded exactness before", [ALPHA + " ", BETA]),
            ("padded exactness after", [ALPHA, BETA + " "]),
        ]
        for label, pair in bad_pairs:
            with self.subTest(label=label):
                result, _ = self._run([pair])
                self.assertFalse(result.ok)
        for blank_text in ("", "   "):
            reviewed = _reviewed({BLANK: blank_text})
            for label, pair in (("blank before", [BLANK, BETA]),
                                ("blank after", [ALPHA, BLANK])):
                with self.subTest(label=label, blank_text=repr(blank_text)):
                    result, _ = self._run([pair], reviewed=reviewed)
                    self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
