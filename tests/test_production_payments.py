"""Offline regression tests: no project .env, database setup, or provider requests.

Run: .venv/Scripts/python.exe -B -m unittest discover -s tests -v
"""

import hashlib
import hmac
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import django
import requests
from django.conf import settings


if not settings.configured:
    settings.configure(
        SECRET_KEY="offline-payments-tests-only",
        DEBUG=True,
        INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "transactions"],
        DATABASES={"default": {"ENGINE": "django.db.backends.dummy"}},
        REST_FRAMEWORK={"DEFAULT_AUTHENTICATION_CLASSES": [], "DEFAULT_PERMISSION_CLASSES": []},
        PAYSTACK_SECRET_KEY="sk_test_offline_regression_key",
        PAYSTACK_BASE_URL="https://api.paystack.co",
        USE_TZ=True,
    )
    django.setup()

from django.test import override_settings
from rest_framework.test import APIRequestFactory
from integrations import dojah
from transactions import paystack, views
from transactions.models import TransactionStatus


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.network = self.enterContext(patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Provider/network requests are forbidden in this suite"),
        ))
        self.enterContext(patch(
            "django.db.backends.base.base.BaseDatabaseWrapper.cursor",
            side_effect=AssertionError("Database access is forbidden in this suite"),
        ))
        self.factory = APIRequestFactory()


class PaystackBoundaryTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.transaction = SimpleNamespace(
            id="transaction-id", buyer=SimpleNamespace(email="buyer@example.invalid"),
            vendor_id="vendor-id", amount=Decimal("100.01"),
            escrow_fee=Decimal("1.50"), delivery_fee=Decimal("2.25"),
        )

    def test_unconfigured_keys_never_fabricate_success_or_contact_provider(self):
        operations = [
            lambda: paystack.create_subaccount("Business", "001", "0123456789"),
            lambda: paystack.initialize_transaction(self.transaction),
            lambda: paystack.create_transfer("100.01", "recipient", "reason"),
            lambda: paystack.create_refund("reference", "100.01"),
        ]
        with patch.object(paystack.requests, "post") as post:
            for key in ("", "sk_test_xxxx", "sk_live_xxxx_placeholder"):
                for operation in operations:
                    with self.subTest(key=key, operation=operation), override_settings(PAYSTACK_SECRET_KEY=key):
                        with self.assertRaises(paystack.PaystackError):
                            operation()
            post.assert_not_called()

    def test_real_initialize_contract_includes_displayed_fees_and_auth_headers(self):
        expected = {"authorization_url": "https://checkout.paystack.com/session", "reference": "transaction-id"}
        response = Mock(status_code=200)
        response.json.return_value = {"status": True, "data": expected}
        with patch.object(paystack.requests, "post", return_value=response) as post:
            self.assertEqual(paystack.initialize_transaction(self.transaction), expected)
        post.assert_called_once_with(
            "https://api.paystack.co/transaction/initialize",
            json={
                "email": "buyer@example.invalid", "amount": 10376, "reference": "transaction-id",
                "metadata": {"transaction_id": "transaction-id", "vendor_id": "vendor-id"},
            },
            headers={"Authorization": "Bearer sk_test_offline_regression_key", "Content-Type": "application/json"},
            timeout=20,
        )

    @override_settings(DEBUG=False, PAYSTACK_SECRET_KEY="sk_test_offline_regression_key")
    def test_production_test_keys_cannot_make_payments_or_verify_webhooks(self):
        operations = [
            lambda: paystack.create_subaccount("Business", "001", "0123456789"),
            lambda: paystack.initialize_transaction(self.transaction),
            lambda: paystack.create_transfer("100.01", "recipient", "reason"),
            lambda: paystack.create_refund("reference", "100.01"),
        ]
        with patch.object(paystack.requests, "post") as post:
            for operation in operations:
                with self.subTest(operation=operation), self.assertRaises(paystack.PaystackError):
                    operation()
            post.assert_not_called()
        body = b'{"event":"charge.success"}'
        signature = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest()
        self.assertFalse(paystack.verify_webhook_signature(
            SimpleNamespace(headers={"x-paystack-signature": signature}, body=body),
        ))

    @override_settings(DEBUG=False, PAYSTACK_SECRET_KEY="sk_live_offline_regression_key")
    def test_production_configured_live_key_preserves_requests_and_signatures(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": True, "data": {"subaccount_code": "ACCT_provider"}}
        with patch.object(paystack.requests, "post", return_value=response) as post:
            self.assertEqual(paystack.create_subaccount("Business", "001", "0123456789"), {"subaccount_code": "ACCT_provider"})
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer sk_live_offline_regression_key")
        body = b'{"event":"charge.success"}'
        signature = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest()
        self.assertTrue(paystack.verify_webhook_signature(
            SimpleNamespace(headers={"x-paystack-signature": signature}, body=body),
        ))

    def test_zero_fees_do_not_add_a_sample_or_estimated_fee(self):
        self.transaction.escrow_fee = Decimal("0.00")
        self.transaction.delivery_fee = Decimal("0.00")
        with patch.object(paystack, "_post", return_value={}) as post:
            paystack.initialize_transaction(self.transaction)
        self.assertEqual(post.call_args.args[1]["amount"], 10001)

    def test_provider_errors_are_errors(self):
        bad_json = Mock(status_code=502)
        bad_json.json.side_effect = ValueError("invalid JSON")
        declined = Mock(status_code=200)
        declined.json.return_value = {"status": False, "message": "Declined"}
        unavailable = Mock(status_code=503)
        unavailable.json.return_value = {"status": False, "message": "Unavailable"}
        for response in (bad_json, declined, unavailable):
            with self.subTest(response=response), patch.object(paystack.requests, "post", return_value=response):
                with self.assertRaises(paystack.PaystackError):
                    paystack.create_transfer("100.01", "recipient", "reason")
        with patch.object(paystack.requests, "post", side_effect=requests.Timeout("Timed out")):
            with self.assertRaises(paystack.PaystackError):
                paystack.create_refund("reference", "100.01")

    def test_signed_webhooks_require_a_configured_key_and_matching_body(self):
        body = b'{"event":"charge.success"}'
        for key in ("", "sk_test_xxxx", "sk_live_xxxx_placeholder", "sk_test_offline_regression_key"):
            signature = hmac.new(key.encode(), body, hashlib.sha512).hexdigest()
            request = SimpleNamespace(headers={"x-paystack-signature": signature}, body=body)
            with self.subTest(key=key), override_settings(PAYSTACK_SECRET_KEY=key):
                self.assertEqual(paystack.verify_webhook_signature(request), key == "sk_test_offline_regression_key")
        for request in (
            SimpleNamespace(headers={}, body=body),
            SimpleNamespace(headers={"x-paystack-signature": "invalid"}, body=body),
            SimpleNamespace(headers={"x-paystack-signature": signature}, body=body + b" "),
        ):
            self.assertFalse(paystack.verify_webhook_signature(request))


class PaymentViewTests(OfflineTestCase):
    def test_verification_reports_each_persisted_state_without_changing_it(self):
        for status in (*TransactionStatus.values, "initialized"):
            escrow = Mock(status=status)
            with self.subTest(status=status), patch.object(views, "_get_transaction", return_value=escrow):
                response = views.verify_payment(self.factory.post("/verify", {"transaction_id": "transaction-id"}, format="json"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, {"status": "ok", "transaction_status": status})
            self.assertEqual(escrow.status, status)
            escrow.save.assert_not_called()

    def test_verification_missing_transaction_is_404(self):
        with patch.object(views, "_get_transaction", return_value=None):
            response = views.verify_payment(self.factory.post("/verify", {"transaction_id": "missing"}, format="json"))
        self.assertEqual(response.status_code, 404)

    def test_accepting_dispute_reports_unavailable_without_settlement_or_mutation(self):
        escrow = Mock(status=TransactionStatus.DISPUTED)
        dispute = Mock(outcome=None)
        with patch.object(views, "_get_transaction", return_value=escrow), patch.object(views.Dispute.objects, "filter") as query:
            query.return_value.order_by.return_value.first.return_value = dispute
            response = views.accept_dispute(self.factory.post("/accept", {"transaction_id": "transaction-id"}, format="json"))
        self.assertEqual(response.status_code, 503)
        self.assertIn("unavailable", response.data["error"])
        self.assertEqual(escrow.status, TransactionStatus.DISPUTED)
        self.assertIsNone(dispute.outcome)
        escrow.save.assert_not_called()
        dispute.save.assert_not_called()
        self.network.assert_not_called()

    def test_accepting_dispute_preserves_validation_and_missing_states(self):
        response = views.accept_dispute(self.factory.post("/accept", {}, format="json"))
        self.assertEqual(response.status_code, 400)
        for escrow, expected_status in ((None, 404), (Mock(status=TransactionStatus.CREATED), 400)):
            with patch.object(views, "_get_transaction", return_value=escrow):
                response = views.accept_dispute(self.factory.post("/accept", {"transaction_id": "transaction-id"}, format="json"))
            self.assertEqual(response.status_code, expected_status)
        with patch.object(views, "_get_transaction", return_value=Mock(status=TransactionStatus.DISPUTED)), patch.object(views.Dispute.objects, "filter") as query:
            query.return_value.order_by.return_value.first.return_value = None
            response = views.accept_dispute(self.factory.post("/accept", {"transaction_id": "transaction-id"}, format="json"))
        self.assertEqual(response.status_code, 404)

    def test_valid_provider_webhook_still_records_funding(self):
        body = b'{"event":"charge.success","data":{"reference":"payment-reference"}}'
        signature = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest()
        request = self.factory.generic("POST", "/webhook", body, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=signature)
        with patch.object(views.Transaction.objects, "filter") as query:
            response = views.paystack_webhook(request)
        self.assertEqual(response.status_code, 200)
        query.assert_called_once_with(payment_ref="payment-reference")
        self.assertEqual(query.return_value.update.call_args.kwargs["status"], TransactionStatus.FUNDED)

    def test_unsigned_webhook_never_records_funding(self):
        request = self.factory.post("/webhook", {"event": "charge.success", "data": {"reference": "payment-reference"}}, format="json")
        with patch.object(views.Transaction.objects, "filter") as query:
            response = views.paystack_webhook(request)
        self.assertEqual(response.status_code, 400)
        query.assert_not_called()


class DojahEnvironmentTests(OfflineTestCase):
    @override_settings(
        DEBUG=False, DOJAH_APP_ID="offline-test-app", DOJAH_SECRET_KEY="offline-test-key",
        DOJAH_BASE_URL="https://sandbox.dojah.io",
    )
    def test_production_sandbox_config_fails_before_provider_access(self):
        with patch.object(dojah.requests, "get") as get:
            with self.assertRaises(dojah.DojahError) as raised:
                dojah.validate_configuration()
            self.assertEqual(raised.exception.code, "configuration_requires_production_dojah_url")
            with self.assertRaises(dojah.DojahError):
                dojah.DojahClient()
            get.assert_not_called()

    @override_settings(DOJAH_APP_ID="offline-test-app", DOJAH_SECRET_KEY="offline-test-key")
    def test_development_sandbox_and_production_host_remain_configurable(self):
        with patch.object(dojah.requests, "get") as get:
            for debug, base_url in ((True, "https://sandbox.dojah.io"), (False, "https://api.dojah.io")):
                with self.subTest(debug=debug), override_settings(DEBUG=debug, DOJAH_BASE_URL=base_url):
                    dojah.validate_configuration()
                    dojah.DojahClient()
            get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
