"""Tests for acl_engine.group_acl_builder — Group-based ACL resolution."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from acl_engine.group_acl_builder import GroupAclBuilder, _group_ace, _user_ace_aad, _user_ace_external
from acl_engine.group_id_formats import SfGroupIdFormats
from acl_engine.identity_models import (
    EntityVisibility,
    SfGroup,
    SfUser,
    UserOrGroupType,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _make_sf_user(
    user_id: str = "005000000000001",
    name: str = "Test User",
    email: str = "test@example.com",
    federation_id: str = "",
    parent_role_id: str = "",
    permission_sets: list | None = None,
) -> SfUser:
    return SfUser(
        id=user_id,
        name=name,
        email=email,
        federation_identifier=federation_id,
        user_name=f"{name.lower().replace(' ', '.')}@test.com",
        parent_role_id=parent_role_id,
        permission_sets=[{"Id": "ps1", "Label": "Read"}] if permission_sets is None else permission_sets,
    )


def _make_builder(
    owd_map: dict[str, EntityVisibility] | None = None,
    users: list[SfUser] | None = None,
    groups: list[SfGroup] | None = None,
    frozen: set[str] | None = None,
    parent_map: dict[str, tuple[str, str]] | None = None,
) -> GroupAclBuilder:
    sf_client = MagicMock()
    builder = GroupAclBuilder(
        sf_client=sf_client,
        parent_map=parent_map or {},
    )
    builder._owd_map = owd_map or {}
    if users is not None:
        builder._users_by_id = {u.id: u for u in users}
    if groups is not None:
        builder._groups_by_id = {g.id: g for g in groups}
    builder._frozen_users = frozen or set()
    return builder


# ── ACE factory tests ────────────────────────────────────────────────────────


class TestAceFactories:
    def test_group_ace(self):
        ace = _group_ace("AccountTopLevel")
        assert ace == {
            "accessType": "grant",
            "type": "externalGroup",
            "value": "AccountTopLevel",
        }

    def test_user_ace_aad_with_guid(self):
        guid = "f1126041-cb51-4f20-82d5-722b4cfcdfa1"
        ace = _user_ace_aad(guid)
        assert ace is not None
        assert ace["value"] == guid

    def test_user_ace_aad_rejects_email(self):
        ace = _user_ace_aad("user@tenant.com")
        assert ace is None

    def test_user_ace_external_returns_none(self):
        user = _make_sf_user(user_id="005ABC")
        ace = _user_ace_external(user)
        assert ace is None


# ── PUBLIC OWD tests ─────────────────────────────────────────────────────────


class TestPublicOwd:
    def test_public_owd_produces_grant_everyone_ace(self):
        builder = _make_builder(owd_map={"Account": EntityVisibility.READ})
        records = [{"Id": "001ABC", "objectType": "Account"}]

        result = asyncio.run(builder._build_acl_map("Account", records, {}))

        assert "001ABC" in result
        assert len(result["001ABC"]) == 1
        assert result["001ABC"][0]["value"] == "everyone"
        assert result["001ABC"][0]["type"] == "everyone"
        assert result["001ABC"][0]["accessType"] == "grant"

    def test_edit_owd_is_public(self):
        builder = _make_builder(owd_map={"Lead": EntityVisibility.EDIT})
        records = [{"Id": "00Q001"}]
        result = asyncio.run(builder._build_acl_map("Lead", records, {}))
        assert result["00Q001"][0]["value"] == "everyone"

    def test_read_edit_transfer_owd_is_public(self):
        builder = _make_builder(owd_map={"Case": EntityVisibility.READ_EDIT_TRANSFER})
        records = [{"Id": "500001"}]
        result = asyncio.run(builder._build_acl_map("Case", records, {}))
        assert result["500001"][0]["value"] == "everyone"

    def test_multiple_records_all_get_same_acl(self):
        builder = _make_builder(owd_map={"Account": EntityVisibility.READ})
        records = [{"Id": "001A"}, {"Id": "001B"}, {"Id": "001C"}]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        assert len(result) == 3
        for record_id in ("001A", "001B", "001C"):
            assert result[record_id][0]["value"] == "everyone"


# ── PRIVATE OWD tests ────────────────────────────────────────────────────────


class TestPrivateOwd:
    def test_private_owd_includes_global_users_group(self):
        user1 = _make_sf_user(user_id="005U1", permission_sets=[{"Id": "ps1"}])
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[user1],
        )
        records = [{"Id": "001X", "Shares": {"records": []}}]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        acl = result["001X"]
        assert any(a["value"] == "AccountGlobalUsers" for a in acl)

    def test_user_share_adds_user_ace(self):
        user1 = _make_sf_user(
            user_id="005U1",
            federation_id="f1126041-cb51-4f20-82d5-722b4cfcdfa1",
            permission_sets=[{"Id": "ps1"}],
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[user1],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "005U1",
                    "UserOrGroup": {"Type": "User"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        acl = result["001X"]
        user_aces = [a for a in acl if a["type"] == "user"]
        assert len(user_aces) == 1
        assert user_aces[0]["value"] == "f1126041-cb51-4f20-82d5-722b4cfcdfa1"

    def test_user_share_with_parent_role_adds_role_group(self):
        user1 = _make_sf_user(
            user_id="005U1",
            federation_id="f1126041-cb51-4f20-82d5-722b4cfcdfa1",
            parent_role_id="00E_PARENT",
            permission_sets=[{"Id": "ps1"}],
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[user1],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "005U1",
                    "UserOrGroup": {"Type": "User"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        acl = result["001X"]
        group_values = [a["value"] for a in acl if a["type"] == "externalGroup"]
        assert "Account00EPARENTRole" in group_values

    def test_frozen_user_share_is_skipped(self):
        user1 = _make_sf_user(user_id="005FROZEN", permission_sets=[{"Id": "ps1"}])
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[user1],
            frozen={"005FROZEN"},
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "005FROZEN",
                    "UserOrGroup": {"Type": "User"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        acl = result["001X"]
        user_aces = [a for a in acl if a["type"] == "user"]
        assert len(user_aces) == 0

    def test_user_without_permission_sets_is_skipped(self):
        user1 = _make_sf_user(user_id="005NP", permission_sets=[])
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[user1],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "005NP",
                    "UserOrGroup": {"Type": "User"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        user_aces = [a for a in result["001X"] if a["type"] == "user"]
        assert len(user_aces) == 0

    def test_group_share_role_adds_role_group_ace(self):
        group = SfGroup(
            id="00G_ROLE_GRP",
            type=UserOrGroupType.ROLE,
            related_id="00E_ROLE1",
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
            groups=[group],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "00G_ROLE_GRP",
                    "UserOrGroup": {"Type": "Queue"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        acl = result["001X"]
        group_values = [a["value"] for a in acl if a["type"] == "externalGroup"]
        assert "Account00EROLE1Role" in group_values

    def test_group_share_organization_adds_all_internal_users(self):
        group = SfGroup(
            id="00G_ORG",
            type=UserOrGroupType.ORGANIZATION,
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
            groups=[group],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "00G_ORG",
                    "UserOrGroup": {"Type": "Queue"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        group_values = [a["value"] for a in result["001X"] if a["type"] == "externalGroup"]
        assert "AccountAllInternalUsers" in group_values

    def test_group_share_manager_adds_manager_group(self):
        group = SfGroup(
            id="00G_MGR",
            type=UserOrGroupType.MANAGER,
            related_id="005MGR1",
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
            groups=[group],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "00G_MGR",
                    "UserOrGroup": {"Type": "Queue"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        group_values = [a["value"] for a in result["001X"] if a["type"] == "externalGroup"]
        assert "Account005MGR1Manager" in group_values

    def test_group_share_role_and_subordinates(self):
        group = SfGroup(
            id="00G_RAS",
            type=UserOrGroupType.ROLE_AND_SUBORDINATES,
            related_id="00E_RAS_ROLE",
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
            groups=[group],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "00G_RAS",
                    "UserOrGroup": {"Type": "Queue"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        group_values = [a["value"] for a in result["001X"] if a["type"] == "externalGroup"]
        assert "Account00ERASROLERoleAndSubordinates" in group_values

    def test_group_share_public_group(self):
        group = SfGroup(
            id="00G_PG",
            type=UserOrGroupType.REGULAR,
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
            groups=[group],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "00G_PG",
                    "UserOrGroup": {"Type": "Queue"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        group_values = [a["value"] for a in result["001X"] if a["type"] == "externalGroup"]
        assert "Account00GPGPublicGroup" in group_values

    def test_no_shares_still_has_global_users(self):
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
        )
        records = [{"Id": "001EMPTY", "Shares": {"records": []}}]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        assert len(result["001EMPTY"]) == 1
        assert result["001EMPTY"][0]["value"] == "AccountGlobalUsers"

    def test_group_share_territory(self):
        group = SfGroup(
            id="00G_TERR",
            type=UserOrGroupType.TERRITORY,
            related_id="0ML_TERR1",
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
            groups=[group],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "00G_TERR",
                    "UserOrGroup": {"Type": "Queue"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        group_values = [a["value"] for a in result["001X"] if a["type"] == "externalGroup"]
        assert "Account0MLTERR1Territory" in group_values

    def test_group_share_territory_and_subordinates(self):
        group = SfGroup(
            id="00G_TERR_SUB",
            type=UserOrGroupType.TERRITORY_AND_SUBORDINATES,
            related_id="0ML_TERR2",
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
            groups=[group],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "00G_TERR_SUB",
                    "UserOrGroup": {"Type": "Queue"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        group_values = [a["value"] for a in result["001X"] if a["type"] == "externalGroup"]
        assert "Account0MLTERR2TerritoryAndSubordinates" in group_values

    def test_group_share_territory_and_subordinates_internal(self):
        group = SfGroup(
            id="00G_TERR_INT",
            type=UserOrGroupType.TERRITORY_AND_SUBORDINATES_INTERNAL,
            related_id="0ML_TERR3",
        )
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
            groups=[group],
        )
        records = [{
            "Id": "001X",
            "Shares": {
                "records": [{
                    "UserOrGroupId": "00G_TERR_INT",
                    "UserOrGroup": {"Type": "Queue"},
                }]
            },
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        group_values = [a["value"] for a in result["001X"] if a["type"] == "externalGroup"]
        assert "Account0MLTERR3TerritoryAndSubordinates" in group_values


# ── CONTROLLED BY PARENT tests ───────────────────────────────────────────────


class TestControlledByParent:
    def test_controlled_by_parent_with_public_parent(self):
        """If parent object is PUBLIC, child gets grant-everyone ACL."""
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.READ,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            parent_map={"Contact": ("AccountId", "Account")},
        )
        records = [{"Id": "003C1"}]
        result = asyncio.run(builder._build_acl_map("Contact", records, {}))
        assert result["003C1"][0]["value"] == "everyone"

    def test_cbp_inherits_parent_private_acl(self):
        """Child inherits parent's full private ACL (GlobalUsers + shares)."""
        owner = _make_sf_user(
            user_id="005OWNER",
            federation_id="a2b3c4d5-e6f7-8901-2345-678901234567",
        )
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[owner],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        # Mock SOQL to return parent record
        builder._sf.query_all = AsyncMock(return_value=[
            {"Id": "001ACC", "OwnerId": "005OWNER"},
        ])

        contact_records = [{
            "Id": "003C1",
            "AccountId": "001ACC",
        }]

        # Pre-inject shares on parent (simulate _fetch_and_inject_shares)
        async def _mock_fetch_shares(obj_type, records):
            for r in records:
                r["Shares"] = {
                    "records": [{
                        "UserOrGroupId": "005OWNER",
                        "UserOrGroup": {"Type": "User"},
                    }]
                }

        builder._fetch_and_inject_shares = AsyncMock(side_effect=_mock_fetch_shares)

        result = asyncio.run(builder._build_acl_map("Contact", contact_records, {}))
        acl = result["003C1"]

        # Should have child's GlobalUsers + parent's GlobalUsers + owner user ACE
        values = [a["value"] for a in acl]
        assert "ContactGlobalUsers" in values, "child GlobalUsers missing"
        assert "AccountGlobalUsers" in values, "parent GlobalUsers missing"
        assert "a2b3c4d5-e6f7-8901-2345-678901234567" in values, "parent owner ACE missing"

    def test_cbp_orphan_no_parent_id_gets_deny_everyone(self):
        """Contact with no AccountId gets deny-everyone ACL."""
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        records = [{"Id": "003ORPHAN"}]  # No AccountId
        result = asyncio.run(builder._build_acl_map("Contact", records, {}))
        acl = result["003ORPHAN"]
        assert acl[0]["accessType"] == "deny"
        assert acl[0]["value"] == "everyone"

    def test_cbp_no_parent_map_entry_gets_deny_everyone(self):
        """CBP object type with no parent_map entry falls back to deny-everyone."""
        builder = _make_builder(
            owd_map={"CustomObj__c": EntityVisibility.CONTROLLED_BY_PARENT},
            users=[],
            parent_map={},  # No entry for CustomObj__c
        )
        records = [{"Id": "a01001"}]
        result = asyncio.run(builder._build_acl_map("CustomObj__c", records, {}))
        acl = result["a01001"]
        assert acl[0]["accessType"] == "deny"

    def test_cbp_cache_reuse_across_chunks(self):
        """Second chunk should reuse cached parent ACLs without re-fetching."""
        owner = _make_sf_user(
            user_id="005OWNER",
            federation_id="a2b3c4d5-e6f7-8901-2345-678901234567",
        )
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[owner],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        builder._sf.query_all = AsyncMock(return_value=[
            {"Id": "001ACC", "OwnerId": "005OWNER"},
        ])

        async def _mock_fetch_shares(obj_type, records):
            for r in records:
                r["Shares"] = {"records": []}

        builder._fetch_and_inject_shares = AsyncMock(side_effect=_mock_fetch_shares)

        # First chunk
        chunk1 = [{"Id": "003C1", "AccountId": "001ACC"}]
        result1 = asyncio.run(builder._build_acl_map("Contact", chunk1, {}))
        assert "003C1" in result1

        # Second chunk — same parent, should be cached
        chunk2 = [{"Id": "003C2", "AccountId": "001ACC"}]
        result2 = asyncio.run(builder._build_acl_map("Contact", chunk2, {}))
        assert "003C2" in result2

        # query_all should only have been called once (for the first chunk's parent fetch)
        assert builder._sf.query_all.call_count == 1
        # _fetch_and_inject_shares called only once (for parent shares in first chunk)
        assert builder._fetch_and_inject_shares.call_count == 1

    def test_cbp_parent_with_group_shares(self):
        """Parent role/group shares are inherited by child."""
        group = SfGroup(
            id="00G_ROLE_GRP",
            type=UserOrGroupType.ROLE,
            related_id="00E_ROLE1",
        )
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[],
            groups=[group],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        builder._sf.query_all = AsyncMock(return_value=[
            {"Id": "001ACC", "OwnerId": ""},
        ])

        async def _mock_fetch_shares(obj_type, records):
            for r in records:
                r["Shares"] = {
                    "records": [{
                        "UserOrGroupId": "00G_ROLE_GRP",
                        "UserOrGroup": {"Type": "Queue"},
                    }]
                }

        builder._fetch_and_inject_shares = AsyncMock(side_effect=_mock_fetch_shares)

        records = [{"Id": "003C1", "AccountId": "001ACC"}]
        result = asyncio.run(builder._build_acl_map("Contact", records, {}))
        acl = result["003C1"]

        # Parent's role group should be in child's ACL
        values = [a["value"] for a in acl if a["type"] == "externalGroup"]
        assert "ContactGlobalUsers" in values, "child GlobalUsers"
        assert "Account00EROLE1Role" in values, "parent role group inherited"

    def test_cbp_multiple_children_different_parents(self):
        """Two contacts pointing to different accounts get different ACLs."""
        owner_a = _make_sf_user(user_id="005A", federation_id="aaaa1111-2222-3333-4444-555566667777")
        owner_b = _make_sf_user(user_id="005B", federation_id="bbbb1111-2222-3333-4444-555566667777")
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[owner_a, owner_b],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        builder._sf.query_all = AsyncMock(return_value=[
            {"Id": "001A", "OwnerId": "005A"},
            {"Id": "001B", "OwnerId": "005B"},
        ])

        async def _mock_fetch_shares(obj_type, records):
            for r in records:
                owner_id = r.get("OwnerId", "")
                r["Shares"] = {
                    "records": [{
                        "UserOrGroupId": owner_id,
                        "UserOrGroup": {"Type": "User"},
                    }] if owner_id else []
                }

        builder._fetch_and_inject_shares = AsyncMock(side_effect=_mock_fetch_shares)

        records = [
            {"Id": "003C1", "AccountId": "001A"},
            {"Id": "003C2", "AccountId": "001B"},
        ]
        result = asyncio.run(builder._build_acl_map("Contact", records, {}))

        # Contact 1 should have owner A's GUID
        vals_c1 = [a["value"] for a in result["003C1"]]
        assert "aaaa1111-2222-3333-4444-555566667777" in vals_c1
        assert "bbbb1111-2222-3333-4444-555566667777" not in vals_c1

        # Contact 2 should have owner B's GUID
        vals_c2 = [a["value"] for a in result["003C2"]]
        assert "bbbb1111-2222-3333-4444-555566667777" in vals_c2
        assert "aaaa1111-2222-3333-4444-555566667777" not in vals_c2

    def test_cbp_parent_deleted_gets_deny_everyone(self):
        """Child pointing to a parent that SOQL can't find gets deny-everyone."""
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        # SOQL returns empty — parent was deleted
        builder._sf.query_all = AsyncMock(return_value=[])

        async def _mock_fetch_shares(obj_type, records):
            for r in records:
                r["Shares"] = {"records": []}

        builder._fetch_and_inject_shares = AsyncMock(side_effect=_mock_fetch_shares)

        records = [{"Id": "003C1", "AccountId": "001DELETED"}]
        result = asyncio.run(builder._build_acl_map("Contact", records, {}))
        acl = result["003C1"]
        assert acl[0]["accessType"] == "deny"
        assert acl[0]["value"] == "everyone"

    def test_cbp_mixed_orphans_and_valid_in_same_chunk(self):
        """Chunk with both valid parent refs and orphans: each gets correct ACL."""
        owner = _make_sf_user(
            user_id="005OWN",
            federation_id="cccc1111-2222-3333-4444-555566667777",
        )
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[owner],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        builder._sf.query_all = AsyncMock(return_value=[
            {"Id": "001ACC", "OwnerId": "005OWN"},
        ])

        async def _mock_fetch_shares(obj_type, records):
            for r in records:
                owner_id = r.get("OwnerId", "")
                r["Shares"] = {
                    "records": [{
                        "UserOrGroupId": owner_id,
                        "UserOrGroup": {"Type": "User"},
                    }] if owner_id else []
                }

        builder._fetch_and_inject_shares = AsyncMock(side_effect=_mock_fetch_shares)

        records = [
            {"Id": "003VALID", "AccountId": "001ACC"},
            {"Id": "003ORPHAN"},  # No AccountId
        ]
        result = asyncio.run(builder._build_acl_map("Contact", records, {}))

        # Valid child inherits parent ACL
        valid_vals = [a["value"] for a in result["003VALID"]]
        assert "ContactGlobalUsers" in valid_vals
        assert "cccc1111-2222-3333-4444-555566667777" in valid_vals

        # Orphan gets deny-everyone
        orphan_acl = result["003ORPHAN"]
        assert orphan_acl[0]["accessType"] == "deny"

    def test_cbp_parent_fetch_soql_failure_deny_everyone(self):
        """If parent SOQL query fails, affected children get deny-everyone."""
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        # SOQL raises exception
        builder._sf.query_all = AsyncMock(side_effect=Exception("SOQL timeout"))

        async def _mock_fetch_shares(obj_type, records):
            for r in records:
                r["Shares"] = {"records": []}

        builder._fetch_and_inject_shares = AsyncMock(side_effect=_mock_fetch_shares)

        records = [{"Id": "003C1", "AccountId": "001ACC"}]
        result = asyncio.run(builder._build_acl_map("Contact", records, {}))
        acl = result["003C1"]
        assert acl[0]["accessType"] == "deny"
        assert acl[0]["value"] == "everyone"


