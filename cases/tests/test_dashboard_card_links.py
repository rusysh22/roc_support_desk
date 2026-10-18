"""Dashboard summary cards link to the tickets behind their numbers."""
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cases.models import CaseCategory, CaseRecord, Message
from core.models import User

Status = CaseRecord.Status

# card link key -> the number the dashboard shows for it
CARD_NUMBERS = {
    "total": "total_in_range",
    "active": "total_active",
    "unassigned": "total_unassigned",
    "resolved": "resolved_in_range",
    "resolved_today": "resolved_today",
    "sla_breached": "sla_breached",
    "sla_at_risk": "sla_at_risk",
    "unread": "unread_count",
}


@patch("licensing.models.LicenseRecord.get_effective_status", return_value="active")
@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class DashboardCardLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="boss", login_username="boss", email="boss@example.com",
            role_access=User.RoleAccess.SUPERADMIN, is_staff=True, is_superuser=True,
        )
        cls.agent = User.objects.create_user(
            username="agent", login_username="agent", email="agent@example.com",
            role_access=User.RoleAccess.SUPPORTDESK, is_staff=True,
        )
        cls.hardware = CaseCategory.objects.create(name="Hardware", slug="hardware", prefix_code="HW")
        cls.software = CaseCategory.objects.create(name="Software", slug="software", prefix_code="SW")
        now = timezone.now()

        def ticket(subject, status, category=None, assigned=None, due=None, **extra):
            case = CaseRecord.objects.create(
                subject=subject, problem_description=subject, status=status, category=category or cls.hardware,
                assigned_to=assigned, resolution_due_at=due, requester_email="user@example.com", **extra,
            )
            return case

        cls.breached = ticket("breached", Status.NEW, due=now - timedelta(hours=3))
        cls.at_risk = ticket("at risk", Status.IN_PROGRESS, assigned=cls.agent, due=now + timedelta(hours=10))
        cls.calm = ticket("calm", Status.PENDING_USER, category=cls.software, assigned=cls.agent, due=now + timedelta(days=5))
        cls.unread = ticket("unread", Status.ASSIGNED, assigned=cls.agent, due=now + timedelta(days=5))
        Message.objects.create(case=cls.unread, body="Hello?", direction=Message.Direction.INBOUND, channel=Message.Channel.WEB)
        cls.resolved_today = ticket("resolved today", Status.RESOLVED, assigned=cls.agent)
        cls.closed = ticket("closed", Status.CLOSED, category=cls.software, assigned=cls.agent)
        cls.in_approval = ticket("waiting approval", Status.PENDING_APPROVAL)  # hidden from the inbox by default
        cls.spam = ticket("spam", Status.NEW, is_spam=True)

        # Outside the selected range: still active, but not "created this month"; old resolved ticket.
        cls.old_active = ticket("old active", Status.ASSIGNED, assigned=cls.agent, due=now + timedelta(days=9))
        cls.old_resolved = ticket("old resolved", Status.RESOLVED, assigned=cls.agent)
        long_ago = now - timedelta(days=60)
        CaseRecord.objects.filter(pk__in=[cls.old_active.pk, cls.old_resolved.pk]).update(created_at=long_ago)
        CaseRecord.objects.filter(pk=cls.old_resolved.pk).update(updated_at=long_ago)
        CaseRecord.objects.filter(pk=cls.closed.pk).update(updated_at=now - timedelta(days=2))

    def setUp(self):
        self.client.force_login(self.admin)

    def list_count(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.context["cases"].paginator.count

    def assert_every_card_matches(self, **dashboard_filters):
        response = self.client.get(reverse("desk:dashboard"), dashboard_filters)
        self.assertEqual(response.status_code, 200)
        for key, number_name in CARD_NUMBERS.items():
            url = response.context["card_urls"][key]
            with self.subTest(card=key, filters=dashboard_filters):
                if url is None:  # nothing to open: the number is 0 by definition
                    self.assertEqual((key, response.context[number_name]), ("unassigned", 0))
                    continue
                self.assertEqual(self.list_count(url), response.context[number_name], f"{key} → {url}")
        return response

    def test_every_card_opens_exactly_the_tickets_it_counts(self, _license):
        response = self.assert_every_card_matches()
        numbers = {key: response.context[name] for key, name in CARD_NUMBERS.items()}
        # The fixture is built so that no card is trivially 0 or all the same.
        self.assertEqual(numbers["total"], 7)         # 6 + the one waiting for approval; spam and old ones excluded
        self.assertGreater(numbers["active"], numbers["unassigned"])
        for key in ("unassigned", "resolved", "resolved_today", "sla_breached", "sla_at_risk", "unread"):
            self.assertGreaterEqual(numbers[key], 1, key)

    def test_cards_keep_the_dashboard_filters(self, _license):
        self.assert_every_card_matches(category="hardware")
        self.assert_every_card_matches(category="software")
        self.assert_every_card_matches(assigned_to=str(self.agent.pk))
        response = self.client.get(reverse("desk:dashboard"), {"category": "software", "priority": "P3"})
        query = parse_qs(urlparse(response.context["card_urls"]["active"]).query)
        self.assertEqual((query["category"], query["priority"]), (["software"], ["P3"]))

    def test_unassigned_is_not_a_link_when_filtered_to_one_agent(self, _license):
        url = self.client.get(reverse("desk:dashboard"), {"assigned_to": str(self.agent.pk)}).context["card_urls"]["unassigned"]
        self.assertIsNone(url)
        self.assertIn("assigned_to=unassigned", self.client.get(reverse("desk:dashboard"), {"assigned_to": "unassigned"}).context["card_urls"]["unassigned"])

    def test_only_range_cards_carry_the_dates(self, _license):
        response = self.client.get(reverse("desk:dashboard"), {"date_from": "2026-01-01", "date_to": "2026-01-31"})
        urls = response.context["card_urls"]
        for key in ("total", "resolved"):
            self.assertEqual(parse_qs(urlparse(urls[key]).query)["date_from"], ["2026-01-01"], key)
        for key in ("active", "unassigned", "sla_breached", "sla_at_risk", "unread", "resolved_today"):
            self.assertNotIn("date_from", parse_qs(urlparse(urls[key]).query), key)

    def test_card_links_are_on_the_page(self, _license):
        html = self.client.get(reverse("desk:dashboard")).content.decode()
        for key in ("total", "active", "sla_breached", "resolved_today", "sla_at_risk"):
            url = self.client.get(reverse("desk:dashboard")).context["card_urls"][key]
            self.assertIn(f'href="{url.replace("&", "&amp;")}"', html, key)

    def test_list_understands_status_all_and_updated_date(self, _license):
        base = reverse("desk:case_list")
        inbox = self.list_count(base)
        everything = self.list_count(base + "?status=all")
        self.assertEqual(everything - inbox, 1)  # the ticket waiting for approval
        today = timezone.localdate().isoformat()
        self.assertEqual(self.list_count(f"{base}?status=Resolved,Closed&updated_date={today}"), 1)
        chips = self.client.get(base + f"?status=all&updated_date={today}").context["active_filter_chips"]
        labels = [chip["label"] for chip in chips]
        self.assertIn("Status: All", labels)
        self.assertIn(f"Updated on: {today}", labels)

    def test_cards_open_the_list_with_the_filter_panel_closed(self, _license):
        urls = self.client.get(reverse("desk:dashboard")).context["card_urls"]
        for key, url in urls.items():
            if url:
                self.assertEqual(parse_qs(urlparse(url).query)["panel"], ["closed"], key)
        for view in ("desk:case_list", "desk:case_kanban", "desk:case_calendar"):
            base = reverse(view)
            closed = self.client.get(base, {"assigned_to": "unassigned", "panel": "closed"}).content.decode()
            normal = self.client.get(base, {"assigned_to": "unassigned"}).content.decode()
            with self.subTest(view=view):
                self.assertIn('name="panel" value="closed"', closed)      # kept when searching again
                self.assertNotIn('name="panel" value="closed"', normal)
                self.assertIn("!== 'closed'", closed)                      # panel starts hidden despite the filter
