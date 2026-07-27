# Data retention and privacy

Tenant settings control recording enablement and retention days within platform bounds. Every AI greeting discloses the virtual assistant and possible recording. A call may pause and resume recording for payment data, authentication codes or other sensitive fragments; these controls require an audit event.

Recordings are encrypted by the storage platform, tenant-prefixed and private. The database stores metadata and an object key, not a public URL. Playback produces a short-lived signed URL only after content permission and access audit.

The worker selects expired objects by tenant and policy, records an idempotent deletion job, deletes storage, then marks metadata deleted. Safe retries reuse the same key. Failures go to a dead-letter queue and alert operations. Legal holds override automated deletion through a separately audited permission.

Data-subject deletion first identifies all tenant-owned customer references. Depending on legal policy, data is deleted or anonymized: phone/email values are replaced with irreversible tenant-salted tokens, free text is removed, recordings are deleted and aggregate usage is retained without identity. Audit records preserve the action but not erased content.

Backups inherit retention and encryption controls. A restore must not resurrect data beyond the approved recovery window; expired content is reprocessed immediately after recovery.
