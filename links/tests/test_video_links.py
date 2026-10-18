"""Redirect portal: YouTube / TikTok video links and the sound option."""
import io
import shutil
import tempfile
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from links.forms import ShortLinkForm
from links.media import (
    VideoLinkError, embed_for, normalize_video_link, parse_video_link, resolve_tiktok_short,
)
from links.models import ShortLink

YT = "dQw4w9WgXcQ"
TT_ID = "7234567890123456789"


class ParseTests(TestCase):
    def test_youtube_forms_become_one_canonical_link(self):
        for url in (
            f"https://www.youtube.com/watch?v={YT}",
            f"https://youtube.com/watch?v={YT}&t=42s&list=PL1",
            f"https://m.youtube.com/watch?v={YT}",
            f"https://youtu.be/{YT}?si=abc",
            f"https://www.youtube.com/embed/{YT}",
            f"https://www.youtube.com/live/{YT}",
            f"http://music.youtube.com/watch?v={YT}",
        ):
            with self.subTest(url=url):
                info = parse_video_link(url)
                self.assertEqual((info["provider"], info["video_id"], info["portrait"]), ("youtube", YT, False))
                self.assertEqual(info["canonical"], f"https://www.youtube.com/watch?v={YT}")

    def test_youtube_shorts_are_portrait(self):
        info = parse_video_link(f"https://www.youtube.com/shorts/{YT}")
        self.assertEqual((info["video_id"], info["portrait"], info["canonical"]),
                         (YT, True, f"https://www.youtube.com/shorts/{YT}"))

    def test_tiktok_video_link(self):
        info = parse_video_link(f"https://www.tiktok.com/@some.user_1/video/{TT_ID}?is_from_webapp=1&sender_device=pc")
        self.assertEqual((info["provider"], info["video_id"], info["portrait"]), ("tiktok", TT_ID, True))
        self.assertEqual(info["canonical"], f"https://www.tiktok.com/@some.user_1/video/{TT_ID}")

    def test_tiktok_share_links_are_recognised_but_not_embeddable_yet(self):
        for url in ("https://vm.tiktok.com/ZMabc123/", "https://vt.tiktok.com/ZSabc123/", "https://www.tiktok.com/t/ZTabc123/"):
            with self.subTest(url=url):
                self.assertEqual(parse_video_link(url)["provider"], "tiktok-short")
        self.assertIsNone(embed_for("https://vm.tiktok.com/ZMabc123/"))

    def test_everything_else_is_refused(self):
        for url in (
            "", "not a link", "javascript:alert(1)", "ftp://youtube.com/watch?v=" + YT,
            "https://vimeo.com/123456789",
            f"https://youtube.com.evil.example/watch?v={YT}",           # look-alike host
            f"https://evil.example/?u=https://www.youtube.com/watch?v={YT}",
            f"https://www.youtube.com@evil.example/watch?v={YT}",       # userinfo trick
            f"https://tiktok.com.evil.example/@a/video/{TT_ID}",
            "https://www.youtube.com/watch?v=short",                    # not a video id
            "https://www.youtube.com/channel/UC123",
            "https://www.tiktok.com/@someone",                          # a profile, not a video
        ):
            with self.subTest(url=url), self.assertRaises(VideoLinkError):
                parse_video_link(url)


