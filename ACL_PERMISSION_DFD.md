# ACL/Permission Logic - Data Flow Diagram

## Overview
This document describes the data flow for ACL (Access Control List) resolution in the Salesforce CRM Custom Connector.

---

## Level 0: Context Diagram

```
┌─────────────────────┐
│                     │
│  Salesforce API     │──────┐
│  (Records, Shares,  │      │
│   Users, Groups)    │      │
│                     │      │
└─────────────────────┘      │
                             │
                             ▼
                    ┌────────────────────┐
                    │                    │
                    │   ACL Resolution   │
                    │      System        │
                    │                    │
                    └────────────────────┘
                             │
                             │
                             ▼
┌─────────────────────┐      │      ┌─────────────────────┐
│                     │      │      │                     │
│  Microsoft Graph    │◄─────┴─────►│  Graph Connector    │
│  API (M365 Users)   │              │  Items with ACLs    │
│                     │              │                     │
└─────────────────────┘              └─────────────────────┘
```

---

## Level 1: ACL Resolution Process Flow

```
┌──────────────────────────────────────────────────────────────────┐
│                     ACL RESOLUTION WORKFLOW                       │
└──────────────────────────────────────────────────────────────────┘

   START
     │
     ▼
┌─────────────────────┐
│  1. Load Records    │◄──── Salesforce Query Results
│  by Object Type     │      (Accounts, Cases, etc.)
└─────────────────────┘
     │
     ▼
┌─────────────────────┐
│  2. Get Org-Wide    │◄──── SELECT DefaultAccountAccess,
│  Defaults (OWD)     │      DefaultCaseAccess... FROM Organization
└─────────────────────┘
     │
     ▼
┌─────────────────────┐
│  3. Sort Objects    │      Sort by: dependency depth,
│  by Dependency      │      priority, alphabetical
└─────────────────────┘
     │
     ▼
     ┌──────────────────────────────────────┐
     │  FOR EACH Object Type (Account,      │
     │  Contact, Case, etc.)                 │
     └──────────────────────────────────────┘
     │
     ▼
┌─────────────────────┐
│  4. Check           │
│  Visibility Type    │
└─────────────────────┘
     │
     ├────────────────────────┬────────────────────────┐
     │                        │                        │
     ▼                        ▼                        ▼
┌──────────┐          ┌─────────────┐         ┌──────────────┐
│ PUBLIC?  │          │ CONTROLLED  │         │   PRIVATE?   │
│          │          │ BY PARENT?  │         │              │
└──────────┘          └─────────────┘         └──────────────┘
     │                        │                        │
     ▼                        ▼                        ▼
┌──────────┐          ┌─────────────┐         ┌──────────────┐
│ Return   │          │ Inherit     │         │ Build        │
│ everyone │          │ Parent ACL  │         │ Private ACL  │
│ grant    │          │             │         │              │
└──────────┘          └─────────────┘         └──────────────┘
                             │                        │
                             │                        │
                             └────────┬───────────────┘
                                      │
                                      ▼
                             ┌─────────────────┐
                             │  5. Aggregate   │
                             │  ACLs for all   │
                             │  records        │
                             └─────────────────┘
                                      │
                                      ▼
                                    RETURN
                             ACL Map by Object Type
```

---

## Level 2: Private ACL Building (Detailed)

