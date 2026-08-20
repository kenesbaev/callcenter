"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Bot, PhoneCall, PlugZap, RadioTower } from "lucide-react";
import { Button, StatusBadge } from "@teamora/ui";
import { apiRequest } from "@/lib/api";
import type {
  AuthResponse,
  AIRealtimeDiagnostic,
  AIRealtimeStatus,
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

const technicalStatusLabel: Record<string, string> = {
  configured_live_verification_required: "Настроено — нужна live-проверка",
  configuration_ready_live_verification_required:
    "Конфигурация готова — нужна live-проверка",
  live_openai_verification_required: "Требуется live-проверка OpenAI",
  live_not_requested: "Live-проверка не запрашивалась",
  not_applicable: "Не требуется",
  not_configured: "Не настроено",
  not_verified: "Не проверено",
  tunnel_configured_live_verification_required:
    "Туннель настроен — нужна live-проверка",
};

function formatTechnicalStatus(value: string | null | undefined) {
  if (!value) return "—";
  return technicalStatusLabel[value] ?? value.replaceAll("_", " ");
}

export function IntegrationsView() {
  const queryClient = useQueryClient();
  const [projectId, setProjectId] = useState("");
  const [liveNumber, setLiveNumber] = useState("");
  const [telephonyNotice, setTelephonyNotice] = useState("");
  const [aiNotice, setAiNotice] = useState("");
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => apiRequest<AuthResponse>("/auth/me"),
  });
  const integrations = useQuery({
    queryKey: ["integrations"],
    queryFn: () => apiRequest<Integration[]>("/integrations"),
  });
  const realtimeAI = useQuery({
    queryKey: ["ai-realtime", "status"],
    queryFn: () => apiRequest<AIRealtimeStatus>("/ai-realtime/status"),
    enabled:
      me.data?.user.role === "tenant_owner" ||
      me.data?.user.role === "tenant_manager",
    refetchInterval: 30_000,
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
      setTelephonyNotice(
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
      setTelephonyNotice(`Диагностический вызов: ${result.status}.`);
      void queryClient.invalidateQueries({ queryKey: ["telephony"] });
    },
  });
  const localAiTest = useMutation({
    mutationFn: () =>
      apiRequest<AIRealtimeDiagnostic>("/ai-realtime/diagnostics/local", {
        method: "POST",
      }),
    onSuccess: (result) => {
      setAiNotice(
        result.status === "local_mock_passed"
          ? `Mock Realtime: VAD, transcript и usage подтверждены; ${result.outputBytes} байт audio.`
          : `Mock Realtime завершён со статусом ${result.status}.`,
      );
      void queryClient.invalidateQueries({ queryKey: ["ai-realtime"] });
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
          <article className="panel integration-card ai-realtime-card">
            <div className="integration-mark">
              <Bot aria-hidden="true" size={20} />
            </div>
            <div className="row-between">
              <h2>OpenAI Realtime Voice AI</h2>
              <StatusBadge
                tone={
                  realtimeAI.data?.enabled && realtimeAI.data.configured
                    ? "success"
                    : "warning"
                }
              >
                {realtimeAI.data?.enabled
                  ? realtimeAI.data.configured
                    ? "Настроен"
                    : "Нет API key"
                  : "Отключён"}
              </StatusBadge>
            </div>
            {realtimeAI.isError ? (
              <p role="alert">
                Не удалось получить безопасный статус Voice AI.
              </p>
            ) : (
              <div className="telephony-status-grid">
                <span>
                  Provider<strong>{realtimeAI.data?.provider ?? "—"}</strong>
                </span>
                <span>
                  Model<strong>{realtimeAI.data?.model ?? "—"}</strong>
                </span>
                <span>
                  Voice<strong>{realtimeAI.data?.voice ?? "—"}</strong>
                </span>
                <span>
                  Сессии<strong>{realtimeAI.data?.active_sessions ?? 0}</strong>
                </span>
                <span>
                  Последний статус
                  <strong>{realtimeAI.data?.last_session_state ?? "—"}</strong>
                </span>
                <span>
                  Live verification
                  <strong>
                    {formatTechnicalStatus(realtimeAI.data?.live_verification)}
                  </strong>
                </span>
                <span>
                  TTFA p50
                  <strong>
                    {realtimeAI.data?.latency.p50_ms != null
                      ? `${Math.round(realtimeAI.data.latency.p50_ms)} мс`
                      : "Недоступно"}
                  </strong>
                </span>
                <span>
                  TTFA p95
                  <strong>
                    {realtimeAI.data?.latency.p95_ms != null
                      ? `${Math.round(realtimeAI.data.latency.p95_ms)} мс`
                      : "Недоступно"}
                  </strong>
                </span>
                <span>
                  TTFA p99
                  <strong>
                    {realtimeAI.data?.latency.p99_ms != null
                      ? `${Math.round(realtimeAI.data.latency.p99_ms)} мс`
                      : "Недоступно"}
                  </strong>
                </span>
              </div>
            )}
            <p>
              Ключ хранится только на сервере и здесь не отображается. Локальный
              Mock Realtime подтверждает контракт, но не качество или
              доступность live-модели.
            </p>
            {me.data?.user.role === "tenant_owner" && (
              <div className="telephony-diagnostic-controls">
                <Button
                  disabled={localAiTest.isPending}
                  onClick={() => localAiTest.mutate()}
                  type="button"
                >
                  {localAiTest.isPending
                    ? "Проверяем Mock Realtime…"
                    : "Локальный Voice AI test"}
                </Button>
                {localAiTest.error && (
                  <div className="form-error" role="alert">
                    {localAiTest.error.message}
                  </div>
                )}
                {aiNotice && (
                  <div className="compact-note" role="status">
                    {aiNotice}
                  </div>
                )}
              </div>
            )}
            {realtimeAI.data?.last_safe_error && (
              <div className="form-warning" role="status">
                Последняя безопасная ошибка: {realtimeAI.data.last_safe_error}
              </div>
            )}
          </article>
        )}
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
                {telephony.data?.status
                  ? formatTechnicalStatus(telephony.data.status)
                  : telephony.isPending
                    ? "Проверка…"
                    : "Недоступно"}
              </StatusBadge>
            </div>
            {telephony.isError ? (
              <p role="alert">
                Не удалось получить безопасный статус телефонии.
              </p>
            ) : (
              <div className="telephony-status-grid">
                <span>
                  Архитектура
                  <strong>{telephony.data?.deployment_mode ?? "—"}</strong>
                </span>
                <span>
                  Media Gateway
                  <strong>
                    {telephony.data?.media_gateway_placement ?? "—"}
                  </strong>
                </span>
                <span>
                  Edge
                  <strong>
                    {formatTechnicalStatus(telephony.data?.edge_connectivity)}
                  </strong>
                </span>
                <span>
                  Browser WebRTC
                  <strong>
                    {formatTechnicalStatus(telephony.data?.browser_webrtc)}
                  </strong>
                </span>
                <span>
                  ARI
                  <strong>{formatTechnicalStatus(telephony.data?.ari)}</strong>
                </span>
                <span>
                  Trunk
                  <strong>
                    {formatTechnicalStatus(telephony.data?.sip_trunk)}
                  </strong>
                </span>
                <span>
                  Регистрация
                  <strong>
                    {formatTechnicalStatus(telephony.data?.registration)}
                  </strong>
                </span>
                <span>
                  RTP
                  <strong>
                    {formatTechnicalStatus(telephony.data?.external_media)}
                  </strong>
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
                {telephonyNotice && (
                  <div className="compact-note" role="status">
                    {telephonyNotice}
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
