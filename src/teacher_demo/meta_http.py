"""Synchronous one-send HTTPS transport for one Meta payload.

This internal helper sends exactly one ``POST /v1/responses`` to
the fixed ``api.meta.ai`` origin over verified TLS and returns the
accepted ``meta_stream.collect_response`` result unchanged. It
validates and serializes an owned copy of the accepted builder
payload (with only ``stream`` changed to true) before creating
any connection, never retries, redirects, or reconnects, and
closes the acquired response and connection on every path.

The TLS context is constructed explicitly as
``ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)`` with normal
server-auth trust loading; the environment-sensitive default
context factory is never used, no environment is read or
modified, and key logging is left disabled. Endpoint, model, and
trust are not caller-configurable. Invalid caller input raises
one fixed plain ``ValueError`` before any network construction;
transport and provider outcomes keep the accepted fixed
``MetaProtocolError`` surface with no response body, header, or
exception data copied into failures. The socket timeout is an
inactivity timeout only, not a total operation deadline.
"""
import copy
import http.client
import json
import math
import ssl

from teacher_demo import meta_protocol, meta_stream

MAX_REQUEST_BYTES = 128 * 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024

__all__ = [
    "MAX_REQUEST_BYTES",
    "READ_CHUNK_BYTES",
    "send_payload",
]

_HOST = "api.meta.ai"
_PORT = 443
_PATH = "/v1/responses"
_INVALID_REQUEST = "meta http request is invalid"
_EXPECTED_FIELDS = frozenset((
    "model",
    "reasoning",
    "max_output_tokens",
    "store",
    "stream",
    "text",
    "input",
))
_ASCII_DIGITS = frozenset("0123456789")


class _BodyIncomplete(Exception):
    """Internal marker: EOF before the declared body completed."""


def _invalid_request():
    raise ValueError(_INVALID_REQUEST)


def _is_strict_text(value):
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _is_json_data(value, active):
    """True for ordinary JSON data with strict text/finite numbers."""
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return _is_strict_text(value)
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in active:
            return False
        active.add(marker)
        try:
            return all(_is_json_data(item, active) for item in value)
        finally:
            active.discard(marker)
    if isinstance(value, dict):
        marker = id(value)
        if marker in active:
            return False
        active.add(marker)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    return False
                if not _is_strict_text(key):
                    return False
                if not _is_json_data(item, active):
                    return False
            return True
        finally:
            active.discard(marker)
    return False


def _is_valid_credential(credential):
    if not isinstance(credential, str) or not credential:
        return False
    return all(33 <= ord(character) <= 126 for character in credential)


def _is_valid_timeout(timeout_seconds):
    if isinstance(timeout_seconds, bool):
        return False
    if isinstance(timeout_seconds, int):
        return timeout_seconds > 0
    if isinstance(timeout_seconds, float):
        return math.isfinite(timeout_seconds) and timeout_seconds > 0
    return False


