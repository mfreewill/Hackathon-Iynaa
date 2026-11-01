"""EXIF, transparency, multiframe and cleanup tests. Synthetic only."""
import hashlib
import io
import pathlib
import sys
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageFile  # noqa: E402

import teacher_demo.page_decoder as page_decoder  # noqa: E402
from teacher_demo.page_decoder import PageDecodeError, decode_page  # noqa: E402

RIGHTS = "synthetic-fixture-rights-note"
RED = (220, 30, 30)
GREEN = (30, 180, 30)
BLUE = (30, 30, 220)
YELLOW = (220, 200, 30)


def save(image, fmt, **kwargs):
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()


def exif_bytes(orientation):
    exif = Image.Exif()
    exif[274] = orientation
    return exif.tobytes()


def two_block_jpeg(orientation=None):
    """16x8 JPEG: left 8x8 block red, right 8x8 block green."""
    with Image.new("RGB", (16, 8), RED) as image:
        for x in range(8, 16):
            for y in range(8):
                image.putpixel((x, y), GREEN)
        kwargs = {"quality": 100, "subsampling": 0}
        if orientation is not None:
            kwargs["exif"] = exif_bytes(orientation)
        return save(image, "JPEG", **kwargs)


def open_result(result):
    image = Image.open(io.BytesIO(result.png_bytes))
    image.load()
    return image


def flat_pixels(image):
    getter = getattr(image, "get_flattened_data", None)
    return list(getter() if getter is not None else image.getdata())


def close_enough(pixel, expected, delta=24):
    return all(abs(int(a) - int(b)) <= delta
               for a, b in zip(pixel[:3], expected))


class ExifOrientationTests(unittest.TestCase):
    def test_no_exif_records_none_not_applied(self):
        result = decode_page(two_block_jpeg(), rights_ref=RIGHTS)
        self.assertIsNone(result.exif_orientation)
        self.assertFalse(result.orientation_applied)
        self.assertIsNone(result.render_dpi)
        page = result.decoded_source.page
        self.assertEqual((page.width, page.height), (16, 8))

    def test_orientation_1_recorded_not_applied(self):
        result = decode_page(two_block_jpeg(1), rights_ref=RIGHTS)
        self.assertEqual(result.exif_orientation, 1)
        self.assertFalse(result.orientation_applied)
        with open_result(result) as decoded:
            self.assertEqual(decoded.size, (16, 8))
            self.assertTrue(close_enough(
                decoded.convert("RGB").getpixel((4, 4)), RED))
            self.assertTrue(close_enough(
                decoded.convert("RGB").getpixel((12, 4)), GREEN))

    def test_orientation_6_rotates_and_swaps_dimensions(self):
        original = two_block_jpeg(6)
        result = decode_page(original, rights_ref=RIGHTS)
        self.assertEqual(result.exif_orientation, 6)
        self.assertTrue(result.orientation_applied)
        self.assertEqual(result.original_bytes, original)
        page = result.decoded_source.page
        self.assertEqual((page.width, page.height), (8, 16))
        self.assertEqual(page.source_sha256,
                         hashlib.sha256(original).hexdigest())
        self.assertEqual(page.render_sha256,
                         hashlib.sha256(result.png_bytes).hexdigest())
        with open_result(result) as decoded:
            self.assertEqual(decoded.size, (8, 16))
            rgb = decoded.convert("RGB")
            # 90 degrees clockwise: stored left block becomes top.
            self.assertTrue(close_enough(rgb.getpixel((4, 4)), RED),
                            rgb.getpixel((4, 4)))
            self.assertTrue(close_enough(rgb.getpixel((4, 12)), GREEN),
                            rgb.getpixel((4, 12)))
            # Returned PNG must not carry an orientation to re-apply.
            self.assertIn(decoded.getexif().get(274), (None, 1))

    def test_orientation_3_rotates_180_without_swap(self):
        result = decode_page(two_block_jpeg(3), rights_ref=RIGHTS)
        self.assertEqual(result.exif_orientation, 3)
        self.assertTrue(result.orientation_applied)
        with open_result(result) as decoded:
            self.assertEqual(decoded.size, (16, 8))
            rgb = decoded.convert("RGB")
            self.assertTrue(close_enough(rgb.getpixel((4, 4)), GREEN))
            self.assertTrue(close_enough(rgb.getpixel((12, 4)), RED))


