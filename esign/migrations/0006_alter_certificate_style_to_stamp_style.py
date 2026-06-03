from django.db import migrations, models


def migrate_old_styles(apps, schema_editor):
    """Map old page-based certificate styles to the new stamp styles."""
    SignatureDocument = apps.get_model("esign", "SignatureDocument")
    mapping = {
        "none":       "simple",
        "compact":    "simple",
        "page":       "branded",
        "logo_stamp": "branded",
    }
    for old, new in mapping.items():
        SignatureDocument.objects.filter(certificate_style=old).update(certificate_style=new)


class Migration(migrations.Migration):

    dependencies = [
        ("esign", "0005_alter_signaturedocument_pdf_max_length"),
    ]

    operations = [
        migrations.RunPython(migrate_old_styles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="signaturedocument",
            name="certificate_style",
            field=models.CharField(
                choices=[
                    ("simple", "Simple — signature with text annotation"),
                    ("branded", "Branded Stamp — signature in a logo frame"),
                ],
                default="simple",
                help_text="Visual style for the signature stamp placed on the document.",
                max_length=20,
                verbose_name="Stamp Style",
            ),
        ),
    ]
