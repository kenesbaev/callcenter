import { readFile, stat } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { z } from "zod";
import type {
  RealtimeProviderEvent,
  RealtimeSessionConfig,
  RealtimeUsage,
} from "@teamora/contracts";
import { OpenAiRealtimeProvider } from "./providers/openai-realtime.js";

const APPROVAL_PHRASE = "I_APPROVE_OPENAI_TEST_SPEND";
const MAX_AUDIO_BYTES = 24_000 * 2 * 180;
// The budget calculator is deliberately pinned to the model whose current
// official pricing was reviewed with this canary. Other application models
// remain configurable, but require their own reviewed canary price table.
const SUPPORTED_CANARY_MODELS = new Set(["gpt-realtime-2.1-mini"]);

const environmentSchema = z.object({
  OPENAI_API_KEY: z.string().min(20),
  OPENAI_REALTIME_MODEL: z.string().min(1),
  OPENAI_REALTIME_MODEL_ALLOWLIST: z.string().min(1),
  OPENAI_REALTIME_VOICE: z.string().min(1).default("marin"),
  OPENAI_LIVE_TEST_APPROVAL: z.literal(APPROVAL_PHRASE),
  OPENAI_LIVE_TEST_BUDGET_USD: z.coerce.number().positive().max(10),
  OPENAI_LIVE_TEST_MAX_SESSIONS: z.coerce.number().int().min(1).max(3),
  OPENAI_LIVE_TEST_MAX_SESSION_SECONDS: z.coerce.number().int().min(5).max(180),
  OPENAI_LIVE_TEST_TOTAL_TIMEOUT_SECONDS: z.coerce
    .number()
    .int()
    .min(30)
    .max(600),
  OPENAI_LIVE_TEST_AUDIO_PATH: z.string().min(1),
});

export type LiveCanarySettings = {
  apiKey: string;
  model: string;
  voice: string;
  budgetUsd: number;
  maxSessions: number;
  maxSessionSeconds: number;
  totalTimeoutSeconds: number;
  audioPath: string;
};

export function parseLiveCanarySettings(
  environment: NodeJS.ProcessEnv,
  cwd = process.cwd(),
): LiveCanarySettings {
  const parsed = environmentSchema.safeParse(environment);
  if (!parsed.success) {
    throw new Error(
      `Live OpenAI canary is disabled: ${parsed.error.issues
        .map((issue) => issue.path.join("."))
        .join(", ")} must be configured`,
    );
  }
  const value = parsed.data;
  const allowlist = new Set(
    value.OPENAI_REALTIME_MODEL_ALLOWLIST.split(",")
      .map((item) => item.trim())
      .filter(Boolean),
  );
  if (
    !allowlist.has(value.OPENAI_REALTIME_MODEL) ||
    !SUPPORTED_CANARY_MODELS.has(value.OPENAI_REALTIME_MODEL)
  ) {
    throw new Error("Live OpenAI canary model is not explicitly allowlisted");
  }
  const fixtureRoots = [
    resolve(cwd, "services/voice-gateway/tests/fixtures/live-openai"),
    resolve(cwd, "tests/fixtures/live-openai"),
  ];
  const audioPath = resolve(cwd, value.OPENAI_LIVE_TEST_AUDIO_PATH);
  if (
    !isAbsolute(audioPath) ||
    !fixtureRoots.some((root) => isWithin(root, audioPath))
  ) {
    throw new Error(
      "Live OpenAI canary audio must be inside the dedicated test-fixture directory",
    );
  }
  if (!audioPath.toLowerCase().endsWith(".pcm")) {
    throw new Error(
      "Live OpenAI canary accepts only raw PCM16LE 24 kHz mono .pcm fixtures",
    );
  }
  return {
    apiKey: value.OPENAI_API_KEY,
    model: value.OPENAI_REALTIME_MODEL,
    voice: value.OPENAI_REALTIME_VOICE,
    budgetUsd: value.OPENAI_LIVE_TEST_BUDGET_USD,
    maxSessions: value.OPENAI_LIVE_TEST_MAX_SESSIONS,
    maxSessionSeconds: value.OPENAI_LIVE_TEST_MAX_SESSION_SECONDS,
    totalTimeoutSeconds: value.OPENAI_LIVE_TEST_TOTAL_TIMEOUT_SECONDS,
    audioPath,
  };
}

