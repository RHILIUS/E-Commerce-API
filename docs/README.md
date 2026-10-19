# Backend documentation

These guides explain what the backend does, how it saves information, and how to
run it. No earlier knowledge of the project is assumed. The
[repository introduction](../README.md) explains the repositories and local setup.

## Suggested reading order

1. [How the backend works](architecture.md): services, code responsibilities,
   request handling, saved data, and common terms.
2. [Docker development](docker-development.md): start the local services and learn
   where commands run.
3. [Accounts and sign-in](authentication/README.md): understand registration,
   credentials, cookies, and session invalidation.
4. Choose a feature from the map below. Its guide explains the behavior before
   presenting the detailed contract.

## Feature map

| Area           | Guide                                                                  | What it explains                                                        |
| -------------- | ---------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Authentication | [Accounts and sign-in](authentication/README.md)                       | Registration, login, refresh, logout, and their request contracts       |
| Authentication | [Registration email delivery](authentication/registration-otp-api.md)  | Queuing and delivering one-time codes, expiry, and retries              |
| Account        | [Personal profile](account/profile-api.md)                             | Editing personal details and address independently of recipients        |
| Account        | [Profile photos](account/profile-photo-api.md)                         | Uploading, cropping, removing, and storing photos                       |
| Account        | [Change password](account/change-password-api.md)                      | Password checks, emailed codes, and ending existing sessions            |
| Browsing       | [Product catalog](ecommerce/product-catalog.md)                        | Search, ranking, pagination, and display currencies                     |
| Browsing       | [Product detail](ecommerce/product-detail.md)                          | One product, gallery, visibility, and purchase eligibility              |
| Browsing       | [Store catalog](ecommerce/store-page.md)                               | Store information, products, and sales-based ranking                    |
| Shopping       | [Store carts](ecommerce/carts.md)                                      | Saved quantities, ownership, stock checks, and held cart lines          |
| Shopping       | [Checkout overview](checkout/README.md)                                | How a purchase becomes one order per store                              |
| Shopping       | [Saved recipients](checkout/recipients-api.md)                         | Delivery addresses shared by account settings and checkout              |
| Shopping       | [Checkout preview](checkout/preview-api.md)                            | Reviewing prices and availability without placing an order              |
| Shopping       | [Cash on delivery](checkout/cod-api.md)                                | Atomic placement, safe retries, and confirmation                        |
| Shopping       | [Stripe test checkout](checkout/stripe-api.md)                         | Hosted payment, stock holds, webhooks, and recovery                     |
| Orders         | [Orders, cancellation, refunds, and history](ecommerce/user-orders.md) | Reading purchases, cancelling eligible orders, and following outcomes   |
| Data           | [Checkout and order models](checkout/models.md)                        | The records and constraints behind checkout and inventory               |
| Accounting     | [Demo wallet ledger](wallet/README.md)                                 | Recording store credits and confirmed refunds without editable balances |

## Development and operations

| Guide                                                 | When to use it                                                       |
| ----------------------------------------------------- | -------------------------------------------------------------------- |
| [Interactive API documentation](api-documentation.md) | Explore requests in Swagger or maintain OpenAPI annotations          |
| [Docker development](docker-development.md)           | Start services, run commands, inspect workers, or use Dev Containers |
| [Taxonomy](taxonomy.md)                               | Import product categories and their allowed specification values     |
| [Seed data](seed.md)                                  | Populate or update sample users, stores, products, and purchases     |
| [Disposable resets](reset-dev.md)                     | Deliberately rebuild a disposable database and uploaded media        |
| [Pull request checks](pull-request-checks.md)         | Understand automated checks and merge requirements                   |
| [Deployment](deployment-guide.md)                     | Prepare a server, configure HTTPS, deploy, and maintain it           |
| [Automatic deployment](github-actions-deployment.md)  | Configure or troubleshoot releases from GitHub Actions               |

## Responsibilities across repositories

This repository maintains exact backend contracts and implementation explanations.
The [frontend guides](../../frontend/docs/README.md) describe customer screens and
browser behavior. The [central documentation](../../docs/README.md) owns shared
architecture and the [diagram index](../../docs/diagrams/README.md).

Shared process documents can contain both implemented and proposed phases. Check
those labels against the relevant API guide and implementation before using them
as a contract. The sibling links require the checkout layout described in the
repository introduction; use its repository links when browsing on GitHub.
