"""
Core App — Models
==================
Provides the foundational models for the entire RoC Desk system:

- ``AuditableModel``  — abstract base with UUID pk, timestamps, and audit FKs.
- ``User``            — custom user model with login_username, NIK, role_access.
- ``CompanyUnit``     — organisational entity (e.g. a subsidiary or business unit).
- ``Employee``        — internal staff / end-user who submits or receives cases.
"""
import re
import uuid

# ─────────────────────────────────────────────────────────────────────────────
# Module Access Registry
# Maps every configurable sidebar module to a slug, label, emoji, and section.
# ─────────────────────────────────────────────────────────────────────────────

MODULE_REGISTRY = [
    # (slug,                  label,                   emoji,  section)
    ("dashboard",            "Dashboard",             "📊",  "Workspace"),
    ("task_management",      "Task Management",       "📋",  "Workspace"),
    ("eform_management",     "e-Form Management",     "🔄",  "Workspace"),
    ("projects",             "Projects",              "📈",  "Workspace"),
    ("knowledge_management", "Knowledge Management",  "📑",  "Content & Docs"),
    ("esign",                "E-Sign",                "✒️",  "Content & Docs"),
    ("form_creator",         "Form Creator",          "📝",  "Content & Docs"),
    ("shared_docs",          "Shared Docs",           "📂",  "Content & Docs"),
    ("shorten_link",         "Shorten Link",          "🔗",  "Content & Docs"),
    ("announcement_management", "Announcements",     "📣",  "Content & Docs"),
    ("my_approvals",         "My Approvals",          "✅",  "My Workspace"),
    ("my_signatures",        "My Signatures",         "✍️",  "My Workspace"),
    ("my_eforms",            "My e-Forms",            "📝",  "My Workspace"),
    ("my_tickets",           "My Tickets",            "🎟️", "My Workspace"),
    ("app_portal",           "App Portal",            "🚀",  "Portal"),
]

# Full set of modules available to non-PortalUser roles (mirrors base.html nav)
_STAFF_MODULES = {
    "dashboard", "task_management", "eform_management", "projects",
    "knowledge_management", "esign", "form_creator", "shared_docs", "shorten_link",
    "my_approvals", "my_signatures", "my_eforms",
}

_ENGINEER_MODULES = {
    "dashboard", "task_management",
}

ROLE_DEFAULT_MODULES = {
    "SuperAdmin":  _STAFF_MODULES,
    "Manager":     _STAFF_MODULES,
    "SupportDesk": _STAFF_MODULES,
    "Auditor":     _STAFF_MODULES,
    "Engineer":    _ENGINEER_MODULES,
    "PortalUser":  {"my_tickets", "my_eforms", "app_portal"},
}

DEFAULT_PORTAL_USER_MODULES = ["my_tickets", "my_eforms", "app_portal"]

# Capabilities a User Role can grant. The code only ever asks "does this user have
# <permission>?" — which roles hold which permission is data (UserRole.permissions).
# A new feature adds its permission codes here, as (code, label, group).
PERMISSION_REGISTRY = []
ALL_PERMISSIONS = "*"


def permission_codes():
    return [code for code, _label, _group in PERMISSION_REGISTRY]

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import FileExtensionValidator, RegexValidator
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


def default_portal_user_modules():
    return list(DEFAULT_PORTAL_USER_MODULES)


def default_role_module_defaults():
    return {
        role: sorted(modules)
        for role, modules in ROLE_DEFAULT_MODULES.items()
    }


