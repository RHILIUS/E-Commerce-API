---
name: documentation-writing
description: Write or revise maintained backend documentation with plain-language explanations, accurate current contracts, and a clear reading order.
---

# Documentation writing

Apply this workflow when writing or revising maintained backend documentation, including guides updated alongside implementation. These instructions are self-contained; no existing feature document is a required style example. PR descriptions have their own writing workflow.

Write maintained documentation for a future implementer who has no prior project context. Explain the current system in simple, concrete language while preserving exact technical rules.

- Start with the feature's purpose and the person's experience before naming endpoints, models, serializers, or services. Define unfamiliar terms and abbreviations on first use; use short examples when they explain a real distinction.
- Provide a repository introduction, a documentation reading map, and an architecture introduction. These are responsibilities, not fixed filenames or assumptions that particular guides exist. Inspect the current structure and maintain or create the appropriate guide without relying on another document as a required style example.
- Organize feature guides around purpose and entry points, the normal successful flow, saved records and ownership, business rules and failure recovery, then exact requests/responses and code references. Adapt headings and combine sections when the feature is small.
- Explain transactions, locks, idempotency, uncertain outcomes, external-service handoffs, and persistence in plain language before detailing their mechanics. Simplicity must not hide authorization, concurrency, data-integrity, or recovery requirements.
- Setup and operations guides should explain services and connections before prerequisites, ordered commands, settings, and troubleshooting. Label where commands run, define placeholders, and distinguish local settings, container settings, and deployed settings.
- Keep frontend behavior in the frontend guides, exact backend contracts, code explanations, and per-app ERDs in this repository, and shared architecture and business-process diagrams in the sibling documentation repository. Link to maintained sources instead of duplicating their full contents.
- Check statements against code and current consumers. Correct stale claims within scope and report discrepancies elsewhere. Distinguish implemented behavior from proposals and retain real limits such as test-only payments or unavailable settlement workflows.
- Write lasting explanations rather than change logs, implementation dates, PR narratives, or session reports. Do not add Verification sections, historical test results, completed checklists, or validation-run screenshots to maintained overview and feature guides. Put execution evidence in the PR or handoff; keep reusable testing instructions in development/contributor guides and operational health checks in runbooks.
- Continue all checks required by the change. Specialized diagram/ERD validation and PR reporting requirements remain in force; this writing guidance does not remove them.
- Keep the documentation root as the reading map. Group backend architecture and service explanations under `docs/architecture/`, per-app ERD guides and editable SVGs under `docs/erd/` following [erd-documentation](../erd-documentation/SKILL.md), endpoint guides under `docs/api/` by feature, local setup and contribution instructions under `docs/development/`, and deployment/maintenance/recovery under `docs/operations/`. Keep plain-language feature explanations with their API contracts; internal services without public endpoints belong with architecture. Avoid redundant `-api` filename suffixes inside the API directory.
- Keep indexes and relative links current when guides are added, moved, or removed. Check target files and anchors. Explain the sibling repository layout and provide repository URLs for readers browsing only one repository on GitHub.

## Check the result

Confirm explanations match current code and consumers. Check file links, Markdown anchors, indexes, and configured formatting. Keep actual limitations visible. Put validation evidence and unresolved discrepancies in the handoff; specialized ERD validation requirements still apply when that kind of document changes.
