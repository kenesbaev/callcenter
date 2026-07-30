from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import RoleName
from teamora_api.main import app
from teamora_api.models import CallbackTask, CallFlow, CallFlowVersion, Customer
from tests.test_customer_profiles import create_customer, create_project, default_project_id
from tests.test_project_configuration import authenticated_client, create_member

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


def valid_definition(languages: list[str] | None = None) -> dict[str, object]:
    enabled = languages or ["ru"]
    start_id = str(uuid4())
    question_id = str(uuid4())
    yes_id = str(uuid4())
    no_id = str(uuid4())

    def texts(prefix: str) -> dict[str, str]:
        return {language: f"{prefix} {language}" for language in enabled}

    return {
        "schema_version": 1,
        "nodes": [
            {
                "id": start_id,
                "system_key": "start",
                "name": "Начало",
                "node_type": "start",
                "order": 0,
                "next_node_id": question_id,
            },
            {
                "id": question_id,
                "system_key": "confirm",
                "name": "Подтверждение",
                "node_type": "customer_question",
                "text_by_language": texts("Подтвердите"),
                "order": 10,
                "answers": [
                    {
                        "key": "yes",
                        "label_by_language": texts("Да"),
                        "next_node_id": yes_id,
                    },
                    {
                        "key": "no",
                        "label_by_language": texts("Нет"),
                        "next_node_id": no_id,
                    },
                ],
                "fallback_node_id": no_id,
            },
            {
                "id": yes_id,
                "system_key": "success_end",
                "name": "Успешное завершение",
                "node_type": "end",
                "text_by_language": texts("Спасибо"),
                "order": 20,
            },
            {
                "id": no_id,
                "system_key": "decline_end",
                "name": "Отказ",
                "node_type": "end",
                "text_by_language": texts("До свидания"),
                "order": 30,
            },
        ],
    }


async def create_flow(
    client: AsyncClient,
    csrf: str,
    project_id: str,
    *,
    languages: list[str] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    enabled = languages or ["ru"]
    response = await client.post(
        "/api/v1/call-flows",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": project_id,
            "name": "Сценарий продаж",
            "description": "Ветвящийся сценарий",
            "default_language_code": enabled[0],
            "language_codes": enabled,
        },
    )
    assert response.status_code == 201, response.text
    flow = response.json()
    draft = next(version for version in flow["versions"] if version["status"] == "draft")
    return flow, draft


