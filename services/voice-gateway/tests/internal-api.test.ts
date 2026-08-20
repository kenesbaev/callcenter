import { createServer } from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  TelephonyCommandContext,
  TelephonyCommandResult,
  TelephonyProvider,
} from "@teamora/contracts";
import { createGatewayRequestHandler } from "../src/internal-api.js";
import type {
  OperatorHandoffController,
  WebRtcProvisioningController,
} from "../src/internal-api.js";

const serviceToken = "gateway-service-token-for-tests";
const body = {
  version: "1",
  command: "answer",
  tenantId: "10000000-0000-4000-8000-000000000001",
  projectId: "10000000-0000-4000-8000-000000000002",
  callId: "10000000-0000-4000-8000-000000000003",
  providerCallId: "channel-1",
  commandId: "10000000-0000-4000-8000-000000000004",
  idempotencyKey: "internal-command-key",
  timestamp: "2026-07-30T10:00:00.000Z",
  correlationId: "correlation-1",
  parameters: {},
};

const servers: Array<ReturnType<typeof createServer>> = [];
afterEach(async () => {
  await Promise.all(
    servers
      .splice(0)
      .map(
        (server) =>
          new Promise<void>((resolve) => server.close(() => resolve())),
      ),
  );
});

function mockProvider() {
  const response = (context: TelephonyCommandContext, state = "active") =>
    Promise.resolve({
      commandId: context.commandId,
      provider: "asterisk-ari",
      accepted: true,
      state,
      providerCallId: context.providerCallId,
      occurredAt: "2026-07-30T10:00:01.000Z",
      safeMetadata: {},
    } as TelephonyCommandResult);
  return {
    name: "asterisk-ari",
    status: "configured",
    originate: vi.fn((context) => response(context, "ringing")),
    answer: vi.fn((context) => response(context)),
    hangup: vi.fn((context) => response(context, "completed")),
    hold: vi.fn((context) => response(context, "on_hold")),
    resume: vi.fn((context) => response(context)),
    transfer: vi.fn((context) => response(context, "transferred")),
    getCallState: vi.fn((context) => response(context)),
    startRecording: vi.fn((context) => response(context)),
    pauseRecording: vi.fn((context) => response(context)),
    resumeRecording: vi.fn((context) => response(context)),
    stopRecording: vi.fn((context) => response(context)),
    createExternalMedia: vi.fn((context) => response(context)),
  } as TelephonyProvider & { answer: ReturnType<typeof vi.fn> };
}

async function start(
  provider?: TelephonyProvider,
  runRealtimeDiagnostic?: () => Promise<
    Record<string, string | number | boolean>
  >,
  handoffController?: OperatorHandoffController,
  webRtcProvisioner?: WebRtcProvisioningController,
) {
  const handler = createGatewayRequestHandler({
    serviceToken,
    trustedHosts: ["127.0.0.1"],
    maxBodyBytes: 65_536,
    ...(provider ? { provider } : {}),
    ...(runRealtimeDiagnostic ? { runRealtimeDiagnostic } : {}),
    ...(handoffController ? { handoffController } : {}),
    ...(webRtcProvisioner ? { webRtcProvisioner } : {}),
  });
  const server = createServer(async (request, response) => {
    if (!(await handler(request, response))) {
      response.writeHead(404).end();
    }
  });
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string")
    throw new Error("test server did not start");
  return `http://127.0.0.1:${address.port}`;
}

