from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from connector.acl import AclResolver
from connector.identity_sync import EntityVisibility
from connector.ingest import ingest_content
from connector.transform import SalesforceItemTransformer
from tests.mock_data import (
    OWNER_GUID,
    SHARED_GUID,
    TENANT_ID,
    build_acl_map,
    build_private_case_permissions_bundle,
    get_all_salesforce_records,
    build_private_case_permissions_bundle,
    public_acl,
)


class RecordingGraphClient:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, dict, dict | None]] = []
        self.delete_calls: list[tuple[str, dict | None]] = []

    def put(self, path_or_url: str, *, json_body=None, headers=None):
        self.put_calls.append((path_or_url, json_body, headers))
        return {}

    def delete(self, path_or_url: str, *, headers=None):
        self.delete_calls.append((path_or_url, headers))
        return {}


def test_transformer_handles_all_mock_objects(test_config):
    transformer = SalesforceItemTransformer(
        test_config.connector.salesforce.instance_url,
        test_config.connector.schema,
    )
    expected_acl = public_acl()

    for record in get_all_salesforce_records():
        transformed_items = transformer.transform_record(record, expected_acl)
        assert len(transformed_items) == 1
        transformed_item = transformed_items[0]

        assert transformed_item["id"] == record["Id"]
        assert transformed_item["properties"]["objectType"] == record["objectType"]
        assert transformed_item["content"]["type"] == "text"
        assert transformed_item["acl"] == expected_acl


def test_acl_resolver_inherits_parent_public_acl_for_contact(test_config):
    records = get_all_salesforce_records()
    account = next(record for record in records if record["objectType"] == "Account")
    contact = next(record for record in records if record["objectType"] == "Contact")

    resolver = AclResolver.__new__(AclResolver)
    resolver._config = test_config
    resolver._handlers = {}
    resolver._graph_client = None
    resolver._tenant_id = TENANT_ID
    resolver._helper = SimpleNamespace(
        get_org_wide_defaults_map=AsyncMock(
            return_value={
                "Account": EntityVisibility.PUBLIC_READ_WRITE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            }
        )
    )

    acl_map = resolver.resolve({"Account": [account], "Contact": [contact]})

    assert acl_map["Account"][account["Id"]] == public_acl()
    assert acl_map["Contact"][contact["Id"]] == public_acl()


def test_acl_resolver_builds_user_grants_for_private_case(test_config):
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
    resolver._resolve_user_guid = lambda user: permissions_bundle["graph_ids_by_identifier"].get(user.UserName or user.Email)

    acl_map = resolver.resolve({"Case": [case_record]})

    assert acl_map["Case"][case_record["Id"]] == [
        {"accessType": "grant", "type": "user", "value": OWNER_GUID},
        {"accessType": "grant", "type": "user", "value": SHARED_GUID},
    ]


def test_ingest_content_uploads_mock_records(monkeypatch, test_config):
    raw_records = get_all_salesforce_records()
    expected_acl_map = build_acl_map(raw_records, public_acl())
    graph_client = RecordingGraphClient()

    monkeypatch.setattr("connector.ingest.get_all_items_from_api", lambda config, since: iter(raw_records))

    class FakeResolver:
        def __init__(self, config, handlers, graph_client=None):
            self._acl_map = expected_acl_map

        def resolve(self, records_by_object_type):
            return self._acl_map

    monkeypatch.setattr("connector.ingest.AclResolver", FakeResolver)

    ingest_content(test_config, graph_client, since=None)

    assert len(graph_client.put_calls) == len(raw_records)
    assert graph_client.delete_calls == []

    for path_or_url, payload, headers in graph_client.put_calls:
        assert test_config.connector.id in path_or_url
        assert payload["acl"]
        assert payload["content"]["type"] == "text"
        assert headers == {"content-type": "application/json"}