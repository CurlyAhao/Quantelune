"""Restricted preview state. Created only on a fresh preview database; no migration."""

import uuid
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class PreviewConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "preview_configs"
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    vault_namespace: Mapped[str] = mapped_column(String(64), default=lambda: uuid.uuid4().hex)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    reserved: Mapped[Decimal] = mapped_column(Numeric(20, 8), default=Decimal("0"))
    revision: Mapped[int] = mapped_column(default=1)
    key_configured: Mapped[bool] = mapped_column(default=False)


class PreviewTurn(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "preview_turns"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"))
    request_id: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    scope_json: Mapped[str] = mapped_column(Text)
    authorization_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="running")
    response_json: Mapped[str] = mapped_column(Text, default="[]")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PreviewEffect(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "preview_effects"
    __table_args__ = (UniqueConstraint("turn_id", "operation_hash"),)
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("preview_turns.id"))
    operation_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    result_json: Mapped[str] = mapped_column(Text, default="{}")


class PreviewCharge(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "preview_charges"
    config_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("preview_configs.id"))
    turn_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("preview_turns.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    # Reservation is retained on errors, missing/unverified usage and interruption.
    status: Mapped[str] = mapped_column(String(32), default="reserved")
