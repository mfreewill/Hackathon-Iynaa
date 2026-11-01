"""pdf-fix-v1 regressions: rendered bounds + construction ownership.

Tiny hand-built synthetic PDFs only.
"""
import ctypes
import io
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import pypdfium2 as pdfium  # noqa: E402
import pypdfium2.raw as pdfium_raw  # noqa: E402
from PIL import Image  # noqa: E402

from teacher_demo.page_decoder import PageDecodeError, decode_page  # noqa: E402

RIGHTS = "synthetic-fixture-rights-note"


def build_pdf(pages):
    """pages: dicts with mediabox, content, optional cropbox/rotate."""
    objects = []
    content_ids, page_ids = [], []
    for spec in pages:
        stream = spec["content"]
        objects.append(b"<< /Length " + str(len(stream)).encode()
                       + b" >>\nstream\n" + stream + b"\nendstream")
        content_ids.append(len(objects))
    for spec, content_id in zip(pages, content_ids):
        mx0, my0, mx1, my1 = spec["mediabox"]
        body = (f"<< /Type /Page /Parent {{pages_id}} 0 R "
                f"/MediaBox [{mx0:g} {my0:g} {mx1:g} {my1:g}] ")
        if "cropbox" in spec:
            cx0, cy0, cx1, cy1 = spec["cropbox"]
            body += f"/CropBox [{cx0:g} {cy0:g} {cx1:g} {cy1:g}] "
        if "rotate" in spec:
            body += f"/Rotate {spec['rotate']} "
        body += f"/Contents {content_id} 0 R /Resources << >> >>"
        objects.append(body.encode())
        page_ids.append(len(objects))
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    pages_id = len(objects)
    objects = [body.replace(b"{pages_id}", str(pages_id).encode())
               for body in objects]
    objects.append(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    catalog_id = len(objects)
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} "
            f"/Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def direct_native_rgba(data, page_index):
    """Independent direct PDFium render at scale 3, concretely closed."""
    document = page = bitmap = view = owned = None
    try:
        document = pdfium.PdfDocument(data)
        page = document.get_page(page_index)
        bitmap = page.render(scale=3)
        view = bitmap.to_pil()
        owned = view.copy()
        owned.load()
        rgba = owned.convert("RGBA")
        try:
            return rgba.size, rgba.tobytes()
        finally:
            rgba.close()
    finally:
        for resource in (owned, view, bitmap, page, document):
            if resource is not None:
                resource.close()


def decoded_rgba(result):
    image = Image.open(io.BytesIO(result.png_bytes))
    try:
        image.load()
        rgba = image.convert("RGBA")
        try:
            return rgba.size, rgba.tobytes()
        finally:
            rgba.close()
    finally:
        image.close()


FIRST_PAGE = {"mediabox": (0, 0, 72, 72),
              "content": b"0 1 0 rg 0 0 72 72 re f"}
SELECTED_CONTENT = b"0 0 1 rg 0 0 20 10 re f 1 0 0 rg 0 0 10 10 re f"


