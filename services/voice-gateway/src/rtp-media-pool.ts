import { RtpDiagnosticAdapter } from "./rtp-diagnostic.js";

type ReservedMedia = { port: number; adapter: RtpDiagnosticAdapter };

export class RtpMediaPool {
  private readonly reservations = new Map<string, ReservedMedia>();

  constructor(
    private readonly bindHost: string,
    private readonly advertisedHost: string,
    private readonly portStart: number,
    private readonly portEnd: number,
  ) {
    if (portStart > portEnd) throw new Error("Invalid RTP media port range");
  }

  async reserve(callId: string): Promise<ReservedMedia> {
    const existing = this.reservations.get(callId);
    if (existing) return existing;
    const occupied = new Set(
      [...this.reservations.values()].map((item) => item.port),
    );
    for (let port = this.portStart; port <= this.portEnd; port += 1) {
      if (occupied.has(port)) continue;
      const adapter = new RtpDiagnosticAdapter(this.bindHost, port);
      try {
        await adapter.start();
        const reserved = { port, adapter };
        this.reservations.set(callId, reserved);
        return reserved;
      } catch (error) {
        if (!isAddressInUse(error)) throw error;
      }
    }
    throw new Error("RTP media port pool exhausted");
  }

  externalHost(callId: string): string {
    const reserved = this.reservations.get(callId);
    if (!reserved) throw new Error("RTP media is not reserved for Call");
    return `${this.advertisedHost}:${reserved.port}`;
  }

  get(callId: string): RtpDiagnosticAdapter | undefined {
    return this.reservations.get(callId)?.adapter;
  }

  async release(callId: string): Promise<void> {
    const reserved = this.reservations.get(callId);
    this.reservations.delete(callId);
    await reserved?.adapter.stop();
  }

  async shutdown(): Promise<void> {
    const active = [...this.reservations.values()];
    this.reservations.clear();
    await Promise.all(active.map((item) => item.adapter.stop()));
  }

  get activeCount(): number {
    return this.reservations.size;
  }
}

function isAddressInUse(error: unknown): boolean {
  return (
    error instanceof Error &&
    "code" in error &&
    (error as Error & { code?: string }).code === "EADDRINUSE"
  );
}
