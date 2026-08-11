# OpenAI Realtime Voice AI

Contract review date: **2026-08-05**. K-Line uses the current server-to-server
WebSocket contract documented by OpenAI, not the retired preview contract:

- [Realtime API](https://developers.openai.com/api/docs/guides/realtime)
- [Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations)
- [Voice activity detection](https://developers.openai.com/api/docs/guides/realtime-vad)
- [Realtime model prompting](https://developers.openai.com/api/docs/guides/realtime-models-prompting)
- [Voice agents](https://developers.openai.com/api/docs/guides/voice-agents)
- [Realtime WebSocket](https://developers.openai.com/api/docs/guides/realtime-websocket)

## Boundary and configuration

The media path is:

`SIP/RTP → Asterisk External Media → Voice Gateway → Realtime provider → Voice Gateway → Asterisk`.

`RealtimeVoiceProvider` has deterministic Mock and OpenAI implementations. The
Gateway owns the provider WebSocket, audio buffering and interruption accounting.
FastAPI owns tenant/project authorization, pinned versions, tools, transcripts,
usage and audit. Neither a browser nor Asterisk receives the OpenAI key.

The OpenAI provider connects to `wss://api.openai.com/v1/realtime?model=...` with
server-side Bearer authorization. It sends `session.update`, appends base64 PCM
through `input_audio_buffer.append`, receives audio from
`response.output_audio.delta`, and dispatches function arguments only after
`response.function_call_arguments.done`.

Configuration is centralized:

- `OPENAI_REALTIME_ENABLED`
- `OPENAI_REALTIME_PROVIDER=mock|openai`
- `OPENAI_REALTIME_MODEL`
- `OPENAI_REALTIME_MODEL_ALLOWLIST`
- `OPENAI_REALTIME_VOICE`
- `OPENAI_REALTIME_REASONING_EFFORT`
- VAD, timeout, queue and message-size settings
- `OPENAI_API_KEY` only in server secrets

The default configured model is `gpt-realtime-2.1`, but startup rejects a model
outside the explicit allowlist. No silent fallback occurs. A configured model is
not considered account-access verified until a separately authorized live canary.

## Session and audio lifecycle

Each Call has at most one durable `AIRealtimeSession`. It pins the published AI
Operator, Call Flow and Knowledge revision and uses the lifecycle:

`pending → connecting → active ↔ reconnecting/degraded → closing → closed|failed`.

Stage 15 supplies PCMU/8 kHz RTP. The single controlled conversion boundary in
the Gateway converts mono PCMU/8 kHz to signed PCM16LE/24 kHz for Realtime and
back to PCMU/8 kHz for RTP. Sequence numbers, bounded queues, packet/frame counts,
clipping, dropped frames and queue delay are diagnostic signals. Echo/test mode
remains available independently of OpenAI.

Server VAD is the default, with configurable threshold, prefix padding, silence
duration and idle timeout. Semantic VAD is sent only when explicitly selected and
supported by the configured contract/model.

For barge-in the Gateway clears unsent playback immediately, sends
`response.cancel`, and then `conversation.item.truncate` with the actually played
audio duration. Duplicate/stale provider events are deduplicated by provider event
ID and session version. Old output frames are never replayed after interruption.

## Prompt, language and tools

The prompt is built only from pinned published versions and contains concise voice
rules, one-question-at-a-time behavior, language, knowledge boundaries, tool
confirmation and escalation. Retrieved documents are explicitly untrusted data,
not instructions. The model cannot change tenant/project or access PostgreSQL,
Redis, MinIO, SIP credentials, arbitrary HTTP or a shell.

Language codes are normalized lowercase BCP 47 codes. Supported initial paths are
`ru`, `uz`, `en`, `kaa`, `kaa-latn` and `kaa-cyrl`; `ka` remains Georgian. KAA
voice quality is **not** considered production verified without live voice evals.

Typed tools are enabled per published operator. Every call is tenant/project/Call
authorized, schema checked, timeout bounded, audited and idempotent. Available
definitions include knowledge search, customer read, confirmed customer/task/
callback/result actions, transfer request and conversation end. A transfer tool
creates only a `TransferRequest` in Stage 16; it does not move a SIP channel.
`search_knowledge` queries only the pinned revision and returns citations or
`no_match`; the full knowledge base is never sent to the model.

## Disclosure, transcripts, usage and fallback

When policy requires it, a localized deterministic disclosure is played before
mutating tools. The event is persisted so reconnect does not skip it. The text
does not claim recording when recording is disabled.

Customer and AI transcript finals are stored separately with provider item ID,
language, interruption marker and timestamps. Partial provider deltas are for live
display and are not sufficient authorization for a mutating action.

Usage records store returned provider/model/session identifiers and actual audio,
text and cached token quantities. Cost is `unavailable` unless a confirmed pricing
snapshot exists. SIP/DID/telephone costs are never inferred from AI usage.

Provider errors use bounded reconnect. The caller receives a local fallback prompt
instead of silence. Exhausted retries create a human transfer request (and later a
callback/task where configured), close media/session resources and preserve a safe
error code. Invalid keys, unavailable models, rate limits, timeouts, malformed
events, audio errors and tool timeouts are distinguishable without logging secrets.

## Delivery, evaluation and live status

UI updates reuse the Stage 12 transactional outbox. `ai.*` notifications contain
only Call/session IDs and state; REST remains canonical. Delivery is at least once,
so the UI deduplicates and refetches.

The deterministic eval set separates content/citation/language/tool/interruption/
latency checks from future live evaluation. The local Mock OpenAI WebSocket proves
protocol and media behavior only. It does not prove model access, speech quality,
latency SLA or real SIP interoperability.

Latency diagnostics define TTFA as the elapsed time from the final
`speech_stopped` timestamp to the first audio frame actually accepted by the
Gateway playback path. The API aggregates confirmed non-negative TTFA samples in
PostgreSQL and reports p50, p95 and p99 separately with the sample count. Missing
samples remain unavailable; local measurements are diagnostics and are not an SLA.

Current status: **IMPLEMENTED — LIVE OPENAI VERIFICATION REQUIRED**. A live test
requires a securely configured key, confirmed model access, explicit permission to
spend a capped test budget and local test audio. No live customer number is used.
The complete product path remains blocked until both live OpenAI and real provider
SIP audio are verified end to end.
