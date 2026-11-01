"""Per-acquisition lifetime observer for PDF raw handles.

Pure Python: callers supply load/close/address callbacks, so this
module is self-testable with fakes and never calls native code
itself. The prepared PDF regressions wire it to pypdfium2's raw
load/close under the decoder lock.

Each acquisition is its own generation, identified only by the
actual retained handle object across all lifetime states (a
numeric address is reusable and is evidence, never authority).
Attempts, concrete completions, suppressions, lock state, and
teardown are recorded separately; only a product-phase underlying
close that returns without exception completes a lifetime. A
close callback that raises leaves the lifetime ``uncertain`` and
non-retryable: no later explicit or teardown close may call it
again. The authoritative verdict is computed after teardown and
includes residual second ownership and still-armed finalizers.
"""
import gc
import weakref
from dataclasses import dataclass, field


class ObserverVerdictError(AssertionError):
    """Final (post-teardown) cleanup verdict failure."""


@dataclass
class Lifetime:
    generation: int
    handle: object
    address: object
    source: object = None
    state: str = "open"  # open | closed | uncertain
    completion_phase: object = None
    error: object = None


@dataclass
class Event:
    kind: str
    phase: str
    generation: object = None
    address: object = None
    lock_held: object = None


@dataclass
class LifetimeObserver:
    load_func: object
    close_func: object
    address_of: object
    lock_state: object = None
    expected_acquisitions: object = None
    phase: str = field(default="product", init=False)
    lifetimes: list = field(default_factory=list, init=False)
    events: list = field(default_factory=list, init=False)
    residual_finalizers_detached: int = field(default=0, init=False)
    residual_finalizers_still_armed: int = field(default=0, init=False)
    residual_second_ownership_count: int = field(default=0, init=False)
    teardown_failure: object = field(default=None, init=False)

    # -- observed operations --------------------------------------
    def load(self, data, size=None, password=None):
        if size is None:
            size = len(data)
        handle = self.load_func(data, size, password)
        address = self._safe_address(handle)
        if address is not None:
            lifetime = Lifetime(
                generation=len(self.lifetimes) + 1,
                handle=handle, address=address, source=data)
            self.lifetimes.append(lifetime)
            self._record("acquisition", lifetime.generation, address)
        return handle

    def close(self, handle):
        address = self._safe_address(handle)
        target, refusal = self._attribute(handle, address)
        if target is None:
            self._record(refusal, None, address)
            return None
        self._record("attempt", target.generation, address)
        try:
            result = self.close_func(handle)
        except BaseException as exc:
            # A non-return does not prove no release effect: the
            # outcome is explicitly uncertain and non-retryable.
            target.state = "uncertain"
            target.error = type(exc).__name__
            self._record("uncertain", target.generation, address)
            raise
        target.state = "closed"
        target.completion_phase = self.phase
        target.source = None
        self._record("completion", target.generation, address)
        return result

    # -- accounting ------------------------------------------------
    @property
    def acquisition_count(self):
        return len(self.lifetimes)

    @property
    def product_completion_count(self):
        return sum(1 for item in self.lifetimes
                   if item.completion_phase == "product")

    @property
    def teardown_completion_count(self):
        return sum(1 for item in self.lifetimes
                   if item.completion_phase == "teardown")

    def _count(self, kind):
        return sum(1 for event in self.events if event.kind == kind)

    @property
    def duplicate_count(self):
        return self._count("duplicate")

    @property
    def unattributed_count(self):
        return self._count("unattributed")

    @property
    def ambiguous_count(self):
        return self._count("ambiguous")

    @property
    def uncertain_count(self):
        return sum(1 for item in self.lifetimes
                   if item.state == "uncertain")

    @property
    def uncertain_retry_count(self):
        return self._count("uncertain_retry")

    def _cleanup_problems(self, expected_acquisitions, final):
        if expected_acquisitions is None:
            expected_acquisitions = self.expected_acquisitions
        problems = []
        if (expected_acquisitions is not None
                and self.acquisition_count != expected_acquisitions):
            problems.append(
                f"acquisitions={self.acquisition_count}, "
                f"expected {expected_acquisitions}")
        for item in self.lifetimes:
            if item.state == "uncertain":
                problems.append(
                    f"generation {item.generation} uncertain "
                    f"({item.error})")
            elif item.state != "closed":
                problems.append(f"generation {item.generation} open")
            elif item.completion_phase != "product":
                problems.append(
                    f"generation {item.generation} completed in "
                    f"{item.completion_phase!r} phase")
        for kind in ("duplicate", "unattributed", "ambiguous",
                     "uncertain_retry", "teardown_error"):
            if self._count(kind):
                problems.append(f"{kind} events: {self._count(kind)}")
        for event in self.events:
            if (event.kind == "completion"
                    and event.phase == "product"
                    and event.lock_held is False):
                problems.append(
                    f"generation {event.generation} closed unlocked")
        if final:
            if self.residual_second_ownership_count:
                problems.append(
                    "residual second ownership: "
                    f"{self.residual_second_ownership_count}")
            if self.residual_finalizers_still_armed:
                problems.append(
                    "finalizers still armed: "
                    f"{self.residual_finalizers_still_armed}")
        return problems

    def assert_product_cleanup(self, expected_acquisitions=None):
        """Interim product-phase check; not the final verdict."""
        problems = self._cleanup_problems(
            expected_acquisitions, final=False)
        if problems:
            raise AssertionError(
                "product cleanup not proved: " + "; ".join(problems))

    def assert_final_verdict(self, expected_acquisitions=None):
        """Authoritative verdict, valid only after teardown."""
        problems = self._cleanup_problems(
            expected_acquisitions, final=True)
        if problems:
            raise ObserverVerdictError(
                "final cleanup verdict failed: " + "; ".join(problems))

    # -- context / teardown ---------------------------------------
    def __enter__(self):
        self.phase = "product"
        return self

    def __exit__(self, exc_type, exc, tb):
        # Interception is still installed by the caller here.
        self.phase = "teardown"
        gc.collect()
        self.residual_finalizers_detached += (
            self._detach_residual_finalizers())
        for item in list(self.lifetimes):
            # Uncertain lifetimes are never retried (LTS-03).
            if item.state == "open":
                try:
                    self.close(item.handle)
                except Exception:
                    self._record("teardown_error", item.generation,
                                 item.address)
        gc.collect()
        self.residual_finalizers_detached += (
            self._detach_residual_finalizers())
        self.residual_finalizers_still_armed = (
            self._count_residual_finalizers())
        if self.expected_acquisitions is not None:
            problems = self._cleanup_problems(
                self.expected_acquisitions, final=True)
            if problems:
                self.teardown_failure = ObserverVerdictError(
                    "final cleanup verdict failed: "
                    + "; ".join(problems))
                if exc_type is None:
                    raise self.teardown_failure
        return False

    # -- internals --------------------------------------------------
    def _safe_address(self, handle):
        try:
            return self.address_of(handle)
        except Exception:
            return None

    def _lock_held(self):
        if self.lock_state is None:
            return None
        try:
            return bool(self.lock_state())
        except Exception:
            return None

    def _record(self, kind, generation, address):
        self.events.append(Event(
            kind=kind, phase=self.phase, generation=generation,
            address=address, lock_held=self._lock_held()))

    @staticmethod
    def _identity_of(handle):
        # A wrapper may be passed where its retained raw handle is
        # expected; the raw object is the identity. An unrelated
        # same-address object has no such identity and is refused.
        raw = getattr(handle, "raw", None)
        if raw is not None and raw is not handle:
            return raw
        return handle

    def _attribute(self, handle, address):
        # Identity across ALL lifetime states is the only
        # attribution authority; address is never used here.
        identity = self._identity_of(handle)
        matches = [item for item in self.lifetimes
                   if item.handle is identity]
        if not matches:
            return None, "unattributed"
        open_matches = [item for item in matches
                        if item.state == "open"]
        if len(open_matches) == 1:
            return open_matches[0], None
        if len(open_matches) > 1:
            return None, "ambiguous"
        if any(item.state == "uncertain" for item in matches):
            return None, "uncertain_retry"
        return None, "duplicate"

    def _finalizer_lifetimes(self, finalizer):
        try:
            peeked = finalizer.peek()
        except Exception:
            return []
        if not peeked:
            return []
        candidates = []
        for arg in peeked[2]:
            candidates.append(self._identity_of(arg))
        matched = []
        for item in self.lifetimes:
            if any(candidate is item.handle
                   for candidate in candidates):
                matched.append(item)
        return matched

    def _residual_finalizers(self):
        pairs = []
        for obj in gc.get_objects():
            if type(obj) is weakref.finalize and obj.alive:
                matched = self._finalizer_lifetimes(obj)
                if matched:
                    pairs.append((obj, matched))
        return pairs

    def _detach_residual_finalizers(self):
        detached = 0
        for finalizer, matched in self._residual_finalizers():
            try:
                was_detached = finalizer.detach() is not None
            except Exception:
                was_detached = False
            if was_detached:
                detached += 1
                for item in matched:
                    if item.state != "open":
                        self.residual_second_ownership_count += 1
                        self._record("residual_second_owner",
                                     item.generation, item.address)
                    else:
                        self._record("residual_finalizer_detached",
                                     item.generation, item.address)
        return detached

    def _count_residual_finalizers(self):
        return len(self._residual_finalizers())
