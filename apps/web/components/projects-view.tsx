"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  Bot,
  CalendarClock,
  ChevronRight,
  Clock3,
  FolderKanban,
  Gauge,
  Languages,
  Phone,
  Plus,
  Radio,
  Save,
  Settings2,
  Users,
  Workflow,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, StatusBadge } from "@teamora/ui";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { ApiClientError, apiRequest } from "@/lib/api";
import type {
  AuthResponse,
  Page,
  Project,
  ProjectOptions,
  Role,
} from "@/lib/types";

const daySchema = z.object({
  key: z.enum([
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
  ]),
  enabled: z.boolean(),
  start: z.string(),
  end: z.string(),
});

const projectSchema = z
  .object({
    name: z.string().trim().min(2, "Введите название проекта").max(160),
    description: z.string().trim().max(1000),
    status: z.enum(["active", "paused", "archived"]),
    default_language: z.enum(["", "ru", "uz", "en", "kaa"]),
    timezone: z.string().trim().max(64),
    outbound_phone_number_id: z.string(),
    inbound_phone_number_ids: z.array(z.string()),
    ai_operator_id: z.string(),
    knowledge_source_id: z.string(),
    call_flow_id: z.string(),
    operator_user_ids: z.array(z.string()),
    max_concurrent_calls: z
      .string()
      .regex(/^$|^[1-9][0-9]{0,2}$/, "Укажите число от 1 до 1000"),
    recording_enabled: z.enum(["inherit", "enabled", "disabled"]),
    recording_disclosure_required: z.enum(["inherit", "enabled", "disabled"]),
    max_attempts: z.number().int().min(1).max(20),
    retry_intervals: z.string(),
    callback_default_delay_minutes: z.number().int().min(1).max(43_200),
    callback_max_schedule_days: z.number().int().min(1).max(365),
    callback_allow_operator_scheduling: z.boolean(),
    callback_require_assignee: z.boolean(),
    callback_overdue_first: z.boolean(),
    working_days: z.array(daySchema).length(7),
  })
  .superRefine((value, context) => {
    for (const [index, day] of value.working_days.entries()) {
      if (
        day.enabled &&
        (!/^\d{2}:\d{2}$/.test(day.start) || !/^\d{2}:\d{2}$/.test(day.end))
      ) {
        context.addIssue({
          code: "custom",
          path: ["working_days", index, "start"],
          message: "Укажите начало и окончание рабочего дня",
        });
      } else if (day.enabled && day.start >= day.end) {
        context.addIssue({
          code: "custom",
          path: ["working_days", index, "end"],
          message: "Окончание должно быть позже начала",
        });
      }
    }
    const intervals = parseRetryIntervals(value.retry_intervals);
    if (
      intervals.some(
        (interval) =>
          !Number.isInteger(interval) || interval < 1 || interval > 10_080,
      ) ||
      intervals.length !== value.max_attempts - 1
    ) {
      context.addIssue({
        code: "custom",
        path: ["retry_intervals"],
        message: `Нужно указать ${value.max_attempts - 1} интервал(а) от 1 до 10080 минут`,
      });
    }
  });

export type ProjectForm = z.infer<typeof projectSchema>;

type EditorSection =
  "main" | "telephony" | "ai" | "operators" | "schedule" | "rules";

const dayOptions: Array<{
  key: ProjectForm["working_days"][number]["key"];
  label: string;
}> = [
  { key: "monday", label: "Понедельник" },
  { key: "tuesday", label: "Вторник" },
  { key: "wednesday", label: "Среда" },
  { key: "thursday", label: "Четверг" },
  { key: "friday", label: "Пятница" },
  { key: "saturday", label: "Суббота" },
  { key: "sunday", label: "Воскресенье" },
];

const sectionOptions: Array<{
  id: EditorSection;
  label: string;
  icon: typeof Settings2;
}> = [
  { id: "main", label: "Основное", icon: Settings2 },
  { id: "telephony", label: "Телефония", icon: Phone },
  { id: "ai", label: "AI и знания", icon: Bot },
  { id: "operators", label: "Операторы", icon: Users },
  { id: "schedule", label: "Рабочее время", icon: Clock3 },
  { id: "rules", label: "Лимиты и правила", icon: Gauge },
];

