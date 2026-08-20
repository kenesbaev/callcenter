import { describe, expect, it } from "vitest";
import {
  conservativeRealtimeCostUsd,
  parseLiveCanarySettings,
} from "../src/live-openai-canary.js";

const base = {
  OPENAI_API_KEY: "test-key-not-secret-123456",
  OPENAI_REALTIME_MODEL: "gpt-realtime-2.1-mini",
  OPENAI_REALTIME_MODEL_ALLOWLIST: "gpt-realtime-2.1-mini,gpt-realtime-2.1",
  OPENAI_REALTIME_VOICE: "marin",
  OPENAI_LIVE_TEST_APPROVAL: "I_APPROVE_OPENAI_TEST_SPEND",
  OPENAI_LIVE_TEST_BUDGET_USD: "10",
  OPENAI_LIVE_TEST_MAX_SESSIONS: "3",
  OPENAI_LIVE_TEST_MAX_SESSION_SECONDS: "180",
  OPENAI_LIVE_TEST_TOTAL_TIMEOUT_SECONDS: "600",
  OPENAI_LIVE_TEST_AUDIO_PATH:
    "services/voice-gateway/tests/fixtures/live-openai/synthetic.pcm",
};

describe("live OpenAI canary safety policy", () => {
  it("is fail-closed without explicit spend approval", () => {
    expect(() =>
      parseLiveCanarySettings({ ...base, OPENAI_LIVE_TEST_APPROVAL: "" }),
    ).toThrow(/disabled/);
  });

  it("rejects an unallowlisted model and a fixture outside the isolated directory", () => {
    expect(() =>
      parseLiveCanarySettings({ ...base, OPENAI_REALTIME_MODEL: "unknown" }),
    ).toThrow(/allowlisted/);
    expect(() =>
      parseLiveCanarySettings({
        ...base,
        OPENAI_LIVE_TEST_AUDIO_PATH: "customer-recording.pcm",
      }),
    ).toThrow(/test-fixture/);
  });

  it("caps sessions, duration and budget during validation", () => {
    expect(() =>
      parseLiveCanarySettings({ ...base, OPENAI_LIVE_TEST_MAX_SESSIONS: "4" }),
    ).toThrow(/configured/);
    expect(() =>
      parseLiveCanarySettings({
        ...base,
        OPENAI_LIVE_TEST_BUDGET_USD: "10.01",
      }),
    ).toThrow(/configured/);
    expect(() =>
      parseLiveCanarySettings({
        ...base,
        OPENAI_LIVE_TEST_MAX_SESSION_SECONDS: "181",
      }),
    ).toThrow(/configured/);
  });

  it("calculates a conservative published-price upper bound", () => {
    expect(
      conservativeRealtimeCostUsd({
        inputAudioTokens: 1_000_000,
        outputAudioTokens: 1_000_000,
        inputTextTokens: 1_000_000,
        outputTextTokens: 1_000_000,
      }),
    ).toBe(33);
  });
});
