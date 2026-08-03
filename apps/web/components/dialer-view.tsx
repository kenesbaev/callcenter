"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CalendarClock,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  ClipboardList,
  Mail,
  MapPin,
  MicOff,
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
  VolumeX,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, StatusBadge } from "@teamora/ui";
import { ApiClientError, apiRequest, idempotencyKey } from "@/lib/api";
import type {
  Call,
  CallState,
  CallStatus,
  CallResultCatalog,
  CallResultCategory,
  CallResultDefinition,
  CallResultResponse,
  DialerAssignment,
  DialerCompleteAndNextResponse,
  DialerFlowExecution,
  DialerHistoryItem,
  Page,
  Project,
  Task,
  TransferCandidate,
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

function localizedFlowValue(values: Record<string, string>, language: string) {
  return (
    values[language] ??
    values[language.split("-", 1)[0]] ??
    values.ru ??
    Object.values(values)[0] ??
    ""
  );
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

export function shouldHandleDialerShortcut(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return true;
  return !(
    target.closest("[role='dialog']") ||
    target.isContentEditable ||
    ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(target.tagName)
  );
}

export function DialerView() {
  const queryClient = useQueryClient();
  const [message, setMessage] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [selectedPhoneId, setSelectedPhoneId] = useState("");
  const [activeTab, setActiveTab] = useState<"script" | "history" | "tasks">(
    "script",
  );
  const [flowValue, setFlowValue] = useState("");
  const [transferDestination, setTransferDestination] = useState("");
  const [newTaskType, setNewTaskType] = useState<"manual" | "callback">(
    "manual",
  );
  const [newTaskTitle, setNewTaskTitle] = useState("");
  const [newTaskDueAt, setNewTaskDueAt] = useState("");
  const resultSectionRef = useRef<HTMLFormElement>(null);
  const completionKeyRef = useRef("");
  const saveAndNextRef = useRef(false);
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
    refetchInterval: (query) => {
      const value = query.state.data;
      return value && terminalCallStates.has(value.status) ? false : 5000;
    },
    structuralSharing: (current, incoming) =>
      newestCall(current as Call | null | undefined, incoming as Call | null),
  });
  const callState = useQuery({
    queryKey: ["dialer", "call-state", activeCall.data?.id],
    queryFn: () => apiRequest<CallState>(`/calls/${activeCall.data?.id}/state`),
    enabled: Boolean(activeCall.data?.id),
    refetchInterval: (query) => (query.state.data?.terminal ? false : 5000),
  });
  const flow = useQuery({
    queryKey: ["dialer", "flow", activeCall.data?.id],
    queryFn: () =>
      apiRequest<DialerFlowExecution | null>(
        `/dialer/flow/${activeCall.data?.id}`,
      ),
    enabled: Boolean(activeCall.data?.id),
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
      apiRequest<DialerHistoryItem[]>(
        `/dialer/history/details?customer_id=${assignment.data?.customer.id}`,
      ),
    enabled: Boolean(assignment.data?.customer.id),
  });
  const transferCandidates = useQuery({
    queryKey: ["team", "transfer-candidates", selectedProjectId],
    queryFn: () =>
      apiRequest<TransferCandidate[]>(
        `/team/transfer-candidates?project_id=${selectedProjectId}`,
      ),
    enabled: Boolean(selectedProjectId),
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
  const phones = (assignment.data?.customer.contacts ?? []).filter(
    (contact) => contact.kind === "phone",
  );
  const emails = (assignment.data?.customer.contacts ?? []).filter(
    (contact) => contact.kind === "email",
  );
  const allowedActions = new Set(callState.data?.allowed_actions ?? []);

  useEffect(() => {
    if (!assignment.data) {
      setSelectedPhoneId("");
      return;
    }
    const selectedIsAvailable = phones.some(
      (contact) => contact.id === selectedPhoneId,
    );
    if (!selectedIsAvailable) {
      setSelectedPhoneId(
        phones.find((contact) => contact.is_primary)?.id ?? phones[0]?.id ?? "",
      );
    }
  }, [assignment.data, phones, selectedPhoneId]);

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

  useEffect(() => {
    completionKeyRef.current = "";
  }, [call?.id]);

  function resultPayload(value: ResultForm) {
    return {
      result_definition_id: value.result_definition_id,
      comment: value.comment,
      callback_at:
        selectedResult?.requires_callback && value.callback_at
          ? new Date(value.callback_at).toISOString()
          : null,
      task:
        (selectedResult?.creates_task || value.create_task) && value.task_due_at
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
    };
  }

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
          customer_contact_id: selectedPhoneId || null,
          lock_token: assignment.data?.lock_token,
          callback_task_id: assignment.data?.callback_task_id,
          from_number: "MOCK",
        }),
      }),
    onSuccess: (value) => {
      queryClient.setQueryData(["dialer", "active-call"], value);
      void queryClient.invalidateQueries({ queryKey: ["team", "presence"] });
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
    onSuccess: (value) => {
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      );
      void queryClient.invalidateQueries({ queryKey: ["team", "presence"] });
    },
    onError: showError,
  });
  const hangupCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/hangup`, {
        method: "POST",
        headers: commandHeaders(call, "dialer-hangup"),
      }),
    onSuccess: (value) => {
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      );
      void queryClient.invalidateQueries({ queryKey: ["team", "presence"] });
    },
    onError: showError,
  });
  const holdCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/hold`, {
        method: "POST",
        headers: commandHeaders(call, "dialer-hold"),
      }),
    onSuccess: (value) => {
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      );
      void queryClient.invalidateQueries({ queryKey: ["team", "presence"] });
    },
    onError: showError,
  });
  const resumeCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/resume`, {
        method: "POST",
        headers: commandHeaders(call, "dialer-resume"),
      }),
    onSuccess: (value) => {
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      );
      void queryClient.invalidateQueries({ queryKey: ["team", "presence"] });
    },
    onError: showError,
  });
  const transferCall = useMutation({
    mutationFn: () =>
      apiRequest<Call>(`/calls/${call?.id}/transfer`, {
        method: "POST",
        headers: commandHeaders(call, "dialer-transfer"),
        body: JSON.stringify({
          destination: transferDestination,
          reason: "operator_requested",
        }),
      }),
    onSuccess: (value) => {
      queryClient.setQueryData<Call | null>(
        ["dialer", "active-call"],
        (current) => newestCall(current, value),
      );
      void queryClient.invalidateQueries({ queryKey: ["team", "presence"] });
      setMessage("Звонок передан в очередь операторов Mock-провайдера.");
    },
    onError: showError,
  });
  const saveResult = useMutation({
    mutationFn: (value: ResultForm) =>
      apiRequest<CallResultResponse>(`/calls/${call?.id}/result`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dialer-result") },
        body: JSON.stringify(resultPayload(value)),
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
        queryClient.invalidateQueries({ queryKey: ["analytics"] }),
        queryClient.invalidateQueries({ queryKey: ["team", "presence"] }),
      ]);
    },
    onError: showError,
  });

  const completeAndNext = useMutation({
    mutationFn: (value: ResultForm) => {
      if (!call || !assignment.data)
        throw new Error("Нет активного назначения");
      completionKeyRef.current ||= idempotencyKey("dialer-complete-next");
      return apiRequest<DialerCompleteAndNextResponse>(
        "/dialer/complete-and-next",
        {
          method: "POST",
          headers: { "Idempotency-Key": completionKeyRef.current },
          body: JSON.stringify({
            call_id: call.id,
            customer_id: assignment.data.customer.id,
            lock_token: assignment.data.lock_token,
            expected_state_version: call.state_version,
            flow_execution_state_version: flow.data?.state_version ?? null,
            project_id: call.project_id,
            result: resultPayload(value),
          }),
        },
      );
    },
    onSuccess: async (value) => {
      queryClient.setQueryData(["dialer", "active-call"], null);
      queryClient.setQueryData(["dialer", "current"], value.next_assignment);
      queryClient.removeQueries({ queryKey: ["dialer", "call-state"] });
      queryClient.removeQueries({ queryKey: ["dialer", "flow"] });
      resultForm.reset();
      setFlowValue("");
      setMessage(
        value.queue_complete
          ? "Результат сохранён. Очередь завершена."
          : "Результат сохранён. Следующий клиент уже назначен.",
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["calls"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
        queryClient.invalidateQueries({ queryKey: ["dashboard"] }),
        queryClient.invalidateQueries({ queryKey: ["analytics"] }),
        queryClient.invalidateQueries({ queryKey: ["team", "presence"] }),
      ]);
    },
    onError: showError,
    onSettled: () => {
      saveAndNextRef.current = false;
    },
  });

  const stepFlow = useMutation({
    mutationFn: (answerKey?: string) => {
      if (!call || !flow.data?.current_node)
        throw new Error("Сценарий недоступен");
      return apiRequest<DialerFlowExecution>(`/dialer/flow/${call.id}/steps`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dialer-flow-step") },
        body: JSON.stringify({
          node_id: flow.data.current_node.id,
          expected_state_version: flow.data.state_version,
          answer_key: answerKey ?? null,
          value: flowValue || null,
          confirm_action: [
            "update_customer_field",
            "create_task",
            "create_callback",
            "transfer_request",
          ].includes(flow.data.current_node.node_type),
          language_code: flow.data.language_code,
        }),
      });
    },
    onSuccess: (value) => {
      queryClient.setQueryData(["dialer", "flow", call?.id], value);
      setFlowValue("");
      void queryClient.invalidateQueries({ queryKey: ["tasks"] });
      void queryClient.invalidateQueries({ queryKey: ["dialer", "current"] });
    },
    onError: showError,
  });
  const backFlow = useMutation({
    mutationFn: () =>
      apiRequest<DialerFlowExecution>(`/dialer/flow/${call?.id}/back`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dialer-flow-back") },
        body: JSON.stringify({
          expected_state_version: flow.data?.state_version,
        }),
      }),
    onSuccess: (value) =>
      queryClient.setQueryData(["dialer", "flow", call?.id], value),
    onError: showError,
  });
  const createDialerTask = useMutation({
    mutationFn: () => {
      if (!assignment.data) throw new Error("Клиент не назначен");
      return apiRequest<Task>("/tasks", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dialer-task") },
        body: JSON.stringify({
          project_id: assignment.data.customer.project_id,
          customer_id: assignment.data.customer.id,
          task_type: newTaskType,
          title: newTaskTitle,
          description: "",
          priority: "normal",
          call_id: call?.id ?? null,
          due_at: new Date(newTaskDueAt).toISOString(),
          comment: "Создано из Dialer",
        }),
      });
    },
    onSuccess: async () => {
      setNewTaskTitle("");
      setNewTaskDueAt("");
      setMessage(
        newTaskType === "callback" ? "Перезвон создан." : "Задача создана.",
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dialer", "current"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      ]);
    },
    onError: showError,
  });
  const completeDialerTask = useMutation({
    mutationFn: (taskId: string) =>
      apiRequest<Task>(`/tasks/${taskId}/complete`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("dialer-task-complete") },
      }),
    onSuccess: async () => {
      setMessage("Задача завершена.");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["dialer", "current"] }),
        queryClient.invalidateQueries({ queryKey: ["tasks"] }),
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
    if (!valid) return;
    if (saveAndNextRef.current) completeAndNext.mutate(value);
    else saveResult.mutate(value);
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

  useEffect(() => {
    if (!call) return;
    void queryClient.invalidateQueries({
      queryKey: ["dialer", "call-state", call.id],
    });
  }, [call, queryClient]);

  useEffect(() => {
    if (!assignment.data && !call && !resultForm.formState.isDirty) return;
    const warnBeforeExit = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warnBeforeExit);
    return () => window.removeEventListener("beforeunload", warnBeforeExit);
  }, [assignment.data, call, resultForm.formState.isDirty]);

  useEffect(() => {
    const onShortcut = (event: KeyboardEvent) => {
      if (!shouldHandleDialerShortcut(event.target)) return;
      if (event.altKey && event.code === "KeyC" && assignment.data && !call) {
        event.preventDefault();
        startCall.mutate();
      } else if (
        event.altKey &&
        event.code === "KeyH" &&
        call &&
        !callState.data?.terminal
      ) {
        event.preventDefault();
        hangupCall.mutate();
      } else if (event.altKey && event.code === "KeyP" && call) {
        event.preventDefault();
        if (allowedActions.has("hold")) holdCall.mutate();
        else if (allowedActions.has("resume")) resumeCall.mutate();
      } else if (event.altKey && event.code === "KeyR") {
        event.preventDefault();
        resultSectionRef.current?.scrollIntoView({ behavior: "smooth" });
      } else if (
        event.ctrlKey &&
        event.code === "Enter" &&
        callState.data?.terminal
      ) {
        event.preventDefault();
        saveAndNextRef.current = event.shiftKey;
        void resultForm.handleSubmit(submitResult)();
      }
    };
    window.addEventListener("keydown", onShortcut);
    return () => window.removeEventListener("keydown", onShortcut);
  });

  const customerHistory = useMemo(
    () => (history.data ?? []).slice(0, 10),
    [history.data],
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
    completeAndNext.isPending ||
    stepFlow.isPending ||
    backFlow.isPending ||
    createDialerTask.isPending ||
    completeDialerTask.isPending ||
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
                      {phones.find((contact) => contact.id === selectedPhoneId)
                        ?.value ??
                        primaryPhone(assignment.data) ??
                        "Телефон отсутствует"}
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
                  {assignment.data.source === "callback"
                    ? "Перезвон"
                    : assignment.data.source === "retry"
                      ? "Повторная попытка"
                      : "Новый"}
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
              <div className="dialer-contact-picker">
                <label className="field">
                  <span>Номер для звонка</span>
                  <select
                    disabled={Boolean(call)}
                    onChange={(event) => setSelectedPhoneId(event.target.value)}
                    value={selectedPhoneId}
                  >
                    {phones.map((contact) => (
                      <option key={contact.id} value={contact.id}>
                        {contact.value}
                        {contact.is_primary ? " · основной" : ""}
                        {contact.label ? ` · ${contact.label}` : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="dialer-contact-summary">
                  <Mail size={16} />
                  <span>
                    {emails.find((contact) => contact.is_primary)?.value ??
                      emails[0]?.value ??
                      "E-mail не указан"}
                  </span>
                </div>
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
                <div>
                  <span>Город / регион</span>
                  <strong>
                    {[
                      assignment.data.customer.city,
                      assignment.data.customer.region,
                    ]
                      .filter(Boolean)
                      .join(", ") || "—"}
                  </strong>
                </div>
                <div>
                  <span>Организация</span>
                  <strong>
                    {assignment.data.customer.organization ?? "—"}
                  </strong>
                </div>
                <div>
                  <span>Ответственный</span>
                  <strong>
                    {assignment.data.customer.assigned_user_id
                      ? "Назначен"
                      : "Не назначен"}
                  </strong>
                </div>
              </div>
              <div className="dialer-customer-details">
                <div>
                  <MapPin size={16} />
                  <span>
                    {assignment.data.customer.address ?? "Адрес не указан"}
                  </span>
                </div>
                <div>
                  <UserRound size={16} />
                  <span>
                    {[
                      assignment.data.customer.job_title,
                      assignment.data.customer.organization,
                    ]
                      .filter(Boolean)
                      .join(" · ") || "Должность не указана"}
                  </span>
                </div>
                {assignment.data.customer.tags.length > 0 && (
                  <div className="dialer-tags">
                    {assignment.data.customer.tags.map((tag) => (
                      <StatusBadge key={tag}>{tag}</StatusBadge>
                    ))}
                  </div>
                )}
                {assignment.data.customer.description && (
                  <p>{assignment.data.customer.description}</p>
                )}
                {Object.keys(assignment.data.customer.custom_fields).length >
                  0 && (
                  <dl className="dialer-custom-fields">
                    {Object.entries(assignment.data.customer.custom_fields).map(
                      ([key, value]) => (
                        <div key={key}>
                          <dt>{key}</dt>
                          <dd>
                            {Array.isArray(value)
                              ? value.join(", ")
                              : String(value)}
                          </dd>
                        </div>
                      ),
                    )}
                  </dl>
                )}
              </div>
              {!call && (
                <div className="dialer-primary-actions">
                  <Button
                    disabled={isBusy || !selectedPhoneId}
                    onClick={() => startCall.mutate()}
                    title="Alt+C"
                  >
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
                    {allowedActions.has("answer") && (
                      <Button
                        disabled={isBusy}
                        onClick={() => answerCall.mutate()}
                      >
                        <Phone size={17} />
                        Имитировать ответ
                      </Button>
                    )}
                    {allowedActions.has("hold") && (
                      <Button
                        disabled={isBusy}
                        onClick={() => holdCall.mutate()}
                        variant="secondary"
                      >
                        <PauseCircle size={17} />
                        Удержать
                      </Button>
                    )}
                    {allowedActions.has("resume") && (
                      <Button
                        disabled={isBusy}
                        onClick={() => resumeCall.mutate()}
                        variant="secondary"
                      >
                        <PlayCircle size={17} />
                        Продолжить
                      </Button>
                    )}
                    {allowedActions.has("transfer") && (
                      <div className="dialer-transfer-control">
                        <select
                          aria-label="Оператор для перевода"
                          onChange={(event) =>
                            setTransferDestination(event.target.value)
                          }
                          value={transferDestination}
                        >
                          <option value="">
                            Выберите доступного оператора
                          </option>
                          {transferCandidates.data?.map((operator) => (
                            <option
                              key={operator.user_id}
                              value={operator.user_id}
                            >
                              {operator.display_name}
                              {operator.extension
                                ? ` · ${operator.extension}`
                                : ""}
                              {` · Доступен`}
                            </option>
                          ))}
                        </select>
                        <Button
                          disabled={isBusy || !transferDestination}
                          onClick={() => transferCall.mutate()}
                          variant="secondary"
                        >
                          <PhoneForwarded size={17} />
                          Перевести
                        </Button>
                      </div>
                    )}
                    {allowedActions.has("hangup") && (
                      <button
                        aria-label="Завершить звонок"
                        className="hangup-button"
                        disabled={isBusy}
                        onClick={() => hangupCall.mutate()}
                        type="button"
                        title="Alt+H"
                      >
                        <PhoneOff size={20} />
                      </button>
                    )}
                    <button
                      className="mock-media-button"
                      disabled
                      title="Микрофон будет доступен после подключения WebRTC"
                      type="button"
                    >
                      <MicOff size={16} />
                    </button>
                    <button
                      className="mock-media-button"
                      disabled
                      title="Громкость будет доступна после подключения WebRTC"
                      type="button"
                    >
                      <VolumeX size={16} />
                    </button>
                  </div>
                </div>
              )}
              {call && terminalCallStates.has(call.status) && (
                <form
                  className="call-result-form"
                  onSubmit={resultForm.handleSubmit(submitResult)}
                  ref={resultSectionRef}
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
                  {selectedResult?.do_not_call && (
                    <div className="dialer-warning">
                      <CircleAlert size={17} />
                      После сохранения клиент будет исключён из очереди звонков.
                    </div>
                  )}
                  <div className="dialer-result-actions">
                    <Button
                      disabled={isBusy || !selectedResult}
                      onClick={() => {
                        saveAndNextRef.current = false;
                      }}
                      type="submit"
                      variant="secondary"
                    >
                      {saveResult.isPending ? "Сохраняем…" : "Сохранить"}
                    </Button>
                    <Button
                      disabled={isBusy || !selectedResult}
                      onClick={() => {
                        saveAndNextRef.current = true;
                      }}
                      type="submit"
                      title="Ctrl+Shift+Enter"
                    >
                      {completeAndNext.isPending
                        ? "Сохраняем…"
                        : "Сохранить и следующий"}
                      <ChevronRight size={16} />
                    </Button>
                  </div>
                </form>
              )}
            </>
          )}
          {message && <div className="dialer-message">{message}</div>}
        </section>
        <aside className="dialer-side-stack">
          <section className="panel dialer-runtime-panel">
            <div
              className="dialer-tabs"
              role="tablist"
              aria-label="Рабочие вкладки"
            >
              <button
                aria-selected={activeTab === "script"}
                className={activeTab === "script" ? "active" : ""}
                onClick={() => setActiveTab("script")}
                role="tab"
                type="button"
              >
                <ClipboardList size={15} /> Сценарий
              </button>
              <button
                aria-selected={activeTab === "history"}
                className={activeTab === "history" ? "active" : ""}
                onClick={() => setActiveTab("history")}
                role="tab"
                type="button"
              >
                <History size={15} /> История
              </button>
              <button
                aria-selected={activeTab === "tasks"}
                className={activeTab === "tasks" ? "active" : ""}
                onClick={() => setActiveTab("tasks")}
                role="tab"
                type="button"
              >
                <ListTodo size={15} /> Задачи
              </button>
            </div>

            {activeTab === "script" && (
              <div className="dialer-tab-panel" role="tabpanel">
                {!call ? (
                  <div className="dialer-tab-empty">
                    Сценарий откроется после начала звонка.
                  </div>
                ) : flow.isPending ? (
                  <div className="dialer-flow-skeleton skeleton">
                    Загрузка сценария
                  </div>
                ) : flow.isError ? (
                  <div className="dialer-tab-empty error-state">
                    Не удалось восстановить сценарий.
                  </div>
                ) : !flow.data ? (
                  <div className="dialer-tab-empty">
                    Для проекта нет опубликованного сценария. Звонок можно
                    продолжить без него.
                  </div>
                ) : (
                  <>
                    <div className="row-between dialer-flow-meta">
                      <StatusBadge
                        tone={
                          flow.data.status === "completed"
                            ? "success"
                            : "primary"
                        }
                      >
                        {flow.data.status === "completed"
                          ? "Завершён"
                          : "В работе"}
                      </StatusBadge>
                      <select
                        aria-label="Язык сценария"
                        disabled={flow.data.status === "completed"}
                        onChange={(event) =>
                          queryClient.setQueryData<DialerFlowExecution>(
                            ["dialer", "flow", call.id],
                            (current) =>
                              current
                                ? {
                                    ...current,
                                    language_code: event.target.value,
                                  }
                                : current,
                          )
                        }
                        value={flow.data.language_code}
                      >
                        {flow.data.language_codes.map((language) => (
                          <option key={language} value={language}>
                            {language.toUpperCase()}
                          </option>
                        ))}
                      </select>
                    </div>
                    {flow.data.current_node ? (
                      <div className="dialer-flow-node">
                        <span className="eyebrow">
                          {flow.data.current_node.name}
                        </span>
                        <h3>
                          {localizedFlowValue(
                            flow.data.current_node.text_by_language,
                            flow.data.language_code,
                          ) || "Выполните следующий шаг"}
                        </h3>
                        {localizedFlowValue(
                          flow.data.current_node.hint_by_language,
                          flow.data.language_code,
                        ) && (
                          <p className="dialer-flow-hint">
                            {localizedFlowValue(
                              flow.data.current_node.hint_by_language,
                              flow.data.language_code,
                            )}
                          </p>
                        )}
                        {flow.data.current_node.node_type === "value_input" && (
                          <label className="field">
                            <span>Ответ клиента</span>
                            <input
                              onChange={(event) =>
                                setFlowValue(event.target.value)
                              }
                              value={flowValue}
                            />
                          </label>
                        )}
                        {flow.data.current_node.answers.length > 0 ? (
                          <div className="dialer-flow-answers">
                            {flow.data.current_node.answers.map((answer) => (
                              <Button
                                disabled={stepFlow.isPending}
                                key={answer.id}
                                onClick={() => stepFlow.mutate(answer.key)}
                                variant="secondary"
                              >
                                {localizedFlowValue(
                                  answer.label_by_language,
                                  flow.data?.language_code ?? "ru",
                                ) || answer.key}
                                <ChevronRight size={15} />
                              </Button>
                            ))}
                          </div>
                        ) : (
                          <Button
                            disabled={stepFlow.isPending}
                            onClick={() => stepFlow.mutate(undefined)}
                          >
                            {[
                              "update_customer_field",
                              "create_task",
                              "create_callback",
                              "transfer_request",
                            ].includes(flow.data.current_node.node_type)
                              ? "Подтвердить действие"
                              : flow.data.current_node.node_type === "end"
                                ? "Завершить сценарий"
                                : "Далее"}
                            <ChevronRight size={15} />
                          </Button>
                        )}
                        <Button
                          disabled={
                            !flow.data.steps.length || backFlow.isPending
                          }
                          onClick={() => backFlow.mutate()}
                          variant="secondary"
                        >
                          <ChevronLeft size={15} /> Назад
                        </Button>
                      </div>
                    ) : (
                      <div className="dialer-flow-complete">
                        <CheckCircle2 size={22} /> Сценарий завершён
                      </div>
                    )}
                    {flow.data.steps.length > 0 && (
                      <div className="dialer-flow-path">
                        <strong>Пройденный путь</strong>
                        {flow.data.steps.map((step) => (
                          <span key={step.id}>
                            {step.sequence}.{" "}
                            {step.text_snapshot || step.system_key}
                            {step.selected_answer_label
                              ? ` → ${step.selected_answer_label}`
                              : ""}
                          </span>
                        ))}
                      </div>
                    )}
                  </>
                )}
              </div>
            )}

            {activeTab === "history" && (
              <div className="dialer-tab-panel compact-history" role="tabpanel">
                {history.isPending ? (
                  <div className="skeleton">Загрузка истории</div>
                ) : customerHistory.length === 0 ? (
                  <div className="dialer-tab-empty">
                    Предыдущих звонков нет.
                  </div>
                ) : (
                  customerHistory.map((item) => (
                    <article className="dialer-history-card" key={item.call.id}>
                      <div className="row-between">
                        <strong>
                          {item.result_label ??
                            callStateLabels[item.call.status]}
                        </strong>
                        <span>{formatTimer(item.call.duration_seconds)}</span>
                      </div>
                      <small>
                        {item.call.started_at
                          ? new Date(item.call.started_at).toLocaleString(
                              "ru-RU",
                            )
                          : "Дата не указана"}
                        {item.operator_name ? ` · ${item.operator_name}` : ""}
                      </small>
                      {item.comment && <p>{item.comment}</p>}
                      {item.summary && (
                        <p className="dialer-summary">
                          AI summary: {item.summary}
                        </p>
                      )}
                    </article>
                  ))
                )}
              </div>
            )}

            {activeTab === "tasks" && (
              <div className="dialer-tab-panel" role="tabpanel">
                {(assignment.data?.pending_tasks ?? []).length === 0 ? (
                  <div className="dialer-tab-empty">Активных задач нет.</div>
                ) : (
                  <div className="dialer-task-list">
                    {assignment.data?.pending_tasks.map((task) => (
                      <article key={task.id}>
                        <div>
                          <strong>{task.title}</strong>
                          <span>
                            {new Date(task.due_at).toLocaleString("ru-RU")}
                          </span>
                          {task.comment && <p>{task.comment}</p>}
                        </div>
                        <Button
                          disabled={
                            task.status !== "in_progress" ||
                            completeDialerTask.isPending
                          }
                          onClick={() => completeDialerTask.mutate(task.id)}
                          variant="secondary"
                        >
                          Завершить
                        </Button>
                      </article>
                    ))}
                  </div>
                )}
                {assignment.data && (
                  <div className="dialer-quick-task">
                    <h3>Новая задача</h3>
                    <div className="field">
                      <label htmlFor="dialer-task-type">Тип</label>
                      <select
                        id="dialer-task-type"
                        onChange={(event) =>
                          setNewTaskType(
                            event.target.value as "manual" | "callback",
                          )
                        }
                        value={newTaskType}
                      >
                        <option value="manual">Обычная задача</option>
                        <option value="callback">Перезвон</option>
                      </select>
                    </div>
                    <div className="field">
                      <label htmlFor="dialer-task-title">Название</label>
                      <input
                        id="dialer-task-title"
                        onChange={(event) =>
                          setNewTaskTitle(event.target.value)
                        }
                        value={newTaskTitle}
                      />
                    </div>
                    <div className="field">
                      <label htmlFor="dialer-task-date">Дата и время</label>
                      <input
                        id="dialer-task-date"
                        min={new Date().toISOString().slice(0, 16)}
                        onChange={(event) =>
                          setNewTaskDueAt(event.target.value)
                        }
                        type="datetime-local"
                        value={newTaskDueAt}
                      />
                    </div>
                    <Button
                      disabled={
                        createDialerTask.isPending ||
                        newTaskTitle.trim().length < 2 ||
                        !newTaskDueAt
                      }
                      onClick={() => createDialerTask.mutate()}
                    >
                      {newTaskType === "callback"
                        ? "Создать перезвон"
                        : "Создать задачу"}
                    </Button>
                  </div>
                )}
              </div>
            )}
          </section>
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
              customerHistory.slice(0, 3).map((item) => (
                <div className="history-row" key={item.call.id}>
                  <div>
                    <strong>
                      {item.result_label ?? callStateLabels[item.call.status]}
                    </strong>
                    <span>
                      {item.call.started_at
                        ? new Date(item.call.started_at).toLocaleString("ru-RU")
                        : "—"}
                    </span>
                  </div>
                  <span>{formatTimer(item.call.duration_seconds)}</span>
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
