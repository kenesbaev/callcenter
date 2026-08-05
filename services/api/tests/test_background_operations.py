from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from teamora_api.background_service import enqueue_background_job
from teamora_api.db import SessionFactory, set_tenant_context, tenant_transaction
from teamora_api.enums import CallChannel, CallDirection, CallerType, CallStatus
from teamora_api.errors import ApiError
from teamora_api.main import app
from teamora_api.models import (
    BackgroundJob,
    BackgroundJobEvent,
    Call,
    Customer,
    CustomerImport,
    JobCommandSubmission,
    LegalHold,
    Membership,
    RealtimeEvent,
    RetentionCandidate,
    RetentionPolicy,
    StorageConsistencyIssue,
    StorageObject,
)
from teamora_api.object_storage import StoredObjectMetadata

Register = Callable[[AsyncClient, str], Awaitable[tuple[dict[str, object], str]]]


@dataclass(frozen=True)
class TenantContext:
    tenant_id: UUID
    user_id: UUID
    project_id: UUID
    slug: str
    csrf: str


async def _tenant_context(
    client: AsyncClient,
    suffix: str,
    register: Register,
) -> TenantContext:
    auth, csrf = await register(client, suffix)
    projects = await client.get("/api/v1/projects?limit=10")
    assert projects.status_code == 200, projects.text
    project_id = UUID(projects.json()["items"][0]["id"])
    return TenantContext(
        tenant_id=UUID(str(auth["tenant"]["id"])),  # type: ignore[index]
        user_id=UUID(str(auth["user"]["id"])),  # type: ignore[index]
        project_id=project_id,
        slug=str(auth["tenant"]["slug"]),  # type: ignore[index]
        csrf=csrf,
    )


