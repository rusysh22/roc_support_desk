from django.test import SimpleTestCase, override_settings

from core.middleware import ContentSecurityPolicyMiddleware


class ContentSecurityPolicyMiddlewareTests(SimpleTestCase):
    @override_settings(
        USE_AZURE_STORAGE=True,
        USE_S3_STORAGE=False,
        AZURE_ACCOUNT_NAME="examplestorage",
        AZURE_CUSTOM_DOMAIN="",
    )
    def test_azure_storage_is_allowed_for_fetch_and_frames(self):
        middleware = ContentSecurityPolicyMiddleware(lambda request: None)

        self.assertIn(
            "frame-src 'self' https://examplestorage.blob.core.windows.net ",
            middleware.csp,
        )
        self.assertIn(
            "connect-src 'self' https://examplestorage.blob.core.windows.net ",
            middleware.csp,
        )

    def test_video_players_of_the_redirect_portal_may_be_framed(self):
        frame_src = next(part for part in ContentSecurityPolicyMiddleware.CSP.split(";") if part.strip().startswith("frame-src"))
        self.assertIn("https://www.youtube-nocookie.com", frame_src)
        self.assertIn("https://www.tiktok.com", frame_src)
