import { createServer } from "node:http";
import { gatewayEnvironmentSchema } from "@teamora/config";
import { logger } from "./logger.js";
import { registry } from "./metrics.js";
import { createGatewayRequestHandler } from "./internal-api.js";
import { AsteriskAriProvider } from "./providers/asterisk-ari.js";
import { OpenAiRealtimeProvider } from "./providers/openai-realtime.js";
import { MockRealtimeProvider } from "./providers/mock-realtime.js";
import { AsteriskAriListener } from "./ari-listener.js";
import { ProviderEventClient } from "./provider-event-client.js";
import { RtpDiagnosticAdapter } from "./rtp-diagnostic.js";
import { RtpMediaPool } from "./rtp-media-pool.js";
import { VoiceRuntime } from "./voice-runtime.js";
import { runLocalAiDiagnostic } from "./local-ai-diagnostic.js";
import { attachVoiceLab } from "./voice-lab.js";

const parsed = gatewayEnvironmentSchema.safeParse(process.env);
if (!parsed.success) {
  logger.fatal(
    {
      issues: parsed.error.issues.map((issue) => ({
        path: issue.path.join("."),
        message: issue.message,
      })),
    },
    "invalid configuration",
  );
  process.exit(1);
}
const config = parsed.data;
const realtimeProvider = !config.OPENAI_REALTIME_ENABLED
  ? undefined
  : config.OPENAI_REALTIME_PROVIDER === "mock"
    ? new MockRealtimeProvider()
    : new OpenAiRealtimeProvider({
        apiKey: config.OPENAI_API_KEY!,
        ...(config.OPENAI_REALTIME_URL
          ? { url: config.OPENAI_REALTIME_URL }
          : {}),
        connectTimeoutMs: config.OPENAI_REALTIME_CONNECT_TIMEOUT_MS,
        maxMessageBytes: config.OPENAI_REALTIME_MAX_MESSAGE_BYTES,
        maxQueueBytes: config.OPENAI_REALTIME_MAX_QUEUE_BYTES,
      });
const telephonyProvider =
  config.ASTERISK_ARI_URL &&
  config.ASTERISK_ARI_USERNAME &&
  config.ASTERISK_ARI_PASSWORD &&
  config.ASTERISK_EXTERNAL_MEDIA_HOST
    ? new AsteriskAriProvider({
        baseUrl: config.ASTERISK_ARI_URL,
        username: config.ASTERISK_ARI_USERNAME,
        password: config.ASTERISK_ARI_PASSWORD,
        externalHost: config.ASTERISK_EXTERNAL_MEDIA_HOST,
        transport: config.ASTERISK_EXTERNAL_MEDIA_TRANSPORT,
        timeoutMs: config.ASTERISK_ARI_TIMEOUT_MS,
        application: config.ASTERISK_ARI_APPLICATION,
        pjsipEndpoint: config.ASTERISK_PJSIP_ENDPOINT,
      })
    : undefined;
const mediaAdapter = telephonyProvider
  ? new RtpDiagnosticAdapter(
      config.ASTERISK_RTP_BIND_HOST,
      config.ASTERISK_RTP_PORT_START,
    )
  : undefined;
await mediaAdapter?.start();
const advertisedMediaHost = config.ASTERISK_EXTERNAL_MEDIA_HOST?.replace(
  /:\d+$/,
  "",
);
const mediaPool =
  telephonyProvider && advertisedMediaHost
    ? new RtpMediaPool(
        config.ASTERISK_RTP_BIND_HOST,
        advertisedMediaHost,
        config.ASTERISK_RTP_PORT_START + 1,
        config.ASTERISK_RTP_PORT_END,
      )
    : undefined;
const providerEventClient = telephonyProvider
  ? new ProviderEventClient(
      config.API_INTERNAL_URL,
      config.GATEWAY_SERVICE_TOKEN,
      config.ASTERISK_ARI_TIMEOUT_MS,
    )
  : undefined;
const voiceRuntime =
  realtimeProvider && providerEventClient && mediaPool
    ? new VoiceRuntime(
        new Map([[config.OPENAI_REALTIME_PROVIDER, realtimeProvider]]),
        providerEventClient,
        mediaPool,
      )
    : undefined;
const ariListener =
  telephonyProvider && providerEventClient
    ? new AsteriskAriListener(
        {
          baseUrl: config.ASTERISK_ARI_URL!,
          username: config.ASTERISK_ARI_USERNAME!,
          password: config.ASTERISK_ARI_PASSWORD!,
          application: config.ASTERISK_ARI_APPLICATION,
          reconnectMinMs: config.ASTERISK_ARI_RECONNECT_MIN_MS,
          reconnectMaxMs: config.ASTERISK_ARI_RECONNECT_MAX_MS,
        },
        telephonyProvider,
        providerEventClient,
        voiceRuntime,
      )
    : undefined;