async def _invite_member(
    owner: AsyncClient,
    context: TenantContext,
    *,
    suffix: str,
    role: str,
) -> tuple[AsyncClient, UUID, str]:
    email = f"background-{role}-{suffix}@example.com"
    password = "SecureInvite123!"  # noqa: S105 - isolated test credential
    invitation = await owner.post(
        "/api/v1/team/invitations",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"background-invite-{role}-{suffix}",
        },
        json={
            "email": email,
            "role": role,
            "project_ids": [str(context.project_id)],
        },
    )
    assert invitation.status_code == 201, invitation.text
    token = invitation.json()["acceptance_token"]
    assert token

    member_client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    accepted = await member_client.post(
        "/api/v1/team/invitations/accept",
        json={
            "tenant_slug": context.slug,
            "token": token,
            "display_name": f"Background {role}",
            "password": password,
        },
    )
    assert accepted.status_code == 200, accepted.text
    membership_id = UUID(accepted.json()["membership_id"])
    login = await member_client.post(
        "/api/v1/auth/login",
        json={"company_slug": context.slug, "email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    async with tenant_transaction(context.tenant_id) as session:
        membership = await session.scalar(
            select(Membership).where(
                Membership.tenant_id == context.tenant_id,
                Membership.id == membership_id,
            )
        )
        assert membership is not None
        user_id = membership.user_id
    return member_client, user_id, login.json()["csrf_token"]


async def _enqueue(
    context: TenantContext,
    *,
    key: str,
    job_type: str = "test.background",
    created_by_user_id: UUID | None = None,
) -> BackgroundJob:
    async with tenant_transaction(context.tenant_id) as session:
        job, created = await enqueue_background_job(
            session,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            created_by_user_id=created_by_user_id or context.user_id,
            job_type=job_type,
            queue="tests",
            priority=40,
            safe_payload={"project_id": context.project_id, "scan_scope": "test"},
            idempotency_key=key,
            correlation_id=f"correlation-{key}",
        )
        assert created is True
        await session.flush()
        job_id = job.id
    async with tenant_transaction(context.tenant_id) as session:
        persisted = await session.scalar(
            select(BackgroundJob).where(
                BackgroundJob.tenant_id == context.tenant_id,
                BackgroundJob.id == job_id,
            )
        )
        assert persisted is not None
        session.expunge(persisted)
        return persisted


async def test_transactional_enqueue_rollback_and_idempotency(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    context = await _tenant_context(client, unique_suffix, register)
    job_key = f"transactional-{unique_suffix}"
    import_id = uuid4()
    async with tenant_transaction(context.tenant_id) as session:
        first, created = await enqueue_background_job(
            session,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            created_by_user_id=context.user_id,
            job_type="customer_import.preview",
            queue="imports",
            priority=60,
            safe_payload={"import_id": import_id, "project_id": context.project_id},
            idempotency_key=job_key,
            correlation_id=f"enqueue-{unique_suffix}",
        )
        repeated, repeated_created = await enqueue_background_job(
            session,
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            created_by_user_id=context.user_id,
            job_type="customer_import.preview",
            queue="imports",
            priority=60,
            safe_payload={"import_id": import_id, "project_id": context.project_id},
            idempotency_key=job_key,
            correlation_id=f"enqueue-repeat-{unique_suffix}",
        )
        assert created is True
        assert repeated_created is False
        assert repeated.id == first.id
        committed_id = first.id

        with pytest.raises(ApiError) as reused:
            await enqueue_background_job(
                session,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                created_by_user_id=context.user_id,
                job_type="customer_import.preview",
                queue="imports",
                priority=60,
                safe_payload={"import_id": uuid4(), "project_id": context.project_id},
                idempotency_key=job_key,
                correlation_id=f"enqueue-conflict-{unique_suffix}",
            )
        assert reused.value.code == "idempotency_key_reused"

    async with tenant_transaction(context.tenant_id) as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(BackgroundJob).where(BackgroundJob.id == committed_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(BackgroundJobEvent)
                .where(BackgroundJobEvent.job_id == committed_id)
            )
            == 1
        )

    concurrent_key = f"concurrent-{unique_suffix}"
    concurrent_import_id = uuid4()

    async def enqueue_concurrently() -> tuple[UUID, bool]:
        async with tenant_transaction(context.tenant_id) as session:
            job, created = await enqueue_background_job(
                session,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                created_by_user_id=context.user_id,
                job_type="customer_import.preview",
                queue="imports",
                priority=60,
                safe_payload={
                    "import_id": concurrent_import_id,
                    "project_id": context.project_id,
                },
                idempotency_key=concurrent_key,
                correlation_id=f"concurrent-{unique_suffix}",
            )
            return job.id, created

    concurrent_results = await asyncio.gather(enqueue_concurrently(), enqueue_concurrently())
    assert len({job_id for job_id, _created in concurrent_results}) == 1
    assert sorted(created for _job_id, created in concurrent_results) == [False, True]

    rollback_key = f"rollback-{unique_suffix}"
    with pytest.raises(RuntimeError, match="force rollback"):
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, context.tenant_id)
            await enqueue_background_job(
                session,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                created_by_user_id=context.user_id,
                job_type="customer_import.commit",
                queue="imports",
                safe_payload={"import_id": uuid4()},
                idempotency_key=rollback_key,
                correlation_id=f"rollback-{unique_suffix}",
            )
            raise RuntimeError("force rollback")

    async with tenant_transaction(context.tenant_id) as session:
        assert (
            await session.scalar(
                select(BackgroundJob.id).where(BackgroundJob.idempotency_key == rollback_key)
            )
            is None
        )
        with pytest.raises(ValueError, match="Sensitive background job payload"):
            await enqueue_background_job(
                session,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                job_type="unsafe.test",
                safe_payload={"api_token": "must-not-be-stored"},
                idempotency_key=f"unsafe-{unique_suffix}",
                correlation_id=f"unsafe-{unique_suffix}",
            )


async def test_job_rbac_project_scope_and_tenant_isolation(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    context = await _tenant_context(client, f"jobs-a-{unique_suffix}", register)
    owner_job = await _enqueue(context, key=f"owner-{unique_suffix}")
    operator, operator_user_id, operator_csrf = await _invite_member(
        client,
        context,
        suffix=f"operator-{unique_suffix}",
        role="human_operator",
    )
    analyst, _analyst_user_id, analyst_csrf = await _invite_member(
        client,
        context,
        suffix=f"analyst-{unique_suffix}",
        role="analyst",
    )
    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        operator_job = await _enqueue(
            context,
            key=f"operator-{unique_suffix}",
            created_by_user_id=operator_user_id,
        )
        operator_list = await operator.get("/api/v1/background-jobs")
        assert operator_list.status_code == 200, operator_list.text
        assert [item["id"] for item in operator_list.json()["items"]] == [str(operator_job.id)]
        operator_manage = await operator.post(
            f"/api/v1/background-jobs/{operator_job.id}/cancel",
            headers={
                "X-CSRF-Token": operator_csrf,
                "Idempotency-Key": f"operator-cancel-{unique_suffix}",
            },
            json={"expected_version": 1},
        )
        assert operator_manage.status_code == 403

        analyst_list = await analyst.get("/api/v1/background-jobs")
        assert analyst_list.status_code == 200, analyst_list.text
        assert {item["id"] for item in analyst_list.json()["items"]} >= {
            str(owner_job.id),
            str(operator_job.id),
        }
        analyst_manage = await analyst.post(
            f"/api/v1/background-jobs/{owner_job.id}/cancel",
            headers={
                "X-CSRF-Token": analyst_csrf,
                "Idempotency-Key": f"analyst-cancel-{unique_suffix}",
            },
            json={"expected_version": 1},
        )
        assert analyst_manage.status_code == 403

        foreign_context = await _tenant_context(other, f"jobs-b-{unique_suffix}", register)
        foreign_job = await _enqueue(foreign_context, key=f"foreign-{unique_suffix}")
        hidden = await other.get(f"/api/v1/background-jobs/{owner_job.id}")
        assert hidden.status_code == 404
        hidden_reverse = await client.get(f"/api/v1/background-jobs/{foreign_job.id}")
        assert hidden_reverse.status_code == 404
        foreign_project = await client.get(f"/api/v1/background-jobs?project_id={foreign_context.project_id}")
        assert foreign_project.status_code in {404, 422}

        async with tenant_transaction(foreign_context.tenant_id) as session:
            assert (
                await session.scalar(select(BackgroundJob.id).where(BackgroundJob.id == owner_job.id)) is None
            )
    finally:
        await operator.aclose()
        await analyst.aclose()
        await other.aclose()


async def test_job_cancel_retry_optimistic_version_and_command_idempotency(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    context = await _tenant_context(client, unique_suffix, register)
    cancel_job = await _enqueue(context, key=f"cancel-{unique_suffix}")
    headers = {
        "X-CSRF-Token": context.csrf,
        "Idempotency-Key": f"cancel-command-{unique_suffix}",
    }
    cancelled = await client.post(
        f"/api/v1/background-jobs/{cancel_job.id}/cancel",
        headers=headers,
        json={"expected_version": cancel_job.lock_version, "reason": "test cancellation"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["state_version"] == cancel_job.lock_version + 1

    replay = await client.post(
        f"/api/v1/background-jobs/{cancel_job.id}/cancel",
        headers=headers,
        json={"expected_version": cancel_job.lock_version, "reason": "test cancellation"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["state_version"] == cancelled.json()["state_version"]
    conflict = await client.post(
        f"/api/v1/background-jobs/{cancel_job.id}/cancel",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"cancel-stale-{unique_suffix}",
        },
        json={"expected_version": cancel_job.lock_version},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "background_job_conflict"

    retry_job = await _enqueue(context, key=f"retry-{unique_suffix}")
    async with tenant_transaction(context.tenant_id) as session:
        persisted = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == retry_job.id).with_for_update()
        )
        assert persisted is not None
        persisted.status = "dead_letter"
        persisted.attempt_count = persisted.max_attempts
        persisted.safe_error_code = "temporary_failure"
    retried = await client.post(
        f"/api/v1/background-jobs/{retry_job.id}/retry",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"retry-command-{unique_suffix}",
        },
        json={"expected_version": retry_job.lock_version, "reason": "reviewed manual retry"},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "pending"
    assert retried.json()["safe_error_code"] is None
    assert retried.json()["max_attempts"] == retry_job.max_attempts + 1

    async with tenant_transaction(context.tenant_id) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(JobCommandSubmission)
                .where(JobCommandSubmission.job_id == cancel_job.id)
            )
            == 1
        )
        event_types = set(
            await session.scalars(
                select(BackgroundJobEvent.event_type).where(
                    BackgroundJobEvent.job_id.in_((cancel_job.id, retry_job.id))
                )
            )
        )
        assert {"cancelled", "manual_retry_requested"} <= event_types


async def test_retention_purged_job_payload_disables_and_rejects_retry(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    context = await _tenant_context(client, f"purged-job-{unique_suffix}", register)
    job = await _enqueue(context, key=f"purged-retry-{unique_suffix}")
    async with tenant_transaction(context.tenant_id) as session:
        persisted = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == job.id).with_for_update()
        )
        assert persisted is not None
        persisted.status = "dead_letter"
        persisted.result_metadata = {"retention_payload_purged": True}

    detail = await client.get(f"/api/v1/background-jobs/{job.id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["can_retry"] is False

    rejected = await client.post(
        f"/api/v1/background-jobs/{job.id}/retry",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"purged-retry-command-{unique_suffix}",
        },
        json={"expected_version": job.lock_version, "reason": "must not execute empty payload"},
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "background_job_payload_purged"

    async with tenant_transaction(context.tenant_id) as session:
        persisted = await session.scalar(select(BackgroundJob).where(BackgroundJob.id == job.id))
        assert persisted is not None
        assert persisted.status == "dead_letter"
        assert persisted.result_metadata == {"retention_payload_purged": True}
        assert (
            await session.scalar(
                select(func.count())
                .select_from(JobCommandSubmission)
                .where(JobCommandSubmission.job_id == job.id)
            )
            == 0
        )


async def test_domain_jobs_reject_generic_commands(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    context = await _tenant_context(client, f"domain-command-{unique_suffix}", register)
    job = await _enqueue(
        context,
        key=f"domain-import-{unique_suffix}",
        job_type="customer_import.process",
    )
    response = await client.post(
        f"/api/v1/background-jobs/{job.id}/cancel",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"domain-cancel-{unique_suffix}",
        },
        json={"expected_version": job.lock_version},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "domain_job_command_required"
    async with tenant_transaction(context.tenant_id) as session:
        persisted = await session.scalar(select(BackgroundJob).where(BackgroundJob.id == job.id))
        assert persisted is not None
        assert persisted.status == "pending"


async def test_background_import_request_idempotency_and_exact_command_replay(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = await _tenant_context(client, f"background-import-{unique_suffix}", register)
    uploads: list[str] = []

    class FakeStorage:
        def __init__(self, _settings: object) -> None:
            pass

        async def put_bytes(self, *, key: str, data: bytes, content_type: str) -> StoredObjectMetadata:
            uploads.append(key)
            return StoredObjectMetadata(
                checksum_sha256=sha256(data).hexdigest(),
                size_bytes=len(data),
                content_type=content_type,
            )

    monkeypatch.setattr(
        "teamora_api.routers.customer_imports.PrivateObjectStorage",
        FakeStorage,
    )
    preview_key = f"import-preview-{unique_suffix}"
    csv_content = b"name,phone\nAlice,+998901234567\n"
    preview = await client.post(
        "/api/v1/customers/import/background/preview",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": preview_key},
        data={"project_id": str(context.project_id)},
        files={"file": ("customers.csv", csv_content, "text/csv")},
    )
    assert preview.status_code == 202, preview.text
    import_id = UUID(preview.json()["id"])
    preview_job_id = UUID(preview.json()["job_id"])

    replay = await client.post(
        "/api/v1/customers/import/background/preview",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": preview_key},
        data={"project_id": str(context.project_id)},
        files={"file": ("customers.csv", csv_content, "text/csv")},
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["id"] == str(import_id)
    assert uploads == [uploads[0]]

    mismatched = await client.post(
        "/api/v1/customers/import/background/preview",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": preview_key},
        data={"project_id": str(context.project_id)},
        files={"file": ("customers.csv", b"name\nDifferent\n", "text/csv")},
    )
    assert mismatched.status_code == 409, mismatched.text
    assert mismatched.json()["error"]["code"] == "idempotency_key_reused"
    assert len(uploads) == 1

    expired_key = f"import-expired-{unique_suffix}"
    expired_preview = await client.post(
        "/api/v1/customers/import/background/preview",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": expired_key},
        data={"project_id": str(context.project_id)},
        files={"file": ("expired.csv", csv_content, "text/csv")},
    )
    assert expired_preview.status_code == 202, expired_preview.text
    expired_import_id = UUID(expired_preview.json()["id"])
    expired_job_id = UUID(expired_preview.json()["job_id"])
    async with tenant_transaction(context.tenant_id) as session:
        expired_import = await session.scalar(
            select(CustomerImport).where(CustomerImport.id == expired_import_id).with_for_update()
        )
        expired_job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == expired_job_id).with_for_update()
        )
        assert expired_import is not None
        assert expired_job is not None
        expired_import.status = "ready"
        expired_import.mapping = {"display_name": "name"}
        expired_import.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        expired_job.status = "completed"
    expired_commit = await client.post(
        f"/api/v1/customers/import/background/{expired_import_id}/background-commit",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"expired-commit-{unique_suffix}",
        },
        json={"expected_version": 1},
    )
    assert expired_commit.status_code == 410, expired_commit.text
    assert expired_commit.json()["error"]["code"] == "customer_import_expired"
    async with tenant_transaction(context.tenant_id) as session:
        expired_status = await session.scalar(
            select(CustomerImport.status).where(CustomerImport.id == expired_import_id)
        )
        assert expired_status == "expired"

    async with tenant_transaction(context.tenant_id) as session:
        import_record = await session.scalar(
            select(CustomerImport).where(CustomerImport.id == import_id).with_for_update()
        )
        preview_job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == preview_job_id).with_for_update()
        )
        assert import_record is not None
        assert preview_job is not None
        import_record.status = "ready"
        import_record.mapping = {"display_name": "name", "phone": "phone"}
        import_record.sheet_names = ["CSV"]
        import_record.selected_sheet = "CSV"
        import_record.source_rows = {"CSV": {"headers": ["name", "phone"], "rows": [{"name": "Alice"}]}}
        import_record.row_count = 1
        import_record.total_rows = 1
        import_record.progress = 100
        preview_job.status = "completed"
        preview_job.progress = 100
        preview_job.completed_at = datetime.now(UTC)

    normalized_preview = await client.get(f"/api/v1/customers/import/background/{import_id}/status")
    assert normalized_preview.status_code == 200, normalized_preview.text
    assert normalized_preview.json()["preview_rows"] == [
        {
            "row_number": 2,
            "values": {"name": "Alice"},
            "duplicate_fields": [],
            "errors": [],
        }
    ]

    commit_key = f"import-commit-{unique_suffix}"
    commit = await client.post(
        f"/api/v1/customers/import/background/{import_id}/background-commit",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": commit_key},
        json={"expected_version": 1},
    )
    assert commit.status_code == 202, commit.text
    assert commit.json()["status"] == "queued"
    process_job_id = UUID(commit.json()["job_id"])
    commit_replay = await client.post(
        f"/api/v1/customers/import/background/{import_id}/background-commit",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": commit_key},
        json={"expected_version": 1},
    )
    assert commit_replay.status_code == 202, commit_replay.text
    assert commit_replay.json()["job_id"] == str(process_job_id)

    changed_replay = await client.post(
        f"/api/v1/customers/import/background/{import_id}/background-commit",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": commit_key},
        json={"expected_version": 2},
    )
    assert changed_replay.status_code == 409, changed_replay.text
    assert changed_replay.json()["error"]["code"] == "idempotency_key_reused"

    cancel_key = f"import-cancel-{unique_suffix}"
    cancel = await client.post(
        f"/api/v1/customers/import/background/{import_id}/cancel",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": cancel_key},
        json={"expected_version": 1},
    )
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "cancelled"
    cancel_replay = await client.post(
        f"/api/v1/customers/import/background/{import_id}/cancel",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": cancel_key},
        json={"expected_version": 1},
    )
    assert cancel_replay.status_code == 200, cancel_replay.text
    assert cancel_replay.json()["state_version"] == cancel.json()["state_version"]

    async with tenant_transaction(context.tenant_id) as session:
        failed_import = await session.scalar(
            select(CustomerImport).where(CustomerImport.id == import_id).with_for_update()
        )
        failed_job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == process_job_id).with_for_update()
        )
        assert failed_import is not None
        assert failed_job is not None
        failed_import.status = "failed"
        failed_job.status = "failed"
        retry_version = failed_job.lock_version

    retry_key = f"import-retry-{unique_suffix}"
    retry = await client.post(
        f"/api/v1/customers/import/background/{import_id}/retry",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": retry_key},
        json={"expected_version": retry_version},
    )
    assert retry.status_code == 202, retry.text
    assert retry.json()["status"] == "queued"
    assert retry.json()["can_retry"] is False
    retry_replay = await client.post(
        f"/api/v1/customers/import/background/{import_id}/retry",
        headers={"X-CSRF-Token": context.csrf, "Idempotency-Key": retry_key},
        json={"expected_version": retry_version},
    )
    assert retry_replay.status_code == 202, retry_replay.text
    assert retry_replay.json()["state_version"] == retry.json()["state_version"]

    async with tenant_transaction(context.tenant_id) as session:
        report_import = await session.scalar(
            select(CustomerImport).where(CustomerImport.id == import_id).with_for_update()
        )
        report_job = await session.scalar(
            select(BackgroundJob).where(BackgroundJob.id == process_job_id).with_for_update()
        )
        assert report_import is not None
        assert report_job is not None
        report_import.status = "completed"
        report_import.processing_completed_at = datetime.now(UTC)
        report_import.report_storage_object_id = None
        report_job.status = "dead_letter"
        report_job.safe_error_code = "customer_import_report_storage_unavailable"
        report_retry_version = report_job.lock_version

    report_retry = await client.post(
        f"/api/v1/customers/import/background/{import_id}/retry",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"import-report-retry-{unique_suffix}",
        },
        json={"expected_version": report_retry_version},
    )
    assert report_retry.status_code == 202, report_retry.text
    assert report_retry.json()["status"] == "completed"
    assert report_retry.json()["can_retry"] is False
    assert report_retry.json()["completed_at"] is not None

    async with tenant_transaction(context.tenant_id) as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(BackgroundJob)
                .where(
                    BackgroundJob.tenant_id == context.tenant_id,
                    BackgroundJob.type == "customer_import.process",
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(JobCommandSubmission)
                .where(JobCommandSubmission.job_id == process_job_id)
            )
            == 4
        )


