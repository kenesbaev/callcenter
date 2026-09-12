"use client";

import { useQuery } from "@tanstack/react-query";
import {
  CircleStop,
  Headphones,
  Mic,
  MicOff,
  PhoneOff,
  Radio,
  ShieldCheck,
  VolumeX,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Button, StatusBadge } from "@teamora/ui";
import { ApiClientError, apiRequest } from "@/lib/api";
import type {
  AIRealtimeStatus,
  RealtimeUsageSummary,
  VoiceLabTicket,
} from "@/lib/types";
import { QueryError, SectionSkeleton } from "@/components/query-state";

type LabState = "idle" | "connecting" | "listening" | "error";
type TranscriptLine = {
  itemId: string;
  speaker: "customer" | "ai";
  text: string;
  final: boolean;
};
type PlaybackItem = {
  startTime: number;
  endTime: number;
  sources: Set<AudioBufferSourceNode>;
};

const PAID_CONFIRMATION = "I_APPROVE_PAID_VOICE_LAB";

export function VoiceLabView() {
  const status = useQuery({
    queryKey: ["ai-realtime", "voice-lab-status"],
    queryFn: () => apiRequest<AIRealtimeStatus>("/ai-realtime/status"),
    refetchInterval: 30_000,
  });
  const [language, setLanguage] = useState<"ru" | "uz">("ru");
  const [paidApproved, setPaidApproved] = useState(false);
  const [labState, setLabState] = useState<LabState>("idle");
  const [notice, setNotice] = useState("Готов к проверке микрофона.");
  const [muted, setMuted] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [usage, setUsage] = useState<RealtimeUsageSummary>({});
  const socketRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const captureRef = useRef<AudioWorkletNode | null>(null);
  const captureSourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const mutedRef = useRef(false);
  const playbackCursorRef = useRef(0);
  const playbackRef = useRef(new Map<string, PlaybackItem>());
  const startedAtRef = useRef(0);

  const sendInterrupt = useCallback(() => {
    const context = contextRef.current;
    const socket = socketRef.current;
    if (!context) return;
    const now = context.currentTime;
    const items = [...playbackRef.current].map(([itemId, item]) => ({
      item_id: itemId,
      played_ms: Math.max(
        0,
        Math.min(
          (now - item.startTime) * 1_000,
          (item.endTime - item.startTime) * 1_000,
        ),
      ),
    }));
    for (const item of playbackRef.current.values()) {
      for (const source of item.sources) {
        try {
          source.stop();
        } catch {
          // The source may have ended between reading state and stopping it.
        }
      }
    }
    playbackRef.current.clear();
    playbackCursorRef.current = now;
    if (items.length && socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "interrupt", items }));
    }
  }, []);

  const releaseResources = useCallback(
    async (sendStop: boolean) => {
      const socket = socketRef.current;
      const stream = streamRef.current;
      const context = contextRef.current;
      const capture = captureRef.current;
      const captureSource = captureSourceRef.current;
      if (sendStop && socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "stop" }));
      }
      sendInterrupt();
      socketRef.current = null;
      streamRef.current = null;
      contextRef.current = null;
      captureRef.current = null;
      captureSourceRef.current = null;
      mutedRef.current = false;
      capture?.disconnect();
      captureSource?.disconnect();
      stream?.getTracks().forEach((track) => track.stop());
      if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000);
      if (context && context.state !== "closed") await context.close();
      playbackRef.current.clear();
      playbackCursorRef.current = 0;
    },
    [sendInterrupt],
  );

  useEffect(
    () => () => {
      void releaseResources(false);
    },
    [releaseResources],
  );

  useEffect(() => {
    if (labState === "idle" || labState === "error") return;
    const timer = window.setInterval(() => {
      setSeconds(
        Math.max(0, Math.floor((Date.now() - startedAtRef.current) / 1_000)),
      );
    }, 250);
    return () => window.clearInterval(timer);
  }, [labState]);

  const stop = useCallback(async () => {
    await releaseResources(true);
    setLabState("idle");
    setMuted(false);
    setNotice("Тест завершён. Можно запустить новый разговор.");
  }, [releaseResources]);

  const enqueueAudio = useCallback(
    (data: string, itemId: string, rate: number) => {
      const context = contextRef.current;
      if (!context || !data || !Number.isFinite(rate)) return;
      const binary = window.atob(data);
      const bytes = new Uint8Array(binary.length);
      for (let index = 0; index < binary.length; index += 1)
        bytes[index] = binary.charCodeAt(index);
      if (bytes.byteLength % 2) return;
      const samples = new Int16Array(bytes.buffer);
      const buffer = context.createBuffer(1, samples.length, rate);
      const output = buffer.getChannelData(0);
      for (let index = 0; index < samples.length; index += 1)
        output[index] = samples[index] / 32_768;
      const source = context.createBufferSource();
      source.buffer = buffer;
      source.connect(context.destination);
      const startTime = Math.max(
        context.currentTime + 0.025,
        playbackCursorRef.current,
      );
      const endTime = startTime + buffer.duration;
      const existing = playbackRef.current.get(itemId);
      const item = existing ?? {
        startTime,
        endTime,
        sources: new Set<AudioBufferSourceNode>(),
      };
      item.endTime = endTime;
      item.sources.add(source);
      playbackRef.current.set(itemId, item);
      playbackCursorRef.current = endTime;
      source.onended = () => {
        item.sources.delete(source);
        if (
          item.sources.size === 0 &&
          context.currentTime >= item.endTime - 0.02
        )
          playbackRef.current.delete(itemId);
      };
      source.start(startTime);
    },
    [],
  );

  const appendTranscript = useCallback((event: Record<string, unknown>) => {
    const speaker: TranscriptLine["speaker"] =
      event.speaker === "customer" ? "customer" : "ai";
    const itemId = String(event.item_id ?? "unknown");
    const text = String(event.text ?? "");
    const final = event.final === true;
    if (!text) return;
    setTranscript((current) => {
      const index = current.findIndex(
        (line) => line.itemId === itemId && line.speaker === speaker,
      );
      if (index < 0)
        return [...current, { itemId, speaker, text, final }].slice(-40);
      const next = [...current];
      next[index] = {
        ...next[index],
        text: final ? text : `${next[index].text}${text}`,
        final,
      };
      return next;
    });
  }, []);

  const start = async () => {
    if (!status.data?.voice_lab_enabled || labState !== "idle") return;
    setLabState("connecting");
    setNotice("Запрашиваю микрофон и создаю защищённую сессию…");
    setTranscript([]);
    setUsage({});
    setSeconds(0);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });
      streamRef.current = stream;
      const ticket = await apiRequest<VoiceLabTicket>(
        "/ai-realtime/lab/ticket",
        {
          method: "POST",
          body: JSON.stringify({
            language,
            confirmation:
              status.data.provider === "openai" && paidApproved
                ? PAID_CONFIRMATION
                : "",
          }),
        },
      );
      const context = new AudioContext({ latencyHint: "interactive" });
      contextRef.current = context;
      await context.resume();
      await context.audioWorklet.addModule("/voice-lab-processor.js");
      const capture = new AudioWorkletNode(context, "voice-lab-capture");
      const captureSource = context.createMediaStreamSource(stream);
      const silent = context.createGain();
      silent.gain.value = 0;
      captureSource.connect(capture);
      capture.connect(silent).connect(context.destination);
      captureRef.current = capture;
      captureSourceRef.current = captureSource;
      const websocketUrl = new URL(ticket.websocket_url, window.location.href);
      websocketUrl.protocol =
        window.location.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(websocketUrl, [
        "teamora-voice-lab",
        `teamora-ticket.${ticket.token}`,
      ]);
      socketRef.current = socket;
      capture.port.onmessage = (message: MessageEvent<ArrayBuffer>) => {
        if (socket.readyState !== WebSocket.OPEN || mutedRef.current) return;
        socket.send(
          JSON.stringify({
            type: "audio",
            data: arrayBufferToBase64(message.data),
          }),
        );
      };
      socket.onmessage = (message) => {
        let event: Record<string, unknown>;
        try {
          event = JSON.parse(String(message.data)) as Record<string, unknown>;
        } catch {
          return;
        }
        if (event.type === "ready") {
          startedAtRef.current = Date.now();
          setLabState("listening");
          setNotice(
            ticket.paid_provider
              ? "Live OpenAI подключён. Говорите — микрофон активен."
              : "Локальный mock подключён. Это проверка тракта без расходов OpenAI.",
          );
        } else if (event.type === "audio") {
          enqueueAudio(
            String(event.data ?? ""),
            String(event.item_id ?? "unknown"),
            Number(event.sample_rate ?? 24_000),
          );
        } else if (event.type === "transcript") {
          appendTranscript(event);
        } else if (event.type === "user_speaking") {
          sendInterrupt();
        } else if (event.type === "usage") {
          setUsage((event.usage as RealtimeUsageSummary | undefined) ?? {});
        } else if (event.type === "error") {
          setNotice(
            `Voice AI остановлен: ${String(event.code ?? "unknown_error")}`,
          );
          setLabState("error");
          void releaseResources(false);
        }
      };
      socket.onerror = () => {
        setNotice("Не удалось открыть защищённое голосовое соединение.");
        setLabState("error");
      };
      socket.onclose = (event) => {
        if (socketRef.current !== socket) return;
        void releaseResources(false);
        setLabState((current) => (current === "error" ? current : "idle"));
        if (event.reason === "voice_lab_time_limit")
          setNotice("Достигнут серверный лимит времени. Тест завершён.");
      };
    } catch (error) {
      await releaseResources(false);
      setLabState("error");
      setNotice(
        error instanceof ApiClientError
          ? error.message
          : error instanceof DOMException && error.name === "NotAllowedError"
            ? "Разрешите доступ к микрофону в браузере."
            : "Не удалось запустить микрофон или аудиосистему браузера.",
      );
    }
  };

  const toggleMute = () => {
    const next = !mutedRef.current;
    mutedRef.current = next;
    streamRef.current?.getAudioTracks().forEach((track) => {
      track.enabled = !next;
    });
    setMuted(next);
  };

  if (status.isPending) return <SectionSkeleton />;
  if (status.isError)
    return (
      <QueryError
        error={status.error}
        retry={() => void status.refetch()}
        title="Не удалось получить статус Voice AI"
      />
    );

  const live = status.data.provider === "openai";
  const canStart =
    status.data.voice_lab_enabled &&
    status.data.enabled &&
    status.data.configured &&
    (!live || paidApproved) &&
    labState === "idle";
  const maxSeconds = status.data.voice_lab_max_seconds;

  return (
    <>
      <div className="page-heading voice-lab-heading">
        <div>
          <h1>Voice AI Lab</h1>
          <p>Поговорите с текущим голосовым AI до подключения SIP-номера.</p>
        </div>
        <StatusBadge
          tone={status.data.voice_lab_enabled ? "success" : "warning"}
        >
          {status.data.voice_lab_enabled ? "Доступен" : "Отключён сервером"}
        </StatusBadge>
      </div>

      <div className="voice-lab-grid">
        <section className="panel voice-lab-console">
          <div
            className="voice-lab-orb"
            data-state={labState}
            aria-hidden="true"
          >
            <span />
            <Mic size={34} />
          </div>
          <div className="voice-lab-state" aria-live="polite">
            <strong>
              {labState === "connecting"
                ? "Подключение"
                : labState === "listening"
                  ? muted
                    ? "Микрофон выключен"
                    : "Слушаю"
                  : labState === "error"
                    ? "Нужна проверка"
                    : "Готов"}
            </strong>
            <span>
              {formatDuration(seconds)} / {formatDuration(maxSeconds)}
            </span>
            <p>{notice}</p>
          </div>

          <div className="voice-lab-controls">
            {labState === "idle" || labState === "error" ? (
              <Button
                type="button"
                onClick={() => void start()}
                disabled={!canStart}
              >
                <Radio size={17} /> Начать разговор
              </Button>
            ) : (
              <>
                <Button type="button" variant="secondary" onClick={toggleMute}>
                  {muted ? <Mic size={17} /> : <MicOff size={17} />}
                  {muted ? "Включить микрофон" : "Выключить микрофон"}
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  onClick={sendInterrupt}
                >
                  <VolumeX size={17} /> Прервать ответ AI
                </Button>
                <Button
                  type="button"
                  variant="danger"
                  onClick={() => void stop()}
                >
                  <PhoneOff size={17} /> Завершить
                </Button>
              </>
            )}
          </div>

          {!status.data.voice_lab_enabled && (
            <div className="voice-lab-warning" role="status">
              <CircleStop size={18} />
              <span>
                Включите <code>OPENAI_VOICE_LAB_ENABLED=true</code> на сервере
                после настройки провайдера.
              </span>
            </div>
          )}
          {live && (
            <label className="voice-lab-approval">
              <input
                type="checkbox"
                checked={paidApproved}
                disabled={labState !== "idle"}
                onChange={(event) => setPaidApproved(event.target.checked)}
              />
              <span>
                Я понимаю, что этот тест создаёт платную OpenAI Realtime-сессию
                максимум на {maxSeconds} секунд.
              </span>
            </label>
          )}
        </section>

        <aside className="panel voice-lab-meta">
          <div className="row-between">
            <h2>Текущая конфигурация</h2>
            <Headphones size={20} aria-hidden="true" />
          </div>
          <dl>
            <div>
              <dt>Provider</dt>
              <dd>{status.data.provider}</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>{status.data.model}</dd>
            </div>
            <div>
              <dt>Voice</dt>
              <dd>{status.data.voice}</dd>
            </div>
            <div>
              <dt>Лимит</dt>
              <dd>{maxSeconds} сек.</dd>
            </div>
            <div>
              <dt>Tokens</dt>
              <dd>{usage.totalTokens ?? 0}</dd>
            </div>
          </dl>
          <label className="field">
            <span>Язык теста</span>
            <select
              value={language}
              disabled={labState !== "idle"}
              onChange={(event) =>
                setLanguage(event.target.value as "ru" | "uz")
              }
            >
              <option value="ru">Русский</option>
              <option value="uz">O‘zbekcha</option>
            </select>
          </label>
          <div className="voice-lab-security">
            <ShieldCheck size={20} />
            <p>
              API-ключ остаётся на сервере. Браузер получает одноразовый
              короткий билет, а не ключ OpenAI.
            </p>
          </div>
        </aside>
      </div>

      <section className="panel voice-lab-transcript">
        <div className="row-between">
          <div>
            <h2>Транскрипт теста</h2>
            <p className="panel-subtitle">
              Не вводите пароли, PIN, SMS-коды и данные карт.
            </p>
          </div>
          <StatusBadge>
            {transcript.length ? `${transcript.length} реплик` : "Пусто"}
          </StatusBadge>
        </div>
        {transcript.length ? (
          <div className="voice-lab-lines">
            {transcript.map((line) => (
              <article
                key={`${line.itemId}-${line.speaker}`}
                data-speaker={line.speaker}
              >
                <span>{line.speaker === "customer" ? "Вы" : "Voice AI"}</span>
                <p>{line.text}</p>
              </article>
            ))}
          </div>
        ) : (
          <div className="voice-lab-empty">
            После начала разговора реплики появятся здесь.
          </div>
        )}
      </section>
    </>
  );
}

function arrayBufferToBase64(value: ArrayBuffer): string {
  const bytes = new Uint8Array(value);
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1)
    binary += String.fromCharCode(bytes[index]);
  return window.btoa(binary);
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
    .toString()
    .padStart(2, "0");
  const remainder = Math.floor(seconds % 60)
    .toString()
    .padStart(2, "0");
  return `${minutes}:${remainder}`;
}
