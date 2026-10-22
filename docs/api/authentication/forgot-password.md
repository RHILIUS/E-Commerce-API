# Forgot password

Customers who cannot sign in can prove access to their account email and choose a new password. This public recovery flow is separate from registration and Account → Change Password.

## Requests

All routes start with `/api/auth/forgot-password/`. They require no login token. Obtain the CSRF cookie and masked token with GET before posting. Send the masked token in `X-CSRFToken` and include browser cookies. Every POST explicitly checks CSRF, including anonymous requests. Browser origins must be included in `CORS_ALLOWED_ORIGINS`, which also supplies `CSRF_TRUSTED_ORIGINS`.

| Method and suffix | JSON request                   | HTTP 200 response                                      |
| ----------------- | ------------------------------ | ------------------------------------------------------ |
| GET root          | None                           | `csrf_token`                                           |
| POST `request/`   | `email`                        | `challenge_id`, `expires_at`, `retry_after`, `message` |
| POST `resend/`    | `email`, `challenge_id`        | Same challenge response, with a new identifier         |
| POST `verify/`    | `email`, `challenge_id`, `otp` | `expires_at`, `message`; reset cookie set              |
| POST `confirm/`   | `new_password`                 | `message`; reset and refresh cookies cleared           |

Emails are trimmed and lowercased. Challenge identifiers are UUIDs. Codes contain exactly six ASCII digits. `expires_at` is an absolute timestamp. `retry_after` is seconds. Request and resend responses, cooldowns and queue errors are equivalent for active, inactive and unknown accounts. They do not disclose account membership.

## Codes and limits

The backend stores a hashed code and queues the same task shape for every email after the transaction commits. The worker sends only when the account is eligible and the challenge is still current and unexpired. Temporary transport failures use bounded retries within expiry. Publication or provider acceptance does not prove inbox delivery.

Codes expire after 300 seconds. A successful send preparation starts a 60-second cooldown. Resend rotates the challenge UUID and code, invalidates an earlier reset grant, and preserves failed attempts. Restarting issuance also preserves attempts.

Each normalized email has a durable 900-second window: at most 20 requests, 5 sends, 20 verification operations and 5 incorrect codes. Request and verification budgets include rejected validation attempts after serializer validation. Send budget is charged after cooldown and challenge checks. Exhaustion requires waiting for the window to end. The IP throttle is shared across all five endpoints, including GET, at 20 requests per minute. Serializer failures do not consume email budgets.

Unknown and inactive emails also retain budget state. There is currently no automatic purge of those records. IP throttling limits creation pressure but does not provide a database retention policy.

## Reset permission and saving

Successful verification consumes the code and sets a ten-minute, single-use `password_reset` cookie. It is HttpOnly, SameSite=Lax, scoped to `/api/auth/forgot-password/`, and Secure outside DEBUG mode. The secret is never returned in JSON; only its SHA-256 digest is persisted. Responses are marked non-cacheable.

Permission is bound to the account, normalized email and encoded password. Account deactivation, email changes or credential changes make outstanding permission unusable. Confirmation locks both account and state, rechecks permission and password policy, then atomically saves the password, increments `authentication_version` and consumes permission. Competing confirmations can succeed only once. Old access and refresh tokens fail the session-version check.

Passwords use 8–64 characters, at least one uppercase letter, lowercase letter and digit, and must differ from the current password. Passwords are not trimmed. Confirmation is a browser-side comparison; the API accepts only `new_password`.

After commit, a password-changed notification is queued. Publication failure is logged without secrets and cannot undo the password save. Email tasks redact argument representations. The broker still handles sensitive code payloads and needs the project's normal access controls. There is no durable outbox: a process or broker failure after commit can lose email publication. Controlled resend is the recovery path for code delivery.

## Errors and recovery

| Status | Meaning and next action                                                                                                    |
| ------ | -------------------------------------------------------------------------------------------------------------------------- |
| 400    | Field arrays such as `email`, `otp` or `new_password`, or `detail` plus `code`; correct the field or follow recovery below |
| 403    | CSRF check failed; reload/bootstrap and retry                                                                              |
| 429    | Rate or attempt limit; `retry_after` and `Retry-After` specify the wait                                                    |
| 503    | Queue/database operation unavailable; preserve cooldown and do not assume a final password save failed                     |

`invalid_code` allows correction. `code_expired` requires resend. `challenge_unavailable` requires restart. `challenge_exhausted` requires waiting and restarting. `reset_unavailable` requires email verification again. `rate_limited` requires waiting. `delivery_unavailable` can occur after code preparation commits. `operation_unavailable` reports database failure.

If the final response is lost, the frontend returns to Login with an uncertain message and never automatically replays confirmation. Try signing in with the new password; start recovery again if needed.

## Implementation

- [Service](../../../apps/authentication/services/password_reset.py): budgets, grants and atomic saving.
- [Views](../../../apps/authentication/views/password_reset.py): CSRF, cookies and API responses.
- [Serializers](../../../apps/authentication/serializers/password_reset.py): request validation and schema.
- [Tasks](../../../apps/authentication/tasks.py): current-challenge checks and email delivery.
- [Data model](../../erd/guides/authentication.md): persisted fields and relationships.
- [Authentication index](README.md).
