import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import { z } from "zod";
import type {
  TelephonyCommand,
  TelephonyCommandResult,
  TelephonyProvider,
} from "@teamora/contracts";
import { telephonyCommandNames } from "@teamora/contracts";
import { logger } from "./logger.js";
import type { RtpDiagnosticAdapter } from "./rtp-diagnostic.js";

const safeParameter = z.union([z.string(), z.number(), z.boolean()]);
const commandSchema = z
  .object({
    version: z.literal("1"),
    command: z.enum(telephonyCommandNames),
    tenantId: z.uuid(),
    projectId: z.uuid(),
    callId: z.uuid(),
    providerCallId: z.string().min(1).max(200).optional(),
    commandId: z.uuid(),
    idempotencyKey: z.string().min(8).max(160),
    timestamp: z.iso.datetime({ offset: true }),
    correlationId: z.string().min(1).max(160),
    parameters: z.record(z.string(), safeParameter),
  })
  .strict();
const localMediaTestSchema = z
  .object({
    codec: z.enum(["ulaw", "alaw"]),
    packets: z.number().int().min(10).max(500),
  })
  .strict();

export type GatewayInternalApiOptions = {
  serviceToken: string;
  trustedHosts: string[];
  maxBodyBytes: number;
  provider?: TelephonyProvider;
  mediaAdapter?: RtpDiagnosticAdapter;
};

type CachedCommand = {
  fingerprint: string;
  response: TelephonyCommandResult;
};

export function createGatewayRequestHandler(
  options: GatewayInternalApiOptions,
) {
  const submissions = new Map<string, CachedCommand>();
  return async (request: IncomingMessage, response: ServerResponse) => {
    const path = new URL(request.url ?? "/", "http://gateway.internal")
      .pathname;
    const correlationId = safeCorrelationId(request);
    response.setHeader("x-correlation-id", correlationId);

    const isCommand = path === "/internal/v1/telephony/commands";
    const isLocalMediaTest =
      path === "/internal/v1/telephony/diagnostics/local-media";
    if (!isCommand && !isLocalMediaTest) return false;
    if (request.method !== "POST") {
      json(response, 405, error("method_not_allowed", "POST is required"));
      return true;
    }
    if (!trustedHost(request, options.trustedHosts)) {
      json(
        response,
        400,
        error("untrusted_host", "Request host is not trusted"),
      );
      return true;
    }
    if (!authorized(request, options.serviceToken)) {
      json(
        response,
        401,
        error("service_auth_invalid", "Service authentication failed"),
      );
      return true;
    }
    if (
      !(request.headers["content-type"] ?? "")
        .toLowerCase()
        .startsWith("application/json")
    ) {
      json(
        response,
        415,
        error("content_type_invalid", "application/json is required"),
      );
      return true;
    }

    let rawBody: Buffer;
    try {
      rawBody = await readBody(request, options.maxBodyBytes);
    } catch (bodyError) {
      const tooLarge = bodyError instanceof BodyTooLargeError;
      json(
        response,
        tooLarge ? 413 : 400,
        error(
          tooLarge ? "body_too_large" : "body_invalid",
          tooLarge ? "Request body is too large" : "Request body is invalid",
        ),
      );
      return true;
    }

    let parsedJson: unknown;
    try {
      parsedJson = JSON.parse(rawBody.toString("utf8"));
    } catch {
      json(
        response,
        400,
        error("json_invalid", "Request body is not valid JSON"),
      );
      return true;
    }
    if (isLocalMediaTest) {
      const diagnostic = localMediaTestSchema.safeParse(parsedJson);
      if (!diagnostic.success) {
        json(
          response,
          422,
          error("diagnostic_invalid", "Local media diagnostic is invalid"),
        );
        return true;
      }
      if (!options.mediaAdapter) {
        json(
          response,
          503,
          error(
            "rtp_adapter_unavailable",
            "RTP diagnostic adapter is unavailable",
          ),
        );
        return true;
      }
      try {
        const result = await options.mediaAdapter.runLocalTest(
          diagnostic.data.codec,
          diagnostic.data.packets,
        );
        json(response, 200, result);
      } catch (diagnosticError) {
        logger.warn(
          {
            correlationId,
            code:
              diagnosticError instanceof Error
                ? diagnosticError.name
                : "diagnostic_error",
          },
          "local RTP diagnostic failed",
        );
        json(
          response,
          409,
          error("rtp_diagnostic_failed", "Local RTP diagnostic failed"),
        );
      }
      return true;
    }
    const parsed = commandSchema.safeParse(parsedJson);
    if (!parsed.success) {
      json(
        response,
        422,
        error("command_invalid", "Telephony command is invalid"),
      );
      return true;
    }
    if (!options.provider) {
      json(
        response,
        503,
        error("asterisk_not_configured", "Asterisk provider is not configured"),
      );
      return true;
    }

    const command = parsed.data as TelephonyCommand;
    const fingerprint = createHash("sha256")
      .update(
        JSON.stringify({
          command: command.command,
          tenantId: command.tenantId,
          projectId: command.projectId,
          callId: command.callId,
          providerCallId: command.providerCallId,
          parameters: command.parameters,
        }),
      )
      .digest("hex");
    const cached = submissions.get(command.idempotencyKey);
    if (cached) {
      if (cached.fingerprint !== fingerprint) {
        json(
          response,
          409,
          error(
            "idempotency_key_reused",
            "Idempotency-Key was reused with another command",
          ),
        );
      } else {
        json(response, 200, {
          ...cached.response,
          commandId: command.commandId,
        });
      }
      return true;
    }

    try {
      const result = await dispatch(options.provider, command);
      submissions.set(command.idempotencyKey, {
        fingerprint,
        response: result,
      });
      if (submissions.size > 2_000)
        submissions.delete(submissions.keys().next().value as string);
      logger.info(
        {
          correlationId,
          command: command.command,
          commandId: command.commandId,
          callId: command.callId,
          tenantId: command.tenantId,
          provider: options.provider.name,
        },
        "telephony command completed",
      );
      json(response, 200, result);
    } catch (providerError) {
      logger.warn(
        {
          correlationId,
          command: command.command,
          commandId: command.commandId,
          callId: command.callId,
          code:
            providerError instanceof Error
              ? providerError.name
              : "provider_error",
        },
        "telephony command failed",
      );
      json(
        response,
        502,
        error(
          "provider_command_failed",
          "Telephony provider rejected the command",
        ),
      );
    }
    return true;
  };
}

