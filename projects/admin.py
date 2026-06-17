from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline
from .models import Project, ProjectPhase, ProjectUpdate, ProjectComment


class ProjectUpdateInline(TabularInline):
    model = ProjectUpdate
    extra = 1
    fields = ("title", "content", "shared_doc", "attachment", "created_by")
    readonly_fields = ("created_by",)


class ProjectPhaseInline(TabularInline):
    model = ProjectPhase
    extra = 1
    fields = ("name", "order", "status", "start_date", "end_date")


@admin.register(Project)
class ProjectAdmin(ModelAdmin):
    list_display = ("name", "client_name", "status", "is_public", "created_by", "created_at")
    search_fields = ("name", "client_name", "description")
    list_filter = ("status", "is_public")
    inlines = [ProjectPhaseInline]
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")


@admin.register(ProjectPhase)
class ProjectPhaseAdmin(ModelAdmin):
    list_display = ("project", "name", "order", "status", "start_date", "end_date")
    list_filter = ("status", "project")
    search_fields = ("name", "description", "project__name")
    inlines = [ProjectUpdateInline]
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")


@admin.register(ProjectUpdate)
class ProjectUpdateAdmin(ModelAdmin):
    list_display = ("title", "phase", "created_by", "created_at")
    search_fields = ("title", "content")
    list_filter = ("phase__project",)
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")


@admin.register(ProjectComment)
class ProjectCommentAdmin(ModelAdmin):
    list_display = ("phase", "user", "guest_name", "is_visible", "created_at")
    list_filter = ("phase__project", "is_visible")
    search_fields = ("comment", "guest_name", "guest_email")
    readonly_fields = ("id", "created_at", "updated_at", "created_by", "updated_by")
    list_editable = ("is_visible",)

