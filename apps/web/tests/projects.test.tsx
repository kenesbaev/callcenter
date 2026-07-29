import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  canManageProjects,
  projectFormToPayload,
  ProjectsView,
  type ProjectForm,
} from "@/components/projects-view";
import type { Project, ProjectOptions, Role } from "@/lib/types";

const apiRequestMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  ApiClientError: class ApiClientError extends Error {
    constructor(
      public readonly status: number,
      public readonly code: string,
      message: string,
    ) {
      super(message);
    }
  },
  apiRequest: apiRequestMock,
}));

const project: Project = {
  id: "3a42ad12-1e9f-4cd6-8a7e-ff6d8d178d14",
  name: "Операторский проект",
  description: "Рабочая кампания",
  status: "active",
  default_language: null,
  timezone: null,
  outbound_number: null,
  outbound_phone_number_id: null,
  inbound_phone_number_ids: [],
  ai_operator_id: null,
  knowledge_source_id: null,
  call_flow_id: null,
  call_result_catalog_id: "83817965-7182-4d7c-9508-64f936cd24cf",
  max_concurrent_calls: null,
  effective_settings: {
    default_language: "ru",
    timezone: "Asia/Tashkent",
    max_concurrent_calls: 5,
    recording_enabled: true,
    recording_disclosure_required: true,
  },
  working_hours: {},
  recording_enabled: null,
  recording_disclosure_required: null,
  max_attempts: 3,
  retry_intervals_minutes: [15, 60],
  callback_rules: {
    default_delay_minutes: 60,
    max_schedule_days: 30,
    allow_operator_scheduling: true,
    require_assignee: false,
    overdue_first: true,
  },
  is_default: false,
  archived_at: null,
  operator_user_ids: ["6c4a14c8-695d-497b-b683-917eb53d5a60"],
  created_at: "2026-07-29T10:00:00Z",
  updated_at: "2026-07-29T10:00:00Z",
};

const options: ProjectOptions = {
  tenant_defaults: project.effective_settings,
  operators: [],
  phone_numbers: [],
  ai_operators: [],
  knowledge_sources: [],
  call_flows: [],
};

function renderProjects(role: Role, selectedProject: Project = project) {
  apiRequestMock.mockImplementation((path: string) => {
    if (path === "/auth/me") {
      return Promise.resolve({
        user: {
          id: "user-id",
          email: "user@example.com",
          display_name: "User",
          role,
        },
        tenant: { id: "tenant-id", name: "K-Line", slug: "k-line" },
        csrf_token: "csrf",
      });
    }
    if (path === "/projects?limit=100") {
      return Promise.resolve({
        items: [selectedProject],
        total: 1,
        limit: 100,
        offset: 0,
      });
    }
    if (path === "/projects/options") return Promise.resolve(options);
    throw new Error(`Unexpected API path: ${path}`);
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <ProjectsView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
});

describe("project permissions", () => {
  it("allows owners and managers to open the full project editor", async () => {
    expect(canManageProjects("tenant_owner")).toBe(true);
    expect(canManageProjects("tenant_manager")).toBe(true);
    renderProjects("tenant_manager");

    fireEvent.click(
      await screen.findByRole("button", { name: /Операторский проект/ }),
    );
    expect(await screen.findByText("Конфигурация проекта")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Сохранить" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "В архив" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Лимиты и правила" }));
    expect(screen.getByText("Запись разговоров")).toBeInTheDocument();
  });

  it("shows assigned project to an operator without management settings", async () => {
    expect(canManageProjects("human_operator")).toBe(false);
    renderProjects("human_operator");

    expect(await screen.findByText("Операторский проект")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Новый проект" }),
    ).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: /Операторский проект/ }),
    );
    expect(
      await screen.findByText(
        "Изменять конфигурацию проекта могут владелец и менеджер компании.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Сохранить" }),
    ).not.toBeInTheDocument();
  });

  it("opens an archived project without offering a duplicate archive action", async () => {
    renderProjects("tenant_owner", {
      ...project,
      status: "archived",
      archived_at: "2026-07-29T11:00:00Z",
    });
    fireEvent.click(
      await screen.findByRole("button", { name: /Операторский проект/ }),
    );
    expect(await screen.findByText("Конфигурация проекта")).toBeInTheDocument();
    expect(screen.getAllByText("Архив").length).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: "В архив" }),
    ).not.toBeInTheDocument();
  });
});

describe("project payload", () => {
  it("sends inherited settings as null and serializes schedule and retry rules", () => {
    const form: ProjectForm = {
      name: "Project",
      description: "Description",
      status: "active",
      default_language: "",
      timezone: "",
      outbound_phone_number_id: "",
      inbound_phone_number_ids: [],
      ai_operator_id: "",
      knowledge_source_id: "",
      call_flow_id: "",
      operator_user_ids: [],
      max_concurrent_calls: "",
      recording_enabled: "inherit",
      recording_disclosure_required: "inherit",
      max_attempts: 3,
      retry_intervals: "30, 120",
      callback_default_delay_minutes: 60,
      callback_max_schedule_days: 30,
      callback_allow_operator_scheduling: true,
      callback_require_assignee: false,
      callback_overdue_first: true,
      working_days: [
        { key: "monday", enabled: true, start: "09:00", end: "18:00" },
        { key: "tuesday", enabled: false, start: "09:00", end: "18:00" },
        { key: "wednesday", enabled: false, start: "09:00", end: "18:00" },
        { key: "thursday", enabled: false, start: "09:00", end: "18:00" },
        { key: "friday", enabled: false, start: "09:00", end: "18:00" },
        { key: "saturday", enabled: false, start: "09:00", end: "18:00" },
        { key: "sunday", enabled: false, start: "09:00", end: "18:00" },
      ],
    };

    expect(projectFormToPayload(form)).toMatchObject({
      default_language: null,
      timezone: null,
      max_concurrent_calls: null,
      recording_enabled: null,
      retry_intervals_minutes: [30, 120],
      working_hours: {
        monday: { enabled: true, start: "09:00", end: "18:00" },
        tuesday: { enabled: false },
      },
    });
  });
});
