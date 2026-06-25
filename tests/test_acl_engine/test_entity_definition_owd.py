# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""
Tests for EntityDefinition-based OWD fetching.

Covers both OWDFetcher (org_wide_defaults.py) and IdentityQueryClient
(identity_queries.py) EntityDefinition paths, including:
  - Happy path: EntityDefinition resolves all objects
  - Partial resolution: some objects fall back to Organization table
  - Query failure: graceful fallback to Organization table
  - Value mapping: all InternalSharingModel values → correct OWD values
  - OWD overrides applied on top of EntityDefinition results
  - Flag off: old behaviour unchanged
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from acl_engine.models import OWDVisibility
from acl_engine.org_wide_defaults import OWDFetcher, _ENTITY_DEF_TO_OWD_VALUE
from acl_engine.identity_models import EntityVisibility
from acl_engine.identity_queries import IdentityQueryClient, _ENTITY_DEF_TO_VISIBILITY


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_sf_client(
    entity_def_records: list[dict] | None = None,
    org_records: list[dict] | None = None,
    entity_def_error: Exception | None = None,
    org_error: Exception | None = None,
    org_describe_fields: list[str] | None = None,
) -> MagicMock:
    """Build a mock SalesforceClient with configurable query_all/query responses."""
    sf = MagicMock()

    async def _query_all(soql: str, *, tooling: bool = False) -> list[dict]:
        if tooling:
            if entity_def_error:
                raise entity_def_error
            return entity_def_records if entity_def_records is not None else []
        if org_error:
            raise org_error
        return org_records if org_records is not None else []

    sf.query_all = AsyncMock(side_effect=_query_all)

    async def _query(soql: str, *, tooling: bool = False) -> dict:
        if tooling:
            if entity_def_error:
                raise entity_def_error
            return {"records": entity_def_records or []}
        if org_error:
            raise org_error
        return {"records": org_records or []}

    sf.query = AsyncMock(side_effect=_query)

    if org_describe_fields is not None:
        sf.describe_sobject = AsyncMock(return_value={
            "fields": [{"name": f} for f in org_describe_fields]
        })
    else:
        sf.describe_sobject = AsyncMock(return_value={
            "fields": [{"name": "DefaultAccountAccess"}, {"name": "DefaultCaseAccess"}]
        })

    return sf


