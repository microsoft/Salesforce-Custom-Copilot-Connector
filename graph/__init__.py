"""
Graph models package.

This package calls Graph API to manage external connection and schema, and defines data models for Graph external items and ACL entries.

Modules
-------
client
    GraphClient class that wraps Graph API calls with retry logic and long-running operation handling.

connection
    Functions to manage the external connection lifecycle: creation, retrieval, readiness check, and item cleanup.

schema
    Functions to manage the external connection schema: creation, retrieval, and existence check.

The GraphClient automatically handles long-running operations by polling the provided ``Location`` header until completion. The retry logic includes exponential backoff for transient errors and specific handling for authentication errors.
"""