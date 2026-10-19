# Account profile API

The profile holds the account holder's personal name, phone, and address. It is
separate from delivery recipients, which can describe someone else receiving an
order. Updating the personal address does not rewrite saved recipients or purchases.

## How profile editing works

1. Read the signed-in customer's saved profile.
2. Send the complete editable personal details and nested address when saving.
3. The backend validates them and saves the user and address in one transaction:
   both changes succeed together, or neither is saved.
4. Return the saved profile. The frontend updates its form and shared account name
   and address country from that response.

If a response is lost, saving may already have succeeded. The frontend reloads the
profile for review rather than automatically repeating the write. Field-validation
failures preserve its draft. Photos have a [separate save flow](profile-photo-api.md).

## Scope and authentication

`GET /api/account/profile/` and `PUT /api/account/profile/` require Bearer access-token authentication and operate only on the authenticated account. No account
ID is accepted in the path. Both return 200 with the complete profile; missing or
invalid authentication returns 401. Other write methods are unsupported (405).
No authentication cookies or tokens are changed.

## Read response

```json
{
  "id": 1,
  "username": "buyer",
  "email": "buyer@example.com",
  "full_name": "Buyer",
  "phone_number": "+639171234567",
  "address": {
    "country_code": "PH",
    "address_line1": "123 Example Street",
    "address_line2": "",
    "city": "Manila",
    "region": "Metro Manila",
    "postal_code": "1000"
  },
  "profile_image": null,
  "profile_image_crop": {}
}
```

GET has no writes. `address` is null when the account has no personal address.
`profile_image` is the stored image URL, or null when absent; the crop metadata is
read-only. The serializer uses request context for local image URLs.

## Save request

PUT accepts `full_name`, `phone_number` and a complete nested `address`. All three
are required. Full name is nonblank, trimmed and limited to 100 characters. Phone
numbers use the existing E.164 validation and must be unique across accounts;
keeping one's own phone is allowed. Phone country and address country are
independent. This endpoint does not verify phone ownership.

Address country, address_line1 and city are required and nonblank. Country is
normalized to uppercase using registration's supported-country validation.
Address line limits are 150 characters; city and region are 100; postal_code is 20. Address_line2, region and postal_code are optional and default to empty
strings when omitted. Address cannot be null or deleted through PUT.

Username, email, ownership, password, permissions and photo fields cannot be
changed through this endpoint. Extra input fields are ignored by the serializers.

Updates lock the account and save User and UserAddress in one transaction. A
missing personal address is created on the first save. Database phone conflicts
after validation return the same phone field error as pre-existing conflicts;
unrelated database failures propagate and roll back both writes.

400 validation errors map fields to message lists, with address errors nested:

```json
{
  "phone_number": ["Phone number is already taken."],
  "address": { "city": ["This field may not be blank."] }
}
```

## Downstream behavior and frontend handoff

Personal-address updates do not modify saved recipients, order snapshots or
pending payments. Catalog display pricing and new COD checkout reviews resolve
currency from the updated personal-address country; Stripe retains its explicit
USD target and strict FX checks remain unchanged. Previously issued checkout
previews and saved purchases retain their existing signed/saved currency values.

The Profile tab consumes the authoritative PUT response and updates
AuthContext's `fullName` from `full_name` and `address_country_code` from
`address.country_code`. Existing frontend resource dependencies will refresh
country-dependent data. Login/refresh also return the updated name/country.

[Photo management](profile-photo-api.md) uses a separate authenticated
upload/re-crop/removal flow with compression and storage cleanup.
[Password changes](change-password-api.md) use their own endpoints. Verified
email/phone changes are outside the profile endpoint.

## Existing database migration

`authentication.0002_remove_user_country_code_useraddress` removes the old
`User.country_code` field without copying its values to personal addresses. It
was designed for disposable data. Applying it to a database whose country values
must be preserved requires a separate data-preservation plan before migration.
Rolling the schema back cannot recover discarded values. This does not change
the current API rule: personal-address country belongs to `UserAddress`.
