import string

from django.db import migrations, models


def dedupe_prefix_codes(apps, schema_editor):
    CaseCategory = apps.get_model("cases", "CaseCategory")
    used = set()
    alphabet = string.ascii_uppercase + string.digits
    for cat in CaseCategory.objects.order_by("created_at", "pk"):
        code = (cat.prefix_code or "RQ").strip().upper()[:2] or "RQ"
        if code not in used:
            if cat.prefix_code != code:
                cat.prefix_code = code
                cat.save(update_fields=["prefix_code"])
            used.add(code)
            continue
        replacement = None
        for a in alphabet:
            for b in alphabet:
                candidate = f"{a}{b}"
                if candidate not in used:
                    replacement = candidate
                    break
            if replacement:
                break
        if not replacement:
            raise RuntimeError("Unable to assign unique prefix_code; all 2-char codes are used.")
        cat.prefix_code = replacement
        cat.save(update_fields=["prefix_code"])
        used.add(replacement)


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0047_priority_matrix_rule"),
    ]

    operations = [
        migrations.RunPython(dedupe_prefix_codes, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="casecategory",
            name="prefix_code",
            field=models.CharField(
                default="RQ",
                help_text="Unique 2-letter or number prefix for ticket sequence (e.g. RQ, IN, HR). Names may be duplicated; codes must not.",
                max_length=2,
                unique=True,
                verbose_name="Prefix Code",
            ),
        ),
    ]
