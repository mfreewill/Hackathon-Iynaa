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
historical root tests, historical browser tests, the current
local guided candidate, earlier hosted verification, the
historical local genuine rehearsal, the historical latest
genuine hosted rehearsal, and professional teaching quality.
The current guided candidate is the measured final source:
prepare a page reference with no model request, inspect the exact
page, explicitly confirm analysis, work through one active review
step at a time, save or discard same-question edits before
approval, and explicitly open the preview.
The completed rehearsals and earlier failed attempts involved
the application model; the engineering tests used synthetic
providers.
None of the test results certifies teaching quality.

The public site link is
[hackathon-iynaa.site](https://hackathon-iynaa.site).
It is valid for an earlier published build whose operating state,
with the historical latest hosted rehearsal below, was observed
on 2026-11-02.
The final guided UI is locally verified and is not yet deployed
there.
This report accompanies the source; it is not an always-current
deployment-status statement.

Meta AI built this standalone implementation and authored its
application code and test suites.
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
final guided UI.
For the final UI, legacy browser suites that have not been
migrated still need matching server fixtures and
navigation-specific migration; no blanket browser-green claim is
made for them.

## Current guided candidate — local verification (2026-11-03)

The full current Windows root run passed every applicable method.
One genuine-Linux-only test was skipped on Windows, and the strict
`NOT_PASS` for that skip is retained.
A genuine Ubuntu-on-WSL guard-module run, including the
counterpart of the Windows-skipped test, passed.
That guard run is not a full Linux root run, target-Linux proof,
or deployed-host evidence, and it is reported separately from the
Windows result.

Local real-application HTTP and Chrome regressions passed for the
guided desktop and phone flow with synthetic providers: select
and prepare one file-page with no model request, inspect the exact
page, explicitly confirm analysis, review source text and flags
before optional edits, use explicit save or discard, generate only
from the reviewed included non-blank subset, save or discard
same-question edits before approval, remain in question review
after a first approval, and explicitly open the approved-only
preview.
Those regressions also covered source notes, draft preservation,
distinct partial-draft, all-abstention, incomplete, and invalid
request summaries, budget and expiry behavior, selected-page
preparation with synthetic model output, held status, cancel, and
snapshot recovery schedules in which stale delivery does not
replace newer work, and bounded portrait, landscape, desktop
control-geometry, and keyboard checks.
The bounded geometry and keyboard checks are not physical-Safari
proof.

No new genuine Meta request was made for this final UI.
Local synthetic evidence is not genuine Meta educational quality
and does not identify the cause of the earlier incomplete local
request.
Some deprecation and asynchronous warnings remain; no clean-log
claim is made.
Final Linux deployment-version compatibility and a fresh
authorized genuine run remain separately gated.

## Genuine Meta rehearsals

Both genuine rehearsals used Meta AI in explicit Meta mode, for
page understanding and source-grounded question drafting.
The runtime code retains the model identifier and endpoint it
requires; this report refers to the model generically as Meta AI.
The historical local rehearsal and the latest hosted rehearsal
are distinct runs and are not combined.
Both were made against earlier source and are not proof of the
current guided candidate above.

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

### Latest hosted rehearsal (2026-11-02)

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

Each rehearsal used one development page.
The latest hosted evidence is one successful question request
at `high` effort.
Source review and teacher-facing edits were model-assisted
developer work.
They were not professional human-teacher approval and are not a
human-teacher gold standard.
Neither rehearsal certifies transcription or OCR accuracy,
Bloom classification, grade fit, Arabic accuracy, factual
quality, or calibration.

## Hosted verification

Earlier hosted support is verified separately from the historical
baseline above and from the current local guided candidate.
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
It is not a full root run of the current guided candidate, whose
Windows and Ubuntu-on-WSL guard results are reported separately
above.
Latest counts and hashes are not manually maintained in this file,
and no raw development paths, identifiers, or hashes are
reproduced here.
Warnings are not claimed absent.

The public HTTPS link at
[hackathon-iynaa.site](https://hackathon-iynaa.site) is valid for
an earlier published build.
For that earlier build, exact assets, the standard hosted
launcher, protected anonymous session reads, secure session
cookies, and the finite retained usage policy were read back on
2026-11-02.
The final guided UI is not yet deployed there, and any future
deployment of it requires version-specific verification.
The earlier read-back is not a production-quality or availability
guarantee.

Hosted verification with synthetic providers is not another
live application-model call.
It does not establish human-teacher quality.
The genuine hosted rehearsal above is historical live-model
evidence against earlier source;
the synthetic checks above do not add another model run and are
not a new genuine run of the final UI.
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
The two genuine rehearsals also cannot establish those broader
claims.

## Limits of this evidence

- No professional human-teacher approval is recorded.
- No accuracy or calibration rate is reported.
- The public demonstration link at
  [hackathon-iynaa.site](https://hackathon-iynaa.site) was read
  back on 2026-11-02 for an earlier published build.
  The final guided UI is locally verified but not yet deployed,
  so no completed release of it is recorded here.
  Final Linux deployment-version compatibility and a fresh
  authorized genuine run remain separately gated.
  No demonstration video or final event submission is recorded
  here, and the earlier endpoint read-back carries no
  production-quality or availability guarantee.
- Workspace content is in memory and can be lost on refresh,
  hosted session expiry, or restart.
  Optional hosted accounting persists usage metadata only.
  It does not preserve pages, drafts, or decisions.
- SAM made no contribution to the final runtime.
  It was evaluated, showed no demonstrated incremental value in
  the pilot, and was removed under an explicit scope pivot.
- No project license has been selected, and no rights clearance is
  inferred from these tests.
