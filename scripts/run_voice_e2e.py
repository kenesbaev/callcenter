from __future__ import annotations

import asyncio
import secrets
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "api"))

from run_telephony_e2e import (
    assert_local_environment,
    cleanup_fixture,
    configure_route,
    run_local_call,
)
from teamora_api.config import get_settings
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import CallStatus, TaskType
from teamora_api.models import (
    AIRealtimeSession,
    Call,
    CallbackTask,
    CallEvent,
    CallFlowExecution,
    KnowledgeRetrievalExecution,
    Project,
    TranscriptSegment,
    UsageRecord,
)

API_URL = "http://127.0.0.1:8000"
CALLER_NUMBER = "+998900000001"
TERMINAL = {
    CallStatus.COMPLETED,
    CallStatus.BUSY,
    CallStatus.NO_ANSWER,
    CallStatus.FAILED,
    CallStatus.CANCELLED,
}


def require(response: httpx.Response, expected: int) -> dict[str, object]:
    if response.status_code != expected:
        raise RuntimeError(
            f"Fixture API failed ({response.status_code}): {response.text[:500]}"
        )
    return response.json()


async def register_workspace(suffix: str) -> tuple[UUID, str, str, str]:
    slug = f"telephony-e2e-voice-{suffix}"
    password = f"LocalOnly-1-{secrets.token_urlsafe(18)}"
    async with httpx.AsyncClient(base_url=API_URL, timeout=20) as client:
        auth = require(
            await client.post(
                "/api/v1/auth/register",
                json={
                    "company_name": f"Voice E2E {suffix}",
                    "company_slug": slug,
                    "display_name": "Voice E2E Owner",
                    "email": f"voice-e2e-{suffix}@example.com",
                    "password": password,
                },
            ),
            201,
        )
        csrf = str(auth["csrf_token"])
        tenant_id = UUID(str(dict(auth["tenant"])["id"]))
        projects = require(await client.get("/api/v1/projects"), 200)
        project_id = str(next(iter(projects["items"]))["id"])

        customer = require(
            await client.post(
                "/api/v1/customers",
                headers={"X-CSRF-Token": csrf},
                json={
                    "project_id": project_id,
                    "display_name": "Voice E2E Customer",
                    "preferred_language": "ru",
                    "phone": CALLER_NUMBER,
                },
            ),
            201,
        )

        operator = require(
            await client.post(
                "/api/v1/ai-operators",
                headers={"X-CSRF-Token": csrf},
                json={
                    "name": "Voice E2E AI",
                    "description": "Deterministic local realtime operator",
                    "system_instructions": (
                        "Use only verified knowledge, speak briefly, and never reveal secrets."
                    ),
                    "allowed_languages": ["ru", "uz", "en", "kaa-latn", "kaa-cyrl"],
                    "allowed_tools": [
                        "search_knowledge",
                        "create_callback",
                        "request_human_operator",
                        "end_call",
                    ],
                },
            ),
            201,
        )
        published_operator = require(
            await client.post(
                f"/api/v1/ai-operators/{operator['id']}/publish",
                headers={"X-CSRF-Token": csrf},
            ),
            200,
        )

        flow = require(
            await client.post(
                "/api/v1/call-flows",
                headers={"X-CSRF-Token": csrf},
                json={
                    "project_id": project_id,
                    "name": "Voice E2E flow",
                    "description": "Pinned deterministic voice flow",
                    "default_language_code": "ru",
                    "language_codes": ["ru"],
                },
            ),
            201,
        )
        draft = next(
            version
            for version in list(flow["versions"])
            if version["status"] == "draft"
        )
        start_id, end_id = str(uuid4()), str(uuid4())
        saved_flow = require(
            await client.put(
                f"/api/v1/call-flows/{flow['id']}/versions/{draft['id']}",
                headers={"X-CSRF-Token": csrf},
                json={
                    "expected_lock_version": draft["lock_version"],
                    "definition": {
                        "schema_version": 1,
                        "nodes": [
                            {
                                "id": start_id,
                                "system_key": "start",
                                "name": "Start",
                                "node_type": "start",
                                "order": 0,
                                "next_node_id": end_id,
                            },
                            {
                                "id": end_id,
                                "system_key": "end",
                                "name": "End",
                                "node_type": "end",
                                "order": 10,
                                "text_by_language": {"ru": "Спасибо"},
                            },
                        ],
                    },
                },
            ),
            200,
        )
        published_flow = require(
            await client.post(
                f"/api/v1/call-flows/{flow['id']}/versions/{saved_flow['id']}/publish",
                headers={"X-CSRF-Token": csrf},
                json={"expected_lock_version": saved_flow["lock_version"]},
            ),
            200,
        )

        knowledge = require(
            await client.post(
                "/api/v1/knowledge/bases",
                headers={"X-CSRF-Token": csrf},
                json={
                    "project_id": project_id,
                    "name": "Voice E2E knowledge",
                    "description": "Deterministic FAQ citations",
                    "default_language_code": "ru",
                },
            ),
            201,
        )
        require(
            await client.post(
                "/api/v1/knowledge/text",
                headers={
                    "X-CSRF-Token": csrf,
                    "Idempotency-Key": f"voice-e2e-knowledge-{suffix}",
                },
                json={
                    "project_id": project_id,
                    "knowledge_base_id": knowledge["id"],
                    "language": "ru",
                    "title": "Deterministic FAQ",
                    "content": (
                        "Deterministic FAQ confirms that K-Line support is available "
                        "from nine until eighteen on business days."
                    ),
                },
            ),
            201,
        )
        knowledge_state = require(
            await client.get(f"/api/v1/knowledge/bases/{knowledge['id']}"), 200
        )
        draft_revision = dict(knowledge_state["draft_revision"])
        published_knowledge = require(
            await client.post(
                f"/api/v1/knowledge/revisions/{draft_revision['id']}/publish",
                headers={"X-CSRF-Token": csrf},
                json={"expected_version": draft_revision["lock_version"]},
            ),
            200,
        )

        require(
            await client.patch(
                f"/api/v1/projects/{project_id}",
                headers={"X-CSRF-Token": csrf},
                json={
                    "ai_operator_id": operator["id"],
                    "call_flow_id": flow["id"],
                    "default_language": "ru",
                    "recording_enabled": True,
                    "recording_disclosure_required": True,
                },
            ),
            200,
        )
    print("voice fixture published AI, Call Flow, Knowledge and customer", flush=True)
    assert customer["id"]
    assert published_operator["active_version_id"]
    assert published_flow["id"]
    assert published_knowledge["id"]
    return tenant_id, slug, project_id, str(customer["id"])


