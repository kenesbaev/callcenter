# Asterisk setup

The local `telephony` Compose profile builds a minimal Asterisk image with PJSIP, ARI, DID routing, queue policy, CDR, recording volume and a bounded RTP range. It is excluded from the default startup.

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

Production requirements include private ARI, TLS, immutable image pinning, certificate rotation, provider IP allowlists, OS hardening, fail2ban, monitored CDR anomalies, and a backup route. Kubernetes is intentionally out of scope for the MVP.
