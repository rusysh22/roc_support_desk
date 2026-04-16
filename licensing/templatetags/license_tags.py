"""
licensing/templatetags/license_tags.py
========================================
Template tags and filters for license-gated rendering.

Usage:
    {% load license_tags %}

    {% has_feature 'whatsapp' as wa_enabled %}
    {% if wa_enabled %}...{% endif %}

    {% get_license_status as lic_status %}
    {% if lic_status == 'grace' %}...{% endif %}
"""
from django import template

register = template.Library()


# ---------------------------------------------------------------------------
# {% has_feature 'feature_name' %}
# ---------------------------------------------------------------------------

@register.simple_tag(takes_context=True)
def has_feature(context, feature_name: str) -> bool:
    """
    Return True if the named feature is enabled in the current license.

    Example:
        {% has_feature 'whatsapp' as wa_on %}
        {% if wa_on %}<a href="...">WhatsApp</a>{% endif %}
    """
    license_obj = context.get('license')
    if license_obj is None:
        try:
            from licensing.models import LicenseRecord
            license_obj = LicenseRecord.get_current()
        except Exception:
            return False

    # During trial/partial_lock, premium features are always off
    license_status = context.get('license_status', 'unlicensed')
    if license_status in ('trial', 'unlicensed'):
        return False

    from licensing.middleware import PARTIAL_LOCK_DISABLED_FEATURES
    if license_status == 'partial_lock' and feature_name in PARTIAL_LOCK_DISABLED_FEATURES:
        return False

    return bool(license_obj.features_json.get(feature_name, False))


# ---------------------------------------------------------------------------
# {% get_license_status %}
# ---------------------------------------------------------------------------

@register.simple_tag(takes_context=True)
def get_license_status(context) -> str:
    """
    Return the effective license status string.

    Example:
        {% get_license_status as lic_status %}
        {% if lic_status == 'grace' %}...{% endif %}
    """
    status = context.get('license_status')
    if status:
        return status

    try:
        from licensing.models import LicenseRecord
        return LicenseRecord.get_current().get_effective_status()
    except Exception:
        return 'unlicensed'


# ---------------------------------------------------------------------------
# {% trial_seconds_left %}
# ---------------------------------------------------------------------------

@register.simple_tag(takes_context=True)
def trial_seconds_left(context) -> int:
    """
    Return seconds remaining in today's trial quota.
    Returns 0 if not in trial mode or quota exhausted.

    Example:
        {% trial_seconds_left as secs %}
        <span id="trial-countdown" data-seconds="{{ secs }}"></span>
    """
    # First try to get from context (injected by context processor)
    val = context.get('trial_seconds_left', None)
    if val is not None:
        return max(0, int(val))

    # Fallback: query DB directly
    try:
        from datetime import date
        from django.conf import settings
        from licensing.models import TrialRecord

        cfg = getattr(settings, 'LICENSE_SETTINGS', {})
        duration_s  = cfg.get('TRIAL_DURATION_SECONDS', 900)
        today_trial = TrialRecord.objects.filter(trial_date=date.today()).first()
        used        = today_trial.total_seconds_used if today_trial else 0
        return max(0, duration_s - used)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Filter: feature name → human-readable label
# ---------------------------------------------------------------------------

_FEATURE_LABELS = {
    'whatsapp':       'WhatsApp Gateway',
    'email_settings': 'Email Settings',
    'form_builder':   'Form Builder',
    'kb_manage':      'Knowledge Base Management',
    'short_links':    'Short Links & QR Code',
    'analytics':      'Analytics Dashboard',
    'company_units':  'Company Units',
    'sla_reports':    'SLA Reports',
    'audit_export':   'Audit Log Export',
    'api_access':     'API Access',
}


@register.filter
def feature_label(feature_key: str) -> str:
    """Convert a feature key to its human-readable label."""
    return _FEATURE_LABELS.get(feature_key, feature_key.replace('_', ' ').title())