function isWithin(root: string, candidate: string): boolean {
  const result = relative(root, candidate);
  return result === "" || (!result.startsWith(`..${sep}`) && result !== "..");
}

export function conservativeRealtimeCostUsd(usage: RealtimeUsage): number {
  // Current 2026-08-12 published per-million-token prices for
  // gpt-realtime-2.1-mini. Cached inputs are deliberately charged here at the
  // normal input rate, making this a budget upper bound rather than an invoice.
  return (
    ((usage.inputAudioTokens ?? 0) * 10 +
      (usage.outputAudioTokens ?? 0) * 20 +
      (usage.inputTextTokens ?? 0) * 0.6 +
      (usage.outputTextTokens ?? 0) * 2.4) /
    1_000_000
  );
}

async function runSession(
  settings: LiveCanarySettings,
  audio: Uint8Array,
): Promise<{ eventTypes: string[]; costUpperBoundUsd: number }> {
  const provider = new OpenAiRealtimeProvider({
    apiKey: settings.apiKey,
    connectTimeoutMs: 10_000,
    maxMessageBytes: 1_048_576,
    maxQueueBytes: 4_194_304,
  });
  const eventTypes = new Set<string>();
  let usage: RealtimeUsage = {};
  let sessionClosed = false;
  let interrupted = false;
  let toolCompleted = false;
  let session: Awaited<ReturnType<typeof provider.createSession>> | undefined;
  const config: RealtimeSessionConfig = {
    callId: "live-canary-call",
    tenantId: "live-canary-tenant",
    projectId: "live-canary-project",
    correlationId: "live-canary",
    model: settings.model,
    voice: settings.voice,
    language: "en",
    instructions:
      "This is a bounded synthetic canary. After hearing the test question, call search_knowledge exactly once, then answer briefly using its result.",
    toolDefinitions: [
      {
        name: "search_knowledge",
        description: "Search the isolated synthetic canary knowledge fixture.",
        inputSchema: {
          type: "object",
          properties: { query: { type: "string" } },
          required: ["query"],
          additionalProperties: false,
        },
        timeoutMs: 5_000,
      },
    ],
    vad: {
      type: "server_vad",
      threshold: 0.5,
      prefixPaddingMs: 300,
      silenceDurationMs: 700,
      idleTimeoutMs: 10_000,
    },
    inputAudioFormat: { type: "audio/pcm", rate: 24_000, channels: 1 },
    outputAudioFormat: { type: "audio/pcm", rate: 24_000, channels: 1 },
  };
  try {
    session = await provider.createSession(
      config,
      async (event: RealtimeProviderEvent) => {
        eventTypes.add(event.type);
        if (
          event.type === "tool.requested" &&
          event.name === "search_knowledge" &&
          !toolCompleted
        ) {
          toolCompleted = true;
          await session?.sendToolResult(event.callId, {
            status: "matched",
            excerpt: "The synthetic canary support code is K-LINE-CANARY.",
            citations: [{ document: "live-canary-fixture", page: 1 }],
          });
        }
        if (event.type === "audio.output" && !interrupted) {
          interrupted = true;
          await session?.interrupt(event.itemId, 20);
        }
        if (event.type === "response.completed" && event.usage) {
          usage = addUsage(usage, event.usage);
        }
        if (event.type === "session.closed") sessionClosed = true;
        if (event.type === "session.error") {
          throw new Error(
            `OpenAI Realtime canary provider error: ${event.code}`,
          );
        }
      },
    );
    for (let offset = 0; offset < audio.byteLength; offset += 4_800) {
      await session.appendAudio(audio.slice(offset, offset + 4_800));
      await delay(20);
    }
    const deadline = Date.now() + settings.maxSessionSeconds * 1_000;
    while (
      Date.now() < deadline &&
      !(
        eventTypes.has("speech.started") &&
        eventTypes.has("speech.stopped") &&
        eventTypes.has("audio.output") &&
        eventTypes.has("response.completed") &&
        eventTypes.has("tool.requested")
      )
    ) {
      await delay(100);
    }
    for (const required of [
      "session.ready",
      "speech.started",
      "speech.stopped",
      "audio.output",
      "response.completed",
      "tool.requested",
    ]) {
      if (!eventTypes.has(required))
        throw new Error(`Live canary did not observe ${required}`);
    }
    if (!interrupted)
      throw new Error("Live canary did not verify cancel/truncate");
    await session.close("live_canary_complete");
    await delay(50);
    if (!sessionClosed) eventTypes.add("session.close_requested");
    return {
      eventTypes: [...eventTypes].sort(),
      costUpperBoundUsd: conservativeRealtimeCostUsd(usage),
    };
  } finally {
    await session?.close("live_canary_cleanup").catch(() => undefined);
    await provider.shutdown();
  }
}

