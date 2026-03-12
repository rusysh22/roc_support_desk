import io
import uuid

import qrcode
from django.core.files.base import ContentFile
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
        return f"{base}/s/{self.slug}/"

    def save(self, *args, **kwargs):
        generate_qr = not self.qr_code
        super().save(*args, **kwargs)
        if generate_qr:
            self._generate_qr_code()

    def _generate_qr_code(self):
        """Generate and save a QR code image for this short link."""
        short_url = self.get_short_url()
        if not short_url or short_url.startswith("/"):
            short_url = f"/s/{self.slug}/"

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_H,
            box_size=10,
            border=2,
        )
        qr.add_data(short_url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="#1e293b", back_color="#ffffff")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        filename = f"qr-{self.slug}.png"
        # Use update_fields to avoid triggering save() recursion
        self.qr_code.save(filename, ContentFile(buffer.read()), save=False)
        ShortLink.objects.filter(pk=self.pk).update(qr_code=self.qr_code.name)