async def enable_disclosure(tenant_id: UUID, project_id: UUID) -> None:
    """Restore the voice-specific policy after the generic SIP fixture setup."""
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project)
            .where(Project.tenant_id == tenant_id, Project.id == project_id)
            .with_for_update()
        )
        if project is None:
            raise RuntimeError("Voice E2E project was not found")
        project.recording_disclosure_required = True


async def verify_voice_result(
    tenant_id: UUID, project_id: UUID, did: str, customer_id: str
) -> UUID:
    deadline = time.monotonic() + 40
    last = "call missing"
    while time.monotonic() < deadline:
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, tenant_id)
            call = await session.scalar(
                select(Call)
                .where(
                    Call.tenant_id == tenant_id,
                    Call.project_id == project_id,
                    Call.to_number == did,
                )
                .order_by(Call.created_at.desc())
                .limit(1)
            )
            if call is not None:
                realtime = await session.scalar(
                    select(AIRealtimeSession).where(
                        AIRealtimeSession.tenant_id == tenant_id,
                        AIRealtimeSession.call_id == call.id,
                    )
                )
                transcript_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(TranscriptSegment)
                        .where(
                            TranscriptSegment.tenant_id == tenant_id,
                            TranscriptSegment.call_id == call.id,
                            TranscriptSegment.is_final.is_(True),
                        )
                    )
                    or 0
                )
                retrieval_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(KnowledgeRetrievalExecution)
                        .where(
                            KnowledgeRetrievalExecution.tenant_id == tenant_id,
                            KnowledgeRetrievalExecution.call_id == call.id,
                        )
                    )
                    or 0
                )
                callback_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(CallbackTask)
                        .where(
                            CallbackTask.tenant_id == tenant_id,
                            CallbackTask.call_id == call.id,
                            CallbackTask.task_type == TaskType.CALLBACK,
                        )
                    )
                    or 0
                )
                usage_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(UsageRecord)
                        .where(
                            UsageRecord.tenant_id == tenant_id,
                            UsageRecord.call_id == call.id,
                            UsageRecord.provider == "openai",
                        )
                    )
                    or 0
                )
                disclosure_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(CallEvent)
                        .where(
                            CallEvent.tenant_id == tenant_id,
                            CallEvent.call_id == call.id,
                            CallEvent.event_type == "ai.disclosure_played",
                        )
                    )
                    or 0
                )
                flow_execution = await session.scalar(
                    select(CallFlowExecution).where(
                        CallFlowExecution.tenant_id == tenant_id,
                        CallFlowExecution.call_id == call.id,
                    )
                )
                checks = {
                    "terminal": call.status in TERMINAL,
                    "customer": str(call.customer_id) == customer_id,
                    "pinned_ai": call.ai_operator_version_id is not None,
                    "pinned_flow": call.call_flow_version_id is not None,
                    "pinned_knowledge": call.knowledge_base_revision_id is not None,
                    "session_closed": realtime is not None
                    and realtime.state in {"closed", "failed"},
                    "barge_in": realtime is not None
                    and realtime.interruption_count >= 1,
                    "transcripts": transcript_count >= 2,
                    "retrieval": retrieval_count == 1,
                    "callback": callback_count == 1,
                    "usage": usage_count >= 1,
                    "disclosure": disclosure_count == 1,
                    "flow_runtime": flow_execution is not None,
                }
                if all(checks.values()):
                    print(
                        "voice backend verified: pinned revisions, disclosure, transcripts, "
                        "citation retrieval, callback tool, usage and cleanup",
                        flush=True,
                    )
                    return call.id
                last = ", ".join(key for key, value in checks.items() if not value)
        await asyncio.sleep(0.5)
    raise RuntimeError(f"Voice backend verification timed out (pending={last})")


