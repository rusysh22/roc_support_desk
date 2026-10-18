"""Marketplace activation, heartbeat, and entitlement utilities."""
import hashlib
import hmac
import json
import logging

import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .entitlements import (
    EntitlementSignatureError,
    verify_entitlement_binding,
    verify_entitlement_signature,
)

logger = logging.getLogger(__name__)


TIER_DEFAULT_FEATURES: dict[str, dict] = {
    'trial': {
        'whatsapp': False, 'email_settings': False, 'form_builder': False,
        'kb_manage': False, 'short_links': False, 'analytics': False,
        'company_units': False, 'sla_reports': False, 'audit_export': False,
        'api_access': False, 'esign': False, 'shared_docs': False,
        'project_management': False,
    },
    'starter': {
        'whatsapp': True, 'email_settings': True, 'form_builder': True,
        'kb_manage': True, 'short_links': True, 'analytics': False,
        'company_units': False, 'sla_reports': False, 'audit_export': False,
        'api_access': False, 'esign': False, 'shared_docs': False,
        'project_management': False,
    },
    'professional': {
        'whatsapp': True, 'email_settings': True, 'form_builder': True,
        'kb_manage': True, 'short_links': True, 'analytics': True,
        'company_units': True, 'sla_reports': True, 'audit_export': True,
        'api_access': False, 'esign': True, 'shared_docs': True,
        'project_management': True,
    },
    'business': {
        'whatsapp': True, 'email_settings': True, 'form_builder': True,
        'kb_manage': True, 'short_links': True, 'analytics': True,
        'company_units': True, 'sla_reports': True, 'audit_export': True,
        'api_access': True, 'esign': True, 'shared_docs': True,
        'project_management': True,
    },
    'enterprise': {
        'whatsapp': True, 'email_settings': True, 'form_builder': True,
        'kb_manage': True, 'short_links': True, 'analytics': True,
        'company_units': True, 'sla_reports': True, 'audit_export': True,
        'api_access': True, 'esign': True, 'shared_docs': True,
        'project_management': True,
    },
}


