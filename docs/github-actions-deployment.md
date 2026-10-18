# EC2 Auto Deploy on origin main changes

## Scope and release behavior

The [deployment workflow](../.github/workflows/deploy-production.yml) deploys
pushes to `main`, including merged PRs, to the existing production Compose stack.
It can also be run manually from Actions with **main** selected. Other branches
cannot run the deployment job.

This is a single-server release with a planned outage. For initial server setup
and TLS certificate setup/renewal, follow the
[backend deployment guide](deployment-guide.md). Those steps remain outside this
Actions workflow; backup setup and infrastructure upgrades require separate work.
The frontend is deployed separately. The workflow does not reset demo data, flush
Redis, remove volumes, copy `.env`, or run tests against production.

The [release script](../scripts/deploy-production.sh) is streamed from the triggering
commit over SSH, so even the first deployment can update an existing checkout that
does not yet contain it. It:

1. Acquires the same host lock as `reset-demo.sh`, before fetching or checking out
   code. Both operations must use the same user, checkout and `TMPDIR` (normally
   `/tmp`). Manual Docker commands do not participate in this lock.
2. Refuses tracked edits and untracked non-ignored files, without deleting them.
   Ignored `.env`, certificates and uploads remain in place.
3. Fetches `origin/main` without interactive Git authentication and checks out the
   exact triggering SHA in detached HEAD state. If that SHA is no longer the latest
   `origin/main` at fetch time, it skips without touching Docker. This prevents an
   older queued run from overwriting a newer deployment. A newer push during an
   active release is handled by a subsequent run.
4. Validates Compose, requires the existing database and Redis containers to be
   healthy, refuses running release jobs, then builds the API image while the
   existing containers continue serving. A preflight/build failure does not stop
   services, although the checkout already points at the attempted release.
5. Stops Nginx first, then API, both workers and Beat, allowing up to 120 seconds
   per stop command for graceful shutdown. Requests can be interrupted and tasks
   exceeding the grace period can be terminated; this is not a guaranteed task
   drain. PostgreSQL and Redis remain running.
6. Runs migrations and static collection in temporary containers using the new
   image. `migrate` runs every release and skips already applied migrations.
7. Recreates API, both workers and Beat, waits for API socket health and running
   containers, then verifies a database connection through Django. It recreates
   Nginx last (refreshing its resolved API address and template/environment), waits
   for it to run, and checks `nginx -t`.

API socket health, a database connection and container-running state do not prove
public HTTPS routing, task consumption, email delivery or payment integrations.
Verify those separately after deployment. Database and broker configuration changes,
new services or changed service names require a release-script review. The script
never recreates PostgreSQL/Redis to apply infrastructure changes.

## GitHub configuration

Create a **production** environment under repository **Settings → Environments**.
Restrict its deployment branches to `main`. Leave required reviewers disabled for
fully automatic deployment, or enable them if manual approval is desired.

Add these environment variables:

| Variable                | Value                                                           |
| ----------------------- | --------------------------------------------------------------- |
| `EC2_HOST`              | YOUR EC2 HOST                                                   |
| `EC2_USER`              | `ubuntu`                                                        |
| `EC2_DEPLOY_PATH`       | `/home/ubuntu/ecommerce/backend`                                |
| `AWS_REGION`            | YOUR_AWS_REGION (eg. `ap-southeast-1`)                          |
| `AWS_ROLE_ARN`          | ARN of `github-ecommerce-production-deploy` in your AWS account |
| `EC2_SECURITY_GROUP_ID` | The instance security group ID (`sg-...`, not a rule ID)        |

`~/ecommerce/backend` resolves to that path for `ubuntu`. The workflow requires an
absolute path without spaces. SSH uses port 22. Hostnames and IPv4 addresses are
supported; IPv6/custom SSH ports require a workflow change.

Add these environment secrets:

| Secret                | Contents                                                             |
| --------------------- | -------------------------------------------------------------------- |
| `EC2_SSH_PRIVATE_KEY` | Complete unencrypted private key for a dedicated deployment identity |
| `EC2_SSH_KNOWN_HOSTS` | Verified OpenSSH known-hosts entry for `EC2_HOST`                    |

