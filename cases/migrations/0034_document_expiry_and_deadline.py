"""
Migration: Add expiry and deadline config to document template system.

Fields added:
- DocumentTemplate.token_validity_days
- DocumentTemplate.approver_deadline_days
- CaseDocument.token_expires_at
- DocumentApprovalStep.approve_by
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0033_add_document_template_system'),
    ]

    operations = [
        migrations.AddField(
            model_name='documenttemplate',
            name='token_validity_days',
            field=models.PositiveIntegerField(
                default=7,
                help_text='How many days the approval magic-link URL remains valid. 0 = never expires.',
                verbose_name='Link Validity (days)',
            ),
        ),
        migrations.AddField(
            model_name='documenttemplate',
            name='approver_deadline_days',
            field=models.PositiveIntegerField(
                default=3,
                help_text='How many days each approver has to act before the step is considered overdue. 0 = no deadline.',
                verbose_name='Approver Deadline (days)',
            ),
        ),
        migrations.AddField(
            model_name='casedocument',
            name='token_expires_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text='When set, the approval magic-link URL becomes invalid after this datetime.',
                verbose_name='Token Expires At',
            ),
        ),
        migrations.AddField(
            model_name='documentapprovalstep',
            name='approve_by',
            field=models.DateTimeField(
                blank=True,
                null=True,
                help_text="Deadline for this approver to act. Derived from template's approver_deadline_days.",
                verbose_name='Approve By',
            ),
        ),
    ]
