from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Header, Request

from teamora_api.audit import write_audit
from teamora_api.config import get_settings
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.domain.simulator import (
    add_simulated_message,
    create_simulated_call,
    finish_simulated_call,
)
from teamora_api.enums import LanguageCode
from teamora_api.errors import ApiError
from teamora_api.schemas.calls import (
    CallDetail,
    CallRead,
    CallSummaryRead,
    SimulatorCallCreate,
    SimulatorMessageCreate,
    SimulatorMessageResponse,
    TranscriptSegmentRead,
)

router = APIRouter(prefix="/simulator", tags=["development-simulator"])


def ensure_simulator() -> None:
    if not get_settings().simulator_available:
        raise ApiError(404, "not_found", "Resource was not found")


def call_read(call: object) -> CallRead:
    return CallRead.model_validate(call, from_attributes=True)


def segment_read(segment: object) -> TranscriptSegmentRead:
    return TranscriptSegmentRead.model_validate(segment, from_attributes=True)


@router.post("/calls", response_model=CallDetail, status_code=201)
async def start_simulation(
    payload: SimulatorCallCreate,
    request: Request,
    session: SessionDep,
    idempotency_key: str = Header(min_length=8, max_length=160, alias="Idempotency-Key"),
    principal: Principal = require_permission("simulator:use"),
) -> CallDetail:
    ensure_simulator()
    if payload.language == LanguageCode.KAA and not get_settings().karakalpak_experimental:
        raise ApiError(
            422, "kaa_feature_disabled", "Karakalpak is experimental and the feature flag is disabled"
        )
    async with session.begin_nested():
        call, disclosure = await create_simulated_call(
            session,
            tenant_id=principal.tenant_id,
            ai_operator_id=payload.ai_operator_id,
            language=payload.language,
            customer_name=payload.customer_name,
            customer_phone=payload.customer_phone,
            idempotency_key=idempotency_key,
        )
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="simulator.call_started",
            resource_type="call",
            resource_id=call.id,
            correlation_id=request.state.correlation_id,
            safe_metadata={"language": payload.language.value, "not_a_phone_call": True},
        )
    await session.commit()
    return CallDetail(**call_read(call).model_dump(), transcript=[segment_read(disclosure)], summary=None)


@router.post("/calls/{call_id}/messages", response_model=SimulatorMessageResponse)
async def send_message(
    call_id: UUID,
    payload: SimulatorMessageCreate,
    request: Request,
    session: SessionDep,
    idempotency_key: str = Header(min_length=8, max_length=160, alias="Idempotency-Key"),
    principal: Principal = require_permission("simulator:use"),
) -> SimulatorMessageResponse:
    ensure_simulator()
    async with session.begin_nested():
        call, customer, assistant, execution, transfer = await add_simulated_message(
            session,
            tenant_id=principal.tenant_id,
            call_id=call_id,
            text=payload.text,
            idempotency_key=idempotency_key,
        )
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="simulator.message_processed",
            resource_type="call",
            resource_id=call.id,
            correlation_id=request.state.correlation_id,
            safe_metadata={"tool": execution.tool_name, "transfer": transfer, "not_a_phone_call": True},
        )
    await session.commit()
    return SimulatorMessageResponse(
        call=call_read(call),
        customer_segment=segment_read(customer),
        assistant_segment=segment_read(assistant),
        tool_name=execution.tool_name,
        tool_result=execution.safe_result or {},
        transfer_requested=transfer,
    )


@router.post("/calls/{call_id}/finish", response_model=CallDetail)
async def finish_simulation(
    call_id: UUID,
    request: Request,
    session: SessionDep,
    idempotency_key: str = Header(min_length=8, max_length=160, alias="Idempotency-Key"),
    principal: Principal = require_permission("simulator:use"),
) -> CallDetail:
    ensure_simulator()
    async with session.begin_nested():
        call, summary, _usage = await finish_simulated_call(
            session,
            tenant_id=principal.tenant_id,
            call_id=call_id,
            idempotency_key=idempotency_key,
        )
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="simulator.call_finished",
            resource_type="call",
            resource_id=call.id,
            correlation_id=request.state.correlation_id,
            safe_metadata={"result": summary.result, "not_a_phone_call": True},
        )
    await session.commit()
    from sqlalchemy import select

    from teamora_api.models import TranscriptSegment

    segments = list(
        await session.scalars(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.tenant_id == principal.tenant_id,
                TranscriptSegment.call_id == call.id,
            )
            .order_by(TranscriptSegment.sequence)
        )
    )
    return CallDetail(
        **call_read(call).model_dump(),
        transcript=[segment_read(segment) for segment in segments],
        summary=CallSummaryRead(
            summary=summary.summary,
            topics=summary.topics,
            result=summary.result,
            generated_by=summary.generated_by,
        ),
    )
