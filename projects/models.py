"""
Projects App — Models
======================
Provides models for:

- ``SharedDocument`` — Infographic/HTML/Markdown documents shareable via public link.
- ``Project``        — Project overview with phases and updates for client visibility.
- ``ProjectPhase``   — Individual phases within a project (Blueprint, UAT, etc.).
- ``ProjectUpdate``  — Progress updates attached to a phase, optionally linking SharedDocs.
- ``ProjectComment`` — Public or authenticated comments on a phase.
"""
from django.db import models
from django.core.cache import cache
from core.models import AuditableModel


# SharedDocument has been moved to shared_docs/models.py


# =====================================================================
# Project
# =====================================================================

class Project(AuditableModel):
    """
    Top-level project record shared with clients/management via a public link.

    The ``is_public`` flag controls whether the timeline page is accessible
    without authentication. Setting it to False returns a 404 to unauthenticated
    visitors, effectively making it private.
    """

    class Status(models.TextChoices):
        PLANNING   = "PLANNING",   "Planning"
        ACTIVE     = "ACTIVE",     "Active"
        ON_HOLD    = "ON_HOLD",    "On Hold"
        COMPLETED  = "COMPLETED",  "Completed"
        CANCELLED  = "CANCELLED",  "Cancelled"

    name = models.CharField(
        max_length=255,
        verbose_name="Project Name",
        help_text="Full project name (e.g. ERP D365 Implementation — PGE).",
    )
    client_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Client / Entity",
        help_text="Name of the client or entity this project belongs to.",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description",
        help_text="Short description shown at the top of the public timeline.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNING,
        verbose_name="Status",
    )
    is_public = models.BooleanField(
        default=True,
        verbose_name="Public",
        help_text="If enabled, anyone with the link can view the project timeline.",
    )
    allow_public_crud = models.BooleanField(
        default=False,
        verbose_name="Public Editable",
        help_text="If enabled, unauthenticated visitors with the link can add phases and updates.",
    )
    followers = models.ManyToManyField(
        "core.User",
        blank=True,
        related_name="followed_projects",
        verbose_name="Editors / Followers",
        help_text="Specific staff users allowed to edit this project.",
    )

    class Meta:
        verbose_name = "Project"
        verbose_name_plural = "Projects"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


# =====================================================================
# Project Phase
# =====================================================================

class ProjectPhase(AuditableModel):
    """
    An individual phase within a Project (e.g. Blueprint, Development, UAT).

    Phases are ordered by the ``order`` field. Status transitions are tracked
    to provide a visual timeline to clients.
    """

    class Status(models.TextChoices):
        PENDING     = "PENDING",      "Pending"
        IN_PROGRESS = "IN_PROGRESS",  "In Progress"
        REVIEW      = "REVIEW",       "Under Review"
        REVISED     = "REVISED",      "Revised"
        COMPLETED   = "COMPLETED",    "Completed"
        SKIPPED     = "SKIPPED",      "Skipped"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="phases",
        verbose_name="Project",
    )
    name = models.CharField(
        max_length=150,
        verbose_name="Phase Name",
        help_text="Phase name (e.g. Blueprint Signed, Development, UAT).",
    )
    order = models.IntegerField(
        default=0,
        verbose_name="Order",
        help_text="Display order — lower numbers appear first (can be negative).",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Status",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description",
        help_text="Phase description or deliverable notes.",
    )
    start_date = models.DateField(null=True, blank=True, verbose_name="Start Date")
    end_date   = models.DateField(null=True, blank=True, verbose_name="End / Target Date")

    class Meta:
        verbose_name = "Project Phase"
        verbose_name_plural = "Project Phases"
        ordering = ["order"]

    def __str__(self):
        return f"{self.project.name} › {self.name}"


# =====================================================================
# Project Update
# =====================================================================

class ProjectUpdate(AuditableModel):
    """
    A progress update attached to a ProjectPhase.

    Updates can optionally link to a SharedDocument (infographic) or carry
    a file attachment (photo, small video, PDF). Both channels are surfaced
    on the public timeline.
    """
    phase = models.ForeignKey(
        ProjectPhase,
        on_delete=models.CASCADE,
        related_name="updates",
        verbose_name="Phase",
    )
    title = models.CharField(
        max_length=200,
        verbose_name="Update Title",
        help_text="Short title (e.g. Kick-off Meeting completed).",
    )
    content = models.TextField(
        blank=True,
        verbose_name="Detail",
        help_text="Full detail or outcome of this update.",
    )
    shared_doc = models.ForeignKey(
        "shared_docs.SharedDocument",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="project_updates",
        verbose_name="Linked Document",
        help_text="Optionally link to a Shared Infographic document.",
    )
    attachment = models.FileField(
        upload_to="projects/updates/",
        null=True,
        blank=True,
        verbose_name="Attachment",
        help_text="Upload a photo, short video, or small document.",
    )
    update_date = models.DateField(
        null=True, 
        blank=True,
        verbose_name="Update Date",
        help_text="Date of this update. Defaults to today if left blank."
    )

    class Meta:
        verbose_name = "Project Update"
        verbose_name_plural = "Project Updates"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.phase.name}] {self.title}"


# =====================================================================
# Project Comment
# =====================================================================

class ProjectComment(AuditableModel):
    """
    A comment on a ProjectPhase — supports both authenticated users and
    unauthenticated guests (client-side visitors with the public link).

    Rate limiting is enforced in the view layer (not the model).
    Either ``user`` or ``guest_name`` must be provided; the view validates this.
    """
    phase = models.ForeignKey(
        ProjectPhase,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="Phase",
    )
    user = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="project_comments",
        verbose_name="Staff User",
        help_text="Set automatically when an authenticated staff user comments.",
    )
    guest_name = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Guest Name",
        help_text="Display name for unauthenticated visitors.",
    )
    guest_email = models.EmailField(
        blank=True,
        verbose_name="Guest Email",
        help_text="Contact email for unauthenticated visitors (not shown publicly).",
    )
    comment = models.TextField(verbose_name="Comment")
    is_visible = models.BooleanField(
        default=True,
        verbose_name="Visible",
        help_text="Uncheck to soft-hide a comment without deleting it.",
    )

    class Meta:
        verbose_name = "Project Comment"
        verbose_name_plural = "Project Comments"
        ordering = ["created_at"]

    def __str__(self):
        author = self.user.username if self.user else self.guest_name or "Anonymous"
        return f"Comment by {author} on {self.phase.name}"

    @property
    def author_display(self):
        """Human-readable author name for templates."""
        if self.user:
            return self.user.username
        return self.guest_name or "Anonymous"


# =====================================================================
# Phase Checklist
# =====================================================================

class PhaseChecklist(AuditableModel):
    """
    A simple task or checklist item attached to a ProjectPhase.
    Allows staff to check off granular tasks within a phase.
    """
    phase = models.ForeignKey(
        ProjectPhase,
        on_delete=models.CASCADE,
        related_name="checklists",
        verbose_name="Phase",
    )
    task_name = models.CharField(
        max_length=255,
        verbose_name="Task Name",
        help_text="Description of the task to be completed.",
    )
    is_completed = models.BooleanField(
        default=False,
        verbose_name="Completed",
    )
    order = models.IntegerField(
        default=0,
        verbose_name="Order",
        help_text="Display order — lower numbers appear first (can be negative).",
    )

    class Meta:
        verbose_name = "Phase Checklist Item"
        verbose_name_plural = "Phase Checklist Items"
        ordering = ["order", "created_at"]

    def __str__(self):
        status = "[x]" if self.is_completed else "[ ]"
        return f"{status} {self.task_name}"

