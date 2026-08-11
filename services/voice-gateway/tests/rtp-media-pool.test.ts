import dgram from "node:dgram";
import { describe, expect, it } from "vitest";
import { RtpMediaPool } from "../src/rtp-media-pool.js";

async function freePort(): Promise<number> {
  const socket = dgram.createSocket("udp4");
  await new Promise<void>((resolve) => socket.bind(0, "127.0.0.1", resolve));
  const address = socket.address();
  const port = typeof address === "string" ? 0 : address.port;
  await new Promise<void>((resolve) => socket.close(() => resolve()));
  return port;
}

describe("RTP media pool", () => {
  it("reserves isolated adapters for concurrent Calls and releases only the requested Call", async () => {
    const start = await freePort();
    const pool = new RtpMediaPool(
      "127.0.0.1",
      "voice-gateway",
      start,
      start + 10,
    );
    try {
      const first = await pool.reserve("call-1");
      const second = await pool.reserve("call-2");
      expect(first.port).not.toBe(second.port);
      expect(pool.externalHost("call-1")).toBe(`voice-gateway:${first.port}`);
      expect(pool.activeCount).toBe(2);
      await pool.release("call-1");
      expect(pool.activeCount).toBe(1);
      expect(pool.get("call-2")).toBe(second.adapter);
    } finally {
      await pool.shutdown();
    }
  });
});
