import { describe, expect, it, vi } from "vitest";
import type {
  RealtimeProviderEvent,
  RealtimePlaybackPosition,
  RealtimeSessionConfig,
  RealtimeVoiceProvider,
  RealtimeVoiceSession,
  TelephonyProvider,
} from "@teamora/contracts";
import { VoiceSessionController } from "../src/session-controller.js";
import { ToolExecutor } from "../src/tool-executor.js";

class MockRealtimeSession implements RealtimeVoiceSession {
  readonly providerSessionId = "provider-session";
  appendAudio = vi.fn(async (_audio: Uint8Array) => undefined);
  sendToolResult = vi.fn(
    async (_callId: string, _result: unknown) => undefined,
  );
  interrupt = vi.fn(async () => undefined);
  interruptPlayback?: (items: RealtimePlaybackPosition[]) => Promise<void>;
  close = vi.fn(async (_reason: string) => undefined);
}

class MockProvider implements RealtimeVoiceProvider {
  readonly name = "mock";
  readonly status = "development" as const;
  readonly session = new MockRealtimeSession();
  attempts = 0;
  failAttempts = 0;
  handler?: (event: RealtimeProviderEvent) => Promise<void>;
  async createSession(
    _config: RealtimeSessionConfig,
    handler: (event: RealtimeProviderEvent) => Promise<void>,
  ): Promise<RealtimeVoiceSession> {
    this.attempts += 1;
    if (this.attempts <= this.failAttempts)
      throw new Error("temporary connection error");
    this.handler = handler;
    return this.session;
  }
  async shutdown(): Promise<void> {}
}

function setup(provider = new MockProvider()) {
  const telephony = {
    name: "mock",
    status: "development",
    originate: vi.fn(),
    answer: vi.fn(),
    hold: vi.fn(),
    resume: vi.fn(),
    getCallState: vi.fn(),
    createExternalMedia: vi.fn(),
    transfer: vi.fn(async () => undefined),
    hangup: vi.fn(),
    startRecording: vi.fn(),
    pauseRecording: vi.fn(),
    resumeRecording: vi.fn(),
    stopRecording: vi.fn(),
  } as unknown as TelephonyProvider;
  const emit = vi.fn(async () => undefined);
  const writeAudio = vi.fn(async (audio: Uint8Array) => audio.byteLength / 48);
  const config: RealtimeSessionConfig = {
    callId: "call-1",
    tenantId: "tenant-1",
    model: "mock",
    voice: "mock",
    instructions: "safe",
    language: "ru",
    toolDefinitions: [
      {
        name: "search_knowledge",
        description: "test",
        inputSchema: { type: "object" },
        timeoutMs: 100,
      },
    ],
  };
  const controller = new VoiceSessionController({
    callId: "call-1",
    tenantId: "tenant-1",
    projectId: "project-1",
    channelId: "channel-1",
    config,
    provider,
    telephony,
    tools: new ToolExecutor(async () => ({ ok: true })),
    emit,
    writeAudio,
    maxReconnects: 2,
  });
  return { controller, emit, provider, telephony, writeAudio };
}