```
┌──────────────────────────────────────────────────────────────────┐
│               BUILD PRIVATE ACL (For each record)                 │
└──────────────────────────────────────────────────────────────────┘

   INPUT: Records for specific object type
     │
     ▼
┌─────────────────────┐
│  1. Get Share       │◄──── Query: SELECT Id, (SELECT UserOrGroupId,
│  Records            │      UserOrGroup.Type FROM Shares) FROM ObjectName
└─────────────────────┘
     │
     ▼
     ┌──────────────────────────────────────┐
     │  FOR EACH Record                      │
     └──────────────────────────────────────┘
     │
     ▼
┌─────────────────────┐
│  2. Extract         │
│  Owner + Shares     │
└─────────────────────┘
     │
     ├─────────────────┬──────────────┐
     ▼                 ▼              ▼
┌──────────┐    ┌──────────┐   ┌──────────┐
│ OwnerId  │    │   USER   │   │  GROUP   │
│  → user  │    │  shares  │   │  shares  │
│   set    │    │  → user  │   │  → group │
│          │    │    set   │   │    set   │
└──────────┘    └──────────┘   └──────────┘
     │                 │              │
     └─────────────────┴──────────────┘
                       │
                       ▼
┌─────────────────────────────────────────┐
│  3. EXPAND GROUPS                       │
│  (Recursive group resolution)           │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────┐
│  Query Group Type   │◄──── SELECT Type, RelatedId FROM Group
│  & Related ID       │
└─────────────────────┘
     │
     ├──────────┬──────────┬──────────┬──────────┬──────────┐
     ▼          ▼          ▼          ▼          ▼          ▼
┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐
│ ORG  │  │ ROLE │  │ ROLE │  │MNGR  │  │MNGR+ │  │PUBLIC│
│      │  │      │  │  +   │  │      │  │SUBS  │  │GROUP │
│      │  │      │  │ SUBS │  │      │  │      │  │      │
└──────┘  └──────┘  └──────┘  └──────┘  └──────┘  └──────┘
   │         │         │         │         │         │
   │         │         │         │         │         │
   ▼         ▼         ▼         ▼         ▼         ▼
┌──────────────────────────────────────────────────────┐
│  → everyone    → role      → role     → manager     │
│     flag         users       tree      hierarchy    │
└──────────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  4. Check "everyone" Flag               │
│  If TRUE → Mark as PUBLIC record        │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  5. Collect ALL unique User IDs         │
│  (union of all records)                 │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  6. GET AUTHORIZED USERS                │
│  FROM SALESFORCE                        │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  Query: SELECT Id, Name, Email,         │
│  FederationIdentifier, UserName,        │
│  IsFrozen FROM User WHERE Id IN (...)   │
│  AND PermissionSets allow ObjectType    │
└─────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────┐
│  7. Filter out FROZEN users             │
└─────────────────────────────────────────┘
     │
     ▼
     ┌──────────────────────────────────────┐
     │  FOR EACH Record                      │
     └──────────────────────────────────────┘
     │
     ├─────────────────┐
     │                 │
     ▼                 ▼
┌──────────┐    ┌─────────────────┐
│ PUBLIC?  │    │ Build User ACLs │
│          │    │                 │
└──────────┘    └─────────────────┘
     │                 │
     ▼                 ▼
┌──────────┐    ┌─────────────────┐
│ Return   │    │ Resolve M365    │
│ everyone │    │ GUIDs           │
│ grant    │    │                 │
└──────────┘    └─────────────────┘
                       │
                       ▼
                ┌─────────────────┐
                │ Return ACL      │
                │ entries or      │
                │ deny all        │
                └─────────────────┘
                       │
                       ▼
                    OUTPUT:
             ACL Map (Record ID → ACL List)
```

---

## Level 3: M365 GUID Resolution (User Mapping)

