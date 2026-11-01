# Teacher-review domain (`src/teacher_demo/`)

Offline, stdlib-only, Python 3.11-compatible domain slice. It makes
**no model API call and no network request**, renders no page, and
serves no UI/HTTP. Callers put `src/` on `sys.path` (as the tests and
`example_offline.py` do), build proposals elsewhere, and admit them
here for teacher review. All test fixtures are synthetic.

## Shape of the code

- `teacher_demo.adapter.TeacherWorkspace` wraps the accepted
  `source_contract.Workspace` core for source identity, exact fact
  anchors, dependency resolution, and teacher decisions. It does not
  rewrite or bypass the core, and it never exposes the core
  create-from-blank path: there is no `create_question` or
  `create_region` on the adapter.
- `teacher_demo.source_protocol` mechanically validates the source
  section shape (`sections` / `page_continues` / `review_notes`).
  Admitted sections are linked to the full page with a decoder-owned
  full-page extent (`box_origin =
  "decoder_full_page_extent_not_model_geometry"`, `page_link =
  "full_page"`). That is evidence access, not a claim that any
  section was geometrically localized on the page.
- `teacher_demo.question_protocol_v2` is the versioned question
  protocol described below.
- Raw model output is stored exactly as supplied (deep-copied, never
  stripped or normalized); external IDs are namespaced by request
  (`"<request_id>:<external_id>"`) without rewriting the stored raw
  output. Exact Unicode (tashkeel, Arabic-Indic digits, newlines)
  and per-field model/teacher authorship are preserved.

## Public adapter surface (`TeacherWorkspace`)

Construct with `TeacherWorkspace(decoded_source, *, grade,
objective)`, where `decoded_source` is a
`teacher_demo.DecodedSourcePage` wrapping one
`source_contract.SourcePage`. Read-only properties: `core_workspace`,
`source`, `grade`, `objective`, `source_revision`,
`settings_revision`. Errors are `TeacherDemoError` and its subclass
`RequestStateError` (unknown, closed, or superseded/stale request).
Error messages never echo caller text.

Settings and source requests:

- `set_grade_objective(grade, objective)` — one workspace-wide
  setting; an identical re-set is a no-op, a real change bumps
  `settings_revision`.
- `begin_source_request(request_id=None) -> str` — fresh request
  identity; supersedes any still-working source request.
- `admit_source_proposal(request_id, raw, *, model_name=None) ->
  tuple` — validates and admits sections as *unreviewed*
  suggestions; identical re-delivery is idempotent, a conflicting
  re-use of an identity is rejected and the original kept.
- `finish_source_request(request_id, outcome)` /
  `cancel_source_request(request_id)` — close the request;
  already-admitted items are kept and late results are refused.

Region review (explicit teacher actions only):

- `set_region_inclusion(region_id, inclusion)` with `inclusion` in
  `included` / `excluded` / `unresolved` (regions start
  `unreviewed`).
- `edit_region_text(region_id, new_text)` /
  `edit_region_label(region_id, new_label)` — raw text/label are
  never overwritten; identical-value edits are no-ops.
- `move_region_up(region_id)` / `move_region_down(region_id) ->
  bool` — display order only; never changes revisions or approvals.
- `set_required_precedence(before_id, after_id, present)` — specific
  required order pairs, distinct from display position.

Question requests:

- `begin_question_request(request_id=None, requested_slots=None) ->
  str` — requires terminal source analysis (no source request still
  working) and at least one *usable* region. The usable population
  is the reviewed `included` regions whose current teacher text is
  nonblank: `str.strip()` decides blankness only, and usable text is
  kept exactly (whitespace, tashkeel, and mixed digits preserved).
  A blank region keeps its teacher `included` decision visible; it
  is withheld from generation, never silently excluded or rewritten.
  A named terminal non-success (`incomplete`, `invalid_schema`,
  `empty`, `error`, `refusal`, `timeout`, `rate_limited`, `unknown`,
  `cancelled`) with already admitted usable reviewed text remains
  eligible; generation before terminal analysis, while any source
  request is working, or with no usable region left raises
  `RequestStateError`. It freezes, for this request, the usable
  current texts, the requested slots (default: one per Bloom level,
  `slot-<level>`), the grade/objective, and the source/settings
  revisions.
