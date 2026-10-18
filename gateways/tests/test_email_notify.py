"""Ticket update emails: who gets what, config toggles, preferences and the ticket button."""
from django.core import mail
from django.test import TestCase, override_settings

from cases.models import CaseAuditLog, CaseCategory, CaseRecord, Message
from core.models import CompanyUnit, Employee, NotificationConfig, User, UserNotificationPreference


@override_settings(SITE_URL="https://desk.example.com", CELERY_BROKER_URL="redis://127.0.0.1:1/0")
class EmailNotifyTestCase(TestCase):
    """Redis is unreachable in tests, so buffered events are sent straight away."""

    def setUp(self):
        unit = CompanyUnit.objects.create(code="IT", name="IT")
        self.category = CaseCategory.objects.create(name="General", slug="general", prefix_code="GN")
        self.agent = self.make_user("agent", User.RoleAccess.SUPPORTDESK)
        self.manager = self.make_user("manager", User.RoleAccess.MANAGER)
        self.employee = Employee.objects.create(full_name="Rina Requester", email="rina@example.com", unit=unit)
        self.case = CaseRecord.objects.create(
            subject="Laptop will not start", problem_description="It beeps three times.",
            category=self.category, requester=self.employee, requester_email="rina@example.com",
            requester_name="Rina Requester", assigned_to=self.agent,
        )
        mail.outbox = []

    def make_user(self, login, role):
        return User.objects.create_user(
            username=login.title(), email=f"{login}@example.com", login_username=login,
            initials=login[:2].upper(), password="pass-1234", role_access=role,
        )

    def recipients(self):
        return sorted(addr for m in mail.outbox for addr in m.to)

    def staff_reply(self, channel=Message.Channel.WEB, by=None):
        with self.captureOnCommitCallbacks(execute=True):
            Message.objects.create(
                case=self.case, body="Please restart while holding the power button.",
                direction=Message.Direction.OUTBOUND, channel=channel, sender_staff=by or self.agent,
            )

    def change_status(self, new_status, by=None, note=""):
        old = self.case.status
        self.case.status = new_status
        self.case.save(update_fields=["status"])
        with self.captureOnCommitCallbacks(execute=True):
            CaseAuditLog.objects.create(
                case=self.case, action="UPDATE", field_name="status", old_value=old, new_value=new_status,
                created_by=by or self.agent,
            )


class RequesterEmailTests(EmailNotifyTestCase):
    def test_portal_reply_emails_the_requester_with_a_ticket_button(self):
        self.staff_reply()
        self.assertEqual(self.recipients(), ["rina@example.com"])
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("View ticket details", html)
        self.assertIn(f"https://desk.example.com/portal/chat/{self.case.pk}/", html)
        self.assertIn("Please restart while holding the power button.", html)

    def test_reply_sent_by_email_is_not_repeated(self):
        self.staff_reply(channel=Message.Channel.EMAIL)
        self.assertEqual(self.recipients(), [])

    def test_progress_update_reaches_requester_but_not_the_actor(self):
        self.change_status(CaseRecord.Status.IN_PROGRESS)
        self.assertEqual(self.recipients(), ["rina@example.com"])
        self.assertIn("Status changed to In Progress", mail.outbox[0].subject)

    def test_resolved_is_left_to_the_dedicated_email(self):
        self.change_status(CaseRecord.Status.RESOLVED)
        self.assertEqual(self.recipients(), [])

    def test_toggle_off(self):
        cfg = NotificationConfig.get_solo()
        cfg.email_requester_on_status = False
        cfg.save()
        self.change_status(CaseRecord.Status.IN_PROGRESS)
        self.assertEqual(self.recipients(), [])

    def test_master_switch_off(self):
        cfg = NotificationConfig.get_solo()
        cfg.email_updates_enabled = False
        cfg.save()
        self.staff_reply()
        self.change_status(CaseRecord.Status.IN_PROGRESS)
        self.assertEqual(self.recipients(), [])


class StaffEmailTests(EmailNotifyTestCase):
    def test_status_change_by_someone_else_emails_the_assignee(self):
        self.change_status(CaseRecord.Status.PENDING_USER, by=self.manager)
        self.assertEqual(self.recipients(), ["agent@example.com", "rina@example.com"])
        staff_mail = next(m for m in mail.outbox if m.to == ["agent@example.com"])
        self.assertIn(f"https://desk.example.com/desk/cases/{self.case.pk}/", staff_mail.alternatives[0][0])

    def test_requester_reply_emails_assignee(self):
        with self.captureOnCommitCallbacks(execute=True):
            Message.objects.create(case=self.case, body="Opening message", direction=Message.Direction.INBOUND,
                                   channel=Message.Channel.WEB, sender_employee=self.employee)
        self.assertEqual(self.recipients(), [])  # the opening message is the new-ticket email's job
        with self.captureOnCommitCallbacks(execute=True):
            Message.objects.create(case=self.case, body="Still broken.", direction=Message.Direction.INBOUND,
                                   channel=Message.Channel.WEB, sender_employee=self.employee)
        self.assertEqual(self.recipients(), ["agent@example.com"])

    def test_assignment_emails_new_assignee_and_requester(self):
        admin = self.make_user("admin", User.RoleAccess.SUPERADMIN)
        self.case.assigned_to = self.manager
        self.case.updated_by = admin  # the previous assignee (agent) is told too
        with self.captureOnCommitCallbacks(execute=True):
            self.case.save()
        self.assertEqual(self.recipients(), ["agent@example.com", "manager@example.com", "rina@example.com"])
        assignee_mail = next(m for m in mail.outbox if m.to == ["manager@example.com"])
        self.assertIn("assigned to you", assignee_mail.subject)

    def test_personal_preference_can_turn_email_off(self):
        UserNotificationPreference.objects.update_or_create(user=self.agent, defaults={"email_enabled": False})
        self.change_status(CaseRecord.Status.PENDING_USER, by=self.manager)
        self.assertEqual(self.recipients(), ["rina@example.com"])


class DedicatedEmailTests(EmailNotifyTestCase):
    def test_acknowledgement_has_ticket_button_and_can_be_turned_off(self):
        from gateways.tasks import send_case_acknowledgment_task

        send_case_acknowledgment_task(str(self.case.pk))
        self.assertIn("View ticket details", mail.outbox[0].alternatives[0][0])
        cfg = NotificationConfig.get_solo()
        cfg.email_requester_on_received = False
        cfg.save()
        mail.outbox = []
        self.assertEqual(send_case_acknowledgment_task(str(self.case.pk)), "skipped:disabled")
        self.assertEqual(mail.outbox, [])

    def test_resolved_email_has_ticket_button(self):
        from gateways.tasks import send_requester_ticket_status_email_task

        self.case.status = CaseRecord.Status.RESOLVED
        self.case.solving_steps = "Replaced the battery."
        self.case.save()
        send_requester_ticket_status_email_task(str(self.case.pk), CaseRecord.Status.RESOLVED)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn("View ticket details", html)
        self.assertIn("Replaced the battery.", html)
