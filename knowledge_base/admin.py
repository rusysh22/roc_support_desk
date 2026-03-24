"""
Knowledge Base App — Django Admin Registration
================================================
"""
from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import Article, ArticleImage, ArticleTag


@admin.register(ArticleTag)
class ArticleTagAdmin(ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ArticleImage)
class ArticleImageAdmin(ModelAdmin):
    list_display = ("alt_text", "image", "created_at")
    search_fields = ("alt_text",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")


@admin.register(Article)
class ArticleAdmin(ModelAdmin):
    """Admin for knowledge base articles."""

    list_display = (
        "title",
        "category",
        "status",
        "is_published",
        "created_by",
        "reviewed_by",
        "created_at",
    )
    list_filter = ("status", "is_published", "category")
    search_fields = ("title", "problem_summary", "root_cause", "solution")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("-created_at",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by", "is_published")
    filter_horizontal = ("tags",)

    fieldsets = (
        ("Article Info", {
            "fields": ("title", "slug", "category", "source_case", "tags"),
        }),
        ("Content", {
            "fields": ("problem_summary", "root_cause", "solution"),
        }),
        ("Publishing", {
            "fields": ("status", "is_published", "reviewed_by", "reviewed_at", "rejection_reason"),
        }),
        ("Audit Trail", {
            "classes": ("collapse",),
            "fields": ("id", "created_at", "updated_at", "created_by", "updated_by"),
        }),
    )

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)
