from __future__ import annotations

import hmac
from uuid import UUID

from fastapi import APIRouter, Header
from pydantic import BaseModel
from sqlalchemy import select

from teamora_api.config import get_settings
from teamora_api.db import set_tenant_context
from teamora_api.dependencies import SessionDep
from teamora_api.enums import TransferStatus
from teamora_api.errors import ApiError
from teamora_api.models import TransferRequest
from teamora_api.transfer_service import OPEN_TRANSFER_STATUSES, offer_next_operator

router = APIRouter(prefix="/internal/v1/transfers", tags=["internal-live-transfers"])


class TransferTimeoutCommand(BaseModel):
    tenant_id: UUID
    transfer_request_id: UUID
    job_id: UUID


def _verify_service_token(authorization: str | None) -> None:
    settings = get_settings()
    expected = settings.gateway_service_token
    supplied = (authorization or "").removeprefix("Bearer ")
    if expected is None or not hmac.compare_digest(supplied, expected.get_secret_value()):
        raise ApiError(401, "service_authentication_failed", "Service authentication failed")


@router.post("/process-expired", include_in_schema=False)
async def process_expired_offer(
    command: TransferTimeoutCommand,
    session: SessionDep,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    _verify_service_token(authorization)
    await set_tenant_context(session, command.tenant_id)
    transfer = await session.scalar(
        select(TransferRequest)
        .where(
            TransferRequest.tenant_id == command.tenant_id,
            TransferRequest.id == command.transfer_request_id,
        )
        .with_for_update()
    )
    if transfer is None:
        return {"processed": False, "reason": "transfer_not_found"}
    if transfer.status not in OPEN_TRANSFER_STATUSES or transfer.status == TransferStatus.CONNECTED:
        return {"processed": False, "reason": "transfer_closed"}
    before = transfer.attempt_count
    await offer_next_operator(
        session,
        transfer=transfer,
        settings=get_settings(),
        correlation_id=f"background-job:{command.job_id}",
    )
    await session.commit()
    return {
        "processed": transfer.attempt_count != before or transfer.status != TransferStatus.OFFERED,
        "status": transfer.status.value,
        "attempt_count": transfer.attempt_count,
    }
