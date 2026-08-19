"""
Cases App — Forms
==================
Forms for public case submission and internal staff RCA updates.
"""
import magic

from django import forms
from django.core.exceptions import ValidationError
import dns.resolver

from core.models import CompanyUnit, JobRole, normalize_phone_e164, phone_regex
from .models import CaseCategory, CaseRecord, DocumentTemplate


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
        required=False,
        widget=forms.EmailInput(attrs={
            "class": "jk-input",
            "placeholder": "e.g. john.doe@company.com",
            "autocomplete": "email",
        }),
    )
    requester_phone = forms.CharField(
        label="Your Phone Number",
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs={
            "class": "jk-input",
            "placeholder": "e.g. 0812xxxxxxxx",
            "autocomplete": "tel",
        }),
    )
    # Which of the two contact fields above is the active/authoritative one.
    contact_mode = forms.ChoiceField(
        choices=[("email", "email"), ("phone", "phone")],
        required=False,
        initial="email",
        widget=forms.HiddenInput(),
    )
    # Optional enrichment: offered when an existing Employee was matched by
    # one contact method but is missing the other, so it can be added on.
    additional_phone = forms.CharField(
        required=False,
        max_length=20,
        widget=forms.TextInput(attrs={"class": "jk-input"}),
    )
    additional_email = forms.EmailField(
        required=False,
        widget=forms.EmailInput(attrs={"class": "jk-input"}),
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
            "x-ref": "jobRoleInput",
            ":disabled": "locked",
            ":class": "locked ? 'bg-slate-50 text-slate-500 cursor-not-allowed' : ''",
        }),
    )

    # Overridden to 'master' mode in __init__ when site_config.job_role_mode == 'master'
    _job_role_mode = 'freetext'

    category = forms.ModelChoiceField(
        queryset=CaseCategory.objects.none(),
        label="Category",
        widget=forms.Select(attrs={
            "class": "jk-select pointer-events-none bg-slate-50 opacity-90",
            "tabindex": "-1"
        }),
    )

    def __init__(self, *args, job_role_mode='freetext', **kwargs):
        super().__init__(*args, **kwargs)
        self._job_role_mode = job_role_mode

        if job_role_mode == 'master':
            active_roles = JobRole.objects.filter(is_active=True)
            choices = [('', '— Pilih Job Role —')] + [(r.name, r.name) for r in active_roles]
            self.fields['job_role'] = forms.ChoiceField(
                label="Job Role",
                choices=choices,
                widget=forms.Select(attrs={
                    "class": "jk-select",
                    "x-ref": "jobRoleInput",
                    ":disabled": "locked",
                    ":class": "locked ? 'bg-slate-50 text-slate-500 cursor-not-allowed' : ''",
                }),
            )

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

    # Set by clean_requester_email when the domain looks unusual, so the view
    # can surface a non-blocking warning instead of rejecting the submission.
    email_domain_warning = None

    def clean_requester_email(self):
        """
        Flag emails whose domain has no MX record (including NXDOMAIN) as
        potentially mistyped, but never block submission on it — corporate
        domains can be behind private/split-horizon DNS that this server
        can't resolve even though the address is legitimate.
        """
        email = self.cleaned_data.get("requester_email")
        if not email:
            return email

        domain = email.split('@')[-1]
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = 5.0
            resolver.lifetime = 5.0
            resolver.resolve(domain, 'MX')
        except dns.resolver.NXDOMAIN:
            self.email_domain_warning = (
                f"Heads up: the domain '{domain}' could not be found. "
                f"Please double-check for typos — we've submitted your ticket anyway."
            )
        except (dns.resolver.NoAnswer, dns.resolver.NoNameservers, dns.exception.Timeout, Exception):
            # Domain may have A-record-only mail or DNS is temporarily unreachable;
            # allow through rather than blocking legitimate corporate emails.
            pass

        return email

    def clean_requester_phone(self):
        """Normalize common Indonesian local formats to E.164 and validate."""
        phone = (self.cleaned_data.get("requester_phone") or "").strip()
        if not phone:
            return phone

        digits = normalize_phone_e164(phone)
        try:
            phone_regex(digits)
        except ValidationError:
            raise ValidationError(
                "Enter a valid phone number, e.g. 0812xxxxxxxx or +6281234567890."
            )
        return digits

    def clean_additional_phone(self):
        """Normalize the optional enrichment phone number the same way."""
        phone = (self.cleaned_data.get("additional_phone") or "").strip()
        if not phone:
            return phone

        digits = normalize_phone_e164(phone)
        try:
            phone_regex(digits)
        except ValidationError:
            raise ValidationError(
                "Enter a valid phone number, e.g. 0812xxxxxxxx or +6281234567890."
            )
        return digits

    def clean(self):
        """
        Exactly one of requester_email / requester_phone is authoritative,
        chosen via contact_mode. The other is discarded even if it has a
        (stale) value, so switching modes never mixes data.
        """
        cleaned = super().clean()
        mode = cleaned.get("contact_mode") or "email"

        if mode == "phone":
            cleaned["requester_email"] = ""
            if not cleaned.get("requester_phone"):
                self.add_error("requester_phone", "Phone number is required.")
        else:
            cleaned["requester_phone"] = ""
            if not cleaned.get("requester_email"):
                self.add_error("requester_email", "Work email is required.")

        return cleaned

    ALLOWED_MIME_TYPES = {
        "application/pdf",
        "image/png", "image/jpeg", "image/gif", "image/webp",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/x-ole-storage",  # .xls legacy Excel 97-2003 (detected by magic bytes)
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain", "text/csv",
        "application/zip",
    }

    def validate_attachments(self, files):
        """Validate each uploaded file: size and MIME type is allowed."""
        from core.models import SiteConfig
        site_config = SiteConfig.get_solo()
        max_bytes = site_config.max_upload_size_mb * 1024 * 1024
        
        errors = []
        for f in files:
            if f.size > max_bytes:
                size_mb = round(f.size / (1024 * 1024), 1)
                errors.append(
                    f'File "{f.name}" is {size_mb} MB. '
                    f"Maximum allowed size is {site_config.max_upload_size_mb} MB per file. "
                    f"For larger files, please upload to your Cloud Drive "
                    f"and paste the link in the Reference Link field."
                )
                continue

            # Validate actual file content via magic bytes (prevents extension spoofing)
            try:
                header = f.read(2048)
                f.seek(0)
                detected_mime = magic.from_buffer(header, mime=True)
            except Exception:
                errors.append(f'File "{f.name}": could not determine file type.')
                continue

            if detected_mime not in self.ALLOWED_MIME_TYPES:
                errors.append(
                    f'File "{f.name}" has a disallowed type ({detected_mime}). '
                    f"Accepted types: PDF, images, Word, Excel, PowerPoint, plain text, CSV, ZIP."
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
        
        # Scope the assigned_to field — only SupportDesk, Manager, SuperAdmin
        if "assigned_to" in self.fields:
            self.fields["assigned_to"].queryset = User.objects.filter(
                is_active=True,
                role_access__in=[
                    User.RoleAccess.SUPPORTDESK,
                    User.RoleAccess.MANAGER,
                    User.RoleAccess.SUPERADMIN,
                ],
            ).order_by("first_name", "username")

        # If the case is already Closed, make all fields read-only
        # UNLESS the edit permission status is 'Approved'
        if (self.instance and 
            self.instance.pk and 
            self.instance.status == CaseRecord.Status.CLOSED and
            self.instance.edit_permission_status != CaseRecord.EditPermissionStatus.APPROVED):
            
            for field_name, field in self.fields.items():
                field.disabled = True
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

    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if not f:
            return f

        # Validate file size
        from core.models import SiteConfig
        site_config = SiteConfig.get_solo()
        max_bytes = site_config.max_upload_size_mb * 1024 * 1024

        if f.size > max_bytes:
            size_mb = round(f.size / (1024 * 1024), 1)
            raise forms.ValidationError(
                f'File "{f.name}" is {size_mb} MB. Maximum allowed size is {site_config.max_upload_size_mb} MB.'
            )

        # Validate actual file content via magic bytes
        try:
            header = f.read(2048)
            f.seek(0)
            detected_mime = magic.from_buffer(header, mime=True)
        except Exception:
            raise forms.ValidationError(f'File "{f.name}": could not determine file type.')

        if detected_mime not in CaseCreateForm.ALLOWED_MIME_TYPES:
            raise forms.ValidationError(
                f'File "{f.name}" has a disallowed type ({detected_mime}). '
                f"Accepted types: PDF, images, Word, Excel, PowerPoint, plain text, CSV, ZIP."
            )

        return f


# =====================================================================
# Document Template Form (Admin)
# =====================================================================

class DocumentTemplateForm(forms.ModelForm):
    """Form for creating/editing a DocumentTemplate."""

    class Meta:
        model = DocumentTemplate
        fields = ["title", "description", "body_html", "categories"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "jk-input", "placeholder": "e.g. Surat Kronologi"}),
            "description": forms.Textarea(attrs={
                "class": "jk-input",
                "rows": 3,
                "placeholder": "Short description of this document template...",
            }),
            "body_html": forms.Textarea(attrs={
                "id": "document-body-editor",
                "class": "jk-input font-mono text-sm",
                "rows": 20,
                "placeholder": "Write the document body in HTML. Use {{placeholder}} for dynamic fields.",
            }),
            "categories": forms.CheckboxSelectMultiple(),
        }


