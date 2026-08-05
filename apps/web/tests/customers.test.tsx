import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  canManageCustomers,
  customerFormToPayload,
  CustomersView,
  type CustomerForm,
} from "@/components/customers-view";
import type {
  BackgroundCustomerImport,
  Customer,
  CustomerImportPreview,
  Project,
  ProjectOptions,
  Role,
} from "@/lib/types";

const apiRequestMock = vi.hoisted(() => vi.fn());
const apiUploadMock = vi.hoisted(() => vi.fn());
const idempotencyKeyMock = vi.hoisted(() => vi.fn());

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
  apiUpload: apiUploadMock,
  idempotencyKey: idempotencyKeyMock,
}));

let rejectFirstBackgroundCancel = false;
let backgroundCancelRequestCount = 0;
let showFailedBackgroundImport = false;

const project: Project = {
  id: "3a42ad12-1e9f-4cd6-8a7e-ff6d8d178d14",
  name: "Розничный проект",
  description: "",
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
  is_default: true,
  archived_at: null,
  operator_user_ids: ["6c4a14c8-695d-497b-b683-917eb53d5a60"],
  created_at: "2026-07-29T10:00:00Z",
  updated_at: "2026-07-29T10:00:00Z",
};

const customer: Customer = {
  id: "a1e74029-10d3-40db-9b3c-1c765149f572",
  project_id: project.id,
  display_name: "Алишер Каримов",
  external_reference: "CRM-42",
  preferred_language: "ru",
  status: "new",
  city: "Ташкент",
  region: "Ташкент",
  address: "ул. Амира Темура, 1",
  job_title: "Директор",
  organization: "K-Line Bank",
  tags: ["VIP"],
  description: "Клиент розничного направления",
  source: "CRM",
  assigned_user_id: "6c4a14c8-695d-497b-b683-917eb53d5a60",
  custom_fields: { segment: "retail" },
  contacts: [
    {
      id: "14a463e7-daea-4810-8c20-f1955ad2c37a",
      kind: "phone",
      value: "+998901234567",
      label: "Мобильный",
      is_primary: true,
    },
    {
      id: "b55f04b8-d9c8-45fd-85d3-839df242fc52",
      kind: "email",
      value: "client@example.com",
      label: null,
      is_primary: true,
    },
  ],
  locked_by_user_id: null,
  locked_until: null,
  last_call_at: null,
  next_call_at: null,
  next_contact_at: null,
  archived_at: null,
  created_at: "2026-07-29T10:00:00Z",
  updated_at: "2026-07-29T10:00:00Z",
};

const options: ProjectOptions = {
  tenant_defaults: project.effective_settings,
  operators: [
    {
      user_id: "6c4a14c8-695d-497b-b683-917eb53d5a60",
      display_name: "Ответственный оператор",
      email: "operator@example.com",
      role: "human_operator",
    },
  ],
  phone_numbers: [],
  ai_operators: [],
  knowledge_sources: [],
  call_flows: [],
};

const preview: CustomerImportPreview = {
  id: "16f86e8b-2cb4-4356-8e6f-6210fbbd50af",
  project_id: project.id,
  file_name: "clients.csv",
  file_type: "csv",
  sheet_names: ["CSV"],
  selected_sheet: "CSV",
  headers: ["ФИО", "Телефон"],
  mapping: { display_name: "ФИО", phone: "Телефон" },
  update_rule: "skip",
  total_rows: 1,
  valid_rows: 1,
  duplicate_rows: 0,
  error_rows: 0,
  rows: [
    {
      row_number: 2,
      values: { display_name: "Новый клиент" },
      duplicate_fields: [],
      errors: [],
    },
  ],
  expires_at: "2026-07-30T10:00:00Z",
};

const backgroundImport: BackgroundCustomerImport = {
  id: "bb9cf9ac-f0e1-4d6c-af04-6a25325de224",
  job_id: "9ae5f54d-4cb1-4ec0-a13b-c3562f07496f",
  project_id: project.id,
  file_name: "large-clients.csv",
  file_type: "csv",
  status: "staging",
  progress: 42,
  sheet_names: ["CSV"],
  selected_sheet: "CSV",
  headers: ["ФИО", "Телефон"],
  mapping: { display_name: "ФИО", phone: "Телефон" },
  update_rule: "skip",
  preview_rows: [],
  total_rows: 80_000,
  valid_rows: 40_000,
  invalid_rows: 1,
  duplicate_rows: 4,
  created: 0,
  updated: 0,
  skipped: 0,
  error_count: 1,
  safe_error_code: null,
  processing_duration_ms: null,
  state_version: 3,
  can_commit: false,
  can_cancel: true,
  can_retry: false,
  report_available: true,
  created_at: "2026-08-04T08:00:00Z",
  completed_at: null,
};

