"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowLeft,
  CheckCircle2,
  ChevronRight,
  CirclePlay,
  GitBranch,
  Plus,
  RefreshCcw,
  RotateCcw,
  Save,
  Send,
  ShieldAlert,
  Trash2,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { ApiClientError, apiRequest } from "@/lib/api";
import type {
  AuthResponse,
  CallFlow,
  CallFlowAnswer,
  CallFlowDefinition,
  CallFlowNode,
  CallFlowNodeType,
  CallFlowPreview,
  CallFlowValidation,
  CallFlowVersion,
  CustomerFieldDefinition,
  Project,
} from "@/lib/types";

const nodeTypes: Array<{ value: CallFlowNodeType; label: string }> = [
  { value: "start", label: "Начало" },
  { value: "operator_text", label: "Текст оператора" },
  { value: "customer_question", label: "Вопрос клиенту" },
  { value: "info_hint", label: "Подсказка" },
  { value: "choice", label: "Выбор варианта" },
  { value: "value_input", label: "Ввод значения" },
  { value: "update_customer_field", label: "Обновление поля" },
  { value: "create_task", label: "Создание задачи" },
  { value: "create_callback", label: "Создание перезвона" },
  { value: "transfer_request", label: "Запрос перевода" },
  { value: "end", label: "Завершение" },
];

const statusLabels = {
  draft: "Черновик",
  published: "Опубликован",
  archived: "Архив",
};

const actionTypes = new Set<CallFlowNodeType>([
  "update_customer_field",
  "create_task",
  "create_callback",
  "transfer_request",
]);

function canManage(role: AuthResponse["user"]["role"] | undefined) {
  return role === "tenant_owner" || role === "tenant_manager";
}

function cloneDefinition(definition: CallFlowDefinition): CallFlowDefinition {
  return structuredClone(definition);
}

function createNode(type: CallFlowNodeType, order: number): CallFlowNode {
  const id = crypto.randomUUID();
  return {
    id,
    system_key: `step_${id.replaceAll("-", "").slice(0, 8)}`,
    name: nodeTypes.find((item) => item.value === type)?.label ?? "Новый шаг",
    node_type: type,
    text_by_language: {},
    hint_by_language: {},
    order,
    is_required: false,
    customer_field_definition_id: null,
    answers: [],
    next_node_id: null,
    fallback_node_id: null,
    action_config: {},
  };
}

function emptyAnswer(language: string, index: number): CallFlowAnswer {
  return {
    id: crypto.randomUUID(),
    key: index === 0 ? "yes" : index === 1 ? "no" : `option_${index + 1}`,
    label_by_language: {
      [language]:
        index === 0 ? "Да" : index === 1 ? "Нет" : `Вариант ${index + 1}`,
    },
    next_node_id: null,
    is_required: true,
  };
}

function versionTone(status: CallFlowVersion["status"]) {
  if (status === "published") return "success" as const;
  if (status === "draft") return "warning" as const;
  return "neutral" as const;
}

