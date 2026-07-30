import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  canCreateTasks,
  canManageAllTasks,
  TasksView,
} from "@/components/tasks-view";
import type {
  AuthResponse,
  Page,
  Project,
  Role,
  Task,
  TaskOptions,
} from "@/lib/types";

const apiRequestMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  apiRequest: apiRequestMock,
  idempotencyKey: (prefix: string) => `${prefix}-test-key`,
  ApiClientError: class ApiClientError extends Error {},
}));

const project: Project = {
  id: "11111111-1111-4111-8111-111111111111",
  name: "Розничные клиенты",
  description: "",
  status: "active",
  default_language: "ru",
  timezone: "Asia/Tashkent",
  outbound_number: null,
  outbound_phone_number_id: null,
  inbound_phone_number_ids: [],
  ai_operator_id: null,
  knowledge_source_id: null,
  call_flow_id: null,
  call_result_catalog_id: "catalog-id",
  max_concurrent_calls: 4,
  effective_settings: {
    default_language: "ru",
    timezone: "Asia/Tashkent",
    max_concurrent_calls: 4,
    recording_enabled: true,
    recording_disclosure_required: true,
  },
  working_hours: {},
  recording_enabled: true,
  recording_disclosure_required: true,
  max_attempts: 3,
  retry_intervals_minutes: [15, 60],
  callback_rules: {
    default_delay_minutes: 60,
    max_schedule_days: 30,
    allow_operator_scheduling: true,
    require_assignee: false,
    overdue_first: true,
  },
  is_default: true,
  archived_at: null,
  operator_user_ids: ["22222222-2222-4222-8222-222222222222"],
  created_at: "2026-07-30T08:00:00Z",
  updated_at: "2026-07-30T08:00:00Z",
};

const task: Task = {
  id: "33333333-3333-4333-8333-333333333333",
  tenant_id: "44444444-4444-4444-8444-444444444444",
  project_id: project.id,
  project_name: project.name,
  project_timezone: "Asia/Tashkent",
  task_type: "callback",
  title: "Перезвонить клиенту",
  description: "Уточнить решение по заявке",
  priority: "high",
  status: "pending",
  customer_id: "55555555-5555-4555-8555-555555555555",
  customer_name: "Анна Каримова",
  customer_phone: "+998901112233",
  call_id: null,
  call_outcome_id: null,
  assigned_user_id: "22222222-2222-4222-8222-222222222222",
  assigned_user_name: "Оператор Один",
  created_by_user_id: "22222222-2222-4222-8222-222222222222",
  created_by_user_name: "Оператор Один",
  due_at: "2026-07-30T07:00:00Z",
  started_at: null,
  completed_at: null,
  cancelled_at: null,
  cancellation_reason: null,
  comment: "Клиент просил утром",
  source: "manual",
  is_overdue: true,
  created_at: "2026-07-29T10:00:00Z",
  updated_at: "2026-07-29T10:00:00Z",
};

const options: TaskOptions = {
  operators: [
    {
      user_id: "22222222-2222-4222-8222-222222222222",
      display_name: "Оператор Один",
    },
  ],
  customers: [
    {
      id: task.customer_id,
      display_name: task.customer_name,
      phone: task.customer_phone,
    },
  ],
};

function auth(role: Role): AuthResponse {
  return {
    user: {
      id: "22222222-2222-4222-8222-222222222222",
      email: "operator@example.com",
      display_name: "Оператор Один",
      role,
    },
    tenant: {
      id: "44444444-4444-4444-8444-444444444444",
      name: "K-Line",
      slug: "k-line",
    },
    csrf_token: "csrf",
  };
}

function renderTasks(role: Role = "tenant_owner") {
  apiRequestMock.mockImplementation((path: string, init?: RequestInit) => {
    if (path === "/auth/me") return Promise.resolve(auth(role));
    if (path === "/projects?limit=100")
      return Promise.resolve<Page<Project>>({
        items: [project],
        total: 1,
        limit: 100,
        offset: 0,
      });
    if (path.startsWith("/tasks/options?project_id="))
      return Promise.resolve(options);
    if (path.startsWith("/tasks?") && !init)
      return Promise.resolve<Page<Task>>({
        items: [task],
        total: 1,
        limit: 20,
        offset: 0,
      });
    if (path === `/tasks/${task.id}/events`)
      return Promise.resolve([
        {
          id: "66666666-6666-4666-8666-666666666666",
          task_id: task.id,
          event_type: "created",
          actor_user_id: task.created_by_user_id,
          actor_name: task.created_by_user_name,
          safe_snapshot: { status: "pending" },
          created_at: task.created_at,
        },
      ]);
    if (path === `/tasks/${task.id}/start` && init?.method === "POST")
      return Promise.resolve({
        ...task,
        status: "in_progress",
        is_overdue: true,
      });
    throw new Error(`Unexpected API path: ${path}`);
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <TasksView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
});

describe("unified tasks workspace", () => {
  it("renders tabs, overdue state and project-timezone task data", async () => {
    renderTasks();
    expect(await screen.findByText("Перезвонить клиенту")).toBeInTheDocument();
    for (const label of [
      "Все",
      "Просроченные",
      "Сегодня",
      "Будущие",
      "Завершённые",
    ]) {
      expect(screen.getByRole("tab", { name: label })).toBeInTheDocument();
    }
    expect(screen.getAllByText("Просрочена").length).toBeGreaterThan(0);
    expect(screen.getByText(/Анна Каримова/)).toBeInTheDocument();
  });

  it("opens details with immutable event history", async () => {
    renderTasks();
    fireEvent.click(await screen.findByText("Перезвонить клиенту"));
    expect(
      await screen.findByRole("dialog", { name: "Карточка задачи" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Задача создана")).toBeInTheDocument();
    expect(screen.getByText("Уточнить решение по заявке")).toBeInTheDocument();
  });

  it("creates a form with project customers and operators", async () => {
    renderTasks("tenant_manager");
    fireEvent.click(
      await screen.findByRole("button", { name: /Создать задачу/ }),
    );
    const dialog = screen.getByRole("dialog", { name: "Создание задачи" });
    expect(dialog).toBeInTheDocument();
    expect(
      await within(dialog).findByRole("option", { name: /Анна Каримова/ }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole("option", { name: "Оператор Один" }),
    ).toBeInTheDocument();
  });

  it("starts a task with an idempotency key", async () => {
    renderTasks("human_operator");
    fireEvent.click(await screen.findByRole("button", { name: "Начать" }));
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        `/tasks/${task.id}/start`,
        expect.objectContaining({
          method: "POST",
          headers: { "Idempotency-Key": "task-start-test-key" },
        }),
      ),
    );
  });

  it("keeps analysts read-only and exposes role helpers", async () => {
    renderTasks("analyst");
    expect(await screen.findByText("Перезвонить клиенту")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Создать задачу/ }),
    ).not.toBeInTheDocument();
    expect(canCreateTasks("human_operator")).toBe(true);
    expect(canCreateTasks("analyst")).toBe(false);
    expect(canManageAllTasks("tenant_manager")).toBe(true);
    expect(canManageAllTasks("human_operator")).toBe(false);
  });
});
