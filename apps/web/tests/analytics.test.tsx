import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AnalyticsWorkspace } from "@/components/analytics-workspace";
import type { AnalyticsMetric, AnalyticsOverview } from "@/lib/types";

const apiRequestMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ apiRequest: apiRequestMock }));

const metric = (value: number | null): AnalyticsMetric => ({
  value,
  numerator: null,
  denominator: null,
  previous_value: value === null ? null : Math.max(0, value - 1),
  absolute_change: value === null ? null : 1,
  percentage_change: value && value > 1 ? 10 : null,
  is_available: value !== null,
  data_quality_flags: [],
});

const overview: AnalyticsOverview = {
  summary: {
    period: {
      date_from: "2026-08-01T00:00:00Z",
      date_to: "2026-08-02T00:00:00Z",
      timezone: "Asia/Tashkent",
      bucket: "hour",
    },
    active_calls: metric(2),
    attempted_calls: metric(10),
    connected_calls: metric(7),
    answer_rate: metric(70),
    successful_calls: metric(4),
    success_rate: metric(50),
    success_rate_connected: metric(57.1),
    average_duration_seconds: metric(125),
    ai_calls: metric(3),
    human_calls: metric(7),
    simulator_calls: metric(1),
    sip_calls: metric(9),
    ai_minutes: metric(12.5),
    ai_cost_usd: {
      ...metric(null),
      data_quality_flags: ["missing_usage_price"],
    },
    callbacks: metric(2),
    transfers: {
      requested: metric(2),
      successful: metric(1),
      failed: metric(1),
      success_rate: metric(50),
    },
    data_quality_flags: ["test_data_present", "missing_usage_price"],
    telephony_cost_included: false,
  },
  timeseries: [
    {
      bucket_start: "2026-08-01T08:00:00Z",
      attempted: 10,
      connected: 7,
      successful: 4,
      ai_calls: 3,
      human_calls: 7,
    },
  ],
  outcomes: [{ key: "sale", label: "Продажа", value: 4, color: "#16A34A" }],
  languages: [{ key: "ru", label: "RU", value: 8, color: null }],
  callers: [],
  channels: [],
  tasks: { overdue: 1, today: 2, future: 3, completed: 4, by_type: [] },
  operator_statuses: {
    available: 2,
    busy: 1,
    on_hold: 0,
    away: 0,
    on_break: 1,
    offline: 2,
    active_members: 5,
    blocked_members: 1,
  },
  recent_calls: {
    items: [
      {
        id: "call-1",
        occurred_at: "2026-08-01T08:00:00Z",
        project_id: "project-1",
        project_name: "Продажи",
        customer_name: "Клиент",
        phone_masked: "+•••••••4567",
        direction: "outbound",
        caller_type: "human_operator",
        channel: "sip",
        language: "ru",
        duration_seconds: 125,
        result_label: "Продажа",
        result_category: "successful",
        status: "completed",
        transferred: true,
        operator_name: "Оператор",
        is_test: false,
      },
    ],
    total: 1,
    limit: 10,
    offset: 0,
  },
};

function renderAnalytics(detailed = false) {
  apiRequestMock.mockImplementation((path: string) => {
    if (path === "/analytics/filter-options")
      return Promise.resolve({
        projects: [
          {
            id: "project-1",
            name: "Продажи",
            status: "active",
            timezone: "Asia/Tashkent",
          },
        ],
        operators: [],
        default_timezone: "Asia/Tashkent",
        max_period_days: 366,
        financial_metrics_visible: true,
      });
    if (path.startsWith("/analytics/overview?"))
      return Promise.resolve(overview);
    if (path.startsWith("/analytics/operators?"))
      return Promise.resolve({
        items: [
          {
            operator_id: "operator-1",
            operator_name: "Оператор",
            project_names: ["Продажи"],
            attempted: 10,
            connected: 7,
            successful: 4,
            answer_rate: 70,
            success_rate: 40,
            average_duration_seconds: 125,
            transfers: 1,
            callbacks: 2,
            talk_time_seconds: 875,
            is_active: true,
          },
        ],
        total: 1,
        limit: 25,
        offset: 0,
      });
    if (path.startsWith("/analytics/projects?"))
      return Promise.resolve({
        items: [
          {
            project_id: "project-1",
            project_name: "Продажи",
            project_status: "active",
            attempted: 10,
            connected: 7,
            successful: 4,
            ai_calls: 3,
            human_calls: 7,
            callbacks: 2,
            ai_minutes: 12.5,
            ai_cost_usd: null,
            cost_is_available: false,
          },
        ],
        total: 1,
        limit: 25,
        offset: 0,
      });
    throw new Error(`Unexpected path: ${path}`);
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <AnalyticsWorkspace detailed={detailed} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
  window.history.replaceState(null, "", "/app/overview");
});

describe("analytics workspace", () => {
  it("shows exact KPI, comparison, test badge, recent calls and unavailable cost", async () => {
    renderAnalytics();
    expect(await screen.findByText("Начатые попытки")).toBeInTheDocument();
    expect(screen.getByText("70%")).toBeInTheDocument();
    expect(screen.getAllByText("Недоступно").length).toBeGreaterThan(0);
    expect(screen.getByText("Есть тестовые данные")).toBeInTheDocument();
    expect(screen.getAllByText("Продажи").length).toBeGreaterThan(0);
    expect(screen.getByText(/•4567/)).toBeInTheDocument();
  });

  it("persists project and period filters in URL", async () => {
    renderAnalytics();
    const project = await screen.findByDisplayValue("Все проекты");
    fireEvent.change(project, { target: { value: "project-1" } });
    fireEvent.change(await screen.findByDisplayValue("Сегодня"), {
      target: { value: "7d" },
    });
    await waitFor(() => {
      expect(window.location.search).toContain("project_id=project-1");
      expect(window.location.search).toContain("preset=7d");
    });
  });

  it("renders operator and project performance tables", async () => {
    renderAnalytics(true);
    expect(
      await screen.findByText("Производительность операторов"),
    ).toBeInTheDocument();
    expect(await screen.findByText("Сравнение проектов")).toBeInTheDocument();
    expect(screen.getAllByText("Оператор").length).toBeGreaterThan(0);
    expect(screen.getByText("Стоимость недоступна")).toBeInTheDocument();
  });
});
