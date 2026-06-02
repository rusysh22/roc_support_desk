"""E-Signature App — Admin Registration."""
from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import (
    SignatureDocument,
    SignatureEvent,
    SignatureFlowStep,
    SignatureFlowTemplate,
    SignaturePlacement,
    Signer,
)


class SignerInline(TabularInline):
    model = Signer
    extra = 0
    readonly_fields = ("token", "token_expires_at", "status", "acted_at", "signed_ip")
    fields = ("order", "user", "external_name", "external_email", "role", "status", "acted_at", "token")


class PlacementInline(TabularInline):
    model = SignaturePlacement
    extra = 0
    fields = ("signer", "page_number", "field_type", "x", "y", "width", "height", "required")


class EventInline(TabularInline):
    model = SignatureEvent
    extra = 0
    readonly_fields = ("event", "actor_user", "actor_label", "ip", "detail", "created_at")
    fields = ("event", "actor_user", "actor_label", "ip", "detail", "created_at")
    ordering = ("-created_at",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SignatureDocument)
class SignatureDocumentAdmin(ModelAdmin):
    list_display  = ("title", "status", "routing_mode", "page_count", "created_by", "created_at")
    list_filter   = ("status", "routing_mode")
    search_fields = ("title",)
    readonly_fields = ("document_hash", "page_count", "created_at", "updated_at")
    inlines = [SignerInline, PlacementInline, EventInline]


class FlowStepInline(TabularInline):
    model = SignatureFlowStep
    extra = 1
    fields = ("order", "user", "role")


@admin.register(SignatureFlowTemplate)
class SignatureFlowTemplateAdmin(ModelAdmin):
    list_display  = ("name", "routing_mode", "is_active")
    list_filter   = ("routing_mode", "is_active")
    inlines       = [FlowStepInline]


@admin.register(SignatureEvent)
class SignatureEventAdmin(ModelAdmin):
    list_display  = ("document", "event", "actor_user", "actor_label", "ip", "created_at")
    list_filter   = ("event",)
    readonly_fields = ("document", "event", "actor_user", "actor_label", "ip", "detail", "created_at")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
