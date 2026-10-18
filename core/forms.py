"""
Core App — Forms
====================
"""
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

User = get_user_model()


class ForgotPasswordForm(forms.Form):
    """
    Form for requesting a password reset OTP.
    Validates that the email exists in the system.
    """
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            "class": "jk-input",
            "placeholder": "Enter your registered email address",
            "autofocus": True,
        })
    )

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if not User.objects.filter(email=email).exists():
            raise ValidationError("We could not find an account with that email address.")
        return email


class ResetPasswordOTPForm(forms.Form):
    """
    Form for validating the OTP and resetting the password.
    """
    otp = forms.CharField(
        max_length=6,
        required=True,
        widget=forms.TextInput(attrs={
            "class": "jk-input text-center text-xl tracking-widest font-mono",
            "placeholder": "000000",
            "autofocus": True,
        }),
        label="6-Digit OTP"
    )
    new_password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            "class": "jk-input",
            "placeholder": "Enter new password",
        }),
        label="New Password"
    )
    confirm_password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            "class": "jk-input",
            "placeholder": "Confirm new password",
        }),
        label="Confirm Password"
    )

    def clean(self):
        cleaned_data = super().clean()
        new_password = cleaned_data.get("new_password")
        confirm_password = cleaned_data.get("confirm_password")

        if new_password and confirm_password:
            if new_password != confirm_password:
                self.add_error("confirm_password", "Passwords do not match.")

        return cleaned_data


class EmailConfigForm(forms.ModelForm):
    """
    Form for updating global Email Settings in the Support Desk admin.
    """
    class Meta:
        from .models import EmailConfig
        model = EmailConfig
        exclude = ["id", "created_at", "updated_at", "created_by", "updated_by"]
        widgets = {
            "imap_host": forms.TextInput(attrs={"class": "jk-input"}),
            "imap_port": forms.NumberInput(attrs={"class": "jk-input"}),
            "imap_user": forms.TextInput(attrs={"class": "jk-input"}),
            "imap_password": forms.PasswordInput(
                attrs={"class": "jk-input", "placeholder": "Leave blank to keep unchanged"}, 
                render_value=False
            ),
            
            "smtp_host": forms.TextInput(attrs={"class": "jk-input"}),
            "smtp_port": forms.NumberInput(attrs={"class": "jk-input"}),
            "smtp_user": forms.TextInput(attrs={"class": "jk-input"}),
            "smtp_password": forms.PasswordInput(
                attrs={"class": "jk-input", "placeholder": "Leave blank to keep unchanged"}, 
                render_value=False
            ),
            "smtp_use_tls": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "smtp_use_ssl": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "default_from_email": forms.TextInput(attrs={"class": "jk-input"}),
        }

    def clean_imap_password(self):
        new_password = self.cleaned_data.get('imap_password')
        if not new_password and self.instance and self.instance.pk:
            # If the field is left blank in the form, retain the original password
            return self.instance.imap_password
        return new_password

    def clean_smtp_password(self):
        new_password = self.cleaned_data.get('smtp_password')
        if not new_password and self.instance and self.instance.pk:
            # If the field is left blank in the form, retain the original password
            return self.instance.smtp_password
        return new_password

