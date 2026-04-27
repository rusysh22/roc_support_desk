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
        NIK             — Nomor Induk Karyawan (employee ID), unique.
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
    Organisational unit within the company.

    Examples: IT, FIN, HR, OPS.
    """

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
        verbose_name = "Company Unit"
        verbose_name_plural = "Company Units"
        ordering = ["code"]

    def __str__(self):
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

    def __str__(self):
        return self.site_name


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
