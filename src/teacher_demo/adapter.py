"""Teacher-review adapter over ``source_contract.Workspace``.

The adapter is the single snapshot authority for status, approval
blocks, linked dependents, and preview. It reuses the accepted core
for source identity, fact anchors, dependency resolution, and teacher
decisions; it never exposes the core create-from-blank path.
"""
from __future__ import annotations

import copy
import uuid
from collections import Counter
from dataclasses import dataclass, field

from source_contract import (
    FactAnchor,
    QuestionProposal,
    RegionProposal,
    SourcePage,
    Workspace,
)

from .question_protocol_v2 import (
    question_prompt_v2,
    validate_question_proposal_v2,
)
from .source_protocol import validate_source_proposal

FULL_PAGE_BOX_ORIGIN = "decoder_full_page_extent_not_model_geometry"
_INCLUSIONS = ("included", "excluded", "unresolved")
_TERMINAL_FAILURES = (
    "incomplete", "invalid_schema", "empty", "error", "refusal",
    "timeout", "rate_limited", "unknown", "cancelled",
)


class TeacherDemoError(Exception):
    """Base class for adapter errors."""


class RequestStateError(TeacherDemoError):
    """A request is unknown, closed, or superseded; its result is refused."""


@dataclass(frozen=True)
class DecodedSourcePage:
    """One decoded page handed to the domain by a future renderer."""

    page: SourcePage

    @property
    def identity(self):
        return self.page.identity


@dataclass(frozen=True)
class CriterionView:
    text: str = ""
    origin: str = ""
    supporting_facts: tuple = ()


@dataclass(frozen=True)
class RegionView:
    region_id: str = ""
    external_section_id: str = ""
    request_id: str = ""
    raw_text: str = ""
    raw_label: str = ""
    current_text: str = ""
    current_label: str = ""
    text_author: str = "model"
    label_author: str = "model"
    inclusion: str = "unreviewed"
    model_flags: tuple = ()
    box_origin: str = ""
    source_box: tuple = ()
    page_link: str = "full_page"
    display_position: int = 0
    linked_question_ids: tuple = ()


@dataclass(frozen=True)
class QuestionView:
    question_id: str = ""
    slot_id: str = ""
    request_id: str = ""
    status: str = "draft"
    blocks: tuple = ()
    original_question: str = ""
    current_question: str = ""
    original_answer_guidance: str = ""
    current_answer_guidance: str = ""
    original_cognitive_explanation: str = ""
    current_cognitive_explanation: str = ""
    generation_grade: str = ""
    generation_objective: str = ""
    current_grade: str = ""
    current_objective: str = ""
    model_level: str = ""
    current_level: str = ""
    level_author: str = "model"
    question_author: str = "model"
    hypothetical_assumptions: tuple = ()
    evaluate_criteria: tuple = ()
    create_rubric: tuple = ()
    supporting_region_ids: tuple = ()
    anchors: tuple = ()
    required_order_relationships: tuple = ()
    field_authors: tuple = ()
    teacher_edited: bool = False


@dataclass(frozen=True)
class SlotOutcome:
    request_id: str = ""
    slot_id: str = ""
    level: str = ""
    outcome: str = "pending"
    question_id: object = None
    abstain_reason: object = None


@dataclass(frozen=True)
class RequestView:
    request_id: str = ""
    kind: str = ""
    state: str = "working"
    outcome: object = None
    bound_source_revision: int = 0
    bound_settings_revision: int = 0
    admitted_count: int = 0


@dataclass(frozen=True)
class PreviewEntry:
    question_id: str = ""
    question: str = ""
    bloom_level: str = ""
    author: str = "model"
    answer_guidance: str = ""
    resolved_anchors: tuple = ()
    original_question: str = ""
    original_answer_guidance: str = ""


@dataclass(frozen=True)
class WorkspaceSnapshot:
    source_id: str = ""
    grade: str = ""
    objective: str = ""
    source_revision: int = 0
    settings_revision: int = 0
    regions: tuple = ()
    questions: tuple = ()
    slot_outcomes: tuple = ()
    requests: tuple = ()
    preview_items: tuple = ()
    can_generate: bool = False
    included_count: int = 0
    withheld_count: int = 0
    source_analysis_terminal: bool = False


@dataclass
class _RegionRec:
    region_id: str
    external_section_id: str
    request_id: str
    raw_text: str
    raw_label: str
    current_label: str
    model_flags: tuple
    raw_section: dict
    label_author: str = "model"
    label_revision: int = 0
    text_author: str = "model"
    pending_text: object = None  # teacher text set before first review


@dataclass
class _RequestRec:
    request_id: str
    kind: str  # "source" | "questions"
    state: str = "working"  # working | terminal | superseded
    outcome: object = None
    bound_source_revision: int = 0
    bound_settings_revision: int = 0
    bound_workspace_epoch: int = 0
    raw_outputs: list = field(default_factory=list)
    admitted_ids: list = field(default_factory=list)
    requested_slots: tuple = ()
    bound_included: dict = field(default_factory=dict)
    slot_outcomes: list = field(default_factory=list)
    bound_grade: str = ""
    bound_objective: str = ""
    accepted_items: dict = field(default_factory=dict)