```
┌──────────────────────────────────────────────────────────────────┐
│              RESOLVE M365 GUID (For each Salesforce User)         │
└──────────────────────────────────────────────────────────────────┘

   INPUT: Salesforce User Object
     │
     ▼
┌─────────────────────┐
│  1. Try             │
│  Federation         │
│  Identifier         │
└─────────────────────┘
     │
     ├──────────┐
     ▼          ▼
┌──────┐   ┌──────┐
│Found?│   │ NULL │
└──────┘   └──────┘
     │          │
     YES        NO
     │          │
     ▼          ▼
┌──────┐   ┌─────────────────────┐
│RETURN│   │  2. Try             │
│      │   │  UserName           │
└──────┘   └─────────────────────┘
                    │
                    ├──────────┐
                    ▼          ▼
               ┌──────┐   ┌──────┐
               │Found?│   │ NULL │
               └──────┘   └──────┘
                    │          │
                    YES        NO
                    │          │
                    ▼          ▼
               ┌──────┐   ┌─────────────────────┐
               │RETURN│   │  3. Try             │
               │      │   │  Email              │
               └──────┘   └─────────────────────┘
                                 │
                                 ├──────────┐
                                 ▼          ▼
                            ┌──────┐   ┌──────┐
                            │Found?│   │ NULL │
                            └──────┘   └──────┘
                                 │          │
                                 YES        NO
                                 │          │
                                 ▼          ▼
                            ┌──────┐   ┌──────┐
                            │RETURN│   │RETURN│
                            │      │   │ NULL │
                            └──────┘   └──────┘


SUB-PROCESS: Resolve Principal GUID
────────────────────────────────────

   INPUT: identifier (string)
     │
     ▼
┌─────────────────────┐
│  Check if NULL      │
│  or empty           │
└─────────────────────┘
     │
     ├──────────┐
     ▼          ▼
┌──────┐   ┌──────────┐
│ YES  │   │    NO    │
│      │   │          │
└──────┘   └──────────┘
     │          │
     ▼          ▼
┌──────┐   ┌──────────────────┐
│RETURN│   │  Check Cache     │
│ NULL │   │                  │
└──────┘   └──────────────────┘
                    │
                    ├──────────┐
                    ▼          ▼
               ┌──────┐   ┌──────┐
               │FOUND │   │  NO  │
               └──────┘   └──────┘
                    │          │
                    ▼          ▼
               ┌──────┐   ┌──────────────────┐
               │RETURN│   │  Check if GUID   │
               │CACHED│   │  format?         │
               └──────┘   └──────────────────┘
                                 │
                                 ├──────────┐
                                 ▼          ▼
                            ┌──────┐   ┌──────┐
                            │ YES  │   │  NO  │
                            └──────┘   └──────┘
                                 │          │
                                 ▼          ▼
                            ┌──────┐   ┌───────────────┐
                            │CACHE │   │ Graph Client  │
                            │&     │   │ Available?    │
                            │RETURN│   └───────────────┘
                            └──────┘          │
                                              ├──────────┐
                                              ▼          ▼
                                         ┌──────┐   ┌──────┐
                                         │ YES  │   │  NO  │
                                         └──────┘   └──────┘
                                              │          │
                                              ▼          ▼
                                         ┌──────┐   ┌──────┐
                                         │LOOKUP│   │CACHE │
                                         │GRAPH │   │NULL  │
                                         │      │   │&     │
                                         └──────┘   │RETURN│
                                              │     └──────┘
                                              ▼
                                    ┌──────────────────┐
                                    │  GRAPH API       │
                                    │  LOOKUP          │
                                    └──────────────────┘
                                              │
                                              ▼
                                    ┌──────────────────┐
                                    │  1. Direct GET   │
                                    │  /users/{id}     │
                                    └──────────────────┘
                                              │
                                              ├──────────┐
                                              ▼          ▼
                                         ┌──────┐   ┌──────┐
                                         │FOUND │   │ 404  │
                                         └──────┘   └──────┘
                                              │          │
                                              ▼          ▼
                                         ┌──────┐   ┌───────────────┐
                                         │CACHE │   │  2. Filter    │
                                         │&     │   │  Query        │
                                         │RETURN│   │  ?$filter=... │
                                         └──────┘   └───────────────┘
                                                           │
                                                           ├──────────┐
                                                           ▼          ▼
                                                      ┌──────┐   ┌──────┐
                                                      │FOUND │   │ NONE │
                                                      └──────┘   └──────┘
                                                           │          │
                                                           ▼          ▼
                                                      ┌──────┐   ┌──────┐
                                                      │CACHE │   │CACHE │
                                                      │&     │   │NULL  │
                                                      │RETURN│   │&     │
                                                      │      │   │RETURN│
                                                      └──────┘   └──────┘
```

---

## Data Stores

```
┌────────────────────────────────────────────────────────────┐
│                      DATA STORES (CACHES)                   │
└────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────┐
│  D1: Principal ID Cache             │
│  ────────────────────────────────   │
│  Key: SF identifier (email/UPN/FID) │
│  Value: M365 GUID or None           │
│  Purpose: Avoid repeated Graph API  │
│           lookups                   │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  D2: Group Cache                    │
│  ────────────────────────────────   │
│  Key: Salesforce Group ID           │
│  Value: (Set of User IDs,           │
│          includes_everyone flag)    │
│  Purpose: Avoid re-expanding groups │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  D3: Role Children Cache            │
│  ────────────────────────────────   │
│  Key: Parent Role ID                │
│  Value: Set of Child Role IDs       │
│  Purpose: Fast hierarchy traversal  │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  D4: Users and Managers             │
│  ────────────────────────────────   │
│  Key: None (list)                   │
│  Value: List of User objects        │
│  Purpose: Manager hierarchy queries │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  D5: Frozen Users Set               │
│  ────────────────────────────────   │
│  Key: None (set)                    │
│  Value: Set of frozen User IDs      │
│  Purpose: Quick frozen user check   │
└─────────────────────────────────────┘
```

