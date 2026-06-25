# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Tests for acl_engine.identity_sync — Identity Crawl handler."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from acl_engine.group_id_formats import SfGroupIdFormats
from acl_engine.identity_models import (
    EntityVisibility,
    GroupIdentityType,
    SfGroup,
    SfUser,
    UserOrGroupType,
)
from acl_engine.identity_sync import (
    ChildGroupRef,
    IdentityCrawlEnumerator,
    IdentityGatherer,
    IdentitySyncHandler,
    TopLevelGroupInfo,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_user(user_id: str = "005U1", name: str = "User") -> SfUser:
    return SfUser(id=user_id, name=name, email=f"{name.lower()}@test.com")


def _mock_query_client(**overrides) -> MagicMock:
    """Create a mock IdentityQueryClient with async methods."""
    qc = MagicMock()

    owd_map = overrides.get("owd_map", {"Account": EntityVisibility.READ})
    qc.get_org_wide_defaults = AsyncMock(return_value=owd_map)
    qc.get_authorized_users = AsyncMock(return_value=overrides.get("authorized_users", []))
    qc.get_global_access_users = AsyncMock(return_value=overrides.get("global_users", []))
    qc.get_group_share_ids = AsyncMock(return_value=overrides.get("group_share_ids", []))
    qc.get_groups_by_ids = AsyncMock(return_value=overrides.get("groups", []))
    qc.get_role_hierarchy = AsyncMock(return_value=overrides.get("role_hierarchy", {}))
    qc.get_roles_assigned_to_users = AsyncMock(return_value=overrides.get("roles_assigned", set()))
    qc.get_users_for_role = AsyncMock(return_value=overrides.get("role_users", []))
    qc.get_group_member_users = AsyncMock(return_value=overrides.get("group_members", []))
    qc.get_user_by_id = AsyncMock(return_value=overrides.get("single_user", None))
    qc.get_manager_and_subordinates = AsyncMock(return_value=overrides.get("mgr_and_subs", []))
    qc.get_territory_users = AsyncMock(return_value=overrides.get("territory_users", []))
    qc.get_all_descendant_territory_ids = AsyncMock(return_value=overrides.get("descendant_territory_ids", set()))

    return qc


# ── IdentityCrawlEnumerator tests ────────────────────────────────────────────


class TestIdentityCrawlEnumerator:
    def test_emits_one_group_per_object(self):
        qc = _mock_query_client(owd_map={
            "Account": EntityVisibility.READ,
            "Lead": EntityVisibility.NONE,
        })
        enum = IdentityCrawlEnumerator(
            query_client=qc,
            object_names=["Account", "Lead"],
            parent_map={},
            owd_overrides={},
        )
        groups = asyncio.run(enum.enumerate())
        assert len(groups) == 2
        ids = {g.group_id for g in groups}
        assert "AccountTopLevel" in ids
        assert "LeadTopLevel" in ids

    def test_owd_is_preserved_in_metadata(self):
        qc = _mock_query_client(owd_map={"Account": EntityVisibility.NONE})
        enum = IdentityCrawlEnumerator(
            query_client=qc,
            object_names=["Account"],
            parent_map={},
            owd_overrides={},
        )
        groups = asyncio.run(enum.enumerate())
        assert groups[0].owd == EntityVisibility.NONE

    def test_controlled_by_parent_resolves_to_public(self):
        qc = _mock_query_client(owd_map={
            "Account": EntityVisibility.READ,
            "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
        })
        enum = IdentityCrawlEnumerator(
            query_client=qc,
            object_names=["Contact"],
            parent_map={"Contact": ("AccountId", "Account")},
            owd_overrides={},
        )
        groups = asyncio.run(enum.enumerate())
        assert groups[0].owd == EntityVisibility.READ

    def test_controlled_by_parent_stays_private_when_parent_private(self):
        qc = _mock_query_client(owd_map={
            "Account": EntityVisibility.NONE,
            "Contact": EntityVisibility.CONTROLLED_BY_PARENT,
        })
        enum = IdentityCrawlEnumerator(
            query_client=qc,
            object_names=["Contact"],
            parent_map={"Contact": ("AccountId", "Account")},
            owd_overrides={},
        )
        groups = asyncio.run(enum.enumerate())
        assert groups[0].owd == EntityVisibility.CONTROLLED_BY_PARENT

    def test_owd_overrides_applied(self):
        qc = _mock_query_client(owd_map={"Account": EntityVisibility.READ})
        enum = IdentityCrawlEnumerator(
            query_client=qc,
            object_names=["Account"],
            parent_map={},
            owd_overrides={"Account": "Private"},
        )
        groups = asyncio.run(enum.enumerate())
        assert groups[0].owd == EntityVisibility.NONE


# ── IdentityGatherer tests ───────────────────────────────────────────────────


class TestIdentityGathererPublic:
    def test_public_owd_skips_user_query(self):
        users = [_make_user("005A", "Alice"), _make_user("005B", "Bob")]
        qc = _mock_query_client(authorized_users=users)
        gatherer = IdentityGatherer(qc)

        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.READ)
        )
        assert result.group_id == "AccountTopLevel"
        # PUBLIC OWD uses grant-everyone on content items; no users needed in group
        assert len(result.users) == 0
        assert len(result.child_groups) == 0
        qc.get_authorized_users.assert_not_awaited()

    def test_public_owd_empty_group(self):
        qc = _mock_query_client(authorized_users=[])
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Lead", EntityVisibility.EDIT)
        )
        assert len(result.users) == 0
        qc.get_authorized_users.assert_not_awaited()


