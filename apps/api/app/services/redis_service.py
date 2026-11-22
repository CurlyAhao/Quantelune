"""Redis connection management with health check and graceful degradation."""

import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("statforge.redis")

_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Get or create the shared Redis connection pool."""
    global _pool
    if _pool is None:
        _pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _pool


async def redis_health_check() -> dict[str, Any]:
    """Check Redis connectivity. Returns status dict."""
    try:
        r = await get_redis()
        await r.ping()
        return {"status": "ok"}
    except Exception as exc:
        logger.warning("Redis health check failed: %s", type(exc).__name__)
        return {"status": "unavailable", "detail": type(exc).__name__}


async def redis_set(key: str, value: str, ex: int | None = None) -> bool:
    """Set a key with optional expiry (seconds). Degrades gracefully."""
    try:
        r = await get_redis()
        await r.set(key, value, ex=ex)
        return True
    except Exception:
        logger.warning("Redis SET failed for key=%s", key)
        return False


async def redis_get(key: str) -> str | None:
    """Get a key value. Returns None on miss or failure."""
    try:
        r = await get_redis()
        return await r.get(key)
    except Exception:
        logger.warning("Redis GET failed for key=%s", key)
        return None


async def redis_delete(key: str) -> bool:
    """Delete a key. Degrades gracefully."""
    try:
        r = await get_redis()
        await r.delete(key)
        return True
    except Exception:
        logger.warning("Redis DELETE failed for key=%s", key)
        return False


async def close_redis() -> None:
    """Close the Redis connection pool."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
