"""
licensing/context_processors.py
================================
Injects license status and trial info into every template context.
This makes {{ license_status }}, {{ trial_seconds_left }}, etc.
available in all templates without needing explicit view logic.
"""
import logging

logger = logging.getLogger(__name__)


def license_context(request):
    """
    Context processor: inject license info into all template contexts.

    Available template variables:
    - license            : LicenseRecord instance
    - license_status     : effective status string ('active', 'trial', 'grace', etc.)
    - trial_seconds_left : seconds remaining in today's trial (0 if not in trial)
    """
    try:
        from .models import LicenseRecord

        # Prefer pre-computed values set by middleware (faster — saves a DB query)
        license_obj    = getattr(request, 'license', None)
        license_status = getattr(request, 'license_status', None)

        if license_obj is None:
            license_obj    = LicenseRecord.get_current()
            license_status = license_obj.get_effective_status()

        from .middleware import LicenseGateMiddleware
        is_exempt = LicenseGateMiddleware._is_exempt(request.path)
        trial_blocked      = getattr(request, '_trial_blocked', False)
        trial_seconds_left = getattr(request, '_trial_seconds_left', None)

        # If timing was skipped (exempt path), fetch from DB for the header countdown
        if trial_seconds_left is None and license_status == 'trial':
            try:
                from .models import TrialRecord
                from django.conf import settings
                from datetime import date
                cfg = getattr(settings, 'LICENSE_SETTINGS', {})
                duration_s = cfg.get('TRIAL_DURATION_SECONDS', 900)
                today_trial = TrialRecord.objects.filter(trial_date=date.today()).first()
                if today_trial:
                    trial_seconds_left = max(0, duration_s - today_trial.total_seconds_used)
                else:
                    trial_seconds_left = duration_s
            except:
                trial_seconds_left = 0
        
        trial_seconds_left = trial_seconds_left or 0

        return {
            'license':            license_obj,
            'license_status':     license_status,
            'trial_seconds_left': trial_seconds_left,
            'is_license_exempt':  is_exempt,
            'trial_blocked':      trial_blocked,
        }
    except Exception as exc:
        logger.debug(f"[LicenseContext] Could not load license context: {exc}")
        return {
            'license':            None,
            'license_status':     'unlicensed',
            'trial_seconds_left': 0,
        }
