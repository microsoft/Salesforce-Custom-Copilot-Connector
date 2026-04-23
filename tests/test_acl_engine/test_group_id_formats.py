"""Tests for acl_engine.group_id_formats — External group ID format constants."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from acl_engine.group_id_formats import SfGroupIdFormats


class TestSfGroupIdFormats:
    """Verify format strings produce correct group IDs."""

    def test_top_level(self):
        assert SfGroupIdFormats.TOP_LEVEL.format("Account") == "Account-TopLevel"

    def test_global_users(self):
        assert SfGroupIdFormats.GLOBAL_USERS.format("Lead") == "Lead-GlobalUsers"

    def test_all_internal_users(self):
        assert SfGroupIdFormats.ALL_INTERNAL_USERS.format("Case") == "Case-AllInternalUsers"

    def test_role(self):
        assert SfGroupIdFormats.ROLE.format("Account", "00E123") == "Account-00E123-Role"

    def test_role_and_subordinates(self):
        assert SfGroupIdFormats.ROLE_AND_SUBORDINATES.format("Account", "00E123") == "Account-00E123-RoleAndSubordinates"

    def test_role_no_parents(self):
        assert SfGroupIdFormats.ROLE_NO_PARENTS.format("Account", "00E123") == "Account-00E123-RoleNoParents"

    def test_role_and_subordinates_no_parents(self):
        assert SfGroupIdFormats.ROLE_AND_SUBORDINATES_NO_PARENTS.format("Account", "00E123") == "Account-00E123-RoleAndSubordinatesNoParents"

    def test_public_group(self):
        assert SfGroupIdFormats.PUBLIC_GROUP.format("Account", "00G456") == "Account-00G456-PublicGroup"

    def test_manager(self):
        assert SfGroupIdFormats.MANAGER.format("Opportunity", "005789") == "Opportunity-005789-Manager"

    def test_manager_and_subordinates(self):
        assert SfGroupIdFormats.MANAGER_AND_SUBORDINATES.format("Opportunity", "005789") == "Opportunity-005789-ManagerAndSubordinates"

    def test_territory(self):
        assert SfGroupIdFormats.TERRITORY.format("Account", "0ML123") == "Account-0ML123-Territory"

    def test_territory_and_subordinates(self):
        assert SfGroupIdFormats.TERRITORY_AND_SUBORDINATES.format("Account", "0ML123") == "Account-0ML123-TerritoryAndSubordinates"

    def test_formats_are_deterministic(self):
        """Same inputs always produce the same output."""
        for _ in range(10):
            assert SfGroupIdFormats.ROLE.format("Account", "00EABC") == "Account-00EABC-Role"

    def test_different_objects_produce_different_ids(self):
        assert SfGroupIdFormats.TOP_LEVEL.format("Account") != SfGroupIdFormats.TOP_LEVEL.format("Lead")

    def test_18_char_salesforce_id(self):
        """Verify typical 18-char Salesforce IDs work correctly."""
        sf_id = "00E5g000001ABCdEF"
        result = SfGroupIdFormats.ROLE.format("Account", sf_id)
        assert result == f"Account-{sf_id}-Role"
        assert sf_id in result
