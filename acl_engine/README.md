# ACL Engine

Orchestrates the complete **Access Control List (ACL) resolution pipeline** to determine which users should have access to each Salesforce record when surfaced in Microsoft Search.

## How It Works

The engine queries Salesforce sharing metadata and translates it into Microsoft Graph ACL entries:

1. **Org-Wide Defaults (OWD)** — Checks object-level visibility (`Public`, `Private`, `ControlledByParent`).
2. **Share Tables** — Queries `<Object>Share` tables for record-level sharing rules.
3. **Principal Expansion** — Expands sharing entries into individual user ACLs by resolving:
   - Direct user references
   - Roles and role-with-subordinates hierarchies
   - Territory-based sharing (Territory2)
   - Queues, public groups, and manager grants
4. **AAD Mapping** — Converts Salesforce User IDs to Azure AD GUIDs for Graph API compatibility.

---

## Flowchart — ACL Resolution Pipeline

```mermaid
flowchart TD
    START([resolve\nobject_type, record_id]) --> OWD[Fetch OWD for object_type\nSOQL: SELECT ... FROM Organization]

    OWD --> CHECK_OWD{OWD Visibility?}

    CHECK_OWD -->|Public / Read / Edit /\nReadEditTransfer / All| PUBLIC_RESULT[Return PUBLIC_SENTINEL\nis_public = true]

    CHECK_OWD -->|ControlledByParent /\nControlledByCampaign| CBP_DEPTH{depth >= MAX_PARENT_DEPTH?}
    CBP_DEPTH -->|Yes| FALLBACK_PRIVATE[Fallback: resolve as Private]
    CBP_DEPTH -->|No| GET_PARENT[Lookup parent from schema.json\nparentFieldName, parentObjectName]
    GET_PARENT --> PARENT_FOUND{Parent info found?}
    PARENT_FOUND -->|No| FALLBACK_PRIVATE
    PARENT_FOUND -->|Yes| FETCH_PARENT_ID[SOQL: SELECT parentField\nFROM objectType\nWHERE Id = recordId]
    FETCH_PARENT_ID --> HAS_PARENT{Parent record exists?}
    HAS_PARENT -->|No| FALLBACK_PRIVATE
    HAS_PARENT -->|Yes| RECURSE[Recurse: resolve\nparent_type, parent_id, depth+1]
    RECURSE --> OWD

    CHECK_OWD -->|Private / Unrecognised| PRIVATE_ACL[Resolve Private ACL]

    FALLBACK_PRIVATE --> PRIVATE_ACL

    PRIVATE_ACL --> FETCH_OWNER[Fetch OwnerId\nSOQL: SELECT OwnerId FROM objectType]
    FETCH_OWNER --> RESOLVE_OWNER[Resolve owner principal]
    RESOLVE_OWNER --> OWNER_PUBLIC{Owner = PUBLIC_SENTINEL?}
    OWNER_PUBLIC -->|Yes| PUBLIC_RESULT
    OWNER_PUBLIC -->|No| ADD_OWNER[Add owner users to ACL set]

    ADD_OWNER --> FETCH_SHARES[Fetch share entries\nSOQL: SELECT UserOrGroupId, RowCause\nFROM objectTypeShare]
    FETCH_SHARES --> LOOP_SHARES{For each share entry}

    LOOP_SHARES --> RESOLVE_PRINCIPAL[Resolve principal]
    RESOLVE_PRINCIPAL --> PRINCIPAL_PUBLIC{= PUBLIC_SENTINEL?}
    PRINCIPAL_PUBLIC -->|Yes| PUBLIC_RESULT
    PRINCIPAL_PUBLIC -->|No| ADD_USERS[Add resolved users to ACL set]
    ADD_USERS --> LOOP_SHARES

    LOOP_SHARES -->|Done| MAP_AAD[Map SF User IDs → AAD GUIDs\nvia PrincipalMapper]
    MAP_AAD --> RESULT([Return AclResult\nuser_ids, is_public, owd])

    style PUBLIC_RESULT fill:#2d6,stroke:#1a4,color:#fff
    style RESULT fill:#26d,stroke:#14a,color:#fff
    style FALLBACK_PRIVATE fill:#f92,stroke:#c60,color:#fff
```

