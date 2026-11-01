"""Offline source -> review -> provisional questions -> approved preview.

Provider-free contract harness for synthetic fixtures only. No network,
no model API, no rendering, no UI. All state is IN-MEMORY and NON-DURABLE:
it vanishes with the process and must never be described as persisted.

Ownership and identity model (single teacher authority):

- Every external result (region or question proposal) carries an explicit
  immutable ``SourceIdentity`` binding: source id + source bytes hash +
  page index + render hash. Admission compares the whole binding to this
  workspace's source; a reused local id alone never suffices, and a
  malformed identity is rejected, never filled in from the receiver.
- IDs are owned, not recycled. A region proposal is immutable once
  admitted (a conflicting re-use of its id is rejected; a byte-identical
  resubmission is a no-op). A question id, once created, cannot be
  overwritten by direct creation or by a provider proposal, so
  teacher-owned work and its approval state cannot be replaced or
  inherited. New work needs a new id.
- An approval lives on the question record itself (not in a side map
  keyed by reusable id) and is honored only while the teacher decision
  is still "approved" *and* the dependency fingerprint still matches.
  Dependencies are defined once (supporting regions UNION required-
  order endpoints) and consumed by both evidence resolution and the
  fingerprint, which pins monotonic revisions (reviewed-region,
  per-order-pair, question). Changing a dependency and later changing
  it back does NOT resurrect an approval; only a new explicit teacher
  approval does. No-ops revoke nothing; independent questions are
  never invalidated by changes they did not depend on.
- The original draft of every question (direct or model-proposal
  arrival) is preserved separately from teacher edits and exposed,
  with its current state, as immutable snapshots. Rejection is an
  explicit persistent teacher decision, lifted only by a fresh
  explicit approval against current, complete dependencies.

Further separations: geometry origin vs semantic-label origin; model
suggestion (``text_candidate``, preserved raw) vs teacher text;
included/excluded/unresolved reviewed state; rights references are
metadata, never legal clearance. Text is stored and compared exactly
(codepoint-for-codepoint, no normalization), and source text is opaque
data, never an instruction. Records validate and copy their fields at
construction (boxes and sequences become immutable tuples), so later
mutation of a caller's list cannot alias into stored state.
"""
from __future__ import annotations

import hashlib
import math
import string
from dataclasses import dataclass, field

_OK = "ok"
_INCLUSIONS = ("included", "excluded", "unresolved")


class ContractError(Exception):
    """Base class for all contract violations."""


class SourceMismatchError(ContractError):
    """A result was offered to a source/page/render it does not belong to."""


class EvidenceError(ContractError):
    """A fact anchor does not resolve to current reviewed teacher text."""


class StaleProposalError(ContractError):
    """An async proposal was generated against superseded review state."""


class ProviderOutcomeError(ContractError):
    """Non-success provider outcome (timeout/429/refusal/unknown/...)."""


class ValidationError(ContractError):
    """Malformed record, unknown/duplicate identifier, or bad state."""


# -- validation primitives (runtime checks; annotations alone enforce nothing)
def _str(name: str, value: object, nonempty: bool = False) -> str:
    if not isinstance(value, str) or (nonempty and value == ""):
        raise ValidationError(f"{name} must be a {'non-empty ' if nonempty else ''}str")
    return value


def _int(name: str, value: object, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValidationError(f"{name} must be an int >= {minimum}")
    return value


_HEX = frozenset(string.hexdigits)


def _sha256(name: str, value: object) -> str:
    # Genuine digest grammar: exactly 64 hex characters. The supplied
    # representation (e.g. upper case) is preserved, never rewritten.
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in _HEX for c in value)):
        raise ValidationError(f"{name} must be a SHA-256 digest (64 hex chars)")
    return value


# Explicitly supported source-coordinate conventions. No substring
# guessing and no fail-open fallback: anything else is rejected.
# "raster_pixels_top_left" means rendered-raster pixel coordinates,
# origin at the top-left, x in [0, width], y in [0, height]; it makes
# no claim of equivalence to any PDF-point space (no renderer here).
_FIXED_BOX_LIMITS = {"normalized_0_1000_top_left": (1000.0, 1000.0)}
_RASTER_PIXELS = "raster_pixels_top_left"
_SUPPORTED_CONVENTIONS = frozenset(_FIXED_BOX_LIMITS) | {_RASTER_PIXELS}