const statusLabels: Record<Project["status"], string> = {
  active: "Активен",
  paused: "Приостановлен",
  archived: "Архив",
};

const languageLabels = {
  ru: "Русский",
  uz: "Узбекский",
  en: "Английский",
  kaa: "Каракалпакский",
};

export function canManageProjects(role: Role | undefined): boolean {
  return role === "tenant_owner" || role === "tenant_manager";
}

function canReadProjects(role: Role | undefined): boolean {
  return (
    role === "tenant_owner" ||
    role === "tenant_manager" ||
    role === "human_operator" ||
    role === "analyst"
  );
}

function parseRetryIntervals(value: string): number[] {
  if (!value.trim()) return [];
  return value
    .split(",")
    .map((entry) => Number(entry.trim()))
    .filter((entry) => !Number.isNaN(entry));
}

function nullableBoolean(
  value: ProjectForm["recording_enabled"],
): boolean | null {
  if (value === "inherit") return null;
  return value === "enabled";
}

function booleanMode(value: boolean | null): ProjectForm["recording_enabled"] {
  if (value === null) return "inherit";
  return value ? "enabled" : "disabled";
}

export function projectFormToPayload(value: ProjectForm) {
  return {
    name: value.name.trim(),
    description: value.description.trim(),
    status: value.status,
    default_language: value.default_language || null,
    timezone: value.timezone.trim() || null,
    outbound_phone_number_id: value.outbound_phone_number_id || null,
    inbound_phone_number_ids: value.inbound_phone_number_ids,
    ai_operator_id: value.ai_operator_id || null,
    knowledge_source_id: value.knowledge_source_id || null,
    call_flow_id: value.call_flow_id || null,
    operator_user_ids: value.operator_user_ids,
    max_concurrent_calls: value.max_concurrent_calls
      ? Number(value.max_concurrent_calls)
      : null,
    recording_enabled: nullableBoolean(value.recording_enabled),
    recording_disclosure_required: nullableBoolean(
      value.recording_disclosure_required,
    ),
    max_attempts: value.max_attempts,
    retry_intervals_minutes: parseRetryIntervals(value.retry_intervals),
    callback_rules: {
      default_delay_minutes: value.callback_default_delay_minutes,
      max_schedule_days: value.callback_max_schedule_days,
      allow_operator_scheduling: value.callback_allow_operator_scheduling,
      require_assignee: value.callback_require_assignee,
      overdue_first: value.callback_overdue_first,
    },
    working_hours: Object.fromEntries(
      value.working_days.map((day) => [
        day.key,
        day.enabled
          ? { enabled: true, start: day.start, end: day.end }
          : { enabled: false },
      ]),
    ),
  };
}

function blankProjectForm(): ProjectForm {
  return {
    name: "",
    description: "",
    status: "active",
    default_language: "",
    timezone: "",
    outbound_phone_number_id: "",
    inbound_phone_number_ids: [],
    ai_operator_id: "",
    knowledge_source_id: "",
    call_flow_id: "",
    operator_user_ids: [],
    max_concurrent_calls: "",
    recording_enabled: "inherit",
    recording_disclosure_required: "inherit",
    max_attempts: 3,
    retry_intervals: "15, 60",
    callback_default_delay_minutes: 60,
    callback_max_schedule_days: 30,
    callback_allow_operator_scheduling: true,
    callback_require_assignee: false,
    callback_overdue_first: true,
    working_days: dayOptions.map((day, index) => ({
      key: day.key,
      enabled: index < 5,
      start: "09:00",
      end: "18:00",
    })),
  };
}