@dataclass
class _QuestionRec:
    question_id: str
    slot_id: str
    request_id: str
    model_level: str
    current_level: str
    generation_grade: str
    generation_objective: str
    raw_item: dict
    original_assumptions: tuple = ()
    current_assumptions: tuple = ()
    original_criteria: tuple = ()
    current_criteria: tuple = ()
    original_rubric: tuple = ()
    current_rubric: tuple = ()
    level_author: str = "model"
    field_authors: dict = field(default_factory=dict)
    edit_revision: int = 0
    approved_edit_revision: object = None
    approved_settings_revision: object = None
    approved_label_revisions: dict = field(default_factory=dict)
    approved_core_fingerprint: object = None


def _criteria_views(raw_criteria):
    views = []
    for entry in raw_criteria:
        facts = tuple((f["region_id"], f["quote"])
                      for f in entry["supporting_facts"])
        views.append(CriterionView(text=entry["text"], origin=entry["origin"],
                                   supporting_facts=facts))
    return tuple(views)


def _canonical_fact(fact):
    """One owned ``(region_id, quote)`` pair from a dict or pair input."""
    if isinstance(fact, dict):
        if set(fact.keys()) != {"region_id", "quote"}:
            raise TeacherDemoError("supporting fact is malformed")
        region_id, quote = fact["region_id"], fact["quote"]
    elif isinstance(fact, (list, tuple)) and len(fact) == 2:
        region_id, quote = fact
    else:
        raise TeacherDemoError("supporting fact is malformed")
    if not isinstance(region_id, str) or not region_id.strip():
        raise TeacherDemoError("supporting fact is malformed")
    if not isinstance(quote, str) or not quote.strip():
        raise TeacherDemoError("supporting fact is malformed")
    return (region_id, quote)


def _canonical_criteria(value):
    """Validate an edit-time criteria/rubric value into owned views.

    Shape only (types, origins, nonblank facts): whether quotes
    currently resolve is an approval question, never repaired here.
    Caller containers, dicts, and ``CriterionView`` objects are never
    stored by reference. Input text is preserved exactly.
    """
    if not isinstance(value, (list, tuple)):
        raise TeacherDemoError("criteria are malformed")
    views = []
    for entry in value:
        if isinstance(entry, CriterionView):
            text, origin = entry.text, entry.origin
            raw_facts = entry.supporting_facts
        elif isinstance(entry, dict):
            if set(entry.keys()) != {"text", "origin", "supporting_facts"}:
                raise TeacherDemoError("criterion is malformed")
            text, origin = entry["text"], entry["origin"]
            raw_facts = entry["supporting_facts"]
        else:
            raise TeacherDemoError("criterion is malformed")
        if not isinstance(text, str) or not text.strip():
            raise TeacherDemoError("criterion is malformed")
        if origin not in ("source", "objective"):
            raise TeacherDemoError("criterion is malformed")
        if not isinstance(raw_facts, (list, tuple)):
            raise TeacherDemoError("criterion is malformed")
        facts = tuple(_canonical_fact(f) for f in raw_facts)
        if origin == "objective" and facts:
            raise TeacherDemoError(
                "an objective criterion cannot carry source facts")
        if origin == "source" and not facts:
            raise TeacherDemoError(
                "a source criterion needs its supporting facts")
        views.append(CriterionView(text=text, origin=origin,
                                   supporting_facts=facts))
    return tuple(views)


def _canonical_assumptions(value):
    if not isinstance(value, (list, tuple)):
        raise TeacherDemoError("hypothetical assumptions are malformed")
    for entry in value:
        if not isinstance(entry, str) or not entry.strip():
            raise TeacherDemoError("hypothetical assumptions are malformed")
    return tuple(value)


def _source_support_groups(criteria):
    """Multiset of admitted source-support groups, grouping preserved."""
    return Counter(c.supporting_facts for c in criteria
                   if c.origin == "source")


def _fresh_id(given, existing, kind):
    if given is not None:
        if not isinstance(given, str) or not given.strip():
            raise TeacherDemoError("request id is malformed")
        if given in existing:
            raise RequestStateError("request id already exists; use a fresh identity")
        return given
    while True:
        candidate = f"{kind}-{uuid.uuid4().hex[:16]}"
        if candidate not in existing:
            return candidate


