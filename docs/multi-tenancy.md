# Multi-tenancy

Tenant isolation uses two independent controls: explicit application predicates and PostgreSQL Row-Level Security. Both are mandatory.

For dashboard requests, the signed access token identifies a membership; the API loads that membership and sets the transaction-local database tenant context. A body, query string or header cannot switch tenants. Platform operations use separate permissions and audited elevation rather than disabling tenant policy in ordinary repositories.

For inbound calls, the normalized DID selects one active phone-number record and tenant before any business data is loaded. A missing or duplicate mapping fails closed. The resulting call, Redis keys, knowledge search, tool permission, CRM credential reference and object-storage prefix all carry the resolved tenant.

Object keys have the form `tenants/{tenant_id}/...` and are private. Signed URLs are short-lived and issued only after RBAC plus audit checks. Redis keys include the tenant and call identifiers. Metrics may carry tenant IDs only where cardinality and privacy policy permit; transcript and phone content are excluded.

The integration test creates a call for company A, authenticates as company B, expects 404, verifies the response contains no A data, and confirms the app database role sees the row only when its RLS context is A.
