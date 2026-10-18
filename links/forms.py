import logging

from django import forms
from django.db import transaction
from .models import ShortLink
from django.core.exceptions import ValidationError
import re

from .media import VideoLinkError, classify_upload, compress_image, normalize_video_link, uploaded_video_has_audio
from core.upload_security import safe_filename

logger = logging.getLogger(__name__)

INPUT_CLASS = "w-full border border-gray-300 rounded-lg px-4 py-2 focus:ring focus:ring-indigo-100"


class ShortLinkForm(forms.ModelForm):
    portal_media_file = forms.FileField(
        required=False, label="Image or Video",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp,image/gif,video/mp4,video/webm,video/quicktime"}),
    )
    remove_portal_media = forms.BooleanField(required=False, label="Remove current media")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["portal_layout"].required = False

    class Meta:
        model = ShortLink
        fields = [
            "target_url", "slug", "title", "description",
            "require_login", "portal_enabled", "portal_countdown", "portal_layout", "portal_message",
            "portal_video_url", "portal_media_sound",
        ]
        widgets = {
            "target_url": forms.URLInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "https://example.com/very/long/url..."
            }),
            "slug": forms.TextInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "custom-slug-here"
            }),
            "title": forms.TextInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "Catchy Title for Social Card"
            }),
            "description": forms.Textarea(attrs={
                "class": INPUT_CLASS,
                "rows": 3,
                "placeholder": "Brief description..."
            }),
            "portal_countdown": forms.NumberInput(attrs={
                "class": INPUT_CLASS, "min": 0, "max": 600,
            }),
            "portal_video_url": forms.URLInput(attrs={
                "class": INPUT_CLASS,
                "placeholder": "Add a video link (YouTube or TikTok)",
            }),
            "portal_media_sound": forms.CheckboxInput(attrs={
                "class": "h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500",
            }),
            "portal_message": forms.Textarea(attrs={
                "class": INPUT_CLASS,
                "rows": 3,
                "placeholder": "e.g. You are leaving the support portal. Please keep your credentials private.",
            }),
        }

    def clean_slug(self):
        slug = self.cleaned_data.get("slug")
        if not slug:
            raise ValidationError("Slug cannot be empty.")

        # Alphanumeric and hyphens/underscores only
        if not re.match(r'^[\w-]+$', slug):
            raise ValidationError("Slug can only contain letters, numbers, hyphens, and underscores.")

        return slug

    def clean_portal_layout(self):
        # not sent by older clients / partial posts: keep what the link has (new links start as the full page)
        return self.cleaned_data.get("portal_layout") or self.instance.portal_layout or "full"

    def clean_portal_countdown(self):
        seconds = self.cleaned_data.get("portal_countdown")
        if seconds is not None and seconds > 600:
            raise ValidationError("Redirect after can be at most 10 minutes.")
        return seconds

    def clean_portal_video_url(self):
        url = (self.cleaned_data.get("portal_video_url") or "").strip()
        if not url:
            return ""
        try:
            return normalize_video_link(url)["canonical"]
        except VideoLinkError as exc:
            raise ValidationError(str(exc)) from exc

    def clean(self):
        cleaned = super().clean()
        url = cleaned.get("portal_video_url") or ""
        if url and cleaned.get("portal_media_file") and url != (self.instance.portal_video_url or ""):
            self.add_error("portal_video_url", "Use either an uploaded file or a video link, not both.")
        return cleaned

    def clean_portal_media_file(self):
        f = self.cleaned_data.get("portal_media_file")
        if not f:
            return f
        kind, mime, error = classify_upload(f)
        if error:
            raise ValidationError(error)
        self._media_kind, self._media_mime = kind, mime
        return f

    def save(self, commit=True):
        instance = super().save(commit=False)
        old_name = instance.portal_media.name if instance.portal_media else ""
        new_file = self.cleaned_data.get("portal_media_file")
        queue_video = False

        # One media per link: a new file replaces the video link, a (new) link replaces the file.
        if new_file:
            instance.portal_video_url = ""
        elif instance.portal_video_url:
            instance.portal_media = None
            instance.portal_media_type = ""
            instance.portal_media_has_audio = None

        if new_file:
            if self._media_kind == "image":
                compressed = compress_image(new_file, self._media_mime)
                instance.portal_media.save(compressed.name, compressed, save=False)
            else:
                new_file.seek(0)
                instance.portal_media_has_audio = uploaded_video_has_audio(new_file)  # before it is re-encoded
                instance.portal_media.save(safe_filename(new_file.name, self._media_mime), new_file, save=False)
                queue_video = True
            instance.portal_media_type = self._media_kind
            if self._media_kind == "image":
                instance.portal_media_has_audio = None
        elif self.cleaned_data.get("remove_portal_media"):
            instance.portal_media = None
            instance.portal_media_type = ""
            instance.portal_media_has_audio = None

        if commit:
            instance.save()
            self._save_m2m()
            new_name = instance.portal_media.name if instance.portal_media else ""
            if old_name and old_name != new_name:
                ShortLink._meta.get_field("portal_media").storage.delete(old_name)
            if queue_video:
                transaction.on_commit(lambda: _queue_video_compression(instance.pk, new_name))
        return instance


def _queue_video_compression(link_id, media_name):
    from .tasks import compress_portal_video
    try:
        compress_portal_video.delay(str(link_id), media_name)
    except Exception:
        # Broker unavailable — the original upload is still served, just uncompressed
        logger.exception("Could not queue portal video compression for link %s", link_id)
