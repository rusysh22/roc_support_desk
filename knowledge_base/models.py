"""
Knowledge Base App — Models
=============================
Auto-generated solution articles derived from resolved CaseRecords.

When a case is closed, staff can generate an ``Article`` from the
case's ``root_cause_analysis`` and ``solving_steps``, creating a
searchable knowledge base for future reference.
"""
from django.db import models
from django.utils.text import slugify

from core.models import AuditableModel


class Article(AuditableModel):
    """
    Knowledge base article derived from a resolved CaseRecord.

    Links back to the originating case and category for traceability.
    Can also be created independently by staff.
    """

    title = models.CharField(max_length=500, verbose_name="Title")
    slug = models.SlugField(max_length=520, unique=True, blank=True, verbose_name="Slug")

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

    problem_summary = models.TextField(
        verbose_name="Problem Summary",
        help_text="Concise description of the problem.",
    )
    root_cause = models.TextField(
        verbose_name="Root Cause",
        help_text="Documented root cause of the issue.",
    )
    solution = models.TextField(
        verbose_name="Solution",
        help_text="Step-by-step resolution procedure.",
    )

    is_published = models.BooleanField(
        default=False,
        db_index=True,
        verbose_name="Published",
        help_text="Only published articles are visible in the knowledge base.",
    )

    class Meta:
        verbose_name = "Article"
        verbose_name_plural = "Articles"
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        """Auto-generate slug from title if not provided."""
        if not self.slug:
            base_slug = slugify(self.title)[:500]
            slug = base_slug
            counter = 1
            while Article.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        status = "✅" if self.is_published else "📝"
        return f"{status} {self.title}"
