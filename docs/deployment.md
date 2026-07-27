# Deployment

Docker Compose is the deployment unit for the MVP. Kubernetes is intentionally excluded.

1. Pin and scan every base image; never deploy mutable `latest` tags in a release manifest.
2. Provide secrets from the environment/Vault/KMS, validate TLS certificates and keep data/voice networks private.
3. Back up PostgreSQL and object storage and verify restore metadata before migration.
4. Run `alembic check`, test upgrade/downgrade on a restored copy, then apply with the migration owner.
5. Start PostgreSQL, Redis and object storage; wait for health.
6. Start API and worker; check liveness, readiness and protected endpoint behavior.
7. Start web and gateway; keep realtime unavailable if provider verification has not passed.
8. Start the optional telephony profile only after firewall and provider allowlists are active.
9. Run external TLS smoke checks, tenant isolation, webhook replay, recording access and graceful-shutdown canaries.

Nginx is the only public HTTP entry. PostgreSQL, Redis, MinIO, ARI and media ports remain private. The supplied Compose port bindings are for local development and are not a hardened production firewall.

Rollback uses an immutable previous image and a database-compatible migration plan. Never bypass a failed migration or RLS check. Health alone is not evidence that OpenAI, SIP, CRM or payments work end to end.
