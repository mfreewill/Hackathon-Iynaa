"""Native contract tests for the proposed root ``launch.py``.

``launch.py`` is intentionally absent while these tests are
authored. Every test first asserts that the file exists, so a
pre-implementation run fails on that missing entry point
rather than passing, skipping, or erroring at collection.
Only the standard library is imported at module level; the
existing app modules, FastAPI and Uvicorn are imported inside
the tests, after the missing-file assertion.

No test performs Meta inference, opens a listener, starts a
subprocess, or uses a real credential. The provider,
transport and Uvicorn boundaries are patched recorders,
except where the real disconnected factory is exercised
through FastAPI's ``TestClient``. Patching happens before the
launch module is loaded by absolute path, so the recorded
objects are the existing ``MetaProvider``, ``create_app``
and ``uvicorn.run`` however the entry point imports them.
Sensitive synthetic values are compared as booleans so they
never appear in unittest failure output.
"""
from __future__ import annotations

import contextlib
import importlib
import importlib.util
import inspect
import io
import itertools
import os
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCH_PATH = ROOT / "launch.py"
SRC_DIR = ROOT / "src"
ENV_NAME = "HACKATHON_IYNAA_META_API_KEY"
FIXED_ERROR = "invalid local launch configuration"

_ABSENT = object()


