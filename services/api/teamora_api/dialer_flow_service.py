from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.call_flow_service import ACTION_NODE_TYPES, definition_from_storage, localized
from teamora_api.customer_service import validate_custom_fields
from teamora_api.dependencies import Principal
from teamora_api.enums import (
    TaskPriority,
    TaskSource,
    TaskType,
    TelephonyCommandName,
    TransferStatus,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    CallFlow,
    CallFlowExecution,
    CallFlowExecutionStep,
    CallFlowVersion,
    Customer,
    CustomerFieldDefinition,
    TransferRequest,
)
from teamora_api.project_access import resolve_project
from teamora_api.schemas.call_flows import CallFlowNode
from teamora_api.schemas.dialer import (
    DialerFlowExecutionRead,
    DialerFlowStepRead,
    DialerFlowStepRequest,
)
from teamora_api.task_service import create_task_record
from teamora_api.telephony.service import TelephonyService


def _step_read(step: CallFlowExecutionStep) -> DialerFlowStepRead:
    return DialerFlowStepRead(
        id=step.id,
        sequence=step.sequence,
        node_id=step.node_id,
        system_key=step.system_key,
        node_type=step.node_type,
        language_code=step.language_code,
        text_snapshot=step.text_snapshot,
        hint_snapshot=step.hint_snapshot,
        selected_answer_key=step.selected_answer_key,
        selected_answer_label=step.selected_answer_label,
        input_value=step.input_value,
        next_node_id=step.next_node_id,
        action_status=step.action_status,
        occurred_at=step.occurred_at,
    )


async def _runtime_context(
    session: AsyncSession,
    execution: CallFlowExecution,
) -> tuple[CallFlow, CallFlowVersion, dict[UUID, CallFlowNode]]:
    version = await session.scalar(
        select(CallFlowVersion).where(
            CallFlowVersion.tenant_id == execution.tenant_id,
            CallFlowVersion.project_id == execution.project_id,
            CallFlowVersion.id == execution.call_flow_version_id,
        )
    )
    if version is None:
        raise ApiError(409, "call_flow_version_missing", "Зафиксированная версия сценария недоступна")
    flow = await session.scalar(
        select(CallFlow).where(
            CallFlow.tenant_id == execution.tenant_id,
            CallFlow.project_id == execution.project_id,
            CallFlow.id == version.call_flow_id,
        )
    )
    if flow is None:
        raise ApiError(409, "call_flow_missing", "Сценарий звонка недоступен")
    definition = definition_from_storage(version.definition)
    return flow, version, {node.id: node for node in definition.nodes}


async def serialize_execution(
    session: AsyncSession,
    execution: CallFlowExecution,
) -> DialerFlowExecutionRead:
    flow, _, nodes = await _runtime_context(session, execution)
    steps = list(
        await session.scalars(
            select(CallFlowExecutionStep)
            .where(
                CallFlowExecutionStep.tenant_id == execution.tenant_id,
                CallFlowExecutionStep.execution_id == execution.id,
            )
            .order_by(CallFlowExecutionStep.sequence)
        )
    )
    return DialerFlowExecutionRead(
        id=execution.id,
        call_id=execution.call_id,
        call_flow_version_id=execution.call_flow_version_id,
        current_node=nodes.get(execution.current_node_id) if execution.current_node_id else None,
        status=execution.status,
        language_code=execution.language_code,
        language_codes=list(flow.language_codes),
        state_version=execution.state_version,
        values=dict(execution.values),
        steps=[_step_read(step) for step in steps],
        started_at=execution.started_at,
        completed_at=execution.completed_at,
    )


