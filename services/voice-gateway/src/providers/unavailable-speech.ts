import type {
  LanguageCode,
  ProviderStatus,
  SpeechToTextProvider,
  TextToSpeechProvider,
} from "@teamora/contracts";

export class ProviderUnavailableError extends Error {
  constructor(provider: string) {
    super(
      `${provider} is unavailable until credentials and quality validation are complete`,
    );
  }
}

abstract class UnavailableSpeechAdapter {
  readonly status: ProviderStatus = "unavailable";
  protected fail(): never {
    throw new ProviderUnavailableError(this.name);
  }
  abstract readonly name: string;
}

export class AzureSpeechAdapter
  extends UnavailableSpeechAdapter
  implements SpeechToTextProvider, TextToSpeechProvider
{
  readonly name = "azure-speech";
  async transcribe(
    _input: Uint8Array,
    _language: LanguageCode,
  ): Promise<{ text: string; confidence?: number }> {
    return this.fail();
  }
  async synthesize(
    _text: string,
    _language: LanguageCode,
    _voice: string,
  ): Promise<Uint8Array> {
    return this.fail();
  }
}

export class YandexSpeechKitAdapter
  extends UnavailableSpeechAdapter
  implements SpeechToTextProvider, TextToSpeechProvider
{
  readonly name = "yandex-speechkit";
  async transcribe(
    _input: Uint8Array,
    _language: LanguageCode,
  ): Promise<{ text: string; confidence?: number }> {
    return this.fail();
  }
  async synthesize(
    _text: string,
    _language: LanguageCode,
    _voice: string,
  ): Promise<Uint8Array> {
    return this.fail();
  }
}
