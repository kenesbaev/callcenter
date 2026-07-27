# Database schema

## Ownership model

The initial Alembic migration creates the requested domain model as 58 tables. Global roots are deliberately limited to `tenants`, `users`, `roles`, `permissions`, `plans`, and `system_incidents`. Every other business table is tenant-owned and contains a non-null `tenant_id`, `created_at`, and `updated_at`.

Tenant-owned groups:

- identity: settings, memberships, refresh tokens, invitations;
- telephony: numbers, trunks, credential references, inbound and outbound routes;
- AI configuration: operators and immutable versions, voices, languages, flows and versions;
- knowledge: sources, documents, chunks and sync jobs;
- customer data: customers, contacts and notes;
- calls: calls, participants, events, transcript segments, summaries, recordings, tags and outcomes;
- human handoff: operators, statuses, queues, membership and transfer requests;
- integrations: integrations, credential references, field maps, webhook endpoints and deliveries;
- tools: definitions, permissions and executions;
- commerce and operations: usage, limits, subscriptions, invoices, payments, notifications, audit records and feature flags.

## Tenant enforcement

The application role is not a PostgreSQL superuser and must not own the tables. Every tenant-owned table has `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL SECURITY`, and a policy equivalent to:

```sql
USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
```

The API starts a transaction and sets `app.tenant_id` from the verified membership. It also includes an explicit tenant predicate in ORM queries. DID resolution is a privileged control-plane operation that resolves a tenant before switching into that tenant context; a frontend-supplied tenant ID is never authoritative.

## Sensitive data

`sip_credential_references` and `integration_credential_references` contain opaque secret references, never credentials. Recordings store a tenant-prefixed private object key. Refresh tokens are stored only as hashes. Tool executions store hashed arguments plus deliberately reduced safe arguments/results.

## Versioning and lifecycle

Published operator and call-flow versions are immutable. Each call pins its operator version so an in-flight call cannot change when a newer version is published. Transcript segments are ordered by `(tenant_id, call_id, sequence)`. Webhook delivery and usage idempotency keys are unique within a tenant.

The migration source of truth is `services/api/alembic/versions`. Schema edits require `alembic check`, upgrade, downgrade in an isolated database, and a tenant isolation test.
