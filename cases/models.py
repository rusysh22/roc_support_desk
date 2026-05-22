"""
Cases App — Models
===================
Core case management models for the RoC Desk system.

- ``CaseCategory``  — service catalogue item driving the client grid UI.
- ``CaseRecord``    — the central Problem & Solving record with SLA tracking.
- ``Message``       — omnichannel thread message (WhatsApp / Email / Web).
- ``Attachment``    — file upload linked to a Message.
"""
import re
import uuid

from django.db import models
from django.utils.text import slugify

from core.models import AuditableModel


# =====================================================================
# Ticket Category
# =====================================================================

class CaseCategory(AuditableModel):
    """
    Service catalogue category displayed as a card on the client portal.

    Supports one level of nesting: a category with ``parent=None`` is a
    **Main Category** shown on the portal grid.  A category with a parent
    is a **Sub-Category** shown after the user clicks the main category.
    Categories without children link directly to the ticket form.
    """

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Parent Category",
        help_text="Leave empty for a Main Category. Select a parent to make this a Sub-Category.",
    )
    name = models.CharField(max_length=200, verbose_name="Category Name")
    slug = models.SlugField(max_length=220, unique=True, blank=True, verbose_name="Slug")
    icon = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Icon",
        help_text="CSS icon class or emoji, e.g. 'fas fa-laptop-code' or '🖥️'.",
    )
    description = models.TextField(blank=True, verbose_name="Description")
    template_subject = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="Template Subject",
        help_text="Optional text template for the subject field.",
    )
    template_text = models.TextField(
        blank=True,
        verbose_name="Template Text",
        help_text="Optional text template for the problem description field.",
    )
    prefix_code = models.CharField(
        max_length=2,
        default="RQ",
        verbose_name="Prefix Code",
        help_text="2-letter or number prefix for ticket sequence (e.g. RQ, IN, HR).",
    )
    is_confidential = models.BooleanField(
        default=False,
        verbose_name="Confidential Category",
        help_text="Tickets in this category are confidential — only users with 'can handle confidential' permission can access them.",
    )
    is_attachment_mandatory = models.BooleanField(
        default=False,
        verbose_name="Attachment Mandatory",
        help_text="If checked, users must upload at least one attachment when submitting a ticket in this category.",
    )

    class WorkflowType(models.TextChoices):
        STANDARD = "standard", "Standard (no document/approval)"
        DOCUMENT_ONLY = "document_only", "Document Only (attach, no approval)"
        APPROVAL_ONLY = "approval_only", "Approval Only (no document)"
        DOCUMENT_APPROVAL = "document_approval", "Document + Approval"

    workflow_type = models.CharField(
        max_length=20,
        choices=WorkflowType.choices,
        default=WorkflowType.STANDARD,
        verbose_name="Workflow Type",
        help_text="Controls whether tickets in this category require documents, approvals, both, or neither.",
    )

    class Meta:
        verbose_name = "Ticket Category"
        verbose_name_plural = "Ticket Categories"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        """Auto-generate slug from name if not provided."""
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def is_parent(self):
        """True if this category has sub-categories."""
        return self.children.exists()

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name


# =====================================================================
# RCA Template
# =====================================================================

class RCATemplate(AuditableModel):
    """
    Predefined Root Cause Analysis and Solving Steps templates.

    Linked to a CaseCategory so that staff see relevant quick-fill
    buttons when documenting a ticket's resolution.  Templates without
    a category are shown for ALL tickets as general-purpose options.
    """

    category = models.ForeignKey(
        CaseCategory,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="rca_templates",
        verbose_name="Category",
        help_text="Leave empty to make this template available for all categories.",
    )
    name = models.CharField(
        max_length=200,
        verbose_name="Template Name",
        help_text="Short label shown on the quick-fill button, e.g. 'Penambahan Akses User'.",
    )
    rca_text = models.TextField(
        blank=True,
        verbose_name="Root Cause Analysis Text",
        help_text="Template text for the RCA field. Leave blank to skip.",
    )
    solving_steps_text = models.TextField(
        blank=True,
        verbose_name="Solving Steps Text",
        help_text="Template text for the Solving Steps field. Leave blank to skip.",
    )
    order = models.PositiveIntegerField(
        default=0,
        verbose_name="Display Order",
        help_text="Lower numbers appear first.",
    )

    class Meta:
        verbose_name = "RCA Template"
        verbose_name_plural = "RCA Templates"
        ordering = ["order", "name"]

    def __str__(self):
        prefix = f"[{self.category.name}] " if self.category else "[Global] "
        return f"{prefix}{self.name}"