class RenderedExtentBoundsTests(unittest.TestCase):
    def test_oversized_raw_crop_with_small_native_raster_decodes(self):
        for cropbox in ((0, 0, 5000, 10), (0, 0, 2000, 2000)):
            with self.subTest(cropbox=cropbox):
                original = build_pdf([
                    FIRST_PAGE,
                    {"mediabox": (0, 0, 20, 10), "cropbox": cropbox,
                     "rotate": 90, "content": SELECTED_CONTENT},
                ])
                expected = direct_native_rgba(original, 1)
                self.assertEqual(expected[0], (30, 60))
                result = decode_page(original, 1, rights_ref=RIGHTS)
                self.assertEqual(result.original_bytes, original)
                self.assertEqual(result.page_count, 2)
                page = result.decoded_source.page
                self.assertEqual(page.page_index, 1)
                self.assertEqual((page.width, page.height), (30, 60))
                actual = decoded_rgba(result)
                self.assertEqual(actual, expected)
                pixels = {tuple(actual[1][i:i + 4])
                          for i in range(0, len(actual[1]), 4)}
                self.assertIn((255, 0, 0, 255), pixels)
                self.assertIn((0, 0, 255, 255), pixels)

    def test_ordinary_crop_and_rotation_matches_native(self):
        original = build_pdf([
            {"mediabox": (0, 0, 72, 36), "cropbox": (0, 0, 72, 36),
             "rotate": 90,
             "content": b"0 0 1 rg 0 0 72 36 re f "
                        b"1 0 0 rg 0 0 36 36 re f"},
        ])
        expected = direct_native_rgba(original, 0)
        result = decode_page(original, 0, rights_ref=RIGHTS)
        self.assertEqual(decoded_rgba(result), expected)

    def test_truly_overlimit_rendered_extent_refused(self):
        original = build_pdf([
            {"mediabox": (0, 0, 5000, 10), "cropbox": (0, 0, 5000, 10),
             "content": b"1 0 0 rg 0 0 5000 10 re f"},
        ])
        with self.assertRaises(PageDecodeError) as ctx:
            decode_page(original, 0, rights_ref=RIGHTS)
        self.assertEqual(ctx.exception.code, "image_too_large")

    def test_degenerate_crop_refused(self):
        original = build_pdf([
            {"mediabox": (0, 0, 20, 10), "cropbox": (0, 0, 0, 10),
             "content": SELECTED_CONTENT},
        ])
        with self.assertRaises(PageDecodeError) as ctx:
            decode_page(original, 0, rights_ref=RIGHTS)
        self.assertEqual(ctx.exception.code, "invalid_dimensions")


def build_zero_page_pdf():
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [] /Count 0 >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 3\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n"
            + str(xref).encode() + b"\n%%EOF\n")
    return bytes(out)


def _handle_address(handle):
    if isinstance(handle, pdfium.PdfDocument):
        handle = handle.raw
    if not handle:
        return None
    return ctypes.cast(handle, ctypes.c_void_p).value


class ConstructionOwnershipTests(unittest.TestCase):
    def _track_loads_closes(self):
        loads, closes = [], []
        real_load = pdfium_raw.FPDF_LoadMemDocument64
        real_close = pdfium_raw.FPDF_CloseDocument

        def tracking_load(data, size, password):
            handle = real_load(data, size, password)
            address = _handle_address(handle)
            if address is not None:
                loads.append(address)
            return handle

        def tracking_close(handle):
            closes.append(_handle_address(handle))
            return real_close(handle)

        patches = [
            mock.patch.object(pdfium_raw, "FPDF_LoadMemDocument64",
                              tracking_load),
            mock.patch.object(pdfium_raw, "FPDF_CloseDocument",
                              tracking_close),
        ]
        return loads, closes, patches

    def _run_tracked(self, data):
        loads, closes, patches = self._track_loads_closes()
        for patch in patches:
            patch.start()
        try:
            try:
                outcome = ("ok", decode_page(data, 0,
                                             rights_ref=RIGHTS))
            except PageDecodeError as exc:
                outcome = ("refused", exc)
        finally:
            for patch in reversed(patches):
                patch.stop()
        return outcome, loads, closes

    def test_zero_page_construction_failure_closes_raw_handle(self):
        outcome, loads, closes = self._run_tracked(build_zero_page_pdf())
        self.assertEqual(outcome[0], "refused")
        self.assertEqual(outcome[1].code, "malformed_pdf")
        # Exactly one non-null raw acquisition, discharged exactly
        # once by the product (this test closes nothing itself).
        self.assertEqual(len(loads), 1)
        self.assertEqual(closes, loads)

    def test_success_and_encrypted_refusal_close_exactly_once(self):
        good = build_pdf([FIRST_PAGE])
        outcome, loads, closes = self._run_tracked(good)
        self.assertEqual(outcome[0], "ok")
        self.assertEqual(len(loads), 1)
        self.assertEqual(closes, loads)

        from test_page_decoder_pdf import build_encrypted_pdf
        outcome, loads, closes = self._run_tracked(
            build_encrypted_pdf(b""))
        self.assertEqual(outcome[0], "refused")
        self.assertEqual(outcome[1].code, "encrypted_pdf")
        self.assertEqual(len(loads), 1)
        self.assertEqual(closes, loads)


if __name__ == "__main__":
    unittest.main()
