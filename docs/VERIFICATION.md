# Verification — Iynaa Lesson Studio

This report records the measured engineering evidence for
Iynaa Lesson Studio, a teacher-focused source-to-lesson
tool.
Its scope is the teacher flow from a full Arabic source
page, through teacher source review and six Bloom
question slots, to a teacher lesson preview that contains
approved questions only.

Studio is a new composition of that flow: distinct
creation, configuration, and confirmation steps,
original-versus-reviewed source, a focused individual
question navigator, and a teacher lesson preview.
It is not a mere restyling and not a clone of a full
existing Iynaa platform.
The public API, domain, transport, and approval rules are
unchanged.
By default, file selection is first; an explicit optional
no-file path for grade and goal settings remains
available.
Same keyed drafts are retained through question and stage
navigation; explicit save or discard comes before
approval, and approval or preview is never automatic.
Evidence is the full-page original, not tight highlighted
localization.
The generation failure or abstention summary remains
readable; full slot outcomes and raw technical metadata
are disclosed on demand.

Root and browser counts below for the first public-code
baseline are historical results.
They are not current hosted acceptance totals.
Hosted verification methods are described separately,
without manually maintained latest counts in this file.
Current final verification for this Studio source is
recorded separately from the exact packaged sources and
raw test results; it is not asserted here as complete.
This file describes verification methods and limits and
makes no safety guarantee.

The sections below keep these kinds of evidence separate:
historical root tests, historical browser tests, current
local synthetic Studio verification, earlier hosted
verification, the distinct newest genuine guided run
(2026-11-03), the historical local genuine rehearsal, the
earlier genuine hosted rehearsal (2026-11-02), and
professional teaching quality.
Synthetic evidence and live-model evidence are separate
layers.
No new genuine application-model call occurred for
Studio, and prior live-model evidence stays bound to its
pre-Studio version.
The guided flow covered by the genuine runs is: prepare
a page reference with no model request, inspect the exact
page, explicitly confirm analysis, work through one
active review step at a time, save or discard
same-question edits before approval, and explicitly open
the preview.
None of the test results certifies teaching quality.