---

## Flowchart — Principal Resolution (Type Dispatch)

When a principal ID is encountered (owner or share entry), it is routed to a specialised handler based on its type:

```mermaid
flowchart TD
    PRINCIPAL([Principal ID]) --> IS_USER{Starts with '005'?}

    IS_USER -->|Yes| USER_HANDLER[UserHandler.resolve\nSOQL: SELECT Id FROM User\nWHERE Id = ? AND IsActive = true]
    USER_HANDLER --> ACTIVE{Active user?}
    ACTIVE -->|Yes| RETURN_USER([Return user_id])
    ACTIVE -->|No| RETURN_EMPTY([Return empty set])

    IS_USER -->|No| GROUP_FETCH[Fetch Group record\nSOQL: SELECT Id, Type, RelatedId\nFROM Group WHERE Id = ?]
    GROUP_FETCH --> GROUP_TYPE{Group.Type?}

    GROUP_TYPE -->|Role| ROLE[RoleHandler\nUsers in this role only]
    GROUP_TYPE -->|RoleAndSubordinates| ROLE_SUB[RoleHandler\nDFS role hierarchy\ncollect all descendant users]
    GROUP_TYPE -->|Territory| TERR[TerritoryHandler\nUsers assigned to territory]
    GROUP_TYPE -->|TerritoryAndSubordinates| TERR_SUB[TerritoryHandler\nDFS territory hierarchy\ncollect all descendant users]
    GROUP_TYPE -->|Organization| ORG([Return PUBLIC_SENTINEL])
    GROUP_TYPE -->|Manager| MGR[QueueHandler\nDirect reports of user]
    GROUP_TYPE -->|ManagerAndSubordinates| MGR_SUB[QueueHandler\nAll transitive reports]
    GROUP_TYPE -->|Queue / PublicGroup / Other| STATIC[QueueHandler\nExpand GroupMember table\nrecursive for nested groups]

    ROLE --> RETURN_USERS([Return user IDs])
    ROLE_SUB --> RETURN_USERS
    TERR --> RETURN_USERS
    TERR_SUB --> RETURN_USERS
    MGR --> RETURN_USERS
    MGR_SUB --> RETURN_USERS
    STATIC --> RETURN_USERS

    style ORG fill:#2d6,stroke:#1a4,color:#fff
    style RETURN_USER fill:#26d,stroke:#14a,color:#fff
    style RETURN_USERS fill:#26d,stroke:#14a,color:#fff
    style RETURN_EMPTY fill:#999,stroke:#666,color:#fff
```

---

## Sequence Diagram — Full ACL Resolution for a Private Record

