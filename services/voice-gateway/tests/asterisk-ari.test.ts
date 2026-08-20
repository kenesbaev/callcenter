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
      application: "teamora-voice",
      pjsipEndpoint: "provider-endpoint",
      mediaFormat: "ulaw",
      transport: "udp",
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
          "/ari/channels?endpoint=PJSIP%2F%2B998901234567%40provider-endpoint",
        ),
        expect.stringContaining("/ari/channels/ari-channel-1/answer"),
        expect.stringContaining("/ari/channels/ari-channel-1/hold"),
        expect.stringContaining("/ari/channels/ari-channel-1/continue"),
        expect.stringContaining(
          "/ari/bridges/teamora-bridge-10000000-0000-4000-8000-000000000003/record",
        ),
        expect.stringContaining(
          "/ari/recordings/live/teamora-10000000-0000-4000-8000-000000000003",
        ),
        expect.stringContaining("/ari/channels/externalMedia"),
      ]),
    );
    expect(JSON.stringify(calls)).not.toContain("ari-secret");
    expect(fetchImplementation).toHaveBeenCalledTimes(12);
    const externalMedia = calls.find((entry) =>
      entry.url.includes("/ari/channels/externalMedia"),
    );
    expect(externalMedia?.url).toContain("transport=udp");
    expect(externalMedia?.url).toContain("encapsulation=rtp");
    expect(externalMedia?.url).toContain("direction=both");
  });

  it("creates idempotent bridge and channel resource requests", async () => {
    const fetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(null, { status: 204 }),
    );
    const fetchImplementation = fetchMock as typeof fetch;
    const provider = new AsteriskAriProvider({
      baseUrl: "http://ari.local:8088",
      username: "ari-user",
      password: "ari-secret",
      externalHost: "gateway.internal:60000",
      fetchImplementation,
    });
    await provider.createBridge("teamora-bridge-call-id");
    await provider.addChannelToBridge(
      "teamora-bridge-call-id",
      "customer-channel-id",
    );
    await provider.destroyBridge("teamora-bridge-call-id");
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringContaining("/ari/bridges?type=mixing%2Cproxy_media"),
      expect.stringContaining(
        "/ari/bridges/teamora-bridge-call-id/addChannel?channel=customer-channel-id",
      ),
      expect.stringContaining("/ari/bridges/teamora-bridge-call-id"),
    ]);
  });

  it("creates an allowlisted operator leg and never treats ARI acceptance as completed", async () => {
    const fetchMock = vi.fn(
      async (_input: string | URL | Request, _init?: RequestInit) =>
        new Response(null, { status: 204 }),
    );
    const provider = new AsteriskAriProvider({
      baseUrl: "http://ari.local:8088",
      username: "ari-user",
      password: "ari-secret",
      externalHost: "gateway.internal:60000",
      pjsipEndpoint: "provider-endpoint",
      fetchImplementation: fetchMock as typeof fetch,
    });
    const channel = await provider.originateOperatorLeg(
      context,
      "browser",
      "webrtc:10000000-0000-4000-8000-000000000099",
    );
    expect(channel).toBe(`teamora-operator-${context.callId}`);
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      "endpoint=PJSIP%2Foperator-10000000-0000-4000-8000-000000000099",
    );
    await expect(
      provider.originateOperatorLeg(context, "mobile", "+99912345678"),
    ).resolves.toBe(channel);
    await expect(
      provider.originateOperatorLeg(context, "mobile", "premium-number"),
    ).rejects.toThrow("Invalid mobile operator destination");
    expect(JSON.stringify(fetchMock.mock.calls)).not.toContain("ari-secret");
  });

  it("provisions and revokes an ephemeral browser endpoint through ARI push config", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    const fetchImplementation = vi.fn(
      async (input: string | URL | Request, init?: RequestInit) => {
        calls.push(
          init ? { url: String(input), init } : { url: String(input) },
        );
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
    const membershipId = "10000000-0000-4000-8000-000000000099";
    const username = `operator-${membershipId}`;
    const password = "ephemeral-operator-password-with-safe-length";
    await provider.provisionWebRtcEndpoint(membershipId, username, password);
    await provider.revokeWebRtcEndpoint(membershipId);

    expect(calls.map(({ url }) => url)).toEqual([
      expect.stringContaining(
        `/asterisk/config/dynamic/res_pjsip/auth/${username}`,
      ),
      expect.stringContaining(
        `/asterisk/config/dynamic/res_pjsip/aor/${username}`,
      ),
      expect.stringContaining(
        `/asterisk/config/dynamic/res_pjsip/endpoint/${username}`,
      ),
      expect.stringContaining(
        `/asterisk/config/dynamic/res_pjsip/endpoint/${username}`,
      ),
      expect.stringContaining(
        `/asterisk/config/dynamic/res_pjsip/aor/${username}`,
      ),
      expect.stringContaining(
        `/asterisk/config/dynamic/res_pjsip/auth/${username}`,
      ),
    ]);
    expect(calls.slice(0, 3).every(({ init }) => init?.method === "PUT")).toBe(
      true,
    );
    expect(calls.slice(3).every(({ init }) => init?.method === "DELETE")).toBe(
      true,
    );
    expect(calls[2]?.init?.body).toContain('"attribute":"webrtc"');
    expect(JSON.stringify(calls.map(({ url }) => url))).not.toContain(password);
  });
});
