"use client";

import { useQuery } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import Link from "next/link";
import { StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type { AIRealtimeSession, Call, LiveTransfer } from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { useRealtime } from "@/components/realtime-provider";

const languageLabel: Record<string, string> = {
  ru: "Русский",
  en: "English",
  uz: "O‘zbekcha",
  kaa: "Qaraqalpaq (Experimental)",
};

export function LiveCallsView() {
  const realtime = useRealtime();
  const calls = useQuery({
    queryKey: ["live-calls"],
    queryFn: () => apiRequest<Call[]>("/live-calls"),
    refetchInterval: realtime.connected ? 60_000 : 15_000,
  });
  const aiSessions = useQuery({
    queryKey: ["ai-realtime", "active"],
    queryFn: () =>
      apiRequest<AIRealtimeSession[]>("/ai-realtime/sessions/active"),
    refetchInterval: realtime.connected ? 60_000 : 15_000,
  });
  const transfers = useQuery({
    queryKey: ["transfers", "live-calls"],
    queryFn: () => apiRequest<LiveTransfer[]>("/transfers"),
    refetchInterval: realtime.connected ? 60_000 : 15_000,
  });
  const aiByCall = new Map(
    (aiSessions.data ?? []).map((session) => [session.call_id, session]),
  );
  const transferByCall = new Map(
    (transfers.data ?? []).map((transfer) => [transfer.call_id, transfer]),
  );

  if (calls.isPending) return <SectionSkeleton />;
  if (calls.isError)
    return (
      <QueryError
        error={calls.error}
        retry={() => void calls.refetch()}
        title="Не удалось загрузить активные звонки"
      />
    );

  return (
    <>
      <div className="page-heading row-between">
        <h1>Активные звонки</h1>
        <StatusBadge tone={calls.data.length ? "success" : "neutral"}>
          {calls.data.length} в линии
        </StatusBadge>
      </div>
      <section className="panel">
        {calls.data.length === 0 ? (
          <div className="empty-state">
            <div>
              <Radio aria-hidden="true" size={28} />
              <h3>Сейчас нет активных звонков</h3>
              <p>
                SIP ещё не подключён. Тестовый звонок можно запустить в
                симуляторе.
              </p>
              <Link
                className="tv-button tv-button-secondary"
                href="/app/dev-simulator"
              >
                Открыть симулятор
              </Link>
            </div>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Звонок</th>
                  <th>Канал</th>
                  <th>Язык</th>
                  <th>Статус</th>
                  <th>Voice AI</th>
                  <th>Перевод</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {calls.data.map((call) => {
                  const ai = aiByCall.get(call.id);
                  const transfer = transferByCall.get(call.id);
                  return (
                    <tr key={call.id}>
                      <td>
                        <strong>{call.id.slice(0, 8)}</strong>
                      </td>
                      <td>{call.channel === "sip" ? "SIP" : "Симулятор"}</td>
                      <td>{languageLabel[call.language ?? ""] ?? "—"}</td>
                      <td>
                        <StatusBadge
                          tone={
                            call.status === "active" ? "success" : "warning"
                          }
                        >
                          {call.status === "active" ? "В разговоре" : "Перевод"}
                        </StatusBadge>
                      </td>
                      <td>
                        {ai ? (
                          <div className="ai-call-state">
                            <StatusBadge
                              tone={
                                ai.state === "degraded" ? "warning" : "primary"
                              }
                            >
                              {ai.state === "active"
                                ? "слушает / говорит"
                                : ai.state}
                            </StatusBadge>
                            <small>
                              {ai.language_code.toUpperCase()} · {ai.model} ·
                              прерываний {ai.interruption_count}
                              {typeof ai.latency.first_audio_latency_ms ===
                              "number"
                                ? ` · TTFA ${Math.round(ai.latency.first_audio_latency_ms)} мс`
                                : ""}
                            </small>
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>
                        {transfer ? (
                          <div className="ai-call-state">
                            <StatusBadge
                              tone={
                                transfer.status === "connected"
                                  ? "success"
                                  : "warning"
                              }
                            >
                              {transfer.status}
                            </StatusBadge>
                            <small>{transfer.reason}</small>
                          </div>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>
                        <Link href={`/app/conversations/${call.id}`}>
                          Открыть
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
