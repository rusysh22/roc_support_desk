"""Upload validation, self-signing, contributors, remind cooldown, sign order and job role defaults."""
import json
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import CompanyUnit, Employee, User
from esign.forms import DocumentUploadForm
from esign.models import SignatureDocument, SignatureEvent, Signer
from esign.services import compact_orders, default_job_role, remind_signer, send_document

PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
MEDIA = tempfile.mkdtemp(prefix="esign-tests-")


def set_job_role(user, job_role):
    """Users get an Employee record automatically (core signal); give it a job role."""
    employee = Employee.objects.filter(email__iexact=user.email).first()
    if employee is None:
        unit = CompanyUnit.objects.get_or_create(code="ALPHA", defaults={"name": "Alpha"})[0]
        employee = Employee.objects.create(full_name=user.get_full_name(), email=user.email, unit=unit)
    employee.job_role = job_role
    employee.save()


def make_user(login, email=None):
    return User.objects.create_user(
        username=login.title(), email=email if email is not None else f"{login}@example.com",
        login_username=login, initials=login[:2].upper(), password="pass-1234",
        first_name=login.title(), role_access=User.RoleAccess.MANAGER,
    )


@override_settings(
    MEDIA_ROOT=MEDIA,
    MIDDLEWARE=[m for m in settings.MIDDLEWARE if not m.startswith("licensing.")],
)
class EsignTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.owner = make_user("owner")
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        self.client.force_login(self.owner)

    def make_doc(self, **kwargs):
        data = {"title": "Approval Letter", "original_pdf": SimpleUploadedFile("letter.pdf", PDF_BYTES), "created_by": self.owner}
        data.update(kwargs)
        return SignatureDocument.objects.create(**data)

    def add_signer(self, doc, user, order=1, **kwargs):
        return Signer.objects.create(document=doc, user=user, order=order, created_by=self.owner, **kwargs)


class UploadValidationTests(EsignTestCase):
    def form(self, upload):
        return DocumentUploadForm(
            {"title": "Doc", "routing_mode": "sequential"}, {"original_pdf": upload},
        )

    def test_pdf_is_accepted(self):
        self.assertTrue(self.form(SimpleUploadedFile("ok.pdf", PDF_BYTES)).is_valid())

    def test_other_extension_or_fake_pdf_is_rejected(self):
        for upload in (SimpleUploadedFile("scan.png", PDF_BYTES), SimpleUploadedFile("fake.pdf", b"just some text")):
            with self.subTest(name=upload.name):
                form = self.form(upload)
                self.assertFalse(form.is_valid())
                self.assertIn("Only PDF files are accepted.", form.errors["original_pdf"])

    def test_upload_zone_is_compact_and_pdf_only(self):
        html = self.client.get(reverse("esign:document_create")).content.decode()
        self.assertIn('x-data="esignPdfDrop(', html)
        self.assertIn("PDF only · max", html)
        self.assertIn('accept="application/pdf,.pdf"', html)


class SignOrderTests(EsignTestCase):
    def test_compact_orders_closes_gaps_and_keeps_ties(self):
        self.assertEqual(compact_orders([1, 3, 3, 7]), [1, 2, 2, 3])
        self.assertEqual(compact_orders([5]), [1])

    def test_save_configuration_renumbers_and_stores_contributors(self):
        doc = self.make_doc()
        payload = {
            "routing_mode": "sequential",
            "signers": [
                {"type": "user", "user_id": str(self.alice.pk), "order": 1},
                {"type": "user", "user_id": str(self.bob.pk), "order": 4},
            ],
            "placements": [],
            "contributors": [str(self.bob.pk), str(self.owner.pk)],
        }
        response = self.client.post(
            reverse("esign:save_configuration", args=[doc.pk]), json.dumps(payload), content_type="application/json",
        )
        self.assertTrue(response.json()["ok"])
        self.assertEqual(list(doc.signers.order_by("order").values_list("order", flat=True)), [1, 2])
        self.assertEqual(list(doc.contributors.all()), [self.bob])  # the owner is never their own contributor

    def test_configure_page_has_next_number_and_self_sign_shortcut(self):
        doc = self.make_doc()
        html = self.client.get(reverse("esign:document_configure", args=[doc.pk])).content.decode()
        self.assertIn("openAddSigner()", html)
        self.assertIn("renumberOrders()", html)
        self.assertIn("I'll sign this myself", html)
        self.assertIn('id="esign-current-user"', html)


