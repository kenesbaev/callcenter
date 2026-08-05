"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArchiveRestore,
  Database,
  HardDrive,
  Settings,
  ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { Button, StatusBadge } from "@teamora/ui";
import {
  BackgroundOperationsView,
  type OperationsSection,
} from "@/components/background-operations-view";
import { QueryError, SectionSkeleton } from "@/components/query-state";
import { ApiClientError, apiRequest } from "@/lib/api";
import type { AuthResponse, Role, TenantSettings } from "@/lib/types";

type SettingsValues = Pick<
  TenantSettings,
  | "timezone"
  | "default_language"
  | "recording_enabled"
  | "recording_disclosure_required"
  | "retention_days"
  | "max_concurrent_calls"
>;

type SettingsTab = "general" | OperationsSection;

const tabs: Array<{
  id: SettingsTab;
  label: string;
  icon: typeof Settings;
  managerOnly?: boolean;
}> = [
  { id: "general", label: "Основные", icon: Settings, managerOnly: true },
  { id: "jobs", label: "Background Jobs", icon: Database },
  { id: "storage", label: "Storage", icon: HardDrive },
  {
    id: "retention",
    label: "Retention",
    icon: ArchiveRestore,
    managerOnly: true,
  },
  {
    id: "legal-holds",
    label: "Legal Holds",
    icon: ShieldCheck,
    managerOnly: true,
  },
];

function isManager(role: Role): boolean {
  return role === "tenant_owner" || role === "tenant_manager";
}

export function SettingsView() {
  const [tab, setTab] = useState<SettingsTab>("general");
  const auth = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
  });
  const role = auth.data?.user.role;
  const availableTabs = role
    ? tabs.filter((item) => !item.managerOnly || isManager(role))
    : [];
  const activeTab = availableTabs.some((item) => item.id === tab)
    ? tab
    : (availableTabs[0]?.id ?? "jobs");

  if (auth.isPending) return <SectionSkeleton />;
  if (auth.isError)
    return (
      <QueryError
        error={auth.error}
        retry={() => void auth.refetch()}
        title="Не удалось определить права доступа"
      />
    );
  if (!role) return null;

  return (
    <>
      <div className="page-heading">
        <h1>Настройки и операции</h1>
        <p>
          Конфигурация компании, durable background queue, MinIO и безопасный
          retention
        </p>
      </div>
      <div className="team-tabs settings-tabs" role="tablist">
        {availableTabs.map((item) => {
          const Icon = item.icon;
          return (
            <button
              aria-selected={activeTab === item.id}
              key={item.id}
              onClick={() => setTab(item.id)}
              role="tab"
              type="button"
            >
              <Icon size={16} /> {item.label}
            </button>
          );
        })}
      </div>
      {activeTab === "general" ? (
        <GeneralSettingsForm canManage={role === "tenant_owner"} />
      ) : (
        <BackgroundOperationsView role={role} section={activeTab} />
      )}
    </>
  );
}

function GeneralSettingsForm({ canManage }: { canManage: boolean }) {
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
    <form
      className="panel settings-form"
      onSubmit={form.handleSubmit((values) => update.mutate(values))}
    >
      <div className="settings-section">
        <div>
          <h2>Основные</h2>
          {!canManage && (
            <p className="panel-subtitle">
              Только владелец может изменять эти настройки.
            </p>
          )}
        </div>
        <div className="settings-fields">
          <div className="field">
            <label htmlFor="settings-timezone">Часовой пояс</label>
            <select
              disabled={!canManage}
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
              disabled={!canManage}
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
          <p className="panel-subtitle">
            Детальные сроки и двухэтапный purge находятся во вкладке Retention.
          </p>
        </div>
        <div className="settings-fields">
          <div className="field">
            <label htmlFor="settings-retention">
              Legacy срок записей, дней
            </label>
            <input
              disabled={!canManage}
              id="settings-retention"
              max="3650"
              min="1"
              type="number"
              {...form.register("retention_days", {
                valueAsNumber: true,
                required: true,
              })}
            />
          </div>
          <div className="field">
            <label htmlFor="settings-concurrency">Одновременные звонки</label>
            <input
              disabled={!canManage}
              id="settings-concurrency"
              max="100"
              min="1"
              type="number"
              {...form.register("max_concurrent_calls", {
                valueAsNumber: true,
                required: true,
              })}
            />
          </div>
          <label className="toggle-row">
            <input
              disabled={!canManage}
              type="checkbox"
              {...form.register("recording_enabled")}
            />
            <span>
              <strong>Запись разговоров</strong>
              <small>Live retention требует отдельной SIP-проверки.</small>
            </span>
          </label>
          <label className="toggle-row">
            <input
              disabled={!canManage}
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
        {canManage && (
          <Button disabled={update.isPending} type="submit">
            {update.isPending ? "Сохраняем…" : "Сохранить"}
          </Button>
        )}
        {saved && <StatusBadge tone="success">Сохранено</StatusBadge>}
        <StatusBadge tone="warning">KAA Experimental выключен</StatusBadge>
      </div>
    </form>
  );
}
