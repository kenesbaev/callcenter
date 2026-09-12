import { createHash, createHmac, randomUUID } from "node:crypto";
import type { TelephonyProviderEvent } from "@teamora/contracts";

export type InboundAcceptance = {
  callId: string;
  tenantId: string;
  projectId: string;
  stateVersion: number;
  recordingAllowed: boolean;
  disclosureRequired: boolean;
  aiSessionAvailable: boolean;
  duplicate: boolean;
};

export type AiSessionConfiguration = {
  sessionId: string;
  callId: string;
  tenantId: string;
  projectId: string;
  stateVersion: number;
  provider: "mock" | "openai";
  model: string;
  voice: string;
  language: string;
  instructions: string;
  safetyIdentifier?: string;
  tools: Array<{
    name: string;
    description: string;
    input_schema: Record<string, unknown>;
    timeout_ms: number;
  }>;
  recordingAllowed: boolean;
  disclosureRequired: boolean;
  disclosureText?: string;
  vad: Record<string, unknown>;
  reasoningEffort?: "low" | "medium" | "high";
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

  async configureAiSession(context: {
    tenantId: string;
    projectId: string;
    callId: string;
    correlationId: string;
    requestedLanguage?: string;
  }): Promise<AiSessionConfiguration> {
    return this.post<AiSessionConfiguration>(
      "/api/v1/webhooks/ai-realtime/session",
      context,
    );
  }

  async publishAiEvent(event: Record<string, unknown>): Promise<void> {
    await this.post("/api/v1/webhooks/ai-realtime/events", event);
  }

  async executeAiTool(request: Record<string, unknown>): Promise<unknown> {
    return this.post<unknown>("/api/v1/webhooks/ai-realtime/tools", request);
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
    let lastStatus = 0;
    for (let attempt = 0; attempt < 3; attempt += 1) {
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
      if (response.ok) return (await response.json()) as T;
      lastStatus = response.status;
      if (![429, 500, 502, 503, 504].includes(response.status) || attempt === 2)
        break;
      const retryAfterSeconds = Number(
        response.headers.get("retry-after") ?? 0,
      );
      const delayMs = Math.min(
        1_000,
        retryAfterSeconds > 0 ? retryAfterSeconds * 1_000 : 100 * 2 ** attempt,
      );
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
    throw new Error(`Provider event backend rejected status ${lastStatus}`);
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
