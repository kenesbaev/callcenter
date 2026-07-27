"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Button, StatusBadge } from "@teamora/ui";
import { ApiClientError, apiRequest } from "@/lib/api";
import { knowledgeSchema, type KnowledgeValues } from "@/lib/schemas";
import type { KnowledgeDocument, Page } from "@/lib/types";

export function KnowledgeView() {
  const client = useQueryClient();
  const [error, setError] = useState("");
  const documents = useQuery({
    queryKey: ["knowledge"],
    queryFn: () => apiRequest<Page<KnowledgeDocument>>("/knowledge"),
  });
  const form = useForm<KnowledgeValues>({
    resolver: zodResolver(knowledgeSchema),
    defaultValues: { title: "", language: "ru", content: "" },
  });
  const create = useMutation({
    mutationFn: (values: KnowledgeValues) =>
      apiRequest<KnowledgeDocument>("/knowledge/text", {
        method: "POST",
        body: JSON.stringify(values),
      }),
    onSuccess: async () => {
      form.reset({ title: "", language: "ru", content: "" });
      setError("");
      await client.invalidateQueries({ queryKey: ["knowledge"] });
    },
    onError: (caught) =>
      setError(
        caught instanceof ApiClientError
          ? caught.message
          : "Не удалось сохранить документ",
      ),
  });
  return (
    <>
      <div className="page-heading">
        <h1>База знаний</h1>
      </div>
      <div className="content-grid">
        <section className="panel">
          <div className="row-between">
            <div>
              <h2>Документы</h2>
            </div>
            <StatusBadge>{documents.data?.total ?? 0}</StatusBadge>
          </div>
          {documents.isPending && (
            <div className="knowledge-list">
              <div className="knowledge-row skeleton">Загрузка</div>
            </div>
          )}
          {documents.isError && (
            <div className="error-state">
              <div>
                <h3>Не удалось загрузить базу знаний</h3>
                <p>{documents.error.message}</p>
              </div>
            </div>
          )}
          {documents.data?.items.length === 0 && (
            <div className="empty-state">
              <div>
                <BookOpen aria-hidden="true" size={28} />
                <h3>Документов пока нет</h3>
                <p>Добавьте проверенный текст для ответов AI.</p>
              </div>
            </div>
          )}
          <div className="knowledge-list">
            {documents.data?.items.map((document) => (
              <article className="knowledge-row" key={document.id}>
                <div>
                  <div className="inline-badges">
                    <strong>{document.title}</strong>
                    <StatusBadge>{document.language.toUpperCase()}</StatusBadge>
                  </div>
                  <p>
                    {document.content.length > 220
                      ? `${document.content.slice(0, 220)}…`
                      : document.content}
                  </p>
                </div>
              </article>
            ))}
          </div>
        </section>
        <section className="form-card">
          <h2>Добавить текст</h2>
          <p className="panel-subtitle">Файлы и синхронизация — в разработке</p>
          <form
            className="form-stack"
            onSubmit={form.handleSubmit((values) => create.mutate(values))}
            noValidate
          >
            <div className="field">
              <label htmlFor="knowledge-title">Название</label>
              <input
                id="knowledge-title"
                placeholder="График работы"
                {...form.register("title")}
              />
              {form.formState.errors.title && (
                <span className="field-error">
                  {form.formState.errors.title.message}
                </span>
              )}
            </div>
            <div className="field">
              <label htmlFor="knowledge-language">Язык</label>
              <select id="knowledge-language" {...form.register("language")}>
                <option value="ru">Русский</option>
                <option value="en">Английский</option>
                <option value="uz">Узбекский — beta</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="knowledge-content">Проверенная информация</label>
              <textarea
                id="knowledge-content"
                placeholder="Поддержка работает с понедельника по пятницу…"
                rows={9}
                {...form.register("content")}
              />
              {form.formState.errors.content && (
                <span className="field-error">
                  {form.formState.errors.content.message}
                </span>
              )}
            </div>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            <Button disabled={create.isPending} type="submit">
              {create.isPending ? "Сохраняем…" : "Сохранить"}
            </Button>
          </form>
        </section>
      </div>
    </>
  );
}