Status: this source describes the local Studio candidate.
A prior guided version was served at
[hackathon-iynaa.site](https://hackathon-iynaa.site).
These documents do not assert a Studio public cutover and
do not by themselves establish which build is live.
Any later cutover needs an exact-version read-back.
This report accompanies the source; it is not an
always-current deployment-status statement.
The newest genuine run below used deployed pre-Studio
guided code.

Meta authored the original product, application code,
test suites, and the original inline vector artwork in
code (not image-model output).
The resulting code and tests were independently reviewed
and executed; independent review and execution were
separate and are not all attributed to Meta.
This report does not claim Meta performed every
independent review or all execution tooling.
Cairo is a publicly sourced third-party font licensed
under the SIL Open Font License 1.1 (SIL OFL 1.1).
It is not AI-generated and not Meta-generated.
See `THIRD_PARTY_NOTICES.md` and
`src/teacher_demo/static/fonts/OFL.txt`.

Bundled-asset note: this version adds the bundled Cairo
font and fixed same-origin mappings for
`/assets/studio.js` and `/assets/cairo.ttf`.
The content-security policy allows `font-src 'self'` only
with `default-src 'none'`, no external scripts, styles,
or fonts, no eval or inline relaxation, and no generic
static-path exposure.
No new package, runtime service, or CDN is needed.
This replaces any earlier statement that no new runtime
asset was added.

## Historical baseline: root tests

- Command: `python -B -m unittest discover -s tests -p "test_*.py" -v`
- Python: 3.11.15
- Result: 367 ordinary test methods passed.
- Failures: 0
- Errors: 0
- Skips: 0

That first public-code baseline passed the complete same
367 ordinary root methods, with zero failures, zero
errors, and zero skips.
That copy preserved unchanged executable behavior, and
that run made no new model call.
This result does not by itself accept the later hosted
launcher.

The root suites use synthetic fixtures and synthetic test
providers.
They cover contracts, safety boundaries, page handling,
HTTP journeys, cancellation and currentness behavior, and
Meta transport behavior without treating synthetic
responses as genuine model output.

Known nonblocking asynchronous-test and deprecation
warnings were present.
The recorded test logs are therefore not described as
clean.

## Historical baseline: browser tests

Browser coverage for the first public-code baseline was
executed separately from root discovery.
Root discovery does not include the browser suites.

- Groups: 17
- Method and fixture invocations: 45
- Distinct methods: 31

These runs used identical user-interface and HTTP code
and synthetic test providers.
They demonstrate browser-visible workflow behavior
against a served application.
They are not genuine Meta output and are not included in
the 367-method root count.
They do not by themselves accept the later hosted
launcher or the Studio composition.
Each browser suite still needs matching server fixtures
and navigation-specific alignment before it can support a
Studio claim; no blanket browser-green claim is made for
them.

## Current local Studio verification (synthetic)

Current Studio verification here is synthetic and local.
The complete new local journey plus specific boundary
cases have been executed with a synthetic provider,
including no-file settings access and visible keyboard focus after page preparation.
That execution is not exhaustive coverage and does not
support a claim that every test or browser file was run.

The synthetic journey exercised, against a real local
application: default file-first creation and the explicit
optional no-file grade and goal path; preparation of one
file-page with no model request; inspection of the exact
page; explicit confirmation before any source request;
original-versus-reviewed source review with a flag
summary and full details on demand; explicit save or
discard; generation only from the reviewed, included,
non-blank subset; readable generation summaries with full
slot outcomes and raw metadata on demand; the focused
individual question navigator with same keyed drafts
retained through navigation; save or discard before
same-question approval; no automatic approval and no
automatic preview; remaining in question review after a
first approval; and explicitly opening the approved-only
teacher lesson preview.
Boundary cases in that synthetic execution include draft
retention, distinct partial-draft, all-abstention,
incomplete, and invalid outcomes, budget and expiry
behavior, held status, cancellation, and recovery where
stale delivery does not replace newer work.
Desktop and phone layouts, keyboard behavior, the native
Arabic file picker, temporary-work notices, source
warnings, and reduced-motion behavior were checked
within that synthetic scope.
Bounded geometry and keyboard checks are not physical
Safari proof.

No live inference is claimed for Studio itself.
Synthetic evidence in this section and live pre-Studio
evidence in the genuine runs below are separate layers.
Synthetic evidence is not genuine Meta educational
quality and does not identify the cause of any earlier
incomplete request.
Physical device behavior, teacher gold-standard quality,
classroom quality, and all possible states remain
unproven.
Deprecation and asynchronous warnings remain; no
clean-log claim is made.
No current moving test total is maintained in this file,
and no latest count is guessed here.

## Genuine Meta rehearsals

The genuine runs below used Meta AI in explicit Meta
mode, for page understanding and source-grounded question
drafting.
The runtime code retains the model identifier and
endpoint it requires; this report refers to the model
generically as Meta AI.
The newest guided run, the historical local rehearsal,
and the earlier hosted rehearsal are distinct runs and
are not combined; their counts must not be added together.
The two earlier rehearsals were made against earlier
source.
All three runs predate Studio and remain bound to their
pre-Studio versions.
Prior failed attempts recorded below remain failures and
are not deleted or relabeled.

### Newest genuine guided run (2026-11-03) — distinct

Scope: deployed pre-art guided code; one private
development page; one source call and one question call,
no retries.

- Source request: HTTP 201
- Question request: HTTP 201
- Raw draft questions: 6
- Abstentions: 0
- Private-preview items after explicit developer review: 6
- Effective preview coverage: Remember 1, Understand 4, Create 1

All six current questions, guidance, and explanations
were explicitly edited under model-assisted developer
review, and three original higher Bloom labels were
downgraded; no evidence or source text was rewritten.
Coverage is therefore three levels only — not all six
Bloom levels — and the result is not raw-model-perfect
output.
The observed source request took 237.833 seconds and the
observed question request took 101.233 seconds.
Each duration is a single timing sample only, not a
benchmark, typical latency, or speedup claim.
This run is not combined with the earlier hosted
rehearsal (5 preview items, 1 rejected) or the historical
local rehearsal (4 preview items) below, and it is not a
live run of Studio.
No source page, screenshot, or video from this run is
published.

### Historical local rehearsal

- Source request: HTTP 201
- Question request: HTTP 201
- Raw draft questions: 6
- Provisional model-assisted developer approvals: 4
- Rejections: 2
- Approved-only preview items: 4

An earlier genuine local question request ended
incomplete with its original cause still unresolved; the
later local success does not explain it.

### Earlier hosted rehearsal (2026-11-02) — historical

- Source effort: `max`
- Question effort: `high`
- Selected output ceiling: 131072
- Source request: HTTP 201
- Question request: HTTP 201
- Raw draft questions: 6
- Abstentions: 0
- Provisionally developer-reviewed preview items: 5
- Rejected drafts: 1
- Preview coverage: Remember 1, Understand 3, Create 1

The six raw drafts are not six accepted questions.
Five drafts remained in the approved-only preview after
model-assisted developer source review and explicit
question edits and decisions, and one draft was rejected.
Accepted coverage is therefore Remember 1, Understand 3,
and Create 1 — not all six Bloom levels.
The final approved-only preview, preserved originals, and
evidence links were verified.
The standard public launcher was checked after this
rehearsal; no second full model run occurred after that
cutover.

The observed hosted source request took 119.045 seconds.
The observed hosted question request took 105.176 seconds.
Each duration describes one observed request only.
Neither is a benchmark, a typical latency, or evidence of
a controlled speedup.

Before the successful hosted rehearsal, one hosted source
attempt failed and its original cause remains unresolved.
A later hosted question attempt was diagnosed as
exhausting its earlier output ceiling.
Both remain failures, with no refunded or reset attempts.
The later success does not explain the original source
failure, and this report does not diagnose it further.

The source page, its text, private identifiers, and
screenshots from these runs are not published in this
report or repository.

Each genuine run used one development page.
Source review and teacher-facing edits were
model-assisted developer work.
They were not professional human-teacher approval and are
not a human-teacher gold standard; the newest run's six
private-preview items likewise reflect explicit developer
edits, not teacher approval.
The six live drafts edited in these rehearsals are not
human teacher-gold and are not six validated Bloom levels.
No genuine run certifies transcription or OCR accuracy,
Bloom classification, grade fit, Arabic accuracy, factual
quality, or calibration.
No new accuracy, latency, or quality claim is made for
Studio here.

## Hosted verification

Earlier hosted support is verified separately from the
historical baseline above and from the current local
synthetic Studio verification.
Native backend tests cover the hosted launcher
configuration, the finite usage ledger, and server
session idle behavior.
Real-browser tests against a served application with
synthetic providers cover the hosted notice, expiry,
preview, and draft behavior over HTTP, including the
finite attempt cap.
Held-body and late-response schedules cover responses
that arrive or complete after the browser confirmation
deadline.
Results from different operating systems are reported
separately and are not merged into one total.

An earlier full Linux root run remains historical
evidence against earlier source.
It is not a full root run of the current Studio source.
Latest counts and hashes are not manually maintained in
this file, and no raw development paths, identifiers, or
hashes are reproduced here.
Warnings are not claimed absent.

See Status at the top for what these documents do and do
not assert about what is live.
On 2026-11-02, exact assets, the standard hosted
launcher, protected anonymous session reads, secure
session cookies, and the finite retained usage policy
were read back for the build then live; any future
cutover requires its own version-specific verification
and exact-version read-back.
That read-back is not a production-quality or
availability guarantee.

Hosted verification with synthetic providers is not
another live application-model call.
It does not establish human-teacher quality.
The genuine runs above are the live-model evidence,
against the source versions stated there; the synthetic
checks add no model run and are not a live run of Studio.
No demonstration video, event submission, or public
source-page rights are claimed here.

## What synthetic coverage establishes

Synthetic tests can establish that the implemented
contracts behave as specified under controlled inputs.
That includes source-anchored validation, explicit
teacher decisions, approval invalidation, preview
eligibility, bounded lifecycles, refusal paths, and safe
handling of synthetic failure metadata.

Synthetic tests cannot establish that a genuine model
reads a new Arabic page accurately, asks good questions
for a particular grade, or produces material a
professional teacher should use without review.
The genuine runs also cannot establish those broader
claims.

## Limits of this evidence

- No professional human-teacher approval is recorded.
- No accuracy or calibration rate is reported.
- See Status at the top: these documents do not assert a
  Studio public cutover.
  No demonstration video, final event submission, or
  public source-page rights are recorded here, and
  endpoint read-backs carry no production-quality or
  availability guarantee.
- Workspace content is in memory and can be lost on
  refresh, hosted session expiry, or restart.
  Optional hosted accounting persists usage metadata
  only.
  It does not preserve pages, drafts, or decisions.
- SAM made no contribution to the final runtime.
  It was evaluated, showed no demonstrated incremental
  value in the pilot, and was removed under an explicit
  scope pivot.
- No project license has been selected, and no rights
  clearance is inferred from these tests.
