from __future__ import annotations

from teamora_api.enums import RoleName

PERMISSIONS_BY_ROLE: dict[RoleName, frozenset[str]] = {
    RoleName.PLATFORM_ADMIN: frozenset(
        {
            "platform:read",
            "platform:manage_tenants",
            "platform:manage_limits",
            "platform:manage_incidents",
        }
    ),
    RoleName.TENANT_OWNER: frozenset(
        {
            "tenant:manage",
            "settings:read",
            "settings:manage",
            "team:read",
            "team:manage",
            "integrations:read",
            "integrations:manage",
            "sip:manage",
            "billing:read",
            "billing:manage",
            "calls:read",
            "calls:manage",
            "customers:manage",
            "projects:read",
            "projects:manage",
            "call_flows:read",
            "call_flows:manage",
            "call_results:read",
            "call_results:manage",
            "dialer:use",
            "callbacks:manage",
            "tasks:read",
            "tasks:create",
            "tasks:manage",
            "recordings:read",
            "operators:manage",
            "knowledge:manage",
            "analytics:read",
            "audit:read",
            "simulator:use",
        }
    ),
    RoleName.TENANT_MANAGER: frozenset(
        {
            "calls:read",
            "calls:manage",
            "recordings:read",
            "operators:manage",
            "knowledge:manage",
            "queues:manage",
            "customers:manage",
            "projects:read",
            "projects:manage",
            "call_flows:read",
            "call_flows:manage",
            "call_results:read",
            "call_results:manage",
            "dialer:use",
            "callbacks:manage",
            "tasks:read",
            "tasks:create",
            "tasks:manage",
            "analytics:read",
            "audit:read",
            "simulator:use",
            "settings:read",
            "team:read",
            "team:manage",
            "integrations:read",
        }
    ),
    RoleName.HUMAN_OPERATOR: frozenset(
        {
            "assigned_calls:read",
            "assigned_calls:accept",
            "customers:assigned",
            "projects:read",
            "call_flows:read",
            "call_results:read",
            "dialer:use",
            "callbacks:manage",
            "tasks:read",
            "tasks:create",
            "tasks:manage",
            "simulator:use",
            "analytics:self",
        }
    ),
    RoleName.ANALYST: frozenset(
        {
            "calls:read",
            "analytics:read",
            "projects:read",
            "call_flows:read",
            "call_results:read",
            "tasks:read",
            "team:read",
        }
    ),
    RoleName.BILLING_ADMIN: frozenset({"billing:read", "usage:read"}),
}


def role_has_permission(role: RoleName, permission: str) -> bool:
    return permission in PERMISSIONS_BY_ROLE.get(role, frozenset())
