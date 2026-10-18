from django.db import migrations, models


def backfill_first_response(apps, schema_editor):
    CaseRecord = apps.get_model("cases", "CaseRecord")
    Message = apps.get_model("cases", "Message")
    CaseAuditLog = apps.get_model("cases", "CaseAuditLog")

    for case in CaseRecord.objects.filter(first_response_at__isnull=True).iterator():
        candidates = []
        first_out = (
            Message.objects.filter(
                case_id=case.pk,
                direction="OUT",
                is_system=False,
                sender_staff_id__isnull=False,
            )
            .order_by("sent_at")
            .values_list("sent_at", flat=True)
            .first()
        )
        if first_out:
            candidates.append(first_out)
        first_assign = (
            CaseAuditLog.objects.filter(
                case_id=case.pk,
                field_name="assigned_to",
            )
            .exclude(new_value="")
            .exclude(new_value__isnull=True)
            .order_by("created_at")
            .values_list("created_at", flat=True)
            .first()
        )
        if first_assign:
            candidates.append(first_assign)
        if candidates:
            CaseRecord.objects.filter(pk=case.pk).update(first_response_at=min(candidates))


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0048_casecategory_unique_prefix_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="caserecord",
            name="first_response_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Set once when the ticket is first assigned or first replied to by staff.",
                null=True,
                verbose_name="First Response At",
            ),
        ),
        migrations.RunPython(backfill_first_response, migrations.RunPython.noop),
    ]
