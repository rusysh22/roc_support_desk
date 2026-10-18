from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0059_siteconfig_esign_allow_external_signers"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="company_logo",
            field=models.ImageField(
                blank=True,
                help_text=(
                    "Logo printed on generated documents — e-Signature stamps and case "
                    "letterheads (kop surat). Falls back to System Logo if left blank."
                ),
                null=True,
                upload_to="site_config/",
                verbose_name="Company Logo",
            ),
        ),
        migrations.AlterField(
            model_name="siteconfig",
            name="logo",
            field=models.ImageField(
                blank=True,
                help_text=(
                    "Logo shown across the web app itself — sidebar/navbar, login, register, "
                    "forgot password, and other auth screens."
                ),
                null=True,
                upload_to="site_config/",
                verbose_name="System Logo",
            ),
        ),
    ]
