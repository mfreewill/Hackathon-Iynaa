"""Bounded page-decoder tests. Tiny synthetic fixtures only.

Fixtures are newly constructed with Pillow / pypdfium2 in this file;
no external pages and no network.
Renders and tests here are not HTTP/browser/model-quality proof.
"""
import hashlib
import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PIL import Image  # noqa: E402

from teacher_demo.page_decoder import (  # noqa: E402
    PageDecodeError,
    decode_page,
)

RIGHTS = "synthetic-fixture-rights-note"


def png_bytes(size=(6, 4), mode="RGBA", color=(200, 30, 40, 128)):
    buffer = io.BytesIO()
    with Image.new(mode, size, color) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(size=(8, 5), color=(30, 120, 200)):
    buffer = io.BytesIO()
    with Image.new("RGB", size, color) as image:
        image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def open_png(data):
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


class ImageDecodeIdentityTests(unittest.TestCase):
    """Cycle 1: real image decoding and source identity."""

    def test_png_decodes_to_exact_pixels_and_identity(self):
        original = png_bytes()
        result = decode_page(original, 0, rights_ref=RIGHTS)
        self.assertEqual(result.original_bytes, original)
        self.assertEqual(result.media_type, "image/png")
        self.assertEqual(result.page_count, 1)
        self.assertTrue(result.decoder.startswith("Pillow "))
        self.assertIsNone(result.render_dpi)
        page = result.decoded_source.page
        self.assertEqual(page.source_sha256,
                         hashlib.sha256(original).hexdigest())
        self.assertEqual(page.render_sha256,
                         hashlib.sha256(result.png_bytes).hexdigest())
        self.assertEqual((page.width, page.height), (6, 4))
        self.assertEqual(page.page_index, 0)
        self.assertEqual(page.coordinate_convention,
                         "raster_pixels_top_left")
        self.assertEqual(page.rights_ref, RIGHTS)
        self.assertFalse(page.rights_is_clearance)
        with open_png(result.png_bytes) as decoded:
            self.assertEqual(decoded.format, "PNG")
            self.assertEqual(decoded.size, (6, 4))
            self.assertEqual(decoded.convert("RGBA").getpixel((0, 0)),
                             (200, 30, 40, 128))
        self.assertEqual(result.decoded_source.identity.source_id,
                         page.source_id)

    def test_jpeg_decodes_with_actual_dimensions(self):
        original = jpeg_bytes()
        result = decode_page(original, rights_ref=RIGHTS)
        self.assertEqual(result.media_type, "image/jpeg")
        self.assertEqual(result.original_bytes, original)
        page = result.decoded_source.page
        self.assertEqual((page.width, page.height), (8, 5))
        with open_png(result.png_bytes) as decoded:
            self.assertEqual(decoded.size, (8, 5))
            red, green, blue = decoded.convert("RGB").getpixel((4, 2))
            self.assertAlmostEqual(red, 30, delta=6)
            self.assertAlmostEqual(green, 120, delta=6)
            self.assertAlmostEqual(blue, 200, delta=6)

    def test_source_id_is_fresh_per_decode_hashes_stable(self):
        original = png_bytes()
        first = decode_page(original, rights_ref=RIGHTS)
        second = decode_page(original, rights_ref=RIGHTS)
        self.assertNotEqual(first.decoded_source.page.source_id,
                            second.decoded_source.page.source_id)
        self.assertEqual(first.decoded_source.page.source_sha256,
                         second.decoded_source.page.source_sha256)
        self.assertTrue(first.decoded_source.page.source_id)


if __name__ == "__main__":
    unittest.main()