```mermaid
sequenceDiagram
    participant CMD as Command (deploy/ingest)
    participant RES as Resolver
    participant OWD as OWDFetcher
    participant SF as SalesforceClient
    participant SHR as ShareFetcher
    participant UH as UserHandler
    participant GH as GroupHandler
    participant RH as RoleHandler
    participant PM as PrincipalMapper

    CMD->>RES: resolve(object_type, record_id)
    RES->>OWD: get_owd(object_type)
    OWD->>SF: SOQL: SELECT DefaultAccountAccess, ... FROM Organization
    SF-->>OWD: {DefaultAccountAccess: "Private", ...}
    OWD-->>RES: "Private"

    Note over RES: OWD = Private → resolve record-level ACLs

    RES->>SHR: get_owner_id(object_type, record_id)
    SHR->>SF: SOQL: SELECT OwnerId FROM Account WHERE Id = '...'
    SF-->>SHR: OwnerId = "005xxx"
    SHR-->>RES: "005xxx"

    RES->>UH: resolve("005xxx")
    UH->>SF: SOQL: SELECT Id FROM User WHERE Id = '005xxx' AND IsActive = true
    SF-->>UH: [record found]
    UH-->>RES: {"005xxx"}

    RES->>SHR: get_share_entries(object_type, record_id)

    Note over SHR: Dynamic field discovery (first call per object)
    SHR->>SF: Describe AccountShare → find parent field & access level field
    SF-->>SHR: parentField=AccountId, accessLevel=AccountAccessLevel

    SHR->>SF: SOQL: SELECT UserOrGroupId, RowCause, AccountAccessLevel<br/>FROM AccountShare WHERE AccountId = '...'
    SF-->>SHR: [{UserOrGroupId: "00Gxxx", RowCause: "Rule"}, ...]
    SHR-->>RES: [ShareEntry(...), ...]

    loop For each ShareEntry
        RES->>GH: resolve("00Gxxx")
        GH->>SF: SOQL: SELECT Id, Type, RelatedId FROM Group WHERE Id = '00Gxxx'
        SF-->>GH: {Type: "RoleAndSubordinates", RelatedId: "00Exxx"}

        GH->>RH: resolve_role_and_subordinates("00Exxx")

        Note over RH: Iterative DFS through UserRole hierarchy
        RH->>SF: SOQL: SELECT Id FROM User WHERE UserRoleId = '00Exxx' AND IsActive = true
        SF-->>RH: ["005aaa", "005bbb"]
        RH->>SF: SOQL: SELECT Id FROM UserRole WHERE ParentRoleId = '00Exxx'
        SF-->>RH: ["00Eyyy"]
        RH->>SF: SOQL: SELECT Id FROM User WHERE UserRoleId = '00Eyyy' AND IsActive = true
        SF-->>RH: ["005ccc"]
        RH-->>GH: {"005aaa", "005bbb", "005ccc"}
        GH-->>RES: {"005aaa", "005bbb", "005ccc"}
    end

    Note over RES: Collected all user IDs: {"005xxx", "005aaa", "005bbb", "005ccc"}

    RES->>PM: map_to_aad(user_ids)
    PM->>SF: Batch: get User FederationIdentifier / Email for each ID
    PM-->>RES: Graph ACL entries with AAD GUIDs

    RES-->>CMD: AclResult(is_public=false, user_ids=[...])
```

---

## Sequence Diagram — ControlledByParent (Recursive)

```mermaid
sequenceDiagram
    participant RES as Resolver
    participant OWD as OWDFetcher
    participant SF as SalesforceClient

    Note over RES: Resolving Case record (OWD = ControlledByParent)

    RES->>OWD: get_owd("Case")
    OWD-->>RES: "ControlledByParent"

    Note over RES: Look up parent from schema.json:<br/>Case → parentField: AccountId, parentType: Account

    RES->>SF: SOQL: SELECT AccountId FROM Case WHERE Id = '500xxx'
    SF-->>RES: AccountId = "001yyy"

    Note over RES: Recurse: resolve("Account", "001yyy", depth+1)

    RES->>OWD: get_owd("Account")
    OWD-->>RES: "Private"

    Note over RES: Account is Private → resolve record-level ACLs for "001yyy"
    Note over RES: (continues with owner + share entries as in Private flow above)
```

---

## Sequence Diagram — Share Table Dynamic Field Discovery

```mermaid
sequenceDiagram
    participant SHR as ShareFetcher
    participant SF as SalesforceClient

    Note over SHR: First call for object type "Account"

    SHR->>SF: Describe AccountShare (fields metadata)
    SF-->>SHR: [{name: "AccountId", type: "reference", referenceTo: ["Account"]},<br/>{name: "AccountAccessLevel", type: "picklist"}, ...]

    Note over SHR: Pass 1: Found reference field "AccountId" → cache
    Note over SHR: Found access level field "AccountAccessLevel" → cache

    SHR->>SF: SOQL: SELECT UserOrGroupId, RowCause, AccountAccessLevel<br/>FROM AccountShare WHERE AccountId = '001xxx'
    SF-->>SHR: share entries

    Note over SHR: Subsequent calls for "Account" use cached field names

    Note over SHR: For custom object "Work_Order__c"

    SHR->>SF: Describe Work_Order__Share (fields metadata)
    SF-->>SHR: [{name: "ParentId", type: "reference"}, {name: "AccessLevel", type: "picklist"}]

    Note over SHR: Pass 2: Found generic "ParentId" → cache
    Note over SHR: Found generic "AccessLevel" → cache
```

