# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

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
        assert SfGroupIdFormats.TOP_LEVEL.format("Account") == "AccountTopLevel"

    def test_global_users(self):
        assert SfGroupIdFormats.GLOBAL_USERS.format("Lead") == "LeadGlobalUsers"

    def test_all_internal_users(self):
        assert SfGroupIdFormats.ALL_INTERNAL_USERS.format("Case") == "CaseAllInternalUsers"

    def test_role(self):
        assert SfGroupIdFormats.ROLE.format("Account", "00E123") == "Account00E123Role"

    def test_role_and_subordinates(self):
        assert SfGroupIdFormats.ROLE_AND_SUBORDINATES.format("Account", "00E123") == "Account00E123RoleAndSubordinates"

    def test_role_no_parents(self):
        assert SfGroupIdFormats.ROLE_NO_PARENTS.format("Account", "00E123") == "Account00E123RoleNoParents"

    def test_role_and_subordinates_no_parents(self):
        assert SfGroupIdFormats.ROLE_AND_SUBORDINATES_NO_PARENTS.format("Account", "00E123") == "Account00E123RoleAndSubordinatesNoParents"

    def test_public_group(self):
        assert SfGroupIdFormats.PUBLIC_GROUP.format("Account", "00G456") == "Account00G456PublicGroup"

    def test_manager(self):
        assert SfGroupIdFormats.MANAGER.format("Opportunity", "005789") == "Opportunity005789Manager"

    def test_manager_and_subordinates(self):
        assert SfGroupIdFormats.MANAGER_AND_SUBORDINATES.format("Opportunity", "005789") == "Opportunity005789ManagerAndSubordinates"

    def test_territory(self):
        assert SfGroupIdFormats.TERRITORY.format("Account", "0ML123") == "Account0ML123Territory"

    def test_territory_and_subordinates(self):
        assert SfGroupIdFormats.TERRITORY_AND_SUBORDINATES.format("Account", "0ML123") == "Account0ML123TerritoryAndSubordinates"

    def test_formats_are_deterministic(self):
        """Same inputs always produce the same output."""
        for _ in range(10):
            assert SfGroupIdFormats.ROLE.format("Account", "00EABC") == "Account00EABCRole"

    def test_different_objects_produce_different_ids(self):
        assert SfGroupIdFormats.TOP_LEVEL.format("Account") != SfGroupIdFormats.TOP_LEVEL.format("Lead")

    def test_18_char_salesforce_id(self):
        """Verify typical 18-char Salesforce IDs work correctly."""
        sf_id = "00E5g000001ABCdEF"
        result = SfGroupIdFormats.ROLE.format("Account", sf_id)
        assert result == f"Account{sf_id}Role"
        assert sf_id in result

    # ── Sanitization tests ────────────────────────────────────────────────

    def test_custom_object_underscores_stripped(self):
        """Custom object names like 'My_Custom__c' must produce alphanumeric IDs."""
        result = SfGroupIdFormats.TOP_LEVEL.format("Account_Owner_Name__c")
        assert result == "AccountOwnerNamecTopLevel"
        assert "_" not in result

    def test_custom_object_role_stripped(self):
        result = SfGroupIdFormats.ROLE.format("ACS_Customer__c", "00E123")
        assert result == "ACSCustomerc00E123Role"
        assert "_" not in result

    def test_hyphens_stripped(self):
        result = SfGroupIdFormats.PUBLIC_GROUP.format("Account", "00G-456-XYZ")
        assert result == "Account00G456XYZPublicGroup"
        assert "-" not in result

    def test_spaces_stripped(self):
        result = SfGroupIdFormats.GLOBAL_USERS.format("My Object")
        assert result == "MyObjectGlobalUsers"
        assert " " not in result

    def test_clean_input_unchanged(self):
        """Already-clean inputs produce the same result as before."""
        assert SfGroupIdFormats.TOP_LEVEL.format("Account") == "AccountTopLevel"
        assert SfGroupIdFormats.ROLE.format("Lead", "00E123") == "Lead00E123Role"
