"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarClock,
  CheckCircle2,
  History,
  Phone,
  PhoneCall,
  PhoneOff,
  RotateCcw,
  UserRound,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, StatusBadge } from "@teamora/ui";
import { ApiClientError, apiRequest } from "@/lib/api";
import type {
  Call,
  CallResultResponse,
  DialerAssignment,
  Page,
  Project,
} from "@/lib/types";

const resultSchema = z
  .object({
    result: z.enum([
      "success",
      "no_answer",
      "busy",
      "callback",
      "wrong_number",
      "do_not_call",
      "not_interested",
      "failed",
      "other",
    ]),
    comment: z.string().max(4000),
    callback_at: z.string(),
  })
  .superRefine((value, context) => {
    if (value.result === "callback" && !value.callback_at) {
      context.addIssue({
        code: "custom",
        path: ["callback_at"],
        message: "Укажите дату и время перезвона",
      });
    }
  });

type ResultForm = z.infer<typeof resultSchema>;

const resultOptions = [
  ["success", "Успешно"],
  ["no_answer", "Нет ответа"],
  ["busy", "Занято"],
  ["callback", "Перезвон"],
  ["wrong_number", "Неверный номер"],
  ["do_not_call", "Не звонить"],
  ["not_interested", "Не заинтересован"],
  ["failed", "Ошибка"],
  ["other", "Другое"],
] as const;

