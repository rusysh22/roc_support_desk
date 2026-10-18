from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0061_fix_csp_allowed_frame_hosts_help_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="logo_light",
            field=models.ImageField(
                blank=True,
                help_text=(
                    "Used wherever the logo sits on a light/white background — login form "
                    "panel, register, forgot password, and other auth screens. Falls back "
                    "to the dark-background logo above if left blank."
                ),
                null=True,
                upload_to="site_config/",
                verbose_name="System Logo — Light Background",
            ),
        ),
        migrations.AlterField(
            model_name="siteconfig",
            name="logo",
            field=models.ImageField(
                blank=True,
                help_text=(
                    "Used wherever the logo sits on a dark background — sidebar/navbar, "
                    "dark login panels, etc. Falls back to the light-background logo below "
                    "if left blank."
                ),
                null=True,
                upload_to="site_config/",
                verbose_name="System Logo — Dark Background",
            ),
        ),
    ]
