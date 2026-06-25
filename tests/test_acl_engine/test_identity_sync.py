# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from salesforce.sharing_model import (
    EntityShareBase,
    IdentitySyncQueries,
    SalesforceIdentitySOQLResponseProcessor,
)


def test_entity_share_parser_tolerates_missing_id():
    processor = SalesforceIdentitySOQLResponseProcessor()

    parsed = processor.get(
        {
            "records": [
                {
                    "UserOrGroupId": "005000000000001AAA",
                    "RowCause": "Manual",
                    "UserOrGroup": {"Type": "User"},
                }
            ]
        },
        EntityShareBase,
    )

    assert len(parsed) == 1
    assert parsed[0].Id == ""
    assert parsed[0].UserOrGroupId == "005000000000001AAA"
    assert parsed[0].RowCause == "Manual"
    assert parsed[0].UserOrGroup is not None
    assert parsed[0].UserOrGroup.Type == "User"


def test_share_query_requests_nested_share_id():
    assert "(SELECT Id, UserOrGroupId, UserOrGroup.Type from Shares)" in IdentitySyncQueries.AllSharesFromRecords