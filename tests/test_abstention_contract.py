"""Abstention prompt/schema/domain contradiction (synthetic only).

Honest abstentions must carry no draft content. The domain already
enforces this; the outgoing wire schema must match it. SAFETY must
pass before and after the schema repair; REPAIR must fail before
and pass after. Weakening the domain validator is forbidden.
Quote grounding across texts stays a domain-only gate.
"""
import copy, json, sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jsonschema import Draft202012Validator
from teacher_demo.meta_protocol import build_payload, parse_response
from teacher_demo.question_protocol_v2 import (
    QUESTION_SCHEMA_V2, question_prompt_v2, validate_question_proposal_v2)
HEADING = "Synthetic heading for the contract test."
BODY = "Alpha particles drift leftward, then cluster together, then settle downward."
QUOTE = "cluster together"
REGIONS, GRADE, OBJECTIVE = {"r1": HEADING, "r2": BODY}, "Grade 4", "Describe the synthetic sequence in order."
SLOTS = [{"slot_id": "slot-remember", "level": "remember"},
         {"slot_id": "slot-understand", "level": "understand"}]

def draft_item(slot):
    return {"slot_id": slot["slot_id"], "level": slot["level"], "status": "draft",
            "question": "What happens after the particles drift leftward?",
            "answer_guidance": "They cluster together, then settle downward.",
            "cognitive_explanation": "Recall the stated sequence; fits the grade objective.",
            "hypothetical_assumptions": [], "evaluate_criteria": [], "create_rubric": [],
            "evidence": [{"region_id": "r2", "quote": QUOTE}],
            "required_order_relationships": [], "abstain_reason": None}

def abstain_item(slot):
    return {"slot_id": slot["slot_id"], "level": slot["level"], "status": "abstain",
            "question": "", "answer_guidance": "", "cognitive_explanation": "",
            "hypothetical_assumptions": [], "evaluate_criteria": [], "create_rubric": [],
            "evidence": [], "required_order_relationships": [],
            "abstain_reason": "The synthetic source does not support this slot."}

def mixed_proposal():
    return {"items": [draft_item(SLOTS[0]), abstain_item(SLOTS[1])]}

def all_abstain_proposal():
    return {"items": [abstain_item(SLOTS[0]), abstain_item(SLOTS[1])]}

ABSTAIN_MUTATIONS = (
    ("question", "Synthetic question that must not appear on an abstention."),
    ("answer_guidance", "Synthetic guidance that must not appear on an abstention."),
    ("cognitive_explanation", "Synthetic explanation that must not appear on an abstention."),
    ("hypothetical_assumptions", ["Synthetic hypothetical assumption."]),
    ("evaluate_criteria", [{"text": "Synthetic evaluate criterion.", "origin": "objective", "supporting_facts": []}]),
    ("create_rubric", [{"text": "Synthetic create criterion.", "origin": "objective", "supporting_facts": []}]),
    ("evidence", [{"region_id": "r2", "quote": QUOTE}]),
    ("required_order_relationships", [["synthetic-first", "synthetic-second"]]),
)
DRAFT_EMPTY_CASES = (
    ("question", ""), ("answer_guidance", ""), ("cognitive_explanation", ""), ("evidence", []))

