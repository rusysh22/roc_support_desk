import uuid

from django.db import migrations, models


def randomize_existing_tokens(apps, schema_editor):
    """
    AddField's ALTER TABLE default is evaluated once, so every pre-existing
    row would otherwise share the identical progress_token — re-randomize
    each row individually so the token is a real per-document secret.
    """
    SignatureDocument = apps.get_model("esign", "SignatureDocument")
    for doc in SignatureDocument.objects.all():
        doc.progress_token = uuid.uuid4()
        doc.save(update_fields=["progress_token"])


class Migration(migrations.Migration):

    dependencies = [
        ("esign", "0009_remove_signaturedocument_certificate_style_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="signaturedocument",
            name="progress_token",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                help_text=(
                    "Separate secret from the document's own ID, required to view the public "
                    "progress page. Regenerating it revokes any previously shared progress link."
                ),
                verbose_name="Progress Link Token",
            ),
        ),
        migrations.RunPython(randomize_existing_tokens, migrations.RunPython.noop),
    ]
