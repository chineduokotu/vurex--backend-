"""Offline unit tests for Dojah KYC integration and verification flows.

Run: .venv/Scripts/python.exe -B -m unittest tests.test_dojah_kyc -v
"""

import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import django
from django.conf import settings


if not settings.configured:
    settings.configure(
        SECRET_KEY="offline-dojah-tests-only",
        DEBUG=True,
        INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "transactions", "identity", "notifications"],
        DATABASES={"default": {"ENGINE": "django.db.backends.dummy"}},
        REST_FRAMEWORK={"DEFAULT_AUTHENTICATION_CLASSES": [], "DEFAULT_PERMISSION_CLASSES": []},
        PAYSTACK_SECRET_KEY="sk_test_offline_regression_key",
        PAYSTACK_BASE_URL="https://api.paystack.co",
        DOJAH_APP_ID="64d4ab23b2793b00401a2993",
        DOJAH_SECRET_KEY="test_sk_offline_mock_key",
        DOJAH_BASE_URL="https://api.dojah.io",
        DOJAH_HASH_SALT="offline_test_hash_salt",
        DOJAH_WEBHOOK_SECRET="offline_test_webhook_secret",
        DOJAH_ALLOW_SANDBOX=False,
        KYC_NAME_MATCH_THRESHOLD=0.85,
        USE_TZ=True,
    )
    django.setup()

from django.test import override_settings
from rest_framework.test import APIRequestFactory
from integrations import dojah
from identity import services, views


class DojahOfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Provider/network requests are forbidden in this suite"),
        ))
        self.factory = APIRequestFactory()


class DojahConfigurationTests(DojahOfflineTestCase):
    @override_settings(
        DOJAH_APP_ID="test_pk_invalid_public_key_used_as_app_id",
        DOJAH_SECRET_KEY="test_sk_valid_secret",
        DOJAH_BASE_URL="https://api.dojah.io",
    )
    def test_public_key_as_app_id_is_rejected(self):
        with self.assertRaises(dojah.DojahError) as raised:
            dojah.validate_configuration()
        self.assertEqual(raised.exception.code, "configuration_invalid_dojah_app_id")

    @override_settings(
        DOJAH_APP_ID="prod_pk_live_public_key",
        DOJAH_SECRET_KEY="prod_sk_live_secret",
        DOJAH_BASE_URL="https://api.dojah.io",
    )
    def test_prod_public_key_as_app_id_is_rejected(self):
        with self.assertRaises(dojah.DojahError) as raised:
            dojah.validate_configuration()
        self.assertEqual(raised.exception.code, "configuration_invalid_dojah_app_id")

    @override_settings(
        DEBUG=False,
        DOJAH_APP_ID="64d4ab23b2793b00401a2993",
        DOJAH_SECRET_KEY="test_sk_valid_secret",
        DOJAH_BASE_URL="https://sandbox.dojah.io",
        DOJAH_ALLOW_SANDBOX=False,
    )
    def test_production_sandbox_url_rejected_by_default(self):
        with self.assertRaises(dojah.DojahError) as raised:
            dojah.validate_configuration()
        self.assertEqual(raised.exception.code, "configuration_requires_production_dojah_url")

    @override_settings(
        DEBUG=False,
        DOJAH_APP_ID="64d4ab23b2793b00401a2993",
        DOJAH_SECRET_KEY="test_sk_valid_secret",
        DOJAH_BASE_URL="https://sandbox.dojah.io",
        DOJAH_ALLOW_SANDBOX=True,
    )
    def test_production_sandbox_url_allowed_with_explicit_flag(self):
        # Should not raise when DOJAH_ALLOW_SANDBOX=True
        dojah.validate_configuration()

    @override_settings(
        DEBUG=True,
        DOJAH_APP_ID="64d4ab23b2793b00401a2993",
        DOJAH_SECRET_KEY="test_sk_valid_secret",
        DOJAH_BASE_URL="https://sandbox.dojah.io",
    )
    def test_debug_mode_allows_sandbox(self):
        dojah.validate_configuration()

    @override_settings(
        DEBUG=False,
        DOJAH_APP_ID="64d4ab23b2793b00401a2993",
        DOJAH_SECRET_KEY="prod_sk_live_secret",
        DOJAH_BASE_URL="https://api.dojah.io",
    )
    def test_production_mode_allows_api_url(self):
        dojah.validate_configuration()


