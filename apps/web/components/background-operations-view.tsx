"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArchiveRestore,
  Ban,
  Database,
  HardDrive,
  Play,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { useRealtime } from "@/components/realtime-provider";
import { ApiClientError, apiRequest } from "@/lib/api";
import {
  createLogicalMutationKeyStore,
  type LogicalMutationKeyStore,
} from "@/lib/logical-mutation-key";
import type {
  BackgroundJobAttempt,
  BackgroundJobDetail,
  BackgroundJobEvent,
  BackgroundJobStats,
  BackgroundJobStatus,
  BackgroundJobSummary,
  LegalHold,
  Page,
  RetentionCandidate,
  RetentionPolicy,
  RetentionPreview,
  Role,
  StorageIssue,
  StorageSummary,
} from "@/lib/types";

export type OperationsSection =
  "jobs" | "storage" | "retention" | "legal-holds";

const jobStatusLabels: Record<BackgroundJobStatus, string> = {
  pending: "В очереди",
  scheduled: "Запланирована",
  running: "Выполняется",
  retry_wait: "Ожидает повтора",
  completed: "Завершена",
  failed: "Ошибка",
  dead_letter: "Dead-letter",
  cancel_requested: "Отмена запрошена",
  cancelled: "Отменена",
};

function jobTone(status: BackgroundJobStatus) {
  if (status === "completed") return "success" as const;
  if (status === "failed" || status === "dead_letter") return "danger" as const;
  if (status === "cancelled") return "neutral" as const;
  return "warning" as const;
}

