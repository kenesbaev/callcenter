from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select

from teamora_api.dependencies import Principal, SessionDep
from teamora_api.enums import OperatorVersionStatus
from teamora_api.errors import ApiError
from teamora_api.models import CallFlow, CallFlowVersion, CustomerFieldDefinition, Project
from teamora_api.project_access import resolve_project
from teamora_api.schemas.call_flows import (
    CallFlowDefinition,
    CallFlowNode,
    CallFlowPreviewNode,
    CallFlowPreviewRequest,
    CallFlowPreviewResult,
    CallFlowRead,
    CallFlowValidationIssue,
    CallFlowValidationResult,
    CallFlowVersionRead,
    CallFlowVersionSummary,
)

BRANCH_NODE_TYPES = {"customer_question", "choice"}
INPUT_NODE_TYPES = BRANCH_NODE_TYPES | {"value_input"}
TEXT_REQUIRED_NODE_TYPES = {
    "operator_text",
    "customer_question",
    "info_hint",
    "choice",
    "value_input",
    "end",
}
ACTION_NODE_TYPES = {
    "update_customer_field",
    "create_task",
    "create_callback",
    "transfer_request",
}
AUTOMATIC_NODE_TYPES = {
    "start",
    "operator_text",
    "info_hint",
    "update_customer_field",
    "create_task",
    "create_callback",
    "transfer_request",
}


def default_definition(language_code: str) -> CallFlowDefinition:
    start_id = uuid4()
    end_id = uuid4()
    return CallFlowDefinition(
        nodes=[
            CallFlowNode(
                id=start_id,
                system_key="start",
                name="Начало",
                node_type="start",
                order=0,
                next_node_id=end_id,
            ),
            CallFlowNode(
                id=end_id,
                system_key="end",
                name="Завершение",
                node_type="end",
                order=10,
                text_by_language={language_code: "Разговор завершён."},
            ),
        ]
    )


def definition_from_storage(raw: dict[str, object]) -> CallFlowDefinition:
    try:
        return CallFlowDefinition.model_validate(raw)
    except ValidationError:
        return CallFlowDefinition()


def version_summary(version: CallFlowVersion) -> CallFlowVersionSummary:
    return CallFlowVersionSummary(
        id=version.id,
        version=version.version,
        status=version.status,
        lock_version=version.lock_version,
        created_from_version_id=version.created_from_version_id,
        published_at=version.published_at,
        created_at=version.created_at,
        updated_at=version.updated_at,
    )


def version_read(version: CallFlowVersion) -> CallFlowVersionRead:
    return CallFlowVersionRead(
        **version_summary(version).model_dump(),
        call_flow_id=version.call_flow_id,
        project_id=version.project_id,
        definition=definition_from_storage(version.definition),
    )


async def flow_read(session: SessionDep, flow: CallFlow) -> CallFlowRead:
    versions = list(
        await session.scalars(
            select(CallFlowVersion)
            .where(
                CallFlowVersion.tenant_id == flow.tenant_id,
                CallFlowVersion.project_id == flow.project_id,
                CallFlowVersion.call_flow_id == flow.id,
            )
            .order_by(CallFlowVersion.version.desc())
        )
    )
    return CallFlowRead(
        id=flow.id,
        project_id=flow.project_id,
        name=flow.name,
        description=flow.description,
        default_language_code=flow.default_language_code,
        language_codes=flow.language_codes,
        active_version_id=flow.active_version_id,
        is_active=flow.is_active,
        archived_at=flow.archived_at,
        versions=[version_summary(version) for version in versions],
        created_at=flow.created_at,
        updated_at=flow.updated_at,
    )


