"""
Tests for SalesforceIdentitySOQLResponseProcessor
"""

import pytest
from salesforce_identity.response_processor import SalesforceIdentitySOQLResponseProcessor
from salesforce_identity.models import (
    User,
    PermissionSetAssignment,
    Group,
    EntityShareBase,
    ObjectRecord,
    UserRole,
)


class TestResponseProcessor:
    """Test response processor."""

    def test_parse_permission_set_assignment(self):
        """Test parsing permission set assignment."""
        # Arrange
        processor = SalesforceIdentitySOQLResponseProcessor()
        response = {
            "records": [
                {
                    "Id": "0Pa5w000006V0b1CAC",
                    "attributes": {"type": "PermissionSetAssignment"},
                    "AssigneeId": "0055w00000CmT7xAAF",
                    "PermissionSetId": "0PS5w000002d8e3GAA",
                    "IsActive": True
                }
            ]
        }
        
        # Act
        result = processor.get(response, PermissionSetAssignment)
        
        # Assert
        assert len(result) == 1
        assert result[0].Id == "0Pa5w000006V0b1CAC"
        assert result[0].AssigneeId == "0055w00000CmT7xAAF"

    def test_parse_user_with_role(self):
        """Test parsing user with nested role."""
        # Arrange
        processor = SalesforceIdentitySOQLResponseProcessor()
        response = {
            "records": [
                {
                    "Id": "0055w00000CkeZ1AAJ",
                    "attributes": {"type": "User"},
                    "Name": "Charlotte Walton",
                    "Email": "charlotte@test.com",
                    "UserRoleId": "00E5w000004XkNoEAK",
                    "UserRole": {
                        "attributes": {"type": "UserRole"},
                        "Id": "00E5w000004XkNoEAK",
                        "ParentRoleId": "00E5w000004XkNaEAK"
                    }
                }
            ]
        }
        
        # Act
        result = processor.get(response, User)
        
        # Assert
        assert len(result) == 1
        assert result[0].Id == "0055w00000CkeZ1AAJ"
        assert result[0].Name == "Charlotte Walton"
        assert result[0].UserRole is not None
        assert result[0].UserRole.ParentRoleId == "00E5w000004XkNaEAK"

    def test_parse_user_with_permission_sets(self):
        """Test parsing user with nested permission set assignments."""
        # Arrange
        processor = SalesforceIdentitySOQLResponseProcessor()
        response = {
            "records": [
                {
                    "Id": "0055w00000CkeZ1AAJ",
                    "attributes": {"type": "User"},
                    "Name": "Charlotte Walton",
                    "PermissionSetAssignments": {
                        "totalSize": 2,
                        "done": True,
                        "records": [
                            {
                                "Id": "0Pa5w000007Ioj5CAC",
                                "attributes": {"type": "PermissionSetAssignment"},
                                "PermissionSetId": "0PS5w000002d8e3GAA"
                            },
                            {
                                "Id": "0Pa5w000006VciLCAS",
                                "attributes": {"type": "PermissionSetAssignment"},
                                "PermissionSetId": "0PS5w000002j6DNGAY"
                            }
                        ]
                    }
                }
            ]
        }
        
        # Act
        result = processor.get(response, User)
        
        # Assert
        assert len(result) == 1
        assert result[0].PermissionSetAssignments is not None
        # Note: PermissionSets are processed separately by the helper

    def test_parse_entity_share_with_user_or_group(self):
        """Test parsing entity share with nested UserOrGroup."""
        # Arrange
        processor = SalesforceIdentitySOQLResponseProcessor()
        response = {
            "records": [
                {
                    "Id": "00r5w0000MoeqAjAQI",
                    "attributes": {"type": "AccountShare"},
                    "UserOrGroupId": "0055w00000DRjk2AAD",
                    "UserOrGroup": {
                        "attributes": {"type": "Name"},
                        "Type": "User"
                    }
                }
            ]
        }
        
        # Act
        result = processor.get(response, EntityShareBase)
        
        # Assert
        assert len(result) == 1
        assert result[0].UserOrGroupId == "0055w00000DRjk2AAD"
        assert result[0].UserOrGroup is not None
        assert result[0].UserOrGroup.Type == "User"

    def test_parse_object_record_with_shares(self):
        """Test parsing object record with nested shares."""
        # Arrange
        processor = SalesforceIdentitySOQLResponseProcessor()
        response = {
            "records": [
                {
                    "Id": "0015w00002BAArkAAH",
                    "attributes": {"type": "Account"},
                    "IsDeleted": False,
                    "Shares": {
                        "totalSize": 2,
                        "done": True,
                        "records": [
                            {
                                "Id": "00r5w0000MoeqAjAQI",
                                "attributes": {"type": "AccountShare"},
                                "UserOrGroupId": "0055w00000DRjk2AAD"
                            }
                        ]
                    }
                }
            ]
        }
        
        # Act
        result = processor.get(response, ObjectRecord)
        
        # Assert
        assert len(result) == 1
        assert result[0].Id == "0015w00002BAArkAAH"
        assert result[0].IsDeleted is False
        assert result[0].Shares is not None

    def test_parse_group(self):
        """Test parsing group."""
        # Arrange
        processor = SalesforceIdentitySOQLResponseProcessor()
        response = {
            "records": [
                {
                    "Id": "00G5w000005Rp25EAC",
                    "attributes": {"type": "Group"},
                    "Type": "Queue",
                    "RelatedId": None,
                    "DoesIncludeBosses": False,
                    "Name": "Support Queue"
                }
            ]
        }
        
        # Act
        result = processor.get(response, Group)
        
        # Assert
        assert len(result) == 1
        assert result[0].Id == "00G5w000005Rp25EAC"
        assert result[0].Type == "Queue"

    def test_parse_empty_response(self):
        """Test parsing empty response."""
        # Arrange
        processor = SalesforceIdentitySOQLResponseProcessor()
        response = {"records": []}
        
        # Act
        result = processor.get(response, User)
        
        # Assert
        assert len(result) == 0

    def test_filter_invalid_fields(self):
        """Test that invalid fields are filtered out."""
        # Arrange
        processor = SalesforceIdentitySOQLResponseProcessor()
        response = {
            "records": [
                {
                    "Id": "0055w00000CkeZ1AAJ",
                    "attributes": {"type": "User"},
                    "Name": "Valid Name",
                    "InvalidField": "This should be ignored",
                    "AnotherInvalidField": 12345
                }
            ]
        }
        
        # Act
        result = processor.get(response, User)
        
        # Assert
        assert len(result) == 1
        assert result[0].Name == "Valid Name"
        assert not hasattr(result[0], "InvalidField")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
