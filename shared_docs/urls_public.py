from django.urls import path
from . import views_public

app_name = "shared_docs_public"

urlpatterns = [
    path("<uuid:pk>/", views_public.public_doc_view, name="doc_view"),
]
