# Database schema

## Ownership model

The initial Alembic migration creates the requested domain model as 58 tables. Global roots are deliberately limited to `tenants`, `users`, `roles`, `permissions`, `plans`, and `system_incidents`. Every other business table is tenant-owned and contains a non-null `tenant_id`, `created_at`, and `updated_at`.

Tenant-owned groups:

- projects: projects and project-user assignments;

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

## Project boundary and Dialer leases

`projects` belongs to a tenant and has a unique tenant/name pair, one default project,
working-hours configuration, an outbound number, and a positive concurrent-call limit.
`project_users` can reference only a user with a membership in the same tenant.

`customers`, `customer_contacts`, `calls`, `callback_tasks`, `ai_operators`, and
`call_flows` have a non-null project reference. Composite tenant/project foreign keys
prevent a row from pointing at another tenant's project. Customer phone uniqueness is
enforced by the partial index `uq_customer_contacts_project_phone` for `kind = 'phone'`.

`customers.lock_token`, `locked_by_user_id`, and `locked_until` form the Dialer lease.
The token must be presented when starting a call, renewing a lease, or releasing a
customer. This prevents a stale browser tab from releasing a newer assignment.

Stage 3 extends `customers` and `customer_contacts` instead of creating a parallel CRM
domain. Customer contacts use normalized project-scoped uniqueness, while the global
`phone_numbers.e164` constraint is intentionally retained for DID/SIP inbound routing.
`customer_field_definitions` owns typed project field definitions, and
`customer_imports` stores short-lived preview state plus an idempotent commit report.
Archived customers retain their contacts and call history but are excluded from Dialer
assignment.
