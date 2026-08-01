from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from teamora_api.call_state import CallStatus
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import CallChannel, CallDirection, CallerType, RoleName
from teamora_api.main import app
from teamora_api.models import (
    AuditLog,
    Call,
    Invitation,
    Membership,
    OperatorPresence,
)

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


async def project_id(client: AsyncClient) -> str:
    response = await client.get("/api/v1/projects?limit=10")
    assert response.status_code == 200, response.text
    return response.json()["items"][0]["id"]


async def invite_and_accept(
    owner: AsyncClient,
    *,
    csrf: str,
    tenant_slug: str,
    suffix: str,
    role: str = "human_operator",
    projects: list[str] | None = None,
) -> tuple[AsyncClient, dict[str, object], str, str]:
    email = f"{role}+{suffix}@example.com"
    password = "SecureInvite123!"  # noqa: S105 - isolated test credential
    response = await owner.post(
        "/api/v1/team/invitations",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"invite-{suffix}-{role}"},
        json={"email": email, "role": role, "project_ids": projects or []},
    )
    assert response.status_code == 201, response.text
    invitation = response.json()
    assert invitation["acceptance_token"]
    invited = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    accepted = await invited.post(
        "/api/v1/team/invitations/accept",
        json={
            "tenant_slug": tenant_slug,
            "token": invitation["acceptance_token"],
            "display_name": f"Member {suffix}",
            "password": password,
        },
    )
    assert accepted.status_code == 200, accepted.text
    login = await invited.post(
        "/api/v1/auth/login",
        json={"company_slug": tenant_slug, "email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    return invited, accepted.json(), login.json()["csrf_token"], invitation["acceptance_token"]


async def test_invitation_lifecycle_idempotency_and_team_card(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await project_id(client)
    email = f"operator+{unique_suffix}@example.com"
    body = {
        "email": email,
        "role": "human_operator",
        "project_ids": [project],
        "expires_in_days": 7,
    }
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": f"invite-idem-{unique_suffix}"}
    first = await client.post("/api/v1/team/invitations", headers=headers, json=body)
    assert first.status_code == 201, first.text
    assert first.json()["acceptance_token"]
    replay = await client.post("/api/v1/team/invitations", headers=headers, json=body)
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == first.json()["id"]
    assert replay.json()["acceptance_token"] is None

    invited = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        accept_body = {
            "tenant_slug": auth["tenant"]["slug"],
            "token": first.json()["acceptance_token"],
            "display_name": "Test Operator",
            "password": "SecureInvite123!",
        }
        accepted = await invited.post("/api/v1/team/invitations/accept", json=accept_body)
        assert accepted.status_code == 200, accepted.text
        repeated = await invited.post("/api/v1/team/invitations/accept", json=accept_body)
        assert repeated.status_code == 200
        assert repeated.json()["already_accepted"] is True

        response = await client.get("/api/v1/team?role=human_operator")
        assert response.status_code == 200
        assert response.json()["total"] == 1
        member = response.json()["items"][0]
        assert member["email"] == email
        assert member["projects"] == [{"id": project, "name": "Основной проект"}]

        invitation_rows = await client.get("/api/v1/team/invitations")
        assert invitation_rows.status_code == 200
        assert invitation_rows.json()["items"][0]["status"] == "accepted"
    finally:
        await invited.aclose()


async def test_role_matrix_last_owner_and_optimistic_concurrency(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await project_id(client)
    manager, manager_accept, manager_csrf, _ = await invite_and_accept(
        client,
        csrf=csrf,
        tenant_slug=auth["tenant"]["slug"],
        suffix=f"manager-{unique_suffix}",
        role="tenant_manager",
        projects=[project],
    )
    analyst, analyst_accept, analyst_csrf, _ = await invite_and_accept(
        client,
        csrf=csrf,
        tenant_slug=auth["tenant"]["slug"],
        suffix=f"analyst-{unique_suffix}",
        role="analyst",
        projects=[project],
    )
    try:
        owner = next(
            item
            for item in (await client.get("/api/v1/team")).json()["items"]
            if item["role"] == "tenant_owner"
        )
        forbidden = await manager.put(
            f"/api/v1/team/{owner['membership_id']}/role",
            headers={"X-CSRF-Token": manager_csrf},
            json={"role": "human_operator", "expected_version": owner["state_version"]},
        )
        assert forbidden.status_code == 403

        manager_self = await manager.get(f"/api/v1/team/{manager_accept['membership_id']}")
        manager_change = await manager.put(
            f"/api/v1/team/{manager_accept['membership_id']}/role",
            headers={"X-CSRF-Token": manager_csrf},
            json={
                "role": "human_operator",
                "expected_version": manager_self.json()["state_version"],
            },
        )
        assert manager_change.status_code == 403

        last_owner = await client.put(
            f"/api/v1/team/{owner['membership_id']}/role",
            headers={"X-CSRF-Token": csrf},
            json={"role": "tenant_manager", "expected_version": owner["state_version"]},
        )
        assert last_owner.status_code == 409
        assert last_owner.json()["error"]["code"] == "last_owner_protected"

        self_block = await client.post(
            f"/api/v1/team/{owner['membership_id']}/block",
            headers={"X-CSRF-Token": csrf},
            json={"expected_version": owner["state_version"], "reason": "unsafe self block"},
        )
        assert self_block.status_code == 409

        analyst_member = await client.get(f"/api/v1/team/{analyst_accept['membership_id']}")
        assert analyst_member.status_code == 200
        denied = await analyst.patch(
            f"/api/v1/team/{analyst_accept['membership_id']}/profile",
            headers={"X-CSRF-Token": analyst_csrf},
            json={"expected_version": analyst_member.json()["state_version"], "extension": "999"},
        )
        assert denied.status_code == 403

        analyst_self_denied = await analyst.patch(
            f"/api/v1/team/{analyst_accept['membership_id']}/profile",
            headers={"X-CSRF-Token": analyst_csrf},
            json={
                "expected_version": analyst_member.json()["state_version"],
                "job_title": "Should remain read-only",
            },
        )
        assert analyst_self_denied.status_code == 403

        manager_member = await client.get(f"/api/v1/team/{manager_accept['membership_id']}")
        current_version = manager_member.json()["state_version"]
        changed = await client.patch(
            f"/api/v1/team/{manager_accept['membership_id']}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_version": current_version, "job_title": "Supervisor"},
        )
        assert changed.status_code == 200
        conflict = await client.patch(
            f"/api/v1/team/{manager_accept['membership_id']}/profile",
            headers={"X-CSRF-Token": csrf},
            json={"expected_version": current_version, "job_title": "Stale edit"},
        )
        assert conflict.status_code == 409
    finally:
        await manager.aclose()
        await analyst.aclose()


async def test_owner_transfer_is_atomic(client: AsyncClient, unique_suffix: str, register: Register) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await project_id(client)
    target, accepted, _target_csrf, _ = await invite_and_accept(
        client,
        csrf=csrf,
        tenant_slug=auth["tenant"]["slug"],
        suffix=f"transfer-{unique_suffix}",
        role="tenant_manager",
        projects=[project],
    )
    try:
        team = (await client.get("/api/v1/team")).json()["items"]
        owner = next(item for item in team if item["role"] == "tenant_owner")
        manager = next(item for item in team if item["membership_id"] == accepted["membership_id"])
        response = await client.post(
            f"/api/v1/team/{manager['membership_id']}/transfer-ownership",
            headers={"X-CSRF-Token": csrf},
            json={
                "current_owner_expected_version": owner["state_version"],
                "target_expected_version": manager["state_version"],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["role"] == "tenant_owner"
        tenant_id = UUID(auth["tenant"]["id"])
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, tenant_id)
            memberships = list(
                await session.scalars(select(Membership).where(Membership.tenant_id == tenant_id))
            )
            assert sum(value.role == RoleName.TENANT_OWNER for value in memberships) == 1
            assert (
                next(value for value in memberships if value.id == UUID(owner["membership_id"])).role
                == RoleName.TENANT_MANAGER
            )
    finally:
        await target.aclose()


async def test_presence_multiple_tabs_busy_and_block_safety(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await project_id(client)
    operator, accepted, operator_csrf, _ = await invite_and_accept(
        client,
        csrf=csrf,
        tenant_slug=auth["tenant"]["slug"],
        suffix=f"presence-{unique_suffix}",
        projects=[project],
    )
    try:
        for session_key in ("browser-tab-one", "browser-tab-two"):
            heartbeat = await operator.post(
                "/api/v1/team/presence/heartbeat",
                headers={"X-CSRF-Token": operator_csrf},
                json={"session_key": session_key},
            )
            assert heartbeat.status_code == 200
        status = await operator.put(
            "/api/v1/team/presence/status",
            headers={"X-CSRF-Token": operator_csrf},
            json={"session_key": "browser-tab-one", "status": "available"},
        )
        assert status.status_code == 200
        assert status.json()["effective_status"] == "available"
        candidates = await client.get(f"/api/v1/team/transfer-candidates?project_id={project}")
        assert candidates.status_code == 200
        assert [item["membership_id"] for item in candidates.json()] == [accepted["membership_id"]]
        system_status = await operator.put(
            "/api/v1/team/presence/status",
            headers={"X-CSRF-Token": operator_csrf},
            json={"session_key": "browser-tab-one", "status": "busy"},
        )
        assert system_status.status_code == 422

        tenant_id = UUID(auth["tenant"]["id"])
        operator_user_id = UUID(accepted["membership_id"])
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, tenant_id)
            membership = await session.scalar(select(Membership).where(Membership.id == operator_user_id))
            assert membership is not None
            call = Call(
                tenant_id=tenant_id,
                project_id=UUID(project),
                channel=CallChannel.DEVELOPMENT_SIMULATOR,
                direction=CallDirection.OUTBOUND,
                caller_type=CallerType.HUMAN_OPERATOR,
                status=CallStatus.ACTIVE,
                operator_user_id=membership.user_id,
                provider="mock",
                provider_state="active",
                started_at=datetime.now(UTC),
                answered_at=datetime.now(UTC),
            )
            session.add(call)

        busy = await operator.get("/api/v1/team/presence")
        assert busy.status_code == 200
        assert busy.json()["effective_status"] == "busy"
        candidates = await client.get(f"/api/v1/team/transfer-candidates?project_id={project}")
        assert candidates.json() == []
        member = await client.get(f"/api/v1/team/{accepted['membership_id']}")
        blocked = await client.post(
            f"/api/v1/team/{accepted['membership_id']}/block",
            headers={"X-CSRF-Token": csrf},
            json={"expected_version": member.json()["state_version"], "reason": "security review"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "operator_has_active_call"

        async with SessionFactory.begin() as session:
            await set_tenant_context(session, tenant_id)
            call = await session.scalar(
                select(Call).where(Call.operator_user_id.is_not(None), Call.tenant_id == tenant_id)
            )
            assert call is not None
            call.status = CallStatus.COMPLETED
            call.ended_at = datetime.now(UTC)
        available = await operator.get("/api/v1/team/presence")
        assert available.json()["effective_status"] == "available"

        async with SessionFactory.begin() as session:
            await set_tenant_context(session, tenant_id)
            old = datetime.now(UTC) - timedelta(minutes=3)
            membership = await session.scalar(
                select(Membership).where(Membership.id == UUID(accepted["membership_id"]))
            )
            assert membership is not None
            membership.presence_last_seen_at = old
            for presence in await session.scalars(
                select(OperatorPresence).where(OperatorPresence.membership_id == membership.id)
            ):
                presence.heartbeat_at = old
        offline = await operator.get("/api/v1/team/presence")
        assert offline.json()["effective_status"] == "offline"
    finally:
        await operator.aclose()


async def test_block_restore_and_dialer_denies_blocked_member(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await project_id(client)
    operator, accepted, operator_csrf, _ = await invite_and_accept(
        client,
        csrf=csrf,
        tenant_slug=auth["tenant"]["slug"],
        suffix=f"blocked-{unique_suffix}",
        projects=[project],
    )
    try:
        member = (await client.get(f"/api/v1/team/{accepted['membership_id']}")).json()
        blocked = await client.post(
            f"/api/v1/team/{accepted['membership_id']}/block",
            headers={"X-CSRF-Token": csrf},
            json={"expected_version": member["state_version"], "reason": "temporary suspension"},
        )
        assert blocked.status_code == 200
        denied = await operator.post(
            f"/api/v1/dialer/next-client?project_id={project}",
            headers={"X-CSRF-Token": operator_csrf},
        )
        assert denied.status_code == 401
        restored = await client.post(
            f"/api/v1/team/{accepted['membership_id']}/restore",
            headers={"X-CSRF-Token": csrf},
            json={"expected_version": blocked.json()["state_version"]},
        )
        assert restored.status_code == 200
        assert restored.json()["is_active"] is True
    finally:
        await operator.aclose()


async def test_invitation_cancel_expiry_and_tenant_isolation(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, f"invite-a-{unique_suffix}")
    response = await client.post(
        "/api/v1/team/invitations",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"cancel-{unique_suffix}"},
        json={"email": f"cancel+{unique_suffix}@example.com", "role": "analyst"},
    )
    assert response.status_code == 201
    invitation = response.json()
    cancelled = await client.post(
        f"/api/v1/team/invitations/{invitation['id']}/cancel",
        headers={"X-CSRF-Token": csrf},
        json={"expected_version": invitation["state_version"]},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    rejected = await client.post(
        "/api/v1/team/invitations/accept",
        json={
            "tenant_slug": auth["tenant"]["slug"],
            "token": invitation["acceptance_token"],
            "display_name": "Cancelled",
            "password": "SecureInvite123!",
        },
    )
    assert rejected.status_code == 410

    expiring = await client.post(
        "/api/v1/team/invitations",
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"expires-{unique_suffix}"},
        json={"email": f"expired+{unique_suffix}@example.com", "role": "analyst"},
    )
    assert expiring.status_code == 201
    tenant_id = UUID(auth["tenant"]["id"])
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        expiring_invitation = await session.scalar(
            select(Invitation).where(Invitation.id == UUID(expiring.json()["id"]))
        )
        assert expiring_invitation is not None
        expiring_invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expired = await client.post(
        "/api/v1/team/invitations/accept",
        json={
            "tenant_slug": auth["tenant"]["slug"],
            "token": expiring.json()["acceptance_token"],
            "display_name": "Expired",
            "password": "SecureInvite123!",
        },
    )
    assert expired.status_code == 410

    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        await register(other, f"invite-b-{unique_suffix}")
        foreign_project = await project_id(other)
        foreign_assignment = await client.post(
            "/api/v1/team/invitations",
            headers={"X-CSRF-Token": csrf, "Idempotency-Key": f"foreign-{unique_suffix}"},
            json={
                "email": f"foreign+{unique_suffix}@example.com",
                "role": "human_operator",
                "project_ids": [foreign_project],
            },
        )
        assert foreign_assignment.status_code == 422
        hidden = await other.get(f"/api/v1/team/{auth['user']['id']}")
        assert hidden.status_code in {404, 422}
        assert invitation["email"] not in (await other.get("/api/v1/team/invitations")).text
    finally:
        await other.aclose()


async def test_team_audit_and_rls_for_new_tables(
    client: AsyncClient, unique_suffix: str, register: Register
) -> None:
    auth, csrf = await register(client, unique_suffix)
    project = await project_id(client)
    operator, accepted, _operator_csrf, _ = await invite_and_accept(
        client,
        csrf=csrf,
        tenant_slug=auth["tenant"]["slug"],
        suffix=f"audit-{unique_suffix}",
        projects=[project],
    )
    try:
        member = (await client.get(f"/api/v1/team/{accepted['membership_id']}")).json()
        updated = await client.put(
            f"/api/v1/team/{accepted['membership_id']}/projects",
            headers={"X-CSRF-Token": csrf},
            json={"expected_version": member["state_version"], "project_ids": [project]},
        )
        assert updated.status_code == 200
        history = await client.get(f"/api/v1/team/{accepted['membership_id']}/history")
        assert history.status_code == 200
        assert any(item["action"] == "team.member_projects_changed" for item in history.json()["items"])

        tenant_id = UUID(auth["tenant"]["id"])
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, tenant_id)
            assert (
                await session.scalar(select(Invitation).where(Invitation.tenant_id == tenant_id)) is not None
            )
            assert (
                await session.scalar(
                    select(AuditLog).where(
                        AuditLog.tenant_id == tenant_id,
                        AuditLog.action == "team.member_projects_changed",
                    )
                )
                is not None
            )
    finally:
        await operator.aclose()