# =====================================================================
# Ticket Record
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
        PENDING_APPROVAL = "PendingApproval", "Pending Approval"
        REVISION_REQUIRED = "RevisionRequired", "Revision Required"
        OPEN = "Open", "Open"
        INVESTIGATING = "Investigating", "Investigating"
        PENDING_INFO = "PendingInfo", "Pending Info"
        RESOLVED = "Resolved", "Resolved"
        CLOSED = "Closed", "Closed"

    class Source(models.TextChoices):
        EVOLUTION_WA = "EvolutionAPI_WA", "WhatsApp (Evolution API)"
        EMAIL = "Email", "Email"
        WEBFORM = "WebForm", "Web Form"
        WEBCHAT = "WebChat", "Web Chat"
        TEAMS_BOT = "Teams_Bot", "Microsoft Teams (Bot)"

    class Priority(models.TextChoices):
        LOW = "Low", "Low"
        MEDIUM = "Medium", "Medium"
        HIGH = "High", "High"
        CRITICAL = "Critical", "Critical"

    class Type(models.TextChoices):
        QUESTION = "Question", "Question"
        INCIDENT = "Incident", "Incident"
        REQUEST = "Request", "Request"

    class EditPermissionStatus(models.TextChoices):
        NONE = "None", "None"
        REQUESTED = "Requested", "Requested"
        APPROVED = "Approved", "Approved"
        REJECTED = "Rejected", "Rejected"

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
    edit_permission_status = models.CharField(
        max_length=20,
        choices=EditPermissionStatus.choices,
        default=EditPermissionStatus.NONE,
        verbose_name="Edit Permission Status",
        help_text="Tracks approval workflow for editing closed tickets.",
    )
    edit_requested_by = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="edit_requests",
        verbose_name="Edit Requested By",
    )
    edit_request_reason = models.TextField(
        blank=True,
        verbose_name="Edit Request Reason",
    )
    amendment_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Amendment Count",
        help_text="Tracks how many times a closed ticket was edited.",
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
    last_viewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last Viewed At",
        help_text="Timestamp when staff last opened this ticket's detail page.",
    )
    client_last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Client Last Read At",
        help_text="Timestamp when the portal user (requester) last opened this ticket's chat.",
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
    hold_wa_session = models.BooleanField(
        default=False,
        verbose_name="Hold WA Session",
        help_text="If true, bypasses the 60-minute auto end-session for WhatsApp conversations."
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

    # --- Chat portal guest access ---
    guest_token = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="Guest Token",
        help_text="Random token that allows anonymous users to access their own ticket via chat.",
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
        verbose_name = "Ticket Record"
        verbose_name_plural = "Ticket Records"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.status}] {self.subject}"

    @property
    def is_locked(self) -> bool:
        """True while an approval workflow is running — blocks edits."""
        return self.status in (self.Status.PENDING_APPROVAL, self.Status.REVISION_REQUIRED)

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
        """Human-readable case identifier derived from category prefix and UUID."""
        prefix = self.category.prefix_code if self.category else "RQ"
        return f"{prefix}-{str(self.id)[:8].upper()}"



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
        TEAMS = "Teams", "Microsoft Teams"

    class DeliveryStatus(models.TextChoices):
        PENDING = "Pending", "Pending"
        SUCCESS = "Success", "Success"
        FAILED = "Failed", "Failed"

    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name="Ticket",
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
    is_deleted = models.BooleanField(
        default=False,
        verbose_name="Is Deleted",
    )
    is_system = models.BooleanField(
        default=False,
        verbose_name="Is System Message",
        help_text="Auto-generated messages: ticket created, status changed, etc.",
    )
    is_edited = models.BooleanField(
        default=False,
        verbose_name="Is Edited",
    )
    original_body = models.TextField(
        blank=True,
        verbose_name="Original Body",
        help_text="Stores the body before edit, for audit trail.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Metadata",
        help_text="Arbitrary structured data for actionable message types (polls, approvals, etc.).",
    )
    quoted_message = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
        verbose_name="Quoted Message",
        help_text="The message being replied to (quote reply).",
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
# Message Reaction (Emoji)
# =====================================================================

class MessageReaction(AuditableModel):
    """
    Emoji reaction on a message, sent via WhatsApp reaction API.
    """

    message = models.ForeignKey(
        Message,
        on_delete=models.CASCADE,
        related_name="reactions",
        verbose_name="Message",
    )
    emoji = models.CharField(
        max_length=10,
        verbose_name="Emoji",
    )
    reacted_by = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="message_reactions",
        verbose_name="Reacted By",
    )

    class Meta:
        verbose_name = "Message Reaction"
        verbose_name_plural = "Message Reactions"
        unique_together = ("message", "reacted_by")

    def __str__(self):
        return f"{self.emoji} on {self.message_id}"


