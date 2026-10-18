from datetime import datetime, timezone

from django.test import SimpleTestCase

from esign.views_public import _cache_busted


class CacheBustedUrlTests(SimpleTestCase):
    """The preview PDF cache-buster must not corrupt an Azure SAS query string."""

    when = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)

    def test_plain_url_gets_a_question_mark(self):
        url = _cache_busted("/media/esign/preview/x.pdf", self.when)
        self.assertEqual(url, f"/media/esign/preview/x.pdf?v={int(self.when.timestamp())}")

    def test_sas_url_keeps_its_signature_intact(self):
        sas = "https://acct.blob.core.windows.net/c/x.pdf?se=2026&sp=r&sig=abc%3D"
        url = _cache_busted(sas, self.when)
        self.assertEqual(url, f"{sas}&v={int(self.when.timestamp())}")
        self.assertEqual(url.count("?"), 1)
