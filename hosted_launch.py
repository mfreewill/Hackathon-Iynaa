"""Hosted entry point for the teacher workspace.

Serves the existing app on IPv4 loopback in one process,
for one exact public hostname supplied by the operator.
The allowed Host and Origin are derived only from that
hostname, never from forwarded headers, and session
cookies are always secure. Import is inert: no environment
read, provider construction, model request, or server
start happens until the public functions below are called.

The default mode is disconnected (``provider=None``), even
when a credential variable happens to exist. Only an
explicit Meta request reads ``HACKATHON_IYNAA_META_API_KEY``
from this server process's own environment, opens the
existing usage ledger for an explicitly supplied policy,
constructs the existing ``MetaProvider``, and injects only
its budgeted wrapper. That is configuration wiring, not
inference: construction and startup make no model request.
The ledger is opened as supplied; nothing here creates,
initializes, or resets it.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import uvicorn

from teacher_demo.hosted_usage import (
    BudgetedProvider,
    UsageLedger,
    UsagePolicy,
)
from teacher_demo.http_app import create_app
from teacher_demo.meta_provider import MetaProvider

_ENV_NAME = "HACKATHON_IYNAA_META_API_KEY"
_FIXED_ERROR = "invalid hosted launch configuration"
_BIND_HOST = "127.0.0.1"
_MAX_POLICY_BYTES = 16384
_POLICY_FIELDS = frozenset((
    "max_attempts",
    "max_output_tokens",
    "window_end",
    "minimum_spacing_seconds",
))


def _is_valid_credential(value):
    """Mirror the existing provider's credential rule exactly."""
    if not isinstance(value, str) or not value:
        return False
    return all(33 <= ord(character) <= 126 for character in value)


def _is_positive_finite_number(value):
    """Mirror the existing provider's timeout rule exactly."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value > 0
    if isinstance(value, float):
        return math.isfinite(value) and value > 0
    return False


def _is_valid_hostname(value):
    """Return True only for an exact lowercase DNS hostname.

    The value is checked as supplied and never normalized:
    ASCII lowercase letters, digits, hyphens, and dots only;
    at least two labels of 1 to 63 characters; at most 253
    characters in total; no label empty or starting or
    ending with a hyphen. A final label of digits only is
    refused, so an IP address is never accepted as a name.
    """
    if not isinstance(value, str) or not value:
        return False
    if len(value) > 253:
        return False
    labels = value.split(".")
    if len(labels) < 2:
        return False
    for label in labels:
        if not 1 <= len(label) <= 63:
            return False
        if label.startswith("-") or label.endswith("-"):
            return False
        for character in label:
            if not ("a" <= character <= "z"
                    or "0" <= character <= "9"
                    or character == "-"):
                return False
    if all("0" <= character <= "9" for character in labels[-1]):
        return False
    return True


def _is_valid_usage_path(value):
    """Return True only for a nonempty filesystem path."""
    if isinstance(value, str):
        return value != ""
    if isinstance(value, os.PathLike):
        resolved = os.fspath(value)
        return isinstance(resolved, str) and resolved != ""
    return False


def create_hosted_app(
    *, hostname, port=8000, meta=False, usage_path=None,
    policy=None, timeout_seconds=600.0, session_idle_seconds=1800.0,
):
    """Create the existing app for one exact public hostname.

    Every value is validated before any ledger, provider,
    or app construction. Any invalid configuration, and any
    failure to open the ledger, raises one fixed value-free
    ``ValueError``. It is raised plainly outside any
    exception handler so it carries no cause or context.
    The public port is not part of the derived Host or
    Origin; it only names the local port in ``main``.
    """
    if not _is_valid_hostname(hostname):
        raise ValueError(_FIXED_ERROR)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError(_FIXED_ERROR)
    if type(meta) is not bool:
        raise ValueError(_FIXED_ERROR)
    if not _is_positive_finite_number(session_idle_seconds):
        raise ValueError(_FIXED_ERROR)
    provider = None
    if meta:
        if not isinstance(policy, UsagePolicy):
            raise ValueError(_FIXED_ERROR)
        if not _is_valid_usage_path(usage_path):
            raise ValueError(_FIXED_ERROR)
        if not _is_positive_finite_number(timeout_seconds):
            raise ValueError(_FIXED_ERROR)
        credential = os.environ.get(_ENV_NAME)
        if not _is_valid_credential(credential):
            raise ValueError(_FIXED_ERROR)
        ledger = None
        ledger_failed = False
        try:
            ledger = UsageLedger(usage_path, policy)
            ledger.snapshot()
        except Exception:
            ledger_failed = True
        if ledger_failed:
            raise ValueError(_FIXED_ERROR)
        delegate = MetaProvider(
            credential=credential,
            max_output_tokens=policy.max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        provider = BudgetedProvider(delegate, ledger)
    return create_app(
        allowed_hosts=(hostname,),
        allowed_origin=f"https://{hostname}",
        provider=provider,
        secure_cookie=True,
        session_idle_seconds=session_idle_seconds,
    )


class _ArgumentRefusal(Exception):
    """Internal value-free signal for a refused command line."""


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that never prints supplied argument text."""

    def error(self, message):
        raise _ArgumentRefusal from None