---

## External Systems

```
┌────────────────────────────────────────────────────────────┐
│                    EXTERNAL SYSTEMS                         │
└────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────┐
│  Salesforce API                     │
│  ─────────────────                  │
│  Queries:                           │
│  • Organization (OWD)               │
│  • Object Shares                    │
│  • Groups & GroupMembers            │
│  • Users                            │
│  • UserRoles                        │
│  • UserLogin (frozen)               │
│  • Territory2 (future)              │
└─────────────────────────────────────┘

┌─────────────────────────────────────┐
│  Microsoft Graph API                │
│  ───────────────────                │
│  Endpoints:                         │
│  • GET /users/{id}                  │
│  • GET /users?$filter=...           │
│  Purpose: Map SF users to M365 GUIDs│
└─────────────────────────────────────┘
```

---

## ACL Entry Format (Output)

```
┌────────────────────────────────────────────────────────────┐
│                    ACL ENTRY STRUCTURE                      │
└────────────────────────────────────────────────────────────┘

GRANT ACL Entry:
{
  "accessType": "grant",
  "type": "user",
  "value": "<M365 GUID or UPN>"
}

PUBLIC ACL Entry:
{
  "accessType": "grant",
  "type": "everyone",
  "value": "<tenant_id or 'everyone'>"
}

DENY ALL ACL Entry:
{
  "accessType": "deny",
  "type": "everyone",
  "value": "<tenant_id or 'everyone'>"
}
```

---

## Key Decision Points

### 1. Visibility Type Decision
```
IF OWD = Public → Grant everyone
ELSE IF OWD = ControlledByParent → Inherit parent ACL
ELSE → Build private ACL
```

### 2. Group Type Resolution
```
IF Group.Type = "Organization" → Set everyone flag = TRUE
ELSE IF Group.Type = "Role" → Get users in role
ELSE IF Group.Type = "RoleAndSubordinates" → Get role tree users
ELSE IF Group.Type = "Manager" → Get direct reports
ELSE IF Group.Type = "ManagerAndSubordinatesInternal" → Get manager hierarchy
ELSE → Query GroupMembers recursively
```

### 3. M365 User Resolution Priority
```
1. FederationIdentifier (preferred, direct GUID)
2. UserName (SSO username)
3. Email (fallback lookup)
```

### 4. Final ACL Decision
```
IF marked as public → Return everyone grant
ELSE IF user ACL entries exist → Return user grants
ELSE → Return deny all (no access)
```

---

## Performance Optimizations

1. **Batching**: User and group queries batched (max 100 IDs per query)
2. **Caching**: All lookups cached (principals, groups, roles)
3. **Lazy Loading**: Frozen users, role hierarchy loaded once
4. **Deduplication**: Seen values set prevents duplicate ACL entries
5. **Early Exit**: Public visibility skips share/user processing

---

## Error Handling

1. **Missing Handler**: Default to public ACL
2. **No Parent ACL**: Fall back to private ACL building
3. **Graph API Errors**: Cache NULL, continue (user excluded from ACL)
4. **Frozen Users**: Excluded during authorization phase
5. **Empty User Sets**: Return deny all ACL

---

## Summary

**Total Data Flow Steps**: 
- Level 0: 3 external systems
- Level 1: 5 main process groups
- Level 2: 7 sub-processes for private ACL
- Level 3: 3-tier fallback resolution for M365 mapping

**Cache Strategy**: 5 cache stores for performance
**External API Calls**: Salesforce (7+ query types), Microsoft Graph (2 endpoints)
**Output**: ACL Map (Record ID → List of ACL entries)
