export type RealtimeStatus = "connected" | "reconnecting" | "offline";

export type RealtimeEventEnvelope = {
  schema_version: number;
  event_id: string;
  cursor: number;
  event_type: string;
  occurred_at: string;
  project_id: string | null;
  aggregate_type: string;
  aggregate_id: string;
  aggregate_version: number | null;
  payload: Record<string, unknown>;
};

type ServerMessage =
  | { type: "subscribed"; cursor: number }
  | { type: "event"; event: RealtimeEventEnvelope }
  | { type: "resync_required"; cursor: number }
  | { type: "pong"; cursor: number }
  | { type: "cursor"; cursor: number }
  | { type: "server_shutdown"; retry_after_ms: number };

export type RealtimeClientOptions = {
  storageKey: string;
  projectIds?: string[];
  onEvent: (event: RealtimeEventEnvelope) => void;
  onResync: () => void;
  onStatus: (status: RealtimeStatus) => void;
  websocketFactory?: (url: string) => WebSocket;
};

const MAX_DEDUPLICATION_IDS = 2_000;

export class RealtimeClient {
  private socket: WebSocket | null = null;
  private stopped = true;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private readonly seen = new Set<string>();
  private readonly seenOrder: string[] = [];
  private readonly aggregateVersions = new Map<string, number>();
  private currentCursor: number | null = null;
  private channel: BroadcastChannel | null = null;
  private readonly handleOffline = () => {
    if (this.stopped) return;
    this.options.onStatus("offline");
    if (this.socket && this.socket.readyState < WebSocket.CLOSING) {
      this.socket.close(4000, "network offline");
    }
  };
  private readonly handleOnline = () => {
    if (
      this.stopped ||
      this.socket?.readyState === WebSocket.OPEN ||
      this.socket?.readyState === WebSocket.CONNECTING
    )
      return;
    this.reconnectAttempt = Math.max(1, this.reconnectAttempt);
    this.connect();
  };

  constructor(private readonly options: RealtimeClientOptions) {}

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    if (typeof BroadcastChannel !== "undefined") {
      this.channel = new BroadcastChannel(`${this.options.storageKey}:control`);
      this.channel.addEventListener("message", (event) => {
        if (event.data === "logout") this.stop();
      });
    }
    window.addEventListener("offline", this.handleOffline);
    window.addEventListener("online", this.handleOnline);
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.reconnectTimer = null;
    this.pingTimer = null;
    const socket = this.socket;
    this.socket = null;
    this.channel?.close();
    this.channel = null;
    window.removeEventListener("offline", this.handleOffline);
    window.removeEventListener("online", this.handleOnline);
    if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close(1000, "client stopped");
    }
    this.options.onStatus("offline");
  }

  updateProjects(projectIds: string[]): void {
    this.options.projectIds = projectIds;
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(
        JSON.stringify({
          type: "subscribe",
          cursor: this.cursor(),
          project_ids: projectIds,
        }),
      );
    }
  }

  private connect(): void {
    if (this.stopped || typeof window === "undefined") return;
    this.options.onStatus(this.reconnectAttempt ? "reconnecting" : "offline");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url =
      process.env.NEXT_PUBLIC_REALTIME_URL ??
      `${protocol}//${window.location.host}/api/v1/realtime/ws`;
    const createSocket =
      this.options.websocketFactory ??
      ((value: string) => new WebSocket(value));
    const socket = createSocket(url);
    const isReconnect = this.reconnectAttempt > 0;
    this.socket = socket;
    socket.addEventListener("open", () => {
      this.reconnectAttempt = 0;
      socket.send(
        JSON.stringify({
          type: "subscribe",
          cursor: this.cursor(),
          project_ids: this.options.projectIds ?? [],
        }),
      );
      this.options.onStatus("connected");
      if (isReconnect) this.options.onResync();
      this.pingTimer = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "ping" }));
        }
      }, 25_000);
    });
    socket.addEventListener("message", (message) =>
      this.handleMessage(message.data),
    );
    socket.addEventListener("close", () => this.scheduleReconnect());
    socket.addEventListener("error", () => socket.close());
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== "string") return;
    let message: ServerMessage;
    try {
      message = JSON.parse(raw) as ServerMessage;
    } catch {
      return;
    }
    if (message.type === "resync_required") {
      this.setCursor(0);
      this.options.onResync();
      return;
    }
    if (message.type === "cursor") {
      if (
        Number.isSafeInteger(message.cursor) &&
        message.cursor > this.cursor()
      ) {
        this.setCursor(message.cursor);
      }
      return;
    }
    if (message.type !== "event") return;
    const event = message.event;
    if (
      event.schema_version !== 1 ||
      !event.event_id ||
      !Number.isSafeInteger(event.cursor)
    )
      return;
    if (this.seen.has(event.event_id)) return;
    if (event.cursor <= this.cursor()) return;
    this.remember(event.event_id);
    this.setCursor(event.cursor);
    if (event.aggregate_version !== null) {
      const aggregateKey = `${event.aggregate_type}:${event.aggregate_id}`;
      const currentVersion = this.aggregateVersions.get(aggregateKey);
      if (
        currentVersion !== undefined &&
        event.aggregate_version <= currentVersion
      )
        return;
      this.aggregateVersions.set(aggregateKey, event.aggregate_version);
    }
    this.options.onEvent(event);
  }

  private scheduleReconnect(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = null;
    if (this.stopped) return;
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      this.options.onStatus("offline");
      return;
    }
    this.options.onStatus("reconnecting");
    const base = Math.min(30_000, 500 * 2 ** this.reconnectAttempt++);
    const jitter = Math.floor(Math.random() * Math.max(1, base * 0.25));
    this.reconnectTimer = setTimeout(() => this.connect(), base + jitter);
  }

  private remember(eventId: string): void {
    this.seen.add(eventId);
    this.seenOrder.push(eventId);
    if (this.seenOrder.length > MAX_DEDUPLICATION_IDS) {
      const oldest = this.seenOrder.shift();
      if (oldest) this.seen.delete(oldest);
    }
  }

  private cursor(): number {
    if (this.currentCursor !== null) return this.currentCursor;
    const stored = localStorage.getItem(this.options.storageKey);
    const parsed = Number(stored ?? 0);
    this.currentCursor =
      Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : 0;
    return this.currentCursor;
  }

  private setCursor(cursor: number): void {
    this.currentCursor = cursor;
    localStorage.setItem(this.options.storageKey, String(cursor));
  }
}

export function broadcastRealtimeLogout(storageKey: string): void {
  if (typeof BroadcastChannel === "undefined") return;
  const channel = new BroadcastChannel(`${storageKey}:control`);
  channel.postMessage("logout");
  channel.close();
}
