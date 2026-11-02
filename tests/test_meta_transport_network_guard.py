"""Regressions for the Linux no-argument socketpair test bootstrap.

Every guard behavior runs in a fresh isolated interpreter process
(``-I -B``, ``stdin=DEVNULL``, sanitized allowlist environment,
finite timeout, owned ``subprocess.run`` cleanup) because audit
hooks and wrappers are irreversible within a process. The parent
test process never activates the guard itself.

Platform labeling is explicit. On genuine Linux the pair, explicit,
nesting, trap, and cleanup children run the Linux branch as-is, and
the Runner child proves the real blocked-loop wake. Elsewhere those
children patch ``_NET_IS_LINUX`` for classification-only coverage of
the same branch; that patch is labeled here and in each affected
test and is never presented as genuine-Linux proof. The Runner case
has no patched substitute: off Linux it is reported as skipped.

No child attempts an external connection. Forbidden targets use the
documentation address 192.0.2.1 behind an independent native trap,
so even a broken guard cannot transmit.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent
_GENUINE_LINUX = sys.platform.startswith("linux")
_BRANCH_MODE = "genuine" if _GENUINE_LINUX else "patched"
_CHILD_TIMEOUT_SECONDS = 30
_ALLOWED_ENV_KEYS = (
    "COMSPEC", "HOME", "PATH", "PATHEXT", "SystemRoot",
    "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "USERPROFILE",
    "WINDIR", "windir",
)


def _child_env():
    env = {
        key: os.environ[key]
        for key in _ALLOWED_ENV_KEYS
        if key in os.environ
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


_CHILD_PREAMBLE = r'''
import sys
sys.path.insert(0, sys.argv[1])
import os
import socket
import meta_transport_child_fixture as fx


def apply_branch_mode():
    mode = sys.argv[2] if len(sys.argv) > 2 else "none"
    if mode == "patched":
        assert not sys.platform.startswith("linux")
        fx._NET_IS_LINUX = True
    elif mode == "genuine":
        assert sys.platform.startswith("linux")
        assert fx._NET_IS_LINUX is True
    elif mode != "none":
        raise AssertionError("unknown branch mode")
    return mode


def expect_permission(fn):
    try:
        result = fn()
    except PermissionError:
        return
    try:
        if isinstance(result, tuple):
            for item in result:
                close = getattr(item, "close", None)
                if callable(close):
                    close()
        else:
            close = getattr(result, "close", None)
            if callable(close):
                close()
    finally:
        raise AssertionError("expected PermissionError")

'''

_INERTIA_SCRIPT = r'''
import sys
sys.path.insert(0, sys.argv[1])
import socket
import ssl
orig_pair = socket.socketpair
orig_socket_class = socket.socket
orig_send = socket.socket.send
orig_connect = socket.socket.connect
orig_ssl_class = ssl.SSLSocket
orig_timeout = socket.getdefaulttimeout()
import meta_transport_child_fixture as fx
assert socket.socketpair is orig_pair
assert socket.socket is orig_socket_class
assert socket.socket.send is orig_send
assert socket.socket.connect is orig_connect
assert ssl.SSLSocket is orig_ssl_class
assert fx._NET_HOOK_INSTALLED is False
assert fx._NET_WRAPPERS_INSTALLED is False
assert fx._NET_SOCKETPAIR_INSTALLED is False
assert fx._net_effective_mode() is None
a, b = socket.socketpair()
try:
    a.sendall(b"x")
    assert b.recv(1) == b"x"
finally:
    a.close()
    b.close()
with fx._network_guard("loopback"):
    assert socket.socketpair is fx._net_socketpair_wrapper
    assert socket.socket is orig_socket_class
    assert ssl.SSLSocket is orig_ssl_class
    assert socket.socket.send is not orig_send
    wrapped_pair = socket.socketpair
    wrapped_send = socket.socket.send
    wrapped_connect = socket.socket.connect
assert fx._net_effective_mode() is None
with fx._network_guard("loopback"):
    assert socket.socketpair is wrapped_pair
    assert socket.socket.send is wrapped_send
    assert socket.socket.connect is wrapped_connect
    assert socket.socket is orig_socket_class
    assert ssl.SSLSocket is orig_ssl_class
assert socket.getdefaulttimeout() == orig_timeout
a, b = socket.socketpair()
try:
    b.sendall(b"y")
    assert a.recv(1) == b"y"
finally:
    a.close()
    b.close()
print("ok")
'''

_RUNNER_SCRIPT = r'''
import sys
sys.path.insert(0, sys.argv[1])
assert sys.platform.startswith("linux")
import asyncio
import threading
import time
import meta_transport_child_fixture as fx
assert fx._NET_IS_LINUX is True


async def answer():
    return 42


with fx._network_guard("loopback"):
    with asyncio.Runner() as runner:
        assert runner.run(answer()) == 42
    started = threading.Event()
    state = {}
    errors = []

    def worker():
        try:
            async def blocked():
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                state["loop"] = loop
                state["future"] = future
                started.set()
                return await future

            with asyncio.Runner() as worker_runner:
                state["value"] = worker_runner.run(blocked())
        except BaseException as exc:
            errors.append(repr(exc))
            started.set()

    thread = threading.Thread(target=worker)
    thread.start()
    assert started.wait(10)
    assert not errors, errors
    time.sleep(0.3)
    state["loop"].call_soon_threadsafe(state["future"].set_result, 7)
    thread.join(10)
    assert not thread.is_alive()
    assert not errors, errors
    assert state.get("value") == 7
print("ok")
'''

_PAIR_BODY = r'''
branch = apply_branch_mode()
before_timeout = socket.getdefaulttimeout()
with fx._network_guard("loopback"):
    _genuine_guarded_listen = socket.socket.listen
    _observed_listeners = []

    def _observing_listen(self, *args, **kwargs):
        _observed_listeners.append(self)
        return _genuine_guarded_listen(self, *args, **kwargs)

    try:
        socket.socket.listen = _observing_listen
        a, b = socket.socketpair()
    finally:
        socket.socket.listen = _genuine_guarded_listen
    try:
        assert len(_observed_listeners) == 1
        assert _observed_listeners[0].fileno() == -1
        assert isinstance(a, socket.socket)
        assert isinstance(b, socket.socket)
        assert a.family == socket.AF_INET
        assert b.family == socket.AF_INET
        assert fx._net_is_stream(a.type)
        assert fx._net_is_stream(b.type)
        assert a.getpeername() == b.getsockname()
        assert b.getpeername() == a.getsockname()
        assert a.getsockname()[0] == "127.0.0.1"
        assert b.getsockname()[0] == "127.0.0.1"
        assert a.gettimeout() is None
        assert b.gettimeout() is None
        assert a.get_inheritable() is False
        assert b.get_inheritable() is False
        a.sendall(b"ping")
        assert b.recv(4) == b"ping"
        b.sendall(b"pong!")
        assert a.recv(5) == b"pong!"
        a.sendall(b"end")
        a.close()
        assert b.recv(3) == b"end"
        assert b.recv(1) == b""
    finally:
        a.close()
        b.close()
assert socket.getdefaulttimeout() == before_timeout
print("ok")
'''

_EXPLICIT_BODY = r'''
branch = apply_branch_mode()


def expect_delegated(fn):
    try:
        a, b = fn()
    except PermissionError:
        raise AssertionError("wrapper refused delegated call")
    except OSError:
        return
    try:
        assert a.family == socket.AF_INET
        a.sendall(b"d")
        assert b.recv(1) == b"d"
    finally:
        a.close()
        b.close()


with fx._network_guard("loopback"):
    pass
assert fx._net_effective_mode() is None
a, b = socket.socketpair()
try:
    if branch == "genuine":
        assert a.family == socket.AF_UNIX
    a.sendall(b"i")
    assert b.recv(1) == b"i"
finally:
    a.close()
    b.close()
with fx._network_guard("loopback"):
    if hasattr(socket, "AF_UNIX"):
        expect_permission(
            lambda: socket.socketpair(
                socket.AF_UNIX, socket.SOCK_STREAM))
        expect_permission(
            lambda: socket.socketpair(family=socket.AF_UNIX))
    expect_permission(
        lambda: socket.socketpair(
            socket.AF_INET6, socket.SOCK_STREAM))
    expect_permission(
        lambda: socket.socketpair(
            socket.AF_INET, socket.SOCK_DGRAM))
    expect_permission(
        lambda: socket.socketpair(
            socket.AF_INET, socket.SOCK_STREAM,
            socket.IPPROTO_UDP))
    expect_permission(lambda: socket.socketpair(-1))
    expect_permission(
        lambda: socket.socketpair(type=socket.SOCK_STREAM))
    expect_delegated(
        lambda: socket.socketpair(
            socket.AF_INET, socket.SOCK_STREAM))
    expect_delegated(
        lambda: socket.socketpair(
            socket.AF_INET, socket.SOCK_STREAM, 0))
    a, b = socket.socketpair()
    try:
        assert a.family == socket.AF_INET
    finally:
        a.close()
        b.close()
print("ok")
'''

_NESTED_BODY = r'''
branch = apply_branch_mode()
import threading


def healthy_pair():
    a, b = socket.socketpair()
    try:
        assert a.family == socket.AF_INET
        a.sendall(b"h")
        assert b.recv(1) == b"h"
    finally:
        a.close()
        b.close()


with fx._network_guard("refuse-all"):
    expect_permission(lambda: socket.socketpair())
    expect_permission(
        lambda: socket.socketpair(
            socket.AF_INET, socket.SOCK_STREAM))
    expect_permission(
        lambda: socket.socket(
            socket.AF_INET, socket.SOCK_STREAM))
with fx._network_guard("loopback"):
    healthy_pair()
    with fx._network_guard("refuse-all"):
        expect_permission(lambda: socket.socketpair())
    healthy_pair()
with fx._network_guard("refuse-all"):
    with fx._network_guard("loopback"):
        expect_permission(lambda: socket.socketpair())
    expect_permission(lambda: socket.socketpair())
with fx._network_guard("loopback"):
    healthy_pair()
    errors = []

    def worker():
        try:
            healthy_pair()
        except BaseException as exc:
            errors.append(repr(exc))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(10)
    assert not thread.is_alive()
    assert not errors, errors
assert fx._net_effective_mode() is None
print("ok")
'''

_TRAP_SETUP = r'''
branch = apply_branch_mode()
trapped = []
real_connect = socket.socket.connect
real_connect_ex = socket.socket.connect_ex
real_send = socket.socket.send
real_sendall = socket.socket.sendall
real_sendto = socket.socket.sendto
real_getpeername = socket.socket.getpeername


def is_loopback_address(address):
    return (
        isinstance(address, tuple)
        and len(address) >= 2
        and address[0] in ("127.0.0.1", b"127.0.0.1")
    )


def peer_is_loopback(sock):
    try:
        return is_loopback_address(real_getpeername(sock))
    except Exception:
        return False


def trap_connect(self, address):
    if is_loopback_address(address):
        return real_connect(self, address)
    trapped.append("connect")
    raise AssertionError("forbidden connect reached trap")


def trap_connect_ex(self, address):
    if is_loopback_address(address):
        return real_connect_ex(self, address)
    trapped.append("connect_ex")
    raise AssertionError("forbidden connect_ex reached trap")


def trap_send(self, *args, **kwargs):
    if peer_is_loopback(self):
        return real_send(self, *args, **kwargs)
    trapped.append("send")
    raise AssertionError("forbidden send reached trap")


def trap_sendall(self, *args, **kwargs):
    if peer_is_loopback(self):
        return real_sendall(self, *args, **kwargs)
    trapped.append("sendall")
    raise AssertionError("forbidden sendall reached trap")


def trap_sendto(self, *args, **kwargs):
    trapped.append("sendto")
    raise AssertionError("sendto reached trap")


socket.socket.connect = trap_connect
socket.socket.connect_ex = trap_connect_ex
socket.socket.send = trap_send
socket.socket.sendall = trap_sendall
socket.socket.sendto = trap_sendto
udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
unconnected = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    ipv6_sock = socket.socket(
        socket.AF_INET6, socket.SOCK_STREAM)
except Exception:
    ipv6_sock = None
pair_a = None
pair_b = None
'''

_TRAP_CHECKS = r'''
try:
    with fx._network_guard("loopback"):
        if hasattr(socket, "AF_UNIX"):
            expect_permission(
                lambda: socket.socket(
                    socket.AF_UNIX, socket.SOCK_STREAM))
        expect_permission(
            lambda: socket.socket(
                socket.AF_INET6, socket.SOCK_STREAM))
        expect_permission(
            lambda: socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM))
        probe = socket.socket(
            socket.AF_INET, socket.SOCK_STREAM)
        try:
            expect_permission(
                lambda: probe.connect(("192.0.2.1", 9)))
            expect_permission(
                lambda: probe.connect(("localhost", 9)))
            expect_permission(
                lambda: probe.connect_ex(("192.0.2.1", 9)))
        finally:
            probe.close()
        if ipv6_sock is not None:
            expect_permission(
                lambda: ipv6_sock.connect(("::1", 9, 0, 0)))
        expect_permission(
            lambda: socket.getaddrinfo(
                "127.0.0.1", 9, socket.AF_INET6,
                socket.SOCK_STREAM))
        infos = socket.getaddrinfo(
            "127.0.0.1", 9, socket.AF_INET,
            socket.SOCK_STREAM)
        assert infos
        expect_permission(
            lambda: udp.sendto(b"x", ("127.0.0.1", 9)))
        expect_permission(lambda: unconnected.send(b"x"))
        expect_permission(lambda: unconnected.recv(1))
        pair_a, pair_b = socket.socketpair()
        pair_a.sendall(b"a")
        assert pair_b.recv(1) == b"a"
        with fx._network_guard("refuse-all"):
            expect_permission(lambda: pair_a.send(b"x"))
            expect_permission(lambda: pair_a.sendall(b"x"))
            expect_permission(lambda: pair_b.recv(1))
        pair_a.sendall(b"b")
        assert pair_b.recv(1) == b"b"
    assert trapped == [], trapped
finally:
    for sock in (pair_a, pair_b, udp, unconnected, ipv6_sock):
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
print("ok")
'''

_PARTIAL_BODY = r'''
branch = apply_branch_mode()


def healthy():
    a, b = socket.socketpair()
    try:
        a.sendall(b"h")
        assert b.recv(1) == b"h"
    finally:
        a.close()
        b.close()


with fx._network_guard("loopback"):
    healthy()
    saved_listen = socket.socket.listen
    saved_connect = socket.socket.connect
    seen = {}

    def recording_listen(self, *args, **kwargs):
        seen["listener"] = self
        return saved_listen(self, *args, **kwargs)

    def failing_connect(self, *args, **kwargs):
        seen["client"] = self
        saved_connect(self, *args, **kwargs)
        raise OSError("injected connect failure")

    socket.socket.listen = recording_listen
    socket.socket.connect = failing_connect
    try:
        try:
            socket.socketpair()
        except OSError as exc:
            assert "injected connect failure" in str(exc)
        else:
            raise AssertionError("expected injected failure")
    finally:
        socket.socket.listen = saved_listen
        socket.socket.connect = saved_connect
    assert socket.socket.listen is saved_listen
    assert socket.socket.connect is saved_connect
    assert seen["listener"].fileno() == -1
    assert seen["client"].fileno() == -1
    healthy()
    foreign_a, foreign_b = socket.socketpair()
    saved_accept = socket.socket.accept

    def mismatch_accept(self, *args, **kwargs):
        real_sock, _addr = saved_accept(self, *args, **kwargs)
        peer = foreign_a.getpeername()
        real_sock.close()
        return foreign_a, peer

    socket.socket.accept = mismatch_accept
    try:
        try:
            socket.socketpair()
        except OSError as exc:
            assert "peer verification" in str(exc)
        else:
            raise AssertionError("expected peer mismatch")
    finally:
        socket.socket.accept = saved_accept
    assert socket.socket.accept is saved_accept
    assert foreign_a.fileno() == -1
    foreign_b.close()
    healthy()
print("ok")
'''

_DISPATCH_SCRIPT = r'''
import sys
sys.path.insert(0, sys.argv[1])
mode = sys.argv[2]
expected = sys.argv[3]
import socket
import meta_transport_child_fixture as fx
assert fx._net_effective_mode() is None
if expected == "unknown":
    assert mode not in fx._MODES
    assert fx._main(["prog", mode]) == 64
    assert fx._net_effective_mode() is None
    print("ok")
    raise SystemExit(0)
assert mode in fx._MODES


def probe(args):
    if expected == "refuse-all":
        assert fx._net_effective_mode() == "refuse-all"
        try:
            sock = socket.socket(
                socket.AF_INET, socket.SOCK_STREAM)
        except PermissionError:
            pass
        else:
            sock.close()
            raise AssertionError("construction allowed")
        try:
            pair = socket.socketpair()
        except PermissionError:
            pass
        else:
            pair[0].close()
            pair[1].close()
            raise AssertionError("socketpair allowed")
    elif expected == "inactive":
        assert fx._net_effective_mode() is None
    else:
        raise AssertionError("bad expected value")
    return 0


fx._MODES[mode] = probe
assert fx._main(["prog", mode]) == 0
print("ok")
'''


class MetaTransportNetworkGuardTests(unittest.TestCase):
    """Subprocess-isolated guard regressions (see module notes)."""

    def _run_child(self, script, *args):
        command = [
            sys.executable, "-I", "-B", "-c", script,
            str(_TESTS_DIR), *args,
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_CHILD_TIMEOUT_SECONDS,
                env=_child_env(),
                cwd=str(_REPO_ROOT),
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            self.fail(
                "guard child timed out: "
                + repr((exc.stdout, exc.stderr))
            )
        self.assertEqual(
            completed.returncode, 0,
            "guard child failed\nstdout:\n"
            + completed.stdout
            + "\nstderr:\n"
            + completed.stderr,
        )
        return completed

    def test_import_inertia_and_stable_identities(self):
        self._run_child(_INERTIA_SCRIPT)

    @unittest.skipUnless(
        _GENUINE_LINUX,
        "genuine Linux Runner case requires Linux; "
        "run on Ubuntu, never by platform spoofing",
    )
    def test_genuine_linux_runner_and_threadsafe_wake(self):
        self._run_child(_RUNNER_SCRIPT)

    def test_tcp_pair_bytes_eof_and_no_listener(self):
        # Genuine branch on Linux; classification-only patch else.
        self._run_child(
            _CHILD_PREAMBLE + _PAIR_BODY, _BRANCH_MODE)

    def test_inactive_and_explicit_socketpair_calls(self):
        # Genuine branch on Linux; classification-only patch else.
        # Allowed explicit AF_INET calls delegate to the stock
        # callable, whose success is platform-defined; the child
        # fails only if the wrapper itself refuses them.
        self._run_child(
            _CHILD_PREAMBLE + _EXPLICIT_BODY, _BRANCH_MODE)

    def test_refuse_all_nesting_and_restoration(self):
        # Genuine branch on Linux; classification-only patch else.
        self._run_child(
            _CHILD_PREAMBLE + _NESTED_BODY, _BRANCH_MODE)

    def test_forbidden_targets_denied_without_transmit(self):
        # Genuine branch on Linux; classification-only patch else.
        # The child trap sits under the guard wrappers and blocks
        # any forbidden native operation the guard might miss.
        self._run_child(
            _CHILD_PREAMBLE + _TRAP_SETUP + _TRAP_CHECKS,
            _BRANCH_MODE)

    def test_pair_partial_failure_and_peer_mismatch_cleanup(self):
        # Genuine branch on Linux; classification-only patch else.
        # Seams wrap the installed guard wrappers, so guard checks
        # still run; only the observed operation then fails.
        self._run_child(
            _CHILD_PREAMBLE + _PARTIAL_BODY, _BRANCH_MODE)

    def test_synthetic_dispatch_installs_refuse_all(self):
        if str(_TESTS_DIR) not in sys.path:
            sys.path.insert(0, str(_TESTS_DIR))
        import meta_transport_child_fixture as fixture

        synthetic_modes = sorted(
            mode for mode in fixture._MODES
            if mode not in ("real_child", "real_guarded")
        )
        self.assertTrue(synthetic_modes)
        for mode in synthetic_modes:
            with self.subTest(mode=mode):
                self._run_child(
                    _DISPATCH_SCRIPT, mode, "refuse-all")
        for mode in ("real_child", "real_guarded"):
            with self.subTest(mode=mode):
                self._run_child(
                    _DISPATCH_SCRIPT, mode, "inactive")
        with self.subTest(mode="syn_not_a_mode"):
            self._run_child(
                _DISPATCH_SCRIPT, "syn_not_a_mode", "unknown")


if __name__ == "__main__":
    unittest.main()
