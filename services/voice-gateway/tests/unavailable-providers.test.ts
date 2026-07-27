import { describe, expect, it } from "vitest";
import {
  AzureSpeechAdapter,
  ProviderUnavailableError,
  YandexSpeechKitAdapter,
} from "../src/providers/unavailable-speech.js";

describe("inactive speech adapters", () => {
  it.each([new AzureSpeechAdapter(), new YandexSpeechKitAdapter()])(
    "keeps $name unavailable without credentials",
    async (provider) => {
      expect(provider.status).toBe("unavailable");
      await expect(
        provider.transcribe(new Uint8Array(), "uz"),
      ).rejects.toBeInstanceOf(ProviderUnavailableError);
    },
  );
});
