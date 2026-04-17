"""
licensing/middleware.py
========================
Phase 3 — License enforcement middleware.

LicenseGateMiddleware : Runs on every request. Routes based on effective license status.
TrialTimerMiddleware  : Tracks daily trial usage. Blocks when timer is exhausted.
"""
import logging
import time
from datetime import date

from urllib.parse import urlparse
from django.db.models import F
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import resolve, reverse
from django.core.management import call_command
from django.core.cache import cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths that bypass all license checks
# ---------------------------------------------------------------------------
LICENSE_EXEMPT_PREFIXES = (
    '/license/',      # All license management pages
    '/auth/',         # Login, logout, password reset
    '/admin/',        # Django admin (setup before license)
    '/static/',       # Static assets
    '/media/',        # Uploaded media
    '/api/gateways/', # Evolution API / WhatsApp webhook
    '/s/',            # Short link redirects (public)
    '/favicon',       # Browser favicon
    '/docs/',         # Public portal user documentation (no login required)
)

# Status groups for routing decisions
FULL_ACCESS_STATUSES  = ('active', 'grace')
BASIC_ACCESS_STATUSES = ('trial', 'partial_lock')
BLOCKED_STATUSES      = ('expired', 'suspended', 'unlicensed')

# Features disabled during partial_lock (grace period expired)
PARTIAL_LOCK_DISABLED_FEATURES = {
    'analytics', 'sla_reports', 'audit_export',
    'api_access', 'whatsapp', 'email_settings', 'form_builder',
}


# ---------------------------------------------------------------------------
# LicenseGateMiddleware
# ---------------------------------------------------------------------------

class LicenseGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def _handle_redirect(self, request, view_name):
        """
        Helper to handle redirects that don't break HTMX partials.
        If HTMX request, send HX-Redirect header. Else, standard redirect.
        """
        url = reverse(view_name)
        if request.headers.get('HX-Request'):
            response = HttpResponse()
            response['HX-Redirect'] = url
            return response
        return redirect(url)

    def __call__(self, request):
        # Attach license info first so it's available even on exempt paths
        try:
            from .models import LicenseRecord
            license_obj = LicenseRecord.get_current()
            status      = license_obj.get_effective_status()
            
            request.license        = license_obj
            request.license_status = status
        except Exception as exc:
            logger.exception(f"[LicenseGate] Error fetching license: {exc}")
            return self.get_response(request)

        # Skip routing enforcement for exempt paths
        if self._is_exempt(request.path):
            return self.get_response(request)

        # Skip if HTMX request originated from an exempt page (prevents loops)
        current_url = request.headers.get('HX-Current-URL')
        if current_url:
            try:
                if self._is_exempt(urlparse(current_url).path):
                    return self.get_response(request)
            except Exception:
                pass

        # --- Route by status ---
        if status in FULL_ACCESS_STATUSES:
            # Full access — grace shows a banner (handled in template context)
            response = self.get_response(request)
            return response

        elif status in BASIC_ACCESS_STATUSES:
            # Trial or partial_lock — allow through; TrialTimerMiddleware handles trial timing
            # Partial_lock feature blocking is done by @feature_required decorator (Phase 4)
            
            # --- Auto-Enforce Disconnection for Partial Lock ---
            if status == 'partial_lock':
                enforced_key = f"license_enforced_{license_obj.id}_{status}"
                if not cache.get(enforced_key):
                    try:
                        call_command('enforce_license')
                        cache.set(enforced_key, True, 21600) # 6 hours
                    except Exception as e:
                        logger.error(f"[LicenseGate] Auto-enforcement failed: {e}")

            response = self.get_response(request)
            return response

        elif status == 'expired':
            # --- Auto-Enforce for Expired ---
            enforced_key = f"license_enforced_{license_obj.id}_expired"
            if not cache.get(enforced_key):
                try:
                    call_command('enforce_license')
                    cache.set(enforced_key, True, 21600)
                except Exception as e:
                    logger.error(f"[LicenseGate] Auto-enforcement failed: {e}")
            
            return self._handle_redirect(request, 'licensing:expired')

        elif status == 'suspended':
            return self._handle_redirect(request, 'licensing:suspended')

        else:
            # 'unlicensed' or unknown → prompt activation
            return self._handle_redirect(request, 'licensing:activate')

    @staticmethod
    def _is_exempt(path: str) -> bool:
        return any(path.startswith(prefix) for prefix in LICENSE_EXEMPT_PREFIXES)


# ---------------------------------------------------------------------------
# TrialTimerMiddleware
# ---------------------------------------------------------------------------

class TrialTimerMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Only apply to trial (or unlicensed) status
        status = getattr(request, 'license_status', None)
        if status not in ('trial', 'unlicensed'):
            return self.get_response(request)

        # Also skip exempt paths
        if LicenseGateMiddleware._is_exempt(request.path):
            return self.get_response(request)

        # Skip if HTMX request originated from an exempt page (prevents loops)
        current_url = request.headers.get('HX-Current-URL')
        if current_url:
            try:
                if LicenseGateMiddleware._is_exempt(urlparse(current_url).path):
                    return self.get_response(request)
            except Exception:
                pass

        from django.conf import settings
        from .models import TrialRecord

        cfg         = getattr(settings, 'LICENSE_SETTINGS', {})
        max_days    = cfg.get('TRIAL_MAX_DAYS', 1)
        duration_s  = cfg.get('TRIAL_DURATION_SECONDS', 900)
        today       = date.today()

        # ------------------------------------------------------------------
        # 1. Check if today would exceed max trial days
        # ------------------------------------------------------------------
        today_trial, created = TrialRecord.objects.get_or_create(trial_date=today)

        if created:
            total_trial_days = TrialRecord.objects.count()  # includes today
            if total_trial_days > max_days:
                # This new day exceeds the allowed trial period
                today_trial.delete()
                logger.info("[Trial] Max trial days exceeded — redirecting to upgrade")
                return LicenseGateMiddleware(self.get_response)._handle_redirect(request, 'licensing:upgrade')

        # ------------------------------------------------------------------
        # 2. Check if today's quota is already exhausted
        # ------------------------------------------------------------------
        seconds_left = max(0, duration_s - today_trial.total_seconds_used)

        if seconds_left <= 0:
            logger.info("[Trial] Today's trial quota exhausted")
            request._trial_blocked = True
            return render(request, 'licensing/trial_ended_today.html', {
                'license':   getattr(request, 'license', None),
                'max_days':  max_days,
                'used_days': TrialRecord.objects.count(),
            }, status=403)

        # ------------------------------------------------------------------
        # 3. Allow request, track elapsed time
        # ------------------------------------------------------------------
        request._trial_seconds_left = seconds_left
        request._trial_start        = time.monotonic()

        response = self.get_response(request)

        # ------------------------------------------------------------------
        # 4. After response: record how long this request took
        # ------------------------------------------------------------------
        elapsed = max(1, int(time.monotonic() - request._trial_start))
        TrialRecord.objects.filter(trial_date=today).update(
            total_seconds_used=F('total_seconds_used') + elapsed
        )

        return response
