"""Selected-PDF-page decoder tests. Tiny synthetic fixtures only."""
import hashlib
import io
import pathlib
import struct
import sys
import threading
import time
import unittest
from fractions import Fraction
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import pypdfium2 as pdfium  # noqa: E402
import pypdfium2.raw as pdfium_raw  # noqa: E402
from PIL import Image  # noqa: E402
from pypdfium2.version import PDFIUM_INFO, PYPDFIUM_INFO  # noqa: E402

import teacher_demo.page_decoder as page_decoder  # noqa: E402
from teacher_demo.page_decoder import PageDecodeError, decode_page  # noqa: E402

RIGHTS = "synthetic-fixture-rights-note"


def build_pdf(pages):
    """pages: [(width_pt, height_pt, (r, g, b))] painted full-page."""
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    page_ids = []
    content_ids = []
    for width, height, (red, green, blue) in pages:
        stream = (f"{red / 255:g} {green / 255:g} {blue / 255:g} rg "
                  f"0 0 {width:g} {height:g} re f").encode()
        content_ids.append(add(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"))
    for (width, height, _), content_id in zip(pages, content_ids):
        page_ids.append(add(
            f"<< /Type /Page /Parent {{pages_id}} 0 R "
            f"/MediaBox [0 0 {width:g} {height:g}] "
            f"/Contents {content_id} 0 R /Resources << >> >>".encode()))
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    pages_id = add(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())
    objects = [body.replace(b"{pages_id}", str(pages_id).encode())
               for body in objects]

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


class SelectedPageTests(unittest.TestCase):
    def test_second_page_only_renders_with_identity(self):
        original = build_pdf([(72, 72, (255, 0, 0)),
                              (144, 72, (0, 0, 255))])
        result = decode_page(original, 1, rights_ref=RIGHTS)
        self.assertEqual(result.original_bytes, original)
        self.assertEqual(result.media_type, "application/pdf")
        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.render_dpi, 216)
        self.assertIn(str(PYPDFIUM_INFO), result.decoder)
        self.assertIn(str(PDFIUM_INFO), result.decoder)
        page = result.decoded_source.page
        self.assertEqual(page.page_index, 1)
        # 144x72 pt at scale 3 (216 DPI) is 432x216 px.
        self.assertEqual((page.width, page.height), (432, 216))
        self.assertEqual(page.source_sha256,
                         hashlib.sha256(original).hexdigest())
        self.assertEqual(page.render_sha256,
                         hashlib.sha256(result.png_bytes).hexdigest())
        self.assertEqual(page.coordinate_convention,
                         "raster_pixels_top_left")
        self.assertEqual(page.rights_ref, RIGHTS)
        self.assertFalse(page.rights_is_clearance)
        with Image.open(io.BytesIO(result.png_bytes)) as decoded:
            decoded.load()
            self.assertEqual(decoded.format, "PNG")
            self.assertEqual(decoded.size, (page.width, page.height))
            pixel = decoded.convert("RGB").getpixel((216, 108))
            self.assertEqual(pixel, (0, 0, 255))


_PADDING = bytes.fromhex(
    "28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")


def _rc4(key, data):
    state = list(range(256))
    j = 0
    for i in range(256):
        j = (j + state[i] + key[i % len(key)]) & 0xFF
        state[i], state[j] = state[j], state[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) & 0xFF
        j = (j + state[i]) & 0xFF
        state[i], state[j] = state[j], state[i]
        out.append(byte ^ state[(state[i] + state[j]) & 0xFF])
    return bytes(out)


def build_encrypted_pdf(user_password):
    """One-page Standard handler R2 (40-bit RC4) encrypted PDF."""
    padded_user = (user_password + _PADDING)[:32]
    padded_owner = (b"owner-test" + _PADDING)[:32]
    owner_key = hashlib.md5(padded_owner).digest()[:5]
    owner_value = _rc4(owner_key, padded_user)
    permissions = -4
    file_id = b"0123456789abcdef"
    file_key = hashlib.md5(
        padded_user + owner_value
        + struct.pack("<i", permissions) + file_id).digest()[:5]
    user_value = _rc4(file_key, _PADDING)
    stream = b"0 0 1 rg 0 0 72 72 re f"
    object_key = hashlib.md5(
        file_key + (4).to_bytes(3, "little")
        + (0).to_bytes(2, "little")).digest()[:10]
    encrypted_stream = _rc4(object_key, stream)
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] "
        b"/Contents 4 0 R /Resources << >> >>",
        (b"<< /Length " + str(len(encrypted_stream)).encode()
         + b" >>\nstream\n" + encrypted_stream + b"\nendstream"),
        (f"<< /Filter /Standard /V 1 /R 2 /Length 40 "
         f"/O <{owner_value.hex()}> /U <{user_value.hex()}> "
         f"/P {permissions} >>").encode(),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(bodies, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(bodies) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(bodies) + 1} /Root 1 0 R "
            f"/Encrypt 5 0 R /ID [<{file_id.hex()}>"
            f"<{file_id.hex()}>] >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def center_pixel(result):
    with Image.open(io.BytesIO(result.png_bytes)) as decoded:
        decoded.load()
        return decoded.convert("RGB").getpixel(
            (decoded.size[0] // 2, decoded.size[1] // 2))


class RefusalTests(unittest.TestCase):
    def assert_refused(self, data, code, **kwargs):
        with self.assertRaises(PageDecodeError) as ctx:
            decode_page(data, **kwargs)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_first_page_control_is_distinguishable(self):
        original = build_pdf([(72, 72, (255, 0, 0)),
                              (72, 72, (0, 0, 255))])
        first = decode_page(original, 0, rights_ref=RIGHTS)
        second = decode_page(original, 1, rights_ref=RIGHTS)
        self.assertEqual(center_pixel(first), (255, 0, 0))
        self.assertEqual(center_pixel(second), (0, 0, 255))
        self.assertNotEqual(first.decoded_source.page.source_id,
                            second.decoded_source.page.source_id)

    def test_invalid_and_out_of_range_indices_refused(self):
        original = build_pdf([(72, 72, (255, 0, 0))])
        for bad in (1, 5, -1, True, False, 0.0, Fraction(0, 1), "0", None):
            with self.subTest(page_index=repr(bad)):
                self.assert_refused(original, "invalid_page_index",
                                    page_index=bad, rights_ref=RIGHTS)

    def test_missing_eof_and_truncation_refused_ws_control_ok(self):
        original = build_pdf([(72, 72, (255, 0, 0))])
        self.assertTrue(original.rstrip().endswith(b"%%EOF"))
        self.assert_refused(original.replace(b"%%EOF", b""),
                            "malformed_pdf", rights_ref=RIGHTS)
        self.assert_refused(original[: len(original) // 2],
                            "malformed_pdf", rights_ref=RIGHTS)
        lawful = original + b"\r\n \t"
        self.assertEqual(decode_page(lawful).original_bytes, lawful)

    def test_empty_password_encrypted_refused(self):
        original = build_encrypted_pdf(b"")
        document = pdfium.PdfDocument(original)
        try:
            self.assertEqual(
                pdfium_raw.FPDF_GetSecurityHandlerRevision(document.raw),
                2)
        finally:
            document.close()
        self.assert_refused(original, "encrypted_pdf",
                            rights_ref=RIGHTS)

    def test_password_encrypted_refused(self):
        self.assert_refused(build_encrypted_pdf(b"secret-test"),
                            "encrypted_pdf", rights_ref=RIGHTS)

    def test_unencrypted_control_decodes(self):
        original = build_pdf([(72, 72, (0, 0, 255))])
        self.assertEqual(center_pixel(decode_page(original)),
                         (0, 0, 255))


class BoundsTests(unittest.TestCase):
    def assert_refused(self, data, code, **kwargs):
        with self.assertRaises(PageDecodeError) as ctx:
            decode_page(data, **kwargs)
        self.assertEqual(ctx.exception.code, code)
        return ctx.exception

    def test_byte_bound_scoped_opposite_controls(self):
        original = build_pdf([(72, 72, (255, 0, 0))])
        with mock.patch.object(page_decoder, "MAX_ORIGINAL_BYTES",
                               len(original) - 1):
            self.assert_refused(original, "input_too_large")
        with mock.patch.object(page_decoder, "MAX_ORIGINAL_BYTES",
                               len(original)):
            self.assertEqual(decode_page(original).original_bytes,
                             original)

    def test_edge_and_area_scoped_bounds_precede_render(self):
        original = build_pdf([(72, 72, (255, 0, 0))])  # 216x216 px
        renders = []
        real_render = pdfium.PdfPage.render

        def counting_render(page_self, *args, **kwargs):
            renders.append(page_self)
            return real_render(page_self, *args, **kwargs)

        with mock.patch.object(pdfium.PdfPage, "render", counting_render):
            with mock.patch.object(page_decoder, "MAX_EDGE_PIXELS", 215):
                self.assert_refused(original, "image_too_large")
            with mock.patch.object(page_decoder, "MAX_PIXELS",
                                   216 * 216 - 1):
                self.assert_refused(original, "image_too_large")
            self.assertEqual(renders, [])
            with mock.patch.object(page_decoder, "MAX_EDGE_PIXELS", 216):
                with mock.patch.object(page_decoder, "MAX_PIXELS",
                                       216 * 216):
                    self.assertEqual(decode_page(original).page_count, 1)
            self.assertEqual(len(renders), 1)

    def test_fractional_points_use_ceiling_zero_refused(self):
        original = build_pdf([(72.5, 36.25, (255, 0, 0))])
        page = decode_page(original).decoded_source.page
        self.assertEqual((page.width, page.height), (218, 109))
        zero = build_pdf([(0, 72, (255, 0, 0))])
        exc = self.assert_refused(zero, "invalid_dimensions")
        self.assertNotIn("72", str(exc))

    def test_huge_page_refused_without_rendering(self):
        original = build_pdf([(100000, 100000, (255, 0, 0))])
        with mock.patch.object(pdfium.PdfPage, "render",
                               side_effect=AssertionError("rendered")):
            self.assert_refused(original, "image_too_large")


class CleanupAndSerializationTests(unittest.TestCase):
    def _track(self, data, **kwargs):
        bitmaps, closed = [], []
        real_render = pdfium.PdfPage.render
        real_closes = {cls: cls.close for cls in (
            pdfium.PdfDocument, pdfium.PdfPage, pdfium.PdfBitmap)}

        def tracking_render(page_self, *args, **kw):
            bitmap = real_render(page_self, *args, **kw)
            bitmaps.append(bitmap)
            return bitmap

        def make_close(cls):
            def tracking_close(self):
                closed.append(self)
                return real_closes[cls](self)
            return tracking_close

        patches = [mock.patch.object(pdfium.PdfPage, "render",
                                     tracking_render)]
        patches += [mock.patch.object(cls, "close", make_close(cls))
                    for cls in real_closes]
        for patch in patches:
            patch.start()
        try:
            try:
                outcome = ("ok", decode_page(data, **kwargs))
            except PageDecodeError as exc:
                outcome = ("refused", exc)
        finally:
            for patch in reversed(patches):
                patch.stop()
        return outcome, bitmaps, closed

    def test_cleanup_on_success_and_refusal(self):
        good = build_pdf([(72, 72, (255, 0, 0))])
        outcome, bitmaps, closed = self._track(good)
        self.assertEqual(outcome[0], "ok")
        self.assertTrue(bitmaps)
        for bitmap in bitmaps:
            self.assertTrue(any(bitmap is item for item in closed))
        self.assertTrue(any(isinstance(item, pdfium.PdfDocument)
                            for item in closed))
        self.assertTrue(any(isinstance(item, pdfium.PdfPage)
                            for item in closed))
        # Owned PNG remains valid after every owner closed.
        self.assertEqual(center_pixel(outcome[1]), (255, 0, 0))

        outcome, bitmaps, closed = self._track(
            build_encrypted_pdf(b""))
        self.assertEqual(outcome[0], "refused")
        self.assertEqual(outcome[1].code, "encrypted_pdf")
        self.assertTrue(any(isinstance(item, pdfium.PdfDocument)
                            for item in closed))

    def test_renders_serialized_across_documents(self):
        red = build_pdf([(72, 72, (255, 0, 0))])
        blue = build_pdf([(72, 72, (0, 0, 255))])
        real_render = pdfium.PdfPage.render
        guard = threading.Lock()
        state = {"active": 0, "peak": 0}

        def slow_render(page_self, *args, **kwargs):
            with guard:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            try:
                time.sleep(0.05)
                return real_render(page_self, *args, **kwargs)
            finally:
                with guard:
                    state["active"] -= 1

        results, errors = {}, {}

        def run(name, data):
            try:
                results[name] = decode_page(data, rights_ref=RIGHTS)
            except Exception as exc:  # re-raised via assertion below
                errors[name] = exc

        with mock.patch.object(pdfium.PdfPage, "render", slow_render):
            threads = [threading.Thread(target=run, args=("red", red)),
                       threading.Thread(target=run, args=("blue", blue))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
        self.assertEqual(errors, {})
        self.assertEqual(state["peak"], 1)
        self.assertEqual(center_pixel(results["red"]), (255, 0, 0))
        self.assertEqual(center_pixel(results["blue"]), (0, 0, 255))


if __name__ == "__main__":
    unittest.main()
