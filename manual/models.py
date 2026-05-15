"""Manual app models — tree-structured user manual with TipTap JSON content."""
from django.db import models
from django.conf import settings
from django.utils.text import slugify


class ManualLandingConfig(models.Model):
    """Singleton: controls the hero section on the User Manual landing page."""
    hero_title = models.CharField(
        max_length=200,
        default="Documentation & User Guide",
    )
    hero_subtitle = models.TextField(
        blank=True,
        default="Select an application manual to get started.",
    )
    hero_image = models.ImageField(
        upload_to="manual/hero/",
        blank=True,
        null=True,
        help_text="Optional background image for the landing page hero section.",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Landing Page Config"

    @classmethod
    def get_solo(cls):
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create()
        return obj

    def __str__(self):
        return "Landing Page Config"


class Manual(models.Model):
    """Top-level application manual (e.g. D365, HR System)."""
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=10, default="📗", help_text="Emoji icon")
    is_published = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="manuals_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]
        verbose_name = "Manual"
        verbose_name_plural = "Manuals"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.title

    def get_root_pages(self):
        return self.pages.filter(parent=None, is_published=True).order_by("order", "title")

    def get_all_root_pages(self):
        """Include unpublished, for editor views."""
        return self.pages.filter(parent=None).order_by("order", "title")


class ManualPage(models.Model):
    """A page node in a manual — self-referencing tree with unlimited depth."""
    manual = models.ForeignKey(Manual, on_delete=models.CASCADE, related_name="pages")
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="children",
    )
    title = models.CharField(max_length=300)
    slug = models.SlugField(max_length=320, blank=True)
    content = models.JSONField(default=dict, blank=True, help_text="TipTap JSON document")
    is_faq = models.BooleanField(default=False, help_text="Mark this node as FAQ")
    is_published = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="manual_pages_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]
        verbose_name = "Manual Page"
        verbose_name_plural = "Manual Pages"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.title) or f"page-{self.pk or 0}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.manual.title} / {self.title}"

    def get_children(self):
        return self.children.filter(is_published=True).order_by("order", "title")

    def get_all_children(self):
        return self.children.all().order_by("order", "title")

    def breadcrumb(self):
        """Returns list of ancestors from root to self."""
        crumbs = []
        node = self
        while node:
            crumbs.insert(0, node)
            node = node.parent
        return crumbs

    def is_leaf(self):
        return not self.children.exists()
