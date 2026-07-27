import {
  Counter,
  Gauge,
  Histogram,
  Registry,
  collectDefaultMetrics,
} from "prom-client";

export const registry = new Registry();
collectDefaultMetrics({ register: registry, prefix: "teamora_voice_gateway_" });
export const activeCalls = new Gauge({
  name: "teamora_voice_active_calls",
  help: "Current active voice sessions",
  registers: [registry],
});
export const failedTools = new Counter({
  name: "teamora_voice_failed_tools_total",
  help: "Failed typed tool executions",
  registers: [registry],
  labelNames: ["code"],
});
export const transfers = new Counter({
  name: "teamora_voice_transfers_total",
  help: "Voice calls transferred to a human queue",
  registers: [registry],
  labelNames: ["reason"],
});
export const providerLatency = new Histogram({
  name: "teamora_voice_provider_latency_seconds",
  help: "Realtime provider operation latency",
  registers: [registry],
  labelNames: ["operation"],
});