class DojahVerificationClientTests(DojahOfflineTestCase):
    @override_settings(
        DOJAH_APP_ID="64d4ab23b2793b00401a2993",
        DOJAH_SECRET_KEY="test_sk_valid_secret",
        DOJAH_BASE_URL="https://api.dojah.io",
    )
    def test_verify_nin_success_and_headers(self):
        mock_response = Mock(status_code=200)
        mock_response.json.return_value = {
            "entity": {
                "firstname": "CHUKWUEMEKA",
                "surname": "OKONKWO",
                "middlename": "JOHN",
                "birthdate": "1992-04-15",
                "phone": "08012345678",
                "gender": "male",
            }
        }
        with patch.object(dojah.requests, "get", return_value=mock_response) as get:
            client = dojah.DojahClient()
            result = client.verify_nin("70123456789")

        self.assertEqual(result.first_name, "CHUKWUEMEKA")
        self.assertEqual(result.last_name, "OKONKWO")
        self.assertEqual(result.middle_name, "JOHN")
        self.assertEqual(result.date_of_birth, "1992-04-15")
        self.assertEqual(result.phone, "08012345678")
        self.assertEqual(result.gender, "male")

        get.assert_called_once_with(
            "https://api.dojah.io/api/v1/kyc/nin",
            params={"nin": "70123456789"},
            headers={
                "Accept": "application/json",
                "AppId": "64d4ab23b2793b00401a2993",
                "Authorization": "test_sk_valid_secret",
            },
            timeout=(5.0, 20.0),
            allow_redirects=False,
        )

    @override_settings(
        DOJAH_APP_ID="64d4ab23b2793b00401a2993",
        DOJAH_SECRET_KEY="test_sk_valid_secret",
        DOJAH_BASE_URL="https://api.dojah.io",
    )
    def test_verify_bvn_success_with_phone_number1(self):
        mock_response = Mock(status_code=200)
        mock_response.json.return_value = {
            "entity": {
                "bvn": "2*****22222",
                "first_name": "CHUKWUEMEKA",
                "last_name": "OKONKWO",
                "middle_name": "JOHN",
                "date_of_birth": "1992-04-15",
                "phone_number1": "08012345678",
                "bank_name": "Access Bank",
            }
        }
        with patch.object(dojah.requests, "get", return_value=mock_response) as get:
            client = dojah.DojahClient()
            result = client.verify_bvn("22222222222")

        self.assertEqual(result.first_name, "CHUKWUEMEKA")
        self.assertEqual(result.last_name, "OKONKWO")
        self.assertEqual(result.middle_name, "JOHN")
        self.assertEqual(result.date_of_birth, "1992-04-15")
        self.assertEqual(result.phone, "08012345678")
        self.assertEqual(result.bank_name, "Access Bank")

        get.assert_called_once_with(
            "https://api.dojah.io/api/v1/kyc/bvn/full",
            params={"bvn": "22222222222"},
            headers={
                "Accept": "application/json",
                "AppId": "64d4ab23b2793b00401a2993",
                "Authorization": "test_sk_valid_secret",
            },
            timeout=(5.0, 20.0),
            allow_redirects=False,
        )

    @override_settings(
        DOJAH_APP_ID="64d4ab23b2793b00401a2993",
        DOJAH_SECRET_KEY="test_sk_valid_secret",
        DOJAH_BASE_URL="https://api.dojah.io",
    )
    def test_invalid_input_rejected_before_network(self):
        client = dojah.DojahClient()
        with patch.object(dojah.requests, "get") as get:
            for bad_id in ("", "123", "1234567890", "123456789012", "abcdefghijk"):
                with self.subTest(bad_id=bad_id):
                    with self.assertRaises(dojah.DojahError) as nin_err:
                        client.verify_nin(bad_id)
                    self.assertEqual(nin_err.exception.code, "id_invalid_format")

                    with self.assertRaises(dojah.DojahError) as bvn_err:
                        client.verify_bvn(bad_id)
                    self.assertEqual(bvn_err.exception.code, "id_invalid_format")
            get.assert_not_called()

    @override_settings(
        DOJAH_APP_ID="64d4ab23b2793b00401a2993",
        DOJAH_SECRET_KEY="test_sk_valid_secret",
        DOJAH_BASE_URL="https://api.dojah.io",
    )
    def test_provider_errors_mapped_properly(self):
        client = dojah.DojahClient()

        # 404 Not Found
        r404 = Mock(status_code=404)
        with patch.object(dojah.requests, "get", return_value=r404):
            with self.assertRaises(dojah.DojahError) as exc:
                client.verify_nin("70123456789")
            self.assertEqual(exc.exception.code, "id_not_found")

        # 400 with "Not Found" in error message
        r400_not_found = Mock(status_code=400)
        r400_not_found.json.return_value = {"error": "Record not found in database"}
        with patch.object(dojah.requests, "get", return_value=r400_not_found):
            with self.assertRaises(dojah.DojahError) as exc:
                client.verify_bvn("22222222222")
            self.assertEqual(exc.exception.code, "id_not_found")

        # 400 with "Invalid" in error message
        r400_invalid = Mock(status_code=400)
        r400_invalid.json.return_value = {"error": "Invalid BVN provided"}
        with patch.object(dojah.requests, "get", return_value=r400_invalid):
            with self.assertRaises(dojah.DojahError) as exc:
                client.verify_bvn("22222222222")
            self.assertEqual(exc.exception.code, "id_invalid_format")

        # 401 Unauthorized / Bad AppId
        r401 = Mock(status_code=401)
        with patch.object(dojah.requests, "get", return_value=r401):
            with self.assertRaises(dojah.DojahError) as exc:
                client.verify_nin("70123456789")
            self.assertEqual(exc.exception.code, "provider_access_rejected")

        # 424 Failed Dependency (upstream service down)
        r424 = Mock(status_code=424)
        with patch.object(dojah.requests, "get", return_value=r424):
            with self.assertRaises(dojah.DojahError) as exc:
                client.verify_nin("70123456789")
            self.assertEqual(exc.exception.code, "provider_unavailable")
            self.assertTrue(exc.exception.retryable)

        # 503 Service Unavailable
        r503 = Mock(status_code=503)
        with patch.object(dojah.requests, "get", return_value=r503):
            with self.assertRaises(dojah.DojahError) as exc:
                client.verify_bvn("22222222222")
            self.assertEqual(exc.exception.code, "provider_unavailable")
            self.assertTrue(exc.exception.retryable)


