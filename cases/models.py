"""
Cases App — Models
===================
Core case management models for the RoC Desk system.

- ``CaseCategory``  — service catalogue item driving the client grid UI.
- ``TicketLocation`` — master list of ticket locations shown on submit.
- ``CaseRecord``    — the central Problem & Solving record with SLA tracking.
- ``Message``       — omnichannel thread message (WhatsApp / Email / Web).
- ``Attachment``    — file upload linked to a Message.
"""
import re
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core.models import AuditableModel


# =====================================================================
# SLA Policy
# =====================================================================

class SLAPolicy(AuditableModel):
    """
    Defines SLA thresholds (in hours) based on Priority (P1–P4).
    Managed exclusively via Django Admin.
    """
    priority = models.CharField(
        max_length=20,
        choices=[
            ("P1", "P1"),
            ("P2", "P2"),
            ("P3", "P3"),
            ("P4", "P4"),
        ],
        unique=True,
        verbose_name="Priority Level"
    )
    response_time_hours = models.PositiveIntegerField(
        default=2,
        verbose_name="Response SLA (Hours)",
        help_text="Time to first response"
    )
    resolution_time_hours = models.PositiveIntegerField(
        default=24,
        verbose_name="Resolution SLA (Hours)",
        help_text="Time to resolve ticket"
    )

    class Meta:
        verbose_name = "SLA Policy"
        verbose_name_plural = "SLA Policies"
        ordering = ["resolution_time_hours"]

    def __str__(self):
        return f"SLA Policy for {self.priority}"


class PriorityMatrixRule(AuditableModel):
    severity = models.CharField(
        max_length=5,
        choices=[
            ("1", "Severity 1"),
            ("2", "Severity 2"),
            ("3", "Severity 3"),
        ],
        verbose_name="Severity",
    )
    impact = models.CharField(
        max_length=20,
        choices=[
            ("High", "High"),
            ("Medium", "Medium"),
            ("Low", "Low"),
        ],
        verbose_name="Impact",
    )
    priority = models.CharField(
        max_length=20,
        choices=[
            ("P1", "P1"),
            ("P2", "P2"),
            ("P3", "P3"),
            ("P4", "P4"),
        ],
        verbose_name="Priority",
    )

    class Meta:
        verbose_name = "Priority Matrix Rule"
        verbose_name_plural = "Priority Matrix Rules"
        ordering = ["severity", "impact"]
        constraints = [
            models.UniqueConstraint(
                fields=["severity", "impact"],
                name="uniq_priority_matrix_severity_impact",
            ),
        ]

    def __str__(self):
        return f"Severity {self.severity} × {self.impact} → {self.priority}"

    @classmethod
    def as_lookup_dict(cls) -> dict:
        return {
            f"{row.severity}|{row.impact}": row.priority
            for row in cls.objects.all().only("severity", "impact", "priority")
        }

    @classmethod
    def lookup(cls, severity: str, impact: str, default: str = "P3") -> str:
        return cls.as_lookup_dict().get(f"{severity}|{impact}", default)


# =====================================================================
# Ticket Category
# =====================================================================

