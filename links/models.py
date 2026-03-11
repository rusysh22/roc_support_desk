import uuid
from django.db import models
from django.conf import settings


class ShortLink(models.Model):
    id = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False, verbose_name="ID"
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Created At")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Updated At")

    target_url = models.URLField(max_length=2000, verbose_name="Target URL")
    slug = models.CharField(
        max_length=100, unique=True,
        verbose_name="Custom Slug",
        help_text="e.g. 'promo2024'"
    )
    title = models.CharField(max_length=255, blank=True, verbose_name="Card Title")
    description = models.TextField(blank=True, verbose_name="Card Description")
    clicks = models.PositiveIntegerField(default=0, verbose_name="Click Count")
    qr_code = models.ImageField(
        upload_to="qr_codes/", blank=True, null=True, verbose_name="QR Code Image"
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="links_shortlink_created",
        verbose_name="Created By"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="links_shortlink_updated",
        verbose_name="Updated By"
    )

    class Meta:
        verbose_name = "Short Link"
        verbose_name_plural = "Short Links"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.slug} → {self.target_url[:60]}"

    def get_short_url(self):
        from django.conf import settings
        base = getattr(settings, "SITE_URL", "")
        return f"{base}/l/{self.slug}/"
