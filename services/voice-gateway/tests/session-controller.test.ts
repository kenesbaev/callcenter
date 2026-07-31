import { describe, expect, it, vi } from "vitest";
import type {
  RealtimeProviderEvent,
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
    writeAudio: vi.fn(async () => undefined),
    maxReconnects: 2,
  });
  return { controller, emit, provider, telephony };
}

describe("VoiceSessionController", () => {
  it("starts and supports barge-in", async () => {
    const { controller, provider, emit } = setup();
    await controller.start();
    expect(controller.state).toBe("active");
    await controller.customerSpeechStarted();
    expect(provider.session.interrupt).toHaveBeenCalledOnce();
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "audio.barge_in", tenantId: "tenant-1" }),
    );
  });

  it("reconnects boundedly after a transient connect error", async () => {
    const provider = new MockProvider();
    provider.failAttempts = 1;
    const { controller, emit } = setup(provider);
    await controller.start();
    expect(controller.state).toBe("active");
    expect(provider.attempts).toBe(2);
    expect(emit).toHaveBeenCalledWith(
      expect.objectContaining({ type: "session.reconnect" }),
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

  it("closes realtime before transferring to a human queue", async () => {
    const { controller, provider, telephony } = setup();
    await controller.start();
    await controller.transfer("support", "explicit_request");
    expect(provider.session.close).toHaveBeenCalledWith("human_transfer");
    expect(telephony.transfer).toHaveBeenCalledWith(
      expect.objectContaining({
        tenantId: "tenant-1",
        projectId: "project-1",
        callId: "call-1",
        providerCallId: "channel-1",
      }),
      "support",
      "explicit_request",
    );
    expect(controller.state).toBe("transferring");
  });
});
