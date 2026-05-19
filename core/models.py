"""
Core App — Models
==================
Provides the foundational models for the entire RoC Desk system:

- ``AuditableModel``  — abstract base with UUID pk, timestamps, and audit FKs.
- ``User``            — custom user model with login_username, NIK, role_access.
- ``CompanyUnit``     — organisational unit (e.g. IT, FIN, HR).
- ``Employee``        — internal staff / end-user who submits or receives cases.
"""
import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


# =====================================================================
# Abstract Base
# =====================================================================

class AuditableModel(models.Model):
    """
    Abstract base model that provides universal audit fields.

    Every operational model in RoC Desk **must** inherit from this to
    guarantee full traceability of record creation and modification.

    Fields:
        id            — UUID v4 primary key (avoids sequential-id exposure).
        created_at    — Auto-set on INSERT.
        updated_at    — Auto-set on every UPDATE.
        created_by    — FK to the User who created the record (nullable for
                        system-generated records such as webhook imports).
        updated_by    — FK to the User who last modified the record.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        verbose_name="ID",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_created",
        verbose_name="Created By",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_updated",
        verbose_name="Updated By",
    )

    class Meta:
        abstract = True


# =====================================================================
# Custom User Model
# =====================================================================

class User(AbstractUser):
    """
    Custom user model for RoC Desk admin/staff authentication.

    Authentication is performed via ``login_username`` (not the default
    ``username`` field).  The ``username`` field is retained purely as a
    display name.

    Additional fields:
        login_username  — unique credential used for logging in.
        NIK             — employee identification number, unique.
        role_access     — determines permission tier.
        initials        — user initials used as a signature.
    """

    class RoleAccess(models.TextChoices):
        SUPERADMIN = "SuperAdmin", "Super Admin"
        MANAGER = "Manager", "Manager"
        SUPPORTDESK = "SupportDesk", "Support Desk"
        AUDITOR = "Auditor", "Auditor"
        PORTALUSER = "PortalUser", "Portal User"

    # Override: username is kept for display only, NOT for login
    username = models.CharField(
        max_length=150,
        verbose_name="Display Name",
        help_text="Human-readable display name (not used for login).",
    )

    email = models.EmailField(
        unique=True,
        verbose_name="Email address",
    )

    login_username = models.CharField(
        max_length=150,
        unique=True,
        verbose_name="Login Username",
        help_text="Unique credential used for authentication.",
    )

    nik = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        verbose_name="NIK",
        help_text="Nomor Induk Karyawan — unique employee identifier.",
    )

    role_access = models.CharField(
        max_length=20,
        choices=RoleAccess.choices,
        default=RoleAccess.SUPPORTDESK,
        verbose_name="Role Access",
    )

    initials = models.CharField(
        max_length=5,
        verbose_name="Initials",
        help_text="User initials used as a signature (e.g., 'mrs').",
    )

    phone_number = EncryptedCharField(
        max_length=30,
        blank=True,
        default='',
        verbose_name="Phone Number",
        help_text="User's phone/mobile number (encrypted at rest).",
    )

    can_handle_confidential = models.BooleanField(
        default=False,
        verbose_name="Can Handle Confidential",
        help_text="Allow this user to access tickets in confidential categories.",
    )

    must_change_password = models.BooleanField(
        default=False,
        verbose_name="Must Change Password",
        help_text="Force this user to change their password on next login.",
    )

    timezone = models.CharField(
        max_length=64,
        default="Asia/Jakarta",
        verbose_name="Timezone",
        help_text="User's local timezone for displaying dates and times.",
    )

    # --- Auth configuration ---
    USERNAME_FIELD = "login_username"
    REQUIRED_FIELDS = ["username", "email"]

    class Meta:
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["login_username"]

    def __str__(self):
        return f"{self.username} ({self.login_username})"


# =====================================================================
# Company Unit
# =====================================================================

class CompanyUnit(AuditableModel):
    """
    Organisational unit — supports one level of parent-child nesting.

    A unit with ``parent=None`` is a top-level entity (e.g. a holding company
    or standalone organisation).  A unit with a parent is a child unit
    (e.g. a department, subsidiary, or branch under the parent).
    """

    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Parent Unit",
        help_text="Leave empty for a top-level unit. Select a parent to nest this unit under it.",
    )
    name = models.CharField(max_length=150, verbose_name="Unit Name")
    code = models.CharField(
        max_length=20,
        unique=True,
        verbose_name="Unit Code",
        help_text="Short identifier, e.g. IT, FIN, HR.",
    )

    # --- Location ---
    address = models.TextField(
        blank=True,
        default='',
        verbose_name="Address",
        help_text="Full street address of this unit's office.",
    )
    city = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name="City",
    )
    province = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name="Province",
    )
    latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        verbose_name="Latitude",
        help_text="GPS latitude, e.g. -6.2088000",
    )
    longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True,
        verbose_name="Longitude",
        help_text="GPS longitude, e.g. 106.8456000",
    )

    class Meta:
        verbose_name = "Unit"
        verbose_name_plural = "Units"
        ordering = ["code"]

    @property
    def is_parent(self):
        return self.children.exists()

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return f"{self.code} — {self.name}"


# =====================================================================
# Employee
# =====================================================================

phone_regex = RegexValidator(
    regex=r"^\+?[1-9]\d{6,14}$",
    message="Phone number must be in E.164 format (e.g. +6281234567890).",
)


class Employee(AuditableModel):
    """
    Internal employee / end-user who interacts with the service desk.

    The ``phone_number`` is stored in E.164 format so it can be matched
    directly against WhatsApp sender IDs from Evolution API webhooks.
    """

    full_name = models.CharField(max_length=255, verbose_name="Full Name")
    email = models.EmailField(unique=True, null=True, blank=True, verbose_name="Email")
    phone_number = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        validators=[phone_regex],
        verbose_name="Phone Number",
        help_text="E.164 format, e.g. +6281234567890",
    )
    job_role = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="Job Role",
    )
    unit = models.ForeignKey(
        CompanyUnit,
        on_delete=models.PROTECT,
        related_name="employees",
        verbose_name="Company Unit",
    )

    class Meta:
        verbose_name = "Employee"
        verbose_name_plural = "Employees"
        ordering = ["full_name"]

    def clean(self):
        super().clean()
        if self.phone_number:
            raw = self.phone_number.lstrip("+")
            if not raw.isdigit() or not (7 <= len(raw) <= 15):
                from django.core.exceptions import ValidationError
                raise ValidationError({
                    "phone_number": (
                        f"'{self.phone_number}' is not a valid phone number. "
                        "It must be in E.164 format (7-15 digits, e.g. +6281234567890). "
                        "Numbers longer than 15 digits are likely WhatsApp Linked Device IDs (LID)."
                    )
                })

    def has_valid_phone(self):
        """Returns True if phone_number is a valid E.164 number (7-15 digits)."""
        if not self.phone_number:
            return False
        raw = self.phone_number.lstrip("+")
        return raw.isdigit() and 7 <= len(raw) <= 15

    def __str__(self):
        return f"{self.full_name} ({self.unit.code})"


# =====================================================================
# Configuration
# =====================================================================

class SiteConfig(AuditableModel):
    """
    Singleton model to hold global website configurations.
    """
    site_name = models.CharField(
        max_length=100, 
        default="Support Desk",
        verbose_name="Site Name",
        help_text="The name of the site displayed in the navbar, tabs, and login screens."
    )
    favicon = models.ImageField(
        upload_to="site_config/",
        null=True,
        blank=True,
        verbose_name="Favicon",
        help_text="Upload a square \".ico\" or \".png\" image for the browser tab icon."
    )
    logo = models.ImageField(
        upload_to="site_config/",
        null=True,
        blank=True,
        verbose_name="Site Logo",
        help_text="Upload the main logo displayed in the navigation bar."
    )
    max_upload_size_mb = models.PositiveIntegerField(
        default=10,
        verbose_name="Max Upload Size (MB)",
        help_text="Maximum allowed file size for form attachments in Megabytes."
    )
    require_public_login = models.BooleanField(
        default=False,
        verbose_name="Require Public Login",
        help_text="If enabled, all users must be logged in to access the public Ticket Portal and Knowledge Base."
    )

    LOGIN_THEME_CHOICES = [
        ("theme1", "Tema 1 — Classic (Purple Split Panel)"),
        ("theme2", "Tema 2 — Modern (Full Image Left, Dark Form Right)"),
    ]
    login_theme = models.CharField(
        max_length=20,
        choices=LOGIN_THEME_CHOICES,
        default="theme1",
        verbose_name="Login Page Theme",
        help_text="Select the visual theme for the login page and related auth screens.",
    )
    login_image = models.ImageField(
        upload_to="site_config/",
        null=True,
        blank=True,
        verbose_name="Login Page Image (Theme 2)",
        help_text="Primary hero image for Theme 2 left panel. Add more slides via 'Login Slide Images' below.",
    )
    contact_info = models.TextField(
        blank=True,
        default="",
        verbose_name="Contact Info (Narahubung)",
        help_text="Contact details (narahubung) shown on the login page footer. Supports plain text or simple HTML.",
    )

    wa_instance_activated_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="WA Instance Activation Date",
        help_text=(
            "Set this to the date the WhatsApp number was first connected. "
            "Used to enforce a warm-up period (lower send caps in the first 14 days) "
            "to reduce the chance of being flagged by WhatsApp."
        ),
    )

    # --- WhatsApp Gateway Config (overrides .env values when set) ---
    wa_main_instance = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="WA Main Instance Name",
        help_text=(
            "Evolution API instance name for customer-facing messages. "
            "Overrides EVOLUTION_INSTANCE_NAME in .env when set."
        ),
    )
    wa_notif_instance = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="WA Notif Instance Name",
        help_text=(
            "Evolution API instance name for internal staff notifications. "
            "Overrides EVOLUTION_NOTIF_INSTANCE_NAME in .env when set. "
            "Leave blank to use the same instance as customer messages."
        ),
    )
    wa_business_hour_start = models.PositiveSmallIntegerField(
        default=7,
        verbose_name="WA Business Hour Start",
        help_text="Hour (0-23) when internal WA broadcasts become active. Default: 7 (07:00 WIB).",
    )
    wa_business_hour_end = models.PositiveSmallIntegerField(
        default=20,
        verbose_name="WA Business Hour End",
        help_text="Hour (0-23) after which internal WA broadcasts are paused. Default: 20 (20:00 WIB).",
    )
    wa_business_days = models.CharField(
        max_length=20,
        default="0,1,2,3,4",
        verbose_name="WA Business Days",
        help_text=(
            "Comma-separated weekday numbers (0=Mon … 6=Sun) when broadcasts are allowed. "
            "Default: 0,1,2,3,4 (Monday–Friday)."
        ),
    )

    terms_and_privacy = models.TextField(
        blank=True,
        default=(
            "Terms of Use\n"
            "=============\n\n"
            "By using this Support Desk system, you agree to the following terms:\n\n"
            "1. This system is provided for internal use only. All submitted tickets and data "
            "are treated as company property.\n"
            "2. Users must provide accurate information when submitting tickets or requests.\n"
            "3. Misuse of the system, including submitting spam or false reports, may result in "
            "restricted access.\n"
            "4. The system administrator reserves the right to modify these terms at any time.\n\n"
            "Privacy Policy\n"
            "===============\n\n"
            "1. Personal data collected (name, email, phone number, employee ID) is used solely "
            "for ticket management and communication purposes.\n"
            "2. Ticket data may be accessed by authorized support staff and management for "
            "resolution and reporting.\n"
            "3. Confidential tickets are restricted to authorized personnel only.\n"
            "4. We do not share your personal data with third parties without your consent.\n"
            "5. Data retention follows company policy. Closed tickets are retained for audit and "
            "reporting purposes."
        ),
        verbose_name="Terms & Privacy Policy",
        help_text="Terms of use and privacy policy text displayed on the Help & About page.",
    )

    class Meta:
        verbose_name = "Site Configuration"
        verbose_name_plural = "Site Configuration"

    def save(self, *args, **kwargs):
        # Force the singleton behavior. If another instance exists, delete it.
        # This keeps the UUID auditable model logic intact without breaking it by forcing pk=1.
        if not self._state.adding and not self.pk:
            pass
        
        if SiteConfig.objects.exclude(pk=self.pk).exists():
            SiteConfig.objects.exclude(pk=self.pk).delete()
            
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        """
        Returns the singleton instance of SiteConfig.
        Creates one if it doesn't exist.
        """
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create(site_name="Support Desk")
        return obj

    def get_wa_daily_limit(self) -> int:
        """
        Return the effective daily WA outbound send cap based on instance age.

        Warm-up schedule (days since wa_instance_activated_at):
          Day  0– 3 : 20 messages/day  (very cautious)
          Day  4– 7 : 50 messages/day
          Day  8–14 : 150 messages/day
          Day 15+   : 1500 messages/day (full capacity)

        If wa_instance_activated_at is not set, returns the full limit.
        """
        from django.utils import timezone

        if not self.wa_instance_activated_at:
            return 1500

        days = (timezone.now() - self.wa_instance_activated_at).days
        if days <= 3:
            return 20
        if days <= 7:
            return 50
        if days <= 14:
            return 150
        return 1500

    def __str__(self):
        return self.site_name


# =====================================================================
# AI Assistant Configuration
# =====================================================================

class AIConfig(AuditableModel):
    """
    Singleton configuration for the AI Assistant widget.

    Controls the Gemini-powered Q&A assistant that appears in the
    floating chat widget on Knowledge Base and User Manual pages.

    All parameters are configurable from the Admin panel — no hardcoding.
    The API key is stored encrypted at rest via EncryptedCharField.
    """

    AI_PROVIDER_CHOICES = [
        ("gemini", "Google Gemini"),
    ]

    GEMINI_MODEL_CHOICES = [
        ("gemini-3-flash", "Gemini 3 Flash (Newest)"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash (Recommended)"),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite (Cheapest)"),
        ("gemini-2.0-flash", "Gemini 2.0 Flash (Deprecated)"),
        ("gemini-1.5-flash", "Gemini 1.5 Flash (Deprecated)"),
    ]

    AI_SOURCES_CHOICES = [
        ("both", "Knowledge Base + User Manual"),
        ("kb_only", "Knowledge Base Only"),
        ("manual_only", "User Manual Only"),
    ]

    # ── Master switch ──────────────────────────────────────────────────
    ai_enabled = models.BooleanField(
        default=False,
        verbose_name="Enable AI Assistant",
        help_text="Show the 'Tanya AI' tab in the floating chat widget.",
    )

    # ── Provider & credentials ─────────────────────────────────────────
    ai_provider = models.CharField(
        max_length=30,
        choices=AI_PROVIDER_CHOICES,
        default="gemini",
        verbose_name="AI Provider",
    )
    ai_api_key = EncryptedCharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="API Key",
        help_text="Your Google AI Studio API key (stored encrypted).",
    )
    ai_model_name = models.CharField(
        max_length=100,
        choices=GEMINI_MODEL_CHOICES,
        default="gemini-2.0-flash",
        verbose_name="Gemini Model",
        help_text="The Gemini model to use for generating answers.",
    )

    # ── Generation parameters ──────────────────────────────────────────
    ai_temperature = models.FloatField(
        default=0.3,
        verbose_name="Temperature",
        help_text=(
            "Controls creativity (0.0 = deterministic, 1.0 = creative). "
            "Recommended: 0.3 for factual Q&A."
        ),
    )
    ai_max_output_tokens = models.PositiveIntegerField(
        default=1024,
        verbose_name="Max Output Tokens",
        help_text="Maximum length of the AI's response in tokens (~750 words).",
    )

    # ── Knowledge retrieval ────────────────────────────────────────────
    ai_sources = models.CharField(
        max_length=20,
        choices=AI_SOURCES_CHOICES,
        default="both",
        verbose_name="Knowledge Sources",
        help_text="Which knowledge sources the AI will use to answer questions.",
    )
    ai_max_context_docs = models.PositiveIntegerField(
        default=5,
        verbose_name="Max Context Documents",
        help_text=(
            "Maximum number of relevant articles/pages to include in each AI query. "
            "Higher = more context but slower response."
        ),
    )

    # ── Prompts & UI copy ─────────────────────────────────────────────
    ai_system_prompt = models.TextField(
        verbose_name="System Prompt",
        default=(
            "You are the AI Assistant for {site_name}.\n"
            "Your task: answer user questions ONLY based on the provided context documents.\n\n"
            "IMPORTANT Rules:\n"
            "1. Answer in the same language as the user's question.\n"
            "2. If the answer is NOT in the context, honestly say: "
            "'I'm sorry, but I couldn't find that information in our documentation. "
            "Please contact the Support Desk for further assistance.'\n"
            "3. DO NOT invent or hallucinate information not present in the context.\n"
            "4. Provide concise, clear, and easy-to-understand answers.\n"
            "5. If relevant, cite the document from which the information was derived.\n"
        ),
        help_text=(
            "Instructions sent to the AI before each question. "
            "Use {site_name} as a placeholder for the site name."
        ),
    )
    ai_welcome_message = models.TextField(
        default=(
            "Hello! I am an AI Assistant ready to help you. "
            "Ask me anything about our Knowledge Base and User Manual."
        ),
        verbose_name="Welcome Message",
        help_text="Message shown at the top of the AI chat panel.",
    )
    ai_placeholder_text = models.CharField(
        max_length=200,
        default="Ask something...",
        verbose_name="Input Placeholder",
        help_text="Placeholder text inside the AI question input box.",
    )

    # ── Rate limiting ──────────────────────────────────────────────────
    ai_rate_limit_per_hour = models.PositiveIntegerField(
        default=30,
        verbose_name="Rate Limit (per hour)",
        help_text=(
            "Maximum questions a single IP/session can ask per hour. "
            "Set to 0 to disable rate limiting."
        ),
    )

    class Meta:
        verbose_name = "AI Assistant Configuration"
        verbose_name_plural = "AI Assistant Configuration"

    def save(self, *args, **kwargs):
        """Enforce singleton — only one AIConfig record may exist."""
        if AIConfig.objects.exclude(pk=self.pk).exists():
            AIConfig.objects.exclude(pk=self.pk).delete()
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        """Return the singleton instance, creating it if necessary."""
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj

    def __str__(self):
        status = "✅ Enabled" if self.ai_enabled else "❌ Disabled"
        return f"AI Assistant Config ({status})"


# =====================================================================
# SSO Configuration
# =====================================================================

class SSOConfig(AuditableModel):
    """
    Singleton configuration for SSO login via Microsoft and Google accounts.
    Toggle each provider independently; credentials are encrypted at rest.
    """

    sso_enabled = models.BooleanField(
        default=False,
        verbose_name="Enable SSO",
        help_text="Master switch — show SSO login buttons on the login page.",
    )

    # --- Microsoft ---
    microsoft_enabled = models.BooleanField(
        default=False,
        verbose_name="Enable Microsoft SSO",
    )
    microsoft_client_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Microsoft Client ID (App ID)",
        help_text="Application (client) ID from Azure AD App Registration.",
    )
    microsoft_client_secret = EncryptedCharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Microsoft Client Secret",
        help_text="Client secret from Azure AD App Registration (stored encrypted).",
    )
    microsoft_tenant_id = models.CharField(
        max_length=200,
        blank=True,
        default="organizations",
        verbose_name="Microsoft Tenant ID",
        help_text=(
            "'organizations' = any Microsoft work/school account. "
            "Enter a specific Tenant UUID to restrict login to a single organisation. "
            "'common' = any Microsoft account including personal."
        ),
    )

    # --- Google ---
    google_enabled = models.BooleanField(
        default=False,
        verbose_name="Enable Google SSO",
    )
    google_client_id = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Google Client ID",
        help_text="Client ID from Google Cloud Console OAuth 2.0 credentials.",
    )
    google_client_secret = EncryptedCharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Google Client Secret",
        help_text="Client secret from Google Cloud Console (stored encrypted).",
    )
    google_allowed_domains = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Google Allowed Domains",
        help_text=(
            "Comma-separated list of allowed email domains for Google SSO "
            "(e.g. company.com,subsidiary.co.id). "
            "Leave blank to allow any Google account."
        ),
    )

    # --- Whitelist Ticket ---
    sso_whitelist_category = models.ForeignKey(
        "cases.CaseCategory",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="SSO Whitelist Ticket Category",
        help_text=(
            "Ticket category used for new SSO user whitelist requests. "
            "If left blank, the system will automatically use or create an 'SSO Access Request' category."
        ),
    )

    class Meta:
        verbose_name = "SSO Configuration"
        verbose_name_plural = "SSO Configuration"

    def save(self, *args, **kwargs):
        if SSOConfig.objects.exclude(pk=self.pk).exists():
            SSOConfig.objects.exclude(pk=self.pk).delete()
        super().save(*args, **kwargs)
        self._sync_providers_to_settings()

    def _sync_providers_to_settings(self):
        """Update SOCIALACCOUNT_PROVIDERS in-memory so changes take effect without restart."""
        try:
            from django.conf import settings

            providers = {}
            if self.microsoft_enabled and self.microsoft_client_id:
                providers["microsoft"] = {
                    "TENANT": self.microsoft_tenant_id or "organizations",
                    "APP": {
                        "client_id": self.microsoft_client_id,
                        "secret": self.microsoft_client_secret or "",
                        "key": "",
                    },
                }
            if self.google_enabled and self.google_client_id:
                google_cfg = {
                    "APP": {
                        "client_id": self.google_client_id,
                        "secret": self.google_client_secret or "",
                        "key": "",
                    },
                    "SCOPE": ["profile", "email"],
                    "AUTH_PARAMS": {"access_type": "online"},
                }
                if self.google_allowed_domains:
                    domains = [d.strip() for d in self.google_allowed_domains.split(",") if d.strip()]
                    if domains:
                        google_cfg["HOSTED_DOMAIN"] = domains[0]
                providers["google"] = google_cfg
            settings.SOCIALACCOUNT_PROVIDERS = providers
        except Exception:
            pass

    @classmethod
    def get_solo(cls):
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj

    def __str__(self):
        providers = []
        if self.microsoft_enabled:
            providers.append("Microsoft")
        if self.google_enabled:
            providers.append("Google")
        status = ", ".join(providers) if providers else "Disabled"
        return f"SSO Config — {status}"


# =====================================================================
# User Feedback
# =====================================================================

class Feedback(AuditableModel):
    """
    Stores user feedback submitted from the Help & About page.
    """

    class FeedbackType(models.TextChoices):
        BUG = "Bug", "Bug Report"
        FEATURE = "Feature", "Feature Request"
        IMPROVEMENT = "Improvement", "Improvement Suggestion"
        OTHER = "Other", "Other"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="feedbacks",
        verbose_name="Submitted By",
    )
    feedback_type = models.CharField(
        max_length=20,
        choices=FeedbackType.choices,
        default=FeedbackType.OTHER,
        verbose_name="Type",
    )
    subject = models.CharField(
        max_length=200,
        verbose_name="Subject",
    )
    message = models.TextField(
        verbose_name="Message",
    )
    is_read = models.BooleanField(
        default=False,
        verbose_name="Read",
    )

    class Meta:
        verbose_name = "Feedback"
        verbose_name_plural = "Feedbacks"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.feedback_type}] {self.subject}"


class EmailConfig(AuditableModel):
    """
    Singleton model to hold dynamic global email configurations (IMAP/SMTP).
    """
    # IMAP Configuration (Receiving)
    imap_host = models.CharField(max_length=255, default="imap.gmail.com", verbose_name="IMAP Host")
    imap_port = models.IntegerField(default=993, verbose_name="IMAP Port")
    imap_user = models.CharField(max_length=255, blank=True, null=True, verbose_name="IMAP User")
    imap_password = EncryptedCharField(max_length=255, blank=True, null=True, verbose_name="IMAP App Password", help_text="e.g. Gmail App Password (16 chars, no spaces)")

    # SMTP Configuration (Sending)
    smtp_host = models.CharField(max_length=255, default="smtp.gmail.com", verbose_name="SMTP Host")
    smtp_port = models.IntegerField(default=587, verbose_name="SMTP Port")
    smtp_user = models.CharField(max_length=255, blank=True, null=True, verbose_name="SMTP User")
    smtp_password = EncryptedCharField(max_length=255, blank=True, null=True, verbose_name="SMTP App Password")
    smtp_use_tls = models.BooleanField(default=True, verbose_name="Use TLS")
    smtp_use_ssl = models.BooleanField(default=False, verbose_name="Use SSL")
    default_from_email = models.CharField(max_length=255, blank=True, null=True, verbose_name="Default From Email", help_text="Usually matches SMTP User")

    class Meta:
        verbose_name = "Email Configuration"
        verbose_name_plural = "Email Configuration"

    def save(self, *args, **kwargs):
        if not self._state.adding and not self.pk:
            pass
        
        if EmailConfig.objects.exclude(pk=self.pk).exists():
            EmailConfig.objects.exclude(pk=self.pk).delete()
            
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        """
        Returns the singleton instance of EmailConfig. 
        Creates one if it doesn't exist.
        """
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj

    def __str__(self):
        return "Email Configuration"


# =====================================================================
# Teams Integration Configuration
# =====================================================================

class TeamsConfig(AuditableModel):
    """
    Singleton model for Microsoft Teams integration settings.
    Supports both 1-way (Incoming Webhook, free) and 2-way (Bot Framework, paid/Azure).
    """
    # --- 1-WAY: Incoming Webhook (free) ---
    incoming_webhook_url = EncryptedCharField(
        max_length=1000, blank=True, null=True,
        verbose_name="Incoming Webhook URL",
        help_text="From Teams channel → Connectors → Incoming Webhook",
    )
    is_notification_enabled = models.BooleanField(
        default=False,
        verbose_name="Enable Teams Notifications",
    )

    # --- 2-WAY: Bot Framework (requires Azure subscription) ---
    is_bot_enabled = models.BooleanField(
        default=False,
        verbose_name="Enable Teams Bot (2-Way)",
    )
    bot_app_id = models.CharField(
        max_length=255, blank=True, null=True,
        verbose_name="Bot App ID",
        help_text="Azure Bot Service App ID (GUID)",
    )
    bot_app_password = EncryptedCharField(
        max_length=255, blank=True, null=True,
        verbose_name="Bot App Password / Client Secret",
    )
    tenant_id = models.CharField(
        max_length=255, blank=True, null=True,
        verbose_name="Azure Tenant ID",
    )
    bot_webhook_secret = EncryptedCharField(
        max_length=255, blank=True, null=True,
        verbose_name="Bot Webhook Secret",
        help_text="Used to verify inbound webhook requests from Teams",
    )
    service_url = models.CharField(
        max_length=500, blank=True, null=True,
        verbose_name="Service URL",
        help_text="Auto-filled from first incoming Teams Bot message — do not edit manually",
    )

    class Meta:
        verbose_name = "Teams Configuration"
        verbose_name_plural = "Teams Configuration"

    def save(self, *args, **kwargs):
        if TeamsConfig.objects.exclude(pk=self.pk).exists():
            TeamsConfig.objects.exclude(pk=self.pk).delete()
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj

    def __str__(self):
        return "Teams Configuration"


# =====================================================================
# Notification Channel Configuration
# =====================================================================

class NotificationConfig(AuditableModel):
    """
    Singleton model that controls which channels receive internal notifications
    when a new support ticket is created.
    """
    # Channel toggles
    notify_new_ticket_email = models.BooleanField(
        default=False,
        verbose_name="Email Notification",
    )
    notify_new_ticket_whatsapp = models.BooleanField(
        default=False,
        verbose_name="WhatsApp Notification",
    )
    notify_new_ticket_teams = models.BooleanField(
        default=False,
        verbose_name="Teams Notification",
    )

    # Email: comma-separated addresses of internal recipients
    email_recipients = models.TextField(
        blank=True,
        verbose_name="Email Recipients",
        help_text="Comma-separated email addresses, e.g. agen1@corp.com, agen2@corp.com",
    )

    # WhatsApp: comma-separated E.164 phone numbers of internal recipients
    whatsapp_recipients = models.TextField(
        blank=True,
        verbose_name="WhatsApp Recipients",
        help_text="Comma-separated numbers in E.164 format, e.g. 628123456789, 628987654321",
    )

    class Meta:
        verbose_name = "Notification Configuration"
        verbose_name_plural = "Notification Configuration"

    def save(self, *args, **kwargs):
        if NotificationConfig.objects.exclude(pk=self.pk).exists():
            NotificationConfig.objects.exclude(pk=self.pk).delete()
        super().save(*args, **kwargs)

    @classmethod
    def get_solo(cls):
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj

    def get_email_recipients_list(self):
        if not self.email_recipients:
            return []
        return [e.strip() for e in self.email_recipients.split(',') if e.strip()]

    def get_whatsapp_recipients_list(self):
        if not self.whatsapp_recipients:
            return []
        return [p.strip() for p in self.whatsapp_recipients.split(',') if p.strip()]

    def __str__(self):
        return "Notification Configuration"


# =====================================================================
# Dynamic Form Creator
# =====================================================================

class DynamicForm(AuditableModel):
    """
    Represents a customizable form created by the admin to be published in the public portal.
    """
    title = models.CharField(max_length=255, verbose_name="Form Title")
    description = models.TextField(blank=True, verbose_name="Form Description")
    slug = models.SlugField(max_length=255, unique=True, verbose_name="URL Slug")
    
    is_published = models.BooleanField(default=False, verbose_name="Is Published")
    requires_login = models.BooleanField(default=False, verbose_name="Requires Login to Submit")
    collect_user = models.BooleanField(default=False, verbose_name="Collect User Login")
    collect_company = models.BooleanField(default=False, verbose_name="Collect Company Unit")
    
    # Styling
    background_color = models.CharField(max_length=50, blank=True, default="#f8fafc", verbose_name="Background Color", help_text="e.g. #f8fafc or a Tailwind class")
    background_image = models.ImageField(upload_to="form_backgrounds/", null=True, blank=True, verbose_name="Background Image")
    header_image = models.ImageField(upload_to="form_headers/", null=True, blank=True, verbose_name="Header Image")
    
    success_message = models.TextField(default="Thank you! Your response has been submitted.", verbose_name="Success Message")
    show_on_portal = models.BooleanField(default=False, verbose_name="Show on Client Portal", help_text="Display this form as a card on the Client Portal dashboard.")

    class Meta:
        verbose_name = "Dynamic Form"
        verbose_name_plural = "Dynamic Forms"
        ordering = ['-created_at']

    def __str__(self):
        return self.title


class FormField(AuditableModel):
    """
    Represents a single field/question inside a DynamicForm.
    """
    class FieldTypes(models.TextChoices):
        TEXT = 'text', 'Short Text'
        TEXTAREA = 'textarea', 'Long Text'
        NUMBER = 'number', 'Number'
        EMAIL = 'email', 'Email Address'
        DROPDOWN = 'dropdown', 'Dropdown Select'
        RADIO = 'radio', 'Multiple Choice (Single Answer)'
        CHECKBOX = 'checkbox', 'Checkboxes (Multiple Answers)'
        DATE = 'date', 'Date Picker'
        DATETIME = 'datetime', 'Date & Time Picker'
        SURVEY = 'survey', 'Survey Scale (Linear)'
        ATTACHMENT = 'attachment', 'File Upload (Single)'
        ATTACHMENT_MULTIPLE = 'attachment_multiple', 'File Upload (Multiple)'
        TITLE_DESC = 'title_desc', 'Title & Description'
        PAGE_BREAK = 'page_break', 'Section / Page Break'

    form = models.ForeignKey(DynamicForm, on_delete=models.CASCADE, related_name="fields")
    field_type = models.CharField(max_length=20, choices=FieldTypes.choices, default=FieldTypes.TEXT)
    label = models.CharField(max_length=255, verbose_name="Field Label/Question")
    help_text = models.CharField(max_length=255, blank=True, verbose_name="Help Text")
    is_required = models.BooleanField(default=False, verbose_name="Is Required")
    
    order = models.PositiveIntegerField(default=0, verbose_name="Sort Order")
    
    # Needs to store choices for dropdowns/radios as a list: ["Option A", "Option B"]
    choices = models.JSONField(default=list, blank=True, verbose_name="Choices (for Dropdowns/Radios)")

    # Settings for advanced formatting (e.g. number formatting, currencies)
    settings = models.JSONField(default=dict, blank=True, verbose_name="Field Settings")

    class Meta:
        verbose_name = "Form Field"
        verbose_name_plural = "Form Fields"
        ordering = ['order']

    def __str__(self):
        return f"{self.form.title} - {self.label}"


class FormSubmission(models.Model):
    """
    Stores a user's submitted answers to a DynamicForm.
    Doesn't inherit from AuditableModel to keep it decoupled from staff updating.
    """
    form = models.ForeignKey(DynamicForm, on_delete=models.CASCADE, related_name="submissions")
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL, 
        related_name="form_submissions"
    )
    
    # Store answers as a dictionary: { "field_id_1": "User Answer", "field_id_2": ["Option A", "Option B"] }
    answers = models.JSONField(default=dict)
    
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Form Submission"
        verbose_name_plural = "Form Submissions"
        ordering = ['-submitted_at']

    def __str__(self):
        return f"Submission to {self.form.title} at {self.submitted_at.strftime('%Y-%m-%d %H:%M')}"
# =====================================================================
# OTP Tokens
# =====================================================================
# Login Slide Images
# =====================================================================

class LoginSlideImage(models.Model):
    """
    Ordered slideshow images for the Theme 2 login page left panel.
    Multiple images can be uploaded and reordered via the admin inline.
    """
    site_config = models.ForeignKey(
        SiteConfig,
        on_delete=models.CASCADE,
        related_name="slide_images",
        verbose_name="Site Config",
    )
    image = models.ImageField(
        upload_to="site_config/slides/",
        verbose_name="Image",
        help_text="Recommended portrait/square, min 800×1000px.",
    )
    order = models.PositiveIntegerField(
        default=0,
        verbose_name="Order",
        help_text="Lower numbers appear first.",
    )

    class Meta:
        verbose_name = "Login Slide Image"
        verbose_name_plural = "Login Slide Images"
        ordering = ["order"]

    def __str__(self):
        return f"Slide {self.order} — {self.site_config.site_name}"


# =====================================================================

class OTPToken(models.Model):
    """
    Model to store One-Time Passwords for password resets.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="otp_tokens",
    )
    token = models.CharField(max_length=6, verbose_name="OTP Code")
    created_at = models.DateTimeField(auto_now_add=True)
    is_used = models.BooleanField(default=False)

    class Meta:
        verbose_name = "OTP Token"
        verbose_name_plural = "OTP Tokens"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} - {self.token}"

    def is_valid(self) -> bool:
        """
        Check if the token is valid (not used and within 15 minutes).
        """
        if self.is_used:
            return False

        from datetime import timedelta
        from django.utils import timezone

        expiration_time = self.created_at + timedelta(minutes=15)
        return timezone.now() <= expiration_time


