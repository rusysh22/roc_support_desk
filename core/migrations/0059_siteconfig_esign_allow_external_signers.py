from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0058_siteconfig_csp_allowed_frame_hosts"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="esign_allow_external_signers",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "If disabled, staff can only add Internal (System) Users as e-Signature signers — "
                    "the External Email option (public magic-link, no login) is hidden and blocked."
                ),
                verbose_name="Allow External Email Signers (e-Sign)",
            ),
        ),
    ]