def _valid_module_slugs():
    return {slug for slug, _label, _emoji, _section in MODULE_REGISTRY}


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
        ENGINEER = "Engineer", "Engineer"
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
    user_role = models.ForeignKey(
        "core.UserRole",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="users",
        verbose_name="User Role",
        help_text="Role from the User Roles master data. Sets the base access level and permissions.",
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

    module_access = models.JSONField(
        null=True,
        blank=True,
        default=None,
        verbose_name="Module Access",
        help_text=(
            "Extra module slugs on top of the role's Module Defaults. "
            "Leave null to inherit role defaults only."
        ),
    )

    esign_tnc_accepted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="E-Sign Terms & Conditions Accepted At",
        help_text="When the user last accepted the E-Sign Terms & Conditions. Re-confirmed every 12 months.",
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

    ESIGN_TNC_VALIDITY_DAYS = 365

    @property
    def esign_tnc_is_valid(self):
        """Whether the user's E-Sign Terms & Conditions acceptance is still within the 1-year validity window."""
        if not self.esign_tnc_accepted_at:
            return False
        from django.utils import timezone
        import datetime
        return timezone.now() - self.esign_tnc_accepted_at < datetime.timedelta(days=self.ESIGN_TNC_VALIDITY_DAYS)

    # ── User Role (master data) ──
    def save(self, *args, **kwargs):
        self._sync_role()
        super().save(*args, **kwargs)

    def _sync_role(self):
        """Keep ``role_access`` (the base tier most checks still read) and ``user_role`` consistent.

        A custom role sets the tier from its ``base_access``; a system role always matches
        ``role_access``, so code that changes ``role_access`` directly moves the user to the
        matching system role.
        """
        role = self.user_role if self.user_role_id else None
        if role is None or role.is_system:
            if role is None or role.code != self.role_access:
                self.user_role = UserRole.objects.filter(code=self.role_access, is_system=True).first()
        else:
            self.role_access = role.base_access

    @property
    def effective_role(self):
        """The user's User Role (falls back to the system role of ``role_access``)."""
        if not hasattr(self, "_effective_role"):
            role = self.user_role if self.user_role_id else None
            if role is None:
                role = UserRole.objects.filter(code=self.role_access, is_system=True).first()
            self._effective_role = role
        return self._effective_role

    def has_capability(self, code):
        """Whether the user's User Role grants permission ``code`` (see PERMISSION_REGISTRY)."""
        if not self.is_authenticated or not self.is_active:
            return False
        role = self.effective_role
        return bool(role and role.is_active and role.grants(code))

    @property
    def role_label(self):
        role = self.effective_role
        return role.name if role else self.get_role_access_display()

    def get_accessible_modules(self):
        role_mods = SiteConfig.get_role_modules(self.role_access)
        role = self.user_role if self.user_role_id else None
        if role is not None and role.code != self.role_access:
            # A custom role adds its own module defaults on top of its base tier's.
            role_mods = role_mods | SiteConfig.get_role_modules(role.code)
        if self.module_access is None:
            return role_mods
        if not isinstance(self.module_access, list):
            return role_mods
        override = {str(m).strip() for m in self.module_access if str(m).strip()}
        return override | role_mods


class UserRole(AuditableModel):
    """User Role master data: what a role is called, which base tier it has, and which
    permissions (PERMISSION_REGISTRY) it grants. Module defaults per role live in
    SiteConfig.role_module_defaults, keyed by ``code``.

    The six built-in roles exist as system rows (code = ``User.RoleAccess`` value) so they
    can be given permissions too; they can't be renamed into another tier or deleted.
    """

    code = models.CharField(
        max_length=50,
        unique=True,
        validators=[RegexValidator(r"^[A-Za-z][A-Za-z0-9_]*$", "Letters, digits and underscores; start with a letter.")],
        verbose_name="Code",
        help_text="Stable key, e.g. finance_staff. Cannot be changed later.",
    )
    name = models.CharField(max_length=100, unique=True, verbose_name="Role Name")
    description = models.TextField(blank=True, default="", verbose_name="Description")
    base_access = models.CharField(
        max_length=20,
        choices=User.RoleAccess.choices,
        default=User.RoleAccess.PORTALUSER,
        verbose_name="Base Access Level",
        help_text="Built-in level this role behaves as in modules that do not use permissions yet "
                  "(Portal User = portal only; the others open the Support Desk).",
    )
    permissions = models.JSONField(default=list, blank=True, verbose_name="Permissions")
    is_system = models.BooleanField(default=False, editable=False, verbose_name="System Role")
    is_active = models.BooleanField(default=True, verbose_name="Active")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Display Order")

    class Meta:
        verbose_name = "User Role"
        verbose_name_plural = "User Roles"
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name

    def grants(self, code):
        perms = self.permissions if isinstance(self.permissions, list) else []
        return ALL_PERMISSIONS in perms or code in perms


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
    is_archived = models.BooleanField(default=False, db_index=True, verbose_name="Archived")

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


def normalize_phone_e164(raw):
    """
    Best-effort normalization of common Indonesian local formats to E.164,
    e.g. '0812xxxxxxxx' -> '+62812xxxxxxxx'. Used consistently wherever a
    visitor-typed phone number is matched against or saved to Employee.
    """
    if not raw:
        return ""
    digits = re.sub(r"[\s\-()]", "", raw.strip())
    if digits.startswith("0"):
        digits = "+62" + digits[1:]
    elif digits.startswith("62"):
        digits = "+" + digits
    elif not digits.startswith("+"):
        digits = "+" + digits
    return digits


class Employee(AuditableModel):
    """
    Internal employee / end-user who interacts with the service desk.

    The ``phone_number`` is stored in E.164 format so it can be matched
    directly against WhatsApp sender IDs from Evolution API webhooks.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profile",
        verbose_name="Linked User",
        help_text="Optional staff/portal user account. Matching uses this user's NIK.",
    )
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
    reports_to = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subordinates",
        verbose_name="Reports To (Manager)",
        help_text="The direct manager or supervisor of this employee."
    )
    is_archived = models.BooleanField(default=False, db_index=True, verbose_name="Archived")

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

    @classmethod
    def find_by_contact(cls, email=None, phone=None):
        """Return the first Employee matching either email or phone_number, or None."""
        lookup = models.Q()
        if email:
            lookup |= models.Q(email__iexact=email)
        if phone:
            lookup |= models.Q(phone_number=phone)
        if not lookup:
            return None
        return cls.objects.select_related("unit").filter(lookup).first()

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
    site_url = models.URLField(
        blank=True,
        default="",
        verbose_name="Base Site URL",
        help_text="The main public URL of this application (e.g. https://finance.santika.com). Used for generating links in emails and background tasks. Leave blank to fallback to the .env setting."
    )
    marketplace_url = models.URLField(
        blank=True,
        default="http://localhost:5000",
        verbose_name="Marketplace Server URL",
        help_text="Base URL of the Estalatree licensing server for verification."
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
        verbose_name="System Logo — Dark Background",
        help_text="Used wherever the logo sits on a dark background — sidebar/navbar, dark login panels, etc. Falls back to the light-background logo below if left blank."
    )
    logo_light = models.ImageField(
        upload_to="site_config/",
        null=True,
        blank=True,
        verbose_name="System Logo — Light Background",
        help_text="Used wherever the logo sits on a light/white background — login form panel, register, forgot password, and other auth screens. Falls back to the dark-background logo above if left blank."
    )
    company_logo = models.ImageField(
        upload_to="site_config/",
        null=True,
        blank=True,
        verbose_name="Company Logo",
        help_text="Logo printed on generated documents — e-Signature stamps and case letterheads (kop surat). Falls back to System Logo if left blank."
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
    esign_allow_external_signers = models.BooleanField(
        default=True,
        verbose_name="Allow External Email Signers (e-Sign)",
        help_text=(
            "If disabled, staff can only add Internal (System) Users as e-Signature signers — "
            "the External Email option (public magic-link, no login) is hidden and blocked."
        ),
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
    login_video = models.FileField(
        upload_to="site_config/",
        null=True,
        blank=True,
        validators=[FileExtensionValidator(allowed_extensions=["mp4", "webm"])],
        verbose_name="Login Page Video (Theme 2)",
        help_text=(
            "Optional looping background video for Theme 2's left panel — takes priority over "
            "the image/slideshow above when set. Keep it short (3–8s), muted (no audio track), "
            "H.264 MP4 or WebM, 720p is plenty since it's stretched to fill the panel. "
            "Under ~5 MB is recommended; it's only loaded on large screens (hidden on mobile)."
        ),
    )
    contact_info = models.TextField(
        blank=True,
        default="",
        verbose_name="Contact Info (Narahubung)",
        help_text="Contact details (narahubung) shown on the login page footer. Supports plain text or simple HTML.",
    )
    developer_contact_name = models.CharField(
        max_length=100,
        blank=True,
        default="IT Support",
        verbose_name="Developer Contact Name",
        help_text="Display name shown under Help & About → Contact Developer.",
    )
    developer_contact_email = models.EmailField(
        blank=True,
        default="",
        verbose_name="Developer Contact Email",
        help_text="Email shown under Help & About → Contact Developer, and the recipient for Submit Feedback. "
                  "Leave blank to list the active SuperAdmins instead and only store feedback.",
    )

    # --- Behaviour toggles ---
    require_ticket_location = models.BooleanField(
        default=False,
        verbose_name="Require Location on New Tickets",
        help_text="If enabled, the requester must pick a Ticket Location when submitting a new ticket.",
    )
    show_generate_template_button = models.BooleanField(
        default=True,
        verbose_name="Show 'Generate Template' Button",
        help_text="Show the Generate Template button on the new-ticket form.",
    )
    allow_whatsapp_notifications = models.BooleanField(
        default=True,
        verbose_name="Allow WhatsApp Notifications in My Profile",
        help_text="Let users opt in to personal WhatsApp notifications from My Profile (requires the WhatsApp gateway).",
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
    wa_inbound_enabled = models.BooleanField(
        default=True,
        verbose_name="Enable Inbound (Create Ticket)",
        help_text="If enabled, incoming WhatsApp messages will create new tickets or append to active sessions."
    )
    wa_outbound_enabled = models.BooleanField(
        default=True,
        verbose_name="Enable Outbound (Reply/Escalate)",
        help_text="If enabled, staff can send replies via WhatsApp and system can send outbound notifications."
    )
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

    # --- WhatsApp Rate Limiter Config ---
    wa_rate_recipient_cooldown = models.PositiveSmallIntegerField(
        default=10,
        verbose_name="WA Recipient Cooldown (s)",
        help_text=(
            "Minimum seconds between two messages to the same phone number. "
            "Recommended: 10. Values below 5 risk triggering spam detection."
        ),
    )
    wa_rate_burst_max = models.PositiveSmallIntegerField(
        default=8,
        verbose_name="WA Burst Limit (per 60s)",
        help_text=(
            "Maximum outbound messages in any rolling 60-second window. "
            "Recommended: 8. Values above 12 are risky."
        ),
    )
    wa_rate_minute_max = models.PositiveSmallIntegerField(
        default=15,
        verbose_name="WA Per-Minute Limit",
        help_text=(
            "Maximum outbound messages per minute across all recipients. "
            "Recommended: 15. Values above 20 are risky."
        ),
    )
    wa_rate_hour_max = models.PositiveIntegerField(
        default=200,
        verbose_name="WA Per-Hour Limit",
        help_text=(
            "Maximum outbound messages per hour. "
            "Recommended: 200. Values above 350 are risky."
        ),
    )

    # --- Circuit Breaker Config ---
    wa_cb_max_disconnects = models.PositiveSmallIntegerField(
        default=2,
        verbose_name="WA CB Max Disconnects",
        help_text=(
            "Number of disconnects within 24 hours before the circuit breaker trips. "
            "Recommended: 2. Higher values are too lenient and allow continued sends during instability."
        ),
    )
    wa_cb_block_hours = models.PositiveSmallIntegerField(
        default=4,
        verbose_name="WA CB Block Duration (hours)",
        help_text=(
            "Hours all outbound sends stay blocked after the circuit breaker trips. "
            "Recommended: 4. Values below 2 provide insufficient recovery time."
        ),
    )

    # --- Human Pacing Config ---
    wa_read_pause_min_ms = models.PositiveSmallIntegerField(
        default=1000,
        verbose_name="WA Read Pause Min (ms)",
        help_text=(
            "Minimum milliseconds to pause before starting to type a reply. "
            "Simulates reading the incoming message. Recommended: 1000."
        ),
    )
    wa_read_pause_max_ms = models.PositiveSmallIntegerField(
        default=3500,
        verbose_name="WA Read Pause Max (ms)",
        help_text=(
            "Maximum milliseconds to pause before starting to type a reply. "
            "Recommended: 3500. Values below 1500 look bot-like."
        ),
    )
    wa_typing_speed_cps = models.PositiveSmallIntegerField(
        default=25,
        verbose_name="WA Typing Speed (chars/s)",
        help_text=(
            "Simulated typing speed in characters per second used to calculate composing duration. "
            "Recommended: 25. Values above 40 exceed human typing speed."
        ),
    )

    # --- Opt-in Guard Config ---
    wa_opt_in_ttl_days = models.PositiveSmallIntegerField(
        default=7,
        verbose_name="WA Opt-in TTL (days)",
        help_text=(
            "Days a contact is considered opted-in after their last inbound message. "
            "Internal broadcast is skipped for contacts outside this window. "
            "Recommended: 7. Below 3 is too restrictive; above 30 risks messaging stale contacts."
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
    portal_user_modules = models.JSONField(
        default=default_portal_user_modules,
        blank=True,
        verbose_name="Portal User Modules",
        help_text=(
            "Legacy field — kept in sync with role_module_defaults['PortalUser']. "
            "Prefer Module Defaults (role_module_defaults)."
        ),
    )
    role_module_defaults = models.JSONField(
        default=default_role_module_defaults,
        blank=True,
        verbose_name="Role Module Defaults",
        help_text=(
            "Default module slugs per role. Users without a per-account override "
            "inherit the list for their role_access."
        ),
    )

    # --- Content Security Policy (CSP) Config ---
    csp_allowed_frame_hosts = models.TextField(
        blank=True,
        default="https://www.youtube.com",
        verbose_name="Allowed Embed Domains (CSP frame-src)",
        help_text=(
            "Extra domains allowed to be embedded via &lt;iframe&gt; (e.g. Canva, Figma), "
            "one origin per line or space-separated, e.g. https://www.youtube.com. "
            "Appended to the site's frame-src Content-Security-Policy directive."
        ),
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

    @property
    def logo_for_dark_bg(self):
        """Logo to use wherever it sits on a dark background — the
        dark-background upload, falling back to the light-background one
        if only that was uploaded."""
        return self.logo or self.logo_light

    @property
    def logo_for_light_bg(self):
        """Logo to use wherever it sits on a light/white background — the
        light-background upload, falling back to the dark-background one
        if only that was uploaded."""
        return self.logo_light or self.logo

    @classmethod
    def get_role_modules(cls, role: str) -> set:
        role = (role or "").strip()
        valid = _valid_module_slugs()
        builtin = set(ROLE_DEFAULT_MODULES.get(role, set()))
        try:
            config = cls.get_solo()
            data = config.role_module_defaults
            if isinstance(data, dict) and data:
                if role in data and isinstance(data.get(role), list):
                    return {
                        str(m).strip()
                        for m in data[role]
                        if str(m).strip() in valid
                    }
                if role == "PortalUser":
                    legacy = config.portal_user_modules
                    if isinstance(legacy, list):
                        return {
                            str(m).strip()
                            for m in legacy
                            if str(m).strip() in valid
                        }
                return builtin
            if role == "PortalUser":
                legacy = config.portal_user_modules
                if isinstance(legacy, list) and legacy:
                    result = {str(m).strip() for m in legacy if str(m).strip() in valid}
                    if result:
                        return result
        except Exception:
            pass
        return builtin

    @classmethod
    def get_portal_user_modules(cls) -> set:
        return cls.get_role_modules("PortalUser")

    def sync_portal_user_modules_from_role_defaults(self):
        data = self.role_module_defaults if isinstance(self.role_module_defaults, dict) else {}
        portal = data.get("PortalUser")
        if isinstance(portal, list):
            self.portal_user_modules = list(portal)
        elif not self.portal_user_modules:
            self.portal_user_modules = default_portal_user_modules()

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

    JOB_ROLE_MODE_CHOICES = [
        ('freetext', 'Free Text'),
        ('master',   'Master Data'),
    ]
    job_role_mode = models.CharField(
        max_length=10,
        choices=JOB_ROLE_MODE_CHOICES,
        default='freetext',
        verbose_name="Job Role Input Mode",
        help_text=(
            "Free Text: users type their job role freely. "
            "Master Data: users pick from the Job Roles master list."
        ),
    )

    def __str__(self):
        return self.site_name


# =====================================================================
# Job Role Master Data
# =====================================================================

class JobRole(models.Model):
    name = models.CharField(max_length=150, unique=True, verbose_name="Job Role Name")
    is_active = models.BooleanField(default=True, verbose_name="Active")
    order = models.PositiveIntegerField(default=0, verbose_name="Display Order")

    class Meta:
        ordering = ['order', 'name']
        verbose_name = "Job Role"
        verbose_name_plural = "Job Roles"

    def __str__(self):
        return self.name


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

    def _readable_secret(self, attr: str) -> str:
        """Read an encrypted field without raising when the key has rotated."""
        try:
            return getattr(self, attr) or ""
        except Exception:
            return ""

    def resolved_microsoft_client_id(self) -> str:
        return (self.microsoft_client_id or "").strip()

    def resolved_microsoft_client_secret(self) -> str:
        return self._readable_secret("microsoft_client_secret")

    def microsoft_secret_configured(self) -> bool:
        return bool(self.resolved_microsoft_client_secret())

    def microsoft_credentials_configured(self) -> bool:
        return bool(self.resolved_microsoft_client_id() and self.resolved_microsoft_client_secret())

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
                        "secret": self.resolved_microsoft_client_secret(),
                        "key": "",
                    },
                    "AUTH_PARAMS": {"prompt": "select_account"},
                }
            if self.google_enabled and self.google_client_id:
                google_cfg = {
                    "APP": {
                        "client_id": self.google_client_id,
                        "secret": self._readable_secret("google_client_secret"),
                        "key": "",
                    },
                    "SCOPE": ["profile", "email"],
                    "AUTH_PARAMS": {"access_type": "online", "prompt": "select_account"},
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

    # --- PERSONAL: one Workflows flow that DMs each user via the Flow bot ---
    # The app POSTs {"recipient": "<user email>", "attachments": [<card>]} and
    # the flow posts the card in that user's chat with the Workflows bot.
    personal_webhook_url = EncryptedCharField(
        max_length=1000, blank=True, null=True,
        verbose_name="Personal Notifications Workflow URL",
        help_text="Workflow: 'When a Teams webhook request is received' → "
                  "'Post card in a chat or channel' (Flow bot → Chat with Flow bot, recipient from the request)",
    )
    is_personal_enabled = models.BooleanField(
        default=False,
        verbose_name="Enable Personal Teams Notifications",
    )
    personal_allowed_domains = models.CharField(
        max_length=500, blank=True, default="",
        verbose_name="Microsoft 365 Email Domains",
        help_text="Comma-separated, e.g. company.com. Only users with these email domains get "
                  "personal Teams notifications (others aren't in your Teams tenant). Blank = everyone.",
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

    # Teams channel: ticket activity after creation (gateways/teams_notify.py).
    # Each needs TeamsConfig.is_notification_enabled + a webhook URL as well.
    teams_on_user_reply = models.BooleanField(
        default=True,
        verbose_name="Teams: requester replied",
        help_text="Post to Teams when a requester replies (portal, WhatsApp, or email).",
    )
    teams_on_staff_reply = models.BooleanField(
        default=True,
        verbose_name="Teams: support desk replied",
        help_text="Post to Teams when a support agent replies to the requester.",
    )
    teams_on_status_change = models.BooleanField(
        default=True,
        verbose_name="Teams: status changed",
        help_text="Post to Teams when a ticket changes status (includes the progress note).",
    )
    teams_on_internal_mention = models.BooleanField(
        default=True,
        verbose_name="Teams: @mention in Internal Chat",
        help_text="Post to Teams when someone is @mentioned in a ticket's internal chat.",
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
    is_archived = models.BooleanField(default=False, db_index=True, verbose_name="Archived")
    collaborators = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="collaborating_forms",
        verbose_name="Collaborators",
        help_text="Other staff who can view and edit this form in the builder, alongside the creator.",
    )

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
        ANNOUNCEMENT_PUBLISH = 'ANNOUNCEMENT_PUBLISH', 'Announcement Published'

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
        default=True,
        verbose_name="Teams Notifications",
        help_text="Receive personal notifications in Microsoft Teams (chat with the Workflows bot).",
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
