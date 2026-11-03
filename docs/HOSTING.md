# Optional hosted teacher workspace

The hosted launcher is optional.
Local use through `launch.py` remains the default.
Hosted mode serves the same teacher-only guided flow for one
exact public hostname:
prepare one full Arabic page reference with no model request,
inspect the exact page, explicitly confirm analysis, review the
source in its active step, save or discard edits explicitly,
review Bloom drafts with save or discard before same-question
approval, and explicitly open the approved-only preview.

Status (2026-11-03): the guided flow is publicly deployed at
[hackathon-iynaa.site](https://hackathon-iynaa.site) and has been
read back there.
The new art-directed visual revision of that flow — original
inline vector artwork coded by Meta (not image-model output),
purposeful CSS with reduced-motion support, a true Arabic file
selector, and warning / source-flag summaries with exact full
details behind disclosures — is verified locally only and is
pending public cutover.
A read-back on 2026-11-02 covered the build then live; each
deployed version requires its own version-specific read-back
and verification.
This document accompanies the source; it is not an always-current
deployment-status statement.
A ready domain or certificate does not by itself prove a live
Meta flow.
Command examples below use the placeholder `demo.example.com`.

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
