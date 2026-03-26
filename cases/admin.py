"""
Cases App — Django Admin Registration
=======================================
Provides full admin interfaces with inline messages and attachments,
list filters, and audit field auto-population.
"""
from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline, StackedInline

from .models import Attachment, CaseCategory, CaseRecord, Message, RCATemplate


# =====================================================================
# Inlines
# =====================================================================

class AttachmentInline(TabularInline):
    """Inline attachments within a Message."""
    model = Attachment
    extra = 0
    readonly_fields = ("id", "file_size", "mime_type", "created_at")


class MessageInline(StackedInline):
    """Inline messages within a CaseRecord."""
    model = Message
    extra = 0
    readonly_fields = (
        "id", "external_id", "direction", "channel", "sent_at",
        "created_at", "created_by",
    )
    show_change_link = True


# =====================================================================
# Ticket Category
# =====================================================================

@admin.register(CaseCategory)
class CaseCategoryAdmin(ModelAdmin):
    """Admin for service catalogue categories."""

    list_display = ("name", "parent", "prefix_code", "slug", "icon", "is_confidential", "created_at")
    list_filter = ("parent", "is_confidential")
    search_fields = ("name", "prefix_code", "slug")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# RCA Template
# =====================================================================

@admin.register(RCATemplate)
class RCATemplateAdmin(ModelAdmin):
    """Admin for RCA quick-fill templates."""

    list_display = ("name", "category", "order", "created_at")
    list_filter = ("category",)
    list_editable = ("order",)
    search_fields = ("name", "rca_text", "solving_steps_text")
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# Ticket Record
# =====================================================================

@admin.register(CaseRecord)
class CaseRecordAdmin(ModelAdmin):
    """Admin for case management — includes inline messages."""

    list_display = (
        "case_number",
        "subject",
        "status",
        "source",
        "requester",
        "assigned_to",
        "created_at",
        "response_due_at",
        "resolution_due_at",
        "is_spam",
    )
    list_filter = ("is_spam", "status", "source", "category", "assigned_to")
    search_fields = ("subject", "requester__full_name", "requester__email")
    ordering = ("-created_at",)
    readonly_fields = (
        "id", "case_number", "created_at", "updated_at",
        "created_by", "updated_by", "form_data",
    )
    inlines = [MessageInline]

    fieldsets = (
        ("Ticket Overview", {
            "fields": (
                "id", "case_number", "is_spam", "requester", "category",
                "subject", "status", "source", "assigned_to",
            ),
        }),
        ("Problem & Solving", {
            "fields": (
                "problem_description",
                "root_cause_analysis",
                "solving_steps",
            ),
        }),
        ("SLA Tracking", {
            "fields": ("response_due_at", "resolution_due_at"),
        }),
        ("Dynamic Data", {
            "classes": ("collapse",),
            "fields": ("form_data",),
        }),
        ("Audit Trail", {
            "classes": ("collapse",),
            "fields": ("created_at", "updated_at", "created_by", "updated_by"),
        }),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# Message
# =====================================================================

@admin.register(Message)
class MessageAdmin(ModelAdmin):
    """Admin for individual messages."""

    list_display = (
        "case",
        "direction",
        "channel",
        "sender_display",
        "body_preview",
        "external_id",
        "sent_at",
    )
    list_filter = ("direction", "channel")
    search_fields = ("body", "external_id")
    readonly_fields = (
        "id", "external_id", "sent_at",
        "created_at", "updated_at", "created_by", "updated_by",
    )
    inlines = [AttachmentInline]

    @admin.display(description="Sender")
    def sender_display(self, obj):
        if obj.sender_employee:
            return f"👤 {obj.sender_employee.full_name}"
        if obj.sender_staff:
            return f"🛠️ {obj.sender_staff.username}"
        return "System"

    @admin.display(description="Body")
    def body_preview(self, obj):
        return obj.body[:80] + "..." if len(obj.body) > 80 else obj.body

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# Attachment
# =====================================================================

@admin.register(Attachment)
class AttachmentAdmin(ModelAdmin):
    """Admin for file attachments."""

    list_display = ("original_filename", "mime_type", "file_size", "message", "created_at")
    search_fields = ("original_filename",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
