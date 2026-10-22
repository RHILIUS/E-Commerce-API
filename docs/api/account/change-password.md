# Change Password API

A signed-in customer changes their password by providing the current password
and a one-time code sent to their account email. The code proves access to that
email; it is separate from registration codes.

## How the change works

1. Check the current password and proposed new password, then create a challenge
   and queue its verification email. A **challenge** is the saved, short-lived
   permission to complete this particular password change.
2. The customer enters the received code. Resend replaces the code after the
   cooldown; it does not make old codes valid again.
3. Confirmation checks the challenge, code, and passwords again while the account
   is locked against competing changes.
4. Save the new password, consume the challenge, and increment the account's
   authentication version together. Existing sessions then fail version checks,
   and the customer signs in again.

If confirmation's response is lost, the frontend cannot safely claim failure or
automatically repeat the change. It directs the customer to sign in and explains
that the outcome could not be confirmed.

## Requests and successful responses

All three endpoints require bearer authentication and operate on the signed-in
account. Current-password errors are HTTP 400 field errors, not HTTP 401.

| POST endpoint                           | Required JSON fields                                      | Success                                      |
| --------------------------------------- | --------------------------------------------------------- | -------------------------------------------- |
| `/api/account/change-password/request/` | `current_password`, `new_password`                        | 200 challenge with queued verification email |
| `/api/account/change-password/resend/`  | `challenge_id` UUID                                       | 200 replacement code for the same challenge  |
| `/api/account/change-password/confirm/` | `challenge_id`, `otp`, `current_password`, `new_password` | 200 message, refresh cookie cleared          |

Challenge JSON has `challenge_id` (UUID string), `masked_email`, `expires_at`
(ISO datetime) and `retry_after` (integer seconds). Neither plaintext OTP nor
passwords appear in responses. Confirmation returns `message` and deletes the
HttpOnly `refresh_token` cookie with path `/` and SameSite Lax.

Current password is nonblank, untrimmed and at most 128 characters. New password
uses the registration policy: 8–64 characters, uppercase, lowercase and number;
it must differ from the current password. Password rules are checked again under
the user lock at confirmation. OTP is exactly six ASCII digits. Confirmation
password matching belongs to the frontend. Destination email comes from the user.

## Error contract

| Status | JSON                                                          | Meaning and recovery                                                                                               |
| ------ | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| 400    | Field name mapped to message arrays                           | Correct current/new password or OTP                                                                                |
| 400    | `detail`, `code: challenge_unavailable`                       | Foreign, replaced, consumed or account-invalidated challenge; restart                                              |
| 400    | `detail`, `code: challenge_exhausted`                         | Five incorrect codes; restart subject to account limits                                                            |
| 400    | `detail`, `code: code_expired`                                | Request a replacement code if challenge remains eligible                                                           |
| 401    | `detail` with JWT error metadata when applicable              | Authentication invalid; refresh/sign in                                                                            |
| 429    | `detail`, `code: rate_limited`, integer `retry_after`         | Account or IP budget/cooldown reached                                                                              |
| 503    | `detail`, `code: delivery_unavailable`, integer `retry_after` | Broker did not confirm verification email queuing; wait and retry                                                  |
| 503    | `detail`, `code: operation_unavailable`                       | Database operation failed and rolled back; confirmation UI treats the response as uncertain and directs to sign-in |

Account cooldown and delivery errors include Retry-After. IP throttle errors use
the same JSON cooldown field. A lost confirmation response has an uncertain
outcome: do not automatically replay the password mutation or claim failure.

## Challenge and transaction lifecycle

One `PasswordChangeState` row belongs to each user and has one active UUID at a
time. A request rotates the UUID and resets its failed attempts while preserving
account-level limits. Resend preserves the UUID and attempt budget but replaces
the OTP hash and expiry. Old codes remain invalid if replacement delivery fails.
Challenges bind to the user's email and a keyed digest of the current password.
Email/password changes invalidate them. Registration OTPs cannot authorize them.

Codes use cryptographically secure random generation, Django password hashing,
five-minute expiry, a 60-second resend cooldown, five sends per 15-minute account
window and five incorrect attempts per challenge. Account request and verification
budgets each permit 20 operations per 15 minutes, including failed operations.
Account budgets are durable database fields and cannot be reset by restarting.

IP throttling additionally allows 20 operations per minute across all endpoints
using Django's cache. This approximate guard is per cache instance: use a shared
cache in multi-worker deployments. Forwarded headers are ignored by default (`NUM_PROXIES=0`). Set
`TRUSTED_PROXY_COUNT` to match trusted proxy infrastructure only when the edge
strips client-supplied forwarding headers.

Operations lock the user row and then create/read the state row, serializing
first requests and concurrent confirmations. Failed OTP counters commit before
raising validation errors. Successful confirmation atomically hashes/saves the
password, increments authentication version and consumes the challenge. Database
failure rolls back the update, version and consumption together.

Verification email is queued after challenge commit. Request/resend HTTP 200
confirms broker acceptance, not email-provider acceptance or inbox delivery.
The Celery worker uses the existing `registration-email` queue and rechecks the
user, challenge ID, current email/password binding, OTP hash, expiry and consumed
state before sending. Temporary transport failures retry the same code up to
three times (5, 10, 20 seconds), within expiry; terminal failures are logged with
a task ID and no secrets. Passwords never enter the broker payload. OTP argument
displays are redacted, including retries; protect the plaintext broker payload.

Publication failure retains issuance and budgets and returns the existing 503
`delivery_unavailable` contract with cooldown. Lost publish responses may still
result in delivery; a crash between commit and publish can lose dispatch. There
is no outbox or delivery-status endpoint. Later worker failures do not change the
original response; customers can resend after cooldown. Expiry begins at issuance.
No completion notification email is sent after a successful password change.

## Ending existing sessions

Login tokens carry integer `auth_version`. Protected requests, token verification
and cookie refresh check it against `User.authentication_version` and reject
inactive users. Versionless legacy tokens count as version zero only; refresh
adds the checked claim while rotating them. They stop working after the first
password change. A refresh begun before a change can only issue a stale-version
token, which subsequent requests reject. The frontend also discards late refresh
responses after ending its local session.

See the [frontend change-password guide](../../../../frontend/docs/account/change-password.md)
for the customer interface and lifecycle.
