"""
Microsoft Teams notifications for ticket activity.

Two destinations, fed from the same per-ticket event buffer:

1. **Channel** (TeamsConfig.incoming_webhook_url) — one card per ticket per
   batch window in a support channel. Per-event toggles on NotificationConfig.
2. **Personal** (TeamsConfig.personal_webhook_url) — one Workflows flow that
   posts the card in each recipient's chat with the Workflows bot. The app adds
   ``"recipient": "<email>"`` to the payload; the flow uses it as the chat
   recipient, so users don't join any channel or configure anything.
   Who gets what (each person's UserNotificationPreference applies: Teams
   on/off, event toggles, quiet hours; the actor never notifies themselves):
     * requester replied      → assigned agent + followers        (on_new_message)
     * support desk replied   → requester + assigned + followers  (on_new_message)
     * status changed (+note) → requester + assigned + followers  (on_status_change)
     * @mention               → the mentioned users               (on_mention)
     * assigned to a ticket   → new assignee, previous assignee,  (on_follower_added;
                                requester ("now handled by …")      requester: on_status_change)
     * added as follower      → the new followers                 (on_follower_added)
     * requester changed      → the new requester                 (on_status_change)
   Only emails in TeamsConfig.personal_allowed_domains (your Microsoft 365
   tenant) are sent, so guests/external requesters don't break the flow.

New tickets keep their own immediate channel card (send_teams_notification_task).

Events for one ticket are buffered in Redis for TEAMS_BATCH_SECONDS and sent
as one card per destination. If Redis is unreachable they are sent at once.
Workflows webhooks reply 202, legacy Incoming Webhooks 200 — both are success.
"""
import json
import logging
import re
from datetime import datetime

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

BATCH_SECONDS = int(getattr(settings, "TEAMS_BATCH_SECONDS", 60))
_BUF_KEY = "rocdesk:teams:buf:{}"
_PENDING_KEY = "rocdesk:teams:pending:{}"
_SNIPPET_MAX = 300

