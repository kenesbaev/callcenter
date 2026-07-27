"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CheckCircle2 } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Button, StatusBadge } from "@teamora/ui";
import { ApiClientError, apiRequest } from "@/lib/api";
import { operatorSchema, type OperatorValues } from "@/lib/schemas";
import type { AiOperator, Page } from "@/lib/types";

export function OperatorsView() {
  const client = useQueryClient();
  const [error, setError] = useState("");
  const operators = useQuery({
    queryKey: ["operators"],
    queryFn: () => apiRequest<Page<AiOperator>>("/ai-operators"),
  });
  const form = useForm<OperatorValues>({
    resolver: zodResolver(operatorSchema),
    defaultValues: {
      name: "",
      description: "",
      system_instructions:
        "Отвечай только по базе знаний компании. Если ответа нет, запроси перевод на живого оператора.",
    },
  });
  const create = useMutation({
    mutationFn: (values: OperatorValues) =>
      apiRequest<AiOperator>("/ai-operators", {
        method: "POST",
        body: JSON.stringify({
          ...values,
          allowed_languages: ["ru"],
          greeting_by_language: {
            ru: "Здравствуйте! Я виртуальный помощник. Разговор может записываться.",
          },
          allowed_tools: [
            "search_knowledge",
            "request_human_operator",
            "end_call",
          ],
        }),
      }),
    onSuccess: async () => {
      form.reset();
      setError("");
      await client.invalidateQueries({ queryKey: ["operators"] });
    },
    onError: (caught) =>
      setError(
        caught instanceof ApiClientError
          ? caught.message
          : "Не удалось создать оператора",
      ),
  });
  const publish = useMutation({
    mutationFn: (id: string) =>
      apiRequest<AiOperator>(`/ai-operators/${id}/publish`, { method: "POST" }),
    onSuccess: async () => {
      setError("");
      await client.invalidateQueries({ queryKey: ["operators"] });
    },
    onError: (caught) =>
      setError(
        caught instanceof ApiClientError
          ? caught.message
          : "Не удалось опубликовать оператора",
      ),
  });

  return (
    <>
      <div className="page-heading">
        <h1>AI-операторы</h1>
      </div>
      <div className="content-grid">
        <section className="panel">
          <div className="row-between">
            <div>
              <h2>Операторы</h2>
            </div>
            <StatusBadge tone="primary">С версиями</StatusBadge>
          </div>
          {operators.isPending && (
            <div className="operator-list">
              <div className="operator-row skeleton">Загрузка</div>
              <div className="operator-row skeleton">Загрузка</div>
            </div>
          )}
          {operators.isError && (
            <div className="error-state">
              <div>
                <h3>Не удалось загрузить операторов</h3>
                <p>{operators.error.message}</p>
              </div>
            </div>
          )}
          {operators.data?.items.length === 0 && (
            <div className="empty-state">
              <div>
                <Bot aria-hidden="true" size={28} />
                <h3>Операторов пока нет</h3>
                <p>Создайте первого оператора.</p>
              </div>
            </div>
          )}
          <div className="operator-list">
            {operators.data?.items.map((operator) => (
              <article className="operator-row" key={operator.id}>
                <div>
                  <div className="inline-badges">
                    <strong>{operator.name}</strong>
                    <StatusBadge
                      tone={
                        operator.version.status === "published"
                          ? "success"
                          : "warning"
                      }
                    >
                      {operator.version.status}
                    </StatusBadge>
                    <StatusBadge>
                      {operator.version.allowed_languages
                        .join(", ")
                        .toUpperCase()}
                    </StatusBadge>
                  </div>
                  <p>{operator.description || "Без описания"}</p>
                  <p>
                    Инструменты: {operator.version.allowed_tools.join(", ")}
                  </p>
                </div>
                <div className="operator-row-actions">
                  {operator.version.status === "draft" ? (
                    <Button
                      disabled={publish.isPending}
                      onClick={() => publish.mutate(operator.id)}
                      variant="secondary"
                    >
                      <CheckCircle2 size={15} /> Опубликовать
                    </Button>
                  ) : (
                    <StatusBadge tone="success">Готов к симулятору</StatusBadge>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
        <section className="form-card">
          <h2>Новый оператор</h2>
          <p className="panel-subtitle">Русский язык · база знаний · перевод</p>
          <form
            className="form-stack"
            onSubmit={form.handleSubmit((values) => create.mutate(values))}
            noValidate
          >
            <div className="field">
              <label htmlFor="operator-name">Имя</label>
              <input
                id="operator-name"
                placeholder="Поддержка клиентов"
                {...form.register("name")}
              />
              {form.formState.errors.name && (
                <span className="field-error">
                  {form.formState.errors.name.message}
                </span>
              )}
            </div>
            <div className="field">
              <label htmlFor="operator-description">Роль</label>
              <input
                id="operator-description"
                placeholder="Отвечает на вопросы клиентов"
                {...form.register("description")}
              />
            </div>
            <div className="field">
              <label htmlFor="operator-instructions">Ограничения</label>
              <textarea
                id="operator-instructions"
                rows={7}
                {...form.register("system_instructions")}
              />
              {form.formState.errors.system_instructions && (
                <span className="field-error">
                  {form.formState.errors.system_instructions.message}
                </span>
              )}
            </div>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            <Button disabled={create.isPending} type="submit">
              {create.isPending ? "Создаём…" : "Создать черновик"}
            </Button>
          </form>
        </section>
      </div>
    </>
  );
}
