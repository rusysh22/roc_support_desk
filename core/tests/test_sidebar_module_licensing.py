from types import SimpleNamespace

from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from core.models import SiteConfig, User

ALL_LICENSED_FEATURES = {
    "esign", "short_links", "kb_manage", "shared_docs",
    "form_builder", "project_management",
}


class PortalUserSidebarTests(TestCase):
    """
    Regression test for the Content & Docs section being nested inside the
    Workspace section's {% if %} in templates/base.html — a Portal User never
    has dashboard/task_management/eform_management/projects, so that bug hid
    E-Sign, Shorten Link, Knowledge Management, Shared Docs and Form Creator
    no matter what Module Defaults/Override granted them. Also covers the
    licensed_features gate (licensing/context_processors.py) that must agree
    with what feature_required()/FeatureRequiredMixin allow on the view.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(
            username="Wella Diniati",
            email="wella.diniati@example.com",
            login_username="wella.diniati",
            initials="WD",
            password="pass-wella-1",
            role_access=User.RoleAccess.PORTALUSER,
        )

    def _request(self):
        request = self.factory.get("/")
        request.user = self.user
        SessionMiddleware(lambda req: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        return request

    def _render(self, modules, licensed_features=ALL_LICENSED_FEATURES):
        return render_to_string(
            "base.html",
            {
                "user": self.user,
                "user_modules": list(modules),
                "site_config": SimpleNamespace(site_name="Support Desk", logo=None, favicon=None),
                "ai_config": SimpleNamespace(ai_enabled=False),
                "app_version": "1.0",
                "license": SimpleNamespace(features_json={f: True for f in licensed_features}),
                "license_status": "active",
                "licensed_features": set(licensed_features),
            },
            request=self._request(),
        )

    def test_esign_and_shorten_link_show_without_workspace_modules(self):
        html = self._render(["esign", "shorten_link", "my_signatures", "my_tickets"])
        self.assertIn("E-Sign", html)
        self.assertIn("Shorten Link", html)
        self.assertIn("My Tickets", html)
        self.assertIn("My Signatures", html)
        self.assertNotIn("Task Management", html)

    def test_esign_and_shorten_link_hidden_when_not_granted(self):
        html = self._render(["my_signatures", "my_tickets"])
        self.assertNotIn(">E-Sign<", html)
        self.assertNotIn("Shorten Link", html)
        self.assertIn("My Tickets", html)

    def test_esign_hidden_when_module_granted_but_not_licensed(self):
        html = self._render(["esign", "my_tickets"], licensed_features=set())
        self.assertNotIn(">E-Sign<", html)
        self.assertIn("My Tickets", html)

    def test_shorten_link_hidden_when_module_granted_but_not_licensed(self):
        html = self._render(["shorten_link", "my_tickets"], licensed_features=set())
        self.assertNotIn("Shorten Link", html)
        self.assertIn("My Tickets", html)

    def test_knowledge_management_hidden_when_module_granted_but_not_licensed(self):
        html = self._render(["knowledge_management", "my_tickets"], licensed_features=set())
        self.assertNotIn("Knowledge Management", html)
        self.assertIn("My Tickets", html)


class PortalUserModuleDefaultsTests(TestCase):
    def test_portal_user_inherits_esign_and_shorten_link_from_role_defaults(self):
        config = SiteConfig.get_solo()
        defaults = dict(config.role_module_defaults or {})
        defaults["PortalUser"] = [
            "esign",
            "shorten_link",
            "my_signatures",
            "my_tickets",
        ]
        config.role_module_defaults = defaults
        config.save(update_fields=["role_module_defaults"])

        user = User.objects.create_user(
            username="Portal Tester",
            email="portal.tester@example.com",
            login_username="portal.tester",
            initials="PT",
            password="pass-portal-1",
            role_access=User.RoleAccess.PORTALUSER,
        )
        modules = user.get_accessible_modules()
        self.assertIn("esign", modules)
        self.assertIn("shorten_link", modules)
        self.assertIn("my_tickets", modules)
        self.assertNotIn("dashboard", modules)
        self.assertNotIn("task_management", modules)