# =====================================================================
# Internal Ticket Comment
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
        verbose_name="Ticket",
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
        verbose_name="Ticket",
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


# =====================================================================
# Document Template System
# =====================================================================

class DocumentTemplate(AuditableModel):
    """
    Admin-created letter/document template with HTML body and {{placeholder}} variables.

    Linked to one or more CaseCategories — when a user submits a ticket in a linked
    category, this template's fill-form appears in the submission flow.
    """

    class ApprovalFlow(models.TextChoices):
        NONE = "none", "No Approval Required"
        CLICK_ONLY = "click_only", "Click Validation Only"
        SIGNATURE_ONLY = "signature_only", "Signature Only"
        BOTH = "both", "Click Validation + Signature"

    title = models.CharField(max_length=200, verbose_name="Template Title")
    description = models.TextField(blank=True, verbose_name="Description")
    body_html = models.TextField(
        verbose_name="Document Body (HTML)",
        help_text="HTML content with {{placeholder}} variables, e.g. {{nama_pemohon}}, {{tanggal}}.",
    )
    categories = models.ManyToManyField(
        CaseCategory,
        blank=True,
        related_name="document_templates",
        verbose_name="Applicable Categories",
        help_text="Document fill-form appears on ticket submission for these categories.",
    )
    is_required = models.BooleanField(
        default=False,
        verbose_name="Required",
        help_text="If checked, user must complete this document to submit the ticket.",
    )
    approval_flow = models.CharField(
        max_length=20,
        choices=ApprovalFlow.choices,
        default=ApprovalFlow.CLICK_ONLY,
        verbose_name="Approval Flow",
    )
    token_validity_days = models.PositiveIntegerField(
        default=7,
        verbose_name="Link Validity (days)",
        help_text="How many days the approval magic-link URL remains valid. 0 = never expires.",
    )
    approver_deadline_days = models.PositiveIntegerField(
        default=3,
        verbose_name="Approver Deadline (days)",
        help_text="How many days each approver has to act before the step is considered overdue. 0 = no deadline.",
    )

    class Meta:
        verbose_name = "Document Template"
        verbose_name_plural = "Document Templates"
        ordering = ["title"]

    def __str__(self):
        return self.title

    def extract_placeholders(self):
        """Return unique list of placeholder names found in body_html."""
        return list(dict.fromkeys(re.findall(r'\{\{(\w+)\}\}', self.body_html)))


class DocumentApprovalStage(AuditableModel):
    """
    One stage in a DocumentTemplate's approval chain.

    A template has ordered stages. Each stage has its own set of approvers
    and a policy that governs how many must act before the stage is complete.

    Policies:
    - ALL_REQUIRED: every approver in the stage must approve.
    - ANY_ONE: the first approver to act (approve/reject) concludes the stage;
      remaining pending steps are automatically SKIPPED.

    allow_user_selection: when True, the submitter picks the approvers for
    this stage at ticket submission time (the admin-configured approvers serve
    as a default pool or are ignored).
    """

    class Policy(models.TextChoices):
        ALL_REQUIRED = "all_required", "All Must Approve"
        ANY_ONE = "any_one", "Any One May Approve"

    template = models.ForeignKey(
        DocumentTemplate,
        on_delete=models.CASCADE,
        related_name="approval_stages",
        verbose_name="Document Template",
    )
    order = models.PositiveIntegerField(
        default=1,
        verbose_name="Stage Order",
        help_text="Stages are processed in ascending order.",
    )
    label = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Stage Label",
        help_text="Optional name shown in the UI, e.g. 'Manager Sign-off'.",
    )
    policy = models.CharField(
        max_length=20,
        choices=Policy.choices,
        default=Policy.ALL_REQUIRED,
        verbose_name="Stage Policy",
    )
    allow_user_selection = models.BooleanField(
        default=False,
        verbose_name="Allow User Selection",
        help_text="If checked, the submitter may choose approvers for this stage when submitting.",
    )

    class Meta:
        verbose_name = "Document Approval Stage"
        verbose_name_plural = "Document Approval Stages"
        ordering = ["template", "order"]
        unique_together = ("template", "order")

    def __str__(self):
        label = f" — {self.label}" if self.label else ""
        return f"{self.template.title} › Stage {self.order}{label} [{self.policy}]"


