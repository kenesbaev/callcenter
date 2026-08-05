import type {
  TelephonyCallState,
  TelephonyCommand,
  TelephonyCommandContext,
  TelephonyCommandResult,
  TelephonyProvider,
} from "@teamora/contracts";

type AsteriskOptions = {
  baseUrl: string;
  username: string;
  password: string;
  externalHost: string;
  application?: string;
  pjsipEndpoint?: string;
  mediaFormat?: string;
  transport?: "websocket" | "udp";
  timeoutMs?: number;
  fetchImplementation?: typeof fetch;
};

export class AsteriskAriError extends Error {
  readonly code = "asterisk_ari_error";
  constructor(readonly status: number) {
    super(`Asterisk ARI request failed with status ${status}`);
    this.name = "AsteriskAriError";
  }
}

export class AsteriskAriProvider implements TelephonyProvider {
  readonly name = "asterisk-ari";
  readonly status = "configured" as const;
  private readonly fetchImplementation: typeof fetch;
  private readonly channelContexts = new Map<string, TelephonyCommandContext>();

  constructor(private readonly options: AsteriskOptions) {
    this.fetchImplementation = options.fetchImplementation ?? fetch;
  }

  async originate(
    context: TelephonyCommandContext,
    parameters: TelephonyCommand["parameters"],
  ): Promise<TelephonyCommandResult> {
    const toNumber = requiredString(parameters.toNumber, "toNumber");
    const callerId = requiredString(parameters.fromNumber, "fromNumber");
    const channelId = `teamora-${context.callId}`;
    const endpoint = this.options.pjsipEndpoint
      ? `PJSIP/${toNumber}@${this.options.pjsipEndpoint}`
      : `PJSIP/${toNumber}`;
    const query = new URLSearchParams({
      endpoint,
      app: this.options.application ?? "teamora-voice",
      appArgs: context.callId,
      callerId,
      channelId,
    });
    await this.request(`/channels?${query.toString()}`, { method: "POST" });
    this.channelContexts.set(channelId, context);
    return result(context, this.name, "ringing", channelId);
  }

