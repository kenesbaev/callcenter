"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  FileSpreadsheet,
  Filter,
  Mail,
  Pencil,
  Phone,
  Plus,
  RotateCcw,
  Search,
  SlidersHorizontal,
  Tag,
  Upload,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { z } from "zod";
import { Button, StatusBadge } from "@teamora/ui";
import { CustomerBackgroundImport } from "@/components/customer-background-import";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { ApiClientError, apiRequest, idempotencyKey } from "@/lib/api";
import { customerImportFields } from "@/lib/customer-import";
import type {
  AuthResponse,
  Customer,
  CustomerFieldDefinition,
  CustomerFieldType,
  CustomerImportPreview,
  CustomerImportReport,
  Page,
  Project,
  ProjectOptions,
  Role,
} from "@/lib/types";

const contactSchema = z.object({
  id: z.string().uuid().optional(),
  kind: z.enum(["phone", "email"]),
  value: z.string(),
  label: z.string(),
  is_primary: z.boolean(),
});

const customerSchema = z.object({
  project_id: z.string().uuid("Выберите проект"),
  display_name: z.string().trim().min(2, "Введите ФИО клиента").max(160),
  preferred_language: z.enum(["ru", "uz", "en", "kaa"]),
  external_reference: z.string().trim().max(160),
  status: z.enum(["new", "assigned", "callback", "completed", "do_not_call"]),
  city: z.string().trim().max(160),
  region: z.string().trim().max(160),
  address: z.string().trim().max(500),
  job_title: z.string().trim().max(160),
  organization: z.string().trim().max(200),
  tags: z.string(),
  description: z.string().trim().max(4000),
  source: z.string().trim().max(120),
  assigned_user_id: z.string(),
  next_contact_at: z.string(),
  contacts: z.array(contactSchema).max(20),
  custom_fields: z.record(z.string(), z.unknown()),
});

export type CustomerForm = z.infer<typeof customerSchema>;

const statusLabels: Record<string, string> = {
  new: "Новый",
  assigned: "Назначен",
  callback: "Перезвон",
  completed: "Обработан",
  do_not_call: "Не звонить",
};

const languageLabels: Record<string, string> = {
  ru: "Русский",
  uz: "O‘zbekcha",
  en: "English",
  kaa: "Qaraqalpaqsha",
};

export function canManageCustomers(role: Role | undefined): boolean {
  return role === "tenant_owner" || role === "tenant_manager";
}

function blankCustomerForm(projectId = ""): CustomerForm {
  return {
    project_id: projectId,
    display_name: "",
    preferred_language: "ru",
    external_reference: "",
    status: "new",
    city: "",
    region: "",
    address: "",
    job_title: "",
    organization: "",
    tags: "",
    description: "",
    source: "",
    assigned_user_id: "",
    next_contact_at: "",
    contacts: [
      { kind: "phone", value: "+998", label: "Мобильный", is_primary: true },
    ],
    custom_fields: {},
  };
}

