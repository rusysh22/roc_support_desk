import io
import uuid

import qrcode
from django.core.files.base import ContentFile
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.conf import settings

MEDIA_TYPE_CHOICES = [("image", "Image"), ("video", "Video")]
MAX_PORTAL_COUNTDOWN = 600  # seconds = 10 minutes
PORTAL_LAYOUT_CHOICES = [("full", "Full page"), ("mini", "Mini page")]


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
    is_archived = models.BooleanField(default=False, db_index=True, verbose_name="Archived")
    qr_code = models.ImageField(
        upload_to="qr_codes/", blank=True, null=True, verbose_name="QR Code Image"
    )

    # --- Advanced redirect options ---
    require_login = models.BooleanField(
        default=False, verbose_name="Require Login",
        help_text="Visitors must sign in before being redirected.",
    )
    portal_enabled = models.BooleanField(
        default=False, verbose_name="Redirect Portal",
        help_text="Show an intermediate page (message / image / video) before redirecting.",
    )
    portal_countdown = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(0), MaxValueValidator(MAX_PORTAL_COUNTDOWN)],
        verbose_name="Countdown (seconds)",
        help_text="Seconds before auto-redirect, at most 10 minutes (600). 0 = no countdown, visitor clicks Continue.",
    )
    portal_layout = models.CharField(
        max_length=5, choices=PORTAL_LAYOUT_CHOICES, default="full", verbose_name="Portal layout",
        help_text="Full page: the video / image large on the left, the message and redirect on the right (desktop). "
                  "Mini page: one small centred card.",
    )
    portal_message = models.TextField(blank=True, verbose_name="Portal Message")
    portal_media = models.FileField(
        upload_to="short_links/portal/", blank=True, null=True, verbose_name="Portal Image / Video"
    )
    portal_media_type = models.CharField(
        max_length=10, blank=True, choices=MEDIA_TYPE_CHOICES, verbose_name="Portal Media Type"
    )
    portal_video_url = models.URLField(
        max_length=500, blank=True, verbose_name="Video Link",
        help_text="A YouTube or TikTok video shown on the portal page instead of an uploaded file.",
    )
    portal_media_has_audio = models.BooleanField(
        null=True, blank=True, default=None, editable=False,
        verbose_name="Uploaded video has a sound track",
        help_text="Checked when the video is uploaded; None = unknown.",
    )
    portal_media_sound = models.BooleanField(
        default=True, verbose_name="Play video with sound",
        help_text="Browsers only allow sound after the visitor interacts with the page; until then the "
                  "video starts muted with a button to turn the sound on.",
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

    @property
    def portal_countdown_label(self):
        """"45s", "2m" or "1m 30s" — short form for lists."""
        seconds = self.portal_countdown or 0
        minutes, rest = divmod(seconds, 60)
        if not minutes:
            return f"{seconds}s"
        return f"{minutes}m" + (f" {rest}s" if rest else "")

    @property
    def portal_embed(self):
        """The embedded YouTube / TikTok player for ``portal_video_url``, or None."""
        from .media import embed_for

        return embed_for(self.portal_video_url, sound=self.portal_media_sound) if self.portal_video_url else None

    def get_short_url(self):
        from django.conf import settings
        try:
            from core.models import SiteConfig
            base = SiteConfig.get_solo().site_url or getattr(settings, "SITE_URL", "")
        except Exception:
            base = getattr(settings, "SITE_URL", "")
        return f"{base.rstrip('/')}/s/{self.slug}/"

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
