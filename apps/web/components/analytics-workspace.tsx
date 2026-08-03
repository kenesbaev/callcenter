"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type {
  AnalyticsFilterOptions,
  AnalyticsMetric,
  AnalyticsOverview,
  OperatorPerformancePage,
  ProjectPerformancePage,
} from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";

type Preset = "today" | "yesterday" | "7d" | "30d" | "month" | "custom";

function dateParts(timezone: string, value = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(value);
  const read = (type: string) =>
    parts.find((part) => part.type === type)?.value ?? "";
  return `${read("year")}-${read("month")}-${read("day")}`;
}

function shiftDate(value: string, days: number) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function presetRange(preset: Preset, timezone: string) {
  const today = dateParts(timezone);
  if (preset === "yesterday") return { from: shiftDate(today, -1), to: today };
  if (preset === "7d")
    return { from: shiftDate(today, -6), to: shiftDate(today, 1) };
  if (preset === "30d")
    return { from: shiftDate(today, -29), to: shiftDate(today, 1) };
  if (preset === "month")
    return { from: `${today.slice(0, 7)}-01`, to: shiftDate(today, 1) };
  return { from: today, to: shiftDate(today, 1) };
}

function formatNumber(value: number | null, maximumFractionDigits = 0) {
  if (value === null) return "Недоступно";
  return new Intl.NumberFormat("ru-RU", { maximumFractionDigits }).format(
    value,
  );
}

function formatPercent(value: number | null) {
  return value === null ? "Недоступно" : `${formatNumber(value, 1)}%`;
}

