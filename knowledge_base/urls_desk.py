"""Knowledge Base — Desk/Staff URL Configuration."""
from django.urls import path

from . import views

app_name = "kb_desk"

urlpatterns = [
    # Article CRUD
    path("", views.kb_article_list, name="article_list"),
    path("create/", views.kb_article_create, name="article_create"),
    path("create-from-case/<uuid:case_id>/", views.kb_create_from_case, name="create_from_case"),
    path("<uuid:pk>/edit/", views.kb_article_edit, name="article_edit"),
    path("<uuid:pk>/delete/", views.kb_article_delete, name="article_delete"),

    # Approval workflow
    path("<uuid:pk>/submit/", views.kb_article_submit_review, name="article_submit"),
    path("<uuid:pk>/approve/", views.kb_article_approve, name="article_approve"),
    path("<uuid:pk>/reject/", views.kb_article_reject, name="article_reject"),
    path("<uuid:pk>/unpublish/", views.kb_article_unpublish, name="article_unpublish"),

    # Image upload (Quill.js)
    path("upload-image/", views.kb_image_upload, name="image_upload"),

    # Attachment delete
    path("attachment/<uuid:pk>/delete/", views.kb_attachment_delete, name="attachment_delete"),
]