class ResolveTests(TestCase):
    @staticmethod
    def hops(*locations):
        """A requests.get stand-in answering with the given redirect chain, then a 200."""
        answers = [mock.Mock(status_code=302, headers={"Location": loc}) for loc in locations]
        answers.append(mock.Mock(status_code=200, headers={}))
        return mock.Mock(side_effect=answers)

    def test_share_link_resolves_to_the_video(self):
        opener = self.hops(f"https://www.tiktok.com/@a/video/{TT_ID}?x=1")
        info = resolve_tiktok_short("https://vm.tiktok.com/ZMabc123/", opener=opener)
        self.assertEqual((info["provider"], info["video_id"]), ("tiktok", TT_ID))
        self.assertFalse(opener.call_args.kwargs["allow_redirects"])

    def test_redirect_to_another_site_is_never_followed(self):
        opener = self.hops("https://evil.example/steal", f"https://www.tiktok.com/@a/video/{TT_ID}")
        with self.assertRaises(VideoLinkError):
            resolve_tiktok_short("https://vm.tiktok.com/ZMabc123/", opener=opener)
        self.assertEqual(opener.call_count, 1)  # the evil hop was not requested

    def test_unreachable_or_endless_redirects_ask_for_the_full_link(self):
        with self.assertRaisesMessage(VideoLinkError, "Paste the full video link"):
            resolve_tiktok_short("https://vm.tiktok.com/ZMabc123/", opener=mock.Mock(side_effect=OSError("down")))
        loop = self.hops(*["https://vm.tiktok.com/ZMloop1/"] * 6)
        with self.assertRaises(VideoLinkError):
            resolve_tiktok_short("https://vm.tiktok.com/ZMabc123/", opener=loop)

    def test_normalize_uses_the_resolver_only_for_share_links(self):
        resolver = mock.Mock(return_value={"provider": "tiktok", "video_id": TT_ID, "portrait": True,
                                           "canonical": f"https://www.tiktok.com/@a/video/{TT_ID}"})
        self.assertEqual(normalize_video_link(f"https://youtu.be/{YT}", resolver)["video_id"], YT)
        resolver.assert_not_called()
        self.assertEqual(normalize_video_link("https://vm.tiktok.com/ZMabc123/", resolver)["video_id"], TT_ID)


class EmbedTests(TestCase):
    def test_youtube_embed_is_rebuilt_from_the_id(self):
        embed = embed_for(f"https://youtu.be/{YT}?si=tracking", sound=True)
        self.assertEqual(embed["provider"], "youtube")
        self.assertTrue(embed["src"].startswith(f"https://www.youtube-nocookie.com/embed/{YT}?"))
        self.assertNotIn("mute=1", embed["src"])
        self.assertNotIn("tracking", embed["src"])
        self.assertIn("mute=1", embed_for(f"https://youtu.be/{YT}", sound=False)["src"])

    def test_tiktok_embed(self):
        embed = embed_for(f"https://www.tiktok.com/@a/video/{TT_ID}")
        self.assertEqual((embed["src"], embed["portrait"]), (f"https://www.tiktok.com/embed/v2/{TT_ID}", True))

    def test_unsupported_or_empty_gives_nothing(self):
        for value in ("", "https://example.com/video", "garbage"):
            self.assertIsNone(embed_for(value))


