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
const transferDialSchema = z
  .object({
    tenantId: z.uuid(),
    projectId: z.uuid(),
    callId: z.uuid(),
    transferRequestId: z.uuid(),
    destinationType: z.enum(["browser", "sip", "mobile"]),
    destination: z.string().min(2).max(160),
    idempotencyKey: z.string().min(8).max(160),
  })
  .strict();
const transferCancelSchema = z
  .object({
    callId: z.uuid(),
    reason: z.string().min(2).max(120),
    idempotencyKey: z.string().min(8).max(160),
  })
  .strict();
const webRtcProvisionSchema = z
  .object({
    membershipId: z.uuid(),
    username: z.string().regex(/^operator-[0-9a-f-]{36}$/i),
    password: z.string().min(32).max(160),
    ttlSeconds: z.number().int().min(30).max(120),
  })
  .strict();

export type OperatorHandoffController = {
  dialOperator(input: {
    tenantId: string;
    projectId: string;
    callId: string;
    destinationType: "browser" | "sip" | "mobile";
    destination: string;
    correlationId: string;
  }): Promise<{ status: "connecting"; operatorChannelId: string }>;
  cancelOperator(callId: string, reason: string): Promise<void>;
};

export type WebRtcProvisioningController = {
  provisionWebRtcEndpoint(
    membershipId: string,
    username: string,
    password: string,
  ): Promise<void>;
  revokeWebRtcEndpoint(membershipId: string): Promise<void>;
};

export type GatewayInternalApiOptions = {
  serviceToken: string;
  trustedHosts: string[];
  maxBodyBytes: number;
  provider?: TelephonyProvider;
  mediaAdapter?: RtpDiagnosticAdapter;
  runRealtimeDiagnostic?: () => Promise<
    Record<string, string | number | boolean>
  >;
  handoffController?: OperatorHandoffController;
  webRtcProvisioner?: WebRtcProvisioningController;
};

type CachedCommand = {
  fingerprint: string;
  response:
    | TelephonyCommandResult
    | { status: "connecting"; operatorChannelId: string };
};