class TeamsConfigForm(forms.ModelForm):
    """
    Form for configuring Microsoft Teams integration (1-way webhook + 2-way bot).
    """
    class Meta:
        from .models import TeamsConfig
        model = TeamsConfig
        fields = [
            'incoming_webhook_url', 'is_notification_enabled',
            'is_bot_enabled', 'bot_app_id', 'bot_app_password',
            'tenant_id', 'bot_webhook_secret',
        ]
        widgets = {
            'incoming_webhook_url': forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'https://xxx.webhook.office.com/...'}),
            'is_notification_enabled': forms.CheckboxInput(attrs={'class': 'jk-checkbox'}),
            'is_bot_enabled': forms.CheckboxInput(attrs={'class': 'jk-checkbox'}),
            'bot_app_id': forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'}),
            'bot_app_password': forms.PasswordInput(
                attrs={'class': 'jk-input', 'placeholder': 'Leave blank to keep unchanged'},
                render_value=False,
            ),
            'tenant_id': forms.TextInput(attrs={'class': 'jk-input', 'placeholder': 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'}),
            'bot_webhook_secret': forms.PasswordInput(
                attrs={'class': 'jk-input', 'placeholder': 'Leave blank to keep unchanged'},
                render_value=False,
            ),
        }

    def clean_bot_app_password(self):
        value = self.cleaned_data.get('bot_app_password')
        if not value and self.instance and self.instance.pk:
            return self.instance.bot_app_password
        return value

    def clean_bot_webhook_secret(self):
        value = self.cleaned_data.get('bot_webhook_secret')
        if not value and self.instance and self.instance.pk:
            return self.instance.bot_webhook_secret
        return value


class NotificationConfigForm(forms.ModelForm):
    """
    Form for configuring which channels receive new-ticket notifications.
    """
    class Meta:
        from .models import NotificationConfig
        model = NotificationConfig
        fields = [
            'notify_new_ticket_email', 'notify_new_ticket_whatsapp', 'notify_new_ticket_teams',
            'email_recipients', 'whatsapp_recipients',
        ]
        widgets = {
            'notify_new_ticket_email': forms.CheckboxInput(attrs={'class': 'jk-checkbox'}),
            'notify_new_ticket_whatsapp': forms.CheckboxInput(attrs={'class': 'jk-checkbox'}),
            'notify_new_ticket_teams': forms.CheckboxInput(attrs={'class': 'jk-checkbox'}),
            'email_recipients': forms.Textarea(attrs={
                'class': 'jk-textarea',
                'rows': 3,
                'placeholder': 'agen1@corp.com, agen2@corp.com',
            }),
            'whatsapp_recipients': forms.Textarea(attrs={
                'class': 'jk-textarea',
                'rows': 3,
                'placeholder': '628123456789, 628987654321',
            }),
        }


class DynamicFormForm(forms.ModelForm):
    """
    Form for creating/editing the main settings of a DynamicForm.
    """
    slug = forms.SlugField(required=False, widget=forms.TextInput(attrs={"class": "jk-input"}))

    def clean_slug(self):
        slug = self.cleaned_data.get("slug")
        if slug:
            from .models import DynamicForm
            qs = DynamicForm.objects.filter(slug=slug)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise forms.ValidationError("A form with this slug already exists. Please choose a different one.")
        return slug

    class Meta:
        from .models import DynamicForm
        model = DynamicForm
        exclude = ["id", "created_at", "updated_at", "created_by", "updated_by", "collaborators"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "jk-input", "maxlength": "255"}),
            "description": forms.Textarea(attrs={"class": "jk-textarea", "rows": 3, "maxlength": "10000"}),
            "success_message": forms.Textarea(attrs={"class": "jk-textarea", "rows": 3, "maxlength": "5000"}),
            "background_color": forms.TextInput(attrs={"class": "w-10 h-10 p-0 border-0 rounded shadow-sm cursor-pointer", "type": "color"}),
            "background_image": forms.ClearableFileInput(attrs={"class": "jk-file-input"}),
            "header_image": forms.ClearableFileInput(attrs={"class": "jk-file-input"}),
            "is_published": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "requires_login": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "show_on_portal": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "collect_user": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
            "collect_company": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
        }

