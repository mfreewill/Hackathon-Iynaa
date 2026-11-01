"""Pure bounded collector for one Meta terminal stream.

This module consumes caller-supplied SSE byte chunks in memory.
It does no network, credential lookup, filesystem, process or
environment access and keeps no state between calls. A result is
returned only after one genuine terminal response and orderly
EOF, through the accepted ``meta_protocol.parse_response``. Raw
ordinary JSON is preserved without domain approval or repair.
The accepted payload remains ``stream=false``; deriving a
streaming request belongs to a later reviewed transport.
"""
import json

from teacher_demo import meta_protocol

MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_FRAME_BYTES = 4 * 1024 * 1024

__all__ = [
    "MAX_FRAME_BYTES",
    "MAX_STREAM_BYTES",
    "collect_response",
]

_INVALID_CREDENTIAL_MESSAGE = (
    "meta stream credential is invalid"
)
_TERMINAL_TYPES = frozenset((
    "response.completed",
    "response.failed",
    "response.incomplete",
))
_UTF8_BOM = b"\xef\xbb\xbf"
_CR = 13
_LF = 10


def _refuse(outcome):
    raise meta_protocol.MetaProtocolError(outcome)


def _validate_credential(credential):
    if not isinstance(credential, str):
        raise ValueError(_INVALID_CREDENTIAL_MESSAGE)
    encode_failed = False
    try:
        credential.encode("utf-8")
    except UnicodeEncodeError:
        encode_failed = True
    if encode_failed:
        raise ValueError(_INVALID_CREDENTIAL_MESSAGE)
    if not credential.strip():
        raise ValueError(_INVALID_CREDENTIAL_MESSAGE)


def _contains_secret(value, credential):
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if credential in item:
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return False


