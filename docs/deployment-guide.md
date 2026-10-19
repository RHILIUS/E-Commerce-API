# Backend deployment guide

Use this guide to set up the backend on a new server, connect it to the frontend,
and hand it over to the next maintainer. Follow steps 1–11 in order for a first
installation. The later sections cover updates, routine checks and recovery.

The examples use AWS EC2, Ubuntu 24.04, the login user `ubuntu`, and the server
folder `/home/ubuntu/ecommerce/backend`. Another VM provider can be used if it
provides the same operating system, access and networking requirements.

The scripts automate application setup; they do not establish that an arbitrary
server, DNS record, credential, or provider account is correctly configured. Follow
the target-environment checks in step 10 before opening the deployment to customers.

## Find the instructions you need

- [1. Prepare your computer and repository](#1-prepare-your-computer-and-repository)
- [2. Create the server](#2-create-the-server)
- [3. Point your API domain at the server](#3-point-your-api-domain-at-the-server)
- [4. Verify the server and test SSH access](#4-verify-the-server-and-test-ssh-access)
- [5. Give the server access to the backend repository](#5-give-the-server-access-to-the-backend-repository)
- [6. Prepare the application settings](#6-prepare-the-application-settings)
- [7. Prepare the deployment settings](#7-prepare-the-deployment-settings)
- [8. Check the prerequisites](#8-check-the-prerequisites)
- [9. Run the first deployment](#9-run-the-first-deployment)
- [10. Verify the application and certificate renewal](#10-verify-the-application-and-certificate-renewal)
- [11. Enable automatic deployments with GitHub Actions](#11-enable-automatic-deployments-with-github-actions)
- [Deploy future updates](#deploy-future-updates)
- [Routine maintenance and changing settings](#routine-maintenance-and-changing-settings)
- [Troubleshooting](#troubleshooting)
- [Technical appendix: production Docker reference](#technical-appendix-production-docker-reference)

## Terms used in this guide

| Term                    | Meaning                                                            |
| ----------------------- | ------------------------------------------------------------------ |
| VM / EC2 instance       | A rented server running the backend services                       |
| DNS record              | The mapping from an API hostname to the server address             |
| SSH                     | An encrypted remote terminal connection to that server             |
| HTTPS / TLS certificate | Encrypted web traffic and the certificate identifying its hostname |
| Reverse proxy           | Nginx receiving public requests and forwarding API work to Django  |
| Migration               | A versioned database structure or data update                      |
| Static files            | Packaged admin/API UI assets collected during release              |
| Media                   | Uploaded images that must survive ordinary releases                |

## What you are setting up

Your computer runs `scripts/deploy.sh`. It connects to the server over SSH, which
is an encrypted remote terminal connection. The server runs the backend services
in Docker containers: Django API, PostgreSQL database, Redis task broker, Celery
workers, a scheduler and Nginx. Nginx receives public HTTPS requests. Certbot obtains
and renews the HTTPS certificate.

The script installs the required server tools, downloads the backend, starts the
services and checks HTTPS. **You prepare the VM, DNS, credentials and application
settings first.** The script does not create cloud resources, configure provider
accounts, deploy the frontend, or configure backups and monitoring.

Commands are labeled by where to run them:

| Label                | Where to run it                                                                                                                                                |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Your computer**    | A Bash terminal on the computer you use to manage deployments. Windows users can use an Ubuntu WSL terminal with the files stored inside its Linux filesystem. |
| **Server**           | An SSH session logged in as `ubuntu`, after completing step 4.                                                                                                 |
| **Provider console** | Your browser, signed into AWS, your DNS provider, GitHub, or the relevant service account.                                                                     |

`api.example.com`, `shop.example.com`, `203.0.113.10`, and `/home/alex` are examples.
Replace them with real values. Do not paste example hostnames, addresses or secret
placeholders into a real deployment.

## 1. Prepare your computer and repository

You need access to the backend GitHub repository, the VM provider account and the
API domain's DNS settings. You also need the SMTP account for verification email
and, if enabling test payments, the project's Stripe test account. Arrange access
to the frontend hosting account with its maintainer.

**Your computer:** check the tools used by the deployment script:

```bash
bash --version
python3 --version
git --version
ssh -V
tar --version
curl --version
```

On Ubuntu, missing tools can be installed with:

```bash
sudo apt-get update
sudo apt-get install -y bash python3 git openssh-client tar curl
```

On macOS, use its Bash/OpenSSH/tar/curl and install missing Git or Python 3 through
your approved development-tool installer. A successful version command is enough;
you do not need Docker or Django installed on your computer for deployment.

Clone the backend repository, or use your existing clean `main` checkout:

```bash
git clone https://github.com/RHILIUS/E-Commerce-API.git backend
cd backend
```

A private repository requires your own GitHub access to clone it. This workstation
access is separate from the server deploy key created in step 5. If you already
have the repository, open a terminal in it and update your clean `main` checkout
using your normal Git procedure rather than cloning again.

Confirm these files are present on backend `main`:

```bash
ls scripts/deploy.sh scripts/deployment/remote.sh .env.deploy.example
```

The bootstrap implementation must be merged into `main` before deploying: the
server downloads `main`, not your uncommitted local changes or a feature branch.
For a first installation with no running server, keep the GitHub deployment
workflow disabled until step 11. For a replacement server, keep Actions pointing
to the existing server until the replacement passes acceptance checks.

Create a private folder **outside the repository** for operational files:

```bash
umask 077
OPS_DIR="$HOME/ecommerce-operations"
mkdir -p "$OPS_DIR"
chmod 700 "$OPS_DIR"
```

Keep this terminal open: later workstation commands use `OPS_DIR`. If you open a
new terminal, set `OPS_DIR` again, restore `VM_HOST` and `API_DOMAIN` from step 2
once you have chosen them, and return to the backend checkout.

**Checkpoint:** the tools work, the backend deployment files exist on `main`, and
your operations folder is outside Git.

## 2. Create the server

**AWS console:** open EC2 in the region selected for this deployment and choose
**Launch instance**. Choose the following settings; review current costs in your
account before launching.

| Setting          | What to choose                                                                                                                                                                  |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Operating system | Official Ubuntu Server **24.04 LTS** image. Other versions are rejected by the current script.                                                                                  |
| Architecture     | x86-64 or ARM64; the instance type must match the image.                                                                                                                        |
| Memory           | At least **2 GB RAM**. Increase resources if the complete stack or image builds need more capacity.                                                                             |
| Storage          | Enough for the OS, images, database and uploads, leaving at least **10 GiB free** on the user's home filesystem. Leave room for growth on the actual deployment filesystem too. |
| Login key        | Create/download a dedicated key pair and save the private file as `ecommerce-vm.pem`.                                                                                           |
| Network          | Public IPv4 connectivity with a route to the internet.                                                                                                                          |
| Security group   | Configure the inbound rules below. Record the group ID.                                                                                                                         |

The AWS [instance configuration reference](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-launch-parameters.html)
explains the launch controls. Other providers need equivalent settings.

Configure inbound rules:

| Purpose                                   | Protocol / port | Source                                                            |
| ----------------------------------------- | --------------- | ----------------------------------------------------------------- |
| Your administration connection            | TCP 22          | Your current public IPv4 address only, as a `/32` rule.           |
| HTTP certificate validation and redirects | TCP 80          | Public IPv4, `0.0.0.0/0`. Keep this available for future renewal. |
| Public backend HTTPS                      | TCP 443         | Public IPv4, `0.0.0.0/0`.                                         |

Do not expose PostgreSQL, Redis or the API's internal port. The host firewall,
network routing and outbound rules must also allow SSH, package/image downloads,
GitHub access, certificate requests and the configured external services.

Launch the instance and wait for its status checks. Record its region, instance
ID, security group ID and public IPv4 address. For a stable DNS destination, allocate
an Elastic IP in that region and associate it with the instance; use the resulting
address in the remaining steps. AWS [Elastic IP documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/elastic-ip-addresses-eip.html)
covers association and charges.

**Your computer:** copy the downloaded login key into the operations folder:

```bash
cp -n "$HOME/Downloads/ecommerce-vm.pem" "$OPS_DIR/vm-login.pem"
chmod 600 "$OPS_DIR/vm-login.pem"
VM_HOST=203.0.113.10
API_DOMAIN=api.example.com
```

Replace the two example values above. Use these same values throughout this guide.
If a command asks to overwrite an existing private key, stop and check which
deployment owns it rather than replacing it.

**Checkpoint:** the server is running, you have its private login key, and ports
22/80/443 have the intended rules. No containers need to be started manually.

## 3. Point your API domain at the server

**DNS provider console:** create an `A` record for your chosen API hostname,
pointing to the server's public IPv4 address. For `api.example.com`, the record's
name is usually `api` within the `example.com` zone.

This guide uses a direct, DNS-only record. If your DNS provider also offers an HTTP
proxy, leave that proxy disabled for this setup. Do not publish an `AAAA` record
unless IPv6 routing and HTTP validation are deliberately configured; the script
publishes the service on IPv4.

**Your computer:** check what your computer currently resolves:

```bash
python3 -c 'import socket,sys; print(socket.gethostbyname_ex(sys.argv[1])[2])' "$API_DOMAIN"
```

Wait until the result includes the intended server address. Also inspect the
provider's records for stale `AAAA` entries; the command above only checks IPv4.
Keep DNS pointed at this server while requesting and renewing its certificate.

**Checkpoint:** the API hostname resolves to the intended VM. HTTPS will not work
until the first deployment installs a certificate.

## 4. Verify the server and test SSH access

An SSH **login key** proves that you may log in. A **server host key** proves that
you reached the correct server. They are different keys.

First obtain the server's Ed25519 host-key fingerprint through an independently
trusted source. On EC2, check the instance's system log through the AWS console
for its SSH host-key fingerprints. If unavailable, use an approved provider console
or already verified administrator session to run:

**Trusted server console:**

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

**Your computer:** collect the presented host key and display its fingerprint:

```bash
ssh-keyscan -T 10 -t ed25519 "$VM_HOST" > "$OPS_DIR/vm-known-hosts.candidate"
ssh-keygen -lf "$OPS_DIR/vm-known-hosts.candidate"
```

Compare the `SHA256:...` fingerprint with the trusted console result. A scanned key
is only a candidate until that comparison succeeds. If the results differ, or you
cannot obtain a trusted fingerprint, stop and resolve server identity first.
After they match:

```bash
mv "$OPS_DIR/vm-known-hosts.candidate" "$OPS_DIR/vm-known-hosts"
ssh -i "$OPS_DIR/vm-login.pem" \
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$OPS_DIR/vm-known-hosts" "ubuntu@$VM_HOST"
```

**Server:** confirm the login and prerequisites:

```bash
whoami
cat /etc/os-release
sudo -n true
free -h
df -h "$HOME"
```

Expect user `ubuntu`, Ubuntu 24.04, sufficient memory/free space, and a successful
`sudo -n true` with no password prompt. The script needs a non-root user with
passwordless sudo. If using a custom VM image, have its administrator prepare
that access before continuing. Docker will grant this user administrative control
of the host when bootstrap adds it to the Docker group.

Return to your computer:

```bash
exit
```

**Checkpoint:** you can log in with host verification, and the deployment user can
run sudo without interaction.

## 5. Give the server access to the backend repository

Create a second key, used only for **server → GitHub repository** access. Do not
reuse the VM login key.

**Your computer:**

```bash
ssh-keygen -t ed25519 -C ecommerce-repository-readonly \
  -f "$OPS_DIR/repository-key" -N ''
chmod 600 "$OPS_DIR/repository-key"
cat "$OPS_DIR/repository-key.pub"
```

**GitHub:** open the backend repository → **Settings → Deploy keys → Add deploy
key**. Give it a descriptive title, paste the `.pub` contents, and leave **Allow
write access** unchecked. Add the key. See GitHub's
[deploy-key instructions](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys).

Now prepare GitHub's host identity. Open the official
[GitHub SSH fingerprints page](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints)
and copy its current full `github.com ssh-ed25519 ...` known-hosts entry into a new
file named `github-known-hosts` in your operations folder. For example, open it
with your text editor:

```bash
nano "$OPS_DIR/github-known-hosts"
```

Paste the single entry, save, then verify its fingerprint against that official page:

```bash
ssh-keygen -lf "$OPS_DIR/github-known-hosts"
GIT_SSH_COMMAND="ssh -i $OPS_DIR/repository-key -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$OPS_DIR/github-known-hosts" \
  git ls-remote git@github.com:RHILIUS/E-Commerce-API.git refs/heads/main
```

Expect a commit identifier followed by `refs/heads/main`. If access is denied,
check the deploy key on this specific repository before continuing. Bootstrap will
copy the private repository key and verified GitHub host entry onto the VM; your
VM login private key stays on your computer.

**Checkpoint:** the dedicated read-only key can read backend `main` without asking
for a password or accepting an unknown host.

## 6. Prepare the application settings

There are two configuration files with different jobs:

| File                                            | Purpose                                                         | Where it is used                                                |
| ----------------------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------- |
| `backend.env` in your private operations folder | Django, database, email, payments and public-listener settings. | Bootstrap installs this as the server checkout's single `.env`. |
| `.env.deploy` in your local backend checkout    | Server address and paths to deployment credentials/files.       | Read on your computer by `scripts/deploy.sh`.                   |

**Your computer, backend checkout:** copy the application template once. The `-n` option preserves an existing file:

```bash
cp -n .env.example "$OPS_DIR/backend.env"
chmod 600 "$OPS_DIR/backend.env"
```

If that destination already contains the settings for a deployed system, edit the
existing file instead of overwriting it. Open `backend.env` in a text editor. Replace
values already in the template; do not append duplicate keys.

Generate the Django secret and a separate database password on your computer:

```bash
python3 -c 'import secrets; print(secrets.token_hex(32))'
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Put the first result in `SECRET_KEY` and the second in `DB_PASSWORD`. Treat the
results as secrets and store them in the team's approved password manager.

Set these values, using your actual API and frontend addresses:

```dotenv
SECRET_KEY=PASTE_THE_FIRST_GENERATED_VALUE
DEBUG=False
ALLOWED_HOSTS=api.example.com,localhost,127.0.0.1
DB_NAME=ecommerce
DB_USER=ecommerce
DB_PASSWORD=PASTE_THE_SECOND_GENERATED_VALUE
DB_HOST=localhost
DB_PORT=5432
CELERY_BROKER_URL=redis://redis:6379/0
CORS_ALLOWED_ORIGINS=https://shop.example.com
CSRF_TRUSTED_ORIGINS=https://api.example.com,https://shop.example.com
STRIPE_FRONTEND_ORIGIN=https://shop.example.com
PROD_BIND_ADDRESS=0.0.0.0
PROD_HTTP_PORT=80
PROD_HTTPS_PORT=443
PUBLIC_HTTPS_ORIGIN=https://api.example.com
ALLOW_DEV_RESET=False
USE_AZURE_STORAGE=False
```

Use the real assigned `https://....pages.dev` frontend origin if that is where the
frontend runs. Origins include `https://` and no trailing path; `ALLOWED_HOSTS`
contains hostnames only. The Compose stack creates PostgreSQL using `DB_NAME`,
`DB_USER` and `DB_PASSWORD`; you do not create the database manually. Compose replaces
`DB_HOST`/`DB_PORT` inside containers with its private database address.

Complete the remaining template settings as follows:

| Settings                                                                                               | What to supply                                                                                                                                |
| ------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `EMAIL_BACKEND`                                                                                        | `django.core.mail.backends.smtp.EmailBackend` for delivered verification emails. The template's console backend only prints messages.         |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_TIMEOUT` | The approved SMTP account's actual connection settings, such as the existing Brevo integration. Obtain these from the account owner/provider. |
| `DEFAULT_FROM_EMAIL`                                                                                   | A sender address authorized by that SMTP provider.                                                                                            |
| `STRIPE_SECRET_KEY`                                                                                    | The project's Stripe **test** secret key when enabling online test payments.                                                                  |
| `STRIPE_WEBHOOK_SECRET`                                                                                | The signing secret for this server's registered test webhook endpoint; see below.                                                             |
| `USE_AZURE_STORAGE` and `AZURE_*`                                                                      | Leave filesystem storage selected for this guide. Azure is optional and requires its own account, container and credentials.                  |
| `TRUSTED_PROXY_COUNT`                                                                                  | Keep the template value. Production settings configure the existing private Nginx boundary; do not guess a larger proxy count.                |
| `PGADMIN_*`                                                                                            | Development-only settings; pgAdmin is not part of production Compose.                                                                         |
| Other template settings                                                                                | Retain current defaults unless the feature documentation requires a deliberate change.                                                        |

For Stripe test payments, register
`https://api.example.com/api/shop/stripe/webhook/` in the project's test account,
selecting `checkout.session.completed`, `checkout.session.expired`,
`checkout.session.async_payment_succeeded`, and `checkout.session.async_payment_failed`.
Save that endpoint's signing secret in `STRIPE_WEBHOOK_SECRET`; test delivery once
HTTPS is running. Follow the [implemented Stripe contract](checkout/stripe-api.md)
for account/payment-method requirements. Blank Stripe credentials leave online
payment integration incomplete; successful bootstrap does not test payment delivery.

**Checkpoint:** the application file has unique secrets, exact production origins,
real SMTP settings, and the credentials needed for the integrations you intend to use.

## 7. Prepare the deployment settings

**Your computer, backend checkout:** create the local configuration without
overwriting an existing one:

```bash
cp -n .env.deploy.example .env.deploy
chmod 600 .env.deploy
```

Edit `.env.deploy` and fill every required value. The following is a complete
example for a computer whose operations folder is `/home/alex/ecommerce-operations`:

```dotenv
DEPLOY_HOST=203.0.113.10
DEPLOY_USER=ubuntu
SSH_KEY_FILE=/home/alex/ecommerce-operations/vm-login.pem
SSH_KNOWN_HOSTS_FILE=/home/alex/ecommerce-operations/vm-known-hosts
DEPLOY_PATH=/home/ubuntu/ecommerce/backend
API_DOMAIN=api.example.com
REPOSITORY=git@github.com:RHILIUS/E-Commerce-API.git
CERTIFICATE_EMAIL=maintainer@example.com
APP_ENV_FILE=/home/alex/ecommerce-operations/backend.env
GIT_KEY_FILE=/home/alex/ecommerce-operations/repository-key
GIT_KNOWN_HOSTS_FILE=/home/alex/ecommerce-operations/github-known-hosts
ACTIONS_PUBLIC_KEY_FILE=
```

Use `printf '%s\n' "$OPS_DIR"` to find your own absolute operations-folder path.
Replace `/home/alex`, the VM address, domain and contact email. `CERTIFICATE_EMAIL`
is an actively monitored address used when requesting the certificate.

The file contains literal values: **do not put `$OPS_DIR`, `$HOME`, shell commands
or inline comments in its values**. Relative file paths are resolved from the
`.env.deploy` file's directory. File paths cannot contain spaces or quote characters;
`DEPLOY_PATH` must be an absolute path without symlinked directories. SSH uses port 22. Keep the default server path if you want to use the commands in this guide.

Leave `ACTIONS_PUBLIC_KEY_FILE` empty for now. It can reference a separate public
key if preparing Actions before bootstrap; step 11 also explains authorization
after bootstrap. `.env.deploy` is Git-ignored, and the real application file and
private keys belong outside the repository.

**Checkpoint:** all paths name existing files and the domain/server values match
steps 2–4. The application and both private key files have mode 600.

## 8. Check the prerequisites

**Your computer, backend checkout:**

```bash
bash scripts/deploy.sh check --env-file .env.deploy
```

Expect the message stating that Ubuntu, architecture, sudo, memory, disk space and
DNS lookup checks passed. This command makes no package or application changes.
It verifies your VM login key/host entry, but does not yet test repository credentials,
application settings, inbound public ports or certificate issuance. Step 5 tests Git
separately; the remaining checks happen during deployment and acceptance.

If the check fails, use the [troubleshooting table](#troubleshooting). Resolve the
reported problem before running bootstrap.

## 9. Run the first deployment

**Your computer, backend checkout:**

```bash
bash scripts/deploy.sh bootstrap --env-file .env.deploy
```

Leave the terminal open while the command works. It will:

1. Install server tools and Docker, then prepare the deployment user's access.
2. Clone backend `main` and install your application settings and Git credentials.
3. Build the application image and validate its core production configuration.
4. Obtain the HTTPS certificate using public HTTP validation, and schedule renewal.
5. Start PostgreSQL and Redis, apply database migrations and collect static assets.
6. Start the API, both workers, the scheduler and Nginx.
7. Check the database connection, Nginx configuration, local HTTPS and public HTTPS.

Expect `Bootstrap complete` from the server and
`Public HTTPS schema request passed` from your computer. If the public check fails
after the server reports completion, inspect the running services and DNS/firewall;
do not assume the server setup was reversed.

Bootstrap does not overwrite different existing secrets/credentials and refuses to
adopt an existing production stack. A completed bootstrap rerun reports status;
it does not deploy a newer commit. A fresh database starts empty: the command does
not create an administrator, import old data or seed demo accounts.

If a stage fails, the failure message names the stage. Inspect it before retrying
with the same inputs. Migrations and other completed changes can remain applied;
there is no automatic rollback. Do not run a database reset or delete Docker volumes
to recover. See [troubleshooting](#troubleshooting).

## 10. Verify the application and certificate renewal

### Check services and HTTPS

**Your computer, backend checkout:**

```bash
bash scripts/deploy.sh status --env-file .env.deploy
curl --fail --silent --show-error "https://$API_DOMAIN/api/schema/" -o /dev/null
```

Status prints the current Git commit, containers, certificate dates and renewal-timer
activity. Expect `api`, `worker`, `email-worker`, `beat`, `db`, `redis` and `nginx` to
be running; database/Redis/API health checks should be healthy. The one-off `migrate`
and `collectstatic` jobs may show `Exited (0)`, which is successful completion.
The renewal timer should report `active`. Open `https://api.example.com/api/docs/`
and confirm that the browser trusts the certificate.

### Create an administrator if needed

Log in using the verified SSH command from step 4.

**Server:**

```bash
cd /home/ubuntu/ecommerce/backend
docker compose -f compose.prod.yaml exec api python manage.py createsuperuser
docker compose -f compose.prod.yaml exec api python manage.py check --deploy
```

Follow the interactive account prompts and store its credentials securely. Review
Django's deployment-check findings; successful container startup does not resolve
all operational settings automatically. Open `https://api.example.com/admin/` and
test the administrator login. A new empty catalog is expected until products are
created through the application's normal administration flows.

### Connect and test the frontend

Ask the frontend maintainer to follow the
[Cloudflare Pages deployment guide](../../frontend/docs/deployment/cloudflare-pages.md).
For the existing setup, the frontend uses `VITE_API_URL=/` and its production Pages
Function must have `API_ORIGIN=https://api.example.com`. The backend's configured
frontend origins must match that deployed frontend. Frontend deployment is separate
from this script; do not put backend secrets in frontend environment variables.

From the deployed frontend, verify registration and delivered verification email,
login, refresh/logout, catalog/media, and the checkout features in scope. If using
Stripe, complete a test payment and inspect its webhook result and application
status. Container-running state alone does not prove email or payment processing.

### Test renewal and reboot recovery

**Server, on the disposable acceptance VM:**

```bash
sudo certbot renew --cert-name ecommerce-backend --dry-run --run-deploy-hooks
systemctl is-active certbot.timer
```

Expect a successful renewal simulation and an active timer. The deploy hook copies
the active certificate files and reloads running Nginx; this test exercises that
hook too. Keep public port 80 available for future validation. See
[Certbot renewal documentation](https://eff-certbot.readthedocs.io/en/stable/using.html#renewing-certificates).

To test restart behavior, schedule a maintenance window, then run `sudo reboot`
on the disposable VM. The SSH connection will end. Once the VM is reachable again,
repeat the workstation status/HTTPS checks and the application checks above. Keep
those results in the deployment handover. Do not reboot a shared live server as an
unannounced test.

**Checkpoint:** record the deployed commit, public HTTPS result, business-flow
checks, renewal test and reboot result. Before storing valuable data, arrange an
off-server backup/restore procedure and monitoring with named owners; this tool
provides neither. Docker volumes persist across container recreation but do not
protect against server/disk loss.

## 11. Enable automatic deployments with GitHub Actions

This section is for **EC2**. The existing Actions workflow changes EC2 security-group
rules through AWS, so it cannot target another provider unchanged. On another
provider, use the manual release procedure below. Provider-specific CI access needs
separate implementation.

### Create the Actions login key

Use a third key for **GitHub Actions → VM**. It must differ from both your personal
VM login key and the server's repository key.

**Your computer:**

```bash
ssh-keygen -t ed25519 -C github-actions-ecommerce-deploy \
  -f "$OPS_DIR/actions-login" -N ''
chmod 600 "$OPS_DIR/actions-login"
cat "$OPS_DIR/actions-login.pub"
```

Copy only the `.pub` line. Log into the VM using your verified SSH connection.

**Server:** open `~/.ssh/authorized_keys` with an editor and append a new line
consisting of `restrict ` followed by the copied public key:

```text
restrict ssh-ed25519 PUBLIC_KEY_FROM_actions-login.pub github-actions-ecommerce-deploy
```

Use the actual public key, preserve existing entries, then check permissions:

```bash
chmod 700 ~/.ssh
chmod 600 ~/.ssh/authorized_keys
```

The private `actions-login` file stays off the server. If you supplied this public
key through `ACTIONS_PUBLIC_KEY_FILE` before bootstrap, the script already added
the restricted entry; do not add it twice.

### Configure AWS and GitHub

Follow [AWS OIDC and temporary SSH access](github-actions-deployment.md#aws-oidc-and-temporary-ssh-access)
to create the GitHub OIDC identity provider, role trust policy and permission to
modify this instance's security group. Replace the account, repository, region and
group placeholders in those policies. The role ARN from that step is used below.
This grants Actions temporary AWS credentials; it does not require saved AWS
access-key secrets.

**GitHub backend repository:** open **Settings → Environments**, create/select
`production`, restrict deployment branches to `main`, and add:

| Environment variable    | Value from this deployment                                |
| ----------------------- | --------------------------------------------------------- |
| `EC2_HOST`              | The same address as `DEPLOY_HOST`.                        |
| `EC2_USER`              | `ubuntu`, matching `DEPLOY_USER`.                         |
| `EC2_DEPLOY_PATH`       | `/home/ubuntu/ecommerce/backend`, matching `DEPLOY_PATH`. |
| `AWS_REGION`            | The instance/security-group region.                       |
| `AWS_ROLE_ARN`          | The deployment role ARN created above.                    |
| `EC2_SECURITY_GROUP_ID` | The instance's `sg-...` security-group ID.                |

Add these **environment secrets**, preserving the file contents/newlines:

| Environment secret    | What to paste                                                                                                                          |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `EC2_SSH_PRIVATE_KEY` | Complete contents of the private `actions-login` file, including its BEGIN/END lines. Paste through the approved secret-entry process. |
| `EC2_SSH_KNOWN_HOSTS` | Contents of the verified `vm-known-hosts` file. Its hostname/address must match `EC2_HOST`.                                            |

Use `HOST ssh-ed25519 KEY` for a plain port-22 host entry, with no literal square
brackets around `HOST`. Keep the workstation's SSH `/32` rule. The workflow creates
and later removes its own temporary runner rule; it does not need permanent public
SSH access. Decide with the owner whether production environment reviewers are
required; review requirements affect whether deployments need approval.

### Run the first automated release

1. Confirm step 10 passed and the production environment values/secrets are saved.
2. Enable **EC2 Auto Deploy on origin main changes** in the repository's Actions tab
   if it was disabled. Choose **Run workflow**, with branch `main`.
3. Read the run's jobs, including temporary SSH-rule cleanup. Expect a successful
   release and removal of the runner rule. If cleanup fails, follow the workflow's
   instruction to remove the reported rule ID.
4. Repeat the status, HTTPS and application checks from step 10.
5. Configure the existing **Backend checks** as a required check on `main` through
   repository protection settings, according to the team's merge policy.

Later pushes/merges to `main`, including documentation-only changes, trigger this
workflow. Releases briefly stop application traffic while migrations and restarts
run. Database/Redis remain running. This is a single-server deployment with downtime,
not high availability. See the [workflow reference](github-actions-deployment.md)
for concurrency, failure and recovery details.

## Deploy future updates

For EC2, merge reviewed/tested changes into `main`, watch the Actions release,
and repeat the relevant acceptance checks. Use a fresh manual Actions run on `main`
to retry a resolved failure. An old superseded run can skip instead of deploying.

The workstation also exposes:

```bash
bash scripts/deploy.sh update --env-file .env.deploy
```

**Current limitation:** this wrapper looks up the newest remote SHA, then reads its
release script from the server's local Git objects before the release script fetches.
A newly pushed commit that is not already available locally can therefore fail at
`git show`. Prefer the existing Actions workflow for EC2 releases. This guide does
not claim that the wrapper currently handles every new-commit update. Do not use
`git pull`, delete the checkout, or reset data to work around that error.

### Manual release when Actions is unavailable

For another VM provider, or a reviewed manual release on EC2, stream the release
script from a freshly fetched commit on **your computer**. This avoids the wrapper's
missing-object limitation and uses the same server release lock/order as Actions.
Agree a deployment window and ensure no other operator is changing the stack.

**Your computer, backend checkout:** restore `OPS_DIR` and `VM_HOST` from the earlier
steps if using a new terminal. Then run:

```bash
git fetch origin +refs/heads/main:refs/remotes/origin/main
RELEASE_SHA=$(git rev-parse refs/remotes/origin/main)
```

Only continue if fetch succeeded. Run the following block, using the same verified
login key, host record, user and deployment path as bootstrap:

```bash
set -o pipefail
git show "$RELEASE_SHA:scripts/deploy-production.sh" | \
  ssh -T -i "$OPS_DIR/vm-login.pem" \
    -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$OPS_DIR/vm-known-hosts" \
    -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
    "ubuntu@$VM_HOST" \
    "timeout --signal=TERM --kill-after=30s 25m bash -s -- '$RELEASE_SHA' '/home/ubuntu/ecommerce/backend'"
```

Expect `Deployed <commit>` and then repeat step 10's status, public HTTPS and
application checks. If the script reports `Skipping superseded commit`, a newer
main commit exists: fetch/select the latest revision again before trying another
release. If SSH or a stage fails, inspect the server first; do not assume a lost
connection cancelled the remote operation. This command does not upload application
settings or configure GitHub/AWS resources.

Updates preserve `.env`, certificates and persistent volumes. Database migrations
can still change data/schema and cannot be reversed by simply selecting older code.
Review migration and backup requirements before each release.

## Routine maintenance and changing settings

### Inspect the running system

**Your computer:** use `status` as in step 10. For detailed diagnosis, connect by SSH.

**Server:**

```bash
cd /home/ubuntu/ecommerce/backend
docker compose -f compose.prod.yaml ps -a
docker compose -f compose.prod.yaml logs --tail=100 api worker email-worker beat nginx
git rev-parse HEAD
systemctl status certbot.timer --no-pager
sudo journalctl -u certbot.service -n 50 --no-pager
df -h
```

Logs may contain customer or operational data. Review/redact them before sharing.
Do not print `.env`, private keys or resolved Compose configuration into a ticket.
Arrange monitoring for disk usage, failed containers/tasks, certificate expiry and
backup success; the script does not install an alerting system.

### Change application credentials or settings

Bootstrap deliberately refuses to replace a different server `.env`. For a settings
change, schedule a maintenance window and coordinate with anyone running releases.
Edit the server checkout's `.env` securely, keep its permissions at 600, and update
the private workstation copy/password-manager record too. Then use a normal Actions
release to recreate API/workers/Beat with those values. `docker compose restart`
does not load a changed environment file, and `update` does not upload your local
`backend.env`.

Changing `DB_PASSWORD` in `.env` does **not** change the password inside an existing
PostgreSQL volume. Database credential rotation needs coordinated database/application
changes. Replacing `SECRET_KEY` can invalidate signed sessions/tokens. Hostname,
certificate ownership, Git keys or infrastructure changes also need a reviewed plan;
do not rerun bootstrap expecting it to migrate those identities automatically.

### Understand certificate renewal

Certbot stores the managed certificate under
`/etc/letsencrypt/live/ecommerce-backend`. Its timer renews through the HTTP challenge
path. A root-owned hook copies renewed files into the checkout's `deploy/certs`
and tests/reloads running Nginx. Bootstrap, releases, resets and certificate activation
share a deployment lock when using the same account, checkout and `/tmp`.

If renewal succeeded but activation failed, inspect/fix the reported problem first.
Then, **on the server**, rerun the activation hook:

```bash
sudo env RENEWED_LINEAGE=/etc/letsencrypt/live/ecommerce-backend \
  /etc/letsencrypt/renewal-hooks/deploy/ecommerce-backend
```

The hook waits up to five minutes for a deployment/reset lock. A timeout can leave
the renewed certificate stored by Certbot while Nginx still uses the old copy.
Ordinary `certbot renew` may do nothing because renewal is already complete; the
activation command above handles that case. Check public HTTPS afterward.

## Troubleshooting

Start by recording the command, time, reported stage and deployed commit. Inspect
service status and relevant logs before repeating a command. A lost SSH connection
does not prove the remote command stopped. Completed package installs, certificates,
migrations and static collection can persist after a later failure.

| Symptom                                                        | What to check and what to do next                                                                                                                                                                                  |
| -------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| SSH times out                                                  | Verify VM address/status, your current public IP's port-22 rule, public routing and host firewall. An IP change on your computer may require updating its `/32` rule.                                              |
| Host key missing/changed                                       | Repeat the trusted fingerprint comparison in step 4. For a replacement VM, verify its new identity before updating the local/GitHub records. Do not disable host checking.                                         |
| `Permission denied (publickey)` for VM login                   | Check `DEPLOY_USER`, the selected VM key pair, local key path and mode 600. The repository key cannot log into the VM.                                                                                             |
| `sudo` needs a password                                        | Ask the VM administrator to prepare the non-root account for unattended sudo. Do not change the configured user to root.                                                                                           |
| Unsupported OS, insufficient memory/space                      | Use Ubuntu 24.04 and meet step 2's requirements. Inspect disk growth before allocating more space or removing anything.                                                                                            |
| Repository access denied                                       | Repeat step 5's `git ls-remote`. Check the backend repository's read-only deploy key and verified GitHub host entry.                                                                                               |
| Existing env/key differs                                       | Compare the intended configuration privately. Bootstrap preserves the server files. Use the reviewed settings/credential-change procedure rather than deleting them to force a rerun.                              |
| Existing production stack cannot be adopted                    | Use that installation's existing release procedure. Bootstrap is for a new stack or a partial run it owns.                                                                                                         |
| Certificate validation fails                                   | Check A/AAAA records, direct DNS routing and public port 80. Fix the cause before requesting again; repeated requests can hit CA rate limits.                                                                      |
| Certificate exists but is invalid or expires within seven days | An incomplete bootstrap will stop. Inspect Certbot's certificate/renewal state and repair renewal before resuming with the same domain.                                                                            |
| Build fails                                                    | Inspect the build error and resources. Bootstrap/release build failures happen before application shutdown. Fix the build before retrying.                                                                         |
| Migration/static/startup failure                               | Traffic may be intentionally stopped, and migrations may already have committed. Inspect logs, resolve the failing stage, and review replay safety before rerunning the same bootstrap or a fresh Actions release. |
| Deployment/reset lock busy                                     | Check which operation is running. Wait for its outcome before retrying; do not remove a lock file to bypass it.                                                                                                    |
| Bootstrap complete but public HTTPS fails                      | Check the exact domain, firewall/ports and running Nginx. The server can be initialized even if the final workstation request fails.                                                                               |
| Email never arrives                                            | Confirm SMTP backend/credentials/authorized sender and `email-worker` processing. Successful API startup does not prove delivery.                                                                                  |
| Actions update fails                                           | Read the workflow's failing step, verify production variables/secrets/OIDC, and inspect the VM before a fresh run. Check temporary-rule cleanup even if SSH failed.                                                |
| Workstation `update` reports a missing Git object              | See the documented wrapper limitation above; prefer Actions on EC2.                                                                                                                                                |
| Reboot leaves services unavailable                             | Check Docker service startup, container state, disk space and logs. Inspect one-off jobs/dependencies before starting services manually.                                                                           |

After an interrupted bootstrap, rerun `bootstrap` only after inspecting the server,
with the same configuration and checkout. It does not automatically advance a
partial checkout to a new commit. Handled exits clean temporary SSH payloads and
challenge containers; a forced process/host failure may leave them behind for
operator review. Do not delete arbitrary temporary files or containers blindly.

Never use `docker compose down -v`, `reset-demo.sh`, or a schema reset as deployment
recovery. There is no automatic data rollback or restore in this tool. If the VM is
lost, bootstrap alone creates a new empty stack; recovering business data requires
your tested backup/restore procedure and a controlled cutover.

For related procedures, see [disposable demo reset](reset-dev.md#one-command-disposable-demo-reset),
[GitHub Actions deployment](github-actions-deployment.md), and the
[system deployment architecture](../../docs/architecture/production-deployment.md).

## Technical appendix: production Docker reference

This appendix describes the existing container setup for maintainers who need to
inspect or troubleshoot it. Use the numbered deployment steps above for a new
server and the release procedure for updates. The source of truth is
[`Dockerfile.prod`](../Dockerfile.prod), [`compose.prod.yaml`](../compose.prod.yaml),
[`settings_production.py`](../ecommerce_backend/settings_production.py), and the
[Nginx template](../deploy/nginx.conf.template).

### Runtime image and workers

The runtime uses Python 3.12 on Debian Bookworm and the pinned dependencies in
`requirements.txt`, including Gunicorn 26.2.0. Application processes run as
UID/GID **10001**. The image copies Django runtime source; environment files,
development tools, uploaded media and demo fixtures are excluded from the ordinary
production image. Building the image does not initialize a database, apply
migrations, send email or call Stripe.

Gunicorn starts with one synchronous worker on private container port 8000.
`GUNICORN_CMD_ARGS` allows deliberate runtime tuning. Both Celery workers use
concurrency one; `email-worker` consumes `registration-email`, while the ordinary
worker handles the default queue. Run exactly one Beat scheduler. Measure the
complete stack's resource usage before increasing worker counts. API access
logging is not enabled by default; review sensitive-data exposure before enabling it.

For image-only inspection, run these commands in an isolated backend checkout or
an agreed maintenance environment. Building the production tag replaces the local
image reference used by later container creation; it does not restart running
containers.

```bash
docker build -f Dockerfile.prod -t ecommerce-backend-prod:local .
docker run --rm ecommerce-backend-prod:local id
docker run --rm ecommerce-backend-prod:local gunicorn --version
docker run --rm ecommerce-backend-prod:local python -m pip check
```

The separate `maintenance` build target includes offline taxonomy and seed images.
It is selected only by the maintenance service/profile. See
[production demo reset](reset-dev.md#one-command-disposable-demo-reset) for its
controlled use; ordinary deployment does not reset or seed data.

### Compose services and startup dependencies

The production project is named `ecommerce-backend-prod`, separate from
`ecommerce-backend-dev`. It has its own PostgreSQL, Redis, media and static volumes.
Production Compose does not include pgAdmin or a frontend container.

| Service                          | Responsibility and startup requirement                                                                            |
| -------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `db`                             | PostgreSQL 17 with a persistent volume and readiness check.                                                       |
| `redis`                          | Redis 7 with append-only persistence and a readiness check; dedicated broker database 0.                          |
| `migrate`                        | One-off migrations using the application image; waits for healthy PostgreSQL.                                     |
| `collectstatic`                  | One-off static collection into the shared static volume.                                                          |
| `api`                            | Gunicorn API; requires successful migrations and healthy Redis. Its health check verifies the listening socket.   |
| `worker`, `email-worker`, `beat` | Background consumers/scheduler using the same application image; require successful migrations and healthy Redis. |
| `nginx`                          | Public reverse proxy and static/media serving; requires healthy API and successful static collection.             |
| `maintenance`                    | Optional maintenance-profile job, defaulting to a reset dry run.                                                  |

Failed one-off jobs block dependent startup. The release/bootstrap scripts explicitly
order migration, static collection and application recreation; use those scripts
rather than treating `restart` as a release. An API socket health check does not
prove database access, queue consumption or email/payment delivery. Docker restart
policies restart exited services, not containers merely marked unhealthy.

Compose bounds JSON logs to three files of 10 MB each per configured service.
Named volumes survive ordinary container removal; they do not provide off-host
recovery. Changing `.env` does not update credentials inside an existing PostgreSQL
volume.

### Environment, ports and proxy behavior

All backend containers use the checkout's single `.env`. Compose selects
`ecommerce_backend.settings_production`, forces internal DB addressing to `db:5432`
and the broker to `redis://redis:6379/0`. Production settings force `DEBUG=False`,
secure session/CSRF cookies, static collection, and trust the HTTPS header replaced
by private Nginx. `ALLOW_DEV_RESET` remains an explicit opt-in, defaulting to false.
Development settings and volumes remain separate.

Only Nginx publishes host ports. Without explicit public-host configuration,
Compose defaults to `127.0.0.1:8080` for HTTP and `127.0.0.1:8443` for HTTPS,
with `PUBLIC_HTTPS_ORIGIN=https://localhost:8443`. The main deployment steps change
these to public 80/443 with a real HTTPS origin. These are backend ports, not
frontend hosting. Gunicorn, PostgreSQL and Redis have no published host ports.

Nginx serves `/api/`, `/admin/`, `/static/` and filesystem `/media/`. Other paths
return 404; SPA routes belong to the separately hosted frontend. API documentation
is at `/api/docs/`. The request-body limit is 10 MB and the API proxy read timeout
is 60 seconds. Change these only for a verified requirement. In Azure mode, media
URLs use the configured external storage rather than the filesystem route.

The TLS chain and key are mounted from `deploy/certs/fullchain.pem` and
`deploy/certs/privkey.pem`; they are ignored by Git and excluded from builds.
The bootstrap tool installs and renews them as described above. Nginx reads the
ACME challenge folder through the read-only `deploy/acme` mount; its HTTP listener
serves that challenge path and redirects other requests to `PUBLIC_HTTPS_ORIGIN`.
`PUBLIC_HTTPS_ORIGIN` controls backend redirects; `STRIPE_FRONTEND_ORIGIN` separately
controls payment return URLs.

Nginx forwards the requested Host and replaces the forwarded client-address/HTTPS
headers before passing requests to the private API. Do not expose Gunicorn directly or guess a different proxy trust count.
HSTS is not enabled by the production override; review it only after the domain
and HTTPS behavior are established. PostgreSQL/Redis/Nginx image tags can change
without a source diff; record and deliberately pin tested digests when required
by the deployment's release policy.

### Low-level inspection and shutdown

Run on the server from the backend checkout. `config --quiet` validates without
printing resolved credentials:

```bash
docker compose -f compose.prod.yaml config --quiet
docker compose -f compose.prod.yaml ps -a
docker compose -f compose.prod.yaml logs --tail=100 api nginx migrate collectstatic worker email-worker beat
docker compose -f compose.prod.yaml exec nginx nginx -t
docker compose -f compose.prod.yaml exec api python manage.py check --deploy
```

To deliberately stop and remove this stack's containers during an agreed shutdown:

```bash
docker compose -f compose.prod.yaml down
```

This interrupts service and preserves named volumes. Never add `-v` for normal
operation or deployment recovery. Manual Compose commands bypass the scripts'
deployment/reset lock; coordinate them with other operators. To restart the same already configured stack, with its existing application image,
`.env` and certificates available, run:

```bash
docker compose -f compose.prod.yaml up -d --wait --wait-timeout 180
```

Check service/job status, HTTPS and application behavior afterward. For code updates,
use the release procedure so migration, static collection and consumer recreation
use the intended image and revision.
