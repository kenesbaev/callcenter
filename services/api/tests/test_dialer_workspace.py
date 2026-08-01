from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.main import app
from teamora_api.models import (
    CallbackTask,
    CallFlowExecution,
    CallFlowExecutionStep,
    CallOutcome,
    DialerCompletionSubmission,
)
from tests.test_call_flow_editor import create_flow, create_published_flow, publish, save_definition
from tests.test_call_results import available_results
from tests.test_customer_profiles import create_customer, default_project_id

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


def phone(suffix: str, offset: int = 0) -> str:
    digits = "".join(character for character in suffix if character.isdigit())
    value = (int(digits[-7:] or "1") + offset) % 10_000_000
    return f"+99893{value:07d}"


async def assigned_call(
    client: AsyncClient,
    csrf: str,
    project_id: str,
    suffix: str,
    *,
    offset: int = 0,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    customer = await create_customer(
        client,
        csrf,
        project_id=project_id,
        name=f"Dialer customer {suffix} {offset}",
        contacts=[
            {"kind": "phone", "value": phone(suffix, offset), "is_primary": True},
            {"kind": "phone", "value": phone(suffix, offset + 100), "label": "Резервный"},
            {"kind": "email", "value": f"dialer-{suffix}-{offset}@example.test", "is_primary": True},
        ],
        city="Ташкент",
        region="Ташкент",
        address="Тестовая улица, 1",
        organization="K-Line Test",
        job_title="Клиент",
        tags=["dialer", "test"],
        description="Карточка для проверки рабочего места",
    )
    assignment_response = await client.post(
        f"/api/v1/dialer/next-client?project_id={project_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert assignment_response.status_code == 200, assignment_response.text
    assignment = assignment_response.json()
    reserve = next(
        contact for contact in assignment["customer"]["contacts"] if contact.get("label") == "Резервный"
    )
    call_response = await client.post(
        "/api/v1/calls/start",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"start-{suffix}-{offset}"},
        json={
            "customer_id": customer["id"],
            "customer_contact_id": reserve["id"],
            "lock_token": assignment["lock_token"],
        },
    )
    assert call_response.status_code == 201, call_response.text
    call = call_response.json()
    assert call["to_number"] == reserve["value"]
    return customer, assignment, call


async def test_published_flow_runtime_branches_recovers_and_rejects_conflicts(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    _flow, _version, definition = await create_published_flow(client, csrf, project_id)
    customer, assignment, call = await assigned_call(client, csrf, project_id, unique_suffix)

    assert assignment["source"] == "new"
    current_assignment = await client.get("/api/v1/dialer/current")
    assert current_assignment.status_code == 200
    assert current_assignment.json()["source"] == "new"
    assert assignment["customer"]["city"] == "Ташкент"
    assert len([item for item in assignment["customer"]["contacts"] if item["kind"] == "phone"]) == 2
    response = await client.get(f"/api/v1/dialer/flow/{call['id']}")
    assert response.status_code == 200, response.text
    execution = response.json()
    assert execution["call_flow_version_id"] == call["call_flow_version_id"]
    assert execution["current_node"]["node_type"] == "start"

    response = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/steps",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"flow-start-{unique_suffix}"},
        json={
            "node_id": execution["current_node"]["id"],
            "expected_state_version": execution["state_version"],
            "language_code": "ru",
        },
    )
    assert response.status_code == 200, response.text
    question = response.json()
    assert question["current_node"]["system_key"] == "confirm"

    conflict = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/steps",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"flow-conflict-{unique_suffix}"},
        json={
            "node_id": question["current_node"]["id"],
            "expected_state_version": execution["state_version"],
            "answer_key": "yes",
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "call_flow_execution_conflict"

    arbitrary = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/steps",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"flow-node-{unique_suffix}"},
        json={
            "node_id": str(uuid4()),
            "expected_state_version": question["state_version"],
            "answer_key": "yes",
        },
    )
    assert arbitrary.status_code == 409
    assert arbitrary.json()["error"]["code"] == "call_flow_node_conflict"

    response = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/steps",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"flow-yes-{unique_suffix}"},
        json={
            "node_id": question["current_node"]["id"],
            "expected_state_version": question["state_version"],
            "answer_key": "yes",
        },
    )
    assert response.status_code == 200, response.text
    end = response.json()
    assert end["current_node"]["id"] == definition["nodes"][2]["id"]

    response = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/back",
        headers={
            "X-CSRF-Token": csrf,
            "Idempotency-Key": f"flow-back-{unique_suffix}",
        },
        json={"expected_state_version": end["state_version"]},
    )
    assert response.status_code == 200, response.text
    question_again = response.json()
    assert question_again["current_node"]["system_key"] == "confirm"
    assert question_again["steps"][-1]["action_status"] == "history_not_reverted"

    response = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/steps",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"flow-no-{unique_suffix}"},
        json={
            "node_id": question_again["current_node"]["id"],
            "expected_state_version": question_again["state_version"],
            "answer_key": "no",
        },
    )
    assert response.status_code == 200, response.text
    end = response.json()
    assert end["current_node"]["id"] == definition["nodes"][3]["id"]

    recovered = await client.get(f"/api/v1/dialer/flow/{call['id']}")
    assert recovered.status_code == 200
    assert recovered.json()["state_version"] == end["state_version"]
    assert len(recovered.json()["steps"]) == 4

    response = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/steps",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"flow-end-{unique_suffix}"},
        json={
            "node_id": end["current_node"]["id"],
            "expected_state_version": end["state_version"],
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["current_node"] is None
    assert customer["id"] == assignment["customer"]["id"]


async def test_confirmed_call_flow_action_creates_one_task(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    flow, draft = await create_flow(client, csrf, project_id)
    start_id, action_id, end_id = (str(uuid4()) for _ in range(3))
    definition = {
        "schema_version": 1,
        "nodes": [
            {
                "id": start_id,
                "system_key": "start",
                "name": "Начало",
                "node_type": "start",
                "next_node_id": action_id,
            },
            {
                "id": action_id,
                "system_key": "create_followup",
                "name": "Создать задачу",
                "node_type": "create_task",
                "next_node_id": end_id,
                "action_config": {"title": "Проверить документы", "due_minutes": 30},
            },
            {
                "id": end_id,
                "system_key": "end",
                "name": "Завершение",
                "node_type": "end",
                "text_by_language": {"ru": "Готово"},
            },
        ],
    }
    saved = await save_definition(client, csrf, str(flow["id"]), draft, definition)
    await publish(client, csrf, str(flow["id"]), saved)
    _customer, _assignment, call = await assigned_call(client, csrf, project_id, unique_suffix)
    execution = (await client.get(f"/api/v1/dialer/flow/{call['id']}")).json()
    execution = (
        await client.post(
            f"/api/v1/dialer/flow/{call['id']}/steps",
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"action-start-{unique_suffix}"},
            json={
                "node_id": execution["current_node"]["id"],
                "expected_state_version": execution["state_version"],
            },
        )
    ).json()
    rejected = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/steps",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"action-no-confirm-{unique_suffix}"},
        json={
            "node_id": execution["current_node"]["id"],
            "expected_state_version": execution["state_version"],
        },
    )
    assert rejected.status_code == 422
    key = f"action-confirm-{unique_suffix}"
    accepted = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/steps",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": key},
        json={
            "node_id": execution["current_node"]["id"],
            "expected_state_version": execution["state_version"],
            "confirm_action": True,
        },
    )
    assert accepted.status_code == 200, accepted.text
    replay = await client.post(
        f"/api/v1/dialer/flow/{call['id']}/steps",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": key},
        json={
            "node_id": execution["current_node"]["id"],
            "expected_state_version": execution["state_version"],
            "confirm_action": True,
        },
    )
    assert replay.status_code == 200, replay.text
    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        count = int(
            await session.scalar(
                select(func.count())
                .select_from(CallbackTask)
                .where(
                    CallbackTask.tenant_id == tenant_id,
                    CallbackTask.call_id == UUID(str(call["id"])),
                    CallbackTask.source == "call_flow",
                )
            )
            or 0
        )
    assert count == 1


