# Hackathon Iynaa — Arabic page-to-lesson teacher demo

A standalone demonstration for adult teachers.
It runs locally by default.
An optional hostname-bound hosted launcher is also supported.
See [docs/HOSTING.md](docs/HOSTING.md).

The teacher supplies one Arabic textbook page.
The application proposes source sections from that page.
After teacher review of the source, it drafts questions across the
six Bloom levels for further teacher review.
Only questions the teacher explicitly approves reach the preview.

This is a separate implementation built around the pre-existing
Iynaa product idea and prior educational experience.
It is not a publication of private Iynaa code.
It is a teacher-only workspace, not a released child-facing
service.
The project repository is
[github.com/mfreewill/Hackathon-Iynaa](https://github.com/mfreewill/Hackathon-Iynaa).
It was prepared in the context of the Meta Global AI Developer
Hackathon and its `Build for Your World` theme.

## Why this is not a generic education chatbot

- There is no open-ended student chat and there are no student
  accounts.
- Work starts from one supplied Arabic page, not from an unbounded
  prompt.
- Source text is proposed first and must be reviewed before any
  question generation.
- Supplied and reviewed text is not silently stripped or normalized.
- Questions are versioned, structured, source-anchored proposals,
  not free-form chat replies.
- Each question needs an explicit teacher edit, approval, or
  rejection decision.
- Raw page material and raw model drafts stay distinguishable from
  teacher-reviewed text, decisions, and preview items.
- Editing a source or question dependency can invalidate a prior
  approval.
- Approval is never restored automatically.

## Teacher-controlled flow

1. The teacher uploads a page file, selects the page, and sets the
   grade and learning objective.
2. In explicit Meta mode, the application proposes source sections
   for the full page, with review flags where attention is needed.
3. The teacher edits section text and labels, decides whether each
   section is included, excluded, or unresolved, and can adjust the
   displayed order.
4. Only reviewed, included, non-blank text, together with the grade
   and objective, is used for question generation.
5. The application returns one slot per Bloom level.
   A slot may contain a draft question or an abstention.
   Drafts carry structured cognitive explanation and, where the
   level requires them, assumptions, evaluation criteria, or a
   creation rubric with stated source or objective origins.
6. The teacher edits, approves, or rejects each draft separately.
   Draft, rejected, blocked, and outdated questions do not enter
   the preview.
7. The preview contains approved current questions only, while the
   preserved originals remain available for comparison.

Analysis covers the supplied full page.
It does not claim tight localization of a region or passage on the
page.

## Technical notes

- Source identity binds the page, its selected rendering, and its
  hashes.
  Evidence quotes must resolve exactly, codepoint for codepoint, in
  the current reviewed text of the cited section.
- Approval depends on the question, its supporting sections, any
  required order relationships, and the workspace grade and
  objective.
  A real change to a dependency revokes or outdates the affected
  approvals only.
  Changing a value back does not resurrect an approval.
- Per-field authorship distinguishes model wording from teacher
  edits.
  Approval alone does not make a question teacher-authored.
- The HTTP workspace uses bounded request and generation
  lifecycles, explicit request and generation identity, and named
  cancellation and unknown-outcome states instead of treating every
  outcome as success.
- Explicit Meta mode uses a one-send streaming adapter to Meta AI
  for page understanding and source-grounded question drafting.
  Diagnostic metadata is bounded and value-free.
  Error messages do not echo caller text or credentials.
- Both launchers listen on IPv4 loopback only.
  Uploaded pages, reviewed text, drafts, decisions, and preview
  state live in the running process memory only.
  They are not written to disk.
  The one durable exception is optional hosted usage accounting.
  It stores metadata only in a local SQLite file: the fixed
  policy, the spent attempt count, the last attempt start, and
  whether the window has closed.
  It stores no keys, page content, or drafts.
  No external database service, student account system, or
  container is required.
- SAM is not part of the final runtime.

## Source and test map

- `launch.py` — local launcher for the loopback teacher workspace.
- `hosted_launch.py` — optional hostname-bound hosted launcher.
  See [docs/HOSTING.md](docs/HOSTING.md).
- `example_offline.py` — provider-free offline journey covering
  provisional questions, approval, and invalidation after an edit.
- `src/teacher_demo/` — teacher domain, source and versioned
  question protocols, page decoding, HTTP application, hosted
  usage accounting, and the Meta protocol, stream, transport, and
  HTTP modules.
- `src/teacher_demo/static/` — the Arabic right-to-left browser
  client: `index.html`, `workspace.css`, `workspace.js`,
  `review.js`, and `review-render.js`.
  No frontend build step or bundler is required.
- `tools/generate_tls_fixtures.py` — explicit local generator for
  synthetic TLS test fixtures.
- `tests/` — root `unittest` suites for the domain, protocols, page
  decoding, HTTP journeys and safety, operation lifecycles, and
  Meta transport behavior with synthetic providers and fixtures.
- `tests/browser/` — separate real-browser suites.
  They are not included in root test discovery and require their
  own served application and environment variables, described
  below.

## Requirements

- Python 3.11.
  The historical baseline measurements in
  [docs/VERIFICATION.md](docs/VERIFICATION.md) were recorded on
  Python 3.11.15.
- To run the local application, install `requirements.txt`.
  It directly pins FastAPI, Starlette, Pillow, pypdfium2, and
  Uvicorn.
- To run the root tests and generate the local TLS fixtures,
  install `requirements-dev.txt`.
  It includes the runtime requirements and directly pins HTTPX,
  Playwright, and cryptography for the tests.

The Python 3.11, development-dependency, and local TLS-fixture
setup in this README is current.
Older domain-document instructions that described a
dependency-free setup or Python 3.14 test commands are superseded
for installation and testing.

No packaging or frontend build step is needed to run the launcher.

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

The `meta_http_tls` directory must be newly created and empty.
The generator refuses a missing, nonempty, wrong-type, link, or
reparse destination and does not overwrite existing output.
Do not point it at a populated directory.
The final launch command starts the long-running local server.
Stop it in the usual way when that check is finished.

Use the root-test command exactly as shown.
Do not add Python's `-I` or `-E` flags.
The TLS tests inspect the ambient behavior that those flags would
alter.

The offline example is a contract demonstration.
It is not a model-quality or lesson-readiness claim.

### Local TLS test fixtures

`tools/generate_tls_fixtures.py` is a test-only utility.
It has no default destination and must be given an existing empty
directory through `--output`, as above.
Each run freshly generates only `ca.pem`, `server.pem`,
`wrong-name.pem`, and `server-key.pem` for local test consumers.
The temporary CA private key stays in memory and is not written.

These fixtures are synthetic test material only.
Never use them as production certificates, publish the generated
server key, or install the generated CA into a system or browser
trust store.
Generated PEM files should be excluded from a clean source copy
and regenerated locally through this explicit step.

### Browser tests

Root discovery does not recurse into `tests/browser` and is not
browser proof.
Do not run blanket browser discovery against the default
disconnected mode.

Browser suites require a separately served application and,
depending on the suite, environment variables including
`BROWSER_BASE_URL`, `BROWSER_EXECUTABLE`, and
`BROWSER_ARTIFACT_DIR`.
Installing the Python Playwright package from
`requirements-dev.txt` does not by itself prove that a browser
executable exists at `BROWSER_EXECUTABLE`.

## Run the local teacher workspace

With an activated Python 3.11 environment containing at least the
runtime requirements, run:

```text
python -B launch.py --port 8000
```

Then open `http://127.0.0.1:8000/` in a browser.
The launcher serves one process on IPv4 loopback only.

### Default disconnected mode

The default command makes no model request and uses no key, even if
the Meta environment variable happens to be present.
The workspace reports the provider as unavailable while preserving
the supplied page, reference material, and settings in the running
session.
Disconnected mode does not fabricate a model result.

### Explicit Meta mode

Meta mode is never implicit.
Its command shape is:

```text
python -B launch.py --port 8000 --meta --max-output-tokens <INTEGER_AT_LEAST_16> --timeout-seconds <POSITIVE_NUMBER>
```

For that mode only, set an authorized application key as
`HACKATHON_IYNAA_META_API_KEY` in the server process environment
before starting the launcher.
Use a local or host secret mechanism outside the repository.
The launcher does not read `.env` automatically, and this README
contains no key value.

Keep the key server-side only.
Never put a live value in the repository, browser code, screenshots,
issues, or logs.
Starting the process makes no model request and is not by itself
evidence of a live Meta result.

### Optional hosted mode

Hosted mode is separate from the local default above.
The local launcher stays disconnected unless Meta mode is
explicit, as described in the two subsections above.
The optional hosted launcher serves the same teacher flow for
one exact public hostname, still on loopback behind an HTTPS
reverse proxy.
Its disconnected default also makes no model request.
Hosted Meta mode additionally requires the finite usage ledger
described in [docs/HOSTING.md](docs/HOSTING.md).
Support for hosted mode in this source does not mean a public
endpoint is active.

In hosted mode, the session expires after a configured period
without HTTP activity.
Typing that sends no request does not renew it.
Active server work is retained.
The browser confirmation window is conservative.
It may withdraw current approvals and the preview while a long
or stalled response is still pending.
Displayed drafts and read-only context remain visible locally.
An explicit reload starts again and may lose unsaved work.
There is no automatic retry, cancellation, or heartbeat.
Local mode has no hosted idle lease.

## Measured evidence

Full scope and limits are recorded in
[docs/VERIFICATION.md](docs/VERIFICATION.md).

- Root and browser counts for the first public-code baseline are
  recorded as historical results in
  [docs/VERIFICATION.md](docs/VERIFICATION.md).
  They are not current hosted totals and are not repeated here as
  moving latest numbers.
- One genuine Meta AI demonstration completed the source and
  question requests with HTTP 201 responses.
  It produced six draft questions.
  Four received provisional model-assisted developer approval, two
  were rejected for cognitive fit, and four items remained in the
  approved-only preview.
  The source page and its text are not published here.

Those results do not certify transcription accuracy, Bloom level,
grade fit, Arabic accuracy, or professional teaching quality.
The developer decisions in the genuine run were not human-teacher
approval.
Known nonblocking asynchronous-test and deprecation warnings in
the baseline mean the recorded logs were not clean.

## Limitations

- This source supports local use by default and an optional
  hosted launcher.
  That support does not claim a live public endpoint.
  No demonstration video or event submission is claimed here.
- One development page and one genuine run do not establish
  accuracy or calibration.
  Two of six drafts were rejected, so acceptance of all six Bloom
  levels is not claimed.
- No professional human-teacher approval has been recorded.
- In-memory workspace content can be lost on page refresh,
  browser close, hosted session expiry, or server restart.
  The hosted usage ledger does not back up workspace content.
- Full-page analysis is not tight passage or region localization.
- Generated content can be wrong or unsuitable and requires adult
  teacher review before any classroom use.

## Data, rights, and safety

- Use only textbook or other source material that the operator is
  entitled to process.
- Do not add real keys, private uploads, student material, local
  databases, a hosted usage store, logs, or generated TLS private
  keys to the repository.
- The published fixtures are synthetic.
  No actual textbook page, teacher or student data, private log, or
  screenshot from the genuine run is included.
- A rights reference in the workspace is metadata only.
  It is not legal clearance for source material or publication.
- Dependency licenses do not by themselves clear the rights to
  publish this project or use any particular source material.
- A `.gitignore`, if present, is accident prevention only.
  It is not secret, rights, or publication clearance.

## Meta AI and development attribution

Meta AI built this standalone implementation and authored its
application code and test suites.
In explicit Meta mode, the application also uses Meta AI for
page understanding and source-grounded question drafting.
Those are distinct roles: building the implementation is separate
from the application's runtime use of Meta AI.
The resulting code and tests were independently checked and
executed, as recorded in the verification report.
This README does not claim Meta AI performed every independent
review or all execution tooling.
The runtime code retains the model identifier and endpoint it
requires; this public narrative refers to the model generically
as Meta AI.
None of those contributions is professional teacher judgement, and
this README claims neither human-only coding nor human-teacher
approval.

## License and publication status

No license has been selected.
Nothing in this README selects one or clears rights for a future
textbook page, dependency set, hosting, or wider release.
