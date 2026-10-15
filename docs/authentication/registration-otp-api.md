# Registration OTP delivery

`POST /api/auth/send-otp/` accepts `{ "email": "person@example.com" }` without
requiring authentication. Validation and issuance rules are unchanged.

Success is HTTP 200 with `email`, `message: "Verification email queued."` and
`retry_after` (60 seconds by default). It confirms broker acceptance, not SMTP
acceptance or inbox delivery. The API no longer waits for the email provider.
Validation returns 400; send limits return 429. Broker publication failure returns
503 with `detail`, `retry_after` and `Retry-After`. Issued state and cooldown stay
committed on publication failure. An uncertain outcome may still deliver mail.

Delivery tasks use a dedicated `registration-email` queue. Before each attempt,
the worker checks the code against the current hash, expiry and verified state.
Temporary transport failures receive at most three retries (5, 10, 20 seconds)
before expiry; permanent failures terminate. Retries never issue a new code.
Later delivery failures are logged without secrets and are not returned to the
original request. Customers can request another code after the existing cooldown.

OTP expiry begins at issuance, not at
email delivery. Verification and registration endpoints and cookies are unchanged.
See the [central process](../../../docs/diagrams/specific-business-processes/authentication/registration-otp-email.md)
and [worker setup](../docker-development.md#registration-email-worker).
