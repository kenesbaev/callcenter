from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import time
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]

from run_telephony_e2e import assert_local_environment, configure_route
from run_voice_e2e import cleanup_voice_fixture, register_workspace
from teamora_api.db import SessionFactory, set_tenant_context
from teamora_api.enums import CallStatus
from teamora_api.models import (
    AIRealtimeSession,
    Call,
    CallDetailRecord,
    CallEvent,
    CallRecording,
    HumanOperator,
    Membership,
    OperatorTransferEndpoint,
    TelephonyChannelReservation,
    TelephonyResource,
    TransferAttempt,
    TransferRequest,
)

API_URL = "http://127.0.0.1:8000"
OPERATOR_EXTENSION = "17017"
TERMINAL = {
    CallStatus.COMPLETED,
    CallStatus.BUSY,
    CallStatus.NO_ANSWER,
    CallStatus.FAILED,
    CallStatus.CANCELLED,
}


async def cleanup_transfer_fixture(tenant_id: UUID, slug: str) -> None:
    """Retry exact-fixture cleanup while late provider events finish committing."""
    for attempt in range(5):
        try:
            await cleanup_voice_fixture(tenant_id, slug)
            return
        except DBAPIError:
            if attempt == 4:
                raise
            await asyncio.sleep(0.5 * (2**attempt))


def require(response: httpx.Response, expected: int) -> dict[str, object]:
    if response.status_code != expected:
        raise RuntimeError(
            f"Transfer fixture API failed ({response.status_code}): {response.text[:500]}"
        )
    value = response.json()
    if not isinstance(value, dict):
        raise TypeError("Transfer fixture API returned an unexpected response")
    return value


async def create_operator(
    *,
    owner_email: str,
    owner_password: str,
    tenant_slug: str,
    project_id: str,
    suffix: str,
) -> tuple[httpx.AsyncClient, UUID]:
    owner = httpx.AsyncClient(base_url=API_URL, timeout=20)
    operator = httpx.AsyncClient(base_url=API_URL, timeout=20)
    try:
        auth = require(
            await owner.post(
                "/api/v1/auth/login",
                json={
                    "company_slug": tenant_slug,
                    "email": owner_email,
                    "password": owner_password,
                },
            ),
            200,
        )
        owner_csrf = str(auth["csrf_token"])
        require(
            await owner.post(
                "/api/v1/team/presence/heartbeat",
                headers={"X-CSRF-Token": owner_csrf},
                json={"session_key": f"transfer-e2e-owner-{suffix}"},
            ),
            200,
        )
        require(
            await owner.put(
                "/api/v1/team/presence/status",
                headers={"X-CSRF-Token": owner_csrf},
                json={
                    "session_key": f"transfer-e2e-owner-{suffix}",
                    "status": "away",
                },
            ),
            200,
        )
        operator_email = f"transfer-e2e-{suffix}@example.com"
        operator_password = f"LocalOnly-1-{secrets.token_urlsafe(18)}"
        invitation = require(
            await owner.post(
                "/api/v1/team/invitations",
                headers={
                    "X-CSRF-Token": owner_csrf,
                    "Idempotency-Key": f"transfer-e2e-invite-{suffix}",
                },
                json={
                    "email": operator_email,
                    "role": "human_operator",
                    "project_ids": [project_id],
                },
            ),
            201,
        )
        accepted = require(
            await operator.post(
                "/api/v1/team/invitations/accept",
                json={
                    "tenant_slug": tenant_slug,
                    "token": invitation["acceptance_token"],
                    "display_name": "Local Transfer Operator",
                    "password": operator_password,
                },
            ),
            200,
        )
        membership_id = UUID(str(accepted["membership_id"]))
        login = require(
            await operator.post(
                "/api/v1/auth/login",
                json={
                    "company_slug": tenant_slug,
                    "email": operator_email,
                    "password": operator_password,
                },
            ),
            200,
        )
        csrf = str(login["csrf_token"])
        require(
            await operator.post(
                "/api/v1/team/presence/heartbeat",
                headers={"X-CSRF-Token": csrf},
                json={"session_key": f"transfer-e2e-{suffix}"},
            ),
            200,
        )
        require(
            await operator.put(
                "/api/v1/team/presence/status",
                headers={"X-CSRF-Token": csrf},
                json={
                    "session_key": f"transfer-e2e-{suffix}",
                    "status": "available",
                },
            ),
            200,
        )
        operator.headers.update({"X-CSRF-Token": csrf})
        return operator, membership_id
    finally:
        await owner.aclose()