function renderCustomers(role: Role = "tenant_manager") {
  apiRequestMock.mockImplementation((path: string, init?: RequestInit) => {
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
        items: [project],
        total: 1,
        limit: 100,
        offset: 0,
      });
    }
    if (path === "/projects/options") return Promise.resolve(options);
    if (path.startsWith("/customers/fields?")) {
      return Promise.resolve([
        {
          id: "field-id",
          project_id: project.id,
          name: "Сегмент",
          key: "segment",
          field_type: "text",
          is_required: false,
          sort_order: 0,
          options: [],
          default_value: null,
          is_active: true,
          created_at: "2026-07-29T10:00:00Z",
          updated_at: "2026-07-29T10:00:00Z",
        },
      ]);
    }
    if (path.startsWith("/customers?") && !init) {
      return Promise.resolve({
        items: [customer],
        total: 1,
        limit: 25,
        offset: 0,
      });
    }
    if (path === "/customers/import/preview" && init?.method === "POST") {
      return Promise.resolve(preview);
    }
    if (path.startsWith("/customers/import/background?")) {
      const item = showFailedBackgroundImport
        ? {
            ...backgroundImport,
            status: "failed" as const,
            can_cancel: false,
            can_retry: true,
            safe_error_code: "customer_import_storage_unavailable",
          }
        : backgroundImport;
      return Promise.resolve({
        items: [item],
        total: 1,
        limit: 20,
        offset: 0,
      });
    }
    if (path === `/customers/import/background/${backgroundImport.id}/status`) {
      return Promise.resolve(
        showFailedBackgroundImport
          ? {
              ...backgroundImport,
              status: "failed" as const,
              can_cancel: false,
              can_retry: true,
              safe_error_code: "customer_import_storage_unavailable",
            }
          : backgroundImport,
      );
    }
    if (
      path === `/customers/import/background/${backgroundImport.id}/cancel` &&
      init?.method === "POST"
    ) {
      backgroundCancelRequestCount += 1;
      if (rejectFirstBackgroundCancel && backgroundCancelRequestCount === 1) {
        return Promise.reject(new Error("lost response"));
      }
      return Promise.resolve({
        ...backgroundImport,
        status: "cancel_requested",
        can_cancel: false,
        can_retry: false,
      });
    }
    if (path === `/customers/import/background/${backgroundImport.id}/report`) {
      return Promise.resolve({ url: "https://storage.example/report.csv" });
    }
    if (
      path === `/customers/import/background/${backgroundImport.id}/retry` &&
      init?.method === "POST"
    ) {
      return Promise.resolve({
        ...backgroundImport,
        status: "queued",
        can_cancel: true,
        can_retry: false,
      });
    }
    throw new Error(`Unexpected API path: ${path}`);
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <CustomersView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
  apiUploadMock.mockReset();
  idempotencyKeyMock.mockReset();
  idempotencyKeyMock.mockReturnValue("customer-import-test-key");
  rejectFirstBackgroundCancel = false;
  backgroundCancelRequestCount = 0;
  showFailedBackgroundImport = false;
});

