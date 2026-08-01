from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter
from sqlalchemy import func, select

from teamora_api.call_state import CAPACITY_CALL_STATES
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import CallStatus
from teamora_api.models import Call, UsageRecord
from teamora_api.schemas.calls import DashboardResponse

router = APIRouter(tags=["analytics"])


@router.get("/analytics/dashboard", response_model=DashboardResponse)
async def dashboard(
    session: SessionDep,
    principal: Principal = require_permission("analytics:read"),
) -> DashboardResponse:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    call_filter = Call.tenant_id == principal.tenant_id
    metrics = (
        await session.execute(
            select(
                func.count(Call.id).filter(Call.status.in_(CAPACITY_CALL_STATES)),
                func.count(Call.id).filter(Call.started_at >= today),
                func.count(Call.id).filter(Call.status == CallStatus.COMPLETED),
                func.count(Call.id).filter(Call.transfer_reason.is_not(None)),
                func.coalesce(func.avg(Call.duration_seconds).filter(Call.status == CallStatus.COMPLETED), 0),
                func.count(Call.id).filter(Call.is_demo.is_(True)),
            ).where(call_filter)
        )
    ).one()
    language_rows = (
        await session.execute(
            select(Call.language, func.count(Call.id))
            .where(call_filter, Call.language.is_not(None))
            .group_by(Call.language)
        )
    ).all()
    usage = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageRecord.quantity), 0),
                func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0),
            ).where(
                UsageRecord.tenant_id == principal.tenant_id,
                UsageRecord.metric == "ai_minutes",
            )
        )
    ).one()
    return DashboardResponse(
        active_calls=int(metrics[0]),
        calls_today=int(metrics[1]),
        completed_calls=int(metrics[2]),
        transfers=int(metrics[3]),
        average_duration_seconds=float(metrics[4]),
        used_ai_minutes=Decimal(usage[0]),
        estimated_cost_usd=Decimal(usage[1]),
        language_breakdown={language.value: int(count) for language, count in language_rows},
        is_demo=bool(metrics[5]),
    )
