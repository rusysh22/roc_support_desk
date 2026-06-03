"""
E-Signature App — Models
========================
Provides models for the standalone e-signature/approval workflow:

- SignatureDocument  — the uploaded PDF and its overall signing state.
- Signer            — one participant (system user OR external) with a magic-link token.
- SignaturePlacement — iLovePDF-style drag boxes defining where each signer signs.
- SignatureFlowTemplate / SignatureFlowStep — reusable signing-order presets.
- SignatureEvent     — append-only audit log for every state transition.
"""
import hashlib
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import AuditableModel


# ---------------------------------------------------------------------------
# Upload path helpers
# ---------------------------------------------------------------------------

def _original_pdf_path(instance, filename):
    return f"esign/originals/{instance.id}/{filename}"


def _signed_pdf_path(instance, filename):
    return f"esign/signed/{instance.id}/{filename}"


def _preview_pdf_path(instance, filename):
    return f"esign/preview/{instance.id}/{filename}"


def _signature_image_path(instance, filename):
    return f"esign/signatures/{instance.document_id}/{instance.id}/{filename}"


# ---------------------------------------------------------------------------
# SignatureDocument
# ---------------------------------------------------------------------------

def generate_document_code():
    return f"ESIGN-{uuid.uuid4().hex[:8].upper()}"

class SignatureDocument(AuditableModel):
    """Uploaded PDF file that will be routed through a signing workflow."""

    class RoutingMode(models.TextChoices):
        SEQUENTIAL = "sequential", "Sequential (one by one)"
        PARALLEL   = "parallel",   "Parallel (all at once)"

    class Status(models.TextChoices):
        DRAFT     = "draft",     "Draft"
        PENDING   = "pending",   "Pending Signatures"
        COMPLETED = "completed", "Completed"
        REJECTED  = "rejected",  "Rejected"
        CANCELLED = "cancelled", "Cancelled"

    document_code = models.CharField(
        max_length=20,
        unique=True,
        default=generate_document_code,
        verbose_name="Document Code",
        help_text="Unique sequence/hash identifier for this document."
    )
    title        = models.CharField(max_length=255, verbose_name="Document Title")
    original_pdf = models.FileField(
        upload_to=_original_pdf_path,
        max_length=500,
        verbose_name="Original PDF",
    )
    signed_pdf = models.FileField(
        upload_to=_signed_pdf_path,
        max_length=500,
        blank=True,
        null=True,
        verbose_name="Signed PDF",
    )
    preview_pdf = models.FileField(
        upload_to=_preview_pdf_path,
        max_length=500,
        blank=True,
        null=True,
        verbose_name="Preview PDF",
        help_text="Intermediate signed PDF generated after each signature for in-progress viewing.",
    )
    message = models.TextField(
        blank=True,
        verbose_name="Message to Signers",
        help_text="Optional note sent to all signers with the signing request.",
    )
    routing_mode = models.CharField(
        max_length=20,
        choices=RoutingMode.choices,
        default=RoutingMode.SEQUENTIAL,
        verbose_name="Routing Mode",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    page_count = models.PositiveIntegerField(default=1, verbose_name="Page Count")
    due_at = models.DateTimeField(null=True, blank=True, verbose_name="Due Date")
    document_hash = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Document SHA-256",
        help_text="SHA-256 hash of the original PDF bytes for tamper-evidence.",
    )


    class Meta:
        verbose_name = "Signature Document"
        verbose_name_plural = "Signature Documents"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} [{self.status}]"

    def compute_hash(self):
        """Compute and store the SHA-256 hash of the uploaded original PDF."""
        self.original_pdf.seek(0)
        self.document_hash = hashlib.sha256(self.original_pdf.read()).hexdigest()
        self.original_pdf.seek(0)

    @property
    def signed_count(self):
        return self.signers.filter(status=Signer.Status.SIGNED).count()

    @property
    def total_count(self):
        return self.signers.count()

    @property
    def is_complete(self):
        return (
            self.signers.exists()
            and not self.signers.filter(
                status__in=[Signer.Status.WAITING, Signer.Status.PENDING]
            ).exists()
        )

    @property
    def current_order(self):
        """Lowest order among PENDING signers (used for sequential routing)."""
        first = self.signers.filter(status=Signer.Status.PENDING).order_by("order").first()
        return first.order if first else None


# ---------------------------------------------------------------------------
# Signer
# ---------------------------------------------------------------------------

