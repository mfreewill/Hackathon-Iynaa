"""Prepared PDF construction-lifetime regressions (safety-v1).

These regressions use the per-acquisition LifetimeObserver and
obtain their authoritative verdict only after observer teardown
(residual finalizers and late events included), requiring
concrete product-phase close completions under _PDFIUM_LOCK,
never attempts/suppressions/teardown. Prepared in test-only
turns; execute only in a separately admitted turn after
independent observer review.
"""
import contextlib
import ctypes
import gc
import io
import pathlib
import sys
import unittest
import weakref
from unittest import mock

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parents[0] / "src"))

import pypdfium2 as pdfium  # noqa: E402
import pypdfium2.raw as pdfium_raw  # noqa: E402
from PIL import Image  # noqa: E402
from pypdfium2.internal.bases import ObjectTracker  # noqa: E402

import teacher_demo.page_decoder as page_decoder  # noqa: E402
from pdf_lifetime_observer import LifetimeObserver  # noqa: E402
from teacher_demo.page_decoder import PageDecodeError, decode_page  # noqa: E402
from test_page_decoder_pdf import build_encrypted_pdf, build_pdf  # noqa: E402
from test_page_decoder_pdf_fixes import build_zero_page_pdf  # noqa: E402

RIGHTS = "synthetic-fixture-rights-note"


def _address(handle):
    if isinstance(handle, pdfium.PdfDocument):
        handle = handle.raw
    if not handle:
        return None
    return ctypes.cast(handle, ctypes.c_void_p).value


@contextlib.contextmanager
def observed_pdfium(expected_acquisitions=None):
    """Install the lifetime observer at the raw load/close names.

    Patches stay installed while observer teardown flushes and
    disarms residual finalizers and discharges still-open
    lifetimes as teardown. The final verdict is raised here after
    teardown facts exist (observer ``__exit__`` enforces it when
    expected is known; the explicit call below re-checks it on the
    success path). If the wrapped block raises, that exception is
    preserved and teardown failure evidence stays on the observer.
    """
    observer = LifetimeObserver(
        pdfium_raw.FPDF_LoadMemDocument64,
        pdfium_raw.FPDF_CloseDocument,
        address_of=_address,
        lock_state=lambda: page_decoder._PDFIUM_LOCK.locked(),
        expected_acquisitions=expected_acquisitions,
    )
    patches = [
        mock.patch.object(pdfium_raw, "FPDF_LoadMemDocument64",
                          observer.load),
        mock.patch.object(pdfium_raw, "FPDF_CloseDocument",
                          observer.close),
    ]
    for patch in patches:
        patch.start()
    try:
        with observer:
            yield observer
        if observer.expected_acquisitions is not None:
            observer.assert_final_verdict()
    finally:
        for patch in reversed(patches):
            patch.stop()


def run_decode(data):
    try:
        return ("ok", decode_page(data, 0, rights_ref=RIGHTS))
    except PageDecodeError as exc:
        return ("refused", exc)
    except Exception as exc:  # e.g. an injection escaping untyped
        return ("raised", exc)


class _FailingAddSet(set):
    def __init__(self, values):
        super().__init__(values)
        self.armed = True

    def add(self, item):
        if self.armed:
            self.armed = False
            raise MemoryError("injected at registry add")
        return super().add(item)


