"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type { CallDetail } from "@/lib/types";

export function ConversationDetail({ callId }: { callId: string }) {
  const call = useQuery({
    queryKey: ["calls", callId],
    queryFn: () => apiRequest<CallDetail>(`/calls/${callId}`),
  });
  if (call.isPending)
    return <div className="panel skeleton">Загрузка разговора</div>;
  if (call.isError)
    return (
      <div className="error-state panel">
        <div>
          <h1>Разговор недоступен</h1>
          <p>{call.error.message}</p>
          <Link
            className="tv-button tv-button-secondary"
            href="/app/conversations"
          >
            К разговорам
          </Link>
        </div>
      </div>
    );
  return (
    <>
      <div className="page-heading row-between">
        <div>
          <Link className="back-link" href="/app/conversations">
            ← Разговоры
          </Link>
          <h1>Разговор</h1>
          <p>ID: {call.data.id}</p>
        </div>
        <div className="inline-badges">
          <StatusBadge
            tone={call.data.status === "completed" ? "success" : "warning"}
          >
            {call.data.status}
          </StatusBadge>
          {call.data.is_demo && (
            <StatusBadge tone="warning">Тестовая симуляция</StatusBadge>
          )}
        </div>
      </div>
      <div className="content-grid">
        <section className="panel">
          <h2>Стенограмма</h2>
          <div className="transcript-list">
            {call.data.transcript.map((segment) => (
              <article
                className={`transcript-line ${segment.speaker}`}
                key={segment.id}
              >
                <span>
                  {segment.speaker} · {segment.language.toUpperCase()}
                </span>
                <p>{segment.text}</p>
              </article>
            ))}
          </div>
        </section>
        <aside className="panel">
          <h2>Итог</h2>
          {call.data.summary ? (
            <div className="summary-card">
              <StatusBadge tone="success">
                {call.data.summary.result.replaceAll("_", " ")}
              </StatusBadge>
              <p>{call.data.summary.summary}</p>
              <small>{call.data.summary.generated_by}</small>
            </div>
          ) : (
            <div className="empty-state">
              <p>Итог появится после завершения разговора.</p>
            </div>
          )}
          <div className="session-facts">
            <div>
              <span>Язык</span>
              <strong>
                {call.data.language?.toUpperCase() ?? "Не определён"}
              </strong>
            </div>
            <div>
              <span>Длительность</span>
              <strong>{call.data.duration_seconds} с</strong>
            </div>
            <div>
              <span>Причина перевода</span>
              <strong>{call.data.transfer_reason ?? "Нет"}</strong>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
