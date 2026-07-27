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
import { QueryError, SectionSkeleton } from "@/components/query-state";

function duration(seconds: number) {
  const rounded = Math.round(seconds);
  return rounded < 60
    ? `${rounded} с`
    : `${Math.floor(rounded / 60)} мин ${rounded % 60} с`;
}

export function AnalyticsView() {
  const analytics = useQuery({
    queryKey: ["analytics", "dashboard"],
    queryFn: () => apiRequest<Dashboard>("/analytics/dashboard"),
  });
  if (analytics.isPending) return <SectionSkeleton />;
  if (analytics.isError)
    return (
      <QueryError
        error={analytics.error}
        retry={() => void analytics.refetch()}
        title="Не удалось загрузить аналитику"
      />
    );

  const value = analytics.data;
  const languages = Object.entries(value.language_breakdown).map(
    ([language, calls]) => ({
      language: language.toUpperCase(),
      calls,
    }),
  );
  const transferRate = value.calls_today
    ? Math.round((value.transfers / value.calls_today) * 100)
    : 0;

  return (
    <>
      <div className="page-heading row-between">
        <h1>Аналитика</h1>
        {value.is_demo && (
          <StatusBadge tone="warning">Тестовые данные</StatusBadge>
        )}
      </div>
      <section
        aria-label="Ключевые показатели"
        className="metric-grid analytics-metrics"
      >
        <div className="metric-card panel">
          <span>Звонки сегодня</span>
          <strong>{value.calls_today}</strong>
        </div>
        <div className="metric-card panel">
          <span>Завершено</span>
          <strong>{value.completed_calls}</strong>
        </div>
        <div className="metric-card panel">
          <span>Доля переводов</span>
          <strong>{transferRate}%</strong>
        </div>
        <div className="metric-card panel">
          <span>Средняя длительность</span>
          <strong>{duration(value.average_duration_seconds)}</strong>
        </div>
      </section>
      <div className="content-grid">
        <section className="panel">
          <h2>Звонки по языкам</h2>
          {languages.length ? (
            <div className="dashboard-chart">
              <ResponsiveContainer height="100%" width="100%">
                <BarChart data={languages}>
                  <CartesianGrid
                    stroke="#E2E5EB"
                    strokeDasharray="3 3"
                    vertical={false}
                  />
                  <XAxis dataKey="language" stroke="#7D8494" />
                  <YAxis allowDecimals={false} stroke="#7D8494" />
                  <Tooltip
                    contentStyle={{
                      background: "#fff",
                      border: "1px solid #E2E5EB",
                      borderRadius: 10,
                    }}
                  />
                  <Bar dataKey="calls" fill="#E41739" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="empty-state">
              <div>
                <h3>Нет данных</h3>
                <p>Завершите тестовый разговор.</p>
              </div>
            </div>
          )}
        </section>
        <section className="panel usage-summary">
          <h2>Использование</h2>
          <div>
            <span>AI-минуты</span>
            <strong>{value.used_ai_minutes}</strong>
          </div>
          <div>
            <span>Оценка AI</span>
            <strong>${value.estimated_cost_usd}</strong>
          </div>
          <p>Это оценка API. SIP и телефонные минуты не включены.</p>
        </section>
      </div>
    </>
  );
}
