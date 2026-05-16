from django import forms
from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import AppPortalEntry, ROLE_CHOICES


class AppPortalEntryForm(forms.ModelForm):
    accessible_roles_picker = forms.MultipleChoiceField(
        choices=ROLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Accessible By (Roles)",
        help_text=(
            "Centang role yang boleh melihat app ini. "
            "Jika tidak ada yang dicentang, semua pengguna bisa melihat."
        ),
    )

    class Meta:
        model = AppPortalEntry
        exclude = ["accessible_roles"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = kwargs.get("instance")
        if instance and instance.accessible_roles and instance.accessible_roles.strip() != "all":
            self.fields["accessible_roles_picker"].initial = [
                r.strip() for r in instance.accessible_roles.split(",") if r.strip()
            ]

    def save(self, commit=True):
        instance = super().save(commit=False)
        selected = self.cleaned_data.get("accessible_roles_picker", [])
        instance.accessible_roles = ",".join(selected) if selected else "all"
        if commit:
            instance.save()
        return instance


@admin.register(AppPortalEntry)
class AppPortalEntryAdmin(ModelAdmin):
    form = AppPortalEntryForm
    list_display = ["name", "url", "accent_color", "accessible_roles", "order", "is_active"]
    list_editable = ["order", "is_active"]
    list_filter = ["is_active", "accent_color"]
    search_fields = ["name", "description"]
    ordering = ["order", "name"]
    fieldsets = (
        (None, {
            "fields": ("name", "description", "url"),
        }),
        ("Appearance", {
            "fields": ("icon_emoji", "icon_image", "badge_text", "accent_color"),
        }),
        ("Access & Visibility", {
            "fields": ("accessible_roles_picker", "order", "is_active"),
        }),
    )
