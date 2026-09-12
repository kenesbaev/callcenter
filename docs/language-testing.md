# Language quality testing

Language support is configuration data. Initial labels are Russian and English `production target`, Uzbek `beta`, and Karakalpak `experimental`. A target label is not provider verification.

The current Voice AI Lab is an ephemeral microphone conversation with the configured
Realtime provider. It shows a live transcript and usage but does not persist raw
browser audio or claim a language-quality score. The API key remains server-side,
and the lab is protected by an off-by-default feature flag, short-lived one-use
ticket, session-count limit and hard duration limit.

A later evaluation workflow may store a separately consented audio sample reference,
expected transcript, provider/model/version, raw hypothesis, manual correction,
word/character error measures, human intelligibility score, latency, TTS naturalness
score and reviewer. Raw samples must follow tenant retention policy.

Promotion requires representative speakers, noise conditions, telephony codecs, names/numbers, code-switching, barge-in and domain vocabulary. STT is measured with WER/CER plus task completion; TTS requires native-speaker intelligibility and naturalness review. Thresholds are set before viewing results.

Azure Speech and Yandex SpeechKit adapters exist only as unavailable interfaces until credentials, regional privacy review and real quality evaluation are complete. Uzbek remains beta until that process passes. Karakalpak requires `KARAKALPAK_EXPERIMENTAL=true`, always displays `Experimental`, and cannot be promoted without separately recorded STT and TTS evidence.
