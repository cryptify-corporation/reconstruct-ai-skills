---
name: reconstruct-design
description: "Extract measured design tokens and component specifications from Reconstruct captures. Use for reference styling or mapping a captured interface onto an existing design system."
---

# Design from captured evidence

Work on the selected interface and pinned capture. Determine whether the user wants faithful reconstruction of authorized material or a new design based on reference patterns. Preserve that choice throughout the work.

## Read measured layers

Use `get_page_summary`, `get_themes`, and scoped `get_fonts` for relevant typography and themes. For a component, use `get_page_regions` → `get_page_components` → `get_component_tree`, then the required styles, typography, text, animation or asset layers. Component boundaries are heuristic; inspect a neighboring candidate when a boundary is wrong. Use current tool schemas and exact returned IDs and view keys.

Prefer grouped styles. Expand individual elements only to resolve a concrete ambiguity. Read desktop and mobile profiles independently; two screenshots do not establish every responsive breakpoint. Computed values are measurements, not the original CSS rules or complete token taxonomy.

## Map into the project

Inspect the project's existing tokens and component library before introducing another one. Reuse appropriate primitives, including their keyboard and focus behavior. Extract repeated roles, typography, spacing and effects from evidence; identify inferred token groupings separately from measured values. Avoid rounding away measured geometry in a faithful transfer.

For each relevant component specify variants, observed states, proposed missing states, token mapping, responsive evidence and accessibility behavior. Preserve text runs and measured line breaks when exact rendering is required. Read motion evidence before inventing animation; captured opacity may represent an in-flight entrance rather than a permanently invisible element.

Treat availability of archived images, SVG and fonts separately from permission to ship them. For a reference-based new product, substitute the user's own or appropriately licensed assets and record substitutions. For an authorized faithful reconstruction, retain permitted assets and copy. Do not impose rebranding on the user's own interface.

## Verify and hand off

Check real text/background combinations, focus visibility and component semantics. A token contrast calculation alone is not a full accessibility audit. Keep contrast fixes or intentional departures separate from faithful baseline measurements.

Deliver only the tokens and component specifications needed for the task, with capture evidence and substitutions. Implement primitives only when requested or necessary for the authorized build. Use [the component template](assets/component.md) for substantial handoffs. Screenshots are targeted visual checks; do not derive every value from repeated image analysis when structured evidence is available.
