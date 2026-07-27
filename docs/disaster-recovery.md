# Disaster recovery

Define RPO/RTO per tenant plan before production. Back up PostgreSQL with point-in-time recovery, versioned private object storage, encrypted secret-system metadata and deployment configuration. Redis is rebuildable coordination state and is not the source of truth for calls or usage.

Backups are encrypted with keys separate from the backup location and copied off-site. A scheduled restore drill verifies database checksums, Alembic revision, RLS policies, object references, retention enforcement and credential-reference integrity. Never copy production secrets into a development restore.

Recovery order is network and secret system, PostgreSQL, object storage, API, worker, gateway/web, then telephony. Disable inbound routing until DID resolution, tenant isolation and concurrency limits pass. Reconcile provider call/CDR identifiers and idempotently rebuild missing summaries or usage.

Document the incident timeline in `SystemIncident`; customer content is excluded. Rotate potentially exposed secrets, invalidate sessions and webhook keys, and audit recording access. A disaster-recovery claim requires a timed restore drill; none has been run for this initial local implementation.