async def resolve_flow(
    session: SessionDep,
    principal: Principal,
    flow_id: UUID,
    *,
    for_update: bool = False,
    allow_archived: bool = True,
) -> CallFlow:
    statement = select(CallFlow).where(
        CallFlow.tenant_id == principal.tenant_id,
        CallFlow.id == flow_id,
    )
    if not allow_archived:
        statement = statement.where(CallFlow.archived_at.is_(None), CallFlow.is_active.is_(True))
    if for_update:
        statement = statement.with_for_update(of=CallFlow)
    flow = await session.scalar(statement)
    if flow is None:
        raise ApiError(404, "call_flow_not_found", "Сценарий не найден")
    await resolve_project(session, principal, flow.project_id, active_only=False)
    return flow


async def resolve_version(
    session: SessionDep,
    principal: Principal,
    flow_id: UUID,
    version_id: UUID,
    *,
    for_update: bool = False,
    allow_archived_flow: bool = True,
) -> tuple[CallFlow, CallFlowVersion]:
    flow = await resolve_flow(
        session,
        principal,
        flow_id,
        for_update=for_update,
        allow_archived=allow_archived_flow,
    )
    statement = select(CallFlowVersion).where(
        CallFlowVersion.tenant_id == principal.tenant_id,
        CallFlowVersion.project_id == flow.project_id,
        CallFlowVersion.call_flow_id == flow.id,
        CallFlowVersion.id == version_id,
    )
    if for_update:
        statement = statement.with_for_update(of=CallFlowVersion)
    version = await session.scalar(statement)
    if version is None:
        raise ApiError(404, "call_flow_version_not_found", "Версия сценария не найдена")
    return flow, version


def ensure_draft(version: CallFlowVersion, expected_lock_version: int | None = None) -> None:
    if version.status != OperatorVersionStatus.DRAFT:
        raise ApiError(
            409,
            "call_flow_version_immutable",
            "Опубликованную или архивную версию нельзя изменять",
        )
    if expected_lock_version is not None and version.lock_version != expected_lock_version:
        raise ApiError(
            409,
            "call_flow_version_conflict",
            "Черновик уже изменён другим пользователем. Обновите страницу.",
            details=[
                {
                    "expected_lock_version": expected_lock_version,
                    "current_lock_version": version.lock_version,
                }
            ],
        )


def referenced_node_ids(definition: CallFlowDefinition) -> set[UUID]:
    result: set[UUID] = set()
    for node in definition.nodes:
        if node.next_node_id:
            result.add(node.next_node_id)
        if node.fallback_node_id:
            result.add(node.fallback_node_id)
        result.update(answer.next_node_id for answer in node.answers if answer.next_node_id is not None)
    return result


def broken_reference_issues(definition: CallFlowDefinition) -> list[CallFlowValidationIssue]:
    node_ids = {node.id for node in definition.nodes}
    issues: list[CallFlowValidationIssue] = []
    for node in definition.nodes:
        targets: list[tuple[str, UUID | None]] = [
            ("next_node_id", node.next_node_id),
            ("fallback_node_id", node.fallback_node_id),
        ]
        targets.extend((f"answer:{answer.key}", answer.next_node_id) for answer in node.answers)
        for source, target in targets:
            if target is not None and target not in node_ids:
                issues.append(
                    CallFlowValidationIssue(
                        code="transition_target_missing",
                        message=f"Переход {source} ведёт на отсутствующий узел {target}",
                        node_id=node.id,
                        node_name=node.name,
                    )
                )
    return issues


async def validate_customer_fields(
    session: SessionDep,
    flow: CallFlow,
    definition: CallFlowDefinition,
) -> list[CallFlowValidationIssue]:
    field_ids = {
        node.customer_field_definition_id
        for node in definition.nodes
        if node.customer_field_definition_id is not None
    }
    available: set[UUID] = set()
    if field_ids:
        available = set(
            await session.scalars(
                select(CustomerFieldDefinition.id).where(
                    CustomerFieldDefinition.tenant_id == flow.tenant_id,
                    CustomerFieldDefinition.project_id == flow.project_id,
                    CustomerFieldDefinition.id.in_(field_ids),
                )
            )
        )
    issues: list[CallFlowValidationIssue] = []
    for node in definition.nodes:
        field_id = node.customer_field_definition_id
        if field_id is not None and field_id not in available:
            issues.append(
                CallFlowValidationIssue(
                    code="customer_field_not_available",
                    message="Связанное поле клиента не принадлежит проекту сценария",
                    node_id=node.id,
                    node_name=node.name,
                )
            )
        if node.node_type == "update_customer_field" and field_id is None:
            issues.append(
                CallFlowValidationIssue(
                    code="customer_field_required",
                    message="Для обновления клиента выберите проектное поле",
                    node_id=node.id,
                    node_name=node.name,
                )
            )
    return issues