def _check_outcome(outcome: str) -> None:
    # Unknown/error outcomes are not success; only an explicit ok passes.
    if outcome != _OK:
        raise ProviderOutcomeError(f"provider outcome is not success: {outcome!r}")


def _validate_box(box: object) -> tuple[float, float, float, float]:
    if isinstance(box, (str, bytes)) or not isinstance(box, (tuple, list)) or len(box) != 4:
        raise ValidationError(f"source_box must be 4 numbers, got {box!r}")
    coords = []
    for v in box:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValidationError(f"source_box coordinate is not a finite number: {v!r}")
        coords.append(float(v))
    x0, y0, x1, y1 = coords
    if not (x0 < x1 and y0 < y1):
        raise ValidationError(f"degenerate source_box: {box!r}")
    return (x0, y0, x1, y1)


def _str_tuple(name: str, value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ValidationError(f"{name} must be a tuple/list of str")
    return tuple(_str(name, v) for v in value)


def _order_pairs(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, (tuple, list)):
        raise ValidationError("required_order_relationships must be pairs of ids")
    pairs = []
    for p in value:
        if isinstance(p, (str, bytes)) or not isinstance(p, (tuple, list)) or len(p) != 2:
            raise ValidationError(f"order relationship must be a pair of ids: {p!r}")
        pairs.append((_str("order id", p[0], True), _str("order id", p[1], True)))
    return tuple(pairs)


@dataclass(frozen=True)
class SourceIdentity:
    """The single binding every external result must carry and match."""

    source_id: str
    source_sha256: str
    page_index: int
    render_sha256: str

    def __post_init__(self) -> None:
        _str("source_id", self.source_id, True)
        _sha256("source_sha256", self.source_sha256)
        _int("page_index", self.page_index, 0)
        _sha256("render_sha256", self.render_sha256)


@dataclass(frozen=True)
class SourcePage:
    source_id: str
    source_sha256: str
    page_index: int
    render_sha256: str
    width: int
    height: int
    rights_ref: str  # metadata only; see rights_is_clearance
    coordinate_convention: str  # e.g. "normalized_0_1000_top_left"
    rights_is_clearance: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        SourceIdentity(self.source_id, self.source_sha256,
                       self.page_index, self.render_sha256)
        _int("width", self.width, 1)
        _int("height", self.height, 1)
        _str("rights_ref", self.rights_ref)
        _str("coordinate_convention", self.coordinate_convention, True)

    @property
    def identity(self) -> SourceIdentity:
        return SourceIdentity(self.source_id, self.source_sha256,
                              self.page_index, self.render_sha256)


@dataclass(frozen=True)
class RegionProposal:
    """A (synthetic) provider suggestion. Never teacher-reviewed truth.
    Immutable once admitted; its id cannot be re-homed (see Workspace)."""

    region_id: str
    source_id: str
    source_sha256: str
    page_index: int
    render_sha256: str
    source_box: tuple[float, float, float, float]
    box_origin: str  # geometry provenance
    semantic_label: str
    semantic_label_origin: str  # label provenance, independent of geometry
    text_candidate: str  # model suggestion, preserved raw forever
    source_crop_ref: str  # opaque reference; no URI format is assumed
    order_candidate: int
    review_reasons: tuple[str, ...]
    coordinate_convention: str

    def __post_init__(self) -> None:
        _ = self.identity  # malformed identity fails here, never defaulted
        _str("region_id", self.region_id, True)
        _str("coordinate_convention", self.coordinate_convention, True)
        for name in ("box_origin", "semantic_label", "semantic_label_origin",
                     "text_candidate", "source_crop_ref"):
            _str(name, getattr(self, name))
        _int("order_candidate", self.order_candidate, 0)
        object.__setattr__(self, "source_box", _validate_box(self.source_box))
        object.__setattr__(self, "review_reasons",
                           _str_tuple("review_reasons", self.review_reasons))

    @property
    def identity(self) -> SourceIdentity:
        return SourceIdentity(self.source_id, self.source_sha256,
                              self.page_index, self.render_sha256)


@dataclass(frozen=True)
class ReviewedRegion:
    region_id: str
    teacher_text: str  # teacher-owned; distinct from text_candidate
    inclusion: str  # included | excluded | unresolved
    revision: int


@dataclass(frozen=True)
class FactAnchor:
    region_id: str
    exact_quote: str  # a real fact: non-empty, verbatim in teacher text

    def __post_init__(self) -> None:
        _str("anchor region_id", self.region_id, True)
        quote = _str("exact_quote", self.exact_quote, True)
        if quote.strip() == "":  # whitespace-only is not a fact (check only;
            raise ValidationError("exact_quote cannot be whitespace-only")
        # the stored quote itself is never trimmed or normalized


@dataclass(frozen=True)
class QuestionProposal:
    """A (synthetic) async model proposal. Can never approve anything and
    can never overwrite an existing question id."""

    question_id: str
    source_id: str
    source_sha256: str
    page_index: int
    render_sha256: str
    grade: str
    objective: str
    bloom_task_rationale: str
    question: str
    answer_guidance: str
    supporting_region_ids: tuple[str, ...]
    source_fact_anchors: tuple[FactAnchor, ...]
    required_order_relationships: tuple[tuple[str, str], ...]
    generated_at_epoch: int
    outcome: str = _OK

    def __post_init__(self) -> None:
        _ = self.identity
        _str("question_id", self.question_id, True)
        if isinstance(self.generated_at_epoch, bool) or not isinstance(
                self.generated_at_epoch, int):
            raise ValidationError("generated_at_epoch must be an int")
        object.__setattr__(self, "supporting_region_ids",
                           _str_tuple("supporting_region_ids",
                                      self.supporting_region_ids))
        if not isinstance(self.source_fact_anchors, (tuple, list)):
            raise ValidationError("source_fact_anchors must be FactAnchor items")
        anchors = tuple(self.source_fact_anchors)
        for a in anchors:
            if not isinstance(a, FactAnchor):
                raise ValidationError(f"anchor is not a FactAnchor: {a!r}")
        object.__setattr__(self, "source_fact_anchors", anchors)
        object.__setattr__(self, "required_order_relationships",
                           _order_pairs(self.required_order_relationships))

    @property
    def identity(self) -> SourceIdentity:
        return SourceIdentity(self.source_id, self.source_sha256,
                              self.page_index, self.render_sha256)


@dataclass(frozen=True)
class QuestionDraftSnapshot:
    """Immutable point-in-time copy of a question draft: all five text
    fields plus its evidence (support, anchors, required order)."""

    question_id: str
    grade: str
    objective: str
    bloom_task_rationale: str
    question: str
    answer_guidance: str
    supporting_region_ids: tuple[str, ...]
    source_fact_anchors: tuple[FactAnchor, ...]
    required_order_relationships: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class QuestionSnapshot:
    """Lineage view: the draft as originally received vs as it stands
    now, and how it arrived ("teacher_create" | "model_proposal")."""

    original: QuestionDraftSnapshot
    current: QuestionDraftSnapshot
    origin: str


@dataclass
class _Question:
    question_id: str
    grade: str
    objective: str
    bloom_task_rationale: str
    question: str
    answer_guidance: str
    supporting_region_ids: tuple[str, ...]
    source_fact_anchors: tuple[FactAnchor, ...]
    required_order_relationships: tuple[tuple[str, str], ...]
    original: QuestionDraftSnapshot  # as received; never rewritten by edits
    origin: str  # teacher_create | model_proposal
    revision: int = 0
    teacher_decision: str = "provisional"  # provisional | approved | rejected
    approved_fingerprint: str | None = None  # lives on the record itself


@dataclass(frozen=True)
class ResolvedAnchor:
    region_id: str
    exact_quote: str
    current_teacher_text: str


@dataclass(frozen=True)
class PreviewItem:
    question_id: str
    grade: str
    objective: str
    question: str
    answer_guidance: str  # may infer across anchors; need not be verbatim
    resolved_anchors: tuple[ResolvedAnchor, ...]
    dependency_fingerprint: str


@dataclass(frozen=True)
class Preview:
    source_id: str
    source_sha256: str
    page_index: int
    render_sha256: str
    rights_ref: str
    rights_is_clearance: bool
    is_durable: bool
    durability_label: str
    items: tuple[PreviewItem, ...]


class Workspace:
    """Single-source, single-authority, in-memory review workspace."""

    is_durable = False
    durability_label = "IN_MEMORY_NON_DURABLE"

    def __init__(self, source: SourcePage) -> None:
        if not isinstance(source, SourcePage):
            raise ValidationError("workspace source must be a SourcePage")
        if source.coordinate_convention not in _SUPPORTED_CONVENTIONS:
            raise ValidationError(
                f"unsupported coordinate convention: "
                f"{source.coordinate_convention!r}; supported: "
                f"{sorted(_SUPPORTED_CONVENTIONS)}")
        self._source = source
        self.workspace_epoch = 0  # stale-write guard only (see module doc)
        self._proposals: dict[str, RegionProposal] = {}
        self._reviewed: dict[str, ReviewedRegion] = {}
        self._order: set[tuple[str, str]] = set()
        self._order_revisions: dict[tuple[str, str], int] = {}
        self._questions: dict[str, _Question] = {}

    @property
    def source(self) -> SourcePage:
        """The single bound source. Read-only through the public API:
        rebinding raises AttributeError (no setter). Deliberate
        private/reflection mutation is outside this cooperative
        in-process boundary."""
        return self._source

    # -- identity-bound ingestion ----------------------------------------------
    def _check_identity(self, identity: SourceIdentity) -> None:
        if identity != self.source.identity:
            raise SourceMismatchError(
                f"result identity {identity!r} does not match this "
                f"source/page/render {self.source.identity!r}")

    def _check_box_range(self, box: tuple[float, float, float, float]) -> None:
        x0, y0, x1, y1 = box
        conv = self.source.coordinate_convention
        if conv in _FIXED_BOX_LIMITS:
            limits = _FIXED_BOX_LIMITS[conv]
        elif conv == _RASTER_PIXELS:
            limits = (float(self.source.width), float(self.source.height))
        else:  # unreachable after __init__ validation; refuse, never guess
            raise ValidationError(f"unsupported coordinate convention: {conv!r}")
        if x0 < 0.0 or y0 < 0.0 or x1 > limits[0] or y1 > limits[1]:
            raise ValidationError(
                f"source_box out of range for {conv!r}: {box!r}")

    def submit_region_proposal(self, proposal: RegionProposal,
                               outcome: str = _OK) -> None:
        _check_outcome(outcome)
        if not isinstance(proposal, RegionProposal):
            raise ValidationError("expected a RegionProposal")
        self._check_identity(proposal.identity)
        if proposal.coordinate_convention != self.source.coordinate_convention:
            raise SourceMismatchError(
                "coordinate convention does not match this source: "
                f"{proposal.coordinate_convention!r} != "
                f"{self.source.coordinate_convention!r}")
        self._check_box_range(proposal.source_box)
        existing = self._proposals.get(proposal.region_id)
        if existing is not None:
            if existing == proposal:
                return  # identical resubmission: idempotent no-op
            raise ValidationError(
                f"region id {proposal.region_id!r} is already owned by an "
                "admitted proposal; conflicting re-use is rejected so raw "
                "suggestion, geometry and crop identity cannot be re-homed")
        self._proposals[proposal.region_id] = proposal

    def get_region_proposal(self, region_id: str) -> RegionProposal:
        try:
            return self._proposals[region_id]
        except KeyError:
            raise ValidationError(f"unknown region: {region_id!r}") from None

    # -- teacher review ---------------------------------------------------------
    def review_region(self, region_id: str, teacher_text: str,
                      inclusion: str) -> ReviewedRegion:
        if region_id not in self._proposals:
            raise ValidationError(f"unknown region: {region_id!r}")
        _str("teacher_text", teacher_text)
        if inclusion not in _INCLUSIONS:
            raise ValidationError(f"unknown inclusion state: {inclusion!r}")
        prev = self._reviewed.get(region_id)
        if prev and prev.teacher_text == teacher_text and prev.inclusion == inclusion:
            return prev  # no actual change: revokes nothing, bumps nothing
        reviewed = ReviewedRegion(region_id, teacher_text, inclusion,
                                  (prev.revision + 1) if prev else 1)
        self._reviewed[region_id] = reviewed
        self.workspace_epoch += 1
        return reviewed

    def get_reviewed_region(self, region_id: str) -> ReviewedRegion:
        try:
            return self._reviewed[region_id]
        except KeyError:
            raise ValidationError(f"region not reviewed: {region_id!r}") from None

    def set_order_relationship(self, before_id: str, after_id: str,
                               present: bool) -> None:
        for rid in (before_id, after_id):
            if rid not in self._proposals:
                raise ValidationError(f"order references unknown region: {rid!r}")
        if before_id == after_id:
            raise ValidationError("order relationship needs two distinct regions")
        if not isinstance(present, bool):
            raise ValidationError("present must be a bool")
        pair = (before_id, after_id)
        if (pair in self._order) == present:
            return  # no actual change
        if present:
            self._order.add(pair)
        else:
            self._order.discard(pair)
        # Monotonic per-pair revision: remove+restore can never recreate
        # the fingerprint an earlier approval was granted against.
        self._order_revisions[pair] = self._order_revisions.get(pair, 0) + 1
        self.workspace_epoch += 1

    # -- questions ---------------------------------------------------------------
    def create_question(self, question_id: str, grade: str, objective: str,
                        bloom_task_rationale: str, question: str,
                        answer_guidance: str,
                        supporting_region_ids: tuple[str, ...],
                        source_fact_anchors: tuple[FactAnchor, ...],
                        required_order_relationships: tuple[tuple[str, str], ...]
                        ) -> None:
        self._add_question("teacher_create", question_id, grade, objective,
                           bloom_task_rationale, question, answer_guidance,
                           supporting_region_ids, source_fact_anchors,
                           required_order_relationships)

    def _add_question(self, origin: str, question_id: str, grade: str,
                      objective: str, bloom_task_rationale: str, question: str,
                      answer_guidance: str,
                      supporting_region_ids: tuple[str, ...],
                      source_fact_anchors: tuple[FactAnchor, ...],
                      required_order_relationships: tuple[tuple[str, str], ...]
                      ) -> None:
        _str("question_id", question_id, True)
        if question_id in self._questions:
            raise ValidationError(
                f"question id {question_id!r} already exists; creation and "
                "provider proposals never overwrite teacher-owned work "
                "or inherit its approval (use a new id for new work)")
        for name, value in (("grade", grade), ("objective", objective),
                            ("bloom_task_rationale", bloom_task_rationale),
                            ("question", question),
                            ("answer_guidance", answer_guidance)):
            _str(name, value)
        support = _str_tuple("supporting_region_ids", supporting_region_ids)
        if not support or len(set(support)) != len(support):
            raise ValidationError("supporting_region_ids must be non-empty and unique")
        for rid in support:
            if rid not in self._proposals:
                raise ValidationError(f"supporting region is unknown: {rid!r}")
        if not isinstance(source_fact_anchors, (tuple, list)) or not source_fact_anchors:
            raise EvidenceError("question needs at least one fact anchor")
        anchors = tuple(source_fact_anchors)
        for a in anchors:
            if not isinstance(a, FactAnchor) or not a.exact_quote:
                raise EvidenceError(f"anchor is not a real non-empty fact: {a!r}")
            if a.region_id not in support:
                raise EvidenceError(
                    f"anchor region {a.region_id!r} is not a supporting region")
        orders = _order_pairs(required_order_relationships)
        for before, after in orders:
            if before == after or before not in self._proposals or after not in self._proposals:
                raise ValidationError(
                    f"required order relationship references unknown regions: "
                    f"{(before, after)!r}")
        original = QuestionDraftSnapshot(
            question_id, grade, objective, bloom_task_rationale, question,
            answer_guidance, support, anchors, orders)
        self._questions[question_id] = _Question(
            question_id, grade, objective, bloom_task_rationale, question,
            answer_guidance, support, anchors, orders, original, origin)

    def submit_question_proposal(self, proposal: QuestionProposal) -> None:
        if not isinstance(proposal, QuestionProposal):
            raise ValidationError("expected a QuestionProposal")
        _check_outcome(proposal.outcome)
        self._check_identity(proposal.identity)
        if proposal.generated_at_epoch != self.workspace_epoch:
            raise StaleProposalError(
                f"proposal epoch {proposal.generated_at_epoch} != current "
                f"workspace epoch {self.workspace_epoch}; refusing stale write")
        # Fresh + identity-matched still creates only a new provisional
        # draft; an existing id is rejected by _add_question. The draft's
        # lineage records that it arrived as a model proposal.
        self._add_question(
            "model_proposal", proposal.question_id, proposal.grade,
            proposal.objective, proposal.bloom_task_rationale, proposal.question,
            proposal.answer_guidance, proposal.supporting_region_ids,
            proposal.source_fact_anchors, proposal.required_order_relationships)

    _EDITABLE = ("grade", "objective", "bloom_task_rationale", "question",
                 "answer_guidance")

    def edit_question(self, question_id: str, **changes: str) -> None:
        q = self._question(question_id)
        unknown = set(changes) - set(self._EDITABLE)
        if unknown:
            raise ValidationError(f"non-editable question fields: {sorted(unknown)}")
        actual = {n: _str(n, v) for n, v in changes.items()
                  if getattr(q, n) != v}
        if not actual:
            return  # no actual change
        for name, value in actual.items():
            setattr(q, name, value)
        q.revision += 1
        # An edit revokes an approval (re-approval must be explicit), but
        # it never un-rejects: a rejected question stays rejected until
        # an explicit teacher approval against current dependencies.
        if q.teacher_decision != "rejected":
            q.teacher_decision = "provisional"
        self.workspace_epoch += 1

    def question_snapshot(self, question_id: str) -> QuestionSnapshot:
        """Immutable original/current view; the returned snapshot does
        not change when the question is edited afterwards."""
        q = self._question(question_id)
        current = QuestionDraftSnapshot(
            q.question_id, q.grade, q.objective, q.bloom_task_rationale,
            q.question, q.answer_guidance, q.supporting_region_ids,
            q.source_fact_anchors, q.required_order_relationships)
        return QuestionSnapshot(original=q.original, current=current,
                                origin=q.origin)

    def reject_question(self, question_id: str) -> None:
        """Explicit teacher rejection. Persists through source/question
        edits; only a later explicit approval (validating current
        dependencies) can restore the question."""
        q = self._question(question_id)
        q.teacher_decision = "rejected"

    def _question(self, question_id: str) -> _Question:
        try:
            return self._questions[question_id]
        except KeyError:
            raise ValidationError(f"unknown question: {question_id!r}") from None

    # -- evidence, fingerprint, approval ----------------------------------------
    @staticmethod
    def _dependency_region_ids(q: _Question) -> tuple[str, ...]:
        """The one dependency definition, consumed by both resolution
        and fingerprinting: supporting regions UNION the endpoints of
        required order relationships. Order support is a real
        dependency (reviewed + included, revision-pinned) even when
        the endpoint carries no fact anchor."""
        deps = set(q.supporting_region_ids)
        for before, after in q.required_order_relationships:
            deps.add(before)
            deps.add(after)
        return tuple(sorted(deps))

    @staticmethod
    def _check_complete(q: _Question) -> None:
        # A blank draft may exist and be edited, but an explicitly
        # approved question must be complete. Whitespace is only
        # inspected here; stored text is never trimmed.
        for name in ("grade", "objective", "bloom_task_rationale",
                     "question", "answer_guidance"):
            if getattr(q, name).strip() == "":
                raise ValidationError(
                    f"question field {name!r} is blank; an incomplete "
                    "question cannot be approved")

    def _resolve(self, q: _Question) -> tuple[ResolvedAnchor, ...]:
        self._check_complete(q)
        if not q.supporting_region_ids or not q.source_fact_anchors:
            raise EvidenceError("question needs supporting regions and anchors")
        resolved = []
        for anchor in q.source_fact_anchors:
            if anchor.exact_quote.strip() == "":
                raise EvidenceError(
                    "empty/whitespace-only exact_quote is not a supporting fact")
            if anchor.region_id not in q.supporting_region_ids:
                raise EvidenceError(
                    f"anchor region {anchor.region_id!r} is not a supporting region")
            reviewed = self._reviewed.get(anchor.region_id)
            if reviewed is None:
                raise EvidenceError(f"region {anchor.region_id!r} is not reviewed")
            if reviewed.inclusion != "included":
                raise EvidenceError(
                    f"region {anchor.region_id!r} is {reviewed.inclusion}, "
                    "not included; it cannot support an approved question")
            # Exact codepoint match against *current* teacher text.
            if anchor.exact_quote not in reviewed.teacher_text:
                raise EvidenceError(
                    f"anchor quote does not resolve in current teacher text "
                    f"of {anchor.region_id!r}: {anchor.exact_quote!r}")
            resolved.append(ResolvedAnchor(anchor.region_id, anchor.exact_quote,
                                           reviewed.teacher_text))
        for rid in self._dependency_region_ids(q):
            reviewed = self._reviewed.get(rid)
            if reviewed is None or reviewed.inclusion != "included":
                raise EvidenceError(
                    f"dependency region {rid!r} is not reviewed+included "
                    "(support and order-only endpoints alike)")
        for pair in q.required_order_relationships:
            if pair[0] not in self._proposals or pair[1] not in self._proposals:
                raise EvidenceError(
                    f"required order references unknown region: {pair!r}")
            if pair not in self._order:
                raise EvidenceError(f"required order relationship missing: {pair!r}")
        return tuple(resolved)

    def _fingerprint(self, q: _Question) -> str:
        ident = self.source.identity
        parts = [ident.source_id, ident.source_sha256, ident.render_sha256,
                 str(ident.page_index), q.grade, q.objective, q.question,
                 q.answer_guidance, str(q.revision)]
        for rid in self._dependency_region_ids(q):
            r = self._reviewed.get(rid)
            parts.append("\x00".join(
                (rid, r.teacher_text, r.inclusion, str(r.revision)))
                if r else f"{rid}\x00MISSING")
        for pair in sorted(q.required_order_relationships):
            parts.append(f"{pair[0]}<{pair[1]}={pair in self._order}"
                         f"@r{self._order_revisions.get(pair, 0)}")
        for a in sorted(q.source_fact_anchors, key=lambda a: (a.region_id, a.exact_quote)):
            parts.append(f"{a.region_id}\x00{a.exact_quote}")
        return hashlib.sha256("\x01".join(parts).encode("utf-8")).hexdigest()

    def approve_question(self, question_id: str) -> str:
        """Explicit teacher approval. The only way to reach the preview."""
        q = self._question(question_id)
        self._resolve(q)  # raises; excluded/unresolved/stale evidence blocks
        fingerprint = self._fingerprint(q)
        q.approved_fingerprint = fingerprint
        q.teacher_decision = "approved"
        return fingerprint

    def question_status(self, question_id: str) -> str:
        q = self._question(question_id)
        if q.teacher_decision == "rejected":
            return "rejected"
        if q.approved_fingerprint is None:
            return "provisional"
        if q.teacher_decision != "approved":
            return "invalidated"
        if q.approved_fingerprint != self._fingerprint(q):
            return "invalidated"
        try:
            self._resolve(q)  # completeness + current evidence must agree
        except ContractError:
            return "invalidated"
        return "approved"

    def approved_preview(self) -> Preview:
        """Approved teacher artefact only; re-resolves evidence right now."""
        items = []
        for qid in sorted(self._questions):
            if self.question_status(qid) != "approved":
                continue
            q = self._questions[qid]
            assert q.approved_fingerprint is not None  # status approved implies it
            items.append(PreviewItem(
                qid, q.grade, q.objective, q.question, q.answer_guidance,
                self._resolve(q), q.approved_fingerprint))
        s = self.source
        return Preview(s.source_id, s.source_sha256, s.page_index,
                       s.render_sha256, s.rights_ref, s.rights_is_clearance,
                       self.is_durable, self.durability_label, tuple(items))
