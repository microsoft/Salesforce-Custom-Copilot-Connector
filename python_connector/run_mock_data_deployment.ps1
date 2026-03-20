#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Run full deployment with REAL Graph API and MOCK Salesforce data

.DESCRIPTION
    This script performs the complete deployment flow:
    1. Connection creation (REAL Graph API)
    2. Schema registration (REAL Graph API)  
    3. Item ingestion with ACLs (REAL Graph API + MOCK Salesforce data)
    
    Benefits of mock data:
    - No Salesforce credentials needed
    - Instant data (no API latency)
    - Predictable test data
    - But still validates real Graph connector deployment
    
    Output:
    - Console output with progress
    - Log file: deployment_YYYYMMDD_HHMMSS.log with full details
    - Sample request/response for first item of each object type

.EXAMPLE
    .\run_mock_data_deployment.ps1
    
.NOTES
    Requires: 
    - Valid Azure AD credentials for Graph API
    - env/.env.local configured with AAD_APP_CLIENT_ID and SECRET_AAD_APP_CLIENT_SECRET
    - USE_MOCK_DATA=true
#>

# Set environment to use mock Salesforce data
$env:USE_MOCK_DATA = 'true'

# Get script directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$VenvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"

# Verify Python exists
if (-not (Test-Path $VenvPython)) {
    Write-Error "Python virtual environment not found at: $VenvPython"
    Write-Host "Please activate the virtual environment first"
    exit 1
}

# Verify env file exists
$EnvFile = Join-Path $ProjectRoot "env\.env.local"
if (-not (Test-Path $EnvFile)) {
    Write-Error "Environment file not found: $EnvFile"
    Write-Host "Please copy env/.env.local.example to env/.env.local and configure it"
    exit 1
}

Write-Host "=" -repeat 70 -ForegroundColor Cyan
Write-Host "Full Deployment: Real Graph API + Mock Salesforce Data" -ForegroundColor Cyan
Write-Host "=" -repeat 70 -ForegroundColor Cyan
Write-Host ""
Write-Host "This will perform:" -ForegroundColor Yellow
Write-Host "  1. Connection Creation        [REAL Graph API]" -ForegroundColor White
Write-Host "  2. Schema Registration        [REAL Graph API]" -ForegroundColor White
Write-Host "  3. Search Settings Config     [REAL Graph API]" -ForegroundColor White
Write-Host "  4. Item Ingestion with ACLs   [REAL Graph API + MOCK SF Data]" -ForegroundColor White
Write-Host ""
Write-Host "Data Source:" -ForegroundColor Yellow
Write-Host "  Salesforce Records: MockSalesforceClient" -ForegroundColor Green
Write-Host "  (No real Salesforce API calls - using mock_data folder)" -ForegroundColor Gray
Write-Host ""
Write-Host "Graph API Calls: REAL (requires Azure AD auth)" -ForegroundColor Yellow
Write-Host ""

# Confirm with user
$Confirm = Read-Host "Continue with deployment? (Y/N)"
if ($Confirm -notmatch '^[Yy]') {
    Write-Host "Deployment cancelled" -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "Starting deployment..." -ForegroundColor Green
Write-Host ""

# Run the deployment
Set-Location $ScriptDir
& $VenvPython run_full_deployment.py

# Check exit code
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "=" -repeat 70 -ForegroundColor Green
    Write-Host "✓ DEPLOYMENT COMPLETED SUCCESSFULLY" -ForegroundColor Green
    Write-Host "=" -repeat 70 -ForegroundColor Green
    
    # Find the latest log file
    $LogFile = Get-ChildItem -Path $ScriptDir -Filter "deployment_*.log" | 
               Sort-Object LastWriteTime -Descending | 
               Select-Object -First 1
    
    if ($LogFile) {
        Write-Host ""
        Write-Host "📄 Full log saved to:" -ForegroundColor Cyan
        Write-Host "   $($LogFile.FullName)" -ForegroundColor White
        Write-Host ""
        Write-Host "What was created:" -ForegroundColor Yellow
        Write-Host "  ✓ External connection in Microsoft Graph" -ForegroundColor Green
        Write-Host "  ✓ Schema with 56 searchable properties" -ForegroundColor Green
        Write-Host "  ✓ 20 items ingested (5 per object type)" -ForegroundColor Green
        Write-Host "  ✓ All items have ACL entries" -ForegroundColor Green
        Write-Host ""
        Write-Host "Sample request/response logged for:" -ForegroundColor Yellow
        Write-Host "  • Account" -ForegroundColor Gray
        Write-Host "  • Contact" -ForegroundColor Gray
        Write-Host "  • Lead" -ForegroundColor Gray
        Write-Host "  • Opportunity" -ForegroundColor Gray
        Write-Host ""
        Write-Host "View detailed log:" -ForegroundColor Cyan
        Write-Host "   code `"$($LogFile.FullName)`"" -ForegroundColor Gray
        Write-Host "   # or" -ForegroundColor DarkGray
        Write-Host "   notepad `"$($LogFile.FullName)`"" -ForegroundColor Gray
    }
} else {
    Write-Host ""
    Write-Host "=" -repeat 70 -ForegroundColor Red
    Write-Host "✗ DEPLOYMENT FAILED" -ForegroundColor Red
    Write-Host "=" -repeat 70 -ForegroundColor Red
    
    # Find the latest log file
    $LogFile = Get-ChildItem -Path $ScriptDir -Filter "deployment_*.log" | 
               Sort-Object LastWriteTime -Descending | 
               Select-Object -First 1
    
    if ($LogFile) {
        Write-Host ""
        Write-Host "Check the log file for details:" -ForegroundColor Yellow
        Write-Host "   $($LogFile.FullName)" -ForegroundColor White
    }
    
    exit 1
}
