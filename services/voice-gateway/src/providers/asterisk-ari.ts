import type { TelephonyProvider } from "@teamora/contracts";

type AsteriskOptions = {
  baseUrl: string;
  username: string;
  password: string;
  externalHost: string;
  application?: string;
  transport?: "websocket" | "udp";
};

export class AsteriskAriProvider implements TelephonyProvider {
  readonly name = "asterisk-ari";
  constructor(private readonly options: AsteriskOptions) {}
  async answer(channelId: string): Promise<void> {
    await this.request(`/channels/${encodeURIComponent(channelId)}/answer`, {
      method: "POST",
    });
  }
  async createExternalMedia(
    callId: string,
    _channelId: string,
  ): Promise<{ mediaId: string }> {
    const mediaId = `teamora-${callId}`;
    const transport = this.options.transport ?? "websocket";
    const query = new URLSearchParams({
      app: this.options.application ?? "teamora-voice",
      channelId: mediaId,
      external_host: this.options.externalHost,
      format: "slin16",
      transport,
      encapsulation: transport === "udp" ? "rtp" : "none",
      connection_type: "client",
      direction: "both",
    });
    await this.request(`/channels/externalMedia?${query.toString()}`, {
      method: "POST",
    });
    return { mediaId };
  }
  async transfer(
    channelId: string,
    queue: string,
    _reason: string,
  ): Promise<void> {
    await this.request(
      `/channels/${encodeURIComponent(channelId)}/continue?context=teamora-transfer&extension=${encodeURIComponent(queue)}&priority=1`,
      { method: "POST" },
    );
  }
  async hangup(channelId: string, reason: string): Promise<void> {
    await this.request(
      `/channels/${encodeURIComponent(channelId)}?reason=${encodeURIComponent(reason)}`,
      { method: "DELETE" },
    );
  }
  async pauseRecording(channelId: string): Promise<void> {
    await this.request(
      `/recordings/live/${encodeURIComponent(channelId)}/pause`,
      { method: "POST" },
    );
  }
  async resumeRecording(channelId: string): Promise<void> {
    await this.request(
      `/recordings/live/${encodeURIComponent(channelId)}/unpause`,
      { method: "POST" },
    );
  }
  private async request(path: string, init: RequestInit): Promise<void> {
    const response = await fetch(
      `${this.options.baseUrl.replace(/\/$/, "")}/ari${path}`,
      {
        ...init,
        headers: {
          Authorization: `Basic ${Buffer.from(`${this.options.username}:${this.options.password}`).toString("base64")}`,
        },
        signal: AbortSignal.timeout(5000),
      },
    );
    if (!response.ok)
      throw new Error(
        `Asterisk ARI request failed with status ${response.status}`,
      );
  }
}
