import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WebSocketServer } from "ws";
import type { RealtimeSessionConfig } from "@teamora/contracts";
import {
  buildPcmuRtpPacket,
  pcm16ToBytes,
  pcm24kToPcmuPayload,
  pcmuRtpToPcm24k,
} from "../src/audio-codec.js";
import { MockRealtimeProvider } from "../src/providers/mock-realtime.js";
import { OpenAiRealtimeProvider } from "../src/providers/openai-realtime.js";

const config: RealtimeSessionConfig = {
  callId: "call-1",
  tenantId: "tenant-1",
  projectId: "project-1",
  correlationId: "correlation-1",
  model: "gpt-realtime-2.1",
  voice: "marin",
  instructions: "safe test",
  language: "kaa-latn",
  safetyIdentifier: "cc_test_safety_identifier",
  toolDefinitions: [],
  vad: {
    type: "server_vad",
    threshold: 0.5,
    prefixPaddingMs: 300,
    silenceDurationMs: 700,
    idleTimeoutMs: 30_000,
  },
  inputAudioFormat: { type: "audio/pcm", rate: 24_000, channels: 1 },
  outputAudioFormat: { type: "audio/pcm", rate: 24_000, channels: 1 },
};

const cleanup: Array<() => Promise<void>> = [];
afterEach(async () => {
  await Promise.all(cleanup.splice(0).map((item) => item()));
});

describe("Realtime providers", () => {
  it("implements deterministic mock audio, transcripts, usage and interruption", async () => {
    const provider = new MockRealtimeProvider();
    const events: string[] = [];
    const session = await provider.createSession(config, async (event) => {
      events.push(event.type);
    });
    await session.appendAudio(Buffer.alloc(4_800));
    await session.interrupt("item-test", 50);
    expect(events).toContain("audio.output");
    expect(events).toContain("transcript.segment");
    expect(events).toContain("response.completed");
    expect(events).toContain("audio.interrupted");
    await provider.shutdown();
  });

  it("uses the current server WebSocket contract and sends cancel plus truncate", async () => {
    const http = createServer();
    const websocket = new WebSocketServer({ server: http });
    const received: Array<Record<string, unknown>> = [];
    websocket.on("connection", (socket, request) => {
      expect(request.headers.authorization).toBe(
        "Bearer test-key-not-secret-123456",
      );
      expect(request.headers["openai-safety-identifier"]).toBe(
        "cc_test_safety_identifier",
      );
      socket.on("message", (raw) => {
        const event = JSON.parse(raw.toString()) as Record<string, unknown>;
        received.push(event);
        if (event.type === "session.update") {
          socket.send(
            JSON.stringify({
              type: "session.updated",
              event_id: "provider-session-updated",
            }),
          );
        }
      });
    });
    await new Promise<void>((resolve) => http.listen(0, "127.0.0.1", resolve));
    cleanup.push(async () => {
      websocket.clients.forEach((socket) => socket.terminate());
      await new Promise<void>((resolve) => websocket.close(() => resolve()));
      await new Promise<void>((resolve) => http.close(() => resolve()));
    });
    const port = (http.address() as AddressInfo).port;
    const provider = new OpenAiRealtimeProvider({
      apiKey: "test-key-not-secret-123456",
      url: `ws://127.0.0.1:${port}/v1/realtime`,
    });
    const session = await provider.createSession(
      config,
      vi.fn(async () => undefined),
    );
    await session.appendAudio(Buffer.alloc(480));
    await session.interrupt("item-1", 42.9);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(received[0]).toMatchObject({ type: "session.update" });
    expect(received[0]).toMatchObject({
      session: { output_modalities: ["audio"] },
    });
    expect(
      received.some((event) => event.type === "input_audio_buffer.append"),
    ).toBe(true);
    expect(received.some((event) => event.type === "response.cancel")).toBe(
      true,
    );
    expect(received).toContainEqual(
      expect.objectContaining({
        type: "conversation.item.truncate",
        item_id: "item-1",
        audio_end_ms: 42,
      }),
    );
    await provider.shutdown();
  });

  it("cancels once and truncates every queued playback item", async () => {
    const http = createServer();
    const websocket = new WebSocketServer({ server: http });
    const received: Array<Record<string, unknown>> = [];
    websocket.on("connection", (socket) => {
      socket.on("message", (raw) => {
        const event = JSON.parse(raw.toString()) as Record<string, unknown>;
        received.push(event);
        if (event.type === "session.update") {
          socket.send(JSON.stringify({ type: "session.updated" }));
        }
      });
    });
    await new Promise<void>((resolve) => http.listen(0, "127.0.0.1", resolve));
    cleanup.push(async () => {
      websocket.clients.forEach((socket) => socket.terminate());
      await new Promise<void>((resolve) => websocket.close(() => resolve()));
      await new Promise<void>((resolve) => http.close(() => resolve()));
    });
    const port = (http.address() as AddressInfo).port;
    const provider = new OpenAiRealtimeProvider({
      apiKey: "test-key-not-secret-123456",
      url: `ws://127.0.0.1:${port}/v1/realtime`,
    });
    const session = await provider.createSession(
      config,
      vi.fn(async () => undefined),
    );
    await session.interruptPlayback?.([
      { itemId: "item-1", playedAudioMs: 21.9 },
      { itemId: "item-2", playedAudioMs: 84.1 },
      { itemId: "item-1", playedAudioMs: 100 },
    ]);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(
      received.filter((event) => event.type === "response.cancel"),
    ).toHaveLength(1);
    expect(
      received.filter((event) => event.type === "conversation.item.truncate"),
    ).toEqual([
      expect.objectContaining({ item_id: "item-1", audio_end_ms: 21 }),
      expect.objectContaining({ item_id: "item-2", audio_end_ms: 84 }),
    ]);
    await provider.shutdown();
  });
});

describe("controlled PCMU to PCM24 audio boundary", () => {
  it("converts one 20ms frame in each direction without a second transcoding boundary", () => {
    const source = new Uint8Array(160).fill(0xff);
    const rtp = buildPcmuRtpPacket(source, 1, 160, 7);
    const pcm24 = pcmuRtpToPcm24k(rtp);
    expect(pcm24).toHaveLength(480);
    const encoded = pcm24kToPcmuPayload(pcm16ToBytes(pcm24));
    expect(encoded).toHaveLength(160);
    expect([...encoded]).toEqual([...source]);
  });
});

describe("voice evaluation manifest", () => {
  it("keeps required languages, interruption, safety, tool and fallback cases reproducible", () => {
    const manifest = JSON.parse(
      readFileSync(
        new URL("./fixtures/voice-evals.json", import.meta.url),
        "utf8",
      ),
    ) as { cases: Array<{ id: string; language?: string }> };
    const ids = new Set(manifest.cases.map((item) => item.id));
    expect(manifest.cases.map((item) => item.language)).toEqual(
      expect.arrayContaining(["ru", "uz", "en", "kaa-latn", "kaa-cyrl"]),
    );
    for (const id of [
      "barge_in",
      "knowledge_no_match",
      "document_prompt_injection",
      "allowed_tool",
      "forbidden_tool",
      "provider_fallback",
      "human_transfer",
    ]) {
      expect(ids.has(id)).toBe(true);
    }
  });
});
