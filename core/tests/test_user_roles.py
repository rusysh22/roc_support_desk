"""User Roles master data: permissions, base-tier sync, module defaults and the admin screens."""
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from unittest import mock

from core.models import SiteConfig, User, UserRole

# The product ships no permission codes yet; tests register sample ones.
SAMPLE_PERMISSIONS = [
    ("reports.view", "View reports", "Reports"),
    ("reports.edit", "Edit reports", "Reports"),
    ("reports.approve", "Approve reports", "Reports"),
]
WITH_SAMPLE_PERMISSIONS = [
    mock.patch("core.models.PERMISSION_REGISTRY", SAMPLE_PERMISSIONS),
    mock.patch("core.views_roles.PERMISSION_REGISTRY", SAMPLE_PERMISSIONS),
]

NO_LICENSE_GATE = override_settings(MIDDLEWARE=[m for m in settings.MIDDLEWARE if not m.startswith("licensing.")])


def seed_roles():
    """What migration core 0070 creates (test databases skip data migrations)."""
    for order, (code, label) in enumerate(User.RoleAccess.choices, start=1):
        UserRole.objects.get_or_create(code=code, defaults={
            "name": label, "base_access": code, "is_system": True, "sort_order": order * 10,
            "permissions": ["*"] if code == User.RoleAccess.SUPERADMIN else [],
        })
    staff, _ = UserRole.objects.get_or_create(code="reports_staff", defaults={
        "name": "Reports Staff", "base_access": User.RoleAccess.PORTALUSER,
        "permissions": ["reports.view", "reports.edit"], "sort_order": 70,
    })
    approval, _ = UserRole.objects.get_or_create(code="reports_approval", defaults={
        "name": "Reports Approval", "base_access": User.RoleAccess.PORTALUSER,
        "permissions": ["reports.view", "reports.approve"], "sort_order": 80,
    })
    return staff, approval


def make_user(login, role_access=User.RoleAccess.PORTALUSER, user_role=None):
    return User.objects.create_user(
        username=login.title(), email=f"{login}@example.com", login_username=login, initials=login[:2].upper(),
        password="pass-1234", role_access=role_access, user_role=user_role,
    )


@WITH_SAMPLE_PERMISSIONS[0]
@WITH_SAMPLE_PERMISSIONS[1]
class UserRoleModelTests(TestCase):
    def setUp(self):
        self.staff_role, self.approval_role = seed_roles()

    def test_custom_role_sets_base_tier_and_permissions(self):
        user = make_user("budi", role_access=User.RoleAccess.MANAGER, user_role=self.staff_role)
        self.assertEqual(user.role_access, User.RoleAccess.PORTALUSER)   # follows the role's base level
        self.assertTrue(user.has_capability("reports.edit"))
        self.assertFalse(user.has_capability("reports.approve"))
        self.assertEqual(user.role_label, "Reports Staff")

    def test_system_role_follows_role_access_changes(self):
        user = make_user("sari", role_access=User.RoleAccess.SUPPORTDESK)
        self.assertEqual(user.user_role.code, "SupportDesk")             # linked to its system role
        user.role_access = User.RoleAccess.MANAGER
        user.save()
        self.assertEqual(user.user_role.code, "Manager")

    def test_wildcard_and_inactive_roles(self):
        admin = make_user("root", role_access=User.RoleAccess.SUPERADMIN)
        self.assertTrue(admin.has_capability("reports.approve"))
        self.assertTrue(admin.has_capability("some.future.permission"))
        self.staff_role.is_active = False
        self.staff_role.save()
        user = make_user("budi", user_role=self.staff_role)
        self.assertFalse(user.has_capability("reports.edit"))

    def test_custom_role_adds_its_module_defaults(self):
        config = SiteConfig.get_solo()
        config.role_module_defaults = {"PortalUser": ["my_tickets"], "reports_staff": ["app_portal"]}
        config.save()
        user = make_user("budi", user_role=self.staff_role)
        self.assertEqual(user.get_accessible_modules(), {"my_tickets", "app_portal"})