# =====================================================================
# Login Attempt Tracking (brute-force protection)
# =====================================================================

class LoginAttempt(models.Model):
    """
    Persists login attempt records so brute-force counters survive a Redis restart.
    Auto-cleaned by a Celery beat task after 7 days.
    """
    login_username = models.CharField(max_length=150, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    attempted_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Login Attempt"
        verbose_name_plural = "Login Attempts"
        ordering = ["-attempted_at"]

    def __str__(self):
        status = "OK" if self.success else "FAIL"
        return f"{self.login_username} [{status}] @ {self.attempted_at:%Y-%m-%d %H:%M}"


# =====================================================================
# Audit Log (ISO 27001 A.12.3 — Event Logging)
# =====================================================================

class AuditLog(models.Model):
    """
    Immutable security event log.  Records are append-only — no update/delete
    should ever be performed on this table in application code.
    """

    class Action(models.TextChoices):
        LOGIN_SUCCESS    = 'LOGIN_SUCCESS',    'Login — Success'
        LOGIN_FAIL       = 'LOGIN_FAIL',       'Login — Failed'
        LOGOUT           = 'LOGOUT',           'Logout'
        PASSWORD_CHANGE  = 'PASSWORD_CHANGE',  'Password Changed'
        BULK_IMPORT      = 'BULK_IMPORT',      'Bulk User Import'
        EXPORT           = 'EXPORT',           'Data Export'
        ROLE_CHANGE      = 'ROLE_CHANGE',      'Role Changed'
        SMTP_UPDATE      = 'SMTP_UPDATE',      'SMTP/IMAP Config Updated'
        TICKET_CLOSE     = 'TICKET_CLOSE',     'Ticket Closed'
        TICKET_REOPEN    = 'TICKET_REOPEN',    'Ticket Reopened'
        LICENSE_OP       = 'LICENSE_OP',       'License Operation'
        ACCOUNT_REQUEST  = 'ACCOUNT_REQUEST',  'Account Request Submitted'

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_logs",
        help_text="Authenticated user who triggered the event; null for anonymous actions.",
    )
    actor_username = models.CharField(
        max_length=150, blank=True,
        help_text="Snapshot of login_username at event time (survives user deletion).",
    )
    action = models.CharField(max_length=30, choices=Action.choices, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    target_type = models.CharField(
        max_length=100, blank=True,
        help_text="Model label of the affected object, e.g. 'cases.caserecord'.",
    )
    target_id = models.CharField(max_length=100, blank=True)
    details = models.JSONField(
        default=dict, blank=True,
        help_text="Arbitrary key-value context for the event.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action", "created_at"]),
            models.Index(fields=["actor", "created_at"]),
        ]

    def __str__(self):
        who = self.actor_username or "anonymous"
        return f"[{self.created_at:%Y-%m-%d %H:%M}] {self.action} by {who}"

    @classmethod
    def log(
        cls,
        action: str,
        *,
        request=None,
        actor=None,
        ip_address: str = "",
        target=None,
        details: dict = None,
    ) -> "AuditLog":
        """
        Convenience factory.  Pass either ``request`` (preferred) or explicit
        ``actor`` + ``ip_address`` when no request object is available.
        """
        from ipware import get_client_ip

        if request is not None and actor is None:
            actor = request.user if request.user.is_authenticated else None

        if request is not None and not ip_address:
            ip, _ = get_client_ip(request)
            ip_address = ip or ""

        actor_username = ""
        if actor is not None:
            actor_username = getattr(actor, "login_username", "") or getattr(actor, "username", "")

        target_type = ""
        target_id = ""
        if target is not None:
            target_type = f"{target._meta.app_label}.{target._meta.model_name}"
            target_id = str(target.pk)

        return cls.objects.create(
            actor=actor,
            actor_username=actor_username,
            action=action,
            ip_address=ip_address or None,
            target_type=target_type,
            target_id=target_id,
            details=details or {},
        )


