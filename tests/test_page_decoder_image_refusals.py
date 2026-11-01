"""Image-decoder refusal and bounds tests. Tiny synthetic fixtures only."""
import io
import pathlib
import struct
import sys
import unittest
import zlib
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PIL import Image  # noqa: E402

import teacher_demo.page_decoder as page_decoder  # noqa: E402
from teacher_demo.page_decoder import PageDecodeError, decode_page  # noqa: E402

RIGHTS = "synthetic-fixture-rights-note"


def make(fmt, size=(6, 4), mode="RGBA", color=(200, 30, 40, 128)):
    buffer = io.BytesIO()
    with Image.new(mode, size, color) as image:
        image.save(buffer, format=fmt)
    return buffer.getvalue()


def png(size=(6, 4)):
    return make("PNG", size=size)


def png_header(width, height):
    """Tiny PNG carrying only IHDR+IEND: metadata without pixels."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    ihdr_chunk = struct.pack(">I", len(ihdr)) + chunk
    ihdr_chunk += struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    iend = b"IEND"
    iend_chunk = struct.pack(">I", 0) + iend
    iend_chunk += struct.pack(">I", zlib.crc32(iend) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + ihdr_chunk + iend_chunk


class RefusalCase(unittest.TestCase):
    def assert_refused(self, data, code=None, **kwargs):
        with self.assertRaises(PageDecodeError) as ctx:
            decode_page(data, **kwargs)
        exc = ctx.exception
        self.assertIsInstance(exc.code, str)
        self.assertTrue(exc.code)
        if code is not None:
            self.assertEqual(exc.code, code)
        return exc


class TypeAndIndexRefusalTests(RefusalCase):
    def test_empty_bytes_refused(self):
        self.assert_refused(b"", code="empty_input")

    def test_other_types_refused(self):
        original = png()
        for bad in (bytearray(original), memoryview(original), 5, 0,
                    "not-bytes", None, [original]):
            with self.subTest(bad_type=type(bad).__name__):
                self.assert_refused(bad, code="invalid_type")

    def test_legitimate_bytes_still_accepted(self):
        result = decode_page(png(), rights_ref=RIGHTS)
        self.assertEqual(result.media_type, "image/png")

    def test_nonzero_and_non_int_page_index_refused(self):
        original = png()
        for bad in (1, -1, True, False, 0.0, "0", None):
            with self.subTest(page_index=repr(bad)):
                self.assert_refused(original, code="invalid_page_index",
                                    page_index=bad)

    def test_non_str_rights_ref_refused_value_free(self):
        for bad in (None, 7, b"rights"):
            with self.subTest(rights_type=type(bad).__name__):
                self.assert_refused(png(), code="invalid_rights_ref",
                                    rights_ref=bad)

    def test_rights_ref_preserved_exactly_not_clearance(self):
        weird = "  note: Märchen / مثال  "
        result = decode_page(png(), rights_ref=weird)
        self.assertEqual(result.decoded_source.page.rights_ref, weird)
        self.assertFalse(result.decoded_source.page.rights_is_clearance)


class FormatAndMalformedRefusalTests(RefusalCase):
    def test_unsupported_image_formats_refused(self):
        for fmt, mode, color in (("GIF", "P", 1), ("BMP", "RGB", (1, 2, 3))):
            with self.subTest(fmt=fmt):
                self.assert_refused(make(fmt, mode=mode, color=color),
                                    code="unsupported_format")

    def test_malformed_pdf_refused(self):
        pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
        self.assert_refused(pdf, code="malformed_pdf")

    def test_garbage_refused_without_leaking_content(self):
        marker = b"SECRET-MARKER-98765"
        exc = self.assert_refused(b"garbage-" + marker)
        self.assertNotIn(marker.decode(), str(exc))
        self.assertNotIn(marker.decode(), exc.code)
        self.assertNotIn("garbage", str(exc))

    def test_truncated_png_refused(self):
        original = png()
        self.assert_refused(original[: len(original) // 2],
                            code="malformed_image")

    def test_truncated_jpeg_refused(self):
        original = make("JPEG", mode="RGB", color=(10, 20, 30))
        self.assert_refused(original[: max(10, len(original) // 3)])

    def test_signature_only_png_refused(self):
        self.assert_refused(b"\x89PNG\r\n\x1a\n")

    def test_zero_dimension_header_refused(self):
        self.assert_refused(png_header(0, 4))


class BoundsTests(RefusalCase):
    def test_original_bytes_bound_via_scoped_limit(self):
        original = png()
        with mock.patch.object(page_decoder, "MAX_ORIGINAL_BYTES",
                               len(original) - 1):
            self.assert_refused(original, code="input_too_large")
        with mock.patch.object(page_decoder, "MAX_ORIGINAL_BYTES",
                               len(original)):
            result = decode_page(original, rights_ref=RIGHTS)
            self.assertEqual(result.original_bytes, original)

    def test_max_edge_bound_via_scoped_limit(self):
        original = png(size=(6, 4))
        with mock.patch.object(page_decoder, "MAX_EDGE_PIXELS", 5):
            self.assert_refused(original, code="image_too_large")
        with mock.patch.object(page_decoder, "MAX_EDGE_PIXELS", 6):
            self.assertEqual(decode_page(original).media_type, "image/png")

    def test_max_pixels_bound_via_scoped_limit(self):
        original = png(size=(6, 4))  # 24 pixels
        with mock.patch.object(page_decoder, "MAX_PIXELS", 23):
            self.assert_refused(original, code="image_too_large")
        with mock.patch.object(page_decoder, "MAX_PIXELS", 24):
            self.assertEqual(decode_page(original).media_type, "image/png")

    def test_huge_metadata_header_refused_without_pixel_data(self):
        # 20000 x 10 exceeds the 12000 edge bound; file has no IDAT.
        self.assert_refused(png_header(20000, 10))

    def test_real_bounds_are_the_documented_values(self):
        self.assertEqual(page_decoder.MAX_ORIGINAL_BYTES, 20 * 1024 * 1024)
        self.assertEqual(page_decoder.MAX_EDGE_PIXELS, 12_000)
        self.assertEqual(page_decoder.MAX_PIXELS, 20_000_000)


if __name__ == "__main__":
    unittest.main()
