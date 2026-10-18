"""
Ticket activity → Microsoft Teams (channel card + personal chats, see
gateways/teams_notify.py).

Signals catch every code path that creates a message, a status audit entry,
an assignment or a follower (portal chat, WhatsApp, email, desk replies,
Kanban, status bar, ...), so new features don't need to remember to notify
Teams. Events are queued only after the surrounding transaction commits.
"""
import logging

from django.db import transaction
from django.db.models.signals import m2m_changed, post_init, post_save
from django.dispatch import receiver

from .models import CaseAuditLog, CaseRecord, Message

logger = logging.getLogger(__name__)


def _status_label(code):
    from core.templatetags.custom_tags import status_meta

    return status_meta(code).get("label", code) if code else ""


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _queue(case_id, kind, **data):
    from gateways.teams_notify import queue_teams_event

    def send():
        try:
            queue_teams_event(case_id, kind, **data)
        except Exception:  # never let a notification break the request
            logger.exception("Could not queue Teams %s event for case %s", kind, case_id)

    transaction.on_commit(send)


@receiver(post_save, sender=Message, dispatch_uid="teams_on_message")
def teams_on_message(sender, instance, created, raw=False, **kwargs):
    if raw or not created or instance.is_system or instance.is_deleted:
        return
    from gateways.teams_notify import plain_snippet

    text = plain_snippet(instance.body) or "📎 Attachment"
    if instance.direction == Message.Direction.INBOUND:
        # the ticket's opening message is covered by the "new ticket" card
        # (pk is a UUID, so "first" means "no other message on this ticket yet")
        if not Message.objects.filter(case_id=instance.case_id).exclude(pk=instance.pk).exists():
            return
        emp = instance.sender_employee
        case = instance.case
        actor = (getattr(emp, "full_name", "") if emp else "") or case.requester_name or case.requester_email or "Requester"
        _queue(instance.case_id, "user_reply", actor=actor, channel=instance.get_channel_display(), text=text)
    elif instance.sender_staff_id:
        _queue(instance.case_id, "staff_reply", actor=_name(instance.sender_staff), actor_id=instance.sender_staff_id,
               # role at the time of the reply (the ticket may be reassigned before the card goes out)
               is_assignee=instance.case.assigned_to_id == instance.sender_staff_id,
               channel=instance.get_channel_display(), text=text)


@receiver(post_save, sender=CaseAuditLog, dispatch_uid="teams_on_audit_log")
def teams_on_audit_log(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return
    actor = _name(instance.created_by) if instance.created_by_id else ""
    if instance.field_name == "status" and instance.old_value != instance.new_value:
        _queue(instance.case_id, "status", actor=actor, actor_id=instance.created_by_id, new_status=instance.new_value,
               old_label=_status_label(instance.old_value), new_label=_status_label(instance.new_value))
    elif instance.field_name == "requester" and instance.old_value != instance.new_value:
        # written by cases.views.case_change_requester after the new requester is saved
        case = instance.case
        new_email = ((case.requester.email if case.requester else "") or case.requester_email or "").strip().lower()
        _queue(instance.case_id, "requester_changed", actor=actor, actor_id=instance.created_by_id,
               requester_email=new_email)
    elif instance.field_name == "progress_note" and instance.new_value:
        # old_value holds the status the ticket moved to (cases.views._record_progress_note)
        _queue(instance.case_id, "status_note", actor=actor, actor_id=instance.created_by_id,
               new_status=instance.old_value, new_label=_status_label(instance.old_value), note=instance.new_value)


# --- assignment: compare assigned_to before/after every save ---------------

@receiver(post_init, sender=CaseRecord, dispatch_uid="teams_track_assignee")
def _remember_assignee(sender, instance, **kwargs):
    # read the raw attribute: touching a deferred field here would cost a query per row
    instance._teams_orig_assignee = instance.__dict__.get("assigned_to_id")


@receiver(post_save, sender=CaseRecord, dispatch_uid="teams_on_assignment")
def teams_on_assignment(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    if "assigned_to_id" not in instance.__dict__:  # saved without loading the field
        return
    new, old = instance.assigned_to_id, getattr(instance, "_teams_orig_assignee", None)
    instance._teams_orig_assignee = new
    if new and new != old:
        _queue(instance.id, "assigned", assignee_id=new, assignee_name=_name(instance.assigned_to),
               old_assignee_id=old, actor=_name(instance.updated_by), actor_id=instance.updated_by_id)


@receiver(m2m_changed, sender=CaseRecord.followers.through, dispatch_uid="teams_on_followers")
def teams_on_followers(sender, instance, action, pk_set, reverse=False, **kwargs):
    if action != "post_add" or reverse or not pk_set:
        return
    _queue(instance.id, "follower", user_ids=sorted(pk_set), actor=_name(instance.updated_by),
           actor_id=instance.updated_by_id)


# --- helpers for views that don't go through a signal ----------------------

def queue_status_change(case, old_status, user):
    """For status changes that don't write a CaseAuditLog row (e.g. closing via the closure modal)."""
    if old_status == case.status:
        return
    _queue(case.id, "status", actor=_name(user), actor_id=getattr(user, "pk", None), new_status=case.status,
           old_label=_status_label(old_status), new_label=_status_label(case.status))


def queue_mention(case, author, mentioned_users, text):
    from gateways.teams_notify import plain_snippet

    names = [_name(u) for u in mentioned_users]
    if names:
        _queue(case.id, "mention", actor=_name(author) or "Someone", actor_id=getattr(author, "pk", None),
               mentioned=names, mentioned_ids=[u.pk for u in mentioned_users], text=plain_snippet(text))
