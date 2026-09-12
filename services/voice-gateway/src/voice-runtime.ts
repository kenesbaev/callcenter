import { randomUUID } from "node:crypto";
import type {
  RealtimeSessionConfig,
  RealtimeVoiceProvider,
  TelephonyCommandContext,
  ToolName,
} from "@teamora/contracts";
import { definitionsFor } from "./tool-registry.js";
import { ToolExecutor } from "./tool-executor.js";
import type { ProviderEventClient } from "./provider-event-client.js";
import type { RtpMediaPool } from "./rtp-media-pool.js";
import {
  VoiceSessionController,
  type SafeSessionEvent,
} from "./session-controller.js";

type ActiveVoice = {
  controller: VoiceSessionController;
  sessionId: string;
  correlationId: string;
};

export class VoiceRuntime {
  private readonly active = new Map<string, ActiveVoice>();
  private readonly stopping = new Map<string, Promise<void>>();

  constructor(
    private readonly providers: ReadonlyMap<string, RealtimeVoiceProvider>,
    private readonly backend: ProviderEventClient,
    private readonly mediaPool: RtpMediaPool,
  ) {}

  async reserve(context: TelephonyCommandContext): Promise<string> {
    await this.mediaPool.reserve(context.callId);
    return this.mediaPool.externalHost(context.callId);
  }

  async start(context: TelephonyCommandContext): Promise<void> {
    if (this.active.has(context.callId)) return;
    const media = this.mediaPool.get(context.callId);
    if (!media) throw new Error("Call RTP media was not reserved");
    const configuration = await this.backend.configureAiSession({
      tenantId: context.tenantId,
      projectId: context.projectId,
      callId: context.callId,
      correlationId: context.correlationId,
    });
    const provider = this.providers.get(configuration.provider);
    if (!provider)
      throw new Error("Configured Realtime provider is unavailable");
    const allowedNames = configuration.tools
      .map((tool) => tool.name)
      .filter((name): name is ToolName => isToolName(name));
    const definitions = definitionsFor(allowedNames).map((definition) => {
      const configured = configuration.tools.find(
        (tool) => tool.name === definition.name,
      );
      return configured
        ? {
            ...definition,
            description: configured.description,
            inputSchema: configured.input_schema,
            timeoutMs: configured.timeout_ms,
          }
        : definition;
    });
    const vad =
      configuration.vad.type === "semantic_vad"
        ? {
            type: "semantic_vad" as const,
            eagerness: String(configuration.vad.eagerness ?? "auto") as
              "low" | "medium" | "high" | "auto",
          }
        : {
            type: "server_vad" as const,
            threshold: Number(configuration.vad.threshold ?? 0.5),
            prefixPaddingMs: Number(configuration.vad.prefixPaddingMs ?? 300),
            silenceDurationMs: Number(
              configuration.vad.silenceDurationMs ?? 700,
            ),
            idleTimeoutMs: Number(configuration.vad.idleTimeoutMs ?? 30_000),
          };
    const sessionConfig: RealtimeSessionConfig = {
      callId: context.callId,
      tenantId: context.tenantId,
      projectId: context.projectId,
      correlationId: context.correlationId,
      model: configuration.model,
      voice: configuration.voice,
      instructions: configuration.instructions,
      language: configuration.language,
      toolDefinitions: definitions,
      vad,
      inputAudioFormat: { type: "audio/pcm", rate: 24_000, channels: 1 },
      outputAudioFormat: { type: "audio/pcm", rate: 24_000, channels: 1 },
      ...(configuration.safetyIdentifier
        ? { safetyIdentifier: configuration.safetyIdentifier }
        : {}),
      ...(configuration.reasoningEffort
        ? { reasoningEffort: configuration.reasoningEffort }
        : {}),
    };
    const tools = new ToolExecutor(async (toolRequest) =>
      this.backend.executeAiTool({
        tenantId: toolRequest.context.tenantId,
        projectId: toolRequest.context.projectId ?? context.projectId,
        callId: toolRequest.context.callId,
        sessionId: toolRequest.context.sessionId ?? configuration.sessionId,
        toolCallId: toolRequest.idempotencyKey,
        name: toolRequest.name,
        arguments: toolRequest.arguments as Record<string, unknown>,
        idempotencyKey: toolRequest.idempotencyKey,
        correlationId:
          toolRequest.context.correlationId ?? context.correlationId,
      }),
    );
    const controller = new VoiceSessionController({
      sessionId: configuration.sessionId,
      callId: context.callId,
      tenantId: context.tenantId,
      projectId: context.projectId,
      config: sessionConfig,
      provider,
      tools,
      emit: (event) => this.emit(context.callId, event),
      writeAudio: (audio) => media.writePcm24k(audio),
      clearPlayback: () => media.clearPlayback(),
    });
    this.active.set(context.callId, {
      controller,
      sessionId: configuration.sessionId,
      correlationId: context.correlationId,
    });
    media.attachAudioHandler((audio) => controller.appendAudio(audio));
    try {
      await controller.start();
      if (configuration.disclosureRequired && configuration.disclosureText) {
        await controller.playDisclosure(configuration.disclosureText);
      }
    } catch (error) {
      await this.stop(context.callId, "session_start_failed");
      throw error;
    }
  }

