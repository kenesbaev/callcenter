import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CallFlowEditor } from "@/components/call-flow-editor";
import type {
  AuthResponse,
  CallFlow,
  CallFlowPreview,
  CallFlowVersion,
  Project,
} from "@/lib/types";

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
  id: "project-id",
  name: "Карточная кампания",
  description: "",
  status: "active",
  default_language: "ru",
  timezone: null,
  outbound_number: null,
  outbound_phone_number_id: null,
  inbound_phone_number_ids: [],
  ai_operator_id: null,
  knowledge_source_id: null,
  call_flow_id: "flow-id",
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

const version: CallFlowVersion = {
  id: "version-id",
  call_flow_id: "flow-id",
  project_id: "project-id",
  version: 1,
  status: "draft",
  lock_version: 1,
  created_from_version_id: null,
  published_at: null,
  created_at: "2026-07-29T10:00:00Z",
  updated_at: "2026-07-29T10:00:00Z",
  definition: {
    schema_version: 1,
    nodes: [
      {
        id: "node-start",
        system_key: "start_node",
        name: "Приветствие",
        node_type: "start",
        text_by_language: { ru: "Здравствуйте", uz: "Assalomu alaykum" },
        hint_by_language: {
          ru: "Представьтесь",
          uz: "O'zingizni tanishtiring",
        },
        order: 10,
        is_required: true,
        customer_field_definition_id: null,
        answers: [],
        next_node_id: "node-question",
        fallback_node_id: null,
        action_config: {},
      },
      {
        id: "node-question",
        system_key: "agreement_question",
        name: "Согласие",
        node_type: "customer_question",
        text_by_language: { ru: "Вы согласны?", uz: "Rozimisiz?" },
        hint_by_language: {},
        order: 20,
        is_required: true,
        customer_field_definition_id: null,
        answers: [
          {
            id: "answer-yes",
            key: "yes",
            label_by_language: { ru: "Да", uz: "Ha" },
            next_node_id: "node-end",
            is_required: true,
          },
          {
            id: "answer-no",
            key: "no",
            label_by_language: { ru: "Нет", uz: "Yo'q" },
            next_node_id: "node-end",
            is_required: true,
          },
        ],
        next_node_id: null,
        fallback_node_id: "node-end",
        action_config: {},
      },
      {
        id: "node-end",
        system_key: "end_node",
        name: "Завершение",
        node_type: "end",
        text_by_language: { ru: "Спасибо", uz: "Rahmat" },
        hint_by_language: {},
        order: 30,
        is_required: false,
        customer_field_definition_id: null,
        answers: [],
        next_node_id: null,
        fallback_node_id: null,
        action_config: {},
      },
    ],
  },
};

const flow: CallFlow = {
  id: "flow-id",
  project_id: "project-id",
  name: "Основной сценарий",
  description: "",
  default_language_code: "ru",
  language_codes: ["ru", "uz"],
  active_version_id: null,
  is_active: true,
  archived_at: null,
  versions: [
    {
      id: version.id,
      version: version.version,
      status: version.status,
      lock_version: version.lock_version,
      created_from_version_id: null,
      published_at: null,
      created_at: version.created_at,
      updated_at: version.updated_at,
    },
  ],
  created_at: "2026-07-29T10:00:00Z",
  updated_at: "2026-07-29T10:00:00Z",
};

function auth(role: AuthResponse["user"]["role"]): AuthResponse {
  return {
    user: {
      id: "user-id",
      email: "owner@example.com",
      display_name: "Owner",
      role,
    },
    tenant: { id: "tenant-id", name: "K-Line", slug: "k-line" },
    csrf_token: "csrf",
  };
}

function mockBase(role: AuthResponse["user"]["role"] = "tenant_owner") {
  apiRequestMock.mockImplementation((path: string, init?: RequestInit) => {
    if (path === "/auth/me") return Promise.resolve(auth(role));
    if (path === "/projects/project-id") return Promise.resolve(project);
    if (path.startsWith("/call-flows?project_id="))
      return Promise.resolve([flow]);
    if (path === "/call-flows/flow-id/versions/version-id" && !init)
      return Promise.resolve({
        ...version,
        status: role === "human_operator" ? "published" : version.status,
      });
    if (path.startsWith("/customers/fields?")) return Promise.resolve([]);
    throw new Error(`Unexpected API path: ${path}`);
  });
}

function renderEditor() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <CallFlowEditor projectId="project-id" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
  vi.restoreAllMocks();
});

