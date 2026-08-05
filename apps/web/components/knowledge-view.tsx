"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  BookOpen,
  CheckCircle2,
  Download,
  FileSearch,
  FileText,
  History,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  UploadCloud,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { useRealtime } from "@/components/realtime-provider";
import {
  ApiClientError,
  apiRequest,
  apiUpload,
  idempotencyKey,
} from "@/lib/api";
import { createLogicalMutationKeyStore } from "@/lib/logical-mutation-key";
import type {
  AuthResponse,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeDocumentVersion,
  KnowledgeRetrieval,
  KnowledgeRevision,
  Page,
  Project,
} from "@/lib/types";

const processingStatuses = new Set([
  "uploaded",
  "queued",
  "extracting",
  "chunking",
  "embedding",
]);

const statusLabels: Record<string, string> = {
  uploaded: "Загружен",
  queued: "В очереди",
  extracting: "Извлечение текста",
  chunking: "Разбиение",
  embedding: "Индексация",
  ready: "Готов",
  failed: "Ошибка",
  needs_ocr: "Нужен OCR",
  archived: "Архив",
  draft: "Черновик",
  published: "Опубликовано",
};

function statusTone(
  status: string,
): "neutral" | "success" | "warning" | "danger" | "primary" {
  if (status === "ready" || status === "published") return "success";
  if (status === "failed") return "danger";
  if (status === "needs_ocr" || status === "archived") return "warning";
  if (processingStatuses.has(status) || status === "draft") return "primary";
  return "neutral";
}

function message(error: unknown): string {
  return error instanceof ApiClientError
    ? error.message
    : "Не удалось выполнить операцию. Повторите попытку.";
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} Б`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} КБ`;
  return `${(value / (1024 * 1024)).toFixed(1)} МБ`;
}

