from django import forms

from core.models import CompanyUnit
from core.templatetags.text_filters import sanitize_html

from .models import Announcement

CHECKBOX_CLASS = "w-4 h-4 text-indigo-600 rounded border-slate-300 focus:ring-indigo-500 mt-0.5 shrink-0"
DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


class AnnouncementForm(forms.ModelForm):
    send_email = forms.BooleanField(
        required=False, label="E-mail the audience when published",
        widget=forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}),
    )

    class Meta:
        model = Announcement
        fields = [
            "title", "summary", "body", "link_url", "visibility", "target_entities", "priority",
            "is_pinned", "status", "publish_at", "expires_at", "require_acknowledgement",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "jk-input", "maxlength": 200}),
            "summary": forms.TextInput(attrs={"class": "jk-input", "maxlength": 300}),
            "body": forms.HiddenInput(),
            "link_url": forms.TextInput(attrs={"class": "jk-input", "placeholder": "/kb/a/article-slug/"}),
            "visibility": forms.Select(attrs={"class": "jk-select"}),
            "target_entities": forms.CheckboxSelectMultiple(),
            "priority": forms.Select(attrs={"class": "jk-select"}),
            "is_pinned": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}),
            "status": forms.Select(attrs={"class": "jk-select"}),
            "publish_at": forms.DateTimeInput(attrs={"class": "jk-input", "type": "datetime-local"}, format=DATETIME_FORMAT),
            "expires_at": forms.DateTimeInput(attrs={"class": "jk-input", "type": "datetime-local"}, format=DATETIME_FORMAT),
            "require_acknowledgement": forms.CheckboxInput(attrs={"class": CHECKBOX_CLASS}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target_entities"].queryset = CompanyUnit.objects.filter(is_archived=False).order_by("code")
        self.fields["target_entities"].label_from_instance = lambda e: f"{e.code} — {e.name}"
        for name in ("publish_at", "expires_at"):
            self.fields[name].input_formats = [DATETIME_FORMAT]

    def clean_body(self):
        return str(sanitize_html(self.cleaned_data.get("body") or ""))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("visibility") == Announcement.Visibility.TARGETED and not cleaned.get("target_entities"):
            self.add_error("target_entities", "Select at least one company unit for a targeted announcement.")
        if cleaned.get("visibility") != Announcement.Visibility.TARGETED:
            cleaned["target_entities"] = CompanyUnit.objects.none()
        return cleaned
