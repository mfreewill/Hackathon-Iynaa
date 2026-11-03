# Verification

This report records the measured engineering evidence for the
standalone Arabic page-to-lesson teacher demonstration.
Its scope is the teacher flow from a full source page, through
teacher source review and six Bloom question slots, to an
approved-only preview.

Root and browser counts below are the historical first
public-code baseline.
They are not current hosted acceptance totals.
Hosted verification methods are described separately, without
manually maintained latest counts in this file.

The sections below keep these kinds of evidence separate:
historical root tests, historical browser tests, current local
synthetic UI verification, earlier hosted verification, the
distinct newest genuine guided run (2026-11-03), the historical
local genuine rehearsal, the earlier genuine hosted rehearsal
(2026-11-02), and professional teaching quality.
Synthetic UI evidence and live-model evidence are separate
layers: the art-directed UI is verified synthetically, and the
unchanged live pipeline was verified before the artwork.
The guided flow is: prepare a page reference with no model
request, inspect the exact page, explicitly confirm analysis,
work through one active review step at a time, save or discard
same-question edits before approval, and explicitly open the
preview.
None of the test results certifies teaching quality.

Status (2026-11-03): the guided code is publicly deployed at
[hackathon-iynaa.site](https://hackathon-iynaa.site) and has been
read back there; the newest genuine run below used that deployed
pre-art guided code.
The new art-directed visual revision is verified locally only
and is pending public cutover.
This report accompanies the source; it is not an always-current
deployment-status statement.

Meta AI built this standalone implementation and authored its
application code, test suites, and the original inline vector
artwork in code (not image-model output).
The resulting code and tests were independently checked and
executed.
This report does not claim Meta AI performed every independent
review or all execution tooling.

## Historical baseline: root tests

- Command: `python -B -m unittest discover -s tests -p "test_*.py" -v`
- Python: 3.11.15
- Result: 367 ordinary test methods passed.
- Failures: 0
- Errors: 0
- Skips: 0

That first public-code baseline passed the complete same
367 ordinary root methods, with zero failures, zero errors, and
zero skips.
That copy preserved unchanged executable behavior, and that run
made no new model call.
This result does not by itself accept the later hosted launcher.

The root suites use synthetic fixtures and synthetic test providers.
They cover contracts, safety boundaries, page handling, HTTP
journeys, cancellation and currentness behavior, and Meta
transport behavior without treating synthetic responses as genuine
model output.

Known nonblocking asynchronous-test and deprecation warnings were
present.
The recorded test logs are therefore not described as clean.

## Historical baseline: browser tests

Browser coverage for the first public-code baseline was executed
separately from root discovery.
Root discovery does not include the browser suites.

- Groups: 17
- Method and fixture invocations: 45
- Distinct methods: 31

These runs used identical user-interface and HTTP code and
synthetic test providers.
They demonstrate browser-visible workflow behavior against a
served application.
They are not genuine Meta output and are not included in the
367-method root count.
They do not by themselves accept the later hosted launcher or the
guided UI.
For the guided UI, legacy browser suites that have not been
migrated still need matching server fixtures and
navigation-specific migration; no blanket browser-green claim is
made for them.

## Current local verification — Windows and synthetic UI (2026-11-03)

On Windows, the full root run passed all 420 applicable methods.
One genuine-Linux-only test was skipped on Windows, and the strict
`NOT_PASS` for that skip is retained; no headline pass is claimed
on the basis of the Windows result with that skip.
Target Linux full-root result for the final art source: pending.
That run is in progress independently and no result for it is
claimed here.
A genuine Ubuntu-on-WSL guard-module run, including the
counterpart of the Windows-skipped test, passed.
That guard run is not a full Linux root run, target-Linux proof,
or deployed-host evidence, and it is reported separately from the
Windows result.

Local real-application HTTP and Chrome regressions passed for the
guided desktop and phone flow with synthetic providers: select
and prepare one file-page with no model request, inspect the exact
page, explicitly confirm analysis, review source text with a flag
summary (full raw details behind a disclosure) before optional
edits, use explicit save or discard, generate only
from the reviewed included non-blank subset, save or discard
same-question edits before approval, remain in question review
after a first approval, and explicitly open the approved-only
preview.
For the exact new art code, local synthetic real-application
Chrome checks passed: first screen at 390 and 430 widths with
visible artwork, the actual native Arabic file picker with Tab,
hit, and focus behavior, exact full temporary-work notices
behind their summary, request-free source warning and provenance
disclosure, reduced-motion behavior, and guided and review cases.
Draft, control, outcome, budget, and expiry safety regressions
passed on earlier art snapshots with the same logic, and a final
changed-view core rerun passed; not every legacy browser file
was run, and no such blanket claim is made.
Those regressions also covered source notes, draft preservation,
distinct partial-draft, all-abstention, incomplete, and invalid
request summaries, budget and expiry behavior, selected-page
preparation with synthetic model output, held status, cancel, and
snapshot recovery schedules in which stale delivery does not
replace newer work, and bounded portrait, landscape, desktop
control-geometry, and keyboard checks.
The bounded geometry and keyboard checks are not physical-Safari
proof.

No live inference is claimed for the final art revision itself.
The synthetic UI evidence in this section and the live pre-art
pipeline evidence in the newest genuine run below are separate
evidence layers.
Local synthetic evidence is not genuine Meta educational quality
and does not identify the cause of the earlier incomplete local
request.
Physical iPhone Safari behavior, teacher gold-standard quality,
classroom quality, and all possible states remain unproven.
Some deprecation and asynchronous warnings remain; no clean-log
claim is made.
Target-Linux proof for the final art source remains pending
(see above).

## Genuine Meta rehearsals

The genuine runs below used Meta AI in explicit Meta mode, for
page understanding and source-grounded question drafting.
The runtime code retains the model identifier and endpoint it
requires; this report refers to the model generically as Meta AI.
The newest guided run, the historical local rehearsal, and the
earlier hosted rehearsal are distinct runs and are not combined;
their counts must not be added together.
The two earlier rehearsals were made against earlier source.
Prior failed attempts recorded below remain failures and are
not deleted or relabeled.

### Newest genuine guided run (2026-11-03) — distinct

Scope: deployed pre-art guided code; one private development
page; one source call and one question call, no retries.

- Source request: HTTP 201
- Question request: HTTP 201
- Raw draft questions: 6
- Abstentions: 0
- Private-preview items after explicit developer review: 6
- Effective preview coverage: Remember 1, Understand 4, Create 1

All six current questions, guidance, and explanations were
explicitly edited under model-assisted developer review, and
three original higher Bloom labels were downgraded; no evidence
or source text was rewritten.
Coverage is therefore three levels only — not all six Bloom
levels — and the result is not raw-model-perfect output.
The observed source request took 237.833 seconds and the
observed question request took 101.233 seconds.
Each duration is a single timing sample only, not a benchmark,
typical latency, or speedup claim.
This run is not combined with the earlier hosted rehearsal
(5 preview items, 1 rejected) or the historical local rehearsal
(4 preview items) below, and it is not a live run of the final
art revision.
No source page, screenshot, or video from this run is published.

### Historical local rehearsal

- Source request: HTTP 201
- Question request: HTTP 201
- Raw draft questions: 6
- Provisional model-assisted developer approvals: 4
- Rejections: 2
- Approved-only preview items: 4

An earlier genuine local question request ended incomplete
with its original cause still unresolved; the later local
success does not explain it.

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
model-assisted developer source review and explicit question
edits and decisions, and one draft was rejected.
Accepted coverage is therefore Remember 1, Understand 3, and
Create 1 — not all six Bloom levels.
The final approved-only preview, preserved originals, and
evidence links were verified.
The standard public launcher was checked after this rehearsal;
no second full model run occurred after that cutover.

The observed hosted source request took 119.045 seconds.
The observed hosted question request took 105.176 seconds.
Each duration describes one observed request only.
Neither is a benchmark, a typical latency, or evidence of a
controlled speedup.

Before the successful hosted rehearsal, one hosted source
attempt failed and its original cause remains unresolved.
A later hosted question attempt was diagnosed as exhausting its
earlier output ceiling.
Both remain failures, with no refunded or reset attempts.
The later success does not explain the original source failure,
and this report does not diagnose it further.

The source page, its text, private identifiers, and screenshots
from these runs are not published in this report or repository.

Each genuine run used one development page.
Source review and teacher-facing edits were model-assisted
developer work.
They were not professional human-teacher approval and are not a
human-teacher gold standard; the newest run's six private-preview
items likewise reflect explicit developer edits, not teacher
approval.
No genuine run certifies transcription or OCR accuracy,
Bloom classification, grade fit, Arabic accuracy, factual
quality, or calibration.

## Hosted verification

Earlier hosted support is verified separately from the historical
baseline above and from the current local verification.
Native backend tests cover the hosted launcher configuration,
the finite usage ledger, and server session idle behavior.
Real Chrome tests against a served application with synthetic
providers cover the hosted notice, expiry, preview, and draft
behavior over HTTP, including the finite attempt cap.
Held-body and late-response schedules cover responses that
arrive or complete after the browser confirmation deadline.
Windows results and genuine Ubuntu results are reported
separately and are not merged into one total.

An earlier full Linux root run remains historical evidence
against earlier source.
It is not a full root run of the current source, whose Windows
and Ubuntu-on-WSL guard results are reported separately above
and whose target-Linux full-root result is still pending.
Latest counts and hashes are not manually maintained in this file,
and no raw development paths, identifiers, or hashes are
reproduced here.
Warnings are not claimed absent.

See Status (2026-11-03) at the top for what is live and what is
pending cutover.
On 2026-11-02, exact assets, the standard hosted launcher,
protected anonymous session reads, secure session cookies, and
the finite retained usage policy were read back for the build
then live; any future cutover requires its own version-specific
verification.
That read-back is not a production-quality or availability
guarantee.

Hosted verification with synthetic providers is not another
live application-model call.
It does not establish human-teacher quality.
The genuine runs above are the live-model evidence, against the
source versions stated there; the synthetic checks add no model
run and are not a live run of the final art revision.
No demonstration video or event submission is claimed here.

## What synthetic coverage establishes

Synthetic tests can establish that the implemented contracts behave
as specified under controlled inputs.
That includes source-anchored validation, explicit teacher
decisions, approval invalidation, preview eligibility, bounded
lifecycles, refusal paths, and safe handling of synthetic failure
metadata.

Synthetic tests cannot establish that a genuine model reads a new
Arabic page accurately, asks good questions for a particular grade,
or produces material a professional teacher should use without
review.
The genuine runs also cannot establish those broader
claims.

## Limits of this evidence

- No professional human-teacher approval is recorded.
- No accuracy or calibration rate is reported.
- See Status (2026-11-03) at the top: the art-directed revision
  has no public cutover yet, and its target-Linux full-root
  result is still pending.
  No demonstration video or final event submission is recorded
  here, and endpoint read-backs carry no production-quality or
  availability guarantee.
- Workspace content is in memory and can be lost on refresh,
  hosted session expiry, or restart.
  Optional hosted accounting persists usage metadata only.
  It does not preserve pages, drafts, or decisions.
- SAM made no contribution to the final runtime.
  It was evaluated, showed no demonstrated incremental value in
  the pilot, and was removed under an explicit scope pivot.
- No project license has been selected, and no rights clearance is
  inferred from these tests.
