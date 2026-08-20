from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from teamora_api.config import get_settings
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.main import app
from teamora_api.models import TransferAttempt

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def _project_id(client: AsyncClient) -> str:
    response = await client.get("/api/v1/projects?limit=10")
    assert response.status_code == 200, response.text
    return response.json()["items"][0]["id"]


async def _operator(
    owner: AsyncClient,
    *,
    csrf: str,
    tenant_slug: str,
    project_id: str,
    suffix: str,
) -> tuple[AsyncClient, str, str]:
    email = f"transfer-operator-{suffix}@example.com"
    password = "SecureTransfer123!"  # noqa: S105 - isolated test credential
    invited = await owner.post(
        "/api/v1/team/invitations",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"transfer-invite-{suffix}"},
        json={"email": email, "role": "human_operator", "project_ids": [project_id]},
    )
    assert invited.status_code == 201, invited.text
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    accepted = await client.post(
        "/api/v1/team/invitations/accept",
        json={
            "tenant_slug": tenant_slug,
            "token": invited.json()["acceptance_token"],
            "display_name": f"Transfer Operator {suffix}",
            "password": password,
        },
    )
    assert accepted.status_code == 200, accepted.text
    login = await client.post(
        "/api/v1/auth/login",
        json={"company_slug": tenant_slug, "email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    operator_csrf = login.json()["csrf_token"]
    heartbeat = await client.post(
        "/api/v1/team/presence/heartbeat",
        headers={"X-CSRF-Token": operator_csrf},
        json={"session_key": f"transfer-browser-{suffix}"},
    )
    assert heartbeat.status_code == 200, heartbeat.text
    available = await client.put(
        "/api/v1/team/presence/status",
        headers={"X-CSRF-Token": operator_csrf},
        json={"session_key": f"transfer-browser-{suffix}", "status": "available"},
    )
    assert available.status_code == 200, available.text
    return client, operator_csrf, accepted.json()["membership_id"]


async def _active_ai_call(client: AsyncClient, csrf: str, suffix: str) -> dict[str, object]:
    operator = await client.post(
        "/api/v1/ai-operators",
        headers={"X-CSRF-Token": csrf},
        json={
            "name": f"Transfer AI {suffix}",
            "description": "Transfer routing test",
            "system_instructions": "Transfer to a human when explicitly requested.",
            "allowed_languages": ["ru"],
            "allowed_tools": ["request_human_operator", "end_call"],
        },
    )
    assert operator.status_code == 201, operator.text
    published = await client.post(
        f"/api/v1/ai-operators/{operator.json()['id']}/publish",
        headers={"X-CSRF-Token": csrf},
    )
    assert published.status_code == 200, published.text
    call = await client.post(
        "/api/v1/simulator/calls",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"transfer-call-{suffix}"},
        json={
            "ai_operator_id": operator.json()["id"],
            "language": "ru",
            "customer_name": "Transfer Customer",
            "customer_phone": "+998901234567",
        },
    )
    assert call.status_code == 201, call.text
    return call.json()


async def test_transfer_offer_claim_idempotency_and_tenant_scope(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project_id = await _project_id(client)
    operator, operator_csrf, membership_id = await _operator(
        client,
        csrf=csrf,
        tenant_slug=auth["tenant"]["slug"],
        project_id=project_id,
        suffix=unique_suffix,
    )
    outsider = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        call = await _active_ai_call(client, csrf, unique_suffix)
        owner_session = f"transfer-owner-{unique_suffix}"
        assert (
            await client.post(
                "/api/v1/team/presence/heartbeat",
                headers={"X-CSRF-Token": csrf},
                json={"session_key": owner_session},
            )
        ).status_code == 200
        assert (
            await client.put(
                "/api/v1/team/presence/status",
                headers={"X-CSRF-Token": csrf},
                json={"session_key": owner_session, "status": "away"},
            )
        ).status_code == 200
        body = {
            "reason": "Клиент запросил специалиста",
            "summary": "Подтверждённый контекст",
            "destination_type": "browser",
            "expected_call_version": call["state_version"],
        }
        headers = {"X-CSRF-Token": csrf, "Idempotency-Key": f"transfer-request-{unique_suffix}"}
        created = await client.post(
            f"/api/v1/transfers/calls/{call['id']}/request", headers=headers, json=body
        )
        assert created.status_code == 200, created.text
        transfer = created.json()
        assert transfer["status"] == "offered"
        assert transfer["attempt_count"] == 1
        assert transfer["attempts"][0]["membership_id"] == membership_id
        call_detail = await client.get(f"/api/v1/calls/{call['id']}")
        assert call_detail.status_code == 200, call_detail.text
        assert call_detail.json()["transfers"][0]["id"] == transfer["id"]
        assert call_detail.json()["transfers"][0]["reason"] == body["reason"]
        replay = await client.post(
            f"/api/v1/transfers/calls/{call['id']}/request", headers=headers, json=body
        )
        assert replay.status_code == 200
        assert replay.json()["id"] == transfer["id"]

        incoming = await operator.get("/api/v1/transfers")
        assert incoming.status_code == 200, incoming.text
        assert [item["id"] for item in incoming.json()] == [transfer["id"]]
        claimed = await operator.post(
            f"/api/v1/transfers/requests/{transfer['id']}/claim",
            headers={"X-CSRF-Token": operator_csrf},
            json={"expected_version": transfer["lock_version"]},
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["status"] == "claimed"
        stale = await operator.post(
            f"/api/v1/transfers/requests/{transfer['id']}/claim",
            headers={"X-CSRF-Token": operator_csrf},
            json={"expected_version": transfer["lock_version"]},
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "transfer_version_conflict"

        outsider_auth, _ = await register(outsider, f"other-{unique_suffix}")
        assert outsider_auth["tenant"]["id"] != auth["tenant"]["id"]
        hidden = await outsider.get(f"/api/v1/transfers/requests/{transfer['id']}")
        assert hidden.status_code == 404
    finally:
        await operator.aclose()
        await outsider.aclose()


async def test_unverified_mobile_destination_is_rejected(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, f"mobile-{unique_suffix}")
    project_id = await _project_id(client)
    operator, operator_csrf, membership_id = await _operator(
        client,
        csrf=csrf,
        tenant_slug=auth["tenant"]["slug"],
        project_id=project_id,
        suffix=f"mobile-{unique_suffix}",
    )
    try:
        call = await _active_ai_call(client, csrf, f"mobile-{unique_suffix}")
        created = await client.post(
            f"/api/v1/transfers/calls/{call['id']}/request",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"mobile-transfer-{unique_suffix}",
            },
            json={"reason": "Mobile requested", "destination_type": "mobile"},
        )
        assert created.status_code == 200, created.text
        # No verified allowlisted mobile endpoint: routing must fail safely and
        # return control to AI instead of dialing an arbitrary number.
        assert created.json()["status"] in {"failed", "callback_requested"}
        visible = (await operator.get("/api/v1/transfers")).json()
        assert all(item["status"] != "offered" for item in visible)
        endpoint = await client.put(
            f"/api/v1/transfers/operator/endpoints/{membership_id}",
            headers={"X-CSRF-Token": csrf},
            json={
                "endpoint_type": "mobile",
                "destination": "+998901112233",
                "display_hint": "+998 90 *** 22 33",
            },
        )
        assert endpoint.status_code == 200, endpoint.text
        assert endpoint.json()["is_verified"] is False
        assert (await operator.get("/api/v1/transfers/operator/endpoints")).json() == []
        authorized = await client.post(
            f"/api/v1/transfers/operator/endpoints/{endpoint.json()['id']}/authorize",
            headers={"X-CSRF-Token": csrf},
            json={
                "expected_version": endpoint.json()["lock_version"],
                "confirm_controlled_destination": True,
            },
        )
        assert authorized.status_code == 200, authorized.text
        assert authorized.json()["is_verified"] is True
        available_endpoints = await operator.get("/api/v1/transfers/operator/endpoints")
        assert available_endpoints.status_code == 200, available_endpoints.text
        assert available_endpoints.json() == [
            {
                "id": endpoint.json()["id"],
                "endpoint_type": "mobile",
                "display_hint": "+998 90 *** 22 33",
                "is_verified": True,
                "is_enabled": True,
                "lock_version": 2,
            }
        ]
        assert "+998901112233" not in available_endpoints.text
        assert operator_csrf
    finally:
        await operator.aclose()


async def test_expired_offer_is_retried_once_by_authenticated_worker(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, f"timeout-{unique_suffix}")
    tenant_id = UUID(str(auth["tenant"]["id"]))
    project_id = await _project_id(client)
    first, _first_csrf, first_membership = await _operator(
        client,
        csrf=csrf,
        tenant_slug=str(auth["tenant"]["slug"]),
        project_id=project_id,
        suffix=f"first-{unique_suffix}",
    )
    second: AsyncClient | None = None
    try:
        owner_session = f"timeout-owner-{unique_suffix}"
        assert (
            await client.post(
                "/api/v1/team/presence/heartbeat",
                headers={"X-CSRF-Token": csrf},
                json={"session_key": owner_session},
            )
        ).status_code == 200
        assert (
            await client.put(
                "/api/v1/team/presence/status",
                headers={"X-CSRF-Token": csrf},
                json={"session_key": owner_session, "status": "away"},
            )
        ).status_code == 200
        call = await _active_ai_call(client, csrf, f"timeout-{unique_suffix}")
        created = await client.post(
            f"/api/v1/transfers/calls/{call['id']}/request",
            headers={
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"timeout-transfer-{unique_suffix}",
            },
            json={"reason": "No answer retry", "destination_type": "browser"},
        )
        assert created.status_code == 200, created.text
        transfer = created.json()
        assert transfer["attempts"][0]["membership_id"] == first_membership

        second, _second_csrf, second_membership = await _operator(
            client,
            csrf=csrf,
            tenant_slug=str(auth["tenant"]["slug"]),
            project_id=project_id,
            suffix=f"second-{unique_suffix}",
        )
        async with SessionFactory() as session:
            await set_tenant_context(session, tenant_id)
            attempt = await session.scalar(
                select(TransferAttempt).where(
                    TransferAttempt.tenant_id == tenant_id,
                    TransferAttempt.transfer_request_id == UUID(transfer["id"]),
                    TransferAttempt.status == "offered",
                )
            )
            assert attempt is not None
            attempt.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

        command = {
            "tenant_id": str(tenant_id),
            "transfer_request_id": transfer["id"],
            "job_id": "00000000-0000-4000-8000-000000000017",
        }
        denied = await client.post(
            "/internal/v1/transfers/process-expired",
            headers={"Authorization": "Bearer invalid-service-token"},
            json=command,
        )
        assert denied.status_code == 401
        service_token = get_settings().gateway_service_token
        assert service_token is not None
        headers = {"Authorization": f"Bearer {service_token.get_secret_value()}"}
        processed = await client.post("/internal/v1/transfers/process-expired", headers=headers, json=command)
        assert processed.status_code == 200, processed.text
        assert processed.json()["processed"] is True
        replay = await client.post("/internal/v1/transfers/process-expired", headers=headers, json=command)
        assert replay.status_code == 200
        current = await client.get(f"/api/v1/transfers/requests/{transfer['id']}")
        assert current.status_code == 200, current.text
        attempts = current.json()["attempts"]
        assert [item["status"] for item in attempts] == ["timed_out", "offered"]
        assert attempts[1]["membership_id"] == second_membership
    finally:
        await first.aclose()
        if second is not None:
            await second.aclose()
