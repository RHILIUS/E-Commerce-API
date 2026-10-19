# Registration OTP delivery

Registration uses an emailed one-time password (OTP) to confirm access to the
customer's email address before account creation. The API saves the code first,
then asks a background worker to send it. It does not wait for inbox delivery.
See [accounts and sign-in](README.md) for the full registration and cookie flow.

## How delivery works

1. The API validates the email, checks send limits, and saves a hashed code with
   its expiry and cooldown.
2. Redis accepts a delivery task. Redis is the **broker**, which carries the
   task to the email worker.
3. The worker checks that the code is still current and then calls the configured
   email backend. Delayed tasks cannot send a replaced or consumed code.
4. Temporary transport failures retry the same code within its lifetime. The
   customer can request a replacement after the normal cooldown.

## Request and errors

`POST /api/auth/send-otp/` accepts `{ "email": "person@example.com" }` without
requiring authentication. Email normalization and send limits are described in
[the authentication contract](README.md).

Success is HTTP 200 with `email`, `message: "Verification email queued."` and
`retry_after` (60 seconds by default). It confirms broker acceptance, not SMTP
acceptance or inbox delivery. The API does not wait for the email provider.
Validation returns 400; send limits return 429. Broker publication failure returns
503 with `detail`, `retry_after` and `Retry-After`. Issued state and cooldown stay
committed on publication failure. An uncertain outcome may still deliver mail.

## Worker retries and saved state

Delivery tasks use a dedicated `registration-email` queue. Before each attempt,
the worker checks the code against the current hash, expiry and verified state.
Temporary transport failures receive at most three retries (5, 10, 20 seconds)
before expiry; permanent failures terminate. Retries never issue a new code.
Later delivery failures are logged without secrets and are not returned to the
original request. Customers can request another code after the existing cooldown.

OTP expiry begins at issuance, not at
email delivery. Code verification and account creation use the endpoints and cookies described
in [accounts and sign-in](README.md).
See the [central process](../../../../docs/diagrams/specific-business-processes/authentication/registration-otp-email.md)
and [worker setup](../../development/docker.md#registration-email-worker).