function projectForm(project: Project): ProjectForm {
  return {
    name: project.name,
    description: project.description,
    status: project.status,
    default_language: project.default_language ?? "",
    timezone: project.timezone ?? "",
    outbound_phone_number_id: project.outbound_phone_number_id ?? "",
    inbound_phone_number_ids: project.inbound_phone_number_ids,
    ai_operator_id: project.ai_operator_id ?? "",
    knowledge_source_id: project.knowledge_source_id ?? "",
    call_flow_id: project.call_flow_id ?? "",
    operator_user_ids: project.operator_user_ids,
    max_concurrent_calls: project.max_concurrent_calls?.toString() ?? "",
    recording_enabled: booleanMode(project.recording_enabled),
    recording_disclosure_required: booleanMode(
      project.recording_disclosure_required,
    ),
    max_attempts: project.max_attempts,
    retry_intervals: project.retry_intervals_minutes.join(", "),
    callback_default_delay_minutes:
      project.callback_rules.default_delay_minutes,
    callback_max_schedule_days: project.callback_rules.max_schedule_days,
    callback_allow_operator_scheduling:
      project.callback_rules.allow_operator_scheduling,
    callback_require_assignee: project.callback_rules.require_assignee,
    callback_overdue_first: project.callback_rules.overdue_first,
    working_days: dayOptions.map((day) => {
      const configured = project.working_hours[day.key];
      return {
        key: day.key,
        enabled: configured?.enabled ?? false,
        start: configured?.start ?? "09:00",
        end: configured?.end ?? "18:00",
      };
    }),
  };
}

function ProjectSummary({ project }: { project: Project }) {
  return (
    <section
      className="panel project-summary"
      aria-label="Информация о проекте"
    >
      <div className="project-summary-heading">
        <div>
          <span className="eyebrow">Доступный проект</span>
          <h2>{project.name}</h2>
          <p className="panel-subtitle">
            {project.description || "Описание проекта не заполнено"}
          </p>
        </div>
        <StatusBadge
          tone={
            project.status === "active"
              ? "success"
              : project.status === "paused"
                ? "warning"
                : "neutral"
          }
        >
          {statusLabels[project.status]}
        </StatusBadge>
      </div>
      <div className="project-summary-grid">
        <div>
          <Languages size={17} />
          <span>Язык</span>
          <strong>
            {languageLabels[project.effective_settings.default_language]}
          </strong>
        </div>
        <div>
          <Clock3 size={17} />
          <span>Часовой пояс</span>
          <strong>{project.effective_settings.timezone}</strong>
        </div>
        <div>
          <Gauge size={17} />
          <span>Параллельные звонки</span>
          <strong>{project.effective_settings.max_concurrent_calls}</strong>
        </div>
        <div>
          <Radio size={17} />
          <span>Запись</span>
          <strong>
            {project.effective_settings.recording_enabled
              ? "Включена"
              : "Выключена"}
          </strong>
        </div>
      </div>
      <p className="project-readonly-note">
        Изменять конфигурацию проекта могут владелец и менеджер компании.
      </p>
    </section>
  );
}

