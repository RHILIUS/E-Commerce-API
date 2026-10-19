---
name: pull-request-writing
description: Prepare backend PR comments and descriptions from the implemented diff, API contracts, and actual validation evidence.
---

# Pull request writing

Apply this workflow when the user requests a backend PR comment or description. It governs the written deliverable; it does not authorize creating, publishing, or merging a PR. Standalone commit-message requests do not require the full template.

When asked to prepare a PR comment or PR description, use the following format and base every statement on the actual branch diff, tests, and documented contract. Omit sections that do not apply.

```markdown
## Summary

<Briefly explain the user-facing or system-level outcome and why the change is needed.>

## Changes

- <Group related implementation changes by behavior rather than listing commits.>
- <Call out database migrations, API contract changes, and security-sensitive behavior explicitly.>

## API Contract

- **Endpoint:** `<method and path>`
- **Success:** `<status and response shape>`
- **Errors:** `<relevant status codes and response shapes>`
- **Cookies/authentication:** `<cookie or token behavior, if applicable>`

## Testing

- [x] `<check that passed>`
- [ ] `<check that could not run and the reason>`

## Follow-up / Out of Scope

- <Known follow-up work, documented inconsistency, or intentionally excluded behavior.>
```

Keep the documentation reviewer-focused: describe observable behavior, important implementation and security decisions, verification performed, and unresolved risks. Do not claim that a check passed unless it was run successfully. Never include secrets, credentials, plaintext OTPs, complete tokens, cookies, or `.env` values.
