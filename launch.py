"""Clean local entry point for the teacher workspace.

Runs the existing app on IPv4 loopback in one process.
Import is inert: no environment read, provider construction,
model request, or server start happens until the public
functions below are called.

The default mode is disconnected (``provider=None``), even
when a credential variable happens to exist. Only an
explicit Meta request reads ``HACKATHON_IYNAA_META_API_KEY``
from this server process's own environment and constructs
the existing ``MetaProvider`` from explicitly supplied
settings. That is configuration wiring, not inference:
construction and startup make no model request.
"""
from __future__ import annotations

import argparse
import math
import os
import pathlib
import sys

_SRC = pathlib.Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import uvicorn

from teacher_demo.http_app import create_app
from teacher_demo.meta_provider import MetaProvider

_ENV_NAME = "HACKATHON_IYNAA_META_API_KEY"
_FIXED_ERROR = "invalid local launch configuration"
_HOST = "127.0.0.1"


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


def create_local_app(
    *, port=8000, meta=False, max_output_tokens=None,
    timeout_seconds=None,
):
    """Create the existing app for exact loopback Host/Origin.

    Every value is validated before any provider or app
    construction. Any invalid configuration raises one fixed
    value-free ``ValueError``, raised plainly outside any
    exception handler so it carries no cause or context.
    """
    if isinstance(port, bool) or not isinstance(port, int):
        raise ValueError(_FIXED_ERROR)
    if not 1 <= port <= 65535:
        raise ValueError(_FIXED_ERROR)
    if not isinstance(meta, bool):
        raise ValueError(_FIXED_ERROR)
    provider = None
    if meta:
        credential = os.environ.get(_ENV_NAME)
        if not _is_valid_credential(credential):
            raise ValueError(_FIXED_ERROR)
        if isinstance(max_output_tokens, bool):
            raise ValueError(_FIXED_ERROR)
        if not isinstance(max_output_tokens, int):
            raise ValueError(_FIXED_ERROR)
        if max_output_tokens < 16:
            raise ValueError(_FIXED_ERROR)
        if not _is_positive_finite_number(timeout_seconds):
            raise ValueError(_FIXED_ERROR)
        provider = MetaProvider(
            credential=credential,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
    return create_app(
        allowed_hosts=(f"{_HOST}:{port}",),
        allowed_origin=f"http://{_HOST}:{port}",
        provider=provider,
    )


class _ArgumentRefusal(Exception):
    """Internal value-free signal for a refused command line."""


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that never prints supplied argument text."""

    def error(self, message):
        raise _ArgumentRefusal from None


def _build_parser():
    parser = _Parser(
        prog="launch.py",
        allow_abbrev=False,
        description=(
            "Run the teacher workspace locally on 127.0.0.1. "
            "Disconnected unless Meta mode is explicit."),
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="local TCP port (default: 8000)")
    parser.add_argument(
        "--meta", action="store_true",
        help=(
            "enable explicit Meta mode using the "
            "HACKATHON_IYNAA_META_API_KEY environment variable; "
            "requires the two options below"))
    parser.add_argument(
        "--max-output-tokens", type=int, default=None,
        help="maximum Meta output tokens, an integer of at least 16")
    parser.add_argument(
        "--timeout-seconds", type=float, default=None,
        help="Meta timeout in seconds, a positive finite number")
    return parser


def main(argv=None):
    """Run the local server; return 0, or 2 on refusal.

    Refusals print the one fixed configuration message to
    stderr and construct nothing. Normal ``--help`` keeps
    argparse's successful exit.
    """
    try:
        args = _build_parser().parse_args(argv)
    except _ArgumentRefusal:
        print(_FIXED_ERROR, file=sys.stderr)
        return 2
    try:
        app = create_local_app(
            port=args.port,
            meta=args.meta,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError:
        print(_FIXED_ERROR, file=sys.stderr)
        return 2
    uvicorn.run(
        app, host=_HOST, port=args.port, workers=1,
        reload=False, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