def png(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(out, "PNG")
    return out.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class FormAndPortalTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def data(self, **extra):
        data = {"target_url": "https://example.com/", "slug": "demo", "title": "Demo", "description": "",
                "portal_enabled": "on", "portal_countdown": "0", "portal_message": "", "portal_media_sound": "on"}
        data.update(extra)
        return data

    def save_form(self, instance=None, files=None, **extra):
        form = ShortLinkForm(self.data(**extra), files or {}, instance=instance)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_video_link_is_saved_in_canonical_form(self):
        link = self.save_form(portal_video_url=f"https://youtu.be/{YT}?si=x")
        self.assertEqual(link.portal_video_url, f"https://www.youtube.com/watch?v={YT}")
        self.assertTrue(link.portal_media_sound)

    def test_bad_link_is_explained(self):
        form = ShortLinkForm(self.data(portal_video_url="https://vimeo.com/123"))
        self.assertFalse(form.is_valid())
        self.assertIn("Only YouTube and TikTok", form.errors["portal_video_url"][0])

    def test_tiktok_share_link_is_resolved_on_save(self):
        info = {"provider": "tiktok", "video_id": TT_ID, "portrait": True,
                "canonical": f"https://www.tiktok.com/@a/video/{TT_ID}"}
        with mock.patch("links.forms.normalize_video_link", return_value=info):
            link = self.save_form(portal_video_url="https://vm.tiktok.com/ZMabc123/")
        self.assertEqual(link.portal_video_url, info["canonical"])

    def test_link_and_new_file_together_is_refused(self):
        form = ShortLinkForm(self.data(portal_video_url=f"https://youtu.be/{YT}"),
                             {"portal_media_file": SimpleUploadedFile("a.png", png(), "image/png")})
        self.assertFalse(form.is_valid())
        self.assertIn("either an uploaded file or a video link", form.errors["portal_video_url"][0])

    def test_a_link_replaces_the_uploaded_file_and_a_file_replaces_the_link(self):
        link = self.save_form(files={"portal_media_file": SimpleUploadedFile("a.png", png(), "image/png")})
        self.assertTrue(link.portal_media)
        link = self.save_form(instance=link, portal_video_url=f"https://youtu.be/{YT}")
        self.assertFalse(link.portal_media)
        self.assertEqual(link.portal_media_type, "")
        link = self.save_form(instance=link, portal_video_url=link.portal_video_url,
                              files={"portal_media_file": SimpleUploadedFile("b.png", png("blue"), "image/png")})
        self.assertTrue(link.portal_media)
        self.assertEqual(link.portal_video_url, "")

    def test_sound_option_is_stored(self):
        data = self.data(portal_video_url=f"https://youtu.be/{YT}")
        del data["portal_media_sound"]
        form = ShortLinkForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(form.save().portal_media_sound)

    # ---- the public page
    def uploaded_video(self, sound):
        link = ShortLink(target_url="https://example.com/", slug=f"vid-{int(sound)}", portal_enabled=True,
                         portal_countdown=0, portal_media_type="video", portal_media_sound=sound)
        link.portal_media.save("v.mp4", ContentFile(b"\x00\x00\x00\x18ftypmp42"), save=False)
        link.save()
        return link

    def test_uploaded_video_with_sound_tries_sound_and_offers_a_button(self):
        html = self.client.get(f"/s/{self.uploaded_video(True).slug}/").content.decode()
        self.assertIn('id="portal-video"', html)
        self.assertIn('id="sound-btn"', html)
        self.assertNotRegex(html, r"<video[^>]*\bmuted\b")
        self.assertIn("Tap to turn on sound", html)

    def test_uploaded_video_muted_by_choice(self):
        html = self.client.get(f"/s/{self.uploaded_video(False).slug}/").content.decode()
        self.assertRegex(html, r"<video[^>]*\bmuted\b[^>]*\bautoplay\b")
        self.assertNotIn('id="sound-btn"', html)

    def test_portal_embeds_the_youtube_player(self):
        link = self.save_form(portal_video_url=f"https://youtu.be/{YT}")
        html = self.client.get(f"/s/{link.slug}/").content.decode()
        self.assertIn(f'src="https://www.youtube-nocookie.com/embed/{YT}?', html)
        self.assertNotIn("<video", html)

    def test_portal_embeds_the_tiktok_player_as_portrait(self):
        link = self.save_form(portal_video_url=f"https://www.tiktok.com/@a/video/{TT_ID}")
        html = self.client.get(f"/s/{link.slug}/").content.decode()
        self.assertIn(f'src="https://www.tiktok.com/embed/v2/{TT_ID}"', html)
        self.assertIn("portal__media--portrait", html)

    def test_a_tampered_stored_link_is_never_embedded(self):
        link = self.save_form()
        ShortLink.objects.filter(pk=link.pk).update(portal_video_url="https://evil.example/x")
        html = self.client.get(f"/s/{link.slug}/").content.decode()
        self.assertNotIn("<iframe", html)
        self.assertNotIn("evil.example/x", html.split("Destination")[0])
