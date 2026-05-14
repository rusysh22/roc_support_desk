"""Manual app admin registration."""
from django.contrib import admin
from .models import Manual, ManualLandingConfig, ManualPage


class ManualPageInline(admin.TabularInline):
    model = ManualPage
    extra = 0
    fields = ("title", "parent", "order", "is_published", "is_faq")
    ordering = ("order", "title")


@admin.register(Manual)
class ManualAdmin(admin.ModelAdmin):
    list_display = ("icon", "title", "is_published", "order", "created_at")
    list_editable = ("order", "is_published")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [ManualPageInline]


@admin.register(ManualLandingConfig)
class ManualLandingConfigAdmin(admin.ModelAdmin):
    fields = ("hero_title", "hero_subtitle")


@admin.register(ManualPage)
class ManualPageAdmin(admin.ModelAdmin):
    list_display = ("title", "manual", "parent", "is_published", "is_faq", "order")
    list_filter = ("manual", "is_published", "is_faq")
    list_editable = ("order", "is_published")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("parent",)
