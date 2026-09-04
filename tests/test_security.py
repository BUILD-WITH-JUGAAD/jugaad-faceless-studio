"""Auth, key isolation, job paths, and Epidemic HLS signatures."""

import hashlib
import hmac
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epidemic_engine import check_media_sig, media_sig, allowed_media_url
from studio import auth
from studio.app import _JOB_ID_RE, _job_path, _rate_limit, _account_key_on


class KeyCryptoTest(unittest.TestCase):
    def test_fernet_roundtrip(self):
        token = auth.encrypt_value("sk-test-secret")
        self.assertTrue(token.startswith("fernet:"))
        self.assertEqual(auth.decrypt_value(token), "sk-test-secret")

    def test_legacy_xor_still_reads(self):
        key = auth._key_material()
        raw = b"legacy-pexels-key"
        token = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
        mac = hmac.new(key, token, hashlib.sha256).digest()
        blob = (mac + token).hex()
        self.assertFalse(blob.startswith("fernet:"))
        self.assertEqual(auth.decrypt_value(blob), "legacy-pexels-key")

    def test_empty_and_garbage(self):
        self.assertEqual(auth.encrypt_value(""), "")
        self.assertEqual(auth.decrypt_value(""), "")
        self.assertEqual(auth.decrypt_value("not-hex"), "")
        self.assertEqual(auth.decrypt_value("fernet:AAAA"), "")


class HostIsolationTest(unittest.TestCase):
    def test_local_can_share_env_keys(self):
        with mock.patch.dict(os.environ, {"RENDER": ""}, clear=False):
            os.environ.pop("RENDER", None)
            self.assertFalse(auth.isolate_account_keys())
            self.assertTrue(_account_key_on("", "env-key"))

    def test_hosted_does_not_share_env_keys(self):
        with mock.patch.dict(os.environ, {"RENDER": "true"}):
            self.assertTrue(auth.isolate_account_keys())
            self.assertFalse(_account_key_on("", "env-key"))
            self.assertTrue(_account_key_on("user-key", "env-key"))


class RegisterGateTest(unittest.TestCase):
    def test_invite_required(self):
        with mock.patch.dict(os.environ, {"JUGAAD_INVITE": "desk-42", "JUGAAD_DISABLE_REGISTER": ""}):
            os.environ.pop("JUGAAD_DISABLE_REGISTER", None)
            self.assertTrue(auth.invite_required())
            auth.assert_can_register("desk-42")
            with self.assertRaises(HTTPException) as caught:
                auth.assert_can_register("nope")
            self.assertEqual(caught.exception.status_code, 403)

    def test_register_can_close(self):
        with mock.patch.dict(os.environ, {"JUGAAD_DISABLE_REGISTER": "1"}):
            self.assertFalse(auth.register_open())
            with self.assertRaises(HTTPException):
                auth.assert_can_register("")


class JobIdTest(unittest.TestCase):
    def test_hex8_ok(self):
        self.assertTrue(_JOB_ID_RE.fullmatch("deadbeef"))
        path = _job_path("deadbeef")
        self.assertEqual(path.name, "deadbeef.json")

    def test_traversal_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            _job_path("../secret")
        self.assertEqual(caught.exception.status_code, 404)
        with self.assertRaises(HTTPException):
            _job_path("purgeprobe")


class HlsSigTest(unittest.TestCase):
    def test_signed_epidemic_url(self):
        url = "https://content.epidemicsound.com/hls/track.m3u8"
        self.assertTrue(allowed_media_url(url))
        sig = media_sig(url)
        self.assertTrue(check_media_sig(url, sig))
        self.assertFalse(check_media_sig(url, "0" * 32))
        self.assertFalse(allowed_media_url("https://evil.amazonaws.com/secret"))


class RateLimitTest(unittest.TestCase):
    def test_trips_after_limit(self):
        class Dummy:
            headers = {}
            client = type("C", (), {"host": "203.0.113.9"})()
            session = {}

        request = Dummy()
        for _ in range(3):
            _rate_limit(request, "unit-test-bucket", 3, 60)
        with self.assertRaises(HTTPException) as caught:
            _rate_limit(request, "unit-test-bucket", 3, 60)
        self.assertEqual(caught.exception.status_code, 429)


if __name__ == "__main__":
    unittest.main()
