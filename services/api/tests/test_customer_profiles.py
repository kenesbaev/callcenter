from __future__ import annotations

from collections.abc import Awaitable, Callable
from io import BytesIO
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.main import app
from teamora_api.models import Customer, PhoneNumber

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def default_project_id(client: AsyncClient) -> str:
    response = await client.get("/api/v1/projects")
    assert response.status_code == 200, response.text
    return str(next(item for item in response.json()["items"] if item["is_default"])["id"])


async def create_project(client: AsyncClient, csrf: str, name: str) -> str:
    response = await client.post(
        "/api/v1/projects",
        headers={"X-CSRF-Token": csrf},
        json={"name": name},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def create_customer(
    client: AsyncClient,
    csrf: str,
    *,
    project_id: str | None = None,
    name: str = "Тестовый клиент",
    contacts: list[dict[str, object]] | None = None,
    **fields: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "project_id": project_id,
        "display_name": name,
        "contacts": contacts or [],
        **fields,
    }
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def csv_preview(
    client: AsyncClient,
    csrf: str,
    project_id: str,
    content: str,
    *,
    filename: str = "clients.csv",
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/customers/import/preview",
        headers={"X-CSRF-Token": csrf},
        data={"project_id": project_id},
        files={"file": (filename, content.encode("utf-8"), "text/csv")},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_customer_with_multiple_phones(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    customer = await create_customer(
        client,
        csrf,
        contacts=[
            {"kind": "phone", "value": "+998 90 123-45-67", "is_primary": True},
            {"kind": "phone", "value": "+998 (91) 765-43-21", "label": "Рабочий"},
        ],
    )
    phones = [contact for contact in customer["contacts"] if contact["kind"] == "phone"]
    assert [contact["value"] for contact in phones] == ["+998901234567", "+998917654321"]


async def test_primary_phone_and_email_are_selected(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    customer = await create_customer(
        client,
        csrf,
        contacts=[
            {"kind": "phone", "value": "+998901111111"},
            {"kind": "phone", "value": "+998902222222", "is_primary": True},
            {"kind": "email", "value": "First@Example.com"},
            {"kind": "email", "value": "main@example.com", "is_primary": True},
        ],
    )
    primary = {(item["kind"], item["value"]) for item in customer["contacts"] if item["is_primary"]}
    assert primary == {("phone", "+998902222222"), ("email", "main@example.com")}


async def test_phone_and_email_normalization(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    customer = await create_customer(
        client,
        csrf,
        contacts=[
            {"kind": "phone", "value": "00998 (93) 555-44-33"},
            {"kind": "email", "value": "  CLIENT@Example.COM "},
        ],
    )
    assert {item["value"] for item in customer["contacts"]} == {
        "+998935554433",
        "client@example.com",
    }


async def test_external_reference_is_unique_inside_project(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    await create_customer(client, csrf, external_reference=" CRM-42 ")
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "Другой клиент", "external_reference": "crm-42"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "customer_duplicate"


async def test_same_external_reference_is_allowed_in_another_project(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    first_project = await default_project_id(client)
    second_project = await create_project(client, csrf, "Вторая клиентская база")
    first = await create_customer(client, csrf, project_id=first_project, external_reference="EXT-100")
    second = await create_customer(client, csrf, project_id=second_project, external_reference="EXT-100")
    assert first["project_id"] != second["project_id"]


async def test_customer_tenant_isolation_and_rls(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    first_auth, _first_csrf = await register(client, f"a{unique_suffix}")
    second = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        second_auth, second_csrf = await register(second, f"b{unique_suffix}")
        foreign = await create_customer(second, second_csrf, external_reference="FOREIGN")
        response = await client.get(f"/api/v1/customers/{foreign['id']}")
        assert response.status_code == 404, response.text
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, UUID(str(first_auth["tenant"]["id"])))
            hidden = await session.scalar(select(Customer.id).where(Customer.id == UUID(str(foreign["id"]))))
            assert hidden is None
        assert first_auth["tenant"]["id"] != second_auth["tenant"]["id"]
    finally:
        await second.aclose()


async def test_custom_field_from_another_project_is_rejected(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    default_project = await default_project_id(client)
    second_project = await create_project(client, csrf, "Проект с полями")
    response = await client.post(
        "/api/v1/customers/fields",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": second_project,
            "name": "Кредитный лимит",
            "key": "credit_limit",
            "field_type": "number",
        },
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": default_project,
            "display_name": "Чужое поле",
            "custom_fields": {"credit_limit": 5000},
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "customer_custom_field_unknown"


async def test_required_custom_field_is_validated(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    response = await client.post(
        "/api/v1/customers/fields",
        headers={"X-CSRF-Token": csrf},
        json={
            "project_id": project_id,
            "name": "Продукт",
            "key": "product",
            "field_type": "select",
            "is_required": True,
            "options": ["Карта", "Кредит"],
        },
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={"display_name": "Без продукта"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "customer_custom_field_required"
    created = await create_customer(client, csrf, custom_fields={"product": "Карта"})
    assert created["custom_fields"]["product"] == "Карта"


async def test_archived_customer_is_not_returned_by_dialer(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    customer = await create_customer(
        client,
        csrf,
        contacts=[{"kind": "phone", "value": "+998901010101"}],
    )
    response = await client.post(
        f"/api/v1/customers/{customer['id']}/archive",
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["archived_at"] is not None
    response = await client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json() is None


async def test_customer_without_phone_is_not_returned_by_dialer(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    await create_customer(
        client,
        csrf,
        contacts=[{"kind": "email", "value": "only-email@example.com"}],
    )
    response = await client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text
    assert response.json() is None


async def test_search_by_name_phone_email_and_external_reference(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    customer = await create_customer(
        client,
        csrf,
        name="Алишер Каримов",
        external_reference="BANK-SEARCH-77",
        contacts=[
            {"kind": "phone", "value": "+998907771122"},
            {"kind": "email", "value": "alisher.search@example.com"},
        ],
    )
    for query in ("Алишер", "7771122", "alisher.search", "BANK-SEARCH-77"):
        response = await client.get(f"/api/v1/customers?search={query}")
        assert response.status_code == 200, response.text
        assert [item["id"] for item in response.json()["items"]] == [customer["id"]]


async def test_customer_filters_and_pagination(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    owner_id = str(auth["user"]["id"])
    await create_customer(
        client,
        csrf,
        project_id=project_id,
        name="Первый",
        status="completed",
        preferred_language="uz",
        tags=["VIP"],
        assigned_user_id=owner_id,
    )
    await create_customer(client, csrf, project_id=project_id, name="Второй", tags=["Retail"])
    response = await client.get(
        f"/api/v1/customers?project_id={project_id}&status=completed&language=uz"
        f"&assigned_user_id={owner_id}&tag=VIP&limit=1&offset=0"
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    response = await client.get("/api/v1/customers?limit=1&offset=1")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 2
    assert len(response.json()["items"]) == 1


async def test_csv_preview_does_not_create_customers(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    preview = await csv_preview(
        client,
        csrf,
        project_id,
        "ФИО,Телефон\nИван Иванов,+998901234500\n",
    )
    assert preview["total_rows"] == 1
    response = await client.get("/api/v1/customers")
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 0


async def test_xlsx_preview_and_sheet_selection(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    workbook = Workbook()
    first = workbook.active
    first.title = "Основной"
    first.append(["ФИО", "Телефон"])
    first.append(["Первый лист", "+998901234501"])
    second = workbook.create_sheet("Дополнительный")
    second.append(["ФИО", "Телефон"])
    second.append(["Второй лист", "+998901234502"])
    buffer = BytesIO()
    workbook.save(buffer)
    response = await client.post(
        "/api/v1/customers/import/preview",
        headers={"X-CSRF-Token": csrf},
        data={"project_id": project_id, "sheet_name": "Дополнительный"},
        files={
            "file": (
                "clients.xlsx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert response.status_code == 201, response.text
    preview = response.json()
    assert preview["sheet_names"] == ["Основной", "Дополнительный"]
    assert preview["selected_sheet"] == "Дополнительный"
    assert preview["rows"][0]["values"]["display_name"] == "Второй лист"


async def test_import_column_mapping_is_applied(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    preview = await csv_preview(
        client,
        csrf,
        project_id,
        "Person,Mobile\nMapping Client,+998901234503\n",
    )
    response = await client.patch(
        f"/api/v1/customers/import/{preview['id']}",
        headers={"X-CSRF-Token": csrf},
        json={
            "mapping": {"display_name": "Person", "phone": "Mobile"},
            "update_rule": "skip",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["rows"][0]["values"]["display_name"] == "Mapping Client"
    assert response.json()["error_rows"] == 0


async def test_import_preview_detects_existing_duplicate(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    await create_customer(
        client,
        csrf,
        contacts=[{"kind": "phone", "value": "+998901234504"}],
    )
    preview = await csv_preview(
        client,
        csrf,
        project_id,
        "ФИО,Телефон\nДубликат,+998 90 123-45-04\n",
    )
    assert preview["duplicate_rows"] == 1
    assert "phone" in preview["rows"][0]["duplicate_fields"]


async def test_import_commit_is_idempotent(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    preview = await csv_preview(
        client,
        csrf,
        project_id,
        "ФИО,Телефон\nИдемпотентный клиент,+998901234505\n",
    )
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": f"import-{unique_suffix}"}
    first = await client.post(f"/api/v1/customers/import/{preview['id']}/commit", headers=headers)
    second = await client.post(f"/api/v1/customers/import/{preview['id']}/commit", headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["created"] == 1
    response = await client.get("/api/v1/customers")
    assert response.json()["total"] == 1


async def test_import_report_contains_invalid_row(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    project_id = await default_project_id(client)
    preview = await csv_preview(
        client,
        csrf,
        project_id,
        "ФИО,Телефон\n,+998901234506\nКорректный клиент,+998901234507\n",
    )
    assert preview["error_rows"] == 1
    response = await client.post(
        f"/api/v1/customers/import/{preview['id']}/commit",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"errors-{unique_suffix}"},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["created"] == 1
    assert report["skipped"] == 1
    assert report["errors"][0]["row_number"] == 2


async def test_legacy_customer_payload_remains_compatible(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    response = await client.post(
        "/api/v1/customers",
        headers={"X-CSRF-Token": csrf},
        json={
            "display_name": "Старый формат",
            "phone": "+998901234508",
            "alternate_phone": "+998901234509",
            "email": "legacy@example.com",
            "custom_fields": {"segment": "retail"},
        },
    )
    assert response.status_code == 201, response.text
    customer = response.json()
    assert len(customer["contacts"]) == 3
    assert customer["custom_fields"] == {"segment": "retail"}


async def test_mock_dialer_uses_primary_or_fallback_phone(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    customer = await create_customer(
        client,
        csrf,
        contacts=[
            {"kind": "phone", "value": "+998901234510"},
            {"kind": "phone", "value": "+998901234511", "is_primary": True},
        ],
    )
    assignment = await client.post("/api/v1/dialer/next-client", headers={"X-CSRF-Token": csrf})
    assert assignment.status_code == 200, assignment.text
    response = await client.post(
        "/api/v1/calls/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "customer_id": customer["id"],
            "lock_token": assignment.json()["lock_token"],
            "from_number": "MOCK",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["to_number"] == "+998901234511"
    assert response.json()["provider"] == "mock"


async def test_customer_archive_can_be_restored_and_filtered(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    _auth, csrf = await register(client, unique_suffix)
    customer = await create_customer(client, csrf)
    archived = await client.post(
        f"/api/v1/customers/{customer['id']}/archive",
        headers={"X-CSRF-Token": csrf},
    )
    assert archived.status_code == 200, archived.text
    response = await client.get("/api/v1/customers?archived=only")
    assert response.json()["total"] == 1
    restored = await client.post(
        f"/api/v1/customers/{customer['id']}/restore",
        headers={"X-CSRF-Token": csrf},
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["archived_at"] is None


async def test_telephony_did_is_global_while_customer_contact_is_project_scoped(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    first_auth, _first_csrf = await register(client, f"did-a{unique_suffix}")
    second = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        second_auth, _second_csrf = await register(second, f"did-b{unique_suffix}")
        did = "+998998887766"
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, UUID(str(first_auth["tenant"]["id"])))
            session.add(
                PhoneNumber(
                    tenant_id=UUID(str(first_auth["tenant"]["id"])),
                    e164=did,
                    label="DID A",
                )
            )
        with pytest.raises(IntegrityError):
            async with SessionFactory.begin() as session:
                await set_tenant_context(session, UUID(str(second_auth["tenant"]["id"])))
                session.add(
                    PhoneNumber(
                        tenant_id=UUID(str(second_auth["tenant"]["id"])),
                        e164=did,
                        label="DID B",
                    )
                )
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, UUID(str(first_auth["tenant"]["id"])))
            assert await session.scalar(select(func.count()).select_from(PhoneNumber)) == 1
    finally:
        await second.aclose()