class CaseCategory(AuditableModel):
    """
    Service catalogue category displayed as a card on the client portal.

    Supports unlimited nesting via ``parent``. Root categories (parent=None)
    appear on the portal grid. Categories with children navigate deeper;
    leaf categories link to the ticket form.
    """

    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        verbose_name="Parent Category",
        help_text="Leave empty for a root category. Select any category to nest under it.",
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
        unique=True,
        verbose_name="Prefix Code",
        help_text="Unique 2-letter or number prefix for ticket sequence (e.g. RQ, IN, HR). Names may be duplicated; codes must not.",
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

    enable_change_request = models.BooleanField(
        default=False,
        verbose_name="Enable Change Request",
        help_text="Allow portal users to attach a change request document when submitting tickets in this category.",
    )
    
    class CaseType(models.TextChoices):
        QUESTION = "Question", "Question"
        INCIDENT = "Incident", "Incident"
        REQUEST = "Request", "Request"

    default_case_type = models.CharField(
        max_length=20,
        choices=CaseType.choices,
        blank=True,
        null=True,
        verbose_name="Default Ticket Type",
        help_text="If set, tickets in this category will default to this type."
    )
    default_tags = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Default Tags",
        help_text="Comma-separated tags automatically applied to new tickets."
    )

    is_use_routing_approval = models.BooleanField(
        default=False,
        verbose_name="Use Routing Approval",
        help_text="If checked, new tickets will enter 'Pending Approval' status and require the requester's direct manager to approve.",
    )
    is_need_admin_approval = models.BooleanField(
        default=False,
        verbose_name="Need Admin Approval",
        help_text="If checked along with Routing Approval, tickets will require an additional approval step from the Support Desk Admin after manager approval.",
    )

    @classmethod
    def subtree_ids(cls, slug):
        """ids of the category with this slug and of everything below it (any depth), for filters."""
        root = cls.objects.filter(slug=slug).values_list("id", flat=True).first()
        if root is None:
            return []
        found, frontier = {root}, [root]
        while frontier:
            frontier = list(
                cls.objects.filter(parent_id__in=frontier).exclude(id__in=found).values_list("id", flat=True)
            )
            found.update(frontier)
        return list(found)

    class Meta:
        verbose_name = "Ticket Category"
        verbose_name_plural = "Ticket Categories"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if self.prefix_code:
            self.prefix_code = self.prefix_code.strip().upper()[:2]
        if not self.slug:
            base = slugify(self.name) or "category"
            candidate = base
            n = 2
            while True:
                qs = type(self).objects.filter(slug=candidate)
                if self.pk:
                    qs = qs.exclude(pk=self.pk)
                if not qs.exists():
                    break
                candidate = f"{base}-{n}"
                n += 1
            self.slug = candidate
        super().save(*args, **kwargs)

    @property
    def is_parent(self):
        """True if this category has sub-categories."""
        return self.children.exists()

    def get_descendant_ids(self):
        ids = []
        stack = list(self.children.values_list("pk", flat=True))
        while stack:
            cid = stack.pop()
            ids.append(cid)
            stack.extend(
                type(self).objects.filter(parent_id=cid).values_list("pk", flat=True)
            )
        return ids

    def get_path_parts(self):
        """Ancestor chain as a list, root-first, ending with this category."""
        parts = [self.name]
        node = self.parent
        seen = {self.pk}
        while node and node.pk not in seen:
            parts.append(node.name)
            seen.add(node.pk)
            node = node.parent
        return list(reversed(parts))

    def get_path_display(self):
        return " > ".join(self.get_path_parts())

    def __str__(self):
        return self.get_path_display()

    @classmethod
    def build_parent_map(cls, categories=None):
        cats = list(categories) if categories is not None else list(cls.objects.order_by("name"))
        by_parent = {}
        for cat in cats:
            by_parent.setdefault(cat.parent_id, []).append(cat)
        return cats, by_parent

    @classmethod
    def iter_tree(cls, categories=None, parent_id=None, depth=0):
        _, by_parent = cls.build_parent_map(categories)
        def walk(pid, d):
            for cat in by_parent.get(pid, []):
                yield cat, d
                yield from walk(cat.pk, d + 1)
        yield from walk(parent_id, depth)

    @classmethod
    def parent_select_options(cls, exclude_pks=None):
        exclude_pks = set(exclude_pks or [])
        options = []
        for cat, depth in cls.iter_tree():
            if cat.pk in exclude_pks:
                continue
            indent = ("\u2014" * depth + " ") if depth else ""
            options.append({"id": cat.pk, "label": f"{indent}{cat.name}", "depth": depth})
        return options

    @classmethod
    def portal_search_tree(cls, exclude_slugs=None):
        exclude_slugs = set(exclude_slugs or ())
        cats = [
            c for c in cls.objects.order_by("name")
            if c.slug not in exclude_slugs
        ]
        by_parent = {}
        for cat in cats:
            by_parent.setdefault(cat.parent_id, []).append(cat)

        def walk(parent_id):
            nodes = []
            for cat in by_parent.get(parent_id, []):
                children = walk(cat.pk)
                nodes.append({
                    "id": str(cat.pk),
                    "name": cat.name,
                    "description": cat.description or "",
                    "tags": cat.default_tags or "",
                    "code": cat.prefix_code or "",
                    "slug": cat.slug,
                    "icon": cat.icon or "📝",
                    "has_children": bool(children),
                    "children": children,
                })
            return nodes

        return walk(None)


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


