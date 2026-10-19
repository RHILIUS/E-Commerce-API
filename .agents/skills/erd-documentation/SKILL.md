---
name: erd-documentation
description: Create or update per-app ERD Markdown guides that embed editable Draw.io SVGs and explain models, fields, relationships, and persistence behavior in the backend repository.
---

# ERD documentation

Use this skill for backend data-model documentation, including documentation affected by model changes. Apply [documentation-writing](../documentation-writing/SKILL.md) for prose and [drawio-erd](../drawio-erd/SKILL.md) whenever the illustration needs creating or updating. A diagram-only request may use drawio-erd independently. Stay within the requested scope; a skill or instruction edit does not trigger artifact generation or migration.

Repository paths in backticks are relative to the backend root. ERDs belong in this repository's `docs/erd/` folder, alongside the models and migrations they explain. The sibling `../docs/` repository owns shared architecture and business-process diagrams.

## Layout and ownership

Maintain one guide and one editable SVG per Django app:

```text
docs/erd/
├── guides/
│   ├── authentication.md
│   └── store.md
└── diagrams/
    ├── authentication.drawio.svg
    └── store.drawio.svg
```

Use the actual lowercase app label as the matching file stem. Each SVG has one internal Draw.io page named after that app. Full entity definitions belong to their owning app; other apps use name-only reference rectangles as specified by drawio-erd.

Embed the matching SVG with meaningful alternative text, for example in `guides/store.md`:

```markdown
![Store entities and relationships](../diagrams/store.drawio.svg)
```

Standard Markdown displays the exported SVG image and cannot select an internal Draw.io page. Do not use page fragments or query parameters to select a page. Do not create or update Mermaid ERDs. Migrate existing ERD documents only when the task calls for it; preserve their verified explanations and update affected links when moving them.

## Establish the contract

Before writing, inspect the relevant backend models, inherited fields, migrations, settings, services, and documented contracts. Review current frontend consumers where they affect the explanation. Check the backend ERD index at `docs/erd/README.md` and relevant shared architecture/business-process documentation in `../docs/`. Report discrepancies between code, migrations, and existing diagrams instead of inventing a reconciliation. These ERD skills own the new ERD format; older Mermaid ERD instructions are superseded, while business-process requirements remain separate.

## Guide contents

- State scope and implementation status. Identify proposed behavior and any intentionally omitted entities or fields.
- Render the app's SVG, then explain each owned model's purpose, ownership, and role. Identify its Python model and actual database table name.
- Include a field table for every owned entity with **Field / column**, **Meaning**, and **Constraints and behavior**. Cover every concrete database field, including implicit primary keys and inherited columns. Explain each field's purpose and relevant type/length, PK/FK/unique constraints, nullability, defaults, validation, and lifecycle. Distinguish model attribute names from database column names when they differ. Do not merely repeat the name as its explanation.
- Explain each relationship's foreign-key ownership, cardinality, optionality, and deletion behavior. Explain many-to-many through tables and model-level constraints, including conditional uniqueness, separately from basic cardinality. `blank=True` concerns validation; it does not make a database relationship nullable.
- Explain name-only external references and link to the owning app's guide when it exists. Do not duplicate its field tables or invent links to guides not yet created; identify missing documentation explicitly.
- Add verified persistence/lifecycle notes where relevant: creation, updates, expiry, resets, counters, sensitive data, transaction boundaries, concurrency, and recovery. Distinguish database constraints from application validation and unresolved proposed decisions.
- Link to implementation and relevant existing business-process guides. Add or update the app's entry in `docs/erd/README.md` when creating or moving maintained ERD documentation. Keep the backend documentation reading map and shared diagram index linked to that index rather than maintaining duplicate app lists.

Keep the diagram, field tables, relationships, and lifecycle explanations synchronized. A cross-app schema change may affect both the owning illustration and references in other app diagrams; inspect affected consumers within scope.

## Validation and handoff

Check entity/field coverage against the verified model contract, relative image/source links and anchors, matching app filenames/page names, and the diagram index. Format changed Markdown with the configured formatter. Apply drawio-erd's embedded-data and exact-image validation for changed SVGs. Check the intended Markdown host when available; distinguish host checks from local image rendering. Report actual checks, tooling versions where relevant, omissions, and unresolved discrepancies in the handoff or PR, not as a historical verification section in the maintained guide.
