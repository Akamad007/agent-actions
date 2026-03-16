"""Approval service — manages the lifecycle of pending approvals.

Flow:
  1. Runtime creates a pending ``Approval`` row and returns ``approval_required``.
  2. An operator (human or another system) calls ``approve()`` or ``reject()``,
     supplying their identity as *approver_id*.
  3. ``approve()`` re-executes the action via a provided callback and stores
     the result.  The *approver_id* is persisted on the row.
  4. ``reject()`` marks the approval rejected with no execution.

State machine:  pending → approved | rejected
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import update
from sqlalchemy.orm import sessionmaker

from agent_actions.db import get_session
from agent_actions.models import Approval


class ApprovalNotFound(Exception):
    pass


class ApprovalAlreadyResolved(Exception):
    pass


class ApprovalService:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        action_name: str,
        actor_id: str,
        tenant_id: str | None,
        inputs: dict,
    ) -> Approval:
        approval = Approval(
            id=str(uuid.uuid4()),
            action_name=action_name,
            actor_id=actor_id,
            tenant_id=tenant_id,
            input_payload=json.dumps(inputs, default=str),
            status="pending",
        )
        with get_session(self._session_factory) as session:
            session.add(approval)
            session.flush()
            session.refresh(approval)
            session.expunge(approval)
        return approval

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, approval_id: str) -> Approval:
        with get_session(self._session_factory) as session:
            approval = session.get(Approval, approval_id)
            if approval is None:
                raise ApprovalNotFound(f"Approval '{approval_id}' not found.")
            session.expunge(approval)
            return approval

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Approval]:
        with get_session(self._session_factory) as session:
            q = session.query(Approval)
            if status:
                q = q.filter(Approval.status == status)
            q = q.order_by(Approval.created_at.desc()).offset(offset).limit(limit)
            results = q.all()
            for r in results:
                session.expunge(r)
            return results

    # ------------------------------------------------------------------
    # Resolve
    # ------------------------------------------------------------------

    def approve(
        self,
        approval_id: str,
        execute_fn: Callable[[], Any],
        *,
        approver_id: str | None = None,
    ) -> Approval:
        """Mark the approval as approved, execute the action, and store the result.

        *execute_fn* is a zero-argument callable that runs the action and
        returns its result.  *approver_id* is recorded for audit purposes.
        """
        self._claim_pending(approval_id, next_status="executing", approver_id=approver_id)
        try:
            result = execute_fn()
        except Exception:
            self._set_status(
                approval_id,
                from_status="executing",
                to_status="pending",
                approver_id=None,
            )
            raise

        with get_session(self._session_factory) as session:
            db_approval = session.get(Approval, approval_id)
            if db_approval is None:
                raise ApprovalNotFound(f"Approval '{approval_id}' not found.")
            db_approval.status = "approved"
            db_approval.result_payload = json.dumps(result, default=str)
            db_approval.resolved_at = datetime.now(timezone.utc)
            session.flush()
            session.refresh(db_approval)
            session.expunge(db_approval)
        return db_approval

    def reject(
        self,
        approval_id: str,
        *,
        approver_id: str | None = None,
    ) -> Approval:
        """Mark the approval as rejected. No execution occurs."""
        return self._claim_pending(approval_id, next_status="rejected", approver_id=approver_id)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_pending(self, approval_id: str) -> Approval:
        approval = self.get(approval_id)
        if approval.status != "pending":
            raise ApprovalAlreadyResolved(
                f"Approval '{approval_id}' is already '{approval.status}'."
            )
        return approval

    def _claim_pending(
        self,
        approval_id: str,
        *,
        next_status: str,
        approver_id: str | None,
    ) -> Approval:
        now = datetime.now(timezone.utc)
        resolved_at = now if next_status != "executing" else None
        with get_session(self._session_factory) as session:
            result = session.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == "pending")
                .values(
                    status=next_status,
                    approver_id=approver_id,
                    resolved_at=resolved_at,
                )
            )
            if result.rowcount == 0:
                existing = session.get(Approval, approval_id)
                if existing is None:
                    raise ApprovalNotFound(f"Approval '{approval_id}' not found.")
                raise ApprovalAlreadyResolved(
                    f"Approval '{approval_id}' is already '{existing.status}'."
                )
            approval = session.get(Approval, approval_id)
            session.flush()
            session.refresh(approval)
            session.expunge(approval)
            return approval

    def _set_status(
        self,
        approval_id: str,
        *,
        from_status: str,
        to_status: str,
        approver_id: str | None,
    ) -> None:
        with get_session(self._session_factory) as session:
            session.execute(
                update(Approval)
                .where(Approval.id == approval_id, Approval.status == from_status)
                .values(
                    status=to_status,
                    approver_id=approver_id,
                    resolved_at=None,
                )
            )
