"""Uploaded portal videos: a missing sound track is detected (and said so), audio survives compression,
and the frame is sized from the media's real shape."""
import os
import shutil
import subprocess
import tempfile

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from links.forms import ShortLinkForm
from links.media import has_audio_track, transcode_video, uploaded_video_has_audio
from links.models import ShortLink

FFMPEG = shutil.which("ffmpeg")


def make_video(path, with_audio):
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=1"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
    subprocess.run(cmd, check=True)


def form_data(**extra):
    data = {"target_url": "https://example.com/", "slug": "vid", "title": "", "description": "", "portal_enabled": "on",
            "portal_countdown": "0", "portal_layout": "full", "portal_message": "", "portal_media_sound": "on"}
    data.update(extra)
    return data


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class AudioTrackTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        if not FFMPEG:
            self.skipTest("ffmpeg is not installed")
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def video(self, with_audio):
        path = os.path.join(self.tmp, f"v{int(with_audio)}.mp4")
        make_video(path, with_audio)
        return path

    def test_detects_whether_there_is_a_sound_track(self):
        self.assertTrue(has_audio_track(self.video(True)))
        self.assertFalse(has_audio_track(self.video(False)))

    def test_compression_keeps_the_sound(self):
        out = os.path.join(self.tmp, "out.mp4")
        self.assertTrue(transcode_video(self.video(True), out))
        self.assertTrue(has_audio_track(out))

    def upload(self, with_audio):
        with open(self.video(with_audio), "rb") as fh:
            return SimpleUploadedFile("clip.mp4", fh.read(), content_type="video/mp4")

    def test_upload_records_whether_it_has_sound(self):
        for with_audio in (True, False):
            with self.subTest(with_audio=with_audio):
                form = ShortLinkForm(form_data(slug=f"vid{int(with_audio)}"), {"portal_media_file": self.upload(with_audio)})
                self.assertTrue(form.is_valid(), form.errors)
                link = form.save()
                self.assertIs(link.portal_media_has_audio, with_audio)

    def test_silent_video_has_no_dead_sound_button_and_autoplays_muted(self):
        link = ShortLink(target_url="https://example.com/", slug="quiet", portal_enabled=True, portal_countdown=0,
                         portal_media_type="video", portal_media_sound=True, portal_media_has_audio=False)
        link.portal_media.save("v.mp4", ContentFile(b"\x00\x00\x00\x18ftypmp42"), save=False)
        link.save()
        html = self.client.get("/s/quiet/").content.decode()
        self.assertNotIn('id="sound-btn"', html)
        self.assertRegex(html, r"<video[^>]*\bmuted\b[^>]*\bautoplay\b")

    def test_frame_is_sized_from_the_aspect_ratio_not_from_a_height_cap(self):
        link = ShortLink(target_url="https://example.com/", slug="shape", portal_enabled=True, portal_countdown=0,
                         portal_media_type="video", portal_media_sound=False)
        link.portal_media.save("v.mp4", ContentFile(b"\x00\x00\x00\x18ftypmp42"), save=False)
        link.save()
        html = self.client.get("/s/shape/").content.decode()
        self.assertIn("aspect-ratio: var(--ar-n)", html)
        self.assertIn("--ar-n", html)


class CountdownAndSoundButtonTests(TestCase):
    def link(self, has_audio):
        link = ShortLink(target_url="https://example.com/", slug=f"snd-{has_audio}", portal_enabled=True, portal_countdown=30,
                         portal_media_type="video", portal_media_sound=True, portal_media_has_audio=has_audio)
        link.portal_media.save("v.mp4", ContentFile(b"\x00\x00\x00\x18ftypmp42"), save=False)
        link.save()
        return self.client.get(f"/s/{link.slug}/").content.decode()

    def test_known_sound_track_keeps_the_sound_button(self):
        html = self.link(True)
        self.assertIn("var KNOWN_AUDIO = true;", html)
        self.assertIn('id="sound-btn"', html)
        self.assertIn("var KNOWN_AUDIO = null;", self.link(None))

    def test_touching_the_video_or_turning_sound_on_does_not_stop_the_redirect(self):
        html = self.link(True)
        self.assertNotIn("video.addEventListener('pointerdown', stay)", html)
        self.assertNotIn("addEventListener('volumechange'", html)
        self.assertIn('id="pause-btn"', html)  # staying is an explicit choice