# ═══════════════════════════════════════════════════════════════════════════════
#  OWDFetcher (org_wide_defaults.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOWDFetcherEntityDefinition:
    """Tests for OWDFetcher with USE_ENTITY_DEFINITION_OWD=true."""

    # ── Value Mapping ─────────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "internal_sharing_model, expected_owd",
        [
            ("Private", OWDVisibility.PRIVATE.value),
            ("Read", OWDVisibility.PUBLIC_READ.value),
            ("ReadSelect", OWDVisibility.PUBLIC_READ.value),
            ("ReadWrite", OWDVisibility.PUBLIC_READ_WRITE.value),
            ("ReadWriteTransfer", OWDVisibility.PUBLIC_READ_WRITE_TRANSFER.value),
            ("FullAccess", OWDVisibility.ALL.value),
            ("ControlledByParent", OWDVisibility.CONTROLLED_BY_PARENT.value),
            ("ControlledByCampaign", OWDVisibility.CONTROLLED_BY_CAMPAIGN.value),
            ("ControlledByLeadOrContact", OWDVisibility.CONTROLLED_BY_LEAD_OR_CONTACT.value),
        ],
    )
    def test_entity_def_value_mapping(self, internal_sharing_model: str, expected_owd: str):
        """Each InternalSharingModel value maps to the correct OWDVisibility value."""
        sf = _mock_sf_client(entity_def_records=[
            {"QualifiedApiName": "TestObj", "InternalSharingModel": internal_sharing_model},
        ])
        fetcher = OWDFetcher(
            sf, owd_field_map={}, use_entity_definition_owd=True, object_names=["TestObj"],
        )
        result = asyncio.run(fetcher.get_owd("TestObj"))
        assert result == expected_owd

    def test_unknown_sharing_model_defaults_to_private(self):
        """An unrecognised InternalSharingModel value should default to Private."""
        sf = _mock_sf_client(entity_def_records=[
            {"QualifiedApiName": "TestObj", "InternalSharingModel": "SomeFutureValue"},
        ])
        fetcher = OWDFetcher(
            sf, owd_field_map={}, use_entity_definition_owd=True, object_names=["TestObj"],
        )
        result = asyncio.run(fetcher.get_owd("TestObj"))
        assert result == OWDVisibility.PRIVATE.value

    def test_null_sharing_model_defaults_to_private(self):
        """A null InternalSharingModel should default to Private."""
        sf = _mock_sf_client(entity_def_records=[
            {"QualifiedApiName": "TestObj", "InternalSharingModel": None},
        ])
        fetcher = OWDFetcher(
            sf, owd_field_map={}, use_entity_definition_owd=True, object_names=["TestObj"],
        )
        result = asyncio.run(fetcher.get_owd("TestObj"))
        assert result == OWDVisibility.PRIVATE.value

    # ── Happy Path ────────────────────────────────────────────────────────────

    def test_all_objects_resolved_via_entity_definition(self):
        """When EntityDefinition returns all objects, no Organization query is needed."""
        sf = _mock_sf_client(entity_def_records=[
            {"QualifiedApiName": "Account", "InternalSharingModel": "ReadWrite"},
            {"QualifiedApiName": "Contact", "InternalSharingModel": "ControlledByParent"},
        ])
        fetcher = OWDFetcher(
            sf,
            owd_field_map={"Account": "DefaultAccountAccess"},
            use_entity_definition_owd=True,
            object_names=["Account", "Contact"],
        )
        assert asyncio.run(fetcher.get_owd("Account")) == "Edit"
        assert asyncio.run(fetcher.get_owd("Contact")) == "ControlledByParent"
        # EntityDefinition query should be tooling=True; Organization query should NOT fire
        calls = sf.query_all.call_args_list
        assert len(calls) == 1  # only one query_all call (the EntityDefinition one)
        assert calls[0].kwargs.get("tooling") is True

    def test_cache_primed_once_across_multiple_get_owd_calls(self):
        """The EntityDefinition query fires only once regardless of how many get_owd calls."""
        sf = _mock_sf_client(entity_def_records=[
            {"QualifiedApiName": "Account", "InternalSharingModel": "Private"},
            {"QualifiedApiName": "Contact", "InternalSharingModel": "Read"},
        ])
        fetcher = OWDFetcher(
            sf, owd_field_map={}, use_entity_definition_owd=True,
            object_names=["Account", "Contact"],
        )
        asyncio.run(fetcher.get_owd("Account"))
        asyncio.run(fetcher.get_owd("Contact"))
        asyncio.run(fetcher.get_owd("Account"))
        # Only one tooling query should have been made
        tooling_calls = [c for c in sf.query_all.call_args_list if c.kwargs.get("tooling")]
        assert len(tooling_calls) == 1

    # ── Fallback to Organization Table ────────────────────────────────────────

    def test_object_missing_from_entity_def_falls_back_to_org_table(self):
        """If EntityDefinition doesn't return an object, fall back to Organization query."""
        sf = _mock_sf_client(
            entity_def_records=[
                {"QualifiedApiName": "Account", "InternalSharingModel": "ReadWrite"},
                # Contact missing from EntityDefinition
            ],
            org_records=[{"DefaultAccountAccess": "Private"}],  # Organization table
        )
        fetcher = OWDFetcher(
            sf,
            owd_field_map={"Account": "DefaultAccountAccess"},
            use_entity_definition_owd=True,
            object_names=["Account", "Contact"],
        )
        # Account resolved via EntityDefinition
        assert asyncio.run(fetcher.get_owd("Account")) == "Edit"
        # Contact: not in EntityDefinition, not in owd_field_map → Private
        assert asyncio.run(fetcher.get_owd("Contact")) == "Private"

    def test_entity_def_query_failure_falls_back_to_org_table(self):
        """If the EntityDefinition query fails entirely, fall back to Organization table."""
        sf = _mock_sf_client(
            entity_def_error=RuntimeError("Tooling API unavailable"),
            org_records=[{"DefaultAccountAccess": "Read"}],
        )
        fetcher = OWDFetcher(
            sf,
            owd_field_map={"Account": "DefaultAccountAccess"},
            use_entity_definition_owd=True,
            object_names=["Account"],
        )
        # Should fall back to Organization query → "Read"
        result = asyncio.run(fetcher.get_owd("Account"))
        assert result == "Read"

    def test_entity_def_query_failure_object_without_owd_field_defaults_private(self):
        """If EntityDefinition fails and object has no owdField, default to Private."""
        sf = _mock_sf_client(
            entity_def_error=RuntimeError("Tooling API unavailable"),
        )
        fetcher = OWDFetcher(
            sf,
            owd_field_map={},  # Contact has no owdField
            use_entity_definition_owd=True,
            object_names=["Contact"],
        )
        result = asyncio.run(fetcher.get_owd("Contact"))
        assert result == "Private"

    # ── OWD Overrides ─────────────────────────────────────────────────────────

    def test_owd_override_applied_on_top_of_entity_def(self):
        """OWD_OVERRIDES config should override EntityDefinition values."""
        sf = _mock_sf_client(entity_def_records=[
            {"QualifiedApiName": "Account", "InternalSharingModel": "ReadWrite"},
        ])
        fetcher = OWDFetcher(
            sf,
            owd_field_map={},
            owd_overrides={"Account": "Private"},
            use_entity_definition_owd=True,
            object_names=["Account"],
        )
        result = asyncio.run(fetcher.get_owd("Account"))
        assert result == "Private"

    # ── Flag Off ──────────────────────────────────────────────────────────────

    def test_flag_off_uses_org_table_only(self):
        """When use_entity_definition_owd=False, only the Organization table is queried."""
        sf = _mock_sf_client(
            entity_def_records=[
                {"QualifiedApiName": "Account", "InternalSharingModel": "FullAccess"},
            ],
            org_records=[{"DefaultAccountAccess": "Private"}],
        )
        fetcher = OWDFetcher(
            sf,
            owd_field_map={"Account": "DefaultAccountAccess"},
            use_entity_definition_owd=False,
            object_names=["Account"],
        )
        result = asyncio.run(fetcher.get_owd("Account"))
        assert result == "Private"  # Organization table value, NOT EntityDefinition
        # No tooling query should have been made
        tooling_calls = [c for c in sf.query_all.call_args_list if c.kwargs.get("tooling")]
        assert len(tooling_calls) == 0

    # ── Predicates ────────────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "sharing_model, expect_public",
        [
            ("ReadWrite", True),
            ("Read", True),
            ("ReadSelect", True),
            ("ReadWriteTransfer", True),
            ("FullAccess", True),
            ("Private", False),
            ("ControlledByParent", False),
            ("ControlledByCampaign", False),
        ],
    )
    def test_is_public_after_entity_def_mapping(self, sharing_model: str, expect_public: bool):
        """Mapped EntityDefinition values should produce correct is_public() results."""
        sf = _mock_sf_client(entity_def_records=[
            {"QualifiedApiName": "TestObj", "InternalSharingModel": sharing_model},
        ])
        fetcher = OWDFetcher(
            sf, owd_field_map={}, use_entity_definition_owd=True, object_names=["TestObj"],
        )
        owd = asyncio.run(fetcher.get_owd("TestObj"))
        assert OWDFetcher.is_public(owd) is expect_public

    @pytest.mark.parametrize(
        "sharing_model, expect_cbp",
        [
            ("ControlledByParent", True),
            ("ControlledByCampaign", True),
            ("ControlledByLeadOrContact", True),
            ("ReadWrite", False),
            ("Private", False),
        ],
    )
    def test_is_controlled_by_parent_after_entity_def_mapping(
        self, sharing_model: str, expect_cbp: bool,
    ):
        """Mapped EntityDefinition values should produce correct is_controlled_by_parent() results."""
        sf = _mock_sf_client(entity_def_records=[
            {"QualifiedApiName": "TestObj", "InternalSharingModel": sharing_model},
        ])
        fetcher = OWDFetcher(
            sf, owd_field_map={}, use_entity_definition_owd=True, object_names=["TestObj"],
        )
        owd = asyncio.run(fetcher.get_owd("TestObj"))
        assert OWDFetcher.is_controlled_by_parent(owd) is expect_cbp


