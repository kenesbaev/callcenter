import { describe, expect, it, vi } from "vitest";
import type { TelephonyCommandContext } from "@teamora/contracts";
import { AsteriskAriProvider } from "../src/providers/asterisk-ari.js";

const context: TelephonyCommandContext = {
  tenantId: "10000000-0000-4000-8000-000000000001",
  projectId: "10000000-0000-4000-8000-000000000002",
  callId: "10000000-0000-4000-8000-000000000003",
  providerCallId: "ari-channel-1",
  commandId: "10000000-0000-4000-8000-000000000004",
  idempotencyKey: "telephony-test-key",
  timestamp: "2026-07-30T10:00:00.000Z",
  correlationId: "correlation-1",
};

describe("AsteriskAriProvider", () => {
  it("maps the full telephony contract to bounded ARI requests", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchImplementation = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input);
        calls.push(init ? { url, init } : { url });
        if (init?.method === "GET")
          return new Response(JSON.stringify({ state: "Up" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        return new Response(null, { status: 204 });
      },
    ) as typeof fetch;
    const provider = new AsteriskAriProvider({
      baseUrl: "http://ari.local:8088",
      username: "ari-user",
      password: "ari-secret",
      externalHost: "gateway.internal:60000",
      fetchImplementation,
    });

    await provider.originate(context, {
      fromNumber: "+998711111111",
      toNumber: "+998901234567",
    });
    await provider.answer(context);
    await provider.hold(context);
    await provider.resume(context);
    await provider.transfer(context, "support", "requested");
    await provider.getCallState(context);
    await provider.startRecording(context);
    await provider.pauseRecording(context);
    await provider.resumeRecording(context);
    await provider.stopRecording(context);
    await provider.createExternalMedia(context);
    await provider.hangup(context, "normal");

    expect(calls.map((entry) => entry.url)).toEqual(
      expect.arrayContaining([
        expect.stringContaining(
          "/ari/channels?endpoint=PJSIP%2F%2B998901234567",
        ),
        expect.stringContaining("/ari/channels/ari-channel-1/answer"),
        expect.stringContaining("/ari/channels/ari-channel-1/hold"),
        expect.stringContaining("/ari/channels/ari-channel-1/continue"),
        expect.stringContaining("/ari/channels/ari-channel-1/record"),
        expect.stringContaining(
          "/ari/recordings/live/teamora-10000000-0000-4000-8000-000000000003",
        ),
        expect.stringContaining("/ari/channels/externalMedia"),
      ]),
    );
    expect(JSON.stringify(calls)).not.toContain("ari-secret");
    expect(fetchImplementation).toHaveBeenCalledTimes(12);
  });
});