async def ensure_execution_for_call(
    session: AsyncSession,
    principal: Principal,
    call: Call,
) -> CallFlowExecution | None:
    if call.call_flow_version_id is None:
        return None
    if call.operator_user_id != principal.user_id or call.tenant_id != principal.tenant_id:
        raise ApiError(404, "call_not_found", "Звонок не найден")
    if call.customer_id is None:
        raise ApiError(409, "call_customer_missing", "У звонка отсутствует клиент")
    existing = await session.scalar(
        select(CallFlowExecution).where(
            CallFlowExecution.tenant_id == principal.tenant_id,
            CallFlowExecution.call_id == call.id,
        )
    )
    if existing is not None:
        return existing
    version = await session.scalar(
        select(CallFlowVersion).where(
            CallFlowVersion.tenant_id == principal.tenant_id,
            CallFlowVersion.project_id == call.project_id,
            CallFlowVersion.id == call.call_flow_version_id,
        )
    )
    if version is None:
        raise ApiError(409, "call_flow_version_missing", "Зафиксированная версия сценария недоступна")
    flow = await session.scalar(
        select(CallFlow).where(
            CallFlow.tenant_id == principal.tenant_id,
            CallFlow.project_id == call.project_id,
            CallFlow.id == version.call_flow_id,
        )
    )
    if flow is None:
        raise ApiError(409, "call_flow_missing", "Сценарий звонка недоступен")
    definition = definition_from_storage(version.definition)
    starts = [node for node in definition.nodes if node.node_type == "start"]
    if len(starts) != 1:
        raise ApiError(
            409, "call_flow_runtime_invalid", "Опубликованный сценарий не имеет одного начального узла"
        )
    customer = await session.scalar(
        select(Customer).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.project_id == call.project_id,
            Customer.id == call.customer_id,
        )
    )
    preferred = customer.preferred_language.value if customer and customer.preferred_language else None
    language = call.language.value if call.language else preferred or flow.default_language_code
    if language not in flow.language_codes:
        language = flow.default_language_code
    execution = CallFlowExecution(
        tenant_id=principal.tenant_id,
        project_id=call.project_id,
        call_id=call.id,
        customer_id=call.customer_id,
        operator_user_id=principal.user_id,
        call_flow_version_id=version.id,
        current_node_id=starts[0].id,
        status="active",
        language_code=language,
        state_version=1,
        values={},
        started_at=datetime.now(UTC),
    )
    session.add(execution)
    await session.flush()
    return execution


def _action_due_at(node: CallFlowNode) -> datetime:
    raw = node.action_config.get("due_minutes", 60)
    minutes = raw if isinstance(raw, int) and 0 <= raw <= 525_600 else 60
    return datetime.now(UTC) + timedelta(minutes=minutes)


async def _perform_action(
    session: AsyncSession,
    *,
    principal: Principal,
    call: Call,
    customer: Customer,
    execution: CallFlowExecution,
    node: CallFlowNode,
    value: object | None,
    idempotency_key: str,
    correlation_id: str,
    allow_real_transfer: bool,
) -> str:
    if node.node_type == "update_customer_field":
        if node.customer_field_definition_id is None:
            raise ApiError(422, "call_flow_customer_field_missing", "Для действия не выбрано поле клиента")
        definition = await session.scalar(
            select(CustomerFieldDefinition).where(
                CustomerFieldDefinition.tenant_id == principal.tenant_id,
                CustomerFieldDefinition.project_id == call.project_id,
                CustomerFieldDefinition.id == node.customer_field_definition_id,
            )
        )
        if definition is None:
            raise ApiError(422, "call_flow_customer_field_invalid", "Поле не принадлежит проекту звонка")
        submitted_value = value if value is not None else node.action_config.get("value")
        customer.custom_fields = await validate_custom_fields(
            session,
            tenant_id=principal.tenant_id,
            project_id=call.project_id,
            submitted={definition.key: submitted_value},
            existing=customer.custom_fields,
        )
        execution.values = {**execution.values, definition.key: submitted_value}
        return "completed"
    if node.node_type in {"create_task", "create_callback"}:
        task_type = TaskType.CALLBACK if node.node_type == "create_callback" else TaskType.MANUAL
        priority_value = node.action_config.get("priority", TaskPriority.NORMAL.value)
        try:
            priority = TaskPriority(str(priority_value))
        except ValueError:
            priority = TaskPriority.NORMAL
        title = str(node.action_config.get("title") or node.name)[:240]
        description = str(node.action_config.get("description") or "")[:8000]
        await create_task_record(
            session,
            tenant_id=principal.tenant_id,
            project_id=call.project_id,
            customer_id=customer.id,
            created_by_user_id=principal.user_id,
            task_type=task_type,
            title=title,
            description=description,
            priority=priority,
            due_at=_action_due_at(node),
            assigned_user_id=principal.user_id,
            source=TaskSource.CALL_FLOW,
            correlation_id=correlation_id,
            call_id=call.id,
            idempotency_key=f"flow:{execution.id}:{node.id}:{idempotency_key}",
        )
        return "completed"
    if node.node_type == "transfer_request":
        if not allow_real_transfer:
            existing = await session.scalar(
                select(TransferRequest).where(
                    TransferRequest.tenant_id == call.tenant_id,
                    TransferRequest.call_id == call.id,
                    TransferRequest.status == TransferStatus.REQUESTED,
                )
            )
            if existing is None:
                session.add(
                    TransferRequest(
                        tenant_id=call.tenant_id,
                        project_id=call.project_id,
                        call_id=call.id,
                        status=TransferStatus.REQUESTED,
                        reason=str(node.action_config.get("reason") or "ai_call_flow_requested")[:500],
                        summary="AI Call Flow requested human assistance",
                        language_code=(call.language.value if call.language else execution.language_code),
                        routing_strategy="longest_idle",
                        destination_type="browser",
                        context_snapshot={"flow_node_id": str(node.id)},
                        attempt_count=0,
                        max_attempts=3,
                        idempotency_key=f"flow-transfer:{execution.id}:{node.id}:{idempotency_key}",
                        lock_version=1,
                        requested_at=datetime.now(UTC),
                    )
                )
            return "requested"
        project = await resolve_project(session, principal, call.project_id)
        destination = str(node.action_config.get("destination") or "operator-queue")[:120]
        selection = await TelephonyService().selection_for_call(session, call=call, project=project)
        await TelephonyService().execute(
            session,
            call=call,
            project=project,
            selection=selection,
            command_name=TelephonyCommandName.TRANSFER,
            correlation_id=correlation_id,
            actor_user_id=principal.user_id,
            idempotency_key=f"flow:{execution.id}:{node.id}:{idempotency_key}",
            parameters={"destination": destination, "reason": "call_flow_operator_confirmed"},
        )
        return "completed"
    return "not_applicable"


