# Change Password API

**Status: Implemented in frontend and backend source.** Apply migration 0006 and
restart email workers with matching source before use. The [revision plan](change-password-revision.md)
records the agreed contract, implementation sequence and coordinated release requirements.
Source implementation does not confirm deployment or inbox delivery.

## Customer flow and authorization

Open Account → Change Password, inspect the masked account email, and select
Send verification code. Enter the emailed six-digit one-time password (OTP),
then enter and confirm the new password. A successful save ends existing sessions
and returns to Login. Requesting or verifying a code does not change the password
or invalidate sessions.

Every operation requires a valid Bearer access token. Resolve the account from
that authentication, including when consuming temporary permission. Never accept
an email destination or account identifier as authority. An account-change cookie
alone cannot authorize a signed-out customer or a different account.

Keep account-change permission separate from public recovery and registration.
Do not route the account tab through the anonymous Forgot Password endpoints or
reuse their permission cookie. Reuse focused policy or delivery helpers only
where their responsibilities and authorization rules actually match.

## Requests and responses

All paths below are relative to `/api/account/change-password/`. All successful responses use HTTP 200 and all responses are
non-cacheable. Timestamps are absolute ISO datetimes; cooldowns are integer
seconds. Cookie secrets are never returned in JSON.

| Method and suffix | JSON request               | Success JSON                                                | Side effects                                                                         |
| ----------------- | -------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| GET root          | None                       | `csrf_token`, `masked_email`                                | Establish CSRF cookie; no email or challenge issuance                                |
| POST `request/`   | `{}`                       | `challenge_id`, `masked_email`, `expires_at`, `retry_after` | Create challenge and queue email after commit                                        |
| POST `resend/`    | `challenge_id` UUID        | Same challenge response with replacement UUID               | Replace code and identifier; invalidate previous permission                          |
| POST `verify/`    | `challenge_id` UUID, `otp` | `expires_at`, `message`                                     | Consume OTP and set account-change permission cookie                                 |
| POST `confirm/`   | `new_password`             | `message`                                                   | Save password, consume permission, invalidate sessions, clear change/refresh cookies |

`expires_at` means code expiry on request/resend and permission expiry on verify.
`message` is human-readable; clients must not parse its wording. Confirmation
password matching belongs to the frontend. The API does not accept the old
password as an authorization requirement and never needs a password draft before
verification.

OTP is exactly six ASCII digits. Password policy remains 8–64 characters with
an uppercase letter, lowercase letter and digit, and different from the existing
password. Passwords are never trimmed. Check policy again under the account lock
at final save.

### CSRF and cookie details

Before each POST, obtain a masked `csrf_token` from the authenticated GET
bootstrap response, include cookies, and send the token in `X-CSRFToken`.
Explicitly validate CSRF on every POST even though authentication uses Bearer
tokens. Follow the existing recovery bootstrap/check pattern. Bootstrap must not
clear an existing challenge or grant, so obtaining a fresh token before saving
cannot destroy permission.

Verification sets a separate `password_change` cookie: HttpOnly, SameSite=Lax,
Secure outside DEBUG, with path `/api/account/change-password/` and a ten-minute
maximum age. Use an unpredictable 32-byte random secret and store only its
SHA-256 digest, alongside expiry and the current challenge identifier. The
cookie carries the identifier and secret, following recovery's format; compare
the digest in constant time. Bind permission to the authenticated account,
current email and encoded-password binding. A reset or registration cookie must
never substitute for it. The browser handles Set-Cookie; JavaScript cannot read
the grant.

Successful request/resend invalidates any previous grant on the server and clears
the change cookie in its successful response. If queuing fails after challenge
commit, old server permission is already invalid even if an old browser cookie
remains. Confirmation clears change and refresh cookies on success; an invalid
grant clears the change cookie. Delete cookies using the same path and SameSite
attributes used to set them. Do not clear permission for password-policy errors.

## Durable limits and lifecycle

One `PasswordChangeState` row per user holds the current challenge, grant digest
and expiry, and durable account counters. See the [authentication data-model guide](../../erd/guides/authentication.md)
for every field and relationship. Migration 0006 invalidates legacy challenges
and conservatively carries active budgets into the latest old window, keeping
the last-send cooldown. It does not migrate old authorization into new permission.

Use one shared 15-minute account window for request, send, verification and
failed-code budgets, matching the recovery-style policy. Starting a
new window resets those counters; restarting the form or rotating the code does
not. Existing cooldown timestamps remain authoritative across window boundaries.