# channel card: NotificationConfig toggle per event kind
EVENT_TOGGLES = {
    "user_reply": "teams_on_user_reply",
    "staff_reply": "teams_on_staff_reply",
    "status": "teams_on_status_change",
    "status_note": "teams_on_status_change",  # progress note typed with a status change
    "mention": "teams_on_internal_mention",
}
# personal cards: UserNotificationPreference toggle per event kind
PERSONAL_PREFS = {
    "user_reply": "on_new_message",
    "staff_reply": "on_new_message",
    "status": "on_status_change",
    "mention": "on_mention",
    "assigned": "on_follower_added",
    "follower": "on_follower_added",
    "requester_changed": "on_status_change",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def queue_teams_event(case_id, kind, **data):
    """Buffer one ticket event for Teams (no-op when neither destination wants it)."""
    if not (channel_event_enabled(kind) or personal_enabled()):
        return False
    event = {"kind": kind, "at": timezone.now().isoformat(), **data}
    try:
        r = _redis()
        key = _BUF_KEY.format(case_id)
        pipe = r.pipeline()
        pipe.rpush(key, json.dumps(event, default=str))
        pipe.expire(key, 3600)
        pipe.execute()
        # first event in the window schedules the flush
        if r.set(_PENDING_KEY.format(case_id), "1", nx=True, ex=BATCH_SECONDS + 300):
            flush_teams_events_task.apply_async(args=[str(case_id)], countdown=BATCH_SECONDS)
    except Exception as exc:  # Redis down → don't lose the notification
        logger.warning("Teams buffer unavailable (%s); sending event for case %s immediately", exc, case_id)
        flush_teams_events_task.delay(str(case_id), [event])
    return True


def channel_event_enabled(kind):
    from core.models import NotificationConfig, TeamsConfig

    teams_cfg = TeamsConfig.get_solo()
    if not teams_cfg.is_notification_enabled or not teams_cfg.incoming_webhook_url:
        return False
    toggle = EVENT_TOGGLES.get(kind)
    return bool(toggle and getattr(NotificationConfig.get_solo(), toggle, False))


# kept for callers of the earlier channel-only API
teams_event_enabled = channel_event_enabled


def personal_enabled():
    from core.models import TeamsConfig

    cfg = TeamsConfig.get_solo()
    return bool(cfg.is_personal_enabled and cfg.personal_webhook_url)


def post_to_teams(payload, target="channel"):
    """POST a message payload to the channel or personal webhook. Returns the requests.Response."""
    import requests

    from core.models import TeamsConfig

    cfg = TeamsConfig.get_solo()
    url = cfg.personal_webhook_url if target == "personal" else cfg.incoming_webhook_url
    return requests.post(url, json=payload, timeout=10)


def is_success(response):
    # Incoming Webhook → 200, Workflows webhook → 202 Accepted
    return 200 <= response.status_code < 300


def plain_snippet(html, limit=_SNIPPET_MAX):
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", html or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def allowed_teams_email(email):
    """True if this address belongs to the Microsoft 365 tenant (personal_allowed_domains)."""
    from core.models import TeamsConfig

    if not email or "@" not in email:
        return False
    domains = [d.strip().lower().lstrip("@") for d in (TeamsConfig.get_solo().personal_allowed_domains or "").split(",")]
    domains = [d for d in domains if d]
    return not domains or email.rsplit("@", 1)[1].lower() in domains


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@shared_task(name="gateways.flush_teams_events_task")
def flush_teams_events_task(case_id, inline_events=None):
    """Collect the buffered events for a ticket and send the channel + personal cards."""
    events = inline_events
    if events is None:
        try:
            r = _redis()
            # clear "pending" first: an event arriving now schedules its own
            # flush rather than getting stranded in the buffer
            r.delete(_PENDING_KEY.format(case_id))
            pipe = r.pipeline()
            key = _BUF_KEY.format(case_id)
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            raw, _ = pipe.execute()
            events = [json.loads(x) for x in raw]
        except Exception as exc:
            logger.error("Teams flush for case %s could not read the buffer: %s", case_id, exc)
            return "error:buffer"
    if not events:
        return "skipped:empty"

    from cases.models import CaseRecord

    try:
        case = CaseRecord.objects.select_related("category", "assigned_to", "requester").get(id=case_id)
    except CaseRecord.DoesNotExist:
        return "error:case_not_found"

    events = _merge_status_notes(events)
    sent = []

    channel_events = [e for e in events if channel_event_enabled(e["kind"])]
    if channel_events:
        post_teams_payload_task.delay(build_activity_card(case, channel_events), case.case_number, "channel")
        sent.append("channel")

    if personal_enabled():
        for email, (person_events, portal) in _personal_batches(case, events).items():
            payload = build_activity_card(case, person_events, personal=True, portal=portal)
            payload["recipient"] = email
            post_teams_payload_task.delay(payload, f"{case.case_number} → {email}", "personal")
            sent.append(email)
    return f"sent:{len(sent)}"


@shared_task(
    bind=True,
    name="gateways.post_teams_payload_task",
    max_retries=4,
    default_retry_delay=30,
    acks_late=True,
)
def post_teams_payload_task(self, payload, label="", target="channel"):
    """Send a prepared card; retries on errors and honours 429 Retry-After."""
    try:
        resp = post_to_teams(payload, target)
    except Exception as exc:
        raise self.retry(exc=exc)
    if is_success(resp):
        logger.info("Teams card posted (%s, %s): HTTP %s", target, label, resp.status_code)
        return "success"
    if resp.status_code == 429:
        wait = int(resp.headers.get("Retry-After", "30") or 30)
        raise self.retry(countdown=min(max(wait, 5), 300), exc=Exception("Teams rate limit (429)"))
    logger.warning("Teams %s webhook HTTP %s for %s: %s", target, resp.status_code, label, resp.text[:200])
    if resp.status_code >= 500:
        raise self.retry(exc=Exception(f"Teams webhook HTTP {resp.status_code}"))
    return f"error:http_{resp.status_code}"  # 4xx: bad URL/payload — retrying won't help


# ---------------------------------------------------------------------------
# Personal recipients
# ---------------------------------------------------------------------------

def _personal_batches(case, events):
    """{email: ([events seen from that person's side], is_portal_user)} after preference/domain filters.

    Each event is copied with ``"for"`` set to why this person gets it (``assignee``,
    ``old_assignee``, ``requester``, ``mentioned``, ``follower``, ``staff``) so the
    card can speak to them ("Ticket assigned to you", "Reassigned to …").
    """
    from core.models import User, UserNotificationPreference

    requester_email = ((case.requester.email if case.requester else "") or case.requester_email or "").strip().lower()
    followers = list(case.followers.all())
    team = [(case.assigned_to, "staff")] + [(f, "staff") for f in followers]

    batches = {}
    for ev in events:
        kind = ev["kind"]
        pref_field = PERSONAL_PREFS.get(kind)
        if not pref_field:
            continue
        users, emails = [], []  # [(User, why)], [(email, why)]
        if kind == "user_reply":
            users = team
        elif kind in ("staff_reply", "status"):
            users = team
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
            # the person who was made requester at that moment, not whoever holds it at send time
            emails = [((ev.get("requester_email") or requester_email), "requester")]

        candidates = {}  # email -> (User | None, why)
        for u, why in users:
            # nobody is told about their own actions — except taking a ticket for
            # yourself, so the assignment still lands in your Teams as a to-do
            is_self = u is not None and u.pk == ev.get("actor_id")
            if u and u.is_active and u.email and (not is_self or (kind == "assigned" and why == "assignee")):
                candidates.setdefault(u.email.strip().lower(), (u, why))
        for e, why in emails:
            if e:
                candidates.setdefault(e, (None, why))
        for email, (user, why) in candidates.items():
            if user is None:
                user = User.objects.filter(email__iexact=email, is_active=True).first()
                if user and user.pk == ev.get("actor_id"):
                    continue
            if not allowed_teams_email(email):
                continue
            pref = UserNotificationPreference.objects.filter(user=user).first() if user else None
            # assignment news for the requester follows their "status change" setting
            field = "on_status_change" if (kind == "assigned" and why == "requester") else pref_field
            if pref and (not pref.teams_enabled or not getattr(pref, field, True) or pref.is_in_quiet_hours()):
                continue
            # the requester (unless they're also staff on this ticket) opens the portal chat
            portal = email == requester_email and not (user and user.role_access != User.RoleAccess.PORTALUSER)
            batches.setdefault(email, ([], portal))[0].append(
                {**ev, "for": why, "self": bool(user and user.pk == ev.get("actor_id"))})
    return batches


# ---------------------------------------------------------------------------
# Card — the update itself is the focus; ticket details are a small footer
# ---------------------------------------------------------------------------

def build_activity_card(case, events, personal=False, portal=False):
    """Adaptive Card for a batch of events on one ticket.

    personal=True: the reader is involved in the ticket, so message text is shown even
    on confidential tickets and wording is from their side ("assigned to you").
    portal=True: the reader is the requester → link to the portal chat, no internal facts.
    """
    from core.models import SiteConfig
    from gateways.tasks import _case_desk_url, _case_portal_url

    hide_text = bool(getattr(case.category, "is_confidential", False)) and not personal
    site_name = SiteConfig.get_solo().site_name or "Support Desk"
    assignee = _name(case.assigned_to) if case.assigned_to else "Unassigned"

    # ticket number (bold) and subject (grey), both large
    body = [{"type": "TextBlock", "text": f"🎫 {case.case_number}", "size": "Large", "weight": "Bolder",
             "wrap": True, "spacing": "None"}]
    if case.subject:
        body.append({"type": "TextBlock", "text": case.subject[:120], "size": "Large", "isSubtle": True,
                     "wrap": True, "spacing": "None"})
    if len(events) > 1:
        body.append({"type": "TextBlock", "text": f"🔔 {len(events)} updates on this ticket",
                     "weight": "Bolder", "size": "Medium", "wrap": True, "spacing": "Small"})

    shown = events[-10:]
    for i, ev in enumerate(shown):
        body += _event_blocks(case, ev, hide_text, first=(i == 0 and len(events) == 1))
    if len(events) > 10:
        body.append({"type": "TextBlock", "text": f"…and {len(events) - 10} earlier update(s).",
                     "isSubtle": True, "size": "Small", "wrap": True})
    if hide_text:
        body.append({"type": "TextBlock", "text": "🔒 Confidential ticket — open it in the desk to read the details.",
                     "isSubtle": True, "wrap": True, "size": "Small"})
    # footer: ticket state on one plain line (no pills), e.g. "Status: Assigned · Assigned: Budi · Requester: … · Priority: P3 · Support Desk"
    status_icon = case.STATUS_META.get(case.status, {}).get("icon", "")
    facts = [("Status", f"{status_icon} {case.get_status_display()}".strip()), ("Assigned", assignee)]
    if not portal:
        requester = (case.requester.full_name if case.requester else "") or case.requester_name or case.requester_email or "—"
        facts.append(("Requester", requester))
        if case.priority:
            facts.append(("Priority", case.get_priority_display()))
    facts.append(("", site_name))
    body.append(_facts_line(facts))
    if personal:
        # personal cards arrive from the notification account's chat: replies typed there
        # never reach the ticket, so say so at the very bottom (small red text)
        body.append(_not_monitored_note())

    url = _case_portal_url(case.id) if portal else _case_desk_url(case.id)
    action_title = f"Reply in {site_name}" if any(e["kind"] in ("user_reply", "staff_reply", "mention") for e in events) else "Open Ticket"
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.4",
                "msteams": {"width": "Full"},
                "body": body,
                "actions": [{"type": "Action.OpenUrl", "title": action_title, "url": url}],
            },
        }],
    }


