"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  CheckCircle2,
  ListChecks,
  Pencil,
  Plus,
  RotateCcw,
  Save,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { apiRequest } from "@/lib/api";
import type {
  AuthResponse,
  CallResultCatalog,
  CallResultCategory,
  CallResultDefinition,
  Project,
} from "@/lib/types";

const categories: Array<{
  code: CallResultCategory;
  label: string;
  subtitle: string;
}> = [
  {
    code: "successful",
    label: "Успешные",
    subtitle: "Целевое действие выполнено",
  },
  {
    code: "intermediate",
    label: "Промежуточные",
    subtitle: "Нужен следующий шаг",
  },
  { code: "unreachable", label: "Недозвон", subtitle: "Контакт не состоялся" },
  {
    code: "unsuccessful",
    label: "Неуспешные",
    subtitle: "Работа завершена без успеха",
  },
];

type EditorDraft = {
  id: string | null;
  system_code: string;
  category: CallResultCategory;
  name: string;
  ru: string;
  uz: string;
  en: string;
  kaa: string;
  additional_translations: string;
  description: string;
  color: string;
  requires_comment: boolean;
  requires_callback: boolean;
  requires_callback_at: boolean;
  creates_task: boolean;
  next_customer_status: string;
  return_to_queue: boolean;
  completes_customer: boolean;
  do_not_call: boolean;
  counts_as_success: boolean;
  is_active: boolean;
};

const emptyDraft: EditorDraft = {
  id: null,
  system_code: "",
  category: "intermediate",
  name: "",
  ru: "",
  uz: "",
  en: "",
  kaa: "",
  additional_translations: "",
  description: "",
  color: "#64748B",
  requires_comment: false,
  requires_callback: false,
  requires_callback_at: false,
  creates_task: false,
  next_customer_status: "completed",
  return_to_queue: false,
  completes_customer: true,
  do_not_call: false,
  counts_as_success: false,
  is_active: true,
};

function canManage(role: AuthResponse["user"]["role"] | undefined) {
  return role === "tenant_owner" || role === "tenant_manager";
}

function draftFromDefinition(value: CallResultDefinition): EditorDraft {
  const standard = new Set(["ru", "uz", "en", "kaa"]);
  return {
    id: value.id,
    system_code: value.system_code,
    category: value.category,
    name: value.name,
    ru: value.name_translations.ru ?? "",
    uz: value.name_translations.uz ?? "",
    en: value.name_translations.en ?? "",
    kaa: value.name_translations.kaa ?? "",
    additional_translations: Object.entries(value.name_translations)
      .filter(([language]) => !standard.has(language))
      .map(([language, label]) => `${language}=${label}`)
      .join("\n"),
    description: value.description,
    color: value.color,
    requires_comment: value.requires_comment,
    requires_callback: value.requires_callback,
    requires_callback_at: value.requires_callback_at,
    creates_task: value.creates_task,
    next_customer_status: value.next_customer_status ?? "",
    return_to_queue: value.return_to_queue,
    completes_customer: value.completes_customer,
    do_not_call: value.do_not_call,
    counts_as_success: value.counts_as_success,
    is_active: value.is_active,
  };
}

function translationsFromDraft(draft: EditorDraft) {
  const translations: Record<string, string> = {
    ru: draft.ru.trim(),
    uz: draft.uz.trim(),
    en: draft.en.trim(),
    kaa: draft.kaa.trim(),
  };
  for (const line of draft.additional_translations.split("\n")) {
    const separator = line.indexOf("=");
    if (separator < 1) continue;
    const language = line.slice(0, separator).trim().toLowerCase();
    const label = line.slice(separator + 1).trim();
    if (language && label) translations[language] = label;
  }
  return translations;
}

