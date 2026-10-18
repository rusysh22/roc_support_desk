"""New-ticket "Your Information": employee directory picker and per-category contact requirement."""
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from cases.forms import CaseCreateForm
from cases.models import CaseCategory, CaseRecord
from core.models import CompanyUnit, Employee, JobRole, SiteConfig

NO_LICENSE_GATE = override_settings(MIDDLEWARE=[m for m in settings.MIDDLEWARE if not m.startswith("licensing.")])


class RequesterInfoTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.unit = CompanyUnit.objects.create(code="IT", name="IT Operations")
        self.category = CaseCategory.objects.create(name="General", slug="general", prefix_code="GN")
        self.employee = Employee.objects.create(
            full_name="Budi Santoso", email="budi.santoso@example.com", phone_number="+6281234567890",
            job_role="IT Staff", unit=self.unit,
        )

    def data(self, **overrides):
        base = {
            "category": str(self.category.pk),
            "requester_name": "Someone New",
            "requester_email": "someone@example.com",
            "requester_phone": "",
            "contact_mode": "email",
            "company_unit": str(self.unit.pk),
            "job_role": "Analyst",
            "subject": "Printer jam",
            "problem_description": "The printer on floor 3 is jammed.",
        }
        base.update(overrides)
        return base

    def form(self, **overrides):
        return CaseCreateForm(self.data(**overrides), job_role_mode="freetext")

    def set_requirement(self, value):
        self.category.requester_contact = value
        self.category.save(update_fields=["requester_contact"])


@NO_LICENSE_GATE
class EmployeeSearchTests(RequesterInfoTestCase):
    def test_search_returns_masked_contacts(self):
        resp = self.client.get(reverse("chat:search_employees"), {"q": "budi"})
        result = resp.json()["results"][0]
        self.assertEqual(result["name"], "Budi Santoso")
        self.assertEqual(result["email_masked"], "bu***@example.com")
        self.assertEqual(result["phone_masked"], "+6281****7890")
        self.assertNotIn("budi.santoso@example.com", resp.content.decode())
        self.assertNotIn("+6281234567890", resp.content.decode())

    def test_short_query_returns_nothing(self):
        self.assertEqual(self.client.get(reverse("chat:search_employees"), {"q": "b"}).json()["results"], [])


class ContactRequirementTests(RequesterInfoTestCase):
    def test_either_needs_one_contact(self):
        self.assertTrue(self.form().is_valid())
        self.assertFalse(self.form(requester_email="").is_valid())
        self.assertTrue(self.form(requester_email="", requester_phone="081234567899", contact_mode="phone").is_valid())

    def test_phone_required(self):
        self.set_requirement(CaseCategory.RequesterContact.PHONE)
        form = self.form()
        self.assertFalse(form.is_valid())
        self.assertIn("requester_phone", form.errors)
        self.assertTrue(self.form(requester_phone="081234567899").is_valid())

    def test_both_required(self):
        self.set_requirement(CaseCategory.RequesterContact.BOTH)
        self.assertFalse(self.form().is_valid())
        self.assertTrue(self.form(requester_phone="081234567899").is_valid())

    def test_picked_employee_uses_contacts_on_file(self):
        self.set_requirement(CaseCategory.RequesterContact.BOTH)
        form = self.form(requester_employee=str(self.employee.pk), requester_email="", requester_name="ignored")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["requester_name"], "Budi Santoso")
        self.assertEqual(form.cleaned_data["requester_email"], "budi.santoso@example.com")
        self.assertEqual(form.cleaned_data["requester_phone"], "+6281234567890")

    def test_picked_employee_missing_required_contact(self):
        self.employee.phone_number = None
        self.employee.save()
        self.set_requirement(CaseCategory.RequesterContact.PHONE)
        form = self.form(requester_employee=str(self.employee.pk), requester_email="")
        self.assertFalse(form.is_valid())
        self.assertIn("requester_phone", form.errors)


class JobRoleMasterTests(RequesterInfoTestCase):
    def test_master_mode_uses_job_roles(self):
        JobRole.objects.create(name="IT Staff")
        form = CaseCreateForm(self.data(job_role="Not In List"), job_role_mode="master")
        self.assertFalse(form.is_valid())
        self.assertIn("job_role", form.errors)
        self.assertTrue(CaseCreateForm(self.data(job_role="IT Staff"), job_role_mode="master").is_valid())

    def test_master_mode_without_job_roles_falls_back_to_free_text(self):
        self.assertTrue(CaseCreateForm(self.data(job_role="Anything"), job_role_mode="master").is_valid())

    def test_default_mode_is_master(self):
        self.assertEqual(SiteConfig.get_solo().job_role_mode, "master")


@NO_LICENSE_GATE
class CreateCaseViewTests(RequesterInfoTestCase):
    def test_submit_with_picked_employee_links_record_and_fills_missing_phone(self):
        self.employee.phone_number = None
        self.employee.save()
        self.set_requirement(CaseCategory.RequesterContact.BOTH)
        resp = self.client.post(reverse("cases:create_case"), self.data(
            requester_employee=str(self.employee.pk), requester_email="", requester_phone="081299998888",
            requester_name="Budi Santoso",
        ))
        self.assertEqual(resp.status_code, 302, resp.content.decode()[:500])
        case = CaseRecord.objects.get()
        self.assertEqual(case.requester, self.employee)
        self.assertEqual(case.requester_email, "budi.santoso@example.com")
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.phone_number, "+6281299998888")

    def test_page_renders_picker(self):
        resp = self.client.get(reverse("cases:create_case"))
        self.assertContains(resp, "requester-name-search")
        self.assertContains(resp, 'id="category-contact-data"')
