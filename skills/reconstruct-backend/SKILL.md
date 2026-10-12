---
name: reconstruct-backend
description: "Design or implement the backend for an application reconstructed with Reconstruct. Use for API contracts, persistence, authorization, billing and integrations behind the reconstructed UI."
---

# Implement the reconstructed application's backend

Turn observed UI requirements into an explicitly designed server implementation. Captures show visible behavior, not the original database, private API, security policy or production topology.

## Establish contracts

Read the task's scope and available scenario evidence. List observed inputs, outcomes and state transitions; mark inferred validation and persistence rules as design decisions. Inspect the existing backend, authentication, repository boundaries and migration workflow before choosing technology. Preserve the project's conventions; apply Cryptify-specific standards only in Cryptify projects.

Define typed request/response models, ownership, authorization and errors for the selected flow. Identify its real data invariants: duplicate submissions, conflicting updates, state transitions, time handling and quotas where applicable. Put enforcement at the appropriate server/storage boundary rather than relying on disabled UI controls.

## Implement the needed services

Use the project's persistence abstraction and migration process. Keep domain decisions separate from database operations. Enforce ownership and authorization on reads and writes, and verify with a second identity where the feature is multi-user. Seed realistic synthetic data, never captured private customer information.

Use official provider APIs and the user's authorized integration configuration. Do not reproduce a reference application's private endpoints from network traces. For billing, verify webhook signatures, deduplicate events and derive entitlement from trusted server state. For time-based jobs, define retries, idempotency and failure visibility. Add uploads, notifications, account deletion or backups only when they belong to the requested feature.

Do not ask for secrets in chat. Use existing environment or secret-management conventions and public configuration examples. Record external account setup that the agent cannot perform instead of claiming an integration is live.

## Validate

Test the actual invariants, authorization boundaries and failure handling. For a mocked provider, state what remains unverified against the real service. Exercise the reconstructed UI against the implemented API where available.

Deliver contract changes, migrations, service configuration, validation evidence and remaining dependencies. Do not automatically deploy, bill a real account or start the market-research and launch workflows merely because the backend now works. Reconstruct MCP is an optional source of UI evidence, not a backend generation service.
