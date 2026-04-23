"""
acl_engine
----------
Salesforce ACL resolution engine – rewritten from scratch.

Public surface
--------------
    AclResolver   – main entry point (resolve / resolve_async)
    SalesforceClient – Salesforce REST client
    AclResult     – result dataclass
    PUBLIC_SENTINEL – sentinel value for org-wide access

Quick start
-----------
::
    from acl_engine import AclResolver, SalesforceClient

    client = SalesforceClient(
        instance_url="https://myorg.my.salesforce.com",
        api_version="60.0",
        access_token="<Bearer token>",
    )
    resolver = AclResolver(client)

    result = resolver.resolve("Account", "001xxxxxxxxxxxx")

    if result.is_public:
        # Grant access to the entire tenant
        ...
    else:
        # result.user_ids is a set of Salesforce User Ids
        for user_id in result.user_ids:
            ...

Module layout
-------------
    resolver.py        – Step 1: AclResolver (orchestrator)
    owd.py             – Step 2: OWDFetcher
    share_fetcher.py   – Step 3.1: ShareFetcher
    user_handler.py    – Step 3.2: UserHandler
    group_handler.py   – Step 3.3.0: GroupHandler (dispatcher)
    role_handler.py    – Step 3.3.1: RoleHandler
    territory_handler.py – Step 3.3.2: TerritoryHandler
    queue_handler.py   – Step 3.3.3: QueueHandler
    sf_client.py       – Salesforce REST client
    models.py          – Shared data classes and enums
"""

from acl_engine.resolver import AclResolver
from acl_engine.salesforce_client import SalesforceClient
from acl_engine.models import AclResult, PUBLIC_SENTINEL
from acl_engine.principal_mapper import PrincipalMapper

__all__ = [
    "AclResolver",
    "SalesforceClient",
    "AclResult",
    "PUBLIC_SENTINEL",
    "PrincipalMapper",
]