function localDateTime(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function customerForm(customer: Customer): CustomerForm {
  return {
    project_id: customer.project_id,
    display_name: customer.display_name ?? "",
    preferred_language: customer.preferred_language ?? "ru",
    external_reference: customer.external_reference ?? "",
    status: customer.status as CustomerForm["status"],
    city: customer.city ?? "",
    region: customer.region ?? "",
    address: customer.address ?? "",
    job_title: customer.job_title ?? "",
    organization: customer.organization ?? "",
    tags: customer.tags.join(", "),
    description: customer.description,
    source: customer.source ?? "",
    assigned_user_id: customer.assigned_user_id ?? "",
    next_contact_at: localDateTime(customer.next_contact_at),
    contacts: customer.contacts.map((contact) => ({
      id: contact.id,
      kind: contact.kind === "email" ? "email" : "phone",
      value: contact.value,
      label: contact.label ?? "",
      is_primary: contact.is_primary,
    })),
    custom_fields: customer.custom_fields,
  };
}

export function customerFormToPayload(value: CustomerForm) {
  return {
    ...value,
    external_reference: value.external_reference || null,
    city: value.city || null,
    region: value.region || null,
    address: value.address || null,
    job_title: value.job_title || null,
    organization: value.organization || null,
    tags: value.tags
      .split(",")
      .map((tag) => tag.trim())
      .filter(Boolean),
    source: value.source || null,
    assigned_user_id: value.assigned_user_id || null,
    next_contact_at: value.next_contact_at
      ? new Date(value.next_contact_at).toISOString()
      : null,
    contacts: value.contacts
      .filter((contact) => contact.value.trim())
      .map((contact) => ({
        ...contact,
        id: contact.id || undefined,
        label: contact.label || null,
        value: contact.value.trim(),
      })),
  };
}

function primaryContact(customer: Customer, kind: "phone" | "email") {
  const contacts = customer.contacts.filter((contact) => contact.kind === kind);
  return (
    contacts.find((contact) => contact.is_primary)?.value ?? contacts[0]?.value
  );
}

function statusTone(customer: Customer) {
  if (customer.archived_at) return "neutral" as const;
  if (customer.status === "do_not_call") return "danger" as const;
  if (customer.status === "callback") return "warning" as const;
  if (customer.status === "completed") return "success" as const;
  return "primary" as const;
}

function CustomFieldControl({
  definition,
  value,
  onChange,
}: {
  definition: CustomerFieldDefinition;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const id = `customer-custom-${definition.key}`;
  if (definition.field_type === "boolean") {
    return (
      <label className="customer-check-row" htmlFor={id}>
        <input
          checked={Boolean(value)}
          id={id}
          onChange={(event) => onChange(event.target.checked)}
          type="checkbox"
        />
        <span>{definition.name}</span>
        {definition.is_required && <small>Обязательно</small>}
      </label>
    );
  }
  if (definition.field_type === "select") {
    return (
      <div className="field">
        <label htmlFor={id}>{definition.name}</label>
        <select
          id={id}
          onChange={(event) => onChange(event.target.value)}
          value={String(value ?? "")}
        >
          <option value="">Не выбрано</option>
          {definition.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </div>
    );
  }
  if (definition.field_type === "multiselect") {
    return (
      <div className="field customer-multiselect">
        <span>{definition.name}</span>
        {definition.options.map((option) => {
          const selected = Array.isArray(value) && value.includes(option);
          return (
            <label key={option}>
              <input
                checked={selected}
                onChange={(event) => {
                  const current = Array.isArray(value)
                    ? value.filter(
                        (item): item is string => typeof item === "string",
                      )
                    : [];
                  onChange(
                    event.target.checked
                      ? [...current, option]
                      : current.filter((item) => item !== option),
                  );
                }}
                type="checkbox"
              />
              {option}
            </label>
          );
        })}
      </div>
    );
  }
  const type =
    definition.field_type === "number"
      ? "number"
      : definition.field_type === "date"
        ? "date"
        : definition.field_type === "datetime"
          ? "datetime-local"
          : "text";
  return (
    <div className="field">
      <label htmlFor={id}>
        {definition.name} {definition.is_required && "*"}
      </label>
      {definition.field_type === "textarea" ? (
        <textarea
          id={id}
          onChange={(event) => onChange(event.target.value)}
          value={String(value ?? "")}
        />
      ) : (
        <input
          id={id}
          onChange={(event) =>
            onChange(
              definition.field_type === "number"
                ? event.target.value
                  ? Number(event.target.value)
                  : null
                : definition.field_type === "datetime" && event.target.value
                  ? new Date(event.target.value).toISOString()
                  : event.target.value,
            )
          }
          type={type}
          value={
            definition.field_type === "datetime" && typeof value === "string"
              ? localDateTime(value)
              : String(value ?? "")
          }
        />
      )}
    </div>
  );
}

export function CustomersView() {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [projectFilter, setProjectFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [languageFilter, setLanguageFilter] = useState("");
  const [archiveFilter, setArchiveFilter] = useState<
    "exclude" | "include" | "only"
  >("exclude");
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<Customer | null>(null);
  const [creating, setCreating] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [success, setSuccess] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [importMode, setImportMode] = useState<"synchronous" | "background">(
    "synchronous",
  );
  const [fieldOpen, setFieldOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importProject, setImportProject] = useState("");
  const [importPreview, setImportPreview] =
    useState<CustomerImportPreview | null>(null);
  const [importReport, setImportReport] = useState<CustomerImportReport | null>(
    null,
  );
  const [importError, setImportError] = useState("");
  const [commitKey, setCommitKey] = useState("");
  const [fieldName, setFieldName] = useState("");
  const [fieldKey, setFieldKey] = useState("");
  const [fieldType, setFieldType] = useState<CustomerFieldType>("text");
  const [fieldRequired, setFieldRequired] = useState(false);
  const [fieldOptions, setFieldOptions] = useState("");
  const limit = 25;

  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
    retry: false,
  });
  const canManage = canManageCustomers(me.data?.user.role);
  const projects = useQuery({
    queryKey: ["projects", "customer-options"],
    queryFn: () => apiRequest<Page<Project>>("/projects?limit=100"),
    enabled: canManage,
  });
  const projectOptions = useQuery({
    queryKey: ["projects", "options"],
    queryFn: () => apiRequest<ProjectOptions>("/projects/options"),
    enabled: canManage,
  });

  const queryString = useMemo(() => {
    const values = new URLSearchParams({
      limit: String(limit),
      offset: String(page * limit),
      archived: archiveFilter,
    });
    if (search.trim()) values.set("search", search.trim());
    if (projectFilter) values.set("project_id", projectFilter);
    if (statusFilter) values.set("status", statusFilter);
    if (languageFilter) values.set("language", languageFilter);
    return values.toString();
  }, [
    archiveFilter,
    languageFilter,
    page,
    projectFilter,
    search,
    statusFilter,
  ]);
  const customers = useQuery({
    queryKey: ["customers", queryString],
    queryFn: () => apiRequest<Page<Customer>>(`/customers?${queryString}`),
    enabled: canManage,
  });

  const form = useForm<CustomerForm>({
    resolver: zodResolver(customerSchema),
    defaultValues: blankCustomerForm(),
  });
  const contacts = useFieldArray({ control: form.control, name: "contacts" });
  const selectedProjectId = form.watch("project_id");
  const activeProjectId =
    editing?.project_id ||
    (creating ? selectedProjectId : projectFilter) ||
    projects.data?.items[0]?.id ||
    "";
  const fieldDefinitions = useQuery({
    queryKey: ["customers", "fields", activeProjectId],
    queryFn: () =>
      apiRequest<CustomerFieldDefinition[]>(
        `/customers/fields?project_id=${activeProjectId}&include_inactive=true`,
      ),
    enabled: canManage && Boolean(activeProjectId),
  });
  const selectedProject = projects.data?.items.find(
    (project) => project.id === selectedProjectId,
  );
  const customValues = form.watch("custom_fields");
  const availableOperators = (projectOptions.data?.operators ?? []).filter(
    (operator) => selectedProject?.operator_user_ids.includes(operator.user_id),
  );

  useEffect(() => {
    if (projectFilter || !projects.data?.items.length) return;
    const project =
      projects.data.items.find((item) => item.is_default) ??
      projects.data.items[0];
    setProjectFilter(project.id);
    setImportProject(project.id);
  }, [projectFilter, projects.data]);

  useEffect(
    () => setPage(0),
    [search, projectFilter, statusFilter, languageFilter, archiveFilter],
  );

  const saveCustomer = useMutation({
    mutationFn: (value: CustomerForm) =>
      apiRequest<Customer>(
        editing ? `/customers/${editing.id}` : "/customers",
        {
          method: editing ? "PATCH" : "POST",
          body: JSON.stringify(customerFormToPayload(value)),
        },
      ),
    onSuccess: async (customer) => {
      setSubmitError("");
      setSuccess(editing ? "Карточка клиента обновлена" : "Клиент создан");
      setEditing(customer);
      setCreating(false);
      form.reset(customerForm(customer));
      await queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
    onError: (error) =>
      setSubmitError(
        error instanceof ApiClientError
          ? error.message
          : "Не удалось сохранить клиента",
      ),
  });

  const archiveCustomer = useMutation({
    mutationFn: ({ id, restore }: { id: string; restore: boolean }) =>
      apiRequest<Customer>(
        `/customers/${id}/${restore ? "restore" : "archive"}`,
        { method: "POST" },
      ),
    onSuccess: async (customer) => {
      setSuccess(
        customer.archived_at ? "Клиент архивирован" : "Клиент восстановлен",
      );
      setEditing(customer);
      form.reset(customerForm(customer));
      await queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
    onError: (error) =>
      setSubmitError(
        error instanceof Error ? error.message : "Операция не выполнена",
      ),
  });

  const createField = useMutation({
    mutationFn: () =>
      apiRequest<CustomerFieldDefinition>("/customers/fields", {
        method: "POST",
        body: JSON.stringify({
          project_id: activeProjectId,
          name: fieldName,
          key: fieldKey,
          field_type: fieldType,
          is_required: fieldRequired,
          options: fieldOptions
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean),
        }),
      }),
    onSuccess: async () => {
      setFieldName("");
      setFieldKey("");
      setFieldOptions("");
      setFieldRequired(false);
      setSuccess("Пользовательское поле создано");
      await queryClient.invalidateQueries({
        queryKey: ["customers", "fields", activeProjectId],
      });
    },
    onError: (error) =>
      setSubmitError(
        error instanceof Error ? error.message : "Не удалось создать поле",
      ),
  });

  const toggleField = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) =>
      apiRequest<CustomerFieldDefinition>(`/customers/fields/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ is_active: active }),
      }),
    onSuccess: async () =>
      queryClient.invalidateQueries({
        queryKey: ["customers", "fields", activeProjectId],
      }),
  });

  const createPreview = useMutation({
    mutationFn: async () => {
      if (!importFile || !importProject)
        throw new Error("Выберите проект и файл");
      const body = new FormData();
      body.set("project_id", importProject);
      body.set("file", importFile);
      return apiRequest<CustomerImportPreview>("/customers/import/preview", {
        method: "POST",
        body,
      });
    },
    onSuccess: (preview) => {
      setImportPreview(preview);
      setImportReport(null);
      setImportError("");
      setCommitKey(idempotencyKey("customer-import"));
    },
    onError: (error) =>
      setImportError(
        error instanceof Error ? error.message : "Не удалось проверить файл",
      ),
  });

  const updatePreview = useMutation({
    mutationFn: ({
      mapping,
      sheet,
      rule,
    }: {
      mapping: Record<string, string>;
      sheet: string;
      rule: "skip" | "update";
    }) =>
      apiRequest<CustomerImportPreview>(
        `/customers/import/${importPreview?.id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            mapping,
            sheet_name: sheet,
            update_rule: rule,
          }),
        },
      ),
    onSuccess: (preview) => {
      setImportPreview(preview);
      setImportError("");
    },
    onError: (error) =>
      setImportError(
        error instanceof Error ? error.message : "Mapping не сохранён",
      ),
  });

  const commitImport = useMutation({
    mutationFn: () =>
      apiRequest<CustomerImportReport>(
        `/customers/import/${importPreview?.id}/commit`,
        {
          method: "POST",
          headers: { "Idempotency-Key": commitKey },
        },
      ),
    onSuccess: async (report) => {
      setImportReport(report);
      setImportError("");
      await queryClient.invalidateQueries({ queryKey: ["customers"] });
    },
    onError: (error) =>
      setImportError(
        error instanceof Error ? error.message : "Импорт не выполнен",
      ),
  });

  function startCreate() {
    const project = projectFilter || projects.data?.items[0]?.id || "";
    setEditing(null);
    setCreating(true);
    setSuccess("");
    setSubmitError("");
    form.reset(blankCustomerForm(project));
  }

  function openCustomer(customer: Customer) {
    setEditing(customer);
    setCreating(false);
    setSuccess("");
    setSubmitError("");
    form.reset(customerForm(customer));
  }

  function closeEditor() {
    setEditing(null);
    setCreating(false);
    setSubmitError("");
  }

  function selectPrimary(index: number, kind: "phone" | "email") {
    form.getValues("contacts").forEach((contact, contactIndex) => {
      if (contact.kind === kind)
        form.setValue(
          `contacts.${contactIndex}.is_primary`,
          contactIndex === index,
        );
    });
  }

  function setCustomField(key: string, value: unknown) {
    form.setValue(
      "custom_fields",
      { ...form.getValues("custom_fields"), [key]: value },
      { shouldDirty: true },
    );
  }

  if (me.isPending) return <SectionSkeleton />;
  if (!canManage) {
    return (
      <section className="panel error-state">
        <div>
          <h1>Нет доступа к клиентской базе</h1>
          <p>Управлять клиентами могут владелец и менеджер компании.</p>
        </div>
      </section>
    );
  }

  const totalPages = Math.max(
    1,
    Math.ceil((customers.data?.total ?? 0) / limit),
  );

  return (
    <>
      <div className="page-heading row-between">
        <div>
          <h1>Клиенты</h1>
          <p>Проектные базы, контакты, ответственные сотрудники и импорт</p>
        </div>
        <div className="customer-heading-actions">
          <Button
            onClick={() => setFieldOpen((value) => !value)}
            variant="secondary"
          >
            <SlidersHorizontal size={16} /> Поля проекта
          </Button>
          <Button
            onClick={() => setImportOpen((value) => !value)}
            variant="secondary"
          >
            <Upload size={16} /> Импорт
          </Button>
          <Button onClick={startCreate}>
            <UserPlus size={16} /> Новый клиент
          </Button>
        </div>
      </div>

      {success && (
        <div className="customer-success" role="status">
          <Check size={17} /> {success}
        </div>
      )}

      {fieldOpen && (
        <section className="panel customer-field-manager">
          <div className="customer-panel-heading">
            <div>
              <h2>Пользовательские поля проекта</h2>
              <p>
                Ключ и тип не меняются после создания — исторические значения
                сохраняются.
              </p>
            </div>
            <button
              aria-label="Закрыть поля"
              className="icon-button"
              onClick={() => setFieldOpen(false)}
              type="button"
            >
              <X size={18} />
            </button>
          </div>
          <div className="customer-field-create">
            <div className="field">
              <label htmlFor="field-name">Название</label>
              <input
                id="field-name"
                onChange={(event) => setFieldName(event.target.value)}
                value={fieldName}
              />
            </div>
            <div className="field">
              <label htmlFor="field-key">Системный ключ</label>
              <input
                id="field-key"
                onChange={(event) =>
                  setFieldKey(
                    event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ""),
                  )
                }
                value={fieldKey}
              />
            </div>
            <div className="field">
              <label htmlFor="field-type">Тип</label>
              <select
                id="field-type"
                onChange={(event) =>
                  setFieldType(event.target.value as CustomerFieldType)
                }
                value={fieldType}
              >
                {[
                  "text",
                  "textarea",
                  "number",
                  "boolean",
                  "date",
                  "datetime",
                  "select",
                  "multiselect",
                ].map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
            </div>
            {(fieldType === "select" || fieldType === "multiselect") && (
              <div className="field">
                <label htmlFor="field-options">Варианты через запятую</label>
                <input
                  id="field-options"
                  onChange={(event) => setFieldOptions(event.target.value)}
                  value={fieldOptions}
                />
              </div>
            )}
            <label className="customer-check-row">
              <input
                checked={fieldRequired}
                onChange={(event) => setFieldRequired(event.target.checked)}
                type="checkbox"
              />{" "}
              Обязательное
            </label>
            <Button
              disabled={
                !fieldName || fieldKey.length < 2 || createField.isPending
              }
              onClick={() => createField.mutate()}
            >
              <Plus size={16} /> Добавить поле
            </Button>
          </div>
          <div className="customer-field-list">
            {fieldDefinitions.data?.map((definition) => (
              <div key={definition.id}>
                <div>
                  <strong>{definition.name}</strong>
                  <small>
                    {definition.key} · {definition.field_type}
                  </small>
                </div>
                <StatusBadge
                  tone={definition.is_active ? "success" : "neutral"}
                >
                  {definition.is_active ? "Активно" : "Архив"}
                </StatusBadge>
                <button
                  className="tv-button tv-button-quiet"
                  onClick={() =>
                    toggleField.mutate({
                      id: definition.id,
                      active: !definition.is_active,
                    })
                  }
                  type="button"
                >
                  {definition.is_active ? "Отключить" : "Включить"}
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      {importOpen && (
        <section className="panel customer-import-panel">
          <div className="customer-panel-heading">
            <div>
              <h2>Импорт CSV/XLSX</h2>
              <p>
                До atomic finalization клиенты в базу не записываются.
                Синхронный режим — до 2 МБ и 500 строк, фоновый — до 25 МБ.
              </p>
            </div>
            <button
              aria-label="Закрыть импорт"
              className="icon-button"
              onClick={() => setImportOpen(false)}
              type="button"
            >
              <X size={18} />
            </button>
          </div>
          <div className="customer-import-mode" role="tablist">
            <button
              aria-selected={importMode === "synchronous"}
              onClick={() => setImportMode("synchronous")}
              role="tab"
              type="button"
            >
              Быстрый импорт
            </button>
            <button
              aria-selected={importMode === "background"}
              onClick={() => setImportMode("background")}
              role="tab"
              type="button"
            >
              Большой фоновый импорт
            </button>
          </div>
          {importMode === "synchronous" ? (
            <>
              <div className="customer-import-upload">
                <div className="field">
                  <label htmlFor="import-project">Проект</label>
                  <select
                    id="import-project"
                    onChange={(event) => setImportProject(event.target.value)}
                    value={importProject}
                  >
                    {projects.data?.items
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
                  <span>{importFile?.name ?? "Выберите CSV или XLSX"}</span>
                  <input
                    accept=".csv,.xlsx"
                    onChange={(event) =>
                      setImportFile(event.target.files?.[0] ?? null)
                    }
                    type="file"
                  />
                </label>
                <Button
                  disabled={!importFile || createPreview.isPending}
                  onClick={() => createPreview.mutate()}
                >
                  <Upload size={16} />{" "}
                  {createPreview.isPending ? "Проверяем…" : "Создать preview"}
                </Button>
              </div>
              {importError && (
                <div className="form-error" role="alert">
                  {importError}
                </div>
              )}
              {importPreview && !importReport && (
                <div className="customer-import-preview">
                  <div className="customer-import-summary">
                    <StatusBadge>{importPreview.total_rows} строк</StatusBadge>
                    <StatusBadge tone="success">
                      {importPreview.valid_rows} готово
                    </StatusBadge>
                    <StatusBadge tone="warning">
                      {importPreview.duplicate_rows} дублей
                    </StatusBadge>
                    <StatusBadge tone="danger">
                      {importPreview.error_rows} ошибок
                    </StatusBadge>
                  </div>
                  {importPreview.sheet_names.length > 1 && (
                    <div className="field">
                      <label htmlFor="import-sheet">Лист XLSX</label>
                      <select
                        id="import-sheet"
                        onChange={(event) =>
                          updatePreview.mutate({
                            mapping: importPreview.mapping,
                            sheet: event.target.value,
                            rule: importPreview.update_rule,
                          })
                        }
                        value={importPreview.selected_sheet}
                      >
                        {importPreview.sheet_names.map((sheet) => (
                          <option key={sheet} value={sheet}>
                            {sheet}
                          </option>
                        ))}
                      </select>
                    </div>
                  )}
                  <div className="customer-mapping-grid">
                    {[
                      ...customerImportFields,
                      ...(fieldDefinitions.data
                        ?.filter((field) => field.is_active)
                        .map(
                          (field) =>
                            [`custom.${field.key}`, field.name] as const,
                        ) ?? []),
                    ].map(([field, label]) => (
                      <div className="field" key={field}>
                        <label htmlFor={`mapping-${field}`}>{label}</label>
                        <select
                          id={`mapping-${field}`}
                          onChange={(event) => {
                            const mapping = { ...importPreview.mapping };
                            if (event.target.value)
                              mapping[field] = event.target.value;
                            else delete mapping[field];
                            updatePreview.mutate({
                              mapping,
                              sheet: importPreview.selected_sheet,
                              rule: importPreview.update_rule,
                            });
                          }}
                          value={importPreview.mapping[field] ?? ""}
                        >
                          <option value="">Не импортировать</option>
                          {importPreview.headers.map((header) => (
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
                        checked={importPreview.update_rule === "skip"}
                        onChange={() =>
                          updatePreview.mutate({
                            mapping: importPreview.mapping,
                            sheet: importPreview.selected_sheet,
                            rule: "skip",
                          })
                        }
                        type="radio"
                      />{" "}
                      Пропускать дубли
                    </label>
                    <label>
                      <input
                        checked={importPreview.update_rule === "update"}
                        onChange={() =>
                          updatePreview.mutate({
                            mapping: importPreview.mapping,
                            sheet: importPreview.selected_sheet,
                            rule: "update",
                          })
                        }
                        type="radio"
                      />{" "}
                      Обновлять найденных клиентов
                    </label>
                  </div>
                  <div className="table-wrap">
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>Строка</th>
                          <th>ФИО</th>
                          <th>Статус проверки</th>
                        </tr>
                      </thead>
                      <tbody>
                        {importPreview.rows.map((row) => (
                          <tr key={row.row_number}>
                            <td>{row.row_number}</td>
                            <td>{String(row.values.display_name ?? "—")}</td>
                            <td>
                              {row.errors.length ? (
                                <span className="table-error">
                                  {row.errors.join("; ")}
                                </span>
                              ) : row.duplicate_fields.length ? (
                                <span className="table-warning">
                                  Дубликат: {row.duplicate_fields.join(", ")}
                                </span>
                              ) : (
                                <span className="table-success">Готово</span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="customer-import-actions">
                    <Button
                      disabled={
                        commitImport.isPending ||
                        updatePreview.isPending ||
                        !commitKey
                      }
                      onClick={() => commitImport.mutate()}
                    >
                      <Download size={16} />{" "}
                      {commitImport.isPending
                        ? "Импортируем…"
                        : "Подтвердить импорт"}
                    </Button>
                  </div>
                </div>
              )}
              {importReport && (
                <div className="customer-import-report">
                  <Check size={24} />
                  <div>
                    <h3>Импорт завершён</h3>
                    <p>
                      Создано: {importReport.created} · Обновлено:{" "}
                      {importReport.updated} · Пропущено: {importReport.skipped}{" "}
                      · Дубли: {importReport.duplicates}
                    </p>
                    {importReport.errors.length > 0 && (
                      <small>
                        Строк с ошибками: {importReport.errors.length}
                      </small>
                    )}
                  </div>
                </div>
              )}
            </>
          ) : (
            <CustomerBackgroundImport
              defaultProjectId={importProject || projectFilter}
              onCustomersChanged={() =>
                queryClient.invalidateQueries({ queryKey: ["customers"] })
              }
              projects={projects.data?.items ?? []}
            />
          )}
        </section>
      )}

      {(creating || editing) && (
        <section className="panel customer-editor">
          <div className="customer-panel-heading">
            <div>
              <h2>
                {editing ? editing.display_name : "Новая карточка клиента"}
              </h2>
              <p>Контакты, профиль, ответственный и проектные поля</p>
            </div>
            <button
              aria-label="Закрыть карточку"
              className="icon-button"
              onClick={closeEditor}
              type="button"
            >
              <X size={18} />
            </button>
          </div>
          <form
            onSubmit={form.handleSubmit((value) => saveCustomer.mutate(value))}
          >
            <div className="customer-form-grid">
              <div className="field">
                <label htmlFor="customer-project">Проект</label>
                <select
                  disabled={Boolean(editing)}
                  id="customer-project"
                  {...form.register("project_id")}
                >
                  <option value="">Выберите проект</option>
                  {projects.data?.items
                    .filter(
                      (project) =>
                        project.status === "active" ||
                        project.id === editing?.project_id,
                    )
                    .map((project) => (
                      <option key={project.id} value={project.id}>
                        {project.name}
                      </option>
                    ))}
                </select>
                {form.formState.errors.project_id && (
                  <small className="field-error">
                    {form.formState.errors.project_id.message}
                  </small>
                )}
              </div>
              <div className="field customer-span-2">
                <label htmlFor="customer-name">ФИО</label>
                <input id="customer-name" {...form.register("display_name")} />
                {form.formState.errors.display_name && (
                  <small className="field-error">
                    {form.formState.errors.display_name.message}
                  </small>
                )}
              </div>
              <div className="field">
                <label htmlFor="customer-status">Статус</label>
                <select id="customer-status" {...form.register("status")}>
                  {Object.entries(statusLabels).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="customer-language">Язык</label>
                <select
                  id="customer-language"
                  {...form.register("preferred_language")}
                >
                  {Object.entries(languageLabels).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="customer-reference">Внешний ID</label>
                <input
                  id="customer-reference"
                  {...form.register("external_reference")}
                />
              </div>
              <div className="field">
                <label htmlFor="customer-source">Источник</label>
                <input id="customer-source" {...form.register("source")} />
              </div>
              <div className="field">
                <label htmlFor="customer-assignee">Ответственный</label>
                <select
                  id="customer-assignee"
                  {...form.register("assigned_user_id")}
                >
                  <option value="">Не назначен</option>
                  {availableOperators.map((operator) => (
                    <option key={operator.user_id} value={operator.user_id}>
                      {operator.display_name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="customer-next-contact">Следующий контакт</label>
                <input
                  id="customer-next-contact"
                  type="datetime-local"
                  {...form.register("next_contact_at")}
                />
              </div>
              <div className="field">
                <label htmlFor="customer-city">Город</label>
                <input id="customer-city" {...form.register("city")} />
              </div>
              <div className="field">
                <label htmlFor="customer-region">Регион</label>
                <input id="customer-region" {...form.register("region")} />
              </div>
              <div className="field customer-span-2">
                <label htmlFor="customer-address">Адрес</label>
                <input id="customer-address" {...form.register("address")} />
              </div>
              <div className="field">
                <label htmlFor="customer-job">Должность</label>
                <input id="customer-job" {...form.register("job_title")} />
              </div>
              <div className="field">
                <label htmlFor="customer-organization">Организация</label>
                <input
                  id="customer-organization"
                  {...form.register("organization")}
                />
              </div>
              <div className="field customer-span-2">
                <label htmlFor="customer-tags">Теги через запятую</label>
                <input id="customer-tags" {...form.register("tags")} />
              </div>
              <div className="field customer-span-2">
                <label htmlFor="customer-description">Описание</label>
                <textarea
                  id="customer-description"
                  {...form.register("description")}
                />
              </div>
            </div>

            <div className="customer-editor-section">
              <div className="customer-section-title">
                <div>
                  <h3>Телефоны и e-mail</h3>
                  <p>Для каждого типа выберите один основной контакт.</p>
                </div>
                <div>
                  <button
                    className="tv-button tv-button-secondary"
                    onClick={() =>
                      contacts.append({
                        kind: "phone",
                        value: "",
                        label: "",
                        is_primary: !form
                          .getValues("contacts")
                          .some((item) => item.kind === "phone"),
                      })
                    }
                    type="button"
                  >
                    <Phone size={15} /> Телефон
                  </button>
                  <button
                    className="tv-button tv-button-secondary"
                    onClick={() =>
                      contacts.append({
                        kind: "email",
                        value: "",
                        label: "",
                        is_primary: !form
                          .getValues("contacts")
                          .some((item) => item.kind === "email"),
                      })
                    }
                    type="button"
                  >
                    <Mail size={15} /> E-mail
                  </button>
                </div>
              </div>
              <div className="customer-contact-list">
                {contacts.fields.map((contact, index) => {
                  const kind = form.watch(`contacts.${index}.kind`);
                  const primary = form.watch(`contacts.${index}.is_primary`);
                  return (
                    <div className="customer-contact-row" key={contact.id}>
                      <span className="customer-contact-icon">
                        {kind === "phone" ? (
                          <Phone size={16} />
                        ) : (
                          <Mail size={16} />
                        )}
                      </span>
                      <input
                        aria-label={`${kind === "phone" ? "Телефон" : "E-mail"} ${index + 1}`}
                        placeholder={
                          kind === "phone"
                            ? "+998901234567"
                            : "client@example.com"
                        }
                        {...form.register(`contacts.${index}.value`)}
                      />
                      <input
                        aria-label={`Метка контакта ${index + 1}`}
                        placeholder="Мобильный / рабочий"
                        {...form.register(`contacts.${index}.label`)}
                      />
                      <label className="customer-primary-control">
                        <input
                          checked={primary}
                          onChange={() => selectPrimary(index, kind)}
                          type="radio"
                        />{" "}
                        Основной
                      </label>
                      <button
                        aria-label={`Удалить контакт ${index + 1}`}
                        className="icon-button"
                        onClick={() => contacts.remove(index)}
                        type="button"
                      >
                        <X size={16} />
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>

            {(fieldDefinitions.data?.filter(
              (definition) => definition.is_active,
            ).length ?? 0) > 0 && (
              <div className="customer-editor-section">
                <div className="customer-section-title">
                  <div>
                    <h3>Поля проекта</h3>
                    <p>Проверяются согласно конфигурации выбранного проекта.</p>
                  </div>
                </div>
                <div className="customer-custom-grid">
                  {fieldDefinitions.data
                    ?.filter((definition) => definition.is_active)
                    .map((definition) => (
                      <CustomFieldControl
                        definition={definition}
                        key={definition.id}
                        onChange={(value) =>
                          setCustomField(definition.key, value)
                        }
                        value={customValues[definition.key]}
                      />
                    ))}
                </div>
              </div>
            )}

            {submitError && (
              <div className="form-error" role="alert">
                {submitError}
              </div>
            )}
            <div className="customer-editor-actions">
              {editing && (
                <Button
                  disabled={archiveCustomer.isPending}
                  onClick={() =>
                    archiveCustomer.mutate({
                      id: editing.id,
                      restore: Boolean(editing.archived_at),
                    })
                  }
                  type="button"
                  variant="secondary"
                >
                  {editing.archived_at ? (
                    <RotateCcw size={16} />
                  ) : (
                    <Archive size={16} />
                  )}
                  {editing.archived_at ? "Восстановить" : "Архивировать"}
                </Button>
              )}
              <Button disabled={saveCustomer.isPending} type="submit">
                {saveCustomer.isPending ? "Сохраняем…" : "Сохранить клиента"}
              </Button>
            </div>
          </form>
        </section>
      )}

      <section className="panel customer-list-panel">
        <div className="customer-filter-bar">
          <label className="search-control">
            <Search aria-hidden="true" size={16} />
            <input
              aria-label="Поиск клиентов"
              onChange={(event) => setSearch(event.target.value)}
              placeholder="ФИО, телефон, e-mail или внешний ID"
              value={search}
            />
          </label>
          <label>
            <Filter size={15} />
            <select
              aria-label="Проект клиентов"
              onChange={(event) => setProjectFilter(event.target.value)}
              value={projectFilter}
            >
              <option value="">Все проекты</option>
              {projects.data?.items.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
          <select
            aria-label="Статус клиентов"
            onChange={(event) => setStatusFilter(event.target.value)}
            value={statusFilter}
          >
            <option value="">Все статусы</option>
            {Object.entries(statusLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="Язык клиентов"
            onChange={(event) => setLanguageFilter(event.target.value)}
            value={languageFilter}
          >
            <option value="">Все языки</option>
            {Object.entries(languageLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="Архив клиентов"
            onChange={(event) =>
              setArchiveFilter(event.target.value as typeof archiveFilter)
            }
            value={archiveFilter}
          >
            <option value="exclude">Активные</option>
            <option value="only">Архивные</option>
            <option value="include">Все</option>
          </select>
          <StatusBadge>{customers.data?.total ?? 0} клиентов</StatusBadge>
        </div>
        {customers.isPending && <SectionSkeleton />}
        {customers.isError && (
          <QueryError
            error={customers.error}
            retry={() => void customers.refetch()}
            title="Не удалось загрузить клиентов"
          />
        )}
        {customers.data && customers.data.items.length === 0 && (
          <div className="empty-state">
            <div>
              <Users size={28} />
              <h3>Клиенты не найдены</h3>
              <p>
                Измените фильтры, импортируйте базу или создайте карточку
                вручную.
              </p>
            </div>
          </div>
        )}
        {customers.data && customers.data.items.length > 0 && (
          <>
            <div className="table-wrap">
              <table className="data-table customer-table">
                <thead>
                  <tr>
                    <th>Клиент</th>
                    <th>Контакты</th>
                    <th>Проект / ответственный</th>
                    <th>Теги</th>
                    <th>Статус</th>
                    <th>Следующий контакт</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {customers.data.items.map((customer) => {
                    const project = projects.data?.items.find(
                      (item) => item.id === customer.project_id,
                    );
                    const assignee = projectOptions.data?.operators.find(
                      (item) => item.user_id === customer.assigned_user_id,
                    );
                    return (
                      <tr
                        className={customer.archived_at ? "is-archived" : ""}
                        key={customer.id}
                      >
                        <td>
                          <strong>
                            {customer.display_name ?? "Без имени"}
                          </strong>
                          <div className="table-secondary">
                            {customer.external_reference ??
                              customer.organization ??
                              customer.id.slice(0, 8)}
                          </div>
                        </td>
                        <td>
                          <div className="customer-table-contact">
                            <span>
                              {primaryContact(customer, "phone") ?? "—"}
                            </span>
                            <small>
                              {primaryContact(customer, "email") ??
                                "Нет e-mail"}
                            </small>
                          </div>
                        </td>
                        <td>
                          <span>{project?.name ?? "Проект"}</span>
                          <div className="table-secondary">
                            {assignee?.display_name ?? "Не назначен"}
                          </div>
                        </td>
                        <td>
                          <div className="customer-tag-list">
                            {customer.tags.slice(0, 2).map((tag) => (
                              <span key={tag}>
                                <Tag size={11} />
                                {tag}
                              </span>
                            ))}
                            {customer.tags.length > 2 && (
                              <small>+{customer.tags.length - 2}</small>
                            )}
                          </div>
                        </td>
                        <td>
                          <StatusBadge tone={statusTone(customer)}>
                            {customer.archived_at
                              ? "Архив"
                              : (statusLabels[customer.status] ??
                                customer.status)}
                          </StatusBadge>
                        </td>
                        <td>
                          {customer.next_contact_at
                            ? new Date(customer.next_contact_at).toLocaleString(
                                "ru-RU",
                              )
                            : "—"}
                        </td>
                        <td>
                          <button
                            aria-label={`Открыть клиента ${customer.display_name ?? ""}`}
                            className="icon-button"
                            onClick={() => openCustomer(customer)}
                            type="button"
                          >
                            <Pencil size={16} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="customer-pagination">
              <span>
                Страница {page + 1} из {totalPages}
              </span>
              <div>
                <button
                  aria-label="Предыдущая страница"
                  className="icon-button"
                  disabled={page === 0}
                  onClick={() => setPage((value) => Math.max(0, value - 1))}
                  type="button"
                >
                  <ChevronLeft size={18} />
                </button>
                <button
                  aria-label="Следующая страница"
                  className="icon-button"
                  disabled={page + 1 >= totalPages}
                  onClick={() => setPage((value) => value + 1)}
                  type="button"
                >
                  <ChevronRight size={18} />
                </button>
              </div>
            </div>
          </>
        )}
      </section>
    </>
  );
}
