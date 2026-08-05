"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { PhoneCall, PlugZap, RadioTower } from "lucide-react";
import { Button, StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type {
  AuthResponse,
  Integration,
  Page,
  Project,
  TelephonyDiagnostic,
  TelephonyStatus,
} from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";

const statusLabel = {
  unavailable: "Недоступно",
  development: "Разработка",
  configured: "Настроено",
  verified: "Проверено",
};

export function IntegrationsView() {
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState("");
  const [liveNumber, setLiveNumber] = useState("");
  const [notice, setNotice] = useState("");
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
  });
  const integrations = useQuery({
    queryKey: ["integrations"],
    queryFn: () => apiRequest<Integration[]>("/integrations"),
  });
  const telephony = useQuery({
    queryKey: ["telephony", "status"],
    queryFn: () => apiRequest<TelephonyStatus>("/telephony/status"),
    enabled:
      me.data?.user.role === "tenant_owner" ||
      me.data?.user.role === "tenant_manager",
    refetchInterval: 30_000,
  });
  const projects = useQuery({
    queryKey: ["projects", "telephony-options"],
    queryFn: () => apiRequest<Page<Project>>("/projects?limit=100"),
    enabled: me.data?.user.role === "tenant_owner",
  });
  useEffect(() => {
    if (!projectId && projects.data?.items[0])
      setProjectId(projects.data.items[0].id);
  }, [projectId, projects.data?.items]);
  const localTest = useMutation({
    mutationFn: () =>
      apiRequest<TelephonyDiagnostic>("/telephony/diagnostics/local", {
        method: "POST",
        body: JSON.stringify({
          project_id: projectId,
          codec: "ulaw",
          packets: 50,
        }),
      }),
    onSuccess: (result) => {
      setNotice(
        result.status === "local_test_passed"
          ? "Локальный RTP-тест подтверждён в обоих направлениях."
          : `Локальный тест завершён: ${result.safe_error_code ?? result.status}`,
      );
      void queryClient.invalidateQueries({ queryKey: ["telephony"] });
    },
  });
  const liveTest = useMutation({
    mutationFn: () =>
      apiRequest<TelephonyDiagnostic>("/telephony/diagnostics/live", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          project_id: projectId,
          destination: liveNumber,
          confirmation: "CALL_ALLOWED_TEST_NUMBER",
        }),
      }),
    onSuccess: (result) => {
      setNotice(`Диагностический вызов: ${result.status}.`);
      void queryClient.invalidateQueries({ queryKey: ["telephony"] });
    },
  });
  if (integrations.isPending) return <SectionSkeleton />;
  if (integrations.isError)
    return (
      <QueryError
        error={integrations.error}
        retry={() => void integrations.refetch()}
        title="Не удалось загрузить интеграции"
      />
    );
  return (
    <>
      <div className="page-heading">
        <h1>Интеграции</h1>
      </div>
      <div className="integration-grid">
        {(me.data?.user.role === "tenant_owner" ||
          me.data?.user.role === "tenant_manager") && (
          <article className="panel integration-card telephony-integration-card">
            <div className="integration-mark">
              <RadioTower aria-hidden="true" size={20} />
            </div>
            <div className="row-between">
              <h2>Телефония SIP / Asterisk</h2>
              <StatusBadge
                tone={
                  telephony.data?.live_audio_verified ? "success" : "warning"
                }
              >
                {telephony.data?.status ??
                  (telephony.isPending ? "Проверка…" : "Недоступно")}
              </StatusBadge>
            </div>
            {telephony.isError ? (
              <p role="alert">
                Не удалось получить безопасный статус телефонии.
              </p>
            ) : (
              <div className="telephony-status-grid">
                <span>
                  ARI<strong>{telephony.data?.ari ?? "—"}</strong>
                </span>
                <span>
                  Trunk<strong>{telephony.data?.sip_trunk ?? "—"}</strong>
                </span>
                <span>
                  Регистрация
                  <strong>{telephony.data?.registration ?? "—"}</strong>
                </span>
                <span>
                  RTP<strong>{telephony.data?.external_media ?? "—"}</strong>
                </span>
                <span>
                  Кодеки
                  <strong>
                    {telephony.data?.codecs.join(", ") || "не заданы"}
                  </strong>
                </span>
                <span>
                  Каналы
                  <strong>
                    {telephony.data?.channel_usage
                      .map((item) => `${item.occupied}/${item.limit}`)
                      .join(", ") || "—"}
                  </strong>
                </span>
              </div>
            )}
            <p>
              Live audio считается проверенным только после подтверждённого RTP
              в обоих направлениях. Пароли и полные номера здесь не
              отображаются.
            </p>
            {me.data?.user.role === "tenant_owner" && (
              <div className="telephony-diagnostic-controls">
                <select
                  aria-label="Проект для теста телефонии"
                  onChange={(event) => setProjectId(event.target.value)}
                  value={projectId}
                >
                  {(projects.data?.items ?? []).map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name}
                    </option>
                  ))}
                </select>
                <Button
                  disabled={!projectId || localTest.isPending}
                  onClick={() => localTest.mutate()}
                  type="button"
                >
                  {localTest.isPending
                    ? "Проверяем RTP…"
                    : "Локальный media test"}
                </Button>
                <label>
                  Разрешённый тестовый номер
                  <input
                    onChange={(event) => setLiveNumber(event.target.value)}
                    placeholder="+998…"
                    type="tel"
                    value={liveNumber}
                  />
                </label>
                <Button
                  disabled={
                    !projectId ||
                    !/^\+[1-9][0-9]{7,14}$/.test(liveNumber) ||
                    liveTest.isPending
                  }
                  onClick={() => {
                    if (
                      window.confirm(
                        "Выполнить один разрешённый внешний диагностический вызов?",
                      )
                    ) {
                      liveTest.mutate();
                    }
                  }}
                  type="button"
                >
                  <PhoneCall aria-hidden="true" size={16} />
                  Controlled live test
                </Button>
                {(localTest.error || liveTest.error) && (
                  <div className="form-error" role="alert">
                    {(localTest.error ?? liveTest.error)?.message}
                  </div>
                )}
                {notice && (
                  <div className="compact-note" role="status">
                    {notice}
                  </div>
                )}
              </div>
            )}
          </article>
        )}
        {integrations.data.map((integration) => (
          <article
            className="panel integration-card"
            key={integration.provider}
          >
            <div className="integration-mark">
              <PlugZap aria-hidden="true" size={20} />
            </div>
            <div className="row-between">
              <h2>{integration.display_name}</h2>
              <StatusBadge
                tone={integration.status === "verified" ? "success" : "warning"}
              >
                {statusLabel[integration.status]}
              </StatusBadge>
            </div>
            <div className="capability-list">
              {integration.capabilities.map((capability) => (
                <span key={capability}>{capability}</span>
              ))}
            </div>
            <p>{integration.note}</p>
          </article>
        ))}
      </div>
    </>
  );
}
