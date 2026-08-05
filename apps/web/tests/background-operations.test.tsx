import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsView } from "@/components/settings-view";
import type {
  BackgroundJobDetail,
  BackgroundJobStats,
  LegalHold,
  RetentionPolicy,
  Role,
  StorageSummary,
} from "@/lib/types";

const apiRequestMock = vi.hoisted(() => vi.fn());
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
  idempotencyKey: idempotencyKeyMock,
}));

let rejectFirstJobRetry = false;
let jobRetryRequestCount = 0;

const job: BackgroundJobDetail = {
  id: "job-id",
  project_id: null,
  type: "customer_import",
  queue: "imports",
  priority: 50,
  status: "running",
  progress: 62,
  attempt_count: 1,
  max_attempts: 4,
  scheduled_at: null,
  available_at: "2026-08-04T08:00:00Z",
  started_at: "2026-08-04T08:00:01Z",
  completed_at: null,
  cancelled_at: null,
  safe_error_code: null,
  state_version: 3,
  created_at: "2026-08-04T08:00:00Z",
  updated_at: "2026-08-04T08:00:02Z",
  can_cancel: true,
  can_retry: false,
  correlation_id: "correlation-id",
  causation_id: null,
  payload_summary: { import_id: "import-id" },
  result_metadata: {},
  safe_error_message: null,
};

const retryableJob: BackgroundJobDetail = {
  ...job,
  id: "dead-letter-job",
  status: "dead_letter",
  progress: 100,
  attempt_count: 4,
  can_cancel: false,
  can_retry: true,
  safe_error_code: "import_validation_failed",
  safe_error_message: "Импорт требует ручного повтора",
};

const stats: BackgroundJobStats = {
  queued: 2,
  running: 1,
  retry_wait: 3,
  completed: 9,
  failed: 0,
  dead_letter: 1,
  cancel_requested: 0,
  oldest_pending_at: "2026-08-04T07:00:00Z",
  can_manage: true,
};

const storage: StorageSummary = {
  object_count: 12,
  total_bytes: 5_242_880,
  issue_count: 1,
  pending_purge_count: 2,
  categories: [{ category: "import_source", object_count: 2, bytes: 1_024 }],
  last_scan_at: "2026-08-04T08:00:00Z",
  can_scan: true,
};

const policy: RetentionPolicy = {
  id: "policy-id",
  automatic_purge_enabled: false,
  grace_period_days: 14,
  call_recording_days: null,
  transcript_days: null,
  temporary_import_days: 7,
  import_report_days: 30,
  archived_knowledge_days: null,
  realtime_event_hours: 24,
  completed_job_days: 30,
  failed_job_days: 90,
  state_version: 2,
  updated_at: "2026-08-04T08:00:00Z",
  can_manage: true,
};

const hold: LegalHold = {
  id: "hold-id",
  project_id: null,
  scope_type: "tenant",
  scope_id: null,
  reason: "Судебный запрос",
  created_by_user_id: "owner-id",
  created_at: "2026-08-04T08:00:00Z",
  released_by_user_id: null,
  released_at: null,
  can_release: true,
  state_version: 1,
};

let currentRole: Role = "tenant_owner";

function mockApi() {
  apiRequestMock.mockImplementation((path: string, init?: RequestInit) => {
    if (path === "/auth/me")
      return Promise.resolve({
        user: {
          id: "user-id",
          email: "user@example.com",
          display_name: "User",
          role: currentRole,
        },
        tenant: { id: "tenant-id", name: "K-Line", slug: "k-line" },
        csrf_token: "csrf",
      });
    if (path === "/settings")
      return Promise.resolve({
        timezone: "Asia/Tashkent",
        default_language: "ru",
        recording_enabled: true,
        recording_disclosure_required: true,
        retention_days: 90,
        max_concurrent_calls: 5,
        updated_at: "2026-08-04T08:00:00Z",
      });
    if (path === "/background-jobs/stats") return Promise.resolve(stats);
    if (path.startsWith("/background-jobs/dead-letter?"))
      return Promise.resolve({
        items: [retryableJob],
        total: 1,
        limit: 25,
        offset: 0,
      });
    if (path.startsWith("/background-jobs?"))
      return Promise.resolve({ items: [job], total: 1, limit: 25, offset: 0 });
    if (path === `/background-jobs/${retryableJob.id}`)
      return Promise.resolve(retryableJob);
    if (path === `/background-jobs/${retryableJob.id}/attempts`)
      return Promise.resolve([]);
    if (path === `/background-jobs/${retryableJob.id}/events`)
      return Promise.resolve([]);
    if (
      path === `/background-jobs/${retryableJob.id}/retry` &&
      init?.method === "POST"
    ) {
      jobRetryRequestCount += 1;
      if (rejectFirstJobRetry && jobRetryRequestCount === 1) {
        return Promise.reject(new Error("lost response"));
      }
      return Promise.resolve({
        ...retryableJob,
        status: "pending",
        can_retry: false,
      });
    }
    if (path === `/background-jobs/${job.id}`) return Promise.resolve(job);
    if (path === `/background-jobs/${job.id}/attempts`)
      return Promise.resolve([
        {
          id: "attempt-id",
          job_id: job.id,
          attempt: 1,
          status: "running",
          worker_id: "worker-1",
          started_at: job.started_at,
          completed_at: null,
          duration_ms: null,
          safe_error_code: null,
          safe_error_message: null,
        },
      ]);
    if (path === `/background-jobs/${job.id}/events`)
      return Promise.resolve([
        {
          id: "event-id",
          job_id: job.id,
          event_type: "job.started",
          progress: 0,
          safe_snapshot: {},
          occurred_at: job.started_at,
        },
      ]);
    if (path === `/background-jobs/${job.id}/cancel` && init?.method === "POST")
      return Promise.resolve({ ...job, status: "cancel_requested" });
    if (path === "/storage/summary") return Promise.resolve(storage);
    if (path === "/storage/issues?limit=100")
      return Promise.resolve({ items: [], total: 0, limit: 100, offset: 0 });
    if (path === "/storage/scan" && init?.method === "POST")
      return Promise.resolve({ job_id: "scan-job" });
    if (path === "/retention/policy") return Promise.resolve(policy);
    if (path === "/retention/candidates?limit=100")
      return Promise.resolve({ items: [], total: 0, limit: 100, offset: 0 });
    if (path === "/retention/preview" && init?.method === "POST")
      return Promise.resolve({
        id: "preview-id",
        policy_version: 2,
        eligible_objects: 4,
        eligible_bytes: 2048,
        excluded_by_legal_hold: 1,
        excluded_by_active_reference: 2,
        created_at: "2026-08-04T08:10:00Z",
      });
    if (path === "/legal-holds?limit=100")
      return Promise.resolve({
        items: [hold],
        total: 1,
        limit: 100,
        offset: 0,
      });
    if (path === "/legal-holds" && init?.method === "POST")
      return Promise.resolve(hold);
    throw new Error(`Unexpected API path: ${path}`);
  });
}

