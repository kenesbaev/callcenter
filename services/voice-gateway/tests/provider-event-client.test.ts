import { createHmac } from "node:crypto";
import { describe, expect, it, vi } from "vitest";
import { ProviderEventClient } from "../src/provider-event-client.js";

describe("ProviderEventClient", () => {
  it("signs inbound routing without credentials in URL or payload", async () => {
    const secret = "local-provider-event-secret-for-tests";
    const fetchImplementation = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        const raw = String(init?.body ?? "");
        const headers = new Headers(init?.headers);
        const webhookId = headers.get("x-teamora-id") ?? "";
        const timestamp = headers.get("x-teamora-timestamp") ?? "";
        const expected = createHmac("sha256", secret)
          .update(`${webhookId}.${timestamp}.`)
          .update(raw)
          .digest("base64");
        expect(headers.get("x-teamora-signature")).toBe(`v1,${expected}`);
        expect(url).not.toContain(secret);
        expect(raw).not.toContain(secret);
        return new Response(
          JSON.stringify({
            callId: "10000000-0000-4000-8000-000000000003",
            tenantId: "10000000-0000-4000-8000-000000000001",
            projectId: "10000000-0000-4000-8000-000000000002",
            stateVersion: 1,
            recordingAllowed: false,
            disclosureRequired: true,
            duplicate: false,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      },
    ) as typeof fetch;
    const client = new ProviderEventClient(
      "http://api.internal",
      secret,
      1_000,
      fetchImplementation,
    );
    const result = await client.acceptInbound({
      providerEventId: "ari-event-1",
      channelId: "channel-1",
      did: "+998711111111",
      callerNumber: "+998901234567",
      sourceIp: "10.0.0.12",
      occurredAt: "2026-08-05T09:00:00.000Z",
      correlationId: "correlation-1",
      safePayload: { codec: "ulaw" },
    });
    expect(result.tenantId).toBe("10000000-0000-4000-8000-000000000001");
  });

  it("derives stable event IDs independent of object key order", () => {
    const client = new ProviderEventClient(
      "http://api.internal",
      "local-provider-event-secret-for-tests",
      1_000,
    );
    expect(client.eventId({ type: "StasisStart", timestamp: "1" })).toBe(
      client.eventId({ timestamp: "1", type: "StasisStart" }),
    );
  });

  it("retries bounded transient backend failures without changing the command", async () => {
    const requests: string[] = [];
    const fetchImplementation = vi.fn(async (_input, init) => {
      requests.push(String(init?.body ?? ""));
      if (requests.length < 3)
        return new Response("temporarily unavailable", { status: 503 });
      return new Response(JSON.stringify({ accepted: true }), {
        status: 202,
        headers: { "content-type": "application/json" },
      });
    }) as typeof fetch;
    const client = new ProviderEventClient(
      "http://api.internal",
      "local-provider-event-secret-for-tests",
      1_000,
      fetchImplementation,
    );

    await client.publishAiEvent({
      eventType: "ai.listening",
      providerEventId: "provider-event-1",
    });

    expect(fetchImplementation).toHaveBeenCalledTimes(3);
    expect(new Set(requests)).toHaveLength(1);
  });
});