describe("call flow editor", () => {
  it("shows an empty state and lets a manager start creating a scenario", async () => {
    apiRequestMock.mockImplementation((path: string) => {
      if (path === "/auth/me") return Promise.resolve(auth("tenant_manager"));
      if (path === "/projects/project-id")
        return Promise.resolve({ ...project, call_flow_id: null });
      if (path.startsWith("/call-flows?project_id="))
        return Promise.resolve([]);
      throw new Error(`Unexpected API path: ${path}`);
    });
    renderEditor();
    expect(await screen.findByText("Сценариев пока нет")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Создать сценарий" }));
    expect(
      screen.getByRole("region", { name: "Создание сценария" }),
    ).toBeInTheDocument();
  });

  it("lets an owner edit and save a draft with optimistic lock", async () => {
    mockBase();
    apiRequestMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/auth/me") return Promise.resolve(auth("tenant_owner"));
      if (path === "/projects/project-id") return Promise.resolve(project);
      if (path.startsWith("/call-flows?project_id="))
        return Promise.resolve([flow]);
      if (path === "/call-flows/flow-id/versions/version-id" && !init)
        return Promise.resolve(version);
      if (path.startsWith("/customers/fields?")) return Promise.resolve([]);
      if (
        path === "/call-flows/flow-id/versions/version-id" &&
        init?.method === "PUT"
      ) {
        return Promise.resolve({ ...version, lock_version: 2 });
      }
      throw new Error(`Unexpected API path: ${path}`);
    });
    renderEditor();
    const text = await screen.findByLabelText("Текст · RU");
    fireEvent.change(text, { target: { value: "Добрый день" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        "/call-flows/flow-id/versions/version-id",
        expect.objectContaining({
          method: "PUT",
          body: expect.stringContaining('"expected_lock_version":1'),
        }),
      ),
    );
    expect(await screen.findByText("Черновик сохранён")).toBeInTheDocument();
  });

  it("keeps a published scenario read-only for an operator", async () => {
    mockBase("human_operator");
    renderEditor();
    const name = await screen.findByLabelText("Название");
    expect(name).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Сохранить" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Опубликовать" }),
    ).not.toBeInTheDocument();
  });

  it("switches language without losing the selected version", async () => {
    mockBase();
    renderEditor();
    expect(await screen.findByLabelText("Текст · RU")).toHaveValue(
      "Здравствуйте",
    );
    fireEvent.change(screen.getByLabelText("Язык редактора"), {
      target: { value: "uz" },
    });
    expect(await screen.findByLabelText("Текст · UZ")).toHaveValue(
      "Assalomu alaykum",
    );
  });

  it("runs a safe preview through the selected answer", async () => {
    mockBase();
    const firstPreview: CallFlowPreview = {
      language_code: "ru",
      path: [
        {
          id: "node-start",
          system_key: "start_node",
          name: "Приветствие",
          node_type: "start",
          text: "Здравствуйте",
          hint: "",
          answers: [],
          action_is_inert: false,
        },
      ],
      current_node: {
        id: "node-question",
        system_key: "agreement_question",
        name: "Согласие",
        node_type: "customer_question",
        text: "Вы согласны?",
        hint: "",
        answers: [
          { key: "yes", label: "Да" },
          { key: "no", label: "Нет" },
        ],
        action_is_inert: false,
      },
      completed: false,
      inert_actions: [],
    };
    apiRequestMock.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/auth/me") return Promise.resolve(auth("tenant_owner"));
      if (path === "/projects/project-id") return Promise.resolve(project);
      if (path.startsWith("/call-flows?project_id="))
        return Promise.resolve([flow]);
      if (path === "/call-flows/flow-id/versions/version-id" && !init)
        return Promise.resolve(version);
      if (path.startsWith("/customers/fields?")) return Promise.resolve([]);
      if (path.endsWith("/preview")) {
        const request = JSON.parse(String(init?.body)) as {
          answers: unknown[];
        };
        return Promise.resolve(
          request.answers.length
            ? { ...firstPreview, current_node: null, completed: true }
            : firstPreview,
        );
      }
      throw new Error(`Unexpected API path: ${path}`);
    });
    renderEditor();
    fireEvent.click(
      await screen.findByRole("button", { name: "Запустить preview" }),
    );
    expect(await screen.findByText("Вы согласны?")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Да" }));
    expect(await screen.findByText("Сценарий завершён")).toBeInTheDocument();
    expect(apiRequestMock).toHaveBeenLastCalledWith(
      "/call-flows/flow-id/versions/version-id/preview",
      expect.objectContaining({
        body: expect.stringContaining('"answer_key":"yes"'),
      }),
    );
  });
});
