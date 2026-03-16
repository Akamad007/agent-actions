"""Django ORM models for agent-actions persistence."""

from __future__ import annotations

import json
import uuid

from django.db import models
from django.utils import timezone


class Approval(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action_name = models.CharField(max_length=255)
    actor_id = models.CharField(max_length=255)
    tenant_id = models.CharField(max_length=255, null=True, blank=True)
    input_payload = models.TextField()  # JSON (redacted)
    status = models.CharField(max_length=50, default="pending")
    approver_id = models.CharField(max_length=255, null=True, blank=True)
    result_payload = models.TextField(null=True, blank=True)  # JSON
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "django_agent_actions"
        db_table = "django_agent_actions_approval"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Approval({self.id}, {self.action_name}, {self.status})"

    def get_inputs(self) -> dict:
        return json.loads(self.input_payload)

    def get_result(self) -> dict | None:
        if self.result_payload is None:
            return None
        return json.loads(self.result_payload)


class AuditLog(models.Model):
    action_name = models.CharField(max_length=255)
    actor_id = models.CharField(max_length=255)
    tenant_id = models.CharField(max_length=255, null=True, blank=True)
    input_payload = models.TextField()  # JSON (redacted)
    policy_decision = models.CharField(max_length=50)
    status = models.CharField(max_length=50)
    approval_id = models.CharField(max_length=255, null=True, blank=True)
    approver_id = models.CharField(max_length=255, null=True, blank=True)
    idempotency_key = models.CharField(max_length=255, null=True, blank=True)
    result_payload = models.TextField(null=True, blank=True)  # JSON
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "django_agent_actions"
        db_table = "django_agent_actions_auditlog"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"AuditLog({self.id}, {self.action_name}, {self.status})"

    def get_inputs(self) -> dict:
        return json.loads(self.input_payload)

    def get_result(self) -> dict | None:
        if self.result_payload is None:
            return None
        return json.loads(self.result_payload)


class IdempotencyRecord(models.Model):
    action_name = models.CharField(max_length=255)
    idempotency_key = models.CharField(max_length=255)
    # Empty string used instead of NULL so the unique constraint covers all rows.
    tenant_id = models.CharField(max_length=255, default="")
    status = models.CharField(max_length=50, default="in_progress")
    result_payload = models.TextField(null=True, blank=True)  # JSON
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "django_agent_actions"
        db_table = "django_agent_actions_idempotencyrecord"
        # Idempotency keys are scoped per (action, key, tenant) so two tenants
        # using the same key for the same action do not collide.
        unique_together = [("action_name", "idempotency_key", "tenant_id")]

    def get_result(self) -> dict | None:
        if self.result_payload is None:
            return None
        return json.loads(self.result_payload)
