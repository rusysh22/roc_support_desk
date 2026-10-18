"""Knowledge Base — URL Configuration."""
from django.urls import path
from . import views

app_name = "knowledge_base"

urlpatterns = [
    # Main portal view
    path("", views.kb_home, name="home"),
    
    # Search view
    path("search/", views.kb_search, name="search"),

    # Live-typing search suggestions (JSON, AJAX)
    path("search/suggest/", views.kb_search_suggest, name="search_suggest"),
    
    # Category list view
    path("c/<slug:slug>/", views.kb_category, name="category"),
    
    # Article details view
    path("a/<slug:slug>/", views.kb_article_detail, name="article_detail"),

    # Like / unlike an article (POST, login required)
    path("a/<slug:slug>/like/", views.kb_article_like, name="article_like"),

    # Article comment submission (POST, login required)
    path("a/<slug:slug>/comment/", views.kb_article_comment, name="article_comment"),

    # Delete a comment (POST): its author or a SuperAdmin
    path("a/<slug:slug>/comment/<uuid:comment_id>/delete/", views.kb_article_comment_delete, name="article_comment_delete"),
]
