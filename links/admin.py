from django.contrib import admin
from .models import ShortLink


@admin.register(ShortLink)
class ShortLinkAdmin(admin.ModelAdmin):
    list_display = ("slug", "title", "target_url", "clicks", "created_at")
    search_fields = ("slug", "title", "target_url")
    readonly_fields = ("id", "clicks", "created_at", "updated_at", "created_by", "updated_by")
    list_per_page = 25