class DocumentApproverConfig(AuditableModel):
    """
    Pre-configured approver assigned to a DocumentApprovalStage.

    Multiple approvers per stage are supported; the stage's policy determines
    whether all or just one must act.
    """

    stage = models.ForeignKey(
        DocumentApprovalStage,
        on_delete=models.CASCADE,
        related_name="approver_configs",
        verbose_name="Approval Stage",
    )
    approver = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        related_name="document_approver_configs",
        verbose_name="Approver",
    )
    order = models.PositiveIntegerField(
        default=1,
        verbose_name="Display Order",
        help_text="Display order within the stage. Does not affect processing sequence.",
    )

    class Meta:
        verbose_name = "Document Approver Config"
        verbose_name_plural = "Document Approver Configs"
        ordering = ["stage", "order"]
        unique_together = ("stage", "approver")

    def __str__(self):
        return f"{self.stage} → {self.approver}"


# =====================================================================
# Case Document (generated instance per ticket)
# =====================================================================

def case_document_pdf_path(instance, filename):
    """Generate upload path: media/documents/<case_uuid>/<filename>."""
    return f"documents/{instance.case.id}/{filename}"


class CaseDocument(AuditableModel):
    """
    A document generated from a DocumentTemplate for a specific CaseRecord.

    Created when a user fills in the placeholder form during ticket submission.
    Transitions: draft → pending_approval → approved / rejected.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Ticket",
    )
    template = models.ForeignKey(
        DocumentTemplate,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="generated_documents",
        verbose_name="Document Template",
        help_text="Null when workflow_type=APPROVAL_ONLY (no document body needed).",
    )
    revision_number = models.PositiveIntegerField(
        default=1,
        verbose_name="Revision Number",
        help_text="Increments each time the user submits a revision after rejection.",
    )
    filled_data = models.JSONField(
        default=dict,
        verbose_name="Filled Data",
        help_text="Placeholder values provided by the user, e.g. {'nama_pemohon': 'John Doe'}.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    generated_pdf = models.FileField(
        upload_to=case_document_pdf_path,
        blank=True,
        null=True,
        verbose_name="Generated PDF",
    )
    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        verbose_name="Access Token",
        help_text="Magic-link token for approver access without login.",
    )
    token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Token Expires At",
        help_text="When set, the approval magic-link URL becomes invalid after this datetime.",
    )
    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Submitted At",
    )

    class Meta:
        verbose_name = "Case Document"
        verbose_name_plural = "Case Documents"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template.title} — {self.case.case_number} [{self.status}]"

    @property
    def is_token_expired(self) -> bool:
        """True if the magic-link URL has passed its expiry datetime."""
        if not self.token_expires_at:
            return False
        from django.utils import timezone
        return timezone.now() > self.token_expires_at

    @property
    def is_fully_approved(self) -> bool:
        """True when every non-skipped step has been approved."""
        steps = self.approval_steps.exclude(status=DocumentApprovalStep.Status.SKIPPED)
        return steps.exists() and not steps.filter(
            status__in=[DocumentApprovalStep.Status.PENDING, DocumentApprovalStep.Status.REJECTED]
        ).exists()


# =====================================================================
# Document Approval Step
# =====================================================================

def approval_signature_path(instance, filename):
    """Generate upload path: media/documents/<case_uuid>/signatures/<filename>."""
    return f"documents/{instance.document.case.id}/signatures/{filename}"


class DocumentApprovalStep(AuditableModel):
    """
    One approval action row per approver per CaseDocument.

    Steps are processed sequentially by order. The next approver is notified
    only after the previous step reaches status=approved.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SKIPPED = "skipped", "Skipped (ANY_ONE policy satisfied)"

    class ApprovalType(models.TextChoices):
        CLICK = "click", "Click Validation"
        SIGNATURE = "signature", "Digital Signature"

    document = models.ForeignKey(
        CaseDocument,
        on_delete=models.CASCADE,
        related_name="approval_steps",
        verbose_name="Document",
    )
    approver = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        related_name="document_approval_steps",
        verbose_name="Approver",
    )
    order = models.PositiveIntegerField(default=1, verbose_name="Step Order")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )
    approval_type = models.CharField(
        max_length=20,
        choices=ApprovalType.choices,
        default=ApprovalType.CLICK,
        verbose_name="Approval Type",
    )
    signature_image = models.FileField(
        upload_to=approval_signature_path,
        blank=True,
        null=True,
        verbose_name="Signature Image",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    acted_at = models.DateTimeField(null=True, blank=True, verbose_name="Action Taken At")
    approve_by = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Approve By",
        help_text="Deadline for this approver to act. Derived from template's approver_deadline_days.",
    )

    class Meta:
        verbose_name = "Document Approval Step"
        verbose_name_plural = "Document Approval Steps"
        ordering = ["document", "order"]

    stage_order = models.PositiveIntegerField(
        default=1,
        verbose_name="Stage Order",
        help_text="Which stage this step belongs to (copied from DocumentApprovalStage.order at creation).",
    )
    stage_policy = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Stage Policy Snapshot",
        help_text="Policy of the stage at the time this step was created (all_required or any_one).",
    )

    @property
    def is_overdue(self) -> bool:
        """True if this step has passed its approve_by deadline and is still pending."""
        if self.status != self.Status.PENDING or not self.approve_by:
            return False
        from django.utils import timezone
        return timezone.now() > self.approve_by

    def __str__(self):
        return f"{self.document} — Stage {self.stage_order} Step {self.order}: {self.approver} [{self.status}]"


