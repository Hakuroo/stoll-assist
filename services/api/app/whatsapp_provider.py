import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from uuid import UUID

import httpx

from app.settings import Settings

logger = logging.getLogger(__name__)


class WhatsAppProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code
        self.latency_ms = latency_ms


class WhatsAppProviderTimeout(WhatsAppProviderError):
    pass


@dataclass(frozen=True)
class WhatsAppSendResult:
    provider_message_id: str
    latency_ms: int


class WhatsAppProvider(ABC):
    @abstractmethod
    def send_text(self, *, to: str, body: str, outbound_id: UUID) -> WhatsAppSendResult:
        raise NotImplementedError


@dataclass
class FakeWhatsAppProvider(WhatsAppProvider):
    provider_message_id: str = "fake-whatsapp-message-id"
    error: WhatsAppProviderError | None = None
    calls: list[dict[str, str]] = field(default_factory=list)

    def send_text(self, *, to: str, body: str, outbound_id: UUID) -> WhatsAppSendResult:
        self.calls.append(
            {
                "to": to,
                "body": body,
                "outbound_id": str(outbound_id),
            }
        )
        if self.error is not None:
            raise self.error
        return WhatsAppSendResult(provider_message_id=self.provider_message_id, latency_ms=1)


class MetaWhatsAppProvider(WhatsAppProvider):
    def __init__(
        self,
        *,
        access_token: str,
        phone_number_id: str,
        api_version: str,
        timeout_seconds: float,
    ) -> None:
        self._access_token = access_token
        self._phone_number_id = phone_number_id
        self._api_version = api_version
        self._timeout_seconds = timeout_seconds

    def send_text(self, *, to: str, body: str, outbound_id: UUID) -> WhatsAppSendResult:
        url = f"https://graph.facebook.com/{self._api_version}/{self._phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        headers = {
            "authorization": f"Bearer {self._access_token}",
            "content-type": "application/json",
        }

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            latency_ms = _latency_ms(started)
            logger.warning(
                "Meta WhatsApp send timed out outbound_id=%s latency_ms=%s",
                outbound_id,
                latency_ms,
            )
            raise WhatsAppProviderTimeout(
                "WhatsApp provider timed out",
                retryable=True,
                latency_ms=latency_ms,
            ) from exc
        except httpx.HTTPError as exc:
            latency_ms = _latency_ms(started)
            logger.warning(
                "Meta WhatsApp send failed outbound_id=%s latency_ms=%s",
                outbound_id,
                latency_ms,
            )
            raise WhatsAppProviderError(
                "WhatsApp provider request failed",
                retryable=True,
                latency_ms=latency_ms,
            ) from exc

        latency_ms = _latency_ms(started)
        logger.info(
            "Meta WhatsApp send completed outbound_id=%s status_code=%s latency_ms=%s",
            outbound_id,
            response.status_code,
            latency_ms,
        )
        if response.status_code >= 400:
            raise WhatsAppProviderError(
                f"WhatsApp provider returned HTTP {response.status_code}",
                retryable=response.status_code >= 500,
                status_code=response.status_code,
                latency_ms=latency_ms,
            )

        try:
            data = response.json()
            provider_message_id = data["messages"][0]["id"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WhatsAppProviderError(
                "WhatsApp provider response did not include a message id",
                retryable=False,
                status_code=response.status_code,
                latency_ms=latency_ms,
            ) from exc

        return WhatsAppSendResult(
            provider_message_id=str(provider_message_id),
            latency_ms=latency_ms,
        )


def get_whatsapp_provider(settings: Settings) -> WhatsAppProvider:
    return MetaWhatsAppProvider(
        access_token=settings.meta_access_token,
        phone_number_id=settings.meta_phone_number_id,
        api_version=settings.meta_api_version,
        timeout_seconds=settings.whatsapp_request_timeout_seconds,
    )


def _latency_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
