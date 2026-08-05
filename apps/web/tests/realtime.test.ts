import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  RealtimeClient,
  type RealtimeEventEnvelope,
  type RealtimeStatus,
} from "@/lib/realtime";
import { realtimeQueryKeys } from "@/components/realtime-provider";

type Listener = (event: { data?: string }) => void;

class MockSocket {
  readyState: number = WebSocket.CONNECTING;
  sent: string[] = [];
  private listeners = new Map<string, Listener[]>();

  addEventListener(type: string, listener: Listener): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  send(payload: string): void {
    this.sent.push(payload);
  }

  close(): void {
    this.readyState = WebSocket.CLOSED;
    this.emit("close", {});
  }

  open(): void {
    this.readyState = WebSocket.OPEN;
    this.emit("open", {});
  }

  message(payload: object): void {
    this.emit("message", { data: JSON.stringify(payload) });
  }

  private emit(type: string, event: { data?: string }): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

function envelope(
  cursor: number,
  eventId = `event-${cursor}`,
): RealtimeEventEnvelope {
  return {
    schema_version: 1,
    event_id: eventId,
    cursor,
    event_type: "call.state_changed",
    occurred_at: "2026-08-03T08:00:00Z",
    project_id: "project-1",
    aggregate_type: "call",
    aggregate_id: "call-1",
    aggregate_version: cursor,
    payload: { status: "active" },
  };
}

describe("RealtimeClient", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("connects, subscribes with cursor, deduplicates, and persists progress", () => {
    const socket = new MockSocket();
    const events: RealtimeEventEnvelope[] = [];
    const statuses: RealtimeStatus[] = [];
    localStorage.setItem("realtime-test", "4");
    const client = new RealtimeClient({
      storageKey: "realtime-test",
      projectIds: ["project-1"],
      websocketFactory: () => socket as unknown as WebSocket,
      onEvent: (event) => events.push(event),
      onResync: vi.fn(),
      onStatus: (status) => statuses.push(status),
    });

    client.start();
    socket.open();
    expect(JSON.parse(socket.sent[0])).toEqual({
      type: "subscribe",
      cursor: 4,
      project_ids: ["project-1"],
    });
    socket.message({ type: "event", event: envelope(5) });
    socket.message({ type: "event", event: envelope(5) });
    socket.message({ type: "event", event: envelope(3, "old-event") });
    socket.message({
      type: "event",
      event: { ...envelope(6), aggregate_version: 4 },
    });

    expect(events).toHaveLength(1);
    expect(localStorage.getItem("realtime-test")).toBe("6");
    expect(statuses).toContain("connected");
    client.stop();
    expect(statuses.at(-1)).toBe("offline");
  });

  it("requests canonical REST resync and accepts a server cursor", () => {
    const socket = new MockSocket();
    const resync = vi.fn();
    const client = new RealtimeClient({
      storageKey: "realtime-resync",
      websocketFactory: () => socket as unknown as WebSocket,
      onEvent: vi.fn(),
      onResync: resync,
      onStatus: vi.fn(),
    });

    client.start();
    socket.open();
    socket.message({ type: "cursor", cursor: 9 });
    expect(localStorage.getItem("realtime-resync")).toBe("9");
    socket.message({ type: "resync_required", cursor: 9 });
    expect(resync).toHaveBeenCalledOnce();
    expect(localStorage.getItem("realtime-resync")).toBe("0");
    client.stop();
  });

  it("enters reconnecting state after a socket closes", () => {
    vi.useFakeTimers();
    const first = new MockSocket();
    const sockets = [first, new MockSocket()];
    const statuses: RealtimeStatus[] = [];
    const client = new RealtimeClient({
      storageKey: "realtime-reconnect",
      websocketFactory: () => sockets.shift() as unknown as WebSocket,
      onEvent: vi.fn(),
      onResync: vi.fn(),
      onStatus: (status) => statuses.push(status),
    });

    client.start();
    first.open();
    first.close();
    expect(statuses.at(-1)).toBe("reconnecting");
    client.stop();
    vi.useRealTimers();
  });

  it("delivers the same event independently to two tabs sharing persisted progress", () => {
    const firstSocket = new MockSocket();
    const secondSocket = new MockSocket();
    const firstEvents: RealtimeEventEnvelope[] = [];
    const secondEvents: RealtimeEventEnvelope[] = [];
    const makeClient = (socket: MockSocket, events: RealtimeEventEnvelope[]) =>
      new RealtimeClient({
        storageKey: "realtime-shared-tabs",
        websocketFactory: () => socket as unknown as WebSocket,
        onEvent: (event) => events.push(event),
        onResync: vi.fn(),
        onStatus: vi.fn(),
      });
    const firstClient = makeClient(firstSocket, firstEvents);
    const secondClient = makeClient(secondSocket, secondEvents);

    firstClient.start();
    secondClient.start();
    firstSocket.open();
    secondSocket.open();
    firstSocket.message({ type: "event", event: envelope(1) });
    secondSocket.message({ type: "event", event: envelope(1) });

    expect(firstEvents).toHaveLength(1);
    expect(secondEvents).toHaveLength(1);
    firstClient.stop();
    secondClient.stop();
  });

  it("switches to offline fallback and reconnects when the browser returns online", () => {
    const first = new MockSocket();
    const second = new MockSocket();
    const sockets = [first, second];
    const statuses: RealtimeStatus[] = [];
    const client = new RealtimeClient({
      storageKey: "realtime-network",
      websocketFactory: () => sockets.shift() as unknown as WebSocket,
      onEvent: vi.fn(),
      onResync: vi.fn(),
      onStatus: (status) => statuses.push(status),
    });
    const online = vi.spyOn(navigator, "onLine", "get");

    online.mockReturnValue(true);
    client.start();
    first.open();
    online.mockReturnValue(false);
    window.dispatchEvent(new Event("offline"));
    expect(statuses.at(-1)).toBe("offline");
    online.mockReturnValue(true);
    window.dispatchEvent(new Event("online"));
    second.open();
    expect(statuses.at(-1)).toBe("connected");
    client.stop();
  });

  it("maps Stage 14 events to canonical REST query groups", () => {
    expect(realtimeQueryKeys("job.progress")).toEqual([
      ["background-jobs"],
      ["customer-imports"],
      ["knowledge"],
    ]);
    expect(realtimeQueryKeys("import.completed")).toContainEqual(["customers"]);
    expect(realtimeQueryKeys("retention.preview_ready")).toContainEqual([
      "retention",
    ]);
    expect(realtimeQueryKeys("storage.issue_detected")).toContainEqual([
      "storage",
    ]);
  });
});
