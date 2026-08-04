import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeView } from "@/components/knowledge-view";
import type {
  AuthResponse,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeRetrieval,
  Project,
} from "@/lib/types";

const apiRequestMock = vi.hoisted(() => vi.fn());
const apiUploadMock = vi.hoisted(() => vi.fn());

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
  idempotencyKey: () => "knowledge-test-idempotency",
}));

const project = {
  id: "project-id",
  name: "Поддержка",
} as Project;

const draft = {
  id: "revision-draft",
  knowledge_base_id: "base-id",
  project_id: project.id,
  version: 2,
  status: "draft" as const,
  lock_version: 4,
  created_from_revision_id: "revision-published",
  embedding_provider: "mock",
  embedding_model: "kline-deterministic-v1",
  embedding_dimension: 64,
  embedding_status: "development" as const,
  index_version: "kb-index-v2",
  published_at: null,
  created_at: "2026-08-03T10:00:00Z",
};

const published = {
  ...draft,
  id: "revision-published",
  version: 1,
  status: "published" as const,
  created_from_revision_id: null,
  published_at: "2026-08-02T10:00:00Z",
};

const base: KnowledgeBase = {
  id: "base-id",
  project_id: project.id,
  name: "База поддержки",
  description: "Проверенные инструкции",
  default_language_code: "ru",
  active_revision_id: published.id,
  archived_at: null,
  lock_version: 3,
  draft_revision: draft,
  published_revision: published,
  document_count: 1,
  created_at: "2026-08-01T10:00:00Z",
};

const document: KnowledgeDocument = {
  id: "document-id",
  source_id: base.id,
  project_id: project.id,
  title: "Рабочее время",
  language: "ru",
  content: "",
  archived_at: null,
  created_at: "2026-08-03T10:00:00Z",
  version: {
    id: "document-version-id",
    document_id: "document-id",
    revision_id: draft.id,
    version: 2,
    status: "ready",
    title_snapshot: "Рабочее время",
    language_code: "ru",
    original_filename: "hours.pdf",
    has_original: true,
    file_type: "pdf",
    content_type: "application/pdf",
    checksum_sha256: "a".repeat(64),
    content_length: 4096,
    page_count: 2,
    chunk_count: 3,
    safe_error_code: null,
    safe_error_message: null,
    lock_version: 5,
    created_at: "2026-08-03T10:00:00Z",
  },
};

const retrieval: KnowledgeRetrieval = {
  revision_id: published.id,
  hits: [
    {
      chunk_id: "chunk-id",
      document_id: document.id,
      document_version_id: document.version!.id,
      document_version: 1,
      title: document.title,
      language: "ru",
      page: 2,
      section: "График",
      excerpt: "Поддержка работает с девяти до восемнадцати.",
      lexical_score: 0.75,
      vector_score: 0.82,
      combined_score: 0.792,
      revision_id: published.id,
    },
  ],
  no_match: false,
  provider: "mock",
  model: "kline-deterministic-v1",
  provider_status: "development",
  index_version: "kb-index-v1",
  notice: "Deterministic retrieval mode — no generative LLM was used",
  usage: { input_items: 1, input_characters: 28, estimated_tokens: 4 },
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

function mockApi(role: AuthResponse["user"]["role"] = "tenant_owner") {
  apiRequestMock.mockImplementation((path: string, init?: RequestInit) => {
    if (path === "/auth/me") return Promise.resolve(auth(role));
    if (path === "/projects?limit=100")
      return Promise.resolve({
        items: [project],
        total: 1,
        limit: 100,
        offset: 0,
      });
    if (path.startsWith("/knowledge/bases?"))
      return Promise.resolve({
        items: [{ ...base, draft_revision: role === "analyst" ? null : draft }],
        total: 1,
        limit: 100,
        offset: 0,
      });
    if (path === `/knowledge/bases/${base.id}/revisions`)
      return Promise.resolve(
        role === "analyst" ? [published] : [draft, published],
      );
    if (path.startsWith("/knowledge/documents?"))
      return Promise.resolve({
        items: [document],
        total: 1,
        limit: 100,
        offset: 0,
      });
    if (path === "/knowledge/retrieval/test" && init?.method === "POST")
      return Promise.resolve(retrieval);
    if (path === "/knowledge/citations/chunk-id")
      return Promise.resolve({
        title: "Рабочее время",
        text: "Поддержка работает с девяти до восемнадцати.",
        page: 2,
      });
    if (path === `/knowledge/revisions/${draft.id}/publish`)
      return Promise.resolve({ ...draft, status: "published" });
    if (path === `/knowledge/documents/${document.version!.id}/archive`)
      return Promise.resolve({ ...document.version!, status: "archived" });
    throw new Error(`Unexpected API path: ${path}`);
  });
}

function renderKnowledge() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={queryClient}>
      <KnowledgeView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
  apiUploadMock.mockReset();
});

