"""Shared activity ordering and personal read positions for desk and portal."""
from django.db.models import BooleanField, Case, Count, DateTimeField, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from core.models import User
from .models import CaseActivityRead, CaseAuditLog, CaseComment, CaseRecord, Message


def visible_cases(user):
    cases = CaseRecord.objects.all()
    if user.role_access == User.RoleAccess.PORTALUSER:
        participant = Q(followers=user)
        if user.email:
            participant |= Q(requester_email__iexact=user.email)
        return cases.filter(participant).distinct()
    if user.role_access == User.RoleAccess.ENGINEER:
        cases = cases.filter(assigned_to=user)
    if user.role_access != User.RoleAccess.SUPERADMIN and not user.can_handle_confidential:
        cases = cases.filter(category__is_confidential=False)
    return cases


def annotate_activity(cases, user):
    def latest(model, field, **filters):
        return Subquery(model.objects.filter(case_id=OuterRef("pk"), **filters)
                        .order_by(f"-{field}").values(field)[:1], output_field=DateTimeField())

    cases = cases.annotate(
        last_msg_at=latest(Message, "sent_at", is_deleted=False),
        last_audit_at=latest(CaseAuditLog, "created_at"),
        last_note_at=latest(CaseComment, "created_at") if user.role_access != User.RoleAccess.PORTALUSER
                     else Value(None, output_field=DateTimeField()),
        activity_read_at=Subquery(CaseActivityRead.objects.filter(case_id=OuterRef("pk"), user=user)
                                 .values("last_read_at")[:1]),
    ).annotate(last_activity_at=Greatest(
        "updated_at", Coalesce("last_msg_at", "updated_at"),
        Coalesce("last_audit_at", "updated_at"), Coalesce("last_note_at", "updated_at"),
    ))
    return cases.annotate(has_unread_activity=Case(
        When(Q(activity_read_at__isnull=True) | Q(last_activity_at__gt=F("activity_read_at")), then=Value(True)),
        default=Value(False), output_field=BooleanField(),
    ))


def mark_activity_read(case, user, read_at=None):
    if user.is_authenticated:
        at = read_at or timezone.now()
        reader, _ = CaseActivityRead.objects.get_or_create(case=case, user=user, defaults={"last_read_at": at})
        CaseActivityRead.objects.filter(pk=reader.pk, last_read_at__lt=at).update(last_read_at=at)


def mark_all_activity_read(user):
    """Move the user's read position past every currently-unread ticket. Returns how many."""
    if not user.is_authenticated:
        return 0
    at = timezone.now()
    case_ids = list(unread_cases(user).values_list("pk", flat=True))
    CaseActivityRead.objects.bulk_create(
        [CaseActivityRead(case_id=pk, user=user, last_read_at=at) for pk in case_ids],
        update_conflicts=True, unique_fields=["case", "user"], update_fields=["last_read_at"],
        batch_size=500,
    )
    return len(case_ids)


def attach_unread_counts(cases, user):
    """
    Set ``case.unread_count`` on already-fetched cases (annotated by annotate_activity):
    chat messages + internal notes by other people since the user's read position.
    A ticket that is unread only because of e.g. a status change still counts as 1.
    Two grouped queries for the whole page, not one per row.
    """
    unread_ids = [c.pk for c in cases if getattr(c, "has_unread_activity", False)]
    counts = {}
    if unread_ids:
        reader_at = Subquery(CaseActivityRead.objects.filter(case_id=OuterRef("case_id"), user=user).values("last_read_at")[:1])
        msgs = (Message.objects.filter(case_id__in=unread_ids, is_deleted=False, is_system=False)
                .exclude(sender_staff=user).annotate(reader_at=reader_at)
                .filter(Q(reader_at__isnull=True) | Q(sent_at__gt=F("reader_at"))))
        notes = (CaseComment.objects.filter(case_id__in=unread_ids).exclude(author=user).annotate(reader_at=reader_at)
                 .filter(Q(reader_at__isnull=True) | Q(created_at__gt=F("reader_at"))))
        for qs in (msgs, notes):
            for row in qs.order_by().values("case_id").annotate(n=Count("pk")):
                counts[row["case_id"]] = counts.get(row["case_id"], 0) + row["n"]
    for c in cases:
        c.unread_count = max(1, counts.get(c.pk, 0)) if c.pk in unread_ids else 0
    return cases


def annotate_portal_activity(cases, user):
    """Count replies in SQL, including a marker for progress updates without chat."""
    incoming = Message.objects.filter(case_id=OuterRef("pk"), direction="OUT", is_system=False, is_deleted=False).exclude(sender_staff=user)
    incoming = incoming.annotate(reader_at=Subquery(
        CaseActivityRead.objects.filter(case_id=OuterRef("case_id"), user=user).values("last_read_at")[:1],
    )).filter(Q(reader_at__isnull=True) | Q(sent_at__gt=F("reader_at")))
    counts = incoming.order_by().values("case_id").annotate(total=Count("pk")).values("total")[:1]
    return annotate_activity(cases, user).annotate(portal_unread_count=Case(
        When(has_unread_activity=True, then=Greatest(Value(1), Coalesce(Subquery(counts, output_field=IntegerField()), Value(0)))),
        default=Value(0), output_field=IntegerField(),
    ))


def portal_activity_snapshot(user):
    return [{"id": str(case.pk), "subject": case.subject, "unread": case.portal_unread_count,
             "activity": case.last_activity_at.isoformat()}
            for case in annotate_portal_activity(visible_cases(user), user).only("id", "subject")]


def unread_cases(user):
    cases = visible_cases(user).filter(is_spam=False, is_archived=False, master_ticket__isnull=True)
    if user.role_access != User.RoleAccess.PORTALUSER:
        cases = cases.exclude(status__in=[CaseRecord.Status.PENDING_APPROVAL, CaseRecord.Status.REVISION_REQUIRED])
    return annotate_activity(cases, user).filter(has_unread_activity=True)
