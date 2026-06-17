from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import SharedDocument

@admin.register(SharedDocument)
class SharedDocumentAdmin(ModelAdmin):
    list_display = ("title", "is_active", "created_by", "created_at", "updated_at")
    search_fields = ("title", "content")
    list_filter = ("is_active",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")