class TestIdentityGathererPrivate:
    def test_private_owd_creates_global_users_child(self):
        qc = _mock_query_client(
            owd_map={"Account": EntityVisibility.NONE},
            group_share_ids=[],
            role_hierarchy={},
            roles_assigned=set(),
        )
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.NONE)
        )
        assert len(result.users) == 0
        child_ids = [c.group_id for c in result.child_groups]
        assert "AccountGlobalUsers" in child_ids

    def test_private_owd_creates_role_groups_from_shares(self):
        role_group = SfGroup(
            id="00G_ROLE",
            type=UserOrGroupType.ROLE,
            related_id="00E_ROLE1",
        )
        qc = _mock_query_client(
            group_share_ids=["00G_ROLE"],
            groups=[role_group],
            role_hierarchy={"00E_ROLE1": ""},
            roles_assigned={"00E_ROLE1"},
        )
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.NONE)
        )
        child_ids = [c.group_id for c in result.child_groups]
        assert "Account00EROLE1Role" in child_ids

    def test_private_owd_creates_role_hierarchy(self):
        role_group = SfGroup(
            id="00G_ROLE",
            type=UserOrGroupType.ROLE,
            related_id="00E_CHILD",
        )
        qc = _mock_query_client(
            group_share_ids=["00G_ROLE"],
            groups=[role_group],
            role_hierarchy={"00E_CHILD": "00E_PARENT", "00E_PARENT": ""},
            roles_assigned={"00E_CHILD", "00E_PARENT"},
        )
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.NONE)
        )
        child_ids = [c.group_id for c in result.child_groups]
        assert "Account00ECHILDRole" in child_ids
        assert "Account00EPARENTRole" in child_ids

    def test_private_owd_organization_share(self):
        org_group = SfGroup(
            id="00G_ORG",
            type=UserOrGroupType.ORGANIZATION,
        )
        qc = _mock_query_client(
            group_share_ids=["00G_ORG"],
            groups=[org_group],
            role_hierarchy={},
            roles_assigned=set(),
        )
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.NONE)
        )
        child_ids = [c.group_id for c in result.child_groups]
        assert "AccountAllInternalUsers" in child_ids

    def test_private_owd_manager_share(self):
        mgr_group = SfGroup(
            id="00G_MGR",
            type=UserOrGroupType.MANAGER,
            related_id="005MGR1",
        )
        qc = _mock_query_client(
            group_share_ids=["00G_MGR"],
            groups=[mgr_group],
            role_hierarchy={},
            roles_assigned=set(),
        )
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.NONE)
        )
        child_ids = [c.group_id for c in result.child_groups]
        assert "Account005MGR1Manager" in child_ids

    def test_private_owd_public_group_share(self):
        pg_group = SfGroup(
            id="00G_PG",
            type=UserOrGroupType.REGULAR,
            group_members=["005M1", "005M2"],
        )
        qc = _mock_query_client(
            group_share_ids=["00G_PG"],
            groups=[pg_group],
            role_hierarchy={},
            roles_assigned=set(),
        )
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.NONE)
        )
        child_ids = [c.group_id for c in result.child_groups]
        assert "Account00GPGPublicGroup" in child_ids

    def test_empty_public_group_is_not_created(self):
        pg_group = SfGroup(
            id="00G_EMPTY",
            type=UserOrGroupType.REGULAR,
            group_members=[],
        )
        qc = _mock_query_client(
            group_share_ids=["00G_EMPTY"],
            groups=[pg_group],
            role_hierarchy={},
            roles_assigned=set(),
        )
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.NONE)
        )
        child_ids = [c.group_id for c in result.child_groups]
        assert "Account00G_EMPTYPublicGroup" not in child_ids

    def test_private_owd_territory_share(self):
        terr_group = SfGroup(
            id="00G_TERR",
            type=UserOrGroupType.TERRITORY,
            related_id="0ML_TERR1",
        )
        qc = _mock_query_client(
            group_share_ids=["00G_TERR"],
            groups=[terr_group],
            role_hierarchy={},
            roles_assigned=set(),
        )
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.NONE)
        )
        child_ids = [c.group_id for c in result.child_groups]
        assert "Account0MLTERR1Territory" in child_ids

    def test_private_owd_territory_and_subordinates_share(self):
        terr_group = SfGroup(
            id="00G_TERR_SUB",
            type=UserOrGroupType.TERRITORY_AND_SUBORDINATES,
            related_id="0ML_TERR2",
        )
        qc = _mock_query_client(
            group_share_ids=["00G_TERR_SUB"],
            groups=[terr_group],
            role_hierarchy={},
            roles_assigned=set(),
        )
        gatherer = IdentityGatherer(qc)
        result = asyncio.run(
            gatherer.build_top_level_group("Account", EntityVisibility.NONE)
        )
        child_ids = [c.group_id for c in result.child_groups]
        assert "Account0MLTERR2TerritoryAndSubordinates" in child_ids


