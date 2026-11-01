"""Generate disposable synthetic TLS fixtures for local tests only.

Importing this module creates no files and generates no key material.
Generation happens only when ``generate`` is called, or when ``main``
receives the required explicit ``--output`` directory.

The output is synthetic test material. The CA private key exists only
in memory during one call and is never written. These certificates
must never be used as production certificates, installed into a trust
store, or presented as though they identify a real service.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

__all__ = ["generate", "main"]

_INVALID_DESTINATION = "invalid TLS fixture destination"
_GENERATION_FAILED = "TLS fixture generation failed"
_REPARSE_POINT_ATTRIBUTE = 0x00000400
_CA_NAME = x509.Name(
    [
        x509.NameAttribute(
            NameOID.COMMON_NAME,
            "Synthetic Test CA (local TLS fixtures only)",
        )
    ]
)


def _validate_destination(directory):
    """Return an empty real directory path without following links."""
    if isinstance(directory, bytes) or not isinstance(
        directory, (str, os.PathLike)
    ):
        raise ValueError(_INVALID_DESTINATION)
    try:
        supplied = os.fspath(directory)
    except TypeError:
        raise ValueError(_INVALID_DESTINATION) from None
    if isinstance(supplied, bytes) or supplied == "":
        raise ValueError(_INVALID_DESTINATION)

    target = Path(supplied)
    try:
        # lstat inspects the supplied final component itself. This is
        # intentionally done without turning the target into the path it
        # may point to, so link/reparse evidence is not erased.
        info = target.lstat()
    except (FileNotFoundError, NotADirectoryError):
        raise ValueError(_INVALID_DESTINATION) from None

    file_attributes = getattr(info, "st_file_attributes", 0)
    if stat.S_ISLNK(info.st_mode) or (
        file_attributes & _REPARSE_POINT_ATTRIBUTE
    ):
        raise ValueError(_INVALID_DESTINATION)
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(_INVALID_DESTINATION)

    try:
        with os.scandir(target) as entries:
            is_nonempty = next(entries, None) is not None
    except (FileNotFoundError, NotADirectoryError):
        raise ValueError(_INVALID_DESTINATION) from None
    if is_nonempty:
        raise ValueError(_INVALID_DESTINATION)
    return target


def _write_exclusive(path, payload, mode):
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
    )
    descriptor = os.open(path, flags, mode)
    try:
        stream = os.fdopen(descriptor, "wb")
    except Exception:
        os.close(descriptor)
        raise
    with stream:
        stream.write(payload)


def _certificate_builder(subject, issuer, public_key, serial, window):
    not_before, not_after = window
    return (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(public_key)
        .serial_number(serial)
        .not_valid_before(not_before)
        .not_valid_after(not_after)
    )


def _build_ca_certificate(ca_key, window):
    public_key = ca_key.public_key()
    builder = _certificate_builder(
        _CA_NAME,
        _CA_NAME,
        public_key,
        x509.random_serial_number(),
        window,
    )
    builder = builder.add_extension(
        x509.BasicConstraints(ca=True, path_length=0), critical=True
    )
    builder = builder.add_extension(
        x509.KeyUsage(
            digital_signature=False,
            content_commitment=False,
            key_encipherment=False,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=True,
            crl_sign=True,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    )
    builder = builder.add_extension(
        x509.SubjectKeyIdentifier.from_public_key(public_key),
        critical=False,
    )
    builder = builder.add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(public_key),
        critical=False,
    )
    return builder.sign(private_key=ca_key, algorithm=hashes.SHA256())


def _build_leaf_certificate(dns_name, ca_key, server_key, window):
    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, dns_name)]
    )
    server_public_key = server_key.public_key()
    builder = _certificate_builder(
        subject,
        _CA_NAME,
        server_public_key,
        x509.random_serial_number(),
        window,
    )
    builder = builder.add_extension(
        x509.BasicConstraints(ca=False, path_length=None),
        critical=True,
    )
    builder = builder.add_extension(
        x509.KeyUsage(
            digital_signature=True,
            content_commitment=False,
            key_encipherment=True,
            data_encipherment=False,
            key_agreement=False,
            key_cert_sign=False,
            crl_sign=False,
            encipher_only=False,
            decipher_only=False,
        ),
        critical=True,
    )
    builder = builder.add_extension(
        x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
        critical=False,
    )
    builder = builder.add_extension(
        x509.SubjectAlternativeName([x509.DNSName(dns_name)]),
        critical=False,
    )
    builder = builder.add_extension(
        x509.SubjectKeyIdentifier.from_public_key(server_public_key),
        critical=False,
    )
    builder = builder.add_extension(
        x509.AuthorityKeyIdentifier.from_issuer_public_key(
            ca_key.public_key()
        ),
        critical=False,
    )
    return builder.sign(private_key=ca_key, algorithm=hashes.SHA256())


def generate(directory):
    """Create one fresh synthetic TLS fixture set in ``directory``.

    ``directory`` must name an existing, ordinary, empty directory and
    must not itself be a link or reparse target. Destination validation
    is deliberately completed before either private key is created.
    Invalid destinations raise one fixed, value-free ``ValueError``;
    genuine I/O failures may propagate as ``OSError`` after leaving the
    destination as a failed disposable fixture.
    """
    target = _validate_destination(directory)

    ca_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    )
    server_key = rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    )

    issued_at = datetime.now(timezone.utc).replace(microsecond=0)
    window = (
        issued_at - timedelta(days=1),
        issued_at + timedelta(days=30),
    )
    ca_certificate = _build_ca_certificate(ca_key, window)
    server_certificate = _build_leaf_certificate(
        "api.meta.ai", ca_key, server_key, window
    )
    wrong_name_certificate = _build_leaf_certificate(
        "fixture-wrong.invalid", ca_key, server_key, window
    )

    payloads = (
        (
            "ca.pem",
            ca_certificate.public_bytes(serialization.Encoding.PEM),
            0o644,
        ),
        (
            "server.pem",
            server_certificate.public_bytes(
                serialization.Encoding.PEM
            ),
            0o644,
        ),
        (
            "wrong-name.pem",
            wrong_name_certificate.public_bytes(
                serialization.Encoding.PEM
            ),
            0o644,
        ),
        (
            "server-key.pem",
            server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            0o600,
        ),
    )
    for filename, payload, mode in payloads:
        _write_exclusive(target / filename, payload, mode)


class _ArgumentRefusal(Exception):
    """Internal signal used to keep parser errors value-free."""


class _Parser(argparse.ArgumentParser):
    """An argument parser that does not echo supplied argument text."""

    def error(self, message):
        raise _ArgumentRefusal from None


def _build_parser():
    parser = _Parser(
        prog="generate_tls_fixtures.py",
        allow_abbrev=False,
        description=(
            "Generate disposable synthetic TLS fixtures for local "
            "tests only. The output directory must already exist and "
            "be empty. There is no default destination, and this "
            "material must never be used as a production certificate."
        ),
    )
    parser.add_argument(
        "--output",
        required=True,
        metavar="DIR",
        help="existing empty directory (required; no default)",
    )
    return parser


def main(argv=None):
    """Run the explicit-output CLI.

    Returns ``0`` on success, ``2`` for a refused command line or
    destination, and ``1`` for another local I/O failure. Normal help
    retains argparse's successful ``SystemExit``. Diagnostics are fixed
    text and never include generated file bytes or supplied values.
    """
    try:
        args = _build_parser().parse_args(argv)
    except _ArgumentRefusal:
        print(_INVALID_DESTINATION, file=sys.stderr)
        return 2
    try:
        generate(args.output)
    except ValueError:
        print(_INVALID_DESTINATION, file=sys.stderr)
        return 2
    except OSError:
        print(_GENERATION_FAILED, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