def _event_blocks(case, ev, hide_text, first=False):
    """Headline ("<who> (<role>) replied") + the message in a highlighted box + a small time."""
    kind, why = ev["kind"], ev.get("for", "")
    actor = ev.get("actor") or "Someone"
    when = _short_time(ev.get("at"))
    text, label = "", ""

    if kind == "user_reply":
        headline = f"💬 {actor} (Requester) replied"
        text = ev.get("text") or ""
    elif kind == "staff_reply":
        role = "Support Desk · assigned agent" if ev.get("is_assignee") else "Support Desk"
        headline = f"↩️ {actor} ({role}) replied"
        text = ev.get("text") or ""
    elif kind == "status":
        headline = f"🔄 Status: {ev.get('old_label', '?')} → {ev.get('new_label', '?')}"
        if ev.get("actor"):
            headline += f" (by {actor})"
        text, label = ev.get("note") or "", "Progress note"
    elif kind == "mention":
        headline = (f"@ {actor} mentioned you in Internal Chat" if why == "mentioned"
                    else f"@ {actor} mentioned {', '.join(ev.get('mentioned', [])) or 'a colleague'} in Internal Chat")
        text = ev.get("text") or ""
    elif kind == "assigned":
        new_name = ev.get("assignee_name") or "a support agent"
        if why == "assignee" and ev.get("self"):
            headline = "📌 You assigned this ticket to yourself"
        elif why == "assignee":
            headline = "📌 This ticket has been assigned to you" + (f" by {ev['actor']}" if ev.get("actor") else "")
        elif why == "old_assignee":
            headline = f"🔁 This ticket was reassigned to {new_name}" + (f" by {ev['actor']}" if ev.get("actor") else "")
        elif why == "requester":
            headline = f"👤 Your ticket is now handled by {new_name}"
        else:
            headline = f"📌 Assigned to {new_name}" + (f" by {ev['actor']}" if ev.get("actor") else "")
    elif kind == "follower":
        headline = "👥 You were added as a follower" + (f" by {ev['actor']}" if ev.get("actor") else "")
    elif kind == "requester_changed":
        headline = "👤 You are now the requester of this ticket" + (f" (set by {ev['actor']})" if ev.get("actor") else "")
    else:
        headline = f"🔔 {kind}"

    # the update itself is the main content, so it sits in a coloured pill
    style = HEADLINE_PILL_STYLE.get(kind, "accent")
    if kind == "assigned" and why == "old_assignee":
        style = "warning"  # the ticket moved away from the reader
    blocks = [_pill_row([{
        "type": "Column", "width": "auto",
        "items": [{"type": "Container", "style": style, "roundedCorners": True, "items": [{
            "type": "TextBlock", "text": headline, "weight": "Bolder", "wrap": True,
            "size": "Medium" if first else "Default", "color": PILL_TEXT_COLOR[style],
        }]}],
    }], separator=not first)]
    blocks[0]["spacing"] = "Medium"
    if text and not hide_text:
        items = []
        if label:
            items.append({"type": "TextBlock", "text": label, "size": "Small", "isSubtle": True, "spacing": "None"})
        if kind in CHAT_KINDS:  # chat messages in italics (TextBlock has no italic, TextRun does)
            items.append({"type": "RichTextBlock", "spacing": "Small",
                          "inlines": [{"type": "TextRun", "text": text, "italic": True}]})
        else:
            items.append({"type": "TextBlock", "text": text, "wrap": True, "spacing": "Small"})
        blocks.append({"type": "Container", "style": "emphasis", "bleed": False, "spacing": "Small", "items": items})
    blocks.append({"type": "TextBlock", "text": when, "size": "Small", "isSubtle": True, "spacing": "Small"})
    return blocks