describe("knowledge workspace", () => {
  it("shows revision, document processing metadata and publishes a ready draft", async () => {
    mockApi();
    renderKnowledge();
    expect(await screen.findByText("База поддержки")).toBeInTheDocument();
    expect(await screen.findByText("Рабочее время")).toBeInTheDocument();
    expect(screen.getByText(/2 стр/)).toBeInTheDocument();
    expect(screen.getByText(/3 chunks/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Опубликовать/ }));
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        `/knowledge/revisions/${draft.id}/publish`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("runs retrieval and opens the exact citation fragment", async () => {
    mockApi();
    renderKnowledge();
    const input = await screen.findByLabelText("Вопрос для базы знаний");
    fireEvent.change(input, { target: { value: "Когда работает поддержка?" } });
    fireEvent.click(screen.getByRole("button", { name: "Найти" }));
    expect(await screen.findByText(/score 0.792/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Источник" }));
    expect(await screen.findByText("Страница 2")).toBeInTheDocument();
    expect(
      screen.getAllByText("Поддержка работает с девяти до восемнадцати."),
    ).toHaveLength(2);
  });

  it("shows browser upload progress separately from worker status", async () => {
    mockApi();
    apiUploadMock.mockImplementation(
      (
        _path: string,
        _body: FormData,
        _headers: object,
        progress: (value: number) => void,
      ) => {
        progress(64);
        return Promise.resolve(document);
      },
    );
    renderKnowledge();
    const fileInput = await screen.findByLabelText(
      /Выберите или перетащите файл/,
    );
    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(["knowledge"], "knowledge.txt", { type: "text/plain" }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Загрузить" }));
    expect(
      await screen.findByText(/Браузер загрузил 100%/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Фоновая обработка показана/)).toBeInTheDocument();
  });

  it("keeps analyst mode read-only and exposes only the published revision", async () => {
    mockApi("analyst");
    renderKnowledge();
    expect(await screen.findByText("База поддержки")).toBeInTheDocument();
    expect(screen.queryByText("Новая база")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Опубликовать/ }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("v2")).not.toBeInTheDocument();
    expect(await screen.findByText("v1")).toBeInTheDocument();
  });

  it("uploads a new immutable document version and exposes archive controls", async () => {
    mockApi();
    apiUploadMock.mockResolvedValue(document);
    renderKnowledge();

    fireEvent.click(
      await screen.findByRole("button", { name: /Новая версия/ }),
    );
    expect(screen.getByText(/Новая версия документа/)).toBeInTheDocument();
    const fileInput = screen.getByLabelText(/Выберите или перетащите файл/);
    fireEvent.change(fileInput, {
      target: {
        files: [
          new File(["updated knowledge"], "knowledge.txt", {
            type: "text/plain",
          }),
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Загрузить" }));
    await waitFor(() => expect(apiUploadMock).toHaveBeenCalledTimes(1));
    const form = apiUploadMock.mock.calls[0]?.[1] as FormData;
    expect(form.get("document_id")).toBe(document.id);
    expect(form.get("document_expected_version")).toBe(
      String(document.version!.lock_version),
    );

    fireEvent.click(screen.getAllByRole("button", { name: /В архив/ })[1]!);
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        `/knowledge/documents/${document.version!.id}/archive`,
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });
});
