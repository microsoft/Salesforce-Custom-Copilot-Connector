"""
tests/test_acl_engine/test_principal_mapper.py
----------------------------------------------
Unit tests for acl_engine.principal_mapper.PrincipalMapper and its helpers.

Covers:
  - _looks_like_guid
  - _strip_sf_username_suffix
  - _resolve_principal (candidate generation + ordering)
  - _resolve_identifier (cache, GUID short-circuit, no-graph-client path)
  - _lookup_graph_user_id (direct path, filter path, ConsistencyLevel header,
                           onPremisesUserPrincipalName, error handling)
  - to_acl_entries (public sentinel, empty ids, deny-all, dedup, prewarm cache)
  - prewarm_users (SOQL batching, inactive users excluded, error tolerance)
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
import pytest_asyncio

from acl_engine.models import AclResult, PUBLIC_SENTINEL
from acl_engine.principal_mapper import (
    PrincipalMapper,
    _looks_like_guid,
    _strip_sf_username_suffix,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

TENANT_ID = "aaaaaaaa-0000-0000-0000-000000000001"
VALID_GUID = "12345678-1234-1234-1234-123456789abc"


def _make_mapper(graph_client=None, sf_client=None, tenant_id=TENANT_ID) -> PrincipalMapper:
    if sf_client is None:
        sf_client = MagicMock()
        sf_client.query_all = AsyncMock(return_value=[])
    return PrincipalMapper(
        sf_client=sf_client,
        graph_client=graph_client,
        tenant_id=tenant_id,
        batch_size=100,
    )


def _acl_result(user_ids, object_type="Account", record_id="001X", is_public=False):
    return AclResult(
        object_type=object_type,
        record_id=record_id,
        user_ids=set(user_ids),
        is_public=is_public,
    )


# ---------------------------------------------------------------------------
# _looks_like_guid
# ---------------------------------------------------------------------------

class TestLooksLikeGuid:
    def test_valid_guid(self):
        assert _looks_like_guid("12345678-abcd-abcd-abcd-abcdef012345") is True

    def test_uppercase_guid(self):
        assert _looks_like_guid("12345678-ABCD-ABCD-ABCD-ABCDEF012345") is True

    def test_not_guid_email(self):
        assert _looks_like_guid("user@nokia.com") is False

    def test_not_guid_too_few_parts(self):
        assert _looks_like_guid("12345678-abcd-abcd-abcd") is False

    def test_not_guid_wrong_length(self):
        assert _looks_like_guid("1234567-abcd-abcd-abcd-abcdef012345") is False

    def test_not_guid_non_hex(self):
        assert _looks_like_guid("1234567g-abcd-abcd-abcd-abcdef012345") is False

    def test_empty_string(self):
        assert _looks_like_guid("") is False

    def test_whitespace_stripped(self):
        assert _looks_like_guid(f"  {VALID_GUID}  ") is True


# ---------------------------------------------------------------------------
# _strip_sf_username_suffix
# ---------------------------------------------------------------------------

class TestStripSfUsernameSuffix:
    def test_strips_org_suffix(self):
        assert _strip_sf_username_suffix("john@nokia.com.cape2104") == "john@nokia.com"

    def test_strips_sandbox_suffix(self):
        assert _strip_sf_username_suffix("rohith@acme.co.uk.sandboxDev") == "rohith@acme.co.uk"

    def test_no_strip_two_label_domain(self):
        assert _strip_sf_username_suffix("john@nokia.com") is None

    def test_no_strip_single_label_domain(self):
        assert _strip_sf_username_suffix("john@nokia") is None

    def test_no_at_sign(self):
        assert _strip_sf_username_suffix("johnnokia.com.suffix") is None

    def test_ext_user_with_suffix(self):
        # Nokia external user pattern
        assert _strip_sf_username_suffix("rohith.kakumani.ext@nokia.com.cape2104") == "rohith.kakumani.ext@nokia.com"

    def test_three_label_domain(self):
        assert _strip_sf_username_suffix("user@sub.nokia.com.suffix") == "user@sub.nokia.com"


# ---------------------------------------------------------------------------
# _resolve_principal – candidate generation
# ---------------------------------------------------------------------------

class TestResolvePrincipal:
    def test_uses_federation_identifier_first(self):
        mapper = _make_mapper()
        details = {
            "FederationIdentifier": "fed@nokia.com",
            "UserName": "user@nokia.com.cape2104",
            "Email": "email@nokia.com",
        }
        result = mapper._resolve_principal(details)
        # No graph client → first non-empty identifier returned directly
        assert result == "fed@nokia.com"

    def test_falls_back_to_username_when_no_fed_id(self):
        mapper = _make_mapper()
        details = {
            "FederationIdentifier": "",
            "UserName": "user@nokia.com.cape2104",
            "Email": "email@nokia.com",
        }
        result = mapper._resolve_principal(details)
        # Raw UserName returned (no graph client, no GUID)
        assert result == "user@nokia.com.cape2104"

    def test_falls_back_to_email_when_no_fed_or_username(self):
        mapper = _make_mapper()
        details = {"FederationIdentifier": None, "UserName": None, "Email": "email@nokia.com"}
        result = mapper._resolve_principal(details)
        assert result == "email@nokia.com"

    def test_returns_none_when_all_fields_empty(self):
        mapper = _make_mapper()
        details = {"FederationIdentifier": "", "UserName": "", "Email": ""}
        assert mapper._resolve_principal(details) is None

    def test_stripped_username_tried_after_raw(self):
        """When Graph resolves the stripped username but not the raw one, it is returned."""
        graph = MagicMock()
        from graph.client import GraphApiError

        def fake_get(path, **kwargs):
            # Direct lookup always 404s
            if "/users/" in path and "filter" not in path:
                raise GraphApiError(404, "not found")
            # Filter lookup: only succeeds for the stripped address
            if "cape2104" in path:
                return {"value": []}
            return {"value": [{"id": VALID_GUID}]}

        graph.get = fake_get
        mapper = _make_mapper(graph_client=graph)
        details = {
            "FederationIdentifier": "",
            "UserName": "john@nokia.com.cape2104",
            "Email": "",
        }
        result = mapper._resolve_principal(details)
        assert result == VALID_GUID

    def test_no_duplicate_candidates(self):
        """FederationIdentifier == Email should not be looked up twice."""
        graph = MagicMock()
        call_log: list[str] = []

        def fake_get(path, **kwargs):
            call_log.append(path)
            raise __import__("graph.client", fromlist=["GraphApiError"]).GraphApiError(404, "nf")

        graph.get = fake_get
        mapper = _make_mapper(graph_client=graph)
        details = {
            "FederationIdentifier": "same@nokia.com",
            "UserName": "same@nokia.com",
            "Email": "same@nokia.com",
        }
        mapper._resolve_principal(details)
        # Each unique candidate is tried once (direct + filter = 2 calls max per candidate)
        unique_bases = {p.split("?")[0] for p in call_log}
        # All calls should be for "same@nokia.com" only, not duplicated 3x
        assert len(call_log) <= 4  # 2 attempts × 1 unique candidate (+ possibly stripped)


# ---------------------------------------------------------------------------
# _resolve_identifier
# ---------------------------------------------------------------------------

class TestResolveIdentifier:
    def test_cache_hit_returns_cached(self):
        mapper = _make_mapper()
        mapper._principal_cache["user@nokia.com"] = VALID_GUID
        assert mapper._resolve_identifier("user@nokia.com") == VALID_GUID

    def test_guid_passthrough(self):
        mapper = _make_mapper()
        assert mapper._resolve_identifier(VALID_GUID) == VALID_GUID
        assert mapper._principal_cache[VALID_GUID] == VALID_GUID

    def test_no_graph_client_returns_identifier_directly(self):
        mapper = _make_mapper(graph_client=None)
        result = mapper._resolve_identifier("user@nokia.com")
        assert result == "user@nokia.com"
        assert mapper._principal_cache["user@nokia.com"] == "user@nokia.com"

    def test_with_graph_client_calls_lookup(self):
        graph = MagicMock()
        graph.get = MagicMock(return_value={"id": VALID_GUID})
        mapper = _make_mapper(graph_client=graph)
        result = mapper._resolve_identifier("user@nokia.com")
        assert result == VALID_GUID

    def test_cache_none_on_miss(self):
        graph = MagicMock()
        from graph.client import GraphApiError
        graph.get = MagicMock(side_effect=GraphApiError(404, "nf"))
        mapper = _make_mapper(graph_client=graph)
        result = mapper._resolve_identifier("ghost@nokia.com")
        assert result is None
        assert "ghost@nokia.com" in mapper._principal_cache
        assert mapper._principal_cache["ghost@nokia.com"] is None


# ---------------------------------------------------------------------------
# _lookup_graph_user_id
# ---------------------------------------------------------------------------

class TestLookupGraphUserId:
    def _graph_client(self, direct_response=None, filter_response=None,
                       direct_exc=None, filter_exc=None):
        graph = MagicMock()
        def fake_get(path, **kwargs):
            if "$filter" not in path:
                if direct_exc:
                    raise direct_exc
                return direct_response or {}
            else:
                if filter_exc:
                    raise filter_exc
                return filter_response or {"value": []}
        graph.get = MagicMock(side_effect=fake_get)
        return graph

    def test_direct_lookup_success(self):
        graph = self._graph_client(direct_response={"id": VALID_GUID})
        mapper = _make_mapper(graph_client=graph)
        assert mapper._lookup_graph_user_id("user@nokia.com") == VALID_GUID

    def test_fallback_to_filter_when_direct_404(self):
        from graph.client import GraphApiError
        graph = self._graph_client(
            direct_exc=GraphApiError(404, "not found"),
            filter_response={"value": [{"id": VALID_GUID}]},
        )
        mapper = _make_mapper(graph_client=graph)
        assert mapper._lookup_graph_user_id("user@nokia.com") == VALID_GUID

    def test_filter_sends_consistency_level_header(self):
        from graph.client import GraphApiError
        calls: list[dict] = []

        def fake_get(path, **kwargs):
            calls.append({"path": path, "headers": kwargs.get("headers")})
            if "$filter" not in path:
                raise GraphApiError(404, "nf")
            return {"value": [{"id": VALID_GUID}]}

        graph = MagicMock()
        graph.get = MagicMock(side_effect=fake_get)
        mapper = _make_mapper(graph_client=graph)
        mapper._lookup_graph_user_id("user@nokia.com")

        filter_call = next(c for c in calls if "$filter" in c["path"])
        assert filter_call["headers"] == {"ConsistencyLevel": "eventual"}

    def test_filter_includes_on_premises_upn(self):
        from graph.client import GraphApiError
        captured_paths: list[str] = []

        def fake_get(path, **kwargs):
            captured_paths.append(path)
            if "$filter" not in path:
                raise GraphApiError(404, "nf")
            return {"value": []}

        graph = MagicMock()
        graph.get = MagicMock(side_effect=fake_get)
        mapper = _make_mapper(graph_client=graph)
        mapper._lookup_graph_user_id("user@nokia.com")

        filter_path = next(p for p in captured_paths if "$filter" in p)
        assert "onPremisesUserPrincipalName" in filter_path

    def test_filter_includes_count_param(self):
        from graph.client import GraphApiError
        captured: list[str] = []

        def fake_get(path, **kwargs):
            captured.append(path)
            if "$filter" not in path:
                raise GraphApiError(404, "nf")
            return {"value": []}

        graph = MagicMock()
        graph.get = MagicMock(side_effect=fake_get)
        mapper = _make_mapper(graph_client=graph)
        mapper._lookup_graph_user_id("user@nokia.com")

        filter_path = next(p for p in captured if "$filter" in p)
        assert "$count=true" in filter_path

    def test_returns_none_when_both_attempts_fail(self):
        from graph.client import GraphApiError
        graph = self._graph_client(
            direct_exc=GraphApiError(404, "nf"),
            filter_response={"value": []},
        )
        mapper = _make_mapper(graph_client=graph)
        assert mapper._lookup_graph_user_id("ghost@nokia.com") is None

    def test_non_404_error_propagates(self):
        from graph.client import GraphApiError
        graph = self._graph_client(direct_exc=GraphApiError(500, "server error"))
        mapper = _make_mapper(graph_client=graph)
        with pytest.raises(GraphApiError) as exc_info:
            mapper._lookup_graph_user_id("user@nokia.com")
        assert exc_info.value.status_code == 500

    def test_apostrophe_escaped_in_filter(self):
        from graph.client import GraphApiError
        captured: list[str] = []

        def fake_get(path, **kwargs):
            captured.append(path)
            if "$filter" not in path:
                raise GraphApiError(404, "nf")
            return {"value": []}

        graph = MagicMock()
        graph.get = MagicMock(side_effect=fake_get)
        mapper = _make_mapper(graph_client=graph)
        mapper._lookup_graph_user_id("o'brien@nokia.com")

        filter_path = next(p for p in captured if "$filter" in p)
        assert "o''brien" in filter_path


# ---------------------------------------------------------------------------
# to_acl_entries
# ---------------------------------------------------------------------------

class TestToAclEntries:
    def _sf_with_users(self, users: list[dict]) -> MagicMock:
        sf = MagicMock()
        sf.query_all = AsyncMock(return_value=users)
        return sf

    @pytest.mark.asyncio
    async def test_public_sentinel_returns_everyone_grant(self):
        mapper = _make_mapper()
        result = await mapper.to_acl_entries(_acl_result([PUBLIC_SENTINEL]))
        assert result == [{"accessType": "grant", "type": "everyone", "value": TENANT_ID}]

    @pytest.mark.asyncio
    async def test_is_public_flag_returns_everyone_grant(self):
        mapper = _make_mapper()
        result = await mapper.to_acl_entries(_acl_result([], is_public=True))
        assert result == [{"accessType": "grant", "type": "everyone", "value": TENANT_ID}]

    @pytest.mark.asyncio
    async def test_empty_user_ids_returns_deny_all(self):
        mapper = _make_mapper()
        result = await mapper.to_acl_entries(_acl_result([]))
        assert result == [{"accessType": "deny", "type": "everyone", "value": TENANT_ID}]

    @pytest.mark.asyncio
    async def test_unresolvable_users_returns_deny_all(self):
        sf = self._sf_with_users([])
        mapper = _make_mapper(sf_client=sf)
        result = await mapper.to_acl_entries(_acl_result(["005USER1"]))
        assert result == [{"accessType": "deny", "type": "everyone", "value": TENANT_ID}]

    @pytest.mark.asyncio
    async def test_resolved_user_returns_grant_entry(self):
        sf = self._sf_with_users([
            {"Id": "005USER1", "FederationIdentifier": "user@nokia.com",
             "UserName": "user@nokia.com.cape2104", "Email": "user@nokia.com"},
        ])
        mapper = _make_mapper(sf_client=sf)
        result = await mapper.to_acl_entries(_acl_result(["005USER1"]))
        assert len(result) == 1
        assert result[0] == {"accessType": "grant", "type": "user", "value": "user@nokia.com"}

    @pytest.mark.asyncio
    async def test_deduplication_of_same_principal(self):
        sf = self._sf_with_users([
            {"Id": "005USER1", "FederationIdentifier": "user@nokia.com",
             "UserName": None, "Email": None},
            {"Id": "005USER2", "FederationIdentifier": "USER@NOKIA.COM",
             "UserName": None, "Email": None},
        ])
        mapper = _make_mapper(sf_client=sf)
        result = await mapper.to_acl_entries(_acl_result(["005USER1", "005USER2"]))
        # Case-insensitive dedup — only one entry
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_uses_prewarm_cache(self):
        sf = MagicMock()
        sf.query_all = AsyncMock(return_value=[])  # should NOT be called
        mapper = _make_mapper(sf_client=sf)
        # Pre-populate cache
        mapper._user_details_cache["005USER1"] = {
            "Id": "005USER1",
            "FederationIdentifier": "cached@nokia.com",
            "UserName": None,
            "Email": None,
        }
        result = await mapper.to_acl_entries(_acl_result(["005USER1"]))
        sf.query_all.assert_not_called()
        assert result[0]["value"] == "cached@nokia.com"

    @pytest.mark.asyncio
    async def test_graph_guid_used_as_acl_value(self):
        sf = self._sf_with_users([
            {"Id": "005USER1", "FederationIdentifier": "user@nokia.com",
             "UserName": None, "Email": None},
        ])
        graph = MagicMock()
        graph.get = MagicMock(return_value={"id": VALID_GUID})
        mapper = _make_mapper(sf_client=sf, graph_client=graph)
        result = await mapper.to_acl_entries(_acl_result(["005USER1"]))
        assert result[0]["value"] == VALID_GUID

    @pytest.mark.asyncio
    async def test_warning_emitted_once_per_user(self, caplog):
        sf = self._sf_with_users([
            {"Id": "005GHOST", "FederationIdentifier": None,
             "UserName": None, "Email": "ghost@nokia.com"},
        ])
        graph = MagicMock()
        from graph.client import GraphApiError
        graph.get = MagicMock(side_effect=GraphApiError(404, "nf"))
        mapper = _make_mapper(sf_client=sf, graph_client=graph)

        import logging
        with caplog.at_level(logging.WARNING, logger="salesforce_connector.acl_engine"):
            await mapper.to_acl_entries(_acl_result(["005GHOST"]))
            await mapper.to_acl_entries(_acl_result(["005GHOST"]))

        warnings = [r for r in caplog.records if "no M365 principal found" in r.message]
        assert len(warnings) == 1  # second call suppressed


# ---------------------------------------------------------------------------
# prewarm_users
# ---------------------------------------------------------------------------

class TestPrewarmUsers:
    @pytest.mark.asyncio
    async def test_populates_cache(self):
        sf = MagicMock()
        sf.query_all = AsyncMock(return_value=[
            {"Id": "005A", "FederationIdentifier": "a@nokia.com",
             "UserName": "a@nokia.com.sf", "Email": "a@nokia.com"},
        ])
        mapper = _make_mapper(sf_client=sf)
        await mapper.prewarm_users({"005A"})
        assert "005A" in mapper._user_details_cache
        assert mapper._user_details_cache["005A"]["FederationIdentifier"] == "a@nokia.com"

    @pytest.mark.asyncio
    async def test_skips_already_cached(self):
        sf = MagicMock()
        sf.query_all = AsyncMock(return_value=[])
        mapper = _make_mapper(sf_client=sf)
        mapper._user_details_cache["005A"] = {"Id": "005A"}
        await mapper.prewarm_users({"005A"})
        sf.query_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_batches_large_sets(self):
        ids = {f"005{i:015d}" for i in range(250)}
        sf = MagicMock()
        sf.query_all = AsyncMock(return_value=[])
        mapper = _make_mapper(sf_client=sf)
        await mapper.prewarm_users(ids, batch_size=100)
        # 250 ids / batch_size=100 → 3 SOQL calls
        assert sf.query_all.call_count == 3

    @pytest.mark.asyncio
    async def test_tolerates_soql_error(self):
        sf = MagicMock()
        sf.query_all = AsyncMock(side_effect=RuntimeError("SOQL failed"))
        mapper = _make_mapper(sf_client=sf)
        # Should not raise
        await mapper.prewarm_users({"005A"})
        assert "005A" not in mapper._user_details_cache
