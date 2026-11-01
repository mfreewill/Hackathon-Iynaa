"""Pure self-tests for the PDF lifetime observer. Fakes only.

No decoder, no PDFium/native call, and no fake handle ever reaches C:
load/close are Python callbacks defined here.
"""
import pathlib
import sys
import unittest
import weakref

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pdf_lifetime_observer import LifetimeObserver  # noqa: E402


class FakeHandle:
    def __init__(self, address):
        self.address = address


class FakeOwner:
    pass


class FakeNative:
    def __init__(self, addresses):
        self._addresses = list(addresses)
        self.close_calls = []
        self.fail_next_close = False

    def load(self, data, size, password):
        return FakeHandle(self._addresses.pop(0))

    def close(self, handle):
        self.close_calls.append(handle)
        if self.fail_next_close:
            self.fail_next_close = False
            raise RuntimeError("fake underlying close failure")


def make_observer(addresses, lock_held=True):
    native = FakeNative(addresses)
    observer = LifetimeObserver(
        native.load,
        native.close,
        address_of=lambda handle: handle.address if handle else None,
        lock_state=lambda: lock_held,
        expected_acquisitions=len(addresses),
    )
    return native, observer


class LifetimeObserverSelfTests(unittest.TestCase):
    def test_ordinary_first_close_concretely_completes(self):
        native, observer = make_observer([111])
        source = b"source"
        with observer:
            handle = observer.load(source)
            self.assertEqual(observer.acquisition_count, 1)
            self.assertIs(observer.lifetimes[0].source, source)
            self.assertEqual(observer.lifetimes[0].state, "open")
            observer.close(handle)
            observer.assert_product_cleanup(expected_acquisitions=1)
        self.assertEqual(native.close_calls, [handle])
        self.assertEqual(observer.product_completion_count, 1)
        self.assertEqual(observer.teardown_completion_count, 0)

    def test_same_lifetime_duplicate_intercepted(self):
        native, observer = make_observer([111])
        with self.assertRaises(AssertionError):
            with observer:
                handle = observer.load(b"source")
                observer.close(handle)
                observer.close(handle)
                with self.assertRaises(AssertionError):
                    observer.assert_product_cleanup(
                        expected_acquisitions=1)
        self.assertEqual(native.close_calls, [handle])
        self.assertEqual(observer.duplicate_count, 1)

    def test_different_lifetime_reusing_same_address_closes(self):
        native, observer = make_observer([111, 111])
        with observer:
            first = observer.load(b"first")
            observer.close(first)
            second = observer.load(b"second")
            self.assertIsNot(first, second)
            observer.close(second)
            observer.assert_product_cleanup(expected_acquisitions=2)
        # Concrete underlying closes, not merely attempts: the
        # reused address's first close must not be suppressed.
        self.assertEqual(native.close_calls, [first, second])
        self.assertEqual(observer.product_completion_count, 2)
        self.assertEqual(observer.duplicate_count, 0)
        self.assertEqual(
            [item.generation for item in observer.lifetimes], [1, 2])

    def test_missing_cleanup_fails_product_and_teardown_closes(self):
        native, observer = make_observer([111])
        with self.assertRaises(AssertionError):
            with observer:
                handle = observer.load(b"source")
                with self.assertRaises(AssertionError):
                    observer.assert_product_cleanup(
                        expected_acquisitions=1)
        self.assertEqual(native.close_calls, [handle])
        self.assertEqual(observer.product_completion_count, 0)
        self.assertEqual(observer.teardown_completion_count, 1)

    def test_effect_then_exception_is_uncertain_and_not_retried(self):
        # Replaces the former retry-to-clean expectation (LTS-03):
        # the fake callback records its release effect, then raises.
        native, observer = make_observer([111])
        observer.expected_acquisitions = 1
        native.fail_next_close = True
        source = b"source"
        with self.assertRaises(AssertionError):
            with observer:
                handle = observer.load(source)
                with self.assertRaises(RuntimeError):
                    observer.close(handle)
                self.assertEqual(observer.lifetimes[0].state,
                                 "uncertain")
                self.assertIs(observer.lifetimes[0].source, source)
                observer.close(handle)  # explicit retry refused
                self.assertEqual(native.close_calls, [handle])
        # Teardown must not call the callback again either.
        self.assertEqual(native.close_calls, [handle])
        self.assertEqual(observer.product_completion_count, 0)
        self.assertEqual(observer.teardown_completion_count, 0)

    def test_unattributed_closes_refused_before_c(self):
        native, observer = make_observer([111, 111])
        with self.assertRaises(AssertionError):
            with observer:
                observer.close(FakeHandle(999))
                first = observer.load(b"first")
                second = observer.load(b"second")
                # Same address, unknown identity: never attributed.
                observer.close(FakeHandle(111))
                self.assertEqual(native.close_calls, [])
                observer.close(first)
                observer.close(second)
        self.assertEqual(observer.unattributed_count, 2)
        self.assertEqual(observer.ambiguous_count, 0)
        self.assertEqual(native.close_calls, [first, second])

    def test_finalizer_flushed_in_teardown_after_assertion(self):
        native, observer = make_observer([111])
        finalizer_ref = []
        with self.assertRaises(AssertionError):
            with observer:
                handle = observer.load(b"source")
                owner = FakeOwner()
                finalizer_ref.append(
                    weakref.finalize(owner, observer.close, handle))
                cycle = [owner]
                cycle.append(cycle)
                del owner, cycle
                raise AssertionError("simulated product assertion")
        self.assertEqual(native.close_calls, [handle])
        self.assertFalse(finalizer_ref[0].alive)
        self.assertEqual(observer.product_completion_count, 0)
        self.assertEqual(observer.teardown_completion_count, 1)