class AbstentionContractTest(unittest.TestCase):
    def setUp(self):
        self.prompt = question_prompt_v2(REGIONS, SLOTS, grade=GRADE, objective=OBJECTIVE)
        self.request = {"kind": "question_generation", "request_id": "req-synthetic-001",
                        "source_id": "src-synthetic-001", "prompt": self.prompt,
                        "reviewed_regions": dict(REGIONS), "requested_slots": copy.deepcopy(SLOTS),
                        "grade": GRADE, "objective": OBJECTIVE}
        self.schema = build_payload(self.request, max_output_tokens=2048)["text"]["format"]["schema"]
        self.validator = Draft202012Validator(self.schema)

    def schema_errors(self, proposal):
        return list(self.validator.iter_errors(proposal))

    def assert_accepted(self, proposal):
        self.assertEqual([], self.schema_errors(proposal))
        result = validate_question_proposal_v2(proposal, REGIONS, SLOTS)
        self.assertTrue(result.ok, result.issues)
        self.assertFalse(result.cognitive_level_certified)
        self.assertFalse(result.factual_entailment_certified)
        return result

    def assert_refused(self, proposal):
        result = validate_question_proposal_v2(proposal, REGIONS, SLOTS)
        self.assertFalse(result.ok, result.issues)
        self.assertTrue(self.schema_errors(proposal))

    def envelope_bytes(self, raw):
        outer = {"id": "resp-synthetic-001", "object": "response", "model": "muse-spark-1.3",
                 "status": "completed", "error": None, "incomplete_details": None,
                 "output": [{"type": "message", "role": "assistant", "status": "completed",
                             "content": [{"type": "output_text",
                                          "text": json.dumps(raw, ensure_ascii=False)}]}]}
        return json.dumps(outer, ensure_ascii=False).encode("utf-8")

    def test_safety_valid_mixed_proposal_and_payload_integrity(self):
        """Must pass before and after the repair."""
        with self.subTest(case="healthy-mixed"):
            result = self.assert_accepted(mixed_proposal())
            self.assertEqual((1, 1), (result.draft_count, result.abstain_count))
        with self.subTest(case="healthy-all-abstain"):
            result = self.assert_accepted(all_abstain_proposal())
            self.assertEqual((0, 2), (result.draft_count, result.abstain_count))
        with self.subTest(case="prompt-exact-and-detached"):
            request_before = copy.deepcopy(self.request)
            base_before = copy.deepcopy(QUESTION_SCHEMA_V2)
            outgoing_before = copy.deepcopy(self.schema)
            disposable = build_payload(self.request, max_output_tokens=2048)
            self.assertEqual(request_before, self.request)
            self.assertEqual(self.prompt, disposable["input"][0]["content"][0]["text"])
            for exact in (GRADE, OBJECTIVE, HEADING, BODY, "slot-remember", "slot-understand"):
                self.assertIn(exact, self.prompt)
            disposable["text"]["format"]["schema"]["properties"]["items"]["type"] = "MUTATED"
            fresh = build_payload(self.request, max_output_tokens=2048)["text"]["format"]["schema"]
            self.assertEqual(base_before, QUESTION_SCHEMA_V2)
            self.assertEqual(outgoing_before, self.schema)
            self.assertEqual(outgoing_before, fresh)
            self.assertEqual([], self.schema_errors(mixed_proposal()))
        with self.subTest(case="meta-strict-shape"):
            schema = build_payload(self.request, max_output_tokens=2048)["text"]["format"]["schema"]
            self.assertEqual("object", schema.get("type"))
            violations, stack = [], [schema]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    if "allOf" in node or "oneOf" in node:
                        violations.append("combinator")
                    if node.get("type") == "object" or "properties" in node:
                        if node.get("additionalProperties") is not False:
                            violations.append("additionalProperties")
                        if set(node.get("properties", {})) != set(node.get("required", [])):
                            violations.append("required")
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
            self.assertEqual([], violations)
        with self.subTest(case="bad-quote-domain-only"):
            proposal = mixed_proposal()
            proposal["items"][0]["evidence"] = [{"region_id": "r2", "quote": "Text nowhere in the synthetic source."}]
            self.assertEqual([], self.schema_errors(proposal))
            self.assertFalse(validate_question_proposal_v2(proposal, REGIONS, SLOTS).ok)
        with self.subTest(case="raw-unchanged-no-repair"):
            raw = mixed_proposal()
            raw["items"][1]["cognitive_explanation"] = "Synthetic explanation on an abstention."
            snapshot = copy.deepcopy(raw)
            result = parse_response(self.envelope_bytes(raw))
            self.assertEqual("ok", result["outcome"])
            self.assertEqual(snapshot, result["raw"])
            self.assertEqual(snapshot, raw)
            self.assertFalse(validate_question_proposal_v2(result["raw"], REGIONS, SLOTS).ok)

    def test_repair_outgoing_schema_matches_domain_abstain_contract(self):
        """Must fail before and pass after; domain already refuses each case."""
        for field, value in ABSTAIN_MUTATIONS:
            with self.subTest(case="abstain-nonempty", field=field):
                proposal = mixed_proposal()
                proposal["items"][1][field] = copy.deepcopy(value)
                self.assert_refused(proposal)
        for blank in (None, "", "   "):
            with self.subTest(case="abstain-reason-blank", blank=repr(blank)):
                proposal = mixed_proposal()
                proposal["items"][1]["abstain_reason"] = blank
                self.assert_refused(proposal)
        with self.subTest(case="draft-reason-non-null"):
            proposal = mixed_proposal()
            proposal["items"][0]["abstain_reason"] = "Synthetic reason that must not appear on a draft."
            self.assert_refused(proposal)
        for field, value in DRAFT_EMPTY_CASES:
            with self.subTest(case="draft-required-empty", field=field):
                proposal = mixed_proposal()
                proposal["items"][0][field] = copy.deepcopy(value)
                self.assert_refused(proposal)

if __name__ == "__main__":
    unittest.main()
