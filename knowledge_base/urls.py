"""Knowledge Base — URL Configuration."""
from django.urls import path
from . import views

app_name = "knowledge_base"

urlpatterns = [
    # Main portal view
    path("", views.kb_home, name="home"),
    
    # Search view
    path("search/", views.kb_search, name="search"),
    
    # Category list view
    path("c/<slug:slug>/", views.kb_category, name="category"),
    
    # Article details view
    path("a/<slug:slug>/", views.kb_article_detail, name="article_detail"),
]
