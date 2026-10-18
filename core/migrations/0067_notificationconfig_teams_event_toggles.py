from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0066_dynamicform_collaborators"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificationconfig",
            name="teams_on_user_reply",
            field=models.BooleanField(
                default=True,
                help_text="Post to Teams when a requester replies (portal, WhatsApp, or email).",
                verbose_name="Teams: requester replied",
            ),
        ),
        migrations.AddField(
            model_name="notificationconfig",
            name="teams_on_staff_reply",
            field=models.BooleanField(
                default=True,
                help_text="Post to Teams when a support agent replies to the requester.",
                verbose_name="Teams: support desk replied",
            ),
        ),
        migrations.AddField(
            model_name="notificationconfig",
            name="teams_on_status_change",
            field=models.BooleanField(
                default=True,
                help_text="Post to Teams when a ticket changes status (includes the progress note).",
                verbose_name="Teams: status changed",
            ),
        ),
        migrations.AddField(
            model_name="notificationconfig",
            name="teams_on_internal_mention",
            field=models.BooleanField(
                default=True,
                help_text="Post to Teams when someone is @mentioned in a ticket's internal chat.",
                verbose_name="Teams: @mention in Internal Chat",
            ),
        ),
    ]
