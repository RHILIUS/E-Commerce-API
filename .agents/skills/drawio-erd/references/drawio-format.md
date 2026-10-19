# Draw.io format and validation

## Editable document structure

A `.drawio.svg` must be both a valid SVG image and an editable Draw.io document. Renaming `.drawio` XML to `.svg` does not produce an image. Preserve embedded model data in the SVG root's `content` attribute as an XML-escaped `<mxfile>` document.

Inside `<mxfile>`, each `<diagram>` has a stable unique `id` and an app-label `name`. Its content is either an uncompressed `<mxGraphModel>` or the compressed form used by Draw.io. Decode compressed text by Base64 decoding, raw-DEFLATE decompressing, and URL decoding; encode with the inverse operations using UTF-8. Do not double-escape the XML attribute. Preserve the existing representation when practical.

The graph contains a `<root>` with `mxCell` IDs `0` and `1`, where cell `1` belongs to `0`. Entity cells belong to the layer; row cells belong to their entity; marker and field cells belong to their row. Connectors have `edge="1"`, valid `source`/`target` cell IDs, and relative geometry. All IDs must be unique within their graph. Keep stable IDs when updating existing objects.

## Table construction

Use these established styles as the baseline, adjusting width and geometry for content:

```text
Entity:
shape=table;startSize=30;container=1;collapsible=1;childLayout=tableLayout;fixedRows=1;rowLines=0;fontStyle=1;align=center;resizeLast=1;html=1;

Row:
shape=tableRow;horizontal=0;startSize=0;swimlaneHead=0;swimlaneBody=0;fillColor=none;collapsible=0;dropTarget=0;points=[[0,0.5],[1,0.5]];portConstraint=eastwest;top=0;left=0;right=0;bottom=0;

Key-marker cell:
shape=partialRectangle;connectable=0;fillColor=none;top=0;left=0;bottom=0;right=0;overflow=hidden;whiteSpace=wrap;html=1;

Field-name cell:
shape=partialRectangle;connectable=0;fillColor=none;top=0;left=0;bottom=0;right=0;align=left;spacingLeft=6;overflow=hidden;whiteSpace=wrap;html=1;
```

Set the PK row's bottom border to `1`, its marker to `fontStyle=1`, and its field name to `fontStyle=5` (bold plus underline). Row `i`, starting at zero, has `y=30*(i+1)` and height `30`; the entity height is `30*(field_count+1)`. Marker width is `30`, field-cell x is `30`, and field width is entity width minus `30`. Keep alternate bounds and row widths consistent when present.

Use `orthogonalEdgeStyle` or `entityRelationEdgeStyle` with `rounded=1;arcSize=20;`, explicit entry/exit sides, and waypoints when necessary. Existing manually routed connectors may retain `edgeStyle=none;orthogonal=1;` with the same rounding settings to preserve their routes. The `startArrow` marker describes the source endpoint; `endArrow` describes the target endpoint. Set their values using the cardinalities in the owning skill, with `startFill=0;endFill=0`. Do not reverse markers just because the FK direction differs from the connector's source/target direction.

The exported SVG must show gently curved transitions at direction changes, not sharp right-angle corners. For direct SVG editing, trim each incoming/outgoing segment around a bend and join the trimmed points with a quadratic `Q` curve through the original corner. Use about 10 pixels of trim on each side, clamped to half the adjacent segment lengths for short runs. Leave straight connectors and endpoint symbols unchanged. `stroke-linejoin=round` alone only softens the stroke join and does not replace the required bend geometry. Keep the editable rounding settings and visible curves synchronized.

## Keep the image and model synchronized

Prefer exporting the exact page through available Draw.io tooling with embedded diagram data. Record the exporter/version used when available. If editing XML/SVG directly, update both the embedded graph and visible SVG geometry, labels, connector markers, bounds, and text together. Do not only append an invisible embedded page and claim it is the image displayed in Markdown. Each maintained app image must actually display that app's diagram.

Retain plain SVG text or appropriate text fallbacks so labels survive image embedding; do not rely solely on HTML foreign objects. Escape model names and labels as data. Keep the artifact self-contained, with no generated scripts or external resources required to render it.

## Verify the deliverable

1. Parse the SVG, decode its embedded document, and validate graph IDs, parent/source/target references, page name, and intended page count. Compare the entity/column set and relationship endpoints with the verified Django schema.
2. Open the delivered artifact in Draw.io when available and confirm its entities and connectors remain editable. Inspect the actual Draw.io canvas; XML parsing alone does not establish editability or routing correctness.
3. Render and inspect the exact delivered SVG. Check readable titles/fields, PK/FK markers, endpoint cardinalities, gently rounded bends, bounds, clipping, overlaps, and self-reference loops. Confirm short bends remain smooth without distorting endpoint markers. A separately reconstructed layout preview does not validate the delivered image or Draw.io's actual connector routing.
4. Check embedding on the intended Markdown host when available. Report structural checks, exact-image rendering, editor checks, and host checks separately, including any unavailable tooling. Do not claim a successful export, visual inspection, editor opening, or host check that did not occur. Keep validation evidence in the handoff/PR.