export function createGatewayRequestHandler(
  options: GatewayInternalApiOptions,
) {
  const submissions = new Map<string, CachedCommand>();
  const webRtcExpiryTimers = new Map<string, NodeJS.Timeout>();
  return async (request: IncomingMessage, response: ServerResponse) => {
    const path = new URL(request.url ?? "/", "http://gateway.internal")
      .pathname;
    const correlationId = safeCorrelationId(request);
    response.setHeader("x-correlation-id", correlationId);

    const isCommand = path === "/internal/v1/telephony/commands";
    const isLocalMediaTest =
      path === "/internal/v1/telephony/diagnostics/local-media";
    const isRealtimeTest = path === "/internal/v1/ai/diagnostics/local";
    const isTransferDial = path === "/internal/v1/transfers/dial";
    const isTransferCancel = path === "/internal/v1/transfers/cancel";
    const isWebRtcProvision =
      path === "/internal/v1/transfers/webrtc/provision";
    if (
      !isCommand &&
      !isLocalMediaTest &&
      !isRealtimeTest &&
      !isTransferDial &&
      !isTransferCancel &&
      !isWebRtcProvision
    )
      return false;
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
    if (isRealtimeTest) {
      if (!options.runRealtimeDiagnostic) {
        json(
          response,
          503,
          error(
            "ai_diagnostic_unavailable",
            "Local AI diagnostic is unavailable",
          ),
        );
        return true;
      }
      try {
        json(response, 200, await options.runRealtimeDiagnostic());
      } catch (diagnosticError) {
        logger.warn(
          {
            correlationId,
            code:
              diagnosticError instanceof Error
                ? diagnosticError.name
                : "ai_diagnostic_error",
          },
          "local AI Realtime diagnostic failed",
        );
        json(
          response,
          409,
          error("ai_diagnostic_failed", "Local AI Realtime diagnostic failed"),
        );
      }
      return true;
    }
    if (isTransferDial) {
      const transfer = transferDialSchema.safeParse(parsedJson);
      if (!transfer.success) {
        json(
          response,
          422,
          error("transfer_command_invalid", "Transfer command is invalid"),
        );
        return true;
      }
      if (!options.handoffController) {
        json(
          response,
          503,
          error("transfer_unavailable", "Operator handoff is unavailable"),
        );
        return true;
      }
      const cacheKey = `transfer:${transfer.data.idempotencyKey}`;
      const fingerprint = createHash("sha256")
        .update(
          JSON.stringify({
            tenantId: transfer.data.tenantId,
            projectId: transfer.data.projectId,
            callId: transfer.data.callId,
            transferRequestId: transfer.data.transferRequestId,
            destinationType: transfer.data.destinationType,
            destination: transfer.data.destination,
          }),
        )
        .digest("hex");
      const cached = submissions.get(cacheKey);
      if (cached) {
        if (cached.fingerprint !== fingerprint) {
          json(
            response,
            409,
            error(
              "idempotency_key_reused",
              "Idempotency-Key was reused with another transfer",
            ),
          );
        } else {
          json(response, 202, cached.response);
        }
        return true;
      }
      try {
        const result = await options.handoffController.dialOperator({
          tenantId: transfer.data.tenantId,
          projectId: transfer.data.projectId,
          callId: transfer.data.callId,
          destinationType: transfer.data.destinationType,
          destination: transfer.data.destination,
          correlationId,
        });
        submissions.set(cacheKey, { fingerprint, response: result });
        json(response, 202, result);
      } catch (transferError) {
        logger.warn(
          {
            correlationId,
            callId: transfer.data.callId,
            code:
              transferError instanceof Error
                ? transferError.name
                : "transfer_error",
          },
          "operator handoff failed",
        );
        json(
          response,
          409,
          error("operator_handoff_failed", "Operator handoff failed"),
        );
      }
      return true;
    }
    if (isTransferCancel) {
      const transfer = transferCancelSchema.safeParse(parsedJson);
      if (!transfer.success) {
        json(
          response,
          422,
          error("transfer_command_invalid", "Transfer command is invalid"),
        );
        return true;
      }
      if (!options.handoffController) {
        json(
          response,
          503,
          error("transfer_unavailable", "Operator handoff is unavailable"),
        );
        return true;
      }
      await options.handoffController.cancelOperator(
        transfer.data.callId,
        transfer.data.reason,
      );
      json(response, 200, { status: "cancelled" });
      return true;
    }
    if (isWebRtcProvision) {
      const provision = webRtcProvisionSchema.safeParse(parsedJson);
      if (!provision.success) {
        json(
          response,
          422,
          error(
            "webrtc_provision_invalid",
            "WebRTC provision command is invalid",
          ),
        );
        return true;
      }
      if (!options.webRtcProvisioner) {
        json(
          response,
          503,
          error("webrtc_unavailable", "WebRTC provisioning is unavailable"),
        );
        return true;
      }
      try {
        await options.webRtcProvisioner.provisionWebRtcEndpoint(
          provision.data.membershipId,
          provision.data.username,
          provision.data.password,
        );
        const existing = webRtcExpiryTimers.get(provision.data.membershipId);
        if (existing) clearTimeout(existing);
        const timer = setTimeout(() => {
          webRtcExpiryTimers.delete(provision.data.membershipId);
          void options.webRtcProvisioner
            ?.revokeWebRtcEndpoint(provision.data.membershipId)
            .catch(() => undefined);
        }, provision.data.ttlSeconds * 1_000);
        timer.unref();
        webRtcExpiryTimers.set(provision.data.membershipId, timer);
        json(response, 201, {
          status: "provisioned",
          username: provision.data.username,
          expiresInSeconds: provision.data.ttlSeconds,
        });
      } catch (provisionError) {
        logger.warn(
          {
            correlationId,
            membershipId: provision.data.membershipId,
            code:
              provisionError instanceof Error
                ? provisionError.name
                : "webrtc_provision_error",
          },
          "WebRTC endpoint provisioning failed",
        );
        json(
          response,
          409,
          error(
            "webrtc_provision_failed",
            "WebRTC endpoint provisioning failed",
          ),
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
