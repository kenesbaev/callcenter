import { createServer } from "node:http";
import { gatewayEnvironmentSchema } from "@teamora/config";
import { logger } from "./logger.js";
import { registry } from "./metrics.js";
import { createGatewayRequestHandler } from "./internal-api.js";
import { AsteriskAriProvider } from "./providers/asterisk-ari.js";
import { OpenAiRealtimeProvider } from "./providers/openai-realtime.js";

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
const realtimeProvider = config.OPENAI_API_KEY
  ? new OpenAiRealtimeProvider({ apiKey: config.OPENAI_API_KEY })
  : undefined;
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
      })
    : undefined;
const handleInternalRequest = createGatewayRequestHandler({
  serviceToken: config.GATEWAY_SERVICE_TOKEN,
  trustedHosts: config.GATEWAY_TRUSTED_HOSTS.split(",")
    .map((host) => host.trim())
    .filter(Boolean),
  maxBodyBytes: config.GATEWAY_MAX_BODY_BYTES,
  ...(telephonyProvider ? { provider: telephonyProvider } : {}),
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
        openai_realtime: realtimeProvider
          ? "configured_not_end_to_end_verified"
          : "unavailable",
        asterisk: telephonyProvider
          ? "configured_live_verification_required"
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
  await realtimeProvider?.shutdown();
  logger.info("graceful shutdown complete");
  process.exit(0);
}
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));
