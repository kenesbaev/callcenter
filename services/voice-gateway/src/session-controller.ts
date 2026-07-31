import type {
  RealtimeProviderEvent,
  RealtimeSessionConfig,
  RealtimeVoiceProvider,
  RealtimeVoiceSession,
  TelephonyProvider,
  ToolName,
} from "@teamora/contracts";
import type { ToolExecutor } from "./tool-executor.js";

export type SessionState =
  | "idle"
  | "connecting"
  | "active"
  | "transferring"
  | "reconnecting"
  | "closing"
  | "closed"
  | "failed";
export type SafeSessionEvent = {
  type: string;
  callId: string;
  tenantId: string;
  projectId: string;
  safeData?: Record<string, boolean | number | string>;
};

type ControllerOptions = {
  callId: string;
  tenantId: string;
  projectId: string;
  channelId: string;
  config: RealtimeSessionConfig;
  provider: RealtimeVoiceProvider;
  telephony: TelephonyProvider;
  tools: ToolExecutor;
  emit: (event: SafeSessionEvent) => Promise<void>;
  writeAudio: (audio: Uint8Array) => Promise<void>;
  maxReconnects?: number;
};

export class VoiceSessionController {
  private currentState: SessionState = "idle";
  private providerSession?: RealtimeVoiceSession;
  private reconnects = 0;
  constructor(private readonly options: ControllerOptions) {}
  get state(): SessionState {
    return this.currentState;
  }

  async start(): Promise<void> {
    if (this.currentState !== "idle")
      throw new Error("Session can only start once");
    this.currentState = "connecting";
    await this.connect();
  }

  async appendAudio(audio: Uint8Array): Promise<void> {
    if (this.currentState !== "active" || !this.providerSession) return;
    await this.providerSession.appendAudio(audio);
  }

  async customerSpeechStarted(): Promise<void> {
    if (this.currentState !== "active" || !this.providerSession) return;
    await this.providerSession.interrupt();
    await this.options.emit({
      type: "audio.barge_in",
      callId: this.options.callId,
      tenantId: this.options.tenantId,
      projectId: this.options.projectId,
    });
  }

  async transfer(queue: string, reason: string): Promise<void> {
    if (!this.providerSession || this.currentState !== "active")
      throw new Error("Only active calls can transfer");
    this.currentState = "transferring";
    await this.providerSession.close("human_transfer");
    const commandId = crypto.randomUUID();
    await this.options.telephony.transfer(
      {
        tenantId: this.options.tenantId,
        projectId: this.options.projectId,
        callId: this.options.callId,
        providerCallId: this.options.channelId,
        commandId,
        idempotencyKey: `${this.options.callId}:transfer:${commandId}`,
        timestamp: new Date().toISOString(),
        correlationId: commandId,
      },
      queue,
      reason,
    );
    await this.options.emit({
      type: "call.transfer",
      callId: this.options.callId,
      tenantId: this.options.tenantId,
      projectId: this.options.projectId,
      safeData: { reason, queue },
    });
  }

  async close(reason: string): Promise<void> {
    if (["closed", "closing"].includes(this.currentState)) return;
    this.currentState = "closing";
    await this.providerSession?.close(reason);
    this.currentState = "closed";
    await this.options.emit({
      type: "session.closed",
      callId: this.options.callId,
      tenantId: this.options.tenantId,
      projectId: this.options.projectId,
      safeData: { reason },
    });
  }

  private async connect(): Promise<void> {
    try {
      this.providerSession = await this.options.provider.createSession(
        this.options.config,
        (event) => this.handleProviderEvent(event),
      );
      this.currentState = "active";
    } catch (error) {
      await this.handleConnectionFailure(error);
    }
  }

  private async handleConnectionFailure(error: unknown): Promise<void> {
    if (this.reconnects >= (this.options.maxReconnects ?? 2)) {
      this.currentState = "failed";
      await this.options.emit({
        type: "session.failed",
        callId: this.options.callId,
        tenantId: this.options.tenantId,
        projectId: this.options.projectId,
        safeData: {
          code: error instanceof Error ? error.name : "connection_error",
        },
      });
      return;
    }
    this.reconnects += 1;
    this.currentState = "reconnecting";
    await this.options.emit({
      type: "session.reconnect",
      callId: this.options.callId,
      tenantId: this.options.tenantId,
      projectId: this.options.projectId,
      safeData: { attempt: this.reconnects },
    });
    await this.connect();
  }

  private async handleProviderEvent(
    event: RealtimeProviderEvent,
  ): Promise<void> {
    if (event.type === "audio.output")
      await this.options.writeAudio(Buffer.from(event.audioBase64, "base64"));
    else if (event.type === "audio.interrupted")
      await this.customerSpeechStarted();
    else if (event.type === "tool.requested" && this.providerSession) {
      const result = await this.options.tools.execute(
        {
          tenantId: this.options.tenantId,
          callId: this.options.callId,
          allowedTools: this.options.config.toolDefinitions.map(
            (definition) => definition.name as ToolName,
          ),
        },
        event.name,
        event.arguments,
        `${this.options.callId}:${event.callId}`,
      );
      await this.providerSession.sendToolResult(event.callId, result);
    } else if (
      event.type === "session.error" &&
      event.retryable &&
      this.currentState === "active"
    ) {
      await this.providerSession?.close("provider_reconnect");
      await this.handleConnectionFailure(new Error(event.code));
    }
    if (event.type !== "audio.output" && event.type !== "transcript.segment")
      await this.options.emit({
        type: `provider.${event.type}`,
        callId: this.options.callId,
        tenantId: this.options.tenantId,
        projectId: this.options.projectId,
      });
  }
}
