"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, BookOpen, Square } from "lucide-react";
import { useState } from "react";
import type { FormEvent } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { ApiClientError, apiRequest, idempotencyKey } from "@/lib/api";
import type {
  AIRealtimeDiagnostic,
  AiOperator,
  CallDetail,
  Page,
  TranscriptSegment,
} from "@/lib/types";

type SimulatorMessage = {
  call: CallDetail;
  customer_segment: TranscriptSegment;
  assistant_segment: TranscriptSegment;
  tool_name: string;
  tool_result: {
    deterministic_retrieval?: boolean;
    knowledge_revision_id?: string | null;
    citations?: Array<{
      title?: string;
      document_version?: number;
      page?: number | null;
      section?: string | null;
      chunk_id?: string;
    }>;
    scores?: number[];
    reason?: string;
  };
  transfer_requested: boolean;
};

export function SimulatorView() {
  const queryClient = useQueryClient();
  const [operatorId, setOperatorId] = useState("");
  const [language, setLanguage] = useState("ru");
  const [customerName, setCustomerName] = useState("");
  const [phone, setPhone] = useState("+998901234567");
  const [message, setMessage] = useState("");
  const [call, setCall] = useState<CallDetail | null>(null);
  const [lastTool, setLastTool] = useState("");
  const [lastToolResult, setLastToolResult] = useState<
    SimulatorMessage["tool_result"] | null
  >(null);
  const [error, setError] = useState("");
  const [voiceNotice, setVoiceNotice] = useState("");
  const operators = useQuery({
    queryKey: ["operators"],
    queryFn: () => apiRequest<Page<AiOperator>>("/ai-operators"),
  });
  const published =
    operators.data?.items.filter(
      (item) => item.version.status === "published",
    ) ?? [];

  const start = useMutation({
    mutationFn: () =>
      apiRequest<CallDetail>("/simulator/calls", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("sim-start") },
        body: JSON.stringify({
          ai_operator_id: operatorId,
          language,
          customer_name: customerName || null,
          customer_phone: phone,
        }),
      }),
    onSuccess: (data) => {
      setCall(data);
      setError("");
      setLastTool("");
      setLastToolResult(null);
    },
    onError: handleError,
  });
  const send = useMutation({
    mutationFn: (text: string) =>
      apiRequest<SimulatorMessage>(`/simulator/calls/${call?.id}/messages`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("sim-message") },
        body: JSON.stringify({ text }),
      }),
    onSuccess: (data) => {
      setCall((current) =>
        current
          ? {
              ...data.call,
              transcript: [
                ...current.transcript,
                data.customer_segment,
                data.assistant_segment,
              ],
              summary: current.summary,
            }
          : current,
      );
      setLastTool(data.tool_name);
      setLastToolResult(data.tool_result);
      setMessage("");
      setError("");
    },
    onError: handleError,
  });
  const finish = useMutation({
    mutationFn: () =>
      apiRequest<CallDetail>(`/simulator/calls/${call?.id}/finish`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("sim-finish") },
      }),
    onSuccess: async (data) => {
      setCall(data);
      setError("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["calls"] }),
      ]);
    },
    onError: handleError,
  });
  const voiceFixture = useMutation({
    mutationFn: () =>
      apiRequest<AIRealtimeDiagnostic>("/ai-realtime/diagnostics/local", {
        method: "POST",
      }),
    onSuccess: (result) =>
      setVoiceNotice(
        result.status === "local_mock_passed"
          ? "Локальный voice fixture прошёл: VAD, audio, transcript и usage. Live OpenAI не проверялся."
          : `Voice fixture: ${result.status}`,
      ),
    onError: handleError,
  });

  function handleError(caught: unknown) {
    setError(
      caught instanceof ApiClientError
        ? caught.message
        : "Не удалось выполнить симуляцию",
    );
  }

  function submitMessage(event: FormEvent) {
    event.preventDefault();
    if (message.trim() && call?.status === "active")
      send.mutate(message.trim());
  }

  const canStart = Boolean(operatorId && /^\+[1-9][0-9]{7,14}$/.test(phone));
  const selectedOperator = published.find((item) => item.id === operatorId);
  return (
    <>
      <div className="page-heading">
        <h1>Тестовый звонок</h1>
      </div>
      <div className="simulation-banner">
        <AlertTriangle aria-hidden="true" size={18} />
        <span>Симуляция — это не настоящий телефонный звонок.</span>
      </div>
      <div className="compact-note voice-test-note">
        <div>
          <strong>Voice test mode</strong>
          <span>
            Детерминированный локальный media fixture; не выполняет OpenAI API
            запрос и не расходует бюджет.
          </span>
        </div>
        <Button
          disabled={voiceFixture.isPending}
          onClick={() => voiceFixture.mutate()}
          type="button"
          variant="secondary"
        >
          {voiceFixture.isPending ? "Проверяем…" : "Проверить voice fixture"}
        </Button>
        {voiceNotice && <span role="status">{voiceNotice}</span>}
      </div>
      <div className="simulator-layout">
        <section className="form-card">
          <h2>Настройка</h2>
          <p className="panel-subtitle">
            Ответы только по сохранённой базе знаний
          </p>
          <div className="form-stack">
            <div className="field">
              <label htmlFor="sim-operator">Опубликованный оператор</label>
              <select
                disabled={Boolean(call && call.status !== "completed")}
                id="sim-operator"
                onChange={(event) => {
                  const nextId = event.target.value;
                  setOperatorId(nextId);
                  const nextOperator = published.find(
                    (item) => item.id === nextId,
                  );
                  if (
                    nextOperator &&
                    !nextOperator.version.allowed_languages.includes(language)
                  ) {
                    setLanguage(
                      nextOperator.version.allowed_languages[0] ?? "ru",
                    );
                  }
                }}
                value={operatorId}
              >
                <option value="">Выберите оператора</option>
                {published.map((operator) => (
                  <option key={operator.id} value={operator.id}>
                    {operator.name} · v{operator.version.version}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="sim-language">Язык</label>
              <select
                disabled={
                  !selectedOperator ||
                  Boolean(call && call.status !== "completed")
                }
                id="sim-language"
                onChange={(event) => setLanguage(event.target.value)}
                value={language}
              >
                {(selectedOperator?.version.allowed_languages ?? ["ru"]).map(
                  (item) => (
                    <option key={item} value={item}>
                      {item.toUpperCase()}
                    </option>
                  ),
                )}
              </select>
            </div>
            {!operators.isPending && published.length === 0 && (
              <div className="form-warning">
                Сначала создайте и опубликуйте AI-оператора.
              </div>
            )}
            <div className="field">
              <label htmlFor="sim-name">Имя тестового клиента</label>
              <input
                disabled={Boolean(call && call.status !== "completed")}
                id="sim-name"
                onChange={(event) => setCustomerName(event.target.value)}
                value={customerName}
              />
            </div>
            <div className="field">
              <label htmlFor="sim-phone">Тестовый номер E.164</label>
              <input
                disabled={Boolean(call && call.status !== "completed")}
                id="sim-phone"
                onChange={(event) => setPhone(event.target.value)}
                value={phone}
              />
            </div>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            {!call || call.status === "completed" ? (
              <Button
                disabled={!canStart || start.isPending}
                onClick={() => start.mutate()}
              >
                {start.isPending
                  ? "Запускаем…"
                  : call
                    ? "Новая симуляция"
                    : "Начать"}
                <ArrowRight size={15} />
              </Button>
            ) : (
              <Button
                disabled={finish.isPending}
                onClick={() => finish.mutate()}
                variant="secondary"
              >
                <Square size={14} />
                {finish.isPending ? "Завершаем…" : "Завершить и создать итог"}
              </Button>
            )}
          </div>
          {call && (
            <div className="session-facts">
              <div>
                <span>Статус</span>
                <StatusBadge
                  tone={
                    call.status === "completed"
                      ? "success"
                      : call.status === "transferring"
                        ? "warning"
                        : "primary"
                  }
                >
                  {call.status}
                </StatusBadge>
              </div>
              <div>
                <span>Канал</span>
                <strong>Тестовый симулятор</strong>
              </div>
              <div>
                <span>Последний инструмент</span>
                <strong>{lastTool || "Нет"}</strong>
              </div>
              <div>
                <span>Knowledge revision</span>
                <strong>
                  {call.knowledge_base_revision_id
                    ? call.knowledge_base_revision_id.slice(0, 8)
                    : "Не зафиксирована"}
                </strong>
              </div>
            </div>
          )}
        </section>
        <section className="panel transcript-panel">
          <div className="row-between">
            <div>
              <h2>Стенограмма</h2>
            </div>
            {call && <StatusBadge tone="warning">Тестовые данные</StatusBadge>}
          </div>
          {!call && (
            <div className="empty-state">
              <div>
                <h3>Нет активной симуляции</h3>
                <p>Выберите оператора и начните тест.</p>
              </div>
            </div>
          )}
          {call && (
            <>
              <div className="transcript-list" aria-live="polite">
                {call.transcript.map((segment) => (
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
              {call.status === "active" && (
                <form className="chat-compose" onSubmit={submitMessage}>
                  <input
                    aria-label="Сообщение клиента"
                    onChange={(event) => setMessage(event.target.value)}
                    placeholder="Введите вопрос по базе знаний…"
                    value={message}
                  />
                  <Button
                    disabled={!message.trim() || send.isPending}
                    type="submit"
                  >
                    {send.isPending ? "Отправляем…" : "Отправить"}
                  </Button>
                </form>
              )}
              {call.status === "transferring" && (
                <div className="form-warning">
                  Запрошен перевод. Очередь операторов пока не подключена.
                </div>
              )}
              {lastToolResult?.deterministic_retrieval && (
                <section className="simulator-citations">
                  <div className="inline-badges">
                    <BookOpen size={16} />
                    <strong>Источники deterministic retrieval</strong>
                    <StatusBadge tone="warning">Без LLM</StatusBadge>
                  </div>
                  {lastToolResult.citations?.map((citation, index) => (
                    <div
                      className="simulator-citation"
                      key={citation.chunk_id ?? index}
                    >
                      <strong>{citation.title ?? "Документ"}</strong>
                      <span>
                        v{citation.document_version ?? "?"}
                        {citation.page ? ` · стр. ${citation.page}` : ""}
                        {citation.section ? ` · ${citation.section}` : ""}
                        {typeof lastToolResult.scores?.[index] === "number"
                          ? ` · score ${lastToolResult.scores[index].toFixed(3)}`
                          : ""}
                      </span>
                    </div>
                  ))}
                </section>
              )}
              {call.summary && (
                <div className="summary-card">
                  <div className="inline-badges">
                    <strong>Итог разговора</strong>
                    <StatusBadge tone="success">
                      {call.summary.result.replaceAll("_", " ")}
                    </StatusBadge>
                  </div>
                  <p>{call.summary.summary}</p>
                  <small>Создано: {call.summary.generated_by}</small>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </>
  );
}