class NameMatchingTests(unittest.TestCase):
    def test_exact_name_match(self):
        score = services._name_match_score("Chukwuemeka Okonkwo", "Chukwuemeka", "Okonkwo")
        self.assertEqual(score, 1.0)

    def test_first_and_last_match_with_middle_name_on_bvn(self):
        # User registered with "Chukwuemeka Okonkwo" on Vurex.
        # BVN has first_name="Chukwuemeka", last_name="Okonkwo", middle_name="John".
        # Should result in 1.0 confidence match.
        score = services._name_match_score("Chukwuemeka Okonkwo", "Chukwuemeka", "Okonkwo", "John")
        self.assertEqual(score, 1.0)

    def test_name_with_accents_and_case_differences(self):
        score = services._name_match_score("Chukwuémèka OKONKWO", "chukwuemeka", "okonkwo")
        self.assertEqual(score, 1.0)

    def test_first_name_match_only_is_partial_or_rejected(self):
        score = services._name_match_score("Chukwuemeka Okonkwo", "Chukwuemeka", "Adeyemi")
        self.assertLess(score, 0.85)

    def test_complete_mismatch(self):
        score = services._name_match_score("Chukwuemeka Okonkwo", "Babajide", "Sanwoolu")
        self.assertEqual(score, 0.0)


class WebhookSignatureTests(DojahOfflineTestCase):
    @override_settings(DOJAH_WEBHOOK_SECRET="test_webhook_secret_key_12345")
    def test_hmac_sha256_standard_dojah_signature(self):
        body = b'{"event":"kyc.verification","data":{"status":"approved"}}'
        secret = "test_webhook_secret_key_12345"
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        self.assertTrue(dojah.verify_webhook_signature(body, signature))

    @override_settings(DOJAH_WEBHOOK_SECRET="test_webhook_secret_key_12345")
    def test_tampered_body_fails_signature(self):
        body = b'{"event":"kyc.verification","data":{"status":"approved"}}'
        secret = "test_webhook_secret_key_12345"
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        tampered_body = b'{"event":"kyc.verification","data":{"status":"rejected"}}'
        self.assertFalse(dojah.verify_webhook_signature(tampered_body, signature))

    @override_settings(DOJAH_WEBHOOK_SECRET="test_webhook_secret_key_12345")
    def test_hmac_sha512_legacy_signature(self):
        body = b'{"event":"kyc.verification","data":{"status":"approved"}}'
        secret = "test_webhook_secret_key_12345"
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha512).hexdigest()
        self.assertTrue(dojah.verify_webhook_signature(body, signature))

    @override_settings(DOJAH_WEBHOOK_SECRET="test_webhook_secret_key_12345")
    def test_dojah_v2_signature(self):
        body = b'{"event":"kyc.verification","data":{"status":"approved"}}'
        secret = "test_webhook_secret_key_12345"
        signature_v2 = hashlib.sha256(secret.encode("utf-8")).hexdigest()
        self.assertTrue(dojah.verify_webhook_signature(body, signature_v2))

    @override_settings(DOJAH_WEBHOOK_SECRET="")
    def test_unconfigured_webhook_secret_rejects(self):
        body = b'{"event":"kyc.verification"}'
        self.assertFalse(dojah.verify_webhook_signature(body, "any_signature"))