def verify_webhook_signature(request_body: bytes, signature_header: str, secret: str) -> bool:
    """Legacy helper retained only for audit compatibility; no route exposes it."""
    if not signature_header or not signature_header.startswith('sha256=') or not secret:
        return False
    received = signature_header[7:]
    expected = hmac.new(secret.encode('utf-8'), request_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(received, expected)


def generate_fingerprint() -> str:
    """Return a stable fingerprint for the marketplace installation seat."""
    from .models import LicenseRecord

    product_id = getattr(settings, 'LICENSE_SETTINGS', {}).get('PRODUCT_ID', 'roc-support-desk')
    installation_id = LicenseRecord.get_current().installation_id
    return hashlib.sha256(f"{installation_id}:{product_id}".encode()).hexdigest()


def _activation_headers() -> dict:
    secret = getattr(settings, 'LICENSE_SETTINGS', {}).get('ACTIVATION_API_SECRET', '')
    return {'X-Berlanggan-Secret': secret} if secret else {}


def _marketplace_url() -> str:
    from core.models import SiteConfig

    try:
        return SiteConfig.get_solo().marketplace_url.rstrip('/')
    except Exception:
        return getattr(settings, 'LICENSE_SETTINGS', {}).get(
            'MARKETPLACE_URL', 'https://tokowebjaya.com',
        ).rstrip('/')


def _trusted_marketplace_response(data: dict, fingerprint: str) -> dict:
    """Use only signed entitlement fields once a Marketplace key is configured.

    Marketplace responses retain the short-lived activation token at the top
    level, while the signed entitlement is the authority for status, expiry,
    plan limits, and feature grants.
    """
    cfg = getattr(settings, 'LICENSE_SETTINGS', {})
    public_key = cfg.get('ENTITLEMENT_PUBLIC_KEY', '')
    require_signature = cfg.get('REQUIRE_SIGNED_ENTITLEMENT', False) or bool(public_key)
    if not require_signature:
        return data

    entitlement = data.get('entitlement')
    signature = data.get('entitlement_signature')
    verified = verify_entitlement_signature(entitlement, signature, public_key)
    verify_entitlement_binding(
        verified,
        fingerprint=fingerprint,
        product_id=cfg.get('PRODUCT_ID', 'roc-support-desk'),
    )
    if data.get('status') != verified['status']:
        raise EntitlementSignatureError('response status does not match the signed entitlement.')

    # Do not trust duplicate unsigned fields supplied beside the signature.
    trusted = dict(data)
    trusted['status'] = verified['status']
    trusted['license_expires_at'] = verified.get(
        'license_expires_at', verified.get('expires_at'),
    )
    trusted['token_expires_at'] = verified.get('token_expires_at')
    trusted['entitlements'] = verified['entitlements']
    return trusted


def _sync_entitlements(license_obj, data: dict, status: str) -> None:
    """Persist a successful Marketplace response as the local source of truth."""
    # Marketplace separates the commercial end date from the short-lived
    # activation token. `expires_at` is accepted only as a legacy fallback.
    if 'license_expires_at' in data:
        license_obj.expires_at = (
            parse_datetime(data['license_expires_at'])
            if data['license_expires_at'] else None
        )
    elif 'expires_at' in data:
        license_obj.expires_at = parse_datetime(data['expires_at']) if data['expires_at'] else None
    if 'token_expires_at' in data:
        license_obj.token_expires_at = (
            parse_datetime(data['token_expires_at'])
            if data['token_expires_at'] else None
        )
    if data.get('token'):
        license_obj.activation_token = data['token']

    entitlements = data.get('entitlements', {})
    plan = entitlements.get('PLAN_TIER') or entitlements.get('plan') or license_obj.plan_tier or 'starter'
    license_obj.plan_tier = str(plan).lower()
    max_agents = entitlements.get('MAX_AGENTS') or entitlements.get('max_agents')
    if max_agents is not None and str(max_agents).isdigit():
        license_obj.max_agents = int(max_agents)

    features = {}
    for key, value in entitlements.items():
        if str(value).lower() == 'true':
            features[key.lower()] = True
        elif str(value).lower() == 'false':
            features[key.lower()] = False
        else:
            features[key.lower()] = value
    license_obj.features_json = features or TIER_DEFAULT_FEATURES.get(license_obj.plan_tier, {})
    license_obj.status = status
    license_obj.install_fingerprint = generate_fingerprint()
    license_obj.last_verified_at = timezone.now()
    license_obj.save()


def _marketplace_unavailable(license_obj, reason: str, **details) -> bool:
    """Apply offline grace only to transport failures, never business statuses."""
    from .models import LicenseAuditLog

    payload = {'reason': reason, **details}
    if license_obj.last_verified_at:
        hours_since = (timezone.now() - license_obj.last_verified_at).total_seconds() / 3600
        payload['hours_since'] = hours_since
        grace_hours = getattr(settings, 'LICENSE_SETTINGS', {}).get('GRACE_PERIOD_HOURS', 48)
        if hours_since > grace_hours:
            license_obj.status = 'suspended'
            license_obj.save(update_fields=['status'])
    LicenseAuditLog.objects.create(event='verification_failed', payload=payload, signature_valid=None)
    return False


def verify_license_online(license_obj) -> bool:
    """Heartbeat a license and map every documented Marketplace status explicitly."""
    from .models import LicenseAuditLog

    try:
        raw_key = signing.loads(license_obj.license_key, salt='roc-license-v1')
    except signing.BadSignature:
        LicenseAuditLog.objects.create(
            event='fraud_attempt', payload={'reason': 'license_key_bad_signature'}, signature_valid=False,
        )
        return False
    if not license_obj.activation_token:
        LicenseAuditLog.objects.create(
            event='verification_failed', payload={'reason': 'activation_token_missing'}, signature_valid=False,
        )
        return False

    fingerprint = generate_fingerprint()
    try:
        response = requests.post(
            f"{_marketplace_url()}/v1/validate",
            json={
                'license_key': raw_key,
                'fingerprint': fingerprint,
                'token': license_obj.activation_token,
            },
            headers=_activation_headers(),
            timeout=10,
        )
    except requests.RequestException:
        return _marketplace_unavailable(license_obj, 'marketplace_unreachable')

    if response.status_code != 200:
        return _marketplace_unavailable(
            license_obj, 'marketplace_http_error', status_code=response.status_code,
        )
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        logger.error('[License] Invalid JSON from marketplace (%s)', response.status_code)
        return _marketplace_unavailable(license_obj, 'marketplace_invalid_json')
    if not isinstance(data, dict):
        return _marketplace_unavailable(license_obj, 'marketplace_invalid_payload')
    remote_status = data.get('status')
    if remote_status == 'rate_limited':
        # A rate limit is transient: retain the last valid entitlement and retry later.
        LicenseAuditLog.objects.create(event='verification_failed', payload=data, signature_valid=None)
        return False
    if remote_status == 'expired':
        # Per the Marketplace contract this means the activation token expired. The
        # same fingerprint is idempotent at /activate and does not consume a new seat.
        result = activate_license_with_marketplace(raw_key)
        if result.get('success'):
            return True
        license_obj.status = 'unlicensed'
    elif remote_status in ('suspended', 'revoked'):
        license_obj.status = 'suspended'
    elif remote_status not in ('active', 'grace'):
        # invalid, deactivated, or an unknown business status
        license_obj.status = 'unlicensed'

    if remote_status not in ('active', 'grace'):
        # Marketplace error states do not carry an entitlement/signature. They
        # are a normal part of the protocol and must not be reported as a
        # forged signed response.
        license_obj.save(update_fields=['status'])
        LicenseAuditLog.objects.create(event='verification_failed', payload=data, signature_valid=None)
        return False

    try:
        data = _trusted_marketplace_response(data, fingerprint)
    except EntitlementSignatureError as exc:
        license_obj.status = 'suspended'
        license_obj.save(update_fields=['status'])
        LicenseAuditLog.objects.create(
            event='fraud_attempt',
            payload={'reason': 'invalid_signed_entitlement', 'detail': str(exc)},
            signature_valid=False,
        )
        return False

    _sync_entitlements(license_obj, data, remote_status)
    LicenseAuditLog.objects.create(event='verification_ok', payload=data, signature_valid=True)
    return True


def activate_license_with_marketplace(license_key_raw: str) -> dict:
    """Activate this installation through the documented Marketplace API."""
    from .models import LicenseAuditLog, LicenseRecord

    cfg = getattr(settings, 'LICENSE_SETTINGS', {})
    marketplace_url = _marketplace_url()
    fingerprint = generate_fingerprint()
    try:
        response = requests.post(
            f"{marketplace_url}/v1/activate",
            json={
                'license_key': license_key_raw,
                'fingerprint': fingerprint,
                'machine_name': cfg.get('PRODUCT_ID', 'roc-support-desk'),
            },
            headers=_activation_headers(),
            timeout=10,
        )
    except requests.RequestException as exc:
        return {'success': False, 'status': 'transport_error', 'error': f'Could not reach marketplace: {exc}'}

    if response.status_code != 200:
        return {
            'success': False,
            'status': 'transport_error',
            'error': f'Marketplace returned an error (HTTP {response.status_code}).',
        }
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        return {'success': False, 'status': 'invalid_response', 'error': 'Marketplace returned invalid JSON.'}
    if not isinstance(data, dict):
        return {'success': False, 'status': 'invalid_response', 'error': 'Marketplace returned an invalid payload.'}
    remote_status = data.get('status')
    if remote_status not in ('active', 'grace'):
        reason = data.get('message', remote_status or 'invalid_key')
        LicenseAuditLog.objects.create(
            event='fraud_attempt',
            payload={'reason': reason, 'key_provided': license_key_raw[:8] + '...'},
            signature_valid=False,
        )
        return {'success': False, 'status': remote_status or 'invalid', 'error': f'License key rejected: {reason}'}

    try:
        data = _trusted_marketplace_response(data, fingerprint)
    except EntitlementSignatureError as exc:
        LicenseAuditLog.objects.create(
            event='fraud_attempt',
            payload={'reason': 'invalid_signed_entitlement', 'detail': str(exc)},
            signature_valid=False,
        )
        return {'success': False, 'status': 'invalid_signature', 'error': 'Marketplace entitlement signature is invalid.'}

    record = LicenseRecord.get_current()
    record.license_key = signing.dumps(license_key_raw, salt='roc-license-v1')
    record.issued_at = timezone.now()
    record.marketplace_endpoint = marketplace_url
    _sync_entitlements(record, data, remote_status)
    LicenseAuditLog.objects.create(event='activated', payload=data, signature_valid=True)
    return {'success': True, **data}