describe("VoiceSessionController", () => {
  it("starts and supports barge-in", async () => {
    const { controller, provider, emit } = setup();
    await controller.start();
    expect(controller.state).toBe("active");
    await provider.handler?.({
      type: "audio.output",
      audioBase64: Buffer.alloc(480).toString("base64"),
      itemId: "item-1",
      eventId: "audio-1",
    });
    await provider.handler?.({
      type: "speech.started",
      audioStartMs: 100,
      eventId: "speech-1",
    });
    expect(provider.session.interrupt).toHaveBeenCalledOnce();
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "ai.interrupted", tenantId: "tenant-1" }),
    );
  });

  it("truncates every playback item at the actual written position", async () => {
    const { controller, provider } = setup();
    const interruptPlayback = vi.fn(async () => undefined);
    provider.session.interruptPlayback = interruptPlayback;
    await controller.start();
    await provider.handler?.({
      type: "audio.output",
      audioBase64: Buffer.alloc(480).toString("base64"),
      itemId: "item-1",
    });
    await provider.handler?.({
      type: "audio.output",
      audioBase64: Buffer.alloc(960).toString("base64"),
      itemId: "item-2",
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    await provider.handler?.({ type: "speech.started", audioStartMs: 0 });
    expect(interruptPlayback).toHaveBeenCalledWith([
      { itemId: "item-1", playedAudioMs: 10 },
      { itemId: "item-2", playedAudioMs: 20 },
    ]);
    expect(provider.session.interrupt).not.toHaveBeenCalled();
  });

  it("measures time from speech stop to the first emitted audio frame", async () => {
    vi.useFakeTimers();
    try {
      const { controller, provider, emit } = setup();
      await controller.start();
      vi.setSystemTime(1_000);
      await provider.handler?.({
        type: "speech.stopped",
        audioEndMs: 500,
        eventId: "speech-stopped",
      });
      vi.setSystemTime(1_100);
      await provider.handler?.({
        type: "response.started",
        responseId: "response-1",
        eventId: "response-started",
      });
      vi.setSystemTime(1_240);
      await provider.handler?.({
        type: "audio.output",
        audioBase64: Buffer.alloc(480).toString("base64"),
        itemId: "item-1",
        eventId: "audio-1",
      });
      vi.setSystemTime(1_500);
      await provider.handler?.({
        type: "response.completed",
        responseId: "response-1",
        eventId: "response-completed",
      });
      expect(emit).toHaveBeenCalledWith(
        expect.objectContaining({
          type: "ai.response_completed",
          safeData: expect.objectContaining({
            first_audio_latency_ms: 240,
            total_latency_ms: 400,
          }),
        }),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it("reconnects boundedly after a transient connect error", async () => {
    const provider = new MockProvider();
    provider.failAttempts = 1;
    const { controller, emit } = setup(provider);
    await controller.start();
    expect(controller.state).toBe("active");
    expect(provider.attempts).toBe(2);
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "ai.session_degraded" }),
    );
  });

  it("fails after the bounded reconnect budget", async () => {
    const provider = new MockProvider();
    provider.failAttempts = 5;
    const { controller } = setup(provider);
    await controller.start();
    expect(controller.state).toBe("failed");
    expect(provider.attempts).toBe(3);
  });

  it("records a transfer request without executing live telephony", async () => {
    const { controller, provider, telephony, emit } = setup();
    await controller.start();
    await controller.transfer("support", "explicit_request");
    expect(provider.session.close).not.toHaveBeenCalled();
    expect(telephony.transfer).not.toHaveBeenCalled();
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "transfer.requested",
        safeData: { reason: "explicit_request" },
      }),
    );
    expect(controller.state).toBe("active");
  });

  it("clears AI playback during handoff and resumes it after a failed operator leg", async () => {
    const provider = new MockProvider();
    const clearPlayback = vi.fn(async () => undefined);
    const emit = vi.fn(async () => undefined);
    const controller = new VoiceSessionController({
      callId: "call-1",
      tenantId: "tenant-1",
      projectId: "project-1",
      config: {
        callId: "call-1",
        tenantId: "tenant-1",
        model: "mock",
        voice: "mock",
        instructions: "safe",
        language: "ru",
        toolDefinitions: [],
      },
      provider,
      tools: new ToolExecutor(async () => ({ ok: true })),
      emit,
      writeAudio: vi.fn(async () => undefined),
      clearPlayback,
    });
    await controller.start();
    await provider.handler?.({
      type: "audio.output",
      audioBase64: Buffer.alloc(480).toString("base64"),
      itemId: "handoff-item",
    });
    await controller.prepareHandoff();
    expect(controller.state).toBe("degraded");
    expect(provider.session.interrupt).toHaveBeenCalledWith(
      "handoff-item",
      expect.any(Number),
    );
    expect(clearPlayback).toHaveBeenCalled();
    await controller.resumeAfterHandoff();
    expect(controller.state).toBe("active");
  });
});
