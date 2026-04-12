"""
acl_engine/resolver.py
-----------------------
Step 1: Main ACL resolution interface.

This is the single public entry point for the entire ACL engine.
It orchestrates all the sub-steps and returns an AclResult for a single record.

Full pipeline
-------------
  resolve(object_type, record_id)
    │
    ├─ Step 2: OWDFetcher.get_owd(object_type)
    │           ├─ is_public?            → AclResult(is_public=True)  ← DONE
    │           ├─ is_controlled_by_parent? → recurse to parent record ← DONE
    │           └─ requires_private_acl? → continue ↓
    │
    └─ Step 3: _resolve_private_acl(object_type, record_id)
                │
                ├─ 3.1  ShareFetcher.get_owner_id()
                │         └─ _resolve_principal(owner_id)
                │               ├─ User (005...)  → UserHandler.resolve()
                │               └─ Group/other    → GroupHandler.resolve()
                │
                ├─ 3.2  ShareFetcher.get_share_entries()
                │
                └─ 3.3  For each share entry's UserOrGroupId:
                          _resolve_principal(id)
                            ├─ User (005...)  → UserHandler.resolve()
                            │                    → {user_id} or {}
                            └─ Group/other    → GroupHandler.resolve()
                                                 ├─ 3.3.1 Role    → RoleHandler
                                                 ├─ 3.3.2 Territory → TerritoryHandler
                                                 └─ 3.3.3 Queue/Manager → QueueHandler

Controlled-by-parent resolution
--------------------------------
When a record's OWD is "ControlledByParent", the resolver:
  1. Discovers the controlling master-detail (or lookup) field via Tooling API.
  2. Fetches the parent record ID.
  3. Calls _resolve_internal on the parent (recursively, up to _MAX_PARENT_DEPTH).
If the parent chain cannot be resolved, falls back to private ACL on the
original record.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from acl_engine.models import AclResult, PUBLIC_SENTINEL
from acl_engine.org_wide_defaults import OWDFetcher
from acl_engine.salesforce_client import SalesforceClient
from acl_engine.share_fetcher import ShareFetcher
from acl_engine.user_handler import UserHandler
from acl_engine.group_handler import GroupHandler

logger = logging.getLogger("salesforce_connector.acl_engine")


def _load_parent_map(parent_map: dict[str, tuple[str, str]] | None = None) -> dict[str, tuple[str, str]]:
    """
    Return {objectName: (parentFieldName, parentObjectName)}
    for every object that has a 'parentObjectName' entry.

    parentFieldName is derived as '{parentObjectName}Id' (standard Salesforce
    convention, e.g. Contact → AccountId) unless the schema entry explicitly
    provides a 'parentFieldName' key.
    """
    if parent_map is not None:
        return dict(parent_map)
    from salesforce.settings import build_parent_map
    return build_parent_map()


# Maximum depth when following ControlledByParent chains to prevent runaway
# recursion on misconfigured orgs or circular references.
_MAX_PARENT_DEPTH = int(os.getenv("ACL_MAX_PARENT_DEPTH", "5"))


class AclResolver:
    """
    Resolves the effective set of Salesforce User Ids that can access a record.

    Usage
    -----
    ::
        from acl_engine import AclResolver, SalesforceClient

        client = SalesforceClient(
            instance_url="https://myorg.my.salesforce.com",
            api_version="60.0",
            access_token="<Bearer token>",
        )
        resolver = AclResolver(client)

        # Synchronous (spawns a new event loop internally)
        result = resolver.resolve("Account", "001xxxxxxxxxxxx")

        # Async (preferred inside an async context)
        result = await resolver.resolve_async("Account", "001xxxxxxxxxxxx")

        if result.is_public:
            print("Visible to everyone")
        else:
            print("Visible to:", result.user_ids)

    Parameters
    ----------
    sf_client      : A configured SalesforceClient instance.
    owd_field_map  : Pre-built {objectName: owdField}; defaults to loading from config/.
    parent_map     : Pre-built {objectName: (parentFieldName, parentObjectName)}; defaults to loading from config/.
    """

    def __init__(
        self,
        sf_client: SalesforceClient,
        owd_field_map: dict[str, str] | None = None,
        parent_map: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        self._sf = sf_client
        self._owd_fetcher = OWDFetcher(sf_client, owd_field_map=owd_field_map)
        self._share_fetcher = ShareFetcher(sf_client)
        self._user_handler = UserHandler(sf_client)
        self._group_handler = GroupHandler(sf_client)
        # {objectName: (parentFieldName, parentObjectName)} – loaded once from schema.json
        self._parent_map: dict[str, tuple[str, str]] = _load_parent_map(parent_map)

    # ── Public API ────────────────────────────────────────────────────────────

    def resolve(self, object_type: str, record_id: str) -> AclResult:
        """
        Synchronous entry point.

        Internally creates a new event loop via asyncio.run().  Do not call
        this from inside an already-running event loop – use resolve_async
        instead.
        """
        return asyncio.run(self.resolve_async(object_type, record_id))

    async def resolve_async(self, object_type: str, record_id: str) -> AclResult:
        """
        Async entry point – preferred when called from an async context.
        """
        logger.info("=" * 70)
        logger.info(
            "[AclResolver] START  object_type=%-20s  record_id=%s",
            object_type,
            record_id,
        )
        logger.info("=" * 70)

        result = await self._resolve_internal(object_type, record_id, depth=0)

        logger.info(
            "[AclResolver] DONE   is_public=%-5s  user_count=%d",
            result.is_public,
            len(result.user_ids),
        )
        return result

    # ── Internal pipeline ─────────────────────────────────────────────────────

    async def _resolve_internal(
        self,
        object_type: str,
        record_id: str,
        depth: int,
    ) -> AclResult:
        """
        Recursive core of the ACL pipeline.

        Evaluates the OWD for *object_type* and routes to the appropriate path:
        public grant, ControlledByParent recursion, or private ACL resolution.

        Parameters
        ----------
        object_type : Salesforce object API name (e.g. "Account").
        record_id   : 18-char Salesforce record Id.
        depth       : Current recursion depth (guards ControlledByParent chains).

        Returns
        -------
        AclResult with is_public / user_ids populated.
        """
        result = AclResult(object_type=object_type, record_id=record_id)

        # ── Step 2: Fetch OWD and decide path ─────────────────────────────────
        owd = await self._owd_fetcher.get_owd(object_type)
        result.owd = owd

        if self._owd_fetcher.is_public(owd):
            logger.info(
                "[AclResolver] OWD=%s is public → granting org-wide access for %s/%s",
                owd,
                object_type,
                record_id,
            )
            result.is_public = True
            result.user_ids = {PUBLIC_SENTINEL}
            return result

        if self._owd_fetcher.is_controlled_by_parent(owd):
            logger.info(
                "[AclResolver] OWD=%s → ControlledByParent for %s/%s (depth=%d)",
                owd,
                object_type,
                record_id,
                depth,
            )
            return await self._resolve_controlled_by_parent(object_type, record_id, depth)

        # ── Step 3: Private ACL resolution ────────────────────────────────────
        logger.info(
            "[AclResolver] OWD=%s → private ACL required for %s/%s",
            owd,
            object_type,
            record_id,
        )
        result.user_ids = await self._resolve_private_acl(object_type, record_id)
        return result

    # ── Step 3 orchestration ──────────────────────────────────────────────────

    async def _resolve_private_acl(self, object_type: str, record_id: str) -> set[str]:
        """
        Core private ACL pipeline for a single record.

        Steps
        -----
        3.1  Fetch the record's OwnerId and resolve it.
        3.2  Fetch all share table entries for the record.
        3.3  Expand each UserOrGroupId to a set of User Ids.
        """
        all_user_ids: set[str] = set()

        # ── 3.1  Owner ────────────────────────────────────────────────────────
        owner_id = await self._share_fetcher.get_owner_id(object_type, record_id)
        if owner_id:
            logger.info("[AclResolver] 3.1 Owner → %s", owner_id)
            owner_users = await self._resolve_principal(owner_id)
            if PUBLIC_SENTINEL in owner_users:
                return owner_users  # org-owned record
            all_user_ids.update(owner_users)

        # ── 3.2  Share table entries ──────────────────────────────────────────
        share_entries = await self._share_fetcher.get_share_entries(object_type, record_id)
        logger.info("[AclResolver] 3.2 Share entries: %d", len(share_entries))

        # ── 3.3  Expand each UserOrGroupId ────────────────────────────────────
        for entry in share_entries:
            principal_id = entry.user_or_group_id
            logger.debug("[AclResolver] 3.3 Expanding principal %s (RowCause=%s)", principal_id, entry.row_cause)

            users = await self._resolve_principal(principal_id)

            if PUBLIC_SENTINEL in users:
                # Organisation-wide share – no need to enumerate individuals
                logger.info(
                    "[AclResolver] 3.3 Principal %s resolved to PUBLIC_SENTINEL → "
                    "short-circuiting with org-wide access",
                    principal_id,
                )
                return {PUBLIC_SENTINEL}

            all_user_ids.update(users)

        logger.info(
            "[AclResolver] 3.3 Total resolved users for %s/%s: %d",
            object_type,
            record_id,
            len(all_user_ids),
        )
        return all_user_ids

    async def _resolve_principal(self, principal_id: str) -> set[str]:
        """
        Route a single UserOrGroupId to the correct handler.

        - User (starts with "005") → UserHandler.resolve
        - Everything else          → GroupHandler.resolve (dispatches to
                                     Role / Territory / Queue handlers)
        """
        if self._user_handler.is_user_id(principal_id):
            return await self._user_handler.resolve(principal_id)
        return await self._group_handler.resolve(principal_id)

    # ── ControlledByParent resolution ─────────────────────────────────────────

    async def _resolve_controlled_by_parent(
        self,
        object_type: str,
        record_id: str,
        depth: int,
    ) -> AclResult:
        """
        Inherit ACL from the controlling parent record.

        Guards
        ------
        * Depth limit (_MAX_PARENT_DEPTH) – fall back to private ACL on the
          original record if the chain is too deep.
        * Missing parent info – fall back gracefully.
        """
        if depth >= _MAX_PARENT_DEPTH:
            logger.warning(
                "[AclResolver] Max parent depth (%d) reached for %s/%s; "
                "falling back to private ACL on this record",
                _MAX_PARENT_DEPTH,
                object_type,
                record_id,
            )
            result = AclResult(object_type=object_type, record_id=record_id, owd="ControlledByParent")
            result.user_ids = await self._resolve_private_acl(object_type, record_id)
            return result

        parent_field, parent_type = await self._get_controlling_parent_info(object_type)

        if not parent_field or not parent_type:
            logger.warning(
                "[AclResolver] Cannot determine controlling parent for %s; "
                "falling back to private ACL",
                object_type,
            )
            result = AclResult(object_type=object_type, record_id=record_id, owd="ControlledByParent")
            result.user_ids = await self._resolve_private_acl(object_type, record_id)
            return result

        parent_id = await self._fetch_field_value(object_type, record_id, parent_field)

        if not parent_id:
            logger.warning(
                "[AclResolver] Parent record not found for %s/%s (field=%s); "
                "falling back to private ACL",
                object_type,
                record_id,
                parent_field,
            )
            result = AclResult(object_type=object_type, record_id=record_id, owd="ControlledByParent")
            result.user_ids = await self._resolve_private_acl(object_type, record_id)
            return result

        logger.info(
            "[AclResolver] %s/%s → parent %s/%s via field %s (depth=%d)",
            object_type,
            record_id,
            parent_type,
            parent_id,
            parent_field,
            depth + 1,
        )
        return await self._resolve_internal(parent_type, parent_id, depth + 1)

    async def _get_controlling_parent_info(
        self, object_type: str
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Return the controlling parent field and object type for *object_type*
        by looking up the 'parentObjectName' / 'parentFieldName' entries in
        schema.json (loaded once at construction time into self._parent_map).

        parentFieldName defaults to '{parentObjectName}Id' when not explicitly
        set in schema.json (standard Salesforce naming convention).

        Returns (field_api_name, parent_object_type) or (None, None) when the
        object has no parent entry in schema.json.
        """
        entry = self._parent_map.get(object_type)
        if entry:
            parent_field, parent_type = entry
            logger.info(
                "[AclResolver] Parent info for %s: field=%s → %s (from schema.json)",
                object_type, parent_field, parent_type,
            )
            return parent_field, parent_type

        logger.warning(
            "[AclResolver] No parentObjectName in schema.json for %s – "
            "cannot follow ControlledByParent chain",
            object_type,
        )
        return None, None

    async def _fetch_field_value(
        self, object_type: str, record_id: str, field_name: str
    ) -> Optional[str]:
        """Read a single field value from a record."""
        soql = f"SELECT {field_name} FROM {object_type} WHERE Id = '{record_id}' LIMIT 1"
        try:
            records = await self._sf.query_all(soql)
        except RuntimeError:
            return None
        if not records:
            return None
        return records[0].get(field_name)