# Adaptive Cards have no pill element: a pill is a styled, rounded container in an
# auto-width column (roundedCorners is honoured by current Teams clients, ignored elsewhere).
PILL_TEXT_COLOR = {"accent": "Accent", "good": "Good", "warning": "Warning",
                   "attention": "Attention", "emphasis": "Default", "default": "Default"}
HEADLINE_PILL_STYLE = {
    "assigned": "accent", "follower": "accent", "requester_changed": "accent",
    "status": "good", "user_reply": "emphasis", "staff_reply": "emphasis", "mention": "warning",
}
PRIORITY_PILL_STYLE = {"P1": "attention", "P2": "warning", "P3": "accent", "P4": "good"}
# Closest Teams colour to each status colour in CaseRecord.STATUS_META (cards cannot use hex colours).
STATUS_PILL_STYLE = {
    "New": "accent", "Assigned": "accent", "PendingUser": "accent",
    "InProgress": "warning", "PendingApproval": "warning",
    "Resolved": "good",
    "PendingExternal": "attention", "Rejected": "attention", "RevisionRequired": "attention",
    "Closed": "emphasis", "Cancelled": "emphasis",
}
CHAT_KINDS = ("user_reply", "staff_reply", "mention")


def _pill(label, value, style):
    """Small pill: subtle label + bold value, e.g. "Status: Assigned"."""
    runs = []
    if label:
        runs.append({"type": "TextRun", "text": f"{label}: ", "size": "Small", "isSubtle": True})
    runs.append({"type": "TextRun", "text": value or "—", "size": "Small", "weight": "Bolder",
                 "color": PILL_TEXT_COLOR[style]})
    return {"type": "Column", "width": "auto", "items": [{
        "type": "Container", "style": style, "roundedCorners": True,
        "items": [{"type": "RichTextBlock", "inlines": runs}],
    }]}