def _object_from_pairs(pairs):
    """Build one JSON object, refusing any duplicate key."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(_FIXED_ERROR)
        result[key] = value
    return result


def _reject_json_constant(_value):
    """Refuse the non-finite constants JSON would otherwise allow."""
    raise ValueError(_FIXED_ERROR)


def _load_policy(policy_file):
    """Load an explicit policy file as a ``UsagePolicy``.

    At most a bounded number of bytes is read. The document
    must be one object holding exactly the four policy
    fields, with no duplicate keys and no non-numeric,
    boolean, or non-finite values. Any refusal raises the one fixed
    value-free ``ValueError``, outside any exception
    handler. There is no default policy.
    """
    if not _is_valid_usage_path(policy_file):
        raise ValueError(_FIXED_ERROR)
    raw = None
    read_failed = False
    try:
        with open(policy_file, "rb") as handle:
            raw = handle.read(_MAX_POLICY_BYTES + 1)
    except Exception:
        read_failed = True
    if read_failed or raw is None or len(raw) > _MAX_POLICY_BYTES:
        raise ValueError(_FIXED_ERROR)
    data = None
    parse_failed = False
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_from_pairs,
            parse_constant=_reject_json_constant,
        )
    except Exception:
        parse_failed = True
    if parse_failed or not isinstance(data, dict):
        raise ValueError(_FIXED_ERROR)
    if set(data) != _POLICY_FIELDS:
        raise ValueError(_FIXED_ERROR)
    for value in data.values():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(_FIXED_ERROR)
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(_FIXED_ERROR)
    policy = None
    build_failed = False
    try:
        policy = UsagePolicy(
            max_attempts=data["max_attempts"],
            max_output_tokens=data["max_output_tokens"],
            window_end=data["window_end"],
            minimum_spacing_seconds=data["minimum_spacing_seconds"],
        )
    except Exception:
        build_failed = True
    if build_failed:
        raise ValueError(_FIXED_ERROR)
    return policy


def _build_parser():
    parser = _Parser(
        prog="hosted_launch.py",
        allow_abbrev=False,
        description=(
            "Run the teacher workspace for one exact public "
            "hostname. Disconnected unless Meta mode is explicit."),
    )
    parser.add_argument(
        "--hostname", required=True,
        help=(
            "exact lowercase public DNS hostname, "
            "for example demo.example.com"))
    parser.add_argument(
        "--port", type=int, default=8000,
        help="local TCP port (default: 8000)")
    parser.add_argument(
        "--meta", action="store_true",
        help=(
            "enable explicit Meta mode using the "
            "HACKATHON_IYNAA_META_API_KEY environment variable; "
            "requires a usage store and a policy file"))
    parser.add_argument(
        "--usage-store", default=None,
        help="existing usage store file, for Meta mode")
    parser.add_argument(
        "--policy-file", default=None,
        help="usage policy JSON file, for Meta mode")
    parser.add_argument(
        "--timeout-seconds", type=float, default=600.0,
        help=(
            "Meta timeout in seconds, a positive finite "
            "number (default: 600)"))
    parser.add_argument(
        "--session-idle-seconds", type=float, default=1800.0,
        help=(
            "session idle limit in seconds, a positive "
            "finite number (default: 1800)"))
    return parser


def main(argv=None):
    """Run the hosted server; return 0, or 2 on refusal.

    Refusals print the one fixed configuration message to
    stderr and construct nothing. The policy file is read
    only in Meta mode. Normal ``--help`` keeps argparse's
    successful exit.
    """
    try:
        args = _build_parser().parse_args(argv)
    except _ArgumentRefusal:
        print(_FIXED_ERROR, file=sys.stderr)
        return 2
    try:
        if args.meta:
            policy = _load_policy(args.policy_file)
            app = create_hosted_app(
                hostname=args.hostname,
                port=args.port,
                meta=True,
                usage_path=args.usage_store,
                policy=policy,
                timeout_seconds=args.timeout_seconds,
                session_idle_seconds=args.session_idle_seconds,
            )
        else:
            app = create_hosted_app(
                hostname=args.hostname,
                port=args.port,
                meta=False,
                session_idle_seconds=args.session_idle_seconds,
            )
    except ValueError:
        print(_FIXED_ERROR, file=sys.stderr)
        return 2
    uvicorn.run(
        app, host=_BIND_HOST, port=args.port, workers=1,
        reload=False, access_log=False, proxy_headers=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