- `question_request_prompt(request_id) -> str` — builds the prompt
  with the packaged `question_prompt_v2` from *that request's frozen*
  usable included text (exact, never stripped), requested slots,
  grade, and objective. Values are
  embedded only as JSON data (`ensure_ascii=False`); source text is
  data, never instructions. It rejects unknown or wrong-kind,
  closed (cancelled/finished/superseded), and stale
  (source/settings revision moved since `begin_question_request`)
  requests with `RequestStateError` — the same lifecycle boundary
  `admit_question_proposal` applies before a result may land. It is
  pure and repeatable, changes no state, returns no live model
  response, and performs no I/O.
- `admit_question_proposal(request_id, raw, *, model_name=None) ->
  tuple` — validates against the frozen bindings, then admits
  drafts/abstentions per slot. Fresh questions are always drafts,
  never preapproved. Accepted-slot identity is complete: the first
  accepted item of *every* requested slot — draft or abstention,
  including its abstain reason — owns that slot's identity for the
  request. An exact repeat delivery is an idempotent no-op that
  returns the same question IDs and leaves teacher edits and
  decisions untouched; *any* differing item (changed field, changed
  abstain reason, or draft/abstain conversion in either direction)
  refuses the whole proposal atomically before any slot is applied,
  keeping every preserved original. Accepted items are owned deep
  copies: mutating caller input or a `raw_question_output` readback
  afterwards cannot change accepted identity or state. The usual
  lifecycle guards apply unchanged — unknown or wrong-kind, closed
  (cancelled/finished/superseded), and stale-revision requests are
  refused with `RequestStateError` and never applied.
- `finish_question_request(request_id, outcome)` /
  `cancel_question_request(request_id)` — close the request; items
  already admitted by that same request are preserved, and no
  partial or fabricated items are invented for missing slots
  (validation rejects empty/missing-slot output).

Question decisions and raw access:

- `edit_question(question_id, **changes)` — editable fields are
  `question`, `answer_guidance`, `cognitive_explanation`, `grade`,
  `objective`, `bloom_level`, `hypothetical_assumptions`,
  `evaluate_criteria`, and `create_rubric`; originals stay intact.
  Edits are atomic and owned: every supplied field is validated and
  canonicalized into immutable values before any write, so a
  malformed edit changes nothing and caller containers are never
  stored by reference. Admitted source support is immutable per
  field: the grouping of source-origin criteria/rubric facts (exact
  quotes, grouping, and multiplicity, separately for
  `evaluate_criteria` and for `create_rubric`) cannot be changed by
  editing — such an edit is refused atomically, even when mixed
  with otherwise valid field changes. Wording edits, objective-only
  criteria edits, and reordering that keep the same support groups
  are permitted. Broken evidence support can never be healed by an
  edit or by approval; its only recovery is a new generation that
  grounds fresh drafts (new identities) in the corrected included
  evidence, leaving the blocked card exactly as it stands.
- `approve_question(question_id) -> str` (core fingerprint) and
  `reject_question(question_id)` — see "Explicit teacher decisions".
  There is no `approve_all`.
- `raw_source_output(request_id)` / `raw_question_output(request_id)`
  — deep copies of stored raw output, or `None`.
- `snapshot() -> WorkspaceSnapshot` — see next section.

Views exported by `teacher_demo` (all frozen dataclasses):
`WorkspaceSnapshot`, `RegionView`, `QuestionView`, `SlotOutcome`,
`RequestView`, `PreviewEntry`, `CriterionView`, and
`DecodedSourcePage`.

## Snapshot and preview authority

`snapshot()` is the single authority a future page/HTTP/client turn
should read: it alone computes question `status`
(`draft` / `approved` / `rejected` / `outdated`), approval `blocks`,
region `linked_question_ids`, slot outcomes, request states,
`can_generate`, included/withheld counts, and `preview_items`.
There is no UI-owned second approval authority to keep in sync.

The generation counts are a sent/withheld caption over the one
usable population described above: `included_count` is the number
of usable regions that would be sent, and `withheld_count` is
every other region (unreviewed, excluded, unresolved, and
blank-but-`included`). `snapshot()`, `begin_question_request`
eligibility, and the frozen prompt all derive from that single
mapping, so `can_generate` can never enable an action the adapter
would refuse; a blank region's `inclusion` still reads `included`
in its `RegionView`.

