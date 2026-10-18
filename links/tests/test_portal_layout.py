"""Redirect portal: full page / mini page, redirect-after limited to 10 minutes."""
from django.test import TestCase

from links.forms import ShortLinkForm
from links.models import ShortLink

YT = "dQw4w9WgXcQ"


def data(**extra):
    base = {"target_url": "https://example.com/", "slug": "demo", "title": "Demo", "description": "",
            "portal_enabled": "on", "portal_countdown": "10", "portal_layout": "full", "portal_message": ""}
    base.update(extra)
    return base


class CountdownLimitTests(TestCase):
    def test_up_to_ten_minutes_is_accepted(self):
        for seconds in ("0", "45", "60", "600"):
            with self.subTest(seconds=seconds):
                form = ShortLinkForm(data(portal_countdown=seconds, slug=f"s{seconds}"))
                self.assertTrue(form.is_valid(), form.errors)

    def test_more_than_ten_minutes_is_refused(self):
        form = ShortLinkForm(data(portal_countdown="601"))
        self.assertFalse(form.is_valid())
        self.assertIn("at most 10 minutes", form.errors["portal_countdown"][0])

    def test_short_label(self):
        label = lambda s: ShortLink(portal_countdown=s).portal_countdown_label  # noqa: E731
        self.assertEqual([label(45), label(60), label(150), label(600)], ["45s", "1m", "2m 30s", "10m"])


class LayoutTests(TestCase):
    def link(self, layout, **extra):
        return ShortLink.objects.create(target_url="https://example.com/", slug=f"l-{layout}-{len(extra)}", portal_enabled=True,
                                        portal_countdown=90, portal_layout=layout, **extra)

    def test_layout_is_a_choice_and_defaults_to_full(self):
        self.assertEqual(ShortLink().portal_layout, "full")
        self.assertFalse(ShortLinkForm(data(portal_layout="huge")).is_valid())

    def test_full_page_with_media_gets_the_two_column_layout(self):
        link = self.link("full", portal_video_url=f"https://www.youtube.com/watch?v={YT}")
        html = self.client.get(f"/s/{link.slug}/").content.decode()
        self.assertIn('class="portal portal--full"', html)
        self.assertIn("function clock(n)", html)  # 90 s is shown as 1:30

    def test_mini_page_and_links_without_media_stay_a_small_card(self):
        mini = self.link("mini", portal_video_url=f"https://www.youtube.com/watch?v={YT}")
        self.assertIn('<main class="portal">', self.client.get(f"/s/{mini.slug}/").content.decode())
        bare = self.link("full")
        self.assertIn('<main class="portal">', self.client.get(f"/s/{bare.slug}/").content.decode())