async def save_definition(
    client: AsyncClient,
    csrf: str,
    flow_id: str,
    draft: dict[str, object],
    definition: dict[str, object],
) -> dict[str, object]:
    response = await client.put(
        f"/api/v1/call-flows/{flow_id}/versions/{draft['id']}",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_lock_version": draft["lock_version"],
            "definition": definition,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def publish(
    client: AsyncClient,
    csrf: str,
    flow_id: str,
    version: dict[str, object],
) -> dict[str, object]:
    response = await client.post(
        f"/api/v1/call-flows/{flow_id}/versions/{version['id']}/publish",
        headers={"X-CSRF-Token": csrf},
        json={"expected_lock_version": version["lock_version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def create_published_flow(
    client: AsyncClient,
    csrf: str,
    project_id: str,
    *,
    languages: list[str] | None = None,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    flow, draft = await create_flow(client, csrf, project_id, languages=languages)
    definition = valid_definition(languages)
    saved = await save_definition(client, csrf, str(flow["id"]), draft, definition)
    published = await publish(client, csrf, str(flow["id"]), saved)
    return flow, published, definition


async def start_mock_call(
    client: AsyncClient,
    csrf: str,
    *,
    phone: str,
) -> dict[str, object]:
    await create_customer(
        client,
        csrf,
        contacts=[{"kind": "phone", "value": phone}],
        name=f"Клиент {phone[-4:]}",
    )
    assignment = await client.post(
        "/api/v1/dialer/next-client",
        headers={"X-CSRF-Token": csrf},
    )
    assert assignment.status_code == 200, assignment.text
    response = await client.post(
        "/api/v1/calls/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "customer_id": assignment.json()["customer"]["id"],
            "lock_token": assignment.json()["lock_token"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def finish_mock_call(client: AsyncClient, csrf: str, call_id: str) -> None:
    response = await client.post(f"/api/v1/calls/{call_id}/hangup", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    response = await client.post(
        f"/api/v1/calls/{call_id}/result",
        headers={"X-CSRF-Token": csrf},
        json={"result": "success", "comment": ""},
    )
    assert response.status_code == 200, response.text


async def force_definition(
    tenant_id: UUID,
    version_id: UUID,
    definition: dict[str, object],
) -> None:
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        version = await session.scalar(select(CallFlowVersion).where(CallFlowVersion.id == version_id))
        assert version is not None
        version.definition = definition


async def test_owner_creates_call_flow_inside_project(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    flow, draft = await create_flow(client, csrf, project_id)
    assert flow["project_id"] == project_id
    assert draft["version"] == 1
    assert draft["status"] == "draft"


async def test_call_flow_tenant_isolation_and_rls(unique_suffix: str, register: Register) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as first:
        first_auth, first_csrf = await register(first, f"flow-a-{unique_suffix}")
        project_id = await default_project_id(first)
        flow, _draft = await create_flow(first, first_csrf, project_id)
        first_tenant = UUID(str(first_auth["tenant"]["id"]))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as second:
        second_auth, _second_csrf = await register(second, f"flow-b-{unique_suffix}")
        response = await second.get(f"/api/v1/call-flows/{flow['id']}")
        assert response.status_code == 404, response.text
        second_tenant = UUID(str(second_auth["tenant"]["id"]))
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, first_tenant)
        assert await session.scalar(select(CallFlow).where(CallFlow.id == UUID(str(flow["id"]))))
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, second_tenant)
        assert await session.scalar(select(CallFlow).where(CallFlow.id == UUID(str(flow["id"])))) is None


async def test_operator_reads_published_flow_but_cannot_edit(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    flow, published, _definition = await create_published_flow(client, csrf, project_id)
    _operator_id, email = await create_member(
        tenant_id=UUID(str(auth["tenant"]["id"])),
        suffix=unique_suffix,
        role=RoleName.HUMAN_OPERATOR,
        project_id=UUID(project_id),
    )
    operator, operator_csrf = await authenticated_client(f"test-{unique_suffix}", email)
    try:
        response = await operator.get(f"/api/v1/call-flows/{flow['id']}")
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["versions"]] == [published["id"]]
        response = await operator.get(f"/api/v1/call-flows/{flow['id']}/versions/{published['id']}")
        assert response.status_code == 200, response.text
        response = await operator.patch(
            f"/api/v1/call-flows/{flow['id']}",
            headers={"X-CSRF-Token": operator_csrf},
            json={"name": "Запрещено"},
        )
        assert response.status_code == 403, response.text
    finally:
        await operator.aclose()


async def test_validation_requires_single_start(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, draft = await create_flow(client, csrf, await default_project_id(client))
    definition = valid_definition()
    duplicate = deepcopy(definition["nodes"][0])  # type: ignore[index]
    duplicate["id"] = str(uuid4())
    duplicate["system_key"] = "second_start"
    duplicate["name"] = "Второе начало"
    definition["nodes"].append(duplicate)  # type: ignore[union-attr]
    saved = await save_definition(client, csrf, str(flow["id"]), draft, definition)
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/versions/{saved['id']}/validate",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert "single_start_required" in {item["code"] for item in response.json()["errors"]}


async def test_validation_requires_end_node(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, draft = await create_flow(client, csrf, await default_project_id(client))
    definition = {
        "schema_version": 1,
        "nodes": [
            {
                "id": str(uuid4()),
                "system_key": "start",
                "name": "Начало",
                "node_type": "start",
            }
        ],
    }
    saved = await save_definition(client, csrf, str(flow["id"]), draft, definition)
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/versions/{saved['id']}/validate",
        headers={"X-CSRF-Token": csrf},
    )
    assert "end_required" in {item["code"] for item in response.json()["errors"]}


async def test_broken_transition_blocks_publication(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    flow, draft = await create_flow(client, csrf, await default_project_id(client))
    definition = valid_definition()
    definition["nodes"][0]["next_node_id"] = str(uuid4())  # type: ignore[index]
    await force_definition(UUID(str(auth["tenant"]["id"])), UUID(str(draft["id"])), definition)
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/versions/{draft['id']}/publish",
        headers={"X-CSRF-Token": csrf},
        json={"expected_lock_version": draft["lock_version"]},
    )
    assert response.status_code == 422, response.text
    assert any(item["code"] == "transition_target_missing" for item in response.json()["error"]["details"])


async def test_unreachable_node_blocks_publication(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, draft = await create_flow(client, csrf, await default_project_id(client))
    definition = valid_definition()
    definition["nodes"].append(  # type: ignore[union-attr]
        {
            "id": str(uuid4()),
            "system_key": "orphan",
            "name": "Недостижимый узел",
            "node_type": "end",
            "text_by_language": {"ru": "Недостижим"},
        }
    )
    saved = await save_definition(client, csrf, str(flow["id"]), draft, definition)
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/versions/{saved['id']}/publish",
        headers={"X-CSRF-Token": csrf},
        json={"expected_lock_version": saved["lock_version"]},
    )
    assert response.status_code == 422, response.text
    assert any(item["code"] == "node_unreachable" for item in response.json()["error"]["details"])


async def test_automatic_infinite_cycle_blocks_publication(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, draft = await create_flow(client, csrf, await default_project_id(client))
    first_id, second_id, end_id = str(uuid4()), str(uuid4()), str(uuid4())
    definition = {
        "schema_version": 1,
        "nodes": [
            {
                "id": first_id,
                "system_key": "start",
                "name": "Начало",
                "node_type": "start",
                "next_node_id": second_id,
            },
            {
                "id": second_id,
                "system_key": "auto_text",
                "name": "Автоматический текст",
                "node_type": "operator_text",
                "text_by_language": {"ru": "Текст"},
                "next_node_id": first_id,
            },
            {
                "id": end_id,
                "system_key": "end",
                "name": "Конец",
                "node_type": "end",
                "text_by_language": {"ru": "Конец"},
            },
        ],
    }
    saved = await save_definition(client, csrf, str(flow["id"]), draft, definition)
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/versions/{saved['id']}/publish",
        headers={"X-CSRF-Token": csrf},
        json={"expected_lock_version": saved["lock_version"]},
    )
    assert response.status_code == 422, response.text
    assert any(item["code"] == "automatic_cycle" for item in response.json()["error"]["details"])


async def test_customer_field_from_other_project_is_rejected(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    default_project = await default_project_id(client)
    other_project = await create_project(client, csrf, "Другой проект")
    response = await client.post(
        "/api/v1/customers/fields",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": other_project,
            "name": "Чужое поле",
            "key": "foreign_field",
            "field_type": "text",
        },
    )
    field_id = response.json()["id"]
    flow, draft = await create_flow(client, csrf, default_project)
    definition = valid_definition()
    definition["nodes"].insert(  # type: ignore[union-attr]
        1,
        {
            "id": str(uuid4()),
            "system_key": "foreign_update",
            "name": "Обновление",
            "node_type": "update_customer_field",
            "customer_field_definition_id": field_id,
            "next_node_id": definition["nodes"][1]["id"],  # type: ignore[index]
        },
    )
    definition["nodes"][0]["next_node_id"] = definition["nodes"][1]["id"]  # type: ignore[index]
    response = await client.put(
        f"/api/v1/call-flows/{flow['id']}/versions/{draft['id']}",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_lock_version": draft["lock_version"],
            "definition": definition,
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"][0]["code"] == "customer_field_not_available"


async def test_published_version_is_immutable(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, published, definition = await create_published_flow(client, csrf, await default_project_id(client))
    response = await client.put(
        f"/api/v1/call-flows/{flow['id']}/versions/{published['id']}",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_lock_version": published["lock_version"],
            "definition": definition,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "call_flow_version_immutable"


async def test_new_draft_is_copied_from_published_version(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, published, _definition = await create_published_flow(client, csrf, await default_project_id(client))
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/drafts",
        headers={"X-CSRF-Token": csrf},
        json={"source_version_id": published["id"]},
    )
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["version"] == 2
    assert draft["created_from_version_id"] == published["id"]
    assert draft["definition"] == published["definition"]


async def test_old_call_keeps_previous_published_version(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    flow, version_one, _definition = await create_published_flow(client, csrf, project_id)
    call = await start_mock_call(client, csrf, phone="+998900010001")
    assert call["call_flow_version_id"] == version_one["id"]
    await finish_mock_call(client, csrf, str(call["id"]))
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/drafts",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    version_two = await publish(client, csrf, str(flow["id"]), response.json())
    fixed = await client.get(f"/api/v1/call-flows/calls/{call['id']}")
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["id"] == version_one["id"]
    assert version_two["id"] != version_one["id"]


async def test_new_call_uses_latest_published_version(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    flow, version_one, _definition = await create_published_flow(client, csrf, project_id)
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/drafts",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    version_two = await publish(client, csrf, str(flow["id"]), response.json())
    call = await start_mock_call(client, csrf, phone="+998900010002")
    assert call["call_flow_version_id"] == version_two["id"]
    assert call["call_flow_version_id"] != version_one["id"]


async def test_optimistic_lock_rejects_silent_draft_overwrite(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, draft = await create_flow(client, csrf, await default_project_id(client))
    definition = valid_definition()
    saved = await save_definition(client, csrf, str(flow["id"]), draft, definition)
    assert saved["lock_version"] == int(draft["lock_version"]) + 1
    response = await client.put(
        f"/api/v1/call-flows/{flow['id']}/versions/{draft['id']}",
        headers={"X-CSRF-Token": csrf},
        json={
            "expected_lock_version": draft["lock_version"],
            "definition": definition,
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "call_flow_version_conflict"


async def test_preview_follows_yes_and_no_branches(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, published, definition = await create_published_flow(client, csrf, await default_project_id(client))
    start, question, yes_end, no_end = definition["nodes"]  # type: ignore[misc]
    for answer_key, expected_end in (("yes", yes_end), ("no", no_end)):
        response = await client.post(
            f"/api/v1/call-flows/{flow['id']}/versions/{published['id']}/preview",
            headers={"X-CSRF-Token": csrf},
            json={
                "language_code": "ru",
                "answers": [
                    {"node_id": start["id"]},
                    {"node_id": question["id"], "answer_key": answer_key},
                ],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["completed"] is True
        assert response.json()["path"][-1]["id"] == expected_end["id"]


async def test_preview_does_not_modify_customer(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    flow, published, definition = await create_published_flow(client, csrf, await default_project_id(client))
    customer = await create_customer(client, csrf, name="Неизменяемый клиент")
    start = definition["nodes"][0]  # type: ignore[index]
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/versions/{published['id']}/preview",
        headers={"X-CSRF-Token": csrf},
        json={"language_code": "ru", "answers": [{"node_id": start["id"]}]},
    )
    assert response.status_code == 200, response.text
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, UUID(str(auth["tenant"]["id"])))
        stored = await session.scalar(select(Customer).where(Customer.id == UUID(str(customer["id"]))))
        assert stored is not None
        assert stored.custom_fields == {}
        assert (
            await session.scalar(
                select(func.count())
                .select_from(CallbackTask)
                .where(CallbackTask.customer_id == UUID(str(customer["id"])))
            )
        ) == 0


async def test_preview_switches_language(client: AsyncClient, unique_suffix: str, register: Register) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, published, definition = await create_published_flow(
        client,
        csrf,
        await default_project_id(client),
        languages=["ru", "uz"],
    )
    start = definition["nodes"][0]  # type: ignore[index]
    for language in ("ru", "uz"):
        response = await client.post(
            f"/api/v1/call-flows/{flow['id']}/versions/{published['id']}/preview",
            headers={"X-CSRF-Token": csrf},
            json={"language_code": language, "answers": [{"node_id": start["id"]}]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["language_code"] == language
        assert response.json()["current_node"]["text"].endswith(language)


async def test_georgian_and_karakalpak_language_codes_are_distinct(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, _draft = await create_flow(
        client,
        csrf,
        await default_project_id(client),
        languages=["KA", "kaa", "kaa-Latn", "KAA_Cyrl"],
    )

    assert flow["default_language_code"] == "ka"
    assert flow["language_codes"] == ["ka", "kaa", "kaa-latn", "kaa-cyrl"]
    assert flow["language_codes"][0] != flow["language_codes"][1]


async def test_archived_flow_is_not_used_for_new_calls(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    flow, _published, _definition = await create_published_flow(client, csrf, project_id)
    response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/archive",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    call = await start_mock_call(client, csrf, phone="+998900010003")
    assert call["call_flow_version_id"] is None


async def test_flow_history_contains_published_and_archived_versions(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, version_one, _definition = await create_published_flow(
        client, csrf, await default_project_id(client)
    )
    draft_response = await client.post(
        f"/api/v1/call-flows/{flow['id']}/drafts",
        headers={"X-CSRF-Token": csrf},
        json={},
    )
    version_two = await publish(client, csrf, str(flow["id"]), draft_response.json())
    response = await client.get(f"/api/v1/call-flows/{flow['id']}")
    statuses = {item["id"]: item["status"] for item in response.json()["versions"]}
    assert statuses[version_one["id"]] == "archived"
    assert statuses[version_two["id"]] == "published"


async def test_delete_referenced_node_is_rejected(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    flow, draft = await create_flow(client, csrf, await default_project_id(client))
    version = await client.get(f"/api/v1/call-flows/{flow['id']}/versions/{draft['id']}")
    start = version.json()["definition"]["nodes"][0]
    response = await client.request(
        "DELETE",
        f"/api/v1/call-flows/{flow['id']}/versions/{draft['id']}/nodes/{start['next_node_id']}",
        headers={"X-CSRF-Token": csrf},
        json={"expected_lock_version": draft["lock_version"]},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "call_flow_node_referenced"


async def test_mock_dialer_still_works_without_call_flow(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    call = await start_mock_call(client, csrf, phone="+998900010004")
    assert call["provider"] == "mock"
    assert call["call_flow_version_id"] is None
