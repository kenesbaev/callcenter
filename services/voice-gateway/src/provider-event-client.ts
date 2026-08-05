import { createHash, createHmac, randomUUID } from "node:crypto";
import type { TelephonyProviderEvent } from "@teamora/contracts";

export type InboundAcceptance = {
  callId: string;
  tenantId: string;
  projectId: string;
  stateVersion: number;
  recordingAllowed: boolean;
  disclosureRequired: boolean;
  duplicate: boolean;
};

export class ProviderEventClient {
  constructor(
    private readonly apiBaseUrl: string,
    private readonly serviceToken: string,
    private readonly timeoutMs: number,
    private readonly fetchImplementation: typeof fetch = fetch,
  ) {}

  async acceptInbound(input: {
    providerEventId: string;
    channelId: string;
    did: string;
    callerNumber?: string;
    sourceIp: string;
    occurredAt: string;
    correlationId: string;
    safePayload: Record<string, boolean | number | string>;
  }): Promise<InboundAcceptance> {
    return this.post<InboundAcceptance>("/api/v1/webhooks/telephony/inbound", {
      version: "1",
      provider: "asterisk-ari",
      ...input,
    });
  }

  async publish(event: TelephonyProviderEvent): Promise<void> {
    await this.post("/api/v1/webhooks/telephony", event);
  }

  eventId(event: Record<string, unknown>): string {
    return `ari:${createHash("sha256").update(stableJson(event)).digest("hex")}`;
  }

  private async post<T = Record<string, unknown>>(
    path: string,
    body: object,
  ): Promise<T> {
    const raw = JSON.stringify(body);
    const webhookId = randomUUID();
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const signature = createHmac("sha256", decodeSecret(this.serviceToken))
      .update(`${webhookId}.${timestamp}.`)
      .update(raw)
      .digest("base64");
    const response = await this.fetchImplementation(
      `${this.apiBaseUrl.replace(/\/$/, "")}${path}`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-teamora-id": webhookId,
          "x-teamora-timestamp": timestamp,
          "x-teamora-signature": `v1,${signature}`,
        },
        body: raw,
        signal: AbortSignal.timeout(this.timeoutMs),
      },
    );
    if (!response.ok)
      throw new Error(
        `Provider event backend rejected status ${response.status}`,
      );
    return (await response.json()) as T;
  }
}

function decodeSecret(value: string): Buffer {
  const candidate = value.replace(/^whsec_/, "");
  if (/^[A-Za-z0-9+/]+={0,2}$/.test(candidate) && candidate.length % 4 === 0) {
    const decoded = Buffer.from(candidate, "base64");
    if (
      decoded.toString("base64").replace(/=+$/, "") ===
      candidate.replace(/=+$/, "")
    ) {
      return decoded;
    }
  }
  return Buffer.from(candidate, "utf8");
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value as Record<string, unknown>)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${JSON.stringify(key)}:${stableJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}
