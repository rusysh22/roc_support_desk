"""
Cases App — Forms
==================
Forms for public case submission and internal staff RCA updates.
"""
from django import forms

from core.models import CompanyUnit
from .models import CaseCategory, CaseRecord


# =====================================================================
# Public — Case Submission
# =====================================================================

class CaseCreateForm(forms.Form):
    """
    Public form for employees to submit a new case via the web portal.

    Email is validated for format only — no Employee DB lookup required.
    """

    requester_email = forms.EmailField(
        label="Your Work Email",
        widget=forms.EmailInput(attrs={
            "class": "jk-input",
            "placeholder": "e.g. john.doe@company.com",
            "autocomplete": "email",
        }),
    )
    requester_name = forms.CharField(
        max_length=255,
        label="Your Full Name",
        widget=forms.TextInput(attrs={
            "class": "jk-input",
            "placeholder": "e.g. John Doe",
        }),
    )
    company_unit = forms.ModelChoiceField(
        queryset=CompanyUnit.objects.all(),
        label="Company Unit",
        widget=forms.Select(attrs={"class": "jk-select"}),
        help_text="Select the unit you belong to.",
    )
    job_role = forms.CharField(
        max_length=150,
        label="Job Role",
        widget=forms.TextInput(attrs={
            "class": "jk-input",
            "placeholder": "e.g. Staff IT, Manager Finance",
        }),
    )
    category = forms.ModelChoiceField(
        queryset=CaseCategory.objects.all(),
        label="Category",
        widget=forms.Select(attrs={"class": "jk-select"}),
    )
    subject = forms.CharField(
        max_length=500,
        label="Subject",
        widget=forms.TextInput(attrs={
            "class": "jk-input",
            "placeholder": "Brief summary of the issue",
        }),
    )
    problem_description = forms.CharField(
        label="Problem Description",
        widget=forms.Textarea(attrs={
            "class": "jk-textarea",
            "rows": 5,
            "placeholder": "Describe the problem in detail...",
        }),
    )
    link = forms.URLField(
        required=False,
        label="Reference Link (optional)",
        widget=forms.URLInput(attrs={
            "class": "jk-input",
            "placeholder": "https://example.com/relevant-page",
        }),
        help_text="Any URL/link related to this issue.",
    )
    # Note: attachments are handled via raw HTML <input type="file" multiple>
    # in the template. Django 6 file widgets don't support multiple uploads.

    # Max file size: 10 MB
    MAX_FILE_SIZE = 10 * 1024 * 1024

    def validate_attachments(self, files):
        """Validate each uploaded file is ≤ 10 MB. Called from the view."""
        errors = []
        for f in files:
            if f.size > self.MAX_FILE_SIZE:
                size_mb = round(f.size / (1024 * 1024), 1)
                errors.append(
                    f'File "{f.name}" is {size_mb} MB. '
                    f"Maximum allowed size is 10 MB per file. "
                    f"For larger files, please upload to your Cloud Drive "
                    f"and paste the link in the Reference Link field."
                )
        return errors


# =====================================================================
# Staff — Root Cause Analysis & Solving Steps
# =====================================================================

class CaseRCAForm(forms.ModelForm):
    """
    Internal form for support staff to document the Root Cause Analysis
    and solving steps before resolving a case.
    """

    class Meta:
        model = CaseRecord
        fields = [
            "status",
            "root_cause_analysis",
            "solving_steps",
            "assigned_to",
            "response_due_at",
            "resolution_due_at",
        ]
        widgets = {
            "status": forms.Select(attrs={"class": "jk-select"}),
            "root_cause_analysis": forms.Textarea(attrs={
                "class": "jk-textarea",
                "rows": 6,
                "placeholder": "Document the root cause of the problem...",
            }),
            "solving_steps": forms.Textarea(attrs={
                "class": "jk-textarea",
                "rows": 6,
                "placeholder": "Step-by-step solution applied...",
            }),
            "assigned_to": forms.Select(attrs={"class": "jk-select"}),
            "response_due_at": forms.DateTimeInput(attrs={
                "class": "jk-input",
                "type": "datetime-local",
            }),
            "resolution_due_at": forms.DateTimeInput(attrs={
                "class": "jk-input",
                "type": "datetime-local",
            }),
        }

    def clean(self):
        """Enforce RCA completion before Resolved status."""
        cleaned = super().clean()
        status = cleaned.get("status")

        if status == CaseRecord.Status.RESOLVED:
            rca = cleaned.get("root_cause_analysis", "").strip()
            steps = cleaned.get("solving_steps", "").strip()
            if not rca:
                self.add_error(
                    "root_cause_analysis",
                    "Root Cause Analysis is required before marking as Resolved.",
                )
            if not steps:
                self.add_error(
                    "solving_steps",
                    "Solving Steps are required before marking as Resolved.",
                )
        return cleaned


# =====================================================================
# Staff — Reply Message
# =====================================================================

class StaffReplyForm(forms.Form):
    """Form for staff to send a reply message within a case thread."""

    body = forms.CharField(
        label="Reply",
        widget=forms.Textarea(attrs={
            "class": "jk-textarea",
            "rows": 3,
            "placeholder": "Type your reply...",
        }),
    )
    attachment = forms.FileField(
        required=False,
        label="Attachment",
        widget=forms.ClearableFileInput(attrs={"class": "jk-file-input"}),
    )
