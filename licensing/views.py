"""
licensing/views.py
==================
Phase 2 views:
- webhook_receiver  : POST /license/webhook/  — receives marketplace events
- activate_license  : GET/POST /license/activate/ — manual key activation
- license_status    : GET /license/status/ — SuperAdmin dashboard
- license_expired   : GET /license/expired/
- license_suspended : GET /license/suspended/
- license_upgrade   : GET /license/upgrade/
"""
import json
import logging

from ipware import get_client_ip as _ipware_get_client_ip
from django.conf import settings
from django.contrib import messages
from django.core.management import call_command
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core import signing
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import LicenseAuditLog, LicenseRecord, TrialRecord
from .validators import (
    TIER_DEFAULT_FEATURES,
    activate_license_with_marketplace,
    generate_fingerprint,
    verify_webhook_signature,
)
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)


def _get_client_ip(request) -> str:
    ip, _ = _ipware_get_client_ip(request)
    return ip or ""


def _is_superadmin(user) -> bool:
    return user.is_authenticated and (
        user.is_superuser or getattr(user, 'role_access', '') == 'SuperAdmin'
    )


# ---------------------------------------------------------------------------
# Webhook Receiver — POST /license/webhook/
# ---------------------------------------------------------------------------

@csrf_exempt
@require_POST
def webhook_receiver(request):
    """
    Receive and process license lifecycle events from the marketplace.

    Security: HMAC-SHA256 signature verified before ANY processing.
    All attempts (valid or not) are logged to LicenseAuditLog.
    """
    cfg = getattr(settings, 'LICENSE_SETTINGS', {})
    secret = cfg.get('WEBHOOK_SECRET', '')
    source_ip = _get_client_ip(request)

    # --- Step 1: Verify HMAC signature ---
    sig_header = request.headers.get('X-Webhook-Signature', '')
    raw_body = request.body

    if not verify_webhook_signature(raw_body, sig_header, secret):
        LicenseAuditLog.objects.create(
            event='fraud_attempt',
            payload={
                'reason': 'invalid_hmac_signature',
                'signature_header': sig_header[:60] if sig_header else 'missing',
            },
            source_ip=source_ip,
            signature_valid=False,
        )
        logger.warning(f"[License] Invalid webhook signature from {source_ip}")
        return JsonResponse({'error': 'Forbidden'}, status=403)

    # --- Step 2: Parse JSON body ---
    try:
        data = json.loads(raw_body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    # --- Step 3: Validate product_id ---
    expected_product = cfg.get('PRODUCT_ID', 'roc-support-desk')
    if data.get('product_id') != expected_product:
        LicenseAuditLog.objects.create(
            event='fraud_attempt',
            payload={'reason': 'product_id_mismatch', 'received': data.get('product_id')},
            source_ip=source_ip,
            signature_valid=True,
        )
        return JsonResponse({'error': 'Product ID mismatch'}, status=400)

    # --- Step 4: Log webhook receipt ---
    LicenseAuditLog.objects.create(
        event='webhook_received',
        payload=data,
        source_ip=source_ip,
        signature_valid=True,
    )

    # --- Step 5: Process event ---
    event = data.get('event', '')
    license_record = LicenseRecord.get_current()
    fingerprint = generate_fingerprint()

    try:
        if event == 'license.created':
            _process_license_created(license_record, data, fingerprint)

        elif event == 'license.renewed':
            _process_license_renewed(license_record, data)

        elif event in ('license.expired', 'license.cancelled'):
            license_record.status = 'expired'
            license_record.save()
            LicenseAuditLog.objects.create(
                event='expired',
                payload=data,
                source_ip=source_ip,
                signature_valid=True,
            )
            # Trigger enforcement (disconnect apps)
            try:
                call_command('enforce_license')
            except Exception as e:
                logger.error(f"[License] Enforcement failed during expiry webhook: {e}")

        elif event == 'license.suspended':
            license_record.status = 'suspended'
            license_record.save()
            LicenseAuditLog.objects.create(
                event='suspended',
                payload=data,
                source_ip=source_ip,
                signature_valid=True,
            )
            # Trigger enforcement (disconnect apps)
            try:
                call_command('enforce_license')
            except Exception as e:
                logger.error(f"[License] Enforcement failed during suspension webhook: {e}")

        elif event == 'license.upgraded':
            _process_license_upgraded(license_record, data)

        else:
            logger.info(f"[License] Unknown webhook event: {event}")

    except Exception as exc:
        logger.exception(f"[License] Error processing webhook event '{event}': {exc}")
        return JsonResponse({'error': 'Internal processing error'}, status=500)

    return JsonResponse({'status': 'ok'})


def _process_license_created(record, data: dict, fingerprint: str):
    """Handle license.created webhook: set record to active."""
    plan = data.get('plan', 'starter')
    signed_key = signing.dumps(data.get('license_key', ''), salt='roc-license-v1')

    record.license_key          = signed_key
    record.issued_to            = data.get('issued_to', '')
    record.plan_tier            = plan
    record.status               = 'active'
    record.max_agents           = data.get('max_agents', 5)
    record.features_json        = data.get('features', TIER_DEFAULT_FEATURES.get(plan, {}))
    record.install_fingerprint  = fingerprint
    record.marketplace_endpoint = getattr(settings, 'LICENSE_SETTINGS', {}).get('MARKETPLACE_URL', '')
    record.last_verified_at     = timezone.now()

    if data.get('issued_at'):
        record.issued_at = parse_datetime(data['issued_at'])
    if data.get('expires_at'):
        record.expires_at = parse_datetime(data['expires_at'])

    record.save()
    LicenseAuditLog.objects.create(event='activated', payload=data, signature_valid=True)
    logger.info(f"[License] Activated — plan={plan}, issued_to={record.issued_to}")


def _process_license_renewed(record, data: dict):
    """Handle license.renewed: extend expires_at, ensure status=active."""
    if data.get('expires_at'):
        record.expires_at = parse_datetime(data['expires_at'])
    record.status = 'active'
    record.last_verified_at = timezone.now()
    record.save()
    LicenseAuditLog.objects.create(event='activated', payload=data, signature_valid=True)
    logger.info(f"[License] Renewed — new expires_at={record.expires_at}")


def _process_license_upgraded(record, data: dict):
    """Handle license.upgraded: update tier, features, and agent limit."""
    plan = data.get('plan', record.plan_tier)
    record.plan_tier      = plan
    record.max_agents     = data.get('max_agents', record.max_agents)
    record.features_json  = data.get('features', TIER_DEFAULT_FEATURES.get(plan, record.features_json))
    record.save()
    LicenseAuditLog.objects.create(event='activated', payload=data, signature_valid=True)
    logger.info(f"[License] Upgraded — new plan={plan}")


# ---------------------------------------------------------------------------
# Manual Activation — GET+POST /license/activate/
# ---------------------------------------------------------------------------

def activate_license(request):
    """
    Fallback activation page when the webhook was not received.

    SuperAdmin enters the license key manually. The app verifies it
    with the marketplace before activating.
    """
    if not _is_superadmin(request.user):
        return redirect('login')

    if request.method == 'POST':
        key = request.POST.get('license_key', '').strip()
        if not key:
            messages.error(request, "Please enter a license key.")
            return render(request, 'licensing/activate.html', {'license': LicenseRecord.get_current()})

        result = activate_license_with_marketplace(key)

        if result['success']:
            messages.success(
                request,
                f"License activated successfully! Plan: {result.get('plan', '').title()}."
            )
            return redirect('licensing:status')
        else:
            messages.error(request, result['error'])

    return render(request, 'licensing/activate.html', {
        'license': LicenseRecord.get_current(),
    })


# ---------------------------------------------------------------------------
# Start Trial — POST /license/start-trial/
# ---------------------------------------------------------------------------

@require_POST
def start_trial(request):
    """
    Resets the license record to trial mode.
    Only available when the system is unlicensed or already in trial.
    SuperAdmin-only.
    """
    if not _is_superadmin(request.user):
        return redirect('login')

    record = LicenseRecord.get_current()
    allowed_statuses = ('unlicensed', 'trial', 'expired')

    effective = record.get_effective_status()
    if effective not in allowed_statuses:
        messages.error(
            request,
            f"Cannot start trial: current status is '{effective}'. "
            "Trial can only be started when the system is unlicensed or expired."
        )
        return redirect('licensing:activate')

    # Reset to trial
    record.license_key      = ''
    record.status           = 'trial'
    record.plan_tier        = 'trial'
    record.issued_to        = ''
    record.expires_at       = None
    record.features_json    = {}
    record.last_verified_at = None
    record.save()

    # Reset trial usage counters so user gets fresh quota
    TrialRecord.objects.all().delete()

    LicenseAuditLog.objects.create(
        event='trial_started',
        payload={'started_by': request.user.username},
        signature_valid=True,
    )

    messages.success(
        request,
        "✅ Trial mode activated! You have a limited daily quota to explore the system."
    )
    return redirect('desk:case_list')


# ---------------------------------------------------------------------------
# Deactivate License — POST /license/deactivate/
# ---------------------------------------------------------------------------

@require_POST
def deactivate_license(request):
    """
    Clears the stored license key and resets the license record to 'unlicensed'.
    SuperAdmin-only, requires confirmation via POST.
    """
    if not _is_superadmin(request.user):
        return redirect('login')

    confirm = request.POST.get('confirm', '').strip()
    if confirm != 'DEACTIVATE':
        messages.error(request, "Invalid confirmation. Type DEACTIVATE to continue.")
        return redirect('licensing:status')

    record = LicenseRecord.get_current()
    record.license_key         = ''
    record.status              = 'unlicensed'
    record.plan_tier           = ''
    record.issued_to           = ''
    record.expires_at          = None
    record.features_json       = {}
    record.last_verified_at    = None
    record.save()

    LicenseAuditLog.objects.create(
        event='deactivated',
        payload={'deactivated_by': request.user.username},
        signature_valid=True,
    )

    messages.success(request, "License deactivated successfully. The system is now in unlicensed mode.")
    return redirect('licensing:activate')


# ---------------------------------------------------------------------------
# License Status Dashboard — GET /license/status/
# ---------------------------------------------------------------------------

@login_required
def license_status(request):
    """SuperAdmin-only license status and audit log dashboard."""
    if not _is_superadmin(request.user):
        messages.error(request, "Access restricted to SuperAdmin.")
        return redirect('desk:case_list')

    license = LicenseRecord.get_current()
    effective_status = license.get_effective_status()
    audit_logs = LicenseAuditLog.objects.all()[:30]

    # Trial usage info
    from datetime import date
    today_trial = TrialRecord.objects.filter(trial_date=date.today()).first()
    trial_days_used = TrialRecord.objects.count()

    cfg = getattr(settings, 'LICENSE_SETTINGS', {})

    context = {
        'license':           license,
        'effective_status':  effective_status,
        'audit_logs':        audit_logs,
        'today_trial':       today_trial,
        'trial_days_used':   trial_days_used,
        'fingerprint':       generate_fingerprint(),
        'marketplace_url':   cfg.get('MARKETPLACE_URL', ''),
        'trial_max_days':    cfg.get('TRIAL_MAX_DAYS', 1),
        'trial_duration_sec': cfg.get('TRIAL_DURATION_SECONDS', 900),
    }
    return render(request, 'licensing/status.html', context)


# ---------------------------------------------------------------------------
# Expired / Suspended / Upgrade pages
# ---------------------------------------------------------------------------

def license_expired(request):
    license = LicenseRecord.get_current()
    cfg = getattr(settings, 'LICENSE_SETTINGS', {})
    return render(request, 'licensing/expired.html', {
        'license': license,
        'marketplace_url': cfg.get('MARKETPLACE_URL', ''),
    })


def license_suspended(request):
    license = LicenseRecord.get_current()
    cfg = getattr(settings, 'LICENSE_SETTINGS', {})
    return render(request, 'licensing/suspended.html', {
        'license': license,
        'marketplace_url': cfg.get('MARKETPLACE_URL', ''),
    })


def license_upgrade(request):
    license = LicenseRecord.get_current()
    cfg = getattr(settings, 'LICENSE_SETTINGS', {})
    return render(request, 'licensing/upgrade.html', {
        'license': license,
        'marketplace_url': cfg.get('MARKETPLACE_URL', ''),
        'tier_features': TIER_DEFAULT_FEATURES,
    })