def _facts_line(facts):
    """One small line under a separator: subtle labels, bold values, " · " between them."""
    runs = []
    for i, (label, value) in enumerate(facts):
        if i:
            runs.append({"type": "TextRun", "text": "  ·  ", "size": "Small", "isSubtle": True})
        if label:
            runs.append({"type": "TextRun", "text": f"{label}: ", "size": "Small", "isSubtle": True})
        runs.append({"type": "TextRun", "text": value or "—", "size": "Small", "weight": "Bolder"})
    return {"type": "RichTextBlock", "inlines": runs, "separator": True, "spacing": "Medium"}


def _pill_row(columns, separator=False):
    return {"type": "ColumnSet", "spacing": "Medium" if separator else "Small",
            "separator": separator, "columns": columns}


def _not_monitored_note():
    """Red warning text: replies typed in the notification account's chat never reach the ticket."""
    return {"type": "TextBlock", "text": "🔕 This chat is not monitored · No reply",
            "size": "Small", "color": "Attention", "wrap": True, "spacing": "Medium"}


def _name(user):
    return (user.get_full_name() or user.username) if user else ""


def _merge_status_notes(events):
    """Attach each progress note to the status change it belongs to (same new status)."""
    merged = []
    for ev in events:
        if ev["kind"] == "status_note":
            target = next((m for m in reversed(merged)
                           if m["kind"] == "status" and m.get("new_status") == ev.get("new_status")), None)
            if target:
                target["note"] = ev.get("note", "")
                continue
            ev = {**ev, "kind": "status", "old_label": ev.get("old_label") or "…"}
        merged.append(ev)
    return merged


def _short_time(iso):
    try:
        dt = timezone.localtime(datetime.fromisoformat(iso))
        return dt.strftime("%d %b %H:%M")
    except Exception:
        return ""


def _redis():
    import redis

    url = getattr(settings, "CELERY_BROKER_URL", "") or "redis://127.0.0.1:6379/0"
    return redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