class PixelFidelityTests(unittest.TestCase):
    def test_rgba_alpha_values_preserved_exactly(self):
        pixels = [(255, 0, 0, 0), (0, 255, 0, 128),
                  (0, 0, 255, 255), (10, 20, 30, 77)]
        with Image.new("RGBA", (2, 2)) as image:
            image.putdata(pixels)
            original = save(image, "PNG")
        result = decode_page(original, rights_ref=RIGHTS)
        with open_result(result) as decoded:
            self.assertEqual(decoded.mode, "RGBA")
            self.assertEqual(flat_pixels(decoded), pixels)

    def test_grayscale_and_palette_pixels_preserved(self):
        with Image.new("L", (3, 2)) as gray:
            gray.putdata([0, 70, 140, 200, 255, 33])
            result = decode_page(save(gray, "PNG"))
            with open_result(result) as decoded:
                self.assertEqual(flat_pixels(decoded.convert("L")),
                                 [0, 70, 140, 200, 255, 33])
        with Image.new("P", (2, 1)) as pal:
            palette = [0] * 768
            palette[0:3] = [220, 30, 30]
            palette[3:6] = [30, 30, 220]
            pal.putpalette(palette)
            pal.putdata([0, 1])
            result = decode_page(save(pal, "PNG", transparency=0))
            with open_result(result) as decoded:
                rgba = decoded.convert("RGBA")
                self.assertEqual(rgba.getpixel((0, 0)), (220, 30, 30, 0))
                self.assertEqual(rgba.getpixel((1, 0)), (30, 30, 220, 255))

    def test_result_is_immutable(self):
        with Image.new("RGB", (2, 2), RED) as image:
            result = decode_page(save(image, "PNG"))
        with self.assertRaises(FrozenInstanceError):
            result.media_type = "image/jpeg"


class MultiframeTests(unittest.TestCase):
    def test_animated_png_refused(self):
        with Image.new("RGBA", (4, 4), (255, 0, 0, 255)) as first, \
                Image.new("RGBA", (4, 4), (0, 0, 255, 255)) as second:
            buffer = io.BytesIO()
            first.save(buffer, format="PNG", save_all=True,
                       append_images=[second], duration=100, loop=0)
            animated = buffer.getvalue()
        # Fixture must really be multiframe before it proves anything.
        with Image.open(io.BytesIO(animated)) as probe:
            self.assertGreater(getattr(probe, "n_frames", 1), 1)
        with self.assertRaises(PageDecodeError) as ctx:
            decode_page(animated)
        self.assertEqual(ctx.exception.code, "multiframe_image")

    def test_single_frame_png_is_legitimate_opposite(self):
        with Image.new("RGBA", (4, 4), (255, 0, 0, 255)) as image:
            result = decode_page(save(image, "PNG"))
        self.assertEqual(result.page_count, 1)


class CleanupAndGlobalsTests(unittest.TestCase):
    def _decode_tracking_open(self, data):
        opened = []
        closes = []
        real_open = Image.open
        real_close = Image.Image.close

        def tracking_open(*args, **kwargs):
            image = real_open(*args, **kwargs)
            opened.append(image)
            return image

        def tracking_close(image_self):
            closes.append(image_self)
            return real_close(image_self)

        with mock.patch.object(page_decoder.Image, "open", tracking_open), \
                mock.patch.object(Image.Image, "close", tracking_close):
            try:
                outcome = ("ok", decode_page(data, rights_ref=RIGHTS))
            except PageDecodeError as exc:
                outcome = ("refused", exc)
        return outcome, opened, closes

    def test_opened_image_closed_on_success(self):
        with Image.new("RGB", (3, 2), BLUE) as image:
            data = save(image, "PNG")
        outcome, opened, closes = self._decode_tracking_open(data)
        self.assertEqual(outcome[0], "ok")
        self.assertTrue(opened)
        for image in opened:
            self.assertTrue(any(image is closed for closed in closes))
        # Returned bytes own their data after resources are closed.
        with open_result(outcome[1]) as decoded:
            self.assertEqual(decoded.size, (3, 2))

    def test_opened_image_closed_on_refusal(self):
        with Image.new("RGB", (3, 2), BLUE) as image:
            data = save(image, "BMP")
        outcome, opened, closes = self._decode_tracking_open(data)
        self.assertEqual(outcome[0], "refused")
        self.assertTrue(opened)
        for image in opened:
            self.assertTrue(any(image is closed for closed in closes))

    def test_input_buffer_closed_on_success_and_refusal(self):
        with Image.new("RGB", (3, 2), BLUE) as image:
            good = save(image, "PNG")
            bad = save(image, "BMP")
        for data in (good, bad):
            created = []
            real_bytesio = io.BytesIO

            class TrackingBytesIO(real_bytesio):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self._initial = args[0] if args else None
                    created.append(self)

            with mock.patch.object(io, "BytesIO", TrackingBytesIO):
                try:
                    decode_page(data, rights_ref=RIGHTS)
                except PageDecodeError:
                    pass
            inputs = [b for b in created if b._initial == data]
            self.assertTrue(inputs)
            for buffer in inputs:
                self.assertTrue(buffer.closed)

    def test_pillow_global_safety_settings_preserved(self):
        before_pixels = Image.MAX_IMAGE_PIXELS
        before_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
        with Image.new("RGB", (3, 2), BLUE) as image:
            decode_page(save(image, "PNG"))
            with self.assertRaises(PageDecodeError):
                decode_page(save(image, "GIF"))
        self.assertEqual(Image.MAX_IMAGE_PIXELS, before_pixels)
        self.assertEqual(ImageFile.LOAD_TRUNCATED_IMAGES, before_truncated)


if __name__ == "__main__":
    unittest.main()