class SavedCaseFilter(AuditableModel):
    """
    A user's saved combination of Ticket Management (case_list) filters,
    stored as a raw querystring so any current or future filter param works
    without needing a model change.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_case_filters",
        verbose_name="User",
    )
    name = models.CharField(
        max_length=100,
        verbose_name="Name",
        help_text="Short label for this saved filter, e.g. 'My P1 tickets'.",
    )
    query_string = models.TextField(
        verbose_name="Query String",
        help_text="URL-encoded filter parameters captured from the ticket list page.",
    )

    class Meta:
        verbose_name = "Saved Case Filter"
        verbose_name_plural = "Saved Case Filters"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.user})"


class TicketLocation(AuditableModel):
    name = models.CharField(max_length=100, unique=True, verbose_name="Location Name")
    slug = models.SlugField(max_length=50, unique=True, verbose_name="Slug")
    requires_detail = models.BooleanField(
        default=False,
        verbose_name="Requires Custom Location",
        help_text="If checked, the requester must type the location when this option is selected.",
    )
    is_active = models.BooleanField(default=True, verbose_name="Active")
    order = models.PositiveIntegerField(default=0, verbose_name="Display Order")
    is_archived = models.BooleanField(default=False, db_index=True, verbose_name="Archived")

    class Meta:
        verbose_name = "Location / Site"
        verbose_name_plural = "Locations / Sites"
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


# =====================================================================
# Ticket Record
# =====================================================================

class FavoriteService(models.Model):
    """A Ticket Portal service (root category card) the user starred."""

    user = models.ForeignKey("core.User", on_delete=models.CASCADE, related_name="favorite_services")
    category = models.ForeignKey(CaseCategory, on_delete=models.CASCADE, related_name="favorited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Favorite Service"
        verbose_name_plural = "Favorite Services"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "category"], name="cases_favorite_service_unique"),
        ]

    def __str__(self):
        return f"{self.user} ★ {self.category}"


class CaseRecord(AuditableModel):
    """
    The central Problem & Solving record.

    Lifecycle: New → Assigned → In Progress → (Pending User | Pending
    External) → Resolved → Closed, with Rejected/Cancelled as terminal
    off-ramps. Valid transitions are enforced via ``STATUS_META`` /
    ``can_transition_to`` — see below.

    Staff must populate ``root_cause_analysis`` and ``solving_steps``
    before setting the status to *Resolved*.  Closing a case may
    auto-generate a Knowledge Base ``Article``.
    """

    class Status(models.TextChoices):
        # --- Approval-routing sub-workflow (separate from the core
        # lifecycle below — driven by category.is_use_routing_approval /
        # TicketApproval, never set directly by staff) ---
        PENDING_APPROVAL = "PendingApproval", "Pending Approval"
        REVISION_REQUIRED = "RevisionRequired", "Revision Required"
        # --- Core lifecycle ---
        NEW = "New", "New"
        ASSIGNED = "Assigned", "Assigned"
        IN_PROGRESS = "InProgress", "In Progress"
        PENDING_USER = "PendingUser", "Pending User"
        PENDING_EXTERNAL = "PendingExternal", "Pending External"
        RESOLVED = "Resolved", "Resolved"
        CLOSED = "Closed", "Closed"
        REJECTED = "Rejected", "Rejected"
        CANCELLED = "Cancelled", "Cancelled"

    # Single source of truth for allowed transitions + display metadata.
    # Replaces the status_colors/status_columns dicts that used to be
    # duplicated across views.py/views_chat.py/templates. Deliberately
    # excludes PENDING_APPROVAL/REVISION_REQUIRED — those are driven by
    # the approval-routing subsystem, not by direct status edits.
    # "next" is every status a ticket may legally move to (feeds the status
    # <select> and can_transition_to's validation — includes deliberate
    # negative-flow/reopen moves, e.g. Assigned back to New). "primary_next"
    # is the single recommended forward step (or None if terminal) driving
    # the RCA form's one-click "Submit to <status>" shortcut — kept separate
    # from "next" so a branching status (e.g. In Progress) still gets a
    # sensible default action instead of losing the shortcut entirely.
    STATUS_META = {
        Status.NEW: {
            "label": "New", "color": "#6366f1", "icon": "🆕",
            "badge_class": "jk-badge-new",
            "next": {Status.ASSIGNED, Status.REJECTED, Status.CANCELLED},
            "primary_next": Status.ASSIGNED,
        },
        Status.ASSIGNED: {
            "label": "Assigned", "color": "#a855f7", "icon": "📌",
            "badge_class": "jk-badge-assigned",
            "next": {Status.IN_PROGRESS, Status.CANCELLED, Status.NEW},
            "primary_next": Status.IN_PROGRESS,
        },
        Status.IN_PROGRESS: {
            "label": "In Progress", "color": "#f59e0b", "icon": "🔍",
            "badge_class": "jk-badge-investigating",
            "next": {Status.PENDING_USER, Status.PENDING_EXTERNAL,
                     Status.RESOLVED, Status.CANCELLED, Status.ASSIGNED},
            "primary_next": Status.RESOLVED,
        },
        Status.PENDING_USER: {
            "label": "Pending User", "color": "#0ea5e9", "icon": "⏸️",
            "badge_class": "jk-badge-pending",
            "next": {Status.IN_PROGRESS},
            "primary_next": Status.IN_PROGRESS,
        },
        Status.PENDING_EXTERNAL: {
            "label": "Pending External", "color": "#ec4899", "icon": "📡",
            "badge_class": "jk-badge-pending-external",
            "next": {Status.IN_PROGRESS},
            "primary_next": Status.IN_PROGRESS,
        },
        Status.RESOLVED: {
            "label": "Resolved", "color": "#10b981", "icon": "✅",
            "badge_class": "jk-badge-resolved",
            "next": {Status.IN_PROGRESS, Status.CLOSED},
            "primary_next": Status.CLOSED,
        },
        Status.CLOSED: {
            "label": "Closed", "color": "#475569", "icon": "🔒",
            "badge_class": "jk-badge-closed", "next": set(), "primary_next": None,
        },
        Status.REJECTED: {
            "label": "Rejected", "color": "#ef4444", "icon": "🚫",
            "badge_class": "jk-badge-rejected", "next": set(), "primary_next": None,
        },
        Status.CANCELLED: {
            "label": "Cancelled", "color": "#78716c", "icon": "⛔",
            "badge_class": "jk-badge-cancelled", "next": set(), "primary_next": None,
        },
    }

    class Source(models.TextChoices):
        EVOLUTION_WA = "EvolutionAPI_WA", "WhatsApp (Evolution API)"
        EMAIL = "Email", "Email"
        WEBFORM = "WebForm", "Web Form"
        WEBCHAT = "WebChat", "Web Chat"
        TEAMS_BOT = "Teams_Bot", "Microsoft Teams (Bot)"

    class Impact(models.TextChoices):
        HIGH = "High", "High"
        MEDIUM = "Medium", "Medium"
        LOW = "Low", "Low"

    class Severity(models.TextChoices):
        SEV1 = "1", "Severity 1"
        SEV2 = "2", "Severity 2"
        SEV3 = "3", "Severity 3"

    class Priority(models.TextChoices):
        P1 = "P1", "P1"
        P2 = "P2", "P2"
        P3 = "P3", "P3"
        P4 = "P4", "P4"

    # Ascending urgency order (lowest first) — used by bump_priority() and
    # the case-list priority sort annotation.
    PRIORITY_ORDER = [Priority.P4, Priority.P3, Priority.P2, Priority.P1]

    FOLLOW_UP_COOLDOWN_HOURS = 24

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
    requester_phone = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Requester Phone",
        help_text="Phone number (E.164) provided by the requester on the web form.",
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
    location = models.ForeignKey(
        TicketLocation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cases",
        verbose_name="Location",
    )
    location_detail = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Location Detail",
        help_text="Required when the selected location requires a custom value.",
    )

    # --- Core fields ---
    subject = models.CharField(max_length=500, verbose_name="Subject")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
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
    impact = models.CharField(
        max_length=20,
        choices=Impact.choices,
        default=Impact.MEDIUM,
        verbose_name="Impact",
    )
    severity = models.CharField(
        max_length=5,
        choices=Severity.choices,
        default=Severity.SEV2,
        verbose_name="Severity",
    )
    priority = models.CharField(
        max_length=20,
        choices=Priority.choices,
        default=Priority.P3,
        verbose_name="Priority",
        help_text="Calculated from Severity × Impact matrix (P1–P4).",
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
    cc_emails = models.CharField(
        max_length=500,
        blank=True,
        verbose_name="CC Emails",
        help_text=(
            "Comma-separated external email addresses kept in the loop on every "
            "outbound email for this ticket (acknowledgment, staff replies, status "
            "changes) — distinct from Message.cc_emails, which is a one-off CC on "
            "a single reply."
        ),
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
    first_response_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="First Response At",
        help_text="Set once when the ticket is first assigned or first replied to by staff.",
    )
    resolution_due_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Resolution Due At",
        help_text="Deadline for case resolution.",
    )
    sla_paused_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="SLA Paused At",
        help_text="Set when status enters Pending User/Pending External. "
                   "Null while the resolution clock is running.",
    )
    sla_total_paused_duration = models.DurationField(
        default=timedelta,
        blank=True,
        verbose_name="Total SLA Paused Duration",
        help_text="Cumulative time spent paused, for reporting only — "
                   "resolution_due_at is pushed forward directly on "
                   "resume, so breach queries don't need to know this.",
    )

    # --- Follow-up / last user reply tracking ---
    last_user_reply_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last User Reply At",
        help_text="Timestamp of the most recent inbound message/reply from the requester.",
    )
    last_follow_up_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Last Follow-Up At",
        help_text="When the requester last used the Follow Up action to bump priority.",
    )
    follow_up_count = models.PositiveIntegerField(
        default=0,
        verbose_name="Follow-Up Count",
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
            self.Status.NEW,
            self.Status.ASSIGNED,
            self.Status.IN_PROGRESS,
            self.Status.PENDING_USER,
            self.Status.PENDING_EXTERNAL,
        )

    @property
    def terminal_statuses(self):
        """Statuses that have no outbound transitions."""
        return (self.Status.CLOSED, self.Status.REJECTED, self.Status.CANCELLED)

    @property
    def case_number(self) -> str:
        """Human-readable case identifier derived from category prefix and UUID."""
        prefix = self.category.prefix_code if self.category else "RQ"
        return f"{prefix}-{str(self.id)[:8].upper()}"

    @property
    def location_display(self) -> str:
        if not self.location_id:
            return ""
        if self.location.requires_detail and self.location_detail:
            return f"{self.location.name} — {self.location_detail}"
        return self.location.name

    def record_first_response(self, when=None, save=True) -> bool:
        if self.first_response_at:
            return False
        self.first_response_at = when or timezone.now()
        if save and self.pk:
            type(self).objects.filter(pk=self.pk, first_response_at__isnull=True).update(
                first_response_at=self.first_response_at,
            )
        return True

    @property
    def response_time_display(self) -> str:
        if not self.first_response_at or not self.created_at:
            return ""
        total = int((self.first_response_at - self.created_at).total_seconds())
        if total < 0:
            total = 0
        hours, rem = divmod(total, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}h {minutes}m"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    def can_transition_to(self, new_status: str) -> bool:
        """Shared transition gate — used by both CaseRCAForm.clean() and
        case_update_status(). True for a no-op (same status)."""
        if new_status == self.status:
            return True
        if self.status == self.Status.CLOSED:
            return False
        if self.is_locked:
            return False
        meta = self.STATUS_META.get(self.status)
        if not meta:
            return False
        return new_status in meta["next"]

    @classmethod
    def status_choices_from(cls, current_status):
        """Choices list restricted to the current status + its valid next
        statuses — feeds the status <select> so invalid targets are never
        offered, not just rejected server-side."""
        meta = cls.STATUS_META.get(current_status)
        allowed = meta["next"] if meta else set()
        codes = {current_status} | allowed
        return [(c, l) for c, l in cls.Status.choices if c in codes]

    @property
    def primary_next_status(self):
        """The recommended forward status code for the RCA form's one-click
        submit shortcut — explicit per status via STATUS_META["primary_next"],
        so a branching status (e.g. In Progress, which can also move to
        Pending User/External or back to Assigned) still gets a sensible
        one-click default instead of falling back to a bare "Submit"."""
        meta = self.STATUS_META.get(self.status)
        return meta.get("primary_next") if meta else None

    def apply_status_transition(self, new_status: str, *, old_status: str = None) -> None:
        """Applies SLA-pause + hold_wa_session side effects and sets
        self.status. Does NOT validate (call can_transition_to first) and
        does NOT save() — the caller persists, matching existing patterns
        where status is saved together with other fields."""
        old_status = self.status if old_status is None else old_status
        if old_status != new_status:
            pausing = {self.Status.PENDING_USER, self.Status.PENDING_EXTERNAL}
            if new_status in pausing and old_status not in pausing:
                self.sla_paused_at = timezone.now()
            elif old_status in pausing and new_status not in pausing and self.sla_paused_at:
                elapsed = timezone.now() - self.sla_paused_at
                if self.resolution_due_at:
                    self.resolution_due_at += elapsed
                self.sla_total_paused_duration = (self.sla_total_paused_duration or timedelta()) + elapsed
                self.sla_paused_at = None

            if new_status == self.Status.IN_PROGRESS:
                self.hold_wa_session = self.source == self.Source.EVOLUTION_WA
            elif new_status in (self.Status.RESOLVED, self.Status.CLOSED,
                                 self.Status.REJECTED, self.Status.CANCELLED):
                self.hold_wa_session = False

        self.status = new_status

    def calculate_priority(self):
        return PriorityMatrixRule.lookup(
            self.severity,
            self.impact,
            default=self.Priority.P3,
        )

    def can_follow_up(self):
        """(allowed: bool, reason: str) — whether the requester may bump
        priority right now: ticket active, not already at max priority,
        and the cooldown window has elapsed since the last follow-up."""
        if not self.is_active:
            return False, "This ticket is not currently active."
        if self.priority == self.Priority.P1:
            return False, "This ticket is already at the highest priority."
        if self.last_follow_up_at:
            elapsed = timezone.now() - self.last_follow_up_at
            cooldown = timedelta(hours=self.FOLLOW_UP_COOLDOWN_HOURS)
            if elapsed < cooldown:
                remaining = cooldown - elapsed
                hours_left = max(1, int(remaining.total_seconds() // 3600))
                return False, f"You can follow up again in about {hours_left}h."
        return True, ""

    def bump_priority(self) -> None:
        """Raises priority one level (capped at P1) by increasing Impact
        or decreasing Severity, then recalculates SLA deadlines from now
        using the new priority's SLAPolicy. Does NOT save() — caller
        persists, same convention as apply_status_transition()."""
        if self.impact == self.Impact.LOW:
            self.impact = self.Impact.MEDIUM
        elif self.impact == self.Impact.MEDIUM:
            self.impact = self.Impact.HIGH
        elif self.severity == self.Severity.SEV3:
            self.severity = self.Severity.SEV2
        elif self.severity == self.Severity.SEV2:
            self.severity = self.Severity.SEV1

        self.priority = self.calculate_priority()

        policy = SLAPolicy.objects.filter(priority=self.priority).first()
        if policy:
            now = timezone.now()
            self.response_due_at = now + timedelta(hours=policy.response_time_hours)
            self.resolution_due_at = now + timedelta(hours=policy.resolution_time_hours)

        self.follow_up_count += 1
        self.last_follow_up_at = timezone.now()

    def save(self, *args, **kwargs):
        is_new = self._state.adding

        self.priority = self.calculate_priority()
        
        # Pre-save modifications
        if is_new:
            # Apply category defaults
            if self.category:
                if self.category.default_case_type and not getattr(self, '_case_type_set_explicitly', False):
                    if not self.case_type or self.case_type == self.Type.INCIDENT:
                        self.case_type = self.category.default_case_type
                if self.category.default_tags and not self.tags:
                    self.tags = self.category.default_tags

                # Initial Status for Approval Routing
                if self.category.is_use_routing_approval:
                    self.status = self.Status.PENDING_APPROVAL

            # SLA calculation
            if not self.response_due_at or not self.resolution_due_at:
                try:
                    from datetime import timedelta
                    from django.utils import timezone
                    
                    policy = SLAPolicy.objects.filter(priority=self.priority).first()
                    if policy:
                        now = timezone.now()
                        if not self.response_due_at:
                            self.response_due_at = now + timedelta(hours=policy.response_time_hours)
                        if not self.resolution_due_at:
                            self.resolution_due_at = now + timedelta(hours=policy.resolution_time_hours)
                except Exception:
                    pass
                
        super().save(*args, **kwargs)
        
        # Post-save actions (Creation of related objects)
        if is_new and self.category and self.category.is_use_routing_approval:
            # Create Tier 1 Manager Approval
            has_manager = False
            if self.requester and getattr(self.requester, 'reports_to', None):
                TicketApproval.objects.create(
                    case=self,
                    tier=TicketApproval.Tier.MANAGER,
                    approver=self.requester.reports_to,
                    status=TicketApproval.Status.PENDING
                )
                has_manager = True
                
            # Create Tier 2 Admin Approval
            if self.category.is_need_admin_approval:
                TicketApproval.objects.create(
                    case=self,
                    tier=TicketApproval.Tier.ADMIN,
                    approver=None, # None implies Support Desk Admins pool
                    status=TicketApproval.Status.PENDING if has_manager else TicketApproval.Status.PENDING # Technically wait for T1, but we create it now
                )
            
            # If no manager and no admin required, auto-approve
            if not has_manager and not self.category.is_need_admin_approval:
                self.status = self.Status.NEW
                self.save(update_fields=['status'])



# =====================================================================
# Ticket Approval
# =====================================================================

class TicketApproval(AuditableModel):
    """
    Tracks the approval routing for a specific ticket.
    Tier 1 = Manager, Tier 2 = Admin/Support Desk
    """

    class Status(models.TextChoices):
        PENDING = "Pending", "Pending"
        APPROVED = "Approved", "Approved"
        REJECTED = "Rejected", "Rejected"

    class Tier(models.IntegerChoices):
        MANAGER = 1, "Tier 1 (Manager)"
        ADMIN = 2, "Tier 2 (Admin)"

    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="approvals",
        verbose_name="Ticket",
    )
    tier = models.PositiveSmallIntegerField(
        choices=Tier.choices,
        default=Tier.MANAGER,
        verbose_name="Approval Tier",
    )
    approver = models.ForeignKey(
        "core.Employee",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ticket_approvals",
        verbose_name="Approver",
        help_text="If null, any Support Desk staff/admin can approve."
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Status",
    )
    comments = models.TextField(
        blank=True,
        verbose_name="Comments/Notes",
    )
    actioned_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Actioned At",
    )

    class Meta:
        verbose_name = "Ticket Approval"
        verbose_name_plural = "Ticket Approvals"
        ordering = ["case", "tier"]

    def __str__(self):
        approver_name = self.approver.full_name if self.approver else "Admin"
        return f"{self.case.ticket_id} - Tier {self.tier} ({approver_name}) - {self.status}"


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

class CaseActivityRead(models.Model):
    """Read position per participant; opening a ticket never clears another user's badge."""

    case = models.ForeignKey(CaseRecord, on_delete=models.CASCADE, related_name="activity_reads")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="case_activity_reads")
    last_read_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["case", "user"], name="unique_case_activity_reader")]


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
    is_standalone = models.BooleanField(
        default=False,
        verbose_name="Standalone Form",
        help_text="If true, this form can be submitted independently from the e-Forms portal, without needing a Support Ticket."
    )
    logo = models.ImageField(
        upload_to="document_templates/logos/",
        null=True, blank=True,
        verbose_name="Document Logo"
    )
    form_stages = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Form Stages",
        help_text='List of stages (e.g. ["Initiator", "Vendor", "IT Ops"]). The first stage is automatically assigned to the form creator.'
    )
    is_archived = models.BooleanField(default=False, db_index=True, verbose_name="Archived")

    class Meta:
        verbose_name = "Document Template"
        verbose_name_plural = "Document Templates"
        ordering = ["title"]

    def __str__(self):
        return self.title

    def extract_placeholders(self):
        """Return unique list of placeholder names found in body_html."""
        return list(dict.fromkeys(re.findall(r'\{\{(\w+)\}\}', self.body_html)))

    def ordered_fields(self):
        return self.fields.order_by("order")


