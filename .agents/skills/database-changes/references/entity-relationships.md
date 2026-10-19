# Entity-relationship documentation

Repository paths in backticks are relative to the backend root. Markdown links resolve from this reference.

Whenever creating or updating a Markdown ERD document in the **sibling documentation repository** at `../docs/diagrams/erd/`, include explanations alongside the diagram. This is distinct from the backend's local `docs/` folder. Use lowercase kebab-case filenames and update an existing document when it already covers the model.

Before writing, review the [central diagramming specification](../../../../../docs/diagrams/diagramming-specification.md), [diagram index](../../../../../docs/diagrams/README.md), relevant backend models and migrations, and the services and documented contracts that govern the fields. Verify current behavior against implementation; report inconsistencies rather than inventing semantics.

Each ERD Markdown document must:

1. State its scope and implementation status, clearly distinguishing implemented models from proposed designs. Identify omitted entities or fields when the diagram represents only a subset.
2. Include a fenced Mermaid `erDiagram` showing the relevant entities, fields, keys, and relationship cardinalities.
3. Explain each entity's purpose, ownership, and role in the feature, with its actual model name when implemented. Explain relationships, optionality, foreign-key ownership, and deletion behavior.
4. Provide a field table for every entity with columns **Field / column**, **Meaning**, and **Constraints and behavior**. Cover every field shown in the diagram. Explain its business purpose and relevant type, primary/foreign/unique keys, nullability, defaults, validation, and lifecycle behavior. Distinguish model field names from database column names when they differ. Do not merely repeat the field name as its explanation.
5. Add persistence and lifecycle notes where relevant, covering creation, updates, expiry, resets, counters, sensitive-data handling, transaction boundaries, concurrency, and failure or recovery behavior. Document only verified behavior, and identify unresolved decisions for proposed models.
6. Include implementation references and links to related business-process documents. Add or update the diagram index entry and ensure relative links resolve.

Keep the diagram, field tables, and lifecycle notes synchronized when the schema or behavior changes. Check that every diagram entity and field has an explanation. For new or changed Mermaid fences, record the Mermaid version used, validate parsing, and inspect the rendered diagram for readable labels and relationships. Check the intended Markdown host when available; report any parsing, rendering, or host-verification limitations without claiming checks that were not performed. Include documentation changes and actual validation results in the handoff.
