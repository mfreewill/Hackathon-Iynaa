"""First runnable tracer tests for ``teacher_demo.hosted_usage``.

Direct contract tests only: real SQLite stores in temporary
directories, the genuine ``ProviderCallContext``, synthetic data.
The module-level ``_now()`` is the only test seam; each test replaces
it with a lambda returning a controlled epoch float and leaves
``time.monotonic`` and the context untouched. Temporary directories
ignore cleanup errors so a ledger-held SQLite connection cannot fail
teardown on platforms that lock open files.
"""

import importlib.util
import math
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from teacher_demo.meta_protocol import MetaProtocolError  # noqa: E402
from teacher_demo.meta_provider import ProviderCallContext  # noqa: E402

NOW = 1_700_000_000.0
DENIED_CODES = {
    "usage_exhausted",
    "usage_window_closed",
    "usage_rate_limited",
    "usage_accounting_unavailable",
}
POLICY_KEYS = (
    "max_attempts",
    "max_output_tokens",
    "window_end",
    "minimum_spacing_seconds",
)


def _spent(ledger):
    return ledger.snapshot()["spent"]


def _policy_values(policy):
    if isinstance(policy, dict):
        return {key: policy[key] for key in POLICY_KEYS}
    return {key: getattr(policy, key) for key in POLICY_KEYS}


