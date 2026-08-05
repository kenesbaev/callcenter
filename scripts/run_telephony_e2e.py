from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "services" / "api"))

from teamora_api.config import get_settings
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import CallStatus, IntegrationStatus
from teamora_api.models import (
    Call,
    CallDetailRecord,
    CallEvent,
    CallRecording,
    PhoneNumber,
    Project,
    ProjectInboundPhoneNumber,
    SipTrunk,
    TelephonyChannelReservation,
    TelephonyResource,
)

TERMINAL = {
    CallStatus.COMPLETED,
    CallStatus.BUSY,
    CallStatus.NO_ANSWER,
    CallStatus.FAILED,
    CallStatus.CANCELLED,
}


def assert_local_environment() -> None:
    settings = get_settings()
    if settings.app_env in {"staging", "production"}:
        raise RuntimeError("Local telephony E2E refuses staging/production")
    parsed = make_url(settings.database_url)
    if parsed.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Local telephony E2E requires a localhost PostgreSQL URL")


async def register_fixture(suffix: str) -> tuple[UUID, str]:
    slug = f"telephony-e2e-{suffix}"
    async with httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", timeout=15
    ) as client:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "company_name": f"Telephony E2E {suffix}",
                "company_slug": slug,
                "display_name": "Telephony E2E Owner",
                "email": f"telephony-e2e-{suffix}@example.com",
                "password": f"LocalOnly-1-{secrets.token_urlsafe(18)}",
            },
        )
    response.raise_for_status()
    return UUID(response.json()["tenant"]["id"]), slug


async def configure_route(tenant_id: UUID, did: str) -> UUID:
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        project = await session.scalar(
            select(Project)
            .where(Project.tenant_id == tenant_id, Project.is_default.is_(True))
            .with_for_update()
        )
        if project is None:
            raise RuntimeError("E2E default project was not created")
        trunk = SipTrunk(
            tenant_id=tenant_id,
            name="Local SIP provider emulator",
            provider_host="sip-emulator",
            provider_port=5060,
            transport="udp",
            allowed_ips=["172.16.0.0/12"],
            auth_mode="ip",
            codecs=["ulaw"],
            dtmf_mode="rfc4733",
            max_channels=1,
            channel_pool_mode="shared",
            registration_status="not_configured",
            reachability_status="reachable",
            last_status_at=datetime.now(UTC),
            status=IntegrationStatus.CONFIGURED,
        )
        session.add(trunk)
        await session.flush()
        number = PhoneNumber(
            tenant_id=tenant_id,
            sip_trunk_id=trunk.id,
            e164=did,
            label="Local E2E DID",
            is_active=True,
        )
        session.add(number)
        await session.flush()
        session.add(
            ProjectInboundPhoneNumber(
                tenant_id=tenant_id,
                project_id=project.id,
                phone_number_id=number.id,
            )
        )
        project.outbound_phone_number_id = number.id
        project.recording_enabled = True
        project.recording_disclosure_required = False
        project.max_concurrent_calls = 1
        return project.id


def run_local_call(did: str) -> None:
    environment = {**os.environ, "TELEPHONY_TEST_DID": did}
    command = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "infrastructure/telephony-test/docker-compose.yml",
        "--profile",
        "telephony-test",
        "exec",
        "-T",
        "sip-emulator",
        "python",
        "sip_emulator.py",
        "inbound",
        "--target",
        "asterisk:5060",
        "--did",
        did,
    ]
    completed = subprocess.run(
        command,
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.stdout:
        print(completed.stdout.strip(), flush=True)
    if completed.returncode != 0:
        safe_error = (completed.stderr or "local SIP harness failed").strip()
        raise RuntimeError(safe_error[:800])


def remove_local_recording(recording_id: str) -> None:
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "asterisk",
            "rm",
            "-f",
            "--",
            f"/var/spool/asterisk/monitor/{recording_id}.wav",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        timeout=15,
    )


