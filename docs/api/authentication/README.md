# Accounts and sign-in

Authentication tells the backend who is making a request. Registration creates an
account after the customer proves access to an email address. Login then creates
credentials for using protected features such as carts and orders.

An **access token** is a short-lived credential sent with API requests. A
**refresh token** allows the browser to obtain another access token without asking
for the password again. The refresh token stays in an HttpOnly cookie, which
browser JavaScript cannot read. A separate registration cookie authorizes account
creation after email verification; it is not a login credential.

## Registering an account

1. The customer submits an email address. The backend normalizes it, rejects an
   existing account's email, stores a hashed one-time code, and queues its email.
2. The customer submits the email and received code. A successful check consumes
   the code and sets a signed, short-lived `registration_token` cookie.
3. The customer submits account details and a password. The backend takes the
   verified email from the cookie, not from an editable request field.
4. The backend creates the user, personal address, and first default delivery
   recipient together. Failure rolls back all three. Success clears the
   registration cookie and returns HTTP 201; the frontend makes a separate login
   request to sign the customer in.

The personal address describes the account holder. A delivery recipient describes
who receives an order. They start with matching details but can be edited
independently afterward.

Registration codes expire after 10 minutes. The default resend cooldown is
60 seconds, with five sends per 15-minute email window and five incorrect code
attempts. Reissuing replaces the code and resets its verification attempts under
the issuance rules. Expired or exhausted records are removed; callers must restart
code issuance. Verification locks the code record so concurrent failures cannot
lose attempt increments. A verified code cannot be used a second time.

The registration cookie expires after 15 minutes. See
[email delivery](registration-otp.md) for queue failures, resend behavior, and
why an accepted request does not prove inbox delivery.

## Login, refresh, and logout

Login checks the email and password. A successful response contains an access
token and public account data, and sets the refresh cookie. The configured access
token lifetime is 15 minutes; the refresh token lifetime is seven days. Token
expiry determines browser cookie retention: login and refresh set a persistent
cookie with `Max-Age` calculated from the issued token's remaining validity.
`SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"]` remains the source of the token lifetime;
the cookie does not have a separate hard-coded duration. Closing and reopening
the browser can therefore preserve login while the cookie and token remain valid,
unless browser privacy settings or the customer remove the cookie.

Refresh reads the cookie. It checks the user and authentication version, rotates
the refresh token, blacklists the previous token, and returns an access token and
current public user data. Each rotation renews the token's configured lifetime
and updates the cookie to match. With the current settings, the seven-day window
runs from the latest successful rotation, rather than the original login.
Missing or invalid refresh credentials return 401;
invalid-token responses clear the cookie. A refresh token in the request body
cannot replace the required cookie.

Logout attempts to blacklist the refresh token and clears the cookie even if no
valid cookie is present. It does not increment the account's authentication
version, so an already issued access token is not globally revoked by ordinary
logout. The frontend also clears its own in-memory credentials.

A successful [password change](../account/change-password.md) increments the
account's authentication version. Protected requests, refresh, and token
verification reject older versions and inactive users. Legacy tokens without a
version count as version zero and stop working after that account's version changes.

## Browser cookies and public account data

The refresh cookie is named `refresh_token`, uses path `/`, and has HttpOnly and
SameSite=Lax. It is Secure when `DEBUG=False`. The registration cookie has the same
security settings but is scoped to `/api/auth/register/` and has a 15-minute
maximum age. Neither token is returned in JSON.

Login and refresh return this shape:

```json
{
  "access": "<access-token>",
  "user": {
    "id": 1,
    "fullName": "Sample Customer",
    "email": "customer@example.com",
    "address_country_code": "PH",
    "profile_image": null
  }
}
```

`address_country_code` is the personal-address country, or null when absent. It
does not come from the phone or default recipient. `profile_image` is the cropped
avatar URL or null; local image URLs are made absolute using the request. These
are public response fields, not all the fields on the user model.

For direct local browser calls, enable credentials and configure the backend's
allowed frontend origin. Production uses the frontend's same-origin API proxy;
see [deployment](../../operations/deployment.md). Cookie policies still apply, so merely
allowing an origin does not override browser cookie rules.

