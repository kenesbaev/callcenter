import dgram, { type RemoteInfo, type Socket } from "node:dgram";
import { randomBytes } from "node:crypto";
import { logger } from "./logger.js";
import {
  buildPcmuRtpPacket,
  pcm16ToBytes,
  pcm24kToPcmuPayload,
  pcmuRtpToPcm24k,
} from "./audio-codec.js";

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
  private audioHandler: ((audio: Uint8Array) => Promise<void>) | undefined;
  private processing = Promise.resolve();
  private remote?: { address: string; port: number };
  private outputSequence = 0;
  private outputTimestamp = 0;
  private readonly outputSsrc = randomBytes(4).readUInt32BE();
  private playbackGeneration = 0;

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
      const rejectAndClose = (error: Error) => {
        socket.close();
        reject(error);
      };
      socket.once("error", rejectAndClose);
      socket.bind(this.port, this.host, () => {
        socket.off("error", rejectAndClose);
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
    await this.processing;
  }

  attachAudioHandler(handler: (audio: Uint8Array) => Promise<void>): void {
    if (this.audioHandler)
      throw new Error("RTP audio handler is already attached");
    this.audioHandler = handler;
  }

  detachAudioHandler(): void {
    this.audioHandler = undefined;
    this.playbackGeneration += 1;
  }

  async writePcm24k(audio: Uint8Array): Promise<void> {
    const remote = this.remote;
    if (!this.socket || !remote)
      throw new Error("RTP remote endpoint is unavailable");
    const generation = this.playbackGeneration;
    const payload = pcm24kToPcmuPayload(audio);
    for (let offset = 0; offset < payload.byteLength; offset += 160) {
      if (generation !== this.playbackGeneration) return;
      const frame = payload.subarray(
        offset,
        Math.min(offset + 160, payload.byteLength),
      );
      if (frame.byteLength < 160) return;
      const packet = buildPcmuRtpPacket(
        frame,
        this.outputSequence,
        this.outputTimestamp,
        this.outputSsrc,
      );
      this.outputSequence = (this.outputSequence + 1) & 0xffff;
      this.outputTimestamp = (this.outputTimestamp + 160) >>> 0;
      await new Promise<void>((resolve, reject) => {
        this.socket!.send(packet, remote.port, remote.address, (error) =>
          error ? reject(error) : resolve(),
        );
      });
      this.statistics.packetsSent += 1;
      this.statistics.bytesSent += packet.byteLength;
      // PCMU uses 160 samples per 20 ms packet.  Pace playback here so the
      // queue represents real playout time and can be cleared deterministically
      // when VAD reports barge-in.
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }

  async clearPlayback(): Promise<void> {
    this.playbackGeneration += 1;
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
    this.remote = { address: remote.address, port: remote.port };
    if (this.audioHandler) {
      this.processing = this.processing
        .then(async () =>
          this.audioHandler?.(pcm16ToBytes(pcmuRtpToPcm24k(message))),
        )
        .catch((error: unknown) =>
          logger.warn(
            {
              code:
                error instanceof Error ? error.message : "audio_pipeline_error",
            },
            "RTP audio pipeline rejected a frame",
          ),
        );
      return;
    }
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
