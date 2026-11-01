# Offline source/review contract harness

Original Python, stdlib-only, Python 3.11-compatible. Provider-free:
**no model API calls, no network, no UI, no HTTP server, no renderer.**
All "provider proposals" in tests and the example are synthetic local
fixtures, never real inference, and no real school pages are used.
This harness proves contract plumbing only. It is **not** visual/OCR
quality proof and does not satisfy any live school-page model spike.

State is **in-memory and non-durable** (`durability_label =
"IN_MEMORY_NON_DURABLE"`). Rights references are metadata only
(`rights_is_clearance = False`), never legal clearance.

## Run

Use the installation, TLS-fixture, root-test, and offline-example
commands in the README. Those current Python 3.11 and
development-dependency instructions supersede any older
dependency-free setup for running this contract harness.

## Identity binding (constructors)

Every external result carries the full source/page/render identity and
is admitted only if it matches this workspace's `SourceIdentity`
(`source_id`, `source_sha256`, `page_index`, `render_sha256`) exactly.
A reused local `source_id` alone is never enough, and malformed
identity fields are rejected at construction — never silently filled
from the receiver. Both hashes must be genuine SHA-256 digests
(exactly 64 hex characters; the supplied representation, e.g. upper
case, is preserved as-is). The workspace's bound source is read-only
through the public `source` property (rebinding raises
`AttributeError`); deliberate private/reflection mutation is outside
this cooperative in-process boundary.

- `RegionProposal(region_id, source_id, source_sha256, page_index,
  render_sha256, source_box, box_origin, semantic_label,
  semantic_label_origin, text_candidate, source_crop_ref,
  order_candidate, review_reasons, coordinate_convention)`
- `QuestionProposal(question_id, source_id, source_sha256, page_index,
  render_sha256, grade, objective, bloom_task_rationale, question,
  answer_guidance, supporting_region_ids, source_fact_anchors,
  required_order_relationships, generated_at_epoch, outcome="ok")`

Both expose `.identity: SourceIdentity`; so does `SourcePage`.
`source_crop_ref` is an opaque string; no URI format is assumed.

## Lineage and teacher transitions

- `question_snapshot(qid)` returns a frozen `QuestionSnapshot` with
  `original` (the draft exactly as received — all five text fields
  plus support/anchors/required-order evidence), `current` (as it
  stands now), and `origin` (`"teacher_create"` or
  `"model_proposal"`). Edits never rewrite `original`, and an earlier
  returned snapshot does not change.
- `reject_question(qid)` is an explicit teacher decision
  (`question_status == "rejected"`). A rejected question never
  appears in the preview, stays rejected through source and question
  edits, and returns only via a fresh explicit `approve_question`
  that re-validates current dependencies and completeness.

## Ownership and revocation

- IDs are owned, not recycled. Once a region proposal is admitted, a
  conflicting re-use of its id (different raw suggestion, box, crop,
  ...) is rejected and the original is preserved; a byte-identical
  resubmission is a no-op. Once a question id exists, neither
  `create_question` nor a provider proposal can overwrite it or
  inherit its approval. New work uses a new id.
- Approval lives on the question record and counts only while the
  teacher decision is `approved` and the dependency fingerprint still
  matches. The fingerprint pins monotonic revisions (reviewed-region,
  per-order-pair, question), so changing a dependency and changing it
  back does **not** resurrect approval — a new explicit
  `approve_question` is required. Re-asserting identical values is a
  no-op and revokes nothing. Independent questions are unaffected.

## What it enforces

- Source/page/render identity (above) and the actual source-coordinate
  convention are pinned, so a new source cannot accept old results.
- Geometry origin (`box_origin`) and semantic-label origin
  (`semantic_label_origin`) are separate provenance fields.
- Model suggestion (`RegionProposal.text_candidate`, preserved raw) is
  separate from teacher text (`ReviewedRegion.teacher_text`).
- Each region is `included`, `excluded`, or `unresolved`. Only
  reviewed + included regions can support an approved question;
  unrelated reviewed passages remain usable.
- Boxes are validated at runtime, not by annotation: exactly 4 finite
  real numbers (bool/str rejected), non-degenerate, and within the
  declared convention's range. Supported conventions are an explicit
  closed set — `normalized_0_1000_top_left` (0–1000 on both axes) and
  `raster_pixels_top_left` (rendered-raster pixels, origin top-left,
  x ≤ width, y ≤ height). Anything else is rejected when the
  workspace is constructed; no scale is guessed, and no equivalence
  to any PDF-point space is promised (there is no renderer here).
  Records copy boxes and sequences into immutable tuples at
  construction, so mutating a caller's list afterwards cannot alter
  stored state.
- The actual dependency set of a question is defined once and used by
  both evidence resolution and fingerprinting: **supporting regions
  ∪ endpoints of its required order relationships**. An order-only
  endpoint must be reviewed + included and is revision-pinned (a real
  edit revokes, a change-back does not resurrect), but it needs no
  fact anchor — order support and fact support are different.
- Fact anchors must be real: `exact_quote` non-empty and not
  whitespace-only, anchored in a supporting region, resolving
  exactly — no normalization — to that region's *current* teacher
  text. Supporting regions and required order relationships must
  reference real admitted regions; dangling ids are rejected at
  `set_order_relationship`, at question creation, and again at
  approval. No semantic entailment is checked: an answer may
  legitimately infer/synthesize across anchored facts with no
  verbatim full-answer occurrence.
- A draft with a blank (whitespace-only) grade, objective, rationale,
  question, or answer guidance may exist and be edited, but it cannot
  be explicitly approved; `question_status` and the preview agree
  with that. Whitespace is only inspected for these checks — stored
  text is never trimmed or normalized.
- Async proposals carry the workspace epoch they were generated at.
  A stale proposal is rejected and can never (re)instate approval;
  even a fresh proposal creates only a provisional draft.
- Any non-`ok` provider outcome (`timeout`, `rate_limited`,
  `invalid_schema`, `no_match`, `incomplete`, `refusal`, `unknown`,
  `error`, empty) is not success and is rejected.
- Text is preserved codepoint-for-codepoint (Arabic, diacritics,
  digits, mixed scripts). Source text is opaque data, never an
  instruction: embedded "approve everything" text changes nothing.

## Non-goals

No persistence, accounts, database, network layer, renderer, UI,
automatic LLM evaluation, geometry editor, regenerate framework, or
safety framework. The approved preview is an approved teacher
artefact, not proof of a whole lesson's readiness.
