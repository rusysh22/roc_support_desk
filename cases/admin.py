"""
Cases App — Django Admin Registration
"""
from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline, StackedInline

from .models import (
    Attachment, CaseAuditLog, CaseCategory, CaseRecord,
    ChangeRequestApproval, ChangeRequestDocument,
    DocumentTemplate, DocumentTemplateField, Message, RCATemplate,
)


# =====================================================================
# Inlines
# =====================================================================

class AttachmentInline(TabularInline):
    model = Attachment
    extra = 0
    readonly_fields = ("id", "file_size", "mime_type", "created_at")


class MessageInline(StackedInline):
    model = Message
    extra = 0
    readonly_fields = (
        "id", "external_id", "direction", "channel", "sent_at",
        "created_at", "created_by",
    )
    show_change_link = True


class ChangeRequestApprovalInline(TabularInline):
    model = ChangeRequestApproval
    extra = 0
    fields = ("approver", "order", "status", "acted_at", "notes")
    readonly_fields = ("acted_at", "token")
    ordering = ("order",)

    def has_add_permission(self, request, obj=None):
        return False


class DocumentTemplateFieldInline(TabularInline):
    model = DocumentTemplateField
    extra = 1
    max_num = 10
    fields = ("order", "label", "placeholder", "is_required")
    ordering = ("order",)


# =====================================================================
# Ticket Category
# =====================================================================

@admin.register(CaseCategory)
class CaseCategoryAdmin(ModelAdmin):
    list_display = (
        "name", "parent", "enable_change_request", "prefix_code", "slug",
        "is_confidential", "created_at",
    )
    list_filter = ("parent", "is_confidential", "enable_change_request")
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
    list_display = (
        "case_number", "subject", "status", "source",
        "requester", "assigned_to", "created_at",
        "response_due_at", "resolution_due_at", "is_spam",
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
    list_display = (
        "case", "direction", "channel", "sender_display",
        "body_preview", "external_id", "sent_at",
    )
    list_filter = ("direction", "channel")
    search_fields = ("body", "external_id")
    readonly_fields = (
        "id", "external_id", "sent_at",
        "created_at", "updated_at", "created_by", "updated_by",
    )
    inlines = [AttachmentInline]

    def has_module_permission(self, request):
        return False

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
    list_display = ("original_filename", "mime_type", "file_size", "message", "created_at")
    search_fields = ("original_filename",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")

    def has_module_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# Document Template (admin letter management, no portal integration)
# =====================================================================

@admin.register(DocumentTemplate)
class DocumentTemplateAdmin(ModelAdmin):
    list_display = ("title", "field_count", "approval_flow", "is_required", "created_at")
    list_filter = ("approval_flow", "is_required", "categories")
    search_fields = ("title", "description")
    filter_horizontal = ("categories",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")
    inlines = [DocumentTemplateFieldInline]

    @admin.display(description="Fields")
    def field_count(self, obj):
        return obj.fields.count()

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


# =====================================================================
# Change Request Document
# =====================================================================

@admin.register(ChangeRequestDocument)
class ChangeRequestDocumentAdmin(ModelAdmin):
    list_display = ("case", "status", "revision_number", "submitted_at", "created_at")
    list_filter = ("status",)
    search_fields = ("case__subject", "request_change")
    readonly_fields = (
        "id", "generated_pdf", "submitted_at", "revision_number",
        "created_at", "updated_at", "created_by", "updated_by",
    )
    inlines = [ChangeRequestApprovalInline]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
