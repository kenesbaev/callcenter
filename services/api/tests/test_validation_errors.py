import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_validation_uses_consistent_error_envelope(client: AsyncClient) -> None:
    response = await client.post("/api/v1/auth/register", json={"email": "not-an-email"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["correlation_id"]
