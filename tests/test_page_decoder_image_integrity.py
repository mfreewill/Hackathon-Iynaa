"""PNG integrity tests. Tiny newly constructed fixtures only."""
import io
import pathlib
import struct
import sys
import unittest
import zlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from PIL import Image  # noqa: E402

from teacher_demo.page_decoder import PageDecodeError, decode_page  # noqa: E402

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RIGHTS = "synthetic-fixture-rights-note"


def valid_png(size=(3, 2), color=(200, 30, 40, 128)):
    buffer = io.BytesIO()
    with Image.new("RGBA", size, color) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def parse_chunks(data):
    """Return [(type, payload)] for a fixture built by these tests."""
    assert data.startswith(PNG_SIGNATURE)
    chunks = []
    pos = len(PNG_SIGNATURE)
    while pos < len(data):
        (length,) = struct.unpack_from(">I", data, pos)
        ctype = data[pos + 4: pos + 8]
        payload = data[pos + 8: pos + 8 + length]
        chunks.append((ctype, payload))
        pos += 12 + length
    assert pos == len(data)
    return chunks


def build_png(chunks, bad_crc_types=()):
    out = bytearray(PNG_SIGNATURE)
    for ctype, payload in chunks:
        crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
        if ctype in bad_crc_types:
            crc ^= 0xFF
        out += struct.pack(">I", len(payload))
        out += ctype + payload
        out += struct.pack(">I", crc)
    return bytes(out)


def invalid_zlib_png():
    chunks = parse_chunks(valid_png())
    assert any(ctype == b"IDAT" for ctype, _ in chunks)
    replaced = [
        (ctype, b"\xff\xffnot-a-zlib-stream" if ctype == b"IDAT" else payload)
        for ctype, payload in chunks
    ]
    return build_png(replaced)


class InvalidCompressedDataTests(unittest.TestCase):
    def test_valid_png_control_decodes_exact_pixel(self):
        original = valid_png()
        result = decode_page(original, rights_ref=RIGHTS)
        self.assertEqual(result.original_bytes, original)
        self.assertEqual(result.media_type, "image/png")
        self.assertEqual(result.page_count, 1)
        with Image.open(io.BytesIO(result.png_bytes)) as decoded:
            decoded.load()
            self.assertEqual(decoded.convert("RGBA").getpixel((0, 0)),
                             (200, 30, 40, 128))

    def test_crc_correct_invalid_zlib_idat_is_refused(self):
        corrupt = invalid_zlib_png()
        # The IDAT chunk CRC is correct by construction; only the
        # compressed pixel stream is invalid.
        self.assertTrue(
            any(ctype == b"IDAT" for ctype, _ in parse_chunks(corrupt)))
        with self.assertRaises(PageDecodeError) as ctx:
            decode_page(corrupt, rights_ref=RIGHTS)
        self.assertEqual(ctx.exception.code, "malformed_image")
        self.assertNotIn("not-a-zlib-stream", str(ctx.exception))


def insert_before_iend(chunks, chunk):
    result = list(chunks)
    for index in range(len(result) - 1, -1, -1):
        if result[index][0] == b"IEND":
            result.insert(index, chunk)
            return result
    raise AssertionError("fixture has no IEND chunk")


def multiple_idat_and_ancillary_png():
    chunks = parse_chunks(valid_png())
    compressed = b"".join(payload for ctype, payload in chunks
                           if ctype == b"IDAT")
    split = max(1, len(compressed) // 2)
    rebuilt = []
    inserted = False
    for ctype, payload in chunks:
        if ctype == b"IDAT":
            if not inserted:
                rebuilt.append((b"IDAT", compressed[:split]))
                rebuilt.append((b"IDAT", compressed[split:]))
                inserted = True
        else:
            rebuilt.append((ctype, payload))
    rebuilt = insert_before_iend(
        rebuilt, (b"tEXt", b"Comment\x00synthetic ancillary chunk"))
    return build_png(rebuilt)


class ContainerIntegrityTests(unittest.TestCase):
    def assert_malformed(self, data):
        with self.assertRaises(PageDecodeError) as ctx:
            decode_page(data, rights_ref=RIGHTS)
        self.assertEqual(ctx.exception.code, "malformed_image")

    def test_missing_complete_iend_is_refused(self):
        original = valid_png()
        self.assertEqual(parse_chunks(original)[-1][0], b"IEND")
        self.assert_malformed(original[:-12])

    def test_truncated_terminal_chunk_is_refused(self):
        original = valid_png()
        for cut in (1, 4, 8):
            with self.subTest(bytes_removed=cut):
                self.assert_malformed(original[:-cut])

    def test_corrupt_terminal_crc_is_refused(self):
        original = valid_png()
        corrupt = original[:-1] + bytes([original[-1] ^ 0xFF])
        self.assert_malformed(corrupt)

    def test_bytes_after_complete_png_are_refused(self):
        self.assert_malformed(valid_png() + b"trailing")

    def test_corrupt_ancillary_chunk_crc_is_refused(self):
        chunks = insert_before_iend(
            parse_chunks(valid_png()),
            (b"tEXt", b"Comment\x00synthetic ancillary chunk"))
        self.assert_malformed(
            build_png(chunks, bad_crc_types={b"tEXt"}))

    def test_corrupt_idat_crc_is_refused(self):
        self.assert_malformed(
            build_png(parse_chunks(valid_png()),
                      bad_crc_types={b"IDAT"}))

    def test_multiple_idat_and_ancillary_chunk_are_accepted(self):
        original = multiple_idat_and_ancillary_png()
        chunks = parse_chunks(original)
        self.assertGreaterEqual(
            sum(1 for ctype, _ in chunks if ctype == b"IDAT"), 2)
        self.assertIn(b"tEXt", [ctype for ctype, _ in chunks])
        result = decode_page(original, rights_ref=RIGHTS)
        self.assertEqual(result.original_bytes, original)
        with Image.open(io.BytesIO(result.png_bytes)) as decoded:
            decoded.load()
            self.assertEqual(decoded.convert("RGBA").getpixel((0, 0)),
                             (200, 30, 40, 128))


if __name__ == "__main__":
    unittest.main()
