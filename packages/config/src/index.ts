import { z } from "zod";

const booleanFromString = z
  .enum(["true", "false"])
  .default("false")
  .transform((value) => value === "true");
const optionalNonEmptyString = z.preprocess(
  (value) => (value === "" ? undefined : value),
  z.string().min(1).optional(),
);
const optionalUrl = z.preprocess(
  (value) => (value === "" ? undefined : value),
  z.url().optional(),
);
const commaSeparated = z.string().transform((value) =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean),
);

export const gatewayEnvironmentSchema = z
  .object({
    APP_ENV: z
      .enum(["development", "test", "staging", "production"])
      .default("development"),
    LOG_LEVEL: z.preprocess(
      (value) => (typeof value === "string" ? value.toLowerCase() : value),
      z
        .enum(["trace", "debug", "info", "warn", "error", "fatal"])
        .default("info"),
    ),
    API_INTERNAL_URL: z.url(),
    REDIS_URL: z.string().min(1),
    OPENAI_API_KEY: z.preprocess(
      (value) => (value === "" ? undefined : value),
      z.string().min(20).optional(),
    ),
    OPENAI_REALTIME_ENABLED: booleanFromString,
    OPENAI_REALTIME_PROVIDER: z.enum(["mock", "openai"]).default("mock"),
    OPENAI_REALTIME_URL: optionalUrl,
    OPENAI_REALTIME_MODEL: z.string().min(1).default("gpt-realtime-2.1"),
    OPENAI_REALTIME_MODEL_ALLOWLIST: commaSeparated.default([
      "gpt-realtime-2.1-mini",
      "gpt-realtime-2.1",
      "gpt-realtime-2",
      "gpt-realtime-1.5",
    ]),
    OPENAI_REALTIME_VOICE: z.string().min(1).default("marin"),
    OPENAI_REALTIME_REASONING_EFFORT: z
      .enum(["none", "low", "medium", "high"])
      .default("none"),
    OPENAI_REALTIME_CONNECT_TIMEOUT_MS: z.coerce
      .number()
      .int()
      .min(500)
      .max(30_000)
      .default(10_000),
    OPENAI_REALTIME_MAX_MESSAGE_BYTES: z.coerce
      .number()
      .int()
      .min(16_384)
      .max(4_194_304)
      .default(1_048_576),
    OPENAI_REALTIME_MAX_QUEUE_BYTES: z.coerce
      .number()
      .int()
      .min(65_536)
      .max(16_777_216)
      .default(4_194_304),
    OPENAI_VOICE_LAB_ENABLED: booleanFromString.default(false),
    OPENAI_VOICE_LAB_MAX_SECONDS: z.coerce
      .number()
      .int()
      .min(15)
      .max(600)
      .default(180),
    OPENAI_VOICE_LAB_MAX_SESSIONS: z.coerce
      .number()
      .int()
      .min(1)
      .max(10)
      .default(2),
    OPENAI_REALTIME_VAD_TYPE: z
      .enum(["server_vad", "semantic_vad"])
      .default("server_vad"),
    OPENAI_REALTIME_VAD_THRESHOLD: z.coerce.number().min(0).max(1).default(0.5),
    OPENAI_REALTIME_PREFIX_PADDING_MS: z.coerce
      .number()
      .int()
      .min(0)
      .max(5_000)
      .default(300),
    OPENAI_REALTIME_SILENCE_DURATION_MS: z.coerce
      .number()
      .int()
      .min(200)
      .max(5_000)
      .default(700),
    OPENAI_REALTIME_IDLE_TIMEOUT_MS: z.coerce
      .number()
      .int()
      .min(5_000)
      .max(120_000)
      .default(30_000),
    ASTERISK_ARI_URL: optionalUrl,
    ASTERISK_ARI_USERNAME: optionalNonEmptyString,
    ASTERISK_ARI_PASSWORD: optionalNonEmptyString,
    ASTERISK_EXTERNAL_MEDIA_HOST: optionalNonEmptyString,
    ASTERISK_EXTERNAL_MEDIA_TRANSPORT: z
      .enum(["websocket", "udp"])
      .default("websocket"),
    ASTERISK_ARI_TIMEOUT_MS: z.coerce
      .number()
      .int()
      .min(250)
      .max(30_000)
      .default(5_000),
    ASTERISK_ARI_APPLICATION: z.string().min(1).default("teamora-voice"),
    ASTERISK_PJSIP_ENDPOINT: z.string().min(1).default("provider-endpoint"),
    ASTERISK_ARI_RECONNECT_MIN_MS: z.coerce
      .number()
      .int()
      .min(100)
      .max(5_000)
      .default(500),
    ASTERISK_ARI_RECONNECT_MAX_MS: z.coerce
      .number()
      .int()
      .min(1_000)
      .max(120_000)
      .default(30_000),
    ASTERISK_RTP_BIND_HOST: z.string().min(1).default("0.0.0.0"),
    ASTERISK_RTP_PORT_START: z.coerce
      .number()
      .int()
      .min(1_024)
      .max(65_000)
      .default(40_000),
    ASTERISK_RTP_PORT_END: z.coerce
      .number()
      .int()
      .min(1_025)
      .max(65_535)
      .default(40_099),
    TELEPHONY_SIP_ARCHITECTURE: z.enum(["direct", "uz_edge"]).default("direct"),
    TELEPHONY_MEDIA_GATEWAY_PLACEMENT: z
      .enum(["platform", "edge"])
      .default("platform"),
    TELEPHONY_EDGE_TUNNEL_ENABLED: booleanFromString,
    GATEWAY_SERVICE_TOKEN: z.string().min(20),
    GATEWAY_TRUSTED_HOSTS: z
      .string()
      .default("voice-gateway,gateway,localhost,127.0.0.1"),
    GATEWAY_MAX_BODY_BYTES: z.coerce
      .number()
      .int()
      .min(1_024)
      .max(1_048_576)
      .default(65_536),
    KARAKALPAK_EXPERIMENTAL: booleanFromString,
    GATEWAY_PORT: z.coerce.number().int().min(1).max(65535).default(8787),
    GATEWAY_MAX_ACTIVE_CALLS: z.coerce.number().int().min(1).default(100),
  })
  .superRefine((value, context) => {
    if (
      value.OPENAI_REALTIME_ENABLED &&
      !value.OPENAI_REALTIME_MODEL_ALLOWLIST.includes(
        value.OPENAI_REALTIME_MODEL,
      )
    ) {
      context.addIssue({
        code: "custom",
        path: ["OPENAI_REALTIME_MODEL"],
        message:
          "OPENAI_REALTIME_MODEL must be present in OPENAI_REALTIME_MODEL_ALLOWLIST",
      });
    }
    if (
      value.OPENAI_REALTIME_ENABLED &&
      value.OPENAI_REALTIME_PROVIDER === "openai" &&
      !value.OPENAI_API_KEY
    ) {
      context.addIssue({
        code: "custom",
        path: ["OPENAI_API_KEY"],
        message:
          "OPENAI_API_KEY is required when the OpenAI Realtime provider is enabled",
      });
    }
    if (
      value.APP_ENV === "production" &&
      value.OPENAI_REALTIME_PROVIDER === "mock" &&
      value.OPENAI_REALTIME_ENABLED
    ) {
      context.addIssue({
        code: "custom",
        path: ["OPENAI_REALTIME_PROVIDER"],
        message: "Mock Realtime provider cannot be enabled in production",
      });
    }
    if (value.OPENAI_VOICE_LAB_ENABLED && !value.OPENAI_REALTIME_ENABLED) {
      context.addIssue({
        code: "custom",
        path: ["OPENAI_VOICE_LAB_ENABLED"],
        message:
          "OPENAI_REALTIME_ENABLED must be true when OPENAI_VOICE_LAB_ENABLED is true",
      });
    }
    if (value.ASTERISK_RTP_PORT_START > value.ASTERISK_RTP_PORT_END) {
      context.addIssue({
        code: "custom",
        path: ["ASTERISK_RTP_PORT_END"],
        message:
          "ASTERISK_RTP_PORT_END must be greater than or equal to ASTERISK_RTP_PORT_START",
      });
    }
    if (
      value.TELEPHONY_SIP_ARCHITECTURE === "uz_edge" &&
      !value.TELEPHONY_EDGE_TUNNEL_ENABLED
    ) {
      context.addIssue({
        code: "custom",
        path: ["TELEPHONY_EDGE_TUNNEL_ENABLED"],
        message: "A Uzbekistan telephony edge requires a private tunnel",
      });
    }
    if (
      value.OPENAI_REALTIME_ENABLED &&
      value.ASTERISK_ARI_URL &&
      value.ASTERISK_RTP_PORT_END <= value.ASTERISK_RTP_PORT_START
    ) {
      context.addIssue({
        code: "custom",
        path: ["ASTERISK_RTP_PORT_END"],
        message:
          "Voice AI requires at least one RTP port in addition to the diagnostic port",
      });
    }
  });

export type GatewayEnvironment = z.infer<typeof gatewayEnvironmentSchema>;
