from __future__ import annotations

from connector.identity_sync import EntityShareBase, Group, GroupMember, User, UserLogin, UserOrGroup, UserRole

from ..common import OWNER_GUID, OWNER_NAME, OWNER_USERNAME, PUBLIC_GROUP_ID, ROLE_ID, SHARED_GUID, SHARED_NAME, SHARED_USERNAME


def build_user(
    user_id: str,
    *,
    name: str = OWNER_NAME,
    email: str = OWNER_USERNAME,
    username: str = OWNER_USERNAME,
    federation_identifier: str | None = None,
    is_frozen: bool = False,
    role_id: str | None = ROLE_ID,
    manager_id: str | None = None,
) -> User:
    return User(
        Id=user_id,
        Name=name,
        Email=email,
        UserName=username,
        FederationIdentifier=federation_identifier,
        IsActive=True,
        IsFrozen=is_frozen,
        UserRoleId=role_id,
        ManagerId=manager_id,
    )


def build_share(*, share_id: str, user_or_group_id: str, principal_type: str = "User", row_cause: str = "Manual") -> EntityShareBase:
    return EntityShareBase(
        Id=share_id,
        UserOrGroupId=user_or_group_id,
        RowCause=row_cause,
        UserOrGroup=UserOrGroup(Type=principal_type),
    )


def build_group(*, group_id: str = PUBLIC_GROUP_ID, group_type: str = "Group", related_id: str | None = None) -> Group:
    return Group(
        Id=group_id,
        Name="Mock Public Group",
        Type=group_type,
        RelatedId=related_id,
        DoesIncludeBosses=False,
    )


def build_group_member(*, group_id: str = PUBLIC_GROUP_ID, user_or_group_id: str) -> GroupMember:
    return GroupMember(Id=f"gm-{group_id}-{user_or_group_id}", GroupId=group_id, UserOrGroupId=user_or_group_id)


def build_user_role(*, role_id: str = ROLE_ID, parent_role_id: str | None = None) -> UserRole:
    return UserRole(
        Id=role_id,
        ParentRoleId=parent_role_id,
        ContactAccessForAccountOwner="Edit",
        OpportunityAccessForAccountOwner="Edit",
    )


def build_frozen_user(*, user_id: str) -> UserLogin:
    return UserLogin(Id=f"login-{user_id}", UserId=user_id, IsFrozen=True)


__all__ = [
    "OWNER_GUID",
    "OWNER_USERNAME",
    "SHARED_GUID",
    "SHARED_USERNAME",
    "build_frozen_user",
    "build_group",
    "build_group_member",
    "build_share",
    "build_user",
    "build_user_role",
]