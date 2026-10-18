"""Knowledge Base: deleting comments, comments off by default, several contributors per article."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from cases.models import CaseCategory, CaseRecord
from core.models import CompanyUnit, Employee, User
from knowledge_base.models import Article, ArticleComment

Role = User.RoleAccess


def make_user(name, role=Role.PORTALUSER, **extra):
    return User.objects.create_user(
        username=name, login_username=name, email=f"{name}@example.com", role_access=role,
        is_staff=role != Role.PORTALUSER, **extra,
    )


@patch("licensing.models.LicenseRecord.get_effective_status", return_value="active")
@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class CommentsAndContributorsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = make_user("boss", Role.SUPERADMIN, is_superuser=True)
        cls.agent = make_user("agent", Role.SUPPORTDESK)
        cls.ann = make_user("ann")
        cls.bob = make_user("bob")
        cls.category = CaseCategory.objects.create(name="Network", slug="network", prefix_code="NW")

        def article(title, **extra):
            return Article.objects.create(
                title=title, category=cls.category, status=Article.Status.PUBLISHED,
                problem_summary="Wi-Fi drops", root_cause="Channel clash", solution="Switch channel", **extra,
            )
        cls.open_article = article("Comments on", allow_comments=True)
        cls.closed_article = article("Comments off")
        cls.other_article = article("Another", allow_comments=True)

    def comment(self, user, article=None, body="hello"):
        return ArticleComment.objects.create(article=article or self.open_article, user=user, body=body)

    def delete(self, user, comment, article=None):
        self.client.logout()
        if user:
            self.client.force_login(user)
        slug = (article or comment.article).slug
        return self.client.post(reverse("knowledge_base:article_comment_delete", args=[slug, comment.pk]))

    # ---- 1. deleting comments
    def test_everyone_can_delete_their_own_comment(self, _license):
        for user in (self.ann, self.agent, self.admin):
            comment = self.comment(user)
            with self.subTest(user=user.username):
                response = self.delete(user, comment)
                self.assertRedirects(response, reverse("knowledge_base:article_detail", args=[self.open_article.slug]))
                self.assertFalse(ArticleComment.objects.filter(pk=comment.pk).exists())

    def test_only_a_superadmin_can_delete_other_peoples_comments(self, _license):
        by_portal, by_agent = self.comment(self.ann), self.comment(self.agent)
        # another portal user and a support desk agent cannot remove a comment that is not theirs
        self.assertEqual(self.delete(self.bob, by_portal).status_code, 403)
        self.assertEqual(self.delete(self.agent, by_portal).status_code, 403)
        self.assertEqual(self.delete(self.bob, by_agent).status_code, 403)
        self.assertEqual(ArticleComment.objects.count(), 2)
        self.assertEqual(self.delete(self.admin, by_portal).status_code, 302)
        self.assertEqual(self.delete(self.admin, by_agent).status_code, 302)
        self.assertEqual(ArticleComment.objects.count(), 0)

    def test_deleting_needs_login_a_post_and_the_right_article(self, _license):
        comment = self.comment(self.ann)
        response = self.delete(None, comment)
        self.assertIn(reverse("login"), response["Location"])
        self.assertEqual(self.delete(self.admin, comment, article=self.other_article).status_code, 404)  # wrong article
        self.client.force_login(self.admin)
        url = reverse("knowledge_base:article_comment_delete", args=[comment.article.slug, comment.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertTrue(ArticleComment.objects.filter(pk=comment.pk).exists())

    def test_the_delete_button_shows_only_where_allowed(self, _license):
        mine, theirs = self.comment(self.ann, body="mine"), self.comment(self.bob, body="theirs")
        mine_url = reverse("knowledge_base:article_comment_delete", args=[self.open_article.slug, mine.pk])
        theirs_url = reverse("knowledge_base:article_comment_delete", args=[self.open_article.slug, theirs.pk])
        page = reverse("knowledge_base:article_detail", args=[self.open_article.slug])
        self.client.force_login(self.ann)
        html = self.client.get(page).content.decode()
        self.assertIn(mine_url, html)
        self.assertNotIn(theirs_url, html)
        self.client.force_login(self.admin)
        html = self.client.get(page).content.decode()
        self.assertIn(mine_url, html)
        self.assertIn(theirs_url, html)
        self.client.logout()
        html = self.client.get(page).content.decode()
        self.assertNotIn(mine_url, html)

    # ---- 2. comments are a per-article setting, off by default
    def test_comments_are_off_unless_switched_on(self, _license):
        fresh = Article.objects.create(title="Fresh", category=self.category, problem_summary="x", root_cause="x", solution="x")
        self.assertFalse(fresh.allow_comments)

    def test_comment_section_and_posting_follow_the_setting(self, _license):
        self.client.force_login(self.ann)
        on = self.client.get(reverse("knowledge_base:article_detail", args=[self.open_article.slug])).content.decode()
        off = self.client.get(reverse("knowledge_base:article_detail", args=[self.closed_article.slug])).content.decode()
        self.assertIn("Write a comment", on)
        self.assertNotIn("Write a comment", off)

        post = lambda article: self.client.post(reverse("knowledge_base:article_comment", args=[article.slug]), {"body": "hi"})
        post(self.closed_article)
        self.assertEqual(ArticleComment.objects.filter(article=self.closed_article).count(), 0)  # refused, not just hidden
        post(self.open_article)
        self.assertEqual(ArticleComment.objects.filter(article=self.open_article).count(), 1)

    def test_the_desk_form_starts_with_comments_off(self, _license):
        self.client.force_login(self.admin)
        form = self.client.get(reverse("kb_desk:article_create")).context["form"]
        self.assertFalse(form["allow_comments"].value())

    # ---- 3. contributors
    def create_payload(self, **extra):
        data = {"title": "New article", "article_type": Article.ArticleType.ISSUE, "category": self.category.pk,
                "problem_summary": "Problem", "root_cause": "", "solution": "", "tags_input": ""}
        data.update(extra)
        return data

    def test_an_article_can_have_several_contributors(self, _license):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("kb_desk:article_create"),
                                    self.create_payload(contributors=[self.ann.pk, self.bob.pk, self.agent.pk]))
        self.assertEqual(response.status_code, 302)
        article = Article.objects.get(title="New article")
        self.assertEqual({u.pk for u in article.contributors.all()}, {self.ann.pk, self.bob.pk, self.agent.pk})
        # editing replaces the list
        self.client.post(reverse("kb_desk:article_edit", args=[article.pk]), self.create_payload(contributors=[self.bob.pk]))
        self.assertEqual({u.pk for u in article.contributors.all()}, {self.bob.pk})

    def test_inactive_accounts_cannot_be_picked(self, _license):
        gone = make_user("gone")
        User.objects.filter(pk=gone.pk).update(is_active=False)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("kb_desk:article_create"), self.create_payload(contributors=[gone.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("contributors", response.context["form"].errors)

    def ticket(self, employee=None, email=""):
        return CaseRecord.objects.create(
            subject="VPN down", problem_description="Cannot connect", category=self.category,
            requester=employee, requester_email=email or (employee.email if employee else "") or "",
        )

    def employee_without_account(self, email):
        return Employee.objects.create(full_name="Req Uester", email=email, unit=CompanyUnit.objects.create(code="HQ", name="Head Office"))

    def preselected(self, case):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("kb_desk:create_from_case", args=[case.pk]))
        self.assertEqual(response.status_code, 200)
        return response, list(response.context["form"].initial["contributors"])

    def test_from_a_ticket_the_requester_is_the_default_contributor(self, _license):
        # every user has an employee profile (created with the account); the ticket's requester is ann's
        case = self.ticket(employee=self.ann.employee_profile)
        response, picked = self.preselected(case)
        self.assertEqual(picked, [self.ann.pk])
        self.assertFalse(response.context["requester_has_no_account"])

    def test_requester_without_a_link_is_found_by_e_mail(self, _license):
        case = self.ticket(email="BOB@example.com")  # a web-form requester: same address as bob, different case
        _, picked = self.preselected(case)
        self.assertEqual(picked, [self.bob.pk])

    def test_requester_without_an_account_preselects_nobody_and_says_so(self, _license):
        case = self.ticket(employee=self.employee_without_account("stranger@elsewhere.test"))
        response, picked = self.preselected(case)
        self.assertEqual(picked, [])
        self.assertTrue(response.context["requester_has_no_account"])
        self.assertContains(response, "has no user account")

    def test_saving_from_a_ticket_keeps_the_contributors_chosen(self, _license):
        case = self.ticket(employee=self.ann.employee_profile)
        self.client.force_login(self.admin)
        self.client.post(reverse("kb_desk:create_from_case", args=[case.pk]),
                         self.create_payload(source_case=case.pk, contributors=[self.ann.pk, self.agent.pk]))
        article = Article.objects.get(source_case=case)
        self.assertEqual({u.pk for u in article.contributors.all()}, {self.ann.pk, self.agent.pk})

    def test_contributors_are_credited_on_the_article_page(self, _license):
        self.open_article.contributors.set([self.ann, self.agent])
        html = self.client.get(reverse("knowledge_base:article_detail", args=[self.open_article.slug])).content.decode()
        self.assertIn("Contributors:", html)
        self.assertIn("ann", html)
        self.assertIn("agent", html)
        bare = self.client.get(reverse("knowledge_base:article_detail", args=[self.closed_article.slug])).content.decode()
        self.assertNotIn("Contributors:", bare)