# =====================================================================
# Document Template Field
# =====================================================================

class DocumentTemplateField(AuditableModel):
    """
    A configurable input field belonging to a DocumentTemplate.
    Up to 10 fields per template; each field becomes a Quill editor in the portal form.
    """

    template = models.ForeignKey(
        DocumentTemplate,
        on_delete=models.CASCADE,
        related_name="fields",
        verbose_name="Template",
    )
    order = models.PositiveSmallIntegerField(
        default=1,
        verbose_name="Order",
        help_text="Display order (1 = first). Maximum 10 fields per template.",
    )
    label = models.CharField(max_length=200, verbose_name="Field Label")
    variable_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Variable Name",
        help_text="Unique identifier (e.g. 'vendor_name'). Used for PDF placeholders {{ vendor_name }} and E-Sign mapping."
    )
    placeholder = models.CharField(
        max_length=300,
        blank=True,
        verbose_name="Placeholder Hint",
        help_text="Optional hint text shown inside the empty editor.",
    )
    default_content = models.TextField(
        blank=True,
        verbose_name="Default Content",
        help_text="Optional HTML template pre-filled into the editor when the user opens the form. "
                  "Supports basic HTML tags. Leave blank for an empty editor.",
    )
    is_required = models.BooleanField(default=True, verbose_name="Required")
    
    class FieldType(models.TextChoices):
        TEXT = "text", "Short Text"
        RICHTEXT = "richtext", "Rich Text (HTML)"
        DATE = "date", "Date"
        REPEATER = "repeater", "Table / Repeater"
        
    field_type = models.CharField(
        max_length=20,
        choices=FieldType.choices,
        default=FieldType.RICHTEXT,
        verbose_name="Field Type"
    )
    
    is_signer = models.BooleanField(
        default=False,
        verbose_name="Is E-Signer?",
        help_text="Check this if the input of this field should be treated as a unique Signer (Name or Email) for the E-Sign process."
    )
    
    assigned_stage = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Assigned Stage",
        help_text="Which stage fills this field? (e.g. 'Vendor'). Leave blank for all stages."
    )

    class Meta:
        verbose_name = "Template Field"
        verbose_name_plural = "Template Fields"
        ordering = ["template", "order"]
        unique_together = [("template", "order")]

    def __str__(self):
        return f"{self.template.title} — Field {self.order}: {self.label}"


