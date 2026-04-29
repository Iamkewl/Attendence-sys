"""Redis service — nonce store, session cache, rate limiting.

Multi-purpose Redis usage with logical DB separation:
- DB 0: Nonce store (replay protection)
- DB 1: Celery broker (managed by Celery)
- DB 2: Session cache
- DB 3: Rate limiter counters + pub/sub
"""

import redis.asyncio as redis
from redis.exceptions import ResponseError

from backend.core.config import get_settings


def _get_redis(db: int = 0) -> redis.Redis:
    """Create a Redis client for the specified logical DB."""
    settings = get_settings()
    base_url = settings.redis_url.rsplit("/", 1)[0]
    return redis.from_url(f"{base_url}/{db}", decode_responses=True)


# ── Nonce Store (DB 0) ───────────────────────────────────


async def store_nonce(device_id: str, nonce: str, ttl_seconds: int = 60) -> bool:
    """Store a nonce with TTL. Returns False if nonce already exists (replay).

    Uses SET NX (set-if-not-exists) for atomic check-and-set.
    """
    r = _get_redis(db=0)
    try:
        key = f"nonce:{device_id}:{nonce}"
        result = await r.set(key, "1", ex=ttl_seconds, nx=True)
        return result is not None
    finally:
        await r.aclose()


# ── Session Cache (DB 2) ─────────────────────────────────


async def cache_session(user_id: int, data: dict, ttl_seconds: int = 604800) -> None:
    """Cache user session data (role, email, etc.) for quick lookups."""
    r = _get_redis(db=2)
    try:
        key = f"session:{user_id}"
        try:
            await r.hset(key, mapping=data)
        except ResponseError as exc:
            # Redis 3.x does not support multi-field HSET and requires HMSET semantics.
            if "wrong number of arguments for 'hset' command" not in str(exc).lower():
                raise
            kv_pairs: list[object] = []
            for field, value in data.items():
                kv_pairs.extend([field, value])
            await r.execute_command("HMSET", key, *kv_pairs)
        await r.expire(key, ttl_seconds)
    finally:
        await r.aclose()


async def get_cached_session(user_id: int) -> dict | None:
    """Retrieve cached session data."""
    r = _get_redis(db=2)
    try:
        key = f"session:{user_id}"
        data = await r.hgetall(key)
        return data if data else None
    finally:
        await r.aclose()


async def invalidate_session(user_id: int) -> None:
    """Remove cached session on logout."""
    r = _get_redis(db=2)
    try:
        await r.delete(f"session:{user_id}")
    finally:
        await r.aclose()


# ── Rate Limiter (DB 3) ──────────────────────────────────


async def check_rate_limit(key: str, max_requests: int, window_seconds: int) -> bool:
    """Check if a rate limit has been exceeded.

    Returns True if request is ALLOWED, False if RATE LIMITED.
    Uses a sliding window counter.
    """
    r = _get_redis(db=3)
    try:
        redis_key = f"rate:{key}"
        current = await r.get(redis_key)

        if current and int(current) >= max_requests:
            return False

        pipe = r.pipeline()
        pipe.incr(redis_key)
        pipe.expire(redis_key, window_seconds)
        await pipe.execute()
        return True
    finally:
        await r.aclose()


# ── Pub/Sub for SSE (DB 3) ───────────────────────────────


async def publish_attendance_event(schedule_id: int, event_data: str) -> None:
    """Publish an attendance detection event for SSE streaming."""
    r = _get_redis(db=3)
    try:
        await r.publish(f"attendance:{schedule_id}", event_data)
    finally:
        await r.aclose()
