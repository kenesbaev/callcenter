# Stage 17 live readiness

Review date: **2026-08-12**. This document is configuration and test preparation;
it is not a record of a live SIP, OpenAI or browser transfer verification.

## Chosen boundaries

### Direct provider

```text
provider SIP/RTP -> Contabo Asterisk -> co-located Voice Gateway -> OpenAI
                                      -> private FastAPI service API
```

Use this only after the provider confirms that its trunk accepts the Contabo public
IP. Provider IP ranges are the only inbound SIP sources. ARI and the Gateway
internal API remain on private Docker networks. The public surface is limited to
provider SIP, the configured RTP range, HTTPS and the browser SIP WebSocket route.

### Uzbekistan telephony edge

```text
provider SIP/RTP -> Uzbekistan Asterisk -> edge Voice Gateway -> OpenAI
                                           |
                                           +-- WireGuard -- Contabo FastAPI
```

The recommended Stage 17 placement is to co-locate the media-facing Voice Gateway
with Asterisk on the Uzbekistan edge. This avoids carrying RTP/UDP across the
Uzbekistan-to-Contabo WAN and therefore reduces jitter, loss and orphaned media
state. The Gateway keeps one controlled PCMU/PCM conversion boundary and uses the
WireGuard tunnel for authenticated FastAPI commands/events. Its outbound OpenAI
WebSocket path must be measured from that edge before production approval. If that
path is poor, changing placement is an explicit architecture decision followed by
new RTP loss/latency tests; it is not an automatic fallback.

ARI binds only to the edge private container network. It is never routed through a
public reverse proxy. Browser SIP WSS may terminate on an edge TLS proxy or be
strictly proxied to the edge over WireGuard; this public SIP WebSocket must not
expose ARI or the Gateway internal API.

## Edge tunnel template

The examples in `infrastructure/telephony-edge/` contain placeholders only. Use
separate generated WireGuard key pairs in the host secret store. Restrict
`AllowedIPs` to tunnel /32 addresses and only the exact platform service route.
Do not route provider SIP or RTP through Contabo in edge mode.

Minimum boundaries:

- provider SIP: provider ranges -> edge SIP port only;
- provider RTP: provider ranges -> explicit edge RTP range only;
- browser WSS: HTTPS/WSS TLS endpoint only;
- browser DTLS-SRTP/ICE: separately documented public media range;
- WireGuard: platform peer IP -> tunnel UDP port;
- ARI/AMI: no public rule; AMI remains disabled;
- internal API: tunnel source plus service token/signature only;
- SSH/administration: named administrative sources only.

Registration auth and IP auth are both supported. A public IP change requires DNS,
NAT address and firewall updates; IP-auth trunks also require provider-side range
changes. No arbitrary originate is exposed. Controlled outbound diagnostics require
the existing E.164 allowlist, feature gate, owner confirmation and channel lease.

## NAT and media

Set provider transport, codec and DTMF mode only from confirmed provider data.
Configure `ASTERISK_EXTERNAL_SIGNALING_ADDRESS`,
`ASTERISK_EXTERNAL_MEDIA_ADDRESS`, `ASTERISK_LOCAL_NET`, symmetric RTP, rport and
`direct_media=no`. Keep the RTP interval as narrow as capacity permits; container
startup caps it at 1,000 ports. A successful REGISTER, OPTIONS or SIP 200 does not
verify audio. Live audio needs packet counters and intelligible RTP in both
directions, negotiated codec, DTMF, recording and cleanup evidence.

## WebRTC production gate

Production browser transfer requires a real domain and trusted certificate:

- HTTPS and `wss://.../sip-ws` with same-origin policy;
- successful WebSocket Upgrade without mixed content;
- one-time credentials with the existing 30-120 second TTL;
- DTLS-SRTP, ICE, microphone permission and device selection;
- answer, DTMF, mute, hold/resume, hangup and reconnect;
- no SIP password in application state, logs or diagnostics.

Local HTTP/WS tests do not satisfy this gate. The safe readiness command
`python scripts/check_live_readiness.py` prints booleans and statuses only; it does
not print keys, credentials, phone numbers, DIDs or service URLs.

## Live sequence and acceptance

1. Confirm provider/host compatibility, IP ranges, transport, codecs, DTMF, DID,
   channel semantics and an allowlisted test number.
2. Confirm firewall/NAT without changing it from application code.
3. Verify registration or reachability and OPTIONS.
4. Verify inbound signaling, then outbound diagnostic signaling separately.
5. Verify inbound and outbound RTP, codec, loss, jitter, DTMF, recording and CDR.
6. Run the separately authorized bounded OpenAI canary using synthetic audio.
7. Verify browser TLS/WSS and DTLS-SRTP on the real domain.
8. Run AI -> queue -> atomic claim -> operator bridge, keeping the AI leg until
   the operator answer is confirmed.
9. Verify mobile transfer only with separate authorization and a second channel
   reservation where required.
10. Confirm zero sessions, channels, bridges, reservations and orphan attempts.

Current external status remains **IMPLEMENTED LOCALLY — LIVE SIP, OPENAI AND
TRANSFER VERIFICATION REQUIRED**.
