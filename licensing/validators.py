"""
licensing/validators.py
========================
Core cryptographic and verification utilities.

Layer 1: HMAC Webhook Signature verification
Layer 2: Installation Fingerprint (domain binding)
Layer 5: Periodic Online Verification
"""
import hashlib
import hmac
import json
import uuid
from typing import Optional

import requests
from django.conf import settings
from django.core import signing
from django.utils import timezone
from django.utils.dateparse import parse_datetime


# ---------------------------------------------------------------------------
# Default feature flags per plan tier
# Used when marketplace doesn't explicitly send features_json
# ---------------------------------------------------------------------------
TIER_DEFAULT_FEATURES: dict[str, dict] = {
    'trial': {
        'whatsapp':       False,
        'email_settings': False,
        'form_builder':   False,
        'kb_manage':      False,
        'short_links':    False,
        'analytics':      False,
        'company_units':  False,
        'sla_reports':    False,
        'audit_export':   False,
        'api_access':     False,
    },
    'starter': {
        'whatsapp':       True,
        'email_settings': True,
        'form_builder':   True,
        'kb_manage':      True,
        'short_links':    True,
        'analytics':      False,
        'company_units':  False,
        'sla_reports':    False,
        'audit_export':   False,
        'api_access':     False,
    },
    'professional': {
        'whatsapp':       True,
        'email_settings': True,
        'form_builder':   True,
        'kb_manage':      True,
        'short_links':    True,
        'analytics':      True,
        'company_units':  True,
        'sla_reports':    True,
        'audit_export':   True,
        'api_access':     False,
    },
    'business': {
        'whatsapp':       True,
        'email_settings': True,
        'form_builder':   True,
        'kb_manage':      True,
        'short_links':    True,
        'analytics':      True,
        'company_units':  True,
        'sla_reports':    True,
        'audit_export':   True,
        'api_access':     True,
    },
    'enterprise': {
        'whatsapp':       True,
        'email_settings': True,
        'form_builder':   True,
        'kb_manage':      True,
        'short_links':    True,
        'analytics':      True,
        'company_units':  True,
        'sla_reports':    True,
        'audit_export':   True,
        'api_access':     True,
    },
}


# ---------------------------------------------------------------------------
# Layer 1 — HMAC Webhook Signature
# ---------------------------------------------------------------------------

