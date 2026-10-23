# Public store catalog

A store page groups one seller store's public information and active products.
It provides a profile, short product collections for the home view, and a
searchable full catalog. Store membership is separate from public browsing;
these endpoints do not reveal members' private account details.

## How store browsing works

1. Load the store profile for its name, public contact details, and summary.
2. Load Home collections for new products, recent trends, and lifetime best sellers.
3. Use the paginated catalog when the customer searches, sorts, or filters.

A **qualifying sale** is a purchase that meets the payment/delivery rules below.
Store popularity uses qualifying sales, while the landing catalog has a different
ranking contract. The two lists can legitimately show different ordering.

## Access rules

All three endpoints permit guests (`AllowAny`); supplied invalid Bearer credentials
return 401. Store IDs are integer primary keys; missing stores return 404. Only GET
is implemented. Member identities and account contact data are never exposed.

## Profile — GET /api/shop/stores/{id}/

Returns `id`, `name`, `description`, `profile_image`, `banner_image`, `created_at`,
`public_email`, `public_phone`, `address`, `listed_product_count`, `items_sold`,
and `categories`.

Images are absolute storage URLs or null. Optional contact/description values are
blank strings. `address` is null or the existing business address fields:
`country_code`, `address_line1`, `address_line2`, `city`, `region`, `postal_code`.
Creation date is the store creation timestamp, not first sale or a member's join date.
Categories contain `id`, `name`, `product_count`: active taxonomy categories directly
assigned to active products in this store, ordered by name then ID. No descendant
expansion or seller-created categories. Listing count includes out-of-stock active
products. Items sold includes qualifying historical items whose products have since
been archived or deleted, using the order's store identity.

## Home — GET /api/shop/stores/{id}/home/

Returns `new_products`, `trending_products`, and `best_sellers` arrays, each capped
at ten active products. New Products sorts by newest creation then highest ID.
Trending requires positive recent units; Best Sellers requires positive lifetime
units. Empty arrays are valid; unsold products are not promoted into sales sections.

## Catalog — GET /api/shop/stores/{id}/products/

Returns `{count, next, previous, results}` with ten products per page.

| Parameter  | Contract                                                                                      |
| ---------- | --------------------------------------------------------------------------------------------- |
| `q`        | Optional trimmed, case-insensitive substring of name or short description; max 200 characters |
| `sort`     | `latest` (default), `oldest`, `popular`, `best_sellers`                                       |
| `category` | Optional positive active taxonomy ID; exact assignment only                                   |
| `page`     | 1-based page; missing defaults to 1                                                           |

Invalid query values return 400; unknown/inactive categories and invalid/out-of-range
pages return 404. An active category with no matching store products returns zero
results on page 1. Search, category, visibility and ordering apply before pagination.
Zero-sale and out-of-stock products remain in the catalog. Draft/archived products
are excluded even for authenticated store members.

Products reuse catalog fields (`id`, `name`, `short_description`, `price`, `currency`,
`display_price`, `sold_count`, `default_image`) and add integer `stock`. Store responses
map `sold_count` to qualifying lifetime units. Original price and display conversion
use the existing shared pricing service; guests display USD with the existing
original-currency fallback. Default images are prefetched, with lowest order/ID
selected when multiple defaults exist. No per-product image or FX queries.

## Sales qualification and ordering

- Count OrderItem.quantity for paid Stripe orders or delivered/completed COD orders.
- Exclude cancelled orders, successful full-order refunds, and future placements.
- Recent units use placement timestamps from now minus **30 days**, inclusive, to now.
- Best Sellers and store Items sold use lifetime qualifying units.
- Popular/Trending sort by recent units descending; Best Sellers by lifetime units
  descending. Ties use newest product creation then highest ID. Latest uses descending
  creation/ID; Oldest uses ascending creation/ID. No viewer personalization.
- A successful refund removes its whole order. The current refund model represents
  full-order refunds only; partial-item refund behavior is outside this feature.
- Ranking becomes eligible when payment/delivery qualifies, but the window remains
  tied to placement time; a COD order placed over 30 days ago cannot become Trending
  merely because it was delivered today.

The landing catalog's existing ranking is unchanged. Catalog, product detail, and
store responses share the qualifying lifetime sold count, calculated from order
items rather than a stored product counter.
Landing ranking scores can differ from store sales metrics. Sold counts describe
units, not a financial/accounting total.

Frontend behavior: [Store Page](../../../../frontend/docs/ecommerce/store-page.md).
