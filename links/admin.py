import os
from django.contrib import admin
from unfold.admin import ModelAdmin
from .models import ShortLink
from django.utils.html import format_html


@admin.register(ShortLink)
class ShortLinkAdmin(ModelAdmin):
    list_display = ("slug", "target_url", "clicks", "created_by", "created_at", "qr_preview")
    search_fields = ("slug", "target_url", "title")
    list_filter = ("created_at",)
    readonly_fields = ("clicks", "qr_code", "created_by")

    def qr_preview(self, obj):
        if obj.qr_code:
            return format_html('<img src="{}" style="height: 50px;"/>', obj.qr_code.url)
        return "-"
    qr_preview.short_description = "QR Code"

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        
        super().save_model(request, obj, form, change)
        
        # QR code generation logic if slug changed or new
        if 'slug' in form.changed_data or not change:
            host = request.build_absolute_uri('/')
            obj.build_qr_code(host)
            obj.save()