class HostedUsageTracerTests(unittest.TestCase):

    def test_policy_init_and_exhaustion(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.hosted_usage"))
        import teacher_demo.hosted_usage as usage

        clock = [NOW]
        original_now = usage._now
        usage._now = lambda: clock[0]
        self.addCleanup(setattr, usage, "_now", original_now)

        good = {
            "max_attempts": 2,
            "max_output_tokens": 64,
            "window_end": NOW + 3600.0,
            "minimum_spacing_seconds": 5.0,
        }
        policy = usage.UsagePolicy(**good)
        self.assertEqual(policy.max_attempts, 2)
        self.assertEqual(policy.max_output_tokens, 64)
        self.assertEqual(policy.window_end, NOW + 3600.0)
        self.assertEqual(policy.minimum_spacing_seconds, 5.0)
        # Boundary values and plain int epoch/spacing are accepted.
        edge = usage.UsagePolicy(
            max_attempts=1, max_output_tokens=16,
            window_end=int(NOW) + 3600, minimum_spacing_seconds=5)
        self.assertEqual(edge.max_output_tokens, 16)
        # Arguments are keyword-only.
        with self.assertRaises(TypeError):
            usage.UsagePolicy(2, 64, NOW + 3600.0, 5.0)

        bad = (
            [{"max_attempts": v} for v in (0, -1, True, 2.0, "2")]
            + [{"max_output_tokens": v}
               for v in (15, 0, -16, True, 64.0, "64")]
            + [{"window_end": v}
               for v in (0.0, -1.0, True, math.nan, math.inf,
                         -math.inf, "soon")]
            + [{"minimum_spacing_seconds": v}
               for v in (0.0, -1.0, False, math.nan, math.inf, "5")]
        )
        for override in bad:
            with self.subTest(override=override):
                with self.assertRaises((ValueError, TypeError)):
                    usage.UsagePolicy(**{**good, **override})

        with self.assertRaises((AttributeError, TypeError)):
            policy.max_attempts = 99
        self.assertEqual(policy.max_attempts, 2)

        with tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "usage.sqlite3"
            usage.initialize_usage(path, policy)
            self.assertTrue(path.is_file())

            ledger = usage.UsageLedger(path, policy)
            snap = ledger.snapshot()
            self.assertTrue(
                {"version", "policy", "spent", "last_start", "closed"}
                <= set(snap))
            self.assertEqual(snap["version"], 1)
            self.assertEqual(snap["spent"], 0)
            self.assertIsNone(snap["last_start"])
            self.assertFalse(snap["closed"])
            self.assertEqual(_policy_values(snap["policy"]), good)

            ledger.reserve()
            snap = ledger.snapshot()
            self.assertEqual(snap["spent"], 1)
            self.assertEqual(snap["last_start"], NOW)
            del ledger

            reopened = usage.UsageLedger(path, policy)
            self.assertEqual(_spent(reopened), 1)

            clock[0] = NOW + 10.0
            reopened.reserve()
            self.assertEqual(_spent(reopened), 2)
            self.assertEqual(reopened.snapshot()["last_start"], NOW + 10.0)

            clock[0] = NOW + 20.0
            with self.assertRaises(usage.UsageDenied) as caught:
                reopened.reserve()
            self.assertEqual(caught.exception.code, "usage_exhausted")
            self.assertIn(caught.exception.code, DENIED_CODES)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertEqual(_spent(reopened), 2)

            before = path.read_bytes()
            with self.assertRaises(
                    (usage.UsageDenied, ValueError, OSError)):
                usage.initialize_usage(path, policy)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(_spent(usage.UsageLedger(path, policy)), 2)

    def test_missing_corrupt_mismatch_and_locked_store_refuse(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.hosted_usage"))
        import teacher_demo.hosted_usage as usage

        clock = [NOW]
        original_now = usage._now
        usage._now = lambda: clock[0]
        self.addCleanup(setattr, usage, "_now", original_now)

        params = {
            "max_attempts": 3,
            "max_output_tokens": 64,
            "window_end": NOW + 3600.0,
            "minimum_spacing_seconds": 5.0,
        }
        policy = usage.UsagePolicy(**params)

        def assert_public_error(error):
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            text = str(error) + " " + repr(error)
            self.assertNotIn("SENTINELPATH7f3", text)
            self.assertNotIn("SENTINELBYTES9a1", text)
            if isinstance(error, usage.UsageDenied):
                self.assertEqual(
                    error.code, "usage_accounting_unavailable")
            else:
                self.assertIsInstance(error, ValueError)

        def assert_refused(path, open_policy):
            # The constructor may refuse, or defer; if it defers,
            # reserve must refuse with UsageDenied instead.
            try:
                ledger = usage.UsageLedger(path, open_policy)
            except Exception as error:
                assert_public_error(error)
                return
            with self.assertRaises(usage.UsageDenied) as caught:
                ledger.reserve()
            assert_public_error(caught.exception)

        with tempfile.TemporaryDirectory(
                prefix="SENTINELPATH7f3-",
                ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)

            absent = root / "absent.sqlite3"
            assert_refused(absent, policy)
            self.assertFalse(absent.exists())

            corrupt = root / "corrupt.sqlite3"
            corrupt.write_bytes(b"not sqlite\x00SENTINELBYTES9a1")
            before = corrupt.read_bytes()
            assert_refused(corrupt, policy)
            self.assertEqual(corrupt.read_bytes(), before)

            mismatches = (
                ("attempts", {"max_attempts": 4}),
                ("output", {"max_output_tokens": 65}),
                ("window", {"window_end": NOW + 3601.0}),
                ("spacing", {"minimum_spacing_seconds": 6.0}),
            )
            for name, override in mismatches:
                with self.subTest(mismatch=name):
                    store = root / f"mismatch-{name}.sqlite3"
                    usage.initialize_usage(store, policy)
                    before = store.read_bytes()
                    assert_refused(
                        store,
                        usage.UsagePolicy(**{**params, **override}))
                    self.assertEqual(store.read_bytes(), before)
                    self.assertEqual(
                        _spent(usage.UsageLedger(store, policy)), 0)

            missing_dir = root / "no-such-dir"
            with self.assertRaises(
                    (usage.UsageDenied, ValueError, OSError)):
                usage.initialize_usage(missing_dir / "x.sqlite3", policy)
            self.assertFalse(missing_dir.exists())

            locked = root / "locked.sqlite3"
            usage.initialize_usage(locked, policy)
            ledger = usage.UsageLedger(locked, policy)
            self.assertEqual(_spent(ledger), 0)
            external = sqlite3.connect(str(locked))
            try:
                external.execute("BEGIN EXCLUSIVE")
                started = time.monotonic()
                with self.assertRaises(usage.UsageDenied) as caught:
                    ledger.reserve()
                elapsed = time.monotonic() - started
            finally:
                external.close()
            self.assertEqual(
                caught.exception.code, "usage_accounting_unavailable")
            assert_public_error(caught.exception)
            self.assertLess(elapsed, 20.0)
            self.assertEqual(_spent(ledger), 0)

            clock[0] = NOW + 10.0
            ledger.reserve()
            self.assertEqual(_spent(ledger), 1)

    def test_window_latch_and_pacing(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.hosted_usage"))
        import teacher_demo.hosted_usage as usage

        clock = [NOW]
        original_now = usage._now
        usage._now = lambda: clock[0]
        self.addCleanup(setattr, usage, "_now", original_now)

        def assert_denied(ledger, code):
            with self.assertRaises(usage.UsageDenied) as caught:
                ledger.reserve()
            self.assertEqual(caught.exception.code, code)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)

        with tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)

            window_policy = usage.UsagePolicy(
                max_attempts=3, max_output_tokens=64,
                window_end=NOW + 100.0, minimum_spacing_seconds=5.0)
            window_path = root / "window.sqlite3"
            usage.initialize_usage(window_path, window_policy)
            window = usage.UsageLedger(window_path, window_policy)
            window.reserve()
            self.assertEqual(_spent(window), 1)

            clock[0] = NOW + 100.0
            assert_denied(window, "usage_window_closed")
            self.assertEqual(_spent(window), 1)
            self.assertTrue(window.snapshot()["closed"])

            clock[0] = NOW + 10.0
            assert_denied(window, "usage_window_closed")
            self.assertEqual(_spent(window), 1)
            self.assertEqual(window.snapshot()["last_start"], NOW)
            del window
            reopened = usage.UsageLedger(window_path, window_policy)
            self.assertTrue(reopened.snapshot()["closed"])
            assert_denied(reopened, "usage_window_closed")
            self.assertEqual(_spent(reopened), 1)

            pacing_policy = usage.UsagePolicy(
                max_attempts=3, max_output_tokens=64,
                window_end=NOW + 3600.0, minimum_spacing_seconds=30.0)
            pacing_path = root / "pacing.sqlite3"
            usage.initialize_usage(pacing_path, pacing_policy)
            pacing = usage.UsageLedger(pacing_path, pacing_policy)
            clock[0] = NOW
            pacing.reserve()
            self.assertEqual(_spent(pacing), 1)

            assert_denied(pacing, "usage_rate_limited")
            clock[0] = NOW + 29.0
            assert_denied(pacing, "usage_rate_limited")
            self.assertEqual(_spent(pacing), 1)

            clock[0] = NOW + 30.0
            pacing.reserve()
            self.assertEqual(_spent(pacing), 2)
            self.assertEqual(pacing.snapshot()["last_start"], NOW + 30.0)

            clock[0] = NOW + 10.0
            assert_denied(pacing, "usage_rate_limited")
            self.assertEqual(_spent(pacing), 2)
            self.assertEqual(pacing.snapshot()["last_start"], NOW + 30.0)

            clock[0] = NOW + 60.0
            pacing.reserve()
            self.assertEqual(_spent(pacing), 3)
            self.assertEqual(pacing.snapshot()["last_start"], NOW + 60.0)

    def test_wrapper_identity_shared_ledger_and_spent_on_failure(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.hosted_usage"))
        import teacher_demo.hosted_usage as usage

        clock = [NOW]
        original_now = usage._now
        usage._now = lambda: clock[0]
        self.addCleanup(setattr, usage, "_now", original_now)

        policy = usage.UsagePolicy(
            max_attempts=2, max_output_tokens=64,
            window_end=NOW + 3600.0, minimum_spacing_seconds=5.0)
        calls = []
        result = object()
        boom = RuntimeError("synthetic delegate failure")
        source_req = {"kind": "source", "text": "synthetic source"}
        question_req = {"kind": "question", "text": "synthetic question?"}
        extra_req = {"kind": "question", "text": "synthetic extra?"}

        def delegate(request, *, context):
            calls.append((request, context))
            if request is question_req:
                raise boom
            return result

        with tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "usage.sqlite3"
            usage.initialize_usage(path, policy)
            ledger = usage.UsageLedger(path, policy)
            provider = usage.BudgetedProvider(delegate, ledger)

            with self.assertRaises((ValueError, TypeError, AttributeError)):
                provider(source_req, context=object())
            self.assertEqual(calls, [])
            self.assertEqual(_spent(ledger), 0)

            ctx1 = ProviderCallContext(60.0)
            self.assertIs(provider(source_req, context=ctx1), result)
            self.assertEqual(len(calls), 1)
            self.assertIs(calls[0][0], source_req)
            self.assertIs(calls[0][1], ctx1)
            self.assertEqual(_spent(ledger), 1)

            clock[0] = NOW + 10.0
            ctx2 = ProviderCallContext(60.0)
            with self.assertRaises(RuntimeError) as caught:
                provider(question_req, context=ctx2)
            self.assertIs(caught.exception, boom)
            self.assertEqual(len(calls), 2)
            self.assertIs(calls[1][0], question_req)
            self.assertIs(calls[1][1], ctx2)
            self.assertEqual(_spent(ledger), 2)

            clock[0] = NOW + 20.0
            ctx3 = ProviderCallContext(60.0)
            with self.assertRaises(usage.UsageDenied) as denied:
                provider(extra_req, context=ctx3)
            self.assertEqual(denied.exception.code, "usage_exhausted")
            self.assertEqual(len(calls), 2)
            self.assertEqual(_spent(ledger), 2)

    def test_cancellation_before_and_after_reservation(self):
        self.assertIsNotNone(
            importlib.util.find_spec("teacher_demo.hosted_usage"))
        import teacher_demo.hosted_usage as usage

        clock = [NOW]
        original_now = usage._now
        usage._now = lambda: clock[0]
        self.addCleanup(setattr, usage, "_now", original_now)

        policy = usage.UsagePolicy(
            max_attempts=3, max_output_tokens=64,
            window_end=NOW + 3600.0, minimum_spacing_seconds=5.0)
        calls = []
        result = object()
        request = {"kind": "question", "text": "synthetic question?"}

        def delegate(sent, *, context):
            calls.append((sent, context))
            return result

        class CancelAfterReserve:
            """Observing proxy: real reserve first, then set the event."""

            def __init__(self, inner, event):
                self._inner = inner
                self._event = event

            def reserve(self, *args, **kwargs):
                out = self._inner.reserve(*args, **kwargs)
                self._event.set()
                return out

            def __getattr__(self, name):
                return getattr(self._inner, name)

        cancelled = (MetaProtocolError, usage.UsageDenied)
        with tempfile.TemporaryDirectory(
                ignore_cleanup_errors=True) as tmp:
            path = Path(tmp) / "usage.sqlite3"
            usage.initialize_usage(path, policy)
            ledger = usage.UsageLedger(path, policy)
            provider = usage.BudgetedProvider(delegate, ledger)

            event = threading.Event()
            event.set()
            ctx = ProviderCallContext(60.0, cancel_event=event)
            with self.assertRaises(cancelled):
                provider(request, context=ctx)
            self.assertEqual(calls, [])
            self.assertEqual(_spent(ledger), 0)

            event2 = threading.Event()
            proxied = usage.BudgetedProvider(
                delegate, CancelAfterReserve(ledger, event2))
            ctx2 = ProviderCallContext(60.0, cancel_event=event2)
            with self.assertRaises(cancelled):
                proxied(request, context=ctx2)
            self.assertTrue(event2.is_set())
            self.assertEqual(calls, [])
            self.assertEqual(_spent(ledger), 1)

            clock[0] = NOW + 10.0
            ctx3 = ProviderCallContext(60.0)
            self.assertIs(provider(request, context=ctx3), result)
            self.assertEqual(len(calls), 1)
            self.assertIs(calls[0][0], request)
            self.assertIs(calls[0][1], ctx3)
            self.assertEqual(_spent(ledger), 2)


if __name__ == "__main__":
    unittest.main()
