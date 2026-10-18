"""
Email notifications for ticket activity.

Fed by the same signals as the Microsoft Teams notifications (cases/signals.py),
so every code path that replies, changes a status, assigns a ticket, adds a
follower or @mentions someone is covered without extra calls in the views.

Who gets what (NotificationConfig toggles on Apps Connection → Email; people
with an account also follow their own My Profile preferences — email on/off,
event types, quiet hours; nobody is told about their own actions):

    requester replied          → assignee + followers, or the New Ticket
                                 recipients while nobody is assigned  (email_staff_on_reply)
    support desk replied       → requester                             (email_requester_on_reply)
                                 assignee + followers                  (email_staff_on_reply)
    status changed (+ note)    → requester                             (email_requester_on_status)
                                 assignee + followers                  (email_staff_on_status)
    assigned / reassigned      → new + previous assignee               (email_staff_on_assigned)
                                 requester ("now handled by …")        (email_requester_on_assigned)
    @mention in internal chat  → the mentioned users                   (email_staff_on_mention)
    added as follower          → the new followers                     (email_staff_on_follower)
    requester changed          → the new requester                     (email_requester_on_status)

Resolved / Closed for the requester have their own email
(gateways.tasks.send_requester_ticket_status_email_task), and a support desk
reply sent through the Email or WhatsApp channel already reached the requester,
so neither is repeated here.

Events on one ticket are buffered for EMAIL_NOTIFY_BATCH_SECONDS and sent as one
email per recipient. If Redis is unreachable they are sent at once.
"""
import json
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

BATCH_SECONDS = int(getattr(settings, "EMAIL_NOTIFY_BATCH_SECONDS", 60))
_BUF_KEY = "rocdesk:email:buf:{}"
_PENDING_KEY = "rocdesk:email:pending:{}"