# ═══════════════════════════════════════════════════════════════════════════════
#  IdentityQueryClient (identity_queries.py)
# ═══════════════════════════════════════════════════════════════════════════════

class TestIdentityQueryClientEntityDefinition:
    """Tests for IdentityQueryClient.get_org_wide_defaults with EntityDefinition."""

    # ── Value Mapping ─────────────────────────────────────────────────────────

    @pytest.mark.parametrize(
        "internal_sharing_model, expected_visibility",
        [
            ("Private", EntityVisibility.NONE),
            ("Read", EntityVisibility.READ),
            ("ReadSelect", EntityVisibility.READ),
            ("ReadWrite", EntityVisibility.EDIT),
            ("ReadWriteTransfer", EntityVisibility.READ_EDIT_TRANSFER),
            ("FullAccess", EntityVisibility.READ_EDIT_TRANSFER),
            ("ControlledByParent", EntityVisibility.CONTROLLED_BY_PARENT),
            ("ControlledByCampaign", EntityVisibility.CONTROLLED_BY_CAMPAIGN),
            ("ControlledByLeadOrContact", EntityVisibility.CONTROLLED_BY_LEAD_OR_CONTACT),
        ],
    )
    def test_entity_def_value_mapping(
        self, internal_sharing_model: str, expected_visibility: EntityVisibility,
    ):
        """Each InternalSharingModel value maps to the correct EntityVisibility."""
        sf = _mock_sf_client(entity_def_records=[
            {"QualifiedApiName": "TestObj", "InternalSharingModel": internal_sharing_model},
        ])
        qc = IdentityQueryClient(
            sf, owd_field_map={}, use_entity_definition_owd=True, object_names=["TestObj"],
        )
        result = asyncio.run(qc.get_org_wide_defaults())
        assert result["TestObj"] == expected_visibility

    # ── Happy Path ────────────────────────────────────────────────────────────

    def test_all_objects_resolved_via_entity_definition(self):
        """EntityDefinition resolves all objects; no Organization query needed."""
        sf = _mock_sf_client(
            entity_def_records=[
                {"QualifiedApiName": "Account", "InternalSharingModel": "ReadWrite"},
                {"QualifiedApiName": "Contact", "InternalSharingModel": "ControlledByParent"},
                {"QualifiedApiName": "Case", "InternalSharingModel": "Private"},
            ],
            org_describe_fields=["DefaultAccountAccess", "DefaultCaseAccess"],
        )
        qc = IdentityQueryClient(
            sf,
            owd_field_map={"Account": "DefaultAccountAccess", "Case": "DefaultCaseAccess"},
            use_entity_definition_owd=True,
            object_names=["Account", "Contact", "Case"],
        )
        result = asyncio.run(qc.get_org_wide_defaults())
        assert result["Account"] == EntityVisibility.EDIT
        assert result["Contact"] == EntityVisibility.CONTROLLED_BY_PARENT
        assert result["Case"] == EntityVisibility.NONE

    # ── Fallback to Organization Table ────────────────────────────────────────

    def test_partial_entity_def_falls_back_for_remaining(self):
        """Objects missing from EntityDefinition should fall back to Organization query."""
        sf = _mock_sf_client(
            entity_def_records=[
                {"QualifiedApiName": "Account", "InternalSharingModel": "ReadWrite"},
                # Case missing from EntityDefinition
            ],
            org_records=[{"DefaultCaseAccess": "Read"}],
            org_describe_fields=["DefaultCaseAccess"],
        )
        qc = IdentityQueryClient(
            sf,
            owd_field_map={"Case": "DefaultCaseAccess"},
            use_entity_definition_owd=True,
            object_names=["Account", "Case"],
        )
        result = asyncio.run(qc.get_org_wide_defaults())
        assert result["Account"] == EntityVisibility.EDIT
        assert result["Case"] == EntityVisibility.READ

    def test_entity_def_failure_falls_back_to_org_table(self):
        """If EntityDefinition query fails, all objects fall back to Organization."""
        sf = _mock_sf_client(
            entity_def_error=RuntimeError("Tooling API unavailable"),
            org_records=[{"DefaultAccountAccess": "Edit"}],
            org_describe_fields=["DefaultAccountAccess"],
        )
        qc = IdentityQueryClient(
            sf,
            owd_field_map={"Account": "DefaultAccountAccess"},
            use_entity_definition_owd=True,
            object_names=["Account"],
        )
        result = asyncio.run(qc.get_org_wide_defaults())
        assert result["Account"] == EntityVisibility.EDIT

    # ── Flag Off ──────────────────────────────────────────────────────────────

    def test_flag_off_uses_org_table_only(self):
        """When flag is off, only the Organization table is queried."""
        sf = _mock_sf_client(
            entity_def_records=[
                {"QualifiedApiName": "Account", "InternalSharingModel": "FullAccess"},
            ],
            org_records=[{"DefaultAccountAccess": "Private"}],
            org_describe_fields=["DefaultAccountAccess"],
        )
        qc = IdentityQueryClient(
            sf,
            owd_field_map={"Account": "DefaultAccountAccess"},
            use_entity_definition_owd=False,
        )
        result = asyncio.run(qc.get_org_wide_defaults())
        assert result["Account"] == EntityVisibility.NONE  # "Private" from Org table

    # ── Empty / Edge Cases ────────────────────────────────────────────────────

    def test_no_object_names_returns_org_table_results(self):
        """Empty object_names list should skip EntityDefinition and use Organization."""
        sf = _mock_sf_client(
            org_records=[{"DefaultAccountAccess": "Read"}],
            org_describe_fields=["DefaultAccountAccess"],
        )
        qc = IdentityQueryClient(
            sf,
            owd_field_map={"Account": "DefaultAccountAccess"},
            use_entity_definition_owd=True,
            object_names=[],
        )
        result = asyncio.run(qc.get_org_wide_defaults())
        assert result["Account"] == EntityVisibility.READ

    def test_entity_def_returns_empty_records(self):
        """EntityDefinition returns 0 records → all objects fall back to Organization."""
        sf = _mock_sf_client(
            entity_def_records=[],
            org_records=[{"DefaultAccountAccess": "Edit"}],
            org_describe_fields=["DefaultAccountAccess"],
        )
        qc = IdentityQueryClient(
            sf,
            owd_field_map={"Account": "DefaultAccountAccess"},
            use_entity_definition_owd=True,
            object_names=["Account"],
        )
        result = asyncio.run(qc.get_org_wide_defaults())
        assert result["Account"] == EntityVisibility.EDIT


