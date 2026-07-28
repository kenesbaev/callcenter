from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.domain.knowledge import search_knowledge
from teamora_api.enums import (
    CallChannel,
    CallStatus,
    LanguageCode,
    OperatorVersionStatus,
    ToolExecutionStatus,
    TranscriptSpeaker,
    TransferStatus,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    AiOperator,
    AiOperatorVersion,
    Call,
    CallEvent,
    CallSummary,
    Customer,
    CustomerContact,
    ToolExecution,
    TranscriptSegment,
    TransferRequest,
    UsageRecord,
)

DISCLOSURES: dict[LanguageCode, str] = {
    LanguageCode.RU: (
        "Здравствуйте! Вы разговариваете с виртуальным помощником K-Line. "
        "Разговор может записываться. Чем могу помочь?"
    ),
    LanguageCode.EN: (
        "Hello! You are speaking with a K-Line virtual assistant. This call may be recorded. How can I help?"
    ),
    LanguageCode.UZ: (
        "Assalomu alaykum! Siz K-Line virtual yordamchisi bilan gaplashyapsiz. "
        "Suhbat yozib olinishi mumkin. Qanday yordam bera olaman?"
    ),
    LanguageCode.KAA: (
        "Sálem! Siz K-Line virtual járdemshisi menen sóylesip atırsız. Sóylesiw jazıp alınıwı múmkin."
    ),
}

HANDOFF_PHRASES: dict[LanguageCode, str] = {
    LanguageCode.RU: "Я передам обращение живому оператору. Пожалуйста, оставайтесь на линии.",
    LanguageCode.EN: "I will transfer your request to a human operator. Please stay on the line.",
    LanguageCode.UZ: "Murojaatingizni jonli operatorga o'tkazaman. Iltimos, liniyada qoling.",
    LanguageCode.KAA: "Sizi janlı operatorǵa ótkeremen. Iltimas, liniyada qalıń.",
}

UNKNOWN_PHRASES: dict[LanguageCode, str] = {
    LanguageCode.RU: "В базе знаний нет подтверждённого ответа. Я передам вопрос живому оператору.",
    LanguageCode.EN: (
        "I do not have a verified answer in the knowledge base. I will hand this to a human operator."
    ),
    LanguageCode.UZ: "Bilimlar bazasida tasdiqlangan javob yo'q. Savolni jonli operatorga o'tkazaman.",
    LanguageCode.KAA: "Bilim bazasında tastıyıqlanǵan juwap joq. Sorawdı janlı operatorǵa ótkeremen.",
}


def asks_for_human(text: str) -> bool:
    normalized = text.lower()
    phrases = (
        "оператор",
        "человек",
        "живой сотрудник",
        "human",
        "agent",
        "operatorga",
        "odam",
        "janlı operator",
    )
    return any(phrase in normalized for phrase in phrases)


async def next_sequence(session: AsyncSession, tenant_id: UUID, call_id: UUID) -> int:
    value = await session.scalar(
        select(func.max(TranscriptSegment.sequence)).where(
            TranscriptSegment.tenant_id == tenant_id, TranscriptSegment.call_id == call_id
        )
    )
    return int(value or 0) + 1