async def test_storage_summary_scan_and_rls(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    context = await _tenant_context(client, f"storage-a-{unique_suffix}", register)
    storage_id = uuid4()
    async with tenant_transaction(context.tenant_id) as session:
        session.add(
            StorageObject(
                id=storage_id,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                bucket="teamora-private",
                object_key=f"tenants/{context.tenant_id}/tests/{storage_id}",
                category="import_source",
                owner_aggregate_type="customer_import",
                owner_aggregate_id=uuid4(),
                checksum_sha256="a" * 64,
                size_bytes=1234,
                content_type="text/csv",
                status="active",
                retention_state="retained",
                lock_version=1,
            )
        )
    summary = await client.get("/api/v1/storage/summary")
    assert summary.status_code == 200, summary.text
    assert summary.json()["object_count"] == 1
    assert summary.json()["total_bytes"] == 1234
    assert summary.json()["categories"] == [{"category": "import_source", "object_count": 1, "bytes": 1234}]

    scan_headers = {
        "X-CSRF-Token": context.csrf,
        "Idempotency-Key": f"storage-scan-{unique_suffix}",
    }
    scan = await client.post(
        "/api/v1/storage/scan",
        headers=scan_headers,
        json={"project_id": str(context.project_id), "dry_run": True},
    )
    assert scan.status_code == 202, scan.text
    scan_replay = await client.post(
        "/api/v1/storage/scan",
        headers=scan_headers,
        json={"project_id": str(context.project_id), "dry_run": True},
    )
    assert scan_replay.status_code == 202
    assert scan_replay.json()["job_id"] == scan.json()["job_id"]

    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        foreign = await _tenant_context(other, f"storage-b-{unique_suffix}", register)
        foreign_summary = await other.get("/api/v1/storage/summary")
        assert foreign_summary.status_code == 200
        assert foreign_summary.json()["object_count"] == 0
        async with tenant_transaction(foreign.tenant_id) as session:
            assert (
                await session.scalar(select(StorageObject.id).where(StorageObject.id == storage_id)) is None
            )
            assert (
                await session.scalar(
                    select(StorageConsistencyIssue.id).where(
                        StorageConsistencyIssue.tenant_id == context.tenant_id
                    )
                )
                is None
            )
    finally:
        await other.aclose()


async def test_retention_disabled_preview_legal_hold_and_candidate_cancellation(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    context = await _tenant_context(client, unique_suffix, register)
    policy_response = await client.get("/api/v1/retention/policy")
    assert policy_response.status_code == 200, policy_response.text
    policy = policy_response.json()
    assert policy["automatic_purge_enabled"] is False

    updated = await client.patch(
        "/api/v1/retention/policy",
        headers={"X-CSRF-Token": context.csrf},
        json={
            "expected_version": policy["state_version"],
            "archived_knowledge_days": 30,
            "grace_period_days": 7,
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["state_version"] == policy["state_version"] + 1
    stale = await client.patch(
        "/api/v1/retention/policy",
        headers={"X-CSRF-Token": context.csrf},
        json={"expected_version": policy["state_version"], "temporary_import_days": 2},
    )
    assert stale.status_code == 409

    storage_id = uuid4()
    resource_id = uuid4()
    async with tenant_transaction(context.tenant_id) as session:
        session.add(
            StorageObject(
                id=storage_id,
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                bucket="teamora-private",
                object_key=f"tenants/{context.tenant_id}/knowledge/archive/{storage_id}",
                category="knowledge_original",
                owner_aggregate_type="knowledge_document_version",
                owner_aggregate_id=resource_id,
                checksum_sha256="b" * 64,
                size_bytes=4096,
                content_type="text/plain",
                status="archived",
                retention_state="retained",
                archived_at=datetime.now(UTC) - timedelta(days=60),
                lock_version=1,
            )
        )

    preview_headers = {
        "X-CSRF-Token": context.csrf,
        "Idempotency-Key": f"retention-preview-{unique_suffix}",
    }
    preview = await client.post(
        "/api/v1/retention/preview",
        headers=preview_headers,
        json={"policy_version": updated.json()["state_version"]},
    )
    assert preview.status_code == 201, preview.text
    assert preview.json()["eligible_objects"] == 1
    assert preview.json()["eligible_bytes"] == 4096
    preview_replay = await client.post(
        "/api/v1/retention/preview",
        headers=preview_headers,
        json={"policy_version": updated.json()["state_version"]},
    )
    assert preview_replay.status_code == 201
    assert preview_replay.json()["id"] == preview.json()["id"]

    disabled_run = await client.post(
        "/api/v1/retention/run",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"retention-run-{unique_suffix}",
        },
        json={
            "preview_id": preview.json()["id"],
            "policy_version": updated.json()["state_version"],
        },
    )
    assert disabled_run.status_code == 409
    assert disabled_run.json()["error"]["code"] == "retention_automatic_purge_disabled"

    hold_headers = {
        "X-CSRF-Token": context.csrf,
        "Idempotency-Key": f"legal-hold-{unique_suffix}",
    }
    hold = await client.post(
        "/api/v1/legal-holds",
        headers=hold_headers,
        json={
            "scope_type": "tenant",
            "scope_id": None,
            "reason": "Preserve data for a test investigation",
        },
    )
    assert hold.status_code == 201, hold.text
    hold_replay = await client.post(
        "/api/v1/legal-holds",
        headers=hold_headers,
        json={
            "scope_type": "tenant",
            "scope_id": None,
            "reason": "Preserve data for a test investigation",
        },
    )
    assert hold_replay.status_code == 201
    assert hold_replay.json()["id"] == hold.json()["id"]
    hold_key_reuse = await client.post(
        "/api/v1/legal-holds",
        headers=hold_headers,
        json={
            "scope_type": "tenant",
            "scope_id": None,
            "reason": "A different request must not reuse the command key",
        },
    )
    assert hold_key_reuse.status_code == 409
    assert hold_key_reuse.json()["error"]["code"] == "idempotency_key_reused"

    held_preview = await client.post(
        "/api/v1/retention/preview",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"retention-held-{unique_suffix}",
        },
        json={"policy_version": updated.json()["state_version"]},
    )
    assert held_preview.status_code == 201, held_preview.text
    assert held_preview.json()["eligible_objects"] == 0
    assert held_preview.json()["excluded_by_legal_hold"] == 1

    released = await client.post(
        f"/api/v1/legal-holds/{hold.json()['id']}/release",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"release-hold-{unique_suffix}",
        },
        json={"expected_version": hold.json()["state_version"], "reason": "review complete"},
    )
    assert released.status_code == 200, released.text
    assert released.json()["released_at"] is not None
    released_replay = await client.post(
        f"/api/v1/legal-holds/{hold.json()['id']}/release",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"release-hold-{unique_suffix}",
        },
        json={"expected_version": hold.json()["state_version"], "reason": "review complete"},
    )
    assert released_replay.status_code == 200
    assert released_replay.json() == released.json()

    async with tenant_transaction(context.tenant_id) as session:
        retained_policy = await session.scalar(
            select(RetentionPolicy).where(
                RetentionPolicy.tenant_id == context.tenant_id,
                RetentionPolicy.project_id.is_(None),
            )
        )
        storage = await session.scalar(select(StorageObject).where(StorageObject.id == storage_id))
        assert retained_policy is not None and storage is not None
        storage.retention_state = "pending_purge"
        storage.pending_purge_at = datetime.now(UTC)
        candidate = RetentionCandidate(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            policy_id=retained_policy.id,
            storage_object_id=storage.id,
            category="knowledge_original",
            resource_type="knowledge_document_version",
            resource_id=resource_id,
            policy_version=retained_policy.policy_version,
            status="pending_purge",
            eligible_at=datetime.now(UTC) - timedelta(days=1),
            pending_purge_at=datetime.now(UTC),
            grace_until=datetime.now(UTC) + timedelta(days=7),
            idempotency_key=f"candidate-{unique_suffix}",
            safe_metadata={},
            lock_version=1,
        )
        session.add(candidate)
        await session.flush()
        candidate_id = candidate.id

    cancelled = await client.post(
        f"/api/v1/retention/candidates/{candidate_id}/cancel",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"cancel-candidate-{unique_suffix}",
        },
        json={"expected_version": 1, "reason": "administrator cancelled purge"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["state_version"] == 2
    cancelled_replay = await client.post(
        f"/api/v1/retention/candidates/{candidate_id}/cancel",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"cancel-candidate-{unique_suffix}",
        },
        json={"expected_version": 1, "reason": "administrator cancelled purge"},
    )
    assert cancelled_replay.status_code == 200, cancelled_replay.text
    assert cancelled_replay.json()["id"] == cancelled.json()["id"]
    assert cancelled_replay.json()["state_version"] == cancelled.json()["state_version"]
    cancelled_key_reuse = await client.post(
        f"/api/v1/retention/candidates/{candidate_id}/cancel",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"cancel-candidate-{unique_suffix}",
        },
        json={"expected_version": 1, "reason": "a different cancellation request"},
    )
    assert cancelled_key_reuse.status_code == 409
    assert cancelled_key_reuse.json()["error"]["code"] == "idempotency_key_reused"
    async with tenant_transaction(context.tenant_id) as session:
        storage = await session.scalar(select(StorageObject).where(StorageObject.id == storage_id))
        assert storage is not None
        assert storage.retention_state == "retained"
        assert storage.pending_purge_at is None

    other = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        foreign = await _tenant_context(other, f"retention-foreign-{unique_suffix}", register)
        async with tenant_transaction(foreign.tenant_id) as session:
            assert (
                await session.scalar(select(LegalHold.id).where(LegalHold.id == UUID(hold.json()["id"])))
                is None
            )
            assert (
                await session.scalar(
                    select(RetentionCandidate.id).where(RetentionCandidate.id == candidate_id)
                )
                is None
            )
    finally:
        await other.aclose()