async function dispatch(
  provider: TelephonyProvider,
  command: TelephonyCommand,
): Promise<TelephonyCommandResult> {
  const context = command;
  switch (command.command) {
    case "originate":
      return provider.originate(context, command.parameters);
    case "answer":
      return provider.answer(context);
    case "hangup":
      return provider.hangup(
        context,
        stringParameter(command, "reason", "normal"),
      );
    case "hold":
      return provider.hold(context);
    case "resume":
      return provider.resume(context);
    case "transfer":
      return provider.transfer(
        context,
        stringParameter(command, "destination"),
        stringParameter(command, "reason"),
      );
    case "get_call_state":
      return provider.getCallState(context);
    case "start_recording":
      return provider.startRecording(context);
    case "pause_recording":
      return provider.pauseRecording(context);
    case "resume_recording":
      return provider.resumeRecording(context);
    case "stop_recording":
      return provider.stopRecording(context);
    case "create_external_media":
      return provider.createExternalMedia(context);
  }
}

function stringParameter(
  command: TelephonyCommand,
  name: string,
  fallback?: string,
): string {
  const value = command.parameters[name];
  if (typeof value === "string" && value.trim()) return value;
  if (fallback !== undefined) return fallback;
  throw new Error(`${name} is required`);
}

function trustedHost(
  request: IncomingMessage,
  trustedHosts: string[],
): boolean {
  const host = (
    (request.headers.host ?? "").split(":", 1)[0] ?? ""
  ).toLowerCase();
  return trustedHosts.some((candidate) => candidate.toLowerCase() === host);
}

function authorized(request: IncomingMessage, expected: string): boolean {
  const supplied =
    request.headers.authorization?.replace(/^Bearer\s+/i, "") ?? "";
  const suppliedBuffer = Buffer.from(supplied);
  const expectedBuffer = Buffer.from(expected);
  return (
    suppliedBuffer.length === expectedBuffer.length &&
    timingSafeEqual(suppliedBuffer, expectedBuffer)
  );
}

function safeCorrelationId(request: IncomingMessage): string {
  const supplied = request.headers["x-correlation-id"];
  return typeof supplied === "string" && supplied.length <= 160
    ? supplied
    : randomUUID();
}

class BodyTooLargeError extends Error {}

async function readBody(
  request: IncomingMessage,
  maxBodyBytes: number,
): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    size += buffer.length;
    if (size > maxBodyBytes) throw new BodyTooLargeError();
    chunks.push(buffer);
  }
  return Buffer.concat(chunks);
}

function error(code: string, message: string) {
  return { error: { code, message } };
}

function json(response: ServerResponse, status: number, payload: object): void {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(payload));
}
