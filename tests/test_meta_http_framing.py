"""Paired chunked-coding regression tests for ``meta_http``.

Tests-only continuation of the one-send HTTPS oracle. For six
literal ``Transfer-Encoding`` values that the contracted header
validation admits as a sole ``chunked`` coding (after stripping
outer ASCII SP/HTAB and case-folding), the actual response
reader must agree with that validation before the first body
read:

* a correctly chunked healthy SSE body must decode to the exact
  accepted collector result;
* the same healthy SSE bytes sent without any chunk framing
  must never return success, and must keep the fixed closed
  ``MetaProtocolError`` outcome ``unknown`` — a valid semantic
  terminal never substitutes for transport framing.

This module reuses the existing ``test_meta_http`` fixture as a
module (its exact-host/port loopback server, root-loader-only
TLS trust seam, complete one-request TLS-write accounting, and
actual closure checks) and exercises the real ``send_payload``
through ``MetaHttpTest._exchange``. It defines no server, client,
scanner, or collector of its own, mocks no HTTP reply, and
touches no response state. Failure messages are fixed and
value-free.
"""
import unittest

import test_meta_http

# Exact contract population. The escapes denote actual tab and
# space characters in the header value.
_VARIANTS = (
    ("plain", "chunked"),
    ("mixed-case", "ChUnKeD"),
    ("leading-tab", "\tchunked"),
    ("trailing-space", "chunked "),
    ("trailing-tab", "chunked\t"),
    ("mixed-outer", " \tChUnKeD \t"),
)


def _chunked_spec(header_value, wire_body):
    return test_meta_http._spec(
        200,
        [
            ("Content-Type", "text/event-stream"),
            ("Transfer-Encoding", header_value),
            ("Connection", "close"),
        ],
        wire_body,
    )


class MetaHttpFramingTest(unittest.TestCase):
    def test_chunked_coding_variants_decode_encoded_body(self):
        credential = test_meta_http._credential()
        body = test_meta_http._healthy_sse()
        expected_result = test_meta_http._collector_result(
            body, credential
        )
        wire_body = test_meta_http._chunked_body(body)
        for label, header_value in _VARIANTS:
            with self.subTest(variant=label):
                exchange = test_meta_http.MetaHttpTest._exchange(
                    self, [_chunked_spec(header_value, wire_body)]
                )
                self.assertTrue(
                    exchange["exc"] is None,
                    "encoded body must return success",
                )
                self.assertTrue(
                    exchange["result"] == expected_result,
                    "collector result mismatch",
                )
                test_meta_http.MetaHttpTest._assert_one_post(
                    self, exchange
                )
                test_meta_http.MetaHttpTest._assert_closed(
                    self, exchange["captured"]
                )

    def test_chunked_coding_variants_reject_raw_sse(self):
        credential = test_meta_http._credential()
        body = test_meta_http._healthy_sse()
        # The same bytes are a genuine healthy collector input;
        # only the declared chunk framing is absent on the wire.
        self.assertTrue(
            test_meta_http._collector_result(body, credential)
            is not None,
            "healthy collector fixture must itself succeed",
        )
        for label, header_value in _VARIANTS:
            with self.subTest(variant=label):
                exchange = test_meta_http.MetaHttpTest._exchange(
                    self, [_chunked_spec(header_value, body)]
                )
                self.assertTrue(
                    exchange["result"] is None,
                    "raw body must not return success",
                )
                test_meta_http.MetaHttpTest._assert_protocol_error(
                    self, exchange["exc"], "unknown"
                )
                test_meta_http.MetaHttpTest._assert_one_post(
                    self, exchange
                )
                test_meta_http.MetaHttpTest._assert_closed(
                    self, exchange["captured"]
                )


if __name__ == "__main__":
    unittest.main()
