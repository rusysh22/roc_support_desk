"""
Cases App — Forms
==================
Forms for public case submission and internal staff RCA updates.
"""
import json

import magic

from django import forms
from django.core.exceptions import ValidationError
import dns.resolver

from core.models import CompanyUnit, JobRole, normalize_phone_e164, phone_regex
from .models import CaseCategory, CaseRecord, DocumentTemplate, TicketLocation


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
    # Set when the requester was picked from the employee directory; the
    # server then reads the real email / phone from that Employee record.
    requester_employee = forms.UUIDField(required=False, widget=forms.HiddenInput())
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
    location = forms.ModelChoiceField(
        queryset=TicketLocation.objects.none(),
        label="Location",
        empty_label="— Select Location —",
        widget=forms.Select(attrs={
            "class": "jk-select",
            "x-model": "selected",
        }),
    )
    location_detail = forms.CharField(
        required=False,
        max_length=255,
        label="Specify Location",
        widget=forms.TextInput(attrs={
            "class": "jk-input",
            "placeholder": "Enter location",
            ":required": "showDetail",
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

        active_roles = JobRole.objects.filter(is_active=True) if job_role_mode == 'master' else JobRole.objects.none()
        # Master mode with an empty Job Roles list falls back to free text so
        # the form never becomes impossible to submit.
        if job_role_mode == 'master' and active_roles.exists():
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
        self.fields["location"].queryset = TicketLocation.objects.filter(is_active=True)
        self.fields["location"].label_from_instance = lambda obj: obj.name
        from core.models import SiteConfig
        self.fields["location"].required = SiteConfig.get_solo().require_ticket_location
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
            "placeholder": "Describe the problem in detail or paste a screenshot (Ctrl+V)...",
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
    cc_emails = forms.CharField(
        required=False,
        max_length=500,
        label="CC Emails (optional)",
        widget=forms.TextInput(attrs={
            "class": "jk-input",
            "placeholder": "e.g. manager@company.com, lead@company.com",
        }),
        help_text="Comma-separated. These addresses stay CC'd on every email update for this ticket.",
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

    def clean_cc_emails(self):
        from django.core.validators import validate_email

        raw = self.cleaned_data.get("cc_emails", "")
        addresses = [a.strip() for a in raw.split(",") if a.strip()]
        for address in addresses:
            try:
                validate_email(address)
            except ValidationError:
                raise ValidationError(f'"{address}" is not a valid email address.')
        return ", ".join(addresses)

    def clean(self):
        """
        Which contact details are required depends on the category's
        ``requester_contact`` setting. In "either" mode exactly one of
        requester_email / requester_phone is authoritative, chosen via
        contact_mode; the other is discarded so switching never mixes data.
        """
        cleaned = super().clean()
        category = cleaned.get("category")
        requirement = getattr(category, "requester_contact", None) or CaseCategory.RequesterContact.EITHER

        self.picked_employee = None
        employee_id = cleaned.get("requester_employee")
        if employee_id:
            from core.models import Employee
            self.picked_employee = Employee.objects.select_related("unit").filter(pk=employee_id).first()
            if self.picked_employee is None:
                self.add_error(None, "The selected requester no longer exists. Please search again.")
                return cleaned

        if self.picked_employee is not None:
            emp = self.picked_employee
            cleaned["requester_name"] = emp.full_name
            cleaned["requester_email"] = cleaned.get("requester_email") or (emp.email or "")
            cleaned["requester_phone"] = cleaned.get("requester_phone") or (emp.phone_number or "")
            if not cleaned.get("job_role") and emp.job_role:
                cleaned["job_role"] = emp.job_role
            self.errors.pop("requester_name", None)
            if "job_role" in self.errors and cleaned.get("job_role"):
                self.errors.pop("job_role")
            if requirement == CaseCategory.RequesterContact.EITHER:
                if not cleaned["requester_email"] and not cleaned["requester_phone"]:
                    self.add_error("requester_email", "Please add an email address or phone number.")
                requirement = None  # both contacts are kept for a picked employee

        if requirement is None:
            pass
        elif requirement == CaseCategory.RequesterContact.EITHER:
            mode = cleaned.get("contact_mode") or "email"
            if mode == "phone":
                cleaned["requester_email"] = ""
                if not cleaned.get("requester_phone"):
                    self.add_error("requester_phone", "Phone number is required.")
            else:
                cleaned["requester_phone"] = ""
                if not cleaned.get("requester_email"):
                    self.add_error("requester_email", "Work email is required.")
        else:
            needs_email = requirement in (CaseCategory.RequesterContact.EMAIL, CaseCategory.RequesterContact.BOTH)
            needs_phone = requirement in (CaseCategory.RequesterContact.PHONE, CaseCategory.RequesterContact.BOTH)
            if needs_email and not cleaned.get("requester_email"):
                self.add_error("requester_email", "Work email is required for this category.")
            if needs_phone and not cleaned.get("requester_phone"):
                self.add_error("requester_phone", "Phone number is required for this category.")

        location = cleaned.get("location")
        detail = (cleaned.get("location_detail") or "").strip()
        if location and location.requires_detail and not detail:
            self.add_error("location_detail", "Please specify the location.")
        if location and not location.requires_detail:
            cleaned["location_detail"] = ""
        else:
            cleaned["location_detail"] = detail

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
            "impact",
            "severity",
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
            "impact": forms.Select(attrs={
                "class": "jk-select",
                "id": "id_impact",
                "x-model": "impact",
            }),
            "severity": forms.Select(attrs={
                "class": "jk-select",
                "id": "id_severity",
                "x-model": "severity",
            }),
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
        
        # Scope the assigned_to field — SupportDesk, Manager, SuperAdmin, Engineer
        if "assigned_to" in self.fields:
            self.fields["assigned_to"].queryset = User.objects.filter(
                is_active=True,
                role_access__in=[
                    User.RoleAccess.SUPPORTDESK,
                    User.RoleAccess.MANAGER,
                    User.RoleAccess.SUPERADMIN,
                    User.RoleAccess.ENGINEER,
                ],
            ).order_by("first_name", "username")

        # Restrict the status dropdown to the current status plus its
        # valid next statuses (CaseRecord.STATUS_META), so staff can never
        # even select an invalid transition from the UI.
        if "status" in self.fields and self.instance and self.instance.pk:
            self.fields["status"].choices = CaseRecord.status_choices_from(self.instance.status)

        # If the case is already Closed, make all fields read-only
        # UNLESS the edit permission status is 'Approved'
        if (self.instance and 
            self.instance.pk and 
            self.instance.status == CaseRecord.Status.CLOSED and
            self.instance.edit_permission_status != CaseRecord.EditPermissionStatus.APPROVED):
            
            for field_name, field in self.fields.items():
                field.disabled = True
                field.widget.attrs["class"] += " bg-slate-100 opacity-80 cursor-not-allowed"

    # Fields that must be filled before a ticket may sit in a status. Also sent
    # to the page (required_by_status_json) so the Submit button can flag the
    # same fields before the progress-update pop-up opens.
    _SLA_FIELDS = {
        "assigned_to": "Assigned To",
        "response_due_at": "Response SLA",
        "resolution_due_at": "Resolution SLA",
        "root_cause_analysis": "Root Cause Analysis",
        "solving_steps": "Solving Steps",
    }
    REQUIRED_BY_STATUS = {
        # A ticket can't sit in Assigned without a PIC.
        CaseRecord.Status.ASSIGNED: {"assigned_to": "Assigned To"},
        CaseRecord.Status.IN_PROGRESS: {"assigned_to": "Assigned To", "response_due_at": "Response SLA"},
        CaseRecord.Status.RESOLVED: _SLA_FIELDS,
        CaseRecord.Status.CLOSED: _SLA_FIELDS,
    }

    @property
    def required_by_status_json(self):
        return json.dumps({str(status): fields for status, fields in self.REQUIRED_BY_STATUS.items()})

    def clean(self):
        """Enforce valid status transitions, then the fields the target status
        requires (REQUIRED_BY_STATUS)."""
        cleaned = super().clean()
        status = cleaned.get("status")

        if status and self.instance and self.instance.pk and not self.instance.can_transition_to(status):
            self.add_error(
                "status",
                f"Cannot move this ticket from {self.instance.get_status_display()} "
                f"to {dict(CaseRecord.Status.choices).get(status, status)}."
            )
            return cleaned

        for field in self.REQUIRED_BY_STATUS.get(status, {}):
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
    # Attachments are handled outside the form (request.FILES.getlist) since a
    # plain Django FileField only ever binds to one file — see
    # validate_staff_reply_attachment() below, used per-file in the view.

    MAX_ATTACHMENTS = 10


def validate_staff_reply_attachment(f):
    """
    Validate a single staff-reply attachment (size + real MIME type).
    Used per-file in case_send_reply's multi-file loop. Raises
    forms.ValidationError on failure.
    """
    from core.models import SiteConfig
    site_config = SiteConfig.get_solo()
    max_bytes = site_config.max_upload_size_mb * 1024 * 1024

    if f.size > max_bytes:
        size_mb = round(f.size / (1024 * 1024), 1)
        raise forms.ValidationError(
            f'File "{f.name}" is {size_mb} MB. Maximum allowed size is {site_config.max_upload_size_mb} MB.'
        )

    try:
        header = f.read(2048)
        f.seek(0)
        detected_mime = magic.from_buffer(header, mime=True)
    except Exception:
        raise forms.ValidationError(f'File "{f.name}": could not determine file type.')

    media_types = {"video/mp4", "video/webm", "video/quicktime", "video/ogg",
                   "audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav"}
    if detected_mime not in CaseCreateForm.ALLOWED_MIME_TYPES | media_types:
        raise forms.ValidationError(
            f'File "{f.name}" has a disallowed type ({detected_mime}). '
            f"Accepted types: PDF, images, video, audio, Word, Excel, PowerPoint, plain text, CSV, ZIP."
        )
    return detected_mime


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
            'default_case_type', 'default_tags',
            'template_subject', 'template_text',
            'is_confidential', 'is_attachment_mandatory', 'enable_change_request',
            'requester_contact',
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
            'default_case_type': forms.Select(attrs={'class': 'jk-select'}),
            'default_tags': forms.TextInput(attrs={
                'class': 'jk-input',
                'placeholder': 'e.g. sso, access-request, urgent',
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
            'requester_contact': forms.RadioSelect(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        exclude_ids = set()
        if self.instance and self.instance.pk:
            exclude_ids.add(self.instance.pk)
            exclude_ids.update(self.instance.get_descendant_ids())
        options = CaseCategory.parent_select_options(exclude_pks=exclude_ids)
        allowed_ids = [opt["id"] for opt in options]
        label_map = {opt["id"]: opt["label"] for opt in options}
        self.fields["parent"].queryset = CaseCategory.objects.filter(pk__in=allowed_ids)
        self.fields["parent"].choices = [("", "— Root Category (Main) —")] + [
            (opt["id"], opt["label"]) for opt in options
        ]
        self.fields["parent"].empty_label = "— Root Category (Main) —"
        self.fields["parent"].required = False
        self.fields["icon"].required = False
        self.fields["default_case_type"].required = False
        self.fields["default_case_type"].empty_label = "— No Default —"

    def clean_parent(self):
        parent = self.cleaned_data.get("parent")
        if parent and self.instance and self.instance.pk:
            if parent.pk == self.instance.pk:
                raise forms.ValidationError("A category cannot be its own parent.")
            if parent.pk in self.instance.get_descendant_ids():
                raise forms.ValidationError("A category cannot be nested under one of its sub-categories.")
        return parent

    def clean_prefix_code(self):
        code = (self.cleaned_data.get("prefix_code") or "").strip().upper()[:2]
        if not code:
            raise forms.ValidationError("Prefix code is required.")
        qs = CaseCategory.objects.filter(prefix_code__iexact=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f'Prefix code "{code}" is already in use.')
        return code


# =====================================================================
# Ticket Location Form (Admin — Master Data)
# =====================================================================

class TicketLocationForm(forms.ModelForm):
    """
    Form for creating/editing a TicketLocation from the desk admin UI.
    ``slug`` is deliberately excluded — it's auto-generated from ``name``
    in the view (mirrors CaseCategory's own slug auto-generation), since
    it's just an internal identifier nothing else looks it up by.
    """

    class Meta:
        model = TicketLocation
        fields = ['name', 'requires_detail', 'is_active', 'order']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'jk-input',
                'placeholder': 'e.g. Head Office - Jakarta',
            }),
            'requires_detail': forms.CheckboxInput(attrs={
                'class': 'w-4 h-4 text-indigo-600 rounded border-slate-300 focus:ring-indigo-500 mt-0.5 shrink-0',
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'w-4 h-4 text-indigo-600 rounded border-slate-300 focus:ring-indigo-500 mt-0.5 shrink-0',
            }),
            'order': forms.NumberInput(attrs={'class': 'jk-input'}),
        }

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Location name is required.")
        qs = TicketLocation.objects.filter(name__iexact=name)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(f'A location named "{name}" already exists.')
        return name
        return code
