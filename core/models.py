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

    def __str__(self):
        return f"{self.full_name} ({self.unit.code})"