class Signer(AuditableModel):
    """
    One participant in the signing workflow.

    Identity is either a system User (FK) OR an external person identified by
    name + email — exactly one must be provided (enforced in clean()).

    Token provides magic-link access without login for external signers.
    """

    class Role(models.TextChoices):
        SIGNER   = "signer",   "Signer"
        APPROVER = "approver", "Approver"

    class Status(models.TextChoices):
        WAITING = "waiting", "Waiting (not yet their turn)"
        PENDING = "pending", "Pending (action required)"
        SIGNED  = "signed",  "Signed"
        REJECTED = "rejected", "Rejected"

    document = models.ForeignKey(
        SignatureDocument,
        on_delete=models.CASCADE,
        related_name="signers",
        verbose_name="Document",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="esign_signers",
        verbose_name="System User",
        help_text="Leave blank for external signers.",
    )
    external_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="External Name",
        help_text="Full name for signers who do not have a system account.",
    )
    external_email = models.EmailField(
        blank=True,
        verbose_name="External Email",
        help_text="Email address for signers who do not have a system account.",
    )
    order = models.PositiveIntegerField(
        default=1,
        verbose_name="Signing Order",
        help_text="Order in which this signer acts (for sequential routing).",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.SIGNER,
        verbose_name="Role",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Notes",
        help_text="Rejection reason or optional comment left by the signer.",
    )
    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        verbose_name="Access Token",
        help_text="Magic-link token for signer access without login.",
    )
    token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Token Expires At",
    )
    acted_at = models.DateTimeField(null=True, blank=True, verbose_name="Acted At")
    signature_image = models.ImageField(
        upload_to=_signature_image_path,
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Signature Image",
    )
    signed_ip = models.GenericIPAddressField(null=True, blank=True, verbose_name="Signed From IP")
    signed_user_agent = models.TextField(blank=True, verbose_name="Signed User Agent")

    verify_code = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Verification Code",
        help_text="One-time code e-mailed to external signers; required alongside their email to access the signing page.",
    )

    # Stamp annotation preferences — chosen by the signer at signing time
    stamp_timestamp = models.BooleanField(default=True, verbose_name="Include Timestamp on PDF")
    stamp_name = models.BooleanField(default=True, verbose_name="Include Name on PDF")
    stamp_job_role = models.BooleanField(default=False, verbose_name="Include Job Role on PDF")
    stamp_job_role_text = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="Job Role Text",
        help_text="The job role/title the signer chose to include on the PDF.",
    )
    stamp_use_logo = models.BooleanField(default=True, verbose_name="Use Company Logo")

    class Meta:
        verbose_name = "Signer"
        verbose_name_plural = "Signers"
        ordering = ["document", "order"]
        constraints = [
            # A system user can appear only once per document
            models.UniqueConstraint(
                fields=["document", "user"],
                condition=models.Q(user__isnull=False),
                name="esign_unique_signer_per_document",
            ),
        ]

    def __str__(self):
        identity = self.display_name
        return f"{self.document.title} — {identity} [{self.status}]"

    def clean(self):
        has_user     = bool(self.user_id)
        has_external = bool(self.external_email)
        if has_user and has_external:
            raise ValidationError(
                "A signer must be either a system user or an external contact — not both."
            )
        if not has_user and not has_external:
            raise ValidationError(
                "A signer must have either a system user or an external email."
            )
        if has_external and not self.external_name:
            raise ValidationError("External name is required when using an external email.")

    @property
    def display_name(self):
        if self.user_id:
            return self.user.get_full_name() or self.user.username
        return self.external_name

    @property
    def email(self):
        if self.user_id:
            return self.user.email
        return self.external_email

    @property
    def is_token_expired(self):
        if not self.token_expires_at:
            return False
        return timezone.now() > self.token_expires_at


# ---------------------------------------------------------------------------
# SignaturePlacement
# ---------------------------------------------------------------------------

class SignaturePlacement(AuditableModel):
    """
    Defines where on the PDF a signer's signature will be stamped.

    Coordinates (x, y, width, height) are stored as fractions 0.0–1.0 of
    the page dimensions, making them resolution-independent. The stamping
    utility converts them to absolute PDF points using the page mediabox.
    PDF coordinate origin is bottom-left; the UI origin is top-left — the
    stamping utility handles the y-axis flip.
    """

    class FieldType(models.TextChoices):
        SIGNATURE = "signature", "Signature"
        INITIAL   = "initial",   "Initial"
        DATE      = "date",      "Date"
        NAME      = "name",      "Name"

    document    = models.ForeignKey(
        SignatureDocument,
        on_delete=models.CASCADE,
        related_name="placements",
        verbose_name="Document",
    )
    signer = models.ForeignKey(
        Signer,
        on_delete=models.CASCADE,
        related_name="placements",
        verbose_name="Signer",
    )
    field_type = models.CharField(
        max_length=20,
        choices=FieldType.choices,
        default=FieldType.SIGNATURE,
        verbose_name="Field Type",
    )
    page_number = models.PositiveIntegerField(
        default=1,
        verbose_name="Page Number",
        help_text="1-based page number on the PDF.",
    )
    # Normalized coordinates (0.0 – 1.0 relative to page dimensions)
    x      = models.FloatField(verbose_name="X (normalized)")
    y      = models.FloatField(verbose_name="Y (normalized)")
    width  = models.FloatField(verbose_name="Width (normalized)")
    height = models.FloatField(verbose_name="Height (normalized)")
    required = models.BooleanField(default=True, verbose_name="Required")

    class Meta:
        verbose_name = "Signature Placement"
        verbose_name_plural = "Signature Placements"
        ordering = ["document", "page_number", "signer__order"]

    def __str__(self):
        return (
            f"{self.document.title} — {self.signer.display_name} "
            f"p.{self.page_number} ({self.field_type})"
        )


