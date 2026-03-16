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

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from agent_actions.db import get_session
from agent_actions.models import IdempotencyRecord


@dataclass(frozen=True)
class IdempotencyExecution:
    status: str
    result: Any


class IdempotencyService:
    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        wait_timeout_seconds: float = 5.0,
        poll_interval_seconds: float = 0.01,
    ) -> None:
        self._session_factory = session_factory
        self._wait_timeout_seconds = wait_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds

    def get_cached(
        self,
        action_name: str,
        key: str,
        *,
        tenant_id: str | None = None,
    ) -> dict | None:
        """Return the cached result if the (action_name, key, tenant_id) was seen before."""
        tenant_scope = self._normalize_tenant_id(tenant_id)
        with get_session(self._session_factory) as session:
            record = (
                session.query(IdempotencyRecord)
                .filter_by(
                    action_name=action_name,
                    idempotency_key=key,
                    tenant_id=tenant_scope,
                )
                .first()
            )
            if record is None:
                return None
            return record.get_result()

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
        tenant_scope = self._normalize_tenant_id(tenant_id)
        record = IdempotencyRecord(
            action_name=action_name,
            idempotency_key=key,
            tenant_id=tenant_scope,
            status="in_progress",
            result_payload=None,
        )
        try:
            with get_session(self._session_factory) as session:
                session.add(record)
                session.flush()
        except IntegrityError:
            cached = self._wait_for_completed(action_name, key, tenant_id=tenant_scope)
            return IdempotencyExecution(status="duplicate", result=cached)

        try:
            result = execute_fn()
        except Exception:
            self._release_claim(action_name, key, tenant_id=tenant_scope)
            raise

        with get_session(self._session_factory) as session:
            db_record = (
                session.query(IdempotencyRecord)
                .filter_by(
                    action_name=action_name,
                    idempotency_key=key,
                    tenant_id=tenant_scope,
                )
                .first()
            )
            if db_record is None:
                raise RuntimeError("Idempotency claim disappeared before completion.")
            db_record.status = "completed"
            db_record.result_payload = json.dumps(result, default=str)

        return IdempotencyExecution(status="success", result=result)

    def _wait_for_completed(
        self,
        action_name: str,
        key: str,
        *,
        tenant_id: str | None = None,
    ) -> dict:
        deadline = time.monotonic() + self._wait_timeout_seconds
        while time.monotonic() < deadline:
            with get_session(self._session_factory) as session:
                record = (
                    session.query(IdempotencyRecord)
                    .filter_by(
                        action_name=action_name,
                        idempotency_key=key,
                        tenant_id=self._normalize_tenant_id(tenant_id),
                    )
                    .first()
                )
                if record is None:
                    break
                if record.status == "completed" and record.result_payload is not None:
                    return record.get_result()
            time.sleep(self._poll_interval_seconds)
        raise TimeoutError(
            f"Timed out waiting for idempotent result for action '{action_name}'."
        )

    def _release_claim(
        self,
        action_name: str,
        key: str,
        *,
        tenant_id: str | None = None,
    ) -> None:
        with get_session(self._session_factory) as session:
            session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.action_name == action_name,
                    IdempotencyRecord.idempotency_key == key,
                    IdempotencyRecord.tenant_id == self._normalize_tenant_id(tenant_id),
                    IdempotencyRecord.status == "in_progress",
                )
            )

    @staticmethod
    def _normalize_tenant_id(tenant_id: str | None) -> str:
        return tenant_id or ""
