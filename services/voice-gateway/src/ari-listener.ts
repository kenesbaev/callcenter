import { randomUUID } from "node:crypto";
import WebSocket from "ws";
import type {
  TelephonyCommandContext,
  TelephonyProviderEvent,
} from "@teamora/contracts";
import { logger } from "./logger.js";
import type {
  InboundAcceptance,
  ProviderEventClient,
} from "./provider-event-client.js";
import type { AsteriskAriProvider } from "./providers/asterisk-ari.js";
import type { VoiceRuntime } from "./voice-runtime.js";

type AriListenerOptions = {
  baseUrl: string;
  username: string;
  password: string;
  application: string;
  reconnectMinMs: number;
  reconnectMaxMs: number;
};

type AriEvent = {
  type?: string;
  timestamp?: string;
  args?: string[];
  digit?: string;
  channel?: {
    id?: string;
    state?: string;
    caller?: { number?: string };
    dialplan?: { exten?: string };
  };
  bridge?: { id?: string };
  recording?: { name?: string; target_uri?: string };
  cause_txt?: string;
};

export class AsteriskAriListener {
  private socket: WebSocket | undefined;
  private stopping = false;
  private reconnectAttempt = 0;
  private reconnectTimer: NodeJS.Timeout | undefined;
  private processing = Promise.resolve();
  private readonly callContexts = new Map<string, TelephonyCommandContext>();
  private readonly contextsByCall = new Map<string, TelephonyCommandContext>();
  private readonly recordingCalls = new Set<string>();
  private readonly bridgeByCall = new Map<string, string>();
  private readonly mediaByCall = new Map<string, string>();
  private readonly operatorByCall = new Map<string, string>();
  private readonly connectedOperatorCalls = new Set<string>();

  constructor(
    private readonly options: AriListenerOptions,
    private readonly provider: AsteriskAriProvider,
    private readonly events: ProviderEventClient,
    private readonly voiceRuntime?: VoiceRuntime,
  ) {}

  start(): void {
    this.stopping = false;
    this.connect();
  }

