"""Test-only child fixture for ``teacher_demo.meta_transport`` tests.

This file is never imported by product code. The parent test wraps
the real ``asyncio.create_subprocess_exec`` boundary, validates the
production launch record (interpreter, ``-I -B`` flags, entry point,
environment, cwd), and then substitutes ONLY this fixed script with
fixture-only arguments. No credential or payload is ever placed in
this script's argv, environment, or files; the one private IPC
input still arrives on stdin and is consumed by the real product
child or merely drained by synthetic modes.

Modes (every label is an explicit literal):

* ``real_child <port>`` -- installs an IPv4-loopback-only socket
  guard, installs the existing ``test_meta_http._client_seams``
  local-TLS/destination/trust seams, then executes the REAL product
  script branch via ``runpy.run_path(..., run_name="__main__")``.
  The real ``_child_main`` and the real ``meta_http.send_payload``
  do all parsing, sending, and output framing.
* ``real_guarded`` -- installs a refuse-every-network-operation
  guard (no TLS seams, no port), then executes the same real
  script branch. Used so malformed-stdin process cases never run
  a live-capable child unguarded.
* ``syn_*`` -- explicitly synthetic child-protocol modes for
  supervisor failure cases. They emit known synthetic frames
  derived from the existing safe fixtures; they are not real Meta
  responses and not a replacement HTTP helper.

Barrier modes take ``<ready-file> <release-file>`` paths inside a
test-owned temporary directory (plain coordination files holding
one fixed byte, never request data). Every waiting mode is finite:
it exits on its own after a bounded wait if never released.
"""
import contextvars
import copy
import json
import runpy
import socket
import sys
import threading
import time
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SRC_DIR = _TESTS_DIR.parent / "src"
_FIXTURES_DIR = _TESTS_DIR / "fixtures"
_PRODUCT_SCRIPT = _SRC_DIR / "teacher_demo" / "meta_transport.py"
_GUARD_MARKER = "SOCKET-GUARD-REFUSAL\n"
_DIAGNOSTIC_MARKER = "SYNTHETIC-DIAGNOSTIC-MARKER\n"
_CLOSED_FAILURES = (
    "error", "unknown", "invalid_schema", "incomplete",
    "refusal", "empty", "cancelled",
)
_BARRIER_WAIT_SECONDS = 25.0

