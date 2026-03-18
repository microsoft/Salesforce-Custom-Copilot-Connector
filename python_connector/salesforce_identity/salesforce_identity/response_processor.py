"""
Response Processor for Salesforce Identity SOQL Queries

Handles parsing and transformation of SOQL query results into Python dataclass instances.
"""

from __future__ import annotations

import logging
from typing import Any, TypeVar, Type

from .models import (
    PermissionSetAssignment,
    User,
    Group,
    GroupMember,
    EntityShareBase,
    ObjectRecord,
    UserRole,
    UserLogin,
    ObjectPermissions,
    FieldPermissions,
    Organization,
    IdentityResponseBase,
    UserOrGroup,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=IdentityResponseBase)


class SalesforceIdentitySOQLResponseProcessor:
    """Processes Salesforce SOQL query results into typed Python objects."""

    def get(self, response: dict, model_class: Type[T]) -> list[T]:
        """
        Parse SOQL response records into typed objects.

        Args:
            response: Salesforce SOQL response dict with 'records' key
            model_class: Target dataclass type

        Returns:
            List of typed dataclass instances
        """
        records = response.get("records", [])
        if not records:
            return []

        results = []
        for record in records:
            try:
                obj = self._parse_record(record, model_class)
                if obj:
                    results.append(obj)
            except Exception as e:
                logger.warning(
                    f"Failed to parse record for {model_class.__name__}: {e}",
                    exc_info=True,
                )
                continue

        return results

    def _parse_record(self, record: dict, model_class: Type[T]) -> T | None:
        """Parse a single record into a dataclass instance."""
        if not record:
            return None

        # Remove 'attributes' metadata if present
        clean_record = {k: v for k, v in record.items() if k != "attributes"}

        # Handle nested objects
        if model_class == User:
            return self._parse_user(clean_record)
        elif model_class == EntityShareBase:
            return self._parse_entity_share(clean_record)
        elif model_class == ObjectRecord:
            return self._parse_object_record(clean_record)
        else:
            # Generic parsing for simple dataclasses
            return model_class(**self._filter_fields(clean_record, model_class))

    def _parse_user(self, record: dict) -> User:
        """Parse User with nested UserRole and PermissionSetAssignments."""
        user_data = self._filter_fields(record, User)

        # Parse nested UserRole
        if "UserRole" in record and isinstance(record["UserRole"], dict):
            user_role_data = self._filter_fields(record["UserRole"], UserRole)
            user_data["UserRole"] = UserRole(**user_role_data)

        # Keep PermissionSetAssignments as dict for later processing
        if "PermissionSetAssignments" in record:
            user_data["PermissionSetAssignments"] = record["PermissionSetAssignments"]

        return User(**user_data)

    def _parse_entity_share(self, record: dict) -> EntityShareBase:
        """Parse EntityShareBase with nested UserOrGroup."""
        share_data = self._filter_fields(record, EntityShareBase)

        # Parse nested UserOrGroup
        if "UserOrGroup" in record and isinstance(record["UserOrGroup"], dict):
            user_or_group = record["UserOrGroup"]
            share_data["UserOrGroup"] = UserOrGroup(
                Type=user_or_group.get("Type", ""),
                attributes=user_or_group.get("attributes"),
            )

        return EntityShareBase(**share_data)

    def _parse_object_record(self, record: dict) -> ObjectRecord:
        """Parse ObjectRecord with nested Shares."""
        obj_data = self._filter_fields(record, ObjectRecord)

        # Keep Shares as dict for later processing
        if "Shares" in record:
            obj_data["Shares"] = record["Shares"]

        return ObjectRecord(**obj_data)

    def _filter_fields(self, record: dict, model_class: Type) -> dict:
        """Filter record fields to match dataclass fields."""
        if not hasattr(model_class, "__dataclass_fields__"):
            return record

        valid_fields = set(model_class.__dataclass_fields__.keys())
        return {k: v for k, v in record.items() if k in valid_fields}
