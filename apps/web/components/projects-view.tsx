"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FolderKanban, Plus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, StatusBadge } from "@teamora/ui";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { ApiClientError, apiRequest } from "@/lib/api";
import type { Page, Project } from "@/lib/types";

const projectSchema = z.object({
  name: z.string().trim().min(2, "Введите название проекта").max(160),
  description: z.string().trim().max(1000),
  outbound_number: z.union([
    z.literal(""),
    z
      .string()
      .regex(/^\+[1-9][0-9]{7,14}$/, "Используйте формат +998901234567"),
  ]),
  max_concurrent_calls: z.number().int().min(1).max(1000),
});

type ProjectForm = z.infer<typeof projectSchema>;

const statusLabels: Record<Project["status"], string> = {
  active: "Активен",
  paused: "Приостановлен",
  archived: "Архив",
};

export function ProjectsView() {
  const queryClient = useQueryClient();
  const [formOpen, setFormOpen] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const form = useForm<ProjectForm>({
    resolver: zodResolver(projectSchema),
    defaultValues: {
      name: "",
      description: "",
      outbound_number: "",
      max_concurrent_calls: 1,
    },
  });
  const projects = useQuery({
    queryKey: ["projects"],
    queryFn: () => apiRequest<Page<Project>>("/projects?limit=100"),
  });
  const createProject = useMutation({
    mutationFn: (value: ProjectForm) =>
      apiRequest<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          ...value,
          outbound_number: value.outbound_number || null,
          working_hours: {},
          operator_user_ids: [],
        }),
      }),
    onSuccess: async () => {
      setSubmitError("");
      setFormOpen(false);
      form.reset();
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (error) =>
      setSubmitError(
        error instanceof ApiClientError
          ? error.message
          : "Не удалось создать проект",
      ),
  });

  return (
    <>
      <div className="page-heading row-between">
        <div>
          <h1>Проекты</h1>
          <p>Кампании, очереди клиентов и лимиты одновременных звонков</p>
        </div>
        <Button onClick={() => setFormOpen((value) => !value)}>
          <Plus size={16} />
          Новый проект
        </Button>
      </div>
      {formOpen && (
        <section className="panel crm-create-panel">
          <div>
            <h2>Настройка проекта</h2>
            <p className="panel-subtitle">
              Операторы назначаются через экран команды или API проекта.
            </p>
          </div>
          <form
            className="crm-inline-form"
            onSubmit={form.handleSubmit((value) => createProject.mutate(value))}
          >
            <div className="field">
              <label htmlFor="project-name">Название</label>
              <input id="project-name" {...form.register("name")} />
              {form.formState.errors.name && (
                <small className="field-error">
                  {form.formState.errors.name.message}
                </small>
              )}
            </div>
            <div className="field">
              <label htmlFor="project-description">Описание</label>
              <input
                id="project-description"
                {...form.register("description")}
              />
            </div>
            <div className="field">
              <label htmlFor="project-number">Исходящий номер</label>
              <input
                id="project-number"
                placeholder="+998901234567"
                {...form.register("outbound_number")}
              />
            </div>
            <div className="field">
              <label htmlFor="project-limit">Одновременных звонков</label>
              <input
                id="project-limit"
                min={1}
                type="number"
                {...form.register("max_concurrent_calls", {
                  valueAsNumber: true,
                })}
              />
            </div>
            <div className="crm-form-actions">
              {submitError && <span className="form-error">{submitError}</span>}
              <Button disabled={createProject.isPending} type="submit">
                {createProject.isPending ? "Сохраняем…" : "Создать проект"}
              </Button>
            </div>
          </form>
        </section>
      )}
      <section className="panel">
        <div className="table-toolbar">
          <div>
            <h2>Проекты компании</h2>
            <p className="panel-subtitle">
              Каждый проект имеет собственную клиентскую очередь.
            </p>
          </div>
          <StatusBadge>{projects.data?.total ?? 0} проектов</StatusBadge>
        </div>
        {projects.isPending && <SectionSkeleton />}
        {projects.isError && (
          <QueryError
            error={projects.error}
            retry={() => void projects.refetch()}
            title="Не удалось загрузить проекты"
          />
        )}
        {projects.data?.items.length === 0 && (
          <div className="empty-state">
            <div>
              <FolderKanban size={28} />
              <h3>Проектов пока нет</h3>
              <p>Создайте проект перед загрузкой клиентской базы.</p>
            </div>
          </div>
        )}
        {projects.data && projects.data.items.length > 0 && (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Проект</th>
                  <th>Статус</th>
                  <th>Исходящий номер</th>
                  <th>Лимит звонков</th>
                  <th>Операторы</th>
                </tr>
              </thead>
              <tbody>
                {projects.data.items.map((project) => (
                  <tr key={project.id}>
                    <td>
                      <strong>{project.name}</strong>
                      <div className="table-secondary">
                        {project.description ||
                          (project.is_default
                            ? "Основной проект"
                            : "Без описания")}
                      </div>
                    </td>
                    <td>
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
                    </td>
                    <td>{project.outbound_number ?? "Mock"}</td>
                    <td>{project.max_concurrent_calls}</td>
                    <td>{project.operator_user_ids.length}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