async def verify_result(tenant_id: UUID, project_id: UUID, did: str) -> None:
    deadline = time.monotonic() + 30
    last_status = "missing"
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
                last_status = call.status.value
                if call.status in TERMINAL:
                    dtmf_count = int(
                        await session.scalar(
                            select(func.count())
                            .select_from(CallEvent)
                            .where(
                                CallEvent.tenant_id == tenant_id,
                                CallEvent.call_id == call.id,
                                CallEvent.event_type == "call.dtmf",
                            )
                        )
                        or 0
                    )
                    reservation = await session.scalar(
                        select(TelephonyChannelReservation).where(
                            TelephonyChannelReservation.tenant_id == tenant_id,
                            TelephonyChannelReservation.call_id == call.id,
                        )
                    )
                    cdr = await session.scalar(
                        select(CallDetailRecord).where(
                            CallDetailRecord.tenant_id == tenant_id,
                            CallDetailRecord.call_id == call.id,
                        )
                    )
                    active_resources = int(
                        await session.scalar(
                            select(func.count())
                            .select_from(TelephonyResource)
                            .where(
                                TelephonyResource.tenant_id == tenant_id,
                                TelephonyResource.call_id == call.id,
                                TelephonyResource.status.in_(
                                    {"creating", "active", "stopping"}
                                ),
                            )
                        )
                        or 0
                    )
                    recording = await session.scalar(
                        select(CallRecording).where(
                            CallRecording.tenant_id == tenant_id,
                            CallRecording.call_id == call.id,
                        )
                    )
                    pending: list[str] = []
                    if dtmf_count < 1:
                        pending.append("DTMF event")
                    if reservation is None or reservation.status != "released":
                        pending.append("released channel reservation")
                    if cdr is None or cdr.destination_masked == call.from_number:
                        pending.append("masked canonical CDR")
                    if active_resources:
                        pending.append("telephony resource cleanup")
                    if recording is None:
                        pending.append("recording lifecycle event")
                    if not pending:
                        print(
                            "backend verified: terminal call, DTMF, masked CDR, "
                            "recording, released channel/resources",
                            flush=True,
                        )
                        return
                    last_status = f"{call.status.value}; waiting for " + ", ".join(
                        pending
                    )
        await asyncio.sleep(0.5)
    raise RuntimeError(f"Backend telephony verification timed out (last={last_status})")


async def cleanup_fixture(tenant_id: UUID, slug: str) -> None:
    if not slug.startswith("telephony-e2e-"):
        raise RuntimeError("Refusing to clean up a non-E2E tenant")
    settings = get_settings()
    engine = create_async_engine(
        settings.migration_database_url or settings.database_url
    )
    recording_ids: list[str] = []
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            recording_ids = [
                str(value)
                for value in (
                    await connection.execute(
                        text(
                            "SELECT provider_recording_id FROM call_recordings "
                            "WHERE tenant_id=:tenant_id"
                        ),
                        {"tenant_id": tenant_id},
                    )
                ).scalars()
                if value
            ]
            await connection.execute(
                text("DELETE FROM tenants WHERE id=:tenant_id AND slug=:slug"),
                {"tenant_id": tenant_id, "slug": slug},
            )
    finally:
        await engine.dispose()
    for recording_id in recording_ids:
        prefix = "teamora-"
        if not recording_id.startswith(prefix):
            continue
        try:
            canonical = f"{prefix}{UUID(recording_id.removeprefix(prefix))}"
        except ValueError:
            continue
        await asyncio.to_thread(remove_local_recording, canonical)


async def run() -> None:
    assert_local_environment()
    suffix = secrets.token_hex(5)
    did = f"+99871{int(suffix[:8], 16) % 10_000_000:07d}"
    tenant_id: UUID | None = None
    slug = ""
    try:
        tenant_id, slug = await register_fixture(suffix)
        project_id = await configure_route(tenant_id, did)
        run_local_call(did)
        await verify_result(tenant_id, project_id, did)
    finally:
        if tenant_id is not None:
            await cleanup_fixture(tenant_id, slug)
            print("removed exact local telephony E2E tenant fixture", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
