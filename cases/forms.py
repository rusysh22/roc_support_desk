"""
Cases App — Forms
==================
Forms for public case submission and internal staff RCA updates.
"""
from django import forms
from django.core.exceptions import ValidationError
import dns.resolver

from core.models import CompanyUnit
from .models import CaseCategory, CaseRecord


# =====================================================================
# Public — Ticket Submission
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
        queryset=CaseCategory.objects.none(),
        label="Category",
        widget=forms.Select(attrs={
            "class": "jk-select pointer-events-none bg-slate-50 opacity-90",
            "tabindex": "-1"
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show leaf categories (exclude parents that have children)
        parent_ids = CaseCategory.objects.filter(
            parent__isnull=False
        ).values_list("parent_id", flat=True)
        self.fields["category"].queryset = (
            CaseCategory.objects
            .exclude(slug__in=["whatsapp-general", "email-general"])
            .exclude(id__in=parent_ids)
        )
        self.fields["category"].label_from_instance = lambda obj: obj.name
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

    def clean_requester_email(self):
        """
        Validate that the email domain has valid MX records.
        Prevents typos or fake domains from passing through.
        """
        email = self.cleaned_data.get("requester_email")
        if not email:
            return email
            
        domain = email.split('@')[-1]
        try:
            # Query MX records with a short timeout to prevent hanging the server
            resolver = dns.resolver.Resolver()
            resolver.timeout = 3.0
            resolver.lifetime = 3.0
            answers = resolver.resolve(domain, 'MX')
            if not answers:
                raise ValidationError(f"The domain '{domain}' does not appear to be set up to receive emails.")
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            raise ValidationError(f"The domain '{domain}' does not exist or cannot receive emails. Please check for typos.")
        except dns.exception.Timeout:
            # If the DNS server times out, log it but don't strictly block the user 
            # to be safe, or you can block it. Here we raise a validation error for strictness.
            raise ValidationError(f"Could not verify the email domain '{domain}' at this time. Please try again.")
        except Exception:
            raise ValidationError("Invalid email address format or domain.")
            
        return email

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
            "priority",
            "case_type",
            "tags",
            "followers",
            "status",
            "root_cause_analysis",
            "solving_steps",
            "quick_notes",
            "assigned_to",
            "response_due_at",
            "resolution_due_at",
        ]
        widgets = {
            "priority": forms.Select(attrs={"class": "jk-select"}),
            "case_type": forms.Select(attrs={"class": "jk-select"}),
            "tags": forms.TextInput(attrs={
                "class": "jk-input",
                "placeholder": "e.g. login, network, bug",
            }),
            "followers": forms.SelectMultiple(attrs={
                "class": "jk-select",
                "size": "3",
            }),
            "quick_notes": forms.Textarea(attrs={
                "class": "jk-textarea",
                "rows": 3,
                "placeholder": "Quick internal notes regarding this case...",
            }),
            "status": forms.Select(attrs={"class": "jk-select"}),
            "root_cause_analysis": forms.Textarea(attrs={
                "class": "jk-textarea",
                "rows": 6,
                "placeholder": "Document the root cause of the problem...",
                "maxlength": "1500",
            }),
            "solving_steps": forms.Textarea(attrs={
                "class": "jk-textarea",
                "rows": 6,
                "placeholder": "Step-by-step solution applied...",
                "maxlength": "1500",
            }),
            "assigned_to": forms.Select(attrs={"class": "jk-select jk-select-search w-full"}),
            "response_due_at": forms.DateTimeInput(attrs={
                "class": "jk-input",
                "type": "datetime-local",
            }),
            "resolution_due_at": forms.DateTimeInput(attrs={
                "class": "jk-input",
                "type": "datetime-local",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        # Scope the assigned_to field
        if "assigned_to" in self.fields:
            self.fields["assigned_to"].queryset = User.objects.filter(
                is_staff=True, is_active=True
            ).exclude(
                role_access__in=[User.RoleAccess.AUDITOR, User.RoleAccess.PORTALUSER]
            ).order_by("first_name", "username")

        # If the case is already Closed, make all fields read-only
        # UNLESS the edit permission status is 'Approved'
        if (self.instance and 
            self.instance.pk and 
            self.instance.status == CaseRecord.Status.CLOSED and
            self.instance.edit_permission_status != CaseRecord.EditPermissionStatus.APPROVED):
            
            for field_name, field in self.fields.items():
                field.disabled = True
                field.widget.attrs["readonly"] = True
                field.widget.attrs["class"] += " bg-slate-100 opacity-80 cursor-not-allowed"

    def clean(self):
        """Enforce SLA details completion before Resolved or Closed status."""
        cleaned = super().clean()
        status = cleaned.get("status")

        if status in [CaseRecord.Status.RESOLVED, CaseRecord.Status.CLOSED]:
            required_fields = {
                "assigned_to": "Assigned To",
                "response_due_at": "Response SLA",
                "resolution_due_at": "Resolution SLA",
                "root_cause_analysis": "Root Cause Analysis",
                "solving_steps": "Solving Steps",
            }
            
            for field, label in required_fields.items():
                if not cleaned.get(field):
                    self.add_error(
                        field,
                        f"This field is required before marking as {status}."
                    )
        elif status == CaseRecord.Status.INVESTIGATING:
            required_fields = {
                "assigned_to": "Assigned To",
                "response_due_at": "Response SLA",
            }
            
            for field, label in required_fields.items():
                if not cleaned.get(field):
                    self.add_error(
                        field,
                        f"This field is required before marking as {status}."
                    )
        return cleaned


# =====================================================================
# Staff — Reply Message
# =====================================================================

class StaffReplyForm(forms.Form):
    """Form for staff to send a reply message within a case thread."""

    body = forms.CharField(
        required=False,
        label="Reply",
        widget=forms.Textarea(attrs={
            "class": "jk-textarea",
            "rows": 3,
            "placeholder": "Type your reply...",
        }),
    )
    cc_emails = forms.CharField(
        required=False,
        label="CC",
        widget=forms.TextInput(attrs={
            "class": "jk-input",
            "placeholder": "e.g. manager@domain.com, lead@domain.com",
        }),
    )
    attachment = forms.FileField(
        required=False,
        label="Attachment",
        widget=forms.ClearableFileInput(attrs={"class": "jk-file-input"}),
    )
