import dgram, { type RemoteInfo, type Socket } from "node:dgram";
import { randomBytes } from "node:crypto";
import { logger } from "./logger.js";

export type RtpStatistics = {
  packetsReceived: number;
  packetsSent: number;
  bytesReceived: number;
  bytesSent: number;
  invalidPackets: number;
  lastPacketAt?: string;
  remoteAddress?: string;
  remotePort?: number;
  payloadType?: number;
};

export class RtpDiagnosticAdapter {
  private socket: Socket | undefined;
  private statistics: RtpStatistics = {
    packetsReceived: 0,
    packetsSent: 0,
    bytesReceived: 0,
    bytesSent: 0,
    invalidPackets: 0,
  };

  constructor(
    private readonly host: string,
    readonly port: number,
  ) {}

  async start(): Promise<void> {
    if (this.socket) return;
    const socket = dgram.createSocket("udp4");
    socket.on("message", (message, remote) => this.onPacket(message, remote));
    socket.on("error", (error) =>
      logger.warn(
        { code: error.name, port: this.port },
        "RTP diagnostic socket error",
      ),
    );
    await new Promise<void>((resolve, reject) => {
      socket.once("error", reject);
      socket.bind(this.port, this.host, () => {
        socket.off("error", reject);
        resolve();
      });
    });
    this.socket = socket;
    logger.info({ port: this.port }, "RTP diagnostic adapter listening");
  }

  async stop(): Promise<void> {
    const socket = this.socket;
    this.socket = undefined;
    if (!socket) return;
    await new Promise<void>((resolve) => socket.close(() => resolve()));
  }

  snapshot(): RtpStatistics {
    return { ...this.statistics };
  }

  async runLocalTest(
    codec: string,
    packetCount: number,
  ): Promise<Record<string, number | boolean>> {
    if (!this.socket) throw new Error("RTP diagnostic adapter is not started");
    const payloadType = codec === "alaw" ? 8 : codec === "ulaw" ? 0 : -1;
    if (payloadType < 0) throw new Error("Unsupported diagnostic RTP codec");
    const started = Date.now();
    const client = dgram.createSocket("udp4");
    let received = 0;
    client.on("message", () => {
      received += 1;
    });
    await new Promise<void>((resolve) =>
      client.bind(0, "127.0.0.1", () => resolve()),
    );
    try {
      for (let index = 0; index < packetCount; index += 1) {
        const packet = testPacket(payloadType, index);
        await new Promise<void>((resolve, reject) => {
          client.send(packet, this.port, "127.0.0.1", (error) =>
            error ? reject(error) : resolve(),
          );
        });
      }
      const deadline = Date.now() + 2_000;
      while (received < packetCount && Date.now() < deadline) {
        await new Promise((resolve) => setTimeout(resolve, 10));
      }
      return {
        packetsReceived: received,
        packetsSent: packetCount,
        bytesReceived: received * 172,
        bytesSent: packetCount * 172,
        durationMs: Date.now() - started,
        inboundAudioVerified: this.statistics.packetsReceived >= packetCount,
        outboundAudioVerified: received === packetCount,
      };
    } finally {
      client.close();
    }
  }

  private onPacket(message: Buffer, remote: RemoteInfo): void {
    if (message.length < 12 || message[0]! >> 6 !== 2) {
      this.statistics.invalidPackets += 1;
      return;
    }
    const payloadType = message[1]! & 0x7f;
    if (payloadType !== 0 && payloadType !== 8) {
      this.statistics.invalidPackets += 1;
      return;
    }
    this.statistics.packetsReceived += 1;
    this.statistics.bytesReceived += message.length;
    this.statistics.lastPacketAt = new Date().toISOString();
    this.statistics.remoteAddress = remote.address;
    this.statistics.remotePort = remote.port;
    this.statistics.payloadType = payloadType;
    this.socket?.send(message, remote.port, remote.address, (error) => {
      if (error) return;
      this.statistics.packetsSent += 1;
      this.statistics.bytesSent += message.length;
    });
  }
}

function testPacket(payloadType: number, sequence: number): Buffer {
  const packet = Buffer.alloc(172);
  packet[0] = 0x80;
  packet[1] = payloadType;
  packet.writeUInt16BE(sequence & 0xffff, 2);
  packet.writeUInt32BE(sequence * 160, 4);
  randomBytes(4).copy(packet, 8);
  // Alternating companded samples are deterministic enough to prove media
  // transport without claiming speech quality or provider codec negotiation.
  packet.fill(sequence % 2 === 0 ? 0xff : 0x7f, 12);
  return packet;
}
