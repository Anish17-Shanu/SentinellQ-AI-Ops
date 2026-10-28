# Releasing

SentinelIQ follows [semantic versioning](https://semver.org/). The version lives in one place:
`VERSION` in [`src/config.py`](../src/config.py), mirrored in [`pyproject.toml`](../pyproject.toml),
the Docker tag, and `/health`.

## Automated pipeline

| Workflow | Trigger | What it does |
| --- | --- | --- |
| [`ci.yml`](../.github/workflows/ci.yml) | push / PR | Lint, types, security scans, tests with coverage, dashboard checks, Docker build and smoke, serverless cold-start budget |
| [`release.yml`](../.github/workflows/release.yml) | tag `v*` or manual tag input | Re-runs the release gates, builds and pushes the GHCR image, scans it with Trivy, creates the GitHub release |
| [`deploy-container.yml`](../.github/workflows/deploy-container.yml) | manual dispatch | Pulls the released tag on a self-hosted host, restarts Compose, and runs the smoke test |
| [`deploy-vercel.yml`](../.github/workflows/deploy-vercel.yml) | manual dispatch | Deploys the prebuilt serverless function to production and runs the same smoke test |

The release workflow uses only `GITHUB_TOKEN` (permissions `contents: write`,
`packages: write`). No additional repository secret is required to publish the image and
release notes.

Release artifacts:

- `ghcr.io/<owner>/<repo>:vX.Y.Z` and `:latest`, with buildx provenance (`mode=max`) and an
  SBOM attestation.
- A Trivy scan fails the publish job on unfixed `CRITICAL` or `HIGH` findings before the
  GitHub release is created.
- A GitHub release with generated notes for the pushed tag.

## Checklist

1. **Green gates.** `ruff`, `mypy`, `bandit`, `pip-audit`, the unit suite, the Docker smoke
   job, and the cold-start budget must all pass on `main`.
2. **Changelog.** Move the unreleased notes in [`CHANGELOG.md`](../CHANGELOG.md) under a new
   `## [x.y.z] - YYYY-MM-DD` heading and add the compare link.
3. **Version bump.** Update `VERSION`, `pyproject.toml`, and the `User-Agent` strings in
   `src/notifier.py`/`src/sentry.py` when the major version changes.
4. **Rebuild the demo assets.** `python scripts/prepare_vercel.py` and commit the result only if
   `public/` is tracked for the deployment target (it is generated, so normally do not commit it).
5. **Tag.** `git tag -a vX.Y.Z -m "SentinelIQ vX.Y.Z" && git push origin vX.Y.Z`. The tag push
   starts `release.yml`; the same workflow can be dispatched manually with an existing tag.
6. **Confirm the image.** Check the workflow run for the GHCR tag, SBOM attestation, Trivy
   result, and GitHub release. Pin the image digest in deployment manifests.
7. **Deploy both targets, in order.** Dispatch `deploy-container.yml` first (canonical build),
   then `deploy-vercel.yml` (public demo). Both run `scripts/smoke_test.py` against the live
   URL and fail the job on any non-200 or role violation.
8. **Record the hosted numbers.** After the first Vercel deploy, run
   `python scripts/cold_start.py` locally for the baseline and note the platform-reported cold
   start and duration in the release notes. The CI budget is 2 s.
9. **Post-release.** Record the restore-drill date in the team log (see
   [RUNBOOK](RUNBOOK.md#restore-drill-required-quarterly)).

## Required secrets

| Secret | Used by | Notes |
| --- | --- | --- |
| `GITHUB_TOKEN` | `release.yml` | Built in; needs `contents` and `packages` write |
| `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` | `deploy-vercel.yml` | Project settings and a scoped token |
| `SENTINELIQ_BASE_URL`, `SENTINELIQ_ADMIN_KEY` | both deploy workflows | Live URL and an admin key for the smoke test |
| `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH`, `DEPLOY_SSH_KEY`, optional `DEPLOY_KNOWN_HOSTS` | `deploy-container.yml` | SSH target and the key authorized to run Compose |
| `DEPLOY_BASE_URL`, `DEPLOY_ADMIN_KEY` | `deploy-container.yml` | Same idea as the Vercel values |

Deploy workflows check their secrets first. If any are missing they exit with a notice and
skip the deploy, so forks and credential-less clones stay green.

## Compatibility policy

- `/v1` routes are additive-only within a major version. New optional fields are fine; removing
  or renaming a field requires `/v2` and a deprecation window of at least one minor release.
- Storage envelope changes require a migration path and an entry in `CHANGELOG.md`.
- Dashboard changes must keep the no-`innerHTML` invariant enforced by CI.

## Version support

The current major version receives security fixes. Older majors get a one-line upgrade note in
`CHANGELOG.md`. There is no long-term-support branch for a project of this size.
