"""Retained-state accounting for the teacher HTTP publication path.

Pure and socket-free. Every cost here is derived with the accepted
``http_teacher_cost.logical_payload_bytes`` over retained payload
graphs; this module adds no second estimator. Synchronization and
executor objects are never part of a measured graph: the session
graph is composed explicitly from payload fields only (CSRF token,
pending setup, decoded page, workspace, stored provider envelopes,
and raw-retention status). Bookkeeping integers that merely record
a derived cost or reserve are accounted through their sums in
``accounted_total``, never measured as payload themselves.

A candidate's derived cost is computed from the candidate graph at
prepare time and then retained with the published payload by the
caller, so the stored cost and the stored payload are always
constructed in one operation. Logical size is not Python heap size.
"""
from __future__ import annotations

import copy

from .http_teacher_cost import logical_payload_bytes

__all__ = [
    "TERMINAL_SLACK_BYTES",
    "accounted_total",
    "admission_allows",
    "callable_payload_cost",
    "envelope_cost",
    "is_noop_publication",
    "measure_terminal_reserve",
    "prune_terminal_reserves",
    "retain_terminal_reserves",
    "session_graph_cost",
    "working_request_ids",
]

# Fixed headroom added to the mechanically measured finish delta so
# a working request can always reach a truthful no-content terminal
# outcome (finish bookkeeping and retention-status growth included).
# It deliberately does not reserve a maximum successful response.
TERMINAL_SLACK_BYTES = 8192

# Longest terminal outcome the HTTP lifecycle writes; the reserve
# probe finishes with it so the measured delta bounds real finishes.
_PROBE_OUTCOME = "invalid_schema"


def session_graph_cost(csrf_token, pending, decoded, workspace,
                       provider_results, raw_status):
    """Logical cost of one session's retained payload graph.

    Raises ``ValueError`` (value-free, from the cost helper) when any
    retained payload is unsupported; callers must refuse, never
    treat an unmeasurable graph as zero.
    """
    return logical_payload_bytes(
        (csrf_token, pending, decoded, workspace,
         provider_results, raw_status))


def envelope_cost(envelope):
    """Logical cost of one detached provider envelope as stored."""
    return logical_payload_bytes(envelope)


def callable_payload_cost(payload):
    """Logical cost of the payload graph handed to the provider.

    Accepts the plain request mapping or its deep-frozen form; both
    have the same retained shape and therefore the same cost.
    """
    return logical_payload_bytes(payload)


def working_request_ids(snapshot):
    """Request ids still working in an actual domain snapshot."""
    return tuple(
        item.request_id for item in snapshot.requests
        if item.state == "working")


def prune_terminal_reserves(reserves, working_ids):
    """Copy of ``reserves`` restricted to still-working requests."""
    keep = set(working_ids)
    return {request_id: amount
            for request_id, amount in reserves.items()
            if request_id in keep}


def retain_terminal_reserves(reserves, snapshot, raw_status):
    """Reserves still needed until each retained outcome publishes.

    A working request keeps its full measured reserve (finish
    delta plus slack). Once the domain request is closed
    (terminal or superseded) but its retention status has not
    been published yet, the finish-delta portion has been
    consumed by the closing publication's graph growth, so only
    the slack remains, ring-fenced for the later retention-status
    publication. A request whose retention status is already
    stored, or that no longer exists in the snapshot, releases
    its reserve. The input mapping is never mutated.
    """
    if snapshot is None:
        return {}
    states = {item.request_id: item.state
              for item in snapshot.requests}
    retained = {}
    for request_id, amount in reserves.items():
        state = states.get(request_id)
        if state is None:
            continue
        if state == "working":
            retained[request_id] = amount
        elif request_id not in raw_status:
            retained[request_id] = min(amount, TERMINAL_SLACK_BYTES)
    return retained


def is_noop_publication(*, before_snapshot, after_snapshot,
                        before_pending, after_pending,
                        before_graph_cost, after_graph_cost,
                        before_reserves, after_reserves):
    """True when a candidate changes no retained semantic state.

    Every dimension must match: the actual domain snapshot
    (revisions, content, decisions, request lifecycle), the
    pending setup values, the full retained graph cost (which
    also covers snapshot-invisible bound/history growth), and
    the protected terminal reserves. Equality here judges one
    candidate against the state it would replace; it never
    compares across an intervening change, so a genuine
    change-away-and-back (two differing publications) is not
    hidden.
    """
    return (before_snapshot == after_snapshot
            and before_pending == after_pending
            and before_graph_cost == after_graph_cost
            and before_reserves == after_reserves)


def measure_terminal_reserve(workspace, request_id, kind):
    """Reserve that guarantees a no-content terminal finish fits.

    Mechanically derived: finish the request on a detached copy
    with the longest outcome and measure the retained-cost delta,
    floored at zero, plus fixed slack. The authoritative workspace
    is never mutated. A probe that cannot finish (already closed)
    still reserves the slack.
    """
    base = logical_payload_bytes(workspace)
    probe = copy.deepcopy(workspace)
    try:
        if kind == "source":
            probe.finish_source_request(request_id, _PROBE_OUTCOME)
        else:
            probe.finish_question_request(request_id, _PROBE_OUTCOME)
    except Exception:
        return TERMINAL_SLACK_BYTES
    delta = logical_payload_bytes(probe) - base
    return max(0, delta) + TERMINAL_SLACK_BYTES


def admission_allows(candidate_graph_cost, worker_sum, reserve_sum,
                     others_accounted, per_session_cap, total_cap):
    """True when a candidate fits per-session and global caps."""
    mine = candidate_graph_cost + worker_sum + reserve_sum
    return mine <= per_session_cap and others_accounted + mine <= total_cap


def accounted_total(graph_cost, worker_reservations, terminal_reserves):
    """One session's accountable retained total from stored parts."""
    return (graph_cost
            + sum(worker_reservations.values())
            + sum(terminal_reserves.values()))
