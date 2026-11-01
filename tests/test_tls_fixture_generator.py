"""Tests for the test-only local TLS fixture generator.

The generator under test is the future
``tools/generate_tls_fixtures.py`` script. It is loaded by exact path
only after each test first proves that the file exists, so a checkout
without the script fails at that assertion before any third-party
import or temporary fixture work.

Only the standard library is imported at module scope. The
``cryptography`` package is imported inside helpers and tests, after
the existence assertion. Every generation target is an owned
``tempfile.TemporaryDirectory``; no committed fixture file is read,
written, or used as a destination. Certificate parsing and local
``ssl.SSLContext`` loading are the only operations performed on the
generated files. Assertion messages are fixed text and never include
generated file bytes.
"""

import contextlib
import importlib.util
import io
import os
import ssl
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = _REPO_ROOT / "tools" / "generate_tls_fixtures.py"
EXPECTED_FILE_NAMES = frozenset(
    {"ca.pem", "server.pem", "wrong-name.pem", "server-key.pem"}
)
_PEM_BEGIN = "-----BEGIN"
_PRIVATE_MARKER = "PRIVATE KEY"


class TlsFixtureGeneratorTests(unittest.TestCase):
    def _load_generator(self):
        spec = importlib.util.spec_from_file_location(
            "generate_tls_fixtures_under_test", GENERATOR_PATH
        )
        self.assertTrue(
            spec is not None and spec.loader is not None,
            "generator must be importable from its exact path",
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _assert_no_leaked_values(self, texts, sentinel=""):
        for text in texts:
            self.assertFalse(_PEM_BEGIN in text)
            self.assertFalse(_PRIVATE_MARKER in text)
            if sentinel:
                self.assertFalse(sentinel in text)

    def _invoke_main(self, module, argv):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            with contextlib.redirect_stderr(stderr):
                try:
                    result = module.main(list(argv))
                except SystemExit as exc:
                    return (
                        "exit",
                        exc.code,
                        stdout.getvalue(),
                        stderr.getvalue(),
                    )
        return ("return", result, stdout.getvalue(), stderr.getvalue())

    def _assert_main_success(self, outcome):
        _kind, code, stdout, stderr = outcome
        self._assert_no_leaked_values((stdout, stderr))
        self.assertTrue(
            code is None or code == 0, "main must report success"
        )

    def _assert_main_nonzero(self, outcome):
        kind, code, stdout, stderr = outcome
        self._assert_no_leaked_values((stdout, stderr))
        is_int_code = isinstance(code, int) and not isinstance(code, bool)
        if kind == "exit":
            nonzero = (is_int_code and code != 0) or (
                isinstance(code, str) and bool(code)
            )
        else:
            nonzero = is_int_code and code != 0
        self.assertTrue(nonzero, "main refusal must be nonzero")

    def _assert_exact_files(self, directory):
        entries = list(directory.iterdir())
        self.assertEqual(
            {entry.name for entry in entries}, EXPECTED_FILE_NAMES
        )
        for entry in entries:
            self.assertTrue(
                entry.is_file() and not entry.is_symlink(),
                "generated fixture entries must be ordinary files",
            )

    def _snapshot(self, directory):
        return {
            entry.name: entry.read_bytes()
            for entry in directory.iterdir()
        }

    def _assert_snapshot_unchanged(self, before, directory):
        after = self._snapshot(directory)
        self.assertEqual(set(before), set(after))
        for name in sorted(before):
            self.assertTrue(
                before[name] == after[name],
                "pre-existing bytes changed: " + name,
            )

    def _parse_fixture(self, directory):
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        raw = {
            name: (directory / name).read_bytes()
            for name in EXPECTED_FILE_NAMES
        }
        try:
            ca = x509.load_pem_x509_certificate(raw["ca.pem"])
            server = x509.load_pem_x509_certificate(raw["server.pem"])
            wrong = x509.load_pem_x509_certificate(
                raw["wrong-name.pem"]
            )
            key = serialization.load_pem_private_key(
                raw["server-key.pem"], password=None
            )
        except Exception:
            self.fail("generated TLS fixture files must parse as PEM")
        return raw, ca, server, wrong, key

    def _public_bytes(self, public_key):
        from cryptography.hazmat.primitives import serialization

        return public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def _extension(self, certificate, extension_class):
        from cryptography import x509

        try:
            return certificate.extensions.get_extension_for_class(
                extension_class
            ).value
        except x509.ExtensionNotFound:
            self.fail("required certificate extension is missing")

    def _verify_signature(self, certificate, issuer):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import (
            dsa,
            ec,
            ed448,
            ed25519,
            padding,
            rsa,
        )

        verify = getattr(
            certificate, "verify_directly_issued_by", None
        )
        if callable(verify):
            try:
                verify(issuer)
            except Exception:
                self.fail("certificate signature verification failed")
            return
        public_key = issuer.public_key()
        try:
            if isinstance(public_key, rsa.RSAPublicKey):
                public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    padding.PKCS1v15(),
                    certificate.signature_hash_algorithm,
                )
            elif isinstance(public_key, ec.EllipticCurvePublicKey):
                public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    ec.ECDSA(certificate.signature_hash_algorithm),
                )
            elif isinstance(
                public_key,
                (ed25519.Ed25519PublicKey, ed448.Ed448PublicKey),
            ):
                public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                )
            elif isinstance(public_key, dsa.DSAPublicKey):
                public_key.verify(
                    certificate.signature,
                    certificate.tbs_certificate_bytes,
                    certificate.signature_hash_algorithm,
                )
            else:
                self.fail("unsupported issuer public key type")
        except InvalidSignature:
            self.fail("certificate signature verification failed")

    def _assert_currently_valid(self, certificate):
        before = getattr(certificate, "not_valid_before_utc", None)
        if before is None:
            before = certificate.not_valid_before.replace(
                tzinfo=timezone.utc
            )
        after = getattr(certificate, "not_valid_after_utc", None)
        if after is None:
            after = certificate.not_valid_after.replace(
                tzinfo=timezone.utc
            )
        now = datetime.now(timezone.utc)
        tolerance = timedelta(minutes=5)
        self.assertTrue(
            before <= now + tolerance, "certificate is not yet valid"
        )
        self.assertTrue(
            now <= after + tolerance, "certificate is expired"
        )
        self.assertTrue(
            before < after, "certificate validity window is empty"
        )

    def _assert_generate_refuses(self, module, target, sentinel=""):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with self.assertRaises(ValueError) as raised:
            with contextlib.redirect_stdout(stdout):
                with contextlib.redirect_stderr(stderr):
                    module.generate(target)
        message = str(raised.exception)
        self._assert_no_leaked_values(
            (message, stdout.getvalue(), stderr.getvalue()), sentinel
        )
        return message

    def test_import_is_inert_and_requires_explicit_generation(self):
        self.assertTrue(GENERATOR_PATH.is_file(), "TLS fixture generator must exist")
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            previous = Path.cwd()
            stdout = io.StringIO()
            stderr = io.StringIO()
            try:
                os.chdir(work)
                with contextlib.redirect_stdout(stdout):
                    with contextlib.redirect_stderr(stderr):
                        module = self._load_generator()
                self._assert_no_leaked_values(
                    (stdout.getvalue(), stderr.getvalue())
                )
                self.assertEqual(list(work.iterdir()), [])
                self.assertTrue(
                    callable(getattr(module, "generate", None)),
                    "generator must expose generate",
                )
                self.assertTrue(
                    callable(getattr(module, "main", None)),
                    "generator must expose main",
                )
                help_outcome = self._invoke_main(module, ["--help"])
                self._assert_main_success(help_outcome)
                help_text = help_outcome[2] + help_outcome[3]
                self.assertTrue(
                    "--output" in help_text,
                    "help must document the explicit output option",
                )
                self.assertTrue(
                    "usage" in help_text.lower(),
                    "help must show usage",
                )
                self.assertEqual(list(work.iterdir()), [])
            finally:
                os.chdir(previous)

    def test_generated_pems_match_existing_tls_identity_contract(self):
        self.assertTrue(GENERATOR_PATH.is_file(), "TLS fixture generator must exist")
        from cryptography import x509
        from cryptography.x509.oid import ExtendedKeyUsageOID

        module = self._load_generator()
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            module.generate(destination)
            self._assert_exact_files(destination)
            raw, ca, server, wrong, key = self._parse_fixture(
                destination
            )
            private_marker = _PRIVATE_MARKER.encode("ascii")
            for cert_name in ("ca.pem", "server.pem", "wrong-name.pem"):
                self.assertTrue(b"CERTIFICATE" in raw[cert_name])
                self.assertFalse(private_marker in raw[cert_name])
            self.assertTrue(private_marker in raw["server-key.pem"])
            self.assertFalse(b"CERTIFICATE" in raw["server-key.pem"])

            ca_constraints = self._extension(ca, x509.BasicConstraints)
            self.assertTrue(ca_constraints.ca)
            for leaf in (server, wrong):
                leaf_constraints = self._extension(
                    leaf, x509.BasicConstraints
                )
                self.assertFalse(leaf_constraints.ca)
                key_usage = self._extension(leaf, x509.KeyUsage)
                self.assertTrue(key_usage.digital_signature)
                self.assertFalse(key_usage.key_cert_sign)
                self.assertFalse(key_usage.crl_sign)
                extended_usage = self._extension(
                    leaf, x509.ExtendedKeyUsage
                )
                self.assertEqual(
                    list(extended_usage),
                    [ExtendedKeyUsageOID.SERVER_AUTH],
                )

            server_san = self._extension(
                server, x509.SubjectAlternativeName
            )
            self.assertEqual(
                server_san.get_values_for_type(x509.DNSName),
                ["api.meta.ai"],
            )
            self.assertEqual(len(list(server_san)), 1)
            wrong_san = self._extension(
                wrong, x509.SubjectAlternativeName
            )
            self.assertEqual(
                wrong_san.get_values_for_type(x509.DNSName),
                ["fixture-wrong.invalid"],
            )
            self.assertEqual(len(list(wrong_san)), 1)

            self.assertEqual(ca.issuer, ca.subject)
            self.assertEqual(server.issuer, ca.subject)
            self.assertEqual(wrong.issuer, ca.subject)
            self._verify_signature(ca, ca)
            self._verify_signature(server, ca)
            self._verify_signature(wrong, ca)
            for certificate in (ca, server, wrong):
                self.assertTrue(certificate.serial_number > 0)
                self._assert_currently_valid(certificate)

            key_public = self._public_bytes(key.public_key())
            self.assertTrue(
                self._public_bytes(server.public_key()) == key_public,
                "server leaf public key must match the server key",
            )
            self.assertTrue(
                self._public_bytes(wrong.public_key()) == key_public,
                "wrong-name leaf public key must match the server key",
            )
            self.assertTrue(
                self._public_bytes(ca.public_key()) != key_public,
                "CA and server keys must be distinct",
            )

            for leaf_name in ("server.pem", "wrong-name.pem"):
                server_context = ssl.SSLContext(
                    ssl.PROTOCOL_TLS_SERVER
                )
                server_context.load_cert_chain(
                    certfile=str(destination / leaf_name),
                    keyfile=str(destination / "server-key.pem"),
                )
                client_context = ssl.SSLContext(
                    ssl.PROTOCOL_TLS_CLIENT
                )
                client_context.load_verify_locations(
                    cafile=str(destination / "ca.pem")
                )
                self.assertEqual(
                    client_context.verify_mode, ssl.CERT_REQUIRED
                )
                self.assertTrue(client_context.check_hostname)

    def test_generation_refuses_nonempty_or_missing_destination_without_changes(self):
        self.assertTrue(GENERATOR_PATH.is_file(), "TLS fixture generator must exist")
        from cryptography.hazmat.primitives.asymmetric import (
            ec as ec_module,
        )
        from cryptography.hazmat.primitives.asymmetric import (
            rsa as rsa_module,
        )

        module = self._load_generator()
        sentinel = "synthetic-sentinel-7c91"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "missing"
            missing_message = self._assert_generate_refuses(
                module, missing, sentinel
            )
            self.assertFalse(missing.exists())

            ordinary = root / "ordinary-file"
            ordinary.write_bytes(
                (sentinel + "-ordinary\n").encode("ascii")
            )
            ordinary_before = ordinary.read_bytes()
            file_message = self._assert_generate_refuses(
                module, ordinary, sentinel
            )
            self.assertTrue(
                ordinary.is_file() and not ordinary.is_symlink()
            )
            self.assertTrue(
                ordinary.read_bytes() == ordinary_before,
                "ordinary file bytes changed",
            )

            nonempty = root / "nonempty"
            nonempty.mkdir()
            for name in sorted(EXPECTED_FILE_NAMES):
                (nonempty / name).write_bytes(
                    (sentinel + "-" + name + "\n").encode("ascii")
                )
            before = self._snapshot(nonempty)

            # Observer only: the real generate stays in place, and a
            # key-generation call before the refusal fails the test.
            original_rsa_key = rsa_module.generate_private_key
            original_ec_key = ec_module.generate_private_key
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.ExitStack() as stack:
                observers = [
                    stack.enter_context(
                        unittest.mock.patch.object(
                            rsa_module,
                            "generate_private_key",
                            side_effect=AssertionError(
                                "key generation preceded refusal"
                            ),
                        )
                    ),
                    stack.enter_context(
                        unittest.mock.patch.object(
                            ec_module,
                            "generate_private_key",
                            side_effect=AssertionError(
                                "key generation preceded refusal"
                            ),
                        )
                    ),
                ]
                for attr_name, attr_value in list(vars(module).items()):
                    if attr_value is original_rsa_key or (
                        attr_value is original_ec_key
                    ):
                        observers.append(
                            stack.enter_context(
                                unittest.mock.patch.object(
                                    module,
                                    attr_name,
                                    side_effect=AssertionError(
                                        "key generation preceded refusal"
                                    ),
                                )
                            )
                        )
                with self.assertRaises(ValueError) as raised:
                    with contextlib.redirect_stdout(stdout):
                        with contextlib.redirect_stderr(stderr):
                            module.generate(nonempty)
                for observer in observers:
                    self.assertEqual(observer.call_count, 0)
            nonempty_message = str(raised.exception)
            self._assert_no_leaked_values(
                (nonempty_message, stdout.getvalue(), stderr.getvalue()),
                sentinel,
            )
            self._assert_snapshot_unchanged(before, nonempty)

            self.assertTrue(
                missing_message, "refusal must carry a fixed diagnostic"
            )
            self.assertEqual(file_message, missing_message)
            self.assertEqual(nonempty_message, missing_message)

            # Checked only when this platform can create the link in
            # the owned temporary directory without extra privilege.
            link_target = root / "link-target"
            link_target.mkdir()
            link = root / "link"
            linked = False
            try:
                link.symlink_to(link_target, target_is_directory=True)
                linked = link.is_symlink()
            except (NotImplementedError, OSError, RuntimeError):
                linked = False
            if linked:
                link_message = self._assert_generate_refuses(
                    module, link, sentinel
                )
                self.assertEqual(link_message, missing_message)
                self.assertTrue(link.is_symlink())
                self.assertEqual(list(link_target.iterdir()), [])

    def test_separate_generations_are_fresh_and_cli_is_explicit(self):
        self.assertTrue(GENERATOR_PATH.is_file(), "TLS fixture generator must exist")
        module = self._load_generator()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            third = root / "third"
            first.mkdir()
            second.mkdir()
            third.mkdir()

            module.generate(first)
            module.generate(str(second))
            self._assert_exact_files(first)
            self._assert_exact_files(second)
            _raw_first, ca_first, server_first, wrong_first, key_first = (
                self._parse_fixture(first)
            )
            (
                _raw_second,
                ca_second,
                server_second,
                wrong_second,
                key_second,
            ) = self._parse_fixture(second)

            first_key_public = self._public_bytes(key_first.public_key())
            second_key_public = self._public_bytes(
                key_second.public_key()
            )
            self.assertTrue(
                self._public_bytes(server_first.public_key())
                == first_key_public,
                "first server leaf must match its own key",
            )
            self.assertTrue(
                self._public_bytes(wrong_first.public_key())
                == first_key_public,
                "first wrong-name leaf must match its own key",
            )
            self.assertTrue(
                self._public_bytes(server_second.public_key())
                == second_key_public,
                "second server leaf must match its own key",
            )
            self.assertTrue(
                self._public_bytes(wrong_second.public_key())
                == second_key_public,
                "second wrong-name leaf must match its own key",
            )

            self.assertTrue(
                self._public_bytes(ca_first.public_key())
                != self._public_bytes(ca_second.public_key()),
                "separate generations must use fresh CA keys",
            )
            self.assertTrue(
                self._public_bytes(server_first.public_key())
                != self._public_bytes(server_second.public_key()),
                "separate generations must use fresh server keys",
            )
            self.assertTrue(
                self._public_bytes(wrong_first.public_key())
                != self._public_bytes(wrong_second.public_key()),
                "separate wrong-name leaves must use fresh server keys",
            )
            self.assertTrue(
                first_key_public != second_key_public,
                "separate generations must write fresh server keys",
            )
            self.assertTrue(
                ca_first.serial_number != ca_second.serial_number,
                "separate CA certificates must have fresh serials",
            )
            self.assertTrue(
                server_first.serial_number
                != server_second.serial_number,
                "separate server certificates must have fresh serials",
            )
            self.assertTrue(
                wrong_first.serial_number != wrong_second.serial_number,
                "separate wrong-name certificates must have fresh serials",
            )

            created = self._invoke_main(
                module, ["--output", str(third)]
            )
            self._assert_main_success(created)
            self._assert_exact_files(third)
            _raw_third, _ca_third, server_third, _wrong_third, key_third = (
                self._parse_fixture(third)
            )
            self.assertTrue(
                self._public_bytes(server_third.public_key())
                == self._public_bytes(key_third.public_key()),
                "CLI-generated server leaf must match its own key",
            )

            third_before = self._snapshot(third)
            refused = self._invoke_main(
                module, ["--output", str(third)]
            )
            self._assert_main_nonzero(refused)
            self._assert_snapshot_unchanged(third_before, third)


if __name__ == "__main__":
    unittest.main()
