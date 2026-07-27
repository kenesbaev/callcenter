# SIP provider setup

## Provider values

Obtain the registrar/proxy host, port, transport, authentication identity, DID format, provider signaling CIDRs, codec list and concurrent-channel limit. Put values only in the deployment secret system. The repository contains variable names and templates, not credentials.

Normalize inbound DIDs to E.164 before resolving `PhoneNumber`. Unknown, disabled or ambiguous DIDs must be rejected without creating a tenant-scoped call.

## Network security

- Allow SIP signaling only from provider CIDRs and the operational management network.
- Allow the negotiated RTP range only from provider media CIDRs. The Compose example binds telephony ports to loopback and is not an internet deployment.
- Prefer SIP TLS and SRTP when the provider supports them. Manage certificates outside the image.
- Keep ARI and media WebSockets on the private voice network with TLS/mTLS at the infrastructure edge.
- Apply provider and tenant channel limits; alert on bursts, international destinations and abnormal duration.
- Run fail2ban for repeated authentication failures and keep anonymous endpoints disabled.
- Restrict outbound dialing by country, prefix, time and tenant policy to reduce toll fraud.

## Acceptance test

Use a dedicated non-production DID. Capture correlation ID, Asterisk unique ID and call ID without logging audio or transcript. Verify separately: inbound ringing, answer, customer-to-AI audio, AI-to-customer audio, DTMF if enabled, barge-in, clean hangup, human queue transfer, no-operator fallback, CDR, recording consent/pause, and channel-limit rejection.

No SIP test has been performed in this repository because a provider and public network path are absent.

## NAT troubleshooting

Check `external_signaling_address`, `external_media_address`, `local_net`, Contact rewriting, symmetric RTP and provider-advertised media address. A one-way-audio diagnosis must compare the SDP addresses, firewall counters and an RTP capture on both sides; an established SIP dialog alone is not audio proof.