def graph_edges(definition: CallFlowDefinition) -> dict[UUID, set[UUID]]:
    edges: dict[UUID, set[UUID]] = defaultdict(set)
    for node in definition.nodes:
        for target in (node.next_node_id, node.fallback_node_id):
            if target is not None:
                edges[node.id].add(target)
        for answer in node.answers:
            if answer.next_node_id is not None:
                edges[node.id].add(answer.next_node_id)
    return edges


def automatic_cycle_nodes(definition: CallFlowDefinition) -> set[UUID]:
    nodes = {node.id: node for node in definition.nodes}
    visited: set[UUID] = set()
    visiting: set[UUID] = set()
    cycles: set[UUID] = set()

    def visit(node_id: UUID) -> None:
        node = nodes[node_id]
        if node.node_type not in AUTOMATIC_NODE_TYPES or node.next_node_id is None:
            visited.add(node_id)
            return
        if node_id in visiting:
            cycles.add(node_id)
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        target = node.next_node_id
        if target in nodes and nodes[target].node_type in AUTOMATIC_NODE_TYPES:
            visit(target)
            if target in cycles:
                cycles.add(node_id)
        visiting.remove(node_id)
        visited.add(node_id)

    for node in definition.nodes:
        if node.id not in visited:
            visit(node.id)
    return cycles


