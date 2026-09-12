import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceLabView } from "@/components/voice-lab-view";

const apiRequestMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({
  apiRequest: apiRequestMock,
  ApiClientError: class ApiClientError extends Error {},
}));

function renderView() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <VoiceLabView />
    </QueryClientProvider>,
  );
}

function status(overrides: Record<string, unknown> = {}) {
  return {
    configured: true,
    enabled: true,
    provider: "mock",
    model: "mock-realtime-deterministic",
    voice: "mock",
    voice_lab_enabled: true,
    voice_lab_max_seconds: 90,
    live_verification: "not_applicable",
    last_session_state: null,
    last_safe_error: null,
    active_sessions: 0,
    latency: {
      metric: "first_audio_latency_ms",
      samples: 0,
      p50_ms: null,
      p95_ms: null,
      p99_ms: null,
      is_available: false,
    },
    ...overrides,
  };
}

beforeEach(() => apiRequestMock.mockReset());

describe("VoiceLabView", () => {
  it("keeps the start action disabled while the server feature flag is off", async () => {
    apiRequestMock.mockResolvedValue(status({ voice_lab_enabled: false }));
    renderView();
    expect(await screen.findByText("Отключён сервером")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Начать разговор/i }),
    ).toBeDisabled();
    expect(
      screen.getByText(/OPENAI_VOICE_LAB_ENABLED=true/),
    ).toBeInTheDocument();
  });

  it("requires explicit approval before enabling a paid OpenAI session", async () => {
    apiRequestMock.mockResolvedValue(
      status({ provider: "openai", model: "gpt-realtime", voice: "marin" }),
    );
    renderView();
    const start = await screen.findByRole("button", {
      name: /Начать разговор/i,
    });
    expect(start).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /платную OpenAI Realtime-сессию/i,
      }),
    );
    await waitFor(() => expect(start).toBeEnabled());
  });
});
