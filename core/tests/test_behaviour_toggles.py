"""SiteConfig behaviour toggles: each one defaults to the standard product behaviour."""
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from cases.forms import CaseCreateForm
from cases.models import CaseCategory, TicketLocation
from core.models import SiteConfig, User, UserNotificationPreference
from core.notifications import notify_user

NO_LICENSE_GATE = override_settings(MIDDLEWARE=[m for m in settings.MIDDLEWARE if not m.startswith("licensing.")])


def make_user(login, role=User.RoleAccess.PORTALUSER, email=None):
    return User.objects.create_user(
        username=login.title(), email=email or f"{login}@example.com", login_username=login,
        initials=login[:2].upper(), password="pass-1234", role_access=role,
    )


class TicketLocationToggleTests(TestCase):
    def test_location_is_optional_by_default(self):
        self.assertFalse(SiteConfig.get_solo().require_ticket_location)
        self.assertFalse(CaseCreateForm().fields["location"].required)

    def test_location_is_required_when_enabled(self):
        config = SiteConfig.get_solo()
        config.require_ticket_location = True
        config.save()
        self.assertTrue(CaseCreateForm().fields["location"].required)


@NO_LICENSE_GATE
class CreateCasePageTests(TestCase):
    def setUp(self):
        CaseCategory.objects.create(name="General", slug="general", prefix_code="GN")

    def test_generate_template_button_shown_by_default(self):
        self.assertContains(self.client.get(reverse("cases:create_case")), "Generate Template")

    def test_generate_template_button_can_be_hidden(self):
        config = SiteConfig.get_solo()
        config.show_generate_template_button = False
        config.save()
        self.assertNotContains(self.client.get(reverse("cases:create_case")), "Generate Template")

    def test_location_field_hidden_without_locations_unless_required(self):
        self.assertNotContains(self.client.get(reverse("cases:create_case")), 'id="id_location"')
        TicketLocation.objects.create(name="Head Office", slug="head-office")
        self.assertContains(self.client.get(reverse("cases:create_case")), 'id="id_location"')


@NO_LICENSE_GATE
class DeveloperContactTests(TestCase):
    def test_lists_superadmins_when_no_contact_email(self):
        make_user("boss", role=User.RoleAccess.SUPERADMIN, email="boss@example.com")
        self.assertContains(self.client.get(reverse("help_about")), "boss@example.com")

    def test_single_contact_when_configured(self):
        make_user("boss", role=User.RoleAccess.SUPERADMIN, email="boss@example.com")
        config = SiteConfig.get_solo()
        config.developer_contact_name = "Helpdesk Team"
        config.developer_contact_email = "helpdesk@example.com"
        config.save()
        response = self.client.get(reverse("help_about"))
        self.assertContains(response, "helpdesk@example.com")
        self.assertNotContains(response, "boss@example.com")


@NO_LICENSE_GATE
@override_settings(WA_ENABLED=True)
class WhatsAppNotificationToggleTests(TestCase):
    def setUp(self):
        self.user = make_user("agent", role=User.RoleAccess.SUPPORTDESK)
        self.pref, _ = UserNotificationPreference.objects.get_or_create(user=self.user)
        self.pref.email_enabled = False
        self.pref.whatsapp_enabled = True
        self.pref.whatsapp_number = "+6281234567890"
        self.pref.on_new_message = True
        self.pref.save()

    def test_profile_offers_whatsapp_by_default(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("profile")), 'name="whatsapp_enabled"')

    def test_profile_hides_whatsapp_when_disallowed(self):
        config = SiteConfig.get_solo()
        config.allow_whatsapp_notifications = False
        config.save()
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse("profile")), 'name="whatsapp_enabled"')

    def test_whatsapp_dispatch_follows_toggle(self):
        with mock.patch("core.notifications.send_notification_whatsapp_task.delay") as delay:
            notify_user(self.user, "new_message", {})
        delay.assert_called_once()

        config = SiteConfig.get_solo()
        config.allow_whatsapp_notifications = False
        config.save()
        with mock.patch("core.notifications.send_notification_whatsapp_task.delay") as delay:
            notify_user(self.user, "new_message", {})
        delay.assert_not_called()