`preview_items` contains only questions whose status is `approved`
with no blocks, carrying current text plus resolved anchors and the
preserved `original_question` / `original_answer_guidance` for
"view original" access. Draft, rejected, outdated, or blocked
questions never appear in the preview. Each preview entry's
`author` aggregates the question's per-field authors
(`field_authors` on `QuestionView` covers every editable field,
including `bloom_level`): a teacher edit to *any* field — not only
the question wording — makes the current preview version
teacher-authored, while untouched fields keep their own authors.
Approval alone confers no authorship, a same-value no-op edit
changes neither authorship nor approval, and authorship is
independent of the approval decision in both directions.

## Versioned mechanical question schema

`teacher_demo.question_protocol_v2` pins
`QUESTION_PROTOCOL_VERSION = "teacher-question/v2"`,
`QUESTION_SCHEMA_V2`, `BLOOM_LEVELS`, `question_prompt_v2(...)`, and
`validate_question_proposal_v2(...)`. Each item carries real
structured fields — `cognitive_explanation`,
`hypothetical_assumptions`, `evaluate_criteria`, `create_rubric`,
`evidence` (exact quotes anchored codepoint-for-codepoint in the
cited included region), and `required_order_relationships` — rather
than parsing pedagogy out of prose. Source-origin criteria must
name exact supporting facts; objective-origin criteria carry none
and must not pose as source quotes. Evaluate requires criteria;
Create requires a 2–4-criterion rubric; supported synthesis across
anchored facts is valid and no rule requires a verbatim whole
answer.

This validation is **mechanical only**: a passing result is always
`status = "DRAFT"` with `cognitive_level_certified = False` and
`factual_entailment_certified = False`. It certifies neither Bloom
level, factual entailment, nor grade/teaching quality; the teacher
decides. Live output produced against an earlier question shape does not
exercise `teacher-question/v2` and is not evidence for this
schema. No professional teacher validation is claimed.

## Explicit teacher decisions

Nothing is approved by admission, editing, or generation — only by
an explicit `approve_question`, and approval itself rewrites no
field, repairs no anchor, and fills nothing. Approval is blocked
(with named `blocks` codes on the snapshot) by blank fields, a
grade/objective mismatch with the workspace, a dependency that is
not currently included, an anchor that no longer resolves exactly,
a missing required order pair, or an invalid Evaluate/Create
criterion set. The dependency set is one complete union, used for
approval blocks, targeted invalidation, label pins, and
`linked_question_ids` alike: the regions cited by ordinary fact
anchors, *plus* every region cited only by source-origin
Evaluate/Create criteria or rubric facts (admission completes the
supporting-region set with them; no anchor is invented for them —
their exact quotes stay with the citing criterion), *plus* both
endpoints of each required order pair.

Editing an approved question's own content returns it to `draft`; a
real change to a dependency (text, label, inclusion, required pair)
marks only the previously approved dependents `outdated`;
independent approvals survive. A grade/objective change is the one
workspace-wide dependency and outdates all approvals. Identical
re-sets revoke nothing, and changing a value and changing it back
never resurrects an approval — a fresh explicit approval against
current dependencies is required. A rejected question stays
`rejected` through edits and returns only via that fresh explicit
approval. Region inclusion is a separate axis from question
approval, and authorship is a separate axis from both.

## Limits: in-memory and non-durable

All state lives in the `TeacherWorkspace`/core objects in process
memory. The domain writes no files, database, or session store: a
refresh, close, or restart of any future host loses the workspace
(`source_contract` labels this `IN_MEMORY_NON_DURABLE`). Rights
references are metadata only, never legal clearance. The adapter is
a cooperative in-process boundary, not a security boundary against
deliberate private-state mutation by same-process code.

Not in this slice: page rendering/decoding, any provider client or
live inference, HTTP/server, browser UI, persistence, accounts, a
geometry editor, or student publication. The preview is a teacher
artefact, not proof of lesson readiness.

## Run the tests

Use the installation, TLS-fixture, and root-test commands in the
README. Those current Python 3.11 and development-dependency
instructions supersede older dependency-free or Python 3.14 test
instructions for this domain slice.
