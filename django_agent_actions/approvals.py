"""Approval service — manages the lifecycle of pending approvals.

Flow:
  1. Runtime creates a pending Approval row and returns ``approval_required``.
  2. An operator calls ``approve()`` or ``reject()``, supplying their identity
     as *approver_id*.
  3. ``approve()`` re-executes the action via a callback and stores the result.
  4. ``reject()`` marks the approval rejected with no execution.

State machine:  pending → approved | rejected
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone


class ApprovalNotFound(Exception):
    pass


class ApprovalAlreadyResolved(Exception):
    pass


class ApprovalService:
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
    ):
        from django_agent_actions.models import Approval
        from django_agent_actions.redaction import redact_dict

        safe_inputs = redact_dict(inputs)
        return Approval.objects.create(
            id=uuid.uuid4(),
            action_name=action_name,
            actor_id=actor_id,
            tenant_id=tenant_id,
            input_payload=json.dumps(safe_inputs, default=str),
            status="pending",
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get(self, approval_id: str):
        from django_agent_actions.models import Approval

        try:
            return Approval.objects.get(pk=approval_id)
        except Approval.DoesNotExist:
            raise ApprovalNotFound(f"Approval '{approval_id}' not found.")

    def list(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        from django_agent_actions.models import Approval

        qs = Approval.objects.all()
        if status:
            qs = qs.filter(status=status)
        return list(qs[offset : offset + limit])

    # ------------------------------------------------------------------
    # Resolve
    # ------------------------------------------------------------------

    def approve(
        self,
        approval_id: str,
        execute_fn: Callable[[], Any],
        *,
        approver_id: str | None = None,
    ):
        from django_agent_actions.models import Approval

        # Atomic compare-and-set: pending → executing
        updated = Approval.objects.filter(pk=approval_id, status="pending").update(
            status="executing",
            approver_id=approver_id,
        )
        if updated == 0:
            approval = Approval.objects.filter(pk=approval_id).first()
            if approval is None:
                raise ApprovalNotFound(f"Approval '{approval_id}' not found.")
            raise ApprovalAlreadyResolved(
                f"Approval '{approval_id}' is already '{approval.status}'."
            )

        try:
            result = execute_fn()
        except Exception:
            # Roll back to pending so the operator can retry.
            Approval.objects.filter(pk=approval_id, status="executing").update(
                status="pending",
                approver_id=None,
            )
            raise

        Approval.objects.filter(pk=approval_id).update(
            status="approved",
            result_payload=json.dumps(result, default=str),
            resolved_at=timezone.now(),
        )
        return Approval.objects.get(pk=approval_id)

    def reject(
        self,
        approval_id: str,
        *,
        approver_id: str | None = None,
    ):
        from django_agent_actions.models import Approval

        updated = Approval.objects.filter(pk=approval_id, status="pending").update(
            status="rejected",
            approver_id=approver_id,
            resolved_at=timezone.now(),
        )
        if updated == 0:
            approval = Approval.objects.filter(pk=approval_id).first()
            if approval is None:
                raise ApprovalNotFound(f"Approval '{approval_id}' not found.")
            raise ApprovalAlreadyResolved(
                f"Approval '{approval_id}' is already '{approval.status}'."
            )
        return Approval.objects.get(pk=approval_id)
