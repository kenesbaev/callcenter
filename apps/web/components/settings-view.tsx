"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { Button, StatusBadge } from "@teamora/ui";
import { ApiClientError, apiRequest } from "@/lib/api";
import type { TenantSettings } from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";

type SettingsValues = Pick<
  TenantSettings,
  | "timezone"
  | "default_language"
  | "recording_enabled"
  | "recording_disclosure_required"
  | "retention_days"
  | "max_concurrent_calls"
>;

export function SettingsView() {
  const client = useQueryClient();
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const settings = useQuery({
    queryKey: ["tenant-settings"],
    queryFn: () => apiRequest<TenantSettings>("/settings"),
  });
  const form = useForm<SettingsValues>();
  useEffect(() => {
    if (settings.data) form.reset(settings.data);
  }, [form, settings.data]);

  const update = useMutation({
    mutationFn: (values: SettingsValues) =>
      apiRequest<TenantSettings>("/settings", {
        method: "PATCH",
        body: JSON.stringify({
          ...values,
          retention_days: Number(values.retention_days),
          max_concurrent_calls: Number(values.max_concurrent_calls),
        }),
      }),
    onSuccess: async (data) => {
      form.reset(data);
      setSaved(true);
      setError("");
      await client.invalidateQueries({ queryKey: ["tenant-settings"] });
    },
    onError: (caught) => {
      setSaved(false);
      setError(
        caught instanceof ApiClientError
          ? caught.message
          : "Не удалось сохранить настройки",
      );
    },
  });

  if (settings.isPending) return <SectionSkeleton />;
  if (settings.isError)
    return (
      <QueryError
        error={settings.error}
        retry={() => void settings.refetch()}
        title="Не удалось загрузить настройки"
      />
    );

  return (
    <>
      <div className="page-heading row-between">
        <h1>Настройки</h1>
        {saved && <StatusBadge tone="success">Сохранено</StatusBadge>}
      </div>
      <form
        className="panel settings-form"
        onSubmit={form.handleSubmit((values) => update.mutate(values))}
      >
        <div className="settings-section">
          <div>
            <h2>Основные</h2>
          </div>
          <div className="settings-fields">
            <div className="field">
              <label htmlFor="settings-timezone">Часовой пояс</label>
              <select
                id="settings-timezone"
                {...form.register("timezone", { required: true })}
              >
                <option value="Asia/Tashkent">Asia/Tashkent</option>
                <option value="Europe/Moscow">Europe/Moscow</option>
                <option value="UTC">UTC</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="settings-language">Язык по умолчанию</label>
              <select
                id="settings-language"
                {...form.register("default_language")}
              >
                <option value="ru">Русский</option>
                <option value="en">English</option>
                <option value="uz">O‘zbekcha · Beta</option>
              </select>
            </div>
          </div>
        </div>
        <div className="settings-section">
          <div>
            <h2>Звонки и хранение</h2>
          </div>
          <div className="settings-fields">
            <div className="field">
              <label htmlFor="settings-retention">Хранение записей, дней</label>
              <input
                id="settings-retention"
                type="number"
                min="1"
                max="3650"
                {...form.register("retention_days", {
                  valueAsNumber: true,
                  required: true,
                })}
              />
            </div>
            <div className="field">
              <label htmlFor="settings-concurrency">Одновременные звонки</label>
              <input
                id="settings-concurrency"
                type="number"
                min="1"
                max="100"
                {...form.register("max_concurrent_calls", {
                  valueAsNumber: true,
                  required: true,
                })}
              />
            </div>
            <label className="toggle-row">
              <input type="checkbox" {...form.register("recording_enabled")} />
              <span>
                <strong>Запись разговоров</strong>
                <small>Для реальных звонков после подключения storage.</small>
              </span>
            </label>
            <label className="toggle-row">
              <input
                type="checkbox"
                {...form.register("recording_disclosure_required")}
              />
              <span>
                <strong>Уведомлять клиента</strong>
                <small>Обязательно, если запись включена.</small>
              </span>
            </label>
          </div>
        </div>
        {error && (
          <div className="form-error" role="alert">
            {error}
          </div>
        )}
        <div className="settings-actions">
          <Button disabled={update.isPending} type="submit">
            {update.isPending ? "Сохраняем…" : "Сохранить"}
          </Button>
          <StatusBadge tone="warning">KAA Experimental выключен</StatusBadge>
        </div>
      </form>
    </>
  );
}