| Rule                      | Value                                                               |
| ------------------------- | ------------------------------------------------------------------- |
| Code expiry               | Five minutes from issuance                                          |
| Resend cooldown           | 60 seconds                                                          |
| Request/resend operations | 20 per account window                                               |
| Code sends                | Five per account window                                             |
| Verification operations   | 20 per account window                                               |
| Incorrect codes           | Five per account window                                             |
| Verified permission       | Ten minutes, single use                                             |
| IP guard                  | 20 requests per minute shared across all five routes, including GET |

Charge request/resend and verification operations after serializer validation,
including rejected challenge/cooldown operations. Charge sends only after
eligibility, challenge, cooldown and budget checks pass. On the fifth incorrect
code, consume the OTP and return exhausted with the window's remaining wait.
Further verification and issuance are blocked until the window ends. A new
request must still explicitly issue a code; expiry of a window does not revive
a consumed code.

Request and resend rotate the challenge UUID and replace the code hash and
expiry. Successful verification consumes the OTP, destroys its usable hash, and
issues permission; it cannot be replayed to extend permission. Reissuing codes,
deactivation, an email change, or a credential change makes earlier permission
unusable. Check the current account binding at issuance, verification, save and
background delivery.

## Saving, delivery and uncertain outcomes

Lock the user first, then its state row for state-changing operations. Persist
expected attempt and budget failures before returning them. For final save,
recheck the authenticated account, grant, binding, expiry and password policy
under those locks. Atomically save the password, increment
`authentication_version`, and consume the grant. Competing confirmations can
succeed only once. Database failure rolls back the complete mutation.

Queue verification email after challenge commit. Queue acknowledgement means
broker acceptance, not provider acceptance or inbox delivery. Preserve the
current worker checks and bounded temporary transport retries; do not put
passwords into tasks. Redact OTP and permission arguments and logs.

After a successful password commit, queue a password-changed notification.
The notification identifies an account password change separately from public
recovery.
Notification publication/delivery failure cannot turn a committed password save
into a failed API response. There is no durable outbox; delivery is not guaranteed.

Only confirmed success gets a success message. A lost final response or 5xx
returns the frontend to Login with an uncertain-outcome message; never
automatically replay confirmation in those cases. Existing refresh-and-retry on
an explicit 401 may remain: authentication rejects that request before mutation.
If refresh fails, sign in again. A final-save error must not be converted into
a speculative second password mutation.

## Errors and next actions

Validation errors remain HTTP 400 field-name arrays. Structured workflow errors
use `detail` and `code`, with integer `retry_after` where waiting is required.
Rate and delivery cooldown errors also carry `Retry-After`. Standard JWT 401 and
CSRF 403 responses retain their existing framework shapes; do not invent extra
fields in their frontend types.

| Status / code               | Customer or caller recovery                                                           |
| --------------------------- | ------------------------------------------------------------------------------------- |
| 400 field arrays            | Correct `challenge_id`, `otp`, or `new_password` as applicable                        |
| 400 `invalid_code`          | Correct the code while attempts remain                                                |
| 400 `code_expired`          | Resend after cooldown                                                                 |
| 400 `challenge_unavailable` | Restart code issuance; do not reveal another account's challenge                      |
| 429 `challenge_exhausted`   | Wait for the account window, then restart                                             |
| 400 `change_unavailable`    | Missing, malformed, foreign, changed-account, expired or consumed grant: verify again |
| 401                         | Refresh through existing authenticated-client handling; if unsuccessful, sign in      |
| 403                         | Re-bootstrap CSRF and offer a deliberate retry; no password mutation occurred         |
| 429 `rate_limited`          | Wait for `retry_after`                                                                |
| 503 `delivery_unavailable`  | Wait; code issuance may already have committed                                        |
| 503 `operation_unavailable` | Temporary database failure; final-save UI follows uncertain-outcome handling          |

After lost request/resend responses, retain available cooldown information and
allow deliberate restart subject to server limits; do not automatically resend
email. After a lost verification response, restart verification rather than
assuming the browser obtained permission. Server invalidation makes a leftover
cookie harmless after the next successful issuance.

## Implementation references

- [Account service](../../../apps/account/services/password.py): locks, budgets, grants and atomic save.
- [Views](../../../apps/account/views/password.py): authentication, explicit CSRF, cookie behavior and schema.
- [Serializers](../../../apps/account/serializers/password.py): exact request/response fields.
- [Tasks](../../../apps/authentication/tasks.py): verification delivery and post-change notices.
- [Frontend guide](../../../../frontend/docs/account/change-password.md): revised three-stage interface.
- [Approved SBP](../../../../docs/specific-business-processes/guide/account/change-password.md): implemented customer flow.

Return to the [API index](../README.md).
