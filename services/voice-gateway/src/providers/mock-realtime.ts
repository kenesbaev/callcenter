import { randomUUID } from "node:crypto";
import type {
  ProviderStatus,
  RealtimeProviderEvent,
  RealtimeSessionConfig,
  RealtimeVoiceProvider,
  RealtimeVoiceSession,
} from "@teamora/contracts";

type MockScenario =
  "normal" | "rate_limit" | "disconnect" | "malformed" | "delayed";

class MockRealtimeSession implements RealtimeVoiceSession {
  readonly providerSessionId = `mock-${randomUUID()}`;
  private closed = false;
  private itemId = `item-${randomUUID()}`;
  private appendedBytes = 0;
  constructor(
    private config: RealtimeSessionConfig,
    private readonly onEvent: (event: RealtimeProviderEvent) => Promise<void>,
    private readonly scenario: MockScenario,
  ) {}

  async updateSession(config: RealtimeSessionConfig): Promise<void> {
    this.config = config;
  }

  async appendAudio(audio: Uint8Array): Promise<void> {
    if (this.closed) throw new Error("Mock Realtime session is closed");
    this.appendedBytes += audio.byteLength;
    if (this.appendedBytes < 4_800) return;
    this.appendedBytes = 0;
    if (this.scenario === "disconnect") {
      await this.onEvent({
        type: "session.error",
        code: "mock_disconnect",
        retryable: true,
        eventId: randomUUID(),
      });
      return;
    }
    if (this.scenario === "rate_limit") {
      await this.onEvent({
        type: "session.error",
        code: "rate_limit_exceeded",
        retryable: true,
        eventId: randomUUID(),
      });
      return;
    }
    if (this.scenario === "malformed") {
      await this.onEvent({
        type: "session.error",
        code: "invalid_provider_event",
        retryable: false,
        eventId: randomUUID(),
      });
      return;
    }
    if (this.scenario === "delayed")
      await new Promise((resolve) => setTimeout(resolve, 50));
    const responseId = `response-${randomUUID()}`;
    await this.onEvent({
      type: "speech.started",
      audioStartMs: 0,
      eventId: randomUUID(),
    });
    await this.onEvent({
      type: "speech.stopped",
      audioEndMs: 100,
      eventId: randomUUID(),
    });
    await this.onEvent({
      type: "response.started",
      responseId,
      eventId: randomUUID(),
    });
    await this.onEvent({
      type: "transcript.segment",
      speaker: "customer",
      language: this.config.language,
      text: "deterministic test question",
      itemId: `customer-${randomUUID()}`,
      final: true,
      eventId: randomUUID(),
    });
    const pcm = Buffer.alloc(4_800);
    for (let index = 0; index < pcm.length; index += 2)
      pcm.writeInt16LE(index % 320 < 160 ? 4_000 : -4_000, index);
    await this.onEvent({
      type: "audio.output",
      audioBase64: pcm.toString("base64"),
      itemId: this.itemId,
      eventId: randomUUID(),
    });
    await this.onEvent({
      type: "transcript.segment",
      speaker: "ai",
      language: this.config.language,
      text: "Deterministic mock response.",
      itemId: this.itemId,
      final: true,
      eventId: randomUUID(),
    });
    await this.onEvent({
      type: "response.completed",
      responseId,
      usage: { inputAudioTokens: 10, outputAudioTokens: 10, totalTokens: 20 },
      eventId: randomUUID(),
    });
    this.itemId = `item-${randomUUID()}`;
  }

  async sendText(text: string): Promise<void> {
    const pcm = Buffer.alloc(4_800, 0);
    await this.onEvent({
      type: "audio.output",
      audioBase64: pcm.toString("base64"),
      itemId: this.itemId,
      eventId: randomUUID(),
    });
    await this.onEvent({
      type: "transcript.segment",
      speaker: "ai",
      language: this.config.language,
      text,
      itemId: this.itemId,
      final: true,
      eventId: randomUUID(),
    });
  }

  async sendToolResult(_callId: string, _result: unknown): Promise<void> {}
  async interrupt(itemId?: string): Promise<void> {
    await this.onEvent({
      type: "audio.interrupted",
      itemId,
      eventId: randomUUID(),
    });
  }
  async close(reason: string): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    await this.onEvent({
      type: "session.closed",
      reason,
      eventId: randomUUID(),
    });
  }
}

export class MockRealtimeProvider implements RealtimeVoiceProvider {
  readonly name = "mock-realtime";
  readonly status: ProviderStatus = "development";
  private readonly sessions = new Set<MockRealtimeSession>();
  constructor(private readonly scenario: MockScenario = "normal") {}
  async createSession(
    config: RealtimeSessionConfig,
    onEvent: (event: RealtimeProviderEvent) => Promise<void>,
  ): Promise<RealtimeVoiceSession> {
    const session = new MockRealtimeSession(config, onEvent, this.scenario);
    this.sessions.add(session);
    await onEvent({
      type: "session.ready",
      providerSessionId: session.providerSessionId,
      eventId: randomUUID(),
    });
    return session;
  }
  async shutdown(): Promise<void> {
    await Promise.all(
      [...this.sessions].map((session) => session.close("gateway_shutdown")),
    );
    this.sessions.clear();
  }
}