class TestLocalLaunch(unittest.TestCase):
    """Six bounded tests for the clean local entry point."""

    _load_counter = itertools.count(1)

    def _existing(self, name):
        """Import an existing app module, via adjacent ``src``."""
        try:
            return importlib.import_module(name)
        except ModuleNotFoundError:
            src = str(SRC_DIR)
            if src not in sys.path:
                sys.path.insert(0, src)
            return importlib.import_module(name)

    def _load_launch(self):
        """Load root ``launch.py`` fresh, by exact absolute path."""
        name = (
            f"launch_under_test_{next(type(self)._load_counter)}")
        spec = importlib.util.spec_from_file_location(
            name, str(LAUNCH_PATH))
        self.assertTrue(
            spec is not None and spec.loader is not None,
            "launch.py could not be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    @contextlib.contextmanager
    def _controlled_env(self, value):
        """Run with the app credential set or absent, restored after."""
        with mock.patch.dict(os.environ):
            if value is _ABSENT:
                os.environ.pop(ENV_NAME, None)
            else:
                os.environ[ENV_NAME] = value
            yield

    def _assert_secret_equal(self, actual, expected):
        self.assertTrue(
            isinstance(actual, str) and actual == expected,
            "credential value mismatch")

    def _assert_no_leak(self, needle, *texts):
        for text in texts:
            self.assertTrue(
                needle not in text, "sensitive sentinel leaked")

    def test_import_and_help_are_inert(self):
        self.assertTrue(
            LAUNCH_PATH.is_file(),
            "launch.py must exist at the repository root")
        transport = self._existing("teacher_demo.meta_transport")
        provider_module = self._existing(
            "teacher_demo.meta_provider")
        http_app_module = self._existing("teacher_demo.http_app")
        uvicorn = importlib.import_module("uvicorn")
        synthetic = "SYNTHETIC_HELP_CREDENTIAL_7Q"
        run_payload = mock.Mock(
            side_effect=AssertionError("transport invoked"))
        provider_ctor = mock.MagicMock(
            side_effect=AssertionError("provider constructed"))
        factory = mock.Mock(
            side_effect=AssertionError("app constructed"))
        server_run = mock.Mock(
            side_effect=AssertionError("server started"))
        with mock.patch.object(
                transport, "run_payload", run_payload), \
                mock.patch.object(
                    provider_module, "MetaProvider",
                    provider_ctor), \
                mock.patch.object(
                    http_app_module, "create_app", factory), \
                mock.patch.object(uvicorn, "run", server_run), \
                self._controlled_env(synthetic):
            launch = self._load_launch()
            self.assertTrue(callable(launch.create_local_app))
            self.assertTrue(callable(launch.main))
            params = inspect.signature(
                launch.create_local_app).parameters
            self.assertEqual(
                list(params),
                ["port", "meta", "max_output_tokens",
                 "timeout_seconds"])
            for param in params.values():
                self.assertIs(
                    param.kind,
                    inspect.Parameter.KEYWORD_ONLY)
            self.assertTrue(
                type(params["port"].default) is int
                and params["port"].default == 8000)
            self.assertIs(params["meta"].default, False)
            self.assertIs(params["max_output_tokens"].default, None)
            self.assertIs(params["timeout_seconds"].default, None)
            main_params = list(
                inspect.signature(launch.main).parameters.values())
            self.assertTrue(
                len(main_params) == 1
                and main_params[0].name == "argv"
                and main_params[0].default is None)
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), \
                    contextlib.redirect_stderr(stderr):
                try:
                    result = launch.main(["--help"])
                except SystemExit as exit_:
                    self.assertTrue(exit_.code == 0)
                else:
                    self.assertTrue(result == 0)
            help_text = stdout.getvalue()
            for option in (
                    "--port", "--meta", "--max-output-tokens",
                    "--timeout-seconds"):
                self.assertTrue(
                    option in help_text,
                    f"help is missing {option}")
            for forbidden in (
                    "--host", "--workers", "--reload", "--api-key",
                    "--model", "--base-url"):
                self.assertTrue(
                    forbidden not in help_text,
                    f"help offers forbidden {forbidden}")
            self._assert_no_leak(
                synthetic, help_text, stderr.getvalue())
        for recorder in (
                run_payload, provider_ctor, factory, server_run):
            self.assertTrue(
                recorder.call_count == 0, "inert boundary invoked")

    def test_disconnected_ignores_ambient_app_key(self):
        self.assertTrue(
            LAUNCH_PATH.is_file(),
            "launch.py must exist at the repository root")
        transport = self._existing("teacher_demo.meta_transport")
        provider_module = self._existing(
            "teacher_demo.meta_provider")
        run_payload = mock.Mock(
            side_effect=AssertionError("transport invoked"))
        provider_ctor = mock.MagicMock(side_effect=AssertionError(
            "disconnected mode constructed a provider"))
        ambient_values = (
            _ABSENT,
            "",
            "invalid ambient value",
            "non-ascii-ambient-\u00e9",
            "SYNTHETIC_AMBIENT_CREDENTIAL_7Q",
        )
        with mock.patch.object(
                transport, "run_payload", run_payload), \
                mock.patch.object(
                    provider_module, "MetaProvider",
                    provider_ctor):
            launch = self._load_launch()
            for value in ambient_values:
                with self._controlled_env(value):
                    for kwargs in (
                            {},
                            {"meta": False},
                            {"meta": False, "port": 8765}):
                        app = launch.create_local_app(**kwargs)
                        self.assertIs(
                            app.state.teacher_provider, None)
        self.assertTrue(provider_ctor.call_count == 0)
        self.assertTrue(run_payload.call_count == 0)

    def test_explicit_meta_injects_existing_provider_once(self):
        self.assertTrue(
            LAUNCH_PATH.is_file(),
            "launch.py must exist at the repository root")
        provider_module = self._existing(
            "teacher_demo.meta_provider")
        http_app_module = self._existing("teacher_demo.http_app")
        provider_instance = mock.Mock(name="provider")
        app_instance = mock.Mock(name="app")
        provider_ctor = mock.MagicMock(
            return_value=provider_instance)
        factory = mock.Mock(return_value=app_instance)
        synthetic = "SYNTHETIC_META_CREDENTIAL_7Q"
        with mock.patch.object(
                provider_module, "MetaProvider", provider_ctor), \
                mock.patch.object(
                    http_app_module, "create_app", factory):
            launch = self._load_launch()
            with self._controlled_env(synthetic):
                app = launch.create_local_app(
                    port=8123, meta=True, max_output_tokens=64,
                    timeout_seconds=12.5)
                self.assertTrue(provider_ctor.call_count == 1)
                self.assertTrue(factory.call_count == 1)
                ctor_args, ctor_kwargs = provider_ctor.call_args
                self.assertTrue(ctor_args == ())
                self.assertEqual(
                    set(ctor_kwargs),
                    {"credential", "max_output_tokens",
                     "timeout_seconds"})
                self._assert_secret_equal(
                    ctor_kwargs["credential"], synthetic)
                self.assertTrue(
                    type(ctor_kwargs["max_output_tokens"]) is int
                    and ctor_kwargs["max_output_tokens"] == 64)
                self.assertTrue(
                    ctor_kwargs["timeout_seconds"] == 12.5)
                factory_args, factory_kwargs = factory.call_args
                self.assertTrue(factory_args == ())
                self.assertEqual(
                    set(factory_kwargs),
                    {"allowed_hosts", "allowed_origin", "provider"})
                self.assertIs(
                    factory_kwargs["provider"], provider_instance)
                self.assertTrue(
                    factory_kwargs["allowed_hosts"]
                    == ("127.0.0.1:8123",))
                self.assertTrue(
                    factory_kwargs["allowed_origin"]
                    == "http://127.0.0.1:8123")
                self.assertIs(app, app_instance)

                launch.create_local_app(
                    meta=True, max_output_tokens=16,
                    timeout_seconds=1)
                self.assertTrue(provider_ctor.call_count == 2)
                self.assertTrue(factory.call_count == 2)
                _, ctor_kwargs = provider_ctor.call_args_list[1]
                self._assert_secret_equal(
                    ctor_kwargs["credential"], synthetic)
                self.assertTrue(
                    ctor_kwargs["max_output_tokens"] == 16
                    and type(ctor_kwargs["timeout_seconds"]) is int
                    and ctor_kwargs["timeout_seconds"] == 1)
                _, factory_kwargs = factory.call_args_list[1]
                self.assertIs(
                    factory_kwargs["provider"], provider_instance)
                self.assertTrue(
                    factory_kwargs["allowed_hosts"]
                    == ("127.0.0.1:8000",))
                self.assertTrue(
                    factory_kwargs["allowed_origin"]
                    == "http://127.0.0.1:8000")
        self.assertTrue(
            provider_instance.call_count == 0,
            "constructor wiring must not invoke the provider")

    def test_invalid_configuration_is_value_free_and_preconstruction(
            self):
        self.assertTrue(
            LAUNCH_PATH.is_file(),
            "launch.py must exist at the repository root")
        provider_module = self._existing(
            "teacher_demo.meta_provider")
        http_app_module = self._existing("teacher_demo.http_app")
        uvicorn = importlib.import_module("uvicorn")
        provider_instance = mock.Mock(name="provider")
        app_instance = mock.Mock(name="app")
        provider_ctor = mock.MagicMock(
            return_value=provider_instance)
        factory = mock.Mock(return_value=app_instance)
        server_run = mock.Mock()
        synthetic = "SYNTHETIC_VALID_CREDENTIAL_7Q"
        with mock.patch.object(
                provider_module, "MetaProvider", provider_ctor), \
                mock.patch.object(
                    http_app_module, "create_app", factory), \
                mock.patch.object(uvicorn, "run", server_run):
            launch = self._load_launch()

            def check_invalid(label, env, kwargs, sentinel=None):
                ctor_before = provider_ctor.call_count
                factory_before = factory.call_count
                stdout, stderr = io.StringIO(), io.StringIO()
                with self._controlled_env(env), \
                        contextlib.redirect_stdout(stdout), \
                        contextlib.redirect_stderr(stderr):
                    with self.assertRaises(ValueError) as caught:
                        launch.create_local_app(**kwargs)
                error = caught.exception
                self.assertTrue(type(error) is ValueError, label)
                self.assertTrue(str(error) == FIXED_ERROR, label)
                self.assertTrue(
                    error.args == (FIXED_ERROR,), label)
                self.assertIs(error.__cause__, None, label)
                self.assertTrue(
                    error.__context__ is None
                    or error.__suppress_context__, label)
                if sentinel is not None:
                    self._assert_no_leak(
                        sentinel, str(error), stdout.getvalue(),
                        stderr.getvalue())
                self.assertTrue(
                    provider_ctor.call_count == ctor_before, label)
                self.assertTrue(
                    factory.call_count == factory_before, label)

            meta_kwargs = {
                "meta": True, "max_output_tokens": 64,
                "timeout_seconds": 5.0}
            for label, port, sentinel in (
                    ("port_zero", 0, None),
                    ("port_negative", -1, None),
                    ("port_too_large", 65536, None),
                    ("port_bool", True, None),
                    ("port_float", 8000.0, None),
                    ("port_none", None, None),
                    ("port_text", "SENTINEL_PORT_7Q",
                     "SENTINEL_PORT_7Q")):
                check_invalid(
                    label, _ABSENT, {"port": port}, sentinel)
            check_invalid(
                "port_too_large_with_meta", synthetic,
                {**meta_kwargs, "port": 70000})
            check_invalid("credential_absent", _ABSENT, meta_kwargs)
            check_invalid("credential_empty", "", meta_kwargs)
            for label, value, sentinel in (
                    ("credential_space",
                     "SENTINEL_CRED_SPACE_7Q value",
                     "SENTINEL_CRED_SPACE_7Q"),
                    ("credential_tab",
                     "SENTINEL_CRED_TAB_7Q\tvalue",
                     "SENTINEL_CRED_TAB_7Q"),
                    ("credential_non_ascii",
                     "SENTINEL_CRED_NONASCII_7Q\u00e9",
                     "SENTINEL_CRED_NONASCII_7Q"),
                    ("credential_del",
                     "SENTINEL_CRED_DEL_7Q\u007f",
                     "SENTINEL_CRED_DEL_7Q")):
                check_invalid(label, value, meta_kwargs, sentinel)
            for label, tokens, sentinel in (
                    ("tokens_absent", None, None),
                    ("tokens_bool", True, None),
                    ("tokens_below_minimum", 15, None),
                    ("tokens_zero", 0, None),
                    ("tokens_float", 16.0, None),
                    ("tokens_text", "SENTINEL_TOKENS_7Q",
                     "SENTINEL_TOKENS_7Q")):
                check_invalid(
                    label, synthetic,
                    {"meta": True, "max_output_tokens": tokens,
                     "timeout_seconds": 5.0},
                    sentinel)
            for label, timeout, sentinel in (
                    ("timeout_absent", None, None),
                    ("timeout_zero", 0, None),
                    ("timeout_negative", -1.0, None),
                    ("timeout_bool", True, None),
                    ("timeout_nan", float("nan"), None),
                    ("timeout_inf", float("inf"), None),
                    ("timeout_negative_inf", float("-inf"), None),
                    ("timeout_text", "SENTINEL_TIMEOUT_7Q",
                     "SENTINEL_TIMEOUT_7Q")):
                check_invalid(
                    label, synthetic,
                    {"meta": True, "max_output_tokens": 64,
                     "timeout_seconds": timeout},
                    sentinel)

            def check_valid(label, env, kwargs, expect_provider,
                            credential=None):
                ctor_before = provider_ctor.call_count
                factory_before = factory.call_count
                with self._controlled_env(env):
                    app = launch.create_local_app(**kwargs)
                self.assertIs(app, app_instance, label)
                self.assertTrue(
                    factory.call_count == factory_before + 1, label)
                self.assertTrue(
                    provider_ctor.call_count
                    == ctor_before + (1 if expect_provider else 0),
                    label)
                _, factory_kwargs = factory.call_args
                port = kwargs.get("port", 8000)
                self.assertTrue(
                    factory_kwargs["allowed_hosts"]
                    == (f"127.0.0.1:{port}",),
                    label)
                self.assertTrue(
                    factory_kwargs["allowed_origin"]
                    == f"http://127.0.0.1:{port}",
                    label)
                if expect_provider:
                    self.assertIs(
                        factory_kwargs["provider"],
                        provider_instance, label)
                    _, ctor_kwargs = provider_ctor.call_args
                    if credential is not None:
                        self._assert_secret_equal(
                            ctor_kwargs["credential"], credential)
                else:
                    self.assertIs(
                        factory_kwargs["provider"], None, label)

            check_valid("port_minimum", _ABSENT, {"port": 1}, False)
            check_valid(
                "port_maximum", _ABSENT, {"port": 65535}, False)
            check_valid("default_port", synthetic, {}, False)
            check_valid(
                "meta_token_and_int_timeout_boundary", synthetic,
                {"meta": True, "max_output_tokens": 16,
                 "timeout_seconds": 1},
                True, synthetic)
            check_valid(
                "meta_small_positive_timeout", synthetic,
                {"meta": True, "max_output_tokens": 17,
                 "timeout_seconds": 0.001},
                True)
            check_valid(
                "credential_ascii_low_boundary", "!",
                {"meta": True, "max_output_tokens": 16,
                 "timeout_seconds": 2.5},
                True, "!")
            check_valid(
                "credential_ascii_high_boundary", "~",
                {"meta": True, "max_output_tokens": 16,
                 "timeout_seconds": 2.5},
                True, "~")
        self.assertTrue(server_run.call_count == 0)

    def test_main_is_loopback_single_process_and_refuses_unsafe_options(
            self):
        self.assertTrue(
            LAUNCH_PATH.is_file(),
            "launch.py must exist at the repository root")
        transport = self._existing("teacher_demo.meta_transport")
        provider_module = self._existing(
            "teacher_demo.meta_provider")
        uvicorn = importlib.import_module("uvicorn")
        provider_instance = mock.Mock(name="provider")
        provider_ctor = mock.MagicMock(
            return_value=provider_instance)
        run_payload = mock.Mock(
            side_effect=AssertionError("transport invoked"))
        server_run = mock.Mock(return_value=None)
        synthetic = "SYNTHETIC_MAIN_CREDENTIAL_7Q"
        expected_server = {
            "host": "127.0.0.1", "workers": 1, "reload": False,
            "access_log": False}
        with mock.patch.object(
                transport, "run_payload", run_payload), \
                mock.patch.object(
                    provider_module, "MetaProvider",
                    provider_ctor), \
                mock.patch.object(uvicorn, "run", server_run):
            launch = self._load_launch()

            def run_main(argv, env=_ABSENT):
                stdout, stderr = io.StringIO(), io.StringIO()
                with self._controlled_env(env), \
                        contextlib.redirect_stdout(stdout), \
                        contextlib.redirect_stderr(stderr):
                    try:
                        result = launch.main(list(argv))
                    except SystemExit:
                        self.fail(
                            "main must return its exit code, "
                            "not raise SystemExit here")
                return result, stdout.getvalue(), stderr.getvalue()

            def check_served(argv, env, port, provider):
                server_before = server_run.call_count
                result, _, _ = run_main(argv, env)
                self.assertTrue(type(result) is int and result == 0)
                self.assertTrue(
                    server_run.call_count == server_before + 1)
                args, kwargs = server_run.call_args
                self.assertTrue(len(args) == 1)
                self.assertEqual(
                    kwargs, {**expected_server, "port": port})
                self.assertIs(args[0].state.teacher_provider, provider)
                return args[0]

            check_served([], _ABSENT, 8000, None)
            self.assertTrue(provider_ctor.call_count == 0)
            check_served(["--port", "8123"], _ABSENT, 8123, None)
            check_served(
                ["--port", "8124", "--meta",
                 "--max-output-tokens", "32",
                 "--timeout-seconds", "7.5"],
                synthetic, 8124, provider_instance)
            self.assertTrue(provider_ctor.call_count == 1)
            _, ctor_kwargs = provider_ctor.call_args
            self._assert_secret_equal(
                ctor_kwargs["credential"], synthetic)
            self.assertTrue(
                type(ctor_kwargs["max_output_tokens"]) is int
                and ctor_kwargs["max_output_tokens"] == 32)
            self.assertTrue(ctor_kwargs["timeout_seconds"] == 7.5)
            self.assertTrue(provider_instance.call_count == 0)

            valid_meta = [
                "--meta", "--max-output-tokens", "32",
                "--timeout-seconds", "5"]
            refusals = (
                ("host_option",
                 ["--host", "SENTINEL_HOST_7Q"], _ABSENT,
                 "SENTINEL_HOST_7Q"),
                ("host_equals",
                 ["--host=SENTINEL_HOST_EQ_7Q"], _ABSENT,
                 "SENTINEL_HOST_EQ_7Q"),
                ("workers_option",
                 ["--workers=SENTINEL_WORKERS_7Q"], _ABSENT,
                 "SENTINEL_WORKERS_7Q"),
                ("reload_option", ["--reload"], _ABSENT, None),
                ("api_key_option",
                 ["--api-key", "SENTINEL_API_KEY_7Q"], _ABSENT,
                 "SENTINEL_API_KEY_7Q"),
                ("model_option",
                 ["--model", "SENTINEL_MODEL_7Q"], _ABSENT,
                 "SENTINEL_MODEL_7Q"),
                ("unknown_option",
                 ["--unknown-sentinel-7q"], _ABSENT,
                 "unknown-sentinel-7q"),
                ("positional", ["SENTINEL_POSITIONAL_7Q"], _ABSENT,
                 "SENTINEL_POSITIONAL_7Q"),
                ("port_text",
                 ["--port", "SENTINEL_PORT_7Q"], _ABSENT,
                 "SENTINEL_PORT_7Q"),
                ("port_zero", ["--port", "0"], _ABSENT, None),
                ("port_too_large", ["--port=65536"], _ABSENT, None),
                ("port_negative", ["--port=-1"], _ABSENT, None),
                ("meta_without_options", ["--meta"], _ABSENT, None),
                ("meta_without_key", valid_meta, _ABSENT, None),
                ("meta_with_invalid_key", valid_meta,
                 "SENTINEL_BAD_KEY_7Q value",
                 "SENTINEL_BAD_KEY_7Q"),
                ("tokens_below_minimum",
                 ["--meta", "--max-output-tokens", "8",
                  "--timeout-seconds", "5"], synthetic, None),
                ("tokens_text",
                 ["--meta", "--max-output-tokens",
                  "SENTINEL_TOKENS_7Q", "--timeout-seconds", "5"],
                 synthetic, "SENTINEL_TOKENS_7Q"),
                ("timeout_zero",
                 ["--meta", "--max-output-tokens", "32",
                  "--timeout-seconds", "0"], synthetic, None),
                ("timeout_nan",
                 ["--meta", "--max-output-tokens", "32",
                  "--timeout-seconds", "nan"], synthetic, None),
                ("timeout_inf",
                 ["--meta", "--max-output-tokens", "32",
                  "--timeout-seconds", "inf"], synthetic, None),
                ("timeout_text",
                 ["--meta", "--max-output-tokens", "32",
                  "--timeout-seconds", "SENTINEL_TIMEOUT_7Q"],
                 synthetic, "SENTINEL_TIMEOUT_7Q"),
            )
            refusal_stderr = set()
            for label, argv, env, sentinel in refusals:
                server_before = server_run.call_count
                ctor_before = provider_ctor.call_count
                result, stdout, stderr = run_main(argv, env)
                self.assertTrue(
                    type(result) is int and result == 2, label)
                self.assertTrue(
                    server_run.call_count == server_before, label)
                self.assertTrue(
                    provider_ctor.call_count == ctor_before, label)
                self.assertTrue(stdout == "", label)
                self.assertTrue(stderr.strip() != "", label)
                self._assert_no_leak(synthetic, stdout, stderr)
                if sentinel is not None:
                    self._assert_no_leak(sentinel, stdout, stderr)
                refusal_stderr.add(stderr)
            self.assertTrue(
                len(refusal_stderr) == 1,
                "refusal stderr must be one fixed message")
        self.assertTrue(run_payload.call_count == 0)
        self.assertTrue(provider_instance.call_count == 0)
        source = LAUNCH_PATH.read_text(encoding="utf-8")
        guard = re.search(
            r"if\s+__name__\s*==\s*[\"']__main__[\"']\s*:", source)
        self.assertTrue(
            guard is not None, "missing __main__ guard")
        self.assertTrue(
            "main(" in source[guard.end():],
            "__main__ guard must use main's return code")

    def test_disconnected_factory_serves_packaged_assets_and_denies_wrong_host(
            self):
        self.assertTrue(
            LAUNCH_PATH.is_file(),
            "launch.py must exist at the repository root")
        launch = self._load_launch()
        transport = self._existing("teacher_demo.meta_transport")
        http_client = self._existing("teacher_demo.http_client")
        test_client = importlib.import_module(
            "fastapi.testclient").TestClient
        expected_assets = {
            "/": ("index.html", "text/html; charset=utf-8"),
            "/assets/workspace.css": (
                "workspace.css", "text/css; charset=utf-8"),
            "/assets/workspace.js": (
                "workspace.js", "text/javascript; charset=utf-8"),
            "/assets/review.js": (
                "review.js", "text/javascript; charset=utf-8"),
            "/assets/review-render.js": (
                "review-render.js",
                "text/javascript; charset=utf-8"),
        }
        self.assertTrue(dict(http_client._ASSETS) == expected_assets)
        run_payload = mock.Mock(
            side_effect=AssertionError("transport invoked"))
        with mock.patch.object(
                transport, "run_payload", run_payload), \
                self._controlled_env(
                    "SYNTHETIC_AMBIENT_CREDENTIAL_7Q"):
            app = launch.create_local_app()
            self.assertIs(app.state.teacher_provider, None)
            with test_client(
                    app, base_url="http://127.0.0.1:8000") as client:
                for route, (filename, media_type) in (
                        expected_assets.items()):
                    response = client.get(route)
                    expected_bytes = (
                        http_client._STATIC_DIR / filename
                    ).read_bytes()
                    self.assertTrue(
                        response.status_code == 200, route)
                    self.assertTrue(
                        response.content == expected_bytes, route)
                    self.assertTrue(
                        response.headers.get("content-type")
                        == media_type,
                        route)
                    self.assertTrue(
                        response.headers.get("cache-control")
                        == "no-store",
                        route)
                    self.assertTrue(
                        response.headers.get(
                            "content-security-policy")
                        == http_client._CONTENT_SECURITY_POLICY,
                        route)
                    self.assertTrue(
                        response.headers.get(
                            "x-content-type-options") == "nosniff",
                        route)
                wrong_host = client.get(
                    "/", headers={"host": "localhost:8000"})
                self.assertTrue(wrong_host.status_code == 400)
                wrong_port = client.get(
                    "/assets/workspace.js",
                    headers={"host": "127.0.0.1:9999"})
                self.assertTrue(wrong_port.status_code == 400)
        self.assertTrue(run_payload.call_count == 0)
