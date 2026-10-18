from django.db import migrations, models


PRIORITY_MAP = {
    "Critical": ("High", "1", "P1"),
    "High": ("High", "2", "P2"),
    "Medium": ("Medium", "2", "P3"),
    "Low": ("Low", "3", "P4"),
}

SLA_MAP = {
    "Critical": "P1",
    "High": "P2",
    "Medium": "P3",
    "Low": "P4",
}


def forwards_migrate_priority(apps, schema_editor):
    CaseRecord = apps.get_model("cases", "CaseRecord")
    for old, (impact, severity, priority) in PRIORITY_MAP.items():
        CaseRecord.objects.filter(priority=old).update(
            impact=impact,
            severity=severity,
            priority=priority,
        )
    # Any unexpected values → Medium / Sev2 / P3
    CaseRecord.objects.exclude(priority__in=["P1", "P2", "P3", "P4"]).update(
        impact="Medium",
        severity="2",
        priority="P3",
    )

    SLAPolicy = apps.get_model("cases", "SLAPolicy")
    for old, new in SLA_MAP.items():
        SLAPolicy.objects.filter(priority=old).update(priority=new)


def backwards_migrate_priority(apps, schema_editor):
    REVERSE_PRIORITY = {
        "P1": "Critical",
        "P2": "High",
        "P3": "Medium",
        "P4": "Low",
    }
    CaseRecord = apps.get_model("cases", "CaseRecord")
    for new, old in REVERSE_PRIORITY.items():
        CaseRecord.objects.filter(priority=new).update(priority=old)

    SLAPolicy = apps.get_model("cases", "SLAPolicy")
    for new, old in REVERSE_PRIORITY.items():
        SLAPolicy.objects.filter(priority=new).update(priority=old)


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0045_follow_up_priority_tracking"),
    ]

    operations = [
        migrations.AddField(
            model_name="caserecord",
            name="impact",
            field=models.CharField(
                choices=[("High", "High"), ("Medium", "Medium"), ("Low", "Low")],
                default="Medium",
                max_length=20,
                verbose_name="Impact",
            ),
        ),
        migrations.AddField(
            model_name="caserecord",
            name="severity",
            field=models.CharField(
                choices=[
                    ("1", "Severity 1"),
                    ("2", "Severity 2"),
                    ("3", "Severity 3"),
                ],
                default="2",
                max_length=5,
                verbose_name="Severity",
            ),
        ),
        migrations.RunPython(forwards_migrate_priority, backwards_migrate_priority),
        migrations.AlterField(
            model_name="caserecord",
            name="priority",
            field=models.CharField(
                choices=[("P1", "P1"), ("P2", "P2"), ("P3", "P3"), ("P4", "P4")],
                default="P3",
                help_text="Calculated from Severity × Impact matrix (P1–P4).",
                max_length=20,
                verbose_name="Priority",
            ),
        ),
        migrations.AlterField(
            model_name="slapolicy",
            name="priority",
            field=models.CharField(
                choices=[("P1", "P1"), ("P2", "P2"), ("P3", "P3"), ("P4", "P4")],
                max_length=20,
                unique=True,
                verbose_name="Priority Level",
            ),
        ),
    ]
