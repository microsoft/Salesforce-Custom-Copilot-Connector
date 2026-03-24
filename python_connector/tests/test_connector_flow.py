from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from connector.acl import AclResolver
from connector.item_upload_log import (
    get_item_response_debug_log_path,
    get_item_request_debug_log_path,
    get_item_upload_log_path,
)
from test_flow import collect_items
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


class RecordingVerifyGraphClient:
    def __init__(self, responses_by_item_id: dict[str, dict]) -> None:
        self.get_calls: list[str] = []
        self._responses_by_item_id = responses_by_item_id

    def get(self, path_or_url: str, *, headers=None):
        self.get_calls.append(path_or_url)
        item_id = path_or_url.rsplit("/", 1)[-1]
        return self._responses_by_item_id[item_id]


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


def test_transformer_wraps_scalar_for_collection_schema_property(test_config):
    transformer = SalesforceItemTransformer(
        test_config.connector.salesforce.instance_url,
        test_config.connector.schema,
    )
    record = next(record for record in get_all_salesforce_records() if record["objectType"] == "Case")
    record = {**record, "Priority": "High"}

    transformed_items = transformer.transform_record(record, public_acl())

    assert len(transformed_items) == 1
    properties = transformed_items[0]["properties"]
    assert properties["Priority"] == ["High"]
    assert properties["Priority@odata.type"] == "Collection(String)"


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


def test_ingest_content_uploads_mock_records(monkeypatch, test_config, tmp_path):
    raw_records = get_all_salesforce_records()
    expected_acl_map = build_acl_map(raw_records, public_acl())
    graph_client = RecordingGraphClient()
    test_config = replace(test_config, repo_root=tmp_path)

    monkeypatch.setattr("connector.ingest.get_all_items_from_api", lambda config, since: iter(raw_records))

    class FakeResolver:
        def __init__(self, config, handlers, graph_client=None):
            self._acl_map = expected_acl_map

        def resolve(self, records_by_object_type):
            return self._acl_map

    monkeypatch.setattr("connector.ingest.AclResolver", FakeResolver)

    ingest_content(test_config, graph_client, since=None)

    # All records should be uploaded (configured types use converter, others use legacy builder)
    assert len(graph_client.put_calls) == len(raw_records)
    assert graph_client.delete_calls == []

    for path_or_url, payload, headers in graph_client.put_calls:
        assert test_config.connector.id in path_or_url
        assert payload["acl"]
        assert payload["content"]["type"] == "text"
        assert headers == {"content-type": "application/json"}

    log_payload = json.loads(get_item_upload_log_path(test_config).read_text(encoding="utf-8"))
    assert log_payload["connectionId"] == test_config.connector.id
    assert len(log_payload["items"]) == len(raw_records)
    assert {entry["itemId"] for entry in log_payload["items"]} == {record["Id"] for record in raw_records}
    assert all(entry.get("url") for entry in log_payload["items"])

    debug_payload = json.loads(get_item_request_debug_log_path(test_config).read_text(encoding="utf-8"))
    assert debug_payload["connectionId"] == test_config.connector.id
    assert len(debug_payload["requests"]) == len(raw_records)
    assert {entry["itemId"] for entry in debug_payload["requests"]} == {record["Id"] for record in raw_records}
    assert all(entry.get("url") for entry in debug_payload["requests"])
    assert all(entry.get("requestPayload", {}).get("content", {}).get("type") == "text" for entry in debug_payload["requests"])


def test_collect_items_logs_case_graph_responses(test_config, tmp_path):
    raw_records = get_all_salesforce_records()
    case_records = [record for record in raw_records if record["objectType"] == "Case"]
    graph_client = RecordingVerifyGraphClient(
        {
            record["Id"]: {
                "id": record["Id"],
                "properties": {
                    "objectType": record["objectType"],
                    "title": record["Subject"],
                },
                "acl": public_acl(),
            }
            for record in case_records
        }
    )
    test_config = replace(test_config, repo_root=tmp_path)

    verify_object_type, item_count, samples = collect_items(test_config, graph_client, raw_records, show_items=2)

    assert verify_object_type == "Case"
    assert item_count == len(case_records)
    assert len(samples) == 2
    assert all(sample["properties"]["objectType"] == "Case" for sample in samples)
    assert len(graph_client.get_calls) == len(case_records)

    response_log_payload = json.loads(get_item_response_debug_log_path(test_config).read_text(encoding="utf-8"))
    assert response_log_payload["connectionId"] == test_config.connector.id
    assert len(response_log_payload["responses"]) == len(case_records)
    assert {entry["itemId"] for entry in response_log_payload["responses"]} == {
        record["Id"] for record in case_records
    }
    assert all(entry["objectType"] == "Case" for entry in response_log_payload["responses"])
    assert all(entry.get("responsePayload", {}).get("id") == entry["itemId"] for entry in response_log_payload["responses"])