describe("customer workspace", () => {
  it("allows only owners and managers to manage the customer base", () => {
    expect(canManageCustomers("tenant_owner")).toBe(true);
    expect(canManageCustomers("tenant_manager")).toBe(true);
    expect(canManageCustomers("human_operator")).toBe(false);
    expect(canManageCustomers("analyst")).toBe(false);
  });

  it("shows customer filters and the expanded customer row", async () => {
    renderCustomers();
    await waitFor(() =>
      expect(
        apiRequestMock.mock.calls.some(
          ([path]) =>
            typeof path === "string" &&
            path.startsWith("/customers?") &&
            path.includes("project_id="),
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("Алишер Каримов")).toBeInTheDocument();
    expect(screen.getByText("+998901234567")).toBeInTheDocument();
    expect(screen.getByText("client@example.com")).toBeInTheDocument();
    expect(screen.getByLabelText("Проект клиентов")).toBeInTheDocument();
    expect(screen.getByLabelText("Архив клиентов")).toBeInTheDocument();
  });

  it("shows an explicit permission state to an operator", async () => {
    renderCustomers("human_operator");
    expect(
      await screen.findByText("Нет доступа к клиентской базе"),
    ).toBeInTheDocument();
    expect(apiRequestMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/customers?"),
    );
  });

  it("serializes contacts, tags and next contact for the API", () => {
    const form: CustomerForm = {
      project_id: project.id,
      display_name: "Клиент",
      preferred_language: "ru",
      external_reference: " EXT-1 ",
      status: "new",
      city: "",
      region: "",
      address: "",
      job_title: "",
      organization: "",
      tags: "VIP, Retail",
      description: "",
      source: "",
      assigned_user_id: "",
      next_contact_at: "2026-08-01T10:30",
      contacts: [
        {
          kind: "phone",
          value: " +998901234567 ",
          label: "Мобильный",
          is_primary: true,
        },
        { kind: "email", value: "", label: "", is_primary: true },
      ],
      custom_fields: { segment: "retail" },
    };
    const payload = customerFormToPayload(form);
    expect(payload.tags).toEqual(["VIP", "Retail"]);
    expect(payload.contacts).toHaveLength(1);
    expect(payload.contacts[0]?.value).toBe("+998901234567");
    expect(payload.next_contact_at).toContain("2026-08-01");
  });

  it("uploads a CSV as FormData and renders preview status", async () => {
    renderCustomers();
    fireEvent.click(await screen.findByRole("button", { name: /Импорт/ }));
    const input = screen.getByLabelText(/Выберите CSV или XLSX/);
    const file = new File(
      ["ФИО,Телефон\nКлиент,+998901234567"],
      "clients.csv",
      {
        type: "text/csv",
      },
    );
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Создать preview" }));
    expect(await screen.findByText("1 готово")).toBeInTheDocument();
    await waitFor(() => {
      const call = apiRequestMock.mock.calls.find(
        ([path]) => path === "/customers/import/preview",
      );
      expect(call?.[1]?.body).toBeInstanceOf(FormData);
    });
  });

  it("recovers a background import and sends an idempotent cancellation", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    renderCustomers();
    fireEvent.click(await screen.findByRole("button", { name: /Импорт/ }));
    fireEvent.click(
      screen.getByRole("tab", { name: "Большой фоновый импорт" }),
    );

    expect(await screen.findByText("large-clients.csv")).toBeInTheDocument();
    expect(
      await screen.findByText(/42% · резервное обновление/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));

    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        `/customers/import/background/${backgroundImport.id}/cancel`,
        expect.objectContaining({
          method: "POST",
          headers: { "Idempotency-Key": "customer-import-test-key" },
        }),
      ),
    );
    fireEvent.click(screen.getByRole("button", { name: "Скачать отчёт" }));
    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        "https://storage.example/report.csv",
        "_blank",
        "noopener,noreferrer",
      ),
    );
  });

  it("reuses the cancellation key when the first response is lost", async () => {
    rejectFirstBackgroundCancel = true;
    idempotencyKeyMock.mockReturnValueOnce("customer-cancel-lost-response-key");
    renderCustomers();
    fireEvent.click(await screen.findByRole("button", { name: /Импорт/ }));
    fireEvent.click(
      screen.getByRole("tab", { name: "Большой фоновый импорт" }),
    );

    expect(await screen.findByText("large-clients.csv")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Отменить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("lost response");
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));

    await waitFor(() => expect(backgroundCancelRequestCount).toBe(2));
    const cancelCalls = apiRequestMock.mock.calls.filter(
      ([path]) =>
        path === `/customers/import/background/${backgroundImport.id}/cancel`,
    );
    expect(cancelCalls).toHaveLength(2);
    expect(cancelCalls[0]?.[1]?.headers).toEqual({
      "Idempotency-Key": "customer-cancel-lost-response-key",
    });
    expect(cancelCalls[1]?.[1]?.headers).toEqual(cancelCalls[0]?.[1]?.headers);
    expect(idempotencyKeyMock).toHaveBeenCalledTimes(1);
  });

  it("retries a failed background import through its domain endpoint", async () => {
    showFailedBackgroundImport = true;
    renderCustomers();
    fireEvent.click(await screen.findByRole("button", { name: /Импорт/ }));
    fireEvent.click(
      screen.getByRole("tab", { name: "Большой фоновый импорт" }),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Повторить обработку" }),
    );
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        `/customers/import/background/${backgroundImport.id}/retry`,
        expect.objectContaining({
          method: "POST",
          headers: { "Idempotency-Key": "customer-import-test-key" },
        }),
      ),
    );
  });
});
