"""Shared Pydantic schemas."""

from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error response."""

    detail: str
    status_code: int = 400


class PaginationParams(BaseModel):
    """Pagination query parameters."""

    page: int = 1
    page_size: int = 20


class PaginatedResponse(BaseModel):
    """Paginated list response envelope."""

    items: list[Any]
    total: int
    page: int
    page_size: int
