import pytest

from teamora_worker.providers import AMOCRM, BITRIX24, GOOGLE_SHEETS, ProviderUnavailableError


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [BITRIX24, AMOCRM, GOOGLE_SHEETS])
async def test_placeholder_crm_adapters_fail_explicitly(provider: object) -> None:
    assert provider.status == "unavailable"
    with pytest.raises(ProviderUnavailableError):
        await provider.execute("create_lead", {}, "safe-idempotency")
