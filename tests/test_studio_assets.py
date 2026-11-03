"""Fixed studio script and Cairo font asset tests (real app).

The real ``create_app``/TestClient serves exactly two new fixed
routes, ``/assets/studio.js`` and ``/assets/cairo.ttf``; nothing is
mocked. Packaged bytes are read only after a 200 assertion, so on
the current product (routes not yet registered) the new-route
checks fail with 404 rather than a missing-file or import error.
The font allowance is a bounded presentation need, not generic
static hosting: packaged filenames are never request parameters.
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from teacher_demo.http_app import create_app  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
GRADE = "  الصَّفُّ ٥  "
OBJECTIVE = "  هَدَفٌ: قِرَاءَةُ النَّصِّ ١٢٣  "

EXPECTED_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data: blob:; connect-src 'self'; "
    "font-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'")

NEW_ROUTES = (
    ("/assets/studio.js", "studio.js", "text/javascript"),
    ("/assets/cairo.ttf", "cairo.ttf", "font/ttf"))

EXISTING_ROUTES = (
    ("/", "index.html", "text/html"),
    ("/assets/workspace.css", "workspace.css", "text/css"),
    ("/assets/workspace.js", "workspace.js", "text/javascript"),
    ("/assets/review.js", "review.js", "text/javascript"),
    ("/assets/review-render.js", "review-render.js", "text/javascript"))


def _static_dir():
    from teacher_demo import http_client

    return pathlib.Path(http_client.__file__).resolve().parent / "static"


class StudioAssetTests(unittest.TestCase):
    def assert_client_headers(self, response):
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.headers["x-content-type-options"], "nosniff")
        policy = response.headers["content-security-policy"]
        self.assertEqual(policy, EXPECTED_POLICY)
        for directive in (
                "default-src 'none'", "script-src 'self'",
                "style-src 'self'", "connect-src 'self'",
                "font-src 'self'", "base-uri 'none'",
                "frame-ancestors 'none'"):
            self.assertIn(directive, policy)
        self.assertNotIn("font-src 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertNotIn("http", policy)
        self.assertNotIn("*", policy)

    def test_new_studio_routes_serve_exact_packaged_bytes(self):
        static_dir = _static_dir()
        with TestClient(create_app(), base_url=BASE_URL) as client:
            for route, filename, media_type in NEW_ROUTES:
                with self.subTest(route=route):
                    response = client.get(route)
                    self.assertEqual(response.status_code, 200)
                    # Read only after 200 so the baseline is RED 404.
                    self.assertEqual(
                        response.content,
                        (static_dir / filename).read_bytes())
                    self.assertTrue(
                        response.headers["content-type"].startswith(
                            media_type))
                    self.assert_client_headers(response)

    def test_existing_routes_and_root_api_remain_unchanged(self):
        static_dir = _static_dir()
        with TestClient(create_app(), base_url=BASE_URL) as client:
            for route, filename, media_type in EXISTING_ROUTES:
                with self.subTest(route=route):
                    response = client.get(route)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        response.content,
                        (static_dir / filename).read_bytes())
                    self.assertTrue(
                        response.headers["content-type"].startswith(
                            media_type))
                    self.assert_client_headers(response)
            created = client.post(
                "/api/sessions", json={},
                headers={"Origin": BASE_URL})
            self.assertEqual(created.status_code, 201, created.text)
            headers = {
                "Origin": BASE_URL,
                "X-CSRF-Token": created.json()["csrf_token"]}
            settings = client.put(
                "/api/session/settings",
                json={"grade": GRADE, "objective": OBJECTIVE,
                      "rights_ref": ""}, headers=headers)
            self.assertEqual(settings.status_code, 200, settings.text)
            self.assertEqual(settings.json(), {
                "grade": GRADE, "objective": OBJECTIVE,
                "rights_ref": ""})
            page = client.get("/api/session/page")
            self.assertEqual(page.status_code, 404, page.text)
            self.assertEqual(
                page.json(), {"detail": "page not found"})

    def test_studio_routes_refuse_wrong_host_unknown_paths_and_posts(
            self):
        with TestClient(
                create_app(),
                base_url="http://not-allowed.example") as client:
            for route, _filename, _media_type in NEW_ROUTES:
                with self.subTest(route=route):
                    response = client.get(route)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(
                        response.json(), {"detail": "invalid host"})
        with TestClient(create_app(), base_url=BASE_URL) as client:
            for path in (
                    "/assets/studio.jsx",
                    "/assets/cairo.woff",
                    "/assets/cairo.woff2",
                    "/assets/studio.js/extra",
                    "/assets/cairo.ttf/extra",
                    "/assets/fonts/unregistered.ttf",
                    "/assets/fonts/cairo.ttf",
                    "/assets/unknown-studio.js",
                    "/assets/unknown.ttf",
                    "/assets/%2e%2e/static/studio.js",
                    "/assets/../static/cairo.ttf",
                    "/static/studio.js",
                    "/static/cairo.ttf",
                    "/studio.js",
                    "/cairo.ttf"):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).status_code, 404)
            for route, _filename, _media_type in NEW_ROUTES:
                with self.subTest(route=route):
                    posted = client.post(route, json={})
                    self.assertEqual(posted.status_code, 405)


if __name__ == "__main__":
    unittest.main()