# =====================================================================
# User Notification Preferences
# =====================================================================

class UserNotificationPreference(models.Model):
    """
    Per-user notification preferences.

    Each user has one row controlling which channels (email, WhatsApp, Teams)
    they want notifications on, and which event types trigger them.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_pref",
        verbose_name="User",
    )

    # ── Channel toggles ──
    email_enabled = models.BooleanField(
        default=True,
        verbose_name="Email Notifications",
        help_text="Receive notifications via email.",
    )
    whatsapp_enabled = models.BooleanField(
        default=False,
        verbose_name="WhatsApp Notifications",
        help_text="Receive notifications via WhatsApp.",
    )
    teams_enabled = models.BooleanField(
        default=False,
        verbose_name="Teams Notifications",
        help_text="Receive notifications via Microsoft Teams webhook.",
    )

    # ── Contact overrides ──
    whatsapp_number = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name="WhatsApp Number",
        help_text="E.164 format, e.g. +6281234567890. If blank, uses phone_number from profile.",
    )
    teams_webhook_url = models.URLField(
        max_length=1000,
        blank=True,
        default="",
        verbose_name="Teams Webhook URL",
        help_text="Personal Incoming Webhook URL from your Teams channel.",
    )

    # ── Event toggles ──
    on_new_message = models.BooleanField(
        default=True,
        verbose_name="New chat message",
        help_text="Someone replies in a ticket you own or follow.",
    )
    on_mention = models.BooleanField(
        default=True,
        verbose_name="@Mention",
        help_text="Someone @mentions you in a chat.",
    )
    on_status_change = models.BooleanField(
        default=True,
        verbose_name="Status change",
        help_text="A ticket you own or follow changes status.",
    )
    on_follower_added = models.BooleanField(
        default=True,
        verbose_name="Added as follower",
        help_text="You are added as a follower to a ticket.",
    )

    # ── Quiet hours ──
    quiet_start = models.TimeField(
        null=True,
        blank=True,
        verbose_name="Quiet hours start",
        help_text="Notifications are paused from this time. e.g. 22:00",
    )
    quiet_end = models.TimeField(
        null=True,
        blank=True,
        verbose_name="Quiet hours end",
        help_text="Notifications resume at this time. e.g. 07:00",
    )

    class Meta:
        verbose_name = "User Notification Preference"
        verbose_name_plural = "User Notification Preferences"

    def __str__(self):
        channels = []
        if self.email_enabled:
            channels.append("Email")
        if self.whatsapp_enabled:
            channels.append("WA")
        if self.teams_enabled:
            channels.append("Teams")
        return f"{self.user} — {', '.join(channels) or 'None'}"

    def get_whatsapp_destination(self):
        """Return the WhatsApp number to use (preference override or profile phone)."""
        if self.whatsapp_number:
            return self.whatsapp_number
        return getattr(self.user, "phone_number", "") or ""

    def is_in_quiet_hours(self):
        """Check if current time falls within the user's quiet hours window."""
        if not self.quiet_start or not self.quiet_end:
            return False
        from django.utils.timezone import localtime
        import pytz
        try:
            user_tz = pytz.timezone(self.user.timezone or "Asia/Jakarta")
        except Exception:
            user_tz = pytz.timezone("Asia/Jakarta")
        now = localtime(timezone=user_tz).time()
        if self.quiet_start <= self.quiet_end:
            # e.g. 09:00 – 17:00 (same day)
            return self.quiet_start <= now <= self.quiet_end
        else:
            # e.g. 22:00 – 07:00 (overnight)
            return now >= self.quiet_start or now <= self.quiet_end
