"""Knowledge Base: views and likes are counted once per user account and shown on the pages."""
import re
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from cases.models import CaseCategory
from core.models import User
from knowledge_base.models import Article, ArticleLike, ArticleView


def make_user(name):
    return User.objects.create_user(username=name, login_username=name, email=f"{name}@example.com")


@patch("licensing.models.LicenseRecord.get_effective_status", return_value="active")
@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class LikesAndViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ann, cls.bob, cls.cy = make_user("ann"), make_user("bob"), make_user("cy")
        cls.category = CaseCategory.objects.create(name="Network", slug="network", prefix_code="NW")
        cls.article = Article.objects.create(
            title="Wi-Fi keeps dropping", category=cls.category, status=Article.Status.PUBLISHED,
            problem_summary="Wi-Fi drops every few minutes", root_cause="Channel clash", solution="Switch channel",
        )
        cls.draft = Article.objects.create(
            title="Not published yet", category=cls.category, status=Article.Status.DRAFT,
            problem_summary="draft", root_cause="x", solution="y",
        )

    def open(self, user=None, article=None):
        self.client.logout()
        if user:
            self.client.force_login(user)
        return self.client.get(reverse("knowledge_base:article_detail", args=[(article or self.article).slug]))

    def like(self, user, article=None, ajax=True):
        self.client.logout()
        self.client.force_login(user)
        extra = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"} if ajax else {}
        return self.client.post(reverse("knowledge_base:article_like", args=[(article or self.article).slug]), **extra)

    # ---- views
    def test_a_user_account_counts_once_however_often_it_opens_the_article(self, _license):
        for _ in range(3):
            self.open(self.ann)
        self.assertEqual(self.article.views.count(), 1)
        self.open(self.bob)
        response = self.open(self.bob)
        self.assertEqual(self.article.views.count(), 2)
        self.assertEqual(response.context["views_total"], 2)

    def test_visitors_who_are_not_signed_in_are_not_counted(self, _license):
        response = self.open()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ArticleView.objects.count(), 0)
        self.assertEqual(response.context["views_total"], 0)

    def test_unpublished_articles_are_not_reachable_and_not_counted(self, _license):
        self.assertEqual(self.open(self.ann, self.draft).status_code, 404)
        self.assertEqual(ArticleView.objects.count(), 0)

    # ---- likes
    def test_like_is_a_toggle_and_each_account_counts_once(self, _license):
        first = self.like(self.ann).json()
        self.assertEqual(first, {"liked": True, "likes": 1})
        self.assertEqual(self.like(self.bob).json(), {"liked": True, "likes": 2})
        self.assertEqual(self.like(self.ann).json(), {"liked": False, "likes": 1})   # clicking again undoes it
        self.assertEqual(self.like(self.ann).json(), {"liked": True, "likes": 2})
        self.assertEqual(ArticleLike.objects.filter(article=self.article).count(), 2)

    def test_the_database_refuses_a_second_like_or_view_from_the_same_account(self, _license):
        ArticleLike.objects.create(article=self.article, user=self.ann)
        ArticleView.objects.create(article=self.article, user=self.ann)
        for model in (ArticleLike, ArticleView):
            with self.subTest(model=model.__name__), self.assertRaises(IntegrityError), transaction.atomic():
                model.objects.create(article=self.article, user=self.ann)

    def test_liking_needs_an_account_and_a_published_article(self, _license):
        url = reverse("knowledge_base:article_like", args=[self.article.slug])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])
        self.assertEqual(ArticleLike.objects.count(), 0)
        self.assertEqual(self.like(self.ann, self.draft).status_code, 404)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_a_plain_form_post_goes_back_to_the_article(self, _license):
        response = self.like(self.ann, ajax=False)
        self.assertRedirects(response, reverse("knowledge_base:article_detail", args=[self.article.slug]))
        self.assertEqual(ArticleLike.objects.count(), 1)

    # ---- what the pages show
    def test_article_page_shows_both_counts_and_whether_you_liked_it(self, _license):
        self.open(self.ann)
        self.open(self.bob)
        self.like(self.ann)
        page = self.open(self.ann)
        self.assertEqual((page.context["views_total"], page.context["likes_total"], page.context["liked"]), (2, 1, True))
        html = page.content.decode()
        self.assertIn('aria-label="2 views, 1 likes"', html)
        self.assertIn('id="kb-like-btn"', html)
        self.assertIn("Liked", html)
        self.assertFalse(self.open(self.cy).context["liked"])

    def test_signed_out_visitors_see_the_count_and_a_sign_in_link(self, _license):
        self.like(self.ann)
        html = self.open().content.decode()
        self.assertIn('aria-label="0 views, 1 likes"', html)
        self.assertNotIn('id="kb-like-btn"', html)
        self.assertIn("Sign in to like this article", html)

    def test_lists_show_the_counts_without_multiplying_them(self, _license):
        # 2 viewers and 3 likers: a careless join would report 6 for both.
        for user in (self.ann, self.bob):
            self.open(user)
        for user in (self.ann, self.bob, self.cy):
            self.like(user)
        label = 'aria-label="2 views, 3 likes"'
        self.assertEqual(self.client.get(reverse("knowledge_base:category", args=[self.category.slug])).content.decode().count(label), 1)
        self.assertEqual(self.client.get(reverse("knowledge_base:search"), {"q": "Wi-Fi"}).content.decode().count(label), 1)
        home = self.client.get(reverse("knowledge_base:home")).content.decode()
        self.assertGreaterEqual(home.count(label), 1)
        self.assertNotRegex(home, r'aria-label="6 views')
        # an article nobody opened still shows zeros
        Article.objects.create(title="Fresh", category=self.category, status=Article.Status.PUBLISHED,
                                       problem_summary="fresh wi-fi", root_cause="x", solution="y")
        html = self.client.get(reverse("knowledge_base:category", args=[self.category.slug])).content.decode()
        self.assertIn('aria-label="0 views, 0 likes"', html)
        self.assertEqual(len(re.findall(r'aria-label="\d+ views, \d+ likes"', html)), 2)