class TestIdentityGathererChildGroups:
    def test_gather_global_access_users(self):
        users = [_make_user("005ADM", "Admin")]
        qc = _mock_query_client(global_users=users)
        gatherer = IdentityGatherer(qc)

        ref = ChildGroupRef(
            group_id="AccountGlobalUsers",
            group_type=GroupIdentityType.GLOBAL_ACCESS_USERS,
            metadata={"ChildGroupType": "GlobalAccessUsers"},
        )
        result = asyncio.run(gatherer.gather_child_group("Account", ref))
        assert len(result.users) == 1
        assert result.users[0].id == "005ADM"

    def test_gather_role_with_parent(self):
        users = [_make_user("005R1", "RoleUser")]
        qc = _mock_query_client(role_users=users)
        gatherer = IdentityGatherer(qc)

        ref = ChildGroupRef(
            group_id="Account00E_ROLE1Role",
            group_type=GroupIdentityType.ROLE_WITH_PARENT,
            metadata={"RoleId": "00E_ROLE1", "ParentRoleId": "00E_PARENT"},
        )
        result = asyncio.run(gatherer.gather_child_group("Account", ref))
        assert len(result.users) == 1
        assert len(result.child_groups) == 1
        assert result.child_groups[0].group_id == "Account00EPARENTRole"
        assert result.child_groups[0].needs_gather is False

    def test_gather_role_without_parent(self):
        qc = _mock_query_client(role_users=[_make_user()])
        gatherer = IdentityGatherer(qc)

        ref = ChildGroupRef(
            group_id="Account00E_ROOTRole",
            group_type=GroupIdentityType.ROLE_WITHOUT_PARENT,
            metadata={"RoleId": "00E_ROOT", "ParentRoleId": ""},
        )
        result = asyncio.run(gatherer.gather_child_group("Account", ref))
        assert len(result.child_groups) == 0

    def test_gather_all_internal_users(self):
        users = [_make_user("005A"), _make_user("005B")]
        qc = _mock_query_client(authorized_users=users)
        gatherer = IdentityGatherer(qc)

        ref = ChildGroupRef(
            group_id="AccountAllInternalUsers",
            group_type=GroupIdentityType.ALL_INTERNAL_USERS,
            metadata={},
        )
        result = asyncio.run(gatherer.gather_child_group("Account", ref))
        assert len(result.users) == 2

    def test_gather_public_group(self):
        members = [_make_user("005M1"), _make_user("005M2")]
        qc = _mock_query_client(group_members=members)
        gatherer = IdentityGatherer(qc)

        ref = ChildGroupRef(
            group_id="Account00G_PGPublicGroup",
            group_type=GroupIdentityType.PUBLIC_GROUP,
            metadata={"PublicGroupId": "00G_PG"},
        )
        result = asyncio.run(gatherer.gather_child_group("Account", ref))
        assert len(result.users) == 2

    def test_gather_manager(self):
        manager = _make_user("005MGR", "Manager")
        qc = _mock_query_client(single_user=manager)
        gatherer = IdentityGatherer(qc)

        ref = ChildGroupRef(
            group_id="Account005MGRManager",
            group_type=GroupIdentityType.MANAGER,
            metadata={"RelatedId": "005MGR"},
        )
        result = asyncio.run(gatherer.gather_child_group("Account", ref))
        assert len(result.users) == 1
        assert result.users[0].id == "005MGR"

    def test_gather_manager_and_subordinates(self):
        users = [_make_user("005MGR"), _make_user("005SUB1"), _make_user("005SUB2")]
        qc = _mock_query_client(mgr_and_subs=users)
        gatherer = IdentityGatherer(qc)

        ref = ChildGroupRef(
            group_id="Account005MGRManagerAndSubordinates",
            group_type=GroupIdentityType.MANAGER_AND_SUBORDINATES,
            metadata={"RelatedId": "005MGR"},
        )
        result = asyncio.run(gatherer.gather_child_group("Account", ref))
        assert len(result.users) == 3

    def test_gather_territory(self):
        users = [_make_user("005T1", "TerrUser"), _make_user("005T2", "TerrUser2")]
        qc = _mock_query_client(territory_users=users)
        gatherer = IdentityGatherer(qc)

        ref = ChildGroupRef(
            group_id="Account0ML_T1Territory",
            group_type=GroupIdentityType.TERRITORY,
            metadata={"RelatedId": "0ML_T1"},
        )
        result = asyncio.run(gatherer.gather_child_group("Account", ref))
        assert len(result.users) == 2

    def test_gather_territory_and_subordinates(self):
        users = [_make_user("005T1", "TerrUser")]
        qc = _mock_query_client(
            territory_users=users,
            descendant_territory_ids={"0ML_CHILD1"},
        )
        gatherer = IdentityGatherer(qc)

        ref = ChildGroupRef(
            group_id="Account0ML_T1TerritoryAndSubordinates",
            group_type=GroupIdentityType.TERRITORY_AND_SUBORDINATES,
            metadata={"RelatedId": "0ML_T1"},
        )
        result = asyncio.run(gatherer.gather_child_group("Account", ref))
        # get_territory_users called for root + 1 descendant; same users deduped
        assert len(result.users) >= 1


