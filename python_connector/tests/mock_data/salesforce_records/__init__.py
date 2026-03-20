from __future__ import annotations

from typing import Any

from ..common import DEFAULT_RECORD_LIMIT, clamp_limit
from .account_records import get_account_records
from .case_records import get_case_records
from .contact_records import get_contact_records
from .customer_project_records import get_customer_project_records
from .lead_records import get_lead_records
from .opportunity_records import get_opportunity_records


def get_all_salesforce_records(limit_per_object: int = DEFAULT_RECORD_LIMIT) -> list[dict[str, Any]]:
    limit = clamp_limit(limit_per_object)
    return [
        *get_account_records(limit),
        *get_lead_records(limit),
        *get_contact_records(limit),
        *get_opportunity_records(limit),
        *get_case_records(limit),
        *get_customer_project_records(limit),
    ]


__all__ = [
    "get_account_records",
    "get_all_salesforce_records",
    "get_case_records",
    "get_contact_records",
    "get_customer_project_records",
    "get_lead_records",
    "get_opportunity_records",
]