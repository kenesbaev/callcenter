# SIP, Asterisk ARI, and RTP boundary

Stage 15 keeps PostgreSQL and FastAPI as the source of truth. Asterisk owns SIP
signalling and media switching; Voice Gateway owns provider-specific ARI and RTP
I/O. Neither Asterisk nor Voice Gateway writes `Call.status` directly.

```text
SIP provider / local emulator
        | PJSIP + RTP
        v
 Asterisk 20 (private ARI)
        | ARI WebSocket/events          | External Media RTP
        v                               v
 Voice Gateway -----------------> diagnostic media adapter
        | signed, idempotent provider events
        v
 FastAPI -> CallStateService -> PostgreSQL/CallEvent/realtime outbox
```

## Configuration and secrets

Provider values are supplied through the existing secret/configuration layer.
They are never accepted from the browser. Required live inputs are:

- SIP host and port;
- transport (`udp`, `tcp`, or `tls`);
- authentication mode (`registration` or `ip`);
- DID and the provider-approved outbound caller ID;
- username/password for registration, or provider IP/CIDR allowlist for IP auth;
- provider-approved codecs and DTMF mode;
- exact inbound/outbound permissions and channel limits;
- one explicitly allowed E.164 diagnostic destination.

`SIP_AUTH_PASSWORD`, `ASTERISK_ARI_PASSWORD`, and `GATEWAY_SERVICE_TOKEN` must be
environment/secret values. Diagnostics, CDRs, audits, API responses, and logs
must not include them. Live dialling is disabled unless
`TELEPHONY_LIVE_DIAGNOSTIC_ENABLED=true`, the number is in
`TELEPHONY_LIVE_TEST_NUMBER_ALLOWLIST`, its prefix is allowed, and an owner sends
the exact confirmation phrase. The normal Dialer cannot originate through a
live trunk until its separate flag is enabled after acceptance testing.

## PJSIP modes

The Asterisk entrypoint renders one PJSIP endpoint from configuration.
`chan_sip` and AMI are disabled.

Registration authentication renders `registration`, `auth`, `aor`, `endpoint`,
and `identify` sections. IP authentication renders `aor`, `endpoint`, and
`identify` only; it does not create a fake registration. `identify` is populated
only with configured provider IPs or CIDRs. Anonymous endpoint matching is not
enabled.

Both modes use `direct_media=no`, symmetric RTP, `force_rport`, and contact
rewriting so that media remains observable through Asterisk. Codecs and DTMF are
not provider assumptions: the values in the local profile (`ulaw`, RFC4733) are
test-fixture values only. A live report may name a negotiated codec only after it
has been observed in SDP/RTP.

## NAT and Contabo preparation

Before a live test, set the public IPv4 as the external signalling and media
address and list only real container/private networks in `local_net`. Confirm
Contact, Via, received/rport and SDP connection addresses from a redacted trace.

Firewall work is deliberately not automated. After official provider IP ranges
are known, the expected allowlist is:

| Port                                        | Exposure                         | Purpose              |
| ------------------------------------------- | -------------------------------- | -------------------- |
| configured SIP UDP/TCP port (commonly 5060) | provider IPs only                | signalling           |
| configured SIP TLS port (commonly 5061)     | provider IPs only                | TLS signalling       |
| configured narrow UDP RTP range             | provider media IPs only          | phone audio          |
| 8088/TCP                                    | private `voice` network only     | ARI HTTP/WebSocket   |
| 8787/TCP                                    | private application network only | Gateway internal API |
| public application port                     | through Nginx only               | user API/UI          |

Do not expose ARI or the Gateway internal API directly to the Internet.

## Inbound routing

1. PJSIP accepts an INVITE only from an identified endpoint.
2. The dialplan sends normalized DID and remote address to the Stasis app.
3. Voice Gateway sends a signed inbound event to the narrow backend resolver.
4. The resolver performs an exact active-DID lookup, establishes tenant context,
   and then checks project status, working hours, tenant/project/channel limits,
   provider source CIDR, and recording policy.
5. FastAPI creates the `Call`, pins published AI operator, call-flow, and knowledge
   revisions, reserves a channel, and moves it to `ringing` only through
   `CallStateService`.
6. Gateway creates one mixing/proxy-media bridge and one External Media channel,
   answers, and emits normalized provider events.

