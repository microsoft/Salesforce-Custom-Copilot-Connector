from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from Graph.acl import AclResolver
from Item.item_converter import build_handlers_from_config


class AclParentMappingTests(unittest.TestCase):
    def _make_resolver(self, config: dict[str, object]) -> AclResolver:
        resolver = AclResolver.__new__(AclResolver)
        resolver._handlers = build_handlers_from_config(config)
        resolver._tenant_id = "11111111-2222-3333-4444-555555555555"
        return resolver

    def test_parent_controlled_acl_uses_nested_schema_parent_path(self) -> None:
        resolver = self._make_resolver(
            {
                "objectList": [
                    {"objectName": "Account", "selectedFields": {"Name": "Name"}},
                    {
                        "objectName": "Contact",
                        "selectedFields": {
                            "Name": "Name",
                            "Account.Id": "AccountId",
                        },
                        "parentObjectName": "Account",
                        "objectNameAsChild": "Contacts",
                    },
                ]
            }
        )
        expected_acl = [{"accessType": "grant", "type": "everyone", "value": "tenant-guid"}]

        async def fake_private_acl_map(object_name: str, records: list[dict[str, object]]) -> dict[str, list[dict[str, str]]]:
            self.fail(f"Did not expect fallback private ACL path for {object_name}: {records}")

        resolver._build_private_acl_map = fake_private_acl_map  # type: ignore[method-assign]

        result = asyncio.run(
            resolver._build_parent_controlled_acl_map(
                "Contact",
                [{"Id": "003-contact", "Account": {"Id": "001-account"}}],
                {"Account": {"001-account": expected_acl}},
            )
        )

        self.assertEqual(result, {"003-contact": expected_acl})

    def test_parent_controlled_acl_uses_schema_mapped_custom_parent_field(self) -> None:
        resolver = self._make_resolver(
            {
                "objectList": [
                    {"objectName": "Account", "selectedFields": {"Name": "Name"}},
                    {
                        "objectName": "Project__c",
                        "selectedFields": {
                            "Name": "Name",
                            "Account__c": "AccountId",
                        },
                        "parentObjectName": "Account",
                        "objectNameAsChild": "Projects__r",
                    },
                ]
            }
        )
        expected_acl = [{"accessType": "grant", "type": "everyone", "value": "tenant-guid"}]

        async def fake_private_acl_map(object_name: str, records: list[dict[str, object]]) -> dict[str, list[dict[str, str]]]:
            self.fail(f"Did not expect fallback private ACL path for {object_name}: {records}")

        resolver._build_private_acl_map = fake_private_acl_map  # type: ignore[method-assign]

        result = asyncio.run(
            resolver._build_parent_controlled_acl_map(
                "Project__c",
                [{"Id": "a01-project", "Account__c": "001-account"}],
                {"Account": {"001-account": expected_acl}},
            )
        )

        self.assertEqual(result, {"a01-project": expected_acl})

    def test_sort_object_names_uses_schema_parent_dependencies(self) -> None:
        resolver = self._make_resolver(
            {
                "objectList": [
                    {"objectName": "Account", "selectedFields": {"Name": "Name"}},
                    {
                        "objectName": "Project__c",
                        "selectedFields": {"Account__c": "AccountId"},
                        "parentObjectName": "Account",
                        "objectNameAsChild": "Projects__r",
                    },
                    {
                        "objectName": "Task__c",
                        "selectedFields": {"Project__c": "Project__cId"},
                        "parentObjectName": "Project__c",
                        "objectNameAsChild": "Tasks__r",
                    },
                ]
            }
        )

        ordered = resolver._sort_object_names(["Task__c", "Project__c", "Account"])

        self.assertEqual(ordered, ["Account", "Project__c", "Task__c"])


if __name__ == "__main__":
    unittest.main()