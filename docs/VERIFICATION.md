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

## Current release and newest recording

Studio was deployed on 2026-11-03 at
[hackathon-iynaa.site](https://hackathon-iynaa.site).
Exact source, the service entrypoint, served assets,
secure session behavior, and the retained usage policy
were verified on deployment.
On 2026-11-04, five served assets still matched the
checkout.
This is observed state, not indefinite availability.
This documentation correction changes no application bytes.

The newest real Studio recording was made on 2026-11-03
against that deployed Studio.
One source request and one question request each returned
HTTP 201, with six raw drafts and no abstentions.
After explicit model-assisted developer edits, three items
reached the preview and three were rejected.
The effective preview covers Understand 2 and Remember 1
only; originals were preserved.
See the current recording section below for limits.
The approved edited video linked from the README is
111.3 seconds, with English narration and subtitles and
an Arabic user interface.

Current released root verification is recorded below:
Linux passed the complete ordinary root population with
no skips, while Windows remains NOT_PASS in the strict
sense because one genuine-Linux-only test was skipped.
Root and browser counts for the first public-code
baseline remain historical results.
They are not current hosted totals.
This file describes verification methods and limits and
makes no safety guarantee.

The sections below keep these kinds of evidence separate:
current released synthetic root and browser verification,
historical root tests, historical browser tests, bounded
post-fix live evidence, the current Studio recording
(2026-11-03), the historical genuine guided run
(2026-11-03), the historical local genuine rehearsal, the
earlier genuine hosted rehearsal (2026-11-02), and
professional teaching quality.
Synthetic evidence and live-model evidence are separate
layers.
Older live-model evidence below stays bound to its
pre-Studio versions and is explicitly historical.
The flow covered by the genuine runs is: prepare
a page reference with no model request, inspect the exact
page, explicitly confirm analysis, work through one
active review step at a time, save or discard
same-question edits before approval, and explicitly open
the preview.
None of the test results certifies teaching quality.

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
No new runtime dependency is added by the verification work
below; `jsonschema` is development-only.
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

## Current released Studio verification (synthetic providers)

The complete ordinary root test population from the exact
released archive passed on real Linux with no skips.
That run used synthetic providers and made zero
application-model calls.
It is not only the earlier guided baseline.
On Windows, the complete root population was exercised,
all applicable methods passed, and one
genuine-Linux-only test was skipped.
The strict Windows result therefore remains NOT_PASS and
must never be reported as all-PASS.
No current moving test total is maintained in this file,
and no latest count is guessed here.

Other current Studio verification here is synthetic and
local.
The complete new local journey plus specific boundary
cases have been executed with a synthetic provider,
including no-file settings access and visible keyboard focus after page preparation.
That targeted execution is not blanket coverage and does
not support a claim that every browser file was run.

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

Synthetic evidence in this section, bounded post-fix
live evidence below, the current Studio recording, and
older pre-Studio live evidence are separate layers.
Synthetic evidence is not genuine Meta educational
quality and does not identify the cause of any earlier
incomplete request.
Physical device behavior, teacher gold-standard quality,
classroom quality, and all possible states remain
unproven.
Deprecation and asynchronous-test warnings remain; no
clean-log claim is made.

## Released repair — bounded post-fix live evidence

A limited owner-reported title/illustration failure was
reproduced with real Meta against Studio.
The released correction addresses a prompt/schema mismatch for
status-conditioned abstention.
Raw proposals remain separate from teacher-reviewed text,
decisions, and preview items, and malformed output yields
an honest failure rather than fabricated questions.
No guarantee is made that every source produces questions.

Post-fix live evidence is bounded.
One fresh released-source title-only / illustration case
completed its source and question requests and produced
one supported title-recall draft and five abstentions.
That is evidence for the corrected abstention contract
in that case, not a zero-failure guarantee for every
source or reader.
Other post-fix page probes produced unreadable source
proposals, and those pages were manually withheld before
generation.
Other generated drafts required Bloom or
answer-guidance corrections.
These limits are stated without turning the probes into
a benchmark and without implying that all pages pass.
Neither HTTP 201 nor schema conformance establishes
educational accuracy.
The released source includes the native K-12 grade
dropdown in both setup and the question editor (empty
choice, kindergarten, and grades 1 through 12); the grade
persists as a string, with no backend enum coercion promised.

The exact browser recipe method is the one command in the
README, from the `BROWSER_SMOKE` recipe:
`python -B tools/run_studio_smoke.py --browser <installedChrome> --artifacts <freshdir>`.
From the exact archive on Windows, it passed 1 method,
with 390/1280 viewport subTests.
That run is synthetic, not Meta evidence, gives no Safari
or quality guarantee, and is not a claim that all browser
suites pass.

The released source described here is the source deployed
on 2026-11-03 and checked again on 2026-11-04 as recorded
at the top of this report.
That observed deployment state is not an indefinite
availability guarantee.

## Genuine Meta runs

The genuine runs below used Meta AI in explicit Meta
mode, for page understanding and source-grounded question
drafting.
The runtime code retains the model identifier and
endpoint it requires; this report refers to the model
generically as Meta AI.
The current Studio recording, the historical guided run,
the historical local rehearsal, and the earlier hosted
rehearsal are distinct runs and are not combined; their
counts must not be added together.
The three older runs predate Studio and remain bound to
their pre-Studio versions.
They are explicitly historical; their outcomes are not
rewritten.
Prior failed attempts recorded below remain failures and
are not deleted or relabeled.

### Current Studio recording (2026-11-03) — newest

Scope: the current deployed Studio, recorded for the
approved demonstration video.
It is a real run, not synthetic and not a pre-Studio run.
The teacher role was developer-operated.

- Source requests: 1 (HTTP 201)
- Question requests: 1 (HTTP 201)
- Raw draft questions: 6
- Raw abstentions: 0
- Preview items after explicit model-assisted developer edits: 3
- Rejected: 3
- Effective preview coverage: Understand 2, Remember 1
- Originals preserved: yes

The source text was explicitly shortened to the selected
reviewed passage.
This is model-assisted developer review, not professional
human-teacher approval or a human-teacher gold standard.
Coverage is two levels only — not all six Bloom levels —
and no claim is made that all six levels were valid.
An earlier failed recording attempt remains a failure.
This later success does not establish general reliability
and is not combined with any older run below.

The approved edited video is 111.3 seconds (16:9), with
English narration and captions and an Arabic user
interface.
It is an edited developer demonstration with real
captured footage, source attribution, and held-frame
disclosures, not a new live session and not a generic
finished teaching lesson.
The recording retains a private-review label.
ElevenLabs was used solely for the video narration; it
is not an application feature or a Meta-generated voice.
The video shows only the recorded historical Princeton /
Arabic Collections Online page approved for that video.
Arabic Collections Online describes its materials as
believed to be in the public domain.
That narrow approval is not blanket rights to other
books or pages, and synthetic repository fixtures remain
free of private source pages.

### Historical genuine guided run (2026-11-03) — distinct

Scope: deployed pre-art, pre-Studio guided code; one private
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
rehearsal; at that stage, no second full model run had
occurred after that cutover.

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
not a human-teacher gold standard; the current Studio
recording's three preview items and the historical guided
run's six private-preview items likewise reflect explicit
developer edits, not teacher approval.
The live drafts edited in these runs are not
human teacher-gold and are not six validated Bloom levels.
No genuine run certifies transcription or OCR accuracy,
Bloom classification, grade fit, Arabic accuracy, factual
quality, or calibration.
No accuracy, latency, or quality claim is made for
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
evidence against earlier source and is distinct from
the current released-archive Linux root run recorded
above.
Latest counts are not manually maintained in this file,
and no private paths or identifiers are reproduced here.
Deprecation and asynchronous-test warnings remain;
warnings are not claimed absent and no clean-log claim
is made.

See Current release and newest recording at the top for
the observed 2026-11-03 deployment and the 2026-11-04
check that five served assets still matched the checkout.
An earlier read-back on 2026-11-02 covered the build then
live and remains historical.
Those read-backs are not production-quality or
indefinite availability guarantees.

Hosted verification with synthetic providers is not
another live application-model call.
It does not establish human-teacher quality.
The genuine runs above are the live-model evidence,
against the source versions stated there; the synthetic
checks add no model run.
Approval of the demonstration video for judging access
does not by itself claim an event outcome or grant
blanket public source-page rights.

## What synthetic coverage establishes

Synthetic tests can establish that the implemented
contracts behave as specified under controlled inputs.
That includes source-anchored validation, explicit
teacher decisions, approval invalidation, preview
eligibility, bounded lifecycles, refusal paths, and safe
handling of synthetic failure metadata.
Controlled JSON and request-body engineering tests
establish only those tested contracts; they do not mean
that all vulnerabilities are absent, that the system is
secure in general, or that it is production-ready.

Synthetic tests cannot establish that a genuine model
reads a new Arabic page accurately, asks good questions
for a particular grade, or produces material a
professional teacher should use without review.
The genuine runs also cannot establish those broader
claims.

## Limits of this evidence

- No professional human-teacher approval is recorded.
- No accuracy or calibration rate is reported.
- The observed deployment and read-backs at the top
  carry no production-quality or indefinite availability
  guarantee and grant no blanket public source-page
  rights.
  Source quality still varies: unreadable source
  proposals were withheld before generation, and some
  drafts needed Bloom or answer-guidance corrections.
  No run shows six valid Bloom levels after review.
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
