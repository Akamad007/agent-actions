"""Audit logger — writes every action invocation to the AuditLog table.

Inputs are redacted before storage using the centralized ``redact_dict``
utility so that passwords, tokens, and other sensitive field values never
reach the database.
"""

from __future__ import annotations

import json
from typing import Any

from django_agent_actions.redaction import redact_dict


class AuditLogger:
    def log(
        self,
        *,
        action_name: str,
        actor_id: str,
        tenant_id: str | None,
        inputs: dict,
        policy_decision: str,
        status: str,
        result: Any = None,
        approval_id: str | None = None,
        approver_id: str | None = None,
        idempotency_key: str | None = None,
    ):
        from django_agent_actions.models import AuditLog

        safe_inputs = redact_dict(inputs)
        return AuditLog.objects.create(
            action_name=action_name,
            actor_id=actor_id,
            tenant_id=tenant_id,
            input_payload=json.dumps(safe_inputs, default=str),
            policy_decision=policy_decision,
            status=status,
            result_payload=json.dumps(result, default=str) if result is not None else None,
            approval_id=approval_id,
            approver_id=approver_id,
            idempotency_key=idempotency_key,
        )

    def list_logs(
        self,
        *,
        action_name: str | None = None,
        actor_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        from django_agent_actions.models import AuditLog

        qs = AuditLog.objects.all()
        if action_name:
            qs = qs.filter(action_name=action_name)
        if actor_id:
            qs = qs.filter(actor_id=actor_id)
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)
        return list(qs[offset : offset + limit])
