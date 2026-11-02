"""Compact native contract tests for root ``hosted_launch.py``.

The hosted entry point is intentionally absent while these tests
are authored. Every test first asserts that the file exists, so
a pre-implementation run fails on that explicit assertion, not
at collection and never as a skip. Only the standard library is
imported at module level; the existing package modules and
Uvicorn are imported inside the tests, after the assertion, so
a broken existing dependency still surfaces as its own import
error.

No test serves, calls a model, opens a listener, or uses a real
credential. ``MetaProvider``, ``UsageLedger`` and
``BudgetedProvider`` on the real ``teacher_demo`` modules are
replaced, before the launcher is loaded by absolute path, with
named constructor recorders; ``create_app`` and ``uvicorn.run``
are patched the same way. This is a declared constructor
fixture for launcher wiring only: it proves nothing about
ledger behaviour, sessions, HTTP behaviour, or live inference.
Only synthetic sentinel credentials are used, through a scoped
environment, and they are compared as booleans so failure
output never echoes them.
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import inspect
import io
import itertools
import json
import os
import pathlib
import re
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOSTED_PATH = ROOT / "hosted_launch.py"
SRC_DIR = ROOT / "src"
ENV_NAME = "HACKATHON_IYNAA_META_API_KEY"
HOST = "demo.example.com"

_ABSENT = object()


class TestHostedLaunch(unittest.TestCase):
    """Five bounded tests for the hosted entry point."""

    _load_counter = itertools.count(1)

    def _existing(self, name):
        """Import an existing package module, via adjacent ``src``."""
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            src = str(SRC_DIR)
            if src not in sys.path:
                sys.path.insert(0, src)
            return importlib.import_module(name)

    def _mods(self):
        return types.SimpleNamespace(
            transport=self._existing("teacher_demo.meta_transport"),
            provider=self._existing("teacher_demo.meta_provider"),
            http=self._existing("teacher_demo.http_app"),
            usage=self._existing("teacher_demo.hosted_usage"),
            uvicorn=importlib.import_module("uvicorn"),
        )

    def _load(self):
        """Load root ``hosted_launch.py`` fresh, by absolute path."""
        name = f"hosted_launch_under_test_{next(type(self)._load_counter)}"
        spec = importlib.util.spec_from_file_location(
            name, str(HOSTED_PATH))
        self.assertTrue(
            spec is not None and spec.loader is not None,
            "hosted_launch.py could not be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    @contextlib.contextmanager
    def _env(self, value):
        """Run with the app credential set or absent, restored after."""
        with mock.patch.dict(os.environ):
            if value is _ABSENT:
                os.environ.pop(ENV_NAME, None)
            else:
                os.environ[ENV_NAME] = value
            yield

    def _secret_equal(self, actual, expected):
        self.assertTrue(
            isinstance(actual, str) and actual == expected,
            "credential value mismatch")

    def _no_leak(self, needle, *texts):
        for text in texts:
            self.assertTrue(
                needle not in text, "sensitive sentinel leaked")

    @staticmethod
    def _values(call):
        """All positional plus keyword values of one mock call."""
        args, kwargs = call
        return list(args) + list(kwargs.values())

    def _factory_kwargs(self, factory):
        args, kwargs = factory.call_args
        self.assertTrue(args == (), "create_app must be keyword-injected")
        return kwargs

    def _check_factory(self, factory, hostname, provider, idle):
        kwargs = self._factory_kwargs(factory)
        self.assertTrue(
            kwargs.get("allowed_hosts") == (hostname,),
            "allowed_hosts must be exactly the supplied hostname")
        self.assertTrue(
            kwargs.get("allowed_origin") == "https://" + hostname,
            "allowed_origin must be the exact https origin")
        secure = next(
            (kwargs[key] for key in (
                "secure_cookie", "secure_cookies", "cookie_secure")
             if key in kwargs), None)
        self.assertTrue(
            secure is True, "secure cookie must always be True")
        idle_value = next(
            (kwargs[key] for key in (
                "session_idle_seconds", "session_idle_timeout_seconds",
                "session_idle_timeout")
             if key in kwargs), None)
        self.assertTrue(
            idle_value == idle,
            "explicit session idle seconds must be passed")
        if provider is None:
            self.assertTrue(
                kwargs.get("provider") is None,
                "disconnected mode must inject provider=None")
        else:
            self.assertIs(kwargs.get("provider"), provider)
        return kwargs

    def _check_fixed_error(self, error, messages, *needles):
        self.assertTrue(type(error) is ValueError, "must raise ValueError")
        self.assertTrue(
            len(error.args) == 1 and str(error) != "",
            "error must be one fixed message")
        self.assertIs(error.__cause__, None)
        self.assertTrue(
            error.__context__ is None or error.__suppress_context__,
            "error must carry no visible context")
        for needle in needles:
            if needle:
                self._no_leak(needle, str(error), repr(error))
        messages.add(str(error))

    def test_missing_module_assertion_and_import_inertia(self):
        self.assertTrue(
            HOSTED_PATH.is_file(),
            "hosted_launch.py must exist at the repository root")
        mods = self._mods()
        synthetic = "SYNTHETIC_IMPORT_CREDENTIAL_7Q"
        recorders = {
            "run_payload": mock.Mock(
                side_effect=AssertionError("transport invoked")),
            "MetaProvider": mock.MagicMock(
                side_effect=AssertionError("provider constructed")),
            "UsageLedger": mock.MagicMock(
                side_effect=AssertionError("ledger opened")),
            "BudgetedProvider": mock.MagicMock(
                side_effect=AssertionError("wrapper constructed")),
            "create_app": mock.Mock(
                side_effect=AssertionError("app constructed")),
            "run": mock.Mock(
                side_effect=AssertionError("server started")),
        }
        with mock.patch.object(
                mods.transport, "run_payload",
                recorders["run_payload"]), \
                mock.patch.object(
                    mods.provider, "MetaProvider",
                    recorders["MetaProvider"]), \
                mock.patch.object(
                    mods.usage, "UsageLedger",
                    recorders["UsageLedger"]), \
                mock.patch.object(
                    mods.usage, "BudgetedProvider",
                    recorders["BudgetedProvider"]), \
                mock.patch.object(
                    mods.http, "create_app",
                    recorders["create_app"]), \
                mock.patch.object(
                    mods.uvicorn, "run", recorders["run"]), \
                self._env(synthetic):
            launch = self._load()
            self.assertTrue(callable(launch.create_hosted_app))
            self.assertTrue(callable(launch.main))
            params = inspect.signature(
                launch.create_hosted_app).parameters
            self.assertEqual(
                list(params),
                ["hostname", "port", "meta", "usage_path", "policy",
                 "timeout_seconds", "session_idle_seconds"])
            for param in params.values():
                self.assertIs(
                    param.kind, inspect.Parameter.KEYWORD_ONLY)
            self.assertIs(
                params["hostname"].default, inspect.Parameter.empty)
            self.assertTrue(
                type(params["port"].default) is int
                and params["port"].default == 8000)
            self.assertIs(params["meta"].default, False)
            self.assertIs(params["usage_path"].default, None)
            self.assertIs(params["policy"].default, None)
            self.assertTrue(
                params["timeout_seconds"].default == 600.0
                and not isinstance(
                    params["timeout_seconds"].default, bool))
            self.assertTrue(
                params["session_idle_seconds"].default == 1800.0
                and not isinstance(
                    params["session_idle_seconds"].default, bool))
            main_params = list(
                inspect.signature(launch.main).parameters.values())
            self.assertTrue(
                len(main_params) == 1
                and main_params[0].name == "argv"
                and main_params[0].default is None)
        for recorder in recorders.values():
            self.assertTrue(
                recorder.call_count == 0, "import must be inert")

    def test_hostname_validation_and_invalid_config_preconstruction(self):
        self.assertTrue(
            HOSTED_PATH.is_file(),
            "hosted_launch.py must exist at the repository root")
        mods = self._mods()
        app_instance = mock.Mock(name="app")
        factory = mock.Mock(return_value=app_instance)
        provider_ctor = mock.MagicMock(return_value=mock.Mock())
        ledger_ctor = mock.MagicMock(return_value=mock.Mock())
        wrapper_ctor = mock.MagicMock(return_value=mock.Mock())
        server_run = mock.Mock()
        ctors = (provider_ctor, ledger_ctor, wrapper_ctor, server_run)
        with mock.patch.object(mods.provider, "MetaProvider",
                               provider_ctor), \
                mock.patch.object(mods.usage, "UsageLedger",
                                  ledger_ctor), \
                mock.patch.object(mods.usage, "BudgetedProvider",
                                  wrapper_ctor), \
                mock.patch.object(mods.http, "create_app", factory), \
                mock.patch.object(mods.uvicorn, "run", server_run), \
                self._env(_ABSENT):
            launch = self._load()
            for hostname in (
                    "example.com", "demo.example.com",
                    "www.example.com", "a-b.c-d.example.org",
                    "x9.y2.example.net", "hosted.example.co.uk"):
                before = factory.call_count
                app = launch.create_hosted_app(hostname=hostname)
                self.assertIs(app, app_instance)
                self.assertTrue(factory.call_count == before + 1)
                self._check_factory(factory, hostname, None, 1800.0)
            for port in (1, 65535):
                before = factory.call_count
                launch.create_hosted_app(hostname=HOST, port=port)
                self.assertTrue(factory.call_count == before + 1)

            bad_hostnames = (
                "", None, 8000, b"example.com", "Example.com",
                "EXAMPLE.COM", "https://example.com",
                "http://example.com", "example.com:8000",
                "example.com:443", "example.com/", "example.com/path",
                " example.com", "example.com ", "exam ple.com",
                "example\t.com", "example.com\n", "*.example.com",
                "*", "example.com.", ".example.com", "example..com",
                "example.com..", "-example.com", "example-.com",
                "exam_ple.com", "127.0.0.1", "192.168.1.20", "::1",
                "[::1]", "ex\u00e4mple.com",
                "a" * 64 + ".example.com",
                ("a" * 63 + ".") * 4 + "example.com",
            )
            cases = [
                ({"hostname": value}, None) for value in bad_hostnames]
            cases.append((
                {"hostname": "SENTINEL_HOST_7Q.example.com"},
                "SENTINEL_HOST_7Q"))
            for value, sentinel in (
                    (0, None), (-1, None), (65536, None), (True, None),
                    (8000.0, None), (None, None),
                    ("SENTINEL_PORT_7Q", "SENTINEL_PORT_7Q")):
                cases.append(({"hostname": HOST, "port": value}, sentinel))
            for value in ("true", 1, None):
                cases.append(({"hostname": HOST, "meta": value}, None))
            for value, sentinel in (
                    (0, None), (-1.0, None), (float("nan"), None),
                    (float("inf"), None), (True, None), (None, None),
                    ("SENTINEL_IDLE_7Q", "SENTINEL_IDLE_7Q")):
                cases.append((
                    {"hostname": HOST, "session_idle_seconds": value},
                    sentinel))
            messages = set()
            for kwargs, sentinel in cases:
                counts = [factory.call_count] + [
                    ctor.call_count for ctor in ctors]
                with self.assertRaises(ValueError) as caught:
                    launch.create_hosted_app(**kwargs)
                self._check_fixed_error(
                    caught.exception, messages, sentinel)
                self.assertTrue(
                    [factory.call_count]
                    + [ctor.call_count for ctor in ctors] == counts,
                    "invalid configuration must construct nothing")
            self.assertTrue(
                len(messages) == 1,
                "invalid configuration must use one fixed message")

    def test_disconnected_exact_injection_without_credential_activity(self):
        self.assertTrue(
            HOSTED_PATH.is_file(),
            "hosted_launch.py must exist at the repository root")
        mods = self._mods()
        app_instance = mock.Mock(name="app")
        factory = mock.Mock(return_value=app_instance)
        forbidden = {
            "run_payload": mock.Mock(
                side_effect=AssertionError("transport invoked")),
            "MetaProvider": mock.MagicMock(side_effect=AssertionError(
                "disconnected mode constructed a provider")),
            "UsageLedger": mock.MagicMock(side_effect=AssertionError(
                "disconnected mode opened a ledger")),
            "BudgetedProvider": mock.MagicMock(
                side_effect=AssertionError(
                    "disconnected mode constructed a wrapper")),
        }
        ambient = (
            _ABSENT, "", "invalid ambient value",
            "non-ascii-ambient-\u00e9",
            "SYNTHETIC_AMBIENT_CREDENTIAL_7Q",
        )
        with mock.patch.object(
                mods.transport, "run_payload",
                forbidden["run_payload"]), \
                mock.patch.object(
                    mods.provider, "MetaProvider",
                    forbidden["MetaProvider"]), \
                mock.patch.object(
                    mods.usage, "UsageLedger",
                    forbidden["UsageLedger"]), \
                mock.patch.object(
                    mods.usage, "BudgetedProvider",
                    forbidden["BudgetedProvider"]), \
                mock.patch.object(mods.http, "create_app", factory):
            launch = self._load()
            for value in ambient:
                with self._env(value):
                    for kwargs, idle in (
                            ({"hostname": HOST}, 1800.0),
                            ({"hostname": HOST, "meta": False,
                              "port": 8765,
                              "session_idle_seconds": 42.5}, 42.5)):
                        before = factory.call_count
                        app = launch.create_hosted_app(**kwargs)
                        self.assertIs(app, app_instance)
                        self.assertTrue(factory.call_count == before + 1)
                        self._check_factory(factory, HOST, None, idle)
        for recorder in forbidden.values():
            self.assertTrue(
                recorder.call_count == 0,
                "disconnected mode must do no credential work")

    def test_explicit_meta_constructs_ledger_delegate_wrapper_once(self):
        self.assertTrue(
            HOSTED_PATH.is_file(),
            "hosted_launch.py must exist at the repository root")
        mods = self._mods()
        policy = mods.usage.UsagePolicy(
            max_attempts=25, max_output_tokens=64,
            window_end=1893456000.0, minimum_spacing_seconds=2.5)
        delegate = mock.Mock(name="delegate")
        ledger = mock.Mock(name="ledger")
        wrapper = mock.Mock(name="wrapper")
        app_instance = mock.Mock(name="app")
        provider_ctor = mock.MagicMock(return_value=delegate)
        ledger_ctor = mock.MagicMock(return_value=ledger)
        wrapper_ctor = mock.MagicMock(return_value=wrapper)
        factory = mock.Mock(return_value=app_instance)
        ctors = (provider_ctor, ledger_ctor, wrapper_ctor, factory)
        synthetic = "SYNTHETIC_META_CREDENTIAL_7Q"
        with tempfile.TemporaryDirectory() as tmp:
            store = pathlib.Path(tmp) / "usage-store.bin"
            store.write_bytes(b"")
            store_text = str(store)
            with mock.patch.object(
                    mods.provider, "MetaProvider", provider_ctor), \
                    mock.patch.object(
                        mods.usage, "UsageLedger", ledger_ctor), \
                    mock.patch.object(
                        mods.usage, "BudgetedProvider", wrapper_ctor), \
                    mock.patch.object(
                        mods.http, "create_app", factory):
                launch = self._load()
                with self._env(synthetic):
                    app = launch.create_hosted_app(
                        hostname=HOST, port=8123, meta=True,
                        usage_path=store_text, policy=policy,
                        timeout_seconds=12.5, session_idle_seconds=90.0)
                self.assertIs(app, app_instance)
                for ctor in ctors:
                    self.assertTrue(
                        ctor.call_count == 1,
                        "each Meta constructor must run exactly once")
                ledger_values = self._values(ledger_ctor.call_args)
                self.assertTrue(
                    any(value is policy for value in ledger_values),
                    "the same policy object must reach the ledger")
                self.assertTrue(
                    any(str(value) == store_text
                        for value in ledger_values),
                    "the supplied usage path must reach the ledger")
                ctor_args, ctor_kwargs = provider_ctor.call_args
                self.assertTrue(ctor_args == ())
                self._secret_equal(
                    ctor_kwargs.get("credential"), synthetic)
                self.assertTrue(
                    type(ctor_kwargs.get("max_output_tokens")) is int
                    and ctor_kwargs["max_output_tokens"] == 64)
                self.assertTrue(ctor_kwargs.get("timeout_seconds") == 12.5)
                wrapper_values = self._values(wrapper_ctor.call_args)
                self.assertTrue(
                    any(value is delegate for value in wrapper_values),
                    "the delegate must be wrapped")
                self.assertTrue(
                    any(value is ledger for value in wrapper_values),
                    "the ledger must be wrapped with the delegate")
                kwargs = self._check_factory(factory, HOST, wrapper, 90.0)
                self.assertTrue(
                    kwargs.get("provider") is not delegate,
                    "only the budgeted wrapper may be injected")

                base = {
                    "hostname": HOST, "meta": True,
                    "usage_path": store_text, "policy": policy,
                    "timeout_seconds": 5.0}
                bad = [
                    ({**base, "policy": None}, synthetic, None),
                    ({**base, "usage_path": None}, synthetic, None),
                    ({**base, "usage_path": ""}, synthetic, None),
                    ({**base, "usage_path": 12345}, synthetic, None),
                    ({**base, "policy": object()}, synthetic, None),
                    (base, _ABSENT, None),
                    (base, "", None),
                    (base, "SENTINEL_CRED_7Q value", "SENTINEL_CRED_7Q"),
                    (base, "SENTINEL_CRED_7Q\u00e9", "SENTINEL_CRED_7Q"),
                ]
                for value, sentinel in (
                        (0, None), (-1.0, None), (float("nan"), None),
                        (float("inf"), None), (True, None), (None, None),
                        ("SENTINEL_TIMEOUT_7Q", "SENTINEL_TIMEOUT_7Q")):
                    bad.append((
                        {**base, "timeout_seconds": value},
                        synthetic, sentinel))
                messages = set()
                for kwargs, env, sentinel in bad:
                    counts = [ctor.call_count for ctor in ctors]
                    with self._env(env):
                        with self.assertRaises(ValueError) as caught:
                            launch.create_hosted_app(**kwargs)
                    self._check_fixed_error(
                        caught.exception, messages, sentinel, synthetic,
                        store_text)
                    self.assertTrue(
                        [ctor.call_count for ctor in ctors] == counts,
                        "invalid Meta config must construct nothing")
                self.assertTrue(
                    len(messages) == 1,
                    "invalid Meta config must use one fixed message")
        self.assertTrue(delegate.call_count == 0)
        self.assertTrue(wrapper.call_count == 0)

    def test_cli_help_refusals_and_loopback_single_worker_run(self):
        self.assertTrue(
            HOSTED_PATH.is_file(),
            "hosted_launch.py must exist at the repository root")
        mods = self._mods()
        delegate = mock.Mock(name="delegate")
        ledger = mock.Mock(name="ledger")
        wrapper = mock.Mock(name="wrapper")
        app_instance = mock.Mock(name="app")
        provider_ctor = mock.MagicMock(return_value=delegate)
        ledger_ctor = mock.MagicMock(return_value=ledger)
        wrapper_ctor = mock.MagicMock(return_value=wrapper)
        factory = mock.Mock(return_value=app_instance)
        server_run = mock.Mock(return_value=None)
        run_payload = mock.Mock(
            side_effect=AssertionError("transport invoked"))
        counted = (
            provider_ctor, ledger_ctor, wrapper_ctor, factory, server_run)
        synthetic = "SYNTHETIC_CLI_CREDENTIAL_7Q"
        valid_policy = {
            "max_attempts": 25, "max_output_tokens": 64,
            "window_end": 1893456000, "minimum_spacing_seconds": 2.5}
        with tempfile.TemporaryDirectory() as tmp:
            base_dir = pathlib.Path(tmp)
            store = base_dir / "usage-store.bin"
            store.write_bytes(b"")

            def write_policy(name, payload):
                path = base_dir / name
                if isinstance(payload, str):
                    path.write_text(payload, encoding="utf-8")
                else:
                    path.write_text(
                        json.dumps(payload), encoding="utf-8")
                return str(path)

            good_policy = write_policy("policy.json", valid_policy)
            missing_policy = write_policy(
                "missing.json",
                {k: v for k, v in valid_policy.items()
                 if k != "max_attempts"})
            extra_policy = write_policy(
                "extra.json", {**valid_policy, "unexpected": 1})
            broken_policy = write_policy("broken.json", "{not json")
            bool_policy = write_policy(
                "bool.json", {**valid_policy, "max_attempts": True})
            text_policy = write_policy(
                "text.json",
                {**valid_policy, "window_end": "SENTINEL_END_7Q"})
            list_policy = write_policy("list.json", "[1, 2, 3]")
            ghost_policy = str(base_dir / "SENTINEL_POLICY_7Q.json")
            ghost_store = str(base_dir / "SENTINEL_STORE_7Q.bin")
            with mock.patch.object(
                    mods.transport, "run_payload", run_payload), \
                    mock.patch.object(
                        mods.provider, "MetaProvider", provider_ctor), \
                    mock.patch.object(
                        mods.usage, "UsageLedger", ledger_ctor), \
                    mock.patch.object(
                        mods.usage, "BudgetedProvider", wrapper_ctor), \
                    mock.patch.object(
                        mods.http, "create_app", factory), \
                    mock.patch.object(
                        mods.uvicorn, "run", server_run):
                launch = self._load()

                def run_main(argv, env=_ABSENT):
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with self._env(env), \
                            contextlib.redirect_stdout(stdout), \
                            contextlib.redirect_stderr(stderr):
                        try:
                            result = launch.main(list(argv))
                        except SystemExit as exit_:
                            result = exit_.code
                    return result, stdout.getvalue(), stderr.getvalue()

                def check_served(argv, env, port, provider, idle):
                    before = server_run.call_count
                    result, stdout, stderr = run_main(argv, env)
                    self.assertTrue(
                        type(result) is int and result == 0,
                        "valid run must return 0")
                    self.assertTrue(server_run.call_count == before + 1)
                    args, kwargs = server_run.call_args
                    self.assertTrue(
                        len(args) == 1 and args[0] is app_instance)
                    for key, expected in {
                            "host": "127.0.0.1", "port": port,
                            "workers": 1, "reload": False,
                            "access_log": False}.items():
                        self.assertTrue(
                            kwargs.get(key) == expected,
                            f"uvicorn {key} must stay fixed")
                    if "proxy_headers" in kwargs:
                        self.assertTrue(
                            kwargs["proxy_headers"] is False,
                            "forwarded headers must not be trusted")
                    if "forwarded_allow_ips" in kwargs:
                        self.assertTrue(
                            kwargs["forwarded_allow_ips"] != "*",
                            "forwarded IPs must not be wildcard-trusted")
                    self._check_factory(factory, HOST, provider, idle)
                    self._no_leak(synthetic, stdout, stderr)

                counts = [ctor.call_count for ctor in counted]
                result, help_text, help_err = run_main(
                    ["--help"], synthetic)
                self.assertTrue(result == 0)
                for option in (
                        "--hostname", "--port", "--meta",
                        "--usage-store", "--policy-file",
                        "--timeout-seconds", "--session-idle-seconds"):
                    self.assertTrue(
                        option in help_text, f"help is missing {option}")
                for forbidden in (
                        "--init", "--initialize", "--reset", "--workers",
                        "--reload", "--api-key", "--model", "--base-url"):
                    self.assertTrue(
                        forbidden not in help_text,
                        f"help offers forbidden {forbidden}")
                self.assertTrue(
                    re.search(r"--host(?:\s|=|,)", help_text) is None,
                    "help must not offer a bind-host option")
                self._no_leak(synthetic, help_text, help_err)
                self.assertTrue(
                    [ctor.call_count for ctor in counted] == counts,
                    "help must have no side effects")

                meta_argv = [
                    "--hostname", HOST, "--meta",
                    "--usage-store", str(store),
                    "--policy-file", good_policy]
                refusals = (
                    ([], _ABSENT, None),
                    (["--hostname"], _ABSENT, None),
                    (["--hostname", "EXAMPLE.COM"], _ABSENT, None),
                    (["--hostname=example.com:8000"], _ABSENT, None),
                    (["--hostname", "127.0.0.1"], _ABSENT, None),
                    (["--hostname", "example.com."], _ABSENT, None),
                    (["--hostname", HOST, "--port", "0"], _ABSENT, None),
                    (["--hostname", HOST, "--port=65536"], _ABSENT, None),
                    (["--hostname", HOST, "--port", "SENTINEL_PORT_7Q"],
                     _ABSENT, "SENTINEL_PORT_7Q"),
                    (["--hostname", HOST, "--host", "SENTINEL_HOST_7Q"],
                     _ABSENT, "SENTINEL_HOST_7Q"),
                    (["--hostname", HOST, "--init"], _ABSENT, None),
                    (["--hostname", HOST, "--workers", "4"],
                     _ABSENT, None),
                    (["--hostname", HOST, "--reload"], _ABSENT, None),
                    (["--hostname", HOST, "--api-key",
                      "SENTINEL_API_KEY_7Q"], _ABSENT,
                     "SENTINEL_API_KEY_7Q"),
                    (["--hostname", HOST, "--unknown-sentinel-7q"],
                     _ABSENT, "unknown-sentinel-7q"),
                    (["--hostname", HOST, "SENTINEL_POS_7Q"],
                     _ABSENT, "SENTINEL_POS_7Q"),
                    (["--hostname", HOST,
                      "--session-idle-seconds=-5"], _ABSENT, None),
                    (["--hostname", HOST,
                      "--session-idle-seconds", "inf"], _ABSENT, None),
                    (["--hostname", HOST, "--meta"], synthetic, None),
                    (["--hostname", HOST, "--meta",
                      "--usage-store", str(store)], synthetic, None),
                    (["--hostname", HOST, "--meta",
                      "--policy-file", good_policy], synthetic, None),
                    (meta_argv, _ABSENT, None),
                    (meta_argv, "SENTINEL_BAD_KEY_7Q value",
                     "SENTINEL_BAD_KEY_7Q"),
                    (meta_argv + ["--timeout-seconds", "0"],
                     synthetic, None),
                    (meta_argv + ["--timeout-seconds", "nan"],
                     synthetic, None),
                    (["--hostname", HOST, "--meta",
                      "--usage-store", str(store),
                      "--policy-file", ghost_policy],
                     synthetic, "SENTINEL_POLICY_7Q"),
                    (["--hostname", HOST, "--meta",
                      "--usage-store", str(store),
                      "--policy-file", broken_policy], synthetic, None),
                    (["--hostname", HOST, "--meta",
                      "--usage-store", str(store),
                      "--policy-file", missing_policy], synthetic, None),
                    (["--hostname", HOST, "--meta",
                      "--usage-store", str(store),
                      "--policy-file", extra_policy], synthetic, None),
                    (["--hostname", HOST, "--meta",
                      "--usage-store", str(store),
                      "--policy-file", bool_policy], synthetic, None),
                    (["--hostname", HOST, "--meta",
                      "--usage-store", str(store),
                      "--policy-file", text_policy],
                     synthetic, "SENTINEL_END_7Q"),
                    (["--hostname", HOST, "--meta",
                      "--usage-store", str(store),
                      "--policy-file", list_policy], synthetic, None),
                )
                refusal_stderr = set()
                for argv, env, sentinel in refusals:
                    counts = [ctor.call_count for ctor in counted]
                    result, stdout, stderr = run_main(argv, env)
                    self.assertTrue(
                        type(result) is int and result == 2,
                        "refusal must return exit code 2")
                    self.assertTrue(stdout == "")
                    self.assertTrue(stderr.strip() != "")
                    self._no_leak(synthetic, stdout, stderr)
                    if sentinel is not None:
                        self._no_leak(sentinel, stdout, stderr)
                    self._no_leak(str(store), stdout, stderr)
                    refusal_stderr.add(stderr)
                    self.assertTrue(
                        [ctor.call_count for ctor in counted] == counts,
                        "refusal must construct and serve nothing")
                self.assertTrue(
                    len(refusal_stderr) == 1,
                    "refusal stderr must be one fixed message")

                check_served(
                    ["--hostname", HOST], synthetic, 8000, None, 1800.0)
                self.assertTrue(provider_ctor.call_count == 0)
                self.assertTrue(ledger_ctor.call_count == 0)
                check_served(
                    ["--hostname", HOST, "--port", "8123",
                     "--session-idle-seconds", "45",
                     "--timeout-seconds", "30",
                     "--usage-store", ghost_store,
                     "--policy-file", ghost_policy],
                    _ABSENT, 8123, None, 45.0)
                self.assertTrue(provider_ctor.call_count == 0)
                self.assertTrue(ledger_ctor.call_count == 0)
                check_served(
                    ["--hostname", HOST, "--port", "8124", "--meta",
                     "--usage-store", str(store),
                     "--policy-file", good_policy,
                     "--timeout-seconds", "12.5",
                     "--session-idle-seconds", "90"],
                    synthetic, 8124, wrapper, 90.0)
                self.assertTrue(provider_ctor.call_count == 1)
                self.assertTrue(ledger_ctor.call_count == 1)
                self.assertTrue(wrapper_ctor.call_count == 1)
                _, ctor_kwargs = provider_ctor.call_args
                self._secret_equal(
                    ctor_kwargs.get("credential"), synthetic)
                self.assertTrue(
                    ctor_kwargs.get("max_output_tokens") == 64
                    and ctor_kwargs.get("timeout_seconds") == 12.5)
                ledger_values = self._values(ledger_ctor.call_args)
                self.assertTrue(
                    any(str(value) == str(store)
                        for value in ledger_values))
                cli_policy = None
                for value in ledger_values:
                    if hasattr(value, "max_attempts"):
                        cli_policy = value
                self.assertTrue(
                    cli_policy is not None,
                    "CLI policy object must reach the ledger")
                self.assertTrue(cli_policy.max_attempts == 25)
                self.assertTrue(cli_policy.max_output_tokens == 64)
                self.assertTrue(cli_policy.window_end == 1893456000)
                self.assertTrue(
                    cli_policy.minimum_spacing_seconds == 2.5)
                wrapper_values = self._values(wrapper_ctor.call_args)
                self.assertTrue(
                    any(value is delegate for value in wrapper_values)
                    and any(value is ledger for value in wrapper_values))
        self.assertTrue(run_payload.call_count == 0)
        self.assertTrue(delegate.call_count == 0)
        self.assertTrue(wrapper.call_count == 0)
        source = HOSTED_PATH.read_text(encoding="utf-8")
        guard = re.search(
            r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:", source)
        self.assertTrue(guard is not None, "missing __main__ guard")
        self.assertTrue(
            "main(" in source[guard.end():],
            "__main__ guard must use main's return code")
