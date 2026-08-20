import { createServer } from "node:http";
import { randomUUID } from "node:crypto";
import { WebSocketServer, type WebSocket } from "ws";

const port = Number(process.env.MOCK_OPENAI_PORT ?? 8790);
const expectedToken =
  process.env.MOCK_OPENAI_TOKEN ?? "local-test-token-not-a-secret";
const scenario = process.env.MOCK_OPENAI_SCENARIO ?? "normal";
const server = createServer((request, response) => {
  if (request.url === "/health") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(
      JSON.stringify({ status: "ready", provider: "deterministic_mock" }),
    );
    return;
  }
  response.writeHead(404).end();
});
const sockets = new Set<WebSocket>();
const websocket = new WebSocketServer({
  noServer: true,
  maxPayload: 1_048_576,
});

server.on("upgrade", (request, socket, head) => {
  const path = new URL(request.url ?? "/", "http://mock.local").pathname;
  if (
    path !== "/v1/realtime" ||
    request.headers.authorization !== `Bearer ${expectedToken}`
  ) {
    socket.write("HTTP/1.1 401 Unauthorized\r\nConnection: close\r\n\r\n");
    socket.destroy();
    return;
  }
  websocket.handleUpgrade(request, socket, head, (client) =>
    websocket.emit("connection", client),
  );
});

websocket.on("connection", (socket) => {
  sockets.add(socket);
  let audioBytes = 0;
  let outputItem = `item-${randomUUID()}`;
  let pendingToolTranscript: string | undefined;
  let toolPending = false;
  let voiceTurn = 0;
  socket.on("close", () => sockets.delete(socket));
  socket.on("message", (raw) => {
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(raw.toString()) as Record<string, unknown>;
    } catch {
      send(socket, {
        type: "error",
        event_id: randomUUID(),
        error: { code: "invalid_json" },
      });
      return;
    }
    const type = String(event.type ?? "");
    if (type === "session.update") {
      send(socket, {
        type: "session.updated",
        event_id: randomUUID(),
        session: { id: `sess-${randomUUID()}` },
      });
      return;
    }
    if (type === "input_audio_buffer.append") {
      audioBytes += Buffer.from(String(event.audio ?? ""), "base64").byteLength;
      if (scenario === "disconnect") {
        socket.close(1011, "mock_disconnect");
        return;
      }
      if (scenario === "malformed") {
        socket.send("{bad-json");
        return;
      }
      if (scenario === "rate_limit") {
        send(socket, {
          type: "error",
          event_id: randomUUID(),
          error: { code: "rate_limit_exceeded" },
        });
        return;
      }
      if (audioBytes < 4_800) return;
      audioBytes = 0;
      if (toolPending) return;
      const responseItem = outputItem;
      if (scenario === "voice_e2e") {
        voiceTurn += 1;
        if (voiceTurn === 1) {
          toolPending = true;
          emitTurnAndTool(socket, "search_knowledge", {
            query: "deterministic FAQ",
          });
          return;
        }
        if (voiceTurn === 2) {
          toolPending = true;
          emitTurnAndTool(socket, "create_callback", {
            due_at: "2035-01-01T10:00:00Z",
            comment: "Deterministic voice E2E callback",
            confirmed: true,
          });
          return;
        }
      }
      if (scenario === "transfer_e2e" && voiceTurn === 0) {
        voiceTurn += 1;
        toolPending = true;
        emitTurnAndTool(socket, "request_human_transfer", {
          reason: "Deterministic customer request for a live operator",
        });
        return;
      }
      if (scenario === "delayed") {
        setTimeout(() => respond(socket, responseItem, false), 75);
      } else {
        respond(
          socket,
          responseItem,
          scenario === "duplicate",
          undefined,
          scenario,
        );
      }
      outputItem = `item-${randomUUID()}`;
      return;
    }
    if (type === "response.create") {
      respond(
        socket,
        outputItem,
        false,
        pendingToolTranscript ?? "Deterministic disclosure.",
      );
      pendingToolTranscript = undefined;
    } else if (type === "response.cancel")
      send(socket, { type: "response.cancelled", event_id: randomUUID() });
    else if (type === "conversation.item.truncate")
      send(socket, {
        type: "conversation.item.truncated",
        event_id: randomUUID(),
        item_id: event.item_id,
      });
    else if (type === "conversation.item.create") {
      send(socket, {
        type: "conversation.item.created",
        event_id: randomUUID(),
        item: event.item,
      });
      const item =
        event.item && typeof event.item === "object"
          ? (event.item as Record<string, unknown>)
          : {};
      if (item.type === "function_call_output") {
        toolPending = false;
        pendingToolTranscript =
          "Deterministic answer with a verified citation.";
      }
    }
  });
});

