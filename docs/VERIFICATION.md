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
historical root tests, historical browser tests, hosted
verification, one genuine Meta run, and professional teaching
quality.
Only the genuine run involved the application model.
None of the test results certifies teaching quality.

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
They do not by themselves accept the later hosted launcher.

## Genuine Meta demonstration

One genuine application run used Meta AI in explicit Meta mode,
for page understanding and source-grounded question drafting.
The runtime code retains the model identifier and endpoint it
requires; this report refers to the model generically as Meta AI.

- Source request: HTTP 201
- Question request: HTTP 201
- Draft questions produced: 6
- Provisional model-assisted developer approvals: 4
- Rejections for cognitive fit: 2
- Approved-only preview items: 4

The source page, its text, private identifiers, and screenshots
from that run are not published in this report or repository.

This was one development page and one run.
The four developer approvals were provisional and model-assisted.
They were not professional human-teacher approval and are not a
human-teacher gold standard.
Because two drafts were rejected, educational acceptance of all
six Bloom levels is not claimed.
The run does not certify transcription or OCR accuracy, Bloom
classification, grade fit, Arabic accuracy, factual quality, or
calibration.

An earlier genuine
question request ended incomplete with original cause unknown; the
later success does not explain it.

## Hosted verification

Hosted support is verified separately from the historical
baseline above.
Native backend tests cover the hosted launcher configuration,
the finite usage ledger, and server session idle behavior.
Real Chrome tests against a served application with synthetic
providers cover the hosted notice, expiry, preview, and draft
behavior over HTTP, including the finite attempt cap.
Held-body and late-response schedules cover responses that
arrive or complete after the browser confirmation deadline.
Windows results and genuine Ubuntu results are reported
separately and are not merged into one total.

Exact latest hosted counts and digests belong to generated
release evidence outside the source archive.
They are not manually maintained in this file, and no raw
development paths, identifiers, or hashes are reproduced here.

Hosted verification with synthetic providers is not another
live application-model call.
It does not establish human-teacher quality.
Source support for hosting does not claim that a public URL is
live.
Deployment and live-model verification are tracked separately.
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
The single genuine run also cannot establish those broader claims.

## Limits of this evidence

- No professional human-teacher approval is recorded.
- No accuracy or calibration rate is reported.
- No live public deployment, demonstration video, or final event
  submission is recorded here.
  Hosted source support alone does not record a live endpoint.
- Workspace content is in memory and can be lost on refresh,
  hosted session expiry, or restart.
  Optional hosted accounting persists usage metadata only.
  It does not preserve pages, drafts, or decisions.
- SAM made no contribution to the final runtime.
- No project license has been selected, and no rights clearance is
  inferred from these tests.
