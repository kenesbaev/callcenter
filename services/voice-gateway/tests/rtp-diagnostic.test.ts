import dgram from "node:dgram";
import { afterEach, describe, expect, it } from "vitest";
import { RtpDiagnosticAdapter } from "../src/rtp-diagnostic.js";

const adapters: RtpDiagnosticAdapter[] = [];

afterEach(async () => {
  await Promise.all(adapters.splice(0).map((adapter) => adapter.stop()));
});

async function availableUdpPort(): Promise<number> {
  const socket = dgram.createSocket("udp4");
  await new Promise<void>((resolve) => socket.bind(0, "127.0.0.1", resolve));
  const address = socket.address();
  const port = typeof address === "string" ? 0 : address.port;
  await new Promise<void>((resolve) => socket.close(() => resolve()));
  return port;
}

describe("RtpDiagnosticAdapter", () => {
  it("proves deterministic RTP receive and send for PCMU and PCMA", async () => {
    for (const codec of ["ulaw", "alaw"]) {
      const adapter = new RtpDiagnosticAdapter(
        "127.0.0.1",
        await availableUdpPort(),
      );
      adapters.push(adapter);
      await adapter.start();
      const result = await adapter.runLocalTest(codec, 20);
      expect(result.inboundAudioVerified).toBe(true);
      expect(result.outboundAudioVerified).toBe(true);
      expect(result.packetsReceived).toBe(20);
      expect(adapter.snapshot().invalidPackets).toBe(0);
      await adapter.stop();
      adapters.splice(adapters.indexOf(adapter), 1);
    }
  });

  it("rejects unsupported codecs instead of claiming media verification", async () => {
    const adapter = new RtpDiagnosticAdapter(
      "127.0.0.1",
      await availableUdpPort(),
    );
    adapters.push(adapter);
    await adapter.start();
    await expect(adapter.runLocalTest("g722", 10)).rejects.toThrow(
      "Unsupported diagnostic RTP codec",
    );
  });
});
