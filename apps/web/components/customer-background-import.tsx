"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  LoaderCircle,
  RefreshCw,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { useRealtime } from "@/components/realtime-provider";
import { ApiClientError, apiRequest, apiUpload } from "@/lib/api";
import {
  customerImportFields,
  terminalImportStatuses,
} from "@/lib/customer-import";
import { createLogicalMutationKeyStore } from "@/lib/logical-mutation-key";
import type {
  BackgroundCustomerImport,
  CustomerFieldDefinition,
  Page,
  Project,
} from "@/lib/types";

const statusLabels: Record<BackgroundCustomerImport["status"], string> = {
  uploading: "Загрузка",
  previewing: "Проверка",
  preview_ready: "Preview готов",
  queued: "В очереди",
  staging: "Проверка строк",
  ready_to_finalize: "Готов к применению",
  finalizing: "Применение",
  completed: "Завершён",
  failed: "Ошибка",
  expired: "Срок preview истёк",
  cancel_requested: "Отмена запрошена",
  cancelled: "Отменён",
};

function statusTone(status: BackgroundCustomerImport["status"]) {
  if (status === "completed") return "success" as const;
  if (status === "failed" || status === "expired" || status === "cancelled")
    return "danger" as const;
  if (status === "preview_ready" || status === "ready_to_finalize")
    return "primary" as const;
  return "warning" as const;
}

function formatDuration(milliseconds: number | null): string {
  if (milliseconds === null) return "—";
  const seconds = Math.max(0, Math.round(milliseconds / 1000));
  if (seconds < 60) return `${seconds} сек.`;
  return `${Math.floor(seconds / 60)} мин. ${seconds % 60} сек.`;
}

function importMessage(error: unknown): string {
  return error instanceof ApiClientError
    ? error.message
    : error instanceof Error
      ? error.message
      : "Операция фонового импорта не выполнена";
}

