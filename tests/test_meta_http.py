"""One-send HTTPS regression tests for ``teacher_demo.meta_http``.

Every test method first asserts module availability with
``importlib.util.find_spec`` before importing it or starting any
fixture server, so a missing module fails as an ordinary
assertion failure.

Runtime seams (loopback only):

* ``socket.create_connection`` (and the ``http.client`` class
  attribute bound to it) is redirected for the exact expected
  target ``(api.meta.ai, 443)`` to a bound ``127.0.0.1`` TLS
  fixture; any other destination is recorded and refused.
* Only ``ssl.SSLContext.load_default_certs`` is patched, to load
  the synthetic fixture CA into the real newly constructed client
  context (or to load no test roots for untrusted controls).
  ``ssl.SSLContext`` construction and ``ssl.create_default_context``
  are left unmodified, including environment-sensitive behaviour.
* ``http.client.HTTPSConnection`` keeps its real class identity;
  only its real ``__init__``/``getresponse`` methods are wrapped
  in place (and restored afterward) to record the real acquired
  connection/response objects. The real ``ssl.SSLSocket.send``
  is likewise wrapped in place to account for the client bytes
  actually accepted over TLS; no HTTP reply is mocked.

All credentials are the synthetic fixture constant. Assertions
never place credential values in failure text. No real DNS,
external socket, credential lookup or provider call is made.
"""
import base64
import contextlib
import copy
import hashlib
import hmac
import http.client
import importlib
import importlib.util
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_TLS_DIR = _FIXTURES / "meta_http_tls"
_HOST = "api.meta.ai"
_PORT = 443
_PATH = "/v1/responses"
_INVALID_MSG = "meta http request is invalid"
_REFUSED_MSG = "meta protocol response refused"
_SENTINEL = object()


def _load_fixture_json(name):
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _credential():
    return _load_fixture_json("meta_stream_inputs.json")[
        "synthetic_credential"
    ]


def _genuine_payload():
    """Build a real accepted source payload from the PNG fixture."""
    from teacher_demo import meta_protocol

    data = _load_fixture_json("meta_protocol_inputs.json")
    png = base64.b64decode(data["png_base64"])
    request = {
        "kind": "source_analysis",
        "request_id": "req-http-synthetic-001",
        "source_id": "src-http-synthetic-001",
        "source_sha256": data["png_sha256"],
        "render_sha256": hashlib.sha256(png).hexdigest(),
        "media_type": "image/png",
        "png_bytes": png,
        "grade": data["grade"],
        "objective": data["objective"],
    }
    return meta_protocol.build_payload(request, max_output_tokens=1024)


def _envelope(
    status="completed",
    ordinary_text=None,
    content_parts=None,
    extra_output=None,
    extra_fields=None,
    error=None,
    incomplete_details=None,
    include_id=True,
):
    from teacher_demo import meta_protocol

    stream = _load_fixture_json("meta_stream_inputs.json")
    if ordinary_text is None:
        ordinary_text = json.dumps(
            stream["raw"], ensure_ascii=False, separators=(",", ":")
        )
    if content_parts is None:
        content_parts = [{"type": "output_text", "text": ordinary_text}]
    output = []
    if extra_output:
        output.extend(extra_output)
    output.append(
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": content_parts,
        }
    )
    envelope = {
        "object": "response",
        "model": meta_protocol.MODEL,
        "status": status,
        "error": error,
        "incomplete_details": incomplete_details,
        "output": output,
    }
    if include_id:
        envelope["id"] = stream["response_id"]
    if extra_fields:
        envelope.update(extra_fields)
    return envelope


def _sse_event(event_type, envelope):
    event = {"type": event_type, "response": envelope}
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return ("event: " + event_type + "\ndata: " + data + "\n\n").encode(
        "utf-8"
    )


def _healthy_sse():
    return _sse_event("response.completed", _envelope())


def _collector_result(body, credential):
    from teacher_demo import meta_stream

    chunks = [body[index : index + 1] for index in range(len(body))]
    return meta_stream.collect_response(chunks, credential=credential)


def _collector_outcome(body, credential):
    from teacher_demo import meta_protocol

    try:
        return ("ok", _collector_result(body, credential))
    except meta_protocol.MetaProtocolError as exc:
        return (exc.outcome, None)


def _spec(status=200, headers=(), body=b"", fragment=0, action="respond"):
    return {
        "status": status,
        "headers": list(headers),
        "body": body,
        "fragment": fragment,
        "action": action,
    }


def _length_spec(body, content_type="text/event-stream", extra_headers=(),
                 length_value=None, fragment=0):
    headers = []
    if content_type is not None:
        headers.append(("Content-Type", content_type))
    headers.extend(extra_headers)
    if length_value is None:
        length_value = str(len(body))
    headers.append(("Content-Length", length_value))
    headers.append(("Connection", "close"))
    return _spec(200, headers, body, fragment=fragment)