function renderSettings() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <SettingsView />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  apiRequestMock.mockReset();
  idempotencyKeyMock.mockReset();
  idempotencyKeyMock.mockReturnValue("operations-test-key");
  currentRole = "tenant_owner";
  rejectFirstJobRetry = false;
  jobRetryRequestCount = 0;
  mockApi();
});

describe("background operations workspace", () => {
  it("lists a durable job, opens its history, and sends an idempotent cancel", async () => {
    renderSettings();
    fireEvent.click(
      await screen.findByRole("tab", { name: "Background Jobs" }),
    );
    expect(await screen.findByText("customer_import")).toBeInTheDocument();
    expect(screen.getByText("62%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Открыть" }));
    expect(await screen.findByText("job.started")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Отменить" }));
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        "/background-jobs/job-id/cancel",
        expect.objectContaining({
          method: "POST",
          headers: { "Idempotency-Key": "operations-test-key" },
        }),
      ),
    );
  });

  it("starts a MinIO consistency dry-run through the protected command", async () => {
    renderSettings();
    fireEvent.click(await screen.findByRole("tab", { name: "Storage" }));
    expect(await screen.findByText("5.0 МБ")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dry-run scan" }));
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        "/storage/scan",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("loads the dead-letter queue and sends an idempotent manual retry", async () => {
    renderSettings();
    fireEvent.click(
      await screen.findByRole("tab", { name: "Background Jobs" }),
    );
    fireEvent.change(screen.getByLabelText("Статус фоновой задачи"), {
      target: { value: "dead_letter" },
    });
    expect(await screen.findByText("dead-let")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Открыть" }));
    expect(
      await screen.findByText(/import_validation_failed/),
    ).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Повторить" }));
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        "/background-jobs/dead-letter-job/retry",
        expect.objectContaining({
          method: "POST",
          headers: { "Idempotency-Key": "operations-test-key" },
          body: JSON.stringify({ expected_version: 3 }),
        }),
      ),
    );
  });

  it("reuses the manual retry key when the first response is lost", async () => {
    rejectFirstJobRetry = true;
    idempotencyKeyMock.mockReturnValueOnce("job-retry-lost-response-key");
    renderSettings();
    fireEvent.click(
      await screen.findByRole("tab", { name: "Background Jobs" }),
    );
    fireEvent.change(screen.getByLabelText("Статус фоновой задачи"), {
      target: { value: "dead_letter" },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Открыть" }));
    fireEvent.click(await screen.findByRole("button", { name: "Повторить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("lost response");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(jobRetryRequestCount).toBe(2));
    const retryCalls = apiRequestMock.mock.calls.filter(
      ([path]) => path === `/background-jobs/${retryableJob.id}/retry`,
    );
    expect(retryCalls).toHaveLength(2);
    expect(retryCalls[0]?.[1]?.headers).toEqual({
      "Idempotency-Key": "job-retry-lost-response-key",
    });
    expect(retryCalls[1]?.[1]?.headers).toEqual(retryCalls[0]?.[1]?.headers);
    expect(idempotencyKeyMock).toHaveBeenCalledTimes(1);
  });

  it("shows a two-stage retention preview without enabling purge by default", async () => {
    renderSettings();
    fireEvent.click(await screen.findByRole("tab", { name: "Retention" }));
    expect(await screen.findByText("Auto purge выключен")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Dry-run preview" }));
    expect(await screen.findByText(/4 объектов/)).toBeInTheDocument();
    expect(screen.getByText(/Legal hold исключил: 1/)).toBeInTheDocument();
  });

  it("keeps an analyst in read-only operational tabs", async () => {
    currentRole = "analyst";
    renderSettings();
    expect(
      await screen.findByRole("tab", { name: "Background Jobs" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "Основные" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "Retention" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "Legal Holds" }),
    ).not.toBeInTheDocument();
  });

  it("renders an active legal hold and allows an owner to create another", async () => {
    renderSettings();
    fireEvent.click(await screen.findByRole("tab", { name: "Legal Holds" }));
    expect(await screen.findByText("Судебный запрос")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Причина"), {
      target: { value: "Новый запрос регулятора" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Установить hold" }));
    await waitFor(() =>
      expect(apiRequestMock).toHaveBeenCalledWith(
        "/legal-holds",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });
});