Do not commit either secret. Do not reuse the owner's personal
`ecommerce-demo-key.pem` for Actions. On your workstation, generate a dedicated key:

```bash
ssh-keygen -t ed25519 -C github-actions-ecommerce-deploy -f ~/.ssh/ecommerce-actions -N ''
```

Through your existing trusted SSH session, add the generated `.pub` line to
`/home/ubuntu/.ssh/authorized_keys`, prefixed with `restrict ` to disable forwarding
and PTY access while allowing the noninteractive deployment command. Ensure `.ssh`
has mode 700 and `authorized_keys` mode 600. Put the private file's contents into
`EC2_SSH_PRIVATE_KEY`; keep it off EC2.

Obtain the server's public host key through an independently trusted connection
(such as your existing verified SSH session or the EC2 console):

```bash
cat /etc/ssh/ssh_host_ed25519_key.pub
```

Build the known-hosts secret as one line using the same host as `EC2_HOST`:

```text
YOUR_EC2_HOST ssh-ed25519 <actual-server-public-host-key>
```

For the workflow's port 22 connection, use the plain hostname/address without
literal square brackets. The server host key is different from the deployment
user's public key. Do not blindly trust a fresh `ssh-keyscan` result. Actions enforces host-key checking and
never discovers/trusts a new host key during deployment.

## AWS OIDC and temporary SSH access

In IAM, create an OpenID Connect provider for
`https://token.actions.githubusercontent.com` with audience `sts.amazonaws.com`.
Create role `github-ecommerce-production-deploy` with this trust policy, replacing
`ACCOUNT_ID` with your AWS account ID:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:OWNER/REPOSITORY:environment:production"
        }
      }
    }
  ]
}
```

Attach an inline permissions policy scoped to the instance's security group.
Replace `ACCOUNT_ID` and `SECURITY_GROUP_ID` before saving:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:AuthorizeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupIngress"
      ],
      "Resource": "arn:aws:ec2:ap-southeast-1:ACCOUNT_ID:security-group/SECURITY_GROUP_ID"
    }
  ]
}
```

Set the three AWS environment variables above. No AWS access-key secrets are
needed. The job uses `id-token: write` to assume the role with temporary credentials.
The role permits ingress changes on the specified group; this IAM policy does not
restrict those changes to port 22. The workflow itself requests TCP/22 only.

Before SSH, the workflow obtains its public IPv4 address from AWS Check IP,
validates it, creates a single `/32` rule described with the GitHub run ID/attempt,
and records AWS's returned `sgr-...` ID in the runner temporary directory. A short
propagation delay precedes SSH. An `always()` cleanup step revokes only that ID,
including when the release fails. Failed creation (including an existing duplicate)
blocks deployment and never authorizes deletion of an existing rule.

Cleanup failure fails the workflow and prints the rule ID for manual removal.
Rules have no automatic expiry. Forced runner termination, job timeout or a lost
AWS create response can leave a rule without successful cleanup. Inspect inbound
rules with description `GitHub-Actions-RUN_ID-ATTEMPT` after those cases and remove
the leftover temporary rule. A failed SSH attempt may still require cleanup even
though the remote release never started. This changes security-group rules only;
it does not provision IAM, restart the instance or change its other rules.

## EC2 prerequisites

- The existing stack must already run from `/home/ubuntu/ecommerce/backend` using
  Compose project `ecommerce-backend-prod` and `compose.prod.yaml`, with healthy
  PostgreSQL and Redis, valid certificates and the existing `.env`.
- `ubuntu` must own the checkout and be able to run Docker without an interactive
  `sudo` prompt. Git, Bash, `flock`, GNU `timeout`, and a Compose version supporting
  `--wait`, `--wait-timeout` and `--interactive=false` must be installed.
