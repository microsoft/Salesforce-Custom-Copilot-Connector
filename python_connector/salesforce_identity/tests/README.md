# Salesforce Identity Tests

Comprehensive test suite for the `salesforce_identity` module, mirroring the C# test structure from `ClientHelperForIdentitySyncTests.cs`.

## Test Coverage

### ✅ Test Files

| File | Description | Tests |
|------|-------------|-------|
| `test_client_helper.py` | Main ClientHelperForIdentitySync tests | 25+ tests |
| `test_response_processor.py` | Response parsing tests | 8 tests |
| `conftest.py` | Pytest fixtures and configuration | - |

### ✅ Test Categories

#### **1. Permission Set Tests** (`TestPermissionSets`)
- ✅ Basic permission set query
- ✅ Permission sets with filter conditions
- ✅ Permission sets V2 query (with UserType filter)

#### **2. Org-Wide Defaults Tests** (`TestOrgWideDefaults`)
- ✅ Get org-wide defaults
- ✅ Get org-wide defaults map
- ✅ Normalization of ControlledByCampaign/ControlledByLeadOrContact to None

#### **3. Share Tests** (`TestShares`)
- ✅ Get shares for public groups
- ✅ Get shares for public groups (sequential)
- ✅ Get shares from records
- ✅ Get records with shares

#### **4. User Tests** (`TestUsers`)
- ✅ Get users from Salesforce
- ✅ Get users for content ingestion (with nested permission sets)
- ✅ Get users and permission sets
- ✅ Get users and their roles
- ✅ Get frozen users
- ✅ Get users and managers

#### **5. Group Tests** (`TestGroups`)
- ✅ Get group type and related ID
- ✅ Get group members

#### **6. Role Tests** (`TestRoles`)
- ✅ Get user role hierarchy
- ✅ Get user roles assigned to users

#### **7. Global Access Tests** (`TestGlobalAccess`)
- ✅ Get global access users (ModifyAll/ViewAll)

#### **8. Permission Tests** (`TestPermissions`)
- ✅ Get object permissions
- ✅ Get field permissions

#### **9. Miscellaneous Tests** (`TestMisc`)
- ✅ Update access token
- ✅ Checkpoint state tracking

#### **10. Response Processor Tests**
- ✅ Parse PermissionSetAssignment
- ✅ Parse User with nested UserRole
- ✅ Parse User with nested PermissionSetAssignments
- ✅ Parse EntityShareBase with UserOrGroup
- ✅ Parse ObjectRecord with Shares
- ✅ Parse Group
- ✅ Parse empty responses
- ✅ Filter invalid fields

## Running Tests

### **Prerequisites**

```bash
pip install pytest pytest-asyncio pytest-cov
```

### **Run All Tests**

```bash
# From salesforce_identity directory
pytest tests/ -v

# Or from project root
pytest python_connector/salesforce_identity/tests/ -v
```

### **Run Specific Test Class**

```bash
pytest tests/test_client_helper.py::TestPermissionSets -v
pytest tests/test_client_helper.py::TestUsers -v
pytest tests/test_response_processor.py::TestResponseProcessor -v
```

### **Run Single Test**

```bash
pytest tests/test_client_helper.py::TestPermissionSets::test_get_permission_sets_basic -v
```

### **Run with Coverage**

```bash
pytest tests/ --cov=salesforce_identity --cov-report=html
```

This generates an HTML coverage report in `htmlcov/index.html`.

### **Run with Verbose Output**

```bash
pytest tests/ -vv -s
```

## Test Structure

### **Fixtures (conftest.py)**

```python
@pytest.fixture
def mock_sf_client():
    """Mock Salesforce client with query/query_all methods."""
    
@pytest.fixture
def instance_url():
    """Test instance URL."""
    
@pytest.fixture
def access_token():
    """Test access token."""
```

### **Example Test**

```python
@pytest.mark.asyncio
async def test_get_permission_sets_basic(self, mock_sf_client, instance_url, access_token):
    """Test GetPermissionSetsFromSalesforce with basic query."""
    # Arrange
    response_data = {"totalSize": 5, "done": True, "records": [...]}
    mock_sf_client.query.return_value = response_data
    helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
    
    # Act
    result = await helper.get_permission_sets_from_salesforce(...)
    
    # Assert
    assert len(result) == 5
```