# ---------------------------------------------------------------------------
# SignatureFlowTemplate / SignatureFlowStep
# ---------------------------------------------------------------------------

class SignatureFlowTemplate(models.Model):
    """Reusable signing-order preset that can be applied when creating a document."""

    class RoutingMode(models.TextChoices):
        SEQUENTIAL = "sequential", "Sequential"
        PARALLEL   = "parallel",   "Parallel"

    name         = models.CharField(max_length=150, unique=True, verbose_name="Template Name")
    routing_mode = models.CharField(
        max_length=20,
        choices=RoutingMode.choices,
        default=RoutingMode.SEQUENTIAL,
        verbose_name="Routing Mode",
    )
    is_active = models.BooleanField(default=True, verbose_name="Active")

    class Meta:
        verbose_name = "Signing Flow Template"
        verbose_name_plural = "Signing Flow Templates"
        ordering = ["name"]

    def __str__(self):
        return self.name


class SignatureFlowStep(models.Model):
    """One step (signer) within a SignatureFlowTemplate."""

    template = models.ForeignKey(
        SignatureFlowTemplate,
        on_delete=models.CASCADE,
        related_name="steps",
        verbose_name="Flow Template",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="flow_steps",
        verbose_name="User",
    )
    order = models.PositiveIntegerField(default=1, verbose_name="Order")
    role  = models.CharField(
        max_length=20,
        choices=Signer.Role.choices,
        default=Signer.Role.SIGNER,
        verbose_name="Role",
    )

    class Meta:
        verbose_name = "Flow Step"
        verbose_name_plural = "Flow Steps"
        ordering = ["template", "order"]
        unique_together = [("template", "order")]

    def __str__(self):
        return f"{self.template.name} — Step {self.order}: {self.user}"


# ---------------------------------------------------------------------------
# SignatureEvent (append-only audit log)
# ---------------------------------------------------------------------------

class SignatureEvent(models.Model):
    """Immutable audit trail for every action on a SignatureDocument."""

    class Event(models.TextChoices):
        CREATED   = "created",   "Created"
        SENT      = "sent",      "Sent for Signing"
        VIEWED    = "viewed",    "Viewed by Signer"
        SIGNED    = "signed",    "Signed"
        REJECTED  = "rejected",  "Rejected"
        REMINDED  = "reminded",  "Reminder Sent"
        COMPLETED = "completed", "Completed"
        REOPENED  = "reopened",  "Reopened"
        CANCELLED = "cancelled", "Cancelled"

    document    = models.ForeignKey(
        SignatureDocument,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name="Document",
    )
    event = models.CharField(
        max_length=20,
        choices=Event.choices,
        verbose_name="Event",
        db_index=True,
    )
    actor_user  = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="esign_events",
        verbose_name="Actor (User)",
    )
    actor_label = models.CharField(
        max_length=300,
        blank=True,
        verbose_name="Actor Label",
        help_text="Name/email for external actors not in the system.",
    )
    ip         = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP Address")
    detail     = models.TextField(blank=True, verbose_name="Detail")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Timestamp")

    class Meta:
        verbose_name = "Signature Event"
        verbose_name_plural = "Signature Events"
        ordering = ["-created_at"]

    def __str__(self):
        actor = self.actor_user or self.actor_label or "system"
        return f"[{self.document.title}] {self.event} by {actor}"


# ---------------------------------------------------------------------------
# UserSavedSignature
# ---------------------------------------------------------------------------

class UserSavedSignature(models.Model):
    """Stores a user's preferred reusable signature (base64 PNG data-URL)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_signature",
        verbose_name="User",
    )
    signature_data = models.TextField(verbose_name="Signature Data (base64)")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")

    class Meta:
        verbose_name = "Saved Signature"
        verbose_name_plural = "Saved Signatures"

    def __str__(self):
        return f"Saved signature for {self.user}"


# ---------------------------------------------------------------------------
# MobileDrawSession
# ---------------------------------------------------------------------------

class MobileDrawSession(models.Model):
    """
    Short-lived token allowing a signer to draw their signature on a mobile
    device by scanning a QR code. The desktop page polls for completion.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expires_at = models.DateTimeField(verbose_name="Expires At")
    signature_data = models.TextField(blank=True, verbose_name="Signature Data (base64)")
    is_complete = models.BooleanField(default=False, verbose_name="Complete")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")

    class Meta:
        verbose_name = "Mobile Draw Session"
        verbose_name_plural = "Mobile Draw Sessions"

    @property
    def is_expired(self):
        return timezone.now() > self.expires_at
