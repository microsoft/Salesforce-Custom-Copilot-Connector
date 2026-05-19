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

    def test_controlled_by_parent_with_private_parent_uses_owner(self):
        owner = _make_sf_user(user_id="005OWNER", federation_id="a2b3c4d5-e6f7-8901-2345-678901234567")
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[owner],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        records = [{"Id": "003C1", "OwnerId": "005OWNER"}]
        result = asyncio.run(builder._build_acl_map("Contact", records, {}))
        acl = result["003C1"]
        assert any(a["value"] == "ContactGlobalUsers" for a in acl)
        assert any(a["value"] == "a2b3c4d5-e6f7-8901-2345-678901234567" and a["type"] == "user" for a in acl)

    def test_controlled_by_parent_without_owner(self):
        builder = _make_builder(
            owd_map={
                "Account": EntityVisibility.NONE,
                "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
            },
            users=[],
            parent_map={"Contact": ("AccountId", "Account")},
        )
        records = [{"Id": "003C1"}]
        result = asyncio.run(builder._build_acl_map("Contact", records, {}))
        acl = result["003C1"]
        # Only GlobalUsers, no owner
        assert len(acl) == 1
        assert acl[0]["value"] == "ContactGlobalUsers"


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
