import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CallResultsEditor } from "@/components/call-results-editor";
import { groupCallResults, localizedResult } from "@/components/dialer-view";
import type {
  AuthResponse,
  CallResultCatalog,
  CallResultDefinition,
  Project,
  Role,
} from "@/lib/types";

const apiRequestMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  apiRequest: apiRequestMock,
  idempotencyKey: () => "test-idempotency-key",
  ApiClientError: class ApiClientError extends Error {},
}));

const project: Project = {
  id: "project-id",
  name: "Карточный проект",
  description: "",
  status: "active",
  default_language: "ru",
  timezone: null,
  outbound_number: null,
  outbound_phone_number_id: null,
  inbound_phone_number_ids: [],
  ai_operator_id: null,
  knowledge_source_id: null,
  call_flow_id: null,
  call_result_catalog_id: "catalog-id",
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
  is_default: true,
  archived_at: null,
  operator_user_ids: [],
  created_at: "2026-07-29T10:00:00Z",
  updated_at: "2026-07-29T10:00:00Z",
};

function definition(
  overrides: Partial<CallResultDefinition> = {},
): CallResultDefinition {
  return {
    id: "result-id",
    project_id: project.id,
    catalog_id: "catalog-id",
    system_code: "sale",
    category: "successful",
    name: "Продажа",
    name_translations: {
      ru: "Продажа",
      uz: "Sotuv",
      en: "Sale",
      kaa: "Satıw",
      "kaa-latn": "Satıw (Latin)",
    },
    description: "",
    color: "#16A34A",
    sort_order: 10,
    is_active: true,
    requires_comment: false,
    requires_callback: false,
    requires_callback_at: false,
    creates_task: false,
    next_customer_status: "completed",
    return_to_queue: false,
    completes_customer: true,
    do_not_call: false,
    counts_as_success: true,
    archived_at: null,
    used_count: 3,
    created_at: "2026-07-29T10:00:00Z",
    updated_at: "2026-07-29T10:00:00Z",
    ...overrides,
  };
}

const catalog: CallResultCatalog = {
  id: "catalog-id",
  project_id: project.id,
  name: "Результаты звонка",
  is_active: true,
  definitions: [
    definition(),
    definition({
      id: "callback-id",
      system_code: "callback",
      category: "intermediate",
      name: "Перезвонить",
      name_translations: {
        ru: "Перезвонить",
        uz: "Qayta qo‘ng‘iroq",
        en: "Callback",
        kaa: "Qayta qońıraw",
      },
      color: "#F59E0B",
      sort_order: 20,
      requires_callback: true,
      requires_callback_at: true,
      completes_customer: false,
      counts_as_success: false,
      used_count: 0,
    }),
    definition({
      id: "busy-id",
      system_code: "busy",
      category: "unreachable",
      name: "Занято",
      name_translations: {
        ru: "Занято",
        uz: "Band",
        en: "Busy",
        kaa: "Bánt",
      },
      color: "#F97316",
      sort_order: 30,
      counts_as_success: false,
      used_count: 0,
    }),
    definition({
      id: "decline-id",
      system_code: "decline",
      category: "unsuccessful",
      name: "Отказ",
      name_translations: {
        ru: "Отказ",
        uz: "Rad etildi",
        en: "Declined",
        kaa: "Bas tarttı",
      },
      color: "#DC2626",
      sort_order: 40,
      counts_as_success: false,
      used_count: 0,
    }),
  ],
};

function auth(role: Role): AuthResponse {
  return {
    user: {
      id: "user-id",
      email: "user@example.com",
      display_name: "User",
      role,
    },
    tenant: { id: "tenant-id", name: "K-Line", slug: "k-line" },
    csrf_token: "csrf",
  };
}

function renderEditor(role: Role = "tenant_owner") {
  apiRequestMock.mockImplementation((path: string) => {
    if (path === "/auth/me") return Promise.resolve(auth(role));
    if (path === "/projects/project-id") return Promise.resolve(project);
    if (path.startsWith("/call-results?project_id="))
      return Promise.resolve(catalog);
    throw new Error(`Unexpected API path: ${path}`);
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <CallResultsEditor projectId="project-id" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
});

describe("project call result catalog", () => {
  it("renders all four categories and warns about immutable history", async () => {
    renderEditor();
    expect(await screen.findByText("Карточный проект")).toBeInTheDocument();
    for (const heading of [
      "Успешные",
      "Промежуточные",
      "Недозвон",
      "Неуспешные",
    ]) {
      expect(
        screen.getByRole("heading", { name: heading }),
      ).toBeInTheDocument();
    }
    fireEvent.click(
      screen.getAllByRole("button", { name: "Редактировать" })[0],
    );
    expect(
      screen.getByText(/Переименование не изменит сохранённые результаты/),
    ).toBeInTheDocument();
  });

  it("shows local validation feedback for an incomplete result", async () => {
    renderEditor("tenant_manager");
    fireEvent.click(
      await screen.findByRole("button", { name: /Новый результат/ }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(
      await screen.findByText("Заполните название и системный код"),
    ).toBeInTheDocument();
    expect(apiRequestMock).not.toHaveBeenCalledWith(
      "/call-results",
      expect.anything(),
    );
  });

  it("keeps operator access read-only", async () => {
    renderEditor("human_operator");
    expect(await screen.findByText("Продажа")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Новый результат/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Редактировать" }),
    ).not.toBeInTheDocument();
  });
});

describe("Dialer result preparation", () => {
  it("groups active results and excludes disabled or archived definitions", () => {
    const grouped = groupCallResults([
      ...catalog.definitions,
      definition({ id: "disabled", is_active: false }),
      definition({ id: "archived", archived_at: "2026-07-29T12:00:00Z" }),
    ]);
    expect(grouped.successful.map((item) => item.id)).toEqual(["result-id"]);
    expect(grouped.intermediate[0].system_code).toBe("callback");
    expect(grouped.unreachable[0].system_code).toBe("busy");
    expect(grouped.unsuccessful[0].system_code).toBe("decline");
  });

  it("uses normalized BCP 47 language variants without treating ka as kaa", () => {
    const value = definition();
    expect(localizedResult(value, "kaa-Latn")).toBe("Satıw (Latin)");
    expect(localizedResult(value, "kaa-Cyrl")).toBe("Satıw");
    expect(localizedResult(value, "ka")).toBe("Продажа");
  });
});