if (telephonyProvider) {
  try {
    const removed = await telephonyProvider.cleanupWebRtcEndpoints();
    if (removed)
      logger.info({ removed }, "expired WebRTC endpoints reconciled");
  } catch (error) {
    logger.warn(
      { code: error instanceof Error ? error.name : "webrtc_cleanup_error" },
      "WebRTC endpoint reconciliation deferred",
    );
  }
}
ariListener?.start();
const handleInternalRequest = createGatewayRequestHandler({
  serviceToken: config.GATEWAY_SERVICE_TOKEN,
  trustedHosts: config.GATEWAY_TRUSTED_HOSTS.split(",")
    .map((host) => host.trim())
    .filter(Boolean),
  maxBodyBytes: config.GATEWAY_MAX_BODY_BYTES,
  ...(telephonyProvider ? { provider: telephonyProvider } : {}),
  ...(mediaAdapter ? { mediaAdapter } : {}),
  runRealtimeDiagnostic: runLocalAiDiagnostic,
  ...(ariListener ? { handoffController: ariListener } : {}),
  ...(telephonyProvider ? { webRtcProvisioner: telephonyProvider } : {}),
});
let shuttingDown = false;

const server = createServer(async (request, response) => {
  if (await handleInternalRequest(request, response)) return;
  const path = new URL(request.url ?? "/", "http://gateway.internal").pathname;
  if (path === "/health/live") {
    response.writeHead(shuttingDown ? 503 : 200, {
      "content-type": "application/json",
    });
    response.end(
      JSON.stringify({ status: shuttingDown ? "stopping" : "alive" }),
    );
    return;
  }
  if (path === "/health/ready") {
    response.writeHead(shuttingDown ? 503 : 200, {
      "content-type": "application/json",
    });
    response.end(
      JSON.stringify({
        status: shuttingDown ? "stopping" : "ready",
        telephony_deployment: config.TELEPHONY_SIP_ARCHITECTURE,
        media_gateway_placement: config.TELEPHONY_MEDIA_GATEWAY_PLACEMENT,
        edge_connectivity:
          config.TELEPHONY_SIP_ARCHITECTURE === "direct"
            ? "not_applicable"
            : config.TELEPHONY_EDGE_TUNNEL_ENABLED
              ? "configured_live_verification_required"
              : "not_configured",
        openai_realtime: realtimeProvider
          ? config.OPENAI_REALTIME_PROVIDER === "mock"
            ? "mock_local_verification_available"
            : "configured_live_verification_required"
          : "unavailable",
        ai_session: voiceRuntime?.status() ?? "unavailable",
        voice_lab: config.OPENAI_VOICE_LAB_ENABLED
          ? config.OPENAI_REALTIME_PROVIDER === "mock"
            ? "mock_local_verification_available"
            : "configured_live_verification_required"
          : "disabled",
        asterisk: telephonyProvider
          ? "configured_live_verification_required"
          : "unavailable",
        ari_listener: ariListener?.status() ?? "unavailable",
        rtp_adapter: mediaAdapter
          ? "ready_local_verification_available"
          : "unavailable",
      }),
    );
    return;
  }
  if (path === "/metrics") {
    response.writeHead(200, { "content-type": registry.contentType });
    response.end(await registry.metrics());
    return;
  }
  response.writeHead(404, { "content-type": "application/json" });
  response.end(
    JSON.stringify({
      error: { code: "not_found", message: "Resource was not found" },
    }),
  );
});

const voiceLab = realtimeProvider
  ? attachVoiceLab(server, {
      enabled: config.OPENAI_VOICE_LAB_ENABLED,
      provider: realtimeProvider,
      providerName: config.OPENAI_REALTIME_PROVIDER,
      model: config.OPENAI_REALTIME_MODEL,
      voice: config.OPENAI_REALTIME_VOICE,
      ...(config.OPENAI_REALTIME_REASONING_EFFORT === "none"
        ? {}
        : { reasoningEffort: config.OPENAI_REALTIME_REASONING_EFFORT }),
      tokenSecret: config.GATEWAY_SERVICE_TOKEN,
      maxDurationSeconds: config.OPENAI_VOICE_LAB_MAX_SECONDS,
      maxSessions: config.OPENAI_VOICE_LAB_MAX_SESSIONS,
      maxSocketMessageBytes: Math.min(
        config.OPENAI_REALTIME_MAX_MESSAGE_BYTES,
        32_768,
      ),
      maxSocketQueueBytes: config.OPENAI_REALTIME_MAX_QUEUE_BYTES,
    })
  : undefined;

server.listen(config.GATEWAY_PORT, "0.0.0.0", () =>
  logger.info(
    {
      port: config.GATEWAY_PORT,
      realtimeProvider: realtimeProvider
        ? "configured_not_verified"
        : "unavailable",
      telephonyProvider: telephonyProvider
        ? "configured_live_verification_required"
        : "unavailable",
    },
    "voice gateway listening",
  ),
);

async function shutdown(signal: string): Promise<void> {
  if (shuttingDown) return;
  shuttingDown = true;
  logger.info({ signal }, "graceful shutdown started");
  server.close();
  await ariListener?.stop();
  await voiceRuntime?.shutdown("gateway_shutdown");
  await voiceLab?.close();
  await mediaAdapter?.stop();
  await realtimeProvider?.shutdown();
  logger.info("graceful shutdown complete");
  process.exit(0);
}
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));