for _path in (str(_SRC_DIR), str(_TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_EXPECTED_RESULT = None
_FIXTURE_CREDENTIAL = None


# -- shared process-level network authority --------------------------
# One audit hook, registered at most once and never at import, backs
# both this child fixture and (by import) the parent test guard.
# While active it enforces a process-wide numeric IPv4
# stream-loopback floor in every thread; a scoped ``refuse-all``
# policy narrows only the entering thread/task, and any enclosing
# refuse-all scope wins over an inner loopback scope. Leaving a
# scope restores its policy and deactivates the floor when the last
# scope goes, but the immutable hook itself is never removed or
# re-added. Because this runtime emits no audit event for ordinary
# send/receive methods, activation also installs (once, and without
# replacing either class) thin checked wrappers on the public
# ``socket.socket`` and ``ssl.SSLSocket`` operation methods, so the
# same policy reaches traffic on sockets created before a scope
# narrowed. Each wrapper delegates to a private captured original
# only after the policy allows the call; when no policy is active
# it delegates unchanged. This polices cooperative inspected
# Python; it is not OS confinement. Refusals are recorded by event
# name and count only, never by address or payload value.

_NET_LOCK = threading.Lock()
_NET_INSTALL_LOCK = threading.Lock()
_NET_HOOK_INSTALLED = False
_NET_WRAPPERS_INSTALLED = False
_NET_FLOOR_DEPTH = 0
_NET_PROCESS_MODE = None
_NET_MARK_VIOLATIONS = False
_NET_REFUSAL_COUNT = 0
_NET_ACTIVE_SCOPES = []
_NET_THREAD_STATE = threading.local()
_NET_CONTEXT_SCOPE = contextvars.ContextVar(
    "meta_transport_network_scope", default=()
)
_NET_REFUSAL_TEXT = "network refused by test guard"
_NET_CONSTRUCTION_EVENTS = frozenset((
    "socket.__new__", "socket.socket", "socket.fromfd",
    "socket.fromshare", "socket.socketpair",
))
_NET_ADDRESS_EVENTS = frozenset((
    "socket.connect", "socket.connect_ex", "socket.bind",
))
_NET_LISTENER_EVENTS = frozenset(("socket.accept", "socket.listen"))
_NET_DATA_EVENTS = frozenset((
    "socket.send", "socket.sendall", "socket.sendfile",
    "socket.recv", "socket.recv_into", "socket.recvfrom",
    "socket.recvfrom_into", "socket.recvmsg", "socket.recvmsg_into",
))
_NET_LOCAL_EVENTS = frozenset((
    "socket.close", "socket.shutdown", "socket.fileno",
    "socket.getsockname", "socket.getpeername",
    "socket.getsockopt", "socket.setsockopt",
    "socket.setblocking", "socket.settimeout", "socket.gettimeout",
))
_NET_DESCRIPTOR_EVENTS = frozenset((
    "socket.dup", "socket.detach",
))
_NET_ALWAYS_DENIED_EVENTS = frozenset((
    "socket.sendto", "socket.sendmsg", "socket.sendmsg_afalg",
))
_NET_ORIGINAL_SOCKET_METHODS = {}
_NET_ORIGINAL_SSL_METHODS = {}
_NET_SOCKET_WRAPPERS = (
    ("connect", "address"), ("connect_ex", "address"),
    ("bind", "address"),
    ("listen", "listener"), ("accept", "listener"),
    ("send", "data"), ("sendall", "data"), ("sendfile", "data"),
    ("recv", "data"), ("recv_into", "data"),
    ("recvfrom", "data"), ("recvfrom_into", "data"),
    ("recvmsg", "data"), ("recvmsg_into", "data"),
    ("sendto", "denied"), ("sendmsg", "denied"),
    ("sendmsg_afalg", "denied"),
    ("dup", "descriptor"), ("detach", "descriptor"),
    ("makefile", "makefile"),
)
_NET_SSL_WRAPPERS = (
    ("connect", "address"), ("connect_ex", "address"),
    ("bind", "address"),
    ("listen", "listener"), ("accept", "listener"),
    ("send", "data"), ("sendall", "data"), ("sendfile", "data"),
    ("write", "data"),
    ("recv", "data"), ("recv_into", "data"), ("read", "data"),
    ("recvfrom", "data"), ("recvfrom_into", "data"),
    ("sendto", "denied"), ("sendmsg", "denied"),
    ("do_handshake", "handshake"), ("unwrap", "handshake"),
    ("dup", "descriptor"), ("detach", "descriptor"),
    ("makefile", "makefile"),
)


def _net_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _net_is_stream(type_value):
    kind = _net_int(type_value)
    if kind is None:
        return False
    for flag_name in ("SOCK_NONBLOCK", "SOCK_CLOEXEC"):
        flag = _net_int(getattr(socket, flag_name, 0))
        if flag:
            kind &= ~flag
    return kind == int(socket.SOCK_STREAM)


def _net_sock_is_ipv4_stream(sock, family=None, type_value=None):
    if family is None:
        family = getattr(sock, "family", None)
    if type_value is None:
        type_value = getattr(sock, "type", None)
    return (
        _net_int(family) == int(socket.AF_INET)
        and _net_is_stream(type_value)
    )


def _net_host_is_loopback(host):
    return host == "127.0.0.1" or host == b"127.0.0.1"


def _net_address_is_loopback(address):
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    port = _net_int(address[1])
    return (
        _net_host_is_loopback(address[0])
        and port is not None
        and 0 <= port <= 65535
    )


def _net_first_socket(args):
    for arg in args:
        if hasattr(arg, "family") and hasattr(arg, "type"):
            return arg
    return None


def _net_construction_allowed(args):
    sock = _net_first_socket(args)
    if sock is not None:
        if isinstance(sock, type):
            covered = issubclass(sock, socket.socket)
        else:
            covered = isinstance(sock, socket.socket)
        if not covered:
            return False
        index = args.index(sock)
        family = args[index + 1] if len(args) > index + 1 else None
        type_value = args[index + 2] if len(args) > index + 2 else None
        if _net_int(family) is None or _net_int(family) < 0:
            family = getattr(sock, "family", None)
        if _net_int(type_value) is None or _net_int(type_value) < 0:
            type_value = getattr(sock, "type", None)
        return _net_sock_is_ipv4_stream(sock, family, type_value)
    if len(args) >= 2:
        return (
            _net_int(args[0]) == int(socket.AF_INET)
            and _net_is_stream(args[1])
        )
    return False


def _net_getaddrinfo_allowed(args):
    if not args or not _net_host_is_loopback(args[0]):
        return False
    if len(args) >= 3 and args[2] is not None:
        if _net_int(args[2]) not in (0, int(socket.AF_INET)):
            return False
    if len(args) >= 4 and args[3] is not None:
        if _net_int(args[3]) != 0 and not _net_is_stream(args[3]):
            return False
    return True


def _net_socket_address(sock, peer):
    try:
        if peer:
            return _NET_ORIGINAL_SOCKET_METHODS["getpeername"](sock)
        return _NET_ORIGINAL_SOCKET_METHODS["getsockname"](sock)
    except Exception:
        return None


def _net_local_address_is_loopback(sock):
    return _net_address_is_loopback(_net_socket_address(sock, False))


def _net_peer_address_is_loopback(sock):
    return _net_address_is_loopback(_net_socket_address(sock, True))


def _net_loopback_allows(event, args):
    if event in _NET_LOCAL_EVENTS:
        return True
    if event in _NET_ALWAYS_DENIED_EVENTS:
        return False
    if event in _NET_CONSTRUCTION_EVENTS:
        return _net_construction_allowed(args)
    if event == "socket.getaddrinfo":
        return _net_getaddrinfo_allowed(args)
    sock = _net_first_socket(args)
    if event in _NET_ADDRESS_EVENTS:
        if sock is None or not _net_sock_is_ipv4_stream(sock):
            return False
        for arg in args:
            if isinstance(arg, tuple):
                return _net_address_is_loopback(arg)
        return False
    if event in _NET_LISTENER_EVENTS:
        return (
            sock is not None
            and _net_sock_is_ipv4_stream(sock)
            and _net_local_address_is_loopback(sock)
        )
    if event in _NET_DESCRIPTOR_EVENTS:
        return sock is not None and _net_sock_is_ipv4_stream(sock)
    if event in _NET_DATA_EVENTS:
        return (
            sock is not None
            and _net_sock_is_ipv4_stream(sock)
            and _net_peer_address_is_loopback(sock)
        )
    return False


def _net_thread_scopes():
    scopes = getattr(_NET_THREAD_STATE, "scopes", None)
    if scopes is None:
        scopes = []
        _NET_THREAD_STATE.scopes = scopes
    return scopes


def _net_effective_mode():
    with _NET_LOCK:
        process_mode = _NET_PROCESS_MODE
        depth = _NET_FLOOR_DEPTH
        active_scopes = list(_NET_ACTIVE_SCOPES)
    if process_mode is None and depth <= 0:
        return None
    modes = []
    if process_mode is not None:
        modes.append(process_mode)
    scopes = []
    context_scopes = _NET_CONTEXT_SCOPE.get()
    if context_scopes is not None:
        if isinstance(context_scopes, (tuple, list)):
            scopes.extend(context_scopes)
        else:
            scopes.append(context_scopes)
    scopes.extend(_net_thread_scopes())
    for scope in scopes:
        if getattr(scope, "_entered", True) is False:
            continue
        if not any(scope is active for active in active_scopes):
            continue
        modes.append(scope.mode)
    if "refuse-all" in modes:
        return "refuse-all"
    return "loopback"


def _network_refusal_count():
    with _NET_LOCK:
        return _NET_REFUSAL_COUNT


def _net_record_refusal(event):
    global _NET_REFUSAL_COUNT
    with _NET_LOCK:
        _NET_REFUSAL_COUNT += 1
        scopes = list(_NET_ACTIVE_SCOPES)
        mark = _NET_MARK_VIOLATIONS
    for scope in scopes:
        scope.attempts.append(event)
        if scope.mark_violations:
            mark = True
    if mark:
        try:
            _err(_GUARD_MARKER)
        except Exception:
            pass


def _net_raise_refusal(event):
    _net_record_refusal(event)
    raise PermissionError(_NET_REFUSAL_TEXT)


def _net_operation_address(args, kwargs):
    if args:
        return args[0]
    for value in kwargs.values():
        return value
    return None


def _net_check_operation(event, kind, sock, args, kwargs):
    mode = _net_effective_mode()
    if mode is None:
        return
    if mode == "refuse-all":
        _net_raise_refusal(event)
    if kind == "address":
        allowed = (
            _net_sock_is_ipv4_stream(sock)
            and _net_address_is_loopback(
                _net_operation_address(args, kwargs)
            )
        )
    elif kind == "listener":
        allowed = (
            _net_sock_is_ipv4_stream(sock)
            and _net_local_address_is_loopback(sock)
        )
    elif kind == "descriptor":
        allowed = _net_sock_is_ipv4_stream(sock)
    elif kind in ("data", "handshake", "makefile"):
        allowed = (
            _net_sock_is_ipv4_stream(sock)
            and _net_peer_address_is_loopback(sock)
        )
    else:
        allowed = False
    if not allowed:
        _net_raise_refusal(event)


def _network_audit_hook(event, args):
    if not event.startswith("socket."):
        return
    mode = _net_effective_mode()
    if mode is None:
        return
    if event in _NET_LOCAL_EVENTS:
        return
    if mode != "refuse-all" and _net_loopback_allows(event, args):
        return
    _net_record_refusal(event)
    raise PermissionError(_NET_REFUSAL_TEXT)


def _net_make_operation_wrapper(name, kind, original):
    event = "socket." + name

    def _wrapper(self, *args, **kwargs):
        _net_check_operation(event, kind, self, args, kwargs)
        return original(self, *args, **kwargs)

    _wrapper.__name__ = name
    _wrapper.__qualname__ = name
    return _wrapper


def _net_install_operation_wrappers():
    global _NET_WRAPPERS_INSTALLED
    socket.SocketType = socket.socket
    for name in ("getsockname", "getpeername"):
        if name not in _NET_ORIGINAL_SOCKET_METHODS:
            original = getattr(socket.socket, name, None)
            if original is not None and callable(original):
                _NET_ORIGINAL_SOCKET_METHODS[name] = original
    if _NET_WRAPPERS_INSTALLED:
        return
    for name, kind in _NET_SOCKET_WRAPPERS:
        if name in _NET_ORIGINAL_SOCKET_METHODS:
            continue
        original = getattr(socket.socket, name, None)
        if original is None or not callable(original):
            continue
        _NET_ORIGINAL_SOCKET_METHODS[name] = original
        setattr(
            socket.socket, name,
            _net_make_operation_wrapper(name, kind, original),
        )
    try:
        import ssl
    except Exception:
        ssl = None
    if ssl is not None:
        ssl_socket_class = getattr(ssl, "SSLSocket", None)
        if ssl_socket_class is not None:
            for name in ("getsockname", "getpeername"):
                if name not in _NET_ORIGINAL_SSL_METHODS:
                    original = getattr(ssl_socket_class, name, None)
                    if original is not None and callable(original):
                        _NET_ORIGINAL_SSL_METHODS[name] = original
            for name, kind in _NET_SSL_WRAPPERS:
                if name not in vars(ssl_socket_class):
                    continue
                if name in _NET_ORIGINAL_SSL_METHODS:
                    continue
                original = getattr(ssl_socket_class, name, None)
                if original is None or not callable(original):
                    continue
                _NET_ORIGINAL_SSL_METHODS[name] = original
                setattr(
                    ssl_socket_class, name,
                    _net_make_operation_wrapper(name, kind, original),
                )
    _NET_WRAPPERS_INSTALLED = True


# -- Linux no-argument socketpair bootstrap ---------------------------
# Declared seam: the module attribute ``socket.socketpair`` only, and
# only its exact no-argument call. Installed once at first guard
# activation; imports stay inert and neither socket class changes.

_NET_IS_LINUX = sys.platform.startswith("linux")
_NET_SOCKETPAIR_TIMEOUT = 5.0
_NET_ORIGINAL_SOCKETPAIR = None
_NET_SOCKETPAIR_INSTALLED = False


def _net_tcp_socketpair():
    """Genuine numeric IPv4 TCP pair via guarded public operations."""
    listener = None
    client = None
    server = None
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(_NET_SOCKETPAIR_TIMEOUT)
        port = listener.getsockname()[1]
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(_NET_SOCKETPAIR_TIMEOUT)
        client.connect(("127.0.0.1", port))
        server, _peer = listener.accept()
        listener.close()
        listener = None
        if (
            server.getpeername() != client.getsockname()
            or client.getpeername() != server.getsockname()
        ):
            raise OSError("socketpair peer verification failed")
        client.settimeout(None)
        server.settimeout(None)
        client.set_inheritable(False)
        server.set_inheritable(False)
        pair = (client, server)
        client = None
        server = None
        return pair
    except BaseException:
        for sock in (server, client, listener):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        raise


def _net_socketpair_explicit_allowed(args, kwargs):
    names = ("family", "type", "proto")
    if len(args) > 3:
        return True
    values = {}
    for index, value in enumerate(args):
        values[names[index]] = value
    for key, value in kwargs.items():
        if key not in names or key in values:
            return True
        values[key] = value
    family = values.get("family", -1)
    type_value = values.get("type", socket.SOCK_STREAM)
    proto = values.get("proto", 0)
    family_int = _net_int(family)
    proto_int = _net_int(proto)
    if (
        family_int is None
        or proto_int is None
        or _net_int(type_value) is None
    ):
        return True
    if family_int != int(socket.AF_INET):
        return False
    if not _net_is_stream(type_value):
        return False
    return proto_int in (0, int(socket.IPPROTO_TCP))


def _net_socketpair_wrapper(*args, **kwargs):
    original = _NET_ORIGINAL_SOCKETPAIR
    if original is None:
        raise RuntimeError("socketpair wrapper has no original")
    if not _NET_IS_LINUX:
        return original(*args, **kwargs)
    mode = _net_effective_mode()
    if mode is None:
        return original(*args, **kwargs)
    if not args and not kwargs:
        if mode == "refuse-all":
            _net_raise_refusal("socket.socketpair")
        return _net_tcp_socketpair()
    if mode == "refuse-all":
        _net_raise_refusal("socket.socketpair")
    if not _net_socketpair_explicit_allowed(args, kwargs):
        _net_raise_refusal("socket.socketpair")
    return original(*args, **kwargs)


def _net_install_socketpair_wrapper():
    global _NET_ORIGINAL_SOCKETPAIR, _NET_SOCKETPAIR_INSTALLED
    if _NET_SOCKETPAIR_INSTALLED:
        return
    original = getattr(socket, "socketpair", None)
    if original is None or not callable(original):
        _NET_SOCKETPAIR_INSTALLED = True
        return
    if original is _net_socketpair_wrapper:
        _NET_SOCKETPAIR_INSTALLED = True
        return
    _NET_ORIGINAL_SOCKETPAIR = original
    socket.socketpair = _net_socketpair_wrapper
    _NET_SOCKETPAIR_INSTALLED = True


def _ensure_network_audit_hook():
    global _NET_HOOK_INSTALLED
    if (
        _NET_HOOK_INSTALLED
        and _NET_WRAPPERS_INSTALLED
        and _NET_SOCKETPAIR_INSTALLED
    ):
        return
    with _NET_INSTALL_LOCK:
        _net_install_operation_wrappers()
        _net_install_socketpair_wrapper()
        if not _NET_HOOK_INSTALLED:
            sys.addaudithook(_network_audit_hook)
            _NET_HOOK_INSTALLED = True


class _NetworkGuardScope:
    """Scoped policy over the one shared process audit floor."""

    def __init__(self, mode, mark_violations=False):
        if mode not in ("refuse-all", "loopback"):
            raise ValueError("unknown guard mode")
        self.mode = mode
        self.mark_violations = bool(mark_violations)
        self.attempts = []
        self._entered = False
        self._token = None

    @property
    def refusal_count(self):
        return len(self.attempts)

    def __enter__(self):
        global _NET_FLOOR_DEPTH
        if self._entered:
            raise RuntimeError("network guard scope already entered")
        _ensure_network_audit_hook()
        with _NET_LOCK:
            _NET_FLOOR_DEPTH += 1
            _NET_ACTIVE_SCOPES.append(self)
        _net_thread_scopes().append(self)
        current = _NET_CONTEXT_SCOPE.get()
        if current is None:
            current = ()
        elif not isinstance(current, (tuple, list)):
            current = (current,)
        self._token = _NET_CONTEXT_SCOPE.set(tuple(current) + (self,))
        self._entered = True
        return self

    def __exit__(self, *exc_info):
        global _NET_FLOOR_DEPTH
        if not self._entered:
            return False
        self._entered = False
        if self._token is not None:
            try:
                _NET_CONTEXT_SCOPE.reset(self._token)
            except ValueError:
                _NET_CONTEXT_SCOPE.set(())
            self._token = None
        scopes = _net_thread_scopes()
        for index in range(len(scopes) - 1, -1, -1):
            if scopes[index] is self:
                del scopes[index]
                break
        with _NET_LOCK:
            for index in range(len(_NET_ACTIVE_SCOPES) - 1, -1, -1):
                if _NET_ACTIVE_SCOPES[index] is self:
                    del _NET_ACTIVE_SCOPES[index]
                    break
            _NET_FLOOR_DEPTH = max(0, _NET_FLOOR_DEPTH - 1)
        return False


def _network_guard(mode, mark_violations=False):
    return _NetworkGuardScope(mode, mark_violations=mark_violations)


def _activate_network_floor(mode="loopback", mark_violations=False):
    """Persistently activate the floor for this (child) process."""
    global _NET_PROCESS_MODE, _NET_MARK_VIOLATIONS, _NET_FLOOR_DEPTH
    if mode not in ("refuse-all", "loopback"):
        raise ValueError("unknown guard mode")
    _ensure_network_audit_hook()
    with _NET_LOCK:
        _NET_PROCESS_MODE = mode
        _NET_MARK_VIOLATIONS = bool(mark_violations)
        _NET_FLOOR_DEPTH = max(1, _NET_FLOOR_DEPTH)


def _load_fixture_json(name):
    return json.loads(
        (_FIXTURES_DIR / name).read_text(encoding="utf-8")
    )


def _expected_result():
    """The accepted four-field result for the genuine fixtures."""
    global _EXPECTED_RESULT
    if _EXPECTED_RESULT is None:
        from teacher_demo import meta_protocol

        stream = _load_fixture_json("meta_stream_inputs.json")
        _EXPECTED_RESULT = {
            "raw": stream["raw"],
            "model_name": meta_protocol.MODEL,
            "response_id": stream["response_id"],
            "outcome": "ok",
        }
    return copy.deepcopy(_EXPECTED_RESULT)


def _fixture_credential():
    global _FIXTURE_CREDENTIAL
    if _FIXTURE_CREDENTIAL is None:
        _FIXTURE_CREDENTIAL = _load_fixture_json(
            "meta_stream_inputs.json"
        )["synthetic_credential"]
    return _FIXTURE_CREDENTIAL


def _frame_bytes(value):
    text = json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    )
    return text.encode("utf-8") + b"\n"


def _success_frame():
    return _frame_bytes({"result": _expected_result()})


def _out(data):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _err(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    sys.stderr.buffer.write(data)
    sys.stderr.buffer.flush()


def _drain_stdin():
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass


def _install_socket_guard(allow_loopback, mark_violations):
    """Refuse non-loopback (or all) network use in this process."""
    _activate_network_floor(
        "loopback" if allow_loopback else "refuse-all",
        mark_violations=mark_violations,
    )


def _run_real_child(args):
    """Real product __main__ under loopback guard + TLS seams."""
    if len(args) != 1 or not args[0].isascii() or not args[0].isdigit():
        _err("fixture port argument is invalid\n")
        return 64
    port = int(args[0])
    if not 1 <= port <= 65535:
        _err("fixture port argument is invalid\n")
        return 64
    _install_socket_guard(allow_loopback=True, mark_violations=True)
    import test_meta_http

    with test_meta_http._client_seams(port, True):
        runpy.run_path(str(_PRODUCT_SCRIPT), run_name="__main__")
    return 0


def _run_real_guarded(args):
    """Real product __main__ with every network operation refused."""
    if args:
        _err("guard-bootstrap mode takes no arguments\n")
        return 64
    _install_socket_guard(allow_loopback=False, mark_violations=False)
    runpy.run_path(str(_PRODUCT_SCRIPT), run_name="__main__")
    return 0


def _wait_barrier(ready_path, release_path):
    Path(ready_path).write_text("ready", encoding="ascii")
    deadline = time.monotonic() + _BARRIER_WAIT_SECONDS
    release = Path(release_path)
    while time.monotonic() < deadline:
        if release.exists():
            return
        time.sleep(0.01)
    raise SystemExit(9)


def _barrier_paths(args):
    if len(args) != 2:
        _err("barrier mode needs ready and release paths\n")
        raise SystemExit(64)
    return args[0], args[1]


def _flood(stream, total):
    chunk = b"A" * 8192
    written = 0
    try:
        while written < total:
            piece = chunk[: min(len(chunk), total - written)]
            stream.write(piece)
            stream.flush()
            written += len(piece)
    except (BrokenPipeError, OSError):
        pass


# -- synthetic single-shot process-protocol modes --------------------
# Each consumes stdin to EOF first (like the real child), then emits
# exactly what its label says. None of these is a Meta response.


def _syn_valid_success(_args):
    _drain_stdin()
    _out(_success_frame())
    return 0


def _syn_failure(args):
    if len(args) != 1 or args[0] not in _CLOSED_FAILURES:
        _err("synthetic failure literal is not closed\n")
        return 64
    _drain_stdin()
    _out(_frame_bytes({"failure": args[0]}))
    return 0


def _syn_empty(_args):
    _drain_stdin()
    return 0


def _syn_malformed(_args):
    _drain_stdin()
    _out(b"not-json\n")
    return 0


def _syn_toplevel_array(_args):
    _drain_stdin()
    _out(b"[1,2]\n")
    return 0


def _syn_missing_newline(_args):
    _drain_stdin()
    _out(_success_frame()[:-1])
    return 0


def _syn_double_newline(_args):
    _drain_stdin()
    _out(_success_frame() + b"\n")
    return 0


def _syn_prefix_byte(_args):
    _drain_stdin()
    _out(b" " + _success_frame())
    return 0


def _syn_trailing_byte(_args):
    _drain_stdin()
    _out(_success_frame() + b"X")
    return 0


def _syn_duplicate_frame(_args):
    _drain_stdin()
    _out(_success_frame() + _success_frame())
    return 0


def _syn_partial_frame(_args):
    _drain_stdin()
    frame = _success_frame()
    _out(frame[: len(frame) // 2] + b"\n")
    return 0


def _variant_result(mutate):
    result = _expected_result()
    mutate(result)
    return _frame_bytes({"result": result})


def _syn_wrong_model(_args):
    _drain_stdin()
    _out(_variant_result(lambda r: r.__setitem__("model_name", "other")))
    return 0


def _syn_blank_id(_args):
    _drain_stdin()
    _out(_variant_result(lambda r: r.__setitem__("response_id", "  ")))
    return 0


def _syn_missing_id(_args):
    _drain_stdin()
    _out(_variant_result(lambda r: r.pop("response_id")))
    return 0


def _syn_raw_nondict(_args):
    _drain_stdin()
    _out(_variant_result(lambda r: r.__setitem__("raw", [1, 2])))
    return 0


def _syn_outcome_not_ok(_args):
    _drain_stdin()
    _out(_variant_result(lambda r: r.__setitem__("outcome", "error")))
    return 0


def _syn_extra_result_field(_args):
    _drain_stdin()
    _out(_variant_result(lambda r: r.__setitem__("extra", 1)))
    return 0


def _syn_missing_raw(_args):
    _drain_stdin()
    _out(_variant_result(lambda r: r.pop("raw")))
    return 0


def _syn_extra_enclosing(_args):
    _drain_stdin()
    _out(_frame_bytes({"result": _expected_result(), "extra": 1}))
    return 0


def _syn_both_keys(_args):
    _drain_stdin()
    _out(_frame_bytes(
        {"result": _expected_result(), "failure": "error"}
    ))
    return 0


def _syn_failure_timeout_literal(_args):
    _drain_stdin()
    _out(_frame_bytes({"failure": "timeout"}))
    return 0


def _syn_failure_bad_literal(_args):
    _drain_stdin()
    _out(_frame_bytes({"failure": "boom"}))
    return 0


def _syn_dup_key(_args):
    _drain_stdin()
    inner = json.dumps(
        _expected_result(), ensure_ascii=False, separators=(",", ":")
    )
    text = '{"result":' + inner + ',"result":' + inner + "}"
    _out(text.encode("utf-8") + b"\n")
    return 0


def _manual_success(raw_json_text):
    from teacher_demo import meta_protocol

    stream = _load_fixture_json("meta_stream_inputs.json")
    text = (
        '{"result":{"raw":' + raw_json_text
        + ',"model_name":"' + meta_protocol.MODEL
        + '","response_id":"' + stream["response_id"]
        + '","outcome":"ok"}}'
    )
    return text.encode("utf-8") + b"\n"


def _syn_nonfinite(_args):
    _drain_stdin()
    _out(_manual_success('{"x":Infinity}'))
    return 0


def _syn_surrogate(_args):
    _drain_stdin()
    _out(_manual_success('{"x":"\\ud800"}'))
    return 0


def _syn_invalid_utf8(_args):
    _drain_stdin()
    head = _manual_success('{"x":""}')[:-1]
    marker = b'{"x":"'
    index = head.index(marker) + len(marker)
    _out(head[:index] + b"\xff" + head[index:] + b"\n")
    return 0


def _syn_credential_success(_args):
    _drain_stdin()
    result = _expected_result()
    result["raw"] = {"note": _fixture_credential()}
    _out(_frame_bytes({"result": result}))
    return 0


def _syn_credential_stderr(_args):
    _drain_stdin()
    _out(_success_frame())
    _err(_fixture_credential() + "\n")
    return 0


def _syn_stderr_marker(_args):
    _drain_stdin()
    _out(_success_frame())
    _err(_DIAGNOSTIC_MARKER)
    return 0


def _syn_nonzero_after_frame(_args):
    _drain_stdin()
    _out(_success_frame())
    return 3


# -- synthetic flow / bound modes -------------------------------------


def _syn_stdout_flood(_args):
    _flood(sys.stdout.buffer, 256 * 1024)
    return 0


def _syn_stderr_flood(_args):
    _drain_stdin()
    _out(_success_frame())
    _flood(sys.stderr.buffer, 256 * 1024)
    return 0


def _syn_valid_then_flood(_args):
    _drain_stdin()
    _out(_success_frame())
    _flood(sys.stdout.buffer, 128 * 1024)
    return 0


def _trickle(data, pause=0.001):
    buffer = sys.stdout.buffer
    for index in range(len(data)):
        buffer.write(data[index : index + 1])
        buffer.flush()
        time.sleep(pause)


def _syn_trickle_success(_args):
    _drain_stdin()
    _trickle(_success_frame())
    return 0


# -- synthetic barrier modes (ready-file, release-file) ---------------


def _syn_barrier_clean_success(args):
    ready, release = _barrier_paths(args)
    _drain_stdin()
    _out(_success_frame())
    _wait_barrier(ready, release)
    return 0


def _syn_barrier_late_exit(args):
    ready, release = _barrier_paths(args)
    _drain_stdin()
    _out(_success_frame())
    _wait_barrier(ready, release)
    return 3


def _syn_barrier_late_stdout(args):
    ready, release = _barrier_paths(args)
    _drain_stdin()
    _out(_success_frame())
    _wait_barrier(ready, release)
    _out(b"X")
    return 0


def _syn_barrier_late_stderr(args):
    ready, release = _barrier_paths(args)
    _drain_stdin()
    _out(_success_frame())
    _wait_barrier(ready, release)
    _err(_DIAGNOSTIC_MARKER)
    return 0


def _syn_stall(args):
    ready, release = _barrier_paths(args)
    _drain_stdin()
    _wait_barrier(ready, release)
    return 0


def _syn_stdin_never_read(args):
    ready, release = _barrier_paths(args)
    _wait_barrier(ready, release)
    return 0


def _syn_trickle_then_stall(args):
    ready, release = _barrier_paths(args)
    _drain_stdin()
    frame = _success_frame()
    half = len(frame) // 2
    _trickle(frame[:half], pause=0.002)
    _wait_barrier(ready, release)
    _out(frame[half:])
    return 0
def _syn_stdout_pending_hold(args):
    if len(args) != 3 or any(
        not isinstance(item, str) or not item for item in args
    ):
        raise SystemExit(64)
    ready, output_go, release = args
    _wait_barrier(ready, output_go)
    result = _expected_result()
    try:
        sections = result["raw"]["sections"]
    except Exception:
        raise SystemExit(65) from None
    enlarged = False
    if isinstance(sections, list):
        for section in sections:
            if isinstance(section, dict) and isinstance(
                section.get("text"), str
            ):
                section["text"] = section["text"] + ("x" * 8192)
                enlarged = True
                break
    if not enlarged:
        raise SystemExit(65)
    complete = _frame_bytes({"result": result})
    run_start = complete.find(b"x" * 256)
    prefix_end = run_start + 2048 if run_start >= 0 else -1
    if (
        run_start < 0
        or prefix_end <= 1024
        or prefix_end >= len(complete)
        or complete[prefix_end - 1:prefix_end] != b"x"
        or complete[prefix_end:prefix_end + 1] != b"x"
    ):
        raise SystemExit(65)
    try:
        _out(complete[:prefix_end])
    except OSError:
        return 0
    _wait_barrier(ready, release)
    return 0


def _syn_stderr_pending_hold(args):
    ready, release = _barrier_paths(args)
    if not ready or not release:
        raise SystemExit(64)
    Path(ready).write_text("ready", encoding="ascii")
    _drain_stdin()
    try:
        _out(_success_frame())
        _err(b"synthetic-stderr:" + b"x" * 512 + b"\n")
    except OSError:
        return 0
    _wait_barrier(ready, release)
    return 0
_MODES = {
    "real_child": _run_real_child,
    "real_guarded": _run_real_guarded,
    "syn_valid_success": _syn_valid_success,
    "syn_failure": _syn_failure,
    "syn_empty": _syn_empty,
    "syn_malformed": _syn_malformed,
    "syn_toplevel_array": _syn_toplevel_array,
    "syn_missing_newline": _syn_missing_newline,
    "syn_double_newline": _syn_double_newline,
    "syn_prefix_byte": _syn_prefix_byte,
    "syn_trailing_byte": _syn_trailing_byte,
    "syn_duplicate_frame": _syn_duplicate_frame,
    "syn_partial_frame": _syn_partial_frame,
    "syn_wrong_model": _syn_wrong_model,
    "syn_blank_id": _syn_blank_id,
    "syn_missing_id": _syn_missing_id,
    "syn_raw_nondict": _syn_raw_nondict,
    "syn_outcome_not_ok": _syn_outcome_not_ok,
    "syn_extra_result_field": _syn_extra_result_field,
    "syn_missing_raw": _syn_missing_raw,
    "syn_extra_enclosing": _syn_extra_enclosing,
    "syn_both_keys": _syn_both_keys,
    "syn_failure_timeout_literal": _syn_failure_timeout_literal,
    "syn_failure_bad_literal": _syn_failure_bad_literal,
    "syn_dup_key": _syn_dup_key,
    "syn_nonfinite": _syn_nonfinite,
    "syn_surrogate": _syn_surrogate,
    "syn_invalid_utf8": _syn_invalid_utf8,
    "syn_credential_success": _syn_credential_success,
    "syn_credential_stderr": _syn_credential_stderr,
    "syn_stderr_marker": _syn_stderr_marker,
    "syn_nonzero_after_frame": _syn_nonzero_after_frame,
    "syn_stdout_flood": _syn_stdout_flood,
    "syn_stderr_flood": _syn_stderr_flood,
    "syn_valid_then_flood": _syn_valid_then_flood,
    "syn_trickle_success": _syn_trickle_success,
    "syn_barrier_clean_success": _syn_barrier_clean_success,
    "syn_barrier_late_exit": _syn_barrier_late_exit,
    "syn_barrier_late_stdout": _syn_barrier_late_stdout,
    "syn_barrier_late_stderr": _syn_barrier_late_stderr,
    "syn_stall": _syn_stall,
    "syn_stdin_never_read": _syn_stdin_never_read,
    "syn_trickle_then_stall": _syn_trickle_then_stall,
    "syn_stdout_pending_hold": _syn_stdout_pending_hold,
    "syn_stderr_pending_hold": _syn_stderr_pending_hold,
}


def _main(argv):
    if len(argv) < 2:
        _err("fixture mode is required\n")
        return 64
    handler = _MODES.get(argv[1])
    if handler is None:
        _err("fixture mode is unknown\n")
        return 64
    if argv[1] not in ("real_child", "real_guarded"):
        _install_socket_guard(allow_loopback=False, mark_violations=False)
    result = handler(argv[2:])
    return 0 if result is None else int(result)


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