describe("Gateway internal API", () => {
  it("requires service auth and replays an idempotent command once", async () => {
    const provider = mockProvider();
    const baseUrl = await start(provider);
    const unauthorized = await fetch(
      `${baseUrl}/internal/v1/telephony/commands`,
      {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    expect(unauthorized.status).toBe(401);

    const request = () =>
      fetch(`${baseUrl}/internal/v1/telephony/commands`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${serviceToken}`,
          "content-type": "application/json",
          "x-correlation-id": "correlation-1",
        },
        body: JSON.stringify(body),
      });
    expect((await request()).status).toBe(200);
    expect((await request()).status).toBe(200);
    expect(provider.answer).toHaveBeenCalledOnce();

    const conflict = await fetch(`${baseUrl}/internal/v1/telephony/commands`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${serviceToken}`,
        "content-type": "application/json",
      },
      body: JSON.stringify({ ...body, parameters: { changed: true } }),
    });
    expect(conflict.status).toBe(409);
  });

  it("returns a stable error when Asterisk is not configured", async () => {
    const baseUrl = await start();
    const response = await fetch(`${baseUrl}/internal/v1/telephony/commands`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${serviceToken}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(body),
    });
    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error: { code: "asterisk_not_configured" },
    });
  });

  it("protects and returns the deterministic local Realtime diagnostic", async () => {
    const diagnostic = vi.fn(async () => ({
      status: "local_mock_passed",
      liveOpenAiVerified: false,
    }));
    const baseUrl = await start(undefined, diagnostic);
    const response = await fetch(
      `${baseUrl}/internal/v1/ai/diagnostics/local`,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${serviceToken}`,
          "content-type": "application/json",
        },
        body: "{}",
      },
    );
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      status: "local_mock_passed",
      liveOpenAiVerified: false,
    });
    expect(diagnostic).toHaveBeenCalledOnce();
  });

  it("authenticates and validates operator handoff commands", async () => {
    const handoff: OperatorHandoffController = {
      dialOperator: vi.fn(async () => ({
        status: "connecting" as const,
        operatorChannelId: "teamora-operator-call",
      })),
      cancelOperator: vi.fn(async () => undefined),
    };
    const baseUrl = await start(undefined, undefined, handoff);
    const payload = {
      tenantId: body.tenantId,
      projectId: body.projectId,
      callId: body.callId,
      transferRequestId: "10000000-0000-4000-8000-000000000005",
      destinationType: "browser",
      destination: "webrtc:10000000-0000-4000-8000-000000000006",
      idempotencyKey: "transfer-attempt-one",
    };
    const unauthorized = await fetch(`${baseUrl}/internal/v1/transfers/dial`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    expect(unauthorized.status).toBe(401);
    const accepted = await fetch(`${baseUrl}/internal/v1/transfers/dial`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${serviceToken}`,
        "content-type": "application/json",
        "x-correlation-id": "handoff-correlation",
      },
      body: JSON.stringify(payload),
    });
    expect(accepted.status).toBe(202);
    await expect(accepted.json()).resolves.toMatchObject({
      status: "connecting",
      operatorChannelId: "teamora-operator-call",
    });
    expect(handoff.dialOperator).toHaveBeenCalledWith(
      expect.objectContaining({
        tenantId: body.tenantId,
        destinationType: "browser",
        correlationId: "handoff-correlation",
      }),
    );
    const replay = await fetch(`${baseUrl}/internal/v1/transfers/dial`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${serviceToken}`,
        "content-type": "application/json",
      },
      body: JSON.stringify(payload),
    });
    expect(replay.status).toBe(202);
    expect(handoff.dialOperator).toHaveBeenCalledOnce();
  });

  it("provisions an ephemeral WebRTC endpoint without returning its password", async () => {
    const provisioner: WebRtcProvisioningController = {
      provisionWebRtcEndpoint: vi.fn(async () => undefined),
      revokeWebRtcEndpoint: vi.fn(async () => undefined),
    };
    const baseUrl = await start(undefined, undefined, undefined, provisioner);
    const password = "ephemeral-browser-password-with-enough-entropy";
    const response = await fetch(
      `${baseUrl}/internal/v1/transfers/webrtc/provision`,
      {
        method: "POST",
        headers: {
          authorization: `Bearer ${serviceToken}`,
          "content-type": "application/json",
        },
        body: JSON.stringify({
          membershipId: "10000000-0000-4000-8000-000000000006",
          username: "operator-10000000-0000-4000-8000-000000000006",
          password,
          ttlSeconds: 30,
        }),
      },
    );
    expect(response.status).toBe(201);
    const payload = await response.json();
    expect(payload).toEqual({
      status: "provisioned",
      username: "operator-10000000-0000-4000-8000-000000000006",
      expiresInSeconds: 30,
    });
    expect(JSON.stringify(payload)).not.toContain(password);
    expect(provisioner.provisionWebRtcEndpoint).toHaveBeenCalledWith(
      "10000000-0000-4000-8000-000000000006",
      "operator-10000000-0000-4000-8000-000000000006",
      password,
    );
  });
});
