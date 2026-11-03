"""Static setup/source client registration tests (real app).

The real ``create_app``/TestClient serves the fixed client routes;
nothing is mocked and no fake UI server is involved. Browser
behavior itself lives in ``tests/browser/test_setup_source.py``.
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


class HttpClientRegistrationTests(unittest.TestCase):
    def assert_client_headers(self, response):
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.headers["x-content-type-options"], "nosniff")
        policy = response.headers["content-security-policy"]
        for directive in (
                "default-src 'none'", "script-src 'self'",
                "style-src 'self'", "connect-src 'self'",
                "base-uri 'none'", "frame-ancestors 'none'"):
            self.assertIn(directive, policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("http", policy)

    def test_fixed_client_routes_serve_packaged_assets(self):
        with TestClient(create_app(), base_url=BASE_URL) as client:
            index = client.get("/")
            self.assertEqual(index.status_code, 200, index.text)
            self.assertTrue(
                index.headers["content-type"].startswith("text/html"))
            self.assert_client_headers(index)
            html = index.text
            self.assertIn('<html lang="ar" dir="rtl">', html)
            self.assertIn('href="/assets/workspace.css"', html)
            self.assertIn('src="/assets/workspace.js"', html)
            self.assertIn("تهيئة الصفحة", html)
            self.assertIn("تحليل هذه الصفحة", html)
            self.assertIn("مساحة تجريبية غير محفوظة", html)
            self.assertNotIn("http://", html)
            self.assertNotIn("https://", html)
            self.assertNotIn("<script>", html.replace(
                '<script type="module" src="/assets/workspace.js">', ""))

            stylesheet = client.get("/assets/workspace.css")
            self.assertEqual(
                stylesheet.status_code, 200, stylesheet.text)
            self.assertTrue(
                stylesheet.headers["content-type"].startswith(
                    "text/css"))
            self.assert_client_headers(stylesheet)
            self.assertIn("@media (max-width: 1023.98px)",
                          stylesheet.text)
            self.assertIn("prefers-reduced-motion", stylesheet.text)
            self.assertNotIn("http://", stylesheet.text)
            self.assertNotIn("https://", stylesheet.text)
            self.assertNotIn("@import", stylesheet.text)

            script = client.get("/assets/workspace.js")
            self.assertEqual(script.status_code, 200, script.text)
            self.assertTrue(
                script.headers["content-type"].startswith(
                    "text/javascript"))
            self.assert_client_headers(script)
            self.assertIn("/api/sessions", script.text)
            self.assertIn("/api/session/settings", script.text)
            self.assertIn("/api/session/start", script.text)
            self.assertIn("/api/session/source-analysis", script.text)
            self.assertNotIn("http://", script.text)
            self.assertNotIn("https://", script.text)
            self.assertNotIn("eval(", script.text)
            self.assertNotIn("innerHTML", script.text)

    def test_review_script_routes_serve_exact_packaged_bytes(self):
        from teacher_demo import http_client

        static_dir = (
            pathlib.Path(http_client.__file__).resolve().parent
            / "static")
        expected_policy = (
            "default-src 'none'; script-src 'self'; "
            "style-src 'self'; img-src 'self' data: blob:; "
            "connect-src 'self'; font-src 'self'; "
            "base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'")
        with TestClient(create_app(), base_url=BASE_URL) as client:
            for route, filename in (
                    ("/assets/review.js", "review.js"),
                    ("/assets/review-render.js", "review-render.js")):
                with self.subTest(route=route):
                    response = client.get(route)
                    self.assertEqual(
                        response.status_code, 200, response.text)
                    self.assertEqual(
                        response.content,
                        (static_dir / filename).read_bytes())
                    self.assertTrue(
                        response.headers["content-type"].startswith(
                            "text/javascript"))
                    self.assert_client_headers(response)
                    self.assertEqual(
                        response.headers["content-security-policy"],
                        expected_policy)

    def test_review_script_routes_refuse_wrong_host(self):
        with TestClient(
                create_app(),
                base_url="http://not-allowed.example") as client:
            for path in ("/assets/review.js",
                         "/assets/review-render.js"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(
                        response.status_code, 400, response.text)
                    self.assertEqual(
                        response.json(), {"detail": "invalid host"})

    def test_review_script_extra_traversal_and_unknown_paths_are_refused(
            self):
        with TestClient(create_app(), base_url=BASE_URL) as client:
            for path in (
                    "/assets/review.js/extra",
                    "/assets/review-render.js/extra",
                    "/assets/review.jsx",
                    "/assets/review-render.jsx",
                    "/assets/unknown-review.js",
                    "/assets/%2e%2e/static/review.js",
                    "/assets/../static/review-render.js",
                    "/static/review.js",
                    "/review.js"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(
                        response.status_code, 404, response.text)
            for path in ("/assets/review.js",
                         "/assets/review-render.js"):
                with self.subTest(path=path):
                    posted = client.post(path, json={})
                    self.assertEqual(posted.status_code, 405, posted.text)

    def test_unknown_traversal_and_source_paths_are_refused(self):
        with TestClient(create_app(), base_url=BASE_URL) as client:
            for path in (
                    "/index.html",
                    "/static/index.html",
                    "/assets/unknown.js",
                    "/assets/workspace.js/extra",
                    "/assets/%2e%2e/http_app.py",
                    "/assets/../http_client.py",
                    "/src/teacher_demo/http_app.py",
                    "/favicon.ico"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(
                        response.status_code, 404, response.text)
            posted = client.post("/", json={})
            self.assertEqual(posted.status_code, 405, posted.text)

    def test_wrong_host_is_refused_on_client_routes(self):
        with TestClient(
                create_app(),
                base_url="http://not-allowed.example") as client:
            for path in ("/", "/assets/workspace.css",
                         "/assets/workspace.js"):
                with self.subTest(path=path):
                    response = client.get(path)
                    self.assertEqual(
                        response.status_code, 400, response.text)
                    self.assertEqual(
                        response.json(), {"detail": "invalid host"})

    def test_client_registration_does_not_replace_the_api(self):
        with TestClient(create_app(), base_url=BASE_URL) as client:
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


if __name__ == "__main__":
    unittest.main()