# ═══════════════════════════════════════════════════════════════════════════════
#  Mapping constant completeness
# ═══════════════════════════════════════════════════════════════════════════════

class TestMappingConsistency:
    """Verify the two mapping dicts cover the same set of EntityDefinition values."""

    def test_both_mappings_cover_same_keys(self):
        """_ENTITY_DEF_TO_OWD_VALUE and _ENTITY_DEF_TO_VISIBILITY must cover the same keys."""
        assert set(_ENTITY_DEF_TO_OWD_VALUE.keys()) == set(_ENTITY_DEF_TO_VISIBILITY.keys())

    @pytest.mark.parametrize("key", list(_ENTITY_DEF_TO_OWD_VALUE.keys()))
    def test_owd_mapping_produces_valid_owd_visibility_value(self, key: str):
        """Every mapped value must be a valid OWDVisibility member value."""
        valid_values = {v.value for v in OWDVisibility}
        assert _ENTITY_DEF_TO_OWD_VALUE[key] in valid_values

    @pytest.mark.parametrize("key", list(_ENTITY_DEF_TO_VISIBILITY.keys()))
    def test_visibility_mapping_produces_valid_entity_visibility(self, key: str):
        """Every mapped value must be a valid EntityVisibility member."""
        assert isinstance(_ENTITY_DEF_TO_VISIBILITY[key], EntityVisibility)

    def test_public_values_agree_between_mappings(self):
        """Public EntityDefinition values should map to public in both OWDFetcher and IdentityQueryClient."""
        from acl_engine.identity_models import is_public_visibility
        public_ed_values = {"Read", "ReadSelect", "ReadWrite", "ReadWriteTransfer", "FullAccess"}
        for key in public_ed_values:
            assert OWDFetcher.is_public(_ENTITY_DEF_TO_OWD_VALUE[key]), f"{key} should be public in OWDFetcher"
            assert is_public_visibility(_ENTITY_DEF_TO_VISIBILITY[key]), f"{key} should be public in IdentityQueryClient"

    def test_private_values_agree_between_mappings(self):
        """Private EntityDefinition value should map to private in both paths."""
        from acl_engine.identity_models import is_private_visibility
        assert OWDFetcher.requires_private_acl(_ENTITY_DEF_TO_OWD_VALUE["Private"])
        assert is_private_visibility(_ENTITY_DEF_TO_VISIBILITY["Private"])

    def test_controlled_by_parent_values_agree(self):
        """ControlledByParent variants should agree between both mappings."""
        from acl_engine.identity_models import is_controlled_by_parent as iq_cbp
        cbp_keys = {"ControlledByParent", "ControlledByCampaign", "ControlledByLeadOrContact"}
        for key in cbp_keys:
            assert OWDFetcher.is_controlled_by_parent(_ENTITY_DEF_TO_OWD_VALUE[key]), f"{key} OWDFetcher"
            assert iq_cbp(_ENTITY_DEF_TO_VISIBILITY[key]), f"{key} IdentityQueryClient"