function message(error: unknown): string {
  return error instanceof ApiClientError
    ? error.message
    : error instanceof Error
      ? error.message
      : "Операция не выполнена";
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} Б`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} КБ`;
  if (value < 1024 * 1024 * 1024)
    return `${(value / (1024 * 1024)).toFixed(1)} МБ`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} ГБ`;
}

export function BackgroundOperationsView({
  section,
  role,
}: {
  section: OperationsSection;
  role: Role;
}) {
  const [mutationKeys] = useState(createLogicalMutationKeyStore);

  if (section === "jobs")
    return <JobsPanel mutationKeys={mutationKeys} role={role} />;
  if (section === "storage")
    return <StoragePanel mutationKeys={mutationKeys} role={role} />;
  if (section === "retention")
    return <RetentionPanel mutationKeys={mutationKeys} role={role} />;
  return <LegalHoldsPanel mutationKeys={mutationKeys} role={role} />;
}

type OperationsPanelProps = {
  mutationKeys: LogicalMutationKeyStore;
  role: Role;
};

function JobsPanel({ mutationKeys, role }: OperationsPanelProps) {
  const queryClient = useQueryClient();
  const realtime = useRealtime();
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [error, setError] = useState("");
  const limit = 25;
  const listPath =
    status === "dead_letter"
      ? "/background-jobs/dead-letter"
      : "/background-jobs";
  const jobs = useQuery({
    queryKey: ["background-jobs", "list", status, offset],
    queryFn: () => {
      const params = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
      });
      if (status && status !== "dead_letter") params.set("status", status);
      return apiRequest<Page<BackgroundJobSummary>>(`${listPath}?${params}`);
    },
    refetchInterval: realtime.connected ? false : 15_000,
  });
  const stats = useQuery({
    queryKey: ["background-jobs", "stats"],
    queryFn: () => apiRequest<BackgroundJobStats>("/background-jobs/stats"),
    refetchInterval: realtime.connected ? false : 15_000,
  });
  const detail = useQuery({
    queryKey: ["background-jobs", selectedId],
    queryFn: () =>
      apiRequest<BackgroundJobDetail>(`/background-jobs/${selectedId}`),
    enabled: Boolean(selectedId),
  });
  const attempts = useQuery({
    queryKey: ["background-jobs", selectedId, "attempts"],
    queryFn: () =>
      apiRequest<BackgroundJobAttempt[]>(
        `/background-jobs/${selectedId}/attempts`,
      ),
    enabled: Boolean(selectedId),
  });
  const events = useQuery({
    queryKey: ["background-jobs", selectedId, "events"],
    queryFn: () =>
      apiRequest<BackgroundJobEvent[]>(`/background-jobs/${selectedId}/events`),
    enabled: Boolean(selectedId),
  });
  const refresh = async () =>
    queryClient.invalidateQueries({ queryKey: ["background-jobs"] });
  const command = useMutation({
    mutationFn: ({
      action,
      job,
    }: {
      action: "cancel" | "retry";
      job: BackgroundJobSummary;
    }) => {
      const prefix = `job-${action}`;
      return apiRequest<BackgroundJobSummary>(
        `/background-jobs/${job.id}/${action}`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": mutationKeys.get(
              prefix,
              JSON.stringify([job.id, job.state_version]),
            ),
          },
          body: JSON.stringify({ expected_version: job.state_version }),
        },
      );
    },
    onSuccess: async (_value, { action }) => {
      mutationKeys.reset(`job-${action}`);
      setError("");
      await refresh();
    },
    onError: (caught) => setError(message(caught)),
  });
  const canManage = stats.data?.can_manage ?? role === "tenant_owner";

  return (
    <section className="operations-grid">
      <div className="operations-main panel">
        <div className="row-between">
          <div>
            <h2>Фоновые задачи</h2>
            <p className="panel-subtitle">
              Durable PostgreSQL queue · доставка минимум один раз
            </p>
          </div>
          <div className="operations-filterbar">
            <select
              aria-label="Статус фоновой задачи"
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
              value={status}
            >
              <option value="">Все статусы</option>
              {Object.entries(jobStatusLabels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
            <Button onClick={() => void refresh()} variant="quiet">
              <RefreshCw size={14} /> Обновить
            </Button>
          </div>
        </div>
        {stats.data && (
          <div className="operations-stat-grid">
            <div>
              <span>В очереди</span>
              <strong>{stats.data.queued}</strong>
            </div>
            <div>
              <span>Выполняется</span>
              <strong>{stats.data.running}</strong>
            </div>
            <div>
              <span>Retry</span>
              <strong>{stats.data.retry_wait}</strong>
            </div>
            <div>
              <span>Dead-letter</span>
              <strong>{stats.data.dead_letter}</strong>
            </div>
          </div>
        )}
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        {jobs.isPending && <SectionSkeleton />}
        {jobs.isError && (
          <QueryError
            error={jobs.error}
            retry={() => void jobs.refetch()}
            title="Не удалось загрузить фоновые задачи"
          />
        )}
        {jobs.data?.items.length === 0 && (
          <div className="empty-state compact-empty">
            <Database size={28} />
            <p>В выбранной очереди задач нет.</p>
          </div>
        )}
        {jobs.data && jobs.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="data-table operations-table">
              <thead>
                <tr>
                  <th>Задача</th>
                  <th>Очередь</th>
                  <th>Статус</th>
                  <th>Прогресс</th>
                  <th>Попытки</th>
                  <th>Создана</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.data.items.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <strong>{job.type}</strong>
                      <small>{job.id.slice(0, 8)}</small>
                    </td>
                    <td>{job.queue}</td>
                    <td>
                      <StatusBadge tone={jobTone(job.status)}>
                        {jobStatusLabels[job.status]}
                      </StatusBadge>
                    </td>
                    <td>{job.progress}%</td>
                    <td>
                      {job.attempt_count}/{job.max_attempts}
                    </td>
                    <td>{new Date(job.created_at).toLocaleString("ru-RU")}</td>
                    <td>
                      <Button
                        onClick={() => setSelectedId(job.id)}
                        variant="quiet"
                      >
                        Открыть
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {jobs.data && (
          <div className="operations-pagination row-between">
            <span>
              Показано {jobs.data.items.length} из {jobs.data.total}
            </span>
            <div>
              <Button
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - limit))}
                variant="secondary"
              >
                Назад
              </Button>
              <Button
                disabled={offset + limit >= jobs.data.total}
                onClick={() => setOffset(offset + limit)}
                variant="secondary"
              >
                Далее
              </Button>
            </div>
          </div>
        )}
      </div>
      <aside className="operations-detail panel">
        {!selectedId && (
          <div className="empty-state compact-empty">
            <Database size={28} />
            <p>Выберите задачу для просмотра истории.</p>
          </div>
        )}
        {detail.isPending && selectedId && <SectionSkeleton />}
        {detail.data && (
          <>
            <div className="row-between">
              <div>
                <h2>{detail.data.type}</h2>
                <p className="panel-subtitle">{detail.data.id}</p>
              </div>
              <StatusBadge tone={jobTone(detail.data.status)}>
                {jobStatusLabels[detail.data.status]}
              </StatusBadge>
            </div>
            <div className="background-job-progress">
              <div>
                <span style={{ width: `${detail.data.progress}%` }} />
              </div>
              <small>
                {detail.data.progress}% · версия {detail.data.state_version}
              </small>
            </div>
            {detail.data.safe_error_message && (
              <div className="form-error">
                {detail.data.safe_error_code}: {detail.data.safe_error_message}
              </div>
            )}
            {canManage && (
              <div className="operations-actions">
                {detail.data.can_cancel && (
                  <Button
                    disabled={command.isPending}
                    onClick={() =>
                      command.mutate({ action: "cancel", job: detail.data })
                    }
                    variant="danger"
                  >
                    <Ban size={14} /> Отменить
                  </Button>
                )}
                {detail.data.can_retry && (
                  <Button
                    disabled={command.isPending}
                    onClick={() =>
                      command.mutate({ action: "retry", job: detail.data })
                    }
                    variant="secondary"
                  >
                    <RotateCcw size={14} /> Повторить
                  </Button>
                )}
              </div>
            )}
            <section className="operations-timeline">
              <h3>Попытки</h3>
              {attempts.data?.map((attempt) => (
                <div key={attempt.id}>
                  <strong>
                    #{attempt.attempt} · {attempt.status}
                  </strong>
                  <small>
                    {new Date(attempt.started_at).toLocaleString("ru-RU")}
                    {attempt.safe_error_code
                      ? ` · ${attempt.safe_error_code}`
                      : ""}
                  </small>
                </div>
              ))}
            </section>
            <section className="operations-timeline">
              <h3>События</h3>
              {events.data?.map((event) => (
                <div key={event.id}>
                  <strong>{event.event_type}</strong>
                  <small>
                    {new Date(event.occurred_at).toLocaleString("ru-RU")}
                  </small>
                </div>
              ))}
            </section>
          </>
        )}
      </aside>
    </section>
  );
}

function StoragePanel({ mutationKeys, role }: OperationsPanelProps) {
  const queryClient = useQueryClient();
  const realtime = useRealtime();
  const [error, setError] = useState("");
  const summary = useQuery({
    queryKey: ["storage", "summary"],
    queryFn: () => apiRequest<StorageSummary>("/storage/summary"),
    refetchInterval: realtime.connected ? false : 30_000,
  });
  const issues = useQuery({
    queryKey: ["storage", "issues"],
    queryFn: () => apiRequest<Page<StorageIssue>>("/storage/issues?limit=100"),
    refetchInterval: realtime.connected ? false : 30_000,
  });
  const scan = useMutation({
    mutationFn: () =>
      apiRequest<{ job_id: string }>("/storage/scan", {
        method: "POST",
        headers: {
          "Idempotency-Key": mutationKeys.get("storage-scan"),
        },
        body: JSON.stringify({ dry_run: true }),
      }),
    onSuccess: async () => {
      mutationKeys.reset("storage-scan");
      setError("");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["storage"] }),
        queryClient.invalidateQueries({ queryKey: ["background-jobs"] }),
      ]);
    },
    onError: (caught) => setError(message(caught)),
  });
  const canScan = summary.data?.can_scan ?? role === "tenant_owner";
  if (summary.isPending) return <SectionSkeleton />;
  if (summary.isError)
    return (
      <QueryError
        error={summary.error}
        retry={() => void summary.refetch()}
        title="Не удалось загрузить состояние хранилища"
      />
    );
  return (
    <section className="panel">
      <div className="row-between">
        <div>
          <h2>MinIO Storage</h2>
          <p className="panel-subtitle">
            Закрытый tenant-safe registry и consistency scan
          </p>
        </div>
        {canScan && (
          <Button disabled={scan.isPending} onClick={() => scan.mutate()}>
            <RefreshCw size={15} /> Dry-run scan
          </Button>
        )}
      </div>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      <div className="operations-stat-grid">
        <div>
          <span>Объекты</span>
          <strong>{summary.data.object_count}</strong>
        </div>
        <div>
          <span>Объём</span>
          <strong>{formatBytes(summary.data.total_bytes)}</strong>
        </div>
        <div>
          <span>Проблемы</span>
          <strong>{summary.data.issue_count}</strong>
        </div>
        <div>
          <span>Pending purge</span>
          <strong>{summary.data.pending_purge_count}</strong>
        </div>
      </div>
      <div className="storage-category-grid">
        {summary.data.categories.map((category) => (
          <article key={category.category}>
            <HardDrive size={18} />
            <div>
              <strong>{category.category}</strong>
              <small>
                {category.object_count} объектов · {formatBytes(category.bytes)}
              </small>
            </div>
          </article>
        ))}
      </div>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Проблема</th>
              <th>Категория</th>
              <th>Статус</th>
              <th>Обнаружена</th>
              <th>Grace period</th>
            </tr>
          </thead>
          <tbody>
            {issues.data?.items.map((issue) => (
              <tr key={issue.id}>
                <td>
                  <strong>{issue.issue_type}</strong>
                </td>
                <td>{issue.object_category}</td>
                <td>
                  <StatusBadge
                    tone={issue.status === "resolved" ? "success" : "warning"}
                  >
                    {issue.status}
                  </StatusBadge>
                </td>
                <td>{new Date(issue.detected_at).toLocaleString("ru-RU")}</td>
                <td>
                  {issue.grace_expires_at
                    ? new Date(issue.grace_expires_at).toLocaleString("ru-RU")
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {issues.data?.items.length === 0 && (
        <div className="empty-state compact-empty">
          <ShieldCheck size={28} />
          <p>Consistency issues не обнаружены.</p>
        </div>
      )}
    </section>
  );
}

function RetentionPanel({ mutationKeys, role }: OperationsPanelProps) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<RetentionPolicy | null>(null);
  const [preview, setPreview] = useState<RetentionPreview | null>(null);
  const [error, setError] = useState("");
  const policy = useQuery({
    queryKey: ["retention", "policy"],
    queryFn: () => apiRequest<RetentionPolicy>("/retention/policy"),
  });
  useEffect(() => {
    if (policy.data) setDraft(policy.data);
  }, [policy.data]);
  const candidates = useQuery({
    queryKey: ["retention", "candidates"],
    queryFn: () =>
      apiRequest<Page<RetentionCandidate>>("/retention/candidates?limit=100"),
  });
  const refresh = async () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["retention"] }),
      queryClient.invalidateQueries({ queryKey: ["storage"] }),
      queryClient.invalidateQueries({ queryKey: ["background-jobs"] }),
    ]);
  const save = useMutation({
    mutationFn: () =>
      apiRequest<RetentionPolicy>("/retention/policy", {
        method: "PATCH",
        body: JSON.stringify(
          draft && {
            expected_version: policy.data?.state_version,
            automatic_purge_enabled: draft.automatic_purge_enabled,
            grace_period_days: draft.grace_period_days,
            call_recording_days: draft.call_recording_days,
            transcript_days: draft.transcript_days,
            temporary_import_days: draft.temporary_import_days,
            import_report_days: draft.import_report_days,
            archived_knowledge_days: draft.archived_knowledge_days,
            realtime_event_hours: draft.realtime_event_hours,
            completed_job_days: draft.completed_job_days,
            failed_job_days: draft.failed_job_days,
          },
        ),
      }),
    onSuccess: async () => {
      setError("");
      await refresh();
    },
    onError: (caught) => setError(message(caught)),
  });
  const createPreview = useMutation({
    mutationFn: () =>
      apiRequest<RetentionPreview>("/retention/preview", {
        method: "POST",
        headers: {
          "Idempotency-Key": mutationKeys.get(
            "retention-preview",
            String(policy.data?.state_version ?? ""),
          ),
        },
        body: JSON.stringify({ policy_version: policy.data?.state_version }),
      }),
    onSuccess: (value) => {
      mutationKeys.reset("retention-preview");
      setPreview(value);
    },
    onError: (caught) => setError(message(caught)),
  });
  const run = useMutation({
    mutationFn: () =>
      apiRequest<{ job_id: string }>("/retention/run", {
        method: "POST",
        headers: {
          "Idempotency-Key": mutationKeys.get(
            "retention-run",
            JSON.stringify([preview?.id, policy.data?.state_version]),
          ),
        },
        body: JSON.stringify({
          preview_id: preview?.id,
          policy_version: policy.data?.state_version,
        }),
      }),
    onSuccess: async () => {
      mutationKeys.reset("retention-run");
      setError("");
      await refresh();
    },
    onError: (caught) => setError(message(caught)),
  });
  const cancel = useMutation({
    mutationFn: (candidate: RetentionCandidate) =>
      apiRequest<RetentionCandidate>(
        `/retention/candidates/${candidate.id}/cancel`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": mutationKeys.get(
              "retention-cancel",
              JSON.stringify([candidate.id, candidate.state_version]),
            ),
          },
          body: JSON.stringify({ expected_version: candidate.state_version }),
        },
      ),
    onSuccess: async () => {
      mutationKeys.reset("retention-cancel");
      await refresh();
    },
    onError: (caught) => setError(message(caught)),
  });
  if (policy.isPending || !draft) return <SectionSkeleton />;
  if (policy.isError)
    return (
      <QueryError
        error={policy.error}
        retry={() => void policy.refetch()}
        title="Не удалось загрузить retention policy"
      />
    );
  const canManage = policy.data.can_manage ?? role === "tenant_owner";
  const dayFields: Array<[keyof RetentionPolicy, string]> = [
    ["call_recording_days", "Записи звонков"],
    ["transcript_days", "Стенограммы"],
    ["temporary_import_days", "Временные import files"],
    ["import_report_days", "Отчёты импортов"],
    ["archived_knowledge_days", "Архивные knowledge originals"],
    ["completed_job_days", "Завершённые jobs"],
    ["failed_job_days", "Failed/dead-letter jobs"],
  ];
  return (
    <section className="panel">
      <div className="row-between">
        <div>
          <h2>Retention policy</h2>
          <p className="panel-subtitle">
            Необратимое удаление выключено по умолчанию и выполняется в два
            этапа
          </p>
        </div>
        <StatusBadge
          tone={draft.automatic_purge_enabled ? "warning" : "success"}
        >
          {draft.automatic_purge_enabled
            ? "Auto purge включён"
            : "Auto purge выключен"}
        </StatusBadge>
      </div>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      <div className="retention-form-grid">
        <label className="toggle-row">
          <input
            checked={draft.automatic_purge_enabled}
            disabled={!canManage}
            onChange={(event) =>
              setDraft({
                ...draft,
                automatic_purge_enabled: event.target.checked,
              })
            }
            type="checkbox"
          />
          <span>
            <strong>Автоматический purge</strong>
            <small>Включайте только после проверки dry-run.</small>
          </span>
        </label>
        <label className="field">
          <span>Grace period, дней</span>
          <input
            disabled={!canManage}
            min={1}
            onChange={(event) =>
              setDraft({
                ...draft,
                grace_period_days: Number(event.target.value),
              })
            }
            type="number"
            value={draft.grace_period_days}
          />
        </label>
        {dayFields.map(([field, label]) => (
          <label className="field" key={field}>
            <span>{label}, дней</span>
            <input
              disabled={!canManage}
              min={1}
              onChange={(event) =>
                setDraft({
                  ...draft,
                  [field]: event.target.value
                    ? Number(event.target.value)
                    : null,
                })
              }
              placeholder="Не удалять"
              type="number"
              value={String(draft[field] ?? "")}
            />
          </label>
        ))}
      </div>
      {canManage && (
        <div className="operations-actions">
          <Button disabled={save.isPending} onClick={() => save.mutate()}>
            Сохранить policy
          </Button>
          <Button
            disabled={createPreview.isPending}
            onClick={() => createPreview.mutate()}
            variant="secondary"
          >
            <ArchiveRestore size={15} /> Dry-run preview
          </Button>
          {preview && (
            <Button
              disabled={run.isPending || !draft.automatic_purge_enabled}
              onClick={() => run.mutate()}
              title={
                draft.automatic_purge_enabled
                  ? undefined
                  : "Сначала явно включите automatic purge в policy"
              }
              variant="danger"
            >
              <Play size={15} /> Создать pending purge
            </Button>
          )}
        </div>
      )}
      {preview && (
        <div className="retention-preview">
          <strong>Dry-run {preview.id.slice(0, 8)}</strong>
          <span>
            {preview.eligible_objects} объектов ·{" "}
            {formatBytes(preview.eligible_bytes)}
          </span>
          <small>
            Legal hold исключил: {preview.excluded_by_legal_hold} · активные
            ссылки: {preview.excluded_by_active_reference}
          </small>
        </div>
      )}
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Категория</th>
              <th>Scope</th>
              <th>Статус</th>
              <th>Purge после</th>
              <th>Legal hold</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {candidates.data?.items.map((candidate) => (
              <tr key={candidate.id}>
                <td>{candidate.object_category}</td>
                <td>{candidate.owner_aggregate_type}</td>
                <td>
                  <StatusBadge
                    tone={
                      candidate.status === "pending_purge"
                        ? "warning"
                        : "neutral"
                    }
                  >
                    {candidate.status}
                  </StatusBadge>
                </td>
                <td>
                  {candidate.purge_after
                    ? new Date(candidate.purge_after).toLocaleString("ru-RU")
                    : "—"}
                </td>
                <td>{candidate.legal_hold ? "Да" : "Нет"}</td>
                <td>
                  {canManage && candidate.can_cancel && (
                    <Button
                      disabled={cancel.isPending}
                      onClick={() => cancel.mutate(candidate)}
                      variant="quiet"
                    >
                      Отменить
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function LegalHoldsPanel({ mutationKeys, role }: OperationsPanelProps) {
  const queryClient = useQueryClient();
  const [scopeType, setScopeType] = useState<LegalHold["scope_type"]>("tenant");
  const [scopeId, setScopeId] = useState("");
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const holds = useQuery({
    queryKey: ["legal-holds"],
    queryFn: () => apiRequest<Page<LegalHold>>("/legal-holds?limit=100"),
  });
  const canManage = role === "tenant_owner" || role === "tenant_manager";
  const create = useMutation({
    mutationFn: () =>
      apiRequest<LegalHold>("/legal-holds", {
        method: "POST",
        headers: {
          "Idempotency-Key": mutationKeys.get(
            "legal-hold",
            JSON.stringify([
              scopeType,
              scopeType === "tenant" ? null : scopeId,
              reason,
            ]),
          ),
        },
        body: JSON.stringify({
          scope_type: scopeType,
          scope_id: scopeType === "tenant" ? null : scopeId,
          reason,
        }),
      }),
    onSuccess: async () => {
      mutationKeys.reset("legal-hold");
      setReason("");
      setScopeId("");
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["legal-holds"] });
    },
    onError: (caught) => setError(message(caught)),
  });
  const release = useMutation({
    mutationFn: (hold: LegalHold) =>
      apiRequest<LegalHold>(`/legal-holds/${hold.id}/release`, {
        method: "POST",
        headers: {
          "Idempotency-Key": mutationKeys.get(
            "legal-hold-release",
            JSON.stringify([hold.id, hold.state_version]),
          ),
        },
        body: JSON.stringify({ expected_version: hold.state_version }),
      }),
    onSuccess: async () => {
      mutationKeys.reset("legal-hold-release");
      await queryClient.invalidateQueries({ queryKey: ["legal-holds"] });
    },
    onError: (caught) => setError(message(caught)),
  });
  if (holds.isPending) return <SectionSkeleton />;
  if (holds.isError)
    return (
      <QueryError
        error={holds.error}
        retry={() => void holds.refetch()}
        title="Не удалось загрузить legal holds"
      />
    );
  return (
    <section className="panel">
      <div>
        <h2>Legal Holds</h2>
        <p className="panel-subtitle">
          Hold блокирует retention purge связанных объектов
        </p>
      </div>
      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {canManage && (
        <form
          className="legal-hold-form"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <label className="field">
            <span>Scope</span>
            <select
              onChange={(event) =>
                setScopeType(event.target.value as LegalHold["scope_type"])
              }
              value={scopeType}
            >
              <option value="tenant">Tenant</option>
              <option value="project">Project</option>
              <option value="call">Call</option>
              <option value="customer">Customer</option>
              <option value="document">Document</option>
            </select>
          </label>
          {scopeType !== "tenant" && (
            <label className="field">
              <span>UUID объекта</span>
              <input
                onChange={(event) => setScopeId(event.target.value)}
                required
                value={scopeId}
              />
            </label>
          )}
          <label className="field">
            <span>Причина</span>
            <input
              minLength={3}
              onChange={(event) => setReason(event.target.value)}
              required
              value={reason}
            />
          </label>
          <Button disabled={create.isPending} type="submit">
            <ShieldCheck size={15} /> Установить hold
          </Button>
        </form>
      )}
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Scope</th>
              <th>Причина</th>
              <th>Установлен</th>
              <th>Статус</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {holds.data.items.map((hold) => (
              <tr key={hold.id}>
                <td>
                  <strong>{hold.scope_type}</strong>
                  <small>{hold.scope_id ?? "tenant"}</small>
                </td>
                <td>{hold.reason}</td>
                <td>{new Date(hold.created_at).toLocaleString("ru-RU")}</td>
                <td>
                  <StatusBadge tone={hold.released_at ? "neutral" : "warning"}>
                    {hold.released_at ? "Снят" : "Активен"}
                  </StatusBadge>
                </td>
                <td>
                  {canManage && hold.can_release && !hold.released_at && (
                    <Button
                      disabled={release.isPending}
                      onClick={() => release.mutate(hold)}
                      variant="danger"
                    >
                      Снять hold
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {holds.data.items.length === 0 && (
        <div className="empty-state compact-empty">
          <ShieldCheck size={28} />
          <p>Активных legal holds нет.</p>
        </div>
      )}
    </section>
  );
}