async def test_complete_and_next_is_atomic_and_idempotent(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    first, assignment, call = await assigned_call(client, csrf, project_id, unique_suffix)
    second = await create_customer(
        client,
        csrf,
        project_id=project_id,
        name="Следующий клиент",
        contacts=[{"kind": "phone", "value": phone(unique_suffix, 500), "is_primary": True}],
    )
    hangup = await client.post(
        f"/api/v1/calls/{call['id']}/hangup",
        headers={"X-CSRF-Token": csrf},
    )
    assert hangup.status_code == 200, hangup.text
    terminal = hangup.json()
    definitions = await available_results(client, project_id)
    result = next(item for item in definitions if item["system_code"] == "success")
    body = {
        "call_id": call["id"],
        "customer_id": first["id"],
        "lock_token": assignment["lock_token"],
        "expected_state_version": terminal["state_version"],
        "result": {
            "result_definition_id": result["id"],
            "comment": "Успешно",
        },
    }
    key = f"complete-next-{unique_suffix}"
    response = await client.post(
        "/api/v1/dialer/complete-and-next",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": key},
        json=body,
    )
    assert response.status_code == 200, response.text
    completed = response.json()
    assert completed["next_assignment"]["customer"]["id"] == second["id"]
    replay = await client.post(
        "/api/v1/dialer/complete-and-next",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": key},
        json=body,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["next_assignment"]["lock_token"] == completed["next_assignment"]["lock_token"]

    tenant_id = UUID(str(auth["tenant"]["id"]))  # type: ignore[index]
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        assert (
            int(
                await session.scalar(
                    select(func.count())
                    .select_from(CallOutcome)
                    .where(CallOutcome.call_id == UUID(str(call["id"])))
                )
                or 0
            )
            == 1
        )
        assert (
            int(
                await session.scalar(
                    select(func.count())
                    .select_from(DialerCompletionSubmission)
                    .where(DialerCompletionSubmission.idempotency_key == key)
                )
                or 0
            )
            == 1
        )


async def test_runtime_tables_are_tenant_isolated(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    first_auth, first_csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    await create_published_flow(client, first_csrf, project_id)
    _customer, _assignment, call = await assigned_call(client, first_csrf, project_id, unique_suffix)
    await client.get(f"/api/v1/dialer/flow/{call['id']}")
    first_tenant = UUID(str(first_auth["tenant"]["id"]))  # type: ignore[index]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as other:
        other_auth, _other_csrf = await register(other, f"other-{unique_suffix}")
        response = await other.get(f"/api/v1/dialer/flow/{call['id']}")
        assert response.status_code == 404
    other_tenant = UUID(str(other_auth["tenant"]["id"]))  # type: ignore[index]
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, first_tenant)
        execution_id = await session.scalar(
            select(CallFlowExecution.id).where(CallFlowExecution.call_id == UUID(str(call["id"])))
        )
        assert execution_id is not None
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, other_tenant)
        assert (
            await session.scalar(select(CallFlowExecution.id).where(CallFlowExecution.id == execution_id))
            is None
        )
        assert (
            await session.scalar(
                select(CallFlowExecutionStep.id).where(CallFlowExecutionStep.execution_id == execution_id)
            )
            is None
        )
