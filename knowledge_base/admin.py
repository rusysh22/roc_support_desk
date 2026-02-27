"""
Knowledge Base App — Django Admin Registration
================================================
"""
from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Article


@admin.register(Article)
class ArticleAdmin(ModelAdmin):
    """Admin for knowledge base articles."""

    list_display = (
        "title",
        "category",
        "is_published",
        "source_case",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_published", "category")
    search_fields = ("title", "problem_summary", "root_cause", "solution")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")

    fieldsets = (
        ("Article Info", {
            "fields": ("title", "slug", "category", "source_case", "is_published"),
        }),
        ("Content", {
            "fields": ("problem_summary", "root_cause", "solution"),
        }),
        ("Audit Trail", {
            "classes": ("collapse",),
            "fields": ("id", "created_at", "updated_at", "created_by", "updated_by"),
        }),
    )

    def save_model(self, request, obj, form, change):
        """Auto-populate audit fields on save."""
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