- `origin` must refer to the backend GitHub repository and authenticate
  noninteractively. Releases retain checkout-local `core.sshCommand` and enforce
  SSH batch mode, preserving bootstrap credentials. For a private repository, use
  a separate read-only repository deploy key stored on EC2 (or an existing approved credential helper). This is
  separate from the Actions-to-EC2 key. Verify GitHub's SSH host identity when
  configuring EC2-to-GitHub access.
- The instance must be reachable publicly on port 22 once its security group
  permits the runner. Network ACLs, routing and host firewall must also permit SSH.
  Keep the existing workstation `/32` rule for personal access. The workflow adds
  a separate temporary runner `/32` rule and removes only the returned rule ID;
  it never opens SSH to all addresses or removes the personal rule.

Test the existing account's prerequisites on EC2 without displaying `.env`:

```bash
cd /home/ubuntu/ecommerce/backend
git status --short
GIT_TERMINAL_PROMPT=0 git ls-remote origin refs/heads/main
docker compose version
docker compose -f compose.prod.yaml config --quiet
docker compose -f compose.prod.yaml ps
```

Review and resolve local changes; never discard them automatically. The checkout
becomes detached after a release, so use the workflow/script for later releases
instead of relying on a plain `git pull`.

## Enable and verify

1. Configure EC2 access, the AWS OIDC role and all production environment variables/secrets first.
2. Merge the workflow and script into `main`; this push starts the first release.
3. Watch **Actions → EC2 Auto Deploy on origin main changes**. The job reports the
   deployed SHA and the failing stage if a command fails.
4. Verify public API HTTPS, login, catalog/media and required background jobs.

The existing PR checks remain separate. Configure **Backend checks** as a required
check on `main` to enforce tests before merging. This deployment workflow does not
wait for another workflow or protect direct pushes by itself. All `main` changes,
including documentation-only commits, trigger deployment for a predictable rule.

GitHub serializes production runs without cancelling the active deployment. A
newer pending run can replace an older pending run. The host lock also excludes
demo resets; lock contention fails the run, so rerun after maintenance completes.
A manual run deploys the latest main SHA represented by that run; rerunning an old
superseded run will skip. Use a new manual run on `main` after a newer commit.

## Failure and recovery

Preflight/build failures leave the existing containers running. Once shutdown
begins, failures or handled signals attempt to stop Nginx and leave traffic closed;
application services may be partially restarted. The workflow uses a 25-minute
remote timeout and a 30-minute job timeout. Cancellation, forced termination or
host/network failure can still leave an uncertain outcome. Check EC2 before
rerunning; an SSH failure does not prove the remote release stopped.

Inspect status first and review relevant logs locally, redacting secrets/customer
data before sharing them:

```bash
cd /home/ubuntu/ecommerce/backend
git rev-parse HEAD
docker compose -f compose.prod.yaml ps -a
```

Fix the failing build, migration, storage or startup condition and run a fresh
workflow on `main`. Repeating the same current SHA repeats the release; migrations
are tracked by Django, but application data migrations must themselves be reviewed
for safe recovery. If code changes are needed, merge the fix and deploy that SHA.
There is no automatic rollback. Successful migrations/static changes can persist
after a later failure, and replacing code does not reverse database changes.
Never use `down -v`, `reset-demo.sh` or a schema reset as deployment recovery.

After inspecting and resolving a failed release, an operator can rerun the current
script directly (also acquires the lock):

```bash
bash scripts/deploy-production.sh "$(git rev-parse origin/main)" /home/ubuntu/ecommerce/backend
```

That uses the locally fetched `origin/main`; if it is stale the script skips. Fetch
main first or use a fresh Actions run to select the current revision. Normal
releases preserve `.env`, certificates and named volumes, but migrations intentionally
change database state and static collection updates the static volume. Backups and
release-specific migration review remain necessary.

## References

- [Production Docker reference](deployment-guide.md#technical-appendix-production-docker-reference)
- [PR checks](pull-request-checks.md)
- [GitHub deployment controls](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments)
- [Docker Compose production releases](https://docs.docker.com/compose/how-tos/production/)
