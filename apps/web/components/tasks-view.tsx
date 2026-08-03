"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlarmClock,
  ArrowRight,
  CheckCircle2,
  CirclePlay,
  Clock3,
  History,
  ListChecks,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  X,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, StatusBadge } from "@teamora/ui";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { ApiClientError, apiRequest, idempotencyKey } from "@/lib/api";
import { useRealtime } from "@/components/realtime-provider";
import type {
  AuthResponse,
  Page,
  Project,
  Task,
  TaskEvent,
  TaskOptions,
  TaskPriority,
  TaskStatus,
  TaskType,
} from "@/lib/types";

const taskSchema = z.object({
  project_id: z.string().uuid("Выберите проект"),
  customer_id: z.string().uuid("Выберите клиента"),
  task_type: z.enum(["callback", "follow_up", "manual", "system"]),
  title: z.string().trim().min(2, "Введите название").max(240),
  description: z.string().trim().max(8000),
  priority: z.enum(["low", "normal", "high", "urgent"]),
  assigned_user_id: z.string(),
  due_at: z.string().min(1, "Укажите дату и время"),
  comment: z.string().trim().max(4000),
});

type TaskForm = z.infer<typeof taskSchema>;
type TaskPeriod = "" | "overdue" | "today" | "future" | "completed";

const tabs: Array<{ value: TaskPeriod; label: string }> = [
  { value: "", label: "Все" },
  { value: "overdue", label: "Просроченные" },
  { value: "today", label: "Сегодня" },
  { value: "future", label: "Будущие" },
  { value: "completed", label: "Завершённые" },
];

const typeLabels: Record<TaskType, string> = {
  callback: "Перезвон",
  follow_up: "Последующий контакт",
  manual: "Ручная",
  system: "Системная",
};

const statusLabels: Record<TaskStatus, string> = {
  pending: "Ожидает",
  in_progress: "В работе",
  completed: "Завершена",
  cancelled: "Отменена",
};

const priorityLabels: Record<TaskPriority, string> = {
  low: "Низкий",
  normal: "Обычный",
  high: "Высокий",
  urgent: "Срочный",
};

const eventLabels: Record<TaskEvent["event_type"], string> = {
  created: "Задача создана",
  assigned: "Назначен ответственный",
  reassigned: "Ответственный изменён",
  started: "Задача начата",
  rescheduled: "Срок перенесён",
  completed: "Задача завершена",
  cancelled: "Задача отменена",
  restored: "Задача восстановлена",
  priority_changed: "Приоритет изменён",
  comment_added: "Комментарий обновлён",
  updated: "Задача обновлена",
};

function statusTone(task: Task) {
  if (task.status === "completed") return "success" as const;
  if (task.status === "cancelled") return "neutral" as const;
  if (task.is_overdue) return "danger" as const;
  if (task.status === "in_progress") return "warning" as const;
  return "primary" as const;
}

function projectDate(value: string, timezone: string): string {
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: timezone,
    }).format(new Date(value));
  } catch {
    return new Date(value).toLocaleString("ru-RU");
  }
}

function localInputForZone(value: string, timezone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: timezone,
  }).formatToParts(new Date(value));
  const pick = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "00";
  return `${pick("year")}-${pick("month")}-${pick("day")}T${pick("hour")}:${pick("minute")}`;
}

function zonedInputToIso(value: string, timezone: string): string {
  const [datePart, timePart] = value.split("T");
  const [year, month, day] = datePart.split("-").map(Number);
  const [hour, minute] = timePart.split(":").map(Number);
  const guess = Date.UTC(year, month - 1, day, hour, minute);
  const parts = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
    timeZone: timezone,
  }).formatToParts(new Date(guess));
  const pick = (type: Intl.DateTimeFormatPartTypes) =>
    Number(parts.find((part) => part.type === type)?.value ?? 0);
  const rendered = Date.UTC(
    pick("year"),
    pick("month") - 1,
    pick("day"),
    pick("hour"),
    pick("minute"),
    pick("second"),
  );
  return new Date(guess - (rendered - guess)).toISOString();
}

