"""Document Template admin pages: edit no longer 500s, fields (incl. "Is E-Signer?") save, list needs staff."""
import json

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from cases.models import DocumentTemplate, DocumentTemplateField
from core.models import User


@override_settings(MIDDLEWARE=[m for m in settings.MIDDLEWARE if not m.startswith("licensing.")])
class DocumentTemplateAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="Admin", email="admin@example.com", login_username="admin", initials="AD",
            password="pass-1234", role_access=User.RoleAccess.SUPERADMIN,
        )
        self.client.force_login(self.admin)
        self.template = DocumentTemplate.objects.create(
            title="IT Change and Deployment Request Form", body_html="<p>{{ system_name }}</p>",
            is_standalone=True, form_stages=["Requester"],
        )
        DocumentTemplateField.objects.create(
            template=self.template, order=1, label="System Name", variable_name="system_name", field_type="text",
        )
        DocumentTemplateField.objects.create(
            template=self.template, order=2, label="Approver Email", variable_name="approver_email",
            field_type="text", is_signer=True,
        )

    def fields_payload(self):
        return json.dumps([
            {"label": "System Name", "variable_name": "system_name", "field_type": "text", "is_required": True},
            {"label": "Approver Email", "variable_name": "approver_email", "field_type": "text", "is_signer": True},
        ])

    def test_edit_page_opens(self):
        """Regression: the page read the removed ``esign_role`` column and raised FieldError (HTTP 500)."""
        response = self.client.get(reverse("desk:document_template_edit", args=[self.template.pk]))
        self.assertEqual(response.status_code, 200)
        fields = json.loads(response.context["existing_fields_json"])
        self.assertEqual([f["is_signer"] for f in fields], [False, True])

    def test_edit_saves_fields_with_signer_flag(self):
        response = self.client.post(reverse("desk:document_template_edit", args=[self.template.pk]), {
            "title": "IT Change and Deployment Request Form", "description": "", "body_html": "<p>{{ system_name }}</p>",
            "fields_json": self.fields_payload(),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(self.template.fields.order_by("order").values_list("label", "is_signer")),
            [("System Name", False), ("Approver Email", True)],
        )
        self.template.refresh_from_db()
        self.assertEqual(self.template.form_stages, ["Requester"])  # not on the form, left untouched

    def test_create_with_fields(self):
        response = self.client.post(reverse("desk:document_template_create"), {
            "title": "New Template", "description": "", "body_html": "<p>x</p>", "fields_json": self.fields_payload(),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(DocumentTemplate.objects.get(title="New Template").fields.count(), 2)

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("desk:document_template_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)
