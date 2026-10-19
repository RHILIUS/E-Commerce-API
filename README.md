# E-Commerce backend

This application runs the server side of the E-Commerce shop. It manages customer
accounts, products, stores, carts, orders, and Stripe test payments. The frontend
asks this backend to read or change information; the backend checks permissions
and business rules before saving anything.

Stripe payments and the wallet ledger are a demo. They do not move real money.
Delivery operations, settlement release, returns, and withdrawals are not complete
business workflows. Customer order cancellation and Stripe test refunds are
implemented separately from returns.

## Where this repository fits

The project has three separate repositories. Keep their checkouts beside one
another when working across the system:

```text
E-Commerce/
├── frontend/    # Customer website and browser behavior
├── backend/     # This repository: API, saved data, and background jobs
└── docs/        # Shared architecture, data diagrams, and business processes
```

| Repository                                                          | Responsibility                                                 |
| ------------------------------------------------------------------- | -------------------------------------------------------------- |
| [Frontend](https://github.com/RHILIUS/E-Commerce-API-Frontend)      | Displays pages and sends customer requests                     |
| [Backend](https://github.com/RHILIUS/E-Commerce-API)                | Checks requests, saves records, and talks to external services |
| [Project documentation](https://github.com/RHILIUS/E-Commerce-Docs) | Explains how the whole system fits together                    |

The local `docs/` folder explains this backend. The sibling `../docs/` repository
holds project-wide documentation. They are different locations. Sibling links in
these guides assume the layout above; on GitHub, open the corresponding repository
from the table instead.

## Start reading here

1. Read [how the backend works](docs/architecture/README.md) for the services, code
   structure, and path a request takes through the application.
2. Follow [local development setup](docs/development/docker.md) to run it.
3. Use the [documentation index](docs/README.md) to find a feature or operation.
4. Open [interactive API documentation](docs/api/README.md) for exact
   requests and responses while the backend is running.

## Run it locally

The development stack uses Docker Compose to run Django, PostgreSQL, Redis,
background workers, and an optional database administration interface. Python and
application dependencies are installed in the image, so host Python is not
required for this route. The image uses Python 3.12; pinned dependencies live in
`requirements.txt` and `requirements-dev.txt`.

From the backend repository root:

1. Install Docker with Compose and make sure Docker is running.
2. If `.env` does not exist, copy `.env.example` to `.env`. Fill in the application,
   database, and pgAdmin settings described in the
   [development guide](docs/development/docker.md#first-start).
3. Build and start the services:

   ```sh
   docker compose -f compose.dev.yaml up -d --build
   ```

4. Open [Swagger UI](http://localhost:8000/api/docs/) or the
   [Django admin](http://localhost:8000/admin/). Admin sign-in requires a staff
   account; create one with the command in the development guide.
5. To populate a disposable local database, follow the
   [taxonomy](docs/development/taxonomy.md) and [seed-data](docs/development/seed-data.md) guides. An empty
   database can run correctly without containing products.

The frontend runs separately, normally at `http://localhost:5173`. Its public
API base address is `http://localhost:8000`. The backend must allow that frontend
origin for credentialed browser requests. Keep secrets in backend configuration;
do not put Django, database, email, storage, or Stripe secrets in frontend variables.

Email codes need Redis and the email worker as well as a running API. The default
console email backend writes messages to the worker's output rather than sending
to an inbox. See the [email-worker instructions](docs/development/docker.md#registration-email-worker).

## Everyday commands

Run these commands from the backend repository root after starting the stack:

| Command                                                                                              | Purpose                                           |
| ---------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `docker compose -f compose.dev.yaml ps`                                                              | Show service state                                |
| `docker compose -f compose.dev.yaml logs --tail=100 api`                                             | Read recent API logs                              |
| `docker compose -f compose.dev.yaml exec api python manage.py check`                                 | Check Django configuration                        |
| `docker compose -f compose.dev.yaml exec api python manage.py test`                                  | Run Django tests in a separate test database      |
| `docker compose -f compose.dev.yaml exec api python manage.py spectacular --validate --fail-on-warn` | Check API schema generation                       |
| `docker compose -f compose.dev.yaml down`                                                            | Stop the stack while retaining named data volumes |

For troubleshooting, Dev Containers, migrations, and background services, use the
[development guide](docs/development/docker.md). For deployment, follow the
[server deployment guide](docs/operations/deployment.md), then the
[automatic-release reference](docs/operations/github-actions-deployment.md).