async def configure_operator(
    tenant_id: UUID, membership_id: UUID, extension: str
) -> None:
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, tenant_id)
        membership = await session.scalar(
            select(Membership).where(
                Membership.tenant_id == tenant_id,
                Membership.id == membership_id,
            )
        )
        operator = await session.scalar(
            select(HumanOperator).where(
                HumanOperator.tenant_id == tenant_id,
                HumanOperator.membership_id == membership_id,
            )
        )
        if membership is None or operator is None:
            raise RuntimeError("Transfer operator profile was not created")
        operator.extension = extension
        operator.is_transfer_available = True
        session.add(
            OperatorTransferEndpoint(
                tenant_id=tenant_id,
                membership_id=membership_id,
                endpoint_type="sip",
                destination=f"sip:{extension}",
                display_hint=f"Local SIP {extension}",
                is_verified=True,
                is_enabled=True,
                lock_version=1,
            )
        )


def ari_operator_endpoint(action: str, extension: str) -> None:
    if action not in {"provision", "revoke"} or not extension.isdigit():
        raise RuntimeError("Invalid local operator endpoint command")
    javascript = r"""
const action=process.argv[1], id=process.argv[2];
if(!['provision','revoke'].includes(action)||!/^[0-9]{2,12}$/.test(id)) process.exit(2);
const base=process.env.ASTERISK_ARI_URL+'/ari/asterisk/config/dynamic/res_pjsip';
const authorization='Basic '+Buffer.from(process.env.ASTERISK_ARI_USERNAME+':'+process.env.ASTERISK_ARI_PASSWORD).toString('base64');
const request=async(type,method,fields=[])=>{
  const response=await fetch(base+'/'+type+'/'+id,{method,headers:{authorization,'content-type':'application/json'},...(method==='PUT'?{body:JSON.stringify({fields:fields.map(([attribute,value])=>({attribute,value}))})}:{})});
  if(!response.ok && !(method==='DELETE' && response.status===404)){console.error(type+' '+response.status);process.exit(3);}
};
if(action==='provision'){
  await request('aor','PUT',[['contact','sip:'+id+'@sip-emulator:5060'],['max_contacts','1']]);
  await request('endpoint','PUT',[['transport','transport-provider'],['context','teamora-operator'],['disallow','all'],['allow','ulaw'],['aors',id],['direct_media','no'],['force_rport','yes'],['rewrite_contact','yes'],['rtp_symmetric','yes']]);
} else {
  await request('endpoint','DELETE'); await request('aor','DELETE');
}
console.log('local operator endpoint '+(action==='revoke'?'revoked':'provisioned'));
"""
    environment = {
        **os.environ,
        "TELEPHONY_TEST_DID": os.environ.get("TELEPHONY_TEST_DID", "+998711234567"),
        "MOCK_OPENAI_SCENARIO": "transfer_e2e",
    }
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "infrastructure/telephony-test/docker-compose.yml",
            "-f",
            "infrastructure/ai-test/docker-compose.yml",
            "--profile",
            "telephony-test",
            "--profile",
            "ai-test",
            "exec",
            "-T",
            "voice-gateway",
            "node",
            "-e",
            javascript,
            action,
            extension,
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "ARI endpoint command failed")[:500])
    print(completed.stdout.strip(), flush=True)


