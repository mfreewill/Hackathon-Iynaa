"""Thin private bridge from a frozen request to one transport call.

Import is inert beyond normal module loading. Configuration is
explicit and server-only; nothing is read from the environment, files,
or any credential store, and no inference, task, process, or network
work happens at import or construction time. Each call checks its
``ProviderCallContext`` before preparation, builds exactly one payload
with the accepted ``meta_protocol.build_payload``, rechecks the same
context for the ``meta_transport.run_payload`` total budget, sends at
most once, and rechecks the context again before returning the
transport result unchanged. Every plain validation error is one fixed
value-free ``ValueError`` and every operational outcome is a fresh
fixed ``meta_protocol.MetaProtocolError``; neither echoes caller,
credential, payload, or transport-exception data.
"""
from __future__ import annotations

import math
import threading
import time

from teacher_demo import meta_protocol, meta_transport

__all__ = ["MetaProvider", "ProviderCallContext"]

_INVALID = "meta provider request is invalid"
_FIXED_OUTCOMES = {
    "error": "error",
    "unknown": "unknown",
    "invalid_schema": "invalid_schema",
    "incomplete": "incomplete",
    "refusal": "refusal",
    "empty": "empty",
    "cancelled": "cancelled",
    "timeout": "timeout",
}
_UNKNOWN = "unknown"


def _invalid():
    return ValueError(_INVALID)


def _is_positive_finite_number(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    if isinstance(value, float):
        return math.isfinite(value) and value > 0
    return False


def _is_valid_credential(value):
    if not isinstance(value, str) or not value:
        return False
    return all(33 <= ord(character) <= 126 for character in value)


def _fixed_outcome(outcome):
    if isinstance(outcome, str):
        return _FIXED_OUTCOMES.get(outcome, _UNKNOWN)
    return _UNKNOWN


class ProviderCallContext:
    """One monotonic deadline plus one cancellation event per call."""

    __slots__ = ("deadline", "cancel_event")

    def __init__(self, total_timeout_seconds, *, cancel_event=None):
        if not _is_positive_finite_number(total_timeout_seconds):
            raise _invalid()
        if cancel_event is not None and not isinstance(
                cancel_event, threading.Event):
            raise _invalid()
        try:
            deadline = time.monotonic() + total_timeout_seconds
        except OverflowError:
            raise _invalid() from None
        self.deadline = deadline
        if cancel_event is None:
            self.cancel_event = threading.Event()
        else:
            self.cancel_event = cancel_event

    def remaining(self):
        if self.cancel_event.is_set():
            raise meta_protocol.MetaProtocolError("cancelled")
        remaining = self.deadline - time.monotonic()
        if remaining > 0:
            return remaining
        if self.cancel_event.is_set():
            raise meta_protocol.MetaProtocolError("cancelled")
        raise meta_protocol.MetaProtocolError("timeout")


class MetaProvider:
    """Explicitly configured one-send provider callable."""

    __slots__ = ("_credential", "_max_output_tokens", "_timeout_seconds")

    def __init__(self, *, credential, max_output_tokens, timeout_seconds):
        if not _is_valid_credential(credential):
            raise _invalid()
        if isinstance(max_output_tokens, bool):
            raise _invalid()
        if not isinstance(max_output_tokens, int):
            raise _invalid()
        if max_output_tokens < 16:
            raise _invalid()
        if not _is_positive_finite_number(timeout_seconds):
            raise _invalid()
        self._credential = credential
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds

    def __repr__(self):
        return (
            f"MetaProvider(max_output_tokens={self._max_output_tokens!r}, "
            f"timeout_seconds={self._timeout_seconds!r})"
        )

    def __call__(self, request, *, context):
        if not isinstance(context, ProviderCallContext):
            raise _invalid()
        context.remaining()
        payload = None
        build_failed = False
        try:
            payload = meta_protocol.build_payload(
                request, max_output_tokens=self._max_output_tokens)
        except Exception:
            build_failed = True
        if build_failed:
            raise _invalid()
        context.remaining()
        total_timeout_seconds = context.remaining()
        result = None
        failure_outcome = None
        failure_diagnostic = None
        try:
            result = meta_transport.run_payload(
                payload,
                credential=self._credential,
                timeout_seconds=self._timeout_seconds,
                total_timeout_seconds=total_timeout_seconds,
                cancel_event=context.cancel_event,
            )
        except meta_protocol.MetaProtocolError as error:
            failure_outcome = _fixed_outcome(
                getattr(error, "outcome", None)
            )
            if failure_outcome == "incomplete":
                try:
                    candidate = vars(error).get("diagnostic")
                except Exception:
                    candidate = None
                failure_diagnostic = (
                    meta_protocol._validated_diagnostic(
                        candidate, credential=self._credential
                    )
                )
        except Exception:
            failure_outcome = _UNKNOWN
        if failure_outcome is not None:
            error = meta_protocol.MetaProtocolError(failure_outcome)
            if failure_diagnostic is not None:
                error.diagnostic = failure_diagnostic
            raise error
        context.remaining()
        return result
