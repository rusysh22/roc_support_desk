import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cases', '0042_remove_documenttemplatefield_esign_role_and_more'),
        ('core', '0043_add_user_module_access'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SLAPolicy',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('priority', models.CharField(
                    choices=[
                        ('Low', 'Low'),
                        ('Medium', 'Medium'),
                        ('High', 'High'),
                        ('Critical', 'Critical'),
                    ],
                    max_length=20,
                    unique=True,
                    verbose_name='Priority Level',
                )),
                ('response_time_hours', models.PositiveIntegerField(
                    default=2,
                    help_text='Time to first response',
                    verbose_name='Response SLA (Hours)',
                )),
                ('resolution_time_hours', models.PositiveIntegerField(
                    default=24,
                    help_text='Time to resolve ticket',
                    verbose_name='Resolution SLA (Hours)',
                )),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(app_label)s_%(class)s_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(app_label)s_%(class)s_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'SLA Policy',
                'verbose_name_plural': 'SLA Policies',
                'ordering': ['resolution_time_hours'],
            },
        ),
        migrations.AddField(
            model_name='casecategory',
            name='default_case_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('Question', 'Question'),
                    ('Incident', 'Incident'),
                    ('Request', 'Request'),
                ],
                help_text='If set, tickets in this category will default to this type.',
                max_length=20,
                null=True,
                verbose_name='Default Ticket Type',
            ),
        ),
        migrations.AddField(
            model_name='casecategory',
            name='default_tags',
            field=models.CharField(
                blank=True,
                help_text='Comma-separated tags automatically applied to new tickets.',
                max_length=255,
                verbose_name='Default Tags',
            ),
        ),
        migrations.AddField(
            model_name='casecategory',
            name='is_need_admin_approval',
            field=models.BooleanField(
                default=False,
                help_text='If checked along with Routing Approval, tickets will require an additional approval step from the Support Desk Admin after manager approval.',
                verbose_name='Need Admin Approval',
            ),
        ),
        migrations.AddField(
            model_name='casecategory',
            name='is_use_routing_approval',
            field=models.BooleanField(
                default=False,
                help_text="If checked, new tickets will enter 'Pending Approval' status and require the requester's direct manager to approve.",
                verbose_name='Use Routing Approval',
            ),
        ),
        migrations.CreateModel(
            name='TicketApproval',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('tier', models.PositiveSmallIntegerField(
                    choices=[(1, 'Tier 1 (Manager)'), (2, 'Tier 2 (Admin)')],
                    default=1,
                    verbose_name='Approval Tier',
                )),
                ('status', models.CharField(
                    choices=[
                        ('Pending', 'Pending'),
                        ('Approved', 'Approved'),
                        ('Rejected', 'Rejected'),
                    ],
                    default='Pending',
                    max_length=20,
                    verbose_name='Status',
                )),
                ('comments', models.TextField(blank=True, verbose_name='Comments/Notes')),
                ('actioned_at', models.DateTimeField(blank=True, null=True, verbose_name='Actioned At')),
                ('approver', models.ForeignKey(
                    blank=True,
                    help_text='If null, any Support Desk staff/admin can approve.',
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='ticket_approvals',
                    to='core.employee',
                    verbose_name='Approver',
                )),
                ('case', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='approvals',
                    to='cases.caserecord',
                    verbose_name='Ticket',
                )),
                ('created_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(app_label)s_%(class)s_created',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Created By',
                )),
                ('updated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='%(app_label)s_%(class)s_updated',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Updated By',
                )),
            ],
            options={
                'verbose_name': 'Ticket Approval',
                'verbose_name_plural': 'Ticket Approvals',
                'ordering': ['case', 'tier'],
            },
        ),
    ]
