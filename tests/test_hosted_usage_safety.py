"""Real-SQLite safety tests for durable hosted-usage accounting.

Every case uses genuine temporary SQLite store files, the genuine
``BudgetedProvider`` wrapper, and genuine ``ProviderCallContext``
objects. Delegates are tiny synthetic callables; there is no network,
credential, route, or live-model access. Child interpreters are owned
``sys.executable -c`` processes with a key-free environment, DEVNULL
stdin, and bounded timeouts. Temporary directories are cleaned by
their context manager with no ignored cleanup errors, so a leaked
SQLite connection fails loudly instead of passing silently.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from teacher_demo import hosted_usage
from teacher_demo.meta_provider import ProviderCallContext


class HostedUsageSafetyTests(unittest.TestCase):
    """Four direct safety cases over real on-disk usage stores."""

    def test_concurrent_final_permit_across_threads_and_interpreter(self):
        fixed_now = 1900000000.0
        window_end = 2000000000.0
        policy = hosted_usage.UsagePolicy(
            max_attempts=1, max_output_tokens=64,
            window_end=window_end, minimum_spacing_seconds=1.0)
        child_code = (
            "import os, sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from teacher_demo import hosted_usage as usage\n"
            "from teacher_demo.meta_provider import ProviderCallContext\n"
            "usage._now = lambda: float(sys.argv[5])\n"
            "policy = usage.UsagePolicy(\n"
            "    max_attempts=1, max_output_tokens=64,\n"
            "    window_end=float(sys.argv[6]),\n"
            "    minimum_spacing_seconds=1.0)\n"
            "def delegate(request, *, context):\n"
            "    return 'ok'\n"
            "wrapper = usage.BudgetedProvider(\n"
            "    delegate, usage.UsageLedger(sys.argv[2], policy))\n"
            "open(sys.argv[3], 'w').close()\n"
            "for _ in range(1500):\n"
            "    if os.path.exists(sys.argv[4]):\n"
            "        break\n"
            "    time.sleep(0.01)\n"
            "try:\n"
            "    result = wrapper(None, context=ProviderCallContext(60))\n"
            "    print('OK' if result == 'ok' else 'BAD', flush=True)\n"
            "except usage.UsageDenied as error:\n"
            "    print('DENIED ' + error.code, flush=True)\n"
            "fresh = usage.UsageLedger(sys.argv[2], policy).snapshot()\n"
            "print('SPENT %d' % fresh['spent'], flush=True)\n"
        )
        env = {key: os.environ[key] for key in
               ("PATH", "SystemRoot", "windir", "TEMP", "TMP", "HOME",
                "USERPROFILE", "LANG") if key in os.environ}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        real_now = hosted_usage._now
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = root / "usage.db"
            ready = root / "child-ready"
            go = root / "child-go"
            hosted_usage.initialize_usage(store, policy)
            ledger = hosted_usage.UsageLedger(store, policy)
            delegate_calls = []

            def delegate(request, *, context):
                delegate_calls.append(request)
                return "ok"

            wrapper = hosted_usage.BudgetedProvider(delegate, ledger)
            start = threading.Event()
            outcomes = [None] * 6

            def contender(index):
                start.wait(timeout=15)
                try:
                    result = wrapper(None, context=ProviderCallContext(60))
                    outcomes[index] = ("ok", result)
                except hosted_usage.UsageDenied as error:
                    outcomes[index] = ("denied", error.code)
                except BaseException as error:
                    outcomes[index] = ("error", type(error).__name__)

            threads = [threading.Thread(target=contender, args=(index,))
                       for index in range(6)]
            started = []
            child = None
            child_lines = []
            hosted_usage._now = lambda: fixed_now
            try:
                child = subprocess.Popen(
                    [sys.executable, "-c", child_code, str(_SRC),
                     str(store), str(ready), str(go), repr(fixed_now),
                     repr(window_end)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, env=env, text=True)
                for thread in threads:
                    thread.start()
                    started.append(thread)
                for _ in range(1000):
                    if ready.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(ready.exists())
                go.touch()
                start.set()
                out, _err = child.communicate(timeout=30)
                child_lines = out.splitlines()
            finally:
                start.set()
                if not go.exists():
                    go.touch()
                for thread in started:
                    thread.join(timeout=15)
                if child is not None and child.poll() is None:
                    child.kill()
                    child.communicate(timeout=10)
                hosted_usage._now = real_now
            self.assertTrue(all(not thread.is_alive()
                                for thread in started))
            self.assertEqual([item for item in outcomes
                              if item is None or item[0] == "error"], [])
            self.assertEqual(child.returncode, 0)
            thread_wins = [item for item in outcomes
                           if item == ("ok", "ok")]
            thread_denied = [item for item in outcomes
                             if item == ("denied", "usage_exhausted")]
            self.assertEqual(len(thread_wins) + len(thread_denied), 6)
            child_wins = 1 if "OK" in child_lines else 0
            child_denied = 1 if "DENIED usage_exhausted" in child_lines else 0
            self.assertEqual(child_wins + child_denied, 1)
            self.assertEqual(len(thread_wins) + child_wins, 1)
            self.assertEqual(len(delegate_calls), len(thread_wins))
            self.assertIn("SPENT 1", child_lines)
            fresh = hosted_usage.UsageLedger(store, policy).snapshot()
            self.assertEqual(fresh["spent"], 1)
            self.assertEqual(fresh["closed"], False)

    def test_crash_after_reserve_is_not_refunded(self):
        first_now = 1900000000.0
        window_end = 2000000000.0
        env = {key: os.environ[key] for key in
               ("PATH", "SystemRoot", "windir", "TEMP", "TMP", "HOME",
                "USERPROFILE", "LANG") if key in os.environ}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        delegate_crash_code = (
            "import os, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from teacher_demo import hosted_usage as usage\n"
            "from teacher_demo.meta_provider import ProviderCallContext\n"
            "usage._now = lambda: float(sys.argv[4])\n"
            "policy = usage.UsagePolicy(\n"
            "    max_attempts=int(sys.argv[6]), max_output_tokens=64,\n"
            "    window_end=float(sys.argv[5]),\n"
            "    minimum_spacing_seconds=1.0)\n"
            "def delegate(request, *, context):\n"
            "    handle = open(sys.argv[3], 'w')\n"
            "    handle.write('entered')\n"
            "    handle.close()\n"
            "    os._exit(7)\n"
            "wrapper = usage.BudgetedProvider(\n"
            "    delegate, usage.UsageLedger(sys.argv[2], policy))\n"
            "wrapper(None, context=ProviderCallContext(60))\n"
        )
        reserve_crash_code = (
            "import os, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "from teacher_demo import hosted_usage as usage\n"
            "from teacher_demo.meta_provider import ProviderCallContext\n"
            "usage._now = lambda: float(sys.argv[4])\n"
            "policy = usage.UsagePolicy(\n"
            "    max_attempts=int(sys.argv[6]), max_output_tokens=64,\n"
            "    window_end=float(sys.argv[5]),\n"
            "    minimum_spacing_seconds=1.0)\n"
            "real_ledger = usage.UsageLedger(sys.argv[2], policy)\n"
            "class ExitAfterReserve:\n"
            "    def reserve(self):\n"
            "        real_ledger.reserve()\n"
            "        os._exit(11)\n"
            "def delegate(request, *, context):\n"
            "    handle = open(sys.argv[3], 'w')\n"
            "    handle.write('entered')\n"
            "    handle.close()\n"
            "    return 'ok'\n"
            "wrapper = usage.BudgetedProvider(delegate, ExitAfterReserve())\n"
            "wrapper(None, context=ProviderCallContext(60))\n"
        )
        real_now = hosted_usage._now
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy_a = hosted_usage.UsagePolicy(
                max_attempts=2, max_output_tokens=64,
                window_end=window_end, minimum_spacing_seconds=1.0)
            store_a = root / "crash-in-delegate.db"
            marker_a = root / "delegate-entered-a"
            hosted_usage.initialize_usage(store_a, policy_a)
            done_a = subprocess.run(
                [sys.executable, "-c", delegate_crash_code, str(_SRC),
                 str(store_a), str(marker_a), repr(first_now),
                 repr(window_end), "2"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, text=True, timeout=30)
            self.assertEqual(done_a.returncode, 7)
            self.assertEqual(marker_a.read_text(), "entered")
            self.assertEqual(
                hosted_usage.UsageLedger(store_a, policy_a)
                .snapshot()["spent"], 1)
            calls = []

            def delegate(request, *, context):
                calls.append(request)
                return "ok"

            wrapper_a = hosted_usage.BudgetedProvider(
                delegate, hosted_usage.UsageLedger(store_a, policy_a))
            now_box = [first_now + 10.0]
            hosted_usage._now = lambda: now_box[0]
            try:
                self.assertEqual(
                    wrapper_a(None, context=ProviderCallContext(60)), "ok")
                self.assertEqual(len(calls), 1)
                self.assertEqual(
                    hosted_usage.UsageLedger(store_a, policy_a)
                    .snapshot()["spent"], 2)
                now_box[0] = first_now + 20.0
                with self.assertRaises(hosted_usage.UsageDenied) as caught:
                    wrapper_a(None, context=ProviderCallContext(60))
                self.assertEqual(caught.exception.code, "usage_exhausted")
                self.assertEqual(len(calls), 1)
                self.assertEqual(
                    hosted_usage.UsageLedger(store_a, policy_a)
                    .snapshot()["spent"], 2)
            finally:
                hosted_usage._now = real_now
            policy_b = hosted_usage.UsagePolicy(
                max_attempts=1, max_output_tokens=64,
                window_end=window_end, minimum_spacing_seconds=1.0)
            store_b = root / "crash-after-reserve.db"
            marker_b = root / "delegate-entered-b"
            hosted_usage.initialize_usage(store_b, policy_b)
            done_b = subprocess.run(
                [sys.executable, "-c", reserve_crash_code, str(_SRC),
                 str(store_b), str(marker_b), repr(first_now),
                 repr(window_end), "1"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env, text=True, timeout=30)
            self.assertEqual(done_b.returncode, 11)
            self.assertFalse(marker_b.exists())
            self.assertEqual(
                hosted_usage.UsageLedger(store_b, policy_b)
                .snapshot()["spent"], 1)

    def test_commit_ambiguity_and_runtime_store_failures_deny_without_delegate(self):
        window_end = 2000000000.0
        policy = hosted_usage.UsagePolicy(
            max_attempts=3, max_output_tokens=64,
            window_end=window_end, minimum_spacing_seconds=1.0)
        real_now = hosted_usage._now
        real_connect = sqlite3.connect
        now_box = [1900000000.0]
        mode = ["off"]

        class CommitProxy:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, parameters=()):
                if (isinstance(sql, str)
                        and sql.strip().upper() == "COMMIT"
                        and mode[0] != "off"):
                    if mode[0] == "after":
                        self._real.execute(sql, parameters).close()
                    raise sqlite3.OperationalError(
                        "injected-commit-failure-marker")
                return self._real.execute(sql, parameters)

            def close(self):
                return self._real.close()

            def __getattr__(self, name):
                return getattr(self._real, name)

        def proxied_connect(*args, **kwargs):
            return CommitProxy(real_connect(*args, **kwargs))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hosted_usage._now = lambda: now_box[0]
            try:
                for mode_name, expected_spent in (("before", 0),
                                                  ("after", 1)):
                    store = root / ("commit-" + mode_name + ".db")
                    hosted_usage.initialize_usage(store, policy)
                    calls = []

                    def delegate(request, *, context, _calls=calls):
                        _calls.append(request)
                        return "ok"

                    wrapper = hosted_usage.BudgetedProvider(
                        delegate, hosted_usage.UsageLedger(store, policy))
                    sqlite3.connect = proxied_connect
                    try:
                        mode[0] = mode_name
                        with self.assertRaises(
                                hosted_usage.UsageDenied) as caught:
                            wrapper(None, context=ProviderCallContext(60))
                    finally:
                        mode[0] = "off"
                        sqlite3.connect = real_connect
                    error = caught.exception
                    self.assertEqual(
                        error.code, "usage_accounting_unavailable")
                    self.assertEqual(
                        str(error), "usage_accounting_unavailable")
                    self.assertIsNone(error.__cause__)
                    self.assertIsNone(error.__context__)
                    self.assertNotIn(
                        "injected-commit-failure-marker", repr(error))
                    self.assertNotIn("COMMIT", repr(error))
                    self.assertNotIn(str(store), repr(error))
                    self.assertEqual(calls, [])
                    self.assertEqual(
                        hosted_usage.UsageLedger(store, policy)
                        .snapshot()["spent"], expected_spent)
                    now_box[0] += 10.0
                    self.assertEqual(
                        wrapper(None, context=ProviderCallContext(60)),
                        "ok")
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(
                        hosted_usage.UsageLedger(store, policy)
                        .snapshot()["spent"], expected_spent + 1)
                    now_box[0] += 10.0
                    store.unlink()
                missing = root / "missing.db"
                missing_calls = []

                def never_delegate(request, *, context):
                    missing_calls.append(request)
                    return "ok"

                missing_wrapper = hosted_usage.BudgetedProvider(
                    never_delegate,
                    hosted_usage.UsageLedger(missing, policy))
                with self.assertRaises(
                        hosted_usage.UsageDenied) as caught_missing:
                    missing_wrapper(None, context=ProviderCallContext(60))
                self.assertEqual(
                    caught_missing.exception.code,
                    "usage_accounting_unavailable")
                self.assertEqual(missing_calls, [])
                self.assertFalse(missing.exists())
                corrupt = root / "corrupt.db"
                original_bytes = b"not-a-sqlite-store" * 16
                corrupt.write_bytes(original_bytes)
                corrupt_calls = []

                def corrupt_delegate(request, *, context):
                    corrupt_calls.append(request)
                    return "ok"

                corrupt_wrapper = hosted_usage.BudgetedProvider(
                    corrupt_delegate,
                    hosted_usage.UsageLedger(corrupt, policy))
                with self.assertRaises(
                        hosted_usage.UsageDenied) as caught_corrupt:
                    corrupt_wrapper(None, context=ProviderCallContext(60))
                self.assertEqual(
                    caught_corrupt.exception.code,
                    "usage_accounting_unavailable")
                self.assertIsNone(caught_corrupt.exception.__cause__)
                self.assertIsNone(caught_corrupt.exception.__context__)
                self.assertEqual(corrupt_calls, [])
                self.assertEqual(corrupt.read_bytes(), original_bytes)
                corrupt.unlink()
            finally:
                hosted_usage._now = real_now
                sqlite3.connect = real_connect

    def test_window_freshness_across_real_lock_wait(self):
        window_end = 2000000000.0
        policy = hosted_usage.UsagePolicy(
            max_attempts=1, max_output_tokens=64,
            window_end=window_end, minimum_spacing_seconds=1.0)
        real_now = hosted_usage._now
        real_connect = sqlite3.connect
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for label, advance in (("stale", True), ("healthy", False)):
                store = root / (label + ".db")
                hosted_usage.initialize_usage(store, policy)
                now_box = [1900000000.0]
                entered = threading.Event()

                class RecordingProxy:
                    def __init__(self, real, _entered=entered):
                        self._real = real
                        self._entered = _entered

                    def execute(self, sql, parameters=()):
                        return self._real.execute(sql, parameters)

                    def close(self):
                        return self._real.close()

                    def __getattr__(self, name):
                        return getattr(self._real, name)

                def recording_connect(*args, **kwargs):
                    entered.set()
                    return RecordingProxy(real_connect(*args, **kwargs))

                calls = []

                def delegate(request, *, context, _calls=calls):
                    _calls.append(request)
                    return "ok"

                wrapper = hosted_usage.BudgetedProvider(
                    delegate, hosted_usage.UsageLedger(store, policy))
                outcome = []

                def worker(_wrapper=wrapper, _outcome=outcome):
                    try:
                        result = _wrapper(
                            None, context=ProviderCallContext(60))
                        _outcome.append(("ok", result))
                    except hosted_usage.UsageDenied as error:
                        _outcome.append(("denied", error.code))
                    except BaseException as error:
                        _outcome.append(("error", type(error).__name__))

                holder = sqlite3.connect(
                    str(store), timeout=5.0, isolation_level=None)
                thread = threading.Thread(target=worker)
                started = [False]
                hosted_usage._now = lambda _box=now_box: _box[0]
                sqlite3.connect = recording_connect
                try:
                    holder.execute("BEGIN EXCLUSIVE").close()
                    holder.execute(
                        "UPDATE usage_state SET spent = spent "
                        "WHERE id = 1").close()
                    thread.start()
                    started[0] = True
                    self.assertTrue(entered.wait(timeout=10))
                    if advance:
                        now_box[0] = window_end + 1.0
                    holder.execute("COMMIT").close()
                    holder.close()
                    holder = None
                finally:
                    if holder is not None:
                        if holder.in_transaction:
                            holder.execute("ROLLBACK").close()
                        holder.close()
                    if started[0]:
                        thread.join(timeout=15)
                    sqlite3.connect = real_connect
                    hosted_usage._now = real_now
                self.assertFalse(thread.is_alive())
                snapshot = hosted_usage.UsageLedger(
                    store, policy).snapshot()
                if advance:
                    self.assertEqual(
                        outcome, [("denied", "usage_window_closed")])
                    self.assertEqual(calls, [])
                    self.assertEqual(snapshot["spent"], 0)
                    self.assertEqual(snapshot["closed"], True)
                    self.assertIsNone(snapshot["last_start"])
                    hosted_usage._now = lambda: window_end + 2.0
                    try:
                        with self.assertRaises(
                                hosted_usage.UsageDenied) as caught:
                            wrapper(None, context=ProviderCallContext(60))
                    finally:
                        hosted_usage._now = real_now
                    self.assertEqual(
                        caught.exception.code, "usage_window_closed")
                    self.assertEqual(calls, [])
                    self.assertEqual(
                        hosted_usage.UsageLedger(store, policy)
                        .snapshot()["spent"], 0)
                else:
                    self.assertEqual(outcome, [("ok", "ok")])
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(snapshot["spent"], 1)
                    self.assertEqual(snapshot["closed"], False)


if __name__ == "__main__":
    unittest.main()
