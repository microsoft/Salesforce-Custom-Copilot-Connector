"""
acl_engine/user_handler.py
--------------------------
Step 3.2: Handle User-type principals.

A Salesforce User ID always begins with the key-prefix "005".

Responsibilities
----------------
* Identify whether a given UserOrGroupId is a plain User (vs. a Group).
* Validate that the user is active before adding to the allow list
  (inactive / deactivated users must never appear in the final ACL).
* Fetch full user detail when needed via the REST sobjects endpoint.

Two fetch modes
---------------
resolve(user_id)
    Lightweight SOQL: validates IsActive only.  Used in the main ACL pipeline
    where we only need to know *whether* the user should be in the allow list.

get_details(user_id)
    Full REST call to ``GET /sobjects/User/<user_id>``.  Returns the complete
    user record as a dict.  Useful for downstream M365 principal mapping.

    curl equivalent:
        GET /services/data/v60.0/sobjects/User/<user_id>
        Authorization: Bearer <access_token>
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from acl_engine.salesforce_client import SalesforceClient

logger = logging.getLogger("salesforce_connector.acl_engine")

# Salesforce key prefix for User records
USER_ID_PREFIX = "005"


class UserHandler:
    """
    Resolves a single Salesforce User principal.

    Parameters
    ----------
    sf_client : SalesforceClient instance.
    """

    def __init__(self, sf_client: SalesforceClient) -> None:
        self._sf = sf_client

    # ── Type detection ────────────────────────────────────────────────────────

    @staticmethod
    def is_user_id(principal_id: str) -> bool:
        """
        Return True when *principal_id* is a Salesforce User record ID.

        Salesforce assigns key-prefix "005" to all User records, making this
        a reliable O(1) check before any network call is needed.

        If you are ever unsure whether the prefix is correct for your org,
        the GroupHandler's fallback path (query the Group table) will catch it.
        """
        return bool(principal_id) and principal_id.startswith(USER_ID_PREFIX)

    # ── ACL resolution (lightweight) ─────────────────────────────────────────

    async def resolve(self, user_id: str) -> set[str]:
        """
        Return ``{user_id}`` if the user is active, else an empty set.

        Why validate instead of blindly trusting the share table?
        Salesforce may retain share rows for deactivated users, so we must
        re-confirm liveness before emitting an ACL entry.

        Uses a minimal SOQL query (no field data, just existence + IsActive).

        Parameters
        ----------
        user_id : Salesforce User Id (18-char or 15-char).

        Returns
        -------
        set[str] – {user_id} or set()

        curl equivalent:
            GET /services/data/v60.0/query
                ?q=SELECT+Id+FROM+User+WHERE+Id='<user_id>'+AND+IsActive=true+LIMIT+1
        """
        soql = (
            f"SELECT Id FROM User "
            f"WHERE Id = '{user_id}' AND IsActive = true "
            f"LIMIT 1"
        )
        try:
            records = await self._sf.query_all(soql)
        except RuntimeError as exc:
            logger.warning("[UserHandler] Could not validate user %s: %s", user_id, exc)
            return set()

        if records:
            logger.debug("[UserHandler] User %s is active → added to ACL", user_id)
            return {user_id}

        logger.debug("[UserHandler] User %s is inactive or not found → skipped", user_id)
        return set()

    # ── Full user detail fetch (REST sobjects) ────────────────────────────────

    async def get_details(self, user_id: str) -> Optional[dict[str, Any]]:
        """
        Fetch the complete User record via the sObject REST endpoint.

        This is the REST equivalent of:
            GET /services/data/v60.0/sobjects/User/<user_id>

        Returns the full user payload dict (all standard + custom fields), or
        None on any error.

        When to use this vs. resolve()
        --------------------------------
        * ``resolve()`` is used in the ACL pipeline (fast, batch-friendly).
        * ``get_details()`` is for downstream operations that need field values
          such as FederationIdentifier, Email, or UserName for M365 mapping.

        Parameters
        ----------
        user_id : Salesforce User Id (18-char or 15-char).

        Returns
        -------
        dict[str, Any] | None – Raw Salesforce User record, or None on failure.
        """
        try:
            details = await self._sf.get_sobject(sobject_name="User", record_id=user_id)
            logger.debug(
                "[UserHandler] Fetched details for user %s: IsActive=%s Email=%s",
                user_id,
                details.get("IsActive"),
                details.get("Email"),
            )
            return details
        except RuntimeError as exc:
            logger.warning("[UserHandler] Could not fetch details for user %s: %s", user_id, exc)
            return None
