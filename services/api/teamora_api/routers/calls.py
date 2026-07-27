from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.errors import ApiError
from teamora_api.models import Call, CallSummary, TranscriptSegment
from teamora_api.schemas.calls import (
    CallDetail,
    CallRead,
    CallSummaryRead,
    TranscriptSegmentRead,
)
from teamora_api.schemas.common import Page

router = APIRouter(prefix="/calls", tags=["calls"])


def serialize_call(call: Call) -> CallRead:
    return CallRead(
        id=call.id,
        channel=call.channel,
        status=call.status,
        language=call.language,
        customer_id=call.customer_id,
        ai_operator_id=call.ai_operator_id,
        started_at=call.started_at,
        ended_at=call.ended_at,
        duration_seconds=call.duration_seconds,
        transfer_reason=call.transfer_reason,
        is_demo=call.is_demo,
    )


@router.get("", response_model=Page[CallRead])
async def list_calls(
    session: SessionDep,
    principal: Principal = require_permission("calls:read"),
    limit: int = 50,
    offset: int = 0,
) -> Page[CallRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    where = Call.tenant_id == principal.tenant_id
    total = int(await session.scalar(select(func.count()).select_from(Call).where(where)) or 0)
    calls = list(
        await session.scalars(
            select(Call).where(where).order_by(Call.created_at.desc()).limit(limit).offset(offset)
        )
    )
    return Page(items=[serialize_call(call) for call in calls], total=total, limit=limit, offset=offset)


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    session: SessionDep,
    principal: Principal = require_permission("calls:read"),
) -> CallDetail:
    call = await session.scalar(select(Call).where(Call.tenant_id == principal.tenant_id, Call.id == call_id))
    if call is None:
        raise ApiError(404, "call_not_found", "Call was not found")
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
    summary = await session.scalar(
        select(CallSummary).where(
            CallSummary.tenant_id == principal.tenant_id, CallSummary.call_id == call.id
        )
    )
    base = serialize_call(call).model_dump()
    return CallDetail(
        **base,
        transcript=[
            TranscriptSegmentRead(
                id=segment.id,
                sequence=segment.sequence,
                speaker=segment.speaker,
                language=segment.language,
                text=segment.text,
                created_at=segment.created_at,
            )
            for segment in segments
        ],
        summary=(
            CallSummaryRead(
                summary=summary.summary,
                topics=summary.topics,
                result=summary.result,
                generated_by=summary.generated_by,
            )
            if summary
            else None
        ),
    )
