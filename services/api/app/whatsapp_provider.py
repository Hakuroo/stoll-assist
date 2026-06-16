import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from threading import Lock
from uuid import UUID

import httpx

from app.settings import Settings

logger = logging.getLogger(__name__)


class WhatsAppProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "PROVIDER_ERROR",
        retryable: bool = False,
        status_code: int | None = None,
        latency_ms: int | None = None,
        request_started: bool = False,
        provider_message_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.retryable = retryable
        self.status_code = status_code
        self.latency_ms = latency_ms
        self.request_started = request_started
        self.provider_message_id = provider_message_id


class WhatsAppProviderTimeout(WhatsAppProviderError):
    pass


class WhatsAppProviderPreSendError(WhatsAppProviderError):
    pass


class WhatsAppProviderUncertainError(WhatsAppProviderError):
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
    delay_seconds: float = 0.0
    calls: list[dict[str, str]] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def send_text(self, *, to: str, body: str, outbound_id: UUID) -> WhatsAppSendResult:
        with self._lock:
            self.calls.append(
                {
                    "to": to,
                    "body": body,
                    "outbound_id": str(outbound_id),
                }
            )
        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)
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
        if not self._access_token or not self._phone_number_id or not self._api_version:
            raise WhatsAppProviderPreSendError(
                "WhatsApp provider is not configured",
                error_type="CONFIGURATION_ERROR",
                retryable=False,
            )

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
        except (httpx.ConnectTimeout, httpx.ConnectError) as exc:
            latency_ms = _latency_ms(started)
            logger.warning(
                "Meta WhatsApp send failed before request outbound_id=%s latency_ms=%s",
                outbound_id,
                latency_ms,
            )
            raise WhatsAppProviderPreSendError(
                "WhatsApp provider connection failed before sending",
                error_type="CONNECTION_NOT_ESTABLISHED",
                retryable=True,
                latency_ms=latency_ms,
            ) from exc
        except httpx.TimeoutException as exc:
            latency_ms = _latency_ms(started)
            logger.warning(
                "Meta WhatsApp send timed out after request started outbound_id=%s latency_ms=%s",
                outbound_id,
                latency_ms,
            )
            raise WhatsAppProviderTimeout(
                "WhatsApp provider timed out after request started",
                error_type="REQUEST_TIMEOUT_UNKNOWN",
                retryable=False,
                latency_ms=latency_ms,
                request_started=True,
            ) from exc
        except httpx.TransportError as exc:
            latency_ms = _latency_ms(started)
            logger.warning(
                "Meta WhatsApp send disconnected after request started outbound_id=%s latency_ms=%s",
                outbound_id,
                latency_ms,
            )
            raise WhatsAppProviderUncertainError(
                "WhatsApp provider connection was lost after request started",
                error_type="REQUEST_DISCONNECTED_UNKNOWN",
                retryable=False,
                latency_ms=latency_ms,
                request_started=True,
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
                error_type="META_HTTP_ERROR",
                retryable=response.status_code >= 500,
                status_code=response.status_code,
                latency_ms=latency_ms,
                request_started=True,
            )

        try:
            data = response.json()
            provider_message_id = data["messages"][0]["id"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise WhatsAppProviderError(
                "WhatsApp provider response did not include a message id",
                error_type="INVALID_PROVIDER_RESPONSE",
                retryable=False,
                status_code=response.status_code,
                latency_ms=latency_ms,
                request_started=True,
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
