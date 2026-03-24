# Salesforce ACL Test Data Requirements

This document is a short handoff from the tester to the Salesforce Admin for preparing live Salesforce data to validate Salesforce permission data to Microsoft Graph ACL logic.

## Current Constraints And Decisions Needed

| Topic | Current State | Impact | Decision Needed |
| --- | --- | --- | --- |
| Seed data | The current Salesforce org does not yet have enough test records and sharing scenarios for full ACL validation | Some ACL behaviors may not appear during live testing | Add targeted test data for all required scenarios |
| Environment stability | The current Salesforce org used for testing is temporary and may not always be available | Repeatable testing and troubleshooting can be delayed | Confirm whether to continue using this org |
| License capacity | Only five Salesforce licenses are effectively available for the required test users and profiles | It is difficult to create enough distinct owners, shared users, and negative-test users | Decide whether a larger or alternative test org is needed |
| Alternative Salesforce org option | A separate Salesforce org, such as a Salesforce Developer Trial org, could be used as the dedicated test environment. A Salesforce org is the Salesforce environment where users, permissions, objects, and records are configured | A dedicated org would be easier to understand, seed, reset, and reuse for repeatable ACL testing | Confirm whether a dedicated Salesforce org should be created for this work |
| Alternative data source | Nokia Blue may contain more representative data and real-world sharing patterns | Could broaden realistic ACL coverage | Confirm whether we can get help to consume data directly from Nokia's Salesforce Blue instance, or replicate the required Blue data into the test org |

## Test Objective Matrix

| Objective | What Must Be Proven In Live Test |
| --- | --- |
| Record ingestion | Connector reads records from a real Salesforce org and sends them to Graph |
| ACL translation | Salesforce ownership and sharing are converted into the expected Graph ACL entries |
| Identity mapping | Salesforce users are correctly mapped to Entra principals |
| Fallback behavior | Records with no valid mapped principal become deny-all when expected |
| Readback validation | Graph GET responses show the same ACL intent after ingestion |

## Salesforce Admin Responsibility Matrix

| Area | Salesforce Admin Responsibility |
| --- | --- |
| Users | Create or update required Salesforce users for each ACL scenario |
| Permissions | Grant object read access needed for the objects under test |
| Identity mapping | Populate stable identity fields, preferably `FederationIdentifier = Entra object ID` |
| Sharing model | Create owners, direct shares, queues, groups, roles, and manager relationships as required |
| Data population | Create the records and keep naming consistent and traceable |
| Handoff | Provide record IDs, user IDs, and expected access mapping back to the tester |

## Connector Rule Matrix

| Rule | Requirement For Test Data |
| --- | --- |
| Identity resolution order | Connector checks `FederationIdentifier`, then `UserName`, then `Email` |
| Preferred mapping | Use `FederationIdentifier` with the Entra object ID GUID |
| User eligibility | Test users must be active, not frozen, and have object read permission |
| Name filter | Test users should not have `User` in the Salesforce `Name` |
| Unmapped outcome | If no candidate principal maps to Entra, private records fall back to deny-all |
| Parent inheritance | `Contact` can inherit ACL from `Account` when org-wide default is `ControlledByParent` |

## Required User Matrix

| Test Persona | Minimum Count | Purpose | Mapping Requirement |
| --- | --- | --- | --- |
| Mapped owner A | 2 | owner-only and shared-owner cases | Must map to Entra |
| Mapped owner B | 2 | alternate owner and direct share cases | Must map to Entra |
| Mapped queue/group user C | 2 | queue, group, role, or manager expansion cases | Must map to Entra |
| Unmapped user D | 2 | deny-all negative cases | Must not map to Entra |

## Required Object Matrix

| Object | Minimum Count | Primary Validation Goal |
| --- | --- | --- |
| Account | 10 | public ACL behavior and parent references |
| Contact | 10 | controlled-by-parent inheritance from Account |
| Lead | 10 | non-Case ingestion coverage |
| Opportunity | 10 | account-linked business data coverage |
| Case | 10 | core owner/share/deny-all ACL logic |
| Customer_Project__c | 10 | custom-object coverage if used |

## Case Scenario Matrix

| Scenario | Required Salesforce Setup | Expected Graph ACL Result |
| --- | --- | --- |
| Owner-only mapped A | Case owned by mapped user A, no extra share | grant user A |
| Owner-only mapped B | Case owned by mapped user B, no extra share | grant user B |
| Owner-only unmapped D | Case owned by unmapped user D | deny everyone |
| Owner A shared to B | Owner A plus direct Case share to mapped user B | grant user A and B |
| Owner A shared to unmapped D | Owner A plus direct share to unmapped user D | grant user A only |
| Queue or group access | Case owner or share target is queue/group with mapped members | grant expanded mapped members |
| Role-based access | Case share targets role or role-and-subordinates group | grant mapped users in role scope |
| Manager-based access | Case share targets manager-based group | grant mapped manager chain |

## Supporting Setup Matrix

| Setup Area | Minimum Requirement |
| --- | --- |
| Queue | At least 1 queue relevant to Case access |
| Public group | At least 1 group used in Case sharing |
| Role hierarchy | At least 1 parent-child role chain if role tests are needed |
| Manager hierarchy | Manager relationships populated if manager tests are needed |
| Naming | Use a clear prefix such as `ACL-UAT-` for users and records |

## Data Authoring Matrix

| Item | Required Guidance |
| --- | --- |
| Case subject naming | Use deterministic names such as `ACL-UAT-Case-01 OwnerA` |
| Case fields | Populate `Subject`, `Status`, `Priority`, `Origin`, `Reason`, `Type`, `Description`, `AccountId`, and `ContactId` where relevant |
| Custom object properties | If `Customer_Project__c` or any other custom object is in scope, define it with richer representative business properties, not just minimal identifiers. Include enough fields to validate schema coverage and ingestion behavior, such as status, dates, descriptive text, owner-related fields, and relevant lookup or relationship fields where possible |
| User inventory | Share Salesforce user IDs plus matching Entra object IDs for mapped users |
| Record inventory | Share final IDs for Cases, Accounts, Contacts, and other seeded records |

## Expected Handoff Back To Tester

| Deliverable | What Salesforce Admin Should Provide |
| --- | --- |
| User inventory | Salesforce user ID, Name, UserName, Email, FederationIdentifier, Entra object ID |
| Record inventory | Record IDs and titles/subjects for all seeded test records |
| Sharing inventory | Queue membership, group membership, role structure, manager relationships |
| Expected access matrix | Case ID, owner, direct shares, group/queue principals, expected final Entra GUIDs, expected outcome |

## Acceptance Matrix

| Validation Area | Pass Condition |
| --- | --- |
| Ingestion | Connector reads the expected records from Salesforce |
| PUT payloads | Graph PUT payloads reflect the intended ACL candidate set |
| GET validation | Graph GET responses reflect the final stored ACLs |
| Mapped owner cases | Produce `grant user` ACLs |
| Unmapped owner cases | Produce `deny everyone` ACLs |
| Shared cases | Produce the expected union of mapped users |
| Parent-controlled Contact | Inherits Account ACL where applicable |
| Public objects | Show public ACL behavior where applicable |

## Recommended Execution Order

| Step | Action |
| --- | --- |
| 1 | Create or confirm test users |
| 2 | Apply permissions and object read access |
| 3 | Populate `FederationIdentifier` for mapped users |
| 4 | Create queues, groups, roles, and manager links if needed |
| 5 | Create Accounts and Contacts |
| 6 | Create Cases with the required owners and shares |
| 7 | Return the final inventories and expected access matrix to the tester |
