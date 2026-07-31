import { createServer } from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  TelephonyCommandContext,
  TelephonyCommandResult,
  TelephonyProvider,
} from "@teamora/contracts";
import { createGatewayRequestHandler } from "../src/internal-api.js";

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

async function start(provider?: TelephonyProvider) {
  const handler = createGatewayRequestHandler({
    serviceToken,
    trustedHosts: ["127.0.0.1"],
    maxBodyBytes: 65_536,
    ...(provider ? { provider } : {}),
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
});