# =====================================================================
# Category Approver Config (ticket-level, APPROVAL_ONLY workflow)
# =====================================================================

class CategoryApproverConfig(AuditableModel):
    """
    Pre-configured approvers for a CaseCategory's ticket-level approval.

    Used when workflow_type=APPROVAL_ONLY — the ticket itself goes through
    an approval chain before becoming OPEN, with no document body required.
    Sequential processing: step with the lowest order is notified first.
    """

    category = models.ForeignKey(
        CaseCategory,
        on_delete=models.CASCADE,
        related_name="approver_configs",
        verbose_name="Category",
    )
    approver = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        related_name="category_approver_configs",
        verbose_name="Approver",
    )
    order = models.PositiveIntegerField(
        default=1,
        verbose_name="Approval Order",
        help_text="Lower numbers are notified first.",
    )

    class Meta:
        verbose_name = "Category Approver Config"
        verbose_name_plural = "Category Approver Configs"
        ordering = ["category", "order"]
        unique_together = ("category", "approver")

    def __str__(self):
        return f"{self.category} → {self.approver} (step {self.order})"


# =====================================================================
# Document Approval Log (complete audit trail of every action)
# =====================================================================

class DocumentApprovalLog(AuditableModel):
    """
    Immutable audit record for every meaningful event in the document/approval lifecycle.

    Recorded for: submission, each approve/reject/skip action, revision submission,
    token expiry, stage transitions, and final approval.  Used to build the
    timeline shown on the portal tracking page and the admin detail view.
    """

    class Action(models.TextChoices):
        SUBMITTED = "submitted", "Document Submitted"
        STAGE_STARTED = "stage_started", "Approval Stage Started"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        SKIPPED = "skipped", "Step Skipped (ANY_ONE satisfied)"
        REVISION_REQUESTED = "revision_requested", "Revision Requested"
        REVISION_SUBMITTED = "revision_submitted", "Revision Submitted"
        TOKEN_EXPIRED = "token_expired", "Approval Link Expired"
        FULLY_APPROVED = "fully_approved", "All Approvals Completed"
        TICKET_ACTIVATED = "ticket_activated", "Ticket Activated (OPEN)"

    document = models.ForeignKey(
        CaseDocument,
        on_delete=models.CASCADE,
        related_name="approval_logs",
        verbose_name="Document",
    )
    action = models.CharField(
        max_length=30,
        choices=Action.choices,
        verbose_name="Action",
        db_index=True,
    )
    actor = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="document_approval_actions",
        verbose_name="Actor",
        help_text="Staff/approver who performed the action. Null for magic-link or system actions.",
    )
    actor_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Actor Name (snapshot)",
        help_text="Display name at time of action, preserved for anonymous/magic-link approvers.",
    )
    step = models.ForeignKey(
        DocumentApprovalStep,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="Approval Step",
    )
    stage_order = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name="Stage Order",
    )
    notes = models.TextField(
        blank=True,
        verbose_name="Notes / Rejection Reason",
        help_text="Rejection message or any actor-supplied notes for this action.",
    )
    previous_status = models.CharField(
        max_length=30,
        blank=True,
        verbose_name="Previous Status",
    )
    new_status = models.CharField(
        max_length=30,
        blank=True,
        verbose_name="New Status",
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        verbose_name="IP Address",
        help_text="IP of the actor at the time of action (for magic-link approvers).",
    )

    class Meta:
        verbose_name = "Document Approval Log"
        verbose_name_plural = "Document Approval Logs"
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.document.case.case_number}] {self.action} by {self.actor_name or self.actor}"
