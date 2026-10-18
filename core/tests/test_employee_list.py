from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import CompanyUnit, Employee, User
from core.views_employee import filter_employees


NO_LICENSE_GATE = override_settings(MIDDLEWARE=[m for m in settings.MIDDLEWARE if not m.startswith("licensing.")])


@NO_LICENSE_GATE
class EmployeeSearchTests(TestCase):
    def setUp(self):
        self.unit = CompanyUnit.objects.create(code="ITOPS", name="IT Operations")
        self.other_unit = CompanyUnit.objects.create(code="FIN", name="Finance")
        self.admin = User.objects.create_user(
            username="Admin",
            email="admin@example.com",
            login_username="admin",
            initials="AD",
            password="pass-admin-1",
            role_access=User.RoleAccess.SUPERADMIN,
        )
        self.linked = User.objects.create_user(
            username="Rina Wijaya",
            email="rina@example.com",
            login_username="rina.wijaya",
            initials="RW",
            password="pass-rina-1",
            nik="240157",
        )
        self.budi = Employee.objects.create(
            full_name="Budi Santoso",
            email="budi.santoso@example.com",
            phone_number="+6281234567890",
            job_role="IT Staff",
            unit=self.unit,
        )
        self.siti = Employee.objects.create(
            full_name="Siti Aminah",
            email="siti.aminah@example.com",
            phone_number="+6289876543210",
            job_role="Accountant",
            unit=self.other_unit,
            reports_to=self.budi,
        )
        self.client.force_login(self.admin)

    def _names(self, q):
        return list(
            filter_employees(Employee.objects.all(), q).values_list("full_name", flat=True)
        )

    def test_search_by_name(self):
        names = self._names("Budi")
        self.assertIn("Budi Santoso", names)
        self.assertNotIn("Siti Aminah", names)

    def test_search_by_unit_code(self):
        names = self._names("ITOPS")
        self.assertIn("Budi Santoso", names)
        self.assertNotIn("Siti Aminah", names)

    def test_search_by_job_role(self):
        names = self._names("Accountant")
        self.assertIn("Siti Aminah", names)
        self.assertNotIn("Budi Santoso", names)

    def test_search_local_phone_matches_e164(self):
        names = self._names("081234567890")
        self.assertIn("Budi Santoso", names)
        self.assertNotIn("Siti Aminah", names)

    def test_search_by_nik(self):
        names = self._names("240157")
        self.assertIn("Rina Wijaya", names)
        self.assertNotIn("Budi Santoso", names)

    def test_search_name_and_unit_tokens(self):
        names = self._names("Budi ITOPS")
        self.assertEqual(names, ["Budi Santoso"])

    def test_list_view_returns_matching_row(self):
        resp = self.client.get(reverse("users_desk:employee_list"), {"q": "ITOPS"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Budi Santoso")
        self.assertNotContains(resp, "Siti Aminah")