# ── IdentitySyncHandler integration tests ────────────────────────────────────


class TestIdentitySyncHandler:
    def test_full_crawl_public_object(self):
        with patch("acl_engine.identity_sync.IdentityQueryClient") as MockQC:
            instance = MockQC.return_value
            instance.get_org_wide_defaults = AsyncMock(return_value={"Account": EntityVisibility.READ})
            instance.get_authorized_users = AsyncMock(return_value=[])

            handler = IdentitySyncHandler(
                sf_client=MagicMock(),
                object_names=["Account"],
            )
            handler._query_client = instance

            result = handler.run_full_crawl()

            assert len(result.top_level_groups) == 1
            assert result.top_level_groups[0].owd == EntityVisibility.READ
            # PUBLIC OWD: no users emitted (grant-everyone used on content items)
            assert result.total_users_emitted == 0
            assert result.total_groups_emitted >= 1
            instance.get_authorized_users.assert_not_awaited()

    def test_incremental_crawl_same_as_full(self):
        with patch("acl_engine.identity_sync.IdentityQueryClient") as MockQC:
            instance = MockQC.return_value
            instance.get_org_wide_defaults = AsyncMock(return_value={"Lead": EntityVisibility.EDIT})
            instance.get_authorized_users = AsyncMock(return_value=[])

            handler = IdentitySyncHandler(
                sf_client=MagicMock(),
                object_names=["Lead"],
            )
            handler._query_client = instance

            full_result = handler.run_full_crawl()
            incr_result = handler.run_incremental_crawl()

            assert len(full_result.top_level_groups) == len(incr_result.top_level_groups)


