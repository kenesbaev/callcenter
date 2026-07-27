# OpenAI Realtime adapter

The first realtime adapter is behind `RealtimeVoiceProvider`. The model is configured through `OPENAI_REALTIME_MODEL` and defaults to `gpt-realtime-2.1-mini`; platform settings may override an approved model later.

The gateway uses the released server-to-server WebSocket endpoint with bearer authorization. It does not send the removed beta header. Session configuration enables server VAD, interruption, PCM input/output and only the operator's registered function schemas.

Tool requests pass through the gateway allowlist and JSON Schema validator, then to a server-side tenant-authorized dispatcher with timeout and idempotency. The model never receives database access, a general HTTP client, a shell, secret values or a destination URL. Financial and pricing changes require separate deterministic server checks.

Reconnect is bounded. Audio frames are not retried blindly because duplication is unsafe; configuration reads and idempotent control events may be retried. Logs include call and tenant identifiers but redact authorization, audio and transcript content.

Without `OPENAI_API_KEY`, the adapter is `unavailable` while the development simulator remains usable. Credentials alone mean configured, not end-to-end verified. A production gate requires a real audio canary, disclosure check, tool authorization tests, latency/error measurements, barge-in and handoff verification.

Primary references: [Realtime WebSocket](https://developers.openai.com/api/docs/guides/realtime-websocket), [server controls](https://developers.openai.com/api/docs/guides/realtime-server-controls), and [webhook verification](https://developers.openai.com/api/docs/guides/webhooks).
