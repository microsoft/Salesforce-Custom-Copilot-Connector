from __future__ import annotations

from ..common import TENANT_ID


def public_acl(tenant_id: str = TENANT_ID) -> list[dict[str, str]]:
    return [{"accessType": "grant", "type": "everyone", "value": tenant_id}]


def deny_acl(tenant_id: str = TENANT_ID) -> list[dict[str, str]]:
    return [{"accessType": "deny", "type": "everyone", "value": tenant_id}]


def user_acl(*guids: str) -> list[dict[str, str]]:
    return [{"accessType": "grant", "type": "user", "value": guid} for guid in guids]