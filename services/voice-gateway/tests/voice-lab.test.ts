import { createHmac, randomUUID } from "node:crypto";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, describe, expect, it } from "vitest";
import WebSocket from "ws";
import { MockRealtimeProvider } from "../src/providers/mock-realtime.js";
import { attachVoiceLab, verifyVoiceLabTicket } from "../src/voice-lab.js";

const secret = "voice-lab-test-secret-at-least-32-characters";
const cleanup: Array<() => Promise<void>> = [];

afterEach(async () => {
  await Promise.all(cleanup.splice(0).map((item) => item()));
});

function ticket(overrides: Record<string, unknown> = {}): string {
  const payload = {
    aud: "teamora-voice-lab",
    exp: Math.floor(Date.now() / 1_000) + 60,
    jti: randomUUID(),
    language: "ru",
    sub: randomUUID(),
    tenant_id: randomUUID(),
    v: 1,
    ...overrides,
  };
  const encoded = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const signature = createHmac("sha256", secret)
    .update(`teamora-voice-lab:v1.${encoded}`)
    .digest("base64url");
  return `${encoded}.${signature}`;
}

function messageInbox(socket: WebSocket) {
  const queued: Record<string, unknown>[] = [];
  const waiters: Array<(value: Record<string, unknown>) => void> = [];
  socket.on("message", (raw) => {
    const value = JSON.parse(raw.toString()) as Record<string, unknown>;
    const waiter = waiters.shift();
    if (waiter) waiter(value);
    else queued.push(value);
  });
  return {
    next: () =>
      queued.length
        ? Promise.resolve(queued.shift()!)
        : new Promise<Record<string, unknown>>((resolve) =>
            waiters.push(resolve),
          ),
  };
}

describe("Voice Lab ticket", () => {
  it("accepts only signed, current and correctly scoped tickets", () => {
    expect(verifyVoiceLabTicket(ticket(), secret)).toMatchObject({
      aud: "teamora-voice-lab",
      language: "ru",
      v: 1,
    });
    expect(verifyVoiceLabTicket(`${ticket()}x`, secret)).toBeUndefined();
    expect(
      verifyVoiceLabTicket(
        ticket({ exp: Math.floor(Date.now() / 1_000) - 1 }),
        secret,
      ),
    ).toBeUndefined();
  });
});

describe("Voice Lab WebSocket", () => {
  it("keeps the API key server-side, relays mock audio and rejects ticket replay", async () => {
    const server = createServer();
    const provider = new MockRealtimeProvider();
    const runtime = attachVoiceLab(server, {
      enabled: true,
      provider,
      providerName: "mock",
      model: "unused-live-model",
      voice: "unused-live-voice",
      tokenSecret: secret,
      maxDurationSeconds: 30,
      maxSessions: 1,
      maxSocketMessageBytes: 32_768,
      maxSocketQueueBytes: 262_144,
    });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    cleanup.push(async () => {
      await runtime.close();
      await provider.shutdown();
      await new Promise<void>((resolve) => server.close(() => resolve()));
    });
    const port = (server.address() as AddressInfo).port;
    const signedTicket = ticket();
    const client = new WebSocket(
      `ws://127.0.0.1:${port}/voice-lab/ws`,
      ["teamora-voice-lab", `teamora-ticket.${signedTicket}`],
      { origin: `http://127.0.0.1:${port}` },
    );
    const inbox = messageInbox(client);
    await new Promise<void>((resolve, reject) => {
      client.once("open", resolve);
      client.once("error", reject);
    });
    expect(client.protocol).toBe("teamora-voice-lab");
    expect(await inbox.next()).toMatchObject({
      type: "ready",
      provider: "mock",
      sample_rate: 24_000,
    });
    client.send(
      JSON.stringify({
        type: "audio",
        data: Buffer.alloc(4_800).toString("base64"),
      }),
    );
    const types = new Set<string>();
    while (!types.has("usage")) {
      const event = await inbox.next();
      types.add(String(event.type));
    }
    for (const type of ["user_speaking", "audio", "transcript", "usage"])
      expect(types.has(type)).toBe(true);
    client.send(JSON.stringify({ type: "stop" }));
    await new Promise<void>((resolve) => client.once("close", () => resolve()));

    const replay = new WebSocket(
      `ws://127.0.0.1:${port}/voice-lab/ws`,
      ["teamora-voice-lab", `teamora-ticket.${signedTicket}`],
      { origin: `http://127.0.0.1:${port}` },
    );
    const replayStatus = await new Promise<number>((resolve) => {
      replay.once("unexpected-response", (_request, response) =>
        resolve(response.statusCode ?? 0),
      );
      replay.once("error", () => resolve(0));
    });
    expect(replayStatus).toBe(401);
  });
});