# =====================================================================
# Change Request Document (Surat Kronologi)
# =====================================================================

def change_request_pdf_path(instance, filename):
    """Generate upload path: media/change_requests/<case_uuid>/<filename>."""
    return f"change_requests/{instance.case.id}/{filename}"


class ChangeRequestDocument(AuditableModel):
    """
    A change-request / chronology letter submitted by the portal user.

    Two fields describe the request:
    - request_change: what change the requester wants.
    - chronology: background and context justifying the request.

    If approvers are selected, the document routes through an approval
    chain. On full approval the generated PDF is attached to the ticket
    as a Message+Attachment. If no approvers are selected the document
    auto-approves immediately.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending Approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    case = models.ForeignKey(
        CaseRecord,
        on_delete=models.CASCADE,
        related_name="change_requests",
        verbose_name="Ticket",
    )
    document_template = models.ForeignKey(
        DocumentTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="documents",
        verbose_name="Document Template",
    )
    # field_values stores dynamic field content as [{label, value}, ...]
    field_values = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Field Values",
    )
    # Legacy fields — kept nullable for existing records
    request_change = models.TextField(blank=True, verbose_name="Request Change")
    chronology = models.TextField(blank=True, verbose_name="Chronology")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    generated_pdf = models.FileField(
        upload_to=change_request_pdf_path,
        blank=True,
        null=True,
        verbose_name="Generated PDF",
    )
    revision_number = models.PositiveIntegerField(
        default=1,
        verbose_name="Revision Number",
    )
    submitted_at = models.DateTimeField(null=True, blank=True, verbose_name="Submitted At")

    class Meta:
        verbose_name = "Change Request Document"
        verbose_name_plural = "Change Request Documents"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Change Request — {self.case.case_number} [Rev.{self.revision_number}] [{self.status}]"

    @property
    def is_fully_approved(self) -> bool:
        """True when all approvals are collected with no pending or rejected entries."""
        approvals = self.approvals.all()
        if not approvals.exists():
            return True
        return not approvals.filter(
            status__in=[ChangeRequestApproval.Status.PENDING, ChangeRequestApproval.Status.REJECTED]
        ).exists()


# =====================================================================
# Change Request Approval
# =====================================================================

class ChangeRequestApproval(AuditableModel):
    """
    One approval action per approver for a ChangeRequestDocument.
    A UUID magic-link token lets the approver respond without logging in.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    document = models.ForeignKey(
        ChangeRequestDocument,
        on_delete=models.CASCADE,
        related_name="approvals",
        verbose_name="Change Request Document",
    )
    approver = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        related_name="change_request_approvals",
        verbose_name="Approver",
    )
    order = models.PositiveIntegerField(default=1, verbose_name="Approval Order")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        verbose_name="Status",
    )
    notes = models.TextField(blank=True, verbose_name="Notes")
    token = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        db_index=True,
        verbose_name="Access Token",
        help_text="Magic-link token for approver access without login.",
    )
    token_expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Token Expires At")
    acted_at = models.DateTimeField(null=True, blank=True, verbose_name="Acted At")

    class Meta:
        verbose_name = "Change Request Approval"
        verbose_name_plural = "Change Request Approvals"
        ordering = ["document", "order"]
        unique_together = ("document", "approver")

    def __str__(self):
        return f"{self.document} — {self.approver} [{self.status}]"

    @property
    def is_token_expired(self) -> bool:
        if not self.token_expires_at:
            return False
        from django.utils import timezone
        return timezone.now() > self.token_expires_at


