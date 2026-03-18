"""
Constants — mirrors SalesforceConstants.cs
"""

RECORD_ID_LENGTH = 15
CONTENT_FIELD_NAME = "Description"
AUTHORS_SOURCE_PROPERTY = "Authors"
CREATED_BY_SOURCE_PROPERTY = "CreatedBy"
LAST_MODIFIED_BY_SOURCE_PROPERTY = "LastModifiedBy"
SYSTEM_CREATED_BY_USER_ID = "__System.User.CreatedBy.Id"
SYSTEM_MODIFIED_BY_USER_ID = "__System.User.ModifiedBy.Id"

METADATA_COLUMNS = [
    "Id",
    "LastModifiedDate",
    "IsDeleted",
    "Owner.UserRole.ParentRoleId",
    "OwnerId",
    "Owner.Name",
    "LastModifiedById",
    "LastModifiedBy.Name",
    "CreatedById",
    "CreatedBy.Name",
    "CreatedDate",
]

METADATA_COLUMN_SCHEMA_MAPPING: dict[str, str] = {
    "CreatedDate": "CreatedDate",
    "LastModifiedDate": "LastModifiedDate",
    "LastModifiedBy.Name": LAST_MODIFIED_BY_SOURCE_PROPERTY,
    "LastModifiedById": "LastModifiedByUrl",
    "CreatedById": "CreatedByUrl",
    "CreatedBy.Name": CREATED_BY_SOURCE_PROPERTY,
    "Owner.Name": "Owner",
    "OwnerId": "OwnerUrl",
    "Id": "Id",
}

METADATA_OBJECT_COLUMN_SCHEMA_MAPPING: dict[str, list[str]] = {
    "LastModifiedBy": ["Name"],
    "CreatedBy": ["Name"],
    "Owner": ["Name"],
}

# .NET assembly-qualified type name prefix ? Python type tag
TYPE_CONVERTERS: dict[str, str] = {
    "System.Boolean": "bool",
    "System.Double": "float",
    "System.DateTime": "datetime",
    "System.Int32": "int",
    "System.Int64": "int",
    "System.String": "str",
}
