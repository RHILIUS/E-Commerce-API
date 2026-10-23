# Display currencies

Guests and signed-in customers can choose a currency for browsing estimates.
The choice does not change original store prices, personal addresses, checkout,
existing purchases, seller earnings, or payouts. Country detection is not enabled.

## Options and defaults

`GET /api/shop/currencies/` is public. Bearer authentication is optional; invalid
supplied tokens still return 401. A successful 200 response has this shape:

```json
{
  "currencies": [
    {
      "code": "USD",
      "name": "US Dollar",
      "minor_unit": 2,
      "display_supported": true,
      "stripe_supported": true
    }
  ],
  "default_currency": "USD",
  "default_source": "fallback"
}
```

The example shows one option. The backend discovers all current currencies from
[Frankfurter's currency endpoint](https://frankfurter.dev/#currencies), rather than
maintaining a currency list in source. Legacy currencies are not requested.
Babel provides English names and minor-unit precision. `DISPLAY_CURRENCY_ALLOWLIST`
defaults to `None`, exposing every provider choice; set a tuple only to restrict
the discovered list. A restriction cannot introduce a provider-unsupported code.

The validated list is cached for 24 hours. A refresh failure can reuse the last
validated list until seven days after retrieval. A five-minute refresh gate bounds
concurrent requests and failure retries. Cold requests may wait for one provider
call up to `CATALOG_FX_TIMEOUT_SECONDS`. Without usable metadata, the options endpoint
returns 503 with a detail message; the frontend offers retry. The gate also applies
to retries. Django's default local-memory cache is per process; use a shared cache
in production to share the list and gate across workers.

Availability in the list does not guarantee a current rate for every product.
`stripe_supported` still means only the existing USD test-checkout implementation;
it does not claim credentials, live account eligibility, settlement support, or
withdrawal support. Checkout capability discovery and its stricter FX policy
remain separate from these browsing options.

Defaults use the authenticated customer's personal-address country where Babel
resolves one currency, otherwise USD. `default_source` is `address` or `fallback`.
The automatic default can be outside the explicit options, preserving existing
address-based browsing behavior. The browser validates its remembered explicit
choice against `currencies` and applies it ahead of this default.

## Selecting a browsing currency

Send optional `display_currency=PHP` on:

- `GET /api/shop/products/` and `GET /api/shop/products/{id}/`.
- `GET /api/shop/stores/{id}/home/` and `/products/`.
- `GET /api/shop/carts/`.
- Cart item POST, PATCH, and DELETE endpoints, so their returned carts retain the choice.

Use an exact enabled, provider-supported uppercase code. Blank, lowercase, or unsupported values
return 400 with `{"display_currency": ["..."]}`. Validation happens before cart
mutations, so an invalid preference cannot commit a quantity change or removal.
If an explicit code cannot be checked because no usable capability list exists,
return 503 before a cart mutation. Requests omitting `display_currency` do not
need currency discovery and retain automatic browsing during metadata outages.
Other existing permissions, ownership rules, and response shapes are unchanged.
Pagination links retain the query parameter.

Omitting the parameter preserves address/USD defaults. Conversion uses the
existing daily cached display rates and currency precision. Unavailable rates
retain the original amount and currency, so one page or cart may contain several
actual currencies. Never sum different currencies as though they were one total.
See [catalog conversion rules](product-catalog.md#store-currencies-and-display-conversion).

The frontend remembers explicit choices in localStorage, shared across tabs.
Automatic clears that choice. Browser storage failure leaves an in-memory choice
for the mounted application; reload may lose it. Metadata errors offer retry and
do not block browsing. Existing validated choices remain usable during a metadata
refresh. No preference is written to the customer account.

Checkout preview does not accept a currency override yet. COD retains its existing
address/FX policy; Stripe test checkout remains USD. Read [checkout](../checkout/README.md).