# =====================================================================
# E-Form Multi-Stage Submissions (Phase 1)
# =====================================================================

def eform_pdf_path(instance, filename):
    return f"eforms/{instance.id}/pdf/{filename}"

class FormSubmission(AuditableModel):
    """
    Acts as the source of truth for standalone, multi-stage form data.
    """
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_PROGRESS = "in_progress", "In Progress"
        PENDING_ESIGN = "pending_esign", "Pending E-Sign"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID")
    template = models.ForeignKey(
        DocumentTemplate, 
        on_delete=models.PROTECT, 
        related_name="form_submissions", 
        verbose_name="Template"
    )
    initiator_user = models.ForeignKey(
        "core.User", 
        on_delete=models.SET_NULL, 
        null=True, blank=True, 
        related_name="initiated_forms",
        verbose_name="Initiator"
    )
    data_payload = models.JSONField(
        default=dict, 
        blank=True, 
        verbose_name="Form Data Payload"
    )
    current_stage = models.CharField(
        max_length=100, 
        blank=True, 
        verbose_name="Current Stage"
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status"
    )
    guest_token = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        verbose_name="Guest Token",
        help_text="Allows public/vendor access to fill their assigned stage."
    )
    generated_pdf = models.FileField(
        upload_to=eform_pdf_path,
        null=True, blank=True,
        verbose_name="Generated PDF"
    )
    signature_document = models.ForeignKey(
        "esign.SignatureDocument",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="source_form",
        verbose_name="E-Sign Document"
    )

    class Meta:
        verbose_name = "Form Submission"
        verbose_name_plural = "Form Submissions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.template.title} - {self.id} [{self.status}]"
