"""
licensing/models.py
=====================
Data models for the RoC Support Desk license system.

- LicenseRecord  : Singleton — holds the single active license for this installation.
- TrialRecord    : One row per calendar day of trial usage.
- LicenseAuditLog: Append-only event log; never updated or deleted.
"""
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# LicenseRecord — Singleton
# ---------------------------------------------------------------------------

class LicenseRecord(models.Model):
    PLAN_CHOICES = [
        ('trial',        'Trial'),
        ('starter',      'Starter'),
        ('professional', 'Professional'),
        ('business',     'Business'),
        ('enterprise',   'Enterprise'),
    ]
    STATUS_CHOICES = [
        ('trial',       'Trial'),
        ('active',      'Active'),
        ('grace',       'Grace Period'),
        ('partial_lock','Partial Lock'),
        ('expired',     'Expired'),
        ('suspended',   'Suspended'),
        ('unlicensed',  'Unlicensed'),
    ]

    # --- Identity ---
    license_key = models.CharField(
        max_length=500, blank=True,
        help_text="Django-signed encoded key — NOT plain text.",
    )
    issued_to = models.CharField(max_length=255, blank=True, help_text="Company name or email.")
    plan_tier = models.CharField(max_length=30, choices=PLAN_CHOICES, default='trial')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='trial')

    # --- Timestamps ---
    issued_at        = models.DateTimeField(null=True, blank=True)
    expires_at       = models.DateTimeField(
        null=True, blank=True,
        help_text="Null = perpetual license.",
    )
    last_verified_at = models.DateTimeField(
        null=True, blank=True,
        help_text="Last successful online verification timestamp.",
    )

    # --- Limits ---
    max_agents    = models.PositiveIntegerField(default=3)
    features_json = models.JSONField(
        default=dict,
        help_text=(
            'Feature flags for this installation. '
            'Example: {"whatsapp": true, "email_settings": true}'
        ),
    )

    # --- Binding ---
    install_fingerprint = models.CharField(
        max_length=64, blank=True,
        help_text="SHA-256(domain + INSTALL_KEY + product_id) — binds license to this server.",
    )
    marketplace_endpoint = models.URLField(
        blank=True,
        help_text="Marketplace base URL used for re-verification.",
    )

    class Meta:
        verbose_name = "License Record"
        verbose_name_plural = "License Record"

    def __str__(self):
        return f"License [{self.get_plan_tier_display()}] — {self.get_status_display()}"

    # -----------------------------------------------------------------------
    # Singleton accessor
    # -----------------------------------------------------------------------
    @classmethod
    def get_current(cls):
        """Return (or create) the singleton license record (pk=1)."""
        obj, _ = cls.objects.get_or_create(pk=1, defaults={
            'status': 'trial',
            'plan_tier': 'trial',
        })
        return obj

    # -----------------------------------------------------------------------
    # Effective status (anti-fraud Layer 3)
    # -----------------------------------------------------------------------
    def get_effective_status(self) -> str:
        """
        Compute the *real* license state.

        Priority: suspended > active/grace/partial_lock from expiry calc > raw status.

        This is computed from `expires_at` to prevent fraud via direct DB edit
        of the `status` field to 'active'.
        """
        from django.conf import settings

        # Suspended cannot be overridden
        if self.status == 'suspended':
            return 'suspended'

        # Still within validity period (or perpetual)
        if self.expires_at is None or self.expires_at >= timezone.now():
            return self.status  # 'active', 'trial', 'unlicensed', etc.

        # --- License has expired — calculate grace/partial_lock phases ---
        cfg = getattr(settings, 'LICENSE_SETTINGS', {})
        days_since_expiry: int = (timezone.now() - self.expires_at).days

        grace_days        = cfg.get('GRACE_DAYS', 3)
        partial_lock_days = cfg.get('PARTIAL_LOCK_DAYS', 7)

        if days_since_expiry <= grace_days:
            return 'grace'
        elif days_since_expiry <= partial_lock_days:
            return 'partial_lock'
        else:
            return 'expired'

    # -----------------------------------------------------------------------
    # Helpers for templates
    # -----------------------------------------------------------------------
    @property
    def days_since_expiry(self) -> int:
        if not self.expires_at:
            return 0
        delta = timezone.now() - self.expires_at
        return max(0, delta.days)

    @property
    def days_until_partial_lock(self) -> int:
        from django.conf import settings
        cfg = getattr(settings, 'LICENSE_SETTINGS', {})
        partial_lock_days = cfg.get('PARTIAL_LOCK_DAYS', 7)
        return max(0, partial_lock_days - self.days_since_expiry)

    def has_feature(self, feature_name: str) -> bool:
        """Check if a specific feature is enabled for this license."""
        return bool(self.features_json.get(feature_name, False))


# ---------------------------------------------------------------------------
# TrialRecord — one row per calendar day
# ---------------------------------------------------------------------------

class TrialRecord(models.Model):
    trial_date        = models.DateField(unique=True)
    first_access_at   = models.DateTimeField(auto_now_add=True)
    total_seconds_used = models.PositiveIntegerField(
        default=0,
        help_text="Accumulated seconds used today (max = TRIAL_DURATION_SECONDS).",
    )

    class Meta:
        verbose_name = "Trial Record"
        verbose_name_plural = "Trial Records"
        ordering = ['-trial_date']

    def __str__(self):
        return f"Trial {self.trial_date} — {self.total_seconds_used}s used"


# ---------------------------------------------------------------------------
# LicenseAuditLog — append-only
# ---------------------------------------------------------------------------

class LicenseAuditLog(models.Model):
    EVENT_CHOICES = [
        ('webhook_received',   'Webhook Received'),
        ('activated',          'License Activated'),
        ('deactivated',        'License Deactivated'),
        ('expired',            'License Expired'),
        ('suspended',          'License Suspended'),
        ('trial_started',      'Trial Started'),
        ('trial_ended',        'Trial Ended'),
        ('verification_ok',    'Online Verification Succeeded'),
        ('verification_failed','Online Verification Failed'),
        ('fraud_attempt',      'Fraud Attempt Detected'),
        ('disconnected_wa',    'WhatsApp Disconnected (License)'),
        ('disconnected_email', 'Email Disconnected (License)'),
    ]

    event           = models.CharField(max_length=32, choices=EVENT_CHOICES)
    payload         = models.JSONField(default=dict)
    source_ip       = models.GenericIPAddressField(null=True, blank=True)
    signature_valid = models.BooleanField(
        null=True,
        help_text="Result of HMAC signature validation (if applicable).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "License Audit Log"
        verbose_name_plural = "License Audit Logs"
        ordering = ['-created_at']
        # No change/delete permissions — enforced in admin.py

    def __str__(self):
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.get_event_display()}"
