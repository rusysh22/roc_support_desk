"""
E-Signature App — Forms
========================
"""
import magic

from django import forms
from django.core.exceptions import ValidationError

from core.models import SiteConfig, User
from .models import SignatureDocument, Signer, SignaturePlacement


class DocumentUploadForm(forms.ModelForm):
    """Form for the initial PDF upload step."""

    class Meta:
        model = SignatureDocument
        fields = ["title", "original_pdf", "routing_mode", "message", "due_at"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "jk-input",
                "placeholder": "e.g. Approval Letter — June 2026",
            }),
            "routing_mode": forms.Select(attrs={"class": "jk-select"}),
            "message": forms.Textarea(attrs={
                "class": "jk-input",
                "rows": 3,
                "placeholder": "Optional message sent to all signers with the request email.",
            }),
            "due_at": forms.DateTimeInput(attrs={
                "class": "jk-input",
                "type": "datetime-local",
            }),
        }

    def clean_original_pdf(self):
        pdf = self.cleaned_data.get("original_pdf")
        if not pdf:
            return pdf

        site_config = SiteConfig.get_solo()
        max_mb = getattr(site_config, "max_upload_size_mb", 10) or 10
        if pdf.size > max_mb * 1024 * 1024:
            raise ValidationError(f"File size must not exceed {max_mb} MB.")

        mime = magic.from_buffer(pdf.read(2048), mime=True)
        pdf.seek(0)
        if mime != "application/pdf":
            raise ValidationError("Only PDF files are accepted.")

        return pdf


class ExternalSignerForm(forms.Form):
    """Sub-form for adding a single external (non-system) signer."""

    external_name  = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": "jk-input",
            "placeholder": "Full name",
        }),
    )
    external_email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            "class": "jk-input",
            "placeholder": "email@example.com",
        }),
    )
    order = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={"class": "jk-input", "min": "1"}),
    )
    role = forms.ChoiceField(
        choices=Signer.Role.choices,
        widget=forms.Select(attrs={"class": "jk-select"}),
    )


class SignerSignForm(forms.Form):
    """
    Form submitted by a signer on the magic-link signing page.
    The signature image is captured as a base64 data-URL from the canvas.
    """

    action = forms.ChoiceField(
        choices=[("sign", "Sign"), ("reject", "Reject")],
        widget=forms.HiddenInput(),
    )
    signature_data = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
        help_text="Base64 PNG data-URL from the signature canvas.",
    )
    notes = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.Textarea(attrs={
            "class": "jk-input",
            "rows": 3,
            "placeholder": "Reason for rejection (required if rejecting)",
        }),
    )

    def clean(self):
        cleaned = super().clean()
        action = cleaned.get("action")
        if action == "sign" and not cleaned.get("signature_data"):
            raise ValidationError("Please provide your signature before submitting.")
        if action == "reject" and not cleaned.get("notes", "").strip():
            raise ValidationError("A rejection reason is required.")
        return cleaned