class SelfSignTests(EsignTestCase):
    def test_owner_as_only_signer_goes_straight_to_signing(self):
        doc = self.make_doc()
        signer = self.add_signer(doc, self.owner)
        response = self.client.post(reverse("esign:document_send", args=[doc.pk]))
        signer.refresh_from_db()
        self.assertRedirects(response, reverse("esign_public:sign", args=[signer.token]), fetch_redirect_response=False)
        # No request email to yourself — you are signing right now.
        self.assertFalse([m for m in mail.outbox if "Signature Request" in m.subject])

    def test_owner_first_of_several_also_redirected_others_wait(self):
        doc = self.make_doc()
        mine = self.add_signer(doc, self.owner, order=1)
        self.add_signer(doc, self.alice, order=2)
        response = self.client.post(reverse("esign:document_send", args=[doc.pk]))
        mine.refresh_from_db()
        self.assertEqual(response.url, reverse("esign_public:sign", args=[mine.token]))

    def test_detail_shows_sign_now_when_it_is_my_turn(self):
        doc = self.make_doc()
        self.add_signer(doc, self.alice, order=1)
        send_document(doc, actor_user=self.owner)
        self.client.force_login(self.alice)
        doc.contributors.add(self.alice)
        self.assertContains(self.client.get(reverse("esign:document_detail", args=[doc.pk])), "It's your turn to sign")


class RemindCooldownTests(EsignTestCase):
    def setUp(self):
        super().setUp()
        self.doc = self.make_doc()
        self.signer = self.add_signer(self.doc, self.alice)
        send_document(self.doc, actor_user=self.owner)
        self.signer.refresh_from_db()

    def test_reminder_blocked_for_six_hours_after_the_last_request(self):
        self.assertIsNotNone(self.signer.last_requested_at)
        with self.assertRaisesMessage(ValueError, "less than 6 hours ago"):
            remind_signer(self.signer, actor_user=self.owner)
        Signer.objects.filter(pk=self.signer.pk).update(last_requested_at=timezone.now() - timedelta(hours=6, minutes=1))
        self.signer.refresh_from_db()
        remind_signer(self.signer, actor_user=self.owner)
        self.assertTrue(self.doc.events.filter(event=SignatureEvent.Event.REMINDED).exists())
        self.signer.refresh_from_db()
        with self.assertRaises(ValueError):  # the reminder restarts the window
            remind_signer(self.signer, actor_user=self.owner)

    def test_detail_shows_when_the_next_reminder_is_allowed(self):
        response = self.client.get(reverse("esign:document_detail", args=[self.doc.pk]))
        self.assertContains(response, "Remind again after")
        response = self.client.post(reverse("esign:signer_remind", args=[self.doc.pk, self.signer.pk]), follow=True)
        self.assertContains(response, "less than 6 hours ago")