export function CallResultsEditor({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<EditorDraft | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
  });
  const project = useQuery({
    queryKey: ["projects", projectId],
    queryFn: () => apiRequest<Project>(`/projects/${projectId}`),
  });
  const manage = canManage(me.data?.user.role);
  const catalog = useQuery({
    queryKey: ["call-results", projectId, manage],
    queryFn: () =>
      apiRequest<CallResultCatalog>(
        `/call-results?project_id=${projectId}${manage ? "&include_archived=true" : ""}`,
      ),
    enabled: me.isSuccess,
  });
  const definitions = catalog.data?.definitions ?? [];
  const grouped = useMemo(
    () =>
      Object.fromEntries(
        categories.map((category) => [
          category.code,
          definitions
            .filter((definition) => definition.category === category.code)
            .sort((left, right) => left.sort_order - right.sort_order),
        ]),
      ) as Record<CallResultCategory, CallResultDefinition[]>,
    [definitions],
  );

  function showError(value: unknown) {
    setNotice("");
    setError(value instanceof Error ? value.message : "Операция не выполнена");
  }

  async function refreshCatalog(message: string) {
    await queryClient.invalidateQueries({
      queryKey: ["call-results", projectId],
    });
    setDraft(null);
    setError("");
    setNotice(message);
  }

  const save = useMutation({
    mutationFn: async (value: EditorDraft) => {
      const translations = translationsFromDraft(value);
      if (!value.system_code.trim() || !value.name.trim()) {
        throw new Error("Заполните название и системный код");
      }
      if (
        [
          translations.ru,
          translations.uz,
          translations.en,
          translations.kaa,
        ].some((item) => !item)
      ) {
        throw new Error("Заполните переводы RU, UZ, EN и KAA");
      }
      const body = {
        project_id: projectId,
        system_code: value.system_code,
        category: value.category,
        name: value.name,
        name_translations: translations,
        description: value.description,
        color: value.color,
        is_active: value.is_active,
        requires_comment: value.requires_comment,
        requires_callback: value.requires_callback,
        requires_callback_at:
          value.requires_callback && value.requires_callback_at,
        creates_task: value.creates_task,
        next_customer_status: value.next_customer_status || null,
        return_to_queue: value.return_to_queue,
        completes_customer: value.completes_customer,
        do_not_call: value.do_not_call,
        counts_as_success: value.counts_as_success,
        sort_order: value.id
          ? (definitions.find((item) => item.id === value.id)?.sort_order ??
            definitions.length * 10 + 10)
          : definitions.length * 10 + 10,
      };
      return apiRequest<CallResultDefinition>(
        value.id ? `/call-results/${value.id}` : "/call-results",
        {
          method: value.id ? "PATCH" : "POST",
          body: JSON.stringify(
            value.id ? { ...body, project_id: undefined } : body,
          ),
        },
      );
    },
    onSuccess: () => void refreshCatalog("Каталог результатов сохранён."),
    onError: showError,
  });
  const archive = useMutation({
    mutationFn: (id: string) =>
      apiRequest(`/call-results/${id}/archive`, { method: "POST" }),
    onSuccess: () => void refreshCatalog("Результат перемещён в архив."),
    onError: showError,
  });
  const restore = useMutation({
    mutationFn: (id: string) =>
      apiRequest(`/call-results/${id}/restore`, { method: "POST" }),
    onSuccess: () => void refreshCatalog("Результат восстановлен."),
    onError: showError,
  });
  const reorder = useMutation({
    mutationFn: (ids: string[]) =>
      apiRequest<CallResultCatalog>(
        `/call-results/reorder?project_id=${projectId}`,
        {
          method: "POST",
          body: JSON.stringify({ definition_ids: ids }),
        },
      ),
    onSuccess: () => void refreshCatalog("Порядок обновлён."),
    onError: showError,
  });

  function move(definition: CallResultDefinition, direction: -1 | 1) {
    const categoryItems = grouped[definition.category];
    const currentIndex = categoryItems.findIndex(
      (item) => item.id === definition.id,
    );
    const nextIndex = currentIndex + direction;
    if (currentIndex < 0 || nextIndex < 0 || nextIndex >= categoryItems.length)
      return;
    const reordered = [...categoryItems];
    [reordered[currentIndex], reordered[nextIndex]] = [
      reordered[nextIndex],
      reordered[currentIndex],
    ];
    const otherIds = definitions
      .filter((item) => item.category !== definition.category)
      .sort((left, right) => left.sort_order - right.sort_order)
      .map((item) => item.id);
    reorder.mutate([...reordered.map((item) => item.id), ...otherIds]);
  }

  if (me.isPending || project.isPending || catalog.isPending)
    return <SectionSkeleton />;
  if (me.isError || project.isError || catalog.isError) {
    return (
      <QueryError
        title="Каталог результатов недоступен"
        error={
          me.error ??
          project.error ??
          catalog.error ??
          new Error("Каталог результатов недоступен")
        }
        retry={() => {
          void me.refetch();
          void project.refetch();
          void catalog.refetch();
        }}
      />
    );
  }

  return (
    <div className="call-results-page">
      <div className="page-heading row-between">
        <div>
          <div className="project-editor-breadcrumb">
            <Link href="/app/projects">
              <ArrowLeft size={15} /> Проекты
            </Link>
            <span>Каталог результатов</span>
          </div>
          <h1>{project.data?.name}</h1>
          <p>
            Настраиваемые итоги звонка с неизменяемым историческим snapshot.
          </p>
        </div>
        {manage && (
          <Button onClick={() => setDraft({ ...emptyDraft })}>
            <Plus size={16} /> Новый результат
          </Button>
        )}
      </div>

      {(notice || error) && (
        <div
          className={
            error ? "call-results-notice error" : "call-results-notice success"
          }
        >
          {error || notice}
        </div>
      )}

      <div className="call-result-category-grid">
        {categories.map((category) => (
          <section
            className={`panel call-result-category ${category.code}`}
            key={category.code}
          >
            <header>
              <div>
                <span className="eyebrow">{category.subtitle}</span>
                <h2>{category.label}</h2>
              </div>
              <StatusBadge>{grouped[category.code].length}</StatusBadge>
            </header>
            {grouped[category.code].length === 0 ? (
              <div className="call-result-empty">
                <ListChecks size={22} />
                <span>Результатов пока нет</span>
              </div>
            ) : (
              <div className="call-result-list">
                {grouped[category.code].map((definition, index) => (
                  <article
                    className={`call-result-item ${definition.archived_at ? "archived" : ""}`}
                    key={definition.id}
                  >
                    <span
                      className="call-result-color"
                      style={{ background: definition.color }}
                    />
                    <div className="call-result-copy">
                      <strong>
                        {definition.name_translations.ru ?? definition.name}
                      </strong>
                      <span>{definition.system_code}</span>
                      <div className="call-result-flags">
                        {definition.requires_comment && (
                          <small>Комментарий</small>
                        )}
                        {definition.requires_callback && (
                          <small>Перезвон</small>
                        )}
                        {definition.do_not_call && <small>Do not call</small>}
                        {definition.used_count > 0 && (
                          <small>Использован: {definition.used_count}</small>
                        )}
                      </div>
                    </div>
                    <div className="call-result-actions">
                      {!definition.is_active && !definition.archived_at && (
                        <StatusBadge tone="warning">Выключен</StatusBadge>
                      )}
                      {definition.archived_at && (
                        <StatusBadge tone="neutral">Архив</StatusBadge>
                      )}
                      {manage && !definition.archived_at && (
                        <>
                          <button
                            aria-label="Выше"
                            disabled={index === 0 || reorder.isPending}
                            onClick={() => move(definition, -1)}
                            type="button"
                          >
                            <ArrowUp size={14} />
                          </button>
                          <button
                            aria-label="Ниже"
                            disabled={
                              index === grouped[category.code].length - 1 ||
                              reorder.isPending
                            }
                            onClick={() => move(definition, 1)}
                            type="button"
                          >
                            <ArrowDown size={14} />
                          </button>
                          <button
                            aria-label="Редактировать"
                            onClick={() =>
                              setDraft(draftFromDefinition(definition))
                            }
                            type="button"
                          >
                            <Pencil size={14} />
                          </button>
                          <button
                            aria-label="В архив"
                            onClick={() => {
                              if (
                                window.confirm(
                                  "Архивировать результат? История звонков сохранится.",
                                )
                              )
                                archive.mutate(definition.id);
                            }}
                            type="button"
                          >
                            <Archive size={14} />
                          </button>
                        </>
                      )}
                      {manage && definition.archived_at && (
                        <button
                          aria-label="Восстановить"
                          onClick={() => restore.mutate(definition.id)}
                          type="button"
                        >
                          <RotateCcw size={14} />
                        </button>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        ))}
      </div>

      {draft && manage && (
        <section className="panel call-result-editor">
          <header className="row-between">
            <div>
              <span className="eyebrow">Настройка результата</span>
              <h2>{draft.id ? "Редактирование" : "Новый результат"}</h2>
            </div>
            <button
              aria-label="Закрыть редактор"
              onClick={() => setDraft(null)}
              type="button"
            >
              ×
            </button>
          </header>
          {draft.id &&
          definitions.find((item) => item.id === draft.id)?.used_count ? (
            <div className="call-result-history-warning">
              <CheckCircle2 size={17} /> Переименование не изменит сохранённые
              результаты прошлых звонков.
            </div>
          ) : null}
          <div className="call-result-form-grid">
            <label className="field">
              <span>Название</span>
              <input
                value={draft.name}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    name: event.target.value,
                    ru: draft.ru || event.target.value,
                  })
                }
              />
            </label>
            <label className="field">
              <span>Системный код</span>
              <input
                value={draft.system_code}
                onChange={(event) =>
                  setDraft({ ...draft, system_code: event.target.value })
                }
              />
            </label>
            <label className="field">
              <span>Категория</span>
              <select
                value={draft.category}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    category: event.target.value as CallResultCategory,
                  })
                }
              >
                {categories.map((category) => (
                  <option key={category.code} value={category.code}>
                    {category.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Цвет</span>
              <input
                type="color"
                value={draft.color}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    color: event.target.value.toUpperCase(),
                  })
                }
              />
            </label>
            {(["ru", "uz", "en", "kaa"] as const).map((language) => (
              <label className="field" key={language}>
                <span>Название · {language.toUpperCase()}</span>
                <input
                  value={draft[language]}
                  onChange={(event) =>
                    setDraft({ ...draft, [language]: event.target.value })
                  }
                />
              </label>
            ))}
            <label className="field span-two">
              <span>Дополнительные переводы</span>
              <textarea
                placeholder={"kaa-latn=...\nkaa-cyrl=..."}
                rows={3}
                value={draft.additional_translations}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    additional_translations: event.target.value,
                  })
                }
              />
            </label>
            <label className="field span-two">
              <span>Описание</span>
              <textarea
                rows={3}
                value={draft.description}
                onChange={(event) =>
                  setDraft({ ...draft, description: event.target.value })
                }
              />
            </label>
            <label className="field">
              <span>Следующий статус клиента</span>
              <input
                value={draft.next_customer_status}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    next_customer_status: event.target.value,
                  })
                }
              />
            </label>
          </div>
          <div className="call-result-toggle-grid">
            {(
              [
                ["requires_comment", "Обязательный комментарий"],
                ["requires_callback", "Создавать перезвон"],
                ["requires_callback_at", "Обязательная дата перезвона"],
                ["creates_task", "Подготовить общую задачу"],
                ["return_to_queue", "Вернуть клиента в очередь"],
                ["completes_customer", "Завершить работу с клиентом"],
                ["do_not_call", "Отметить do-not-call"],
                ["counts_as_success", "Учитывать как успешный"],
                ["is_active", "Результат активен"],
              ] as const
            ).map(([field, label]) => (
              <label key={field}>
                <input
                  checked={draft[field]}
                  disabled={
                    field === "requires_callback_at" && !draft.requires_callback
                  }
                  onChange={(event) =>
                    setDraft({ ...draft, [field]: event.target.checked })
                  }
                  type="checkbox"
                />
                {label}
              </label>
            ))}
          </div>
          <div className="call-result-editor-actions">
            <Button
              disabled={save.isPending}
              onClick={() => save.mutate(draft)}
            >
              <Save size={16} /> {save.isPending ? "Сохраняем…" : "Сохранить"}
            </Button>
            <Button onClick={() => setDraft(null)} variant="secondary">
              Отмена
            </Button>
          </div>
        </section>
      )}
    </div>
  );
}