function formatTimer(seconds: number) {
  const minutes = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const rest = (seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${rest}`;
}

function useCallSeconds(call: Call | null | undefined) {
  const [seconds, setSeconds] = useState(call?.duration_seconds ?? 0);
  useEffect(() => {
    const update = () => {
      if (!call) return setSeconds(0);
      if (call.status === "completed") return setSeconds(call.duration_seconds);
      const start = call.answered_at ?? call.started_at;
      if (!start) return setSeconds(0);
      setSeconds(
        Math.max(
          0,
          Math.floor((Date.now() - new Date(start).getTime()) / 1000),
        ),
      );
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [call]);
  return seconds;
}

function primaryPhone(assignment: DialerAssignment | null | undefined) {
  return assignment?.customer.contacts.find(
    (contact) => contact.kind === "phone" && contact.is_primary,
  )?.value;
}

export function DialerView() {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const projects = useQuery({
    queryKey: ["projects", "dialer"],
    queryFn: () =>
      apiRequest<Page<Project>>("/projects?status=active&limit=100"),
  });
  useEffect(() => {
    if (selectedProjectId || !projects.data?.items.length) return;
    const defaultProject =
      projects.data.items.find((project) => project.is_default) ??
      projects.data.items[0];
    setSelectedProjectId(defaultProject.id);
  }, [projects.data, selectedProjectId]);
  const assignment = useQuery({
    queryKey: ["dialer", "current"],
    queryFn: () => apiRequest<DialerAssignment | null>("/dialer/current"),
  });
  const activeCall = useQuery({
    queryKey: ["dialer", "active-call"],
    queryFn: () => apiRequest<Call | null>("/calls/active"),
    refetchInterval: 5000,
  });
  const history = useQuery({
    queryKey: ["calls", "dialer-history", assignment.data?.customer.id],
    queryFn: () =>
      apiRequest<Call[]>(
        `/dialer/history?customer_id=${assignment.data?.customer.id}`,
      ),
    enabled: Boolean(assignment.data?.customer.id),
  });
  const resultForm = useForm<ResultForm>({
    resolver: zodResolver(resultSchema),
    defaultValues: { result: "success", comment: "", callback_at: "" },
  });
  const selectedResult = resultForm.watch("result");
  const call = activeCall.data;
  const seconds = useCallSeconds(call);

  const nextClient = useMutation({
    mutationFn: () =>
      apiRequest<DialerAssignment | null>(
        `/dialer/next-client${selectedProjectId ? `?project_id=${selectedProjectId}` : ""}`,
        { method: "POST" },
      ),
    onSuccess: (value) => {
      queryClient.setQueryData(["dialer", "current"], value);
      setMessage(
        value ? "" : "Очередь пуста: добавьте клиентов или задачи на сегодня.",
      );
    },
    onError: showError,
  });
  const startCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>("/calls/start", {
        method: "POST",
        body: JSON.stringify({
          customer_id: assignment.data?.customer.id,
          lock_token: assignment.data?.lock_token,
          callback_task_id: assignment.data?.callback_task_id,
          from_number: "MOCK",
        }),
      }),
    onSuccess: (value) => {
      queryClient.setQueryData(["dialer", "active-call"], value);
      setMessage("");
    },
    onError: showError,
  });
  const answerCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/answer`, { method: "POST" }),
    onSuccess: (value) =>
      queryClient.setQueryData(["dialer", "active-call"], value),
    onError: showError,
  });
  const hangupCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/hangup`, { method: "POST" }),
    onSuccess: (value) =>
      queryClient.setQueryData(["dialer", "active-call"], value),
    onError: showError,
  });
  const saveResult = useMutation({
    mutationFn: (value: ResultForm) =>
      apiRequest<CallResultResponse>(`/calls/${call?.id}/result`, {
        method: "POST",
        body: JSON.stringify({
          result: value.result,
          comment: value.comment,
          callback_at:
            value.result === "callback" && value.callback_at
              ? new Date(value.callback_at).toISOString()
              : null,
        }),
      }),
    onSuccess: async () => {
      queryClient.setQueryData(["dialer", "active-call"], null);
      queryClient.setQueryData(["dialer", "current"], null);
      resultForm.reset();
      setMessage("Результат сохранён. Можно получить следующего клиента.");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["calls"] }),
        queryClient.invalidateQueries({ queryKey: ["callbacks"] }),
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
    },
    onError: showError,
  });
  const release = useMutation({
    mutationFn: () =>
      apiRequest<void>(
        `/dialer/customers/${assignment.data?.customer.id}/release`,
        {
          method: "POST",
          body: JSON.stringify({ lock_token: assignment.data?.lock_token }),
        },
      ),
    onSuccess: () => {
      queryClient.setQueryData(["dialer", "current"], null);
      setMessage("Клиент возвращён в очередь.");
    },
    onError: showError,
  });

  function showError(error: unknown) {
    setMessage(
      error instanceof ApiClientError ? error.message : "Операция не выполнена",
    );
  }

  useEffect(() => {
    const current = assignment.data;
    if (!current) return;
    const heartbeat = () => {
      void apiRequest<void>(
        `/dialer/customers/${current.customer.id}/heartbeat`,
        {
          method: "POST",
          body: JSON.stringify({ lock_token: current.lock_token }),
        },
      ).catch((error: unknown) => {
        if (
          error instanceof ApiClientError &&
          error.code === "dialer_lease_lost"
        ) {
          queryClient.setQueryData(["dialer", "current"], null);
          setMessage(error.message);
        }
      });
    };
    const timer = window.setInterval(heartbeat, 60_000);
    return () => window.clearInterval(timer);
  }, [assignment.data, queryClient]);

  useEffect(() => {
    const current = assignment.data;
    if (!current) return;
    const releaseOnPageExit = () => {
      if (call) return;
      void apiRequest<void>(
        `/dialer/customers/${current.customer.id}/release`,
        {
          method: "POST",
          body: JSON.stringify({ lock_token: current.lock_token }),
          keepalive: true,
        },
      ).catch(() => undefined);
    };
    window.addEventListener("pagehide", releaseOnPageExit);
    return () => window.removeEventListener("pagehide", releaseOnPageExit);
  }, [assignment.data, call]);

  const customerHistory = useMemo(
    () =>
      (history.data ?? [])
        .filter((item) => item.customer_id === assignment.data?.customer.id)
        .slice(0, 5),
    [assignment.data?.customer.id, history.data],
  );
  const isBusy =
    nextClient.isPending ||
    startCall.isPending ||
    answerCall.isPending ||
    hangupCall.isPending ||
    saveResult.isPending ||
    release.isPending;

  return (
    <>
      <div className="page-heading row-between dialer-heading">
        <div>
          <h1>Рабочая станция оператора</h1>
          <p>Карточка клиента, звонок и результат в одном экране</p>
        </div>
        <label className="field dialer-project-select">
          <span>Проект</span>
          <select
            disabled={Boolean(assignment.data || call)}
            onChange={(event) => setSelectedProjectId(event.target.value)}
            value={selectedProjectId}
          >
            <option value="">Выберите проект</option>
            {projects.data?.items.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        <div className="operator-state">
          <span className="presence-dot" />
          <div>
            <strong>Готов к звонкам</strong>
            <span>Development · Mock provider</span>
          </div>
        </div>
      </div>
      <div className="dialer-workspace">
        <section className="panel dialer-customer-panel">
          {!assignment.data ? (
            <div className="dialer-empty">
              <div className="dialer-empty-icon">
                <UserRound size={28} />
              </div>
              <h2>Ожидание клиента</h2>
              <p>
                Сначала выдаются просроченные и сегодняшние перезвоны, затем
                новые клиенты.
              </p>
              <Button
                disabled={isBusy || assignment.isPending}
                onClick={() => nextClient.mutate()}
              >
                <RotateCcw size={16} />
                {nextClient.isPending ? "Получаем…" : "Следующий клиент"}
              </Button>
            </div>
          ) : (
            <>
              <div className="row-between customer-card-head">
                <div className="customer-identity">
                  <div className="customer-avatar">
                    {(assignment.data.customer.display_name ?? "К")
                      .slice(0, 1)
                      .toUpperCase()}
                  </div>
                  <div>
                    <span>Текущий клиент</span>
                    <h2>
                      {assignment.data.customer.display_name ?? "Без имени"}
                    </h2>
                    <p>
                      {primaryPhone(assignment.data) ?? "Телефон отсутствует"}
                    </p>
                  </div>
                </div>
                <StatusBadge
                  tone={
                    assignment.data.source === "callback"
                      ? "warning"
                      : "primary"
                  }
                >
                  {assignment.data.source === "callback" ? "Перезвон" : "Новый"}
                </StatusBadge>
              </div>
              <div className="customer-facts">
                <div>
                  <span>Язык</span>
                  <strong>
                    {assignment.data.customer.preferred_language?.toUpperCase() ??
                      "RU"}
                  </strong>
                </div>
                <div>
                  <span>Внешний ID</span>
                  <strong>
                    {assignment.data.customer.external_reference ??
                      assignment.data.customer.id.slice(0, 8)}
                  </strong>
                </div>
                <div>
                  <span>Последний звонок</span>
                  <strong>
                    {assignment.data.customer.last_call_at
                      ? new Date(
                          assignment.data.customer.last_call_at,
                        ).toLocaleString("ru-RU")
                      : "Первый контакт"}
                  </strong>
                </div>
              </div>
              {!call && (
                <div className="dialer-primary-actions">
                  <Button disabled={isBusy} onClick={() => startCall.mutate()}>
                    <PhoneCall size={18} />
                    Позвонить
                  </Button>
                  <Button
                    disabled={isBusy}
                    onClick={() => release.mutate()}
                    variant="secondary"
                  >
                    Вернуть в очередь
                  </Button>
                </div>
              )}
              {call && (
                <div className={`active-call-console ${call.status}`}>
                  <div>
                    <span>
                      {call.status === "ringing" ? "Вызов клиента" : "Разговор"}
                    </span>
                    <strong className="call-timer">
                      {formatTimer(seconds)}
                    </strong>
                    <small>{call.to_number}</small>
                  </div>
                  <div className="call-control-row">
                    {call.status === "ringing" && (
                      <Button
                        disabled={isBusy}
                        onClick={() => answerCall.mutate()}
                      >
                        <Phone size={17} />
                        Имитировать ответ
                      </Button>
                    )}
                    {(call.status === "ringing" ||
                      call.status === "active") && (
                      <button
                        aria-label="Завершить звонок"
                        className="hangup-button"
                        disabled={isBusy}
                        onClick={() => hangupCall.mutate()}
                        type="button"
                      >
                        <PhoneOff size={20} />
                      </button>
                    )}
                  </div>
                </div>
              )}
              {call?.status === "completed" && (
                <form
                  className="call-result-form"
                  onSubmit={resultForm.handleSubmit((value) =>
                    saveResult.mutate(value),
                  )}
                >
                  <div className="row-between">
                    <div>
                      <h3>Результат звонка</h3>
                      <p>Обязателен перед следующим клиентом</p>
                    </div>
                    <CheckCircle2 size={20} />
                  </div>
                  <div className="result-choice-grid">
                    {resultOptions.map(([value, label]) => (
                      <label key={value}>
                        <input
                          type="radio"
                          value={value}
                          {...resultForm.register("result")}
                        />
                        <span>{label}</span>
                      </label>
                    ))}
                  </div>
                  {selectedResult === "callback" && (
                    <div className="field">
                      <label htmlFor="callback-at">
                        Дата и время перезвона
                      </label>
                      <input
                        id="callback-at"
                        min={new Date().toISOString().slice(0, 16)}
                        type="datetime-local"
                        {...resultForm.register("callback_at")}
                      />
                      {resultForm.formState.errors.callback_at && (
                        <small className="field-error">
                          {resultForm.formState.errors.callback_at.message}
                        </small>
                      )}
                    </div>
                  )}
                  <div className="field">
                    <label htmlFor="call-comment">Комментарий</label>
                    <textarea
                      id="call-comment"
                      placeholder="Кратко зафиксируйте договорённости"
                      rows={3}
                      {...resultForm.register("comment")}
                    />
                  </div>
                  <Button disabled={isBusy} type="submit">
                    {saveResult.isPending
                      ? "Сохраняем…"
                      : "Сохранить результат"}
                  </Button>
                </form>
              )}
            </>
          )}
          {message && <div className="dialer-message">{message}</div>}
        </section>
        <aside className="dialer-side-stack">
          <section className="panel dialer-guide">
            <div className="row-between">
              <h2>Контроль разговора</h2>
              <StatusBadge>Скрипт</StatusBadge>
            </div>
            <ol>
              <li>Представьтесь и назовите K-Line.</li>
              <li>Подтвердите, что разговариваете с нужным клиентом.</li>
              <li>Сообщите цель звонка без раскрытия лишних данных.</li>
              <li>Зафиксируйте результат и следующий шаг.</li>
            </ol>
          </section>
          <section className="panel compact-history">
            <div className="row-between">
              <h2>История</h2>
              <History size={17} />
            </div>
            {customerHistory.length === 0 ? (
              <p className="panel-subtitle">Предыдущих звонков нет</p>
            ) : (
              customerHistory.map((item) => (
                <div className="history-row" key={item.id}>
                  <div>
                    <strong>{item.status}</strong>
                    <span>
                      {item.started_at
                        ? new Date(item.started_at).toLocaleString("ru-RU")
                        : "—"}
                    </span>
                  </div>
                  <span>{formatTimer(item.duration_seconds)}</span>
                </div>
              ))
            )}
          </section>
          <section className="panel dialer-next-step">
            <CalendarClock size={20} />
            <div>
              <strong>Перезвон</strong>
              <p>Создаётся автоматически при выборе результата «Перезвон».</p>
            </div>
          </section>
        </aside>
      </div>
    </>
  );
}
