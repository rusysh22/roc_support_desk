from django.urls import path
from . import views_employee

app_name = "employees_desk"

urlpatterns = [
    path("", views_employee.employee_list, name="employee_list"),
    path("create/", views_employee.employee_create, name="employee_create"),
    path("<uuid:pk>/edit/", views_employee.employee_edit, name="employee_edit"),
    path("<uuid:pk>/delete/", views_employee.employee_delete, name="employee_delete"),
    path("import/template/", views_employee.employee_export_template, name="employee_export_template"),
    path("import/", views_employee.employee_import, name="employee_import"),
]
