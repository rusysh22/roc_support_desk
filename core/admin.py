"""
Core App — Django Admin Registration
======================================
"""
from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.shortcuts import redirect
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import AuditLog, CompanyUnit, Employee, Feedback, User, SiteConfig, SSOConfig, OTPToken, LoginSlideImage, UserNotificationPreference, AIConfig


# =====================================================================
# Custom User Admin
# =====================================================================

@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    """Admin configuration for the custom User model."""

    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = (
        "login_username",
        "username",
        "email",
        "nik",
        "role_access",
        "initials",
        "can_handle_confidential",
        "is_staff",
        "is_active",
    )
    list_filter = ("role_access", "can_handle_confidential", "is_staff", "is_active")
    search_fields = ("login_username", "username", "email", "nik", "initials")
    ordering = ("login_username",)

    fieldsets = (
        (None, {"fields": ("login_username", "password")}),
        (
            "Personal Info",
            {"fields": ("username", "email", "nik", "role_access", "initials", "can_handle_confidential")},
        ),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        ("Important Dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "login_username",
                    "username",
                    "email",
                    "nik",
                    "role_access",
                    "initials",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    # Roles that consume an agent seat (mirrors core/views_user.py)
    _AGENT_ROLES = {User.RoleAccess.SUPPORTDESK, User.RoleAccess.MANAGER}

    def _check_agent_limit(self, request, obj, change) -> bool:
        """
        Return True if the save should be blocked because it would exceed
        the max_agents license limit.

        Triggers when the resulting record would be a new *active* agent seat:
        - New user with an agent role and is_active=True
        - Existing user whose role or active status changes so that a net new
          active agent seat is consumed.
        """
        from django.contrib import messages as django_messages

        if obj.role_access not in self._AGENT_ROLES or not obj.is_active:
            return False

        is_new_active_agent = not change
        if change:
            try:
                old = User.objects.get(pk=obj.pk)
                was_active_agent = old.role_access in self._AGENT_ROLES and old.is_active
                is_new_active_agent = not was_active_agent
            except User.DoesNotExist:
                is_new_active_agent = True

        if not is_new_active_agent:
            return False

        try:
            from licensing.models import LicenseRecord
            license_obj = LicenseRecord.get_current()
            max_allowed = license_obj.max_agents
        except Exception:
            return False

        current_count = User.objects.filter(
            role_access__in=self._AGENT_ROLES, is_active=True
        ).count()

        if max_allowed is not None and current_count >= max_allowed:
            django_messages.error(
                request,
                f"Agent limit reached ({current_count}/{max_allowed}). "
                "Upgrade your license to add more agents. User was NOT saved.",
            )
            return True

        return False

    def save_model(self, request, obj, form, change):
        if self._check_agent_limit(request, obj, change):
            request._admin_agent_limit_blocked = True
            return
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        if getattr(request, '_admin_agent_limit_blocked', False):
            return redirect(request.path)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if getattr(request, '_admin_agent_limit_blocked', False):
            return redirect(request.path)
        return super().response_change(request, obj)


# =====================================================================
# Company Unit Admin
# =====================================================================

@admin.register(CompanyUnit)
class CompanyUnitAdmin(ModelAdmin):
    """Admin configuration for CompanyUnit."""

    list_display = ("code", "name", "created_at", "updated_at")
    search_fields = ("name", "code")
    ordering = ("code",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")

    def save_model(self, request, obj, form, change):
        """Auto-populate audit fields on save."""
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# Employee Admin
# =====================================================================

@admin.register(Employee)
class EmployeeAdmin(ModelAdmin):
    """Admin configuration for Employee."""

    list_display = (
        "full_name",
        "email",
        "phone_number",
        "job_role",
        "unit",
        "created_at",
    )
    list_filter = ("unit",)
    search_fields = ("full_name", "email", "phone_number")
    ordering = ("full_name",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")
    autocomplete_fields = ("unit",)

    def save_model(self, request, obj, form, change):
        """Auto-populate audit fields on save."""
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# Configuration Admin
# =====================================================================

class LoginSlideImageInline(admin.TabularInline):
    model = LoginSlideImage
    extra = 1
    fields = ("image", "order")
    ordering = ("order",)
    verbose_name = "Slide Image"
    verbose_name_plural = "Login Slide Images (drag to reorder)"


@admin.register(SiteConfig)
class SiteConfigAdmin(ModelAdmin):
    inlines = [LoginSlideImageInline]
    """Admin configuration for SiteConfig (Singleton)."""

    list_display = ("site_name", "login_theme", "updated_at")
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        ("Branding", {
            "fields": ("site_name", "logo", "favicon"),
        }),
        ("Login Page", {
            "fields": ("login_theme", "login_image", "contact_info"),
            "description": (
                "Select the login page theme. "
                "<strong>Theme 1</strong>: Classic purple split panel (default). "
                "<strong>Theme 2</strong>: Modern — large image on the left with automatic slideshow. "
                "<em>Login Page Image</em> is the primary image (fallback). "
                "Add more slides via <strong>Login Slide Images</strong> below."
            ),
        }),
        ("Portal Settings", {
            "fields": ("require_public_login", "max_upload_size_mb"),
        }),
        ("Terms & Privacy", {
            "fields": ("terms_and_privacy",),
            "classes": ("collapse",),
        }),
        ("Audit", {
            "fields": ("id", "created_at", "updated_at", "created_by", "updated_by"),
            "classes": ("collapse",),
        }),
    )

    def has_add_permission(self, request):
        # Prevent adding new instances if one already exists
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        # Prevent deleting the single instance
        return False


# =====================================================================
# SSO Configuration Admin
# =====================================================================

@admin.register(SSOConfig)
class SSOConfigAdmin(ModelAdmin):
    """Admin configuration for SSOConfig — single sign-on provider settings."""

    list_display = ("__str__", "sso_enabled", "microsoft_enabled", "google_enabled", "updated_at")
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")
    fieldsets = (
        (
            "Master Switch",
            {
                "fields": ("sso_enabled",),
                "description": (
                    "Enable or disable the entire SSO feature. "
                    "When disabled, SSO buttons will not appear on the login page."
                ),
            },
        ),
        (
            "Microsoft SSO",
            {
                "fields": (
                    "microsoft_enabled",
                    "microsoft_client_id",
                    "microsoft_client_secret",
                    "microsoft_tenant_id",
                ),
                "description": (
                    "Obtain the Client ID, Secret, and Tenant ID from "
                    "<strong>Azure Portal → App Registrations</strong>. "
                    "Redirect URI to register: "
                    "<code>https://&lt;domain&gt;/accounts/microsoft/login/callback/</code>. "
                    "Tenant ID: use <code>organizations</code> for any Microsoft work/school account, "
                    "or enter a specific Tenant UUID to restrict to a single organisation."
                ),
            },
        ),
        (
            "Google SSO",
            {
                "fields": (
                    "google_enabled",
                    "google_client_id",
                    "google_client_secret",
                    "google_allowed_domains",
                ),
                "description": (
                    "Obtain the Client ID and Secret from "
                    "<strong>Google Cloud Console → APIs &amp; Services → Credentials</strong>. "
                    "Redirect URI to register: "
                    "<code>https://&lt;domain&gt;/accounts/google/login/callback/</code>. "
                    "Allowed Domains: leave blank to allow any Google account, or enter your "
                    "organisation's domain (e.g. <code>company.com</code>)."
                ),
            },
        ),
        (
            "Whitelist Ticket",
            {
                "fields": ("sso_whitelist_category",),
                "description": (
                    "Ticket category used when a new SSO user registers and requires whitelisting. "
                    "If left blank, the system will automatically create or reuse the 'SSO Access Request' category."
                ),
            },
        ),
        (
            "Audit",
            {
                "fields": ("id", "created_at", "updated_at", "created_by", "updated_by"),
                "classes": ("collapse",),
            },
        ),
    )

    def has_add_permission(self, request):
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
        # Reload provider settings after save so changes take effect on next restart
        try:
            from core.apps import CoreConfig
            CoreConfig("core", CoreConfig)._load_sso_providers()
        except Exception:
            pass


# =====================================================================
# OTP Token Admin
# =====================================================================

@admin.register(OTPToken)
class OTPTokenAdmin(ModelAdmin):
    """Admin configuration for OTPToken."""
    
    list_display = ("user", "token", "created_at", "is_used", "is_valid_token")
    list_filter = ("is_used", "created_at")
    search_fields = ("user__username", "user__email", "token")
    readonly_fields = ("created_at",)

    def has_module_permission(self, request):
        return False
    
    def is_valid_token(self, obj):
        return obj.is_valid()
    is_valid_token.boolean = True
    is_valid_token.short_description = "Is Valid"


# =====================================================================
# Feedback Admin
# =====================================================================

@admin.register(Feedback)
class FeedbackAdmin(ModelAdmin):
    """Admin configuration for user feedback."""

    list_display = ("subject", "feedback_type", "user", "is_read", "created_at")
    list_filter = ("feedback_type", "is_read", "created_at")
    search_fields = ("subject", "message", "user__username", "user__email")
    readonly_fields = ("id", "user", "feedback_type", "subject", "message", "created_at", "updated_at")
    list_editable = ("is_read",)


# =====================================================================
# Audit Log Admin (read-only)
# =====================================================================

@admin.register(AuditLog)
class AuditLogAdmin(ModelAdmin):
    """Read-only view of the security audit log (ISO 27001 A.12.3)."""

    list_display = ("created_at", "action", "actor_username", "ip_address", "target_type", "target_id")
    list_filter = ("action", "created_at")
    search_fields = ("actor_username", "ip_address", "target_type", "target_id")
    readonly_fields = (
        "actor", "actor_username", "action", "ip_address",
        "target_type", "target_id", "details", "created_at",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# =====================================================================
# User Notification Preference Admin
# =====================================================================

@admin.register(UserNotificationPreference)
class UserNotificationPreferenceAdmin(ModelAdmin):
    """Admin view for user notification preferences."""

    list_display = ("user", "email_enabled", "whatsapp_enabled", "teams_enabled")
    list_filter = ("email_enabled", "whatsapp_enabled", "teams_enabled")
    search_fields = ("user__username", "user__email")
    readonly_fields = ()
    fieldsets = (
        ("User", {"fields": ("user",)}),
        ("Channels", {"fields": ("email_enabled", "whatsapp_enabled", "teams_enabled")}),
        ("Contact Overrides", {"fields": ("whatsapp_number", "teams_webhook_url")}),
        ("Event Toggles", {"fields": ("on_new_message", "on_mention", "on_status_change", "on_follower_added")}),
        ("Quiet Hours", {"fields": ("quiet_start", "quiet_end")}),
    )


# =====================================================================
# AI Assistant Configuration Admin
# =====================================================================

class _APIKeyWidget(forms.TextInput):
    """Password-style widget that never echoes the saved key back to the browser."""

    input_type = "password"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.attrs.setdefault("autocomplete", "new-password")
        self.attrs.setdefault("placeholder", "Enter new API key to replace the saved one…")

    def format_value(self, value):
        del value  # key must never appear in HTML source
        return ""


class _AIConfigAdminForm(forms.ModelForm):
    ai_api_key = forms.CharField(
        label="API Key",
        required=False,
        widget=_APIKeyWidget,
        help_text=(
            "Stored encrypted. Leave blank to keep the current key. "
            "Enter a new value to replace it."
        ),
    )

    class Meta:
        model = AIConfig
        fields = "__all__"

    def clean_ai_api_key(self):
        new_key = self.cleaned_data.get("ai_api_key", "").strip()
        if not new_key:
            # Preserve whatever is already saved — don't wipe the key on a blank submit.
            return self.instance.ai_api_key if self.instance.pk else ""
        return new_key


@admin.register(AIConfig)
class AIConfigAdmin(ModelAdmin):
    """Singleton admin for the Gemini AI assistant configuration."""

    form = _AIConfigAdminForm

    fieldsets = (
        (
            "🔌 General",
            {
                "fields": ("ai_enabled", "ai_provider"),
                "description": "Master switch for the AI assistant widget.",
            },
        ),
        (
            "🔑 API Credentials",
            {
                "fields": ("ai_api_key", "ai_model_name"),
                "description": "API key is stored encrypted. Obtain from Google AI Studio (aistudio.google.com).",
            },
        ),
        (
            "⚙️ Generation Settings",
            {
                "fields": ("ai_temperature", "ai_max_output_tokens"),
                "description": "Parameters controlling the AI response quality and length.",
            },
        ),
        (
            "📚 Knowledge Sources",
            {
                "fields": ("ai_sources", "ai_max_context_docs"),
                "description": "Configure which content the AI uses to answer questions.",
            },
        ),
        (
            "💬 Prompts & Widget Copy",
            {
                "fields": ("ai_system_prompt", "ai_welcome_message", "ai_placeholder_text"),
                "description": "Customize the AI's behavior and the widget's text. Use {site_name} in system prompt.",
            },
        ),
        (
            "🛡️ Rate Limiting",
            {
                "fields": ("ai_rate_limit_per_hour",),
                "description": "Prevent abuse. Set to 0 to disable rate limiting entirely.",
            },
        ),
    )

    def has_add_permission(self, request):
        """Allow creating only if no instance exists yet."""
        return not AIConfig.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        """Redirect list view directly to the singleton edit form."""
        obj = AIConfig.get_solo()
        return redirect('admin:core_aiconfig_change', obj.pk)