class ConstructionLifetimeTests(unittest.TestCase):
    def test_late_registry_failure_one_product_close(self):
        original = build_pdf([(72, 72, (255, 0, 0)),
                              (72, 72, (0, 0, 255))])
        key = pdfium.PdfDocument
        real_set = ObjectTracker[key]
        failing = _FailingAddSet(real_set)
        with observed_pdfium(1) as observer:
            ObjectTracker[key] = failing
            try:
                outcome = run_decode(original)
            finally:
                ObjectTracker[key] = real_set
            fired = not failing.armed
            kind = outcome[0]
            code = outcome[1].code if kind == "refused" else None
            del outcome
            gc.collect()
        # Final verdict is obtained after teardown above.
        self.assertTrue(fired)
        self.assertEqual(kind, "refused")
        self.assertEqual(code, "malformed_pdf")
        observer.assert_final_verdict(1)
        self.assertEqual(observer.teardown_completion_count, 0)

    def test_early_finalizer_failure_one_product_close(self):
        original = build_pdf([(72, 72, (255, 0, 0))])
        real_finalize = weakref.finalize
        fired = []

        def failing_finalize(*args, **kwargs):
            if not fired:
                fired.append(True)
                raise MemoryError("injected before finalizer installs")
            return real_finalize(*args, **kwargs)

        with observed_pdfium(1) as observer:
            with mock.patch.object(weakref, "finalize", failing_finalize):
                outcome = run_decode(original)
            kind = outcome[0]
            code = outcome[1].code if kind == "refused" else None
            del outcome
            gc.collect()
        self.assertTrue(fired)
        self.assertEqual(kind, "refused")
        self.assertEqual(code, "malformed_pdf")
        observer.assert_final_verdict(1)
        self.assertEqual(observer.teardown_completion_count, 0)

    def test_wrapper_allocation_failure_one_owner(self):
        original = build_pdf([(72, 72, (255, 0, 0)),
                              (72, 72, (0, 0, 255))])
        with observed_pdfium() as observer:
            firing = {"fired": False, "loads_at_firing": None}

            class AllocBomb:
                def __new__(cls, *args, **kwargs):
                    firing["fired"] = True
                    firing["loads_at_firing"] = (
                        observer.acquisition_count)
                    raise MemoryError("injected wrapper allocation")

            # Reach the decoder module's actual `object` name lookup
            # used for its wrapper allocation.
            with mock.patch.object(page_decoder, "object", AllocBomb,
                                   create=True):
                outcome = run_decode(original)
            kind = outcome[0]
            code = outcome[1].code if kind == "refused" else None
            message = str(outcome[1]) if kind == "refused" else None
            acquisitions = observer.acquisition_count
            expected = acquisitions if acquisitions in (0, 1) else 1
            observer.expected_acquisitions = expected
            del outcome
            gc.collect()
        self.assertTrue(firing["fired"])
        self.assertEqual(kind, "refused")
        self.assertEqual(code, "malformed_pdf")
        self.assertEqual(message, "page decoding refused: malformed PDF")
        if acquisitions == 0:
            # Allocation moved before acquisition: prove the
            # failure fired before any load, with no closes owed.
            self.assertEqual(firing["loads_at_firing"], 0)
        else:
            self.assertEqual(acquisitions, 1)
        observer.assert_final_verdict(expected)
        self.assertEqual(observer.teardown_completion_count, 0)

    def test_success_zero_page_and_encrypted_controls(self):
        cases = [
            ("ok", None, build_pdf([(72, 72, (255, 0, 0))])),
            ("refused", "malformed_pdf", build_zero_page_pdf()),
            ("refused", "encrypted_pdf", build_encrypted_pdf(b"")),
        ]
        for expected, code, data in cases:
            with self.subTest(code=code):
                with observed_pdfium(1) as observer:
                    outcome = run_decode(data)
                    kind = outcome[0]
                    actual_code = (
                        outcome[1].code if kind == "refused" else None)
                    png = (outcome[1].png_bytes if kind == "ok"
                           else None)
                    del outcome
                    gc.collect()
                self.assertEqual(kind, expected)
                self.assertEqual(actual_code, code)
                if expected == "ok":
                    with Image.open(io.BytesIO(png)) as image:
                        image.load()
                        self.assertEqual(image.size, (216, 216))
                observer.assert_final_verdict(1)
                self.assertEqual(observer.teardown_completion_count, 0)


if __name__ == "__main__":
    unittest.main()
