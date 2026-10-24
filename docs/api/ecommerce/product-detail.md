# Product detail

The product page lets a customer inspect one item before buying: its description,
images, current price, stock, and store. Product visibility and permission to buy
are different rules. An archived item can still be readable while purchasing is
blocked.

## How the page gets its information

1. Request a product by ID.
2. The backend checks whether that viewer may see its lifecycle state.
3. Return current product and gallery data with a purchase-eligibility flag.
4. Cart and checkout writes independently recheck status, ownership where needed,
   and availability. A readable page or `can_purchase` flag does not reserve stock.

## Read access

`GET /api/shop/products/<id>/` returns one product for the Product Page.
Active and archived products are public to anyone with the URL. Drafts require
an authenticated membership in that product's store, with either `admin` or
`staff` role. The current model has no separate owner field. Guests, unrelated
users, and members of other stores receive 404 for drafts. Missing products also
return 404. Invalid supplied Bearer tokens return 401 even for public products.
The endpoint is read-only; it neither archives products nor creates purchases.

## Response

HTTP 200 returns an object with these fields:

- `id`, `name`, `short_description`, `description` (nullable).
- `price`, `currency`, `display_price`: same decimal-string and display-conversion
  contract as the [catalog](product-catalog.md). Explicit `display_currency` takes
  priority; otherwise defaults use personal address, supported verified-country currency, then USD. Failed conversion retains the original
  store currency. These are current product prices, not transaction snapshots.
- `ordered_count`: non-cancelled lifetime units, calculated with the same rules as the
  [catalog](product-catalog.md#product-fields) and store pages.
- `stock`: integer physical stock count.
- `status`: `active`, `draft`, or `archived`; `archived_at`: nullable timestamp.
- `can_purchase`: true only when status is active and stock is greater than zero.
  This describes product eligibility, not buyer authorization or reserved stock.
- `default_image`: `{image, alt_text, crop}` or null, matching the catalog.
- `images`: gallery array of `{id, image, alt_text, crop, is_default, order}`,
  sorted by `order`, then ID. Multiple defaults select the first in this order;
  no marked default yields null even when gallery images exist. An empty gallery
  yields `[]`. Image URLs are storage URLs, made absolute when necessary.
- `store`: `{id, name, profile_image, profile_image_crop, listed_product_count,
created_at}`. The profile image is a URL or null. The count includes only active
  listings, including zero-stock products, and excludes drafts and archives for
  every viewer. `created_at` is store creation; there is no first-sale timestamp
  in the current model. The UI must not label this as a verified first-sale date.

## Purchase and retention boundaries

Archived and draft products remain readable to their permitted audiences but
always return `can_purchase: false`. The [cart](carts.md) and
[checkout](../checkout/README.md) services enforce status and availability during
writes; the response flag alone is not enforcement. This read endpoint does not
create cart lines or purchases.

Changing a product's status to archived preserves its images and order links,
including pending orders and recorded purchase prices. Physical deletion remains
distinct: the existing OrderItem relation uses SET_NULL, so hard deletion removes
the product link. Store deletion/archive workflows must retain product rows when
historical detail URLs need to keep working. This endpoint does not implement
soft deletion, an archive mutation API, or historical product snapshots.

## Browsing currency selection

Optional `display_currency` selects an enabled provider-supported browsing currency. See
[display currencies](currencies.md) for options, defaults, validation, and conversion
fallbacks. It does not change checkout pricing.