export function CallFlowEditor({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [selectedFlowId, setSelectedFlowId] = useState("");
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [language, setLanguage] = useState("ru");
  const [working, setWorking] = useState<CallFlowVersion | null>(null);
  const [dirty, setDirty] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [validation, setValidation] = useState<CallFlowValidation | null>(null);
  const [previewAnswers, setPreviewAnswers] = useState<
    Array<{ node_id: string; answer_key: string | null }>
  >([]);
  const [preview, setPreview] = useState<CallFlowPreview | null>(null);
  const [creating, setCreating] = useState(false);
  const [newFlowName, setNewFlowName] = useState("Основной сценарий");
  const [newFlowLanguages, setNewFlowLanguages] = useState("ru, uz, en, kaa");

  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
  });
  const project = useQuery({
    queryKey: ["projects", projectId],
    queryFn: () => apiRequest<Project>(`/projects/${projectId}`),
  });
  const manage = canManage(me.data?.user.role);
  const flows = useQuery({
    queryKey: ["call-flows", projectId, manage],
    queryFn: () =>
      apiRequest<CallFlow[]>(
        `/call-flows?project_id=${projectId}${manage ? "&include_archived=true" : ""}`,
      ),
    enabled: me.isSuccess,
  });
  const selectedFlow =
    flows.data?.find((item) => item.id === selectedFlowId) ?? null;
  const version = useQuery({
    queryKey: ["call-flow-version", selectedFlowId, selectedVersionId],
    queryFn: () =>
      apiRequest<CallFlowVersion>(
        `/call-flows/${selectedFlowId}/versions/${selectedVersionId}`,
      ),
    enabled: Boolean(selectedFlowId && selectedVersionId),
  });
  const customerFields = useQuery({
    queryKey: ["customers", "fields", projectId],
    queryFn: () =>
      apiRequest<CustomerFieldDefinition[]>(
        `/customers/fields?project_id=${projectId}&include_inactive=true`,
      ),
    enabled: manage,
  });

  useEffect(() => {
    if (!flows.data?.length || selectedFlowId) return;
    const preferred =
      flows.data.find((item) => item.id === project.data?.call_flow_id) ??
      flows.data[0];
    setSelectedFlowId(preferred.id);
  }, [flows.data, project.data?.call_flow_id, selectedFlowId]);

  useEffect(() => {
    if (!selectedFlow) return;
    const preferred =
      selectedFlow.versions.find((item) => item.status === "draft") ??
      selectedFlow.versions.find(
        (item) => item.id === selectedFlow.active_version_id,
      ) ??
      selectedFlow.versions[0];
    if (
      preferred &&
      !selectedFlow.versions.some((item) => item.id === selectedVersionId)
    ) {
      setSelectedVersionId(preferred.id);
    }
  }, [selectedFlow, selectedVersionId]);

  useEffect(() => {
    if (!version.data) return;
    setWorking({
      ...version.data,
      definition: cloneDefinition(version.data.definition),
    });
    setSelectedNodeId(version.data.definition.nodes[0]?.id ?? "");
    setDirty(false);
    setValidation(null);
    setPreview(null);
    setPreviewAnswers([]);
  }, [version.data]);

  useEffect(() => {
    if (selectedFlow?.language_codes.includes(language)) return;
    if (selectedFlow) setLanguage(selectedFlow.default_language_code);
  }, [language, selectedFlow]);

  const selectedNode =
    working?.definition.nodes.find((item) => item.id === selectedNodeId) ??
    null;
  const sortedNodes = useMemo(
    () =>
      [...(working?.definition.nodes ?? [])].sort((a, b) => a.order - b.order),
    [working],
  );
  const editable =
    manage && working?.status === "draft" && selectedFlow?.archived_at === null;

  function clearMessages() {
    setError("");
    setNotice("");
  }

  function replaceWorking(next: CallFlowVersion) {
    setWorking({ ...next, definition: cloneDefinition(next.definition) });
    setDirty(false);
  }

  function updateNode(patch: Partial<CallFlowNode>) {
    if (!working || !selectedNode || !editable) return;
    setWorking({
      ...working,
      definition: {
        ...working.definition,
        nodes: working.definition.nodes.map((node) =>
          node.id === selectedNode.id ? { ...node, ...patch } : node,
        ),
      },
    });
    setDirty(true);
    setValidation(null);
    setPreview(null);
  }

  const createFlow = useMutation({
    mutationFn: () => {
      const languages = newFlowLanguages
        .split(",")
        .map((item) => item.trim().toLowerCase().replaceAll("_", "-"))
        .filter(Boolean);
      return apiRequest<CallFlow>("/call-flows", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          name: newFlowName.trim(),
          description: "",
          default_language_code: languages[0] ?? "ru",
          language_codes: languages.length ? languages : ["ru"],
        }),
      });
    },
    onSuccess: async (flow) => {
      setCreating(false);
      setSelectedFlowId(flow.id);
      setSelectedVersionId(flow.versions[0]?.id ?? "");
      setNotice("Сценарий создан");
      await queryClient.invalidateQueries({
        queryKey: ["call-flows", projectId],
      });
      await queryClient.invalidateQueries({
        queryKey: ["projects", projectId],
      });
    },
    onError: (cause: Error) => setError(cause.message),
  });

  const saveDraft = useMutation({
    mutationFn: (current: CallFlowVersion) =>
      apiRequest<CallFlowVersion>(
        `/call-flows/${current.call_flow_id}/versions/${current.id}`,
        {
          method: "PUT",
          body: JSON.stringify({
            expected_lock_version: current.lock_version,
            definition: current.definition,
          }),
        },
      ),
    onSuccess: (saved) => {
      replaceWorking(saved);
      setNotice("Черновик сохранён");
      void queryClient.invalidateQueries({
        queryKey: ["call-flows", projectId],
      });
    },
    onError: (cause: Error) => {
      setError(
        cause instanceof ApiClientError &&
          cause.code === "call_flow_version_conflict"
          ? "Черновик уже изменён другим администратором. Обновите версию перед продолжением."
          : cause.message,
      );
    },
  });

  async function persistedVersion(): Promise<CallFlowVersion> {
    if (!working) throw new Error("Версия сценария не выбрана");
    if (!dirty) return working;
    const saved = await apiRequest<CallFlowVersion>(
      `/call-flows/${working.call_flow_id}/versions/${working.id}`,
      {
        method: "PUT",
        body: JSON.stringify({
          expected_lock_version: working.lock_version,
          definition: working.definition,
        }),
      },
    );
    replaceWorking(saved);
    return saved;
  }

  const validateFlow = useMutation({
    mutationFn: async () => {
      const current = await persistedVersion();
      return apiRequest<CallFlowValidation>(
        `/call-flows/${current.call_flow_id}/versions/${current.id}/validate`,
        { method: "POST" },
      );
    },
    onSuccess: (result) => {
      setValidation(result);
      setNotice(
        result.valid ? "Граф готов к публикации" : "Найдены ошибки сценария",
      );
    },
    onError: (cause: Error) => setError(cause.message),
  });

  const publish = useMutation({
    mutationFn: async () => {
      const current = await persistedVersion();
      const checked = await apiRequest<CallFlowValidation>(
        `/call-flows/${current.call_flow_id}/versions/${current.id}/validate`,
        { method: "POST" },
      );
      setValidation(checked);
      if (!checked.valid)
        throw new Error("Исправьте ошибки графа перед публикацией");
      return apiRequest<CallFlowVersion>(
        `/call-flows/${current.call_flow_id}/versions/${current.id}/publish`,
        {
          method: "POST",
          body: JSON.stringify({ expected_lock_version: current.lock_version }),
        },
      );
    },
    onSuccess: async (published) => {
      replaceWorking(published);
      setNotice(`Версия ${published.version} опубликована`);
      await queryClient.invalidateQueries({
        queryKey: ["call-flows", projectId],
      });
      await queryClient.invalidateQueries({
        queryKey: ["projects", projectId],
      });
    },
    onError: (cause: Error) => setError(cause.message),
  });

  const createDraft = useMutation({
    mutationFn: () => {
      if (!selectedFlow) throw new Error("Сценарий не выбран");
      return apiRequest<CallFlowVersion>(
        `/call-flows/${selectedFlow.id}/drafts`,
        {
          method: "POST",
          body: JSON.stringify({
            source_version_id: selectedFlow.active_version_id,
          }),
        },
      );
    },
    onSuccess: async (draft) => {
      setSelectedVersionId(draft.id);
      replaceWorking(draft);
      setNotice(`Создан черновик версии ${draft.version}`);
      await queryClient.invalidateQueries({
        queryKey: ["call-flows", projectId],
      });
    },
    onError: (cause: Error) => setError(cause.message),
  });

  const archive = useMutation({
    mutationFn: () => {
      if (!selectedFlow) throw new Error("Сценарий не выбран");
      return apiRequest<CallFlow>(`/call-flows/${selectedFlow.id}/archive`, {
        method: "POST",
      });
    },
    onSuccess: async () => {
      setNotice("Сценарий перенесён в архив");
      setSelectedFlowId("");
      setSelectedVersionId("");
      setWorking(null);
      await queryClient.invalidateQueries({
        queryKey: ["call-flows", projectId],
      });
      await queryClient.invalidateQueries({
        queryKey: ["projects", projectId],
      });
    },
    onError: (cause: Error) => setError(cause.message),
  });

  const runPreview = useMutation({
    mutationFn: async (
      answers: Array<{ node_id: string; answer_key: string | null }>,
    ) => {
      const current = await persistedVersion();
      return apiRequest<CallFlowPreview>(
        `/call-flows/${current.call_flow_id}/versions/${current.id}/preview`,
        {
          method: "POST",
          body: JSON.stringify({ language_code: language, answers }),
        },
      );
    },
    onSuccess: setPreview,
    onError: (cause: Error) => setError(cause.message),
  });

  function selectFlow(id: string) {
    if (
      dirty &&
      !window.confirm("Есть несохранённые изменения. Переключить сценарий?")
    )
      return;
    setSelectedFlowId(id);
    setSelectedVersionId("");
    setWorking(null);
    clearMessages();
  }

  function selectVersion(id: string) {
    if (
      dirty &&
      !window.confirm("Есть несохранённые изменения. Переключить версию?")
    )
      return;
    setSelectedVersionId(id);
    setWorking(null);
    clearMessages();
  }

  function addNode(type: CallFlowNodeType) {
    if (!working || !editable) return;
    const nextOrder =
      Math.max(0, ...working.definition.nodes.map((node) => node.order)) + 10;
    const node = createNode(type, nextOrder);
    setWorking({
      ...working,
      definition: {
        ...working.definition,
        nodes: [...working.definition.nodes, node],
      },
    });
    setSelectedNodeId(node.id);
    setDirty(true);
    setValidation(null);
  }

  function deleteNode() {
    if (!working || !selectedNode || !editable) return;
    const referenced = working.definition.nodes.some(
      (node) =>
        node.id !== selectedNode.id &&
        (node.next_node_id === selectedNode.id ||
          node.fallback_node_id === selectedNode.id ||
          node.answers.some(
            (answer) => answer.next_node_id === selectedNode.id,
          )),
    );
    if (referenced) {
      setError("Сначала удалите переходы, ведущие к этому узлу");
      return;
    }
    if (!window.confirm(`Удалить узел «${selectedNode.name}»?`)) return;
    const nodes = working.definition.nodes.filter(
      (node) => node.id !== selectedNode.id,
    );
    setWorking({ ...working, definition: { ...working.definition, nodes } });
    setSelectedNodeId(nodes[0]?.id ?? "");
    setDirty(true);
  }

  function updateAnswer(answerId: string, patch: Partial<CallFlowAnswer>) {
    if (!selectedNode) return;
    updateNode({
      answers: selectedNode.answers.map((answer) =>
        answer.id === answerId ? { ...answer, ...patch } : answer,
      ),
    });
  }

  function choosePreviewAnswer(answerKey: string | null) {
    if (!preview?.current_node) return;
    const answers = [
      ...previewAnswers,
      { node_id: preview.current_node.id, answer_key: answerKey },
    ];
    setPreviewAnswers(answers);
    runPreview.mutate(answers);
  }

  if (me.isPending || project.isPending || flows.isPending)
    return <SectionSkeleton />;
  if (me.isError)
    return (
      <QueryError
        error={me.error}
        retry={() => void me.refetch()}
        title="Нет доступа к профилю"
      />
    );
  if (project.isError)
    return (
      <QueryError
        error={project.error}
        retry={() => void project.refetch()}
        title="Проект недоступен"
      />
    );
  if (flows.isError)
    return (
      <QueryError
        error={flows.error}
        retry={() => void flows.refetch()}
        title="Не удалось загрузить сценарии"
      />
    );

  return (
    <div className="call-flow-page">
      <header className="page-heading call-flow-heading">
        <div>
          <Link className="call-flow-back" href="/app/projects">
            <ArrowLeft size={15} /> Проекты
          </Link>
          <span className="eyebrow">Редактор разговора</span>
          <h1>{project.data?.name}</h1>
          <p>Версионный сценарий для оператора и будущего AI-канала.</p>
        </div>
        <div className="page-actions">
          {dirty && <StatusBadge tone="warning">Есть изменения</StatusBadge>}
          {manage && (
            <Button
              onClick={() => setCreating(true)}
              type="button"
              variant="secondary"
            >
              <Plus size={16} /> Новый сценарий
            </Button>
          )}
        </div>
      </header>

      {creating && (
        <section
          className="panel call-flow-create"
          aria-label="Создание сценария"
        >
          <div>
            <h2>Новый сценарий</h2>
            <p>
              Языки задаются кодами через запятую; список не ограничен enum.
            </p>
          </div>
          <label className="field">
            <span>Название</span>
            <input
              value={newFlowName}
              onChange={(event) => setNewFlowName(event.target.value)}
            />
          </label>
          <label className="field">
            <span>Языковые коды</span>
            <input
              value={newFlowLanguages}
              onChange={(event) => setNewFlowLanguages(event.target.value)}
              placeholder="ru, uz, en, kaa"
            />
          </label>
          <div className="page-actions">
            <Button
              onClick={() => setCreating(false)}
              type="button"
              variant="quiet"
            >
              Отмена
            </Button>
            <Button
              disabled={createFlow.isPending || newFlowName.trim().length < 2}
              onClick={() => {
                clearMessages();
                createFlow.mutate();
              }}
              type="button"
            >
              Создать
            </Button>
          </div>
        </section>
      )}

      {!flows.data?.length && !creating && (
        <section className="panel empty-state">
          <div>
            <Workflow size={32} />
            <h2>Сценариев пока нет</h2>
            <p>
              Создайте первый черновик. Звонки продолжат работать и без
              опубликованного сценария.
            </p>
            {manage && (
              <Button onClick={() => setCreating(true)}>
                Создать сценарий
              </Button>
            )}
          </div>
        </section>
      )}

      {!!flows.data?.length && (
        <>
          <section className="panel call-flow-toolbar">
            <label className="field">
              <span>Сценарий</span>
              <select
                value={selectedFlowId}
                onChange={(event) => selectFlow(event.target.value)}
              >
                {flows.data.map((flow) => (
                  <option key={flow.id} value={flow.id}>
                    {flow.name}
                    {flow.archived_at ? " — архив" : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Версия</span>
              <select
                value={selectedVersionId}
                onChange={(event) => selectVersion(event.target.value)}
              >
                {selectedFlow?.versions.map((item) => (
                  <option key={item.id} value={item.id}>
                    v{item.version} — {statusLabels[item.status]}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Язык редактора</span>
              <select
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
              >
                {selectedFlow?.language_codes.map((code) => (
                  <option key={code} value={code}>
                    {code.toUpperCase()}
                  </option>
                ))}
              </select>
            </label>
            <div className="call-flow-version-state">
              {working && (
                <StatusBadge tone={versionTone(working.status)}>
                  {statusLabels[working.status]} · v{working.version}
                </StatusBadge>
              )}
              {selectedFlow?.active_version_id === working?.id && (
                <StatusBadge tone="primary">Активная</StatusBadge>
              )}
            </div>
          </section>

          {version.isPending && <SectionSkeleton />}
          {version.isError && (
            <QueryError
              error={version.error}
              retry={() => void version.refetch()}
              title="Версия недоступна"
            />
          )}
          {working && (
            <>
              <div className="call-flow-actions panel">
                <div className="call-flow-action-status">
                  {notice && (
                    <span className="success-note">
                      <CheckCircle2 size={15} />
                      {notice}
                    </span>
                  )}
                  {error && (
                    <span className="form-error" role="alert">
                      <ShieldAlert size={15} />
                      {error}
                    </span>
                  )}
                </div>
                <div className="page-actions">
                  {editable && (
                    <Button
                      disabled={saveDraft.isPending || !dirty}
                      onClick={() => {
                        clearMessages();
                        saveDraft.mutate(working);
                      }}
                      type="button"
                      variant="secondary"
                    >
                      <Save size={15} />{" "}
                      {saveDraft.isPending ? "Сохраняем…" : "Сохранить"}
                    </Button>
                  )}
                  {manage && working.status === "draft" && (
                    <>
                      <Button
                        disabled={validateFlow.isPending}
                        onClick={() => {
                          clearMessages();
                          validateFlow.mutate();
                        }}
                        type="button"
                        variant="quiet"
                      >
                        <CheckCircle2 size={15} /> Проверить
                      </Button>
                      <Button
                        disabled={publish.isPending}
                        onClick={() => {
                          clearMessages();
                          if (
                            window.confirm(
                              "Опубликовать эту версию? После публикации она станет неизменяемой.",
                            )
                          ) {
                            publish.mutate();
                          }
                        }}
                        type="button"
                      >
                        <Send size={15} /> Опубликовать
                      </Button>
                    </>
                  )}
                  {manage &&
                    working.status !== "draft" &&
                    selectedFlow?.archived_at === null && (
                      <Button
                        disabled={createDraft.isPending}
                        onClick={() => createDraft.mutate()}
                        type="button"
                      >
                        <RefreshCcw size={15} /> Новая версия
                      </Button>
                    )}
                  {manage && selectedFlow?.archived_at === null && (
                    <Button
                      disabled={archive.isPending}
                      onClick={() => {
                        if (
                          window.confirm(
                            "Архивировать сценарий? Новые звонки перестанут его получать.",
                          )
                        ) {
                          archive.mutate();
                        }
                      }}
                      type="button"
                      variant="quiet"
                    >
                      <Archive size={15} /> В архив
                    </Button>
                  )}
                </div>
              </div>

              <main className="call-flow-workspace">
                <section className="panel call-flow-nodes">
                  <div className="panel-title-row">
                    <div>
                      <span className="eyebrow">Структура</span>
                      <h2>Узлы</h2>
                    </div>
                    <StatusBadge>{sortedNodes.length}</StatusBadge>
                  </div>
                  {editable && (
                    <label className="field call-flow-add-node">
                      <span>Добавить шаг</span>
                      <select
                        aria-label="Добавить узел"
                        defaultValue=""
                        onChange={(event) => {
                          if (event.target.value)
                            addNode(event.target.value as CallFlowNodeType);
                          event.target.value = "";
                        }}
                      >
                        <option value="">Выберите тип…</option>
                        {nodeTypes.map((type) => (
                          <option key={type.value} value={type.value}>
                            {type.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <div className="call-flow-node-list">
                    {sortedNodes.map((node, index) => (
                      <button
                        className={node.id === selectedNodeId ? "active" : ""}
                        key={node.id}
                        onClick={() => setSelectedNodeId(node.id)}
                        type="button"
                      >
                        <span className="call-flow-node-index">
                          {index + 1}
                        </span>
                        <span>
                          <strong>{node.name}</strong>
                          <small>
                            {
                              nodeTypes.find(
                                (item) => item.value === node.node_type,
                              )?.label
                            }
                          </small>
                        </span>
                        <ChevronRight size={15} />
                      </button>
                    ))}
                  </div>
                </section>

                <section className="panel call-flow-node-editor">
                  {!selectedNode && (
                    <div className="empty-state compact">
                      <div>
                        <GitBranch size={28} />
                        <h3>Выберите узел</h3>
                      </div>
                    </div>
                  )}
                  {selectedNode && (
                    <NodeEditor
                      editable={Boolean(editable)}
                      fields={customerFields.data ?? []}
                      language={language}
                      node={selectedNode}
                      nodes={sortedNodes}
                      onDelete={deleteNode}
                      onUpdate={updateNode}
                      onUpdateAnswer={updateAnswer}
                    />
                  )}
                </section>

                <aside className="panel call-flow-inspector">
                  <div className="panel-title-row">
                    <div>
                      <span className="eyebrow">Контроль</span>
                      <h2>Проверка и preview</h2>
                    </div>
                    <CirclePlay size={19} />
                  </div>

                  {validation && (
                    <div
                      className={
                        validation.valid ? "validation-ok" : "validation-errors"
                      }
                    >
                      <strong>
                        {validation.valid
                          ? "Граф корректен"
                          : `${validation.errors.length} ошибок`}
                      </strong>
                      {validation.errors.map((issue, index) => (
                        <button
                          key={`${issue.code}-${issue.node_id ?? "flow"}-${index}`}
                          onClick={() =>
                            issue.node_id && setSelectedNodeId(issue.node_id)
                          }
                          type="button"
                        >
                          <span>{issue.node_name ?? "Сценарий"}</span>
                          <small>{issue.message}</small>
                        </button>
                      ))}
                    </div>
                  )}

                  <div className="preview-controls">
                    <Button
                      disabled={runPreview.isPending}
                      onClick={() => {
                        clearMessages();
                        setPreviewAnswers([]);
                        runPreview.mutate([]);
                      }}
                      type="button"
                      variant="secondary"
                    >
                      <CirclePlay size={15} /> Запустить preview
                    </Button>
                    {preview && (
                      <>
                        <Button
                          disabled={
                            !previewAnswers.length || runPreview.isPending
                          }
                          onClick={() => {
                            const answers = previewAnswers.slice(0, -1);
                            setPreviewAnswers(answers);
                            runPreview.mutate(answers);
                          }}
                          type="button"
                          variant="quiet"
                        >
                          <RotateCcw size={14} /> Назад
                        </Button>
                        <Button
                          onClick={() => {
                            setPreview(null);
                            setPreviewAnswers([]);
                          }}
                          type="button"
                          variant="quiet"
                        >
                          Сбросить
                        </Button>
                      </>
                    )}
                  </div>

                  {preview && (
                    <div className="preview-card">
                      <div className="preview-path">
                        {preview.path.map((item) => (
                          <span key={item.id}>{item.name}</span>
                        ))}
                      </div>
                      {preview.completed ? (
                        <div className="preview-complete">
                          <CheckCircle2 size={22} />
                          <strong>Сценарий завершён</strong>
                        </div>
                      ) : preview.current_node ? (
                        <>
                          <StatusBadge>
                            {
                              nodeTypes.find(
                                (item) =>
                                  item.value ===
                                  preview.current_node?.node_type,
                              )?.label
                            }
                          </StatusBadge>
                          <h3>{preview.current_node.name}</h3>
                          {preview.current_node.text && (
                            <p>{preview.current_node.text}</p>
                          )}
                          {preview.current_node.hint && (
                            <small>{preview.current_node.hint}</small>
                          )}
                          {preview.current_node.action_is_inert && (
                            <div className="preview-inert">
                              Действие отключено в preview и ничего не
                              записывает.
                            </div>
                          )}
                          <div className="preview-answer-list">
                            {preview.current_node.answers.map((answer) => (
                              <Button
                                key={answer.key}
                                onClick={() => choosePreviewAnswer(answer.key)}
                                type="button"
                                variant="secondary"
                              >
                                {answer.label}
                              </Button>
                            ))}
                            {!preview.current_node.answers.length && (
                              <Button
                                onClick={() => choosePreviewAnswer(null)}
                                type="button"
                                variant="secondary"
                              >
                                Продолжить <ChevronRight size={14} />
                              </Button>
                            )}
                          </div>
                        </>
                      ) : null}
                    </div>
                  )}
                </aside>
              </main>
            </>
          )}
        </>
      )}
    </div>
  );
}

function NodeEditor({
  node,
  nodes,
  fields,
  language,
  editable,
  onUpdate,
  onUpdateAnswer,
  onDelete,
}: {
  node: CallFlowNode;
  nodes: CallFlowNode[];
  fields: CustomerFieldDefinition[];
  language: string;
  editable: boolean;
  onUpdate: (patch: Partial<CallFlowNode>) => void;
  onUpdateAnswer: (answerId: string, patch: Partial<CallFlowAnswer>) => void;
  onDelete: () => void;
}) {
  const transitionNodes = nodes.filter((item) => item.id !== node.id);
  const supportsAnswers =
    node.node_type === "customer_question" || node.node_type === "choice";
  const supportsField = ["value_input", "update_customer_field"].includes(
    node.node_type,
  );

  return (
    <div className="call-flow-node-form">
      <div className="panel-title-row">
        <div>
          <span className="eyebrow">Настройка узла</span>
          <h2>{node.name}</h2>
        </div>
        {editable && (
          <Button
            aria-label="Удалить узел"
            onClick={onDelete}
            type="button"
            variant="quiet"
          >
            <Trash2 size={16} />
          </Button>
        )}
      </div>
      <div className="call-flow-form-grid">
        <label className="field">
          <span>Название</span>
          <input
            disabled={!editable}
            value={node.name}
            onChange={(event) => onUpdate({ name: event.target.value })}
          />
        </label>
        <label className="field">
          <span>Системный ключ</span>
          <input
            disabled={!editable}
            value={node.system_key}
            onChange={(event) => onUpdate({ system_key: event.target.value })}
          />
        </label>
        <label className="field">
          <span>Тип узла</span>
          <select
            disabled={!editable}
            value={node.node_type}
            onChange={(event) =>
              onUpdate({ node_type: event.target.value as CallFlowNodeType })
            }
          >
            {nodeTypes.map((type) => (
              <option key={type.value} value={type.value}>
                {type.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Порядок</span>
          <input
            disabled={!editable}
            min={0}
            type="number"
            value={node.order}
            onChange={(event) =>
              onUpdate({ order: Number(event.target.value) })
            }
          />
        </label>
      </div>
      <label className="field">
        <span>Текст · {language.toUpperCase()}</span>
        <textarea
          disabled={!editable}
          rows={5}
          value={node.text_by_language[language] ?? ""}
          onChange={(event) =>
            onUpdate({
              text_by_language: {
                ...node.text_by_language,
                [language]: event.target.value,
              },
            })
          }
        />
      </label>
      <label className="field">
        <span>Подсказка оператору · {language.toUpperCase()}</span>
        <textarea
          disabled={!editable}
          rows={3}
          value={node.hint_by_language[language] ?? ""}
          onChange={(event) =>
            onUpdate({
              hint_by_language: {
                ...node.hint_by_language,
                [language]: event.target.value,
              },
            })
          }
        />
      </label>
      <label className="checkbox-row">
        <input
          checked={node.is_required}
          disabled={!editable}
          onChange={(event) => onUpdate({ is_required: event.target.checked })}
          type="checkbox"
        />
        <span>Обязательный шаг</span>
      </label>

      {supportsField && (
        <label className="field">
          <span>Поле клиента</span>
          <select
            disabled={!editable}
            value={node.customer_field_definition_id ?? ""}
            onChange={(event) =>
              onUpdate({
                customer_field_definition_id: event.target.value || null,
              })
            }
          >
            <option value="">Не выбрано</option>
            {fields.map((field) => (
              <option key={field.id} value={field.id}>
                {field.name} · {field.key}
              </option>
            ))}
          </select>
        </label>
      )}

      {node.node_type !== "end" && (
        <div className="call-flow-transition-grid">
          <label className="field">
            <span>Следующий узел</span>
            <select
              disabled={!editable || supportsAnswers}
              value={node.next_node_id ?? ""}
              onChange={(event) =>
                onUpdate({ next_node_id: event.target.value || null })
              }
            >
              <option value="">Не выбран</option>
              {transitionNodes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>Fallback-переход</span>
            <select
              disabled={!editable}
              value={node.fallback_node_id ?? ""}
              onChange={(event) =>
                onUpdate({ fallback_node_id: event.target.value || null })
              }
            >
              <option value="">Не выбран</option>
              {transitionNodes.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      {supportsAnswers && (
        <section className="call-flow-answer-editor">
          <div className="panel-title-row">
            <div>
              <h3>Варианты ответа</h3>
              <p>Каждый вариант ведёт к узлу этой версии.</p>
            </div>
            {editable && (
              <Button
                onClick={() =>
                  onUpdate({
                    answers: [
                      ...node.answers,
                      emptyAnswer(language, node.answers.length),
                    ],
                  })
                }
                type="button"
                variant="quiet"
              >
                <Plus size={14} /> Вариант
              </Button>
            )}
          </div>
          {node.answers.map((answer) => (
            <div className="call-flow-answer-row" key={answer.id}>
              <input
                aria-label="Ключ ответа"
                disabled={!editable}
                value={answer.key}
                onChange={(event) =>
                  onUpdateAnswer(answer.id, { key: event.target.value })
                }
              />
              <input
                aria-label={`Текст ответа ${language}`}
                disabled={!editable}
                value={answer.label_by_language[language] ?? ""}
                onChange={(event) =>
                  onUpdateAnswer(answer.id, {
                    label_by_language: {
                      ...answer.label_by_language,
                      [language]: event.target.value,
                    },
                  })
                }
              />
              <select
                aria-label="Переход ответа"
                disabled={!editable}
                value={answer.next_node_id ?? ""}
                onChange={(event) =>
                  onUpdateAnswer(answer.id, {
                    next_node_id: event.target.value || null,
                  })
                }
              >
                <option value="">Нет перехода</option>
                {transitionNodes.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
              {editable && (
                <button
                  aria-label="Удалить вариант"
                  onClick={() =>
                    onUpdate({
                      answers: node.answers.filter(
                        (item) => item.id !== answer.id,
                      ),
                    })
                  }
                  type="button"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
        </section>
      )}

      {actionTypes.has(node.node_type) && (
        <div className="call-flow-action-note">
          <ShieldAlert size={17} />
          <div>
            <strong>Безопасное определение действия</strong>
            <p>
              В редакторе и preview действие не выполняется. Исполнение будет
              подключено на следующих этапах.
            </p>
          </div>
        </div>
      )}
      <div className="call-flow-node-identity">UUID: {node.id}</div>
    </div>
  );
}