## Test Data

Tests use realistic Salesforce data structures matching the C# test responses:

- **Permission Sets**: Profile-owned and custom permission sets
- **Users**: With roles, permission sets, federation identifiers
- **Shares**: AccountShare, OpportunityShare with UserOrGroup types
- **Groups**: Queues, Roles, Organizations, Managers
- **Org-Wide Defaults**: Private, Public, ControlledByParent

## Comparison with C# Tests

| C# Test | Python Equivalent | Status |
|---------|-------------------|--------|
| `EnsureCorrectPermissionSets` | `test_get_permission_sets_basic` | ✅ |
| `EnsureCorrectPermissionSetsV2` | `test_get_permission_sets_v2` | ✅ |
| `GetSharesForPublicGroupsTest` | `test_get_shares_for_public_groups` | ✅ |
| `GetSharesForPublicGroupsSequentialTest` | `test_get_shares_for_public_groups_sequential` | ✅ |
| `GetRecordsWithShares_Success` | `test_get_records_with_shares` | ✅ |
| `GlobalAccessUsersTest` | `test_get_global_access_users` | ✅ |
| `EnsureOrgWideDefaults` | `test_get_org_wide_defaults` | ✅ |
| `GetUsersFromSalesforceTest` | `test_get_users_from_salesforce` | ✅ |
| `GetFrozenUsersFromSalesforceTest` | `test_get_frozen_users` | ✅ |
| `GetGroupTypeAndRelatedIdTest` | `test_get_group_type_and_related_id` | ✅ |

## What's Not Tested

The following areas are **NOT** covered in these tests (would require integration/E2E tests):

- ❌ Actual Salesforce API calls (mocked in unit tests)
- ❌ Network error handling (HttpRequestException, SocketException, etc.)
- ❌ OAuth token expiration and refresh
- ❌ Rate limiting and retry logic
- ❌ Large dataset pagination (tested logically but not with real volume)
- ❌ Concurrent batch processing

For these scenarios, consider creating separate integration tests with a Salesforce sandbox.

## Adding New Tests

### **1. Add Test to Appropriate Class**

```python
@pytest.mark.asyncio
async def test_new_feature(self, mock_sf_client, instance_url, access_token):
    """Test description."""
    # Arrange
    response_data = {...}
    mock_sf_client.query.return_value = response_data
    helper = ClientHelperForIdentitySync(mock_sf_client, instance_url, access_token)
    
    # Act
    result = await helper.new_method(...)
    
    # Assert
    assert result is not None
```

### **2. Run Your New Test**

```bash
pytest tests/test_client_helper.py::TestYourClass::test_new_feature -v
```

### **3. Verify Coverage**

```bash
pytest tests/ --cov=salesforce_identity --cov-report=term-missing
```

## Continuous Integration

Add to your CI pipeline:

```yaml
# .github/workflows/test.yml
- name: Run Salesforce Identity Tests
  run: |
    pip install pytest pytest-asyncio pytest-cov
    pytest python_connector/salesforce_identity/tests/ --cov=salesforce_identity --cov-fail-under=80
```

## Troubleshooting

### **Test Hangs**

If tests hang, ensure you're using `pytest-asyncio`:

```bash
pip install pytest-asyncio
```

And mark async tests with `@pytest.mark.asyncio`.

### **Import Errors**

Ensure `salesforce_identity` is in your Python path:

```bash
# From project root
export PYTHONPATH="${PYTHONPATH}:$(pwd)/python_connector"
pytest python_connector/salesforce_identity/tests/
```

### **Mock Not Working**

Ensure you're returning async mock for async methods:

```python
mock_sf_client.query = AsyncMock(return_value=response_data)
```

## Summary

- **33+ unit tests** covering all major ClientHelperForIdentitySync methods
- **8 response processor tests** for data parsing
- **Mirrors C# test structure** from `ClientHelperForIdentitySyncTests.cs`
- **Easy to run** with `pytest tests/ -v`
- **High coverage** of identity sync functionality

Ready for ACL building integration! 🎉
