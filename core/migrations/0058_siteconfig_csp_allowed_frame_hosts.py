from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0056_clear_redundant_module_overrides"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfig",
            name="csp_allowed_frame_hosts",
            field=models.TextField(
                blank=True,
                default="https://www.canva.com",
                help_text=(
                    "Extra domains allowed to be embedded via <iframe> (e.g. Canva, Figma), "
                    "one origin per line or space-separated, e.g. https://www.canva.com. "
                    "Appended to the site's frame-src Content-Security-Policy directive."
                ),
                verbose_name="Allowed Embed Domains (CSP frame-src)",
            ),
        ),
    ]