  async stop(): Promise<void> {
    this.stopping = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    const socket = this.socket;
    this.socket = undefined;
    if (socket && socket.readyState < WebSocket.CLOSING) {
      await new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, 2_000);
        socket.once("close", () => {
          clearTimeout(timer);
          resolve();
        });
        socket.close(1001, "gateway shutdown");
      });
    }
    await this.processing;
  }

  status(): "connected" | "reconnecting" | "stopped" {
    if (this.stopping) return "stopped";
    return this.socket?.readyState === WebSocket.OPEN
      ? "connected"
      : "reconnecting";
  }

  async dialOperator(input: {
    tenantId: string;
    projectId: string;
    callId: string;
    destinationType: "browser" | "sip" | "mobile";
    destination: string;
    correlationId: string;
  }): Promise<{ status: "connecting"; operatorChannelId: string }> {
    const context = this.contextsByCall.get(input.callId);
    const bridgeId = this.bridgeByCall.get(input.callId);
    if (
      !context ||
      context.tenantId !== input.tenantId ||
      context.projectId !== input.projectId ||
      !bridgeId
    ) {
      throw new Error("Live call bridge was not found");
    }
    const existing = this.operatorByCall.get(input.callId);
    if (existing) return { status: "connecting", operatorChannelId: existing };
    await this.voiceRuntime?.prepareHandoff(input.callId);
    await this.provider.startMoh(bridgeId);
    try {
      const operatorChannelId = await this.provider.originateOperatorLeg(
        { ...context, correlationId: input.correlationId },
        input.destinationType,
        input.destination,
      );
      this.operatorByCall.set(input.callId, operatorChannelId);
      this.callContexts.set(operatorChannelId, context);
      await this.publish(
        context,
        { type: "OperatorDial", timestamp: new Date().toISOString() },
        "transfer.started",
        { operatorChannelId },
      );
      return { status: "connecting", operatorChannelId };
    } catch (error) {
      await this.provider.stopMoh(bridgeId).catch(() => undefined);
      await this.voiceRuntime?.resumeAfterHandoff(input.callId);
      throw error;
    }
  }

  async cancelOperator(callId: string, reason: string): Promise<void> {
    const context = this.contextsByCall.get(callId);
    const operatorChannelId = this.operatorByCall.get(callId);
    const bridgeId = this.bridgeByCall.get(callId);
    if (operatorChannelId) {
      await this.provider
        .hangupChannel(operatorChannelId)
        .catch(() => undefined);
      this.operatorByCall.delete(callId);
      this.callContexts.delete(operatorChannelId);
      this.provider.forgetChannel(operatorChannelId);
    }
    if (bridgeId) await this.provider.stopMoh(bridgeId).catch(() => undefined);
    await this.voiceRuntime?.resumeAfterHandoff(callId);
    if (context) {
      await this.publish(
        context,
        { type: "OperatorDialFailed", timestamp: new Date().toISOString() },
        "transfer.failed",
        { cause: safeCause(reason) },
      );
    }
  }

  private connect(): void {
    if (this.stopping) return;
    const base = new URL(this.options.baseUrl);
    base.protocol = base.protocol === "https:" ? "wss:" : "ws:";
    base.pathname = `${base.pathname.replace(/\/$/, "")}/ari/events`;
    base.search = new URLSearchParams({
      app: this.options.application,
      subscribeAll: "true",
    }).toString();
    const authorization = `Basic ${Buffer.from(`${this.options.username}:${this.options.password}`).toString("base64")}`;
    const socket = new WebSocket(base, { headers: { authorization } });
    this.socket = socket;
    socket.on("open", () => {
      this.reconnectAttempt = 0;
      logger.info("ARI event listener connected");
    });
    socket.on("message", (raw) => {
      this.processing = this.processing
        .then(async () => this.handle(JSON.parse(raw.toString()) as AriEvent))
        .catch((error: unknown) =>
          logger.warn(
            { code: error instanceof Error ? error.name : "ari_event_error" },
            "ARI event handling failed",
          ),
        );
    });
    socket.on("error", (error) =>
      logger.warn({ code: error.name }, "ARI listener connection error"),
    );
    socket.on("close", () => {
      this.socket = undefined;
      if (!this.stopping) this.scheduleReconnect();
    });
  }

  private scheduleReconnect(): void {
    const exponential = Math.min(
      this.options.reconnectMaxMs,
      this.options.reconnectMinMs * 2 ** this.reconnectAttempt,
    );
    this.reconnectAttempt += 1;
    const delay = Math.round(exponential * (0.8 + Math.random() * 0.4));
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
    logger.warn({ delayMs: delay }, "ARI listener reconnect scheduled");
  }

  private async handle(event: AriEvent): Promise<void> {
    const type = event.type ?? "Unknown";
    const channelId = event.channel?.id;
    if (type === "StasisStart" && channelId) {
      await this.onStasisStart(event, channelId);
      return;
    }
    const context = channelId
      ? (this.callContexts.get(channelId) ??
        this.provider.contextForChannel(channelId))
      : (this.contextForBridge(event.bridge?.id) ??
        this.contextForRecording(event.recording?.name));
    if (!context) return;
    if (type === "ChannelStateChange") {
      if (channelId === this.operatorByCall.get(context.callId)) return;
      const state = (event.channel?.state ?? "").toLowerCase();
      if (state === "up")
        await this.publish(context, event, "call.answered", {
          channelState: "Up",
        });
      else if (state.includes("ring"))
        await this.publish(context, event, "call.ringing", {
          channelState: state,
        });
      return;
    }
    if (type === "ChannelDtmfReceived" && event.digit) {
      await this.publish(context, event, "call.dtmf", {
        dtmfDigit: event.digit.slice(0, 1),
      });
      return;
    }
    if (type === "RecordingStarted" && event.recording?.name) {
      await this.publish(context, event, "recording.started", {
        recordingId: event.recording.name,
      });
      return;
    }
    if (type === "RecordingFinished" && event.recording?.name) {
      await this.publish(context, event, "recording.finished", {
        recordingId: event.recording.name,
      });
      this.recordingCalls.delete(context.callId);
      this.contextsByCall.delete(context.callId);
      return;
    }
    if (type === "StasisEnd" || type === "ChannelDestroyed") {
      if (channelId === this.operatorByCall.get(context.callId)) {
        if (this.connectedOperatorCalls.has(context.callId)) {
          await this.publish(context, event, "call.hangup", {
            rawCause: safeCause(event.cause_txt),
          });
          await this.cleanup(context, channelId);
          return;
        }
        await this.cancelOperator(context.callId, "operator_leg_ended");
        return;
      }
      await this.publish(context, event, "call.hangup", {
        rawCause: safeCause(event.cause_txt),
      });
      await this.cleanup(context, channelId);
    }
  }

  private async onStasisStart(
    event: AriEvent,
    channelId: string,
  ): Promise<void> {
    const args = event.args ?? [];
    if (args[0] === "operator") {
      const callId = args[1] ?? "";
      const context = this.contextsByCall.get(callId);
      const bridgeId = this.bridgeByCall.get(callId);
      if (
        !context ||
        !bridgeId ||
        this.operatorByCall.get(callId) !== channelId
      ) {
        await this.provider.hangupChannel(channelId).catch(() => undefined);
        return;
      }
      await this.provider.addChannelToBridge(bridgeId, channelId);
      await this.provider.stopMoh(bridgeId).catch(() => undefined);
      const mediaId = this.mediaByCall.get(callId);
      if (mediaId) {
        await this.provider
          .removeChannelFromBridge(bridgeId, mediaId)
          .catch(() => undefined);
      }
      await this.voiceRuntime?.stop(callId, "human_handoff_connected");
      if (mediaId) {
        this.callContexts.delete(mediaId);
        this.provider.forgetChannel(mediaId);
        await this.provider.hangupChannel(mediaId).catch(() => undefined);
        this.mediaByCall.delete(callId);
      }
      this.connectedOperatorCalls.add(callId);
      await this.publish(context, event, "transfer.completed", {
        operatorChannelId: channelId,
      });
      return;
    }
    const existing = this.provider.contextForChannel(channelId);
    if (existing) {
      this.callContexts.set(channelId, existing);
      this.contextsByCall.set(existing.callId, existing);
      if (channelId.startsWith("teamora-media-")) {
        this.mediaByCall.set(existing.callId, channelId);
        await this.publish(existing, event, "external_media.created", {
          mediaId: channelId,
        });
      }
      return;
    }
    if (args[0] !== "inbound") return;
    const did = normalizeDid(args[1] ?? event.channel?.dialplan?.exten ?? "");
    const sourceIp = normalizeSource(args[2] ?? "");
    const occurredAt = event.timestamp ?? new Date().toISOString();
    let accepted: InboundAcceptance;
    try {
      accepted = await this.events.acceptInbound({
        providerEventId: this.events.eventId(event as Record<string, unknown>),
        channelId,
        did,
        ...(event.channel?.caller?.number
          ? { callerNumber: normalizeDid(event.channel.caller.number) }
          : {}),
        sourceIp,
        occurredAt,
        correlationId: randomUUID(),
        safePayload: {},
      });
    } catch {
      await this.provider.request(
        `/channels/${encodeURIComponent(channelId)}?reason=rejected`,
        { method: "DELETE" },
      );
      return;
    }
    const context: TelephonyCommandContext = {
      tenantId: accepted.tenantId,
      projectId: accepted.projectId,
      callId: accepted.callId,
      providerCallId: channelId,
      commandId: randomUUID(),
      idempotencyKey: `inbound-${accepted.callId}`,
      timestamp: occurredAt,
      correlationId: randomUUID(),
    };
    this.callContexts.set(channelId, context);
    this.contextsByCall.set(context.callId, context);
    this.provider.registerChannelContext(channelId, context);
    const bridgeId = `teamora-bridge-${context.callId}`;
    this.bridgeByCall.set(context.callId, bridgeId);
    await this.provider.createBridge(bridgeId);
    await this.provider.addChannelToBridge(bridgeId, channelId);
    const aiExternalHost =
      accepted.aiSessionAvailable && this.voiceRuntime
        ? await this.voiceRuntime.reserve(context)
        : undefined;
    let media;
    try {
      media = await this.provider.createExternalMedia(context, aiExternalHost);
    } catch (error) {
      if (aiExternalHost) {
        await this.voiceRuntime?.stop(context.callId, "external_media_failed");
      }
      throw error;
    }
    const mediaId = String(media.safeMetadata.mediaId ?? "");
    if (mediaId) {
      this.mediaByCall.set(context.callId, mediaId);
      await this.provider.addChannelToBridge(bridgeId, mediaId);
      await this.publish(context, event, "external_media.created", { mediaId });
    }
    await this.publish(context, event, "bridge.created", { bridgeId });
    await this.provider.answer(context);
    await this.publish(context, event, "call.answered", { channelState: "Up" });
    if (accepted.aiSessionAvailable && this.voiceRuntime) {
      try {
        await this.voiceRuntime.start(context);
      } catch (error) {
        logger.warn(
          {
            callId: context.callId,
            code: error instanceof Error ? error.name : "ai_session_error",
          },
          "AI voice session failed to start",
        );
        await this.publish(context, event, "call.failed", {
          rawCause: "ai_session_unavailable",
        });
      }
    } else if (accepted.aiSessionAvailable) {
      await this.provider
        .request(
          `/channels/${encodeURIComponent(channelId)}/play?media=${encodeURIComponent("sound:vm-sorry")}`,
          { method: "POST" },
        )
        .catch(() => undefined);
      await this.publish(context, event, "call.failed", {
        rawCause: "ai_provider_not_configured",
      });
      await this.provider
        .hangup(context, "provider_error")
        .catch(() => undefined);
    }
    if (accepted.recordingAllowed && !accepted.disclosureRequired) {
      await this.provider.startRecording(context);
      this.recordingCalls.add(context.callId);
    }
  }

  private async cleanup(
    context: TelephonyCommandContext,
    channelId?: string,
  ): Promise<void> {
    const bridgeId = this.bridgeByCall.get(context.callId);
    const mediaId = this.mediaByCall.get(context.callId);
    const operatorChannelId = this.operatorByCall.get(context.callId);
    await this.voiceRuntime?.stop(context.callId, "telephony_cleanup");
    if (operatorChannelId && operatorChannelId !== channelId) {
      await this.provider
        .hangupChannel(operatorChannelId)
        .catch(() => undefined);
      this.callContexts.delete(operatorChannelId);
      this.provider.forgetChannel(operatorChannelId);
    }
    if (mediaId) {
      await this.provider
        .request(`/channels/${encodeURIComponent(mediaId)}`, {
          method: "DELETE",
        })
        .catch(() => undefined);
      await this.publish(
        context,
        { type: "cleanup", timestamp: new Date().toISOString() },
        "external_media.destroyed",
        { mediaId },
      );
    }
    if (bridgeId) {
      await this.provider.destroyBridge(bridgeId).catch(() => undefined);
      await this.publish(
        context,
        { type: "cleanup", timestamp: new Date().toISOString() },
        "bridge.destroyed",
        { bridgeId },
      );
    }
    if (channelId) {
      this.callContexts.delete(channelId);
      this.provider.forgetChannel(channelId);
    }
    this.bridgeByCall.delete(context.callId);
    this.mediaByCall.delete(context.callId);
    this.operatorByCall.delete(context.callId);
    this.connectedOperatorCalls.delete(context.callId);
    if (!this.recordingCalls.has(context.callId)) {
      this.contextsByCall.delete(context.callId);
    }
  }

  private async publish(
    context: TelephonyCommandContext,
    rawEvent: AriEvent,
    eventType: string,
    safePayload: Record<string, boolean | number | string>,
  ): Promise<void> {
    const event: TelephonyProviderEvent = {
      version: "1",
      provider: "asterisk-ari",
      providerEventId: this.events.eventId({
        ...(rawEvent as Record<string, unknown>),
        normalizedEventType: eventType,
        normalizedSafePayload: safePayload,
      }),
      eventType,
      tenantId: context.tenantId,
      projectId: context.projectId,
      callId: context.callId,
      ...(context.providerCallId
        ? { externalCallId: context.providerCallId }
        : {}),
      occurredAt: rawEvent.timestamp ?? new Date().toISOString(),
      providerTimestamp: rawEvent.timestamp ?? new Date().toISOString(),
      correlationId: context.correlationId,
      safePayload,
    };
    await this.events.publish(event);
  }

  private contextForBridge(
    bridgeId?: string,
  ): TelephonyCommandContext | undefined {
    if (!bridgeId) return undefined;
    const callId = [...this.bridgeByCall.entries()].find(
      ([, value]) => value === bridgeId,
    )?.[0];
    return callId
      ? [...this.callContexts.values()].find((item) => item.callId === callId)
      : undefined;
  }

  private contextForRecording(
    name?: string,
  ): TelephonyCommandContext | undefined {
    if (!name?.startsWith("teamora-")) return undefined;
    const callId = name.slice("teamora-".length);
    return this.contextsByCall.get(callId);
  }
}

function normalizeDid(value: string): string {
  const compact = value.replace(/[^0-9+]/g, "");
  if (compact.startsWith("+")) return compact;
  if (compact.startsWith("00")) return `+${compact.slice(2)}`;
  return `+${compact}`;
}

function normalizeSource(value: string): string {
  const bracketed = /^\[([^\]]+)\]/.exec(value)?.[1];
  if (bracketed) return bracketed;
  return value.split(":", 1)[0] ?? value;
}

function safeCause(value?: string): string {
  return (
    (value ?? "unknown").replace(/[^A-Za-z0-9 _.-]/g, "").slice(0, 80) ||
    "unknown"
  );
}
