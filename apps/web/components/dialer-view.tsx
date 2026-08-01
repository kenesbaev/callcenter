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
  PauseCircle,
  PlayCircle,
  PhoneForwarded,
  RotateCcw,
  ListTodo,
  UserRound,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, StatusBadge } from "@teamora/ui";
import { ApiClientError, apiRequest, idempotencyKey } from "@/lib/api";
import type {
  Call,
  CallStatus,
  CallResultCatalog,
  CallResultCategory,
  CallResultDefinition,
  CallResultResponse,
  DialerAssignment,
  Page,
  Project,
} from "@/lib/types";

const resultSchema = z.object({
  result_definition_id: z.string().min(1, "Выберите результат"),
  comment: z.string().max(4000),
  callback_at: z.string(),
  create_task: z.boolean(),
  task_title: z.string().max(240),
  task_due_at: z.string(),
  task_priority: z.enum(["low", "normal", "high", "urgent"]),
});

type ResultForm = z.infer<typeof resultSchema>;

const categoryLabels: Record<CallResultCategory, string> = {
  successful: "Успешные",
  intermediate: "Промежуточные",
  unreachable: "Недозвон",
  unsuccessful: "Неуспешные",
};

export function localizedResult(
  definition: CallResultDefinition,
  language: string | null | undefined,
) {
  const normalized = (language ?? "ru").toLowerCase();
  return (
    definition.name_translations[normalized] ??
    definition.name_translations[normalized.split("-", 1)[0]] ??
    definition.name_translations.ru ??
    definition.name
  );
}

export function groupCallResults(definitions: CallResultDefinition[]) {
  return Object.fromEntries(
    (Object.keys(categoryLabels) as CallResultCategory[]).map((category) => [
      category,
      definitions.filter(
        (definition) =>
          definition.category === category &&
          definition.is_active &&
          !definition.archived_at,
      ),
    ]),
  ) as Record<CallResultCategory, CallResultDefinition[]>;
}