async def advance_execution(
    session: AsyncSession,
    *,
    principal: Principal,
    call: Call,
    payload: DialerFlowStepRequest,
    idempotency_key: str,
    correlation_id: str,
    allow_real_transfer: bool = True,
) -> CallFlowExecution:
    execution = await session.scalar(
        select(CallFlowExecution)
        .where(
            CallFlowExecution.tenant_id == principal.tenant_id,
            CallFlowExecution.call_id == call.id,
            CallFlowExecution.operator_user_id == principal.user_id,
        )
        .with_for_update()
    )
    if execution is None:
        created = await ensure_execution_for_call(session, principal, call)
        if created is None:
            raise ApiError(409, "call_flow_not_attached", "К звонку не прикреплён сценарий")
        execution = created
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtextextended(f"{execution.id}:{idempotency_key}", 0)))
    )
    replay = await session.scalar(
        select(CallFlowExecutionStep).where(
            CallFlowExecutionStep.tenant_id == principal.tenant_id,
            CallFlowExecutionStep.execution_id == execution.id,
            CallFlowExecutionStep.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        return execution
    if execution.status != "active":
        raise ApiError(409, "call_flow_execution_completed", "Сценарий уже завершён")
    if execution.state_version != payload.expected_state_version:
        raise ApiError(409, "call_flow_execution_conflict", "Сценарий уже изменён в другой вкладке")
    if execution.current_node_id != payload.node_id:
        raise ApiError(409, "call_flow_node_conflict", "Текущий узел сценария изменился")
    flow, _, nodes = await _runtime_context(session, execution)
    node = nodes.get(payload.node_id)
    if node is None:
        raise ApiError(422, "call_flow_node_invalid", "Узел не принадлежит версии сценария звонка")
    language = payload.language_code or execution.language_code
    if language not in flow.language_codes:
        raise ApiError(422, "call_flow_language_unavailable", "Язык не включён в сценарий")
    if node.node_type in ACTION_NODE_TYPES and not payload.confirm_action:
        raise ApiError(422, "call_flow_action_confirmation_required", "Подтвердите выполнение действия")
    selected_label: str | None = None
    target = node.next_node_id
    if node.node_type in {"customer_question", "choice"}:
        answer = next((item for item in node.answers if item.key == payload.answer_key), None)
        if answer is None:
            target = node.fallback_node_id
            if target is None:
                raise ApiError(422, "call_flow_answer_invalid", "Выберите допустимый вариант ответа")
        else:
            target = answer.next_node_id
            selected_label = localized(answer.label_by_language, language, flow.default_language_code)
    if node.node_type == "value_input" and node.is_required and payload.value in (None, ""):
        raise ApiError(422, "call_flow_value_required", "Введите обязательное значение")
    if node.node_type != "end" and (target is None or target not in nodes):
        raise ApiError(422, "call_flow_transition_invalid", "Переход сценария недоступен")

    customer = await session.scalar(
        select(Customer)
        .where(
            Customer.tenant_id == principal.tenant_id,
            Customer.project_id == call.project_id,
            Customer.id == execution.customer_id,
        )
        .with_for_update()
    )
    if customer is None:
        raise ApiError(404, "customer_not_found", "Клиент не найден")
    action_status: str | None = None
    if node.node_type in ACTION_NODE_TYPES:
        action_status = await _perform_action(
            session,
            principal=principal,
            call=call,
            customer=customer,
            execution=execution,
            node=node,
            value=payload.value,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            allow_real_transfer=allow_real_transfer,
        )
    if node.node_type == "value_input":
        execution.values = {**execution.values, node.system_key: payload.value}
    sequence = (
        int(
            await session.scalar(
                select(func.max(CallFlowExecutionStep.sequence)).where(
                    CallFlowExecutionStep.execution_id == execution.id
                )
            )
            or 0
        )
        + 1
    )
    now = datetime.now(UTC)
    session.add(
        CallFlowExecutionStep(
            tenant_id=principal.tenant_id,
            execution_id=execution.id,
            sequence=sequence,
            node_id=node.id,
            system_key=node.system_key,
            node_type=node.node_type,
            language_code=language,
            text_snapshot=localized(node.text_by_language, language, flow.default_language_code),
            hint_snapshot=localized(node.hint_by_language, language, flow.default_language_code),
            selected_answer_key=payload.answer_key,
            selected_answer_label=selected_label,
            input_value=payload.value,
            next_node_id=target,
            action_status=action_status,
            actor_user_id=principal.user_id,
            idempotency_key=idempotency_key,
            occurred_at=now,
        )
    )
    execution.language_code = language
    execution.state_version += 1
    if node.node_type == "end":
        execution.status = "completed"
        execution.current_node_id = None
        execution.completed_at = now
    else:
        execution.current_node_id = target
    await session.flush()
    return execution


async def back_execution(
    session: AsyncSession,
    *,
    principal: Principal,
    call: Call,
    expected_state_version: int,
    idempotency_key: str,
) -> CallFlowExecution:
    execution = await session.scalar(
        select(CallFlowExecution)
        .where(
            CallFlowExecution.tenant_id == principal.tenant_id,
            CallFlowExecution.call_id == call.id,
            CallFlowExecution.operator_user_id == principal.user_id,
        )
        .with_for_update()
    )
    if execution is None or execution.status != "active":
        raise ApiError(409, "call_flow_back_unavailable", "Возврат по сценарию недоступен")
    replay = await session.scalar(
        select(CallFlowExecutionStep).where(
            CallFlowExecutionStep.tenant_id == principal.tenant_id,
            CallFlowExecutionStep.execution_id == execution.id,
            CallFlowExecutionStep.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        return execution
    if execution.state_version != expected_state_version:
        raise ApiError(409, "call_flow_execution_conflict", "Сценарий уже изменён в другой вкладке")
    prior = await session.scalar(
        select(CallFlowExecutionStep)
        .where(
            CallFlowExecutionStep.tenant_id == principal.tenant_id,
            CallFlowExecutionStep.execution_id == execution.id,
            CallFlowExecutionStep.node_type != "back",
        )
        .order_by(CallFlowExecutionStep.sequence.desc())
        .limit(1)
    )
    if prior is None:
        raise ApiError(409, "call_flow_back_unavailable", "Предыдущего шага нет")
    sequence = (
        int(
            await session.scalar(
                select(func.max(CallFlowExecutionStep.sequence)).where(
                    CallFlowExecutionStep.execution_id == execution.id
                )
            )
            or 0
        )
        + 1
    )
    now = datetime.now(UTC)
    session.add(
        CallFlowExecutionStep(
            tenant_id=principal.tenant_id,
            execution_id=execution.id,
            sequence=sequence,
            node_id=prior.node_id,
            system_key="back",
            node_type="back",
            language_code=execution.language_code,
            text_snapshot=prior.text_snapshot,
            hint_snapshot=prior.hint_snapshot,
            next_node_id=prior.node_id,
            action_status="history_not_reverted",
            actor_user_id=principal.user_id,
            idempotency_key=idempotency_key,
            occurred_at=now,
        )
    )
    execution.current_node_id = prior.node_id
    execution.state_version += 1
    await session.flush()
    return execution


def complete_execution(execution: CallFlowExecution, *, expected_version: int | None) -> None:
    if expected_version is not None and execution.state_version != expected_version:
        raise ApiError(409, "call_flow_execution_conflict", "Сценарий уже изменён в другой вкладке")
    if execution.status == "active":
        execution.status = "completed"
        execution.current_node_id = None
        execution.completed_at = datetime.now(UTC)
        execution.state_version += 1
