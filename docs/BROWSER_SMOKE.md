# Studio browser smoke (synthetic, self-contained)

Status: NOT_RUN in the environment that packaged this
recipe.
Run it from the exact installed archive to get a result.

`tools/run_studio_smoke.py` makes the existing real-browser
suite `tests/browser/test_studio_journey.py` runnable as one
finite command.
It serves the unmodified application itself, so no external
server has to be prepared first:

- It finds the repository root from its own `tools/`
  location and puts `src/` and `tests/` on `sys.path`.
- It starts one Uvicorn server on a random IPv4 loopback
  port (`127.0.0.1`) running the exact existing
  `create_app`, injected only with the existing test-only
  synthetic `ControlsProvider` at the canonical grade
  `الصف الخامس`.
  Allowed hosts are the exact loopback host (with and
  without the ephemeral port), the allowed origin is the
  exact served origin, and the session cookie is not marked
  secure, because the origin is local HTTP.
- It then runs only `tests/browser/test_studio_journey.py`
  in one fixed `unittest discover` child process with
  `BROWSER_BASE_URL`, `BROWSER_EXECUTABLE`, and
  `BROWSER_ARTIFACT_DIR` set.
  Afterwards it requests server shutdown in a `finally`
  block, waits only for bounded periods, and then checks
  that the server thread is no longer alive before it can
  return success.
  If the thread is still alive, it reports that shutdown
  failure and returns nonzero instead of claiming the
  server stopped.

This is synthetic UI coverage only.
The provider is the deterministic test double from
`tests/test_http_teacher_controls.py`; it is explicitly not
Meta.
No API key, environment provider path, model request,
account, download, or production binding is involved, and
starting or passing this smoke is not Meta, model-quality,
hosted, or live evidence.
It exercises Chromium through the installed Chrome you pass
with `--browser`; it proves nothing about Safari, WebKit,
or Firefox.
The Chromium sandbox stays enabled, as the suite itself
requires (`chromium_sandbox=True`); the runner does not
disable it.

## Requirements

- Python 3.11 with `requirements-dev.txt` installed (it
  pins the Python Playwright package and the runtime
  requirements).
- An already-installed Chrome or Chromium executable, given
  explicitly with `--browser`.
  The runner refuses to start if that file does not exist.
  Do not run any `playwright install` or other browser
  download step for this smoke: the suite launches the
  installed browser directly by path.

## Run it

Run from the source root.
Substitute the actual installed browser path if yours
differs.
Run the command directly in the foreground.
On Windows, do not wrap it in a detached `Start-Process`
invocation.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -B tools\run_studio_smoke.py --browser "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" --artifacts "$env:TEMP\studio-smoke-artifacts"
```

### POSIX shell

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -B tools/run_studio_smoke.py --browser /usr/bin/google-chrome --artifacts /tmp/studio-smoke-artifacts
```

`--artifacts` is optional but recommended.
The directory is caller-selected and must be fresh or
empty; the runner refuses a populated directory, so choose
a new name (or clear it) before each run.
Screenshots (`studio-<viewport>-<stage>.png`) and
`studio-smoke.log` (the child's captured stdout followed
by its full captured stderr) are written there.
Without `--artifacts`, a temporary directory is used and
cleaned up automatically, so the console output is the
record that remains.
Temporary browser files go under the artifact directory (or
the system temporary directory) and are cleaned up too.

## Behavior and limits

- Exit `0` is PASS only: the child exited `0` before the
  timeout, its stderr ends in a plain terminal `OK` (not
  `OK (...)`), it contains no skipped, expected-failure, or
  unexpected-success outcome, the `Ran` count is at least
  one and equals the number of distinct ordinary `... ok`
  test IDs, and those IDs match the test methods parsed
  from the actual `tests/browser/test_studio_journey.py`
  file.
  The expected count comes from that current file; it is
  not a hardcoded stale count.
  If an unknown base class or `load_tests` prevents an
  exact AST set, every locally parsed expected ID must
  still be among the `... ok` IDs, in addition to the
  plain-`OK` and count conditions above.
  An entirely or partly skipped suite, an expected-failure
  outcome, and a no-test suite are therefore not PASS.
  Exit `1`: test failure, any of those non-PASS outcomes,
  timeout, incomplete child cleanup, or a server thread
  still alive after bounded shutdown.
  Exit `2`: setup refused before serving (wrong Python,
  missing browser file, unexpected layout, missing
  dependency, or a non-empty `--artifacts` directory).
- The child gets `stdin` from `DEVNULL`, and its stdout and
  full stderr are captured separately, re-emitted, and
  logged.
  The PASS verdict is read from stderr, where `unittest`
  writes its report.
- The child is capped at 210 seconds with no retry.
  On timeout the runner makes a best-effort stop attempt
  (its process group on POSIX; terminate-then-kill of the
  undetached child only on Windows), with bounded process
  waits and a bounded post-stop `communicate`.
  If the child is not reaped or its pipes do not finish
  within those bounds, cleanup is reported incomplete and
  the result is nonzero FAIL; the runner does not wait
  unboundedly for a killed child.
  This is not an operating-system sandbox and not a
  whole-process-tree guarantee: after a hard kill, a
  browser grandchild may need manual cleanup, especially
  on Windows, where only the direct child is terminated.
  On ordinary errors the suite closes its own browser in
  its `finally` handling.
- The child environment is rebuilt from an allowlist
  (`PATH`, the actual `USERPROFILE`/`HOME`, and basic
  system runtime variables), so API keys and proxy settings
  are not inherited and cannot appear in its output.
  No `HACKATHON_IYNAA_META_API_KEY` is needed; do not set
  one for this smoke.
- A one-line summary names the served origin, the number of
  tests run, the child exit code, timeout and
  incomplete-cleanup flags, and PASS or FAIL.
  A separate stderr line reports a server thread that did
  not stop; that condition forces a nonzero return.

This smoke does not replace the root suites.
Root discovery does not recurse into `tests/browser`, and
this runner does not run the root API tests; run those
separately, exactly as in the README:
`python -B -m unittest discover -s tests -p "test_*.py" -v`
(after the TLS-fixture step described there).
No blanket all-browser-files-executed claim is made: other
suites under `tests/browser` still need their own served
fixtures.