class UserRoleForm(forms.ModelForm):
    """User Role master data. System roles keep their code and base level."""

    permissions = forms.MultipleChoiceField(
        required=False, widget=forms.CheckboxSelectMultiple, label="Permissions",
    )

    class Meta:
        from .models import UserRole
        model = UserRole
        fields = ["name", "code", "base_access", "description", "permissions", "sort_order", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "jk-input", "placeholder": "e.g. Finance Staff"}),
            "code": forms.TextInput(attrs={"class": "jk-input font-mono", "placeholder": "e.g. finance_staff"}),
            "base_access": forms.Select(attrs={"class": "jk-input"}),
            "description": forms.Textarea(attrs={"class": "jk-textarea", "rows": 3}),
            "sort_order": forms.NumberInput(attrs={"class": "jk-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        from .models import ALL_PERMISSIONS, PERMISSION_REGISTRY
        super().__init__(*args, **kwargs)
        self.fields["permissions"].choices = [(ALL_PERMISSIONS, "All permissions (current and future)")] + [
            (code, label) for code, label, _group in PERMISSION_REGISTRY
        ]
        self.permission_groups = {}
        for code, label, group in PERMISSION_REGISTRY:
            self.permission_groups.setdefault(group, []).append((code, label))
        if not self.instance._state.adding:  # UUID pks exist before saving, so check _state
            self.fields["code"].disabled = True
            if self.instance.is_system:
                self.fields["base_access"].disabled = True

    def clean_permissions(self):
        perms = list(dict.fromkeys(self.cleaned_data.get("permissions") or []))
        from .models import ALL_PERMISSIONS
        return [ALL_PERMISSIONS] if ALL_PERMISSIONS in perms else perms


class UserAdminForm(forms.ModelForm):
    """
    Form for creating/editing users in the User Management section.
    """
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={
            "class": "jk-input",
            "placeholder": "Leave blank to keep current, or type a new one",
        }),
        help_text="For new users, if left blank, it will default to 'RoCDesk123!'."
    )

    class Meta:
        model = User
        fields = [
            "login_username", "username", "email", "nik", "user_role", "role_access", 
            "initials", "can_handle_confidential"
        ]
        widgets = {
            "login_username": forms.TextInput(attrs={"class": "jk-input"}),
            "username": forms.TextInput(attrs={"class": "jk-input"}),
            "email": forms.EmailInput(attrs={"class": "jk-input"}),
            "nik": forms.TextInput(attrs={"class": "jk-input"}),
            "user_role": forms.Select(attrs={"class": "jk-input"}),
            # Derived from the chosen User Role's base level (see clean()).
            "role_access": forms.HiddenInput(),
            "initials": forms.TextInput(attrs={"class": "jk-input", "maxlength": "5"}),
            "can_handle_confidential": forms.CheckboxInput(attrs={"class": "jk-checkbox"}),
        }

    def __init__(self, *args, **kwargs):
        from .models import UserRole
        super().__init__(*args, **kwargs)
        roles = UserRole.objects.filter(is_active=True)
        current = self.instance.user_role_id
        if current:
            roles = roles | UserRole.objects.filter(pk=current)
        self.fields["user_role"].queryset = roles.distinct().order_by("sort_order", "name")
        self.fields["user_role"].required = True
        self.fields["user_role"].empty_label = None
        self.fields["user_role"].label = "User Role"
        self.fields["role_access"].required = False
        if not self.initial.get("user_role"):
            # Existing users without a role row yet, or a duplicated user: preselect the system role.
            tier = self.initial.get("role_access") or (self.instance.role_access if self.instance.pk else None)
            system = UserRole.objects.filter(code=tier, is_system=True).first() if tier else None
            if system:
                self.initial["user_role"] = system.pk

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("user_role")
        if role is not None:
            cleaned["role_access"] = role.base_access
        return cleaned

    def clean_nik(self):
        return self.cleaned_data.get("nik") or None

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        
        if password:
            user.set_password(password)
        elif not user.pk:
            # If creating a new user and no password provided, set default
            user.set_password("RoCDesk123!")

        if user.role_access == User.RoleAccess.PORTALUSER:
            user.is_staff = False
        else:
            user.is_staff = True

        if not user.pk:
            user.module_access = None

        if commit:
            user.save()
        return user


class EmployeeForm(forms.ModelForm):
    """
    Form for creating/editing Employees in the Support Desk admin.

    ``nik`` is not an Employee field — it belongs to User. Typing a NIK here
    looks up the matching User and links this Employee to it (``Employee.user``),
    so NIK stays the single identifier shared between login accounts and
    employee records.
    """
    nik = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "jk-input", "placeholder": "e.g. 240157"}),
        label="NIK",
        help_text="Optional. Links this employee to their login account by NIK.",
    )

    class Meta:
        from .models import Employee
        model = Employee
        fields = ["full_name", "email", "phone_number", "job_role", "unit", "reports_to"]
        widgets = {
            "full_name": forms.TextInput(attrs={"class": "jk-input"}),
            "email": forms.EmailInput(attrs={"class": "jk-input"}),
            "phone_number": forms.TextInput(attrs={"class": "jk-input"}),
            "job_role": forms.TextInput(attrs={"class": "jk-input"}),
            "unit": forms.Select(attrs={"class": "jk-input"}),
            "reports_to": forms.Select(attrs={"class": "jk-input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and self.instance.user_id:
            self.fields["nik"].initial = self.instance.user.nik

        from .models import SiteConfig, JobRole
        if SiteConfig.get_solo().job_role_mode == "master":
            current = self.instance.job_role if self.instance and self.instance.pk else ""
            choices = [("", "— Select Job Role —")]
            choices += [(r.name, r.name) for r in JobRole.objects.filter(is_active=True)]
            if current and current not in dict(choices):
                # Preserve an existing value that's no longer in the active master
                # list, so editing an old record doesn't silently blank it out.
                choices.append((current, f"{current} (inactive)"))
            self.fields["job_role"] = forms.ChoiceField(
                required=False,
                label="Job Role",
                choices=choices,
                widget=forms.Select(attrs={"class": "jk-input"}),
            )

    def clean_nik(self):
        from .models import Employee

        nik = (self.cleaned_data.get("nik") or "").strip()
        if not nik:
            return ""

        user = User.objects.filter(nik=nik).first()
        if not user:
            raise ValidationError("No user found with this NIK.")

        clash = Employee.objects.filter(user=user)
        if self.instance and self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise ValidationError(
                f"This NIK is already linked to employee '{clash.first().full_name}'."
            )

        self._matched_user = user
        return nik

    def save(self, commit=True):
        employee = super().save(commit=False)
        employee.user = getattr(self, "_matched_user", None)
        if commit:
            employee.save()
        return employee
