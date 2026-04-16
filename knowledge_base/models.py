"""
Knowledge Base App — Models
=============================
Searchable knowledge base articles for future reference.

Articles can be created independently by staff or generated from
resolved CaseRecords. Supports rich text content with embedded images,
tagging, and a Manager/SuperAdmin approval workflow before publishing.
"""
from django.conf import settings
from django.db import models
from django.utils.text import slugify

from core.models import AuditableModel


def kb_image_upload_path(instance, filename):
    """Upload path for images embedded in KB articles."""
    return f"kb_images/{filename}"


class ArticleTag(AuditableModel):
    """Reusable tag for categorising knowledge base articles."""

    name = models.CharField(max_length=100, unique=True, verbose_name="Tag Name")
    slug = models.SlugField(max_length=120, unique=True, blank=True)

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ArticleImage(AuditableModel):
    """Image uploaded via the Quill editor for embedding in article content."""

    image = models.ImageField(upload_to=kb_image_upload_path, verbose_name="Image")
    alt_text = models.CharField(max_length=255, blank=True, verbose_name="Alt Text")

    class Meta:
        verbose_name = "Article Image"
        verbose_name_plural = "Article Images"

    def __str__(self):
        return self.alt_text or self.image.name


class Article(AuditableModel):
    """
    Knowledge base article.

    Supports rich HTML content (via Quill.js), optional linking to a
    source CaseRecord, tagging, and a two-stage publish workflow:

    Status flow:
        Draft  →  Pending Review  →  Published
                                  →  Rejected (back to Draft)
    """

    class Status(models.TextChoices):
        DRAFT = "Draft", "Draft"
        PENDING = "Pending", "Pending Review"
        PUBLISHED = "Published", "Published"
        REJECTED = "Rejected", "Rejected"

    class ArticleType(models.TextChoices):
        ISSUE = "Issue", "Issue Handling"
        ANNOUNCEMENT = "Announcement", "Announcement"

    title = models.CharField(max_length=500, verbose_name="Title")
    slug = models.SlugField(max_length=520, unique=True, blank=True, verbose_name="Slug")
    
    article_type = models.CharField(
        max_length=20,
        choices=ArticleType.choices,
        default=ArticleType.ISSUE,
        verbose_name="Article Type",
    )

    category = models.ForeignKey(
        "cases.CaseCategory",
        on_delete=models.PROTECT,
        related_name="articles",
        verbose_name="Category",
    )
    source_case = models.ForeignKey(
        "cases.CaseRecord",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="articles",
        verbose_name="Source Case",
        help_text="The CaseRecord this article was generated from (if any).",
    )

    # Rich text content fields (stored as HTML from Quill.js)
    problem_summary = models.TextField(
        blank=True,
        verbose_name="Problem Summary",
        help_text="Concise description of the problem.",
    )
    root_cause = models.TextField(
        blank=True,
        verbose_name="Root Cause",
        help_text="Documented root cause of the issue.",
    )
    solution = models.TextField(
        blank=True,
        verbose_name="Solution",
        help_text="Step-by-step resolution procedure.",
    )

    # Comments
    allow_comments = models.BooleanField(
        default=True,
        verbose_name="Allow Comments",
        help_text="Allow logged-in users to post comments on this article.",
    )

    # Tags
    tags = models.ManyToManyField(
        ArticleTag,
        blank=True,
        related_name="articles",
        verbose_name="Tags",
    )

    # Approval workflow
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
        verbose_name="Status",
    )
    is_published = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Published",
        help_text="Auto-set when status becomes Published.",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_articles",
        verbose_name="Reviewed By",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Reviewed At",
    )
    rejection_reason = models.TextField(
        blank=True,
        verbose_name="Rejection Reason",
    )

    class Meta:
        verbose_name = "Article"
        verbose_name_plural = "Articles"
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        """Auto-generate slug and sync is_published with status."""
        if not self.slug:
            base_slug = slugify(self.title)[:500]
            slug = base_slug
            counter = 1
            while Article.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        # Keep is_published in sync with status
        self.is_published = self.status == self.Status.PUBLISHED
        super().save(*args, **kwargs)

    def __str__(self):
        status_icons = {
            self.Status.DRAFT: "\U0001f4dd",
            self.Status.PENDING: "\u23f3",
            self.Status.PUBLISHED: "\u2705",
            self.Status.REJECTED: "\u274c",
        }
        icon = status_icons.get(self.status, "")
        return f"{icon} {self.title}"


class ArticleComment(AuditableModel):
    """
    User comment on a published KB article.
    Only authenticated users may submit; max 250 characters.
    """

    article = models.ForeignKey(
        Article,
        on_delete=models.CASCADE,
        related_name="comments",
        verbose_name="Article",
    )
    user = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        related_name="kb_comments",
        verbose_name="User",
    )
    body = models.CharField(max_length=250, verbose_name="Comment")

    class Meta:
        verbose_name = "Article Comment"
        verbose_name_plural = "Article Comments"
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.user.username} on '{self.article.title}': {self.body[:50]}"
