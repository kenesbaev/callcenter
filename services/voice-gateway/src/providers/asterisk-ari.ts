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
    try {
      await this.request(`/channels?${query.toString()}`, { method: "POST" });
    } catch (error) {
      if (!(error instanceof AsteriskAriError) || error.status !== 409)
        throw error;
      // The deterministic channel ID makes a retried originate safe after a
      // Gateway restart. Accept 409 only when that exact channel still exists.
      await this.request(`/channels/${encodeURIComponent(channelId)}`, {
        method: "GET",
      });
    }
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
    externalHost = this.options.externalHost,
  ): Promise<TelephonyCommandResult> {
    const mediaId = `teamora-media-${context.callId}`;
    const transport = this.options.transport ?? "websocket";
    const query = new URLSearchParams({
      app: this.options.application ?? "teamora-voice",
      channelId: mediaId,
      external_host: externalHost,
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

  async removeChannelFromBridge(
    bridgeId: string,
    channelId: string,
  ): Promise<void> {
    const query = new URLSearchParams({ channel: channelId });
    await this.request(
      `/bridges/${encodeURIComponent(bridgeId)}/removeChannel?${query}`,
      { method: "POST" },
    );
  }

  async startMoh(bridgeId: string): Promise<void> {
    await this.request(`/bridges/${encodeURIComponent(bridgeId)}/moh`, {
      method: "POST",
    });
  }

  async stopMoh(bridgeId: string): Promise<void> {
    await this.request(`/bridges/${encodeURIComponent(bridgeId)}/moh`, {
      method: "DELETE",
    });
  }

  async hangupChannel(channelId: string): Promise<void> {
    await this.request(`/channels/${encodeURIComponent(channelId)}`, {
      method: "DELETE",
    });
  }

  async originateOperatorLeg(
    context: TelephonyCommandContext,
    destinationType: "browser" | "sip" | "mobile",
    destination: string,
  ): Promise<string> {
    const channelId = `teamora-operator-${context.callId}`;
    const endpoint = operatorEndpoint(
      destinationType,
      destination,
      this.options.pjsipEndpoint,
    );
    const query = new URLSearchParams({
      endpoint,
      app: this.options.application ?? "teamora-voice",
      appArgs: `operator,${context.callId}`,
      channelId,
      timeout: "20",
    });
    try {
      await this.request(`/channels?${query.toString()}`, { method: "POST" });
    } catch (error) {
      if (!(error instanceof AsteriskAriError) || error.status !== 409)
        throw error;
      // Recovery after a Gateway restart must attach to the deterministic
      // existing operator leg instead of creating a second outgoing channel.
      await this.request(`/channels/${encodeURIComponent(channelId)}`, {
        method: "GET",
      });
    }
    this.channelContexts.set(channelId, context);
    return channelId;
  }

  async provisionWebRtcEndpoint(
    membershipId: string,
    username: string,
    password: string,
  ): Promise<void> {
    const expectedUsername = `operator-${membershipId}`;
    if (username !== expectedUsername || !/^[0-9a-f-]{36}$/i.test(membershipId))
      throw new Error("Invalid WebRTC operator identity");
    if (password.length < 32 || password.length > 160)
      throw new Error("Invalid WebRTC credential length");
    await this.updateDynamicObject("auth", username, [
      ["auth_type", "userpass"],
      ["username", username],
      ["password", password],
    ]);
    await this.updateDynamicObject("aor", username, [
      ["max_contacts", "1"],
      ["remove_existing", "yes"],
      ["support_path", "yes"],
      ["maximum_expiration", "120"],
      ["default_expiration", "60"],
    ]);
    try {
      await this.updateDynamicObject("endpoint", username, [
        ["transport", "transport-webrtc"],
        ["context", "teamora-operator"],
        ["disallow", "all"],
        ["allow", "opus,ulaw"],
        ["auth", username],
        ["aors", username],
        ["webrtc", "yes"],
        ["direct_media", "no"],
        ["force_rport", "yes"],
        ["rewrite_contact", "yes"],
        ["rtp_symmetric", "yes"],
      ]);
    } catch (error) {
      await this.deleteDynamicObject("aor", username).catch(() => undefined);
      await this.deleteDynamicObject("auth", username).catch(() => undefined);
      throw error;
    }
  }

  async revokeWebRtcEndpoint(membershipId: string): Promise<void> {
    if (!/^[0-9a-f-]{36}$/i.test(membershipId)) return;
    const username = `operator-${membershipId}`;
    await this.deleteDynamicObject("endpoint", username).catch(() => undefined);
    await this.deleteDynamicObject("aor", username).catch(() => undefined);
    await this.deleteDynamicObject("auth", username).catch(() => undefined);
  }

  async cleanupWebRtcEndpoints(): Promise<number> {
    const endpoints = await this.request("/endpoints", { method: "GET" });
    if (!Array.isArray(endpoints)) return 0;
    const operatorIds = endpoints
      .map((item) => (typeof item.resource === "string" ? item.resource : ""))
      .filter((resource) => /^operator-[0-9a-f-]{36}$/i.test(resource));
    await Promise.all(
      operatorIds.map((resource) =>
        this.revokeWebRtcEndpoint(resource.replace(/^operator-/, "")),
      ),
    );
    return operatorIds.length;
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
          ...(init.body ? { "content-type": "application/json" } : {}),
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

  private async updateDynamicObject(
    objectType: "aor" | "auth" | "endpoint",
    id: string,
    fields: Array<[string, string]>,
  ): Promise<void> {
    await this.request(
      `/asterisk/config/dynamic/res_pjsip/${objectType}/${encodeURIComponent(id)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          fields: fields.map(([attribute, value]) => ({ attribute, value })),
        }),
      },
    );
  }

  private async deleteDynamicObject(
    objectType: "aor" | "auth" | "endpoint",
    id: string,
  ): Promise<void> {
    await this.request(
      `/asterisk/config/dynamic/res_pjsip/${objectType}/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    );
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

function operatorEndpoint(
  type: "browser" | "sip" | "mobile",
  destination: string,
  trunk?: string,
): string {
  if (type === "browser") {
    const match = /^webrtc:([0-9a-f-]{36})$/i.exec(destination);
    if (!match) throw new Error("Invalid browser operator destination");
    return `PJSIP/operator-${match[1]}`;
  }
  if (type === "sip") {
    const match = /^sip:([0-9*#-]{1,32})$/.exec(destination);
    if (!match) throw new Error("Invalid SIP operator destination");
    return `PJSIP/${match[1]}`;
  }
  if (!/^\+[1-9][0-9]{7,14}$/.test(destination) || !trunk)
    throw new Error("Invalid mobile operator destination");
  return `PJSIP/${destination}@${trunk}`;
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
