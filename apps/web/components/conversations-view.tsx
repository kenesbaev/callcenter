"use client";

import { useQuery } from "@tanstack/react-query";
import { Headphones } from "lucide-react";
import Link from "next/link";
import { StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type { Call, Page } from "@/lib/types";

function timestamp(value: string | null) {
  return value
    ? new Intl.DateTimeFormat("ru-RU", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";
}

export function ConversationsView() {
  const calls = useQuery({
    queryKey: ["calls"],
    queryFn: () => apiRequest<Page<Call>>("/calls"),
  });
  return (
    <>
      <div className="page-heading">
        <h1>Разговоры</h1>
      </div>
      <section className="panel">
        <div className="row-between">
          <div>
            <h2>История звонков</h2>
          </div>
          <StatusBadge>{calls.data?.total ?? 0}</StatusBadge>
        </div>
        {calls.isPending && <div className="table-wrap skeleton">Загрузка</div>}
        {calls.isError && (
          <div className="error-state">
            <div>
              <h3>Не удалось загрузить разговоры</h3>
              <p>{calls.error.message}</p>
            </div>
          </div>
        )}
        {calls.data?.items.length === 0 && (
          <div className="empty-state">
            <div>
              <Headphones aria-hidden="true" size={28} />
              <h3>Разговоров пока нет</h3>
              <p>Завершите тестовую симуляцию.</p>
            </div>
          </div>
        )}
        {Boolean(calls.data?.items.length) && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Начало</th>
                  <th>Канал</th>
                  <th>Язык</th>
                  <th>Длительность</th>
                  <th>Статус</th>
                  <th>Результат</th>
                </tr>
              </thead>
              <tbody>
                {calls.data?.items.map((call) => (
                  <tr key={call.id}>
                    <td>
                      <Link href={`/app/conversations/${call.id}`}>
                        <strong>{timestamp(call.started_at)}</strong>
                      </Link>
                    </td>
                    <td>
                      {call.channel.replaceAll("_", " ")}
                      {call.is_demo && (
                        <>
                          {" "}
                          <StatusBadge tone="warning">Тест</StatusBadge>
                        </>
                      )}
                    </td>
                    <td>{call.language?.toUpperCase() ?? "—"}</td>
                    <td>{call.duration_seconds} с</td>
                    <td>
                      <StatusBadge
                        tone={
                          call.status === "completed" ? "success" : "warning"
                        }
                      >
                        {call.status}
                      </StatusBadge>
                    </td>
                    <td>
                      {call.transfer_reason
                        ? `Перевод: ${call.transfer_reason}`
                        : "Ответ по базе знаний"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