function respond(
  socket: WebSocket,
  itemId: string,
  duplicate: boolean,
  transcript = "Deterministic mock response.",
  activeScenario = "normal",
): void {
  const speechId = randomUUID();
  send(socket, {
    type: "input_audio_buffer.speech_started",
    event_id: `speech-start-${speechId}`,
    audio_start_ms: 0,
  });
  send(socket, {
    type: "input_audio_buffer.speech_stopped",
    event_id: `speech-stop-${speechId}`,
    audio_end_ms: 100,
  });
  send(socket, {
    type: "conversation.item.input_audio_transcription.completed",
    event_id: randomUUID(),
    item_id: `input-${speechId}`,
    transcript: "deterministic test question",
  });
  if (activeScenario === "tool_call") {
    send(socket, {
      type: "response.function_call_arguments.done",
      event_id: randomUUID(),
      call_id: `tool-${randomUUID()}`,
      name: "search_knowledge",
      arguments: JSON.stringify({ query: "deterministic FAQ" }),
    });
    return;
  }
  const responseId = `response-${randomUUID()}`;
  send(socket, {
    type: "response.created",
    event_id: randomUUID(),
    response: { id: responseId },
  });
  const delta = {
    type: "response.output_audio.delta",
    event_id: `audio-${responseId}`,
    item_id: itemId,
    delta: Buffer.alloc(4_800).toString("base64"),
  };
  send(socket, delta);
  if (duplicate) send(socket, delta);
  send(socket, {
    type: "response.output_audio_transcript.done",
    event_id: randomUUID(),
    item_id: itemId,
    transcript,
  });
  send(socket, {
    type: "response.done",
    event_id: randomUUID(),
    response: {
      id: responseId,
      usage: {
        total_tokens: 20,
        input_token_details: {
          audio_tokens: 10,
          text_tokens: 0,
          cached_tokens: 0,
        },
        output_token_details: { audio_tokens: 10, text_tokens: 0 },
      },
    },
  });
}

function emitTurnAndTool(
  socket: WebSocket,
  name: string,
  argumentsValue: Record<string, unknown>,
): void {
  const speechId = randomUUID();
  send(socket, {
    type: "input_audio_buffer.speech_started",
    event_id: `speech-start-${speechId}`,
    audio_start_ms: 0,
  });
  send(socket, {
    type: "input_audio_buffer.speech_stopped",
    event_id: `speech-stop-${speechId}`,
    audio_end_ms: 100,
  });
  send(socket, {
    type: "conversation.item.input_audio_transcription.completed",
    event_id: randomUUID(),
    item_id: `input-${speechId}`,
    transcript: "deterministic test question",
  });
  send(socket, {
    type: "response.function_call_arguments.done",
    event_id: randomUUID(),
    call_id: `tool-${randomUUID()}`,
    name,
    arguments: JSON.stringify(argumentsValue),
  });
}
function send(socket: WebSocket, event: Record<string, unknown>): void {
  if (socket.readyState === socket.OPEN) socket.send(JSON.stringify(event));
}

server.listen(port, "0.0.0.0");
function shutdown(): void {
  for (const socket of sockets) socket.close(1001, "shutdown");
  websocket.close();
  server.close(() => process.exit(0));
}
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