export function ProjectsView() {
  const queryClient = useQueryClient();
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(
    null,
  );
  const [creating, setCreating] = useState(false);
  const [section, setSection] = useState<EditorSection>("main");
  const [submitError, setSubmitError] = useState("");
  const [saved, setSaved] = useState(false);
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
    retry: false,
  });
  const role = me.data?.user.role;
  const canManage = canManageProjects(role);
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiRequest<Page<Project>>("/projects?limit=100"),
    enabled: canReadProjects(role),
  });
  const options = useQuery({
    queryKey: ["projects", "options"],
    queryFn: () => apiRequest<ProjectOptions>("/projects/options"),
    enabled: canManage,
  });
  const selectedProject = useMemo(
    () =>
      projects.data?.items.find((item) => item.id === selectedProjectId) ??
      null,
    [projects.data?.items, selectedProjectId],
  );
  const form = useForm<ProjectForm>({
    resolver: zodResolver(projectSchema),
    defaultValues: blankProjectForm(),
  });

  useEffect(() => {
    if (creating) form.reset(blankProjectForm());
    else if (selectedProject) form.reset(projectForm(selectedProject));
  }, [creating, form, selectedProject]);

  const saveProject = useMutation({
    mutationFn: async (value: ProjectForm) => {
      const payload = projectFormToPayload(value);
      return creating
        ? apiRequest<Project>("/projects", {
            method: "POST",
            body: JSON.stringify(payload),
          })
        : apiRequest<Project>(`/projects/${selectedProjectId}`, {
            method: "PATCH",
            body: JSON.stringify(payload),
          });
    },
    onSuccess: async (project) => {
      setSubmitError("");
      setSaved(true);
      setCreating(false);
      setSelectedProjectId(project.id);
      form.reset(projectForm(project));
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error) => {
      setSaved(false);
      setSubmitError(
        error instanceof ApiClientError
          ? error.message
          : "Не удалось сохранить проект",
      );
    },
  });
  const archiveProject = useMutation({
    mutationFn: (projectId: string) =>
      apiRequest<Project>(`/projects/${projectId}/archive`, { method: "POST" }),
    onSuccess: async (project) => {
      setSubmitError("");
      setSaved(true);
      form.reset(projectForm(project));
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error) =>
      setSubmitError(
        error instanceof ApiClientError
          ? error.message
          : "Не удалось архивировать проект",
      ),
  });

  function openProject(project: Project) {
    setCreating(false);
    setSelectedProjectId(project.id);
    setSection("main");
    setSaved(false);
    setSubmitError("");
  }

  function startCreate() {
    setCreating(true);
    setSelectedProjectId(null);
    setSection("main");
    setSaved(false);
    setSubmitError("");
  }

  if (me.isPending) return <SectionSkeleton />;
  if (!canReadProjects(role)) {
    return (
      <section className="panel error-state">
        <div>
          <h1>Нет доступа к проектам</h1>
          <p>Эта страница недоступна для вашей роли.</p>
        </div>
      </section>
    );
  }

  return (
    <>
      <div className="page-heading row-between">
        <div>
          <h1>Проекты</h1>
          <p>Кампании, телефония, команда и правила работы операторов</p>
        </div>
        {canManage && (
          <Button onClick={startCreate}>
            <Plus size={16} />
            Новый проект
          </Button>
        )}
      </div>

      {projects.isPending && <SectionSkeleton />}
      {projects.isError && (
        <QueryError
          error={projects.error}
          retry={() => void projects.refetch()}
          title="Не удалось загрузить проекты"
        />
      )}
      {projects.data?.items.length === 0 && !creating && (
        <section className="panel empty-state">
          <div>
            <FolderKanban size={28} />
            <h3>Проектов пока нет</h3>
            <p>Создайте проект перед загрузкой клиентской базы.</p>
            {canManage && <Button onClick={startCreate}>Создать проект</Button>}
          </div>
        </section>
      )}

      {projects.data && (projects.data.items.length > 0 || creating) && (
        <div className="project-workspace">
          <section className="panel project-list-panel">
            <div className="table-toolbar">
              <div>
                <h2>Проекты компании</h2>
                <p className="panel-subtitle">{projects.data.total} всего</p>
              </div>
            </div>
            <div className="project-list">
              {projects.data.items.map((project) => (
                <button
                  className={`project-list-row${selectedProjectId === project.id ? " active" : ""}`}
                  key={project.id}
                  onClick={() => openProject(project)}
                  type="button"
                >
                  <span className="project-list-icon">
                    <FolderKanban size={17} />
                  </span>
                  <span className="project-list-copy">
                    <strong>{project.name}</strong>
                    <small>
                      {project.is_default
                        ? "Основной проект"
                        : `${project.operator_user_ids.length} операторов`}
                    </small>
                  </span>
                  <StatusBadge
                    tone={
                      project.status === "active"
                        ? "success"
                        : project.status === "paused"
                          ? "warning"
                          : "neutral"
                    }
                  >
                    {statusLabels[project.status]}
                  </StatusBadge>
                  <ChevronRight size={16} />
                </button>
              ))}
            </div>
          </section>

          <div className="project-editor-column">
            {!creating && !selectedProject && (
              <section className="panel project-editor-empty">
                <FolderKanban size={32} />
                <h2>Откройте проект</h2>
                <p>Выберите проект слева, чтобы увидеть его конфигурацию.</p>
              </section>
            )}
            {!creating && selectedProject && !canManage && (
              <ProjectSummary project={selectedProject} />
            )}
            {(creating || (selectedProject && canManage)) && (
              <form
                className="panel project-editor"
                onSubmit={form.handleSubmit((value) =>
                  saveProject.mutate(value),
                )}
              >
                <div className="project-editor-heading">
                  <div>
                    <span className="eyebrow">
                      {creating ? "Новая кампания" : "Конфигурация проекта"}
                    </span>
                    <h2>
                      {creating ? "Создание проекта" : selectedProject?.name}
                    </h2>
                    {!creating && selectedProject?.is_default && (
                      <StatusBadge tone="primary">Основной</StatusBadge>
                    )}
                  </div>
                  <div className="project-editor-actions">
                    {saved && (
                      <StatusBadge tone="success">Сохранено</StatusBadge>
                    )}
                    {!creating && selectedProject && (
                      <Link
                        className="tv-button tv-button-secondary"
                        href={`/app/projects/${selectedProject.id}/flow`}
                      >
                        <Workflow size={16} />
                        Сценарий
                      </Link>
                    )}
                    {!creating &&
                      selectedProject &&
                      !selectedProject.is_default &&
                      selectedProject.status !== "archived" && (
                        <Button
                          disabled={
                            archiveProject.isPending || saveProject.isPending
                          }
                          onClick={() =>
                            archiveProject.mutate(selectedProject.id)
                          }
                          type="button"
                          variant="quiet"
                        >
                          <Archive size={16} />В архив
                        </Button>
                      )}
                    <Button disabled={saveProject.isPending} type="submit">
                      <Save size={16} />
                      {saveProject.isPending ? "Сохраняем…" : "Сохранить"}
                    </Button>
                  </div>
                </div>

                <div className="project-tabs" role="tablist">
                  {sectionOptions.map((item) => {
                    const Icon = item.icon;
                    return (
                      <button
                        aria-selected={section === item.id}
                        className={section === item.id ? "active" : ""}
                        key={item.id}
                        onClick={() => setSection(item.id)}
                        role="tab"
                        type="button"
                      >
                        <Icon size={15} />
                        {item.label}
                      </button>
                    );
                  })}
                </div>

                {options.isPending && <SectionSkeleton />}
                {options.isError && (
                  <QueryError
                    error={options.error}
                    retry={() => void options.refetch()}
                    title="Не удалось загрузить доступные настройки"
                  />
                )}
                {options.data && (
                  <ProjectEditorFields
                    creating={creating}
                    form={form}
                    options={options.data}
                    project={selectedProject}
                    section={section}
                  />
                )}
                {submitError && (
                  <div className="form-error" role="alert">
                    {submitError}
                  </div>
                )}
              </form>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function ProjectEditorFields({
  creating,
  form,
  options,
  project,
  section,
}: {
  creating: boolean;
  form: ReturnType<typeof useForm<ProjectForm>>;
  options: ProjectOptions;
  project: Project | null;
  section: EditorSection;
}) {
  const projectAiOperators = options.ai_operators.filter(
    (operator) => operator.project_id === project?.id,
  );
  const projectFlows = options.call_flows.filter(
    (flow) => flow.project_id === project?.id,
  );

  if (section === "main") {
    return (
      <div className="project-form-grid">
        <FormField label="Название" error={form.formState.errors.name?.message}>
          <input {...form.register("name")} />
        </FormField>
        <FormField label="Статус">
          <select {...form.register("status")} disabled={project?.is_default}>
            <option value="active">Активен</option>
            <option value="paused">Приостановлен</option>
            {!project?.is_default && <option value="archived">Архив</option>}
          </select>
        </FormField>
        <FormField label="Описание" wide>
          <textarea rows={4} {...form.register("description")} />
        </FormField>
        <FormField label="Язык по умолчанию">
          <select {...form.register("default_language")}>
            <option value="">
              Наследовать:{" "}
              {languageLabels[options.tenant_defaults.default_language]}
            </option>
            <option value="ru">Русский</option>
            <option value="uz">Узбекский</option>
            <option value="en">Английский</option>
            <option value="kaa">Каракалпакский</option>
          </select>
        </FormField>
        <FormField label="Часовой пояс">
          <input
            placeholder={`Наследовать: ${options.tenant_defaults.timezone}`}
            {...form.register("timezone")}
          />
        </FormField>
        {!creating && project && (
          <div className="project-extension-card project-form-wide">
            <Settings2 size={18} />
            <div>
              <strong>Каталог результатов подготовлен</strong>
              <p>
                Связь проекта: <code>{project.call_result_catalog_id}</code>.
                Состав результатов будет настроен на Этапе 5.
              </p>
            </div>
          </div>
        )}
      </div>
    );
  }

  if (section === "telephony") {
    return (
      <div className="project-form-grid">
        <FormField label="Исходящий номер">
          <select {...form.register("outbound_phone_number_id")}>
            <option value="">Не назначен / Mock</option>
            {options.phone_numbers.map((number) => (
              <option key={number.id} value={number.id}>
                {number.label} · {number.e164}
              </option>
            ))}
          </select>
        </FormField>
        <div className="project-form-wide">
          <span className="project-field-label">Входящие DID-номера</span>
          <div className="project-option-grid">
            {options.phone_numbers.length === 0 && (
              <p className="project-option-empty">
                У компании пока нет настроенных номеров.
              </p>
            )}
            {options.phone_numbers.map((number) => (
              <label className="project-check-card" key={number.id}>
                <input
                  type="checkbox"
                  value={number.id}
                  {...form.register("inbound_phone_number_ids")}
                />
                <span>
                  <strong>{number.label}</strong>
                  <small>{number.e164}</small>
                </span>
              </label>
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (section === "ai") {
    return (
      <div className="project-form-grid">
        <FormField label="AI-оператор">
          <select {...form.register("ai_operator_id")} disabled={creating}>
            <option value="">Не назначен</option>
            {projectAiOperators.map((operator) => (
              <option key={operator.id} value={operator.id}>
                {operator.name}
              </option>
            ))}
          </select>
          {creating && (
            <small>
              AI-оператор назначается после первого сохранения проекта.
            </small>
          )}
        </FormField>
        <FormField label="Проектная база знаний">
          <select {...form.register("knowledge_source_id")}>
            <option value="">Не назначена</option>
            {options.knowledge_sources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.name}
              </option>
            ))}
          </select>
        </FormField>
        <FormField label="Сценарий разговора">
          <select {...form.register("call_flow_id")} disabled={creating}>
            <option value="">Не назначен</option>
            {projectFlows.map((flow) => (
              <option key={flow.id} value={flow.id}>
                {flow.name}
              </option>
            ))}
          </select>
          <small>
            {creating
              ? "Сценарий назначается после первого сохранения проекта."
              : "Редактор открывается кнопкой «Сценарий» в заголовке проекта."}
          </small>
        </FormField>
      </div>
    );
  }

  if (section === "operators") {
    return (
      <div>
        <span className="project-field-label">Живые операторы проекта</span>
        <div className="project-option-grid">
          {options.operators.length === 0 && (
            <p className="project-option-empty">Нет доступных сотрудников.</p>
          )}
          {options.operators.map((operator) => (
            <label className="project-check-card" key={operator.user_id}>
              <input
                type="checkbox"
                value={operator.user_id}
                {...form.register("operator_user_ids")}
              />
              <span>
                <strong>{operator.display_name}</strong>
                <small>{operator.email}</small>
              </span>
              <StatusBadge>{operator.role.replace("tenant_", "")}</StatusBadge>
            </label>
          ))}
        </div>
      </div>
    );
  }

  if (section === "schedule") {
    return (
      <div className="project-schedule">
        <div className="project-schedule-head">
          <CalendarClock size={18} />
          <div>
            <strong>Рабочие дни и часы</strong>
            <p>
              Новые звонки должны запускаться только внутри этих интервалов.
            </p>
          </div>
        </div>
        {dayOptions.map((day, index) => {
          const enabled = form.watch(`working_days.${index}.enabled`);
          return (
            <div className="project-day-row" key={day.key}>
              <label className="project-day-toggle">
                <input
                  type="checkbox"
                  {...form.register(`working_days.${index}.enabled`)}
                />
                <span>{day.label}</span>
              </label>
              <input
                aria-label={`${day.label}: начало`}
                disabled={!enabled}
                type="time"
                {...form.register(`working_days.${index}.start`)}
              />
              <span>—</span>
              <input
                aria-label={`${day.label}: окончание`}
                disabled={!enabled}
                type="time"
                {...form.register(`working_days.${index}.end`)}
              />
              {(form.formState.errors.working_days?.[index]?.start ||
                form.formState.errors.working_days?.[index]?.end) && (
                <small className="field-error">
                  {form.formState.errors.working_days[index]?.start?.message ??
                    form.formState.errors.working_days[index]?.end?.message}
                </small>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  return (
    <div className="project-form-grid">
      <FormField
        label="Одновременных звонков"
        error={form.formState.errors.max_concurrent_calls?.message}
      >
        <input
          inputMode="numeric"
          placeholder={`Наследовать: ${options.tenant_defaults.max_concurrent_calls}`}
          {...form.register("max_concurrent_calls")}
        />
      </FormField>
      <FormField label="Максимум попыток">
        <input
          min={1}
          max={20}
          type="number"
          {...form.register("max_attempts", { valueAsNumber: true })}
        />
      </FormField>
      <FormField
        label="Интервалы повторных попыток, минуты"
        error={form.formState.errors.retry_intervals?.message}
        wide
      >
        <input placeholder="15, 60" {...form.register("retry_intervals")} />
      </FormField>
      <FormField label="Запись разговоров">
        <select {...form.register("recording_enabled")}>
          <option value="inherit">
            Наследовать:{" "}
            {options.tenant_defaults.recording_enabled
              ? "включена"
              : "выключена"}
          </option>
          <option value="enabled">Включена</option>
          <option value="disabled">Выключена</option>
        </select>
      </FormField>
      <FormField label="Уведомление о записи">
        <select {...form.register("recording_disclosure_required")}>
          <option value="inherit">
            Наследовать:{" "}
            {options.tenant_defaults.recording_disclosure_required
              ? "обязательно"
              : "не обязательно"}
          </option>
          <option value="enabled">Обязательно</option>
          <option value="disabled">Не обязательно</option>
        </select>
      </FormField>
      <FormField label="Перезвон по умолчанию, минуты">
        <input
          min={1}
          type="number"
          {...form.register("callback_default_delay_minutes", {
            valueAsNumber: true,
          })}
        />
      </FormField>
      <FormField label="Горизонт перезвона, дней">
        <input
          min={1}
          max={365}
          type="number"
          {...form.register("callback_max_schedule_days", {
            valueAsNumber: true,
          })}
        />
      </FormField>
      <div className="project-form-wide project-rule-checks">
        <label>
          <input
            type="checkbox"
            {...form.register("callback_allow_operator_scheduling")}
          />
          Оператор может назначить перезвон
        </label>
        <label>
          <input
            type="checkbox"
            {...form.register("callback_require_assignee")}
          />
          Ответственный обязателен
        </label>
        <label>
          <input type="checkbox" {...form.register("callback_overdue_first")} />
          Просроченные перезвоны выдаются первыми
        </label>
      </div>
    </div>
  );
}

function FormField({
  children,
  error,
  label,
  wide = false,
}: {
  children: React.ReactNode;
  error?: string;
  label: string;
  wide?: boolean;
}) {
  return (
    <label className={`field${wide ? " project-form-wide" : ""}`}>
      <span>{label}</span>
      {children}
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}
