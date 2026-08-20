"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type { AIRealtimeSessionDetail, CallDetail } from "@/lib/types";

export function ConversationDetail({ callId }: { callId: string }) {
  const call = useQuery({
    queryKey: ["calls", callId],
    queryFn: () => apiRequest<CallDetail>(`/calls/${callId}`),
  });
  const ai = useQuery({
    queryKey: ["ai-realtime", "details", callId],
    queryFn: () =>
      apiRequest<AIRealtimeSessionDetail>(
        `/calls/${callId}/ai-session/details`,
      ),
    enabled: call.data?.caller_type === "ai_agent",
    retry: false,
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
          {call.data.transfers.length > 0 && (
            <section className="ai-session-summary">
              <h2>Перевод живому оператору</h2>
              <div className="ai-detail-list">
                {call.data.transfers.map((transfer) => (
                  <span key={transfer.id}>
                    {transfer.reason} · {transfer.status}
                  </span>
                ))}
              </div>
            </section>
          )}
          {ai.data && (
            <section className="ai-session-summary">
              <div className="row-between">
                <h2>Voice AI</h2>
                <StatusBadge
                  tone={
                    ai.data.session.state === "failed" ||
                    ai.data.session.state === "degraded"
                      ? "warning"
                      : "primary"
                  }
                >
                  {ai.data.session.state}
                </StatusBadge>
              </div>
              <div className="session-facts">
                <div>
                  <span>Модель</span>
                  <strong>{ai.data.session.model}</strong>
                </div>
                <div>
                  <span>Язык</span>
                  <strong>{ai.data.session.language_code.toUpperCase()}</strong>
                </div>
                <div>
                  <span>Прерывания</span>
                  <strong>{ai.data.session.interruption_count}</strong>
                </div>
                <div>
                  <span>Стоимость</span>
                  <strong>
                    {ai.data.usage.some((item) => item.pricing_available)
                      ? "по pricing snapshot"
                      : "Недоступно"}
                  </strong>
                </div>
              </div>
              {ai.data.tools.length > 0 && (
                <div className="ai-detail-list">
                  <strong>Инструменты</strong>
                  {ai.data.tools.map((tool) => (
                    <span key={tool.id}>
                      {tool.tool_name} · {tool.status}
                      {tool.duration_ms === null
                        ? ""
                        : ` · ${tool.duration_ms} ms`}
                    </span>
                  ))}
                </div>
              )}
              {ai.data.citations.length > 0 && (
                <div className="ai-detail-list">
                  <strong>Источники</strong>
                  {ai.data.citations.flatMap((retrieval) =>
                    retrieval.no_match
                      ? [
                          <span key={`${retrieval.revision_id}-no-match`}>
                            Информация не найдена
                          </span>,
                        ]
                      : retrieval.citations.map((citation, index) => (
                          <span key={`${retrieval.revision_id}-${index}`}>
                            {String(citation.title ?? "Документ")}
                            {citation.page
                              ? ` · стр. ${String(citation.page)}`
                              : ""}
                          </span>
                        )),
                  )}
                </div>
              )}
              <p className="compact-note">
                Телефонные расходы не входят в AI usage. Цена AI показывается
                только при сохранённом pricing snapshot.
              </p>
            </section>
          )}
        </aside>
      </div>
    </>
  );
}
