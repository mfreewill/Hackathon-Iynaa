"""One vertical HTTP tracer: session Start/upload -> page preview.

No route, decoder,
or domain mocks: expected identities are computed with the real
decoder, and the API exercised is:

- ``POST /api/sessions`` -> 201 ``{"csrf_token": ...}`` and an
  opaque HttpOnly, SameSite=Strict session cookie.
- ``PUT /api/session/settings`` (JSON, CSRF + same Origin) ->
  200 echoing exact ``grade``/``objective``/``rights_ref``.
- ``POST /api/session/start?page_index=0`` with the raw uploaded
  bytes as the request body (no multipart) -> 201 page metadata.
- ``GET /api/session/page`` -> the same selected-page metadata,
  including ``original_url`` and ``preview_url``.
- The two download URLs return the exact original bytes and the
  exact decoded PNG bytes with safe fixed names/types, no-store.
"""
import hashlib
import io
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from teacher_demo.http_app import create_app  # noqa: E402
from teacher_demo.page_decoder import decode_page  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
# Padded, diacritized values: they must round-trip exactly, with
# no trimming or Arabic normalization anywhere in the path.
GRADE = "  الصَّفُّ ٥  "
OBJECTIVE = "  هَدَفٌ: قِرَاءَةُ النَّصِّ ١٢٣  "
RIGHTS_REF = "synthetic-fixture-rights-note"


def synthetic_png():
    buffer = io.BytesIO()
    with Image.new("RGBA", (6, 4), (200, 30, 40, 128)) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


class HttpUploadPreviewTracerTests(unittest.TestCase):
    def test_start_upload_preview_roundtrip(self):
        original = synthetic_png()
        expected = decode_page(original, 0, rights_ref=RIGHTS_REF)
        expected_page = expected.decoded_source.page

        with TestClient(create_app(), base_url=BASE_URL) as client:
            created = client.post(
                "/api/sessions", json={},
                headers={"Origin": BASE_URL})
            self.assertEqual(created.status_code, 201, created.text)
            set_cookie = created.headers.get("set-cookie", "").lower()
            self.assertIn("httponly", set_cookie)
            self.assertIn("samesite=strict", set_cookie)
            csrf = created.json()["csrf_token"]
            self.assertIsInstance(csrf, str)
            self.assertTrue(csrf)
            mutation_headers = {
                "Origin": BASE_URL, "X-CSRF-Token": csrf}

            settings = client.put(
                "/api/session/settings",
                json={"grade": GRADE, "objective": OBJECTIVE,
                      "rights_ref": RIGHTS_REF},
                headers=mutation_headers)
            self.assertEqual(settings.status_code, 200, settings.text)
            self.assertEqual(settings.json()["grade"], GRADE)
            self.assertEqual(settings.json()["objective"], OBJECTIVE)
            self.assertEqual(settings.json()["rights_ref"], RIGHTS_REF)

            started = client.post(
                "/api/session/start", params={"page_index": 0},
                content=original,
                headers={**mutation_headers,
                         "Content-Type": "application/octet-stream"})
            self.assertEqual(started.status_code, 201, started.text)
            metadata = started.json()
            self.assertEqual(metadata["media_type"], "image/png")
            self.assertEqual(metadata["page_count"], 1)
            self.assertEqual(metadata["page_index"], 0)
            self.assertEqual(
                (metadata["width"], metadata["height"]),
                (expected_page.width, expected_page.height))
            # Fresh server-generated id (never the direct decode's).
            self.assertTrue(metadata["source_id"].startswith("page-"))
            self.assertNotEqual(metadata["source_id"],
                                expected_page.source_id)
            self.assertEqual(metadata["source_sha256"],
                             hashlib.sha256(original).hexdigest())
            self.assertEqual(metadata["render_sha256"],
                             expected_page.render_sha256)
            self.assertEqual(metadata["decoder"], expected.decoder)
            self.assertIsNone(metadata["render_dpi"])
            self.assertEqual(metadata["grade"], GRADE)
            self.assertEqual(metadata["objective"], OBJECTIVE)
            self.assertEqual(metadata["rights_ref"], RIGHTS_REF)
            self.assertFalse(metadata["rights_is_clearance"])

            fetched = client.get("/api/session/page")
            self.assertEqual(fetched.status_code, 200, fetched.text)
            self.assertEqual(fetched.json(), metadata)

            original_response = client.get(metadata["original_url"])
            self.assertEqual(original_response.status_code, 200)
            self.assertEqual(original_response.content, original)
            self.assertTrue(original_response.headers["content-type"]
                            .startswith("image/png"))
            self.assertIn("no-store", original_response.headers.get(
                "cache-control", ""))
            self.assertIn("original.png", original_response.headers
                          .get("content-disposition", ""))

            preview = client.get(metadata["preview_url"])
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.content, expected.png_bytes)
            self.assertEqual(
                hashlib.sha256(preview.content).hexdigest(),
                metadata["render_sha256"])
            self.assertTrue(preview.headers["content-type"]
                            .startswith("image/png"))
            self.assertIn("no-store",
                          preview.headers.get("cache-control", ""))
            self.assertIn("preview.png", preview.headers.get(
                "content-disposition", ""))
            with Image.open(io.BytesIO(preview.content)) as image:
                image.load()
                self.assertEqual(image.size, (6, 4))
                self.assertEqual(
                    image.convert("RGBA").getpixel((0, 0)),
                    (200, 30, 40, 128))


if __name__ == "__main__":
    unittest.main()
