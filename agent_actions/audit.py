"""Audit logger — writes every action invocation to the audit_logs table.

Inputs are redacted before storage using the centralized ``redact_dict``
utility so that passwords, tokens, and other sensitive field values never
reach the database.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import sessionmaker

from agent_actions.db import get_session
from agent_actions.models import AuditLog
from agent_actions.redaction import redact_dict


class AuditLogger:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

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
    ) -> AuditLog:
        # Redact sensitive fields before storing — passwords/tokens never land in DB.
        safe_inputs = redact_dict(inputs)
        entry = AuditLog(
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
        with get_session(self._session_factory) as session:
            session.add(entry)
            session.flush()
            session.refresh(entry)
            # Detach so the object is usable after session close.
            session.expunge(entry)
        return entry

    def list_logs(
        self,
        *,
        action_name: str | None = None,
        actor_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditLog]:
        with get_session(self._session_factory) as session:
            q = session.query(AuditLog)
            if action_name:
                q = q.filter(AuditLog.action_name == action_name)
            if actor_id:
                q = q.filter(AuditLog.actor_id == actor_id)
            if tenant_id:
                q = q.filter(AuditLog.tenant_id == tenant_id)
            q = q.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
            results = q.all()
            for r in results:
                session.expunge(r)
            return results
