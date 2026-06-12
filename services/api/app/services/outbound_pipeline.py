from sqlalchemy import Engine

from app.repositories.outbox import StoredOutboundMessage, create_outbound_draft
from app.repositories.response_verifications import StoredResponseVerification


def stage_verified_response(
    *,
    engine: Engine,
    tenant_slug: str,
    verification: StoredResponseVerification,
) -> StoredOutboundMessage | None:
    if verification.status != "APPROVED":
        return None
    return create_outbound_draft(
        engine=engine,
        tenant_slug=tenant_slug,
        verification_id=verification.verification_id,
    )
