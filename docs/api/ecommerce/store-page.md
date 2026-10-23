# Public store catalog

A store page groups one seller store's public information and active products.
It provides a profile, short product collections for the home view, and a
searchable full catalog. Store membership is separate from public browsing;
these endpoints do not reveal members' private account details.

## How store browsing works

1. Load the store profile for its name, public contact details, and summary.
2. Load Home collections for new products, recent trends, and lifetime most-ordered products.
3. Use the paginated catalog when the customer searches, sorts, or filters.

A counted order is any non-cancelled order, regardless of payment, fulfillment,
or refund status. Counts measure units ordered rather than completed sales. Store
and landing popularity share this rule, but landing personalization and its
seven-day default window differ from the store's 30-day window.

## Access rules

All three endpoints permit guests (`AllowAny`); supplied invalid Bearer credentials
return 401. Store IDs are integer primary keys; missing stores return 404. Only GET
is implemented. Member identities and account contact data are never exposed.

## Profile — GET /api/shop/stores/{id}/

Returns `id`, `name`, `description`, `profile_image`, `banner_image`, `created_at`,
`public_email`, `public_phone`, `address`, `listed_product_count`, `items_ordered`,
and `categories`.

Images are absolute storage URLs or null. Optional contact/description values are
blank strings. `address` is null or the existing business address fields:
`country_code`, `address_line1`, `address_line2`, `city`, `region`, `postal_code`.
Creation date is the store creation timestamp, not first sale or a member's join date.
Categories contain `id`, `name`, `product_count`: active taxonomy categories directly
assigned to active products in this store, ordered by name then ID. No descendant
expansion or seller-created categories. Listing count includes out-of-stock active
products. Units ordered includes non-cancelled historical items whose products have since
been archived or deleted, using the order's store identity.

## Home — GET /api/shop/stores/{id}/home/

Returns `new_products`, `trending_products`, and `most_ordered` arrays, each capped
at ten active products. New Products sorts by newest creation then highest ID.
Trending requires positive recent units; Most Ordered requires positive lifetime
units. Empty arrays are valid; products without ordered units are not promoted into demand sections.

## Catalog — GET /api/shop/stores/{id}/products/

Returns `{count, next, previous, results}` with ten products per page.

| Parameter  | Contract                                                                                      |
| ---------- | --------------------------------------------------------------------------------------------- |
| `q`        | Optional trimmed, case-insensitive substring of name or short description; max 200 characters |
| `sort`     | `latest` (default), `oldest`, `popular`, `most_ordered`                                       |
| `category` | Optional positive active taxonomy ID; exact assignment only                                   |
| `page`     | 1-based page; missing defaults to 1                                                           |

Invalid query values return 400; unknown/inactive categories and invalid/out-of-range
pages return 404. An active category with no matching store products returns zero
results on page 1. Search, category, visibility and ordering apply before pagination.
Zero-order and out-of-stock products remain in the catalog. Draft/archived products
are excluded even for authenticated store members.

Products reuse catalog fields (`id`, `name`, `short_description`, `price`, `currency`,
`display_price`, `ordered_count`, `default_image`) and add integer `stock`. Store responses
map `ordered_count` to non-cancelled lifetime units. Original price and display conversion
use the existing shared pricing service; guests display USD with the existing
original-currency fallback. Default images are prefetched, with lowest order/ID
selected when multiple defaults exist. No per-product image or FX queries.

## Ordered-unit rules

Count item quantities from every non-cancelled order placed at or before now.
Payment and refund states do not affect eligibility. Refunded orders contribute
unless cancelled; cancelled orders never contribute. One order containing three
units adds three, so these figures measure demand rather than completed sales.

Trending and Popular use the rolling 30 days by placement, including the cutoff.
Most Ordered and Units ordered use lifetime quantities. Store totals retain
archived/deleted-product history; product sections contain active listings only.
Ties prefer newest product creation, then highest ID. Landing ranking uses the
same eligibility rule with lifetime personal-category preference and a seven-day
default recent window. Counts refresh on the next request.

Frontend behavior: [Store Page](../../../../frontend/docs/ecommerce/store-page.md).
