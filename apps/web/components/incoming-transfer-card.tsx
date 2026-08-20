"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Headphones, PhoneCall, PhoneOff, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { useRealtime } from "@/components/realtime-provider";
import { ApiClientError, apiRequest } from "@/lib/api";
import {
  getOperatorSoftphone,
  type SoftphoneState,
} from "@/lib/operator-softphone";
import type {
  LiveTransfer,
  OperatorTransferEndpoint,
  OperatorWebRtcConfiguration,
} from "@/lib/types";

function errorMessage(error: unknown) {
  return error instanceof ApiClientError
    ? error.message
    : "Не удалось обработать предложение перевода.";
}

export function IncomingTransferCard() {
  const queryClient = useQueryClient();
  const realtime = useRealtime();
  const [message, setMessage] = useState("");
  const [now, setNow] = useState(Date.now());
  const [softphoneState, setSoftphoneState] = useState<SoftphoneState>("idle");
  const [muted, setMuted] = useState(false);
  const [endpointType, setEndpointType] = useState<
    "browser" | "sip" | "mobile"
  >("browser");
  const [microphoneId, setMicrophoneId] = useState("");
  const [speakerId, setSpeakerId] = useState("");
  const expiredOffer = useRef<string | null>(null);
  const transfers = useQuery({
    queryKey: ["transfers", "incoming"],
    queryFn: () => apiRequest<LiveTransfer[]>("/transfers"),
    refetchInterval: realtime.connected ? 60_000 : 15_000,
  });
  const transfer = transfers.data?.find((item) =>
    ["offered", "claimed", "connecting", "connected"].includes(item.status),
  );
  const claimed = transfer?.status === "claimed";
  const endpoints = useQuery({
    queryKey: ["transfers", "operator-endpoints"],
    queryFn: () =>
      apiRequest<OperatorTransferEndpoint[]>("/transfers/operator/endpoints"),
    enabled: claimed,
  });
  const audioDevices = useQuery({
    queryKey: ["transfers", "audio-devices"],
    queryFn: () => getOperatorSoftphone().listAudioDevices(),
    enabled: claimed && endpointType === "browser",
    retry: false,
  });
  useEffect(
    () =>
      getOperatorSoftphone().onState((state, error) => {
        setSoftphoneState(state);
        if (error) setMessage(error);
      }),
    [],
  );
  useEffect(() => {
    if (!transfer?.offer_expires_at) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [transfer?.offer_expires_at]);
  useEffect(() => {
    if (transfer && ["connecting", "connected"].includes(transfer.status)) {
      setEndpointType(transfer.destination_type);
    }
  }, [transfer]);
  const remaining = useMemo(
    () =>
      transfer?.offer_expires_at
        ? Math.max(
            0,
            Math.ceil(
              (new Date(transfer.offer_expires_at).getTime() - now) / 1_000,
            ),
          )
        : 0,
    [now, transfer?.offer_expires_at],
  );
  const claim = useMutation({
    mutationFn: () =>
      apiRequest<LiveTransfer>(`/transfers/requests/${transfer?.id}/claim`, {
        method: "POST",
        body: JSON.stringify({ expected_version: transfer?.lock_version }),
      }),
    onSuccess: (value) => {
      queryClient.setQueryData(["transfers", "incoming"], [value]);
      setMessage("Звонок закреплён за вами. Подключите софтфон.");
    },
    onError: (error) => setMessage(errorMessage(error)),
  });
  const decline = useMutation({
    mutationFn: (reason: "operator_declined" | "offer_timeout") =>
      apiRequest<LiveTransfer>(`/transfers/requests/${transfer?.id}/decline`, {
        method: "POST",
        body: JSON.stringify({
          expected_version: transfer?.lock_version,
          reason,
        }),
      }),
    onSuccess: () => {
      setMessage("Предложение передано следующему оператору.");
      void queryClient.invalidateQueries({ queryKey: ["transfers"] });
    },
    onError: (error) => setMessage(errorMessage(error)),
  });
  useEffect(() => {
    if (
      transfer?.status === "offered" &&
      remaining === 0 &&
      expiredOffer.current !== transfer.id &&
      !decline.isPending
    ) {
      expiredOffer.current = transfer.id;
      decline.mutate("offer_timeout");
    }
  }, [decline, remaining, transfer?.id, transfer?.status]);
  const answer = useMutation({
    mutationFn: async () => {
      if (endpointType === "browser") {
        const configuration = await apiRequest<OperatorWebRtcConfiguration>(
          "/transfers/operator/webrtc-config",
          { method: "POST" },
        );
        const softphone = getOperatorSoftphone();
        await softphone.register(configuration, {
          ...(microphoneId ? { microphoneId } : {}),
          ...(speakerId ? { speakerId } : {}),
        });
        softphone.armAutoAnswer();
      }
      const current = (
        queryClient.getQueryData(["transfers", "incoming"]) as
          LiveTransfer[] | undefined
      )?.[0];
      if (!current) throw new Error("Transfer was not found");
      const result = await apiRequest<LiveTransfer>(
        `/transfers/requests/${current.id}/answer`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_version: current.lock_version,
            endpoint_type: endpointType,
          }),
        },
      );
      return result;
    },
    onSuccess: (result) => {
      queryClient.setQueryData(["transfers", "incoming"], [result]);
      setMessage(
        endpointType === "browser"
          ? "Соединяем с клиентом. AI отключится только после подтверждения операторской линии."
          : "Вызов отправлен на подтверждённое устройство. Ответьте на нём — AI отключится только после соединения.",
      );
    },
    onError: (error) => setMessage(errorMessage(error)),
  });

  if (transfers.isPending || !transfer) return null;
  const connected =
    transfer.status === "connected" || softphoneState === "active";
  return (
    <section
      className="panel transfer-offer"
      aria-live="polite"
      data-testid="incoming-transfer"
    >
      <div className="transfer-offer-heading">
        <span className="icon-bubble">
          <PhoneCall size={20} />
        </span>
        <div>
          <div className="eyebrow">Входящий перевод от AI</div>
          <h2>{transfer.context.customer_name || "Клиент"}</h2>
        </div>
        <StatusBadge tone={claimed ? "warning" : "success"}>
          {connected
            ? "В разговоре"
            : claimed
              ? "Закреплён"
              : `${remaining} сек.`}
        </StatusBadge>
      </div>
      <div className="transfer-offer-grid">
        <div>
          <span>Причина</span>
          <strong>{transfer.reason}</strong>
        </div>
        <div>
          <span>Язык</span>
          <strong>{transfer.language_code.toUpperCase()}</strong>
        </div>
        <div>
          <span>Сценарий</span>
          <strong>{transfer.context.flow_node_id || "—"}</strong>
        </div>
        <div>
          <span>Попытка</span>
          <strong>
            {transfer.attempt_count} / {transfer.max_attempts}
          </strong>
        </div>
      </div>
      {transfer.summary || transfer.context.summary ? (
        <p>{transfer.summary || transfer.context.summary}</p>
      ) : null}
      {transfer.context.transcript_excerpt ? (
        <details>
          <summary>Последние реплики</summary>
          <pre>{transfer.context.transcript_excerpt}</pre>
        </details>
      ) : null}
      {Boolean(
        transfer.context.recent_calls?.length ||
        transfer.context.related_tasks?.length,
      ) && (
        <details>
          <summary>Связанные звонки и задачи</summary>
          <div className="transfer-context-list">
            {(transfer.context.recent_calls ?? []).map((call) => (
              <span key={call.id}>
                Звонок {call.id.slice(0, 8)} · {call.status}
              </span>
            ))}
            {(transfer.context.related_tasks ?? []).map((task) => (
              <span key={task.id}>
                {task.type} · {task.status} ·{" "}
                {new Intl.DateTimeFormat("ru-RU", {
                  dateStyle: "short",
                  timeStyle: "short",
                }).format(new Date(task.due_at))}
              </span>
            ))}
          </div>
        </details>
      )}
      <div className="transfer-offer-actions">
        {connected && endpointType === "browser" ? (
          <>
            <Button
              variant="secondary"
              onClick={() => {
                getOperatorSoftphone().setMuted(!muted);
                setMuted((value) => !value);
              }}
            >
              {muted ? "Включить микрофон" : "Выключить микрофон"}
            </Button>
            {softphoneState === "on_hold" ? (
              <Button
                variant="secondary"
                onClick={() => void getOperatorSoftphone().resume()}
              >
                Продолжить
              </Button>
            ) : (
              <Button
                variant="secondary"
                onClick={() => void getOperatorSoftphone().hold()}
              >
                Удержание
              </Button>
            )}
            <Button
              variant="danger"
              onClick={() => void getOperatorSoftphone().hangup()}
            >
              <PhoneOff size={17} /> Завершить
            </Button>
          </>
        ) : connected ? (
          <p className="field-help">
            Звонок подключён через подтверждённое внешнее устройство. Управляйте
            микрофоном и завершением на этом устройстве.
          </p>
        ) : !claimed ? (
          <>
            <Button
              disabled={claim.isPending || remaining === 0}
              onClick={() => claim.mutate()}
            >
              <Headphones size={17} /> Принять
            </Button>
            <Button
              variant="secondary"
              disabled={decline.isPending}
              onClick={() => decline.mutate("operator_declined")}
            >
              <PhoneOff size={17} /> Отклонить
            </Button>
          </>
        ) : transfer.status === "claimed" ? (
          <>
            <label className="transfer-endpoint-choice">
              <span>Способ ответа</span>
              <select
                aria-label="Способ ответа"
                onChange={(event) =>
                  setEndpointType(
                    event.target.value as "browser" | "sip" | "mobile",
                  )
                }
                value={endpointType}
              >
                <option value="browser">Браузерный WebRTC-софтфон</option>
                {(endpoints.data ?? []).map((endpoint) => (
                  <option key={endpoint.id} value={endpoint.endpoint_type}>
                    {endpoint.display_hint}
                  </option>
                ))}
              </select>
            </label>
            {endpointType === "browser" && (
              <>
                <label className="transfer-endpoint-choice compact">
                  <span>Микрофон</span>
                  <select
                    aria-label="Микрофон"
                    onChange={(event) => setMicrophoneId(event.target.value)}
                    value={microphoneId}
                  >
                    <option value="">Системный по умолчанию</option>
                    {(audioDevices.data ?? [])
                      .filter((device) => device.kind === "audioinput")
                      .map((device) => (
                        <option key={device.deviceId} value={device.deviceId}>
                          {device.label}
                        </option>
                      ))}
                  </select>
                </label>
                <label className="transfer-endpoint-choice compact">
                  <span>Динамик</span>
                  <select
                    aria-label="Динамик"
                    onChange={(event) => setSpeakerId(event.target.value)}
                    value={speakerId}
                  >
                    <option value="">Системный по умолчанию</option>
                    {(audioDevices.data ?? [])
                      .filter((device) => device.kind === "audiooutput")
                      .map((device) => (
                        <option key={device.deviceId} value={device.deviceId}>
                          {device.label}
                        </option>
                      ))}
                  </select>
                </label>
              </>
            )}
            <Button disabled={answer.isPending} onClick={() => answer.mutate()}>
              <ShieldCheck size={17} /> Ответить
            </Button>
          </>
        ) : null}
      </div>
      {message ? <p className="form-message">{message}</p> : null}
      <p className="field-help">
        Состояние софтфона: {softphoneState}. Микрофон и DTLS-SRTP активируются
        только после защищённой регистрации.
      </p>
    </section>
  );
}
