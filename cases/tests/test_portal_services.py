"""Ticket Portal service cards: star/favorite and recently / most used."""
from datetime import timedelta

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cases.models import CaseCategory, CaseRecord, FavoriteService
from cases.portal_services import service_usage, toggle_favorite
from core.models import CompanyUnit, Employee, User


@override_settings(MIDDLEWARE=[m for m in settings.MIDDLEWARE if not m.startswith("licensing.")])
class PortalServicesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="Dewi", email="dewi@example.com", login_username="dewi", initials="DE",
            password="pass-1234", role_access=User.RoleAccess.PORTALUSER,
        )
        self.other = User.objects.create_user(
            username="Budi", email="budi@example.com", login_username="budi", initials="BU",
            password="pass-1234", role_access=User.RoleAccess.PORTALUSER,
        )
        self.hardware = CaseCategory.objects.create(name="Hardware", prefix_code="HW")
        self.laptop = CaseCategory.objects.create(name="Laptop", prefix_code="LP", parent=self.hardware)
        self.email = CaseCategory.objects.create(name="Email", prefix_code="EM")
        self.network = CaseCategory.objects.create(name="Network", prefix_code="NW")
        self.employee = Employee.objects.filter(email="dewi@example.com").first() or Employee.objects.create(
            full_name="Dewi", email="dewi@example.com", unit=CompanyUnit.objects.create(code="ALPHA", name="Alpha"),
        )

    def ticket(self, category, days_ago, requester=None):
        case = CaseRecord.objects.create(
            subject="x", category=category, requester=requester or self.employee,
        )
        CaseRecord.objects.filter(pk=case.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return case

    def test_usage_rolls_sub_categories_up_to_the_card(self):
        self.ticket(self.laptop, days_ago=1)        # counts for Hardware
        self.ticket(self.hardware, days_ago=5)
        self.ticket(self.email, days_ago=10)
        usage = service_usage(self.user)
        self.assertEqual(usage[self.hardware.id]["count"], 2)
        self.assertEqual(usage[self.laptop.id]["count"], 1)             # sub-categories have their own count
        self.assertEqual(usage[self.email.id]["count"], 1)
        self.assertGreater(usage[self.hardware.id]["last"], usage[self.email.id]["last"])
        self.assertNotIn(self.network.id, usage)
        self.assertEqual(service_usage(self.other), {})   # only the user's own tickets

    def test_toggle_endpoint(self):
        self.client.force_login(self.user)
        url = reverse("cases:favorite_service_toggle", args=[self.email.id])
        self.assertEqual(self.client.post(url).json(), {"ok": True, "favorite": True})
        self.assertTrue(FavoriteService.objects.filter(user=self.user, category=self.email).exists())
        self.assertEqual(self.client.post(url).json(), {"ok": True, "favorite": False})
        self.assertFalse(FavoriteService.objects.exists())
        # Sub-categories can be starred too; GET and anonymous are refused.
        sub = self.client.post(reverse("cases:favorite_service_toggle", args=[self.laptop.id]))
        self.assertEqual(sub.json(), {"ok": True, "favorite": True})
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)

    def test_dashboard_cards_carry_star_and_usage(self):
        toggle_favorite(self.user, self.network)
        self.ticket(self.email, days_ago=2)
        self.client.force_login(self.user)
        response = self.client.get(reverse("cases:dashboard"))
        html = response.content.decode()
        self.assertIn('id="services-fav-only"', html)
        self.assertIn('data-sort="recent"', html)
        self.assertIn('data-sort="most"', html)
        cards = {c.name: c for c in response.context["categories"]}
        self.assertTrue(cards["Network"].is_favorite)
        self.assertEqual(cards["Email"].use_count, 1)
        self.assertGreater(cards["Email"].last_used, 0)
        self.assertIn('data-fav="1"', html)

    def test_sub_category_page_has_stars(self):
        CaseCategory.objects.create(name="Desktop", prefix_code="DT", parent=self.hardware)
        toggle_favorite(self.user, self.laptop)
        self.ticket(self.laptop, days_ago=1)
        self.client.force_login(self.user)
        response = self.client.get(reverse("cases:category_children", args=[self.hardware.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="svc-fav-btn is-on"', count=1)   # Laptop starred
        self.assertContains(response, 'class="svc-fav-btn"', count=1)         # Desktop not
        self.assertContains(response, reverse("cases:favorite_service_toggle", args=[self.laptop.id]))
        # Same ★ / Recently / Most used toolbar as the portal, and usage data on the cards.
        self.assertContains(response, 'id="services-fav-only"')
        self.assertContains(response, 'data-sort="most"')
        self.assertContains(response, 'data-uses="1"')                        # Laptop has one ticket
        self.assertContains(response, 'data-uses="0"')                        # Desktop none
        # Admin buttons sit beside the star (own class), not over the card icon.
        self.assertNotContains(response, "right-12")

    def test_starred_sub_category_shows_on_the_portal_under_the_star_filter(self):
        toggle_favorite(self.user, self.laptop)
        self.client.force_login(self.user)
        response = self.client.get(reverse("cases:dashboard"))
        starred = response.context["starred_children"]
        self.assertEqual([c.name for c in starred], ["Laptop"])
        self.assertEqual(starred[0].path_label, "Hardware")
        html = response.content.decode()
        card = html[html.index('data-extra="1"') - 400:html.index('data-extra="1"') + 900]
        self.assertIn("svc-fav-hidden", card)            # hidden until the ★ filter is on
        self.assertIn('data-slug="laptop"', card)

    def test_anonymous_portal_has_no_star_tools(self):
        response = self.client.get(reverse("cases:dashboard"))
        if response.status_code == 200:
            self.assertNotContains(response, 'id="services-fav-only"')
            self.assertNotContains(response, 'class="svc-fav-btn')