# ── Group ID consistency between identity sync and ACL builder ───────────────


class TestGroupIdConsistencyAcrossModules:
    """
    Critical verification: group IDs used in identity crawl MUST exactly match
    group IDs referenced in content ACLs.
    """

    def test_top_level_id_matches(self):
        # Identity crawl format
        identity_id = SfGroupIdFormats.TOP_LEVEL.format("Account")
        # ACL builder format (same constant)
        acl_id = SfGroupIdFormats.TOP_LEVEL.format("Account")
        assert identity_id == acl_id == "AccountTopLevel"

    def test_global_users_id_matches(self):
        identity_id = SfGroupIdFormats.GLOBAL_USERS.format("Account")
        acl_id = SfGroupIdFormats.GLOBAL_USERS.format("Account")
        assert identity_id == acl_id == "AccountGlobalUsers"

    def test_role_id_matches(self):
        role_id = "00E5g000001ABC"
        identity_id = SfGroupIdFormats.ROLE.format("Account", role_id)
        acl_id = SfGroupIdFormats.ROLE.format("Account", role_id)
        assert identity_id == acl_id == f"Account{role_id}Role"

    def test_all_internal_users_id_matches(self):
        identity_id = SfGroupIdFormats.ALL_INTERNAL_USERS.format("Account")
        acl_id = SfGroupIdFormats.ALL_INTERNAL_USERS.format("Account")
        assert identity_id == acl_id == "AccountAllInternalUsers"

    def test_manager_id_matches(self):
        user_id = "005DEF"
        identity_id = SfGroupIdFormats.MANAGER.format("Account", user_id)
        acl_id = SfGroupIdFormats.MANAGER.format("Account", user_id)
        assert identity_id == acl_id == f"Account{user_id}Manager"

    def test_public_group_id_matches(self):
        group_id = "00G_XYZ"
        identity_id = SfGroupIdFormats.PUBLIC_GROUP.format("Account", group_id)
        acl_id = SfGroupIdFormats.PUBLIC_GROUP.format("Account", group_id)
        assert identity_id == acl_id == "Account00GXYZPublicGroup"

    def test_territory_id_matches(self):
        terr_id = "0ML_ABC"
        identity_id = SfGroupIdFormats.TERRITORY.format("Account", terr_id)
        acl_id = SfGroupIdFormats.TERRITORY.format("Account", terr_id)
        assert identity_id == acl_id == "Account0MLABCTerritory"

    def test_territory_and_subordinates_id_matches(self):
        terr_id = "0ML_ABC"
        identity_id = SfGroupIdFormats.TERRITORY_AND_SUBORDINATES.format("Account", terr_id)
        acl_id = SfGroupIdFormats.TERRITORY_AND_SUBORDINATES.format("Account", terr_id)
        assert identity_id == acl_id == "Account0MLABCTerritoryAndSubordinates"