class ContributorTests(EsignTestCase):
    def setUp(self):
        super().setUp()
        self.doc = self.make_doc()
        self.signer = self.add_signer(self.doc, self.alice)
        self.doc.contributors.add(self.bob)

    def test_contributor_can_view_read_only_others_cannot(self):
        self.client.force_login(self.bob)
        response = self.client.get(reverse("esign:document_detail", args=[self.doc.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contributor · view only")
        self.assertNotContains(response, reverse("esign:document_cancel", args=[self.doc.pk]))
        self.client.force_login(make_user("carol"))
        self.assertEqual(self.client.get(reverse("esign:document_detail", args=[self.doc.pk])).status_code, 403)

    def test_contributor_cannot_change_the_document(self):
        self.client.force_login(self.bob)
        self.client.post(reverse("esign:document_cancel", args=[self.doc.pk]))
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.status, SignatureDocument.Status.DRAFT)

    def test_contributors_are_emailed_on_updates_but_not_the_actor(self):
        mail.outbox.clear()
        send_document(self.doc, actor_user=self.owner)
        updates = [m for m in mail.outbox if self.bob.email in m.to]
        self.assertEqual(len(updates), 1)
        self.assertIn("Sent for Signing", updates[0].subject)
        mail.outbox.clear()
        self.doc.contributors.add(self.owner)  # even if listed, the actor isn't emailed about their own action
        from esign.services import cancel_document
        cancel_document(self.doc, actor_user=self.owner)
        self.assertEqual([m.to for m in mail.outbox], [[self.bob.email]])

    def test_list_includes_shared_documents(self):
        self.client.force_login(self.bob)
        license_record = mock.Mock(**{"get_effective_status.return_value": "active"})
        with mock.patch("licensing.decorators.LicenseRecord.get_current", return_value=license_record),                 mock.patch("licensing.decorators.has_feature_access", return_value=True):
            response = self.client.get(reverse("esign:document_list"))
        self.assertContains(response, self.doc.document_code)
        self.assertContains(response, "Contributor")

    def test_owner_adds_and_removes_contributors_from_detail(self):
        url = reverse("esign:document_contributors", args=[self.doc.pk])
        self.client.post(url, {"action": "add", "user_id": self.alice.pk})
        self.assertIn(self.alice, self.doc.contributors.all())
        self.client.post(url, {"action": "remove", "user_id": self.bob.pk})
        self.assertNotIn(self.bob, self.doc.contributors.all())
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(url, {"action": "add", "user_id": self.bob.pk}).status_code, 403)


class SignerReopenGuardTests(EsignTestCase):
    def test_reopen_requires_post(self):
        doc = self.make_doc()
        signer = self.add_signer(doc, self.alice, status=Signer.Status.SIGNED)
        response = self.client.get(reverse("esign:signer_reopen", args=[doc.pk, signer.pk]))
        self.assertEqual(response.status_code, 405)
        signer.refresh_from_db()
        self.assertEqual(signer.status, Signer.Status.SIGNED)


class JobRoleDefaultTests(EsignTestCase):
    def test_job_role_comes_from_linked_employee_but_stays_editable(self):
        set_job_role(self.alice, "Finance Manager")
        doc = self.make_doc()
        signer = self.add_signer(doc, self.alice)
        self.assertEqual(default_job_role(signer), "Finance Manager")
        signer.stamp_job_role_text = "Acting CFO"
        self.assertEqual(default_job_role(signer), "Acting CFO")

    def test_sign_page_prefills_job_role(self):
        set_job_role(self.alice, "Finance Manager")
        doc = self.make_doc()
        signer = self.add_signer(doc, self.alice)
        send_document(doc, actor_user=self.owner)
        signer.refresh_from_db()
        # Fresh instance: logging in saves the user, and a core signal re-saves its cached Employee.
        self.client.force_login(User.objects.get(pk=self.alice.pk))
        response = self.client.get(reverse("esign_public:sign", args=[signer.token]))
        self.assertContains(response, 'id="esign-default-job-role"')
        self.assertContains(response, "Finance Manager")
        self.assertContains(response, 'list="esign-job-roles"')


class ViewerZoomTests(EsignTestCase):
    def test_placement_pages_have_zoom_and_no_shrinking_canvas(self):
        doc = self.make_doc()
        self.add_signer(doc, self.alice)
        html = self.client.get(reverse("esign:document_configure", args=[doc.pk])).content.decode()
        send_document(doc, actor_user=self.owner)
        adjust = self.client.get(reverse("esign:document_adjust_placements", args=[doc.pk])).content.decode()
        for page in (html, adjust):
            self.assertIn('title="Zoom in"', page)
            self.assertIn('title="Reset zoom"', page)
            self.assertIn('id="pdf-stage"', page)
            self.assertNotIn("#pdf-canvas { display: block; max-width: 100%", page)
