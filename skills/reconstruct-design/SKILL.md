---
name: reconstruct-design
description: "Extract measured design tokens and component specifications from Reconstruct captures. Use for reference styling or mapping a captured interface onto an existing design system."
---

# Design from captured evidence

Work on the selected interface and pinned capture. Determine whether the user wants faithful reconstruction of authorized material or a new design based on reference patterns. Preserve that choice throughout the work.

## Read measured layers

Use the advertised `get_interface_scene` export and `reconstruct.scene.v1` as the canonical Web/Android model. Select the frame's profile and state, logical coordinate space, typed paint/layout/typography, resources and evidence. Keep measured effective properties distinct from source declarations and inferred layout rules; missing facts remain unknown. A measured rectangle does not establish fixed sizing or padding. Raw DOM/CSS, Android layouts and resource XML are optional diagnostics; they must not become a platform-specific replacement model.

For unresolved Web-specific details, use `get_page_summary`, `get_themes`, and scoped `get_fonts` for relevant typography and themes. For a component, use `get_page_regions` → `get_page_components` → `get_component_tree`, then the required styles, typography, text, animation or asset layers. Component boundaries are heuristic; inspect a neighboring candidate when a boundary is wrong. Use current tool schemas and exact returned IDs and view keys.

Prefer grouped styles. Expand individual elements only to resolve a concrete ambiguity. Read selected profiles/states independently, preserving logical dimensions and any recorded density, font scale, insets, locale and theme. Native accessibility geometry does not resolve the effective font, color or theme from APK declarations. Do not merge CSS-pixel WebView coordinates into native screen bounds without a recorded mapping. For responsive Web work, read desktop and mobile profiles independently, then identify fluid layout rules and breakpoint changes instead of treating all coordinates as reusable absolute positions. A reusable specification should explain stacking, reflow and content order, not just give desktop pixel values. Computed values are measurements, not the original CSS rules or complete token taxonomy.

Newer captures add measured evidence; check `get_page_summary.evidence` before inferring. `breakpoints` lists the page's own media and container widths, and `viewportVariants` holds layouts and screenshots at extra widths between them; behavior between measured widths is still inferred. An element's `component` and `stateCatalog` name the states that apply to its kind, which were measured, which changed nothing and which could not be produced; `states[]` carries the computed delta per state, with `trigger` when an ancestor's state restyles the node. `observedStates` are states the page already showed. Use `get_page_evidence_image` with the screenshot names for a visual check of a state, a revealed menu or the alternate theme. `themeVariants`, `alternateTheme.tokens` and `themeScreenshots` describe the other color scheme; `responded=false` means the page did not switch. `pseudoStyles` hold `::before`, `::after` and styled `::marker`. Hover is not measured on touch profiles. A state absent from the catalog is unknown, not proven identical.

Use `get_spacing` for padding, margin, gaps and radii, and `get_sizing` for component dimensions, only when those measurements are relevant. Pin page/view/version/environment. Default context rows identify the owning component and include source element, parent and child examples; narrow with `component` and follow `hasMore`. Use `summary=true` for the aggregate value matrix, not as a substitute for ownership evidence. Preserve CSS shorthand and fractions. Margin is an outer offset, not a measured distance to a neighbor; gap belongs to a container, whose listed children are examples rather than verified adjacent pairs. Context sizing excludes generic containers. Samples are capped at three per row; counts and `truncated` describe the captured evidence, not full-site coverage. Sample IDs use the raw capture namespace (`idSpace=capture`), not enriched MCP layer IDs; use `find_elements` by sample label to resolve a layer ID before inspecting geometry or claiming which elements a distance separates.


## Map into the project

Inspect the project's existing tokens and component library before introducing another one. Reuse appropriate primitives, including their keyboard and focus behavior. Extract repeated roles, typography, spacing and effects from evidence; identify inferred token groupings separately from measured values. Avoid rounding away measured geometry in a faithful transfer.

For each relevant component specify variants, measured and observed states with their evidence names, proposed missing states, token mapping, responsive evidence and accessibility behavior. Preserve text runs and measured line breaks when exact rendering is required. Map font families and weights to downloadable archived font files; list missing files explicitly, because an external family name alone cannot reproduce typography. Include motion timing, easing, trigger and reduced-motion behavior when measured. Read motion evidence before inventing animation; captured opacity may represent an in-flight entrance rather than a permanently invisible element.

Treat availability of archived images, SVG and fonts separately from permission to ship them. For a reference-based new product, substitute the user's own or appropriately licensed assets and record substitutions. For an authorized faithful reconstruction, retain permitted assets and copy. Do not impose rebranding on the user's own interface.

## Verify and hand off

Check real text/background combinations, focus visibility and component semantics; element `a11y` with `source: chromium` is the browser's computed role and accessible name. A token contrast calculation alone is not a full accessibility audit. Keep contrast fixes or intentional departures separate from faithful baseline measurements.

Deliver only the tokens and component specifications needed for the task, with Scene frame/profile/state and fact provenance, unresolved properties, native diagnostic evidence and substitutions. Do not present an unavailable export/adapter or unsupported paint/layout feature as an exact implementation. Implement primitives only when requested or necessary for the authorized build. Use [the component template](assets/component.md) for substantial handoffs. Screenshots are targeted visual checks; do not derive every value from repeated image analysis when structured evidence is available.


For overlays and disclosures, inspect the Scene's `entryFrameId`, all relevant state frames and transitions before implementing behavior. The entry frame may show consent while `default` is the later dismissed state. Preserve each frame's `viewportBounds` and coordinate space, full backdrop/panel geometry, clipping, stacking, underlying content and `semantics.modal`/`expanded` observations. Use a transition's trigger node in its own source frame; repeated node IDs across frames are not identity. Reproduce and visually check the observed open and closed states at each measured profile. A successful click or icon replacement with `performedUnverified` does not establish that a panel opened; missing modal evidence is an explicit capture gap, not permission to invent its design.
