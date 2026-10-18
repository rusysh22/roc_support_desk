"""Announcement API used by every module that wants to tell users something."""
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from core.models import AuditLog, Employee, User

from .models import Announcement, AnnouncementReceipt


def user_entity_ids(user):
    """Company units a user belongs to, via their employee profile."""
    match = Q(user=user)
    if user.email:
        match |= Q(email__iexact=user.email)
    return set(Employee.objects.filter(match).values_list("unit_id", flat=True))


def live_announcements(now=None):
    now = now or timezone.now()
    return Announcement.objects.filter(
        status=Announcement.Status.PUBLISHED, publish_at__lte=now,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))


def visible_announcements_for(user, now=None):
    announcements = live_announcements(now)
    if not user.is_authenticated:
        return announcements.filter(visibility=Announcement.Visibility.PUBLIC)
    audience = Q(visibility__in=[Announcement.Visibility.PUBLIC, Announcement.Visibility.AUTHENTICATED])
    audience |= Q(visibility=Announcement.Visibility.TARGETED, target_entities__in=user_entity_ids(user))
    receipts = AnnouncementReceipt.objects.filter(announcement=OuterRef("pk"), user=user)
    return announcements.filter(audience).distinct().annotate(
        is_read=Exists(receipts),
        is_acknowledged=Exists(receipts.filter(acknowledged_at__isnull=False)),
    )


def mark_read(announcement, user):
    if user.is_authenticated:
        AnnouncementReceipt.objects.get_or_create(announcement=announcement, user=user)


def acknowledge(announcement, user):
    receipt, _ = AnnouncementReceipt.objects.get_or_create(announcement=announcement, user=user)
    if receipt.acknowledged_at is None:
        receipt.acknowledged_at = timezone.now()
        receipt.save(update_fields=["acknowledged_at"])
    return receipt


def recipients_for(announcement):
    """Active users in the announcement's audience who have an e-mail address."""
    users = User.objects.filter(is_active=True).exclude(email="")
    if announcement.visibility != Announcement.Visibility.TARGETED:
        return users
    targets = list(announcement.target_entities.values_list("pk", flat=True))
    return users.filter(employee_profile__unit_id__in=targets).distinct()


def notify_by_email(announcement):
    """Queue one e-mail per recipient (respecting each user's e-mail opt-out), once per announcement."""
    from .tasks import send_announcement_email_task

    updated = Announcement.objects.filter(pk=announcement.pk, notified_at__isnull=True).update(
        notified_at=timezone.now(),
    )
    if not updated:
        return 0
    count = 0
    for user in recipients_for(announcement).select_related("notification_pref"):
        pref = getattr(user, "notification_pref", None)
        if pref is not None and not pref.email_enabled:
            continue
        send_announcement_email_task.delay(str(announcement.pk), str(user.pk))
        count += 1
    return count


def post_announcement(
    *, title, summary="", body="", source_type=Announcement.SourceType.SYSTEM, source=None, link_url="",
    visibility=Announcement.Visibility.AUTHENTICATED, target_entities=(), require_acknowledgement=False,
    is_pinned=False, priority=Announcement.Priority.NORMAL, actor=None, send_email=False,
):
    """Create and publish an announcement right away.

    E-mails are queued only after the surrounding transaction commits, so a
    rolled-back caller never notifies anyone.
    """
    announcement = Announcement.objects.create(
        title=title[:200],
        summary=summary[:300],
        body=body,
        link_url=link_url,
        source_type=source_type,
        source_content_type=ContentType.objects.get_for_model(source) if source is not None else None,
        source_object_id=str(source.pk) if source is not None else "",
        visibility=visibility,
        require_acknowledgement=require_acknowledgement,
        is_pinned=is_pinned,
        priority=priority,
        status=Announcement.Status.PUBLISHED,
        created_by=actor,
        updated_by=actor,
    )
    if target_entities:
        announcement.target_entities.set(target_entities)
    log_published(announcement, actor)
    if send_email:
        transaction.on_commit(lambda: notify_by_email(announcement))
    return announcement


def log_published(announcement, actor):
    AuditLog.objects.create(
        actor=actor,
        actor_username=getattr(actor, "login_username", "") or "system",
        action=AuditLog.Action.ANNOUNCEMENT_PUBLISH,
        target_type=Announcement._meta.label_lower,
        target_id=str(announcement.pk),
        details={"title": announcement.title, "source": announcement.source_type},
    )
