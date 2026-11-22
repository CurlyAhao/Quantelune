"""Identity lookup without implicit business-resource reads or autoflush."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload

from app.models.user import User


async def find_user(
    db: AsyncSession, *, user_id: uuid.UUID | None = None, email: str | None = None
) -> User | None:
    """Return identity fields only; callers authorize related resources separately."""
    if (user_id is None) == (email is None):
        raise ValueError("Exactly one identity selector is required.")
    statement = select(User).options(noload("*"))
    if user_id is not None:
        statement = statement.where(User.id == user_id)
    else:
        statement = statement.where(User.email == email)
    with db.no_autoflush:
        return await db.scalar(statement)
