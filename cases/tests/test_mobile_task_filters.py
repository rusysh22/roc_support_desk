"""Task Management on a phone: status chips and saved filters sit behind one "Status & saved filters" toggle."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import User


@patch("licensing.models.LicenseRecord.get_effective_status", return_value="active")
@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=["testserver"])
class MobileTaskFiltersTests(TestCase):
    def test_every_task_view_has_the_toggle_before_the_status_chips(self, _):
        admin = User.objects.create_user(username="boss", login_username="boss", email="boss@example.com",
                                         role_access=User.RoleAccess.SUPERADMIN, is_staff=True, is_superuser=True)
        self.client.force_login(admin)
        for name in ("desk:case_list", "desk:case_kanban", "desk:case_calendar"):
            with self.subTest(view=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertIn('class="jk-task-more"', html)
                self.assertLess(html.index('class="jk-task-more"'), html.index("jk-task-extra"))
        css = open("static/css/jokoui.css", encoding="utf-8").read()
        self.assertIn(".jk-task-toolbar.jk-extra-open > .jk-task-extra { display: flex !important; }", css)