async def validate_definition(
    session: SessionDep,
    flow: CallFlow,
    definition: CallFlowDefinition,
) -> CallFlowValidationResult:
    issues = broken_reference_issues(definition)
    starts = [node for node in definition.nodes if node.node_type == "start"]
    ends = [node for node in definition.nodes if node.node_type == "end"]
    if len(starts) != 1:
        issues.append(
            CallFlowValidationIssue(
                code="single_start_required",
                message=f"Ожидается ровно один начальный узел, найдено: {len(starts)}",
            )
        )
    if not ends:
        issues.append(
            CallFlowValidationIssue(
                code="end_required",
                message="Добавьте хотя бы один завершающий узел",
            )
        )

    keys: dict[str, list[CallFlowNode]] = defaultdict(list)
    for node in definition.nodes:
        keys[node.system_key].append(node)
        if node.node_type in BRANCH_NODE_TYPES and not node.answers:
            issues.append(
                CallFlowValidationIssue(
                    code="answers_required",
                    message="Для вопроса или выбора добавьте варианты ответа",
                    node_id=node.id,
                    node_name=node.name,
                )
            )
        for answer in node.answers:
            if answer.is_required and answer.next_node_id is None:
                issues.append(
                    CallFlowValidationIssue(
                        code="answer_transition_required",
                        message=f"Для обязательного варианта «{answer.key}» выберите переход",
                        node_id=node.id,
                        node_name=node.name,
                    )
                )
        if node.node_type == "end" and (
            node.next_node_id is not None or node.fallback_node_id is not None or node.answers
        ):
            issues.append(
                CallFlowValidationIssue(
                    code="end_has_transition",
                    message="Завершающий узел не может иметь переходы",
                    node_id=node.id,
                    node_name=node.name,
                )
            )
        if node.node_type in AUTOMATIC_NODE_TYPES and node.next_node_id is None:
            issues.append(
                CallFlowValidationIssue(
                    code="automatic_transition_required",
                    message="Для автоматического узла выберите следующий шаг",
                    node_id=node.id,
                    node_name=node.name,
                )
            )
        if node.node_type in TEXT_REQUIRED_NODE_TYPES:
            for language in flow.language_codes:
                if not node.text_by_language.get(language, "").strip():
                    issues.append(
                        CallFlowValidationIssue(
                            code="language_text_required",
                            message=f"Заполните текст на языке «{language}»",
                            node_id=node.id,
                            node_name=node.name,
                        )
                    )
        for answer in node.answers:
            for language in flow.language_codes:
                if not answer.label_by_language.get(language, "").strip():
                    issues.append(
                        CallFlowValidationIssue(
                            code="answer_language_required",
                            message=f"Заполните вариант «{answer.key}» на языке «{language}»",
                            node_id=node.id,
                            node_name=node.name,
                        )
                    )
    for key, duplicate_nodes in keys.items():
        if len(duplicate_nodes) > 1:
            for node in duplicate_nodes:
                issues.append(
                    CallFlowValidationIssue(
                        code="system_key_duplicate",
                        message=f"Системный ключ «{key}» повторяется внутри версии",
                        node_id=node.id,
                        node_name=node.name,
                    )
                )

    if len(starts) == 1 and not broken_reference_issues(definition):
        edges = graph_edges(definition)
        reachable: set[UUID] = set()
        pending = [starts[0].id]
        while pending:
            node_id = pending.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            pending.extend(edges[node_id] - reachable)
        for node in definition.nodes:
            if node.id not in reachable:
                issues.append(
                    CallFlowValidationIssue(
                        code="node_unreachable",
                        message="Узел недостижим из начала сценария",
                        node_id=node.id,
                        node_name=node.name,
                    )
                )

    cycles = automatic_cycle_nodes(definition)
    nodes = {node.id: node for node in definition.nodes}
    for node_id in cycles:
        node = nodes[node_id]
        issues.append(
            CallFlowValidationIssue(
                code="automatic_cycle",
                message="Обнаружена бесконечная цепочка автоматических переходов",
                node_id=node.id,
                node_name=node.name,
            )
        )
    issues.extend(await validate_customer_fields(session, flow, definition))
    return CallFlowValidationResult(valid=not issues, errors=issues)


def validation_details(result: CallFlowValidationResult) -> list[dict[str, object]]:
    return [issue.model_dump(mode="json", exclude_none=True) for issue in result.errors]


def localized(value: dict[str, str], language: str, fallback: str) -> str:
    return value.get(language) or value.get(fallback) or next(iter(value.values()), "")


def preview_node(flow: CallFlow, node: CallFlowNode, language: str) -> CallFlowPreviewNode:
    return CallFlowPreviewNode(
        id=node.id,
        system_key=node.system_key,
        name=node.name,
        node_type=node.node_type,
        text=localized(node.text_by_language, language, flow.default_language_code),
        hint=localized(node.hint_by_language, language, flow.default_language_code),
        answers=[
            {
                "key": answer.key,
                "label": localized(
                    answer.label_by_language,
                    language,
                    flow.default_language_code,
                ),
            }
            for answer in node.answers
        ],
        action_is_inert=node.node_type in ACTION_NODE_TYPES,
    )


