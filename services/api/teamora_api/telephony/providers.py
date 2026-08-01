from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

import httpx

from teamora_api.config import Settings
from teamora_api.enums import TelephonyCallState, TelephonyCommandName
from teamora_api.schemas.telephony import TelephonyCommand, TelephonyCommandResult


class ProviderCommandError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class TelephonyProvider(Protocol):
    name: str

    async def execute(self, command: TelephonyCommand) -> TelephonyCommandResult: ...


class MockTelephonyProvider:
    name = "mock"

    def __init__(self, settings: Settings) -> None:
        if not settings.mock_telephony_available:
            raise ProviderCommandError(
                "mock_telephony_forbidden",
                "Mock telephony is disabled in this environment",
                status_code=503,
            )

    async def execute(self, command: TelephonyCommand) -> TelephonyCommandResult:
        state = self._state(command)
        provider_call_id = command.provider_call_id
        if command.command == TelephonyCommandName.ORIGINATE:
            provider_call_id = f"mock:{command.call_id}"
        metadata: dict[str, str | int | float | bool] = {}
        if command.command == TelephonyCommandName.TRANSFER:
            metadata["intermediateState"] = TelephonyCallState.TRANSFER_REQUESTED.value
        if command.command == TelephonyCommandName.START_RECORDING:
            metadata["recordingState"] = "recording"
            metadata["recordingId"] = f"mock-recording:{command.call_id}"
        elif command.command == TelephonyCommandName.PAUSE_RECORDING:
            metadata["recordingState"] = "paused"
        elif command.command == TelephonyCommandName.RESUME_RECORDING:
            metadata["recordingState"] = "recording"
        elif command.command == TelephonyCommandName.STOP_RECORDING:
            metadata["recordingState"] = "stopped"
        elif command.command == TelephonyCommandName.CREATE_EXTERNAL_MEDIA:
            metadata["mediaId"] = f"mock-media:{command.call_id}"
        return TelephonyCommandResult(
            commandId=command.command_id,
            provider=self.name,
            accepted=True,
            state=state,
            providerCallId=provider_call_id,
            occurredAt=datetime.now(UTC),
            safeMetadata=metadata,
        )

    @staticmethod
    def _state(command: TelephonyCommand) -> TelephonyCallState:
        if command.command == TelephonyCommandName.ORIGINATE:
            simulation = str(command.parameters.get("simulate", "ringing"))
            allowed = {
                state.value: state
                for state in (
                    TelephonyCallState.RINGING,
                    TelephonyCallState.BUSY,
                    TelephonyCallState.NO_ANSWER,
                    TelephonyCallState.FAILED,
                )
            }
            return allowed.get(simulation, TelephonyCallState.RINGING)
        states = {
            TelephonyCommandName.ANSWER: TelephonyCallState.ACTIVE,
            TelephonyCommandName.HOLD: TelephonyCallState.ON_HOLD,
            TelephonyCommandName.RESUME: TelephonyCallState.ACTIVE,
            TelephonyCommandName.TRANSFER: TelephonyCallState.TRANSFERRED,
        }
        if command.command == TelephonyCommandName.HANGUP:
            if command.parameters.get("reason") == "cancelled":
                return TelephonyCallState.CANCELLED
            return TelephonyCallState.COMPLETED
        if command.command in states:
            return states[command.command]
        current = command.parameters.get("currentState", TelephonyCallState.UNKNOWN.value)
        try:
            return TelephonyCallState(str(current))
        except ValueError:
            return TelephonyCallState.UNKNOWN


class GatewayTelephonyProvider:
    name = "asterisk-ari"

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport

    async def execute(self, command: TelephonyCommand) -> TelephonyCommandResult:
        if self.settings.gateway_service_token is None:
            raise ProviderCommandError(
                "gateway_not_configured",
                "Voice Gateway service authentication is not configured",
                status_code=503,
            )
        timeout = httpx.Timeout(self.settings.gateway_command_timeout_seconds)
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.gateway_internal_url,
                timeout=timeout,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    "/internal/v1/telephony/commands",
                    headers={
                        "Authorization": ("Bearer " + self.settings.gateway_service_token.get_secret_value()),
                        "Content-Type": "application/json",
                        "X-Correlation-ID": command.correlation_id,
                    },
                    content=command.model_dump_json(by_alias=True),
                )
        except httpx.TimeoutException as exc:
            raise ProviderCommandError(
                "gateway_timeout",
                "Voice Gateway did not respond before the timeout",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderCommandError(
                "gateway_unavailable",
                "Voice Gateway is unavailable",
                status_code=503,
            ) from exc
        if response.status_code >= 400:
            code = "gateway_command_failed"
            try:
                body = response.json()
                candidate = body.get("error", {}).get("code")
                if isinstance(candidate, str) and candidate:
                    code = candidate
            except ValueError:
                pass
            raise ProviderCommandError(
                code,
                "Voice Gateway rejected the telephony command",
                status_code=503 if response.status_code >= 500 else 409,
            )
        try:
            return TelephonyCommandResult.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise ProviderCommandError(
                "gateway_response_invalid",
                "Voice Gateway returned an invalid response",
            ) from exc
