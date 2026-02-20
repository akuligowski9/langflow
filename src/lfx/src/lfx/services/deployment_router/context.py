"""Request-scoped deployment account context for adapter calls."""

from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID

_current_deployment_account_id: ContextVar[UUID | None] = ContextVar(
    "current_deployment_account_id",
    default=None,
)


def set_current_deployment_account_id(account_id: UUID) -> None:
    _current_deployment_account_id.set(account_id)


def get_current_deployment_account_id() -> UUID | None:
    return _current_deployment_account_id.get()
