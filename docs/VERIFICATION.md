# Verification

This report records the measured engineering evidence for the
standalone Arabic page-to-lesson teacher demonstration.
Its scope is the local flow from a full source page, through
teacher source review and six Bloom question slots, to an
approved-only preview.

The sections below keep four kinds of evidence separate:
root tests, browser tests, one genuine Meta run, and professional
teaching quality.
Only the genuine run involved the application model.
None of the test results certifies teaching quality.

Meta AI built this standalone implementation and authored its
application code and test suites.
The resulting code and tests were independently checked and
executed.
This report does not claim Meta AI performed every independent
review or all execution tooling.

## Root tests

- Command: `python -B -m unittest discover -s tests -p "test_*.py" -v`
- Python: 3.11.15
- Result: 367 ordinary test methods passed.
- Failures: 0
- Errors: 0
- Skips: 0

The editorial public-code copy has now passed the complete same
367 ordinary root methods, with zero failures, zero errors, and
zero skips.
That copy preserved unchanged executable behavior, and that run
made no new model call.

The root suites use synthetic fixtures and synthetic test providers.
They cover contracts, safety boundaries, page handling, HTTP
journeys, cancellation and currentness behavior, and Meta
transport behavior without treating synthetic responses as genuine
model output.

Known nonblocking asynchronous-test and deprecation warnings were
present.
The recorded test logs are therefore not described as clean.

## Browser tests

Browser coverage was executed separately from root discovery.
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
- No hosted deployment, demonstration video, or final event
  submission is recorded.
- Workspace state is in memory and can be lost on refresh or
  restart.
- SAM made no contribution to the final runtime.
- No project license has been selected, and no rights clearance is
  inferred from these tests.