class WebhookEndpointViewTests(DojahOfflineTestCase):
    @override_settings(DOJAH_WEBHOOK_SECRET="test_webhook_secret_key_12345")
    def test_valid_webhook_returns_200(self):
        body = b'{"event":"kyc.verification","id":"evt_123"}'
        secret = "test_webhook_secret_key_12345"
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        request = self.factory.generic(
            "POST",
            "/api/webhooks/dojah/",
            body,
            content_type="application/json",
            HTTP_X_DOJAH_SIGNATURE=signature,
        )
        with patch.object(views, "audit") as mock_audit:
            response = views.dojah_webhook(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "received"})
        mock_audit.assert_called_once()

    @override_settings(DOJAH_WEBHOOK_SECRET="test_webhook_secret_key_12345")
    def test_invalid_signature_returns_401(self):
        body = b'{"event":"kyc.verification"}'
        request = self.factory.generic(
            "POST",
            "/api/webhooks/dojah/",
            body,
            content_type="application/json",
            HTTP_X_DOJAH_SIGNATURE="invalid_signature_hex",
        )
        response = views.dojah_webhook(request)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "invalid_signature")


class KycViewResponseContractTests(DojahOfflineTestCase):
    def test_unauthenticated_request_raises_401(self):
        request = self.factory.post("/api/me/kyc/verify-nin/", {"nin": "70123456789"}, format="json")
        with patch.object(views, "_require_authenticated_user", side_effect=views.IdentityError("sign_in_required", "Please sign in.", 401)):
            response = views.kyc_verify_nin(request)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "sign_in_required")

    def test_verified_nin_returns_200_without_error_key(self):
        mock_record = SimpleNamespace(
            status="verified",
            masked_id="701****6789",
            verified_at=SimpleNamespace(isoformat=lambda: "2026-09-24T00:00:00Z"),
            failure_reason="",
        )
        request = self.factory.post("/api/me/kyc/verify-nin/", {"nin": "70123456789"}, format="json")
        with patch.object(views, "_require_authenticated_user", return_value=Mock()), \
             patch.object(views, "submit_nin_verification", return_value=mock_record):
            response = views.kyc_verify_nin(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "verified")
        self.assertEqual(response.data["masked_id"], "701****6789")
        self.assertNotIn("error", response.data)

    def test_rejected_bvn_returns_422_with_error_and_code(self):
        mock_record = SimpleNamespace(
            status="rejected",
            masked_id="222****2222",
            verified_at=None,
            failure_reason="name_mismatch",
        )
        request = self.factory.post("/api/me/kyc/verify-bvn/", {"bvn": "22222222222"}, format="json")
        with patch.object(views, "_require_authenticated_user", return_value=Mock()), \
             patch.object(views, "submit_bvn_verification", return_value=mock_record):
            response = views.kyc_verify_bvn(request)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.data["status"], "rejected")
        self.assertEqual(response.data["code"], "name_mismatch")
        self.assertIn("The name on your BVN does not match", response.data["error"])
        self.assertEqual(response.data["message"], response.data["error"])

    def test_requires_review_returns_202_without_error(self):
        mock_record = SimpleNamespace(
            status="requires_review",
            masked_id="222****2222",
            verified_at=None,
            failure_reason="name_partial_match",
        )
        request = self.factory.post("/api/me/kyc/verify-bvn/", {"bvn": "22222222222"}, format="json")
        with patch.object(views, "_require_authenticated_user", return_value=Mock()), \
             patch.object(views, "submit_bvn_verification", return_value=mock_record):
            response = views.kyc_verify_bvn(request)

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], "requires_review")
        self.assertNotIn("error", response.data)


if __name__ == "__main__":
    unittest.main()
