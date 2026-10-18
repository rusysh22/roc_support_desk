"""Automatic reminder: internal signers still pending after 24 h get an e-mail and a Teams card, once per 24 h."""
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from core.models import UserNotificationPreference
from esign.models import SignatureDocument, SignatureEvent, Signer
from esign.tasks import send_auto_reminders_task
from esign.tests.test_signing_flow import EsignTestCase

AGO_25H = lambda: timezone.now() - timedelta(hours=25)  # noqa: E731


class AutoReminderTests(EsignTestCase):
    def setUp(self):
        super().setUp()
        self.doc = self.make_doc(status=SignatureDocument.Status.PENDING)
        self.signer = self.add_signer(self.doc, self.alice, last_requested_at=AGO_25H())
        patches = {
            "send": mock.patch("esign.tasks._send"),
            "teams": mock.patch("gateways.teams_notify.post_teams_payload_task.delay"),
            "enabled": mock.patch("gateways.teams_notify.personal_enabled", return_value=True),
            "domain": mock.patch("gateways.teams_notify.allowed_teams_email", return_value=True),
        }
        self.m = {name: p.start() for name, p in patches.items()}
        self.addCleanup(mock.patch.stopall)

    def run_job(self):
        send_auto_reminders_task()
        self.signer.refresh_from_db()

    def test_pending_internal_signer_gets_email_and_teams_and_the_clock_restarts(self):
        before = self.signer.last_requested_at
        self.run_job()
        self.assertEqual(self.m["send"].call_count, 1)
        self.assertIn("Reminder", self.m["send"].call_args.kwargs["subject"])
        self.assertEqual(self.m["send"].call_args.kwargs["to_email"], "alice@example.com")
        payload = self.m["teams"].call_args.args[0]
        self.assertEqual(payload["recipient"], "alice@example.com")
        self.assertIn("/sign/", payload["attachments"][0]["content"]["actions"][0]["url"])
        self.assertGreater(self.signer.last_requested_at, before)
        event = SignatureEvent.objects.filter(document=self.doc, event=SignatureEvent.Event.REMINDED).get()
        self.assertIn("e-mail and Teams", event.detail)
        # an hour later nothing is sent again: the next reminder is 24 h after this one
        self.run_job()
        self.assertEqual(self.m["send"].call_count, 1)
        self.assertEqual(self.m["teams"].call_count, 1)

    def test_only_signers_whose_turn_it_is_on_a_live_document(self):
        waiting = self.add_signer(self.doc, self.bob, order=2, status=Signer.Status.WAITING, last_requested_at=AGO_25H())
        external = Signer.objects.create(document=self.doc, external_name="Ext", external_email="ext@out.test", order=3,
                                         last_requested_at=AGO_25H(), created_by=self.owner)
        cancelled = self.make_doc(title="Old", status=SignatureDocument.Status.CANCELLED)
        self.add_signer(cancelled, self.bob, last_requested_at=AGO_25H())
        send_auto_reminders_task()
        recipients = [call.kwargs["to_email"] for call in self.m["send"].call_args_list]
        self.assertEqual(recipients, ["alice@example.com"])
        for other in (waiting, external):
            other.refresh_from_db()
            self.assertLess(other.last_requested_at, timezone.now() - timedelta(hours=24))

    def test_recently_asked_signer_is_left_alone(self):
        Signer.objects.filter(pk=self.signer.pk).update(last_requested_at=timezone.now() - timedelta(hours=3))
        self.run_job()
        self.m["send"].assert_not_called()

    def test_notification_preferences_pick_the_channels(self):
        UserNotificationPreference.objects.update_or_create(user=self.alice, defaults={"email_enabled": False, "teams_enabled": True})
        self.run_job()
        self.m["send"].assert_not_called()
        self.assertEqual(self.m["teams"].call_count, 1)

    def test_no_channel_available_keeps_the_signer_due_for_the_next_run(self):
        self.m["enabled"].return_value = False
        UserNotificationPreference.objects.update_or_create(user=self.alice, defaults={"email_enabled": False})
        before = self.signer.last_requested_at
        self.run_job()
        self.assertEqual(self.signer.last_requested_at, before)
        self.assertFalse(SignatureEvent.objects.filter(event=SignatureEvent.Event.REMINDED).exists())

    @override_settings(ESIGN_AUTO_REMIND_HOURS=0)
    def test_can_be_switched_off(self):
        self.run_job()
        self.m["send"].assert_not_called()