export function CustomerBackgroundImport({
  projects,
  defaultProjectId,
  onCustomersChanged,
}: {
  projects: Project[];
  defaultProjectId: string;
  onCustomersChanged: () => Promise<void> | void;
}) {
  const queryClient = useQueryClient();
  const realtime = useRealtime();
  const [projectId, setProjectId] = useState(defaultProjectId);
  const [file, setFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const completedImportRef = useRef<string | null>(null);
  const [mutationKeys] = useState(createLogicalMutationKeyStore);

  useEffect(() => {
    if (!projectId && defaultProjectId) setProjectId(defaultProjectId);
  }, [defaultProjectId, projectId]);

  const imports = useQuery({
    queryKey: ["customer-imports", "background", projectId],
    queryFn: () =>
      apiRequest<Page<BackgroundCustomerImport>>(
        `/customers/import/background?project_id=${projectId}&limit=20`,
      ),
    enabled: Boolean(projectId),
    refetchInterval: realtime.connected ? false : 15_000,
  });

  useEffect(() => {
    if (selectedId || !imports.data?.items.length) return;
    const recoverable = imports.data.items.find(
      (item) => !terminalImportStatuses.has(item.status),
    );
    setSelectedId((recoverable ?? imports.data.items[0]).id);
  }, [imports.data, selectedId]);

  const current = useQuery({
    queryKey: ["customer-imports", "background", selectedId],
    queryFn: () =>
      apiRequest<BackgroundCustomerImport>(
        `/customers/import/background/${selectedId}/status`,
      ),
    enabled: Boolean(selectedId),
    refetchInterval: (query) => {
      if (realtime.connected) return false;
      const item = query.state.data;
      return item && !terminalImportStatuses.has(item.status) ? 5_000 : false;
    },
  });

  const fieldDefinitions = useQuery({
    queryKey: ["customers", "fields", projectId, "background-import"],
    queryFn: () =>
      apiRequest<CustomerFieldDefinition[]>(
        `/customers/fields?project_id=${projectId}&include_inactive=false`,
      ),
    enabled: Boolean(projectId),
  });

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["customer-imports"] }),
      queryClient.invalidateQueries({ queryKey: ["background-jobs"] }),
    ]);
  };

  const upload = useMutation({
    mutationFn: async () => {
      if (!projectId || !file) throw new Error("Выберите проект и файл");
      const body = new FormData();
      body.set("project_id", projectId);
      body.set("file", file);
      setUploadProgress(0);
      return apiUpload<BackgroundCustomerImport>(
        "/customers/import/background/preview",
        body,
        {
          "Idempotency-Key": mutationKeys.get(
            "customer-background-preview",
            JSON.stringify([
              projectId,
              file.name,
              file.size,
              file.lastModified,
              file.type,
            ]),
          ),
        },
        setUploadProgress,
        { timeoutMs: 5 * 60_000 },
      );
    },
    onSuccess: async (value) => {
      mutationKeys.reset("customer-background-preview");
      setSelectedId(value.id);
      setFile(null);
      setUploadProgress(100);
      setError("");
      setSuccess("Файл принят. Preview выполняется в фоновой очереди.");
      await refresh();
    },
    onError: (caught) => setError(importMessage(caught)),
  });

  const updateMapping = useMutation({
    mutationFn: ({
      mapping,
      sheet,
      rule,
    }: {
      mapping: Record<string, string>;
      sheet: string;
      rule: "skip" | "update";
    }) =>
      apiRequest<BackgroundCustomerImport>(`/customers/import/${selectedId}`, {
        method: "PATCH",
        body: JSON.stringify({
          mapping,
          sheet_name: sheet,
          update_rule: rule,
        }),
      }),
    onSuccess: async () => {
      setError("");
      await refresh();
    },
    onError: (caught) => setError(importMessage(caught)),
  });

  const commit = useMutation({
    mutationFn: () => {
      if (!current.data) throw new Error("Import preview не выбран");
      const fingerprint = JSON.stringify([
        current.data.id,
        current.data.state_version,
      ]);
      return apiRequest<BackgroundCustomerImport>(
        `/customers/import/background/${current.data.id}/background-commit`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": mutationKeys.get(
              "customer-background-commit",
              fingerprint,
            ),
          },
          body: JSON.stringify({
            expected_version: current.data.state_version,
          }),
        },
      );
    },
    onSuccess: async () => {
      mutationKeys.reset("customer-background-commit");
      setError("");
      setSuccess(
        "Импорт поставлен в очередь. Клиенты появятся после atomic finalization.",
      );
      await refresh();
    },
    onError: (caught) => setError(importMessage(caught)),
  });

  const cancel = useMutation({
    mutationFn: () => {
      if (!current.data) throw new Error("Import не выбран");
      const fingerprint = JSON.stringify([
        current.data.id,
        current.data.state_version,
      ]);
      return apiRequest<BackgroundCustomerImport>(
        `/customers/import/background/${current.data.id}/cancel`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": mutationKeys.get(
              "customer-background-cancel",
              fingerprint,
            ),
          },
          body: JSON.stringify({
            expected_version: current.data.state_version,
          }),
        },
      );
    },
    onSuccess: async () => {
      mutationKeys.reset("customer-background-cancel");
      setError("");
      setSuccess("Запрос на отмену принят.");
      await refresh();
    },
    onError: (caught) => setError(importMessage(caught)),
  });

  const retry = useMutation({
    mutationFn: () => {
      if (!current.data) throw new Error("Import не выбран");
      const fingerprint = JSON.stringify([
        current.data.id,
        current.data.state_version,
      ]);
      return apiRequest<BackgroundCustomerImport>(
        `/customers/import/background/${current.data.id}/retry`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": mutationKeys.get(
              "customer-background-retry",
              fingerprint,
            ),
          },
          body: JSON.stringify({
            expected_version: current.data.state_version,
          }),
        },
      );
    },
    onSuccess: async () => {
      mutationKeys.reset("customer-background-retry");
      setError("");
      setSuccess("Повторная обработка поставлена в очередь.");
      await refresh();
    },
    onError: (caught) => setError(importMessage(caught)),
  });

  const report = useMutation({
    mutationFn: () =>
      apiRequest<{ url: string }>(
        `/customers/import/background/${selectedId}/report`,
      ),
    onSuccess: ({ url }) => window.open(url, "_blank", "noopener,noreferrer"),
    onError: (caught) => setError(importMessage(caught)),
  });

  useEffect(() => {
    if (
      current.data?.status !== "completed" ||
      completedImportRef.current === current.data.id
    )
      return;
    completedImportRef.current = current.data.id;
    void onCustomersChanged();
  }, [current.data, onCustomersChanged]);

  const mappingFields = useMemo(
    () => [
      ...customerImportFields,
      ...(fieldDefinitions.data?.map(
        (definition) => [`custom.${definition.key}`, definition.name] as const,
      ) ?? []),
    ],
    [fieldDefinitions.data],
  );
  const item = current.data;

  return (
    <div className="background-import-workspace">
      <div className="customer-import-upload">
        <div className="field">
          <label htmlFor="background-import-project">Проект</label>
          <select
            id="background-import-project"
            onChange={(event) => {
              mutationKeys.resetAll();
              setProjectId(event.target.value);
              setSelectedId("");
            }}
            value={projectId}
          >
            {projects
              .filter((project) => project.status === "active")
              .map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
          </select>
        </div>
        <label className="customer-file-control">
          <FileSpreadsheet size={22} />
          <span>{file?.name ?? "Выберите CSV или XLSX до 25 МБ"}</span>
          <input
            accept=".csv,.xlsx"
            aria-label="Файл большого импорта"
            onChange={(event) => {
              mutationKeys.reset("customer-background-preview");
              setFile(event.target.files?.[0] ?? null);
            }}
            type="file"
          />
        </label>
        <Button
          disabled={!file || upload.isPending}
          onClick={() => upload.mutate()}
        >
          <Upload size={16} />{" "}
          {upload.isPending ? "Загружаем…" : "Создать background preview"}
        </Button>
      </div>

      {(upload.isPending || uploadProgress > 0) && (
        <div className="knowledge-upload-progress">
          <div
            aria-label={`Загрузка файла импорта ${uploadProgress}%`}
            aria-valuemax={100}
            aria-valuemin={0}
            aria-valuenow={uploadProgress}
            className="knowledge-upload-progress-track"
            role="progressbar"
          >
            <span style={{ width: `${uploadProgress}%` }} />
          </div>
          <small>
            Загрузка браузером: {uploadProgress}%. Обработка выполняется
            отдельно Worker.
          </small>
        </div>
      )}

      {error && (
        <div className="form-error" role="alert">
          {error}
        </div>
      )}
      {success && (
        <div className="customer-success" role="status">
          <CheckCircle2 size={17} /> {success}
        </div>
      )}

      <div className="background-import-grid">
        <section className="background-import-history">
          <div className="row-between">
            <strong>Последние импорты</strong>
            <Button onClick={() => void imports.refetch()} variant="quiet">
              <RefreshCw size={14} /> Обновить
            </Button>
          </div>
          {imports.isPending && <small>Загрузка истории…</small>}
          {imports.data?.items.length === 0 && (
            <small>Фоновых импортов пока нет.</small>
          )}
          {imports.data?.items.map((value) => (
            <button
              className={value.id === selectedId ? "active" : ""}
              key={value.id}
              onClick={() => setSelectedId(value.id)}
              type="button"
            >
              <span>{value.file_name}</span>
              <StatusBadge tone={statusTone(value.status)}>
                {statusLabels[value.status]}
              </StatusBadge>
            </button>
          ))}
        </section>

        <section className="background-import-detail">
          {current.isPending && selectedId && (
            <div className="empty-state compact-empty">
              <LoaderCircle className="spin" size={24} />
              <p>Восстанавливаем состояние импорта…</p>
            </div>
          )}
          {!selectedId && (
            <div className="empty-state compact-empty">
              <FileSpreadsheet size={28} />
              <p>Загрузите большой CSV/XLSX или выберите прошлый импорт.</p>
            </div>
          )}
          {item && (
            <>
              <div className="row-between background-import-title">
                <div>
                  <strong>{item.file_name}</strong>
                  <small>
                    {item.total_rows.toLocaleString("ru-RU")} строк · job{" "}
                    {item.job_id?.slice(0, 8) ?? "создаётся"}
                  </small>
                </div>
                <StatusBadge tone={statusTone(item.status)}>
                  {statusLabels[item.status]}
                </StatusBadge>
              </div>
              <div className="background-job-progress">
                <div>
                  <span
                    style={{
                      width: `${Math.max(0, Math.min(100, item.progress))}%`,
                    }}
                  />
                </div>
                <small>
                  {item.progress}% ·{" "}
                  {realtime.connected ? "realtime" : "резервное обновление"}
                </small>
              </div>
              <div className="customer-import-summary">
                <StatusBadge>{item.valid_rows} валидных</StatusBadge>
                <StatusBadge tone="warning">
                  {item.duplicate_rows} дублей
                </StatusBadge>
                <StatusBadge tone="danger">
                  {item.invalid_rows} ошибок
                </StatusBadge>
                {item.completed_at && (
                  <StatusBadge tone="success">
                    {formatDuration(item.processing_duration_ms)}
                  </StatusBadge>
                )}
              </div>

              {item.status === "preview_ready" && (
                <>
                  {item.sheet_names.length > 1 && (
                    <div className="field">
                      <label htmlFor="background-import-sheet">Лист XLSX</label>
                      <select
                        id="background-import-sheet"
                        onChange={(event) =>
                          updateMapping.mutate({
                            mapping: item.mapping,
                            sheet: event.target.value,
                            rule: item.update_rule,
                          })
                        }
                        value={item.selected_sheet}
                      >
                        {item.sheet_names.map((sheet) => (
                          <option key={sheet} value={sheet}>
                            {sheet}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  <div className="customer-mapping-grid background-mapping-grid">
                    {mappingFields.map(([field, label]) => (
                      <div className="field" key={field}>
                        <label htmlFor={`background-mapping-${field}`}>
                          {label}
                        </label>
                        <select
                          id={`background-mapping-${field}`}
                          onChange={(event) => {
                            const mapping = { ...item.mapping };
                            if (event.target.value)
                              mapping[field] = event.target.value;
                            else delete mapping[field];
                            updateMapping.mutate({
                              mapping,
                              sheet: item.selected_sheet,
                              rule: item.update_rule,
                            });
                          }}
                          value={item.mapping[field] ?? ""}
                        >
                          <option value="">Не импортировать</option>
                          {item.headers.map((header) => (
                            <option key={header} value={header}>
                              {header}
                            </option>
                          ))}
                        </select>
                      </div>
                    ))}
                  </div>
                  <div className="customer-import-rule">
                    <label>
                      <input
                        checked={item.update_rule === "skip"}
                        onChange={() =>
                          updateMapping.mutate({
                            mapping: item.mapping,
                            sheet: item.selected_sheet,
                            rule: "skip",
                          })
                        }
                        type="radio"
                      />{" "}
                      Пропускать дубли
                    </label>
                    <label>
                      <input
                        checked={item.update_rule === "update"}
                        onChange={() =>
                          updateMapping.mutate({
                            mapping: item.mapping,
                            sheet: item.selected_sheet,
                            rule: "update",
                          })
                        }
                        type="radio"
                      />{" "}
                      Обновлять найденных
                    </label>
                  </div>
                  {item.preview_rows.length > 0 && (
                    <div className="table-wrap">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>Строка</th>
                            <th>ФИО</th>
                            <th>Проверка</th>
                          </tr>
                        </thead>
                        <tbody>
                          {item.preview_rows.map((row) => (
                            <tr key={row.row_number}>
                              <td>{row.row_number}</td>
                              <td>
                                {String(
                                  row.values.display_name ??
                                    row.values[
                                      item.mapping.display_name ??
                                        item.headers[0] ??
                                        ""
                                    ] ??
                                    "—",
                                )}
                              </td>
                              <td>
                                {row.errors.length
                                  ? row.errors.join("; ")
                                  : row.duplicate_fields.length
                                    ? `Дубликат: ${row.duplicate_fields.join(", ")}`
                                    : "Готово"}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </>
              )}

              {item.status === "completed" && (
                <div className="customer-import-report">
                  <CheckCircle2 size={22} />
                  <div>
                    <strong>Atomic finalization завершён</strong>
                    <p>
                      Создано: {item.created} · Обновлено: {item.updated} ·
                      Пропущено: {item.skipped} · Ошибок: {item.error_count}
                    </p>
                  </div>
                </div>
              )}
              {item.can_retry && (
                <Button
                  disabled={retry.isPending}
                  onClick={() => retry.mutate()}
                  variant="quiet"
                >
                  <RefreshCw size={15} />
                  {retry.isPending
                    ? "Ставим в очередь…"
                    : "Повторить обработку"}
                </Button>
              )}
              <div className="customer-import-actions">
                {item.can_cancel && (
                  <Button
                    disabled={cancel.isPending}
                    onClick={() => cancel.mutate()}
                    variant="danger"
                  >
                    <Ban size={15} /> Отменить
                  </Button>
                )}
                {item.can_commit && (
                  <Button
                    disabled={commit.isPending || updateMapping.isPending}
                    onClick={() => commit.mutate()}
                  >
                    <CheckCircle2 size={15} /> Запустить background commit
                  </Button>
                )}
                {item.report_available && (
                  <Button
                    disabled={report.isPending}
                    onClick={() => report.mutate()}
                    variant="secondary"
                  >
                    <Download size={15} /> Скачать отчёт
                  </Button>
                )}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
