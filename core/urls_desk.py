"""
User Management URL Configuration
=================================
"""
from django.urls import path

from . import views_roles
from . import views_user
from . import views_employee

app_name = "users_desk"

urlpatterns = [
    path("portal-modules/", views_user.portal_modules_settings, name="portal_modules_settings"),
    path("roles/", views_roles.role_list, name="role_list"),
    path("roles/create/", views_roles.role_create, name="role_create"),
    path("roles/<uuid:pk>/edit/", views_roles.role_edit, name="role_edit"),
    path("roles/<uuid:pk>/delete/", views_roles.role_delete, name="role_delete"),
    path("roles/bulk-delete/", views_roles.role_bulk_delete, name="role_bulk_delete"),
    path("roles/bulk-export/", views_roles.role_bulk_export, name="role_bulk_export"),
    path("", views_user.user_list, name="user_list"),
    path("create/", views_user.user_create, name="user_create"),
    path("<int:pk>/edit/", views_user.user_edit, name="user_edit"),
    path("<int:pk>/delete/", views_user.user_delete, name="user_delete"),
    path("export/", views_user.user_export, name="user_export"),
    path("import/template/", views_user.user_import_template, name="user_import_template"),
    path("import/", views_user.user_import, name="user_import"),
    path("bulk-delete/", views_user.user_bulk_delete, name="user_bulk_delete"),
    path("bulk-duplicate/", views_user.user_bulk_duplicate, name="user_bulk_duplicate"),
    path("bulk-archive/", views_user.user_bulk_archive, name="user_bulk_archive"),
    path("bulk-unarchive/", views_user.user_bulk_unarchive, name="user_bulk_unarchive"),
    path("bulk-grant-modules/", views_user.user_bulk_grant_modules, name="user_bulk_grant_modules"),
    path("<int:pk>/archive/", views_user.user_archive, name="user_archive"),
    path("<int:pk>/unarchive/", views_user.user_unarchive, name="user_unarchive"),
    # Password reset
    path("bulk-password-template/", views_user.user_export_password_template, name="user_export_password_template"),
    path("bulk-password/", views_user.user_import_bulk_password, name="user_import_bulk_password"),
    path("bulk-reset-password/", views_user.user_bulk_reset_password, name="user_bulk_reset_password"),
    # Update existing via Excel
    path("export/update-template/", views_user.user_export_update_template, name="user_export_update_template"),
    path("import/update/", views_user.user_import_update, name="user_import_update"),
    
    # Employees
    path("employees/", __import__("core.views_employee", fromlist=["views_employee"]).employee_list, name="employee_list"),
    path("employees/create/", __import__("core.views_employee", fromlist=["views_employee"]).employee_create, name="employee_create"),
    path("employees/<uuid:pk>/edit/", __import__("core.views_employee", fromlist=["views_employee"]).employee_edit, name="employee_edit"),
    path("employees/<uuid:pk>/delete/", __import__("core.views_employee", fromlist=["views_employee"]).employee_delete, name="employee_delete"),
    path("employees/import/template/", __import__("core.views_employee", fromlist=["views_employee"]).employee_export_template, name="employee_export_template"),
    path("employees/import/", __import__("core.views_employee", fromlist=["views_employee"]).employee_import, name="employee_import"),
    path("employees/bulk-archive/", views_employee.employee_bulk_archive, name="employee_bulk_archive"),
    path("employees/bulk-unarchive/", views_employee.employee_bulk_unarchive, name="employee_bulk_unarchive"),
    path("employees/bulk-delete/", views_employee.employee_bulk_delete, name="employee_bulk_delete"),
    path("employees/bulk-duplicate/", views_employee.employee_bulk_duplicate, name="employee_bulk_duplicate"),
    path("employees/bulk-export/", views_employee.employee_bulk_export, name="employee_bulk_export"),
]
