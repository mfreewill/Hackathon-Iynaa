# Teacher HTTP flow

Local, same-origin, in-memory HTTP boundary around the public
`TeacherWorkspace` domain and the real page decoder. The provider is
an internally injected synchronous callable; it is not selectable
over HTTP, and the default app has no provider (analysis and
generation return `503 provider_unavailable`). Tests inject
synthetic providers explicitly labelled not Meta. No browser model,
key, account, credential, or external-provider authority exists.

## Routes

- `GET /`, `GET /assets/workspace.css`, `GET /assets/workspace.js`,
  `GET /assets/review.js`, `GET /assets/review-render.js`
  — the fixed same-origin teacher browser client (see
  "Browser client" below). These are the only client routes: an
  explicit route-to-packaged-file mapping in `http_client.py`, with
  no filename/path parameter, directory browsing, or generic
  static mount. Responses carry the correct HTML/CSS/JS types,
  `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and
  a local-only Content-Security-Policy (same-origin script, style,
  images and API calls; no inline script, external resource, or
  `unsafe-eval`).
- `POST /api/sessions` — create a session; returns the CSRF token and
  sets the `teacher_session` cookie (`HttpOnly`, `SameSite=strict`).
- `PUT /api/session/settings` — exact `{grade, objective, rights_ref}`.
- `POST /api/session/start?page_index=N` — `application/octet-stream`
  page bytes; real decode, then one retained publication.
- `GET /api/session/page`, `GET /api/session/page/original`,
  `GET /api/session/page/preview.png` — page metadata and downloads.
- `POST /api/session/source-analysis` — begin a source provider
  request; `201 {request_id, snapshot, raw_retention}` on success.
- `POST /api/session/questions/generate` — begin a question provider
  request against reviewed included regions only.
- `GET /api/session/snapshot` — the domain snapshot projection.
- `GET /api/session/preview` — `{items}` from snapshot preview items.
- `GET /api/session/requests/{request_id}/raw` — exact stored
  provider envelope when retained, otherwise `404`.
- `PUT /api/session/regions/{region_id}` — one combined
  `{inclusion, current_text, current_label}` review command.
- `POST /api/session/questions/{question_id}/approve` — explicit
  approval with `{}`; never automatic.
- `PATCH /api/session/questions/{question_id}` — edit current
  question fields with any subset (including the empty no-op) of
  the domain edit keys `question`, `answer_guidance`,
  `cognitive_explanation`, `grade`, `objective`, `bloom_level`,
  `hypothetical_assumptions`, `evaluate_criteria`, `create_rubric`;
  `200 {snapshot}`. Every unrecognized key is refused (`400`),
  never filtered away; legitimate values are forwarded exactly to
  `edit_question`, so originals, source anchors/supports, model,
  author, and approval fields stay inaccessible and no text is
  normalized. Criteria/rubric facts keep the domain's object or
  pair encoding. An unknown question with an otherwise valid body
  is `404 {"detail": "question not found"}`; malformed or
  forbidden values are `400 {"detail": "invalid request"}` and
  publish nothing.
- `POST /api/session/questions/{question_id}/reject` — reject with
  exactly `{}` (`200 {snapshot}`); unknown question as above.
- `POST /api/session/regions/{region_id}/move-up` and
  `.../move-down` — pure display movement with exactly `{}`
  (`200 {snapshot}`, with no moved/boolean field); boundary moves
  return the unchanged snapshot and no semantic precedence is
  edited. An unknown region with an otherwise valid body is
  `404 {"detail": "region not found"}`.
- `POST /api/session/requests/{request_id}/cancel` — close one
  working request with `{}`; a running callable still runs.

Every route requires an allowed `Host`. Session creation requires
an allowed `Host` and `Origin`; a mutation on an existing session
additionally requires that session's CSRF token.
`Cache-Control: no-store` is explicit on the actual prepared JSON
responses (`prepared_json`) and on page download responses — not
on every plain-dict metadata response (which FastAPI renders
itself) or on ordinary refusal responses. Path identifiers from
clients/tests should be URL-quoted with `quote(..., safe='')`.

## Browser client

The client is five packaged files (`static/index.html`,
`static/workspace.css`, `static/workspace.js`, `static/review.js`,
`static/review-render.js`; native ES modules and ordinary CSS,
system Arabic fonts, no framework, bundler, CDN or external
request) registered on the same `create_app` by
`http_client.register_client`. There is no separate client
server or launcher in this repository: whoever launches
`create_app` locally — with `allowed_hosts`/`allowed_origin` set
to the actual served loopback origin — serves the page and the
API from that one origin, and the browser is opened at `/` on it.
The provider default is unchanged (`None`, disconnected); the
browser cannot select a provider or hold a credential, and the
CSRF token is kept only in page memory.

The implemented Arabic RTL flow is setup → source review →
questions → teacher preview. `workspace.js` alone owns the
session/CSRF token, API transport, the serialized mutation owner,
busy notification, source adoption and sticky uncertainty;
`review.js` coordinates acknowledged snapshots, keyed local
drafts and `reviewCurrency`, the single review-currentness
authority; `review-render.js` only builds and updates keyed DOM
nodes and never fetches. The page keeps one stable setup form
(page file, grade, learning objective, one-based page number) and
a Source pane: typing or selecting a file commits nothing, and
the one primary action (`بدء تحليل الصفحة`) creates the visit's
single session if needed, then sends the exact settings `PUT`,
the octet-stream Start for the chosen page, and exactly one
source-analysis `POST` — with repeat submission disabled while
pending and no automatic retry. After a successful Start it
adopts the server metadata and shows the server-owned preview,
exact original access, the selected page number, and collapsed
source/render identities, captioned as a whole-page reference
with no mask or localization claim. An acknowledged analysis
snapshot is then reviewed in place: region text/label/inclusion
edits and display moves, question generation against reviewed
included regions, question edits, and explicit approve/reject
decisions all travel through the one serialized owner, and the
teacher preview is derived solely from the authoritative
snapshot's current `preview_items` joined to the same snapshot
by `question_id` — never generated locally, never from unsaved
drafts, and editing never approves. With the default
disconnected provider, analysis and generation attempts report
`503 provider_unavailable` in MSA and present no sections,
questions or approval; live model integration is not part of
this client and is never simulated.

Per-request provenance is read-only lineage, separate from
approval, currentness and the mutation queue. Each region and
question shows its own actual `request_id` and the model name
from that same request's retained raw envelope, read through the
existing `workspace.getJson` transport from
`GET /api/session/requests/{request_id}/raw`; an ordinary
approved preview joins its snapshot question by `question_id`
for the same two values. A model name is accepted only when the
raw payload's `request_id` matches exactly, its `kind` is the
expected `source` or `questions`, retention is `retained`, and
`provider_result.model_name` is a nonblank string — never the
source summary, a global latest model, or an adjacent request.
One request-keyed cache in `review.js` issues at most one read
per request per source generation, resets only on an
acknowledged new Start, and discards late responses bound to an
old source; a completion paints only its own provenance text and
never adopts a snapshot, changes a decision, clears a lock,
rebuilds a dirty editor or overwrites the status line. An
unavailable or failed raw read shows `غير متاح` for that request
only: it is metadata unavailability, not a mutation failure, a
generation failure or unknown source authority, and it never
replaces known metadata of another request or removes a current
approved preview. Question cards keep `generation_grade` /
`generation_objective` visible beside the current values as exact
lineage after teacher edits, with every arbitrary string written
via `textContent`/values and request/model identifiers
direction-isolated. A region's linked-question control keeps the
question wording and shows the server's mapped status only while
`reviewCurrency` is current and no sticky uncertainty applies;
order relationships expose whole-page evidence buttons for each
actual endpoint region with no crop, highlight or
semantic-precedence claim (no semantic-precedence writer exists
on the current HTTP surface, so the order-bearing preview branch
remains controlled-component coverage only); and an acknowledged
display move announces `موضع المقطع` with the one-based position
and total in Arabic-Indic digits on the existing status line,
while a boundary no-op states the position without claiming a
move. Keyed region/question/preview nodes, dirty sibling drafts,
open editors/details and focus stay stable across metadata
reads, status updates and pane switching.

Every Start creates a new workspace, so the discard warning
beside the same primary action follows the destructive operation,
not file metadata: it is hidden only before this visit's first
committed source and then stays visible before every subsequent
Start — including an unchanged file and page, a page-number-only
change, and a same-metadata replacement — and says explicitly
that even the same file and page reset. Selecting a file still
sends nothing. A Start that the server definitely refused before
committing — the known validation/auth/capacity/busy responses
(settings 400/401/403/413/415; Start 400/401/403/409/413/415/429),
such as the healthy invalid-file replacement with identical
committed settings — preserves the prior source, metadata,
preview and snapshot, keeps the visit usable, and makes no second
analysis attempt. Changed settings followed by a failed Start are
not one atomic transaction across the two calls. One further
concrete response is definite for the same reason: session
creation's `503 {"detail": "session limit reached"}`, which the
server returns before inserting any session, takes the ordinary
definite-session-failure path (no unknown-result lock, no
automatic retry, and the deliberate action stays usable). This
exception is the exact stage/status/detail triple only — any
other session `503`, the same string at another stage, a network
failure or an unusable success body remains uncertain below, and
`503` is never definite as a status in general.

If a source-affecting session/settings/Start attempt may have
been transmitted but has no trustworthy final outcome — a network
failure, an unusable expected success body, or an unexpected
server failure outside those definite refusals — the displayed
source must not be trusted. The client then fails closed for the
rest of the visit: it replaces the status with an explicit
unknown-result message (`نتيجة الطلب غير معروفة`), hides the
committed source reference, removes the preview `src` and the
original link's `href`, keeps the exact draft inputs, and locks
the primary action (the submit handler itself re-checks the lock,
so keyboard submission cannot write either). There is no retry,
new session, compensation, or GET-based reconciliation — a later
GET cannot prove an earlier delayed mutation will never commit —
and no restore UI; the bounded recovery is to stop and
intentionally refresh the browser to begin anew, accepting that
this non-durable work may be lost. An analysis failure alone
(including the default `503 provider_unavailable` and a received
provider refusal) does not make the already-acknowledged source
identity unknown and never enters this lock. Below 1024px a
Source/Work tablist exposes exactly
one pane (the other carries `hidden` and leaves focus and the
accessibility tree) while drafts, selection, scroll and per-pane
focus survive switching and returning to desktop width, where
both panes show and the switch hides. The permanent non-durable
warning stays visible; there is no saved/autosaved claim, fake
progress percentage, or create-from-blank control. Synthetic
test fixtures prove neither real Meta output nor educational
correctness: real-provider behavior, educational acceptance,
full-lifecycle behavior, complete keyboard and responsive
acceptance, and any hosting deployment remain pending and are
not claimed by this local client.

## Authority and currency

The decoded page and `TeacherWorkspace` are the authorities. The
snapshot is a faithful projection of domain state (regions,
questions, slot outcomes, requests with bound revisions, preview
items, `can_generate`). Provider results enter only through domain
admission (`admit_source_proposal` / `admit_question_proposal`);
there is no raw-proposal injection route. Generation uses only
reviewed included regions with nonblank current teacher text, and
the preview contains only currently approved questions. In the
browser, every mutation travels through the one serialized owner
in `workspace.js` and review currentness has the one
`reviewCurrency` owner in `review.js`; the per-request metadata
reads above are outside both and can neither publish domain
state nor revive a withdrawn preview.

Requests bind the source/settings revisions at begin. Valid
same-value settings, region, approval, and question-edit commands
— and boundary display moves — are semantic no-ops: identical
snapshot, graph cost, and reserves mean no
publication, so workspace identity, the publication epoch, and the
bound revisions stay current. A genuine change-away-and-back is two
real publications: revisions (and the epoch) advance even when the
final strings equal the originals, so a bound result goes stale.
Start binds setup by pending-tuple identity, so the same rule
applies while a page is decoding. Success publication rechecks the
publication epoch plus source/settings revisions immediately before
commit; string equality alone never proves currency.

## Retained cost and protected capacity

Retained is accounted in logical payload bytes
(`logical_payload_bytes` over the stored graph: CSRF token, pending
setup, decoded page, workspace, stored provider envelopes, and raw
status), never heap size, RSS, response-JSON size, or pickle size.
A session's accounted total is its stored graph cost plus
callable-bound worker reservations plus terminal reserves, admitted
under fixed per-session and global caps by the single
`publish_locked` authority in session-then-registry lock order.

Each working request holds a measured terminal reserve (mechanically
probed finish delta plus fixed slack, not a maximum success). It is
ring-fenced in every candidate's accounted total, so edits, other
sessions, and late results cannot consume the room a live request
needs to reach a truthful terminal outcome: reserve-consuming
publications are non-growing and always fit. `retain_terminal_reserves`
keeps a working request's full reserve; converts a closed request's
reserve to its slack until that request's raw-retention status has
published; and releases the reserve once the status is stored. A
successful publication therefore retires only its own reserve while
preserving live requests' reserves and older closed-pending
requests' closure room. Callable-bound bytes are charged from begin
until the callable actually returns (its future completes);
cancellation, timeout, superseding, and a new Start close or
replace domain state but never free a running worker's reservation
or provider slot early. Within the current source generation,
terminal history (request records, stored envelopes, raw
statuses) is retained and accounted, never silently erased;
repeated work therefore eventually refuses at admission (`413`,
before invocation) instead of accumulating without bound. An
explicit successful new Start is the deliberate exception: it
replaces that workspace, its stored raw envelopes, and its
history with the new page's fresh state — while a callable from
the old generation that is still running remains charged until it
actually returns.

## Atomic publication and raw retention

Every retained write is copy-on-write: the candidate receives all
domain calls, its full graph is measured, and the actual response
is rendered before commit (response-before-commit). A domain,
validation, rendering, or capacity failure before commit publishes
no candidate content. Provider processing detaches and bounds the
returned value, validates the exact envelope, admits the proposal
on a candidate, renders the success response, and only then
commits; any failure terminalizes the admitted request truthfully
(never left working, never claiming success) with the stored
snapshot reflecting exactly what published — that terminal
bookkeeping, and bounded raw when it fits, may publish even though
no candidate content does. This is a local atomicity guarantee
about retained state, not an exactly-once network-delivery claim.

Raw retention is explicit in admitted provider lifecycle responses
(it does not describe pre-admission refusals, which can occur
before any request or envelope exists):
`retained` (the exact bounded envelope is stored and readable at
the raw route), `omitted_capacity` (the envelope existed but did
not fit truthfully), or `not_available`. Raw is never truncated,
partially stored, or claimed when omitted. Sessions and all
retained state are process-memory only and non-durable: a restart
loses sessions, pages, requests, raws, and accounting.