async def create_simulated_call(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    ai_operator_id: UUID,
    language: LanguageCode,
    customer_name: str | None,
    customer_phone: str,
    idempotency_key: str,
) -> tuple[Call, TranscriptSegment]:
    external_id = "sim:" + hashlib.sha256(f"{tenant_id}:{idempotency_key}".encode()).hexdigest()
    existing = await session.scalar(
        select(Call).where(Call.tenant_id == tenant_id, Call.external_call_id == external_id)
    )
    if existing is not None:
        disclosure = await session.scalar(
            select(TranscriptSegment).where(
                TranscriptSegment.tenant_id == tenant_id,
                TranscriptSegment.call_id == existing.id,
                TranscriptSegment.sequence == 1,
            )
        )
        if disclosure is None:
            raise ApiError(409, "idempotency_conflict", "Existing simulator call is incomplete")
        return existing, disclosure

    row = (
        await session.execute(
            select(AiOperator, AiOperatorVersion)
            .join(AiOperatorVersion, AiOperatorVersion.id == AiOperator.active_version_id)
            .where(
                AiOperator.tenant_id == tenant_id,
                AiOperator.id == ai_operator_id,
                AiOperator.is_active.is_(True),
                AiOperatorVersion.tenant_id == tenant_id,
                AiOperatorVersion.status == OperatorVersionStatus.PUBLISHED,
            )
        )
    ).one_or_none()
    if row is None:
        raise ApiError(409, "operator_not_published", "Publish the AI operator before starting a simulation")
    operator, version = row
    if language.value not in version.allowed_languages:
        raise ApiError(422, "language_not_allowed", "This AI operator does not allow the selected language")

    contact = await session.scalar(
        select(CustomerContact).where(
            CustomerContact.tenant_id == tenant_id,
            CustomerContact.project_id == operator.project_id,
            CustomerContact.kind == "phone",
            CustomerContact.normalized_value == customer_phone,
        )
    )
    customer: Customer | None
    if contact is None:
        customer = Customer(
            tenant_id=tenant_id,
            project_id=operator.project_id,
            display_name=customer_name,
            preferred_language=language,
        )
        session.add(customer)
        await session.flush()
        session.add(
            CustomerContact(
                tenant_id=tenant_id,
                project_id=operator.project_id,
                customer_id=customer.id,
                kind="phone",
                normalized_value=customer_phone,
                display_value=customer_phone,
                is_primary=True,
            )
        )
    else:
        customer = await session.get(Customer, contact.customer_id)
        if customer is None or customer.tenant_id != tenant_id:
            raise ApiError(404, "customer_not_found", "Customer was not found")

    now = datetime.now(UTC)
    call = Call(
        tenant_id=tenant_id,
        project_id=operator.project_id,
        external_call_id=external_id,
        channel=CallChannel.DEVELOPMENT_SIMULATOR,
        status=CallStatus.ACTIVE,
        direction="inbound",
        customer_id=customer.id,
        ai_operator_id=ai_operator_id,
        ai_operator_version_id=version.id,
        language=language,
        started_at=now,
        answered_at=now,
        is_demo=True,
    )
    session.add(call)
    await session.flush()
    disclosure = TranscriptSegment(
        tenant_id=tenant_id,
        call_id=call.id,
        sequence=1,
        speaker=TranscriptSpeaker.AI,
        language=language,
        text=version.greeting_by_language.get(language.value) or DISCLOSURES[language],
    )
    session.add(disclosure)
    session.add(
        CallEvent(
            tenant_id=tenant_id,
            call_id=call.id,
            event_type="simulation.started",
            sequence=1,
            safe_payload={"language": language.value, "channel": "development_simulator"},
        )
    )
    await session.flush()
    return call, disclosure


async def add_simulated_message(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    call_id: UUID,
    text: str,
    idempotency_key: str,
) -> tuple[Call, TranscriptSegment, TranscriptSegment, ToolExecution, bool]:
    call = await session.scalar(select(Call).where(Call.tenant_id == tenant_id, Call.id == call_id))
    if call is None:
        raise ApiError(404, "call_not_found", "Call was not found")
    if call.channel != CallChannel.DEVELOPMENT_SIMULATOR or call.status != CallStatus.ACTIVE:
        raise ApiError(409, "call_not_active", "Only an active development simulation accepts messages")
    language = call.language or LanguageCode.RU
    execution_key = f"sim-message:{call.id}:{idempotency_key}"
    existing_execution = await session.scalar(
        select(ToolExecution).where(
            ToolExecution.tenant_id == tenant_id, ToolExecution.idempotency_key == execution_key
        )
    )
    if existing_execution is not None:
        raise ApiError(409, "idempotency_replayed", "This simulator message was already processed")

    sequence = await next_sequence(session, tenant_id, call.id)
    customer_segment = TranscriptSegment(
        tenant_id=tenant_id,
        call_id=call.id,
        sequence=sequence,
        speaker=TranscriptSpeaker.CUSTOMER,
        language=language,
        text=text,
    )
    session.add(customer_segment)

    transfer_requested = asks_for_human(text)
    matches = (
        []
        if transfer_requested
        else await search_knowledge(session, tenant_id=tenant_id, language=language, query=text)
    )
    tool_name = "request_human_operator" if transfer_requested or not matches else "search_knowledge"
    safe_result: dict[str, object]
    if matches:
        answer = matches[0].content
        safe_result = {"matched_document_ids": [str(document.id) for document in matches]}
    else:
        transfer_requested = True
        answer = HANDOFF_PHRASES[language] if asks_for_human(text) else UNKNOWN_PHRASES[language]
        safe_result = {
            "transfer_requested": True,
            "reason": "explicit_request" if asks_for_human(text) else "knowledge_gap",
        }
        call.status = CallStatus.TRANSFERRING
        call.transfer_reason = str(safe_result["reason"])
        session.add(
            TransferRequest(
                tenant_id=tenant_id,
                call_id=call.id,
                status=TransferStatus.REQUESTED,
                reason=str(safe_result["reason"]),
                summary="Development simulator transfer request",
                requested_at=datetime.now(UTC),
            )
        )

    arguments = (
        {"query": text[:500]} if tool_name == "search_knowledge" else {"reason": str(safe_result["reason"])}
    )
    arguments_hash = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()
    execution = ToolExecution(
        tenant_id=tenant_id,
        call_id=call.id,
        tool_name=tool_name,
        idempotency_key=execution_key,
        arguments_hash=arguments_hash,
        safe_arguments=arguments,
        safe_result=safe_result,
        status=ToolExecutionStatus.SUCCEEDED,
        duration_ms=0,
    )
    assistant_segment = TranscriptSegment(
        tenant_id=tenant_id,
        call_id=call.id,
        sequence=sequence + 1,
        speaker=TranscriptSpeaker.AI,
        language=language,
        text=answer,
    )
    session.add_all([execution, assistant_segment])
    event_sequence = (
        int(
            await session.scalar(
                select(func.max(CallEvent.sequence)).where(
                    CallEvent.tenant_id == tenant_id, CallEvent.call_id == call.id
                )
            )
            or 0
        )
        + 1
    )
    session.add(
        CallEvent(
            tenant_id=tenant_id,
            call_id=call.id,
            event_type=f"tool.{tool_name}",
            sequence=event_sequence,
            safe_payload={"status": "succeeded", "transfer_requested": transfer_requested},
        )
    )
    await session.flush()
    return call, customer_segment, assistant_segment, execution, transfer_requested


