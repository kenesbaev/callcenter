from __future__ import annotations

from fastapi import APIRouter

from teamora_api.dependencies import PrincipalDep

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("/current")
async def current_tenant(principal: PrincipalDep) -> dict[str, object]:
    return {
        "id": principal.tenant_id,
        "name": principal.tenant_name,
        "slug": principal.tenant_slug,
        "role": principal.role,
    }
