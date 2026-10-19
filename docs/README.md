# Backend documentation

These guides explain what the backend does, how it saves information, and how to
run it. No earlier knowledge of the project is assumed. The
[repository introduction](../README.md) explains the repositories and local setup.

## Choose a documentation area

| Folder                        | Question it answers                                            |
| ----------------------------- | -------------------------------------------------------------- |
| [Architecture](architecture/) | How does the backend work, and how is its data organized?      |
| [API](api/README.md)          | How do I use an endpoint, and what rules does it enforce?      |
| [Development](development/)   | How do I run, populate, and contribute to the backend locally? |
| [Operations](operations/)     | How do I deploy, maintain, or recover an environment?          |

API guides retain the feature explanations alongside their exact contracts.
Architecture covers internal services such as the wallet ledger even when they
have no customer API. Environment reset instructions live under Operations because
they cover both local development and the deployed disposable demo.

## Suggested reading order

1. [How the backend works](architecture/README.md): services, code responsibilities,
   request handling, saved data, and common terms.
2. [Docker development](development/docker.md): start the local services and learn
   where commands run.
3. [Accounts and sign-in](api/authentication/README.md): understand registration,
   credentials, cookies, and session invalidation.
4. Choose a feature from the map below. Its guide explains the behavior before
   presenting the detailed contract.

## Feature map

| Area           | Guide                                                                      | What it explains                                                        |
| -------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Authentication | [Accounts and sign-in](api/authentication/README.md)                       | Registration, login, refresh, logout, and their request contracts       |
| Authentication | [Registration email delivery](api/authentication/registration-otp.md)      | Queuing and delivering one-time codes, expiry, and retries              |
| Account        | [Personal profile](api/account/profile.md)                                 | Editing personal details and address independently of recipients        |
| Account        | [Profile photos](api/account/profile-photo.md)                             | Uploading, cropping, removing, and storing photos                       |
| Account        | [Change password](api/account/change-password.md)                          | Password checks, emailed codes, and ending existing sessions            |
| Browsing       | [Product catalog](api/ecommerce/product-catalog.md)                        | Search, ranking, pagination, and display currencies                     |
| Browsing       | [Product detail](api/ecommerce/product-detail.md)                          | One product, gallery, visibility, and purchase eligibility              |
| Browsing       | [Store catalog](api/ecommerce/store-page.md)                               | Store information, products, and sales-based ranking                    |
| Shopping       | [Store carts](api/ecommerce/carts.md)                                      | Saved quantities, ownership, stock checks, and held cart lines          |
| Shopping       | [Checkout overview](api/checkout/README.md)                                | How a purchase becomes one order per store                              |
| Shopping       | [Saved recipients](api/checkout/recipients.md)                             | Delivery addresses shared by account settings and checkout              |
| Shopping       | [Checkout preview](api/checkout/preview.md)                                | Reviewing prices and availability without placing an order              |
| Shopping       | [Cash on delivery](api/checkout/cash-on-delivery.md)                       | Atomic placement, safe retries, and confirmation                        |
| Shopping       | [Stripe test checkout](api/checkout/stripe.md)                             | Hosted payment, stock holds, webhooks, and recovery                     |
| Orders         | [Orders, cancellation, refunds, and history](api/ecommerce/user-orders.md) | Reading purchases, cancelling eligible orders, and following outcomes   |
| Data           | [Checkout and order models](architecture/checkout-models.md)               | The records and constraints behind checkout and inventory               |
| Accounting     | [Demo wallet ledger](architecture/wallet-ledger.md)                        | Recording store credits and confirmed refunds without editable balances |

## Development and operations

| Guide                                                           | When to use it                                                       |
| --------------------------------------------------------------- | -------------------------------------------------------------------- |
| [Interactive API documentation](api/README.md)                  | Explore requests in Swagger or maintain OpenAPI annotations          |
| [Docker development](development/docker.md)                     | Start services, run commands, inspect workers, or use Dev Containers |
| [Taxonomy](development/taxonomy.md)                             | Import product categories and their allowed specification values     |
| [Seed data](development/seed-data.md)                           | Populate or update sample users, stores, products, and purchases     |
| [Disposable resets](operations/reset-environment.md)            | Deliberately rebuild a disposable database and uploaded media        |
| [Pull request checks](development/pull-request-checks.md)       | Understand automated checks and merge requirements                   |
| [Deployment](operations/deployment.md)                          | Prepare a server, configure HTTPS, deploy, and maintain it           |
| [Automatic deployment](operations/github-actions-deployment.md) | Configure or troubleshoot releases from GitHub Actions               |

## Responsibilities across repositories

This repository maintains exact backend contracts and implementation explanations.
The [frontend guides](../../frontend/docs/README.md) describe customer screens and
browser behavior. The [central documentation](../../docs/README.md) owns shared
architecture and the [diagram index](../../docs/diagrams/README.md).

Shared process documents can contain both implemented and proposed phases. Check
those labels against the relevant API guide and implementation before using them
as a contract. The sibling links require the checkout layout described in the
repository introduction; use its repository links when browsing on GitHub.