Unknown DIDs and spoofed provider sources are rejected without revealing tenant
data. Duplicate provider event IDs and channel IDs are idempotent.

## ARI listener and resources

The listener authenticates using an HTTP Authorization header (never a query
parameter), reconnects with bounded exponential backoff and jitter, serializes
event handling, and maps channel/bridge/recording IDs to the backend call. It
normalizes Stasis, channel-state, DTMF, recording, hangup, bridge, and External
Media events. Provider timestamps cannot move a terminal call backward.

`telephony_resources` persists channel, bridge, External Media, and recording
lifecycle. Repeated events upsert the same provider resource. Cleanup is safe to
repeat and releases media channels/bridges on BYE, Stasis end, or reconciliation.

## RTP diagnostic proof

OpenAI is not involved. The local adapter accepts RTP version 2 with PCMU (payload 0) or PCMA (payload 8), counts packets/bytes, and echoes valid packets to the
observed remote address. The local media endpoint generates a bounded packet set
and passes only when both ingress and egress counters agree.

The full local SIP harness advertises a client RTP socket, sends deterministic
PCMU packets after ACK, and succeeds only after packets return through:

```text
SIP client -> Asterisk customer channel -> bridge -> External Media
 -> Gateway echo -> External Media -> bridge -> Asterisk -> SIP client
```

It also sends an RFC4733 digit and performs BYE cleanup. This proves local
two-way RTP transport; it does not prove a public provider route, codec quality,
NAT correctness, or live audio.

## Recording and CDR

Recording starts only when project/tenant policy allows it. If disclosure is
required, automatic recording remains blocked until a real disclosure prompt is
available. Asterisk records the deterministic mixing bridge so the customer and
External Media directions are captured together, and writes a server-generated
WAV name. The existing durable
background-job system validates that name, computes SHA-256 and duration, uploads
to the private MinIO bucket with server-side encryption, and applies retention
and legal-hold rules.

Canonical CDRs keep masked DID/destination, timestamps, disposition, normalized
hangup cause, observed codec, duration, and channel use. Provider billable time
and telephony cost remain null unless a trusted provider supplies them.

## Channel capacity

Reservations use a PostgreSQL advisory transaction lock and a unique call
reservation. Shared or separate inbound/outbound pools are configuration. A
terminal state releases capacity transactionally; retries are idempotent. The
local test uses the common assumption that one channel is one simultaneous phone
call. This must be confirmed against the provider contract. A future external
transfer may consume a second channel and belongs to Stage 17.

## Local reproducible verification

Use non-production values in a local `.env`, set a reserved test DID, then run:

```powershell
docker compose -f docker-compose.yml -f infrastructure/telephony-test/docker-compose.yml --profile telephony-test config
docker compose -f docker-compose.yml -f infrastructure/telephony-test/docker-compose.yml --profile telephony-test up -d --build
docker compose -f docker-compose.yml -f infrastructure/telephony-test/docker-compose.yml exec sip-emulator python sip_emulator.py inbound --target asterisk:5060 --did <local-test-did>
```

Run it twice. Each run must show signalling, two-way RTP, DTMF/BYE, a terminal
backend call, a masked CDR, released reservation, and no remaining Teamora
channel/bridge. Busy, no-answer, reject, cancel, invalid DID, spoofed source,
codec mismatch, retransmission, capacity N+1, and restart/reconciliation are
contract scenarios in the same local profile.

## Live acceptance gate

No live call is allowed until credentials are installed through a secure channel,
provider IP/firewall rules are confirmed, the owner approves the exact test
number, and the controlled-live flags are enabled. Verification order is:

1. registration or IP reachability and OPTIONS qualify;
2. inbound INVITE and backend Call creation;
3. inbound RTP observed at Gateway;
4. returned test audio heard/observed at the SIP endpoint;
5. DTMF, BYE, CDR, recording policy, and cleanup;
6. one allowlisted diagnostic outbound call, then one repeat.

Status meanings are intentionally strict: `configured` is configuration only,
`local_test_passed` is local RTP only, `live_signaling_verified` requires a real
provider event from a real dialog, and `live_audio_verified` requires RTP proof
in both directions. Successful acceptance of an originate command by the internal
ARI API leaves the diagnostic pending; it is not signaling proof by itself.
Without provider credentials the release status remains
`IMPLEMENTED — LIVE SIP VERIFICATION REQUIRED`.
