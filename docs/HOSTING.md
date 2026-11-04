# Optional hosted teacher workspace — Iynaa Lesson Studio

The hosted launcher is optional.
Local use through `launch.py` remains the default.
Hosted mode serves the same teacher-only Studio flow for
one exact public hostname: create and configure one full
Arabic page (file selection first by default, with an
explicit optional no-file grade and goal path), prepare a
page reference with no model request, inspect the exact
page, explicitly confirm analysis, compare original and
reviewed source, generate from the reviewed included
subset, review Bloom drafts one at a time in the focused
question navigator with save or discard before approval,
and explicitly open the approved-only teacher lesson
preview.
Same keyed drafts are retained through navigation, and
approval or preview is never automatic.
Evidence is the full-page original, not tight highlighted
localization.
The generation summary remains readable, with full slot
outcomes and raw technical metadata on demand.

Studio is a new composition of that flow, not a mere
restyling and not a clone of a full existing Iynaa
platform.
The public API, domain, transport, and approval rules are
unchanged.

Status: this released Studio source was deployed on
2026-11-03 at
[hackathon-iynaa.site](https://hackathon-iynaa.site).
Exact source, the service entrypoint, served assets,
secure session behavior, and the retained usage policy
were verified on deployment.
On 2026-11-04, five served assets still matched the
checkout.
This records the observed state, not indefinite
availability, and this documentation correction changes
no application bytes.
A read-back on 2026-11-02 covered the build then live
and remains historical; each deployed version requires
its own version-specific read-back and verification.
This document accompanies the source; it is not an
always-current deployment-status statement.
A ready domain or certificate does not by itself prove a
live Meta flow.
Command examples below use the placeholder
`demo.example.com`.

## Bundled assets and content security

This version adds exactly two bundled same-origin assets
to the fixed client surface: `/assets/studio.js` and
`/assets/cairo.ttf`, alongside the setup shell and its
existing stylesheet and scripts.
No new package, runtime service, or CDN is needed.
This replaces any earlier statement that no new runtime
asset was added.

Cairo is a publicly sourced third-party font licensed
under the SIL Open Font License 1.1 (SIL OFL 1.1).
It is not AI-generated and not Meta-generated.
See `THIRD_PARTY_NOTICES.md` and
`src/teacher_demo/static/fonts/OFL.txt`.

The content-security policy allows `font-src 'self'` only
with `default-src 'none'`, no external scripts, styles,
or fonts, no eval or inline relaxation, and no generic
static-path exposure.
Client files are served only through the fixed route
mapping.

Meta authored the original product, application code,
tests, and original inline vector artwork in code (not
image-model output).
Independent review and execution were separate and are
not all attributed to Meta.

## Network shape

- The application always binds to `127.0.0.1` with one worker.
- External HTTPS is terminated by a reverse proxy in front of
  that loopback port.
  Do not expose the application port directly.
- The allowed `Host` and `Origin` are derived exactly from
  `--hostname`.
  Session cookies are always `Secure`.
- Forwarded headers are not trusted.
- `--port` names only the loopback port behind the proxy.
  It is not part of the public `Host` or `Origin`.
- Supply the exact lowercase public DNS hostname.
  An IP address is refused as a hostname.

## Disconnected hosted default

Without `--meta`, hosted mode is disconnected.
It makes no model request and uses no key, even if the key
variable happens to be present.

```text
python -B hosted_launch.py --hostname demo.example.com --port 8000
```

## Explicit hosted Meta mode

Meta mode is never implicit.
All of the following must already be in place before startup:

- An authorized application key is present as
  `HACKATHON_IYNAA_META_API_KEY` in the server process
  environment.
  Keep the value outside the repository and out of browser code,
  screenshots, issues, and logs.
  This document names the variable only.
- A usage store already exists.
  An operator creates it once for a new path through the Python
  API `hosted_usage.initialize_usage(path, UsagePolicy(...))`.
  There is no separate initialization command.
  The launcher never creates, initializes, or resets the store.
- A policy file holds one JSON object with exactly four fields:
  `max_attempts`, `max_output_tokens`, `window_end`, and
  `minimum_spacing_seconds`.
  The policy values must match the values fixed in the store.
  `window_end` is a Unix epoch time in seconds.
  The other limits bound the attempt count, the requested output
  for one call, and the minimum spacing between attempt starts.

Start explicit Meta mode with:

```text
python -B hosted_launch.py --hostname demo.example.com --port 8000 --meta --usage-store <path> --policy-file <path> --timeout-seconds 600 --session-idle-seconds 1800
```

Startup validates the configuration, the store schema, and the
matching policy before serving.
A missing, corrupt, or mismatched store refuses startup.
Starting the process makes no model request.
Here `--timeout-seconds` bounds one Meta request.

## Model stages and output budget

Both model stages use `muse-spark-1.3`.
Source reading runs at reasoning effort `max`.
Question generation runs at reasoning effort `high`.
The effort follows the fixed request format for each stage.
It is not a caller or launcher setting.

`max_output_tokens` is one shared ceiling for a single call.
Reasoning tokens and visible output tokens both count
against it.
They are not separate budgets.
The operator-selected value is 131072.
A ceiling value alone does not guarantee a completed
response.

These settings state configuration only.
Any effect on speed or quality requires measurement.
No speed or quality improvement is claimed here.

## Finite usage ledger

The store is a local SQLite file.
It holds metadata only:
the format version, the fixed policy, the spent attempt count,
the last attempt start, and whether the window has closed.
It holds no credential, page, reviewed text, draft, or decision.

Each permitted model attempt is counted before the provider
call.
The attempt limit, closing time, and spacing are values the
operator selected for this run.
They are not an event judging date.
This code imposes no total-token cap and no dollar cap.
`max_output_tokens` bounds only the requested output for one
call.
Nothing here deletes a virtual machine automatically.

Do not reset, refund, delete, or reinitialize a live store to
retry past its limits.
No quota, uptime, or availability guarantee is made here.

Four refusal states stay distinct:

- the finite attempt limit has been reached;
- the usage window has closed;
- attempts are spaced too closely for now;
- usage accounting cannot be verified now.

A refusal creates no new model content.
Ordinary review of work already in memory remains usable.
That in-memory work can still expire independently through
session inactivity, refresh, browser close, or restart.
The ledger is not a backup of workspace content.
No durable content or backup promise is made.

## Session idle limit

`--session-idle-seconds` configures how long the server keeps a
hosted session without HTTP activity.
The default shown above is 1800 seconds.
See the session confirmation note in
[README.md](../README.md) for what expiry means in the browser.
Local mode has no hosted idle lease.