async def finish_simulated_call(
    session: AsyncSession, *, tenant_id: UUID, call_id: UUID, idempotency_key: str
) -> tuple[Call, CallSummary, UsageRecord]:
    call = await session.scalar(select(Call).where(Call.tenant_id == tenant_id, Call.id == call_id))
    if call is None:
        raise ApiError(404, "call_not_found", "Call was not found")
    existing_summary = await session.scalar(
        select(CallSummary).where(CallSummary.tenant_id == tenant_id, CallSummary.call_id == call.id)
    )
    if existing_summary is not None:
        usage = await session.scalar(
            select(UsageRecord).where(
                UsageRecord.tenant_id == tenant_id,
                UsageRecord.idempotency_key == f"sim-finish:{call.id}:{idempotency_key}",
            )
        )
        if usage is None:
            raise ApiError(409, "call_finish_incomplete", "Existing call finalization is incomplete")
        return call, existing_summary, usage

    if call.status not in {CallStatus.ACTIVE, CallStatus.TRANSFERRING}:
        raise ApiError(409, "call_not_active", "Call cannot be finalized from its current state")
    now = datetime.now(UTC)
    call.ended_at = now
    call.duration_seconds = max(1, int((now - (call.started_at or now)).total_seconds()))
    call.status = CallStatus.COMPLETED
    segments = list(
        await session.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.tenant_id == tenant_id, TranscriptSegment.call_id == call.id)
            .order_by(TranscriptSegment.sequence)
        )
    )
    customer_texts = [segment.text for segment in segments if segment.speaker == TranscriptSpeaker.CUSTOMER]
    result = "transferred" if call.transfer_reason else "resolved_with_knowledge"
    summary_text = (
        f"Development simulation completed with {len(customer_texts)} customer message(s). Result: {result}."
    )
    summary = CallSummary(
        tenant_id=tenant_id,
        call_id=call.id,
        summary=summary_text,
        topics=[],
        result=result,
        generated_by="development_deterministic",
    )
    minutes = Decimal(str(max(1, math.ceil(call.duration_seconds / 60))))
    usage = UsageRecord(
        tenant_id=tenant_id,
        call_id=call.id,
        metric="ai_minutes",
        quantity=minutes,
        unit="minute",
        estimated_cost_usd=minutes * Decimal("0.08"),
        idempotency_key=f"sim-finish:{call.id}:{idempotency_key}",
        occurred_at=now,
    )
    session.add_all([summary, usage])
    await session.flush()
    return call, summary, usage
