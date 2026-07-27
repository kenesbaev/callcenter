"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type { Dashboard } from "@/lib/types";

function duration(seconds: number) {
  const rounded = Math.round(seconds);
  return rounded < 60
    ? `${rounded} с`
    : `${Math.floor(rounded / 60)} мин ${rounded % 60} с`;
}

export function DashboardView() {
  const dashboard = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => apiRequest<Dashboard>("/analytics/dashboard"),
  });
  const data = Object.entries(dashboard.data?.language_breakdown ?? {}).map(
    ([language, calls]) => ({ language: language.toUpperCase(), calls }),
  );

  if (dashboard.isPending)
    return (
      <div className="metric-grid">
        <div className="metric-card skeleton">Загрузка</div>
        <div className="metric-card skeleton">Загрузка</div>
        <div className="metric-card skeleton">Загрузка</div>
        <div className="metric-card skeleton">Загрузка</div>
      </div>
    );
  if (dashboard.isError)
    return (
      <div className="error-state panel">
        <div>
          <h2>Не удалось загрузить аналитику</h2>
          <p>{dashboard.error.message}</p>
          <button
            className="tv-button tv-button-secondary"
            onClick={() => void dashboard.refetch()}
            type="button"
          >
            Повторить
          </button>
        </div>
      </div>
    );

  const value = dashboard.data;
  return (
    <>
      <div className="page-heading row-between">
        <div>
          <h1>Обзор</h1>
        </div>
        {value.is_demo && (
          <StatusBadge tone="warning">Тестовые данные</StatusBadge>
        )}
      </div>
      <section aria-label="Показатели звонков" className="metric-grid">
        <div className="metric-card panel">
          <span>Активные звонки</span>
          <strong>{value.active_calls}</strong>
        </div>
        <div className="metric-card panel">
          <span>Звонки сегодня</span>
          <strong>{value.calls_today}</strong>
        </div>
        <div className="metric-card panel">
          <span>Переводы оператору</span>
          <strong>{value.transfers}</strong>
        </div>
        <div className="metric-card panel">
          <span>Средняя длительность</span>
          <strong>{duration(value.average_duration_seconds)}</strong>
        </div>
      </section>
      <div className="content-grid">
        <section className="panel">
          <h2>Языки</h2>
          {data.length ? (
            <div className="dashboard-chart" aria-label="Звонки по языкам">
              <ResponsiveContainer height="100%" width="100%">
                <BarChart data={data}>
                  <CartesianGrid
                    stroke="#E2E5EB"
                    strokeDasharray="3 3"
                    vertical={false}
                  />
                  <XAxis dataKey="language" stroke="#7D8494" />
                  <YAxis allowDecimals={false} stroke="#7D8494" />
                  <Tooltip
                    contentStyle={{
                      background: "#FFFFFF",
                      border: "1px solid #E2E5EB",
                      borderRadius: 10,
                      color: "#141A2F",
                    }}
                  />
                  <Bar dataKey="calls" fill="#E41739" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="empty-state">
              <div>
                <h3>Пока нет данных</h3>
                <p>Завершите тестовую симуляцию.</p>
              </div>
            </div>
          )}
        </section>
        <section className="panel usage-summary">
          <h2>Использование AI</h2>
          <p className="panel-subtitle">Оценка, не счёт</p>
          <div>
            <span>AI-минуты</span>
            <strong>{value.used_ai_minutes}</strong>
          </div>
          <div>
            <span>Оценка стоимости</span>
            <strong>${value.estimated_cost_usd}</strong>
          </div>
          <p>SIP и телефонные минуты не включены.</p>
        </section>
      </div>
    </>
  );
}
