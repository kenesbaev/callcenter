# Live operator handoff (Stage 17)

Verified against the official OpenAI Realtime SIP transfer guide on 2026-08-11:
<https://developers.openai.com/api/docs/guides/realtime-sip>.

K-Line currently uses Asterisk External Media rather than OpenAI SIP mode. The
customer channel remains in the existing Asterisk mixing bridge while the
operator leg is dialled. The Voice Gateway pauses the AI session, cancels and
truncates pending playback, and starts safe music-on-hold. Only an answered
operator channel entering the Stasis application is added to the bridge. After
that succeeds, External Media and the AI session are removed. Recording remains
on the same bridge, so the Call, CDR and recording identity are continuous.

If a future provider adapter uses OpenAI SIP mode, it may send
`POST /v1/realtime/calls/{call_id}/refer`. A `200` response only confirms that
OpenAI relayed SIP REFER to the downstream SIP provider. It is not a successful
handoff signal. K-Line must still wait for Asterisk/provider channel events
before setting the canonical Call state to `transferred`.

## State and ownership

| Operation                        | Canonical Call state                         | Transfer state        |
| -------------------------------- | -------------------------------------------- | --------------------- |
| AI requests a person             | `active → transfer_requested`                | `requested/offered`   |
| One operator claims              | `transfer_requested → transferring`          | `claimed`             |
| Operator leg starts              | `transferring`                               | `connecting`          |
| Answered leg joins bridge        | `transferring → transferred`                 | `connected`           |
| Attempt fails                    | `transferring → active → transfer_requested` | next offer            |
| Attempts exhausted, AI available | `transferring/transfer_requested → active`   | `failed`              |
| AI unavailable                   | `transfer_requested/transferring → failed`   | callback task created |

`SELECT ... FOR UPDATE`, a per-Call PostgreSQL advisory transaction lock,
optimistic `lock_version`, unique idempotency keys and one active attempt make
claim and retry deterministic. Delivery of realtime notifications remains at
least once; REST/PostgreSQL is canonical.

## Routing

Candidates must belong to the same tenant and project, have an active
membership with Dialer access, a current heartbeat, manual/effective status
`available`, no capacity Call, and `is_transfer_available=true`. Matching the
Call language is preferred. Configurable strategies are `longest_idle`,
`round_robin` and `priority`: the first uses the oldest effective idle time,
round-robin uses a Call-scoped deterministic rotation, and priority uses
language plus stable membership seniority. Every retry excludes prior attempts.

SIP extensions and E.164 mobile destinations are server-side allowlist records.
Changing a destination clears its verification flag. A mobile transfer cannot
start without a verified endpoint and may consume an additional provider
channel; production verification must confirm the provider's shared/separate
channel-pool semantics.

## WebRTC boundary

The API generates a random, single-operator credential and sends it over the
private service-authenticated Gateway API. The Gateway creates an ephemeral
PJSIP auth/AOR/endpoint through ARI push configuration and revokes it after at
most 120 seconds. Only the one-time password, WSS URL and operator SIP URI are
returned to that authenticated operator. No long-lived trunk, ARI or OpenAI
credential reaches the browser, logs or PostgreSQL.

Asterisk maps dynamic PJSIP objects to its private AstDB through `sorcery.conf`;
the Gateway removes leftover `operator-<membership UUID>` endpoints during
startup reconciliation. Static provider trunks remain in `pjsip.conf`. Browser
endpoints use the `transport-webrtc` WebSocket transport, `webrtc=yes`,
DTLS-SRTP, `direct_media=no`, symmetric RTP and receive-only dialplan context.
The operator can select an enumerated microphone and, where the browser exposes
`setSinkId`, a speaker. SIP.js performs bounded reconnect attempts and the UI
can instead choose a verified internal SIP phone or masked mobile destination.
Nginx exposes only `/sip-ws`; ARI remains private. Production must terminate TLS
at Nginx so the browser uses secure WSS. The repository validates the dynamic
contract and local emulator path, while real browser registration and a real
mobile destination remain explicit live verification gates.

## Cleanup

Before an operator is connected, decline, timeout, ARI failure or Gateway
restart stops MOH, removes only the attempted operator leg and resumes the same
AI session. After connection, operator hangup follows normal Call cleanup. All
paths are expected to leave zero AI sessions, channels, bridges and extra
reservations; reconciliation remains the recovery mechanism after process
restart.
