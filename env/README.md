# Env

Environment variable configuration files for local development.

> **⚠️ Security:** Files containing actual credentials (`.env.local`, `.env.local.user`) must **never** be committed to source control. Only `.env.local.example` should be tracked.

## Files

| File | Description |
|------|-------------|
| `.env.local.example` | Template with placeholder values — copy this to `.env.local` and fill in your credentials. |
| `.env.local` | Main config: Salesforce instance URL, API version, client ID, Azure AD app/tenant IDs, query tuning parameters, ACL engine flags. |
| `.env.local.user` | Secrets file containing `SECRET_SALESFORCE_CLIENT_SECRET` and `SECRET_AAD_APP_CLIENT_SECRET`. |

## Quick Setup

```bash
cp env/.env.local.example env/.env.local
# Edit .env.local with your Salesforce and Azure AD credentials
# Create .env.local.user with client secrets
```

Refer to `python run.py guide` for the full list of required environment variables.
