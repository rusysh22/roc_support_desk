import qrcode
from io import BytesIO
from django.db import models
from django.core.files.base import ContentFile
from django.conf import settings
from core.models import AuditableModel


class ShortLink(AuditableModel):
    """
    Model strictly for URL shortening and generating social cards.
    Tracks clicks and stores metadata (title, description).
    """
    target_url = models.URLField(max_length=2000, verbose_name="Target URL")
    slug = models.CharField(max_length=100, unique=True, verbose_name="Custom Slug", help_text="e.g. 'promo2024'")
    
    # Social Card / Metadata details
    title = models.CharField(max_length=255, blank=True, verbose_name="Card Title")
    description = models.TextField(blank=True, verbose_name="Card Description")
    
    clicks = models.PositiveIntegerField(default=0, verbose_name="Click Count")
    
    qr_code = models.ImageField(upload_to="qr_codes/", blank=True, null=True, verbose_name="QR Code Image")

    class Meta:
        verbose_name = "Short Link"
        verbose_name_plural = "Short Links"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.slug} -> {self.target_url}"

    def build_qr_code(self, current_host):
        """
        Generates and saves the QR code for this specific link.
        Needs the `current_host` to form the full URL for the QR code.
        """
        # Formulate full URL (e.g., https://example.com/s/my_slug)
        # Using string methods to prevent multiple slashes.
        url_prefix = current_host.rstrip('/')
        full_url = f"{url_prefix}/s/{self.slug}/"

        qr_img = qrcode.make(full_url)
        
        # Save to BytesIO
        canvas = BytesIO()
        qr_img.save(canvas, format="PNG")
        
        file_name = f"qr_{self.slug}.png"
        self.qr_code.save(file_name, ContentFile(canvas.getvalue()), save=False)
