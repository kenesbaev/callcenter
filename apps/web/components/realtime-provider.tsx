"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  RealtimeClient,
  type RealtimeEventEnvelope,
  type RealtimeStatus,
} from "@/lib/realtime";
import type { Call, CallStatus } from "@/lib/types";

type RealtimeContextValue = {
  status: RealtimeStatus;
  connected: boolean;
  disconnect: () => void;
};

const RealtimeContext = createContext<RealtimeContextValue>({
  status: "offline",
  connected: false,
  disconnect: () => undefined,
});

const queryGroups: Array<{ prefix: string; keys: string[][] }> = [
  {
    prefix: "call.",
    keys: [
      ["calls"],
      ["live-calls"],
      ["dialer"],
      ["team"],
      ["analytics"],
      ["conversations"],
    ],
  },
  {
    prefix: "transfer.",
    keys: [["calls"], ["live-calls"], ["dialer"], ["team"]],
  },
  { prefix: "dialer.", keys: [["dialer"]] },
  {
    prefix: "task.",
    keys: [["tasks"], ["callbacks"], ["dialer"], ["analytics"]],
  },
  {
    prefix: "callback.",
    keys: [["tasks"], ["callbacks"], ["dialer"], ["analytics"]],
  },
  { prefix: "operator.", keys: [["team"], ["calls"], ["live-calls"]] },
  { prefix: "team.", keys: [["team"], ["auth"]] },
  { prefix: "knowledge.", keys: [["knowledge"]] },
];

const terminalCallStates = new Set<CallStatus>([
  "completed",
  "busy",
  "no_answer",
  "failed",
  "cancelled",
]);

function patchLiveCall(
  calls: Call[] | undefined,
  event: RealtimeEventEnvelope,
): Call[] | undefined {
  if (!calls || event.event_type !== "call.state_changed") return calls;
  const status = event.payload.status;
  if (typeof status !== "string" || event.aggregate_version === null)
    return calls;
  const nextStatus = status as CallStatus;
  if (terminalCallStates.has(nextStatus)) {
    return calls.filter((call) => call.id !== event.aggregate_id);
  }
  return calls.map((call) =>
    call.id === event.aggregate_id &&
    call.state_version < (event.aggregate_version ?? 0)
      ? {
          ...call,
          status: nextStatus,
          state_version: event.aggregate_version ?? call.state_version,
        }
      : call,
  );
}

export function RealtimeProvider({
  tenantId,
  userId,
  children,
}: {
  tenantId: string;
  userId: string;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<RealtimeStatus>("offline");
  const clientRef = useRef<RealtimeClient | null>(null);
  const analyticsTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refetchCanonicalState = useCallback(() => {
    for (const key of [
      ["calls"],
      ["live-calls"],
      ["dialer"],
      ["tasks"],
      ["callbacks"],
      ["team"],
      ["analytics"],
      ["conversations"],
      ["knowledge"],
    ]) {
      void queryClient.invalidateQueries({ queryKey: key });
    }
  }, [queryClient]);

  const handleEvent = useCallback(
    (event: RealtimeEventEnvelope) => {
      if (event.event_type === "analytics.invalidated") {
        if (analyticsTimer.current) clearTimeout(analyticsTimer.current);
        analyticsTimer.current = setTimeout(
          () => void queryClient.invalidateQueries({ queryKey: ["analytics"] }),
          350,
        );
        return;
      }
      if (event.event_type === "call.state_changed") {
        queryClient.setQueryData<Call[]>(["live-calls"], (calls) =>
          patchLiveCall(calls, event),
        );
      }
      const group = queryGroups.find(({ prefix }) =>
        event.event_type.startsWith(prefix),
      );
      for (const key of group?.keys ?? []) {
        if (
          event.event_type === "call.state_changed" &&
          key.length === 1 &&
          key[0] === "live-calls"
        ) {
          continue;
        }
        void queryClient.invalidateQueries({ queryKey: key });
      }
    },
    [queryClient],
  );

  useEffect(() => {
    const client = new RealtimeClient({
      storageKey: `kline:realtime:${tenantId}:${userId}:cursor`,
      onEvent: handleEvent,
      onResync: refetchCanonicalState,
      onStatus: setStatus,
    });
    clientRef.current = client;
    client.start();
    return () => {
      client.stop();
      clientRef.current = null;
      if (analyticsTimer.current) clearTimeout(analyticsTimer.current);
    };
  }, [handleEvent, refetchCanonicalState, tenantId, userId]);

  const value = useMemo<RealtimeContextValue>(
    () => ({
      status,
      connected: status === "connected",
      disconnect: () => clientRef.current?.stop(),
    }),
    [status],
  );
  return (
    <RealtimeContext.Provider value={value}>
      {children}
    </RealtimeContext.Provider>
  );
}

export function useRealtime(): RealtimeContextValue {
  return useContext(RealtimeContext);
}
