# Asterisk setup

The local `telephony` Compose profile builds the Ubuntu 24.04 LTS package of Asterisk
20 with PJSIP, ARI, DID routing, queue policy, CDR, recording volume and a bounded RTP
range. It is excluded from the default startup.

```powershell
docker compose --profile telephony config
docker compose --profile telephony build asterisk
docker compose --profile telephony up asterisk
docker compose exec asterisk asterisk -rx "pjsip show registrations"
docker compose exec asterisk asterisk -rx "ari show status"
docker compose exec asterisk asterisk -rx "queue show teamora-support"
```

Templates are rendered at container start so secrets do not enter image layers or Git. Startup fails when required variables are missing. `SIP_ALLOWED_IPS` must be an actual provider allowlist rather than `0.0.0.0/0`.

ARI External Media supports an RTP fallback. Media-over-WebSocket requires an Asterisk build that includes the relevant WebSocket channel/client support; confirm loaded modules and the exact version before selecting `ASTERISK_EXTERNAL_MEDIA_TRANSPORT=websocket`. The repository configuration is architectural preparation, not an end-to-end media verification.

Production requirements include private ARI, TLS, immutable image pinning, certificate rotation, provider IP allowlists, OS hardening, fail2ban, monitored CDR anomalies, a backup carrier route and a tested highly available deployment topology.

Two deployment modes are supported through configuration:

- `TELEPHONY_SIP_ARCHITECTURE=direct`: the provider is officially permitted to
  terminate SIP/RTP on the Contabo public IP. Asterisk and Voice Gateway stay
  co-located there.
- `TELEPHONY_SIP_ARCHITECTURE=uz_edge`: provider SIP/RTP terminates on an
  Uzbekistan host. Asterisk and the media-facing Voice Gateway are co-located on
  that edge, while FastAPI/PostgreSQL remain on the platform host and are reached
  only through a restricted WireGuard tunnel.

`ASTERISK_EXTERNAL_SIGNALING_ADDRESS` and
`ASTERISK_EXTERNAL_MEDIA_ADDRESS` may differ; the legacy
`ASTERISK_PUBLIC_ADDRESS` is only a compatibility fallback. Startup rejects a
catch-all SIP provider allowlist, an inverted or excessively broad RTP range, an
unknown transport, and an edge topology without an enabled private tunnel.

See [Stage 17 live readiness](live-stage17-readiness.md) for the topology,
firewall boundaries and exact live test gates. Neither configuration is a claim of
Sarkor or UZTELECOM compatibility without provider confirmation and live RTP.
