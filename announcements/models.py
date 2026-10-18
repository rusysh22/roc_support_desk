"""
Announcements — Models
======================
General-purpose announcements shown at the bottom of the Ticket Portal.
Any module can post one through ``announcements.services.post_announcement``.
"""
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import AuditableModel


def validate_internal_path(value):
    if value and (not value.startswith("/") or value.startswith("//")):
        raise ValidationError("Use an internal path that starts with '/', e.g. /kb/my-article/.")


class Announcement(AuditableModel):
    class SourceType(models.TextChoices):
        MANUAL = "MANUAL", "General"
        KNOWLEDGE_BASE = "KNOWLEDGE_BASE", "Knowledge Base"
        SYSTEM = "SYSTEM", "System"

    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", "Everyone (including guests)"
        AUTHENTICATED = "AUTHENTICATED", "All signed-in users"
        TARGETED = "TARGETED", "Users of selected company units"

    class Priority(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        IMPORTANT = "IMPORTANT", "Important"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        ARCHIVED = "ARCHIVED", "Archived"

    title = models.CharField(max_length=200, verbose_name="Title")
    summary = models.CharField(
        max_length=300, blank=True, default="", verbose_name="Summary",
        help_text="One or two sentences shown on the Ticket Portal card.",
    )
    body = models.TextField(blank=True, default="", verbose_name="Body")
    link_url = models.CharField(
        max_length=500, blank=True, default="", validators=[validate_internal_path], verbose_name="Link",
        help_text="Optional internal page to open, e.g. /kb/article-slug/.",
    )
    source_type = models.CharField(
        max_length=30, choices=SourceType.choices, default=SourceType.MANUAL, db_index=True, verbose_name="Source",
    )
    source_content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True)
    source_object_id = models.CharField(max_length=64, blank=True, default="")
    source = GenericForeignKey("source_content_type", "source_object_id")
    visibility = models.CharField(
        max_length=20, choices=Visibility.choices, default=Visibility.AUTHENTICATED, verbose_name="Audience",
    )
    target_entities = models.ManyToManyField(
        "core.CompanyUnit", blank=True, related_name="announcements", verbose_name="Target Company Units",
    )
    priority = models.CharField(
        max_length=10, choices=Priority.choices, default=Priority.NORMAL, verbose_name="Priority",
    )
    is_pinned = models.BooleanField(default=False, verbose_name="Pinned")
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True, verbose_name="Status",
    )
    publish_at = models.DateTimeField(default=timezone.now, verbose_name="Publish At")
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Expires At")
    require_acknowledgement = models.BooleanField(default=False, verbose_name="Require acknowledgement")
    # Set once e-mails are queued, so re-saving never notifies twice.
    notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Announcement"
        verbose_name_plural = "Announcements"
        ordering = ["-is_pinned", "-publish_at"]
        indexes = [
            models.Index(fields=["status", "publish_at"]),
            models.Index(fields=["source_content_type", "source_object_id"]),
        ]

    def clean(self):
        super().clean()
        if self.expires_at and self.publish_at and self.expires_at <= self.publish_at:
            raise ValidationError({"expires_at": "Expiry must be after the publish time."})

    @property
    def is_live(self):
        now = timezone.now()
        return (
            self.status == self.Status.PUBLISHED
            and self.publish_at <= now
            and (self.expires_at is None or self.expires_at > now)
        )

    def __str__(self):
        return self.title


class AnnouncementReceipt(models.Model):
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name="receipts")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="announcement_receipts")
    read_at = models.DateTimeField(auto_now_add=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Announcement Receipt"
        verbose_name_plural = "Announcement Receipts"
        constraints = [
            models.UniqueConstraint(fields=["announcement", "user"], name="announcement_receipt_unique"),
        ]