@NO_LICENSE_GATE
@WITH_SAMPLE_PERMISSIONS[0]
@WITH_SAMPLE_PERMISSIONS[1]
class UserRoleAdminTests(TestCase):
    def setUp(self):
        self.staff_role, self.approval_role = seed_roles()
        self.admin = make_user("root", role_access=User.RoleAccess.SUPERADMIN)
        self.client.force_login(self.admin)

    def test_list_and_create(self):
        self.assertContains(self.client.get(reverse("users_desk:role_list")), "Reports Approval")
        response = self.client.post(reverse("users_desk:role_create"), {
            "name": "Reports Auditor", "code": "reports_auditor", "base_access": "PortalUser",
            "permissions": ["reports.view"], "sort_order": "90", "is_active": "on",
        })
        self.assertEqual(response.status_code, 302, response.context and response.context["form"].errors)
        role = UserRole.objects.get(code="reports_auditor")
        self.assertEqual(role.permissions, ["reports.view"])
        self.assertFalse(role.is_system)

    def test_all_permissions_collapses_to_wildcard(self):
        self.client.post(reverse("users_desk:role_edit", args=[self.staff_role.pk]), {
            "name": "Reports Staff", "base_access": "PortalUser", "permissions": ["*", "reports.view"],
            "sort_order": "70", "is_active": "on",
        })
        self.staff_role.refresh_from_db()
        self.assertEqual(self.staff_role.permissions, ["*"])

    def test_system_roles_keep_code_and_level_and_cannot_be_deleted(self):
        manager = UserRole.objects.get(code="Manager")
        self.client.post(reverse("users_desk:role_edit", args=[manager.pk]), {
            "name": "Manager", "code": "hacked", "base_access": "PortalUser", "permissions": ["reports.view"],
            "sort_order": "20", "is_active": "on",
        })
        manager.refresh_from_db()
        self.assertEqual((manager.code, manager.base_access, manager.permissions), ("Manager", "Manager", ["reports.view"]))
        self.client.post(reverse("users_desk:role_delete", args=[manager.pk]))
        self.assertTrue(UserRole.objects.filter(pk=manager.pk).exists())

    def test_role_in_use_cannot_be_deleted(self):
        make_user("budi", user_role=self.staff_role)
        self.client.post(reverse("users_desk:role_delete", args=[self.staff_role.pk]))
        self.assertTrue(UserRole.objects.filter(pk=self.staff_role.pk).exists())

    def test_changing_a_roles_base_level_moves_its_users(self):
        user = make_user("budi", user_role=self.staff_role)
        self.client.post(reverse("users_desk:role_edit", args=[self.staff_role.pk]), {
            "name": "Reports Staff", "base_access": "Auditor", "permissions": ["reports.view"],
            "sort_order": "70", "is_active": "on",
        })
        user.refresh_from_db()
        self.assertEqual(user.role_access, "Auditor")

    def test_module_defaults_page_lists_custom_roles(self):
        response = self.client.get(reverse("users_desk:portal_modules_settings"))
        self.assertContains(response, "reports_staff")

    def test_user_form_takes_a_user_role(self):
        from core.forms import UserAdminForm
        form = UserAdminForm({
            "login_username": "budi", "username": "Budi", "email": "budi@example.com", "nik": "",
            "user_role": self.staff_role.pk, "role_access": "SuperAdmin", "initials": "BU",
        })
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertEqual((user.user_role, user.role_access, user.is_staff), (self.staff_role, "PortalUser", False))
        # Editing a user preselects their role.
        self.assertEqual(UserAdminForm(instance=user).initial.get("user_role"), self.staff_role.pk)

    def test_non_superadmin_is_blocked(self):
        self.client.force_login(make_user("budi", user_role=self.staff_role))
        self.assertNotEqual(self.client.get(reverse("users_desk:role_list")).status_code, 200)