REQUESTER_TOGGLES = {
    "staff_reply": "email_requester_on_reply",
    "status": "email_requester_on_status",
    "assigned": "email_requester_on_assigned",
    "requester_changed": "email_requester_on_status",
}
STAFF_TOGGLES = {
    "user_reply": "email_staff_on_reply",
    "staff_reply": "email_staff_on_reply",
    "status": "email_staff_on_status",
    "assigned": "email_staff_on_assigned",
    "mention": "email_staff_on_mention",
    "follower": "email_staff_on_follower",
}
# UserNotificationPreference field per event kind (people with an account)
PREF_FIELDS = {
    "user_reply": "on_new_message",
    "staff_reply": "on_new_message",
    "status": "on_status_change",
    "mention": "on_mention",
    "assigned": "on_follower_added",
    "follower": "on_follower_added",
    "requester_changed": "on_status_change",
}
# a reply sent through these channels already reached the requester
_REPLY_CHANNELS_ALREADY_DELIVERED = ("Email", "WhatsApp")
_FINAL_FOR_REQUESTER = ("Resolved", "Closed")
STAFF_ACTIVE_WINDOW = timedelta(minutes=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def updates_enabled():
    from core.models import NotificationConfig

    return bool(NotificationConfig.get_solo().email_updates_enabled)


def queue_email_event(case_id, kind, **data):
    """Buffer one ticket event for email (no-op when ticket-update emails are off)."""
    if kind not in PREF_FIELDS or not updates_enabled():
        return False
    event = {"kind": kind, "at": timezone.now().isoformat(), **data}
    try:
        from gateways.teams_notify import _redis

        r = _redis()
        key = _BUF_KEY.format(case_id)
        pipe = r.pipeline()
        pipe.rpush(key, json.dumps(event, default=str))
        pipe.expire(key, 3600)
        pipe.execute()
        if r.set(_PENDING_KEY.format(case_id), "1", nx=True, ex=BATCH_SECONDS + 300):
            flush_email_events_task.apply_async(args=[str(case_id)], countdown=BATCH_SECONDS)
    except Exception as exc:  # Redis down → don't lose the notification
        logger.warning("Email buffer unavailable (%s); sending event for case %s immediately", exc, case_id)
        flush_email_events_task.delay(str(case_id), [event])
    return True


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@shared_task(name="gateways.flush_email_events_task")
def flush_email_events_task(case_id, inline_events=None):
    """Collect the buffered events for a ticket and queue one email per recipient."""
    events = inline_events
    if events is None:
        try:
            from gateways.teams_notify import _redis

            r = _redis()
            r.delete(_PENDING_KEY.format(case_id))
            pipe = r.pipeline()
            key = _BUF_KEY.format(case_id)
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            raw, _ = pipe.execute()
            events = [json.loads(x) for x in raw]
        except Exception as exc:
            logger.error("Email flush for case %s could not read the buffer: %s", case_id, exc)
            return "error:buffer"
    if not events or not updates_enabled():
        return "skipped:empty"

    from cases.models import CaseRecord
    from gateways.teams_notify import _merge_status_notes

    try:
        case = CaseRecord.objects.select_related("category", "assigned_to", "requester").get(id=case_id)
    except CaseRecord.DoesNotExist:
        return "error:case_not_found"

    batches = recipient_batches(case, _merge_status_notes(events))
    for email, info in batches.items():
        send_ticket_activity_email_task.delay(str(case.id), email, info["events"], info["portal"], info["name"])
    return f"queued:{len(batches)}"


@shared_task(
    bind=True,
    name="gateways.send_ticket_activity_email_task",
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_ticket_activity_email_task(self, case_id, to_email, events, portal, recipient_name=""):
    from django.core.mail import EmailMultiAlternatives

    from cases.models import CaseRecord
    from core.models import SiteConfig
    from core.utils.email_branding import attach_logo, render_branded_from_template
    from gateways.tasks import _case_desk_url, _case_portal_url, _resolved_from_email

    try:
        case = CaseRecord.objects.select_related("category", "assigned_to", "requester").get(id=case_id)
    except CaseRecord.DoesNotExist:
        return "error:case_not_found"

    site_name = SiteConfig.get_solo().site_name or "Support Desk"
    items = [event_view(ev) for ev in events[-10:]]
    url = _case_portal_url(case.id) if portal else _case_desk_url(case.id)
    if len(items) == 1:
        title = items[0]["title"]
    else:
        title = f"{len(events)} updates on your ticket" if portal else f"{len(events)} updates on this ticket"
    context = {
        "recipient_name": recipient_name or "there",
        "intro": (
            "Here is the latest on your ticket." if portal
            else "There is new activity on a ticket you are involved in."
        ),
        "items": items,
        "more": max(0, len(events) - len(items)),
        **ticket_summary(case, portal),
    }
    html_body = render_branded_from_template(
        "emails/bodies/ticket_activity.html",
        context,
        site_name=site_name,
        title=title,
        eyebrow=f"{site_name} · {case.case_number}",
        cta_url=url,
        cta_label="View ticket details",
        footer_note=(
            "You receive this email because you submitted this ticket."
            if portal else
            "You receive this email because you are involved in this ticket. "
            "Choose which updates you get under My Profile → Notification Preferences."
        ),
    )
    subject = f"[{case.case_number}] {title} — {case.subject}"[:250]
    plain = "\n".join(
        [f"Hello {recipient_name or 'there'},", "", context["intro"], ""]
        + [f"- {i['title']}" + (f": {i['text']}" if i["text"] else "") for i in items]
        + ["", f"Ticket: {case.case_number} — {case.subject}", f"Status: {case.get_status_display()}",
           f"View ticket details: {url}"]
    )

    cc = []
    if portal and case.cc_emails:
        cc = [e.strip() for e in case.cc_emails.split(",") if e.strip() and e.strip().lower() != to_email.lower()]
    from_email = _resolved_from_email()
    domain = from_email.split("@")[-1] if "@" in from_email else "rocdesk.local"
    thread_id = f"<case-{case.id}@{domain}>"
    msg = EmailMultiAlternatives(
        subject=subject, body=plain, from_email=from_email, to=[to_email], cc=cc or None,
        headers={"In-Reply-To": thread_id, "References": thread_id},
    )
    msg.attach_alternative(html_body, "text/html")
    attach_logo(msg)
    try:
        msg.send(fail_silently=False)
    except Exception as exc:
        logger.warning("Ticket activity email to %s for %s failed: %s", to_email, case.case_number, exc)
        raise self.retry(exc=exc)
    logger.info("Ticket activity email sent to %s for %s (%d event(s))", to_email, case.case_number, len(events))
    return "success"


# ---------------------------------------------------------------------------
# Recipients
# ---------------------------------------------------------------------------

def recipient_batches(case, events):
    """{email: {"events": [...], "portal": bool, "name": str}} after config and preference filters."""
    from core.models import NotificationConfig, User, UserNotificationPreference

    cfg = NotificationConfig.get_solo()
    requester_email = ((case.requester.email if case.requester else "") or case.requester_email or "").strip().lower()
    requester_name = (case.requester.full_name if case.requester else "") or case.requester_name or ""
    team = [(case.assigned_to, "staff")] + [(f, "staff") for f in case.followers.all()]
    staff_recently_active = bool(case.last_viewed_at and timezone.now() - case.last_viewed_at < STAFF_ACTIVE_WINDOW)

    batches = {}
    for ev in events:
        kind = ev["kind"]
        users, emails = [], []  # [(User, why)], [(email, why)]
        if kind == "user_reply":
            users = [(u, why) for u, why in team if u]
            if not users:  # nobody on the ticket yet: the New Ticket recipients
                emails = [(e.strip().lower(), "staff") for e in cfg.get_email_recipients_list()]
        elif kind == "staff_reply":
            users = team
            if ev.get("channel") not in _REPLY_CHANNELS_ALREADY_DELIVERED:
                emails = [(requester_email, "requester")]
        elif kind == "status":
            users = team
            if ev.get("new_status") not in _FINAL_FOR_REQUESTER:
                emails = [(requester_email, "requester")]
        elif kind == "mention":
            users = [(u, "mentioned") for u in User.objects.filter(pk__in=ev.get("mentioned_ids") or [])]
        elif kind == "assigned":
            users = [(u, "assignee") for u in User.objects.filter(pk=ev.get("assignee_id"))]
            users += [(u, "old_assignee") for u in User.objects.filter(pk=ev.get("old_assignee_id"))]
            emails = [(requester_email, "requester")]
        elif kind == "follower":
            users = [(u, "follower") for u in User.objects.filter(pk__in=ev.get("user_ids") or [])]
        elif kind == "requester_changed":
            emails = [((ev.get("requester_email") or requester_email), "requester")]

        candidates = {}  # email -> (User | None, why)
        for u, why in users:
            if u and u.is_active and u.email and u.pk != ev.get("actor_id"):
                candidates.setdefault(u.email.strip().lower(), (u, why))
        for e, why in emails:
            if e:
                candidates.setdefault(e, (None, why))

        for email, (user, why) in candidates.items():
            is_requester = why == "requester"
            toggle = (REQUESTER_TOGGLES if is_requester else STAFF_TOGGLES).get(kind)
            if not toggle or not getattr(cfg, toggle, False):
                continue
            if user is None:
                user = User.objects.filter(email__iexact=email, is_active=True).first()
                if user and user.pk == ev.get("actor_id"):
                    continue
            if kind == "user_reply" and staff_recently_active:
                continue  # someone is looking at the ticket right now
            pref = UserNotificationPreference.objects.filter(user=user).first() if user else None
            field = "on_status_change" if (kind == "assigned" and is_requester) else PREF_FIELDS[kind]
            if pref and (not pref.email_enabled or not getattr(pref, field, True) or pref.is_in_quiet_hours()):
                continue
            portal = is_requester or bool(user and user.role_access == User.RoleAccess.PORTALUSER)
            name = (user.get_full_name() or user.username) if user else (requester_name if is_requester else "")
            entry = batches.setdefault(email, {"events": [], "portal": portal, "name": name})
            entry["events"].append({**ev, "for": why})
    return batches


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

def event_view(ev):
    """{icon, title, meta, text, label, tone} for one event, worded for its reader."""
    from gateways.teams_notify import _short_time, plain_snippet

    kind, why = ev["kind"], ev.get("for", "")
    actor = ev.get("actor") or ""
    for_requester = why == "requester"
    text, label, tone, icon = "", "", "indigo", "🔔"
    if kind == "user_reply":
        icon, title = "💬", f"{actor or 'The requester'} replied"
        text = ev.get("text") or ""
    elif kind == "staff_reply":
        icon = "↩️"
        title = "The support team replied" if for_requester else f"{actor or 'A colleague'} replied"
        if for_requester and actor:
            title = f"{actor} from the support team replied"
        text = ev.get("text") or ""
    elif kind == "status":
        icon, tone = "🔄", "violet"
        title = f"Status changed to {ev.get('new_label') or '…'}"
        text, label = ev.get("note") or "", "Progress note"
    elif kind == "mention":
        icon, tone = "@", "amber"
        title = f"{actor or 'Someone'} mentioned you in the internal chat"
        text = ev.get("text") or ""
    elif kind == "assigned":
        new_name = ev.get("assignee_name") or "a support agent"
        icon = "📌"
        if why == "assignee":
            title = "This ticket has been assigned to you" + (f" by {actor}" if actor else "")
        elif why == "old_assignee":
            icon, tone = "🔁", "amber"
            title = f"This ticket was reassigned to {new_name}"
        else:
            title = f"Your ticket is now handled by {new_name}"
    elif kind == "follower":
        icon, title = "👥", "You were added as a follower" + (f" by {actor}" if actor else "")
    elif kind == "requester_changed":
        icon, title = "👤", "You are now the requester of this ticket"
    else:
        title = "Ticket updated"
    meta = " · ".join(filter(None, [_short_time(ev.get("at")), f"by {actor}" if actor and kind == "status" else ""]))
    return {"icon": icon, "title": title, "meta": meta, "text": plain_snippet(text, 600), "label": label, "tone": tone}


# Main road of a ticket, shown as a progress tracker in the email.
PROGRESS_STEPS = [
    ("New", "Received"),
    ("Assigned", "Assigned"),
    ("InProgress", "In progress"),
    ("Resolved", "Resolved"),
    ("Closed", "Closed"),
]
_STEP_INDEX = {
    "PendingApproval": 0, "RevisionRequired": 0, "New": 0,
    "Assigned": 1,
    "InProgress": 2, "PendingUser": 2, "PendingExternal": 2,
    "Resolved": 3,
    "Closed": 4,
}


def ticket_summary(case, portal):
    """Context for the ticket card: status pill, people and the progress tracker."""
    meta = case.STATUS_META.get(case.status, {})
    stopped = case.status in ("Rejected", "Cancelled")
    current = _STEP_INDEX.get(case.status, 0)
    steps = []
    for i, (_code, label) in enumerate(PROGRESS_STEPS):
        if case.status == "PendingUser" and i == 2:
            label = "Waiting for you" if portal else "Pending user"
        elif case.status == "PendingExternal" and i == 2:
            label = "Waiting on a third party" if portal else "Pending external"
        state = "done" if i < current or (i == current and case.status == "Closed") else "current" if i == current else "todo"
        steps.append({"label": label, "state": "todo" if stopped else state, "number": i + 1})
    requester = (case.requester.full_name if case.requester else "") or case.requester_name or case.requester_email or ""
    return {
        "case_number": case.case_number,
        "case_subject": case.subject or "",
        "status_label": case.get_status_display(),
        "status_color": meta.get("color") or "#6366f1",
        "status_stopped": stopped,
        "assignee_name": (case.assigned_to.get_full_name() or case.assigned_to.username) if case.assigned_to else "",
        "requester_label": "" if portal else requester,
        "priority_label": "" if portal or not case.priority else case.get_priority_display(),
        "steps": steps,
    }
