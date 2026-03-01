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

    class Priority(models.TextChoices):
        LOW = "Low", "Low"
        MEDIUM = "Medium", "Medium"
        HIGH = "High", "High"
        CRITICAL = "Critical", "Critical"

    class Type(models.TextChoices):
        QUESTION = "Question", "Question"
        INCIDENT = "Incident", "Incident"
        REQUEST = "Request", "Request"

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
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        verbose_name="Priority",
    )
    case_type = models.CharField(
        max_length=20,
        choices=Type.choices,
        default=Type.INCIDENT,
        verbose_name="Type",
    )
    tags = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Tags",
        help_text="Comma-separated tags (e.g. login, bug, network)",
    )
    followers = models.ManyToManyField(
        "core.User",
        blank=True,
        related_name="following_cases",
        verbose_name="Followers",
    )
    link = models.URLField(
        max_length=500,
        blank=True,
        verbose_name="Reference Link",
        help_text="URL/link related to this case (e.g. error page, document).",
    )
    has_unread_messages = models.BooleanField(
        default=False,
        verbose_name="Has Unread Messages",
        help_text="True if there are new inbound messages that staff hasn't seen.",
    )

    # --- Problem & Solving ---
    problem_description = models.TextField(verbose_name="Problem Description")
    root_cause_analysis = models.CharField(
        max_length=1500,
        blank=True,
        verbose_name="Root Cause Analysis",
        help_text="To be filled by support staff during investigation.",
    )
    solving_steps = models.CharField(
        max_length=1500,
        blank=True,
        verbose_name="Solving Steps",
        help_text="Must be completed before marking the case as Resolved.",
    )
    quick_notes = models.TextField(
        blank=True,
        verbose_name="Quick Notes",
        help_text="Internal notes or summary for staff.",
    )

    # --- Dynamic form data ---
    form_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Dynamic Form Data",
        help_text="Stores category-specific form inputs as JSON.",
    )

    # --- Bulk Action Fields ---
    is_archived = models.BooleanField(
        default=False,
        verbose_name="Is Archived",
        help_text="Archived tickets are hidden from the main inbox view."
    )
    is_spam = models.BooleanField(
        default=False,
        verbose_name="Is Spam",
        help_text="Tickets marked as spam."
    )
    master_ticket = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sub_tickets",
        verbose_name="Master Ticket",
        help_text="If merged, this links to the primary ticket. Sub-tickets are hidden from the main list."
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

    class DeliveryStatus(models.TextChoices):
        PENDING = "Pending", "Pending"
        SUCCESS = "Success", "Success"
        FAILED = "Failed", "Failed"

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
    is_read = models.BooleanField(
        default=False,
        verbose_name="Is Read",
        help_text="Indicates if the staff has seen this incoming message.",
    )
    delivery_status = models.CharField(
        max_length=20,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.SUCCESS,
        verbose_name="Delivery Status",
    )
    delivery_error = models.TextField(
        blank=True,
        verbose_name="Delivery Error",
    )
    cc_emails = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="CC Emails",
        help_text="Comma-separated secondary recipients for email channels.",
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


# =====================================================================
# Internal Case Comment
# =====================================================================

class CaseComment(AuditableModel):
    """
    Internal notes and discussions left by staff members on a case.
    Not visible to end-users/clients.
    """
    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="internal_comments",
        verbose_name="Case",
    )
    author = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="Author",
    )
    body = models.TextField(verbose_name="Comment Body")
    
    mentions = models.ManyToManyField(
        "core.User",
        blank=True,
        related_name="mentioned_in_comments",
        verbose_name="Mentioned Users"
    )

    class Meta:
        verbose_name = "Internal Comment"
        verbose_name_plural = "Internal Comments"
        ordering = ["created_at"]

    def __str__(self):
        return f"Comment by {self.author} on {self.case.case_number}"


# =====================================================================
# Audit Log
# =====================================================================

class CaseAuditLog(AuditableModel):
    """
    Tracks historical changes to important CaseRecord fields.
    """
    
    class ActionText(models.TextChoices):
        CREATED = "Created", "Case Created"
        UPDATED = "Updated", "Property Updated"
        STATUS_CHANGE = "Status Change", "Status Changed"
        ASSIGNED = "Assigned", "Assigned"
        REASSIGNED = "Reassigned", "Reassigned"
        SLA_CHANGE = "SLA Change", "SLA Changed"
        COMMENT = "Comment", "Internal Comment Added"

    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        verbose_name="Case",
    )
    action = models.CharField(
        max_length=50,
        choices=ActionText.choices,
        default=ActionText.UPDATED,
        verbose_name="Action Type",
    )
    field_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Field Name",
        help_text="The model field that was changed.",
    )
    old_value = models.TextField(
        blank=True,
        verbose_name="Old Value",
    )
    new_value = models.TextField(
        blank=True,
        verbose_name="New Value",
    )

    class Meta:
        verbose_name = "Case Audit Log"
        verbose_name_plural = "Case Audit Logs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.case.case_number}] {self.action} on {self.field_name}"
