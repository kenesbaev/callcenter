import pino from "pino";

export const logger = pino({
  level: process.env.LOG_LEVEL ?? "info",
  base: { service: "voice-gateway" },
  redact: {
    paths: [
      "*.authorization",
      "*.apiKey",
      "*.password",
      "*.secret",
      "*.transcript",
      "*.audio",
      "req.headers.authorization",
    ],
    censor: "[REDACTED]",
  },
});
