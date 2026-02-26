"""
Cases App — Models
===================
Core case management models for the RoC Desk system.

- ``CaseCategory``  — service catalogue item driving the client grid UI.
- ``CaseRecord``    — the central Problem & Solving record with SLA tracking.
- ``Message``       — omnichannel thread message (WhatsApp / Email / Web).
- ``Attachment``    — file upload linked to a Message.
"""
from django.db import models
from django.utils.text import slugify

from core.models import AuditableModel


# =====================================================================
# Case Category
# =====================================================================

class CaseCategory(AuditableModel):
    """
    Service catalogue category displayed as a card on the client portal.

    Examples: "Report Hardware Problem", "System Error Report",
    "Network Access Request".
    """

    name = models.CharField(max_length=200, verbose_name="Category Name")
    slug = models.SlugField(max_length=220, unique=True, blank=True, verbose_name="Slug")
    icon = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Icon",
        help_text="CSS icon class or emoji, e.g. 'fas fa-laptop-code' or '🖥️'.",
    )
    description = models.TextField(blank=True, verbose_name="Description")

    class Meta:
        verbose_name = "Case Category"
        verbose_name_plural = "Case Categories"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        """Auto-generate slug from name if not provided."""
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


# =====================================================================
# Case Record
# =====================================================================

class CaseRecord(AuditableModel):
    """
    The central Problem & Solving record.

    Lifecycle: Open → Investigating → Pending Info → Resolved → Closed.

    Staff must populate ``root_cause_analysis`` and ``solving_steps``
    before setting the status to *Resolved*.  Closing a case may
    auto-generate a Knowledge Base ``Article``.
    """

    class Status(models.TextChoices):
        OPEN = "Open", "Open"
        INVESTIGATING = "Investigating", "Investigating"
        PENDING_INFO = "PendingInfo", "Pending Info"
        RESOLVED = "Resolved", "Resolved"
        CLOSED = "Closed", "Closed"

    class Source(models.TextChoices):
        EVOLUTION_WA = "EvolutionAPI_WA", "WhatsApp (Evolution API)"
        EMAIL = "Email", "Email"
        WEBFORM = "WebForm", "Web Form"

    # --- Relationships ---
    requester = models.ForeignKey(
        "core.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cases",
        verbose_name="Requester (Employee)",
        help_text="Linked Employee record (auto-matched if email exists).",
    )
    category = models.ForeignKey(
        CaseCategory,
        on_delete=models.PROTECT,
        related_name="cases",
        verbose_name="Category",
    )
    assigned_to = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_cases",
        verbose_name="Assigned To",
    )

    # --- Requester Info (direct fields for public submissions) ---
    requester_email = models.EmailField(
        blank=True,
        verbose_name="Requester Email",
        help_text="Email provided by the requester on the web form.",
    )
    requester_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Requester Name",
    )
    requester_job_role = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="Requester Job Role",
    )
    requester_unit_name = models.CharField(
        max_length=150,
        blank=True,
        verbose_name="Requester Company Unit",
    )

    # --- Core fields ---
    subject = models.CharField(max_length=500, verbose_name="Subject")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
        verbose_name="Status",
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.WEBFORM,
        verbose_name="Source",
    )
    link = models.URLField(
        max_length=500,
        blank=True,
        verbose_name="Reference Link",
        help_text="URL/link related to this case (e.g. error page, document).",
    )

    # --- Problem & Solving ---
    problem_description = models.TextField(verbose_name="Problem Description")
    root_cause_analysis = models.TextField(
        blank=True,
        verbose_name="Root Cause Analysis",
        help_text="To be filled by support staff during investigation.",
    )
    solving_steps = models.TextField(
        blank=True,
        verbose_name="Solving Steps",
        help_text="Must be completed before marking the case as Resolved.",
    )

    # --- Dynamic form data ---
    form_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Dynamic Form Data",
        help_text="Stores category-specific form inputs as JSON.",
    )

    # --- SLA Tracking ---
    response_due_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Response Due At",
        help_text="Deadline for first staff response.",
    )
    resolution_due_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Resolution Due At",
        help_text="Deadline for case resolution.",
    )

    class Meta:
        verbose_name = "Case Record"
        verbose_name_plural = "Case Records"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.status}] {self.subject}"

    @property
    def is_active(self) -> bool:
        """Return True if the case is in an active (non-terminal) state."""
        return self.status in (
            self.Status.OPEN,
            self.Status.INVESTIGATING,
            self.Status.PENDING_INFO,
        )

    @property
    def case_number(self) -> str:
        """Human-readable case identifier derived from UUID prefix."""
        return f"CS-{str(self.id)[:8].upper()}"


# =====================================================================
# Message (Omnichannel Thread)
# =====================================================================

class Message(AuditableModel):
    """
    A single message within a CaseRecord's conversation thread.

    Messages may originate from WhatsApp (Evolution API), Email, or the
    Web UI.  ``external_id`` stores the upstream message identifier to
    prevent duplicate webhook inserts.
    """

    class Direction(models.TextChoices):
        INBOUND = "IN", "Inbound"
        OUTBOUND = "OUT", "Outbound"

    class Channel(models.TextChoices):
        WHATSAPP = "WhatsApp", "WhatsApp"
        EMAIL = "Email", "Email"
        WEB = "Web", "Web"

    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Case",
    )
    sender_employee = models.ForeignKey(
        "core.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
        verbose_name="Sender (Employee)",
    )
    sender_staff = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_messages",
        verbose_name="Sender (Staff)",
    )

    body = models.TextField(verbose_name="Message Body")
    direction = models.CharField(
        max_length=3,
        choices=Direction.choices,
        default=Direction.INBOUND,
        verbose_name="Direction",
    )
    channel = models.CharField(
        max_length=20,
        choices=Channel.choices,
        default=Channel.WEB,
        verbose_name="Channel",
    )
    external_id = models.CharField(
        max_length=255,
        blank=True,
        db_index=True,
        verbose_name="External ID",
        help_text="Evolution API message ID or Email Message-ID. Used for dedup.",
    )
    sent_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Sent At",
    )

    class Meta:
        verbose_name = "Message"
        verbose_name_plural = "Messages"
        ordering = ["sent_at"]

    def __str__(self):
        return f"[{self.direction}] {self.body[:60]}"


# =====================================================================
# Attachment
# =====================================================================

def attachment_upload_path(instance, filename):
    """Generate upload path: media/attachments/<case_uuid>/<filename>."""
    return f"attachments/{instance.message.case.id}/{filename}"


class Attachment(AuditableModel):
    """
    File upload linked to a specific Message.

    Supports WhatsApp media downloads, email attachments, and web uploads.
    """

    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="attachments",
        verbose_name="Message",
    )
    file = models.FileField(
        upload_to=attachment_upload_path,
        verbose_name="File",
    )
    original_filename = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Original Filename",
    )
    mime_type = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="MIME Type",
    )
    file_size = models.PositiveIntegerField(
        default=0,
        verbose_name="File Size (bytes)",
    )

    class Meta:
        verbose_name = "Attachment"
        verbose_name_plural = "Attachments"
        ordering = ["created_at"]

    def __str__(self):
        return self.original_filename or str(self.file)
