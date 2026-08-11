import { randomUUID } from "node:crypto";
import { MockRealtimeProvider } from "./providers/mock-realtime.js";

export async function runLocalAiDiagnostic(): Promise<
  Record<string, string | number | boolean>
> {
  const provider = new MockRealtimeProvider();
  const observed = new Set<string>();
  let outputBytes = 0;
  const session = await provider.createSession(
    {
      callId: randomUUID(),
      tenantId: randomUUID(),
      projectId: randomUUID(),
      correlationId: randomUUID(),
      model: "mock-realtime-deterministic",
      voice: "mock",
      language: "ru",
      instructions: "Local contract diagnostic only.",
      toolDefinitions: [],
      vad: {
        type: "server_vad",
        threshold: 0.5,
        prefixPaddingMs: 300,
        silenceDurationMs: 700,
      },
      inputAudioFormat: { type: "audio/pcm", rate: 24_000, channels: 1 },
      outputAudioFormat: { type: "audio/pcm", rate: 24_000, channels: 1 },
    },
    async (event) => {
      observed.add(event.type);
      if (event.type === "audio.output") {
        outputBytes += Buffer.from(event.audioBase64, "base64").byteLength;
      }
    },
  );
  await session.appendAudio(Buffer.alloc(4_800, 0));
  await session.close("local_diagnostic_complete");
  await provider.shutdown();
  const required = [
    "session.ready",
    "speech.started",
    "speech.stopped",
    "response.started",
    "audio.output",
    "transcript.segment",
    "response.completed",
    "session.closed",
  ];
  const passed =
    required.every((event) => observed.has(event)) && outputBytes > 0;
  return {
    status: passed ? "local_mock_passed" : "local_mock_failed",
    provider: "mock-realtime",
    inputBytes: 4_800,
    outputBytes,
    eventTypes: observed.size,
    vadVerified:
      observed.has("speech.started") && observed.has("speech.stopped"),
    transcriptVerified: observed.has("transcript.segment"),
    usageVerified: observed.has("response.completed"),
    liveOpenAiVerified: false,
  };
}
