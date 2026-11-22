"""User domain model."""

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    """A registered StatForge user."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    # Identity reads cannot implicitly load business resources before their ACL.
    projects = relationship("Project", back_populates="owner", lazy="raise")
    auth_sessions = relationship(
        "AuthSession",
        back_populates="user",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
