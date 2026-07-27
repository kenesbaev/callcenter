import { createServer } from "node:http";
import { gatewayEnvironmentSchema } from "@teamora/config";
import { logger } from "./logger.js";
import { registry } from "./metrics.js";
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
const provider = config.OPENAI_API_KEY
  ? new OpenAiRealtimeProvider({ apiKey: config.OPENAI_API_KEY })
  : undefined;
let shuttingDown = false;

const server = createServer(async (request, response) => {
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
        openai_realtime: provider
          ? "configured_not_end_to_end_verified"
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
      provider: provider ? "configured_not_verified" : "unavailable",
    },
    "voice gateway listening",
  ),
);

async function shutdown(signal: string): Promise<void> {
  if (shuttingDown) return;
  shuttingDown = true;
  logger.info({ signal }, "graceful shutdown started");
  server.close();
  await provider?.shutdown();
  logger.info("graceful shutdown complete");
  process.exit(0);
}
process.on("SIGTERM", () => void shutdown("SIGTERM"));
process.on("SIGINT", () => void shutdown("SIGINT"));