function formatTimer(seconds: number) {
  const minutes = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const rest = (seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${rest}`;
}

const terminalCallStates = new Set<CallStatus>([
  "completed",
  "busy",
  "no_answer",
  "failed",
  "cancelled",
]);

const callStateLabels: Record<CallStatus, string> = {
  queued: "В очереди",
  initiated: "Инициализация",
  ringing: "Вызов клиента",
  active: "Разговор",
  on_hold: "На удержании",
  transfer_requested: "Запрошен перевод",
  transferring: "Перевод",
  transferred: "Передан оператору",
  completed: "Завершён",
  busy: "Занято",
  no_answer: "Нет ответа",
  failed: "Ошибка звонка",
  cancelled: "Отменён",
};

export function newestCall(
  current: Call | null | undefined,
  incoming: Call | null,
) {
  if (!current || !incoming) return incoming;
  return incoming.state_version >= current.state_version ? incoming : current;
}

function commandHeaders(call: Call | null | undefined, prefix: string) {
  return {
    "Idempotency-Key": idempotencyKey(prefix),
    ...(call ? { "X-Call-State-Version": String(call.state_version) } : {}),
  };
}

export function callElapsedSeconds(call: Call, now = Date.now()) {
  if (!call.answered_at) return 0;
  const end = call.ended_at ? new Date(call.ended_at).getTime() : now;
  return Math.max(
    0,
    Math.floor((end - new Date(call.answered_at).getTime()) / 1000),
  );
}

function useCallSeconds(call: Call | null | undefined) {
  const [seconds, setSeconds] = useState(call?.duration_seconds ?? 0);
  useEffect(() => {
    const update = () => {
      if (!call) return setSeconds(0);
      setSeconds(callElapsedSeconds(call));
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
    structuralSharing: (current, incoming) =>
      newestCall(current as Call | null | undefined, incoming as Call | null),
  });
  const callResults = useQuery({
    queryKey: ["call-results", "available", selectedProjectId],
    queryFn: () =>
      apiRequest<CallResultCatalog>(
        `/call-results/available?project_id=${selectedProjectId}`,
      ),
    enabled: Boolean(selectedProjectId),
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
    defaultValues: {
      result_definition_id: "",
      comment: "",
      callback_at: "",
      create_task: false,
      task_title: "",
      task_due_at: "",
      task_priority: "normal",
    },
  });
  const selectedResultId = resultForm.watch("result_definition_id");
  const selectedResult = callResults.data?.definitions.find(
    (definition) => definition.id === selectedResultId,
  );
  const createTask = resultForm.watch("create_task");
  const groupedResults = useMemo(
    () => groupCallResults(callResults.data?.definitions ?? []),
    [callResults.data],
  );
  const call = activeCall.data;
  const seconds = useCallSeconds(call);

  useEffect(() => {
    const available = callResults.data?.definitions ?? [];
    if (!available.length) return;
    if (!available.some((definition) => definition.id === selectedResultId)) {
      const defaultResult =
        available.find((definition) => definition.category === "successful") ??
        available[0];
      resultForm.setValue("result_definition_id", defaultResult.id);
    }
  }, [callResults.data, resultForm, selectedResultId]);

  useEffect(() => {
    if (!selectedResult?.creates_task) return;
    resultForm.setValue("create_task", true);
    if (!resultForm.getValues("task_title")) {
      resultForm.setValue(
        "task_title",
        `Задача: ${localizedResult(selectedResult, assignment.data?.customer.preferred_language)}`,
      );
    }
  }, [
    assignment.data?.customer.preferred_language,
    resultForm,
    selectedResult,
  ]);

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
        headers: { "Idempotency-Key": idempotencyKey("dialer-start") },
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
      apiRequest<Call>(`/calls/${call?.id}/answer`, {
        method: "POST",
        headers: commandHeaders(call, "dialer-answer"),
      }),
    onSuccess: (value) =>
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      ),
    onError: showError,
  });
  const hangupCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/hangup`, {
        method: "POST",
        headers: commandHeaders(call, "dialer-hangup"),
      }),
    onSuccess: (value) =>
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      ),
    onError: showError,
  });
  const holdCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/hold`, {
        method: "POST",
        headers: commandHeaders(call, "dialer-hold"),
      }),
    onSuccess: (value) =>
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      ),
    onError: showError,
  });
  const resumeCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/resume`, {
        method: "POST",
        headers: commandHeaders(call, "dialer-resume"),
      }),
    onSuccess: (value) =>
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      ),
    onError: showError,
  });
  const transferCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/transfer`, {
        method: "POST",
        headers: commandHeaders(call, "dialer-transfer"),
        body: JSON.stringify({
          destination: "operator-queue",
          reason: "operator_requested",
        }),
      }),
    onSuccess: (value) => {
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      );
      setMessage("Звонок передан в очередь операторов Mock-провайдера.");
    },
    onError: showError,
  });
  const saveResult = useMutation({
    mutationFn: (value: ResultForm) =>
      apiRequest<CallResultResponse>(`/calls/${call?.id}/result`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dialer-result") },
        body: JSON.stringify({
          result_definition_id: value.result_definition_id,
          comment: value.comment,
          callback_at:
            selectedResult?.requires_callback && value.callback_at
              ? new Date(value.callback_at).toISOString()
              : null,
          task:
            (selectedResult?.creates_task || value.create_task) &&
            value.task_due_at
              ? {
                  title:
                    value.task_title.trim() ||
                    `Задача: ${
                      selectedResult
                        ? localizedResult(
                            selectedResult,
                            assignment.data?.customer.preferred_language,
                          )
                        : "последующий контакт"
                    }`,
                  description: value.comment,
                  priority: value.task_priority,
                  due_at: new Date(value.task_due_at).toISOString(),
                }
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
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
      ]);
    },
    onError: showError,
  });

  function submitResult(value: ResultForm) {
    let valid = true;
    if (selectedResult?.requires_comment && !value.comment.trim()) {
      resultForm.setError("comment", {
        type: "required",
        message: "Для выбранного результата нужен комментарий",
      });
      valid = false;
    }
    if (selectedResult?.requires_callback_at && !value.callback_at) {
      resultForm.setError("callback_at", {
        type: "required",
        message: "Укажите дату и время перезвона",
      });
      valid = false;
    }
    if (
      (selectedResult?.creates_task || value.create_task) &&
      !value.task_due_at
    ) {
      resultForm.setError("task_due_at", {
        type: "required",
        message: "Укажите срок задачи",
      });
      valid = false;
    }
    if (valid) saveResult.mutate(value);
  }
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
    holdCall.isPending ||
    resumeCall.isPending ||
    transferCall.isPending ||
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
              {assignment.data.task && (
                <div className="dialer-task-source">
                  <CalendarClock size={17} />
                  <div>
                    <strong>{assignment.data.task.title}</strong>
                    <span>
                      {new Date(assignment.data.task.due_at).toLocaleString(
                        "ru-RU",
                      )}
                      {assignment.data.task.comment
                        ? ` · ${assignment.data.task.comment}`
                        : ""}
                    </span>
                  </div>
                </div>
              )}
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
                    <span>{callStateLabels[call.status]}</span>
                    <strong className="call-timer">
                      {formatTimer(seconds)}
                    </strong>
                    <small>{call.to_number}</small>
                    <small>
                      {call.provider} · версия {call.state_version}
                    </small>
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
                    {(call.status === "active" ||
                      call.status === "transferred") && (
                      <Button
                        disabled={isBusy}
                        onClick={() => holdCall.mutate()}
                        variant="secondary"
                      >
                        <PauseCircle size={17} />
                        Удержать
                      </Button>
                    )}
                    {call.status === "on_hold" && (
                      <Button
                        disabled={isBusy}
                        onClick={() => resumeCall.mutate()}
                        variant="secondary"
                      >
                        <PlayCircle size={17} />
                        Продолжить
                      </Button>
                    )}
                    {(call.status === "active" ||
                      call.status === "on_hold") && (
                      <Button
                        disabled={isBusy}
                        onClick={() => transferCall.mutate()}
                        variant="secondary"
                      >
                        <PhoneForwarded size={17} />
                        Перевести
                      </Button>
                    )}
                    {!terminalCallStates.has(call.status) &&
                      call.status !== "queued" && (
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
              {call && terminalCallStates.has(call.status) && (
                <form
                  className="call-result-form"
                  onSubmit={resultForm.handleSubmit(submitResult)}
                >
                  <div className="row-between">
                    <div>
                      <h3>Результат звонка</h3>
                      <p>Обязателен перед следующим клиентом</p>
                    </div>
                    <CheckCircle2 size={20} />
                  </div>
                  {callResults.isPending ? (
                    <p className="panel-subtitle">
                      Загружаем результаты проекта…
                    </p>
                  ) : callResults.isError ? (
                    <div className="dialer-message">
                      Не удалось загрузить каталог результатов.
                    </div>
                  ) : (
                    <div className="result-category-stack">
                      {(
                        Object.keys(categoryLabels) as CallResultCategory[]
                      ).map((category) => {
                        const items = groupedResults[category];
                        if (!items.length) return null;
                        return (
                          <fieldset
                            className={`result-category ${category}`}
                            key={category}
                          >
                            <legend>{categoryLabels[category]}</legend>
                            <div className="result-choice-grid">
                              {items.map((definition) => (
                                <label
                                  key={definition.id}
                                  style={
                                    {
                                      "--result-color": definition.color,
                                    } as CSSProperties
                                  }
                                >
                                  <input
                                    type="radio"
                                    value={definition.id}
                                    {...resultForm.register(
                                      "result_definition_id",
                                    )}
                                  />
                                  <span>
                                    {localizedResult(
                                      definition,
                                      assignment.data?.customer
                                        .preferred_language,
                                    )}
                                  </span>
                                </label>
                              ))}
                            </div>
                          </fieldset>
                        );
                      })}
                    </div>
                  )}
                  {resultForm.formState.errors.result_definition_id && (
                    <small className="field-error">
                      {resultForm.formState.errors.result_definition_id.message}
                    </small>
                  )}
                  {selectedResult?.requires_callback && (
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
                  <label className="dialer-create-task">
                    <input
                      disabled={selectedResult?.creates_task}
                      type="checkbox"
                      {...resultForm.register("create_task")}
                    />
                    <span>
                      <strong>Создать общую задачу</strong>
                      <small>
                        {selectedResult?.creates_task
                          ? "Обязательно для выбранного результата"
                          : "Последующий контакт или ручное действие"}
                      </small>
                    </span>
                  </label>
                  {(createTask || selectedResult?.creates_task) && (
                    <div className="dialer-task-fields">
                      <div className="field">
                        <label htmlFor="task-title">Название задачи</label>
                        <input
                          id="task-title"
                          placeholder="Что необходимо сделать"
                          {...resultForm.register("task_title")}
                        />
                      </div>
                      <div className="field">
                        <label htmlFor="task-due-at">Срок задачи</label>
                        <input
                          id="task-due-at"
                          min={new Date().toISOString().slice(0, 16)}
                          type="datetime-local"
                          {...resultForm.register("task_due_at")}
                        />
                        {resultForm.formState.errors.task_due_at && (
                          <small className="field-error">
                            {resultForm.formState.errors.task_due_at.message}
                          </small>
                        )}
                      </div>
                      <div className="field">
                        <label htmlFor="task-priority">Приоритет</label>
                        <select
                          id="task-priority"
                          {...resultForm.register("task_priority")}
                        >
                          <option value="low">Низкий</option>
                          <option value="normal">Обычный</option>
                          <option value="high">Высокий</option>
                          <option value="urgent">Срочный</option>
                        </select>
                      </div>
                    </div>
                  )}
                  <div className="field">
                    <label htmlFor="call-comment">
                      Комментарий
                      {selectedResult?.requires_comment ? " · обязательно" : ""}
                    </label>
                    <textarea
                      id="call-comment"
                      placeholder="Кратко зафиксируйте договорённости"
                      rows={3}
                      {...resultForm.register("comment")}
                    />
                    {resultForm.formState.errors.comment && (
                      <small className="field-error">
                        {resultForm.formState.errors.comment.message}
                      </small>
                    )}
                  </div>
                  <Button disabled={isBusy || !selectedResult} type="submit">
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
          {assignment.data &&
            (assignment.data.pending_tasks ?? []).length > 0 && (
              <section className="panel dialer-pending-tasks">
                <div className="row-between">
                  <h2>Задачи клиента</h2>
                  <ListTodo size={18} />
                </div>
                {(assignment.data.pending_tasks ?? []).map((task) => (
                  <div className="dialer-pending-task" key={task.id}>
                    <div>
                      <strong>{task.title}</strong>
                      <span>
                        {new Date(task.due_at).toLocaleString("ru-RU")}
                      </span>
                    </div>
                    <StatusBadge
                      tone={
                        task.task_type === "callback" ? "warning" : "primary"
                      }
                    >
                      {task.task_type === "callback" ? "Перезвон" : "Задача"}
                    </StatusBadge>
                  </div>
                ))}
              </section>
            )}
        </aside>
      </div>
    </>
  );
}
