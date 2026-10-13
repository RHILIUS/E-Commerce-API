# Account profile API

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

The API and Profile-tab UI are implemented. [Photo management](profile-photo-api.md)
uses a separate authenticated upload/re-crop/removal flow with compression and
storage cleanup. Figma refinement remains pending.
Change Password and verified email/phone changes are outside this endpoint.
