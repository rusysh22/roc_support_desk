"""
licensing/admin.py
==================
Django admin registration for license models.

Rules:
- LicenseRecord  : Editable only by superusers.
- TrialRecord    : Read-only; for inspection only.
- LicenseAuditLog: Append-only — NO change or delete permissions.
"""
from django.contrib import admin

from .models import LicenseAuditLog, LicenseRecord, TrialRecord


# ---------------------------------------------------------------------------
# LicenseRecord
# ---------------------------------------------------------------------------

@admin.register(LicenseRecord)
class LicenseRecordAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'plan_tier', 'status', 'issued_to',
        'issued_at', 'expires_at', 'last_verified_at', 'max_agents',
    ]
    readonly_fields = [
        'id', 'install_fingerprint', 'last_verified_at',
        'issued_at',
    ]
    fieldsets = [
        ('License Identity', {
            'fields': ('license_key', 'issued_to', 'plan_tier', 'status'),
        }),
        ('Validity', {
            'fields': ('issued_at', 'expires_at', 'last_verified_at'),
        }),
        ('Limits & Features', {
            'fields': ('max_agents', 'features_json'),
        }),
        ('Binding & Marketplace', {
            'fields': ('install_fingerprint', 'marketplace_endpoint'),
            'classes': ('collapse',),
        }),
    ]

    def has_delete_permission(self, request, obj=None):
        # Never allow deleting the singleton
        return False

    def has_module_permission(self, request):
        return False

    def has_add_permission(self, request):
        # Prevent accidental creation of extra rows
        return not LicenseRecord.objects.exists()


# ---------------------------------------------------------------------------
# TrialRecord
# ---------------------------------------------------------------------------

@admin.register(TrialRecord)
class TrialRecordAdmin(admin.ModelAdmin):
    list_display = ['trial_date', 'total_seconds_used', 'first_access_at']
    readonly_fields = ['trial_date', 'first_access_at', 'total_seconds_used']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_module_permission(self, request):
        return False


# ---------------------------------------------------------------------------
# LicenseAuditLog — Append-Only
# ---------------------------------------------------------------------------

@admin.register(LicenseAuditLog)
class LicenseAuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'event', 'source_ip', 'signature_valid']
    list_filter  = ['event', 'signature_valid']
    search_fields = ['event', 'source_ip']
    readonly_fields = ['event', 'payload', 'source_ip', 'signature_valid', 'created_at']

    def has_add_permission(self, request):
        return False  # Only created programmatically

    def has_change_permission(self, request, obj=None):
        return False  # Immutable — no editing

    def has_delete_permission(self, request, obj=None):
        return False  # Immutable — no deleting

    def has_module_permission(self, request):
        return False
