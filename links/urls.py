from django.urls import path
from .views import RedirectLinkView

app_name = "links"

urlpatterns = [
    # Captures the slug and passes to the View
    path("<str:slug>/", RedirectLinkView.as_view(), name="redirect"),
]
