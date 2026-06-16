import logging
import signal
import time
from threading import Event
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_engine
from app.queue import dequeue_webhook_event, enqueue_webhook_event
from app.repositories.webhook_events import (
    claim_webhook_event,
    get_webhook_event_for_processing,
    requeue_failed_webhook_event,
)
from app.services.webhook_processor import process_whatsapp_webhook

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("stoll_assist.worker")
shutdown_requested = Event()
MAX_ATTEMPTS = 3


def _request_shutdown(signum: int, _frame: object) -> None:
    logger.info("Shutdown requested by signal %s", signum)
    shutdown_requested.set()


def _process_event(event_id: UUID) -> None:
    engine = get_engine()
    claim = claim_webhook_event(engine=engine, event_id=event_id)
    if not claim.claimed:
        logger.info("Skipping event %s with status %s", event_id, claim.status)
        return

    event = get_webhook_event_for_processing(engine=engine, event_id=event_id)
    logger.info(
        "Processing event %s for tenant %s (attempt %s)",
        event_id,
        event.tenant_slug,
        event.attempt_count,
    )

    try:
        result = process_whatsapp_webhook(
            engine=engine,
            event_id=event_id,
            tenant_slug=event.tenant_slug,
            payload=event.payload,
        )
        logger.info(
            "Completed event %s with status %s, %s normalized messages, %s delivery statuses, %s policy handoffs, %s response plans, %s verifications, %s rejected drafts and %s outbound drafts",
            event_id,
            result.status,
            result.normalized_messages,
            result.delivery_statuses,
            result.policy_handoffs,
            result.response_plans,
            result.response_verifications,
            result.rejected_drafts,
            result.outbound_drafts,
        )
    except Exception:
        logger.exception("Processing failed for event %s", event_id)
        if event.attempt_count < MAX_ATTEMPTS:
            delay_seconds = min(2**event.attempt_count, 10)
            logger.warning(
                "Requeueing event %s after %s seconds (%s/%s)",
                event_id,
                delay_seconds,
                event.attempt_count,
                MAX_ATTEMPTS,
            )
            time.sleep(delay_seconds)
            if requeue_failed_webhook_event(engine=engine, event_id=event_id):
                enqueue_webhook_event(event_id)


def run() -> None:
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    logger.info("Stöll Assist webhook worker started")

    while not shutdown_requested.is_set():
        try:
            event_id = dequeue_webhook_event()
            if event_id is None:
                continue
            _process_event(event_id)
        except (RedisError, SQLAlchemyError):
            logger.exception("Worker infrastructure error")
            time.sleep(2)
        except ValueError:
            logger.exception("Discarding malformed queue item")
        except Exception:
            logger.exception("Unexpected worker error")
            time.sleep(1)

    logger.info("Stöll Assist webhook worker stopped")


if __name__ == "__main__":
    run()
