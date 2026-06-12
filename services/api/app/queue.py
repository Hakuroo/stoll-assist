from functools import lru_cache
from uuid import UUID

from redis import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.settings import get_settings


@lru_cache
def get_redis_client() -> Redis:
    settings = get_settings()

    # BLPOP may legitimately wait several seconds. Keep the socket timeout
    # comfortably above the blocking timeout so an empty queue is not logged
    # as an infrastructure failure.
    socket_timeout = max(settings.worker_block_timeout_seconds + 10, 30)

    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=socket_timeout,
        health_check_interval=30,
    )


def enqueue_webhook_event(event_id: UUID) -> int:
    settings = get_settings()
    return int(get_redis_client().rpush(settings.webhook_queue_name, str(event_id)))


def dequeue_webhook_event(*, timeout_seconds: int | None = None) -> UUID | None:
    settings = get_settings()
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else settings.worker_block_timeout_seconds
    )

    try:
        item = get_redis_client().blpop(
            settings.webhook_queue_name,
            timeout=timeout,
        )
    except RedisTimeoutError:
        # An empty queue is normal. Treat a read timeout as "no work" rather
        # than crashing/logging a worker infrastructure error.
        return None

    if item is None:
        return None

    _, raw_event_id = item
    return UUID(raw_event_id)