# ═══════════════════════════════════════════════════════════════════════════════
#  Wiring: param pass-through from caller → underlying client
# ═══════════════════════════════════════════════════════════════════════════════


class TestAclResolverWiring:
    """AclResolver must forward EntityDefinition params to OWDFetcher."""

    def test_resolver_passes_entity_def_params_to_owd_fetcher(self):
        from acl_engine.resolver import AclResolver
        sf = MagicMock()
        resolver = AclResolver(
            sf_client=sf,
            use_entity_definition_owd=True,
            object_names=["Account", "Case"],
        )
        fetcher = resolver._owd_fetcher
        assert fetcher._use_entity_definition_owd is True
        assert fetcher._object_names == ["Account", "Case"]

    def test_resolver_defaults_entity_def_off(self):
        from acl_engine.resolver import AclResolver
        sf = MagicMock()
        resolver = AclResolver(sf_client=sf)
        assert resolver._owd_fetcher._use_entity_definition_owd is False
        assert resolver._owd_fetcher._object_names == []


class TestGroupAclBuilderWiring:
    """GroupAclBuilder must forward EntityDefinition params to IdentityQueryClient."""

    def test_builder_passes_entity_def_params_to_query_client(self):
        from acl_engine.group_acl_builder import GroupAclBuilder
        sf = MagicMock()
        builder = GroupAclBuilder(
            sf_client=sf,
            use_entity_definition_owd=True,
            object_names=["Account", "Contact"],
        )
        qc = builder._query_client
        assert qc._use_entity_definition_owd is True
        assert qc._object_names == ["Account", "Contact"]

    def test_builder_defaults_entity_def_off(self):
        from acl_engine.group_acl_builder import GroupAclBuilder
        sf = MagicMock()
        builder = GroupAclBuilder(sf_client=sf)
        assert builder._query_client._use_entity_definition_owd is False
        assert builder._query_client._object_names == []


class TestIdentitySyncHandlerWiring:
    """IdentitySyncHandler must forward EntityDefinition params to IdentityQueryClient."""

    def test_sync_handler_passes_entity_def_params(self):
        from acl_engine.identity_sync import IdentitySyncHandler
        sf = MagicMock()
        handler = IdentitySyncHandler(
            sf_client=sf,
            object_names=["Account", "Case"],
            use_entity_definition_owd=True,
        )
        qc = handler._query_client
        assert qc._use_entity_definition_owd is True
        assert qc._object_names == ["Account", "Case"]

    def test_sync_handler_defaults_entity_def_off(self):
        from acl_engine.identity_sync import IdentitySyncHandler
        sf = MagicMock()
        handler = IdentitySyncHandler(sf_client=sf, object_names=["Account"])
        assert handler._query_client._use_entity_definition_owd is False
        assert handler._query_client._object_names == []