---

## Files

| File | Description |
|------|-------------|
| `__init__.py` | Package entry point; documents public API (`AclResolver`, `SalesforceClient`, `AclResult`). |
| `models.py` | Shared data classes and enums (`OWDVisibility`, `PUBLIC_SENTINEL`, `GroupType`, `ShareEntry`). |
| `resolver.py` | Main orchestrator coordinating all resolution steps. |
| `org_wide_defaults.py` | Fetches and interprets Org-Wide Default visibility settings (single cached query). |
| `share_fetcher.py` | Queries `<Object>Share` tables with dynamic field discovery and caching. |
| `user_handler.py` | Validates individual User principals (005-prefix IDs) checking `IsActive`. |
| `group_handler.py` | Dispatches non-user principals to specialised handlers by `Group.Type`. |
| `role_handler.py` | Expands role-based groups using iterative DFS on `UserRole` hierarchy. |
| `territory_handler.py` | Resolves Territory2-based sharing with hierarchy traversal. |
| `queue_handler.py` | Handles queues, public groups, managers, and org-wide grants. |
| `salesforce_client.py` | Thin async REST client for SOQL queries, pagination, and sObject describe. |
| `principal_mapper.py` | Converts Salesforce User IDs into Graph-API-ready ACL entries with AAD GUID resolution. |
| `identity_models.py` | Data models for identity crawl and group ACL (`EntityVisibility`, `SfUser`, `SfGroup`, `GroupIdentityType`). |
| `group_id_formats.py` | Canonical format strings for external group IDs (shared by identity crawl and group ACL builder). |
| `identity_queries.py` | SOQL query methods for identity crawl (authorized users, role hierarchy, shares, frozen users, etc.). |
| `group_acl_builder.py` | Group-based ACL builder: produces ACLs referencing external groups instead of individual users. |
| `identity_sync.py` | Identity Crawl handler: creates/populates external groups in Microsoft Graph. |

## Usage

The engine supports three ACL resolution modes, controlled by environment variables:

| Environment Variable | Value | ACL Mode | Description |
|---------------------|-------|----------|-------------|
| *(default)* | — | **Legacy** | User-only ACL via `graph/legacy_acl_resolver.py` |
| `USE_NEW_ACL_ENGINE` | `true` | **New (user-only)** | Modular user-only ACL via `acl_engine/resolver.py` + `PrincipalMapper` |
| `USE_GROUP_ACL` | `true` | **Group-based** | Group-reference ACLs via `acl_engine/group_acl_builder.py` (requires identity crawl) |

### User-only ACL (existing)

```python
from acl_engine import AclResolver
resolver = AclResolver(config, client)
acl_map = await resolver.resolve(records_by_object_type)
```

### Group-based ACL (new)

```python
from acl_engine import GroupAclBuilder
builder = GroupAclBuilder(sf_client, owd_overrides=config.owd_overrides, parent_map=config.parent_map)
acl_map = builder.resolve(records_by_object_type)
```

## Key Decision Points

| Scenario | Behaviour |
|----------|-----------|
| OWD = Public/Read/Edit | Short-circuit → everyone has access |
| OWD = ControlledByParent | Recursively resolve parent record (max depth 5) |
| OWD = Private | Query owner + share table, expand all principals |
| Principal starts with `005` | Direct user validation (check `IsActive`) |
| Group.Type = Organization | Short-circuit → `PUBLIC_SENTINEL` (everyone) |
| Group.Type = RoleAndSubordinates | DFS through `UserRole.ParentRoleId` hierarchy |
| Parent not found during recursion | Fallback to Private ACL resolution |
| Max parent depth exceeded | Fallback to Private ACL resolution |
