# Backend pull request checks

## Scope

`.github/workflows/pr-checks.yml` runs **Backend checks** for pull requests targeting
`main` when opened, updated, reopened or marked ready for review. Draft PRs also
run checks. The default checkout tests GitHub's temporary PR merge commit, so the
checks exercise the proposed changes together with the target branch. GitHub
cannot run a normal `pull_request` workflow on a PR with merge conflicts; resolve
those first. New runs cancel older runs for the same PR. Every path is checked,
including documentation changes, to avoid leaving required checks pending.

The workflow uses Python 3.12, PostgreSQL 17 and Redis 7, matching the current
production runtime versions. It installs `requirements.txt` and runs:

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test --noinput
python -m unittest discover -s scripts/tests -v
```

Django creates and destroys a separate test database; its test setup applies
migrations. No separate migration step against the initial service database is
needed. Script tests fake Docker calls rather than invoking production services.
Redis provides a disposable broker if application paths enqueue Celery tasks; no
Celery worker or Beat process is started. Queue delivery is not an end-to-end check.

All service credentials are disposable CI values. No repository secrets, EC2
connection, production `.env`, real SMTP credentials, Azure storage credentials,
or Stripe keys are required. Email uses Django's in-memory backend and media uses
local filesystem storage. Existing provider mocks exercise integration behavior;
this does not verify live providers. Fork PRs use `pull_request`, not the privileged
`pull_request_target` event. The job has read-only repository permissions and a
20-minute timeout. It does not deploy or build the production image, and it does
not add a new lint/type-check toolchain.

## Require checks before merging

1. Push the workflow on a branch and open a PR targeting `main`.
2. Let **Backend checks** run successfully at least once.
3. In GitHub repository **Settings → Rules → Rulesets**, create or update an active
   branch ruleset targeting `main` (subject to repository/plan availability).
4. Require pull requests and require the **Backend checks** status check from
   GitHub Actions. Require branches to be up to date before merging if you want
   the checks rerun against the latest `main` before merge.
5. Review bypass permissions: actors allowed to bypass can still merge without
   passing checks. Keep the job name stable after configuring the required check.

The workflow alone reports results; repository rules enforce merge restrictions.
Rules are configured separately and are not changed by adding this file. Merge
queues are outside this setup; enabling one requires a `merge_group` trigger.
Direct pushes to `main` do not trigger this PR-only workflow.

## Troubleshooting and validation

Inspect the failed step in the PR's Checks tab. Missing migration files should be
generated locally and committed; CI never generates and commits migrations for you.
Test failures must be investigated before merging. Re-run transient service or
package-download failures from GitHub Actions. Cancelled older runs are superseded
by the newest PR run.

The authoritative end-to-end validation is the first GitHub-hosted PR run. Local
checks cannot prove GitHub event delivery or branch-rule enforcement.

## References

- [PostgreSQL services in GitHub Actions](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)
- [Required status checks](https://docs.github.com/en/pull-requests/reference/status-checks)
- [Workflow](../.github/workflows/pr-checks.yml)
- [Production runtime](../Dockerfile.prod)
- [Production services](../compose.prod.yaml)