function formatDuration(value: number | null) {
  if (value === null) return "Недоступно";
  const seconds = Math.max(0, Math.round(value));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function Comparison({ metric }: { metric: AnalyticsMetric }) {
  if (metric.previous_value === null || metric.absolute_change === null)
    return <small>Нет сравнения</small>;
  const direction =
    metric.absolute_change > 0 ? "↑" : metric.absolute_change < 0 ? "↓" : "→";
  return (
    <small
      className={metric.absolute_change >= 0 ? "metric-up" : "metric-down"}
    >
      {direction} {formatNumber(Math.abs(metric.absolute_change), 1)}
      {metric.percentage_change === null
        ? ""
        : ` · ${formatNumber(Math.abs(metric.percentage_change), 1)}%`}
    </small>
  );
}

function Kpi({
  label,
  metric,
  format = "number",
  note,
}: {
  label: string;
  metric: AnalyticsMetric;
  format?: "number" | "percent" | "duration" | "money";
  note?: string;
}) {
  const value =
    format === "percent"
      ? formatPercent(metric.value)
      : format === "duration"
        ? formatDuration(metric.value)
        : format === "money"
          ? metric.value === null
            ? "Недоступно"
            : new Intl.NumberFormat("ru-RU", {
                style: "currency",
                currency: "USD",
              }).format(metric.value)
          : formatNumber(metric.value, 1);
  return (
    <article className="metric-card panel analytics-kpi">
      <span>{label}</span>
      <strong>{value}</strong>
      <Comparison metric={metric} />
      {note && <em>{note}</em>}
    </article>
  );
}

function AnalyticsFilters({
  options,
  project,
  preset,
  timezone,
  from,
  to,
  onChange,
}: {
  options: AnalyticsFilterOptions;
  project: string;
  preset: Preset;
  timezone: string;
  from: string;
  to: string;
  onChange: (next: {
    project?: string;
    preset?: Preset;
    timezone?: string;
    from?: string;
    to?: string;
  }) => void;
}) {
  return (
    <section
      className="panel analytics-filter-bar"
      aria-label="Фильтры аналитики"
    >
      <label>
        <span>Проект</span>
        <select
          value={project}
          onChange={(event) => {
            const selected = options.projects.find(
              (item) => item.id === event.target.value,
            );
            onChange({
              project: event.target.value,
              timezone: selected?.timezone ?? options.default_timezone,
            });
          }}
        >
          <option value="">Все проекты</option>
          {options.projects.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
              {item.status === "archived" ? " · архив" : ""}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Период</span>
        <select
          value={preset}
          onChange={(event) =>
            onChange({ preset: event.target.value as Preset })
          }
        >
          <option value="today">Сегодня</option>
          <option value="yesterday">Вчера</option>
          <option value="7d">Последние 7 дней</option>
          <option value="30d">Последние 30 дней</option>
          <option value="month">Текущий месяц</option>
          <option value="custom">Свой период</option>
        </select>
      </label>
      <label>
        <span>Часовой пояс</span>
        <input
          value={timezone}
          onChange={(event) => onChange({ timezone: event.target.value })}
        />
      </label>
      {preset === "custom" && (
        <>
          <label>
            <span>От</span>
            <input
              type="date"
              value={from}
              onChange={(event) => onChange({ from: event.target.value })}
            />
          </label>
          <label>
            <span>До включительно</span>
            <input
              type="date"
              value={shiftDate(to, -1)}
              onChange={(event) =>
                onChange({ to: shiftDate(event.target.value, 1) })
              }
            />
          </label>
        </>
      )}
    </section>
  );
}

export function AnalyticsWorkspace({
  detailed = false,
}: {
  detailed?: boolean;
}) {
  const optionsQuery = useQuery({
    queryKey: ["analytics", "filter-options"],
    queryFn: () =>
      apiRequest<AnalyticsFilterOptions>("/analytics/filter-options"),
  });
  const initial = useMemo(() => {
    if (typeof window === "undefined")
      return {
        project: "",
        preset: "today" as Preset,
        timezone: "",
        from: "",
        to: "",
      };
    const query = new URLSearchParams(window.location.search);
    return {
      project: query.get("project_id") ?? "",
      preset: (query.get("preset") as Preset | null) ?? "today",
      timezone: query.get("timezone") ?? "",
      from: query.get("date_from") ?? "",
      to: query.get("date_to") ?? "",
    };
  }, []);
  const [filters, setFilters] = useState(initial);

  const timezone =
    filters.timezone || optionsQuery.data?.default_timezone || "Asia/Tashkent";
  const range =
    filters.preset === "custom" && filters.from && filters.to
      ? { from: filters.from, to: filters.to }
      : presetRange(filters.preset, timezone);
  const queryString = useMemo(() => {
    const query = new URLSearchParams({
      date_from: range.from,
      date_to: range.to,
      timezone,
    });
    if (filters.project) query.set("project_id", filters.project);
    return query.toString();
  }, [filters.project, range.from, range.to, timezone]);

  useEffect(() => {
    // `/app` stays a backwards-compatible landing URL. Dedicated analytics
    // routes persist filters without changing legacy post-login expectations.
    if (window.location.pathname === "/app") return;
    const query = new URLSearchParams(queryString);
    query.set("preset", filters.preset);
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}?${query}`,
    );
  }, [filters.preset, queryString]);

  const overview = useQuery({
    queryKey: ["analytics", "overview", queryString],
    queryFn: () =>
      apiRequest<AnalyticsOverview>(`/analytics/overview?${queryString}`),
    enabled: optionsQuery.isSuccess,
    refetchInterval: 15_000,
  });
  const operators = useQuery({
    queryKey: ["analytics", "operators", queryString],
    queryFn: () =>
      apiRequest<OperatorPerformancePage>(
        `/analytics/operators?${queryString}&limit=25`,
      ),
    enabled: detailed && optionsQuery.isSuccess,
  });
  const projects = useQuery({
    queryKey: ["analytics", "projects", queryString],
    queryFn: () =>
      apiRequest<ProjectPerformancePage>(
        `/analytics/projects?${queryString}&limit=25`,
      ),
    enabled: detailed && optionsQuery.isSuccess,
  });

  if (optionsQuery.isPending || overview.isPending) return <SectionSkeleton />;
  if (optionsQuery.isError)
    return (
      <QueryError
        title="Не удалось загрузить фильтры"
        error={optionsQuery.error}
        retry={() => void optionsQuery.refetch()}
      />
    );
  if (overview.isError)
    return (
      <QueryError
        title="Не удалось загрузить аналитику"
        error={overview.error}
        retry={() => void overview.refetch()}
      />
    );

  const data = overview.data;
  const summary = data.summary;
  const hasTestData = summary.data_quality_flags.includes("test_data_present");
  const applyFilters = (next: {
    project?: string;
    preset?: Preset;
    timezone?: string;
    from?: string;
    to?: string;
  }) =>
    setFilters((current) => {
      const merged = { ...current, ...next };
      if (next.preset && next.preset !== "custom") {
        const preset = presetRange(
          next.preset,
          next.timezone || merged.timezone || timezone,
        );
        merged.from = preset.from;
        merged.to = preset.to;
      }
      return merged;
    });
  const dateFormatter = new Intl.DateTimeFormat("ru-RU", {
    timeZone: timezone,
    day: "2-digit",
    month: "short",
    hour: summary.period.bucket === "hour" ? "2-digit" : undefined,
  });

  return (
    <>
      <div className="page-heading row-between analytics-heading">
        <div>
          <h1>{detailed ? "Аналитика" : "Обзор"}</h1>
          <p>
            Единые метрики ·{" "}
            {dateFormatter.format(new Date(summary.period.date_from))} —{" "}
            {dateFormatter.format(new Date(summary.period.date_to))}
          </p>
        </div>
        <div className="analytics-heading-badges">
          {hasTestData && (
            <StatusBadge tone="warning">Есть тестовые данные</StatusBadge>
          )}
          <StatusBadge tone="primary">
            UTC → {summary.period.timezone}
          </StatusBadge>
        </div>
      </div>
      <AnalyticsFilters
        options={optionsQuery.data}
        project={filters.project}
        preset={filters.preset}
        timezone={timezone}
        from={range.from}
        to={range.to}
        onChange={applyFilters}
      />
      <section
        className="metric-grid analytics-metric-grid"
        aria-label="Ключевые показатели"
      >
        <Kpi
          label="Активные звонки"
          metric={summary.active_calls}
          note="Текущий snapshot"
        />
        <Kpi label="Начатые попытки" metric={summary.attempted_calls} />
        <Kpi
          label="Процент дозвона"
          metric={summary.answer_rate}
          format="percent"
        />
        <Kpi label="Успешные звонки" metric={summary.successful_calls} />
        <Kpi
          label="Процент успеха"
          metric={summary.success_rate}
          format="percent"
        />
        <Kpi
          label="Средняя длительность"
          metric={summary.average_duration_seconds}
          format="duration"
        />
        <Kpi label="Запросы перевода" metric={summary.transfers.requested} />
        <Kpi label="Успешные переводы" metric={summary.transfers.successful} />
        <Kpi label="AI-звонки" metric={summary.ai_calls} />
        <Kpi label="AI-минуты" metric={summary.ai_minutes} />
        <Kpi
          label="Оценка AI"
          metric={summary.ai_cost_usd}
          format="money"
          note="Телефония не включена"
        />
        <Kpi label="Перезвоны" metric={summary.callbacks} />
      </section>

      <div className="analytics-main-grid">
        <section className="panel analytics-wide-panel">
          <div className="panel-title-row">
            <div>
              <h2>Динамика звонков</h2>
              <p>
                Bucket:{" "}
                {summary.period.bucket === "hour" ? "по часу" : "по дням"}
              </p>
            </div>
          </div>
          {data.timeseries.length ? (
            <div className="analytics-chart">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data.timeseries}>
                  <defs>
                    <linearGradient
                      id="attemptedFill"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="5%"
                        stopColor="#E41739"
                        stopOpacity={0.24}
                      />
                      <stop offset="95%" stopColor="#E41739" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    stroke="#E2E5EB"
                    strokeDasharray="3 3"
                    vertical={false}
                  />
                  <XAxis
                    dataKey="bucket_start"
                    tickFormatter={(value: string) =>
                      dateFormatter.format(new Date(value))
                    }
                    minTickGap={22}
                  />
                  <YAxis allowDecimals={false} />
                  <Tooltip
                    labelFormatter={(value) =>
                      dateFormatter.format(new Date(String(value)))
                    }
                  />
                  <Legend />
                  <Area
                    name="Попытки"
                    type="monotone"
                    dataKey="attempted"
                    stroke="#E41739"
                    fill="url(#attemptedFill)"
                  />
                  <Area
                    name="Соединены"
                    type="monotone"
                    dataKey="connected"
                    stroke="#168A5B"
                    fillOpacity={0}
                  />
                  <Area
                    name="Успешны"
                    type="monotone"
                    dataKey="successful"
                    stroke="#6D5DFB"
                    fillOpacity={0}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="empty-state">
              <div>
                <h3>Нет звонков за период</h3>
                <p>Измените период или проект.</p>
              </div>
            </div>
          )}
        </section>
        <section className="panel">
          <div className="panel-title-row">
            <div>
              <h2>Результаты</h2>
              <p>Исторические snapshots</p>
            </div>
          </div>
          {data.outcomes.length ? (
            <div className="analytics-chart compact">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.outcomes} layout="vertical">
                  <CartesianGrid stroke="#E2E5EB" horizontal={false} />
                  <XAxis type="number" allowDecimals={false} />
                  <YAxis type="category" dataKey="label" width={110} />
                  <Tooltip />
                  <Bar
                    dataKey="value"
                    name="Звонки"
                    fill="#E41739"
                    radius={[0, 6, 6, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <p className="analytics-empty-copy">Нет сохранённых результатов.</p>
          )}
        </section>
      </div>

      <div className="analytics-triple-grid">
        <section className="panel">
          <h2>Команда сейчас</h2>
          <div className="analytics-status-list">
            {(
              [
                "available",
                "busy",
                "on_hold",
                "away",
                "on_break",
                "offline",
              ] as const
            ).map((key) => (
              <div key={key}>
                <span className={`status-dot ${key}`} />
                <span>
                  {
                    {
                      available: "Доступны",
                      busy: "Заняты",
                      on_hold: "На удержании",
                      away: "Отошли",
                      on_break: "Перерыв",
                      offline: "Офлайн",
                    }[key]
                  }
                </span>
                <strong>{data.operator_statuses[key]}</strong>
              </div>
            ))}
          </div>
        </section>
        <section className="panel">
          <h2>Задачи и перезвоны</h2>
          <div className="analytics-status-list">
            <div>
              <span>Просрочены</span>
              <strong>{data.tasks.overdue}</strong>
            </div>
            <div>
              <span>Сегодня</span>
              <strong>{data.tasks.today}</strong>
            </div>
            <div>
              <span>Будущие</span>
              <strong>{data.tasks.future}</strong>
            </div>
            <div>
              <span>Завершены</span>
              <strong>{data.tasks.completed}</strong>
            </div>
          </div>
        </section>
        <section className="panel">
          <h2>Языки</h2>
          {data.languages.length ? (
            <div className="analytics-language-list">
              {data.languages.map((item) => (
                <div key={item.key}>
                  <span>{item.label}</span>
                  <strong>{item.value}</strong>
                </div>
              ))}
            </div>
          ) : (
            <p className="analytics-empty-copy">Нет языковых данных.</p>
          )}
        </section>
      </div>

      <section className="panel analytics-table-panel">
        <div className="panel-title-row">
          <div>
            <h2>Последние звонки</h2>
            <p>Телефоны маскируются на backend</p>
          </div>
          <span>{data.recent_calls.total} за период</span>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Время</th>
                <th>Проект / клиент</th>
                <th>Канал</th>
                <th>Длительность</th>
                <th>Результат</th>
                <th>Состояние</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.recent_calls.items.map((call) => (
                <tr key={call.id}>
                  <td>
                    {new Intl.DateTimeFormat("ru-RU", {
                      timeZone: timezone,
                      dateStyle: "short",
                      timeStyle: "short",
                    }).format(new Date(call.occurred_at))}
                  </td>
                  <td>
                    <strong>{call.project_name}</strong>
                    <br />
                    <small>
                      {call.customer_name ?? "Без клиента"} ·{" "}
                      {call.phone_masked ?? "—"}
                    </small>
                  </td>
                  <td>
                    {call.caller_type === "ai_agent" ? "AI" : "Оператор"} ·{" "}
                    {call.channel === "development_simulator"
                      ? "Simulator"
                      : "SIP"}
                    {call.is_test && (
                      <>
                        <br />
                        <StatusBadge tone="warning">Тест</StatusBadge>
                      </>
                    )}
                  </td>
                  <td>{formatDuration(call.duration_seconds)}</td>
                  <td>
                    {call.result_label ?? "Без результата"}
                    {call.transferred && (
                      <>
                        <br />
                        <small>Переведён</small>
                      </>
                    )}
                  </td>
                  <td>
                    <StatusBadge
                      tone={
                        call.status === "completed"
                          ? "success"
                          : call.status === "failed"
                            ? "danger"
                            : "primary"
                      }
                    >
                      {call.status}
                    </StatusBadge>
                  </td>
                  <td>
                    <Link href={`/app/conversations/${call.id}`}>Открыть</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!data.recent_calls.items.length && (
          <p className="analytics-empty-copy">
            Звонков за выбранный период нет.
          </p>
        )}
      </section>

      {detailed && (
        <>
          <div className="analytics-main-grid analytics-performance-grid">
            <section className="panel analytics-table-panel">
              <h2>Производительность операторов</h2>
              {operators.isPending ? (
                <SectionSkeleton />
              ) : operators.isError ? (
                <p className="table-error">{operators.error.message}</p>
              ) : (
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Оператор</th>
                        <th>Попытки</th>
                        <th>Дозвон</th>
                        <th>Успех</th>
                        <th>Разговор</th>
                      </tr>
                    </thead>
                    <tbody>
                      {operators.data.items.map((item) => (
                        <tr key={item.operator_id}>
                          <td>
                            <strong>{item.operator_name}</strong>
                            <br />
                            <small>
                              {item.project_names.join(", ") || "Без проекта"}
                              {!item.is_active ? " · заблокирован" : ""}
                            </small>
                          </td>
                          <td>{item.attempted}</td>
                          <td>{formatPercent(item.answer_rate)}</td>
                          <td>{formatPercent(item.success_rate)}</td>
                          <td>{formatDuration(item.talk_time_seconds)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            <section className="panel analytics-table-panel">
              <h2>Сравнение проектов</h2>
              {projects.isPending ? (
                <SectionSkeleton />
              ) : projects.isError ? (
                <p className="table-error">{projects.error.message}</p>
              ) : (
                <div className="table-wrap">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Проект</th>
                        <th>Попытки</th>
                        <th>Успех</th>
                        <th>AI / люди</th>
                        <th>AI usage</th>
                      </tr>
                    </thead>
                    <tbody>
                      {projects.data.items.map((item) => (
                        <tr key={item.project_id}>
                          <td>
                            <strong>{item.project_name}</strong>
                            <br />
                            <small>{item.project_status}</small>
                          </td>
                          <td>{item.attempted}</td>
                          <td>{item.successful}</td>
                          <td>
                            {item.ai_calls} / {item.human_calls}
                          </td>
                          <td>
                            {formatNumber(item.ai_minutes, 1)} мин
                            <br />
                            <small>
                              {item.cost_is_available
                                ? new Intl.NumberFormat("ru-RU", {
                                    style: "currency",
                                    currency: "USD",
                                  }).format(item.ai_cost_usd ?? 0)
                                : "Стоимость недоступна"}
                            </small>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>
        </>
      )}
      {summary.data_quality_flags.length > 0 && (
        <section className="panel analytics-quality">
          <h2>Качество данных</h2>
          <p>
            {summary.data_quality_flags
              .map(
                (flag) =>
                  ({
                    test_data_present: "В выборке есть simulator/test звонки.",
                    missing_outcome: "У части звонков ещё нет результата.",
                    missing_usage_price: "Для AI usage нет сохранённой цены.",
                    partial_ai_usage:
                      "Стоимость рассчитана не для всего AI usage.",
                    invalid_duration_excluded:
                      "Повреждённые интервалы длительности исключены.",
                    incomplete_historical_metadata:
                      "У части истории неполные метаданные.",
                  })[flag] ?? flag,
              )
              .join(" ")}
          </p>
          <small>Расходы SIP, DID и Asterisk не включены.</small>
        </section>
      )}
    </>
  );
}