  async stop(callId: string, reason: string): Promise<void> {
    const existing = this.stopping.get(callId);
    if (existing) return existing;
    const pending = this.stopOnce(callId, reason);
    this.stopping.set(callId, pending);
    try {
      await pending;
    } finally {
      this.stopping.delete(callId);
    }
  }

  async prepareHandoff(callId: string): Promise<void> {
    const active = this.active.get(callId);
    if (!active) return;
    await active.controller.prepareHandoff();
  }

  async resumeAfterHandoff(callId: string): Promise<void> {
    const active = this.active.get(callId);
    if (!active) return;
    await active.controller.resumeAfterHandoff();
  }

  private async stopOnce(callId: string, reason: string): Promise<void> {
    const active = this.active.get(callId);
    this.mediaPool.get(callId)?.detachAudioHandler();
    try {
      // Keep the active context until close() has emitted its terminal event.
      // Removing it earlier silently drops ai.session_closed in emit().
      if (active) await active.controller.close(reason);
    } finally {
      this.active.delete(callId);
      await this.mediaPool.release(callId);
    }
  }

  async shutdown(reason: string): Promise<void> {
    await Promise.all(
      [...new Set([...this.active.keys()])].map((callId) =>
        this.stop(callId, reason),
      ),
    );
    await this.mediaPool.shutdown();
  }

  status(): string {
    return this.active.size ? `active:${this.active.size}` : "idle";
  }

  private async emit(callId: string, event: SafeSessionEvent): Promise<void> {
    const active = this.active.get(callId);
    if (!active) return;
    await this.backend.publishAiEvent({
      tenantId: event.tenantId,
      projectId: event.projectId,
      callId: event.callId,
      sessionId: active.sessionId,
      providerEventId: event.providerEventId ?? `gateway:${randomUUID()}`,
      eventType: event.type,
      occurredAt: event.occurredAt,
      correlationId: active.correlationId,
      safePayload: event.safeData ?? {},
    });
  }
}

const knownTools = new Set([
  "advance_call_flow",
  "search_knowledge",
  "get_customer",
  "update_customer_field",
  "create_task",
  "create_callback",
  "submit_call_result",
  "request_human_transfer",
  "end_conversation",
  "wait_for_user",
  "find_customer",
  "create_customer",
  "create_lead",
  "create_support_ticket",
  "get_order_status",
  "book_appointment",
  "reschedule_appointment",
  "cancel_appointment",
  "send_confirmation",
  "request_human_operator",
  "transfer_call",
  "end_call",
]);
function isToolName(value: string): value is ToolName {
  return knownTools.has(value);
}
