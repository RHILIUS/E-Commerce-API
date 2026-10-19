# Product catalog

The catalog is the product list customers browse before choosing an item. Guests
can use it without signing in. Search narrows the list; pagination divides it into
small pages rather than returning every product at once.

## How browsing works

1. The customer opens the catalog or submits a search.
2. The backend selects active products, ranks them, and returns one page.
3. Each card includes the store's original price and a display price for the
   viewer's country currency, where conversion is available.
4. Opening a card uses the separate [product-detail API](product-detail.md).

A display conversion is an estimate for browsing, not an accepted purchase price.
Checkout performs its own review. An unavailable rate keeps the original amount
and currency together rather than relabelling the amount.

## Request and pagination

`GET /api/shop/products/?page=1&q=laptop` is public and returns HTTP 200 with
`{"count": 24, "next": "...?page=2&q=laptop", "previous": null, "results": [...]}`.
Pages contain at most 8 products; `count` is the total matching product count.
`next` and `previous` are absolute page URLs or null, retaining query parameters.
The page defaults to 1. Invalid or out-of-range pages return HTTP 404 with
`{"detail": "Invalid page."}`. Page 1 of an empty catalog returns
`{"count": 0, "next": null, "previous": null, "results": []}`.
Optional `q` is trimmed and matches a case-insensitive substring in product names
or short descriptions. Blank searches match all active products.
Filtering and ranking happen before pagination; only the selected page's images
are prefetched. Rankings may change between requests as orders change or age.

## Which products appear first

Only active products appear, including those
with zero stock. For authenticated users, categories rank by the sum of item
quantities in that user's non-cancelled orders, including pending orders.
Every product in a category receives the same score; unpurchased or missing
categories receive zero. The four ordering priorities are:

1. Personal category quantity across all time, highest first.
2. Product quantity ordered across all users during the rolling popularity window,
   highest first.
3. Product creation date, newest first.
4. Product ID, highest first.

Popularity uses non-cancelled orders whose creation time is between now minus
`PRODUCT_POPULARITY_DAYS` and now (both inclusive). The setting defaults to 7 days
and can be configured through the environment, for example 30 for a quieter store.
Guests and users without qualifying history have zero category scores, so recent
popularity determines their first priority. Missing popularity scores are zero.
Previously purchased products remain eligible. Other users' orders contribute
only to popularity, not personal preferences. Deleted products contribute nothing, and category
changes affect preferences because ranking uses each product's current category.
Scores are calculated in the database and are not included in the response.
Normal Bearer authentication still applies when a token is supplied; invalid
tokens return HTTP 401.

## Product fields

Each product contains:

- `id`: product ID.
- `name`: product name.
- `short_description`: card description.
- `price`: original store price, a decimal string with two fractional digits.
- `currency`: ISO 4217 currency of that original price, from the store.
- `display_price`: `{ "amount": "5600.00", "currency": "PHP" }`, a display-only
  converted decimal string and ISO 4217 currency code. This example can represent
  a PHP 5600 product viewed by a Philippine user, without conversion.
- `sold_count`: integer.
- `default_image`: `null` when no image is marked default; otherwise an object
  containing `image` (storage URL), `alt_text`, and `crop`.

Local image URLs are made absolute using the request; cloud storage URLs are
provided by the configured storage backend. If multiple images are marked
default, the lowest image order and then ID determines the selected image.

## Store currencies and display conversion

`Store.currency` is assigned automatically from `country_code` when a store is
created (PH → PHP, US → USD). The migration backfills existing stores from their
countries without modifying any numeric product prices. Unknown countries and
countries with multiple current tender currencies require an explicit policy;
store creation and migration fail rather than guessing a source currency.
Babel supplies current country-to-currency mappings; the migration freezes its
mapping so applying it later has deterministic results.

The currency is read-only in admin. Store country edits that would change currency
are rejected by model validation and ordinary saves; moving between countries
with the same currency is allowed. Changing pricing currency requires a separate,
explicit price migration. Do not bypass this protection with bulk updates.
Product admin shows the store currency beside each price.

The catalog converts from each store's currency to the authenticated account's
country currency. Guests and accounts without an unambiguous country currency
use USD as their display target. For example, with an illustrative rate of
56 PHP/USD, a PHP-store price of 5600 displays as PHP 5600 for a Philippine user
and USD 100 for a guest. The original `price` and `currency` stay unchanged.

Rates come from [Frankfurter v2](https://frankfurter.dev/), without an API key.
One rate table keyed by the viewer currency serves all store currencies on a page;
there are no per-product network or store lookups. Only the target currency is sent
to the provider. Its table quotes source units per target unit, so conversion divides
the original price by that rate. Decimal arithmetic rounds half up to the target's
standard minor unit (two digits for PHP, zero for JPY, three for KWD).

The Django default cache refreshes rate tables after 24 hours. On failure, valid
cached rows remain usable until seven days after their provider date. Missing,
invalid, or expired rates fall back to the **original store amount and currency**,
never an unchanged amount labeled USD. The response can therefore contain mixed
display currencies during outages. A five-minute refresh gate limits concurrent
and failed requests. `CATALOG_FX_TIMEOUT_SECONDS` defaults to 2 seconds. Empty pages
and pages entirely in the target currency do not contact the provider.

Django's default local-memory cache is per process. Configure a shared Django
cache in production to share tables and refresh gates across workers. Cold pages
may wait for one provider request up to the configured timeout.

This feature only converts catalog display prices. The separate
[checkout preview API](../checkout/preview.md) uses strict single-currency
conversion and signed price snapshots. Placement remains pending. Mixed-currency
catalog or historical amounts must not be treated as a single-currency total.

Viewer country now comes from `UserAddress.country_code`, rather than User.
Accounts without a personal address use the existing USD target. Store source
currency, recipient country and saved purchase amounts are unchanged.
