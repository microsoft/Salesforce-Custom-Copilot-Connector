from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from connector.acl import AclResolver
from connector.identity_sync import EntityVisibility
from connector.transform import SalesforceItemTransformer
from tests.mock_data import (
    OWNER_GUID,
    SHARED_GUID,
    TENANT_ID,
    build_private_case_permissions_bundle,
    get_all_salesforce_records,
    public_acl,
)


def test_transformer_builds_mock_item_with_acl(test_config):
    transformer = SalesforceItemTransformer(
        test_config.connector.salesforce.instance_url,
        test_config.connector.schema,
    )
    record = next(record for record in get_all_salesforce_records() if record["objectType"] == "Opportunity")

    transformed_items = transformer.transform_record(record, public_acl())

    assert len(transformed_items) == 1
    item = transformed_items[0]
    assert item["properties"]["objectType"] == "Opportunity"
    assert item["acl"] == public_acl()
    assert item["content"]["type"] == "text"
    assert "Renewal opportunity sample" in item["content"]["value"]


def test_acl_resolver_builds_mock_private_case_grants(test_config):
    permissions_bundle = build_private_case_permissions_bundle()
    case_record = permissions_bundle["record"]

    resolver = AclResolver.__new__(AclResolver)
    resolver._config = test_config
    resolver._handlers = {"Case": SimpleNamespace(object_name="Case", child_handlers=[])}
    resolver._graph_client = None
    resolver._tenant_id = TENANT_ID
    resolver._helper = SimpleNamespace(
        get_org_wide_defaults_map=AsyncMock(return_value={"Case": EntityVisibility.NONE}),
        get_authorized_users_and_groups_from_salesforce=AsyncMock(
            return_value=(permissions_bundle["authorized_users_by_object"], {})
        ),
    )
    resolver._group_cache = {}
    resolver._principal_id_cache = {}
    resolver._role_children_cache = None
    resolver._users_and_managers = None
    resolver._frozen_users = None
    resolver._get_frozen_users = AsyncMock(return_value=set())

    async def fake_get_shares_by_record(object_name: str, record_ids: list[str]):
        assert object_name == "Case"
        assert record_ids == [case_record["Id"]]
        return permissions_bundle["shares_by_record"]

    async def fake_expand_groups(group_ids: set[str]):
        assert group_ids == set()
        return set(), False

    resolver._get_shares_by_record = fake_get_shares_by_record
    resolver._expand_groups = fake_expand_groups
    resolver._resolve_user_guid = lambda user: permissions_bundle["graph_ids_by_identifier"].get(
        user.UserName or user.Email
    )

    acl_map = resolver.resolve({"Case": [case_record]})

    assert acl_map["Case"][case_record["Id"]] == [
        {"accessType": "grant", "type": "user", "value": OWNER_GUID},
        {"accessType": "grant", "type": "user", "value": SHARED_GUID},
    ]