def start_customer_call(did: str) -> subprocess.Popen[str]:
    environment = {
        **os.environ,
        "TELEPHONY_TEST_DID": did,
        "MOCK_OPENAI_SCENARIO": "transfer_e2e",
    }
    return subprocess.Popen(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "infrastructure/telephony-test/docker-compose.yml",
            "-f",
            "infrastructure/ai-test/docker-compose.yml",
            "--profile",
            "telephony-test",
            "--profile",
            "ai-test",
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
            "--hold-seconds",
            "12",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def set_mock_scenario(scenario: str) -> None:
    if scenario not in {"voice_e2e", "transfer_e2e"}:
        raise RuntimeError("Invalid local Mock OpenAI scenario")
    environment = {
        **os.environ,
        "TELEPHONY_TEST_DID": os.environ.get("TELEPHONY_TEST_DID", "+998711234567"),
        "MOCK_OPENAI_SCENARIO": scenario,
    }
    completed = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "infrastructure/telephony-test/docker-compose.yml",
            "-f",
            "infrastructure/ai-test/docker-compose.yml",
            "--profile",
            "telephony-test",
            "--profile",
            "ai-test",
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "mock-openai-realtime",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            (completed.stderr or "Mock OpenAI scenario setup failed")[:800]
        )
    print(f"Mock OpenAI scenario ready: {scenario}", flush=True)


