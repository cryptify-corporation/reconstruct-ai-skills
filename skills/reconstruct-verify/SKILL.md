---
name: reconstruct-verify
description: "Test and compare a reconstructed interface against pinned Reconstruct evidence. Use for scoped functional checks, visual differences, accessibility checks and reproducible bug reports."
---

# Verify a reconstructed interface

Verify the requested component, page or flow. Maintain separate conclusions for capture coverage, implementation coverage, visual fidelity and functional behavior. Do not collapse them into an unsupported percentage or call a screenshot match functional equivalence.

## Establish the comparison

Pin the reference capture selection and the local implementation revision. Obtain only relevant scenario manifests, steps and image/artifact references through available MCP tools. Read current schemas and retain exact view keys. A recorded replay validates behavior in the captured environment; it is not an executable test of the new implementation.

Match viewport, theme, locale, data shape and application state. Record mismatches that prevent a meaningful comparison. Check source truncation and starter limitations before declaring a missing element an implementation defect.

## Exercise behavior

Derive happy-path assertions from observed actions and outcomes. Add edge cases relevant to the change, such as double submission, refresh, expired sessions or cross-user access, rather than running every possible case for every task. Test the new implementation against local or authorized test environments. Do not fuzz or load-test a reference product.

Reuse the project's test framework and fixtures. For browser tests, prefer roles and accessible labels over brittle source CSS selectors. Check console errors and failed requests with context, distinguishing expected negative cases and unrelated third-party failures from defects. Apply accessibility checks to changed screens and manually inspect behavior automation cannot establish.

## Compare and fix

Use deterministic image comparisons when appropriate, with explicit tolerances and dynamic-region exclusions. Inspect geometry, typography and clipping separately from color differences that are intentional. Grid edge-density scores are coarse diagnostics, not fidelity metrics. Requirements-matrix completion is implementation status, not independent proof that a feature works.

Report only reproduced bugs as confirmed. Use [the verification template](assets/verification.md) to record steps, expected/actual behavior, evidence and severity. Put untested concerns in a separate list. Fix authorized defects, retain meaningful regression checks and rerun affected tests. Broaden testing when dependencies or failures justify it; avoid rerunning unrelated suites after every cosmetic adjustment.

Deliver checks run, results, reproduced defects, intentional differences and unverified areas. A blocked core flow, data loss, incorrect billing or broken authorization prevents readiness for that scope. Do not assert production readiness from a limited visual check.