def verify_webhook_signature(request_body: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify HMAC-SHA256 signature from the marketplace webhook.

    The marketplace signs the raw request body using the shared WEBHOOK_SECRET
    and sends: X-Webhook-Signature: sha256=<hex_digest>

    Uses hmac.compare_digest to prevent timing attacks.
    """
    if not signature_header or not signature_header.startswith('sha256='):
        return False
    if not secret:
        return False

    received = signature_header[7:]  # strip "sha256=" prefix
    expected = hmac.new(
        secret.encode('utf-8'),
        request_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(received, expected)


# ---------------------------------------------------------------------------
# Layer 2 — Installation Fingerprint
# ---------------------------------------------------------------------------

def generate_fingerprint() -> str:
    """
    Generate a unique fingerprint for this installation.

    Composed of: hostname + INSTALL_KEY + product_id
    This fingerprint is sent to the marketplace to bind a license key to
    a specific server/domain. Changing the domain or INSTALL_KEY will
    invalidate the fingerprint.
    """
    cfg = getattr(settings, 'LICENSE_SETTINGS', {})

    # Try to get the current site domain, fall back to ALLOWED_HOSTS
    try:
        from django.contrib.sites.models import Site
        hostname = Site.objects.get_current().domain
    except Exception:
        allowed = getattr(settings, 'ALLOWED_HOSTS', ['localhost'])
        hostname = allowed[0] if allowed else 'localhost'

    install_key = getattr(settings, 'INSTALL_KEY', '') or settings.SECRET_KEY[:12]
    raw = f"{hostname}:{install_key}:{cfg.get('PRODUCT_ID', 'roc-support-desk')}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Layer 5 — Periodic Online Verification
# ---------------------------------------------------------------------------

def verify_license_online(license_obj) -> bool:
    """
    Re-verify the license against the marketplace API.

    Returns True if valid (and updates local record).
    Returns False if invalid (suspends local record) or if marketplace
    is unreachable beyond the grace period.

    Called by:
    - manage.py verify_license (cron / manual)
    - LicenseGateMiddleware every N hours (Phase 3)
    """
    from .models import LicenseAuditLog

    cfg = getattr(settings, 'LICENSE_SETTINGS', {})
    marketplace_url = cfg.get('MARKETPLACE_URL', 'https://tokowebjaya.com')

    # Decode the stored signed key
    try:
        raw_key = signing.loads(license_obj.license_key, salt='roc-license-v1')
    except signing.BadSignature:
        LicenseAuditLog.objects.create(
            event='fraud_attempt',
            payload={'reason': 'license_key_bad_signature'},
            signature_valid=False,
        )
        return False

    try:
        resp = requests.get(
            f"{marketplace_url}/api/v1/validate-token",
            params={
                'token':      raw_key,
                'fingerprint': generate_fingerprint(),
                'product_id': cfg.get('PRODUCT_ID', 'roc-support-desk'),
            },
            timeout=10,
        )
        
        # Check if we got a success code before trying to parse JSON
        if resp.status_code != 200:
            LicenseAuditLog.objects.create(
                event='verification_failed',
                payload={'reason': 'marketplace_http_error', 'status_code': resp.status_code},
                signature_valid=False,
            )
            return False

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            snippet = resp.text[:100] if resp.text else "Empty response"
            logger.error(f"[License] Invalid JSON from marketplace ({resp.status_code}): {snippet}")
            return False

        if data.get('valid'):
            # Sync marketplace data to local record
            if data.get('expires_at'):
                license_obj.expires_at = parse_datetime(data['expires_at'])
            
            # Map plan/tier from new licese_type or old plan field
            plan = data.get('license_type') or data.get('plan')
            if plan:
                license_obj.plan_tier = plan
                # Update features based on plan if not explicitly provided
                if 'features' not in data:
                    license_obj.features_json = TIER_DEFAULT_FEATURES.get(plan, {})
            if data.get('max_agents'):
                license_obj.max_agents = data['max_agents']

            license_obj.last_verified_at = timezone.now()
            license_obj.save()

            LicenseAuditLog.objects.create(
                event='verification_ok',
                payload=data,
                signature_valid=True,
            )
            return True
        else:
            # Marketplace says invalid
            license_obj.status = 'suspended'
            license_obj.save()
            LicenseAuditLog.objects.create(
                event='verification_failed',
                payload=data,
                signature_valid=False,
            )
            return False

    except requests.RequestException:
        # Marketplace unreachable — apply grace period
        if license_obj.last_verified_at:
            hours_since = (timezone.now() - license_obj.last_verified_at).total_seconds() / 3600
            grace_hours = cfg.get('GRACE_PERIOD_HOURS', 48)
            if hours_since > grace_hours:
                license_obj.status = 'suspended'
                license_obj.save()
                LicenseAuditLog.objects.create(
                    event='verification_failed',
                    payload={'reason': 'marketplace_unreachable', 'hours_since': hours_since},
                )
        return False


# ---------------------------------------------------------------------------
# Manual activation — verify key with marketplace before activating
# ---------------------------------------------------------------------------

def activate_license_with_marketplace(license_key_raw: str) -> dict:
    """
    Validate and activate a license key provided manually.

    Returns:
        {'success': True, 'plan': '...', 'expires_at': '...', ...}
        {'success': False, 'error': 'Reason string'}
    """
    from .models import LicenseAuditLog, LicenseRecord

    cfg = getattr(settings, 'LICENSE_SETTINGS', {})
    marketplace_url = cfg.get('MARKETPLACE_URL', 'https://tokowebjaya.com')
    fingerprint = generate_fingerprint()

    try:
        resp = requests.get(
            f"{marketplace_url}/api/v1/validate-token",
            params={
                'token':      license_key_raw,
                'fingerprint': fingerprint,
                'product_id': cfg.get('PRODUCT_ID', 'roc-support-desk'),
            },
            timeout=10,
        )
        
        # Check HTTP status code
        if resp.status_code != 200:
            return {
                'success': False, 
                'error': f"Marketplace returned an error (HTTP {resp.status_code}). Please verify your MARKETPLACE_URL or try again later."
            }

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            # Provide debug context if it's an HTML error page instead of JSON
            content_type = resp.headers.get('Content-Type', 'unknown')
            snippet = resp.text[:100] if resp.text else "Empty response"
            return {
                'success': False,
                'error': f"Invalid response format from marketplace. Expected JSON but got {content_type}. (Preview: {snippet}...)"
            }

        if not data.get('valid'):
            reason = data.get('reason', 'invalid_key')
            LicenseAuditLog.objects.create(
                event='fraud_attempt',
                payload={'reason': reason, 'key_provided': license_key_raw[:8] + '...'},
                signature_valid=False,
            )
            return {'success': False, 'error': f"License key rejected by marketplace: {reason}"}

        # Key is valid — store and activate
        signed_key = signing.dumps(license_key_raw, salt='roc-license-v1')
        
        # Mapping: API v1 uses 'license_type', legacy used 'plan'
        plan = data.get('license_type') or data.get('plan', 'starter')
        features = data.get('features', TIER_DEFAULT_FEATURES.get(plan, {}))
        issued_to = data.get('user_id') or data.get('issued_to', '')

        record = LicenseRecord.get_current()
        record.license_key          = signed_key
        record.issued_to            = issued_to
        record.plan_tier            = plan
        record.status               = 'active'
        record.expires_at           = parse_datetime(data['expires_at']) if data.get('expires_at') else None
        record.issued_at            = parse_datetime(data['issued_at']) if data.get('issued_at') else timezone.now()
        record.max_agents           = data.get('max_agents', 5)
        record.features_json        = features
        record.install_fingerprint  = fingerprint
        record.marketplace_endpoint = marketplace_url
        record.last_verified_at     = timezone.now()
        record.save()

        LicenseAuditLog.objects.create(
            event='activated',
            payload=data,
            signature_valid=True,
        )

        return {'success': True, **data}

    except requests.RequestException as exc:
        return {
            'success': False,
            'error': f"Could not reach marketplace: {exc}. Please try again later.",
        }
