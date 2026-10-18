from django.db import migrations, models
import django.db.models.deletion
import uuid


DEFAULT_MATRIX = [
    ("1", "High", "P1"),
    ("1", "Medium", "P2"),
    ("1", "Low", "P3"),
    ("2", "High", "P2"),
    ("2", "Medium", "P3"),
    ("2", "Low", "P4"),
    ("3", "High", "P3"),
    ("3", "Medium", "P4"),
    ("3", "Low", "P4"),
]


def seed_priority_matrix(apps, schema_editor):
    PriorityMatrixRule = apps.get_model("cases", "PriorityMatrixRule")
    for severity, impact, priority in DEFAULT_MATRIX:
        PriorityMatrixRule.objects.update_or_create(
            severity=severity,
            impact=impact,
            defaults={"priority": priority},
        )


def unseed_priority_matrix(apps, schema_editor):
    PriorityMatrixRule = apps.get_model("cases", "PriorityMatrixRule")
    PriorityMatrixRule.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0046_impact_severity_priority_matrix"),
    ]

    operations = [
        migrations.CreateModel(
            name="PriorityMatrixRule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Created At")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Updated At")),
                ("severity", models.CharField(choices=[("1", "Severity 1"), ("2", "Severity 2"), ("3", "Severity 3")], max_length=5, verbose_name="Severity")),
                ("impact", models.CharField(choices=[("High", "High"), ("Medium", "Medium"), ("Low", "Low")], max_length=20, verbose_name="Impact")),
                ("priority", models.CharField(choices=[("P1", "P1"), ("P2", "P2"), ("P3", "P3"), ("P4", "P4")], max_length=20, verbose_name="Priority")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_created", to="core.user", verbose_name="Created By")),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="%(app_label)s_%(class)s_updated", to="core.user", verbose_name="Updated By")),
            ],
            options={
                "verbose_name": "Priority Matrix Rule",
                "verbose_name_plural": "Priority Matrix Rules",
                "ordering": ["severity", "impact"],
            },
        ),
        migrations.AddConstraint(
            model_name="prioritymatrixrule",
            constraint=models.UniqueConstraint(fields=("severity", "impact"), name="uniq_priority_matrix_severity_impact"),
        ),
        migrations.RunPython(seed_priority_matrix, unseed_priority_matrix),
    ]