def collect_response(chunks, *, credential):
    _validate_credential(credential)

    stream_bytes = 0
    frame_bytes = 0
    line_buffer = bytearray()
    pending_cr = False
    first_line = True

    data_lines = []
    event_field = None
    frame_started = False

    observed_response_id = None
    terminal_seen = False
    terminal_response = None
    done_seen = False

    def _add_frame_bytes(count):
        nonlocal frame_bytes
        if count > MAX_FRAME_BYTES - frame_bytes:
            _refuse("invalid_schema")
        frame_bytes += count

    def _reset_frame():
        nonlocal event_field, frame_bytes, frame_started
        data_lines.clear()
        event_field = None
        frame_started = False
        frame_bytes = 0

    def _check_response_identity(response):
        nonlocal observed_response_id
        if not isinstance(response, dict):
            _refuse("invalid_schema")
        response_id = response.get("id")
        if not isinstance(response_id, str):
            _refuse("invalid_schema")
        if not response_id.strip():
            _refuse("invalid_schema")
        if "model" in response:
            if response["model"] != meta_protocol.MODEL:
                _refuse("invalid_schema")
        if observed_response_id is None:
            observed_response_id = response_id
        elif response_id != observed_response_id:
            _refuse("unknown")
        return response

    def _dispatch_frame():
        nonlocal done_seen, terminal_response
        nonlocal terminal_seen

        if not data_lines:
            return
        data_text = "\n".join(data_lines)
        if data_text == "":
            return
        if credential in data_text:
            _refuse("error")
        if data_text == "[DONE]":
            if event_field not in (None, ""):
                _refuse("invalid_schema")
            if done_seen:
                _refuse("unknown")
            done_seen = True
            return

        event = meta_protocol._strict_json_loads(data_text)
        if _contains_secret(event, credential):
            _refuse("error")
        if not isinstance(event, dict):
            _refuse("invalid_schema")
        event_type = event.get("type")
        if not isinstance(event_type, str):
            _refuse("invalid_schema")
        if not event_type.strip():
            _refuse("invalid_schema")
        if event_field not in (None, ""):
            if event_field != event_type:
                _refuse("invalid_schema")
        if done_seen or terminal_seen:
            _refuse("unknown")

        response = None
        has_response = "response" in event
        if has_response:
            response = _check_response_identity(
                event["response"]
            )
        if event_type == "error":
            _refuse("error")
        if event_type in _TERMINAL_TYPES:
            if not has_response:
                _refuse("invalid_schema")
            expected_status = event_type[len("response."):]
            if response.get("status") != expected_status:
                _refuse("invalid_schema")
            terminal_seen = True
            terminal_response = response

    def _process_line(content_bytes):
        nonlocal event_field, first_line, frame_started

        if first_line and content_bytes.startswith(
            _UTF8_BOM
        ):
            content_bytes = content_bytes[len(_UTF8_BOM):]
        first_line = False
        line = None
        decode_failed = False
        try:
            line = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            decode_failed = True
        if decode_failed:
            raise meta_protocol.MetaProtocolError(
                "invalid_schema"
            )

        if line == "":
            _dispatch_frame()
            _reset_frame()
            return
        if line.startswith(":"):
            return

        frame_started = True
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)
        elif field == "event":
            event_field = value

    iterator = None
    iterator_failed = False
    try:
        iterator = iter(chunks)
    except Exception:
        iterator_failed = True
    if iterator_failed:
        _refuse("unknown")

    while True:
        chunk = None
        chunk_failed = False
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        except Exception:
            chunk_failed = True
        if chunk_failed:
            _refuse("unknown")

        if not isinstance(chunk, bytes):
            _refuse("invalid_schema")
        if len(chunk) > MAX_STREAM_BYTES - stream_bytes:
            _refuse("invalid_schema")
        stream_bytes += len(chunk)

        position = 0
        chunk_length = len(chunk)
        while position < chunk_length:
            if pending_cr:
                if chunk[position] == _LF:
                    _add_frame_bytes(1)
                    position += 1
                    _process_line(bytes(line_buffer))
                    line_buffer.clear()
                    pending_cr = False
                    continue
                _process_line(bytes(line_buffer))
                line_buffer.clear()
                pending_cr = False
                continue

            next_cr = chunk.find(b"\r", position)
            next_lf = chunk.find(b"\n", position)
            if next_cr == -1 and next_lf == -1:
                segment = chunk[position:]
                _add_frame_bytes(len(segment))
                line_buffer.extend(segment)
                position = chunk_length
                break
            if next_cr == -1:
                line_end = next_lf
            elif next_lf == -1:
                line_end = next_cr
            else:
                line_end = min(next_cr, next_lf)

            segment = chunk[position:line_end]
            if segment:
                _add_frame_bytes(len(segment))
                line_buffer.extend(segment)
            ending = chunk[line_end]
            _add_frame_bytes(1)
            position = line_end + 1
            if ending == _LF:
                _process_line(bytes(line_buffer))
                line_buffer.clear()
            else:
                # A following LF is part of this same ending.
                pending_cr = True

    if pending_cr:
        _process_line(bytes(line_buffer))
        line_buffer.clear()
        pending_cr = False

    dangling = frame_started
    if data_lines or event_field is not None:
        dangling = True
    if line_buffer:
        tail_bytes = bytes(line_buffer)
        if first_line and tail_bytes.startswith(_UTF8_BOM):
            tail_bytes = tail_bytes[len(_UTF8_BOM):]
        tail = None
        tail_decode_failed = False
        try:
            tail = tail_bytes.decode("utf-8")
        except UnicodeDecodeError:
            tail_decode_failed = True
        if tail_decode_failed:
            raise meta_protocol.MetaProtocolError(
                "invalid_schema"
            )
        if tail and not tail.startswith(":"):
            dangling = True
    if dangling:
        _refuse("unknown")
    if not terminal_seen:
        _refuse("unknown")

    terminal_bytes = json.dumps(
        terminal_response,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    result = None
    terminal_incomplete = False
    terminal_diagnostic = None
    try:
        result = meta_protocol.parse_response(terminal_bytes)
    except meta_protocol.MetaProtocolError as caught:
        if getattr(caught, "outcome", None) != "incomplete":
            raise
        terminal_incomplete = True
        terminal_diagnostic = meta_protocol._validated_diagnostic(
            getattr(caught, "diagnostic", None),
            credential=credential,
        )
    if terminal_incomplete:
        raise meta_protocol._incomplete_error(terminal_diagnostic)

    ordinary_text = meta_protocol._collect_ordinary_text(
        terminal_response
    )
    if credential in ordinary_text:
        _refuse("error")
    parsed_raw = meta_protocol._strict_json_loads(
        ordinary_text
    )
    if _contains_secret(parsed_raw, credential):
        _refuse("error")
    if _contains_secret(result, credential):
        _refuse("error")
    for ensure_ascii in (False, True):
        serialized = json.dumps(
            result, ensure_ascii=ensure_ascii
        )
        if credential in serialized:
            _refuse("error")
    return result
