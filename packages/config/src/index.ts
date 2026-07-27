import { z } from "zod";

const booleanFromString = z
  .enum(["true", "false"])
  .default("false")
  .transform((value) => value === "true");

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
    OPENAI_REALTIME_MODEL: z.string().min(1).default("gpt-realtime-2.1-mini"),
    OPENAI_REALTIME_VOICE: z.string().min(1).default("marin"),
    ASTERISK_ARI_URL: z.url(),
    ASTERISK_ARI_USERNAME: z.string().min(1),
    ASTERISK_ARI_PASSWORD: z.string().min(1),
    ASTERISK_EXTERNAL_MEDIA_HOST: z.string().min(1),
    ASTERISK_EXTERNAL_MEDIA_TRANSPORT: z
      .enum(["websocket", "udp"])
      .default("websocket"),
    GATEWAY_SERVICE_TOKEN: z.string().min(20),
    KARAKALPAK_EXPERIMENTAL: booleanFromString,
    GATEWAY_PORT: z.coerce.number().int().min(1).max(65535).default(8787),
    GATEWAY_MAX_ACTIVE_CALLS: z.coerce.number().int().min(1).default(100),
  })
  .superRefine((value, context) => {
    if (value.APP_ENV === "production" && !value.OPENAI_API_KEY) {
      context.addIssue({
        code: "custom",
        path: ["OPENAI_API_KEY"],
        message:
          "OPENAI_API_KEY is required when the OpenAI adapter is enabled in production",
      });
    }
  });

export type GatewayEnvironment = z.infer<typeof gatewayEnvironmentSchema>;