async def test_database_retention_preview_rechecks_holds_and_run_replays(
    client: AsyncClient,
    unique_suffix: str,
    register: Register,
) -> None:
    context = await _tenant_context(client, f"database-retention-{unique_suffix}", register)
    policy_response = await client.get("/api/v1/retention/policy")
    assert policy_response.status_code == 200, policy_response.text
    updated = await client.patch(
        "/api/v1/retention/policy",
        headers={"X-CSRF-Token": context.csrf},
        json={
            "expected_version": policy_response.json()["state_version"],
            "automatic_purge_enabled": True,
            "realtime_event_hours": 24,
        },
    )
    assert updated.status_code == 200, updated.text
    policy_version = updated.json()["state_version"]
    now = datetime.now(UTC)
    aged_at = now - timedelta(days=2)

    async with tenant_transaction(context.tenant_id) as session:
        policy = await session.scalar(
            select(RetentionPolicy).where(
                RetentionPolicy.tenant_id == context.tenant_id,
                RetentionPolicy.project_id.is_(None),
            )
        )
        assert policy is not None
        customer = Customer(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            display_name="Held realtime customer",
            status="active",
        )
        session.add(customer)
        await session.flush()
        call = Call(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            channel=CallChannel.DEVELOPMENT_SIMULATOR,
            status=CallStatus.COMPLETED,
            direction=CallDirection.OUTBOUND,
            caller_type=CallerType.HUMAN_OPERATOR,
            customer_id=customer.id,
            provider="mock",
            provider_state="completed",
            ended_at=aged_at,
        )
        session.add(call)
        await session.flush()

        document_id = uuid4()
        aggregate_specs = (
            ("call", call.id),
            ("customer", customer.id),
            ("document", document_id),
        )
        candidate_ids: list[UUID] = []
        for aggregate_type, aggregate_id in aggregate_specs:
            event = RealtimeEvent(
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                event_type=f"retention.test.{aggregate_type}",
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                safe_payload={},
                occurred_at=aged_at,
                publish_status="published",
                published_at=aged_at,
                expires_at=now + timedelta(days=30),
                correlation_id=f"database-retention-{unique_suffix}",
                created_at=aged_at,
                updated_at=aged_at,
            )
            session.add(event)
            await session.flush()
            candidate = RetentionCandidate(
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                policy_id=policy.id,
                storage_object_id=None,
                category="realtime_event",
                resource_type="realtime_event",
                resource_id=event.id,
                policy_version=policy.policy_version,
                status="eligible",
                eligible_at=aged_at,
                idempotency_key=f"database-retention-{aggregate_type}-{unique_suffix}",
                safe_metadata={},
                lock_version=1,
            )
            session.add(candidate)
            await session.flush()
            candidate_ids.append(candidate.id)

        customer_hold = LegalHold(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            scope_type="customer",
            scope_id=customer.id,
            reason="Protect customer realtime history",
            created_by_user_id=context.user_id,
            lock_version=1,
        )
        document_hold = LegalHold(
            tenant_id=context.tenant_id,
            project_id=context.project_id,
            scope_type="document",
            scope_id=document_id,
            reason="Protect document realtime history",
            created_by_user_id=context.user_id,
            lock_version=1,
        )
        session.add_all((customer_hold, document_hold))
        await session.flush()
        customer_hold_id = customer_hold.id
        document_hold_id = document_hold.id

    first_preview = await client.post(
        "/api/v1/retention/preview",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"database-retention-held-{unique_suffix}",
        },
        json={"policy_version": policy_version},
    )
    assert first_preview.status_code == 201, first_preview.text
    assert first_preview.json()["eligible_objects"] == 0
    assert first_preview.json()["excluded_by_legal_hold"] == 3
    async with tenant_transaction(context.tenant_id) as session:
        candidates = list(
            (
                await session.scalars(
                    select(RetentionCandidate).where(RetentionCandidate.id.in_(candidate_ids))
                )
            ).all()
        )
        assert {candidate.status for candidate in candidates} == {"blocked"}
        assert all(candidate.safe_metadata == {"blocked_reason": "legal_hold"} for candidate in candidates)
        holds = list(
            (
                await session.scalars(
                    select(LegalHold).where(LegalHold.id.in_((customer_hold_id, document_hold_id)))
                )
            ).all()
        )
        for hold in holds:
            hold.released_at = now
            hold.released_by_user_id = context.user_id
            hold.lock_version += 1

    released_preview = await client.post(
        "/api/v1/retention/preview",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"database-retention-released-{unique_suffix}",
        },
        json={"policy_version": policy_version},
    )
    assert released_preview.status_code == 201, released_preview.text
    assert released_preview.json()["eligible_objects"] == 3
    assert released_preview.json()["excluded_by_legal_hold"] == 0
    async with tenant_transaction(context.tenant_id) as session:
        candidates = list(
            (
                await session.scalars(
                    select(RetentionCandidate).where(RetentionCandidate.id.in_(candidate_ids))
                )
            ).all()
        )
        assert {candidate.status for candidate in candidates} == {"eligible"}
        assert all(
            candidate.safe_metadata.get("preview_id") == released_preview.json()["id"]
            for candidate in candidates
        )
        session.add(
            LegalHold(
                tenant_id=context.tenant_id,
                project_id=context.project_id,
                scope_type="call",
                scope_id=call.id,
                reason="Protect only the call aggregate",
                created_by_user_id=context.user_id,
                lock_version=1,
            )
        )

    changed_preview = await client.post(
        "/api/v1/retention/preview",
        headers={
            "X-CSRF-Token": context.csrf,
            "Idempotency-Key": f"database-retention-changed-{unique_suffix}",
        },
        json={"policy_version": policy_version},
    )
    assert changed_preview.status_code == 201, changed_preview.text
    assert changed_preview.json()["eligible_objects"] == 2
    assert changed_preview.json()["excluded_by_legal_hold"] == 1

    run_headers = {
        "X-CSRF-Token": context.csrf,
        "Idempotency-Key": f"database-retention-run-{unique_suffix}",
    }
    run = await client.post(
        "/api/v1/retention/run",
        headers=run_headers,
        json={"preview_id": changed_preview.json()["id"], "policy_version": policy_version},
    )
    assert run.status_code == 202, run.text
    async with tenant_transaction(context.tenant_id) as session:
        reviewed_candidate = await session.scalar(
            select(RetentionCandidate).where(
                RetentionCandidate.id.in_(candidate_ids),
                RetentionCandidate.status == "eligible",
            )
        )
        assert reviewed_candidate is not None
        reviewed_candidate.status = "pending_purge"
        reviewed_candidate.pending_purge_at = now
        reviewed_candidate.grace_until = now + timedelta(days=7)

    replay = await client.post(
        "/api/v1/retention/run",
        headers=run_headers,
        json={"preview_id": changed_preview.json()["id"], "policy_version": policy_version},
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["job_id"] == run.json()["job_id"]