class TeacherWorkspace:
    """Single adapter authority: status, blocks, dependents, preview."""

    def __init__(self, decoded_source, *, grade: str, objective: str) -> None:
        page = decoded_source.page if isinstance(decoded_source, DecodedSourcePage) else decoded_source
        if not isinstance(page, SourcePage):
            raise TeacherDemoError("decoded source must wrap a SourcePage")
        if not isinstance(grade, str) or not grade.strip():
            raise TeacherDemoError("grade is malformed")
        if not isinstance(objective, str) or not objective.strip():
            raise TeacherDemoError("objective is malformed")
        self._core = Workspace(page)
        self._grade = grade
        self._objective = objective
        self._source_revision = 0
        self._settings_revision = 0
        self._regions: dict[str, _RegionRec] = {}
        self._display_order: list[str] = []
        self._requests: dict[str, _RequestRec] = {}
        self._order_pairs: set[tuple[str, str]] = set()
        self._questions: dict = {}

    @property
    def core_workspace(self):
        return self._core

    @property
    def source(self):
        return self._core.source

    @property
    def grade(self):
        return self._grade

    @property
    def objective(self):
        return self._objective

    @property
    def source_revision(self):
        return self._source_revision

    @property
    def settings_revision(self):
        return self._settings_revision

    # -- settings (cycle 5 completes invalidation semantics) -----------------
    def set_grade_objective(self, grade: str, objective: str) -> None:
        if not isinstance(grade, str) or not grade.strip():
            raise TeacherDemoError("grade is malformed")
        if not isinstance(objective, str) or not objective.strip():
            raise TeacherDemoError("objective is malformed")
        if grade == self._grade and objective == self._objective:
            return
        self._grade = grade
        self._objective = objective
        self._settings_revision += 1

    # -- source requests ------------------------------------------------------
    def _request(self, request_id, kind):
        rec = self._requests.get(request_id)
        if rec is None or rec.kind != kind:
            raise RequestStateError("unknown request; nothing was applied")
        return rec

    def _require_working(self, rec):
        if rec.state != "working":
            raise RequestStateError(
                "request is closed; late results are never applied")

    def begin_source_request(self, request_id=None) -> str:
        rid = _fresh_id(request_id, self._requests, "source")
        for rec in self._requests.values():
            if rec.kind == "source" and rec.state == "working":
                rec.state = "superseded"
                rec.outcome = "superseded"
        self._requests[rid] = _RequestRec(
            request_id=rid, kind="source",
            bound_source_revision=self._source_revision,
            bound_settings_revision=self._settings_revision,
            bound_workspace_epoch=self._core.workspace_epoch)
        return rid

    def _full_page_box(self):
        conv = self.source.coordinate_convention
        if conv == "normalized_0_1000_top_left":
            return (0.0, 0.0, 1000.0, 1000.0)
        return (0.0, 0.0, float(self.source.width), float(self.source.height))

    def admit_source_proposal(self, request_id, raw, *, model_name=None) -> tuple:
        rec = self._request(request_id, "source")
        self._require_working(rec)
        try:
            validate_source_proposal(raw)
        except ValueError:
            raise TeacherDemoError("source proposal is malformed") from None
        stored_raw = copy.deepcopy(raw)
        page = self.source
        planned = []
        for section in raw["sections"]:
            region_id = f"{request_id}:{section['section_id']}"
            existing = self._regions.get(region_id)
            if existing is not None:
                if existing.raw_section == section:
                    continue  # duplicate delivery: idempotent
                raise TeacherDemoError(
                    "conflicting re-use of a proposal identity; "
                    "the preserved original was kept")
            planned.append((region_id, section))
        label_origin = model_name if isinstance(model_name, str) and model_name else "unnamed_text_model_in_response"
        new_ids = []
        for region_id, section in planned:
            proposal = RegionProposal(
                region_id=region_id, source_id=page.source_id,
                source_sha256=page.source_sha256, page_index=page.page_index,
                render_sha256=page.render_sha256,
                source_box=self._full_page_box(),
                box_origin=FULL_PAGE_BOX_ORIGIN,
                semantic_label=section["kind"],
                semantic_label_origin=label_origin,
                text_candidate=section["text"],
                source_crop_ref=f"full_page:{page.source_id}:{page.page_index}",
                order_candidate=len(self._display_order),
                review_reasons=tuple(section["review_reasons"]),
                coordinate_convention=page.coordinate_convention)
            self._core.submit_region_proposal(proposal)
            self._regions[region_id] = _RegionRec(
                region_id=region_id,
                external_section_id=section["section_id"],
                request_id=request_id,
                raw_text=section["text"], raw_label=section["kind"],
                current_label=section["kind"],
                model_flags=tuple(section["review_reasons"]),
                raw_section=copy.deepcopy(section))
            self._display_order.append(region_id)
            rec.admitted_ids.append(region_id)
            new_ids.append(region_id)
        if stored_raw not in rec.raw_outputs:
            rec.raw_outputs.append(stored_raw)
        return tuple(new_ids) if new_ids else tuple(
            f"{request_id}:{s['section_id']}" for s in raw["sections"])

    def finish_source_request(self, request_id, outcome: str) -> None:
        rec = self._request(request_id, "source")
        self._require_working(rec)
        rec.state = "terminal"
        rec.outcome = outcome

    def cancel_source_request(self, request_id) -> None:
        rec = self._request(request_id, "source")
        self._require_working(rec)
        rec.state = "terminal"
        rec.outcome = "cancelled"

    # -- question requests ------------------------------------------------------
    def _usable_texts(self):
        """The one generation population (UX section 5).

        Reviewed-included regions whose current teacher text is
        nonblank. ``strip()`` decides blankness only; usable text is
        kept exactly. A blank region keeps its teacher ``included``
        decision but is withheld here. ``snapshot`` (``can_generate``
        and the sent/withheld counts), ``begin_question_request``
        eligibility, and the frozen prompt population all derive
        from this single mapping.
        """
        texts = {}
        for region_id in self._display_order:
            reviewed = self._reviewed(region_id)
            if (reviewed is not None
                    and reviewed.inclusion == "included"
                    and reviewed.teacher_text.strip()):
                texts[region_id] = reviewed.teacher_text
        return texts

    def _source_gate_open(self):
        working = any(r.kind == "source" and r.state == "working"
                      for r in self._requests.values())
        terminal = any(r.kind == "source" and r.state == "terminal"
                       for r in self._requests.values())
        return terminal and not working

    def begin_question_request(self, request_id=None, requested_slots=None) -> str:
        usable = self._usable_texts()
        if not self._source_gate_open() or not usable:
            raise RequestStateError(
                "generation requires terminal source analysis and at "
                "least one reviewed included region with current text")
        from .question_protocol_v2 import BLOOM_LEVELS
        if requested_slots is None:
            slots = tuple({"slot_id": f"slot-{level}", "level": level}
                          for level in BLOOM_LEVELS)
        else:
            if not isinstance(requested_slots, (list, tuple)) or not requested_slots:
                raise TeacherDemoError("requested slots are malformed")
            seen = set()
            checked = []
            for entry in requested_slots:
                if not isinstance(entry, dict) or set(entry) != {"slot_id", "level"}:
                    raise TeacherDemoError("requested slots are malformed")
                if (not isinstance(entry["slot_id"], str)
                        or not entry["slot_id"].strip()
                        or entry["slot_id"] in seen
                        or entry["level"] not in BLOOM_LEVELS):
                    raise TeacherDemoError("requested slots are malformed")
                seen.add(entry["slot_id"])
                checked.append(dict(entry))
            slots = tuple(checked)
        rid = _fresh_id(request_id, self._requests, "questions")
        for rec in self._requests.values():
            if rec.kind == "questions" and rec.state == "working":
                rec.state = "superseded"
                rec.outcome = "superseded"
        rec = _RequestRec(
            request_id=rid, kind="questions",
            bound_source_revision=self._source_revision,
            bound_settings_revision=self._settings_revision,
            bound_workspace_epoch=self._core.workspace_epoch,
            requested_slots=slots, bound_included=dict(usable),
            bound_grade=self._grade, bound_objective=self._objective)
        rec.slot_outcomes = [SlotOutcome(
            request_id=rid, slot_id=s["slot_id"], level=s["level"],
            outcome="pending") for s in slots]
        self._requests[rid] = rec
        return rid

    def question_request_prompt(self, request_id) -> str:
        """Build the packaged v2 prompt for one working question request.

        Uses only this request's frozen included text, requested slots,
        grade, and objective. Applies the same unknown/closed/stale
        rejection as ``admit_question_proposal``. This builds a prompt
        string only: it returns no live model response, makes no
        network request, and changes no workspace state.
        """
        rec = self._request(request_id, "questions")
        self._require_working(rec)
        if (rec.bound_source_revision != self._source_revision
                or rec.bound_settings_revision != self._settings_revision):
            raise RequestStateError(
                "request was bound to a superseded source/settings "
                "revision; no prompt was built")
        return question_prompt_v2(
            dict(rec.bound_included), list(rec.requested_slots),
            grade=rec.bound_grade, objective=rec.bound_objective)

    def admit_question_proposal(self, request_id, raw, *, model_name=None) -> tuple:
        rec = self._request(request_id, "questions")
        self._require_working(rec)
        if (rec.bound_source_revision != self._source_revision
                or rec.bound_settings_revision != self._settings_revision):
            raise RequestStateError(
                "request was bound to a superseded source/settings "
                "revision; its result was not applied")
        result = validate_question_proposal_v2(
            raw, rec.bound_included, list(rec.requested_slots))
        if not result.ok:
            codes = ",".join(sorted({i.code for i in result.issues}))
            raise TeacherDemoError(f"question proposal is invalid: {codes}")
        # Own the proposal items before any identity check or write so
        # caller/readback mutation can never change accepted identity.
        owned_items = copy.deepcopy(raw["items"])
        # Every requested slot's first accepted item (draft or
        # abstention) owns its identity while this request is current
        # and working. An exact repeat is idempotent; any differing
        # item refuses the whole proposal before any slot is applied.
        if rec.accepted_items:
            for item in owned_items:
                if rec.accepted_items.get(item["slot_id"]) != item:
                    raise TeacherDemoError(
                        "conflicting re-use of a proposal identity; "
                        "the preserved original was kept")
            return tuple(
                f"{request_id}:{item['slot_id']}"
                for item in owned_items if item["status"] == "draft")
        page = self.source
        planned = []
        returned_ids = []
        for item in owned_items:
            if item["status"] != "draft":
                continue
            question_id = f"{request_id}:{item['slot_id']}"
            returned_ids.append(question_id)
            existing = self._questions.get(question_id)
            if existing is not None:
                if existing.raw_item == item:
                    continue
                raise TeacherDemoError(
                    "conflicting re-use of a proposal identity; "
                    "the preserved original was kept")
            for pair in item["required_order_relationships"]:
                for endpoint in pair:
                    if endpoint not in self._regions:
                        raise TeacherDemoError(
                            "question references an unknown region")
            planned.append((question_id, item))
        new_ids = []
        for question_id, item in planned:
            supporting = []
            for fact in item["evidence"]:
                if fact["region_id"] not in supporting:
                    supporting.append(fact["region_id"])
            # Complete the one dependency population: regions cited
            # only by source-origin criterion/rubric facts support the
            # question too, so the core fingerprint, label pins,
            # approval blocks, and links all cover the same set. No
            # anchor is invented for them; their exact quotes stay
            # with the criterion that cites them.
            for criterion in (item["evaluate_criteria"]
                              + item["create_rubric"]):
                if criterion["origin"] != "source":
                    continue
                for fact in criterion["supporting_facts"]:
                    if fact["region_id"] not in supporting:
                        supporting.append(fact["region_id"])
            proposal = QuestionProposal(
                question_id=question_id, source_id=page.source_id,
                source_sha256=page.source_sha256, page_index=page.page_index,
                render_sha256=page.render_sha256,
                grade=rec.bound_grade, objective=rec.bound_objective,
                bloom_task_rationale=item["cognitive_explanation"],
                question=item["question"],
                answer_guidance=item["answer_guidance"],
                supporting_region_ids=tuple(supporting),
                source_fact_anchors=tuple(
                    FactAnchor(f["region_id"], f["quote"])
                    for f in item["evidence"]),
                required_order_relationships=tuple(
                    tuple(p) for p in item["required_order_relationships"]),
                # Adapter verified source/settings bindings above, so the
                # core epoch stamp is current by construction.
                generated_at_epoch=self._core.workspace_epoch)
            self._core.submit_question_proposal(proposal)
            criteria = _criteria_views(item["evaluate_criteria"])
            rubric = _criteria_views(item["create_rubric"])
            assumptions = tuple(item["hypothetical_assumptions"])
            self._questions[question_id] = _QuestionRec(
                question_id=question_id, slot_id=item["slot_id"],
                request_id=request_id, model_level=item["level"],
                current_level=item["level"],
                generation_grade=rec.bound_grade,
                generation_objective=rec.bound_objective,
                raw_item=copy.deepcopy(item),
                original_assumptions=assumptions,
                current_assumptions=assumptions,
                original_criteria=criteria, current_criteria=criteria,
                original_rubric=rubric, current_rubric=rubric)
            rec.admitted_ids.append(question_id)
            new_ids.append(question_id)
        rec.accepted_items = {
            item["slot_id"]: copy.deepcopy(item) for item in owned_items}
        by_slot = {item["slot_id"]: item for item in owned_items}
        updated = []
        for outcome in rec.slot_outcomes:
            item = by_slot[outcome.slot_id]
            if item["status"] == "draft":
                updated.append(SlotOutcome(
                    request_id=request_id, slot_id=outcome.slot_id,
                    level=outcome.level, outcome="draft",
                    question_id=f"{request_id}:{outcome.slot_id}"))
            else:
                updated.append(SlotOutcome(
                    request_id=request_id, slot_id=outcome.slot_id,
                    level=outcome.level, outcome="abstain",
                    abstain_reason=item["abstain_reason"]))
        rec.slot_outcomes = updated
        stored = copy.deepcopy(raw)
        if stored not in rec.raw_outputs:
            rec.raw_outputs.append(stored)
        return tuple(returned_ids)

    def finish_question_request(self, request_id, outcome: str) -> None:
        rec = self._request(request_id, "questions")
        self._require_working(rec)
        rec.state = "terminal"
        rec.outcome = outcome

    def cancel_question_request(self, request_id) -> None:
        rec = self._request(request_id, "questions")
        self._require_working(rec)
        rec.state = "terminal"
        rec.outcome = "cancelled"

    # -- region review --------------------------------------------------------
    def _region(self, region_id):
        rec = self._regions.get(region_id)
        if rec is None:
            raise TeacherDemoError("unknown region")
        return rec

    def _reviewed(self, region_id):
        try:
            return self._core.get_reviewed_region(region_id)
        except Exception:
            return None

    def _current_text(self, rec):
        reviewed = self._reviewed(rec.region_id)
        if reviewed is not None:
            return reviewed.teacher_text
        if rec.pending_text is not None:
            return rec.pending_text
        return rec.raw_text

    def set_region_inclusion(self, region_id, inclusion: str) -> None:
        rec = self._region(region_id)
        if inclusion not in _INCLUSIONS:
            raise TeacherDemoError("unknown inclusion state")
        reviewed = self._reviewed(region_id)
        if reviewed is not None and reviewed.inclusion == inclusion:
            return  # identical value: no-op, revokes nothing
        text = self._current_text(rec)
        self._core.review_region(region_id, teacher_text=text, inclusion=inclusion)
        rec.pending_text = None
        self._source_revision += 1

    def edit_region_text(self, region_id, new_text: str) -> None:
        rec = self._region(region_id)
        if not isinstance(new_text, str):
            raise TeacherDemoError("region text is malformed")
        if new_text == self._current_text(rec):
            return
        reviewed = self._reviewed(region_id)
        if reviewed is not None:
            self._core.review_region(region_id, teacher_text=new_text,
                                     inclusion=reviewed.inclusion)
        else:
            rec.pending_text = new_text
        rec.text_author = "teacher"
        self._source_revision += 1

    def edit_region_label(self, region_id, new_label: str) -> None:
        rec = self._region(region_id)
        if not isinstance(new_label, str) or not new_label.strip():
            raise TeacherDemoError("semantic label is malformed")
        if new_label == rec.current_label:
            return
        rec.current_label = new_label
        rec.label_author = "teacher"
        rec.label_revision += 1
        self._source_revision += 1

    def move_region_up(self, region_id) -> bool:
        return self._move_region(region_id, -1)

    def move_region_down(self, region_id) -> bool:
        return self._move_region(region_id, +1)

    def _move_region(self, region_id, delta) -> bool:
        # Pure display order: never touches core order pairs or revisions.
        self._region(region_id)
        index = self._display_order.index(region_id)
        target = index + delta
        if target < 0 or target >= len(self._display_order):
            return False
        self._display_order[index], self._display_order[target] = (
            self._display_order[target], self._display_order[index])
        return True

    def set_required_precedence(self, before_id, after_id, present: bool) -> None:
        pair = (before_id, after_id)
        epoch_before = self._core.workspace_epoch
        self._core.set_order_relationship(before_id, after_id, present)
        if present:
            self._order_pairs.add(pair)
        else:
            self._order_pairs.discard(pair)
        if self._core.workspace_epoch != epoch_before:
            self._source_revision += 1

    # -- question decisions -----------------------------------------------------
    def _question_rec(self, question_id):
        rec = self._questions.get(question_id)
        if rec is None:
            raise TeacherDemoError("unknown question")
        return rec

    _CORE_EDIT_MAP = {
        "question": "question", "answer_guidance": "answer_guidance",
        "cognitive_explanation": "bloom_task_rationale",
        "grade": "grade", "objective": "objective",
    }

    def edit_question(self, question_id, **changes) -> None:
        from .question_protocol_v2 import BLOOM_LEVELS
        rec = self._question_rec(question_id)
        allowed = set(self._CORE_EDIT_MAP) | {
            "bloom_level", "hypothetical_assumptions",
            "evaluate_criteria", "create_rubric"}
        unknown = set(changes) - allowed
        if unknown:
            raise TeacherDemoError("non-editable question fields")
        current = self._core.question_snapshot(question_id).current
        # Validate and take owned immutable values for every supplied
        # field before any core or adapter write, so a malformed or
        # support-rebinding edit leaves the prior state untouched.
        core_changes = {}
        for name, core_name in self._CORE_EDIT_MAP.items():
            if name in changes:
                if not isinstance(changes[name], str):
                    raise TeacherDemoError("question field is malformed")
                if getattr(current, core_name) != changes[name]:
                    core_changes[core_name] = changes[name]
        adapter_changes = {}
        if "bloom_level" in changes:
            level = changes["bloom_level"]
            if not isinstance(level, str) or level not in BLOOM_LEVELS:
                raise TeacherDemoError("unknown Bloom level")
            if level != rec.current_level:
                adapter_changes["bloom_level"] = level
        if "hypothetical_assumptions" in changes:
            value = _canonical_assumptions(
                changes["hypothetical_assumptions"])
            if value != rec.current_assumptions:
                adapter_changes["hypothetical_assumptions"] = value
        for name, attr in (("evaluate_criteria", "current_criteria"),
                           ("create_rubric", "current_rubric")):
            if name in changes:
                value = _canonical_criteria(changes[name])
                # Admitted source support is frozen per field (exact
                # facts, grouping, multiplicity; reordering and
                # wording/objective edits keep the same groups). New
                # evidence may arrive only via a new generation.
                if (_source_support_groups(value)
                        != _source_support_groups(getattr(rec, attr))):
                    raise TeacherDemoError(
                        "source support cannot be changed by editing; "
                        "new evidence requires a new generation")
                if value != getattr(rec, attr):
                    adapter_changes[name] = value
        if not core_changes and not adapter_changes:
            return  # identical values: no-op, revokes nothing
        if core_changes:
            self._core.edit_question(question_id, **core_changes)
            for name, core_name in self._CORE_EDIT_MAP.items():
                if core_name in core_changes:
                    rec.field_authors[name] = "teacher"
        if "bloom_level" in adapter_changes:
            rec.current_level = adapter_changes["bloom_level"]
            rec.level_author = "teacher"
        if "hypothetical_assumptions" in adapter_changes:
            rec.current_assumptions = adapter_changes["hypothetical_assumptions"]
            rec.field_authors["hypothetical_assumptions"] = "teacher"
        if "evaluate_criteria" in adapter_changes:
            rec.current_criteria = adapter_changes["evaluate_criteria"]
            rec.field_authors["evaluate_criteria"] = "teacher"
        if "create_rubric" in adapter_changes:
            rec.current_rubric = adapter_changes["create_rubric"]
            rec.field_authors["create_rubric"] = "teacher"
        rec.edit_revision += 1

    def approve_question(self, question_id) -> str:
        rec = self._question_rec(question_id)
        current = self._core.question_snapshot(question_id).current
        blocks = self._approval_blocks(rec, current)
        if blocks:
            raise TeacherDemoError(
                "approval is blocked: " + ",".join(blocks))
        try:
            fingerprint = self._core.approve_question(question_id)
        except Exception as exc:
            raise TeacherDemoError("approval is blocked by current evidence") from exc
        rec.approved_edit_revision = rec.edit_revision
        rec.approved_settings_revision = self._settings_revision
        rec.approved_label_revisions = {
            dep: self._regions[dep].label_revision
            for dep in self._dependency_ids(current) if dep in self._regions}
        rec.approved_core_fingerprint = fingerprint
        return fingerprint

    def reject_question(self, question_id) -> None:
        self._question_rec(question_id)
        self._core.reject_question(question_id)

    # -- raw access / snapshot --------------------------------------------------
    def raw_source_output(self, request_id):
        rec = self._requests.get(request_id)
        if rec is None or rec.kind != "source" or not rec.raw_outputs:
            return None
        if len(rec.raw_outputs) == 1:
            return copy.deepcopy(rec.raw_outputs[0])
        return copy.deepcopy(rec.raw_outputs)

    def raw_question_output(self, request_id):
        rec = self._requests.get(request_id)
        if rec is None or rec.kind != "questions" or not rec.raw_outputs:
            return None
        if len(rec.raw_outputs) == 1:
            return copy.deepcopy(rec.raw_outputs[0])
        return copy.deepcopy(rec.raw_outputs)

    def _dependency_ids(self, core_current):
        deps = set(core_current.supporting_region_ids)
        for before, after in core_current.required_order_relationships:
            deps.add(before)
            deps.add(after)
        return deps

    def _question_views(self):
        views = []
        linked = {}
        for question_id in sorted(self._questions):
            rec = self._questions[question_id]
            snap = self._core.question_snapshot(question_id)
            for dep in self._dependency_ids(snap.current):
                linked.setdefault(dep, []).append(question_id)
            views.append((rec, snap))
        result = []
        for rec, snap in views:
            blocks = self._approval_blocks(rec, snap.current)
            result.append(QuestionView(
                question_id=rec.question_id, slot_id=rec.slot_id,
                request_id=rec.request_id,
                status=self._question_status(rec, snap.current, blocks),
                blocks=blocks,
                original_question=snap.original.question,
                current_question=snap.current.question,
                original_answer_guidance=snap.original.answer_guidance,
                current_answer_guidance=snap.current.answer_guidance,
                original_cognitive_explanation=snap.original.bloom_task_rationale,
                current_cognitive_explanation=snap.current.bloom_task_rationale,
                generation_grade=rec.generation_grade,
                generation_objective=rec.generation_objective,
                current_grade=snap.current.grade,
                current_objective=snap.current.objective,
                model_level=rec.model_level, current_level=rec.current_level,
                level_author=rec.level_author,
                question_author=rec.field_authors.get("question", "model"),
                hypothetical_assumptions=rec.current_assumptions,
                evaluate_criteria=rec.current_criteria,
                create_rubric=rec.current_rubric,
                supporting_region_ids=tuple(snap.current.supporting_region_ids),
                anchors=tuple((a.region_id, a.exact_quote)
                              for a in snap.current.source_fact_anchors),
                required_order_relationships=tuple(
                    tuple(p) for p in snap.current.required_order_relationships),
                field_authors=tuple(sorted({
                    "question": "model", "answer_guidance": "model",
                    "cognitive_explanation": "model", "grade": "teacher",
                    "objective": "teacher",
                    "hypothetical_assumptions": "model",
                    "evaluate_criteria": "model", "create_rubric": "model",
                    "bloom_level": rec.level_author,
                    **rec.field_authors}.items())),
                teacher_edited=rec.edit_revision > 0))
        return tuple(result), linked

    def _question_status(self, rec, core_current, blocks):
        core_status = self._core.question_status(rec.question_id)
        if core_status == "rejected":
            return "rejected"
        if rec.approved_core_fingerprint is None:
            return "draft"
        if rec.edit_revision != rec.approved_edit_revision:
            return "draft"  # own-content edit: unapproved draft (UX §6)
        if core_status != "approved":
            return "outdated"
        if rec.approved_settings_revision != self._settings_revision:
            return "outdated"
        for dep in self._dependency_ids(core_current):
            region = self._regions.get(dep)
            if region is not None and (
                    rec.approved_label_revisions.get(dep)
                    != region.label_revision):
                return "outdated"
        if blocks:
            return "outdated"
        return "approved"

    def _criterion_blocked(self, criterion):
        if criterion.origin not in ("source", "objective"):
            return True
        if not criterion.text.strip():
            return True
        if criterion.origin == "objective":
            return bool(criterion.supporting_facts)
        if not criterion.supporting_facts:
            return True
        for region_id, quote in criterion.supporting_facts:
            reviewed = self._reviewed(region_id)
            if (reviewed is None or reviewed.inclusion != "included"
                    or quote not in reviewed.teacher_text):
                return True
        return False

    def _approval_blocks(self, rec, core_current):
        blocks = []

        def add(code):
            if code not in blocks:
                blocks.append(code)

        for value in (core_current.grade, core_current.objective,
                      core_current.bloom_task_rationale,
                      core_current.question, core_current.answer_guidance):
            if value.strip() == "":
                add("blank_field")
        if (core_current.grade != self._grade
                or core_current.objective != self._objective):
            add("grade_objective_mismatch")
        for dep in self._dependency_ids(core_current):
            reviewed = self._reviewed(dep)
            if reviewed is None or reviewed.inclusion != "included":
                add("dependency_not_included")
        for anchor in core_current.source_fact_anchors:
            reviewed = self._reviewed(anchor.region_id)
            if reviewed is not None and anchor.exact_quote not in reviewed.teacher_text:
                add("anchor_not_resolved")
        for pair in core_current.required_order_relationships:
            if tuple(pair) not in self._order_pairs:
                add("required_order_missing")
        if rec.current_level == "evaluate" and not rec.current_criteria:
            add("criterion_invalid")
        if rec.current_level == "create" and not (2 <= len(rec.current_rubric) <= 4):
            add("criterion_invalid")
        for criterion in tuple(rec.current_criteria) + tuple(rec.current_rubric):
            if self._criterion_blocked(criterion):
                add("criterion_invalid")
        return tuple(blocks)

    def _region_views(self, linked):
        views = []
        for pos, region_id in enumerate(self._display_order, start=1):
            rec = self._regions[region_id]
            reviewed = self._reviewed(region_id)
            proposal = self._core.get_region_proposal(region_id)
            views.append(RegionView(
                region_id=region_id,
                external_section_id=rec.external_section_id,
                request_id=rec.request_id,
                raw_text=rec.raw_text, raw_label=rec.raw_label,
                current_text=self._current_text(rec),
                current_label=rec.current_label,
                text_author=rec.text_author, label_author=rec.label_author,
                inclusion=reviewed.inclusion if reviewed else "unreviewed",
                model_flags=rec.model_flags,
                box_origin=proposal.box_origin,
                source_box=tuple(proposal.source_box),
                page_link="full_page", display_position=pos,
                linked_question_ids=tuple(sorted(linked.get(region_id, ())))))
        return tuple(views)

    def snapshot(self) -> WorkspaceSnapshot:
        questions, linked = self._question_views()
        regions = self._region_views(linked)
        # Sent/withheld caption population (UX section 5): the same
        # usable mapping and source gate that ``begin_question_request``
        # uses, so the counts and ``can_generate`` can never enable an
        # action the gate itself would refuse.
        usable_count = len(self._usable_texts())
        gate_open = self._source_gate_open()
        return WorkspaceSnapshot(
            source_id=self.source.source_id, grade=self._grade,
            objective=self._objective,
            source_revision=self._source_revision,
            settings_revision=self._settings_revision,
            regions=regions,
            questions=questions,
            preview_items=tuple(
                PreviewEntry(
                    question_id=q.question_id, question=q.current_question,
                    bloom_level=q.current_level,
                    # Current-version author from actual edits only
                    # (teacher_edited, edit_revision > 0), not any
                    # field origin. Approval alone never confers it.
                    author=("teacher" if q.teacher_edited else "model"),
                    answer_guidance=q.current_answer_guidance,
                    original_question=q.original_question,
                    original_answer_guidance=q.original_answer_guidance,
                    resolved_anchors=tuple(
                        (rid, quote,
                         self._reviewed(rid).teacher_text
                         if self._reviewed(rid) else "")
                        for rid, quote in q.anchors))
                for q in questions if q.status == "approved" and not q.blocks),
            slot_outcomes=tuple(
                o for r in self._requests.values() for o in r.slot_outcomes),
            requests=tuple(RequestView(
                request_id=r.request_id, kind=r.kind, state=r.state,
                outcome=r.outcome,
                bound_source_revision=r.bound_source_revision,
                bound_settings_revision=r.bound_settings_revision,
                admitted_count=len(r.admitted_ids))
                for r in self._requests.values()),
            can_generate=gate_open and usable_count > 0,
            included_count=usable_count,
            withheld_count=len(regions) - usable_count,
            source_analysis_terminal=gate_open,
        )