def cleanup_exact_test_call(did: str) -> None:
    """Hang up only customer channels whose dialplan extension is this test DID."""
    environment = {
        **os.environ,
        "TELEPHONY_TEST_DID": did,
        "MOCK_OPENAI_SCENARIO": "transfer_e2e",
    }
    base = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "infrastructure/telephony-test/docker-compose.yml",
        "-f",
        "infrastructure/ai-test/docker-compose.yml",
        "--profile",
        "telephony-test",
        "--profile",
        "ai-test",
        "exec",
        "-T",
        "asterisk",
        "asterisk",
        "-rx",
    ]
    listed = subprocess.run(
        [*base, "core show channels concise"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if listed.returncode != 0:
        return
    marker = f"!teamora-inbound!{did}!"
    for line in listed.stdout.splitlines():
        if marker not in line:
            continue
        channel = line.split("!", 1)[0]
        if not channel.startswith("PJSIP/provider-endpoint-"):
            continue
        subprocess.run(
            [*base, f"channel request hangup {channel}"],
            cwd=REPOSITORY_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )


def assert_asterisk_cleanup() -> None:
    environment = {
        **os.environ,
        "TELEPHONY_TEST_DID": os.environ.get("TELEPHONY_TEST_DID", "+998711234567"),
        "MOCK_OPENAI_SCENARIO": "transfer_e2e",
    }
    base = [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "-f",
        "infrastructure/telephony-test/docker-compose.yml",
        "-f",
        "infrastructure/ai-test/docker-compose.yml",
        "--profile",
        "telephony-test",
        "--profile",
        "ai-test",
        "exec",
        "-T",
        "asterisk",
        "asterisk",
        "-rx",
    ]
    channels = subprocess.run(
        [*base, "core show channels concise"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()
    bridges = subprocess.run(
        [*base, "bridge show all"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout
    active_bridges = [line for line in bridges.splitlines()[1:] if line.strip()]
    if channels or active_bridges:
        raise RuntimeError("Asterisk retained transfer channels or bridges")
    print("Asterisk cleanup verified: 0 channels, 0 bridges", flush=True)


async def accept_offer(operator: httpx.AsyncClient) -> UUID:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = await operator.get("/api/v1/transfers")
        if response.status_code == 200:
            offers = [item for item in response.json() if item["status"] == "offered"]
            if offers:
                offer = offers[0]
                claimed = require(
                    await operator.post(
                        f"/api/v1/transfers/requests/{offer['id']}/claim",
                        json={"expected_version": offer["lock_version"]},
                    ),
                    200,
                )
                answered = require(
                    await operator.post(
                        f"/api/v1/transfers/requests/{offer['id']}/answer",
                        json={
                            "expected_version": claimed["lock_version"],
                            "endpoint_type": "sip",
                        },
                    ),
                    200,
                )
                if answered["status"] != "connecting":
                    raise RuntimeError("Operator leg did not enter connecting state")
                return UUID(str(offer["id"]))
        await asyncio.sleep(0.25)
    raise RuntimeError("AI transfer offer was not delivered to the operator")


async def verify_transfer(
    tenant_id: UUID, project_id: UUID, transfer_id: UUID, did: str
) -> UUID:
    deadline = time.monotonic() + 30
    last = "call missing"
    while time.monotonic() < deadline:
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, tenant_id)
            transfer = await session.scalar(
                select(TransferRequest).where(
                    TransferRequest.tenant_id == tenant_id,
                    TransferRequest.id == transfer_id,
                )
            )
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
            if transfer is not None and call is not None:
                attempt = await session.scalar(
                    select(TransferAttempt).where(
                        TransferAttempt.tenant_id == tenant_id,
                        TransferAttempt.transfer_request_id == transfer.id,
                        TransferAttempt.status == "connected",
                    )
                )
                if transfer.status.value == "connected" and attempt is not None:
                    print(
                        "operator claim and confirmed bridge handoff verified",
                        flush=True,
                    )
                    return call.id
                last = f"call={call.status.value}, transfer={transfer.status.value}"
        await asyncio.sleep(0.25)
    raise RuntimeError(f"Transfer did not connect ({last})")


async def verify_cleanup(tenant_id: UUID, call_id: UUID) -> None:
    deadline = time.monotonic() + 30
    last = "not terminal"
    while time.monotonic() < deadline:
        async with SessionFactory.begin() as session:
            await set_tenant_context(session, tenant_id)
            call = await session.scalar(
                select(Call).where(Call.tenant_id == tenant_id, Call.id == call_id)
            )
            if call is None:
                raise RuntimeError("Transferred Call disappeared")
            active_sessions = int(
                await session.scalar(
                    select(func.count())
                    .select_from(AIRealtimeSession)
                    .where(
                        AIRealtimeSession.tenant_id == tenant_id,
                        AIRealtimeSession.call_id == call_id,
                        AIRealtimeSession.state.not_in({"closed", "failed"}),
                    )
                )
                or 0
            )
            active_resources = int(
                await session.scalar(
                    select(func.count())
                    .select_from(TelephonyResource)
                    .where(
                        TelephonyResource.tenant_id == tenant_id,
                        TelephonyResource.call_id == call_id,
                        TelephonyResource.status.in_(
                            {"creating", "active", "stopping"}
                        ),
                    )
                )
                or 0
            )
            active_reservations = int(
                await session.scalar(
                    select(func.count())
                    .select_from(TelephonyChannelReservation)
                    .where(
                        TelephonyChannelReservation.tenant_id == tenant_id,
                        TelephonyChannelReservation.call_id == call_id,
                        TelephonyChannelReservation.status == "reserved",
                    )
                )
                or 0
            )
            recordings = int(
                await session.scalar(
                    select(func.count())
                    .select_from(CallRecording)
                    .where(
                        CallRecording.tenant_id == tenant_id,
                        CallRecording.call_id == call_id,
                    )
                )
                or 0
            )
            cdrs = int(
                await session.scalar(
                    select(func.count())
                    .select_from(CallDetailRecord)
                    .where(
                        CallDetailRecord.tenant_id == tenant_id,
                        CallDetailRecord.call_id == call_id,
                    )
                )
                or 0
            )
            completed_events = int(
                await session.scalar(
                    select(func.count())
                    .select_from(CallEvent)
                    .where(
                        CallEvent.tenant_id == tenant_id,
                        CallEvent.call_id == call_id,
                        CallEvent.event_type == "transfer.completed",
                    )
                )
                or 0
            )
            checks = {
                "terminal": call.status in TERMINAL,
                "AI sessions": active_sessions == 0,
                "resources": active_resources == 0,
                "reservations": active_reservations == 0,
                "recording": recordings == 1,
                "CDR": cdrs == 1,
                "transfer event": completed_events == 1,
            }
            if all(checks.values()):
                print(
                    "cleanup verified: 0 active AI sessions/resources/reservations; "
                    "continuous recording and CDR retained",
                    flush=True,
                )
                return
            last = ", ".join(key for key, value in checks.items() if not value)
        await asyncio.sleep(0.5)
    raise RuntimeError(f"Transfer cleanup timed out ({last})")


async def verify_cross_tenant(decoy_tenant: UUID, transfer_id: UUID) -> None:
    async with SessionFactory.begin() as session:
        await set_tenant_context(session, decoy_tenant)
        leaked = int(
            await session.scalar(
                select(func.count())
                .select_from(TransferRequest)
                .where(TransferRequest.id == transfer_id)
            )
            or 0
        )
        if leaked:
            raise RuntimeError("Cross-tenant transfer visibility detected")


async def run() -> None:
    assert_local_environment()
    set_mock_scenario("transfer_e2e")
    suffix = secrets.token_hex(5)
    did = f"+99871{int(suffix[:8], 16) % 10_000_000:07d}"
    primary_tenant: UUID | None = None
    decoy_tenant: UUID | None = None
    primary_slug = ""
    decoy_slug = ""
    operator: httpx.AsyncClient | None = None
    customer: subprocess.Popen[str] | None = None
    endpoint_ready = False
    try:
        (
            primary_tenant,
            primary_slug,
            project_text,
            _customer_id,
            owner_email,
            owner_password,
        ) = await register_workspace(suffix)
        project_id = await configure_route(primary_tenant, did)
        if str(project_id) != project_text:
            raise RuntimeError("Transfer fixture project changed")
        operator, membership_id = await create_operator(
            owner_email=owner_email,
            owner_password=owner_password,
            tenant_slug=primary_slug,
            project_id=project_text,
            suffix=suffix,
        )
        await configure_operator(primary_tenant, membership_id, OPERATOR_EXTENSION)
        (
            decoy_tenant,
            decoy_slug,
            _decoy_project,
            _decoy_customer,
            _decoy_email,
            _decoy_password,
        ) = await register_workspace(f"{suffix}-decoy")
        ari_operator_endpoint("provision", OPERATOR_EXTENSION)
        endpoint_ready = True
        customer = start_customer_call(did)
        transfer_id = await accept_offer(operator)
        call_id = await verify_transfer(primary_tenant, project_id, transfer_id, did)
        stdout, stderr = await asyncio.to_thread(customer.communicate, timeout=30)
        if stdout:
            print(stdout.strip(), flush=True)
        if customer.returncode != 0:
            raise RuntimeError((stderr or "customer SIP emulator failed")[:800])
        await verify_cleanup(primary_tenant, call_id)
        assert_asterisk_cleanup()
        await verify_cross_tenant(decoy_tenant, transfer_id)
        print("cross-tenant transfer visibility denied", flush=True)
    finally:
        if customer is not None and customer.poll() is None:
            customer.terminate()
            try:
                await asyncio.to_thread(customer.wait, 5)
            except subprocess.TimeoutExpired:
                customer.kill()
        cleanup_exact_test_call(did)
        if operator is not None:
            await operator.aclose()
        if endpoint_ready:
            ari_operator_endpoint("revoke", OPERATOR_EXTENSION)
        # Provider callbacks can still be committing after the SIP process exits.
        await asyncio.sleep(1)
        if primary_tenant is not None:
            await cleanup_transfer_fixture(primary_tenant, primary_slug)
        if decoy_tenant is not None:
            await cleanup_transfer_fixture(decoy_tenant, decoy_slug)
        set_mock_scenario("voice_e2e")
        await asyncio.sleep(1)
        assert_asterisk_cleanup()
        print("removed exact local transfer E2E tenant fixtures", flush=True)


if __name__ == "__main__":
    asyncio.run(run())
