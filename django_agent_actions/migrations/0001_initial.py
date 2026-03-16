import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Approval",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("action_name", models.CharField(max_length=255)),
                ("actor_id", models.CharField(max_length=255)),
                ("tenant_id", models.CharField(blank=True, max_length=255, null=True)),
                ("input_payload", models.TextField()),
                ("status", models.CharField(default="pending", max_length=50)),
                ("approver_id", models.CharField(blank=True, max_length=255, null=True)),
                ("result_payload", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "db_table": "django_agent_actions_approval",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("action_name", models.CharField(max_length=255)),
                ("actor_id", models.CharField(max_length=255)),
                ("tenant_id", models.CharField(blank=True, max_length=255, null=True)),
                ("input_payload", models.TextField()),
                ("policy_decision", models.CharField(max_length=50)),
                ("status", models.CharField(max_length=50)),
                ("approval_id", models.CharField(blank=True, max_length=255, null=True)),
                ("approver_id", models.CharField(blank=True, max_length=255, null=True)),
                (
                    "idempotency_key",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                ("result_payload", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "db_table": "django_agent_actions_auditlog",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="IdempotencyRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("action_name", models.CharField(max_length=255)),
                ("idempotency_key", models.CharField(max_length=255)),
                ("tenant_id", models.CharField(default="", max_length=255)),
                ("status", models.CharField(default="in_progress", max_length=50)),
                ("result_payload", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                "db_table": "django_agent_actions_idempotencyrecord",
            },
        ),
        migrations.AlterUniqueTogether(
            name="idempotencyrecord",
            unique_together={("action_name", "idempotency_key", "tenant_id")},
        ),
    ]
