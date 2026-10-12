---
name: reconstruct-deploy
description: "Prepare or execute deployment of a product built with Reconstruct. Use for release checks, hosting, DNS, migrations and production configuration in the user’s selected infrastructure."
---

# Release the product into the selected environment

Deploy to the infrastructure and environment requested by the user. Preserve existing CI/CD and operational conventions. Do not default every reconstructed application to Vercel or assume the Reconstruct service infrastructure is the target.

## Establish the release

Identify the repository revision, target environment, release mechanism, domain and required services from the task and existing configuration. Inspect existing authorization before requesting approval again. Prepare the concrete release and validation evidence before any final approval required by the task or environment.

Use [the release checklist](assets/release.md) for a substantial deployment. Run relevant build and regression checks; confirm requested flows and any unresolved high-impact defects. Distinguish a limited verification pass from full release readiness.

## Prepare operations

Build immutable artifacts through the project's pipeline. Resolve environment configuration and secrets through the existing secret store; never include live credentials in skill output or client bundles. Confirm nested `ai-skills` submodules are initialized and record their pinned revisions when they are packaged.

Review migration compatibility, backup/recovery requirements and rollback behavior. Do not assume reverting application code reverses a destructive migration. Prepare provider-specific DNS and TLS changes from current configuration and authoritative documentation. Record which account, domain or provider actions the agent can actually perform.

When applicable, validate OAuth redirect URIs, webhook endpoints and signatures, transactional email configuration and production entitlement behavior. Real purchases, refunds or external messages require authorization covering those actions; test-mode validation is not proof of live billing.

## Execute and verify

Execute the authorized release steps, confirm actual deployment status and exercise health and the requested core flow in the target environment. Stop or roll back according to the concrete failure conditions and agreed recovery path. Avoid repeated mutations when the outcome is uncertain; inspect deployment state first.

Report the deployed revision and environment, URLs, checks, migration status, rollback limits and remaining user actions. Do not claim success from a submitted pipeline, a green local build or unverified DNS alone. Set up monitoring or future follow-ups only when requested.
