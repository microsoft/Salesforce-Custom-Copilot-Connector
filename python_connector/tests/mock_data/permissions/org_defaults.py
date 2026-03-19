from __future__ import annotations

from typing import Any

from connector.identity_sync import EntityVisibility


def build_org_defaults_map(
    *,
    account: EntityVisibility = EntityVisibility.PUBLIC_READ_WRITE,
    contact: EntityVisibility = EntityVisibility.CONTROLLED_BY_PARENT,
    opportunity: EntityVisibility = EntityVisibility.PUBLIC_READ_WRITE,
    lead: EntityVisibility = EntityVisibility.PUBLIC_READ_WRITE_TRANSFER,
    case: EntityVisibility = EntityVisibility.PUBLIC_READ_WRITE_TRANSFER,
) -> dict[str, EntityVisibility]:
    return {
        "Account": account,
        "Contact": contact,
        "Opportunity": opportunity,
        "Lead": lead,
        "Case": case,
    }


def build_org_defaults_response(overrides: dict[str, str] | None = None) -> dict[str, Any]:
    record = {
        "attributes": {"type": "Organization"},
        "Id": "00D000000000001AAA",
        "DefaultAccountAccess": "Edit",
        "DefaultContactAccess": "ControlledByParent",
        "DefaultOpportunityAccess": "Edit",
        "DefaultLeadAccess": "ReadEditTransfer",
        "DefaultCampaignAccess": "All",
        "DefaultCaseAccess": "ReadEditTransfer",
    }
    if overrides:
        record.update(overrides)
    return {"totalSize": 1, "done": True, "records": [record]}