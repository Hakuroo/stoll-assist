import hashlib
from typing import Any


def extract_whatsapp_event_identity(payload: dict[str, Any], raw_body: bytes) -> tuple[str, str]:
    """Return a stable event identifier and a broad event kind.

    Meta can batch several message/status objects in one webhook. We combine all
    provider IDs so an identical retry maps to the same database row.
    """
    provider_ids: list[str] = []
    kinds: set[str] = set()

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})

            for message in value.get("messages", []):
                message_id = message.get("id")
                if message_id:
                    provider_ids.append(str(message_id))
                    kinds.add("message")

            for status in value.get("statuses", []):
                status_id = status.get("id")
                if status_id:
                    provider_ids.append(str(status_id))
                    kinds.add("status")

    if provider_ids:
        unique_ids = "|".join(sorted(set(provider_ids)))
        stable_id = "wa:" + hashlib.sha256(unique_ids.encode("utf-8")).hexdigest()
    else:
        stable_id = "sha256:" + hashlib.sha256(raw_body).hexdigest()

    if kinds == {"message"}:
        event_kind = "message"
    elif kinds == {"status"}:
        event_kind = "status"
    elif kinds:
        event_kind = "mixed"
    else:
        event_kind = "unknown"

    return stable_id, event_kind
