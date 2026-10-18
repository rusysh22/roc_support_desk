from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, CompanyUnit, Employee, SiteConfig, User
from knowledge_base.models import Article

from announcements.models import Announcement, AnnouncementReceipt
from announcements.services import notify_by_email, post_announcement, visible_announcements_for

WITHOUT_LICENSE_GATE = override_settings(
    MIDDLEWARE=[m for m in settings.MIDDLEWARE if not m.startswith("licensing.")],
)


def make_user(login, modules=None, role=User.RoleAccess.PORTALUSER):
    return User.objects.create_user(
        username=login.title(), email=f"{login}@example.com", login_username=login,
        initials=login[:2].upper(), password="pass-1234", role_access=role, module_access=modules,
    )


@WITHOUT_LICENSE_GATE
class AnnouncementTestCase(TestCase):
    def setUp(self):
        self.alpha = CompanyUnit.objects.create(code="ALPHA", name="ALPHA")
        self.beta = CompanyUnit.objects.create(code="BETA", name="BETA")
        self.alpha_user = make_user("alphauser")
        self.beta_user = make_user("betauser")
        Employee.objects.filter(user=self.alpha_user).update(unit=self.alpha)
        Employee.objects.filter(user=self.beta_user).update(unit=self.beta)
        self.manager = make_user("comms", ["announcement_management"], role=User.RoleAccess.MANAGER)

    def post(self, **kwargs):
        defaults = {"title": "Hello", "visibility": Announcement.Visibility.AUTHENTICATED}
        defaults.update(kwargs)
        return post_announcement(**defaults)


class VisibilityTests(AnnouncementTestCase):
    def test_guest_sees_public_only(self):
        from django.contrib.auth.models import AnonymousUser
        public = self.post(title="Public", visibility=Announcement.Visibility.PUBLIC)
        self.post(title="Members")
        self.assertEqual(list(visible_announcements_for(AnonymousUser())), [public])

    def test_targeted_by_employee_unit(self):
        targeted = self.post(title="ALPHA only", visibility=Announcement.Visibility.TARGETED, target_entities=[self.alpha])
        self.assertIn(targeted, visible_announcements_for(self.alpha_user))
        self.assertNotIn(targeted, visible_announcements_for(self.beta_user))

    def test_draft_scheduled_and_expired_are_hidden(self):
        now = timezone.now()
        live = self.post(title="Live")
        Announcement.objects.create(title="Draft", status=Announcement.Status.DRAFT, visibility="AUTHENTICATED")
        Announcement.objects.create(
            title="Later", status=Announcement.Status.PUBLISHED, visibility="AUTHENTICATED",
            publish_at=now + timedelta(days=1),
        )
        Announcement.objects.create(
            title="Gone", status=Announcement.Status.PUBLISHED, visibility="AUTHENTICATED",
            publish_at=now - timedelta(days=2), expires_at=now - timedelta(days=1),
        )
        self.assertEqual(list(visible_announcements_for(self.alpha_user)), [live])


class PortalTests(AnnouncementTestCase):
    def test_ticket_portal_shows_section_only_when_there_is_something(self):
        self.client.force_login(self.alpha_user)
        self.assertNotContains(self.client.get(reverse("cases:dashboard")), 'id="announcements-section"')
        self.post(title="Office closed Friday")
        response = self.client.get(reverse("cases:dashboard"))
        self.assertContains(response, 'id="announcements-section"')
        self.assertContains(response, "Office closed Friday")

    def test_detail_marks_read_and_acknowledge(self):
        announcement = self.post(title="Read me", require_acknowledgement=True)
        self.client.force_login(self.alpha_user)
        self.assertContains(self.client.get(reverse("cases:dashboard")), "New")
        self.client.get(reverse("announcements:detail", args=[announcement.pk]))
        self.assertTrue(AnnouncementReceipt.objects.filter(announcement=announcement, user=self.alpha_user).exists())
        self.client.post(reverse("announcements:acknowledge", args=[announcement.pk]))
        receipt = AnnouncementReceipt.objects.get(announcement=announcement, user=self.alpha_user)
        self.assertIsNotNone(receipt.acknowledged_at)

    def test_hidden_announcement_is_404(self):
        announcement = self.post(title="BETA", visibility=Announcement.Visibility.TARGETED, target_entities=[self.beta])
        self.client.force_login(self.alpha_user)
        self.assertEqual(self.client.get(reverse("announcements:detail", args=[announcement.pk])).status_code, 404)

    def test_guest_redirected_when_login_required(self):
        config = SiteConfig.get_solo()
        config.require_public_login = True
        config.save()
        self.assertEqual(self.client.get(reverse("announcements:list")).status_code, 302)

    def test_list_renders(self):
        self.post(title="One")
        self.client.force_login(self.alpha_user)
        self.assertContains(self.client.get(reverse("announcements:list")), "One")