class _FakeFinInfo:
    pass


class _FakeFinOwner:
    def __init__(self, raw):
        self.raw = raw


class IdentityAndVerdictTests(unittest.TestCase):
    def test_stale_identity_cannot_complete_replacement(self):
        native, observer = make_observer([111, 111])
        with self.assertRaises(AssertionError):
            with observer:
                first = observer.load(b"first")
                observer.close(first)
                second = observer.load(b"second")
                observer.close(first)  # stale: refused pre-callback
                self.assertEqual(native.close_calls, [first])
                self.assertEqual(observer.lifetimes[1].state, "open")
                observer.close(second)
                self.assertEqual(native.close_calls, [first, second])

    def test_unknown_alias_cannot_complete_open_lifetime(self):
        native, observer = make_observer([111])
        with self.assertRaises(AssertionError):
            with observer:
                handle = observer.load(b"source")
                observer.close(FakeHandle(111))
                self.assertEqual(native.close_calls, [])
                self.assertEqual(observer.unattributed_count, 1)
                observer.close(handle)
        self.assertEqual(native.close_calls, [handle])

    def test_same_handle_acquired_twice_is_ambiguous(self):
        shared = FakeHandle(111)
        native = FakeNative([])
        observer = LifetimeObserver(
            lambda data, size, password: shared,
            native.close,
            address_of=lambda handle: handle.address if handle else None,
            lock_state=lambda: True,
            expected_acquisitions=2)
        with self.assertRaises(AssertionError):
            with observer:
                observer.load(b"first")
                observer.load(b"second")
                observer.close(shared)
                # One product-phase ambiguous refusal; teardown
                # retries per open lifetime are likewise refused.
                self.assertEqual(observer.ambiguous_count, 1)
        self.assertEqual(native.close_calls, [])
        self.assertGreaterEqual(observer.ambiguous_count, 1)

    def test_delayed_old_finalizer_cannot_complete_replacement(self):
        native, observer = make_observer([111, 111])
        with self.assertRaises(AssertionError):
            with observer:
                first = observer.load(b"first")
                owner = FakeOwner()
                finalizer = weakref.finalize(
                    owner, observer.close, first)
                observer.close(first)
                second = observer.load(b"second")
                del owner  # old finalizer fires in product phase
                self.assertFalse(finalizer.alive)
                self.assertEqual(native.close_calls, [first])
                self.assertEqual(observer.lifetimes[1].state, "open")
                observer.close(second)
        self.assertEqual(native.close_calls, [first, second])

    def test_traceback_held_installed_finalizer_is_verdict_bearing(self):
        native, observer = make_observer([111])
        observer.expected_acquisitions = 1

        def make_traceback_held(handle):
            owner = FakeOwner()
            info, fin_owner = _FakeFinInfo(), _FakeFinOwner(handle)

            def template(info_arg, owner_arg):
                observer.close(owner_arg.raw)

            weakref.finalize(owner, template, info, fin_owner)
            try:
                raise RuntimeError("holder")
            except RuntimeError as exc:
                return exc

        with self.assertRaises(AssertionError):
            with observer:
                handle = observer.load(b"source")
                observer.close(handle)
                kept = make_traceback_held(handle)
                # Interim product evidence looks clean; the verdict
                # after teardown must still fail on the second owner.
                observer.assert_product_cleanup(expected_acquisitions=1)
                del kept
        self.assertEqual(native.close_calls, [handle])

    def test_late_cyclic_owner_duplicate_is_verdict_bearing(self):
        native, observer = make_observer([111])
        observer.expected_acquisitions = 1
        with self.assertRaises(AssertionError):
            with observer:
                handle = observer.load(b"source")
                observer.close(handle)
                owner = FakeOwner()
                weakref.finalize(owner, observer.close, handle)
                cycle = [owner]
                cycle.append(cycle)
                del owner, cycle
                observer.assert_product_cleanup(expected_acquisitions=1)
        self.assertEqual(native.close_calls, [handle])
        self.assertGreaterEqual(observer.duplicate_count, 1)

    def test_unlocked_product_close_refused_by_verdict(self):
        native, observer = make_observer([111], lock_held=False)
        with self.assertRaises(AssertionError):
            with observer:
                handle = observer.load(b"source")
                observer.close(handle)
                with self.assertRaises(AssertionError):
                    observer.assert_product_cleanup(
                        expected_acquisitions=1)
        self.assertEqual(native.close_calls, [handle])

    def test_original_exception_preserved_with_teardown_evidence(self):
        native, observer = make_observer([111])
        observer.expected_acquisitions = 1
        with self.assertRaisesRegex(ValueError, "original failure"):
            with observer:
                handle = observer.load(b"source")
                raise ValueError("original failure")
        self.assertIsNotNone(
            getattr(observer, "teardown_failure", None))
        self.assertEqual(native.close_calls, [handle])
        self.assertEqual(observer.teardown_completion_count, 1)
        self.assertEqual(observer.product_completion_count, 0)


if __name__ == "__main__":
    unittest.main()