function addUsage(left: RealtimeUsage, right: RealtimeUsage): RealtimeUsage {
  return {
    inputAudioTokens:
      (left.inputAudioTokens ?? 0) + (right.inputAudioTokens ?? 0),
    outputAudioTokens:
      (left.outputAudioTokens ?? 0) + (right.outputAudioTokens ?? 0),
    inputTextTokens: (left.inputTextTokens ?? 0) + (right.inputTextTokens ?? 0),
    outputTextTokens:
      (left.outputTextTokens ?? 0) + (right.outputTextTokens ?? 0),
    cachedTokens: (left.cachedTokens ?? 0) + (right.cachedTokens ?? 0),
    totalTokens: (left.totalTokens ?? 0) + (right.totalTokens ?? 0),
  };
}

export async function runLiveOpenAiCanary(
  environment: NodeJS.ProcessEnv = process.env,
): Promise<Record<string, unknown>> {
  const settings = parseLiveCanarySettings(environment);
  const metadata = await stat(settings.audioPath);
  if (
    !metadata.isFile() ||
    metadata.size === 0 ||
    metadata.size > MAX_AUDIO_BYTES
  ) {
    throw new Error(
      "Live canary PCM fixture must be non-empty and no longer than three minutes",
    );
  }
  const audio = new Uint8Array(await readFile(settings.audioPath));
  const startedAt = Date.now();
  let spentUpperBoundUsd = 0;
  const sessions: Array<Record<string, unknown>> = [];
  for (let index = 0; index < settings.maxSessions; index += 1) {
    if (spentUpperBoundUsd >= settings.budgetUsd) break;
    if (Date.now() - startedAt >= settings.totalTimeoutSeconds * 1_000) {
      throw new Error("Live OpenAI canary total timeout reached");
    }
    const result = await runSession(settings, audio);
    spentUpperBoundUsd += result.costUpperBoundUsd;
    sessions.push({ sequence: index + 1, ...result });
    if (spentUpperBoundUsd > settings.budgetUsd) {
      throw new Error("Live OpenAI canary budget upper bound exceeded");
    }
  }
  return {
    status: "live_openai_verified",
    model: settings.model,
    sessions,
    sessionCount: sessions.length,
    costUpperBoundUsd: Number(spentUpperBoundUsd.toFixed(6)),
    budgetUsd: settings.budgetUsd,
  };
}

if (process.argv[1]?.endsWith("live-openai-canary.js")) {
  runLiveOpenAiCanary()
    .then((result) => process.stdout.write(`${JSON.stringify(result)}\n`))
    .catch((error: unknown) => {
      const message =
        error instanceof Error ? error.message : "Live canary failed";
      process.stderr.write(`${message}\n`);
      process.exitCode = 1;
    });
}