function blankForm(project: Project | undefined): TaskForm {
  const timezone = project?.effective_settings.timezone ?? "Asia/Tashkent";
  return {
    project_id: project?.id ?? "",
    customer_id: "",
    task_type: "manual",
    title: "",
    description: "",
    priority: "normal",
    assigned_user_id: "",
    due_at: localInputForZone(
      new Date(Date.now() + 60 * 60 * 1000).toISOString(),
      timezone,
    ),
    comment: "",
  };
}

function taskForm(task: Task): TaskForm {
  return {
    project_id: task.project_id,
    customer_id: task.customer_id,
    task_type: task.task_type,
    title: task.title,
    description: task.description,
    priority: task.priority,
    assigned_user_id: task.assigned_user_id ?? "",
    due_at: localInputForZone(task.due_at, task.project_timezone),
    comment: task.comment,
  };
}

export function canManageAllTasks(
  role: AuthResponse["user"]["role"] | undefined,
) {
  return role === "tenant_owner" || role === "tenant_manager";
}

export function canCreateTasks(role: AuthResponse["user"]["role"] | undefined) {
  return canManageAllTasks(role) || role === "human_operator";
}

export function TasksView() {
  const realtime = useRealtime();
  const queryClient = useQueryClient();
  const [period, setPeriod] = useState<TaskPeriod>("");
  const [projectId, setProjectId] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [operatorFilter, setOperatorFilter] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<Task | "new" | null>(null);
  const [selected, setSelected] = useState<Task | null>(null);
  const [message, setMessage] = useState("");
  const limit = 20;

  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
  });
  const projects = useQuery({
    queryKey: ["projects", "task-filter"],
    queryFn: () => apiRequest<Page<Project>>("/projects?limit=100"),
  });
  useEffect(() => {
    if (!projectId && projects.data?.items[0]) {
      setProjectId(projects.data.items[0].id);
    }
  }, [projectId, projects.data]);

  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(page * limit),
  });
  if (period) params.set("period", period);
  if (projectId) params.set("project_id", projectId);
  if (typeFilter) params.set("task_type", typeFilter);
  if (statusFilter) params.set("status", statusFilter);
  if (priorityFilter) params.set("priority", priorityFilter);
  if (operatorFilter) params.set("assigned_user_id", operatorFilter);
  if (search.trim()) params.set("search", search.trim());

  const tasks = useQuery({
    queryKey: [
      "tasks",
      period,
      projectId,
      typeFilter,
      statusFilter,
      priorityFilter,
      operatorFilter,
      search,
      page,
    ],
    queryFn: () => apiRequest<Page<Task>>(`/tasks?${params.toString()}`),
    enabled: Boolean(projectId),
    refetchInterval: realtime.connected ? 120_000 : 30_000,
  });

  const activeProject = projects.data?.items.find(
    (item) => item.id === projectId,
  );
  const form = useForm<TaskForm>({
    resolver: zodResolver(taskSchema),
    defaultValues: blankForm(activeProject),
  });
  const formProjectId = form.watch("project_id");
  const formProject = projects.data?.items.find(
    (item) => item.id === formProjectId,
  );
  const options = useQuery({
    queryKey: ["tasks", "options", formProjectId],
    queryFn: () =>
      apiRequest<TaskOptions>(`/tasks/options?project_id=${formProjectId}`),
    enabled: Boolean(formProjectId && editing),
  });
  const filterOptions = useQuery({
    queryKey: ["tasks", "options", projectId],
    queryFn: () =>
      apiRequest<TaskOptions>(`/tasks/options?project_id=${projectId}`),
    enabled: Boolean(projectId),
  });
  const events = useQuery({
    queryKey: ["tasks", selected?.id, "events"],
    queryFn: () => apiRequest<TaskEvent[]>(`/tasks/${selected?.id}/events`),
    enabled: Boolean(selected),
  });

  function openCreate() {
    form.reset(blankForm(activeProject));
    setEditing("new");
    setMessage("");
  }

  function openEdit(task: Task) {
    form.reset(taskForm(task));
    setEditing(task);
    setMessage("");
  }

  function errorMessage(error: unknown) {
    setMessage(
      error instanceof ApiClientError
        ? error.message
        : "Не удалось выполнить действие",
    );
  }

  async function invalidateTasks() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["tasks"] }),
      queryClient.invalidateQueries({ queryKey: ["callbacks"] }),
      queryClient.invalidateQueries({ queryKey: ["dialer"] }),
      queryClient.invalidateQueries({ queryKey: ["customers"] }),
    ]);
  }

  const save = useMutation({
    mutationFn: async (value: TaskForm) => {
      const timezone =
        formProject?.effective_settings.timezone ?? "Asia/Tashkent";
      const dueAt = zonedInputToIso(value.due_at, timezone);
      if (editing === "new") {
        return apiRequest<Task>("/tasks", {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey("task-create") },
          body: JSON.stringify({
            ...value,
            assigned_user_id: value.assigned_user_id || null,
            due_at: dueAt,
          }),
        });
      }
      if (!editing) throw new Error("Task editor is closed");
      let updated = await apiRequest<Task>(`/tasks/${editing.id}`, {
        method: "PATCH",
        headers: { "Idempotency-Key": idempotencyKey("task-update") },
        body: JSON.stringify({
          title: value.title,
          description: value.description,
          priority: value.priority,
          comment: value.comment,
        }),
      });
      if (dueAt !== new Date(editing.due_at).toISOString()) {
        updated = await apiRequest<Task>(`/tasks/${editing.id}/reschedule`, {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey("task-reschedule") },
          body: JSON.stringify({ due_at: dueAt }),
        });
      }
      if (
        canManageAllTasks(me.data?.user.role) &&
        (value.assigned_user_id || null) !== editing.assigned_user_id
      ) {
        updated = await apiRequest<Task>(`/tasks/${editing.id}/reassign`, {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey("task-reassign") },
          body: JSON.stringify({
            assigned_user_id: value.assigned_user_id || null,
          }),
        });
      }
      return updated;
    },
    onSuccess: async (task) => {
      setEditing(null);
      setSelected(task);
      setMessage("Задача сохранена");
      await invalidateTasks();
    },
    onError: errorMessage,
  });

  const action = useMutation({
    mutationFn: async ({ task, action }: { task: Task; action: string }) => {
      let body: string | undefined;
      if (action === "cancel") {
        const reason = window.prompt("Укажите причину отмены");
        if (!reason) throw new Error("Отмена действия");
        body = JSON.stringify({ reason });
      }
      return apiRequest<Task>(`/tasks/${task.id}/${action}`, {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey(`task-${action}`) },
        body,
      });
    },
    onSuccess: async (task) => {
      setSelected(task);
      setMessage("Статус задачи обновлён");
      await invalidateTasks();
    },
    onError: (error) => {
      if (error instanceof Error && error.message === "Отмена действия") return;
      errorMessage(error);
    },
  });

  const groupedCounts = useMemo(() => {
    const values = { overdue: 0, active: 0, completed: 0 };
    for (const task of tasks.data?.items ?? []) {
      if (task.is_overdue) values.overdue += 1;
      if (task.status === "completed") values.completed += 1;
      if (task.status === "pending" || task.status === "in_progress")
        values.active += 1;
    }
    return values;
  }, [tasks.data]);

  return (
    <>
      <div className="page-heading row-between tasks-heading">
        <div>
          <h1>Задачи</h1>
          <p>Единая очередь перезвонов, ручных и системных действий</p>
        </div>
        {canCreateTasks(me.data?.user.role) && (
          <Button onClick={openCreate}>
            <Plus size={16} /> Создать задачу
          </Button>
        )}
      </div>

      <section className="task-kpi-grid">
        <article className="panel task-kpi">
          <ListChecks size={20} />
          <div>
            <span>В выборке</span>
            <strong>{tasks.data?.total ?? 0}</strong>
          </div>
        </article>
        <article className="panel task-kpi warning">
          <AlarmClock size={20} />
          <div>
            <span>Просрочено на странице</span>
            <strong>{groupedCounts.overdue}</strong>
          </div>
        </article>
        <article className="panel task-kpi success">
          <CheckCircle2 size={20} />
          <div>
            <span>Завершено на странице</span>
            <strong>{groupedCounts.completed}</strong>
          </div>
        </article>
      </section>

      <section className="panel tasks-panel">
        <div className="filter-tabs" role="tablist" aria-label="Период задач">
          {tabs.map((tab) => (
            <button
              aria-selected={period === tab.value}
              className={period === tab.value ? "active" : ""}
              key={tab.value}
              onClick={() => {
                setPeriod(tab.value);
                setPage(0);
              }}
              role="tab"
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="task-filter-grid">
          <label className="search-control">
            <Search size={16} />
            <input
              aria-label="Поиск задач"
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(0);
              }}
              placeholder="Название, клиент или комментарий"
              value={search}
            />
          </label>
          <select
            aria-label="Проект"
            onChange={(event) => {
              setProjectId(event.target.value);
              setPage(0);
            }}
            value={projectId}
          >
            {projects.data?.items.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
          <select
            aria-label="Тип"
            onChange={(event) => setTypeFilter(event.target.value)}
            value={typeFilter}
          >
            <option value="">Все типы</option>
            {Object.entries(typeLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="Статус"
            onChange={(event) => setStatusFilter(event.target.value)}
            value={statusFilter}
          >
            <option value="">Все статусы</option>
            {Object.entries(statusLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="Приоритет"
            onChange={(event) => setPriorityFilter(event.target.value)}
            value={priorityFilter}
          >
            <option value="">Все приоритеты</option>
            {Object.entries(priorityLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="Оператор"
            onChange={(event) => setOperatorFilter(event.target.value)}
            value={operatorFilter}
          >
            <option value="">Все ответственные</option>
            {filterOptions.data?.operators.map((operator) => (
              <option key={operator.user_id} value={operator.user_id}>
                {operator.display_name}
              </option>
            ))}
          </select>
        </div>

        {tasks.isPending && <SectionSkeleton />}
        {tasks.isError && (
          <QueryError
            error={tasks.error}
            retry={() => void tasks.refetch()}
            title="Не удалось загрузить задачи"
          />
        )}
        {tasks.data?.items.length === 0 && (
          <div className="empty-state">
            <div>
              <ListChecks size={30} />
              <h3>Задач не найдено</h3>
              <p>Измените фильтры или создайте новую задачу.</p>
            </div>
          </div>
        )}
        {tasks.data && tasks.data.items.length > 0 && (
          <div className="task-list">
            {tasks.data.items.map((task) => (
              <article
                className={`task-row${selected?.id === task.id ? " selected" : ""}`}
                key={task.id}
              >
                <button
                  className="task-row-main"
                  onClick={() => setSelected(task)}
                  type="button"
                >
                  <span className={`task-priority-dot ${task.priority}`} />
                  <div className="task-row-copy">
                    <div>
                      <strong>{task.title}</strong>
                      <StatusBadge tone={statusTone(task)}>
                        {task.is_overdue
                          ? "Просрочена"
                          : statusLabels[task.status]}
                      </StatusBadge>
                    </div>
                    <p>
                      {task.customer_name ?? "Клиент"} ·{" "}
                      {task.customer_phone ?? "без телефона"} ·{" "}
                      {typeLabels[task.task_type]}
                    </p>
                  </div>
                  <div className="task-row-due">
                    <Clock3 size={15} />
                    <span>
                      {projectDate(task.due_at, task.project_timezone)}
                    </span>
                    <small>{task.project_name}</small>
                  </div>
                  <ArrowRight size={17} />
                </button>
                <div className="task-row-actions">
                  {task.status === "pending" && (
                    <button
                      aria-label="Начать"
                      onClick={() => action.mutate({ task, action: "start" })}
                      type="button"
                    >
                      <CirclePlay size={16} />
                    </button>
                  )}
                  {task.status === "in_progress" && (
                    <button
                      aria-label="Завершить"
                      onClick={() =>
                        action.mutate({ task, action: "complete" })
                      }
                      type="button"
                    >
                      <CheckCircle2 size={16} />
                    </button>
                  )}
                  {(task.status === "pending" ||
                    task.status === "in_progress") && (
                    <button
                      aria-label="Редактировать"
                      onClick={() => openEdit(task)}
                      type="button"
                    >
                      <Pencil size={15} />
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
        {tasks.data && tasks.data.total > limit && (
          <div className="task-pagination row-between">
            <span>
              {page * limit + 1}–
              {Math.min((page + 1) * limit, tasks.data.total)} из{" "}
              {tasks.data.total}
            </span>
            <div>
              <Button
                disabled={page === 0}
                onClick={() => setPage((value) => value - 1)}
                variant="secondary"
              >
                Назад
              </Button>
              <Button
                disabled={(page + 1) * limit >= tasks.data.total}
                onClick={() => setPage((value) => value + 1)}
                variant="secondary"
              >
                Далее
              </Button>
            </div>
          </div>
        )}
      </section>

      {message && (
        <div className="task-toast" role="status">
          {message}
          <button
            aria-label="Закрыть"
            onClick={() => setMessage("")}
            type="button"
          >
            <X size={14} />
          </button>
        </div>
      )}

      {selected && (
        <div className="task-drawer" role="dialog" aria-label="Карточка задачи">
          <button
            className="task-drawer-backdrop"
            onClick={() => setSelected(null)}
            type="button"
          />
          <aside>
            <div className="row-between">
              <div>
                <span className="eyebrow">
                  {typeLabels[selected.task_type]}
                </span>
                <h2>{selected.title}</h2>
              </div>
              <button
                aria-label="Закрыть карточку"
                className="icon-button"
                onClick={() => setSelected(null)}
                type="button"
              >
                <X size={18} />
              </button>
            </div>
            <div className="task-detail-badges">
              <StatusBadge tone={statusTone(selected)}>
                {selected.is_overdue
                  ? "Просрочена"
                  : statusLabels[selected.status]}
              </StatusBadge>
              <StatusBadge>{priorityLabels[selected.priority]}</StatusBadge>
            </div>
            <dl className="task-detail-grid">
              <div>
                <dt>Клиент</dt>
                <dd>{selected.customer_name ?? "Без имени"}</dd>
              </div>
              <div>
                <dt>Проект</dt>
                <dd>{selected.project_name}</dd>
              </div>
              <div>
                <dt>Срок</dt>
                <dd>
                  {projectDate(selected.due_at, selected.project_timezone)}
                </dd>
              </div>
              <div>
                <dt>Ответственный</dt>
                <dd>{selected.assigned_user_name ?? "Не назначен"}</dd>
              </div>
              <div>
                <dt>Автор</dt>
                <dd>{selected.created_by_user_name}</dd>
              </div>
              <div>
                <dt>Источник</dt>
                <dd>{selected.source}</dd>
              </div>
            </dl>
            {selected.description && (
              <section className="task-detail-copy">
                <h3>Описание</h3>
                <p>{selected.description}</p>
              </section>
            )}
            {selected.comment && (
              <section className="task-detail-copy">
                <h3>Комментарий</h3>
                <p>{selected.comment}</p>
              </section>
            )}
            {selected.cancellation_reason && (
              <section className="task-detail-copy danger">
                <h3>Причина отмены</h3>
                <p>{selected.cancellation_reason}</p>
              </section>
            )}
            <div className="task-detail-actions">
              {selected.status === "pending" && (
                <Button
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate({ task: selected, action: "start" })
                  }
                >
                  <CirclePlay size={16} /> Начать
                </Button>
              )}
              {selected.status === "in_progress" && (
                <Button
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate({ task: selected, action: "complete" })
                  }
                >
                  <CheckCircle2 size={16} /> Завершить
                </Button>
              )}
              {(selected.status === "pending" ||
                selected.status === "in_progress") && (
                <Button onClick={() => openEdit(selected)} variant="secondary">
                  <Pencil size={15} /> Изменить
                </Button>
              )}
              {canManageAllTasks(me.data?.user.role) &&
                (selected.status === "pending" ||
                  selected.status === "in_progress") && (
                  <Button
                    disabled={action.isPending}
                    onClick={() =>
                      action.mutate({ task: selected, action: "cancel" })
                    }
                    variant="danger"
                  >
                    <XCircle size={15} /> Отменить
                  </Button>
                )}
              {canManageAllTasks(me.data?.user.role) &&
                selected.status === "cancelled" && (
                  <Button
                    disabled={action.isPending}
                    onClick={() =>
                      action.mutate({ task: selected, action: "restore" })
                    }
                    variant="secondary"
                  >
                    <RotateCcw size={15} /> Восстановить
                  </Button>
                )}
            </div>
            <section className="task-history">
              <div className="row-between">
                <h3>История</h3>
                <History size={17} />
              </div>
              {events.isPending && <p>Загрузка истории…</p>}
              {events.data?.map((event) => (
                <div className="task-event" key={event.id}>
                  <span />
                  <div>
                    <strong>{eventLabels[event.event_type]}</strong>
                    <p>
                      {event.actor_name ?? "Система"} ·{" "}
                      {projectDate(event.created_at, selected.project_timezone)}
                    </p>
                  </div>
                </div>
              ))}
            </section>
          </aside>
        </div>
      )}

      {editing && (
        <div
          className="task-modal"
          role="dialog"
          aria-modal="true"
          aria-label={
            editing === "new" ? "Создание задачи" : "Редактирование задачи"
          }
        >
          <button
            className="task-modal-backdrop"
            onClick={() => setEditing(null)}
            type="button"
          />
          <form onSubmit={form.handleSubmit((value) => save.mutate(value))}>
            <div className="row-between">
              <div>
                <span className="eyebrow">Рабочий процесс</span>
                <h2>
                  {editing === "new" ? "Новая задача" : "Изменить задачу"}
                </h2>
              </div>
              <button
                aria-label="Закрыть форму"
                className="icon-button"
                onClick={() => setEditing(null)}
                type="button"
              >
                <X size={18} />
              </button>
            </div>
            <div className="task-form-grid">
              <label className="field">
                <span>Проект</span>
                <select
                  disabled={editing !== "new"}
                  {...form.register("project_id")}
                >
                  <option value="">Выберите проект</option>
                  {projects.data?.items.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
                <small>{form.formState.errors.project_id?.message}</small>
              </label>
              <label className="field">
                <span>Клиент</span>
                <select
                  disabled={editing !== "new" || options.isPending}
                  {...form.register("customer_id")}
                >
                  <option value="">Выберите клиента</option>
                  {options.data?.customers.map((customer) => (
                    <option key={customer.id} value={customer.id}>
                      {customer.display_name ?? "Клиент"}
                      {customer.phone ? ` · ${customer.phone}` : ""}
                    </option>
                  ))}
                </select>
                <small>{form.formState.errors.customer_id?.message}</small>
              </label>
              <label className="field">
                <span>Тип</span>
                <select
                  disabled={editing !== "new"}
                  {...form.register("task_type")}
                >
                  <option value="manual">Ручная</option>
                  <option value="callback">Перезвон</option>
                  <option value="follow_up">Последующий контакт</option>
                  {canManageAllTasks(me.data?.user.role) && (
                    <option value="system">Системная</option>
                  )}
                </select>
              </label>
              <label className="field">
                <span>Приоритет</span>
                <select {...form.register("priority")}>
                  <option value="low">Низкий</option>
                  <option value="normal">Обычный</option>
                  <option value="high">Высокий</option>
                  <option value="urgent">Срочный</option>
                </select>
              </label>
              <label className="field span-two">
                <span>Название</span>
                <input
                  {...form.register("title")}
                  placeholder="Что необходимо сделать"
                />
                <small>{form.formState.errors.title?.message}</small>
              </label>
              <label className="field span-two">
                <span>Описание</span>
                <textarea rows={3} {...form.register("description")} />
              </label>
              <label className="field">
                <span>
                  Срок ·{" "}
                  {formProject?.effective_settings.timezone ?? "Asia/Tashkent"}
                </span>
                <input type="datetime-local" {...form.register("due_at")} />
                <small>{form.formState.errors.due_at?.message}</small>
              </label>
              <label className="field">
                <span>Ответственный</span>
                <select
                  disabled={!canManageAllTasks(me.data?.user.role)}
                  {...form.register("assigned_user_id")}
                >
                  <option value="">Не назначен</option>
                  {options.data?.operators.map((operator) => (
                    <option key={operator.user_id} value={operator.user_id}>
                      {operator.display_name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field span-two">
                <span>Комментарий</span>
                <textarea rows={2} {...form.register("comment")} />
              </label>
            </div>
            {message && <div className="dialer-message">{message}</div>}
            <div className="task-modal-actions">
              <Button
                onClick={() => setEditing(null)}
                type="button"
                variant="secondary"
              >
                Отмена
              </Button>
              <Button disabled={save.isPending} type="submit">
                {save.isPending ? "Сохраняем…" : "Сохранить"}
              </Button>
            </div>
          </form>
        </div>
      )}
    </>
  );
}
