from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest

from teamora_api import db
from teamora_api.errors import ApiError


class StubSession:
    rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True


class StubSessionContext:
    def __init__(self, session: StubSession) -> None:
        self.session = session

    async def __aenter__(self) -> StubSession:
        return self.session

    async def __aexit__(self, *_: object) -> None:
        return None


async def test_database_timeout_becomes_safe_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    session = StubSession()
    monkeypatch.setattr(db, "SessionFactory", lambda: StubSessionContext(session))
    dependency = cast(AsyncGenerator[Any, Any], db.get_session())
    await anext(dependency)

    with pytest.raises(ApiError) as raised:
        await dependency.athrow(TimeoutError())

    assert raised.value.status_code == 503
    assert raised.value.code == "database_unavailable"
    assert raised.value.message == "Сервис временно недоступен. Попробуйте ещё раз."
    assert session.rolled_back is True