def _chunked_body(body):
    middle = max(1, len(body) // 2)
    parts = []
    for piece in (body[:middle], body[middle:]):
        parts.append(("%x\r\n" % len(piece)).encode("ascii"))
        parts.append(piece)
        parts.append(b"\r\n")
    parts.append(b"0\r\n\r\n")
    return b"".join(parts)


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


_REASONS = {
    200: "OK", 301: "Moved Permanently", 302: "Found",
    307: "Temporary Redirect", 308: "Permanent Redirect",
    401: "Unauthorized", 429: "Too Many Requests",
    500: "Internal Server Error", 503: "Service Unavailable",
}


class _TlsFixtureServer:
    """Finite raw TLS fixture recording the actual wire request."""

    def __init__(self, specs, cert_name="server.pem"):
        self._specs = list(specs)
        self._lock = threading.Lock()
        self._accepts = 0
        self._tls_attempts = 0
        self._handled = 0
        self._requests = []
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(
            str(_TLS_DIR / cert_name), str(_TLS_DIR / "server-key.pem")
        )
        self._context = context
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(0.2)
        self._listener = listener
        self.port = listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def snapshot(self):
        with self._lock:
            return {
                "accepts": self._accepts,
                "tls_attempts": self._tls_attempts,
                "handled": self._handled,
                "requests": list(self._requests),
            }

    def shutdown(self):
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        self._thread.join(timeout=5.0)
        return not self._thread.is_alive()

    def _serve(self):
        while not self._stop.is_set():
            try:
                raw, _address = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                self._accepts += 1
            try:
                self._handle(raw)
            except Exception:
                # One broken fixture connection must not kill the
                # finite accept loop for later controlled cases.
                pass

    def _handle(self, raw):
        tls = None
        try:
            raw.settimeout(5.0)
            raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                self._tls_attempts += 1
            try:
                tls = self._context.wrap_socket(raw, server_side=True)
            except (ssl.SSLError, OSError):
                return
            tls.settimeout(5.0)
            try:
                request = self._read_request(tls)
                if request is not None:
                    with self._lock:
                        self._requests.append(request)
                        index = len(self._requests) - 1
                    if index < len(self._specs):
                        spec = self._specs[index]
                    else:
                        spec = _spec(500, [("Content-Length", "0")], b"")
                    if spec.get("action") != "drop":
                        self._write_response(tls, spec)
            except (ssl.SSLError, OSError):
                pass
        finally:
            with self._lock:
                self._handled += 1
            if tls is not None:
                try:
                    tls.settimeout(2.0)
                    tls.unwrap()
                except Exception:
                    pass
                try:
                    tls.close()
                except Exception:
                    pass
            else:
                try:
                    raw.close()
                except Exception:
                    pass

    def _read_request(self, tls):
        buffer = bytearray()
        while b"\r\n\r\n" not in buffer:
            if len(buffer) > 65536:
                return None
            chunk = tls.recv(65536)
            if not chunk:
                return None
            buffer.extend(chunk)
        head, separator, rest = bytes(buffer).partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        request_line = lines[0].decode("latin-1")
        headers = []
        for line in lines[1:]:
            name, colon, value = line.partition(b":")
            if colon:
                headers.append(
                    (name.decode("latin-1").strip(),
                     value.decode("latin-1").strip())
                )
        length = 0
        for name, value in headers:
            if name.lower() == "content-length":
                length = int(value)
        if length < 0 or length > 8 * 1024 * 1024:
            return None
        body = bytearray(rest)
        while len(body) < length:
            chunk = tls.recv(65536)
            if not chunk:
                break
            body.extend(chunk)
        return {
            "line": request_line,
            "headers": headers,
            "body": bytes(body[:length]),
        }

    def _write_response(self, tls, spec):
        status = int(spec.get("status", 200))
        reason = _REASONS.get(status, "Fixture")
        lines = ["HTTP/1.1 %d %s" % (status, reason)]
        for name, value in spec.get("headers", []):
            lines.append("%s: %s" % (name, value))
        tls.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        body = spec.get("body", b"") or b""
        fragment = int(spec.get("fragment", 0) or 0)
        if fragment > 0:
            for index in range(0, len(body), fragment):
                tls.sendall(body[index : index + fragment])
        elif body:
            tls.sendall(body)


@contextlib.contextmanager
def _running_server(specs, cert_name="server.pem"):
    server = _TlsFixtureServer(specs, cert_name).start()
    try:
        yield server
    finally:
        if not server.shutdown():
            raise RuntimeError("fixture server did not stop")


def _release_synthetic_keylog_handles(captured, target):
    """Release key logging for one exact test-owned synthetic target.

    Disposal only: callers run this after every observation, in a
    ``finally``, so a detected synthetic keylog regression keeps
    its failure while its context-owned handle no longer blocks
    owned-directory cleanup. No other path is touched.
    """
    if not captured:
        return
    try:
        resolved_target = Path(target).resolve()
    except Exception:
        return
    seen = set()
    for connection in list(captured.get("connections", [])):
        context = getattr(connection, "_context", None)
        if context is None:
            context = getattr(connection, "context", None)
        if context is None or id(context) in seen:
            continue
        seen.add(id(context))
        try:
            filename = getattr(context, "keylog_filename", None)
            if not filename:
                continue
            if Path(filename).resolve() != resolved_target:
                continue
        except Exception:
            continue
        try:
            context.keylog_filename = None
        except Exception:
            pass


@contextlib.contextmanager
def _client_seams(server_port, trust_ca=True):
    captured = {
        "connections": [],
        "responses": [],
        "unexpected": [],
        "tls_writes": [],
    }
    real_create = socket.create_connection
    real_conn_class = http.client.HTTPSConnection
    saved_create_attr = http.client.HTTPConnection.__dict__.get(
        "_create_connection", _SENTINEL
    )
    real_load = ssl.SSLContext.load_default_certs
    saved_init = real_conn_class.__dict__.get("__init__", _SENTINEL)
    saved_getresponse = real_conn_class.__dict__.get(
        "getresponse", _SENTINEL
    )
    real_init = real_conn_class.__init__
    real_getresponse = real_conn_class.getresponse
    saved_send = ssl.SSLSocket.__dict__.get("send", _SENTINEL)
    real_send = ssl.SSLSocket.send
    tls_limit = 1024 * 1024
    tls_state = {"total": 0}
    tls_records = {}
    tls_sockets = {}
    tls_lock = threading.Lock()

    def _observed_init(connection, *args, **kwargs):
        real_init(connection, *args, **kwargs)
        captured["connections"].append(connection)

    def _observed_getresponse(connection, *args, **kwargs):
        response = real_getresponse(connection, *args, **kwargs)
        captured["responses"].append(response)
        return response

    def _tls_record(sock):
        with tls_lock:
            record = tls_records.get(id(sock))
            if record is not None:
                return record
            for connection in list(captured["connections"]):
                if getattr(connection, "sock", None) is sock:
                    record = {
                        "accepted": bytearray(),
                        "uncertain": False,
                        "overflow": False,
                    }
                    tls_records[id(sock)] = record
                    tls_sockets[id(sock)] = sock
                    captured["tls_writes"].append(record)
                    return record
            return None

    def _observed_send(sock, data, *args, **kwargs):
        record = _tls_record(sock)
        if record is None:
            return real_send(sock, data, *args, **kwargs)
        try:
            result = real_send(sock, data, *args, **kwargs)
        except Exception:
            with tls_lock:
                record["uncertain"] = True
            raise
        with tls_lock:
            try:
                view = memoryview(data)
            except Exception:
                record["uncertain"] = True
                return result
            valid = (
                isinstance(result, int)
                and not isinstance(result, bool)
                and 0 <= result <= len(view)
            )
            if not valid:
                record["uncertain"] = True
                return result
            if result:
                piece = view[:result].tobytes()
                room = tls_limit - tls_state["total"]
                if len(piece) > room:
                    record["overflow"] = True
                    piece = piece[: max(room, 0)]
                if piece:
                    record["accepted"].extend(piece)
                    tls_state["total"] += len(piece)
        return result

    def _redirect(address, timeout=None, source_address=None, **kwargs):
        if tuple(address) == (_HOST, _PORT):
            return real_create(
                ("127.0.0.1", server_port), timeout, source_address,
                **kwargs
            )
        captured["unexpected"].append(tuple(address))
        raise OSError("fixture destination refused")

    def _load_certs(context, purpose=ssl.Purpose.SERVER_AUTH):
        if trust_ca:
            context.load_verify_locations(cafile=str(_TLS_DIR / "ca.pem"))
        else:
            real_load(context, purpose)

    try:
        socket.create_connection = _redirect
        http.client.HTTPConnection._create_connection = staticmethod(
            _redirect
        )
        real_conn_class.__init__ = _observed_init
        real_conn_class.getresponse = _observed_getresponse
        ssl.SSLSocket.send = _observed_send
        ssl.SSLContext.load_default_certs = _load_certs
        yield captured
    finally:
        socket.create_connection = real_create
        if saved_create_attr is _SENTINEL:
            try:
                del http.client.HTTPConnection._create_connection
            except AttributeError:
                pass
        else:
            http.client.HTTPConnection._create_connection = (
                saved_create_attr
            )
        if saved_init is _SENTINEL:
            try:
                del real_conn_class.__init__
            except AttributeError:
                pass
        else:
            real_conn_class.__init__ = saved_init
        if saved_getresponse is _SENTINEL:
            try:
                del real_conn_class.getresponse
            except AttributeError:
                pass
        else:
            real_conn_class.getresponse = saved_getresponse
        if saved_send is _SENTINEL:
            try:
                del ssl.SSLSocket.send
            except AttributeError:
                pass
        else:
            ssl.SSLSocket.send = saved_send
        ssl.SSLContext.load_default_certs = real_load


@contextlib.contextmanager
def _temporary_env(updates):
    saved = {}
    for key in updates:
        saved[key] = os.environ.get(key, _SENTINEL)
    try:
        for key, value in updates.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is _SENTINEL:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class MetaHttpTest(unittest.TestCase):
    # -- shared fixture helpers (not product logic) ------------------

    def _exchange(self, specs, cert_name="server.pem", trust_ca=True,
                  payload=None, credential=None, timeout_seconds=5,
                  expect_handled=1, capture_sink=None):
        meta_http = importlib.import_module("teacher_demo.meta_http")
        if payload is None:
            payload = _genuine_payload()
        if credential is None:
            credential = _credential()
        result = None
        exc = None
        with _running_server(specs, cert_name) as server:
            with _client_seams(server.port, trust_ca) as captured:
                if capture_sink is not None: capture_sink.update(captured)
                try:
                    result = meta_http.send_payload(
                        payload,
                        credential=credential,
                        timeout_seconds=timeout_seconds,
                    )
                except Exception as caught:  # returned for assertion
                    exc = caught
                if expect_handled:
                    _wait_until(
                        lambda: server.snapshot()["handled"]
                        >= expect_handled
                    )
                snapshot = server.snapshot()
        return {
            "result": result,
            "exc": exc,
            "server": snapshot,
            "captured": captured,
            "payload": payload,
        }

    def _assert_closed(self, captured, require_response=True):
        self.assertTrue(
            captured["connections"],
            "no acquired connection was observed",
        )
        for connection in captured["connections"]:
            sock = getattr(connection, "sock", None)
            closed = sock is None
            if not closed:
                try:
                    closed = sock.fileno() == -1
                except Exception:
                    closed = False
            self.assertTrue(closed, "connection was not closed")
        if require_response:
            self.assertTrue(
                captured["responses"],
                "no acquired response was observed",
            )
        for response in captured["responses"]:
            try:
                closed = response.isclosed()
            except Exception:
                closed = False
            self.assertTrue(closed, "response was not closed")

    def _context_of(self, captured):
        self.assertTrue(
            captured["connections"],
            "no acquired connection was observed",
        )
        connection = captured["connections"][0]
        context = getattr(connection, "_context", None)
        if context is None:
            context = getattr(connection, "context", None)
        self.assertTrue(
            context is not None, "acquired connection has no TLS context"
        )
        return context

    def _assert_protocol_error(self, exc, outcome,
                               expected_diagnostic=None,
                               credential=None):
        from teacher_demo import meta_protocol

        self.assertTrue(
            isinstance(exc, meta_protocol.MetaProtocolError),
            "expected a typed protocol refusal",
        )
        self.assertTrue(
            getattr(exc, "outcome", None) == outcome,
            "protocol outcome mismatch",
        )
        self.assertTrue(
            exc.args == (_REFUSED_MSG,), "protocol refusal text mismatch"
        )
        if expected_diagnostic is None:
            self.assertTrue(
                set(vars(exc)) == {"outcome"},
                "protocol refusal fields are not closed",
            )
            return
        self.assertTrue(
            outcome == "incomplete",
            "protocol diagnostic requires incomplete outcome",
        )
        self.assertTrue(
            set(vars(exc)) == {"outcome", "diagnostic"},
            "protocol diagnostic fields are not closed",
        )
        self.assertTrue(
            exc.__cause__ is None and exc.__context__ is None,
            "protocol diagnostic retains cause or context",
        )

        def _closed_diagnostic(value):
            if type(value) is not dict:
                return False
            if set(value) != {
                "version", "classifier", "response_status",
                "message_status", "response_id", "reason", "usage",
            }:
                return False
            if type(value["version"]) is not int:
                return False
            if value["version"] != 1:
                return False
            classifiers = {
                "incomplete_details", "response_status",
                "message_status",
            }
            statuses = {
                "completed", "incomplete", "failed", "queued",
                "in_progress", "cancelled", "absent", "other",
            }
            reasons = {
                "max_output_tokens", "content_filter", "absent",
                "other", "malformed",
            }
            if type(value["classifier"]) is not str:
                return False
            if value["classifier"] not in classifiers:
                return False
            if type(value["response_status"]) is not str:
                return False
            if value["response_status"] not in statuses:
                return False
            if value["classifier"] == "message_status":
                if type(value["message_status"]) is not str:
                    return False
                if value["message_status"] not in statuses:
                    return False
            elif value["message_status"] is not None:
                return False
            response_id = value["response_id"]
            if response_id is not None:
                if type(response_id) is not str:
                    return False
                if not response_id.startswith("resp_"):
                    return False
                suffix = response_id[5:]
                if not 1 <= len(suffix) <= 128:
                    return False
                for character in suffix:
                    if (
                        "a" <= character <= "z"
                        or "A" <= character <= "Z"
                        or "0" <= character <= "9"
                        or character in ("_", "-")
                    ):
                        continue
                    return False
            if type(value["reason"]) is not str:
                return False
            if value["reason"] not in reasons:
                return False
            usage = value["usage"]
            if type(usage) is not dict:
                return False
            if set(usage) != {
                "input_tokens", "output_tokens", "total_tokens",
                "reasoning_tokens",
            }:
                return False
            for count in usage.values():
                if count is None:
                    continue
                if type(count) is not int:
                    return False
                if not 0 <= count <= (1 << 53) - 1:
                    return False
            return True

        def _contains_secret(value, secret):
            stack = [value]
            seen = set()
            while stack:
                item = stack.pop()
                if isinstance(item, str):
                    if secret in item:
                        return True
                elif isinstance(item, (dict, list, tuple)):
                    if id(item) in seen:
                        continue
                    seen.add(id(item))
                    if isinstance(item, dict):
                        stack.extend(item.keys())
                        stack.extend(item.values())
                    else:
                        stack.extend(item)
            return False

        diagnostic = vars(exc)["diagnostic"]
        self.assertTrue(
            _closed_diagnostic(diagnostic),
            "protocol diagnostic is not closed",
        )
        self.assertTrue(
            _closed_diagnostic(expected_diagnostic),
            "protocol diagnostic expectation is not closed",
        )
        self.assertTrue(
            diagnostic == expected_diagnostic,
            "protocol diagnostic mismatch",
        )
        secret = credential if credential is not None else _credential()
        self.assertTrue(
            isinstance(secret, str) and bool(secret),
            "protocol diagnostic credential check unavailable",
        )
        self.assertTrue(
            not _contains_secret(diagnostic, secret),
            "protocol diagnostic contains credential",
        )
        serialized_ok = True
        try:
            for ensure_ascii in (False, True):
                serialized = json.dumps(
                    diagnostic,
                    ensure_ascii=ensure_ascii,
                    allow_nan=False,
                    separators=(",", ":"),
                )
                if secret in serialized:
                    serialized_ok = False
        except Exception:
            serialized_ok = False
        self.assertTrue(
            serialized_ok,
            "protocol diagnostic contains credential",
        )

    def _assert_invalid_request(self, exc):
        self.assertTrue(
            type(exc) is ValueError,
            "expected a plain ValueError refusal",
        )
        self.assertTrue(
            exc.args == (_INVALID_MSG,), "request refusal text mismatch"
        )
        public = [key for key in vars(exc) if not key.startswith("_")]
        self.assertTrue(not public, "request refusal carries public data")

    def _assert_one_post(self, exchange):
        server = exchange["server"]
        self.assertEqual(server["accepts"], 1)
        self.assertEqual(len(server["requests"]), 1)
        self.assertEqual(exchange["captured"]["unexpected"], [])
        line = server["requests"][0]["line"]
        self.assertTrue(
            line.startswith("POST " + _PATH + " HTTP/"),
            "wire request line is not the single expected POST",
        )
        # Complete-write accounting: the server record above is
        # only its independently received first request. The bytes
        # below are what this client's TLS socket actually had
        # accepted locally, framed as exactly one HTTP request.
        records = exchange["captured"].get("tls_writes", [])
        self.assertTrue(
            len(records) == 1,
            "expected one observed client TLS write record",
        )
        record = records[0]
        self.assertTrue(
            not record.get("uncertain") and not record.get("overflow"),
            "observed client TLS writes are unverifiable",
        )
        accepted = bytes(record.get("accepted", b""))
        self.assertTrue(
            bool(accepted), "observed client TLS writes are empty"
        )
        head, separator, rest = accepted.partition(b"\r\n\r\n")
        self.assertTrue(
            bool(separator),
            "observed client TLS writes lack one complete frame",
        )
        frame_lines = head.split(b"\r\n") if head else []
        self.assertTrue(
            bool(frame_lines),
            "observed client TLS writes lack one complete frame",
        )
        observed_line = frame_lines[0].decode("latin-1")
        observed_headers = []
        length_values = []
        for raw_line in frame_lines[1:]:
            raw_name, colon, raw_value = raw_line.partition(b":")
            self.assertTrue(
                bool(colon),
                "observed client TLS writes lack one complete frame",
            )
            header_name = raw_name.decode("latin-1").strip()
            header_value = raw_value.decode("latin-1").strip()
            observed_headers.append((header_name, header_value))
            if header_name.lower() == "content-length":
                length_values.append(header_value)
        framing_ok = (
            len(length_values) == 1
            and bool(length_values[0])
            and all(
                character in "0123456789"
                for character in length_values[0]
            )
        )
        self.assertTrue(
            framing_ok,
            "observed client TLS writes lack one complete frame",
        )
        self.assertTrue(
            len(rest) == int(length_values[0]),
            "observed client TLS writes hold surplus or missing bytes",
        )
        server_request = server["requests"][0]
        self.assertTrue(
            observed_line == server_request["line"],
            "observed client TLS request differs from server readback",
        )
        self.assertTrue(
            observed_headers == server_request["headers"],
            "observed client TLS request differs from server readback",
        )
        self.assertTrue(
            rest == server_request["body"],
            "observed client TLS request differs from server readback",
        )

    def _headers(self, request):
        names = []
        values = {}
        for name, value in request["headers"]:
            lowered = name.lower()
            names.append(lowered)
            values.setdefault(lowered, []).append(value)
        return names, values

    def _assert_bearer(self, request, credential):
        _names, values = self._headers(request)
        found = values.get("authorization", [])
        self.assertEqual(len(found), 1)
        expected = "Bearer " + credential
        self.assertTrue(
            hmac.compare_digest(found[0], expected),
            "authorization bearer mismatch",
        )

    # -- 1: one verified POST preserves payload and result ----------

    def test_one_verified_post_preserves_payload_and_result(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_http")
        )
        meta_http = importlib.import_module("teacher_demo.meta_http")
        self.assertEqual(meta_http.MAX_REQUEST_BYTES, 128 * 1024 * 1024)
        self.assertEqual(meta_http.READ_CHUNK_BYTES, 64 * 1024)
        payload = _genuine_payload()
        caller_snapshot = copy.deepcopy(payload)
        credential = _credential()
        body = _healthy_sse()
        expected_result = _collector_result(body, credential)
        exchange = self._exchange(
            [_length_spec(body, fragment=3)],
            payload=payload,
            credential=credential,
            timeout_seconds=5.0,
        )
        self.assertTrue(exchange["exc"] is None, "expected success")
        result = exchange["result"]
        self.assertEqual(
            set(result), {"raw", "model_name", "response_id", "outcome"}
        )
        self.assertEqual(result, expected_result)
        stream = _load_fixture_json("meta_stream_inputs.json")
        self.assertEqual(result["raw"], stream["raw"])
        self.assertEqual(
            result["raw"]["sections"][0]["text"],
            "  نَصّ عربي ١٢ / 12 𝄞\nثانٍ  ",
        )
        self.assertEqual(result["response_id"], stream["response_id"])
        self.assertEqual(payload, caller_snapshot)
        self.assertIs(payload["stream"], False)
        self._assert_one_post(exchange)
        request = exchange["server"]["requests"][0]
        names, values = self._headers(request)
        self.assertEqual(
            sorted(names),
            sorted([
                "host", "authorization", "content-length",
                "content-type", "accept", "accept-encoding",
                "connection",
            ]),
        )
        host = values["host"][0]
        self.assertTrue(
            host in (_HOST, _HOST + ":443"), "wire Host header mismatch"
        )
        self._assert_bearer(request, credential)
        self.assertEqual(
            values["content-length"], [str(len(request["body"]))]
        )
        self.assertEqual(values["content-type"], ["application/json"])
        self.assertEqual(values["accept"], ["text/event-stream"])
        self.assertEqual(values["accept-encoding"], ["identity"])
        self.assertEqual(values["connection"], ["close"])
        outgoing = json.loads(request["body"].decode("utf-8"))
        expected_outgoing = copy.deepcopy(caller_snapshot)
        expected_outgoing["stream"] = True
        self.assertEqual(outgoing, expected_outgoing)
        context = self._context_of(exchange["captured"])
        self.assertIs(context.check_hostname, True)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(
            not getattr(context, "keylog_filename", None),
            "key logging must stay disabled",
        )
        self._assert_closed(exchange["captured"])

    # -- 2: invalid inputs refuse before network --------------------

    def test_invalid_inputs_refuse_before_network(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_http")
        )
        meta_http = importlib.import_module("teacher_demo.meta_http")
        genuine = _genuine_payload()
        credential = _credential()

        payload_cases = []

        def add(label, mutate):
            candidate = copy.deepcopy(genuine)
            mutate(candidate)
            payload_cases.append((label, candidate))

        add("missing model", lambda p: p.pop("model"))
        add("missing input", lambda p: p.pop("input"))
        add("missing reasoning", lambda p: p.pop("reasoning"))
        add("extra field", lambda p: p.__setitem__("extra", 1))
        add("wrong model", lambda p: p.__setitem__("model", "other"))
        add("wrong effort",
            lambda p: p.__setitem__("reasoning", {"effort": "low"}))
        add("reasoning extra key",
            lambda p: p.__setitem__(
                "reasoning", {"effort": "max", "extra": 1}))
        add("reasoning not object",
            lambda p: p.__setitem__("reasoning", "max"))
        add("store true", lambda p: p.__setitem__("store", True))
        add("store numeric zero", lambda p: p.__setitem__("store", 0))
        add("stream true", lambda p: p.__setitem__("stream", True))
        add("stream numeric zero", lambda p: p.__setitem__("stream", 0))
        add("tokens bool",
            lambda p: p.__setitem__("max_output_tokens", True))
        add("tokens too small",
            lambda p: p.__setitem__("max_output_tokens", 15))
        add("tokens float",
            lambda p: p.__setitem__("max_output_tokens", 16.0))
        add("tokens text",
            lambda p: p.__setitem__("max_output_tokens", "1024"))
        add("nonfinite nested",
            lambda p: p["input"][0]["content"][0].__setitem__(
                "text", float("nan")))
        add("infinite nested",
            lambda p: p["input"][0]["content"][0].__setitem__(
                "text", float("inf")))
        add("non-json nested",
            lambda p: p["input"][0]["content"][0].__setitem__(
                "text", object()))
        add("bytes nested",
            lambda p: p["input"][0]["content"][0].__setitem__(
                "text", b"raw"))
        add("set nested",
            lambda p: p["input"][0]["content"][0].__setitem__(
                "text", {"x"}))
        add("lone surrogate text",
            lambda p: p["input"][0]["content"][0].__setitem__(
                "text", "bad\ud800text"))
        add("non-string key", lambda p: p.__setitem__("text", {1: "x"}))
        circular = copy.deepcopy(genuine)
        circular["input"].append(circular)
        payload_cases.append(("circular payload", circular))
        payload_cases.append(("payload none", None))
        payload_cases.append(("payload list", []))
        payload_cases.append(("payload text", "{}"))
        payload_cases.append(("payload bytes", b"{}"))

        credential_cases = [
            ("empty credential", ""),
            ("space credential", "has space"),
            ("tab credential", "has\ttab"),
            ("newline credential", "has\nnewline"),
            ("carriage credential", "has\rcarriage"),
            ("non-ascii credential", "café"),
            ("del credential", "bad\x7fvalue"),
            ("control credential", "bad\x1fvalue"),
            ("surrogate credential", "bad\ud800value"),
            ("none credential", None),
            ("bytes credential", b"synthetic"),
            ("int credential", 123),
        ]
        timeout_cases = [
            ("bool timeout", True),
            ("false timeout", False),
            ("text timeout", "5"),
            ("none timeout", None),
            ("zero timeout", 0),
            ("negative timeout", -1),
            ("negative float timeout", -0.5),
            ("nan timeout", float("nan")),
            ("inf timeout", float("inf")),
            ("negative inf timeout", float("-inf")),
        ]

        body = _healthy_sse()
        expected_result = _collector_result(body, credential)
        specs = [_length_spec(body), _length_spec(body)]
        with _running_server(specs) as server:
            with _client_seams(server.port, True) as captured:
                def attempt(payload, cred, timeout):
                    try:
                        meta_http.send_payload(
                            payload,
                            credential=cred,
                            timeout_seconds=timeout,
                        )
                    except Exception as caught:
                        return caught
                    return None

                for label, candidate in payload_cases:
                    with self.subTest(case=label):
                        self._assert_invalid_request(
                            attempt(candidate, credential, 5)
                        )
                        self.assertEqual(server.snapshot()["accepts"], 0)
                for label, candidate in credential_cases:
                    with self.subTest(case=label):
                        self._assert_invalid_request(
                            attempt(genuine, candidate, 5)
                        )
                        self.assertEqual(server.snapshot()["accepts"], 0)
                for label, candidate in timeout_cases:
                    with self.subTest(case=label):
                        self._assert_invalid_request(
                            attempt(genuine, credential, candidate)
                        )
                        self.assertEqual(server.snapshot()["accepts"], 0)
                self.assertEqual(captured["unexpected"], [])

                # Healthy pairing proves the fixture was live; the
                # integer timeout is the numeric-timeout control.
                result = meta_http.send_payload(
                    genuine, credential=credential, timeout_seconds=5
                )
                self.assertEqual(result, expected_result)
                self.assertTrue(
                    _wait_until(
                        lambda: len(server.snapshot()["requests"]) >= 1
                    ),
                    "fixture never saw the healthy control",
                )
                snapshot = server.snapshot()
                self.assertEqual(snapshot["accepts"], 1)
                self.assertEqual(len(snapshot["requests"]), 1)
                body_length = len(snapshot["requests"][0]["body"])

                # Exact/over outgoing-body bounds via a small
                # temporary bound change, always restored.
                original_max = meta_http.MAX_REQUEST_BYTES
                try:
                    meta_http.MAX_REQUEST_BYTES = body_length
                    exact = meta_http.send_payload(
                        genuine,
                        credential=credential,
                        timeout_seconds=5,
                    )
                    self.assertEqual(exact, expected_result)
                    self.assertTrue(
                        _wait_until(
                            lambda: len(server.snapshot()["requests"])
                            >= 2
                        ),
                        "fixture never saw the exact-bound control",
                    )
                    meta_http.MAX_REQUEST_BYTES = body_length - 1
                    self._assert_invalid_request(
                        attempt(genuine, credential, 5)
                    )
                finally:
                    meta_http.MAX_REQUEST_BYTES = original_max
                snapshot = server.snapshot()
                self.assertEqual(snapshot["accepts"], 2)
                self.assertEqual(len(snapshot["requests"]), 2)

    # -- 3: HTTP failures never retry or follow redirects ----------

    def test_http_failures_never_retry_or_follow_redirects(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_http")
        )
        credential = _credential()
        healthy = _healthy_sse()
        expected_result = _collector_result(healthy, credential)

        with self.subTest(case="success closure"):
            exchange = self._exchange([_length_spec(healthy)])
            self.assertTrue(exchange["exc"] is None, "expected success")
            self.assertEqual(exchange["result"], expected_result)
            self._assert_one_post(exchange)
            self._assert_closed(exchange["captured"])

        marker = "SYNTHETIC-ERROR-MARKER"
        for status in (301, 302, 307, 308, 401, 429, 500, 503):
            with self.subTest(status=status):
                error_body = marker.encode("ascii")
                headers = [
                    ("Content-Type", "text/plain"),
                    ("X-Fixture-Marker", marker),
                    ("Content-Length", str(len(error_body))),
                    ("Connection", "close"),
                ]
                if status in (301, 302, 307, 308):
                    headers.insert(1, ("Location", _PATH + "-again"))
                exchange = self._exchange(
                    [_spec(status, headers, error_body)]
                )
                self._assert_protocol_error(exchange["exc"], "error")
                self.assertTrue(
                    marker not in str(exchange["exc"]),
                    "error body or header data leaked into failure",
                )
                self.assertTrue(
                    marker not in repr(vars(exchange["exc"])),
                    "error body or header data leaked into failure",
                )
                self._assert_one_post(exchange)
                self._assert_closed(exchange["captured"])

        with self.subTest(case="dropped response"):
            exchange = self._exchange([_spec(action="drop")])
            self._assert_protocol_error(exchange["exc"], "unknown")
            self._assert_one_post(exchange)
            self._assert_closed(
                exchange["captured"], require_response=False
            )

    # -- 4: success headers and body completion are truthful --------

    def test_success_headers_and_body_completion_are_truthful(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_http")
        )
        from teacher_demo import meta_stream

        credential = _credential()
        body = _healthy_sse()
        expected_result = _collector_result(body, credential)

        valid_cases = [
            ("content-length", _length_spec(body)),
            ("media type case and parameters", _length_spec(
                body, content_type="Text/Event-Stream; Charset=UTF-8")),
            ("identity encoding", _length_spec(
                body,
                extra_headers=[("Content-Encoding", "identity")])),
            ("chunked", _spec(200, [
                ("Content-Type", "text/event-stream"),
                ("Transfer-Encoding", "chunked"),
                ("Connection", "close"),
            ], _chunked_body(body))),
            ("close-delimited", _spec(200, [
                ("Content-Type", "text/event-stream"),
                ("Connection", "close"),
            ], body)),
        ]
        for label, spec in valid_cases:
            with self.subTest(case=label):
                exchange = self._exchange([spec])
                self.assertTrue(
                    exchange["exc"] is None, "expected success"
                )
                self.assertEqual(exchange["result"], expected_result)
                self._assert_one_post(exchange)

        length = str(len(body))

        def spec_with(headers):
            return _spec(200, headers, body)

        invalid_cases = [
            ("wrong media type", spec_with([
                ("Content-Type", "application/json"),
                ("Content-Length", length),
                ("Connection", "close")])),
            ("missing media type", spec_with([
                ("Content-Length", length),
                ("Connection", "close")])),
            ("duplicate media type", spec_with([
                ("Content-Type", "text/event-stream"),
                ("Content-Type", "text/event-stream"),
                ("Content-Length", length),
                ("Connection", "close")])),
            ("unsupported encoding", spec_with([
                ("Content-Type", "text/event-stream"),
                ("Content-Encoding", "gzip"),
                ("Content-Length", length),
                ("Connection", "close")])),
            ("duplicate encoding", spec_with([
                ("Content-Type", "text/event-stream"),
                ("Content-Encoding", "identity"),
                ("Content-Encoding", "identity"),
                ("Content-Length", length),
                ("Connection", "close")])),
            ("malformed length text", _length_spec(
                body, length_value="abc")),
            ("malformed length sign", _length_spec(
                body, length_value="+12")),
            ("malformed length negative", _length_spec(
                body, length_value="-1")),
            ("malformed length comma", _length_spec(
                body, length_value="12, 12")),
            ("malformed length empty", _length_spec(
                body, length_value="")),
            ("duplicate length", spec_with([
                ("Content-Type", "text/event-stream"),
                ("Content-Length", length),
                ("Content-Length", length),
                ("Connection", "close")])),
            ("length with transfer-encoding", spec_with([
                ("Content-Type", "text/event-stream"),
                ("Content-Length", length),
                ("Transfer-Encoding", "chunked"),
                ("Connection", "close")])),
            ("unsupported transfer-encoding", spec_with([
                ("Content-Type", "text/event-stream"),
                ("Transfer-Encoding", "gzip"),
                ("Connection", "close")])),
            ("transfer-encoding list", spec_with([
                ("Content-Type", "text/event-stream"),
                ("Transfer-Encoding", "chunked, identity"),
                ("Connection", "close")])),
            ("duplicate transfer-encoding", spec_with([
                ("Content-Type", "text/event-stream"),
                ("Transfer-Encoding", "chunked"),
                ("Transfer-Encoding", "chunked"),
                ("Connection", "close")])),
            ("overbound declared length", _length_spec(
                body,
                length_value=str(meta_stream.MAX_STREAM_BYTES + 1))),
        ]
        for label, spec in invalid_cases:
            with self.subTest(case=label):
                exchange = self._exchange([spec])
                self._assert_protocol_error(
                    exchange["exc"], "invalid_schema"
                )
                self._assert_one_post(exchange)

        with self.subTest(case="terminal then short eof is unknown"):
            exchange = self._exchange([
                _length_spec(body, length_value=str(len(body) + 16))
            ])
            self._assert_protocol_error(exchange["exc"], "unknown")
            self.assertTrue(
                exchange["result"] is None,
                "short body must never be an early success",
            )
            self._assert_one_post(exchange)

        credential_body_text = json.dumps(
            {"note": credential}, ensure_ascii=False
        )
        collector_cases = [
            ("failed terminal", _sse_event(
                "response.failed",
                _envelope(
                    status="failed",
                    error={"message": "synthetic failure"}))),
            ("incomplete terminal", _sse_event(
                "response.incomplete",
                _envelope(
                    status="incomplete",
                    incomplete_details={"reason": "synthetic"}))),
            ("refusal", _sse_event(
                "response.completed",
                _envelope(content_parts=[
                    {"type": "refusal",
                     "refusal": "synthetic refusal"}]))),
            ("empty", _sse_event(
                "response.completed",
                _envelope(ordinary_text="   "))),
            ("malformed event", b"data: {not-json}\n\n"),
            ("missing terminal identity", _sse_event(
                "response.completed", _envelope(include_id=False))),
            ("credential bearing", _sse_event(
                "response.completed",
                _envelope(ordinary_text=credential_body_text))),
        ]
        for label, case_body in collector_cases:
            with self.subTest(case=label):
                outcome, _unused = _collector_outcome(
                    case_body, credential
                )
                self.assertTrue(
                    outcome != "ok",
                    "collector fixture must itself refuse",
                )
                exchange = self._exchange([_length_spec(case_body)])
                self._assert_protocol_error(
                    exchange["exc"], outcome,
                    expected_diagnostic=(
                        {
                            "version": 1,
                            "classifier": "incomplete_details",
                            "response_status": "incomplete",
                            "message_status": None,
                            "response_id": None,
                            "reason": "other",
                            "usage": {
                                "input_tokens": None,
                                "output_tokens": None,
                                "total_tokens": None,
                                "reasoning_tokens": None,
                            },
                        }
                        if label == "incomplete terminal"
                        else None
                    ),
                    credential=(
                        credential
                        if label == "incomplete terminal"
                        else None
                    ))
                self._assert_one_post(exchange)

        with self.subTest(case="private metadata never returned"):
            marker = "SYNTHETIC-PRIVATE-MARKER"
            envelope = _envelope(
                extra_output=[{
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": marker}
                    ],
                }],
                extra_fields={
                    "usage": {"input_tokens": 1},
                    "metadata": {"private": marker},
                },
            )
            exchange = self._exchange([
                _length_spec(_sse_event("response.completed", envelope))
            ])
            self.assertTrue(exchange["exc"] is None, "expected success")
            self.assertEqual(exchange["result"], expected_result)
            self.assertTrue(
                marker
                not in json.dumps(
                    exchange["result"], ensure_ascii=False
                ),
                "private or metadata content leaked into result",
            )
            self._assert_one_post(exchange)

    # -- 5: TLS identity and ambient configuration ------------------

    def test_tls_identity_and_ambient_configuration_cannot_redirect(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.meta_http")
        )
        self.assertEqual(sys.flags.ignore_environment, 0)
        credential = _credential()
        body = _healthy_sse()
        expected_result = _collector_result(body, credential)

        with self.subTest(case="trusted chain and correct host"):
            exchange = self._exchange([_length_spec(body)])
            self.assertTrue(exchange["exc"] is None, "expected success")
            self.assertEqual(exchange["result"], expected_result)
            self._assert_one_post(exchange)
            context = self._context_of(exchange["captured"])
            self.assertIs(context.check_hostname, True)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

        with self.subTest(case="untrusted chain"):
            exchange = self._exchange(
                [_length_spec(body)], trust_ca=False
            )
            self._assert_protocol_error(exchange["exc"], "unknown")
            self.assertEqual(exchange["server"]["tls_attempts"], 1)
            self.assertEqual(exchange["server"]["accepts"], 1)
            self.assertEqual(exchange["server"]["requests"], [])
            self.assertEqual(exchange["captured"]["unexpected"], [])

        with self.subTest(case="wrong-name certificate"):
            exchange = self._exchange(
                [_length_spec(body)], cert_name="wrong-name.pem"
            )
            self._assert_protocol_error(exchange["exc"], "unknown")
            self.assertEqual(exchange["server"]["tls_attempts"], 1)
            self.assertEqual(exchange["server"]["accepts"], 1)
            self.assertEqual(exchange["server"]["requests"], [])
            self.assertEqual(exchange["captured"]["unexpected"], [])

        with self.subTest(case="ambient proxy auth and netrc ignored"):
            with tempfile.TemporaryDirectory() as directory:
                netrc_path = Path(directory) / "synthetic.netrc"
                netrc_path.write_text(
                    "machine api.meta.ai login ambient "
                    "password AMBIENT-SYNTHETIC-NOT-REAL\n",
                    encoding="utf-8",
                )
                updates = {
                    "http_proxy": "http://127.0.0.1:1",
                    "https_proxy": "http://127.0.0.1:1",
                    "HTTP_PROXY": "http://127.0.0.1:1",
                    "HTTPS_PROXY": "http://127.0.0.1:1",
                    "NETRC": str(netrc_path),
                    "META_API_KEY": "AMBIENT-SYNTHETIC-NOT-REAL",
                }
                with _temporary_env(updates):
                    exchange = self._exchange([_length_spec(body)])
                self.assertTrue(
                    exchange["exc"] is None, "expected success"
                )
                self.assertEqual(exchange["result"], expected_result)
                self._assert_one_post(exchange)
                request = exchange["server"]["requests"][0]
                self._assert_bearer(request, credential)

        with self.subTest(case="keylog target missing stays missing"):
            with tempfile.TemporaryDirectory() as directory:
                keylog_path = Path(directory) / "keylog-missing.log"
                capture_sink = {}
                try:
                    self.assertTrue(
                        not keylog_path.exists(), "keylog fixture is stale"
                    )
                    with _temporary_env(
                        {"SSLKEYLOGFILE": str(keylog_path)}
                    ):
                        exchange = self._exchange(
                            [_length_spec(body)],
                            capture_sink=capture_sink,
                        )
                        self.assertTrue(
                            not keylog_path.exists(),
                            "ambient keylog setting created a file",
                        )
                    self.assertTrue(
                        exchange["exc"] is None, "expected success"
                    )
                    self.assertEqual(exchange["result"], expected_result)
                    self._assert_one_post(exchange)
                    context = self._context_of(exchange["captured"])
                    self.assertTrue(
                        not getattr(context, "keylog_filename", None),
                        "key logging must stay unset on the used context",
                    )
                finally:
                    _release_synthetic_keylog_handles(
                        capture_sink, keylog_path
                    )

        with self.subTest(case="keylog sentinel stays byte-identical"):
            with tempfile.TemporaryDirectory() as directory:
                keylog_path = Path(directory) / "keylog-sentinel.log"
                capture_sink = {}
                try:
                    sentinel = b"SYNTHETIC-SENTINEL-DO-NOT-TOUCH\n"
                    keylog_path.write_bytes(sentinel)
                    with _temporary_env(
                        {"SSLKEYLOGFILE": str(keylog_path)}
                    ):
                        exchange = self._exchange(
                            [_length_spec(body)],
                            capture_sink=capture_sink,
                        )
                        self.assertEqual(
                            keylog_path.read_bytes(), sentinel
                        )
                    self.assertTrue(
                        exchange["exc"] is None, "expected success"
                    )
                    self.assertEqual(exchange["result"], expected_result)
                    self._assert_one_post(exchange)
                    context = self._context_of(exchange["captured"])
                    self.assertTrue(
                        not getattr(context, "keylog_filename", None),
                        "key logging must stay unset on the used context",
                    )
                finally:
                    _release_synthetic_keylog_handles(
                        capture_sink, keylog_path
                    )


if __name__ == "__main__":
    unittest.main()
