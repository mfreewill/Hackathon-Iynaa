"""Startup must validate the existing usage ledger before wiring Meta."""

import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import hosted_launch
from teacher_demo.hosted_usage import (
    UsageLedger,
    UsagePolicy,
    initialize_usage,
)

_ENV_NAME = "HACKATHON_IYNAA_META_API_KEY"
_SYNTHETIC_KEY = "synthetic-ascii-test-key-0123"
_HOSTNAME = "demo.example.com"
_FIXED_ERROR = "invalid hosted launch configuration"


class HostedStartupLedgerTest(unittest.TestCase):
    def test_meta_startup_validates_existing_ledger_first(self):
        policy = UsagePolicy(
            max_attempts=3,
            max_output_tokens=64,
            window_end=4102444800.0,
            minimum_spacing_seconds=0.5,
        )
        other_policy = UsagePolicy(
            max_attempts=4,
            max_output_tokens=64,
            window_end=4102444800.0,
            minimum_spacing_seconds=0.5,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            missing = root / "missing.sqlite3"
            corrupt = root / "corrupt.sqlite3"
            corrupt_bytes = b"not a sqlite usage store"
            corrupt.write_bytes(corrupt_bytes)
            mismatched = root / "mismatched.sqlite3"
            initialize_usage(mismatched, policy)
            mismatched_bytes = mismatched.read_bytes()
            healthy = root / "healthy.sqlite3"
            initialize_usage(healthy, policy)
            self.assertEqual(
                UsageLedger(healthy, policy).snapshot()["spent"], 0)

            invalid_cases = (
                ("missing", missing, policy, None),
                ("corrupt", corrupt, policy, corrupt_bytes),
                ("policy mismatch", mismatched, other_policy,
                 mismatched_bytes),
            )
            with mock.patch.dict(
                    os.environ, {_ENV_NAME: _SYNTHETIC_KEY}):
                for label, path, case_policy, before in invalid_cases:
                    with self.subTest(case=label):
                        meta_provider = mock.Mock(name="MetaProvider")
                        create_app = mock.Mock(name="create_app")
                        with mock.patch.object(
                                hosted_launch, "MetaProvider",
                                meta_provider), mock.patch.object(
                                hosted_launch, "create_app", create_app):
                            with self.assertRaises(ValueError) as caught:
                                hosted_launch.create_hosted_app(
                                    hostname=_HOSTNAME,
                                    meta=True,
                                    usage_path=path,
                                    policy=case_policy,
                                )
                        self.assertEqual(
                            str(caught.exception), _FIXED_ERROR)
                        self.assertIsNone(caught.exception.__cause__)
                        self.assertIsNone(caught.exception.__context__)
                        meta_provider.assert_not_called()
                        create_app.assert_not_called()
                        if before is None:
                            self.assertFalse(path.exists())
                        else:
                            self.assertEqual(path.read_bytes(), before)

                app_sentinel = object()
                meta_provider = mock.Mock(
                    name="MetaProvider",
                    return_value=mock.Mock(name="MetaProvider instance"))
                create_app = mock.Mock(
                    name="create_app", return_value=app_sentinel)
                with mock.patch.object(
                        hosted_launch, "MetaProvider",
                        meta_provider), mock.patch.object(
                        hosted_launch, "create_app", create_app):
                    app = hosted_launch.create_hosted_app(
                        hostname=_HOSTNAME,
                        meta=True,
                        usage_path=healthy,
                        policy=policy,
                    )
                self.assertIs(app, app_sentinel)
                meta_provider.assert_called_once()
                create_app.assert_called_once()
                self.assertEqual(
                    UsageLedger(healthy, policy).snapshot()["spent"], 0)


if __name__ == "__main__":
    unittest.main()
