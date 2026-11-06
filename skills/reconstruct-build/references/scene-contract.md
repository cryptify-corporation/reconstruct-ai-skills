# Working with reconstruct.scene.v1

This is a reading guide to the advertised normalized Scene artifact, not a promise that every adapter, export tool or target renderer is implemented. Read actual MCP schemas and artifact issues before choosing a workflow.

When `get_interface_scene` is advertised, its manifest identifies `schema` (the Scene contract) and `artifactSchema` (the export envelope). Read the hashed artifact programmatically: `{schema: "reconstruct.scene-export.v1", scene, assets}`. `assets` maps scene resource IDs to archived `path`, authenticated `uri`, `mimeType`, `sha256` and `sizeBytes`. Over HTTP the manifest also carries `download`: `url`/`schemaUrl` are signed absolute links that need no Authorization header, and any relative `/mcp-transfers/…` or `/mcp-assets/…` URI resolves by appending it to `download.baseUrl`. The links expire at `download.expiresAt`; call the tool again for fresh ones and do not publish them. `schemaPath`/`schemaUri`/`schemaSha256` provide the machine-readable JSON Schema separately. Server paths are local only to that server. The manifest shows at most 30 issues with `issueCount`/`issuesTruncated`; the artifact retains every issue. Exported scene IDs include product, version, environment, page and view; producer-local frame/profile IDs stay scoped to that document.


| Scope | Current fields and meaning |
| --- | --- |
| Document | `schema`, `id`, `platform`, optional `entryFrameId`, `profiles`, `frames`, `resources`, `transitions`, `correspondences`, `issues`, `themes`, `layoutSamples` |
| Profile | `id`, logical `width`/`height`, optional `pixelDensity`, `fontScale`, `safeInsets`, `locale`, `theme` |
| Frame | `id`, `profileId`, `stateId`, `coordinateSpace`, optional `extent` and `viewportBounds`, `truncated`, `nodes`, evidence references |
| Node | Frame-local `id`/`parentId`, `kind`, `semantics`, visibility fact, geometry, layout, paint, text runs, media resource reference, actions, motion, evidence and optional `states` |
| Node state | `state` (`hover`, `focus-visible`, `active`, `disabled`, …), `trigger` action kind, `changes` (adapter properties; Web: computed CSS in camelCase), `kind`, `evidence` (includes the state screenshot), optional `triggerNodeId` when an ancestor's state restyles this node, optional `method` |
| Theme | `id`, `current`, `colorScheme`, `tokens` (Web: CSS custom properties) and `evidence` screenshots. Only the current theme was rendered into frames; others are token sets |
| Layout sample | `id`, source `frameId`, window `width`/`height`, optional `contentHeight`, sparse `nodes` (`nodeId`, measured `bounds`, sampled `layout` properties) and `evidence`. Not a frame: appearance and text were not re-measured |
| Fact | `value`, `kind` (`measured`, `declared`, `inferred`), evidence references and optional confidence |
| Geometry | Visual bounds; optional layout bounds, transform, stacking context and paint order. Rectangles are measurements, not size policies |
| Layout | Typed arrangement/placement, axis size constraints, wrap/reverse, padding, gaps, alignment, tracks, clipping and anchors. Use only represented constraints; a minimum above the maximum is valid CSS and the minimum wins |
| Resource | `id`, `kind`, optional URI, MIME type, SHA-256 and byte count. Follow the export's download manifest; an ID alone does not make original bytes available |
| Transition | Source frame, optional destination frame/node, action, outcome, evidence kind/references and optional motion |
| Correspondence | Explicit relationship between frame/node pairs with provenance; it is not guaranteed one-to-one identity |

An absent fact is unknown. The current evidence-kind enum has no `unknown` member: do not invent one. Some value enums contain `unknown`; use that only where the actual schema permits it. Optional native declarations that cannot be resolved into effective properties remain declarations/issues, not measured values.

`SceneGeometry` bounds use the frame's coordinate plane with logical lengths supplied by the adapter. Native Android raw accessibility bounds are physical screen pixels; density-based conversion must happen explicitly at the adapter boundary. WebView evidence may use a separate viewport plane. Density does not supply an unrecorded font scale, system inset, scroll offset or cross-plane transform.

Node states are measured deltas against the frame's default appearance, not separate frames; an absent state was not measured, not proven unchanged. A theme without `current` was not rendered into any frame, so only its tokens are evidence. Layout samples give breakpoint geometry: nodes missing from a sample were not matched, and a sample does not establish the CSS rule that produced it.

Actions express supported or proposed operations. A transition outcome of `changed` or `unchanged` is different from `failed`, `unknown` and `performedUnverified`. Check the associated frame and evidence before claiming success, and test the rebuilt application separately.

Known representational limits must stay visible. Do not encode arbitrary CSS, Android XML or executable expressions inside numeric/type fields. If a needed grid rule, paint, text feature, transform convention or interaction detail is not expressible or not evidenced, record a bounded issue and use native evidence only to diagnose it. A renderer must not hide that loss by inventing defaults or advertise unsupported exact fidelity.

A useful local evidence map records selection → profile/state/frame → node → fact/resource/transition → evidence and implementation check. Keep large artifacts on disk, preserve original resource digests, and keep this map separate from the product UI.

Media placement distinguishes content images, background images and captured surfaces independently of the node's children. Use the placement's own fit and position; a content-image fit does not establish background-image behavior. A media position point is a fractional anchor in remaining space: 0 aligns left/top, 0.5 centers, 1 aligns right/bottom; negative/outside fractions remain valid. Font resources may include `fontFaces`: family, weight bounds, italic state, codepoint coverage and declaration provenance. Register every relevant archived face; do not choose one font file merely because its filename resembles the family.

A transform, when evidenced, is a column-major 4×4 matrix acting on `(x,y,z,1)` column vectors with logical translations in layout-local coordinates. Visual bounds already include its effect. Motion uses milliseconds and ordered keyframes with offsets 0–1. Value order is opacity `[alpha]`, translation `[x,y,z]` logical units, scale `[x,y,z]` multipliers, rotation `[x,y,z]` radians, bounds `[x,y,width,height]`, color normalized `[r,g,b,a]`. Missing easing, delay or iteration count stays unknown. Browser color conversion may report 8-bit precision/gamut limitations; do not hide those issues when claiming fidelity.

Typography `casing` records presentation independently of the original text: `none`, `uppercase`, `lowercase`, or `capitalize`. Apply it in the target platform, respecting the recorded locale; do not rewrite link destinations, accessible labels or source content to simulate it. An absent value remains unknown.


`entryFrameId` selects the first observed interface, including an initial consent overlay when captured. `default` can be a later dismissed state. `viewportBounds` is the observed window in the frame coordinate plane; its origin records scrolling independently of total `extent`. Open overlays are complete frames, not a union of appeared nodes to append to default. Follow the transition trigger in its source frame. `semantics.modal` and `semantics.expanded` are optional observed booleans, including explicit false; neither is inferred from icon changes.


Paint strokes can limit themselves to explicit `edges` (`top`, `right`, `bottom`, `left`); absent edges means the entire boundary. Preserve divider edges without turning them into a surrounding border. Numeric shadow layers include offsets, blur, spread, inset and normalized color. Keep supported layers even when a separate layer has an explicit issue.
