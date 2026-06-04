"""E-Signature — Staff URL patterns (namespace: esign)."""
from django.urls import path

from . import views

app_name = "esign"

urlpatterns = [
    path("",                    views.document_list,    name="document_list"),
    path("new/",                views.document_create,  name="document_create"),
    path("<uuid:pk>/configure/",views.document_configure,name="document_configure"),
    path("<uuid:pk>/configure/apply-template/", views.apply_flow_template, name="apply_flow_template"),
    path("<uuid:pk>/configure/save/",           views.save_configuration,  name="save_configuration"),
    path("<uuid:pk>/replace-pdf/", views.document_replace_pdf, name="document_replace_pdf"),
    path("<uuid:pk>/send/",     views.document_send,    name="document_send"),
    path("<uuid:pk>/",          views.document_detail,  name="document_detail"),
    path("<uuid:pk>/revert-to-draft/", views.document_revert_to_draft, name="document_revert_to_draft"),
    path("<uuid:pk>/cancel/",   views.document_cancel,  name="document_cancel"),
    path("<uuid:pk>/signers/<uuid:signer_pk>/remind/", views.signer_remind, name="signer_remind"),
    path("my-signatures/",           views.my_signatures,         name="my_signatures"),
    path("my-signatures/<uuid:pk>/", views.my_signature_detail,    name="my_signature_detail"),
    path("api/users/",               views.user_search,            name="user_search"),
    path("api/employees/",           views.employee_search,        name="employee_search"),
]
