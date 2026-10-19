# Entity-relationship documentation

Shared ERDs use per-app Markdown guides and editable Draw.io SVGs. Do not create
or maintain Mermaid ERDs. Business-process diagrams retain their separate Mermaid
workflow.

Read and apply the maintained workflows in the sibling frontend checkout:

- [ERD documentation](../../../../../frontend/.agents/skills/erd-documentation/SKILL.md)
  owns the guides, SVG embedding, model/field explanations, relationships,
  lifecycle notes, index maintenance, and documentation validation.
- [Draw.io ERDs](../../../../../frontend/.agents/skills/drawio-erd/SKILL.md)
  owns the editable illustrations, database naming, inherited fields, Crow’s Foot
  cardinality, cross-app references, and exact-image/editor validation.

Paths in those skills are relative to the frontend root. Their shared output is
in the central documentation repository: `../docs/diagrams/erd/guides/` for the
backend app’s Markdown explanation and `../docs/diagrams/erd/diagrams/` for its
matching `{app_label}.drawio.svg`. These output paths resolve from either the
backend or frontend checkout because they share a parent. Keep each detailed
workflow in its owning skill instead of duplicating its requirements here.

The [diagram index](../../../../../docs/diagrams/README.md) links the maintained
app guides; the [diagramming specification](../../../../../docs/diagrams/diagramming-specification.md)
separates ERD and business-process formats. Updating these instructions does not
authorize generating or migrating artifacts beyond the user’s requested scope.
