"""SQLAlchemy ORM models for agent-actions persistence.

Schema notes (v0.2):
  Approval        — added ``approver_id`` column
  AuditLog        — added ``approver_id`` column
  IdempotencyRecord — added ``tenant_id`` column; unique constraint now covers
                      (action_name, idempotency_key, tenant_id) so keys are
                      isolated across tenants.

Existing databases need a one-time migration to add the new nullable columns.
For SQLite dev databases the simplest path is to delete the file and let
``init_db`` recreate it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    action_name: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    input_payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    approver_id: Mapped[str | None] = mapped_column(String, nullable=True)
    result_payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def get_inputs(self) -> dict:
        return json.loads(self.input_payload)

    def get_result(self) -> dict | None:
        if self.result_payload is None:
            return None
        return json.loads(self.result_payload)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_name: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    input_payload: Mapped[str] = mapped_column(Text, nullable=False)  # JSON (redacted)
    policy_decision: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    approval_id: Mapped[str | None] = mapped_column(String, nullable=True)
    approver_id: Mapped[str | None] = mapped_column(String, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    result_payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def get_inputs(self) -> dict:
        return json.loads(self.input_payload)

    def get_result(self) -> dict | None:
        if self.result_payload is None:
            return None
        return json.loads(self.result_payload)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_name: Mapped[str] = mapped_column(String, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False)
    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="in_progress")
    result_payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Idempotency keys are scoped per (action, key, tenant) so two tenants
    # using the same key for the same action do not collide.
    __table_args__ = (
        UniqueConstraint(
            "action_name", "idempotency_key", "tenant_id",
            name="uq_idempotency",
        ),
    )

    def get_result(self) -> dict | None:
        if self.result_payload is None:
            return None
        return json.loads(self.result_payload)
