"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CheckCircle2, Clock3 } from "lucide-react";
import { useState } from "react";
import Link from "next/link";
import { Button, StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type { CallbackTask, Page } from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";

const filters = [
  { value: "", label: "Все" },
  { value: "pending", label: "Ожидают" },
  { value: "in_progress", label: "В работе" },
  { value: "completed", label: "Выполнены" },
];

function callbackTone(status: string): "primary" | "warning" | "success" {
  if (status === "completed") return "success";
  if (status === "in_progress") return "warning";
  return "primary";
}

export function CallbacksView() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState("pending");
  const tasks = useQuery({
    queryKey: ["callbacks", filter],
    queryFn: () =>
      apiRequest<Page<CallbackTask>>(
        `/callbacks?limit=100${filter ? `&status=${filter}` : ""}`,
      ),
    refetchInterval: 15_000,
  });
  const complete = useMutation({
    mutationFn: (taskId: string) =>
      apiRequest<CallbackTask>(`/callbacks/${taskId}/complete`, {
        method: "POST",
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["callbacks"] }),
        queryClient.invalidateQueries({ queryKey: ["customers"] }),
      ]);
    },
  });
  const now = Date.now();

  return (
    <>
      <div className="page-heading row-between">
        <div>
          <h1>Перезвоны</h1>
          <p>Просроченные задачи автоматически идут первыми в диалер</p>
        </div>
        <div className="callback-heading-actions">
          <StatusBadge tone="warning">
            {tasks.data?.total ?? 0} задач
          </StatusBadge>
          <Link className="tv-button tv-button-secondary" href="/app/tasks">
            Все задачи
          </Link>
        </div>
      </div>
      <section className="panel">
        <div className="filter-tabs" role="tablist" aria-label="Статусы задач">
          {filters.map((item) => (
            <button
              aria-selected={filter === item.value}
              className={filter === item.value ? "active" : ""}
              key={item.value}
              onClick={() => setFilter(item.value)}
              role="tab"
              type="button"
            >
              {item.label}
            </button>
          ))}
        </div>
        {tasks.isPending && <SectionSkeleton />}
        {tasks.isError && (
          <QueryError
            error={tasks.error}
            retry={() => void tasks.refetch()}
            title="Не удалось загрузить перезвоны"
          />
        )}
        {tasks.data?.items.length === 0 && (
          <div className="empty-state">
            <div>
              <CalendarClock size={28} />
              <h3>Задач в этом разделе нет</h3>
              <p>Перезвон можно назначить после завершения разговора.</p>
            </div>
          </div>
        )}
        {tasks.data && tasks.data.items.length > 0 && (
          <div className="callback-grid">
            {tasks.data.items.map((task) => {
              const overdue =
                task.status !== "completed" &&
                new Date(task.due_at).getTime() < now;
              return (
                <article
                  className={`callback-card${overdue ? " overdue" : ""}`}
                  key={task.id}
                >
                  <div className="row-between">
                    <StatusBadge tone={callbackTone(task.status)}>
                      {task.status === "pending"
                        ? "Ожидает"
                        : task.status === "in_progress"
                          ? "В работе"
                          : "Выполнена"}
                    </StatusBadge>
                    {overdue && (
                      <StatusBadge tone="danger">Просрочено</StatusBadge>
                    )}
                  </div>
                  <div>
                    <h3>{task.customer_name ?? "Клиент"}</h3>
                    <p>{task.customer_phone ?? "Телефон не указан"}</p>
                  </div>
                  <div className="callback-time">
                    <Clock3 size={15} />
                    <strong>
                      {new Date(task.due_at).toLocaleString("ru-RU")}
                    </strong>
                  </div>
                  {task.note && <p className="callback-note">{task.note}</p>}
                  {task.status !== "completed" && (
                    <Button
                      disabled={complete.isPending}
                      onClick={() => complete.mutate(task.id)}
                      variant="secondary"
                    >
                      <CheckCircle2 size={15} />
                      Выполнено
                    </Button>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>
    </>
  );
}