  async answer(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult> {
    await this.request(`/channels/${channel(context)}/answer`, {
      method: "POST",
    });
    return result(context, this.name, "active");
  }

  async hangup(
    context: TelephonyCommandContext,
    reason: string,
  ): Promise<TelephonyCommandResult> {
    await this.request(
      `/channels/${channel(context)}?reason=${encodeURIComponent(reason)}`,
      { method: "DELETE" },
    );
    return result(context, this.name, "completed");
  }

  async hold(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult> {
    await this.request(`/channels/${channel(context)}/hold`, {
      method: "POST",
    });
    return result(context, this.name, "on_hold");
  }

  async resume(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult> {
    await this.request(`/channels/${channel(context)}/hold`, {
      method: "DELETE",
    });
    return result(context, this.name, "active");
  }

  async transfer(
    context: TelephonyCommandContext,
    destination: string,
    _reason: string,
  ): Promise<TelephonyCommandResult> {
    await this.request(
      `/channels/${channel(context)}/continue?context=teamora-transfer&extension=${encodeURIComponent(destination)}&priority=1`,
      { method: "POST" },
    );
    return result(context, this.name, "transferred");
  }

  async getCallState(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult> {
    const payload = await this.request(`/channels/${channel(context)}`, {
      method: "GET",
    });
    const state = normalizeAriState(
      payload && !Array.isArray(payload) && typeof payload.state === "string"
        ? payload.state
        : "unknown",
    );
    return result(context, this.name, state);
  }

  async startRecording(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult> {
    const recordingId = recording(context);
    const query = new URLSearchParams({
      name: recordingId,
      format: "wav",
      ifExists: "fail",
      beep: "false",
    });
    // Record the mixing bridge, not the customer channel: ARI rejects a
    // channel recording once that channel is already part of a bridge, and a
    // bridge recording is also what captures both SIP and External Media.
    await this.request(`/bridges/${bridge(context)}/record?${query}`, {
      method: "POST",
    });
    return result(context, this.name, "active", undefined, { recordingId });
  }

  async pauseRecording(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult> {
    await this.request(`/recordings/live/${recording(context)}/pause`, {
      method: "POST",
    });
    return result(context, this.name, "active", undefined, {
      recordingState: "paused",
    });
  }

  async resumeRecording(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult> {
    await this.request(`/recordings/live/${recording(context)}/unpause`, {
      method: "POST",
    });
    return result(context, this.name, "active", undefined, {
      recordingState: "recording",
    });
  }

  async stopRecording(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult> {
    await this.request(`/recordings/live/${recording(context)}`, {
      method: "DELETE",
    });
    return result(context, this.name, "active", undefined, {
      recordingState: "stopped",
    });
  }

  async createExternalMedia(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult> {
    const mediaId = `teamora-media-${context.callId}`;
    const transport = this.options.transport ?? "websocket";
    const query = new URLSearchParams({
      app: this.options.application ?? "teamora-voice",
      channelId: mediaId,
      external_host: this.options.externalHost,
      format: this.options.mediaFormat ?? "ulaw",
      transport,
      encapsulation: transport === "udp" ? "rtp" : "none",
      connection_type: "client",
      direction: "both",
    });
    await this.request(`/channels/externalMedia?${query.toString()}`, {
      method: "POST",
    });
    this.channelContexts.set(mediaId, context);
    return result(context, this.name, "active", undefined, { mediaId });
  }

  contextForChannel(channelId: string): TelephonyCommandContext | undefined {
    return this.channelContexts.get(channelId);
  }

  registerChannelContext(
    channelId: string,
    context: TelephonyCommandContext,
  ): void {
    this.channelContexts.set(channelId, context);
  }

  forgetChannel(channelId: string): void {
    this.channelContexts.delete(channelId);
  }

  async createBridge(bridgeId: string): Promise<void> {
    const query = new URLSearchParams({ type: "mixing,proxy_media", bridgeId });
    await this.request(`/bridges?${query.toString()}`, { method: "POST" });
  }

  async addChannelToBridge(bridgeId: string, channelId: string): Promise<void> {
    const query = new URLSearchParams({ channel: channelId });
    await this.request(
      `/bridges/${encodeURIComponent(bridgeId)}/addChannel?${query}`,
      {
        method: "POST",
      },
    );
  }

  async destroyBridge(bridgeId: string): Promise<void> {
    await this.request(`/bridges/${encodeURIComponent(bridgeId)}`, {
      method: "DELETE",
    });
  }

  async listChannels(): Promise<Array<Record<string, unknown>>> {
    const result = await this.request("/channels", { method: "GET" });
    return Array.isArray(result)
      ? (result as Array<Record<string, unknown>>)
      : [];
  }

  async listBridges(): Promise<Array<Record<string, unknown>>> {
    const result = await this.request("/bridges", { method: "GET" });
    return Array.isArray(result)
      ? (result as Array<Record<string, unknown>>)
      : [];
  }

  async request(
    path: string,
    init: RequestInit,
  ): Promise<
    Record<string, unknown> | Array<Record<string, unknown>> | undefined
  > {
    const response = await this.fetchImplementation(
      `${this.options.baseUrl.replace(/\/$/, "")}/ari${path}`,
      {
        ...init,
        headers: {
          authorization: `Basic ${Buffer.from(`${this.options.username}:${this.options.password}`).toString("base64")}`,
          accept: "application/json",
        },
        signal: AbortSignal.timeout(this.options.timeoutMs ?? 5000),
      },
    );
    if (!response.ok) throw new AsteriskAriError(response.status);
    if (response.status === 204) return undefined;
    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) return undefined;
    return (await response.json()) as
      Record<string, unknown> | Array<Record<string, unknown>>;
  }
}

function channel(context: TelephonyCommandContext): string {
  if (!context.providerCallId) throw new Error("providerCallId is required");
  return encodeURIComponent(context.providerCallId);
}

function bridge(context: TelephonyCommandContext): string {
  return encodeURIComponent(`teamora-bridge-${context.callId}`);
}

function recording(context: TelephonyCommandContext): string {
  return encodeURIComponent(`teamora-${context.callId}`);
}

function requiredString(value: unknown, name: string): string {
  if (typeof value !== "string" || !value.trim())
    throw new Error(`${name} is required`);
  return value;
}

function normalizeAriState(state: string): TelephonyCallState {
  const normalized = state.toLowerCase();
  if (normalized === "ring" || normalized === "ringing") return "ringing";
  if (normalized === "up") return "active";
  if (normalized === "busy") return "busy";
  if (normalized === "down") return "completed";
  return "unknown";
}

function result(
  context: TelephonyCommandContext,
  provider: string,
  state: TelephonyCallState,
  providerCallId = context.providerCallId,
  safeMetadata: Record<string, boolean | number | string> = {},
): TelephonyCommandResult {
  return {
    commandId: context.commandId,
    provider,
    accepted: true,
    state,
    ...(providerCallId ? { providerCallId } : {}),
    occurredAt: new Date().toISOString(),
    safeMetadata,
  };
}
