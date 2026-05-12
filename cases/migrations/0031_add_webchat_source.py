from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("cases", "0030_message_metadata"),
    ]

    operations = [
        migrations.AlterField(
            model_name="caserecord",
            name="source",
            field=models.CharField(
                choices=[
                    ("EvolutionAPI_WA", "WhatsApp (Evolution API)"),
                    ("Email", "Email"),
                    ("WebForm", "Web Form"),
                    ("WebChat", "Web Chat"),
                    ("Teams_Bot", "Microsoft Teams (Bot)"),
                ],
                default="WebForm",
                max_length=20,
                verbose_name="Source",
            ),
        ),
    ]
