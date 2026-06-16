from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class NormalizedInboundMessage(BaseModel):
    provider_message_id: str
    sender_wa_id: str
    sender_name: str | None = None
    phone_e164: str | None = None
    message_type: str
    body_text: str | None = None
    provider_timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_message: dict[str, Any]


class NormalizedWhatsAppStatus(BaseModel):
    provider_message_id: str
    status: str
    provider_timestamp: datetime | None = None
    recipient_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None


def normalize_whatsapp_messages(payload: dict[str, Any]) -> list[NormalizedInboundMessage]:
    normalized: list[NormalizedInboundMessage] = []

    for entry in _as_list(payload.get("entry")):
        waba_id = _as_text(entry.get("id"))

        for change in _as_list(entry.get("changes")):
            if change.get("field") != "messages":
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            contact_names = _contact_name_map(value.get("contacts"))

            for message in _as_list(value.get("messages")):
                if not isinstance(message, dict):
                    continue

                provider_message_id = _as_text(message.get("id"))
                sender_wa_id = _as_text(message.get("from"))
                message_type = _as_text(message.get("type")) or "unknown"

                if not provider_message_id or not sender_wa_id:
                    continue

                body_text, content_metadata = _extract_message_content(message, message_type)
                context = message.get("context") if isinstance(message.get("context"), dict) else {}

                message_metadata: dict[str, Any] = {
                    "waba_id": waba_id,
                    "phone_number_id": _as_text(metadata.get("phone_number_id")),
                    "display_phone_number": _as_text(metadata.get("display_phone_number")),
                    **content_metadata,
                }

                context_message_id = _as_text(context.get("id"))
                if context_message_id:
                    message_metadata["context_message_id"] = context_message_id

                normalized.append(
                    NormalizedInboundMessage(
                        provider_message_id=provider_message_id,
                        sender_wa_id=sender_wa_id,
                        sender_name=contact_names.get(sender_wa_id),
                        phone_e164=_to_e164(sender_wa_id),
                        message_type=message_type,
                        body_text=body_text,
                        provider_timestamp=_parse_unix_timestamp(message.get("timestamp")),
                        metadata={key: value for key, value in message_metadata.items() if value is not None},
                        raw_message=message,
                    )
                )

    return normalized


def normalize_whatsapp_statuses(payload: dict[str, Any]) -> list[NormalizedWhatsAppStatus]:
    normalized: list[NormalizedWhatsAppStatus] = []

    for entry in _as_list(payload.get("entry")):
        for change in _as_list(entry.get("changes")):
            if change.get("field") != "messages":
                continue

            value = change.get("value")
            if not isinstance(value, dict):
                continue

            for status in _as_list(value.get("statuses")):
                if not isinstance(status, dict):
                    continue

                provider_message_id = _as_text(status.get("id"))
                delivery_status = _as_text(status.get("status"))
                if not provider_message_id or not delivery_status:
                    continue

                error_code, error_message = _extract_status_error(status.get("errors"))
                normalized.append(
                    NormalizedWhatsAppStatus(
                        provider_message_id=provider_message_id,
                        status=delivery_status,
                        provider_timestamp=_parse_unix_timestamp(status.get("timestamp")),
                        recipient_id=_as_text(status.get("recipient_id")),
                        error_code=error_code,
                        error_message=error_message,
                    )
                )

    return normalized


def _extract_status_error(raw_errors: Any) -> tuple[str | None, str | None]:
    for item in _as_list(raw_errors):
        if not isinstance(item, dict):
            continue
        code = _as_text(item.get("code"))
        title = _as_text(item.get("title"))
        message = _as_text(item.get("message"))
        details = _as_text(item.get("details"))
        safe_parts = [part for part in [title, message, details] if part]
        return code, "; ".join(safe_parts)[:1000] if safe_parts else None
    return None, None


def _extract_message_content(
    message: dict[str, Any], message_type: str
) -> tuple[str | None, dict[str, Any]]:
    content = message.get(message_type)
    if not isinstance(content, dict):
        content = {}

    if message_type == "text":
        return _as_text(content.get("body")), {}

    if message_type == "button":
        return _as_text(content.get("text")), {"button_payload": _as_text(content.get("payload"))}

    if message_type == "interactive":
        interactive_type = _as_text(content.get("type"))
        selection = content.get(interactive_type) if interactive_type else None
        if not isinstance(selection, dict):
            selection = {}
        return _as_text(selection.get("title")) or _as_text(selection.get("id")), {
            "interactive_type": interactive_type,
            "interactive_id": _as_text(selection.get("id")),
            "interactive_description": _as_text(selection.get("description")),
        }

    if message_type in {"image", "video", "document", "audio", "sticker"}:
        return _as_text(content.get("caption")) or _as_text(content.get("filename")), {
            "media_id": _as_text(content.get("id")),
            "mime_type": _as_text(content.get("mime_type")),
            "sha256": _as_text(content.get("sha256")),
            "filename": _as_text(content.get("filename")),
        }

    if message_type == "location":
        latitude = content.get("latitude")
        longitude = content.get("longitude")
        label = _as_text(content.get("name")) or _as_text(content.get("address"))
        if label:
            body = label
        elif latitude is not None and longitude is not None:
            body = f"Ubicación: {latitude}, {longitude}"
        else:
            body = None
        return body, {
            "latitude": latitude,
            "longitude": longitude,
            "location_name": _as_text(content.get("name")),
            "location_address": _as_text(content.get("address")),
        }

    if message_type == "reaction":
        return _as_text(content.get("emoji")), {
            "reaction_message_id": _as_text(content.get("message_id"))
        }

    return None, {}


def _contact_name_map(raw_contacts: Any) -> dict[str, str]:
    names: dict[str, str] = {}
    for contact in _as_list(raw_contacts):
        if not isinstance(contact, dict):
            continue
        wa_id = _as_text(contact.get("wa_id"))
        profile = contact.get("profile") if isinstance(contact.get("profile"), dict) else {}
        name = _as_text(profile.get("name"))
        if wa_id and name:
            names[wa_id] = name
    return names


def _parse_unix_timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(str(value)), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _to_e164(wa_id: str) -> str | None:
    digits = "".join(character for character in wa_id if character.isdigit())
    return f"+{digits}" if digits else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
