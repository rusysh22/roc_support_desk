"""Knowledge Management: shared editing, published = read-only until unpublished, change log,
approvers notified by e-mail + Teams on submit, and the article in the approver's My Approvals."""
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from cases.models import CaseCategory
from core.models import User
from knowledge_base.models import Article, ArticleChangeLog

Role = User.RoleAccess


def make_user(name, role):
    return User.objects.create_user(username=name, login_username=name, email=f"{name}@example.com",
                                    role_access=role, is_staff=True)


@patch("licensing.models.LicenseRecord.get_effective_status", return_value="active")
@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"], CELERY_TASK_ALWAYS_EAGER=True)
class SharingAndLogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = make_user("author", Role.SUPPORTDESK)
        cls.colleague = make_user("colleague", Role.SUPPORTDESK)
        cls.manager = make_user("manager", Role.MANAGER)
        cls.category = CaseCategory.objects.create(name="Network", slug="network", prefix_code="NW")

    def article(self, **extra):
        return Article.objects.create(title="VPN guide", category=self.category, problem_summary="p", root_cause="r",
                                      solution="s", created_by=self.author, **extra)

    def payload(self, **extra):
        data = {"title": "VPN guide v2", "article_type": Article.ArticleType.ISSUE, "category": self.category.pk,
                "problem_summary": "Problem", "root_cause": "", "solution": "", "tags_input": ""}
        data.update(extra)
        return data

    def test_any_knowledge_user_can_edit_someone_elses_draft_and_it_is_logged(self, _):
        article = self.article()
        self.client.force_login(self.colleague)
        response = self.client.post(reverse("kb_desk:article_edit", args=[article.pk]), self.payload())
        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.title, "VPN guide v2")
        entry = article.change_logs.get()
        self.assertEqual((entry.action, entry.actor), (ArticleChangeLog.Action.EDITED, self.colleague))
        self.assertIn("Title", entry.detail)

    def test_published_is_read_only_for_everyone_until_unpublished(self, _):
        article = self.article(status=Article.Status.PUBLISHED)
        for user in (self.colleague, self.manager):
            self.client.force_login(user)
            self.client.post(reverse("kb_desk:article_edit", args=[article.pk]), self.payload())
            article.refresh_from_db()
            self.assertEqual(article.title, "VPN guide")
        page = self.client.get(reverse("kb_desk:article_edit", args=[article.pk]))
        self.assertContains(page, "Published — read-only")
        self.assertContains(page, "<fieldset disabled")
        self.assertNotContains(page, "Save Changes")
        # a support agent cannot unpublish; a manager can, with a reason in the log
        self.client.force_login(self.colleague)
        self.assertEqual(self.client.post(reverse("kb_desk:article_unpublish", args=[article.pk])).status_code, 403)
        self.client.force_login(self.manager)
        self.client.post(reverse("kb_desk:article_unpublish", args=[article.pk]), {"reason": "Outdated steps"})
        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.DRAFT)
        self.assertEqual(article.change_logs.get(action=ArticleChangeLog.Action.UNPUBLISHED).detail, "Outdated steps")
        self.client.post(reverse("kb_desk:article_edit", args=[article.pk]), self.payload())
        article.refresh_from_db()
        self.assertEqual(article.title, "VPN guide v2")

    @patch("gateways.teams_notify.allowed_teams_email", return_value=True)
    @patch("gateways.teams_notify.personal_enabled", return_value=True)
    @patch("gateways.teams_notify.post_teams_payload_task.delay")
    def test_submit_notifies_approvers_with_a_link_to_the_approval(self, teams, *_):
        article = self.article()
        self.client.force_login(self.author)
        self.client.post(reverse("kb_desk:article_submit", args=[article.pk]))
        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.PENDING)
        link = reverse("kb_desk:article_edit", args=[article.pk])
        sent = [m for m in mail.outbox if "Approval needed" in m.subject]
        self.assertEqual([m.to for m in sent], [["manager@example.com"]])
        self.assertIn(link, sent[0].alternatives[0][0])
        card = teams.call_args.args[0]
        self.assertEqual(card["recipient"], "manager@example.com")
        self.assertTrue(card["attachments"][0]["content"]["actions"][0]["url"].endswith(link))
        self.assertTrue(article.change_logs.filter(action=ArticleChangeLog.Action.SUBMITTED).exists())

    def test_pending_article_shows_in_the_managers_my_approvals_and_sidebar_count(self, _):
        article = self.article(status=Article.Status.PENDING)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("chat:my_approvals"))
        self.assertContains(page, "VPN guide")
        self.assertContains(page, reverse("kb_desk:article_edit", args=[article.pk]))
        self.assertEqual(page.context["my_approvals_pending_count"], 1)
        self.client.force_login(self.colleague)  # not an approver
        self.assertNotContains(self.client.get(reverse("chat:my_approvals")), "VPN guide")
