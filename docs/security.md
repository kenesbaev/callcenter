# Security baseline

Implemented foundations include Argon2 password hashing, short-lived JWT access cookies, opaque hashed rotating refresh tokens, HttpOnly cookies, SameSite policy, CSRF double-submit validation, CORS allowlist, trusted hosts, CSP at Nginx, structured redacted logs, RBAC, tenant RLS, prepared ORM statements, correlation IDs, webhook HMAC/timestamp checks and idempotency storage.

Secret columns contain references only. Local development uses an encrypted local secret-store adapter; staging and production must provide Vault/KMS. Secret values must never be returned to the model, frontend, audit metadata or log processor.

Files require content-type and signature validation, maximum size, tenant prefix, private storage and an antivirus adapter before ingestion. Public object ACLs are prohibited. Recording playback requires a short signed URL and an audit action.

The model cannot perform arbitrary SQL, HTTP or shell operations. Each tool has a server-owned schema, timeout, permission, tenant context, minimal response, idempotency key and audit record. Price, discount, order and payment mutation need deterministic domain authorization.

Before production: replace the development rate limiter with Redis-backed per-tenant and per-route limits, complete platform-admin separation, add SSRF/DNS-rebinding defense for configured webhooks, run SAST/dependency/container audits, threat-model SIP fraud, conduct penetration and load testing, verify TLS everywhere, and remove all default credentials. This codebase is not yet production-ready.
