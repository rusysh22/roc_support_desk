from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0060_siteconfig_company_logo"),
    ]

    operations = [
        migrations.AlterField(
            model_name="siteconfig",
            name="csp_allowed_frame_hosts",
            field=models.TextField(
                blank=True,
                default="https://www.youtube.com",
                help_text=(
                    "Extra domains allowed to be embedded via &lt;iframe&gt; (e.g. Canva, Figma), "
                    "one origin per line or space-separated, e.g. https://www.youtube.com. "
                    "Appended to the site's frame-src Content-Security-Policy directive."
                ),
                verbose_name="Allowed Embed Domains (CSP frame-src)",
            ),
        ),
    ]
