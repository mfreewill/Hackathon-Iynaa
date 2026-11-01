"""PNG actual-terminal boundary tests. Tiny synthetic fixtures only."""
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
IEND_CHUNK = b"\x00\x00\x00\x00IEND\xaeB\x60\x82"
RIGHTS = "synthetic-fixture-rights-note"


def valid_png(size=(3, 2), color=(200, 30, 40, 128)):
    buffer = io.BytesIO()
    with Image.new("RGBA", size, color) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def parse_chunks(data):
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


def build_png(chunks):
    out = bytearray(PNG_SIGNATURE)
    for ctype, payload in chunks:
        crc = zlib.crc32(ctype + payload) & 0xFFFFFFFF
        out += struct.pack(">I", len(payload))
        out += ctype + payload
        out += struct.pack(">I", crc)
    return bytes(out)


class ActualTerminalBoundaryTests(unittest.TestCase):
    def assert_malformed(self, data):
        with self.assertRaises(PageDecodeError) as ctx:
            decode_page(data, rights_ref=RIGHTS)
        self.assertEqual(ctx.exception.code, "malformed_image")

    def test_trailing_bytes_then_second_iend_is_refused(self):
        self.assert_malformed(valid_png() + b"trailing-bytes" + IEND_CHUNK)

    def test_corrupt_actual_iend_crc_with_decoy_is_refused(self):
        original = valid_png()
        corrupt_tail = original[:-1] + bytes([original[-1] ^ 0xFF])
        self.assert_malformed(corrupt_tail + IEND_CHUNK)

    def test_nonempty_iend_payload_with_missing_crc_is_refused(self):
        original = valid_png()
        # Drop the genuine terminal chunk; the actual terminal chunk is
        # a nonempty IEND whose payload is a complete IEND sequence,
        # with its own CRC truncated away (absent).
        prefix = original[:-12]
        data = prefix + struct.pack(">I", len(IEND_CHUNK)) + b"IEND" + IEND_CHUNK
        self.assertTrue(data.endswith(IEND_CHUNK))
        self.assert_malformed(data)

    def test_intact_png_control_decodes(self):
        original = valid_png()
        result = decode_page(original, rights_ref=RIGHTS)
        self.assertEqual(result.original_bytes, original)
        self.assertEqual(result.media_type, "image/png")
        with Image.open(io.BytesIO(result.png_bytes)) as decoded:
            decoded.load()
            self.assertEqual(decoded.convert("RGBA").getpixel((0, 0)),
                             (200, 30, 40, 128))

    def test_ancillary_and_multiple_idat_control_decodes(self):
        chunks = parse_chunks(valid_png())
        compressed = b"".join(p for t, p in chunks if t == b"IDAT")
        split = max(1, len(compressed) // 2)
        rebuilt = []
        inserted = False
        for ctype, payload in chunks:
            if ctype == b"IDAT":
                if not inserted:
                    rebuilt.append((b"IDAT", compressed[:split]))
                    rebuilt.append((b"IDAT", compressed[split:]))
                    inserted = True
            elif ctype == b"IEND":
                rebuilt.append((b"tEXt", b"Comment\x00synthetic"))
                rebuilt.append((ctype, payload))
            else:
                rebuilt.append((ctype, payload))
        original = build_png(rebuilt)
        result = decode_page(original, rights_ref=RIGHTS)
        self.assertEqual(result.original_bytes, original)
        with Image.open(io.BytesIO(result.png_bytes)) as decoded:
            decoded.load()
            self.assertEqual(decoded.convert("RGBA").getpixel((0, 0)),
                             (200, 30, 40, 128))


if __name__ == "__main__":
    unittest.main()
