"""Idempotency service — prevents duplicate execution of side-effecting actions.

If an action is invoked with the same ``(action_name, idempotency_key,
tenant_id)`` tuple that was previously executed successfully, the stored
result is returned without re-running the function.

Idempotency keys are tenant-scoped so that two tenants using the same key
for the same action do not collide.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IdempotencyExecution:
    status: str
    result: Any


class IdempotencyService:
    def __init__(
        self,
        *,
        wait_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.05,
    ) -> None:
        self._wait_timeout_seconds = wait_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def execute_once(
        self,
        action_name: str,
        key: str,
        execute_fn,
        *,
        tenant_id: str | None = None,
    ) -> IdempotencyExecution:
        """Run *execute_fn* at most once for a given idempotency tuple.

        Correctness is DB-backed:
        - the first caller inserts an ``in_progress`` claim row
        - concurrent callers wait for that row to become ``completed``
        - only the claiming caller executes the side effect
        """
        from django.db import IntegrityError

        from django_agent_actions.models import IdempotencyRecord

        tenant_scope = tenant_id or ""
        try:
            IdempotencyRecord.objects.create(
                action_name=action_name,
                idempotency_key=key,
                tenant_id=tenant_scope,
                status="in_progress",
            )
        except IntegrityError:
            cached = self._wait_for_completed(action_name, key, tenant_scope=tenant_scope)
            return IdempotencyExecution(status="duplicate", result=cached)

        try:
            result = execute_fn()
        except Exception:
            IdempotencyRecord.objects.filter(
                action_name=action_name,
                idempotency_key=key,
                tenant_id=tenant_scope,
                status="in_progress",
            ).delete()
            raise

        IdempotencyRecord.objects.filter(
            action_name=action_name,
            idempotency_key=key,
            tenant_id=tenant_scope,
        ).update(status="completed", result_payload=json.dumps(result, default=str))

        return IdempotencyExecution(status="success", result=result)

    def _wait_for_completed(
        self,
        action_name: str,
        key: str,
        *,
        tenant_scope: str,
    ) -> dict:
        from django_agent_actions.models import IdempotencyRecord

        deadline = time.monotonic() + self._wait_timeout_seconds
        while time.monotonic() < deadline:
            record = IdempotencyRecord.objects.filter(
                action_name=action_name,
                idempotency_key=key,
                tenant_id=tenant_scope,
            ).first()
            if record is None:
                break
            if record.status == "completed" and record.result_payload is not None:
                return record.get_result()
            time.sleep(self._poll_interval_seconds)
        raise TimeoutError(
            f"Timed out waiting for idempotent result for action '{action_name}'."
        )
