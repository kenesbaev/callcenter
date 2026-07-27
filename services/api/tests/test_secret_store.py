from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from teamora_api.secret_store import EncryptedLocalSecretStore, SecretStoreError


@pytest.mark.asyncio
async def test_local_secret_store_encrypts_and_enforces_tenant() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    path = Path(".test-artifacts") / f"secrets-{tenant_a}.enc"
    store = EncryptedLocalSecretStore(path, Fernet.generate_key().decode())
    try:
        reference = await store.put(tenant_id=tenant_a, kind="sip_password", value="private-value")
        assert b"private-value" not in path.read_bytes()
        assert await store.resolve(tenant_id=tenant_a, reference=reference) == "private-value"
        with pytest.raises(SecretStoreError):
            await store.resolve(tenant_id=tenant_b, reference=reference)
    finally:
        path.unlink(missing_ok=True)
