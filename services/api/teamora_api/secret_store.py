from __future__ import annotations

import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken


class SecretStoreError(RuntimeError):
    pass


class SecretStore(Protocol):
    async def put(self, *, tenant_id: UUID, kind: str, value: str) -> str: ...

    async def resolve(self, *, tenant_id: UUID, reference: str) -> str: ...

    async def delete(self, *, tenant_id: UUID, reference: str) -> None: ...


class EncryptedLocalSecretStore:
    """Development-only encrypted file store. Production config rejects this backend."""

    def __init__(self, path: Path, encryption_key: str) -> None:
        self._path = path.resolve()
        try:
            self._fernet = Fernet(encryption_key.encode())
        except (TypeError, ValueError) as exc:
            raise SecretStoreError("LOCAL_SECRET_ENCRYPTION_KEY must be a valid Fernet key") from exc

    async def put(self, *, tenant_id: UUID, kind: str, value: str) -> str:
        if not value or not kind.replace("_", "").isalnum():
            raise SecretStoreError("Secret kind and value are required")
        secret_id = uuid4()
        records = self._load()
        records[str(secret_id)] = {"tenant_id": str(tenant_id), "kind": kind, "value": value}
        self._save(records)
        return f"local-secret://{tenant_id}/{secret_id}"

    async def resolve(self, *, tenant_id: UUID, reference: str) -> str:
        secret_id = self._parse_reference(tenant_id, reference)
        record = self._load().get(secret_id)
        if record is None or record.get("tenant_id") != str(tenant_id):
            raise SecretStoreError("Secret reference was not found")
        return record["value"]

    async def delete(self, *, tenant_id: UUID, reference: str) -> None:
        secret_id = self._parse_reference(tenant_id, reference)
        records = self._load()
        record = records.get(secret_id)
        if record is None or record.get("tenant_id") != str(tenant_id):
            raise SecretStoreError("Secret reference was not found")
        del records[secret_id]
        self._save(records)

    @staticmethod
    def _parse_reference(tenant_id: UUID, reference: str) -> str:
        prefix = f"local-secret://{tenant_id}/"
        if not reference.startswith(prefix):
            raise SecretStoreError("Secret reference is outside the tenant scope")
        secret_id = reference.removeprefix(prefix)
        try:
            UUID(secret_id)
        except ValueError as exc:
            raise SecretStoreError("Secret reference is invalid") from exc
        return secret_id

    def _load(self) -> dict[str, dict[str, str]]:
        if not self._path.exists():
            return {}
        try:
            plaintext = self._fernet.decrypt(self._path.read_bytes())
            value = json.loads(plaintext)
        except (InvalidToken, OSError, json.JSONDecodeError) as exc:
            raise SecretStoreError("Encrypted secret store could not be read") from exc
        if not isinstance(value, dict):
            raise SecretStoreError("Encrypted secret store is invalid")
        return value

    def _save(self, records: dict[str, dict[str, str]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_bytes(self._fernet.encrypt(json.dumps(records).encode()))
        with suppress(OSError):
            os.chmod(temporary, 0o600)
        temporary.replace(self._path)


class ExternalSecretStoreUnavailable:
    def __init__(self, backend: str) -> None:
        self.backend = backend

    async def put(self, *, tenant_id: UUID, kind: str, value: str) -> str:
        del tenant_id, kind, value
        raise SecretStoreError(f"{self.backend} adapter is not implemented")

    async def resolve(self, *, tenant_id: UUID, reference: str) -> str:
        del tenant_id, reference
        raise SecretStoreError(f"{self.backend} adapter is not implemented")

    async def delete(self, *, tenant_id: UUID, reference: str) -> None:
        del tenant_id, reference
        raise SecretStoreError(f"{self.backend} adapter is not implemented")