# =====================================================================
# Ticket Category Form (Admin)
# =====================================================================

class CaseCategoryForm(forms.ModelForm):
    """Form for creating/editing a CaseCategory from the desk admin UI."""

    class Meta:
        model = CaseCategory
        fields = [
            'name', 'parent', 'icon', 'description', 'prefix_code',
            'template_subject', 'template_text',
            'is_confidential', 'is_attachment_mandatory', 'enable_change_request',
            'is_use_routing_approval', 'is_need_admin_approval',
        ]
        _cb = {'class': 'w-4 h-4 text-indigo-600 rounded border-slate-300 focus:ring-indigo-500 mt-0.5 shrink-0'}
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'jk-input',
                'placeholder': 'e.g. IT Support',
            }),
            'parent': forms.Select(attrs={'class': 'jk-select'}),
            'icon': forms.HiddenInput(),
            'description': forms.Textarea(attrs={
                'class': 'jk-input',
                'rows': 2,
                'placeholder': 'Short description shown on the portal...',
            }),
            'prefix_code': forms.TextInput(attrs={
                'class': 'jk-input',
                'maxlength': '2',
                'placeholder': 'RQ',
                'style': 'text-transform:uppercase;',
            }),
            'template_subject': forms.TextInput(attrs={
                'class': 'jk-input',
                'placeholder': 'e.g. [Issue] Brief description of the problem',
            }),
            'template_text': forms.Textarea(attrs={
                'class': 'jk-input',
                'rows': 6,
                'placeholder': 'e.g. Please describe the issue:\n\nSteps to reproduce:\n1. \n2.',
            }),
            'is_confidential': forms.CheckboxInput(attrs=_cb),
            'is_attachment_mandatory': forms.CheckboxInput(attrs=_cb),
            'enable_change_request': forms.CheckboxInput(attrs=_cb),
            'is_use_routing_approval': forms.CheckboxInput(attrs=_cb),
            'is_need_admin_approval': forms.CheckboxInput(attrs=_cb),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        parent_qs = CaseCategory.objects.filter(parent__isnull=True).order_by('name')
        if self.instance and self.instance.pk:
            parent_qs = parent_qs.exclude(pk=self.instance.pk)
        self.fields['parent'].queryset = parent_qs
        self.fields['parent'].empty_label = '— Root Category (Main) —'
        self.fields['parent'].required = False
        self.fields['icon'].required = False