## Request contracts

All routes below use POST. Registration and login do not require a signed-in
account. For protected business endpoints, send `Authorization: Bearer <access-token>`.

| Route                   | Input                                        | Success                                                           |
| ----------------------- | -------------------------------------------- | ----------------------------------------------------------------- |
| `/api/auth/send-otp/`   | JSON `email`                                 | 200 with normalized `email`, `message`, and integer `retry_after` |
| `/api/auth/verify-otp/` | JSON `email`, `otp`                          | 200 message and registration cookie                               |
| `/api/auth/register/`   | Registration cookie and account fields below | 201 message; registration cookie cleared                          |
| `/api/auth/login/`      | JSON `email`, `password`                     | 200 access token and user; refresh cookie set                     |
| `/api/auth/refresh/`    | Refresh cookie; no token body field required | 200 access token and user; refresh cookie rotated                 |
| `/api/auth/logout/`     | Optional refresh cookie                      | 200 `{"detail": "Logout Successful"}`; cookie cleared             |
| `/api/auth/verify/`     | JSON `token`                                 | 200 `{}` when signature, expiry, user, and version checks succeed |

Registration accepts `username`, `full_name`, `phone_number`, `password`,
`country_code`, `address_line1`, and `city`. `address_line2`, `region`, and
`postal_code` are optional and default to empty strings. Address fields are flat
in this request, unlike the nested profile-update address.

Username is 3–30 characters: letters, numbers, underscores, hyphens, and periods;
it starts with a letter or number and cannot contain consecutive special
characters. Full name is nonblank and at most 100 characters. The international
phone number must be valid and unique. Address lines are at most 150 characters,
city/region 100, and postal code 20; country is a supported two-letter code.
Password is 8–64 characters with an uppercase letter, lowercase letter, and digit.
Passwords are not trimmed. Username and email also require uniqueness; concurrent
uniqueness conflicts return field errors after transaction rollback.

## Failures and recovery

| Situation                                        | Response and next action                                                       |
| ------------------------------------------------ | ------------------------------------------------------------------------------ |
| Invalid request fields                           | 400 field messages; correct the values                                         |
| Wrong login or invalid token                     | 401; sign in again or resolve the relevant credential                          |
| Missing, invalid, or expired registration cookie | 401; repeat email verification to obtain a new cookie                          |
| Incorrect or already consumed registration code  | 400; correct the code or restart issuance as appropriate                       |
| Expired registration code                        | 410; request another code                                                      |
| Registration-code attempt budget exhausted       | 429; request a new code, subject to issuance limits                            |
| Code send cooldown or send limit                 | 429 with `retry_after` and `Retry-After`; wait before resending                |
| Broker did not confirm code publication          | 503 with retry timing; issuance can already be saved, so mail may still arrive |

A lost registration response may mean the account was created. Do not describe
that as a definite failure or silently switch to a different identity. The
frontend offers login when subsequent evidence shows that the email is already
registered. Registration POST itself has no purchase-style idempotency key.

## Code locations

| Location                                                                                   | Responsibility                                                                        |
| ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------- |
| [Authentication views](../../../apps/authentication/views/authentication.py)               | Coordinate endpoints and cookies                                                      |
| [Serializers](../../../apps/authentication/serializers/authentication.py)                  | Validate registration/code input, create account records, and return public user data |
| [Token authentication](../../../apps/authentication/token_authentication.py)               | Check active users and session versions                                               |
| [Code issuance](../../../apps/authentication/services/otp_service.py)                      | Generate hashed codes and enforce issuance limits                                     |
| [Refresh cookies](../../../apps/authentication/services/refresh_cookie.py) | Set persistent login and rotated cookies from the issued token expiration |
| [Registration tokens](../../../apps/authentication/services/registration_token_service.py) | Sign and validate the email-verification credential                                   |
| [Settings](../../../ecommerce_backend/settings.py)                                         | Configure token lifetimes, code policies, and browser origins                         |

The [interactive API guide](../README.md) explains how to explore the
published contracts without confusing access-token responses with cookie values.

## Password recovery

[Forgot password](forgot-password.md) explains public email verification, single-use reset permission, limits and session invalidation.