async def build_preview(
    session: SessionDep,
    flow: CallFlow,
    version: CallFlowVersion,
    payload: CallFlowPreviewRequest,
) -> CallFlowPreviewResult:
    if payload.language_code not in flow.language_codes:
        raise ApiError(422, "call_flow_language_unavailable", "Язык не включён в сценарий")
    definition = definition_from_storage(version.definition)
    validation = await validate_definition(session, flow, definition)
    if not validation.valid:
        raise ApiError(
            422,
            "call_flow_preview_invalid",
            "Исправьте сценарий перед preview",
            details=validation_details(validation),
        )
    nodes = {node.id: node for node in definition.nodes}
    current = next(node for node in definition.nodes if node.node_type == "start")
    supplied = {answer.node_id: answer.answer_key for answer in payload.answers}
    path: list[CallFlowPreviewNode] = []
    inert_actions: list[str] = []
    for _ in range(len(nodes) + 1):
        rendered = preview_node(flow, current, payload.language_code)
        path.append(rendered)
        if rendered.action_is_inert:
            inert_actions.append(current.node_type)
        if current.node_type == "end":
            return CallFlowPreviewResult(
                language_code=payload.language_code,
                path=path,
                current_node=None,
                completed=True,
                inert_actions=inert_actions,
            )
        if current.id not in supplied:
            return CallFlowPreviewResult(
                language_code=payload.language_code,
                path=path,
                current_node=rendered,
                completed=False,
                inert_actions=inert_actions,
            )
        answer_key = supplied[current.id]
        target = current.next_node_id
        if current.node_type in BRANCH_NODE_TYPES:
            answer = next((item for item in current.answers if item.key == answer_key), None)
            target = answer.next_node_id if answer is not None else current.fallback_node_id
        elif current.node_type == "value_input" and answer_key is None:
            target = current.fallback_node_id or current.next_node_id
        if target is None or target not in nodes:
            raise ApiError(422, "call_flow_preview_stopped", "Preview не может продолжить переход")
        current = nodes[target]
    raise ApiError(422, "call_flow_preview_cycle", "Preview превысил безопасную длину пути")


async def create_draft_version(
    session: SessionDep,
    flow: CallFlow,
    *,
    source: CallFlowVersion | None,
) -> CallFlowVersion:
    existing_draft = await session.scalar(
        select(CallFlowVersion).where(
            CallFlowVersion.tenant_id == flow.tenant_id,
            CallFlowVersion.project_id == flow.project_id,
            CallFlowVersion.call_flow_id == flow.id,
            CallFlowVersion.status == OperatorVersionStatus.DRAFT,
        )
    )
    if existing_draft is not None:
        return existing_draft
    next_version = (
        int(
            await session.scalar(
                select(func.max(CallFlowVersion.version)).where(
                    CallFlowVersion.tenant_id == flow.tenant_id,
                    CallFlowVersion.call_flow_id == flow.id,
                )
            )
            or 0
        )
        + 1
    )
    definition = (
        definition_from_storage(source.definition)
        if source is not None
        else default_definition(flow.default_language_code)
    )
    draft = CallFlowVersion(
        tenant_id=flow.tenant_id,
        project_id=flow.project_id,
        call_flow_id=flow.id,
        version=next_version,
        status=OperatorVersionStatus.DRAFT,
        definition=definition.model_dump(mode="json"),
        lock_version=1,
        created_from_version_id=source.id if source else None,
    )
    session.add(draft)
    await session.flush()
    return draft


async def active_version_for_project(
    session: SessionDep,
    *,
    tenant_id: UUID,
    project_id: UUID,
) -> CallFlowVersion | None:
    result = await session.execute(
        select(CallFlowVersion)
        .join(
            CallFlow,
            (CallFlow.id == CallFlowVersion.call_flow_id)
            & (CallFlow.tenant_id == CallFlowVersion.tenant_id)
            & (CallFlow.project_id == CallFlowVersion.project_id),
        )
        .join(
            Project,
            (Project.id == CallFlow.project_id) & (Project.tenant_id == CallFlow.tenant_id),
        )
        .where(
            Project.tenant_id == tenant_id,
            Project.id == project_id,
            Project.call_flow_id == CallFlow.id,
            CallFlow.is_active.is_(True),
            CallFlow.archived_at.is_(None),
            CallFlow.active_version_id == CallFlowVersion.id,
            CallFlowVersion.status == OperatorVersionStatus.PUBLISHED,
        )
    )
    return result.scalar_one_or_none()


def publish_version(flow: CallFlow, version: CallFlowVersion) -> None:
    now = datetime.now(UTC)
    version.status = OperatorVersionStatus.PUBLISHED
    version.published_at = now
    version.lock_version += 1
    flow.active_version_id = version.id
