# Iynaa Lesson Studio — Arabic page-to-lesson teacher tool

Iynaa Lesson Studio is a teacher-focused source-to-lesson tool
for adult teachers.
The teacher supplies one Arabic textbook page (صفحة عربية).
Meta proposes source sections and, after teacher source
review, drafts questions.
Only questions the teacher explicitly approves reach the
teacher lesson preview.
In short: Arabic page → Meta proposals → teacher decisions →
evidence-linked lesson.

## Current release and demonstration

Current release: Studio was deployed on 2026-11-03 at
[hackathon-iynaa.site](https://hackathon-iynaa.site).
Exact source, the service entrypoint, served assets,
secure session behavior, and the retained usage policy
were verified on deployment.
On 2026-11-04, five served assets still matched the
checkout.
This records the observed state, not indefinite
availability.
This documentation correction changes no application bytes.
The source also runs locally by default.
See [docs/HOSTING.md](docs/HOSTING.md) for the optional
hosted launcher.

[Watch the demo](https://drive.google.com/file/d/1m28sUrhKsPEbPvYUZyNf43UT_WfMpdBh/view?usp=drivesdk)

The approved demonstration is an edited 111.3-second
(16:9) recording with English narration and subtitles and
an Arabic user interface.
It shows the current deployed Studio recorded on
2026-11-03, not a synthetic or pre-Studio run.
It is an edited developer demonstration with real captured
footage, source attribution, and held-frame disclosures,
not a new live session or a finished teaching lesson.
The recording retains a private-review label.
ElevenLabs was used solely for the video narration; it is
not an application feature or a Meta-generated voice.

In that recording, one source request and one question
request each returned HTTP 201.
They produced six raw drafts and no abstentions.
After explicit model-assisted developer edits, three items
reached the preview and three were rejected.
The effective preview covers Understand 2 and Remember 1
only, originals were preserved, and the source text had
been explicitly shortened to the selected reviewed passage.
The teacher role was developer-operated.
This is not professional human-teacher approval or gold,
and it does not show six valid Bloom levels.

Current root verification is summarized under Measured
evidence and in [docs/VERIFICATION.md](docs/VERIFICATION.md):
Linux passed with no skips, while the strict Windows
result is NOT_PASS (see that section for the distinction).

> Correction, 2026-11-04: earlier wording that described
> this release as undeployed and its live proof as pending
> described pre-release evidence.
> It is superseded by the observed 2026-11-03 deployment,
> recording, and verification recorded in these documents.

This is a separate implementation built around the
pre-existing Iynaa product idea and prior educational
experience.
It is not a publication of private Iynaa code.
It is a teacher-only workspace, not a released child-facing
service.
The project repository is
[github.com/mfreewill/Hackathon-Iynaa](https://github.com/mfreewill/Hackathon-Iynaa).
It was prepared in the context of the Meta Global AI
Developer Hackathon and its `Build for Your World` theme.
No organizer endorsement or guaranteed eligibility is
claimed.

## What Studio is — and is not

Studio is a new composition of the same teacher flow:
distinct creation, configuration, and confirmation steps,
original-versus-reviewed source, a focused individual
question navigator, and a teacher lesson preview.
It is not a mere restyling of the prior guided flow and it
is not a clone of a full existing Iynaa platform.
The public API, domain, transport, and approval rules are
unchanged.

It is also not a generic education chatbot:

- There is no open-ended student chat and there are no
  student accounts.
- Work starts from one supplied Arabic page, not from an
  unbounded prompt.
- Source text is proposed first and must be reviewed
  before any question generation.
- Supplied and reviewed text is not silently stripped or
  normalized.
- Questions are versioned, structured, source-anchored
  proposals, not free-form chat replies.
- Each question needs an explicit teacher edit, approval,
  or rejection decision.
- Raw page material and raw model drafts stay
  distinguishable from teacher-reviewed text, decisions,
  and preview items.
- Editing a source or question dependency can invalidate a
  prior approval.
- Approval is never restored automatically.

No new export, accounts, persistence, or other scope is
added by this description.

## Teacher-controlled flow

Create and configure a page → confirm analysis → compare
original and reviewed source → generate → review one
question at a time → explicitly open the lesson preview.

1. By default, file selection is first: the teacher
   chooses a page file and selects one file-page, then
   sets the grade and learning objective, then prepares
   the page.
   An explicit optional no-file path remains available for
   setting grade and goal before a file is chosen.
   Preparation shows the page reference only; it sends no
   model request.
2. The teacher inspects the exact page, then explicitly
   confirms analysis to send the source request.
   No model call happens before that confirmation.
   In explicit Meta mode, Meta then proposes source
   sections for the full page.
3. Source review keeps the original proposal and the
   reviewed text distinct.
   A summary of current review flags is shown where
   attention is needed, with the full flag details behind
   a disclosure.
   The teacher saves or discards edits explicitly, decides
   whether each section is included, excluded, or
   unresolved, and can adjust the displayed order.
4. Only reviewed, included, non-blank text, together with
   the grade and objective, is used for question
   generation.
5. The application returns one slot per Bloom level.
   A slot may contain a draft question or an abstention.
   The generation failure or abstention summary remains
   readable; full slot outcomes and raw technical metadata
   are shown on demand behind a disclosure.
   Partial drafts, all abstentions, incomplete results,
   and invalid results stay distinct.
   Drafts carry structured cognitive explanation and,
   where the level requires them, assumptions, evaluation
   criteria, or a creation rubric with stated source or
   objective origins.
6. The question navigator focuses one question at a time.
   Same keyed drafts are retained through question and
   stage navigation.
   Unsaved edits to the same question must be explicitly
   saved or discarded before that question can be
   approved.
   Approval is never automatic.
   A first approval stays in question review; it does not
   jump to the preview.
   Draft, rejected, blocked, and outdated questions do
   not enter the preview.
7. The teacher explicitly opens the lesson preview.
   Preview is never automatic.
   It contains approved current questions only, while the
   preserved originals remain available for comparison.

Analysis covers the supplied full page.
Evidence is the full-page original; the application does
not claim tight localization of a region or passage on
the page.

## Bundled assets and content security

This version adds exactly two bundled same-origin assets
to the fixed client surface: `/assets/studio.js` and
`/assets/cairo.ttf`.
These are in addition to the setup shell and its existing
stylesheet and scripts.
No new package, runtime service, or CDN is needed.
This is distinct from a claim that no new asset was added.

Cairo is a publicly sourced third-party font licensed
under the SIL Open Font License 1.1 (SIL OFL 1.1).
It is not AI-generated and not Meta-generated.
See `docs/THIRD_PARTY_NOTICES.md` and
`src/teacher_demo/static/fonts/OFL.txt` for the notice and
license text.

The content-security policy allows `font-src 'self'`
only.
It uses `default-src 'none'`, allows no external scripts,
styles, or fonts, and allows no eval or inline relaxation.
There is no generic static-path exposure: client files
are served only through the fixed route mapping above.

The composition is an original right-to-left Studio
layout for adult teachers, with finite entrance and action
transitions that respect reduced-motion settings and a
true Arabic file selector backed by the native file
picker.
Temporary-work warnings keep a compact summary with the
exact full notices still accessible, while active errors
and expiry stay visible; source flags likewise use
summary plus full-details disclosure.

## Technical notes

- Source identity binds the page, its selected rendering,
  and its hashes.
  Evidence quotes must resolve exactly, codepoint for
  codepoint, in the current reviewed text of the cited
  section.
- Approval depends on the question, its supporting
  sections, any required order relationships, and the
  workspace grade and objective.
  A real change to a dependency revokes or outdates the
  affected approvals only.
  Changing a value back does not resurrect an approval.
- Per-field authorship distinguishes model wording from
  teacher edits.
  Approval alone does not make a question
  teacher-authored.
- The HTTP workspace uses bounded request and generation
  lifecycles, explicit request and generation identity,
  and named cancellation and unknown-outcome states
  instead of treating every outcome as success.
- Explicit Meta mode uses a one-send streaming adapter to
  Meta AI for page understanding and source-grounded
  question drafting.
  Diagnostic metadata is bounded and value-free.
  Error messages do not echo caller text or credentials.
- Both launchers listen on IPv4 loopback only.
  Uploaded pages, reviewed text, drafts, decisions, and
  preview state live in the running process memory only.
  They are not written to disk.
  The one durable exception is optional hosted usage
  accounting.
  It stores metadata only in a local SQLite file: the
  fixed policy, the spent attempt count, the last attempt
  start, and whether the window has closed.
  It stores no keys, page content, or drafts.
  No external database service, student account system,
  or container is required.
- SAM is not part of the final runtime.
  It was evaluated, showed no demonstrated incremental
  value in the pilot, and was removed under an explicit
  scope pivot.
  The final full-page reference is not tight automatic
  localization.

## Source and test map

- `launch.py` — local launcher for the loopback teacher
  workspace.
- `hosted_launch.py` — optional hostname-bound hosted
  launcher.
  See [docs/HOSTING.md](docs/HOSTING.md).
- `example_offline.py` — provider-free offline journey
  covering provisional questions, approval, and
  invalidation after an edit.
- `src/teacher_demo/` — teacher domain, source and
  versioned question protocols, page decoding, HTTP
  application, hosted usage accounting, and the Meta
  protocol, stream, transport, and HTTP modules.
- `src/teacher_demo/static/` — the Arabic right-to-left
  browser client: `index.html`, `workspace.css`,
  `workspace.js`, `review.js`, `review-render.js`,
  `studio.js`, and `cairo.ttf`.
  No frontend build step or bundler is required.
- `tools/generate_tls_fixtures.py` — explicit local
  generator for synthetic TLS test fixtures.
- `tests/` — root `unittest` suites for the domain,
  protocols, page decoding, HTTP journeys and safety,
  operation lifecycles, and Meta transport behavior with
  synthetic providers and fixtures.
- `tests/browser/` — separate real-browser suites.
  They are not included in root test discovery and require
  their own served application and environment variables,
  described below.

## Requirements

- Python 3.11.
  The historical baseline measurements in
  [docs/VERIFICATION.md](docs/VERIFICATION.md) were
  recorded on Python 3.11.15.
- To run the local application, install `requirements.txt`.
  It directly pins FastAPI, Starlette, Pillow, pypdfium2,
  and Uvicorn.
- To run the root tests and generate the local TLS
  fixtures, install `requirements-dev.txt`.
  It includes the runtime requirements and directly pins
  HTTPX, Playwright, and cryptography for the tests.

The Python 3.11, development-dependency, and local
TLS-fixture setup in this README is current.
Older domain-document instructions that described a
dependency-free setup or Python 3.14 test commands are
superseded for installation and testing.

No packaging or frontend build step is needed to run the
launcher.

## Verification commands

Run these exact commands from the source root.

### Windows PowerShell

Run from the clean-copy root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
New-Item -ItemType Directory -Path tests\fixtures\meta_http_tls
python -B tools\generate_tls_fixtures.py --output tests\fixtures\meta_http_tls
python -B -m unittest discover -s tests -p "test_*.py" -v
python -B example_offline.py
python -B launch.py --port 8000
```

### POSIX shell

Run from the clean-copy root:

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
mkdir tests/fixtures/meta_http_tls
python -B tools/generate_tls_fixtures.py --output tests/fixtures/meta_http_tls
python -B -m unittest discover -s tests -p "test_*.py" -v
python -B example_offline.py
python -B launch.py --port 8000
```

The `meta_http_tls` directory must be newly created and
empty.
The generator refuses a missing, nonempty, wrong-type,
link, or reparse destination and does not overwrite
existing output.
Do not point it at a populated directory.
The final launch command starts the long-running local
server.
Stop it in the usual way when that check is finished.

Use the root-test command exactly as shown.
Do not add Python's `-I` or `-E` flags.
The TLS tests inspect the ambient behavior that those
flags would alter.

The offline example is a contract demonstration.
It is not a model-quality or lesson-readiness claim.

### Local TLS test fixtures

`tools/generate_tls_fixtures.py` is a test-only utility.
It has no default destination and must be given an
existing empty directory through `--output`, as above.
Each run freshly generates only `ca.pem`, `server.pem`,
`wrong-name.pem`, and `server-key.pem` for local test
consumers.
The temporary CA private key stays in memory and is not
written.

These fixtures are synthetic test material only.
Never use them as production certificates, publish the
generated server key, or install the generated CA into a
system or browser trust store.
Generated PEM files should be excluded from a clean
source copy and regenerated locally through this explicit
step.

### Browser tests

Root discovery does not recurse into `tests/browser` and
is not browser proof.
Do not run blanket browser discovery against the default
disconnected mode.

For the self-contained Studio browser smoke, see
[docs/BROWSER_SMOKE.md](docs/BROWSER_SMOKE.md) and run the
one existing command from the source root, substituting the
actual installed Chrome path and a fresh artifact directory:

```text
python -B tools/run_studio_smoke.py --browser <installedChrome> --artifacts <freshdir>
```

That runner serves the application itself for this smoke.
From the exact archive on Windows, it passed 1 method,
with 390/1280 viewport subTests.
That run is synthetic, not Meta evidence, and gives no
Safari or quality guarantee.

Other browser suites still require a separately served
application and, depending on the suite, environment
variables including `BROWSER_BASE_URL`,
`BROWSER_EXECUTABLE`, and `BROWSER_ARTIFACT_DIR`.
Installing the Python Playwright package from
`requirements-dev.txt` does not by itself prove that a
browser executable exists at `BROWSER_EXECUTABLE`.
Each other browser suite needs matching server fixtures and
navigation-specific alignment before it can support a
claim for the Studio composition.
No claim is made here that all browser suites pass, and no
blanket browser-green or all-browser-files-executed claim
is made.

## Run the local teacher workspace

With an activated Python 3.11 environment containing at
least the runtime requirements, run:

```text
python -B launch.py --port 8000
```

Then open `http://127.0.0.1:8000/` in a browser.
The launcher serves one process on IPv4 loopback only.

### Default disconnected mode

The default command makes no model request and uses no
key, even if the Meta environment variable happens to be
present.
The workspace reports the provider as unavailable while
preserving the supplied page, reference material, and
settings in the running session.
Disconnected mode does not fabricate a model result.

### Explicit Meta mode

Meta mode is never implicit.
Its command shape is:

```text
python -B launch.py --port 8000 --meta --max-output-tokens <INTEGER_AT_LEAST_16> --timeout-seconds <POSITIVE_NUMBER>
```

For that mode only, set an authorized application key as
`HACKATHON_IYNAA_META_API_KEY` in the server process
environment before starting the launcher.
Use a local or host secret mechanism outside the
repository.
The launcher does not read `.env` automatically, and this
README contains no key value.

Keep the key server-side only.
Never put a live value in the repository, browser code,
screenshots, issues, or logs.
Starting the process makes no model request and is not by
itself evidence of a live Meta result.

### Optional hosted mode

Hosted mode is separate from the local default above.
The local launcher stays disconnected unless Meta mode is
explicit, as described in the two subsections above.
The optional hosted launcher serves the same teacher flow
for one exact public hostname, still on loopback behind
an HTTPS reverse proxy.
Its disconnected default also makes no model request.
Hosted Meta mode additionally requires the finite usage
ledger described in [docs/HOSTING.md](docs/HOSTING.md).
See Current release and demonstration at the top for the
observed 2026-11-03 deployment and the 2026-11-04 check
that five served assets still matched the checkout.
An earlier read-back on 2026-11-02 covered the build then
live and remains historical.
Neither read-back is a production-quality or indefinite
availability guarantee.

In hosted mode, the session expires after a configured
period without HTTP activity.
Typing that sends no request does not renew it.
Active server work is retained.
The browser confirmation window is conservative.
It may withdraw current approvals and the preview while a
long or stalled response is still pending.
Displayed drafts and read-only context remain visible
locally.
An explicit reload starts again and may lose unsaved
work.
There is no automatic retry, cancellation, or heartbeat.
Local mode has no hosted idle lease.

## Measured evidence

Full scope and limits are recorded in
[docs/VERIFICATION.md](docs/VERIFICATION.md).

- Current released root tests (synthetic providers, zero
  application-model calls): the complete ordinary root
  test population from the exact released archive passed
  on real Linux with no skips.
  On Windows, the complete root population was exercised,
  all applicable methods passed, and one
  genuine-Linux-only test was skipped.
  The strict Windows result therefore remains NOT_PASS;
  it is never described here as all-PASS.
  Deprecation and asynchronous-test warnings remain, so
  no clean-log claim is made.
  The exact archived Studio browser smoke passed on
  Windows at its narrow and desktop viewport subtests.
  It is synthetic, not all browser files and not physical
  Safari.
  The complete local journey plus specific boundary cases
  have also been executed with a synthetic provider,
  including no-file settings access and visible keyboard focus after page preparation.
  Those targeted proofs are not blanket coverage.
  Root and browser counts for the first public-code
  baseline are recorded as historical results in
  [docs/VERIFICATION.md](docs/VERIFICATION.md).
  They are not current hosted totals.
- Post-fix title-only / illustration case (released
  source, real requests): one supported title-recall
  draft and five abstentions.
  This is bounded live evidence for the corrected
  abstention contract, not a zero-failure guarantee for
  every source or reader.
  Other post-fix page probes produced unreadable source
  proposals that were manually withheld before
  generation, and other drafts needed Bloom or
  answer-guidance corrections.
  Neither HTTP 201 nor schema conformance establishes
  educational accuracy.
- Current Studio recording (2026-11-03, deployed
  Studio): see Current release and demonstration at the
  top for the precise result — six raw drafts, no
  abstentions, three preview items (Understand 2,
  Remember 1) and three rejected after explicit
  model-assisted developer edits.
  An earlier failed recording attempt remains a failure;
  the later success does not establish general
  reliability.
  Older live-model evidence below stays bound to its
  pre-Studio versions and is historical.
- Historical genuine guided run (2026-11-03, deployed
  pre-art, pre-Studio guided code; one private development page; one
  source call and one question call, no retries): both
  calls returned HTTP 201, with six raw drafts and no
  abstentions.
  The source request took 237.833 seconds and the
  question request 101.233 seconds; each is a single
  timing sample only, not a benchmark or typical latency.
  Six items reached a private preview only after explicit
  developer review that edited all six current questions,
  guidance, and explanations and downgraded three
  original higher Bloom labels; no evidence or source
  text was rewritten.
  Effective coverage is Remember 1, Understand 4,
  Create 1 — not all six Bloom levels.
  This is not raw-model-perfect output and not
  human-teacher approval, and it is a distinct run, not
  combined with the earlier rehearsals below.
- Earlier genuine hosted rehearsal (2026-11-02, earlier
  source, historical): real hosted source and question
  calls each returned HTTP 201.
  Source effort was `max`, question effort was `high`,
  and the selected output ceiling was 131072.
  The question call returned six raw drafts with no
  abstentions.
  After model-assisted developer source review and
  explicit question edits and decisions, five items
  remained as provisionally developer-reviewed preview
  items and one draft was rejected.
  The five preview items cover Remember 1, Understand 3,
  and Create 1 only — not all six Bloom levels.
  The approved-only preview, preserved originals, and
  evidence links were verified.
  The standard public launcher was checked after this
  rehearsal; at that stage, no second full model run had
  occurred after that cutover.
  The observed source request took 119.045 seconds and
  the observed question request took 105.176 seconds.
  Each is one observed request only, not a benchmark,
  typical latency, or speedup claim.
  Before this success, one hosted source attempt failed
  with its cause still unresolved, and a later hosted
  question attempt exhausted its earlier output ceiling.
  Both remain failures, and the later success does not
  explain the first source failure.
- Historical local rehearsal (distinct from the hosted
  rehearsal above): six raw drafts, four provisional
  model-assisted developer approvals, two rejections, and
  four preview items.
  The source page and its text are not published here.

Those results do not certify transcription accuracy,
Bloom level, grade fit, Arabic accuracy, or professional
teaching quality.
Source review and teacher-facing edits in these runs were
model-assisted developer work, not approval by a human
teacher.
The six live drafts edited in the earlier rehearsals are
not human teacher-gold and are not six validated Bloom
levels.
Warnings are not claimed absent, and the baseline logs
were not clean.
No new accuracy, latency, or quality claim is made for
Studio here.

## Limitations

- The observed deployment at the top is not an
  indefinite availability, event-acceptance, guaranteed
  learning-gain, or blanket public source-page rights
  claim.
  The value shown is controlled, source-grounded
  preparation with traceable teacher choices.
- Source quality varies: some post-fix page probes gave
  unreadable source proposals that were withheld before
  generation, and some drafts needed Bloom or
  answer-guidance corrections.
  A successful response does not prove educational
  accuracy.
- Single-page genuine runs do not establish accuracy or
  calibration.
  No run above covers all six Bloom levels after review,
  and acceptance of all six levels is not claimed.
- No professional human-teacher approval has been
  recorded.
- In-memory workspace content can be lost on page
  refresh, browser close, hosted session expiry, or
  server restart.
  The hosted usage ledger does not back up workspace
  content.
- Full-page analysis is not tight passage or region
  localization.
- Generated content can be wrong or unsuitable and
  requires adult teacher review before any classroom use.

## Data, rights, and safety

- Use only textbook or other source material that the
  operator is entitled to process.
- Do not add real keys, private uploads, student
  material, local databases, a hosted usage store, logs,
  or generated TLS private keys to the repository.
- The published fixtures are synthetic and contain no
  private source pages.
  No actual textbook page, teacher or student data,
  private log, or screenshot from the genuine rehearsals
  is included in the repository.
- The demonstration video shows only the one recorded
  historical Princeton / Arabic Collections Online page
  approved for that video, with attribution in the
  recording.
  Arabic Collections Online describes its materials as
  believed to be in the public domain.
  That narrow approval is not blanket rights to other
  books or pages.
- A rights reference in the workspace is metadata only.
  It is not legal clearance for source material or
  publication.
- Dependency licenses do not by themselves clear the
  rights to publish this project or use any particular
  source material.
- A `.gitignore`, if present, is accident prevention
  only.
  It is not secret, rights, or publication clearance.

## Meta AI and development attribution

Meta authored the original product, application code,
test suites, and the original inline vector artwork in
code (not image-model output).
In explicit Meta mode, the application also uses Meta AI
for page understanding and source-grounded question
drafting.
Those are distinct roles: building the implementation is
separate from the application's runtime use of Meta AI.
The resulting code and tests were independently reviewed
and executed; independent review and execution were
separate and are not all attributed to Meta.
This README does not claim Meta performed every
independent review or all execution tooling.
Cairo is third-party font software, not Meta-generated
(see Bundled assets and content security above).
The runtime code retains the model identifier and
endpoint it requires; this public narrative refers to the
model generically as Meta AI.
None of those contributions is professional teacher
judgement, and this README claims neither human-only
coding nor human-teacher approval.

## License and publication status

No license has been selected.
Nothing in this README selects one or clears rights for a
future textbook page, dependency set, hosting, or wider
release.
Approval of the demonstration video for judging access
does not select a project license or grant blanket
public source-page rights.