async def verify_cross_tenant_hidden(decoy_tenant: UUID, call_id: UUID) -> None:
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, decoy_tenant)
        leaked = int(
            await session.scalar(
                select(func.count())
                .select_from(AIRealtimeSession)
                .where(AIRealtimeSession.call_id == call_id)
            )
            or 0
        )
        if leaked:
            raise RuntimeError("Cross-tenant AI session visibility detected")


async def cleanup_voice_fixture(tenant_id: UUID, slug: str) -> None:
    """Remove only the exact local voice fixture, including immutable task events."""
    if not slug.startswith("telephony-e2e-voice-"):
        raise RuntimeError("Refusing to clean up a non-voice-E2E tenant")
    settings = get_settings()
    engine = create_async_engine(
        settings.migration_database_url or settings.database_url
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            # Task events are intentionally immutable in normal application
            # traffic. The migration connection is used only by this local,
            # exact-tenant cleanup to remove fixtures after the callback tool.
            await connection.execute(
                text("SET LOCAL session_replication_role = 'replica'")
            )
            await connection.execute(
                text("DELETE FROM task_events WHERE tenant_id=:tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("SET LOCAL session_replication_role = 'origin'")
            )
    finally:
        await engine.dispose()
    await cleanup_fixture(tenant_id, slug)


async def run() -> None:
    assert_local_environment()
    suffix = secrets.token_hex(5)
    did = f"+99871{int(suffix[:8], 16) % 10_000_000:07d}"
    primary_tenant: UUID | None = None
    decoy_tenant: UUID | None = None
    primary_slug = ""
    decoy_slug = ""
    try:
        (
            primary_tenant,
            primary_slug,
            project_text,
            customer_id,
        ) = await register_workspace(suffix)
        project_id = await configure_route(primary_tenant, did)
        if str(project_id) != project_text:
            raise RuntimeError("Voice fixture project changed during telephony setup")
        await enable_disclosure(primary_tenant, project_id)
        (
            decoy_tenant,
            decoy_slug,
            _decoy_project,
            _decoy_customer,
        ) = await register_workspace(f"{suffix}-decoy")
        run_local_call(did)
        call_id = await verify_voice_result(
            primary_tenant, project_id, did, customer_id
        )
        await verify_cross_tenant_hidden(decoy_tenant, call_id)
        print("cross-tenant AI session visibility denied", flush=True)
    finally:
        if primary_tenant is not None:
            await cleanup_voice_fixture(primary_tenant, primary_slug)
        if decoy_tenant is not None:
            await cleanup_voice_fixture(decoy_tenant, decoy_slug)
        print("removed exact local voice E2E tenant fixtures", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