class ManagementTests(AnnouncementTestCase):
    def test_requires_module(self):
        self.client.force_login(self.alpha_user)
        self.assertEqual(self.client.get(reverse("announcements_desk:list")).status_code, 403)

    def test_create_publish_sanitizes_and_audits(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("announcements_desk:create")).status_code, 200)
        response = self.client.post(reverse("announcements_desk:create"), {
            "title": "Maintenance", "summary": "Saturday 22:00",
            "body": "<p>Hi</p><script>alert(1)</script>", "visibility": "AUTHENTICATED",
            "priority": "NORMAL", "status": "PUBLISHED", "publish_at": timezone.now().strftime("%Y-%m-%dT%H:%M"),
        })
        self.assertRedirects(response, reverse("announcements_desk:list"))
        announcement = Announcement.objects.get(title="Maintenance")
        self.assertNotIn("<script>", announcement.body)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.Action.ANNOUNCEMENT_PUBLISH).exists())
        self.assertEqual(self.client.get(reverse("announcements_desk:list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("announcements_desk:edit", args=[announcement.pk])).status_code, 200)

    def test_targeted_needs_entities_and_external_link_rejected(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("announcements_desk:create"), {
            "title": "X", "visibility": "TARGETED", "priority": "NORMAL", "status": "PUBLISHED",
            "publish_at": timezone.now().strftime("%Y-%m-%dT%H:%M"), "link_url": "https://evil.example.com",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("target_entities", response.context["form"].errors)
        self.assertIn("link_url", response.context["form"].errors)


class EmailTests(AnnouncementTestCase):
    def test_email_sent_once_to_audience(self):
        announcement = self.post(title="Mail", visibility=Announcement.Visibility.TARGETED, target_entities=[self.alpha])
        with mock.patch("announcements.tasks.send_announcement_email_task.delay") as delay:
            self.assertEqual(notify_by_email(announcement), 1)
            self.assertEqual(notify_by_email(announcement), 0)
        delay.assert_called_once_with(str(announcement.pk), str(self.alpha_user.pk))


class SourceIntegrationTests(AnnouncementTestCase):
    def setUp(self):
        super().setUp()
        from cases.models import CaseCategory
        self.category = CaseCategory.objects.create(name="General", slug="general")

    def test_kb_announcement_article_posts_once(self):
        article = Article.objects.create(
            title="New VPN", category=self.category, article_type=Article.ArticleType.ANNOUNCEMENT,
            problem_summary="<p>Use the new VPN</p>",
        )
        self.assertFalse(Announcement.objects.exists())
        article.status = Article.Status.PUBLISHED
        article.save()
        article.save()
        announcement = Announcement.objects.get()
        self.assertEqual(announcement.source_type, Announcement.SourceType.KNOWLEDGE_BASE)
        self.assertEqual(announcement.visibility, Announcement.Visibility.PUBLIC)
        self.assertEqual(announcement.summary, "Use the new VPN")

    def test_regular_kb_article_does_not_post(self):
        Article.objects.create(title="How to reset", category=self.category, status=Article.Status.PUBLISHED)
        self.assertFalse(Announcement.objects.exists())