# ── OWD override tests ──────────────────────────────────────────────────────


class TestOWDOverrides:
    def test_owd_override_changes_visibility(self):
        builder = _make_builder(owd_map={"Account": EntityVisibility.READ})
        # Override to private
        builder._owd_map["Account"] = EntityVisibility.NONE
        builder._users_by_id = {}
        builder._frozen_users = set()

        records = [{"Id": "001X", "Shares": {"records": []}}]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        # Should get GlobalUsers (private) not TopLevel (public)
        assert result["001X"][0]["value"] == "AccountGlobalUsers"


# ── Group ID consistency test ────────────────────────────────────────────────


class TestGroupIdConsistency:
    """Verify that group IDs used in ACLs match the format constants."""

    def test_public_acl_uses_grant_everyone(self):
        builder = _make_builder(owd_map={"Account": EntityVisibility.READ})
        records = [{"Id": "001X"}]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        assert result["001X"][0] == {"accessType": "grant", "type": "everyone", "value": "everyone"}

    def test_private_acl_uses_global_users_format(self):
        expected = SfGroupIdFormats.GLOBAL_USERS.format("Account")
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
        )
        records = [{"Id": "001X", "Shares": {"records": []}}]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        assert result["001X"][0]["value"] == expected

    def test_role_group_id_matches_format(self):
        expected = SfGroupIdFormats.ROLE.format("Account", "00E_ROLE1")
        group = SfGroup(id="00G1", type=UserOrGroupType.ROLE, related_id="00E_ROLE1")
        builder = _make_builder(
            owd_map={"Account": EntityVisibility.NONE},
            users=[],
            groups=[group],
        )
        records = [{
            "Id": "001X",
            "Shares": {"records": [{"UserOrGroupId": "00G1", "UserOrGroup": {"Type": "Queue"}}]},
        }]
        result = asyncio.run(builder._build_acl_map("Account", records, {}))
        group_values = [a["value"] for a in result["001X"] if a["type"] == "externalGroup"]
        assert expected in group_values