def _prepare_body(payload, credential, timeout_seconds):
    """Validate caller input and return the owned request bytes."""
    if not _is_valid_credential(credential):
        _invalid_request()
    if not _is_valid_timeout(timeout_seconds):
        _invalid_request()
    if not isinstance(payload, dict):
        _invalid_request()
    if set(payload.keys()) != _EXPECTED_FIELDS:
        _invalid_request()
    if payload["model"] != meta_protocol.MODEL:
        _invalid_request()
    if payload["reasoning"] != {"effort": meta_protocol.EFFORT}:
        _invalid_request()
    if payload["store"] is not False:
        _invalid_request()
    if payload["stream"] is not False:
        _invalid_request()
    max_output_tokens = payload["max_output_tokens"]
    if isinstance(max_output_tokens, bool):
        _invalid_request()
    if not isinstance(max_output_tokens, int):
        _invalid_request()
    if max_output_tokens < 16:
        _invalid_request()
    try:
        if not _is_json_data(payload, set()):
            _invalid_request()
    except RecursionError:
        raise ValueError(_INVALID_REQUEST) from None
    try:
        outgoing = copy.deepcopy(payload)
        outgoing["stream"] = True
        body = json.dumps(
            outgoing,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except Exception:
        raise ValueError(_INVALID_REQUEST) from None
    if len(body) > MAX_REQUEST_BYTES:
        _invalid_request()
    return body


def _invalid_schema():
    raise meta_protocol.MetaProtocolError("invalid_schema")


def _validate_success_headers(response):
    """Enforce supported success framing; return declared length."""
    grouped = {}
    for name, value in response.getheaders():
        grouped.setdefault(name.lower(), []).append(value)

    content_types = grouped.get("content-type", [])
    if len(content_types) != 1:
        _invalid_schema()
    media_type = content_types[0].split(";", 1)[0].strip().lower()
    if media_type != "text/event-stream":
        _invalid_schema()

    encodings = grouped.get("content-encoding", [])
    if len(encodings) > 1:
        _invalid_schema()
    if encodings and encodings[0].strip(" \t").lower() != "identity":
        _invalid_schema()

    lengths = grouped.get("content-length", [])
    transfers = grouped.get("transfer-encoding", [])
    if lengths:
        if len(lengths) != 1 or transfers:
            _invalid_schema()
        digits = lengths[0].strip(" \t")
        if not digits:
            _invalid_schema()
        if any(character not in _ASCII_DIGITS for character in digits):
            _invalid_schema()
        significant = digits.lstrip("0") or "0"
        if len(significant) > len(str(meta_stream.MAX_STREAM_BYTES)):
            _invalid_schema()
        declared_length = int(significant)
        if declared_length > meta_stream.MAX_STREAM_BYTES:
            _invalid_schema()
        return declared_length
    if transfers:
        if len(transfers) != 1:
            _invalid_schema()
        if transfers[0].strip(" \t").lower() != "chunked":
            _invalid_schema()
        # The reader selected its decoding from the untrimmed
        # value during header parsing; make the sole validated
        # chunked coding govern it before the first body read.
        response.chunked = True
        response.chunk_left = None
        response.length = None
    return None


def _response_chunks(response, declared_length):
    """Yield bounded body blocks, refusing a short declared body.

    Read failures propagate into the accepted collector, which
    classifies iteration failure as ``unknown``; an EOF ahead of
    the declared ``Content-Length`` is raised the same way rather
    than being mistaken for clean completion.
    """
    total = 0
    while True:
        chunk = response.read(READ_CHUNK_BYTES)
        if isinstance(chunk, bytearray):
            chunk = bytes(chunk)
        if not isinstance(chunk, bytes):
            raise _BodyIncomplete()
        if not chunk:
            if declared_length is not None and total < declared_length:
                raise _BodyIncomplete()
            return
        total += len(chunk)
        if declared_length is not None and total > declared_length:
            raise _BodyIncomplete()
        yield chunk


def _close_quietly(resource):
    if resource is None:
        return
    try:
        resource.close()
    except Exception:
        pass


def send_payload(payload, *, credential, timeout_seconds):
    """Send one accepted builder payload and collect its stream.

    Returns the accepted collector's exact four-field result.
    Plain ``ValueError`` refuses invalid caller input before any
    connection; every transport or provider failure keeps the
    fixed ``MetaProtocolError`` classification. Exactly one
    verified connection and one POST are ever attempted.
    """
    body = _prepare_body(payload, credential, timeout_seconds)
    connection = None
    response = None
    try:
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.load_default_certs(ssl.Purpose.SERVER_AUTH)
            connection = http.client.HTTPSConnection(
                _HOST,
                _PORT,
                timeout=timeout_seconds,
                context=context,
            )
            connection.connect()
            connection.auto_open = 0
            connection.request(
                "POST",
                _PATH,
                body=body,
                headers={
                    "Authorization": "Bearer " + credential,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "Content-Length": str(len(body)),
                },
            )
            response = connection.getresponse()
        except Exception:
            raise meta_protocol.MetaProtocolError("unknown") from None

        if response.status != 200:
            raise meta_protocol.MetaProtocolError("error")

        declared_length = _validate_success_headers(response)
        try:
            return meta_stream.collect_response(
                _response_chunks(response, declared_length),
                credential=credential,
            )
        except meta_protocol.MetaProtocolError:
            raise
        except Exception:
            raise meta_protocol.MetaProtocolError("unknown") from None
    finally:
        _close_quietly(response)
        _close_quietly(connection)
