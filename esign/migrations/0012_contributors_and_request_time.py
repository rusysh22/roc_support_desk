from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("esign", "0011_signaturedocument_is_archived"),
    ]

    operations = [
        migrations.AddField(
            model_name="signaturedocument",
            name="contributors",
            field=models.ManyToManyField(
                blank=True,
                help_text="System users who can open this document (read-only) and get an email on every update.",
                related_name="esign_contributions",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Contributors",
            ),
        ),
        migrations.AddField(
            model_name="signer",
            name="last_requested_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the signing request email was last sent (initial request or reminder).",
                null=True,
                verbose_name="Last Request Sent",
            ),
        ),
    ]
