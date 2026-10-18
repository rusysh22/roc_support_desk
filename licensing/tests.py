import base64
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core import signing
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .middleware import LicenseGateMiddleware
from .models import LicenseRecord
from .entitlements import canonical_entitlement_payload
from .validators import generate_fingerprint, verify_license_online


@override_settings(LICENSE_SETTINGS={
    'MARKETPLACE_URL': 'https://marketplace.example',
    'PRODUCT_ID': 'roc-support-desk',
    'ACTIVATION_API_SECRET': 'test-shared-secret',
    'GRACE_PERIOD_HOURS': 48,
})
class LicenseVerificationTests(TestCase):
    def setUp(self):
        self.license = LicenseRecord.get_current()
        self.license.license_key = signing.dumps('TEST-KEY', salt='roc-license-v1')
        self.license.activation_token = 'old-token'
        self.license.status = 'active'
        self.license.last_verified_at = timezone.now()
        self.license.save()

    @patch('licensing.validators.requests.post')
    def test_rate_limit_keeps_last_known_valid_status(self, post):
        post.return_value = Mock(status_code=200, json=lambda: {'status': 'rate_limited'})

        self.assertFalse(verify_license_online(self.license))
        self.license.refresh_from_db()
        self.assertEqual(self.license.status, 'active')

    @patch('licensing.validators.requests.post')
    def test_invalid_token_response_does_not_require_an_entitlement_signature(self, post):
        post.return_value = Mock(
            status_code=200,
            json=lambda: {
                'status': 'invalid',
                'message': 'Invalid or tampered token — re-activate',
            },
        )

        self.assertFalse(verify_license_online(self.license))
        self.license.refresh_from_db()
        self.assertEqual(self.license.status, 'unlicensed')

    @patch('licensing.validators.requests.post')
    def test_active_heartbeat_recovers_a_suspended_record(self, post):
        self.license.status = 'suspended'
        self.license.save(update_fields=['status'])
        post.return_value = Mock(
            status_code=200,
            json=lambda: {
                'status': 'active',
                'token': 'new-token',
                'entitlements': {'MAX_AGENTS': 7, 'WHATSAPP': True},
            },
        )

        self.assertTrue(verify_license_online(self.license))
        self.license.refresh_from_db()
        self.assertEqual(self.license.status, 'active')
        self.assertEqual(self.license.activation_token, 'new-token')
        self.assertEqual(self.license.max_agents, 7)
        self.assertTrue(self.license.features_json['whatsapp'])
        self.assertEqual(post.call_args.kwargs['headers'], {'X-Berlanggan-Secret': 'test-shared-secret'})

    @patch('licensing.validators.requests.post')
    def test_signed_entitlement_is_authoritative_over_unsigned_response_fields(self, post):
        private_key = Ed25519PrivateKey.generate()
        public_key = base64.b64encode(
            private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode()
        entitlement = {
            'license_id': 'lic_123',
            'fingerprint': generate_fingerprint(),
            'product_id': 'roc-support-desk',
            'status': 'active',
            'issued_at': '2026-07-13T00:00:00Z',
            'license_expires_at': '2027-07-13T00:00:00Z',
            'token_expires_at': '2026-07-20T00:00:00Z',
            'entitlements': {'MAX_AGENTS': 7, 'WHATSAPP': True},
        }
        signature = base64.b64encode(private_key.sign(canonical_entitlement_payload(entitlement))).decode()
        post.return_value = Mock(
            status_code=200,
            json=lambda: {
                'status': 'active',
                'token': 'new-token',
                # These unsigned values must never be trusted.
                'entitlements': {'MAX_AGENTS': 999, 'WHATSAPP': False},
                'entitlement': entitlement,
                'entitlement_signature': signature,
            },
        )

        with self.settings(LICENSE_SETTINGS={
            'MARKETPLACE_URL': 'https://marketplace.example',
            'PRODUCT_ID': 'roc-support-desk',
            'ENTITLEMENT_PUBLIC_KEY': public_key,
            'REQUIRE_SIGNED_ENTITLEMENT': True,
            'GRACE_PERIOD_HOURS': 48,
        }):
            self.assertTrue(verify_license_online(self.license))

        self.license.refresh_from_db()
        self.assertEqual(self.license.max_agents, 7)
        self.assertTrue(self.license.features_json['whatsapp'])
        self.assertEqual(self.license.expires_at.isoformat(), '2027-07-13T00:00:00+00:00')
        self.assertEqual(self.license.token_expires_at.isoformat(), '2026-07-20T00:00:00+00:00')


class LicenseGateTests(TestCase):
    def test_lookup_failure_denies_a_protected_route(self):
        request = RequestFactory().get('/desk/cases/')
        middleware = LicenseGateMiddleware(lambda _request: HttpResponse('application response'))

        with patch('licensing.models.LicenseRecord.get_current', side_effect=RuntimeError('database unavailable')):
            response = middleware(request)

        self.assertEqual(response.status_code, 503)
