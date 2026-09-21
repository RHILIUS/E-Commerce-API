# Product catalog

`GET /api/shop/products/` is public and returns HTTP 200 with a JSON array.
An empty catalog returns `[]`. Only active products appear, including those
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
The endpoint does not yet implement search or pagination.
Normal Bearer authentication still applies when a token is supplied; invalid
tokens return HTTP 401.

Each product contains:

- `id`: product ID.
- `name`: product name.
- `short_description`: card description.
- `price`: decimal string with two fractional digits.
- `sold_count`: integer.
- `default_image`: `null` when no image is marked default; otherwise an object
  containing `image` (storage URL), `alt_text`, and `crop`.

Local image URLs are made absolute using the request; cloud storage URLs are
provided by the configured storage backend. If multiple images are marked
default, the lowest image order and then ID determines the selected image.