export function KnowledgeView() {
  const queryClient = useQueryClient();
  const realtime = useRealtime();
  const [mutationKeys] = useState(createLogicalMutationKeyStore);
  const fileRef = useRef<HTMLInputElement>(null);
  const [projectId, setProjectId] = useState("");
  const [baseId, setBaseId] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [baseName, setBaseName] = useState("");
  const [baseDescription, setBaseDescription] = useState("");
  const [language, setLanguage] = useState("ru");
  const [textTitle, setTextTitle] = useState("");
  const [textContent, setTextContent] = useState("");
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [replacement, setReplacement] = useState<KnowledgeDocument | null>(
    null,
  );
  const [showArchived, setShowArchived] = useState(false);
  const [editingBase, setEditingBase] = useState(false);
  const [editBaseName, setEditBaseName] = useState("");
  const [editBaseDescription, setEditBaseDescription] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [query, setQuery] = useState("");
  const [retrieval, setRetrieval] = useState<KnowledgeRetrieval | null>(null);
  const [preview, setPreview] = useState<{
    title: string;
    text: string;
    page: number | null;
  } | null>(null);

  const auth = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
  });
  const projects = useQuery({
    queryKey: ["projects", "knowledge"],
    queryFn: () => apiRequest<Page<Project>>("/projects?limit=100"),
  });
  useEffect(() => {
    if (!projectId && projects.data?.items[0])
      setProjectId(projects.data.items[0].id);
  }, [projectId, projects.data]);

  const bases = useQuery({
    queryKey: ["knowledge", "bases", projectId, showArchived],
    queryFn: () =>
      apiRequest<Page<KnowledgeBase>>(
        `/knowledge/bases?project_id=${projectId}&include_archived=${showArchived}&limit=100`,
      ),
    enabled: Boolean(projectId),
  });
  useEffect(() => {
    if (!bases.data) return;
    if (!bases.data.items.some((item) => item.id === baseId)) {
      setBaseId(bases.data.items[0]?.id ?? "");
    }
  }, [baseId, bases.data]);

  const selectedBase =
    bases.data?.items.find((item) => item.id === baseId) ?? null;
  useEffect(() => {
    if (!selectedBase) return;
    setEditBaseName(selectedBase.name);
    setEditBaseDescription(selectedBase.description);
    setEditingBase(false);
    setReplacement(null);
  }, [selectedBase]);
  const selectedRevision =
    selectedBase?.draft_revision ?? selectedBase?.published_revision ?? null;
  const revisions = useQuery({
    queryKey: ["knowledge", "revisions", baseId],
    queryFn: () =>
      apiRequest<KnowledgeRevision[]>(`/knowledge/bases/${baseId}/revisions`),
    enabled: Boolean(baseId),
  });
  const documents = useQuery({
    queryKey: [
      "knowledge",
      "documents",
      baseId,
      selectedRevision?.id,
      showArchived,
    ],
    queryFn: () =>
      apiRequest<Page<KnowledgeDocument>>(
        `/knowledge/documents?knowledge_base_id=${baseId}&revision_id=${selectedRevision?.id}&include_archived=${showArchived}&limit=100`,
      ),
    enabled: Boolean(baseId && selectedRevision),
    refetchInterval: (state) =>
      !realtime.connected &&
      state.state.data?.items.some((item) =>
        processingStatuses.has(item.version?.status ?? ""),
      )
        ? 5_000
        : false,
  });

  const role = auth.data?.user.role;
  const canManage = role === "tenant_owner" || role === "tenant_manager";
  const hasBlockingDocuments = documents.data?.items.some(
    (item) =>
      item.version &&
      item.version.status !== "ready" &&
      item.version.status !== "archived",
  );

  async function refreshKnowledge(): Promise<void> {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["knowledge"] }),
      queryClient.invalidateQueries({ queryKey: ["background-jobs"] }),
    ]);
  }

  const createBase = useMutation({
    mutationFn: () =>
      apiRequest<KnowledgeBase>("/knowledge/bases", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          name: baseName,
          description: baseDescription,
          default_language_code: language,
        }),
      }),
    onSuccess: async (created) => {
      setBaseId(created.id);
      setBaseName("");
      setBaseDescription("");
      setError("");
      setSuccess("База знаний создана");
      await refreshKnowledge();
    },
    onError: (caught) => setError(message(caught)),
  });
  const createDraft = useMutation({
    mutationFn: () =>
      apiRequest<KnowledgeRevision>(`/knowledge/bases/${baseId}/draft`, {
        method: "POST",
      }),
    onSuccess: async () => {
      setSuccess("Новая revision создана из опубликованной версии");
      setError("");
      await refreshKnowledge();
    },
    onError: (caught) => setError(message(caught)),
  });
  const updateBase = useMutation({
    mutationFn: () => {
      if (!selectedBase) throw new Error("knowledge base missing");
      return apiRequest<KnowledgeBase>(`/knowledge/bases/${selectedBase.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: editBaseName,
          description: editBaseDescription,
          expected_version: selectedBase.lock_version,
        }),
      });
    },
    onSuccess: async () => {
      setEditingBase(false);
      setSuccess("Настройки базы знаний сохранены");
      setError("");
      await refreshKnowledge();
    },
    onError: (caught) => setError(message(caught)),
  });
  const setBaseArchived = useMutation({
    mutationFn: (base: KnowledgeBase) =>
      apiRequest<KnowledgeBase>(
        `/knowledge/bases/${base.id}/${base.archived_at ? "restore" : "archive"}`,
        {
          method: "POST",
          body: JSON.stringify({ expected_version: base.lock_version }),
        },
      ),
    onSuccess: async (base) => {
      setSuccess(
        base.archived_at
          ? "База знаний архивирована"
          : "База знаний восстановлена",
      );
      setError("");
      await refreshKnowledge();
    },
    onError: (caught) => setError(message(caught)),
  });
  const addText = useMutation({
    mutationFn: () => {
      const payload = {
        project_id: projectId,
        knowledge_base_id: baseId,
        title: textTitle,
        language,
        content: textContent,
      };
      return apiRequest<KnowledgeDocument>("/knowledge/text", {
        method: "POST",
        headers: {
          "Idempotency-Key": mutationKeys.get(
            "knowledge-text",
            JSON.stringify(payload),
          ),
        },
        body: JSON.stringify(payload),
      });
    },
    onSuccess: async () => {
      mutationKeys.reset("knowledge-text");
      setTextTitle("");
      setTextContent("");
      setSuccess("Текст добавлен и проиндексирован");
      setError("");
      await refreshKnowledge();
    },
    onError: (caught) => setError(message(caught)),
  });
  const upload = useMutation({
    mutationFn: async () => {
      if (!uploadFile || !selectedBase?.draft_revision)
        throw new Error("missing upload data");
      const form = new FormData();
      form.set("revision_id", selectedBase.draft_revision.id);
      form.set("title", uploadTitle || uploadFile.name.replace(/\.[^.]+$/, ""));
      form.set("language", language);
      if (replacement?.version) {
        form.set("document_id", replacement.id);
        form.set(
          "document_expected_version",
          String(replacement.version.lock_version),
        );
      }
      form.set("file", uploadFile);
      setUploadPercent(0);
      const fingerprint = JSON.stringify({
        revisionId: selectedBase.draft_revision.id,
        title: uploadTitle || uploadFile.name.replace(/\.[^.]+$/, ""),
        language,
        replacementId: replacement?.id ?? null,
        replacementVersion: replacement?.version?.lock_version ?? null,
        fileName: uploadFile.name,
        fileSize: uploadFile.size,
        fileType: uploadFile.type,
        fileLastModified: uploadFile.lastModified,
      });
      return apiUpload<KnowledgeDocument>(
        "/knowledge/documents/upload",
        form,
        {
          "Idempotency-Key": mutationKeys.get("knowledge-upload", fingerprint),
        },
        setUploadPercent,
      );
    },
    onSuccess: async () => {
      mutationKeys.reset("knowledge-upload");
      setUploadFile(null);
      setUploadTitle("");
      setReplacement(null);
      setUploadPercent(100);
      if (fileRef.current) fileRef.current.value = "";
      setSuccess("Оригинал загружен. Worker начал обработку.");
      setError("");
      await refreshKnowledge();
    },
    onError: (caught) => setError(message(caught)),
  });
  const publish = useMutation({
    mutationFn: () => {
      if (!selectedBase?.draft_revision) throw new Error("draft missing");
      return apiRequest<KnowledgeRevision>(
        `/knowledge/revisions/${selectedBase.draft_revision.id}/publish`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_version: selectedBase.draft_revision.lock_version,
          }),
        },
      );
    },
    onSuccess: async () => {
      setSuccess("Revision опубликована и зафиксирована для новых звонков");
      setError("");
      await refreshKnowledge();
    },
    onError: (caught) => setError(message(caught)),
  });
  const retry = useMutation({
    mutationFn: (version: KnowledgeDocumentVersion) =>
      apiRequest<KnowledgeDocumentVersion>(
        `/knowledge/documents/${version.id}/retry`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": mutationKeys.get(
              `knowledge-retry:${version.id}`,
              String(version.lock_version),
            ),
          },
          body: JSON.stringify({ expected_version: version.lock_version }),
        },
      ),
    onSuccess: async (_response, version) => {
      mutationKeys.reset(`knowledge-retry:${version.id}`);
      setSuccess("Документ возвращён в очередь");
      setError("");
      await refreshKnowledge();
    },
    onError: (caught) => setError(message(caught)),
  });
  const setDocumentArchived = useMutation({
    mutationFn: (document: KnowledgeDocument) => {
      if (!document.version) throw new Error("document version missing");
      const action =
        document.version.status === "archived" ? "restore" : "archive";
      return apiRequest<KnowledgeDocumentVersion>(
        `/knowledge/documents/${document.version.id}/${action}`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_version: document.version.lock_version,
          }),
        },
      );
    },
    onSuccess: async (version) => {
      setSuccess(
        version.status === "archived"
          ? "Документ архивирован"
          : "Документ восстановлен",
      );
      setError("");
      await refreshKnowledge();
    },
    onError: (caught) => setError(message(caught)),
  });
  const download = useMutation({
    mutationFn: (version: KnowledgeDocumentVersion) =>
      apiRequest<{ url: string }>(
        `/knowledge/documents/${version.id}/download`,
      ),
    onSuccess: ({ url }) => {
      window.open(url, "_blank", "noopener,noreferrer");
    },
    onError: (caught) => setError(message(caught)),
  });
  const testRetrieval = useMutation({
    mutationFn: () =>
      apiRequest<KnowledgeRetrieval>("/knowledge/retrieval/test", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey("knowledge-retrieval") },
        body: JSON.stringify({
          project_id: projectId,
          knowledge_base_id: baseId,
          revision_id: selectedBase?.published_revision?.id,
          query,
          language,
          top_k: 5,
          threshold: 0.08,
        }),
      }),
    onSuccess: (result) => {
      setRetrieval(result);
      setError("");
    },
    onError: (caught) => setError(message(caught)),
  });
  const openCitation = useMutation({
    mutationFn: async (
      hit: NonNullable<KnowledgeRetrieval>["hits"][number],
    ) => {
      if (!hit.chunk_id) {
        const result = await apiRequest<{ text: string }>(
          `/knowledge/documents/${hit.document_version_id}/text`,
        );
        return { title: hit.title, text: result.text, page: hit.page };
      }
      const result = await apiRequest<{
        title: string;
        text: string;
        page: number | null;
      }>(`/knowledge/citations/${hit.chunk_id}`);
      return { title: result.title, text: result.text, page: result.page };
    },
    onSuccess: setPreview,
    onError: (caught) => setError(message(caught)),
  });

  const currentProject = projects.data?.items.find(
    (item) => item.id === projectId,
  );
  const progress = useMemo(() => {
    const items = documents.data?.items ?? [];
    const total = items.length;
    const ready = items.filter(
      (item) => item.version?.status === "ready",
    ).length;
    return { total, ready };
  }, [documents.data]);

  function submit(event: FormEvent, action: () => void): void {
    event.preventDefault();
    setSuccess("");
    action();
  }

  function selectUploadFile(file: File | null): void {
    if (!file) return;
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!extension || !["txt", "md", "pdf", "docx"].includes(extension)) {
      setError("Поддерживаются только TXT, Markdown, PDF и DOCX");
      return;
    }
    setError("");
    setUploadFile(file);
  }

  return (
    <>
      <div className="page-heading knowledge-heading">
        <div>
          <h1>База знаний</h1>
          <p>
            Версии документов, безопасная обработка и проверяемые источники для
            AI
          </p>
        </div>
        <div className="knowledge-heading-actions">
          <label className="field compact-field">
            <span>Проект</span>
            <select
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
            >
              {projects.data?.items.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
          {selectedRevision && (
            <StatusBadge tone={statusTone(selectedRevision.status)}>
              revision {selectedRevision.version} ·{" "}
              {statusLabels[selectedRevision.status]}
            </StatusBadge>
          )}
        </div>
      </div>

      {(error || success) && (
        <div
          className={error ? "form-error" : "form-success"}
          role={error ? "alert" : "status"}
        >
          {error || success}
        </div>
      )}

      {projects.isPending ? (
        <SectionSkeleton />
      ) : projects.isError ? (
        <QueryError
          error={projects.error}
          retry={() => void projects.refetch()}
          title="Не удалось загрузить проекты"
        />
      ) : (
        <div className="knowledge-workspace">
          <aside className="panel knowledge-bases-panel">
            <div className="row-between">
              <div>
                <h2>Базы проекта</h2>
                <p className="panel-subtitle">{currentProject?.name}</p>
              </div>
              <div className="inline-badges">
                <StatusBadge>{bases.data?.total ?? 0}</StatusBadge>
                <Button
                  onClick={() => setShowArchived((current) => !current)}
                  variant="quiet"
                >
                  {showArchived ? "Скрыть архив" : "Показать архив"}
                </Button>
              </div>
            </div>
            {bases.isPending && <SectionSkeleton />}
            {bases.isError && (
              <QueryError
                error={bases.error}
                retry={() => void bases.refetch()}
                title="Не удалось загрузить базы знаний"
              />
            )}
            <div className="knowledge-base-list">
              {bases.data?.items.map((base) => (
                <button
                  className={
                    base.id === baseId
                      ? "knowledge-base-button active"
                      : "knowledge-base-button"
                  }
                  key={base.id}
                  onClick={() => setBaseId(base.id)}
                  type="button"
                >
                  <BookOpen size={18} />
                  <span>
                    <strong>{base.name}</strong>
                    <small>{base.document_count} документов</small>
                  </span>
                  {base.published_revision && (
                    <CheckCircle2 aria-label="Опубликована" size={16} />
                  )}
                  {base.archived_at && (
                    <StatusBadge tone="warning">Архив</StatusBadge>
                  )}
                </button>
              ))}
            </div>
            {bases.data?.items.length === 0 && (
              <div className="empty-state compact-empty">
                <BookOpen size={28} />
                <h3>Базы ещё нет</h3>
                <p>Создайте первую базу для выбранного проекта.</p>
              </div>
            )}
            {canManage && (
              <form
                className="knowledge-create-form"
                onSubmit={(event) => submit(event, () => createBase.mutate())}
              >
                <h3>Новая база</h3>
                <input
                  aria-label="Название новой базы"
                  maxLength={160}
                  minLength={2}
                  onChange={(event) => setBaseName(event.target.value)}
                  placeholder="Например, Поддержка"
                  required
                  value={baseName}
                />
                <textarea
                  aria-label="Описание базы"
                  onChange={(event) => setBaseDescription(event.target.value)}
                  placeholder="Что содержит эта база"
                  rows={2}
                  value={baseDescription}
                />
                <Button
                  disabled={
                    !projectId ||
                    baseName.trim().length < 2 ||
                    createBase.isPending
                  }
                  type="submit"
                >
                  <Plus size={15} /> Создать
                </Button>
              </form>
            )}
          </aside>

          <main className="knowledge-main">
            {!selectedBase ? (
              <section className="panel empty-state knowledge-empty-main">
                <BookOpen size={34} />
                <h2>Выберите или создайте базу</h2>
                <p>Документы всегда изолированы по компании и проекту.</p>
              </section>
            ) : (
              <>
                <section className="panel knowledge-summary">
                  <div className="row-between knowledge-summary-row">
                    <div>
                      <div className="inline-badges">
                        <h2>{selectedBase.name}</h2>
                        <StatusBadge>
                          {selectedBase.default_language_code.toUpperCase()}
                        </StatusBadge>
                      </div>
                      <p className="panel-subtitle">
                        {selectedBase.description || "Описание не задано"}
                      </p>
                    </div>
                    <div className="knowledge-summary-actions">
                      {canManage && !selectedBase.archived_at && (
                        <Button
                          onClick={() => setEditingBase((current) => !current)}
                          variant="quiet"
                        >
                          <Pencil size={15} /> Настройки
                        </Button>
                      )}
                      {canManage && !selectedBase.draft_revision && (
                        <Button
                          disabled={createDraft.isPending}
                          onClick={() => createDraft.mutate()}
                          variant="secondary"
                        >
                          <Plus size={15} /> Новая revision
                        </Button>
                      )}
                      {canManage && selectedBase.draft_revision && (
                        <Button
                          disabled={
                            publish.isPending ||
                            Boolean(hasBlockingDocuments) ||
                            progress.ready === 0
                          }
                          onClick={() => publish.mutate()}
                        >
                          <CheckCircle2 size={15} /> Опубликовать
                        </Button>
                      )}
                      {canManage && (
                        <Button
                          disabled={setBaseArchived.isPending}
                          onClick={() => setBaseArchived.mutate(selectedBase)}
                          variant="secondary"
                        >
                          {selectedBase.archived_at ? (
                            <>
                              <RotateCcw size={15} /> Восстановить
                            </>
                          ) : (
                            <>
                              <Archive size={15} /> В архив
                            </>
                          )}
                        </Button>
                      )}
                    </div>
                  </div>
                  {editingBase && (
                    <form
                      className="knowledge-base-edit"
                      onSubmit={(event) =>
                        submit(event, () => updateBase.mutate())
                      }
                    >
                      <label className="field">
                        <span>Название</span>
                        <input
                          minLength={2}
                          onChange={(event) =>
                            setEditBaseName(event.target.value)
                          }
                          required
                          value={editBaseName}
                        />
                      </label>
                      <label className="field">
                        <span>Описание</span>
                        <input
                          onChange={(event) =>
                            setEditBaseDescription(event.target.value)
                          }
                          value={editBaseDescription}
                        />
                      </label>
                      <Button
                        disabled={
                          updateBase.isPending || editBaseName.trim().length < 2
                        }
                        type="submit"
                      >
                        Сохранить
                      </Button>
                    </form>
                  )}
                  <div className="knowledge-facts">
                    <div>
                      <span>Готовность</span>
                      <strong>
                        {progress.ready} / {progress.total}
                      </strong>
                    </div>
                    <div>
                      <span>Embedding</span>
                      <strong>
                        {selectedRevision?.embedding_provider} ·{" "}
                        {selectedRevision?.embedding_status}
                      </strong>
                    </div>
                    <div>
                      <span>Модель</span>
                      <strong>{selectedRevision?.embedding_model}</strong>
                    </div>
                    <div>
                      <span>Index</span>
                      <strong>{selectedRevision?.index_version}</strong>
                    </div>
                  </div>
                  {selectedRevision?.embedding_status === "unavailable" && (
                    <div className="form-warning">
                      Embedding provider unavailable — live verification
                      required
                    </div>
                  )}
                </section>

                <section className="panel">
                  <div className="row-between">
                    <div>
                      <h2>Документы</h2>
                      <p className="panel-subtitle">
                        Оригинал загружается отдельно от фоновой обработки
                      </p>
                    </div>
                    <StatusBadge>{documents.data?.total ?? 0}</StatusBadge>
                  </div>
                  {documents.isPending && <SectionSkeleton />}
                  {documents.isError && (
                    <QueryError
                      error={documents.error}
                      retry={() => void documents.refetch()}
                      title="Не удалось загрузить документы"
                    />
                  )}
                  {documents.data?.items.length === 0 && (
                    <div className="empty-state compact-empty">
                      <FileText size={28} />
                      <h3>В revision пока нет документов</h3>
                      <p>
                        Добавьте текст или загрузите TXT, MD, PDF с текстовым
                        слоем либо DOCX.
                      </p>
                    </div>
                  )}
                  <div className="knowledge-document-list">
                    {documents.data?.items.map((document) => (
                      <article
                        className="knowledge-document-row"
                        key={`${document.id}-${document.version?.id}`}
                      >
                        <div className="knowledge-file-icon">
                          <FileText size={20} />
                        </div>
                        <div className="knowledge-document-copy">
                          <div className="inline-badges">
                            <strong>{document.title}</strong>
                            <StatusBadge>
                              {document.language.toUpperCase()}
                            </StatusBadge>
                            {document.version && (
                              <StatusBadge
                                tone={statusTone(document.version.status)}
                              >
                                {statusLabels[document.version.status] ??
                                  document.version.status}
                              </StatusBadge>
                            )}
                          </div>
                          <p>
                            {document.version?.original_filename} ·{" "}
                            {formatBytes(document.version?.content_length ?? 0)}
                            {document.version?.page_count
                              ? ` · ${document.version.page_count} стр.`
                              : ""}
                            {document.version
                              ? ` · ${document.version.chunk_count} chunks`
                              : ""}
                          </p>
                          {document.version?.safe_error_message && (
                            <small className="field-error">
                              {document.version.safe_error_message}
                            </small>
                          )}
                          {document.version?.background_job && (
                            <div className="knowledge-job-progress">
                              <div className="background-job-progress">
                                <div>
                                  <span
                                    style={{
                                      width: `${document.version.background_job.progress}%`,
                                    }}
                                  />
                                </div>
                                <small>
                                  Общая очередь:{" "}
                                  {document.version.background_job.progress}% ·
                                  попытка{" "}
                                  {
                                    document.version.background_job
                                      .attempt_count
                                  }
                                  /
                                  {document.version.background_job.max_attempts}{" "}
                                  ·{" "}
                                  {realtime.connected
                                    ? "realtime"
                                    : "резервное обновление"}
                                </small>
                              </div>
                            </div>
                          )}
                        </div>
                        <div className="knowledge-row-actions">
                          {document.version?.status === "failed" &&
                            canManage && (
                              <Button
                                disabled={retry.isPending}
                                onClick={() => retry.mutate(document.version!)}
                                variant="secondary"
                              >
                                <RefreshCw size={14} /> Повторить
                              </Button>
                            )}
                          {document.version?.status === "ready" && (
                            <Button
                              onClick={() =>
                                openCitation.mutate({
                                  chunk_id: "",
                                  document_id: document.id,
                                  document_version_id: document.version!.id,
                                  document_version: document.version!.version,
                                  title: document.title,
                                  language: document.language,
                                  page: 1,
                                  section: null,
                                  excerpt: "",
                                  lexical_score: 0,
                                  vector_score: 0,
                                  combined_score: 0,
                                  revision_id: document.version!.revision_id,
                                })
                              }
                              variant="quiet"
                            >
                              <FileSearch size={14} /> Preview
                            </Button>
                          )}
                          {canManage &&
                            selectedBase.draft_revision &&
                            document.version?.status !== "archived" && (
                              <Button
                                onClick={() => {
                                  setReplacement(document);
                                  setUploadTitle(document.title);
                                  fileRef.current?.focus();
                                }}
                                variant="quiet"
                              >
                                <RefreshCw size={14} /> Новая версия
                              </Button>
                            )}
                          {canManage && document.version?.has_original && (
                            <Button
                              disabled={download.isPending}
                              onClick={() => download.mutate(document.version!)}
                              variant="quiet"
                            >
                              <Download size={14} /> Скачать
                            </Button>
                          )}
                          {canManage && document.version && (
                            <Button
                              disabled={setDocumentArchived.isPending}
                              onClick={() =>
                                setDocumentArchived.mutate(document)
                              }
                              variant="quiet"
                            >
                              {document.version.status === "archived" ? (
                                <>
                                  <RotateCcw size={14} /> Восстановить
                                </>
                              ) : (
                                <>
                                  <Archive size={14} /> В архив
                                </>
                              )}
                            </Button>
                          )}
                        </div>
                      </article>
                    ))}
                  </div>
                </section>

                {canManage && selectedBase.draft_revision && (
                  <section className="knowledge-input-grid">
                    <form
                      className="form-card"
                      onSubmit={(event) => submit(event, () => upload.mutate())}
                    >
                      <h2>
                        <UploadCloud size={18} /> Загрузить файл
                      </h2>
                      <p className="panel-subtitle">
                        До 25 МБ · TXT, MD, PDF, DOCX · без OCR
                      </p>
                      <div className="form-stack">
                        <label className="field">
                          <span>Название</span>
                          <input
                            onChange={(event) =>
                              setUploadTitle(event.target.value)
                            }
                            value={uploadTitle}
                          />
                        </label>
                        <label className="field">
                          <span>Язык</span>
                          <input
                            list="knowledge-languages"
                            onChange={(event) =>
                              setLanguage(event.target.value.toLowerCase())
                            }
                            value={language}
                          />
                        </label>
                        {replacement && (
                          <div className="form-warning row-between">
                            <span>
                              Новая версия документа «{replacement.title}»
                            </span>
                            <Button
                              onClick={() => setReplacement(null)}
                              variant="quiet"
                            >
                              Отменить
                            </Button>
                          </div>
                        )}
                        <label
                          className={
                            dragActive
                              ? "knowledge-dropzone drag-active"
                              : "knowledge-dropzone"
                          }
                          onDragEnter={(event) => {
                            event.preventDefault();
                            setDragActive(true);
                          }}
                          onDragLeave={(event) => {
                            event.preventDefault();
                            setDragActive(false);
                          }}
                          onDragOver={(event) => event.preventDefault()}
                          onDrop={(event) => {
                            event.preventDefault();
                            setDragActive(false);
                            selectUploadFile(
                              event.dataTransfer.files[0] ?? null,
                            );
                          }}
                        >
                          <UploadCloud size={28} />
                          <strong>
                            {uploadFile?.name ?? "Выберите или перетащите файл"}
                          </strong>
                          <small>
                            {uploadFile
                              ? formatBytes(uploadFile.size)
                              : ".txt · .md · .pdf · .docx"}
                          </small>
                          <input
                            accept=".txt,.md,.pdf,.docx"
                            onChange={(event) =>
                              selectUploadFile(event.target.files?.[0] ?? null)
                            }
                            ref={fileRef}
                            type="file"
                          />
                        </label>
                        <Button
                          disabled={!uploadFile || upload.isPending}
                          type="submit"
                        >
                          {upload.isPending
                            ? "Загрузка оригинала…"
                            : "Загрузить"}
                        </Button>
                        {(upload.isPending || uploadPercent > 0) && (
                          <div className="knowledge-upload-progress">
                            <div
                              aria-label={`Загрузка оригинала ${uploadPercent}%`}
                              aria-valuemax={100}
                              aria-valuemin={0}
                              aria-valuenow={uploadPercent}
                              className="knowledge-upload-progress-track"
                              role="progressbar"
                            >
                              <span style={{ width: `${uploadPercent}%` }} />
                            </div>
                            <small>
                              Браузер загрузил {uploadPercent}%. Фоновая
                              обработка показана в статусе документа.
                            </small>
                          </div>
                        )}
                      </div>
                    </form>
                    <form
                      className="form-card"
                      onSubmit={(event) =>
                        submit(event, () => addText.mutate())
                      }
                    >
                      <h2>
                        <FileText size={18} /> Текстовый документ
                      </h2>
                      <p className="panel-subtitle">
                        Обрабатывается детерминированно сразу
                      </p>
                      <div className="form-stack">
                        <label className="field">
                          <span>Название</span>
                          <input
                            minLength={2}
                            onChange={(event) =>
                              setTextTitle(event.target.value)
                            }
                            required
                            value={textTitle}
                          />
                        </label>
                        <label className="field">
                          <span>Язык</span>
                          <input
                            list="knowledge-languages"
                            onChange={(event) =>
                              setLanguage(event.target.value.toLowerCase())
                            }
                            value={language}
                          />
                        </label>
                        <label className="field">
                          <span>Текст</span>
                          <textarea
                            minLength={20}
                            onChange={(event) =>
                              setTextContent(event.target.value)
                            }
                            required
                            rows={6}
                            value={textContent}
                          />
                        </label>
                        <Button
                          disabled={
                            addText.isPending || textContent.trim().length < 20
                          }
                          type="submit"
                        >
                          {addText.isPending
                            ? "Индексируем…"
                            : "Добавить текст"}
                        </Button>
                      </div>
                    </form>
                  </section>
                )}

                <section className="panel knowledge-retrieval-panel">
                  <div className="row-between">
                    <div>
                      <h2>
                        <Search size={18} /> Retrieval test
                      </h2>
                      <p className="panel-subtitle">
                        Поиск только по опубликованной revision; без
                        генеративного LLM
                      </p>
                    </div>
                    {retrieval && (
                      <StatusBadge
                        tone={retrieval.no_match ? "warning" : "success"}
                      >
                        {retrieval.no_match
                          ? "no_match"
                          : `${retrieval.hits.length} источников`}
                      </StatusBadge>
                    )}
                  </div>
                  <form
                    className="knowledge-search"
                    onSubmit={(event) =>
                      submit(event, () => testRetrieval.mutate())
                    }
                  >
                    <input
                      aria-label="Вопрос для базы знаний"
                      onChange={(event) => setQuery(event.target.value)}
                      placeholder="Задайте вопрос по опубликованным документам"
                      value={query}
                    />
                    <input
                      className="language-input"
                      list="knowledge-languages"
                      onChange={(event) =>
                        setLanguage(event.target.value.toLowerCase())
                      }
                      value={language}
                    />
                    <Button
                      disabled={
                        !selectedBase.published_revision ||
                        query.trim().length < 2 ||
                        testRetrieval.isPending
                      }
                      type="submit"
                    >
                      {testRetrieval.isPending ? "Ищем…" : "Найти"}
                    </Button>
                  </form>
                  {retrieval && (
                    <div className="retrieval-results">
                      <div className="form-warning">{retrieval.notice}</div>
                      {retrieval.no_match ? (
                        <div className="empty-state compact-empty">
                          <Search size={26} />
                          <h3>Подтверждённый ответ не найден</h3>
                          <p>
                            Simulator предложит перевод живому оператору и не
                            придумает источник.
                          </p>
                        </div>
                      ) : (
                        retrieval.hits.map((hit, index) => (
                          <article className="retrieval-hit" key={hit.chunk_id}>
                            <div className="retrieval-rank">{index + 1}</div>
                            <div>
                              <div className="inline-badges">
                                <strong>{hit.title}</strong>
                                <StatusBadge>
                                  {hit.language.toUpperCase()}
                                </StatusBadge>
                                <span className="score">
                                  score {hit.combined_score.toFixed(3)}
                                </span>
                              </div>
                              <p>{hit.excerpt}</p>
                              <small>
                                v{hit.document_version} ·{" "}
                                {hit.page ? `стр. ${hit.page}` : "без страницы"}
                                {hit.section ? ` · ${hit.section}` : ""} ·
                                lexical {hit.lexical_score.toFixed(3)} · vector{" "}
                                {hit.vector_score.toFixed(3)}
                              </small>
                            </div>
                            <Button
                              onClick={() => openCitation.mutate(hit)}
                              variant="quiet"
                            >
                              Источник
                            </Button>
                          </article>
                        ))
                      )}
                    </div>
                  )}
                </section>

                <section className="panel knowledge-history">
                  <div className="row-between">
                    <h2>
                      <History size={18} /> История revisions
                    </h2>
                    <StatusBadge>{revisions.data?.length ?? 0}</StatusBadge>
                  </div>
                  <div className="revision-list">
                    {revisions.data?.map((revision) => (
                      <div className="revision-row" key={revision.id}>
                        <span>v{revision.version}</span>
                        <StatusBadge tone={statusTone(revision.status)}>
                          {statusLabels[revision.status]}
                        </StatusBadge>
                        <small>
                          {revision.index_version} · {revision.embedding_model}
                        </small>
                        <time>
                          {new Date(revision.created_at).toLocaleString(
                            "ru-RU",
                          )}
                        </time>
                      </div>
                    ))}
                  </div>
                </section>
              </>
            )}
          </main>
        </div>
      )}

      <datalist id="knowledge-languages">
        <option value="ru" />
        <option value="uz" />
        <option value="en" />
        <option value="kaa" />
        <option value="kaa-latn" />
        <option value="kaa-cyrl" />
        <option value="ka" />
      </datalist>

      {preview && (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={() => setPreview(null)}
        >
          <section
            aria-modal="true"
            className="modal-card knowledge-preview-modal"
            onMouseDown={(event) => event.stopPropagation()}
            role="dialog"
          >
            <div className="row-between">
              <div>
                <h2>{preview.title}</h2>
                <p className="panel-subtitle">
                  {preview.page
                    ? `Страница ${preview.page}`
                    : "Извлечённый текст"}
                </p>
              </div>
              <Button onClick={() => setPreview(null)} variant="quiet">
                Закрыть
              </Button>
            </div>
            <pre>{preview.text}</pre>
          </section>
        </div>
      )}
    </>
  );
}
