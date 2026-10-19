# Backend entity-relationship diagrams

These guides explain the database tables owned by the seven project apps. Each
guide renders an editable Draw.io SVG and explains every model and concrete
column, relationships, constraints, and relevant persistence/lifecycle behavior.
The checked-in models and migrations are the implementation references; these
guides do not confirm that a deployed database has applied those migrations.

| App            | Guide                                      | Scope                                                                                                  |
| -------------- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| Authentication | [Authentication](guides/authentication.md) | Accounts, personal addresses, registration verification, password-change budgets, and permission joins |
| Account        | [Account](guides/account.md)               | Saved delivery recipients, default selection, and soft deletion                                        |
| Store          | [Store](guides/store.md)                   | Store identity/currency, addresses, and account membership                                             |
| Ecommerce      | [Ecommerce](guides/ecommerce.md)           | Taxonomy, specifications, catalog, carts, purchase snapshots, and order milestones                     |
| Checkout       | [Checkout](guides/checkout.md)             | Purchase identity, Stripe test attempts/events, and store-order refunds                                |
| Inventory      | [Inventory](guides/inventory.md)           | Per-line inventory holds and payment resolution                                                        |
| Wallet         | [Wallet](guides/wallet.md)                 | Simulated USD ownership, accounting buckets, and immutable journal                                     |

## Files and editing

Each app has a guide at `guides/{app_label}.md` and an editable image at
`diagrams/{app_label}.drawio.svg`. The SVG has one internal Draw.io page named
with the same app label. Markdown renders its exported image; open the SVG in
Draw.io to edit its entities, field rows, and connectors.

Full entities use their database table and column names. Name-only rectangles
reference entities owned by another app. PK marks a primary key and FK a foreign
key; Crow’s Foot endpoints show relationship cardinality. Connectors use gently
rounded orthogonal bends. Keep the visible image and embedded editable diagram
synchronized when changing the schema.

Django’s built-in auth/session/admin/token tables are outside the owned schema of
these project apps. The authentication guide includes the two automatically
generated user-permission join tables and identifies their built-in references.
Abstract base classes, methods, properties, and choice enums are not extra tables.

## Related explanations

- [Backend documentation](../README.md): repository setup and the full reading map.
- [Checkout architecture](../architecture/checkout-models.md): purchase and inventory rules.
- [Wallet ledger](../architecture/wallet-ledger.md): posting, balances, and immutability.
- [Shared business processes](../../../docs/diagrams/README.md): behavior across applications.

The [shared documentation repository](https://github.com/RHILIUS/E-Commerce-Docs)
retains cross-application architecture, business processes, and historical visual
references. The sibling links above require the local checkout layout described
in the [backend introduction](../../README.md). ERDs are maintained here with the
models, rather than copied into the shared repository.
