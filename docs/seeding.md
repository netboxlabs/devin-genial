# Seeding environments from a verified estate

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

The loader ([loading guide](loading.md)) is the verified, reviewable path into a
single target. This guide covers turning one verified load into many seeded
environments, and proving any environment's contents regardless of how they
arrived. Run commands from the repository root.

## Verify any target with zero writes

```sh
just verify-target build/my-estate https://netbox.example            # main schema
just verify-target build/my-estate https://netbox.example "Branch"   # one branch
```

This runs the same strict gate as a load's final readback — full inventory
comparison against the plan's attributes and references, native cable traces
for every generated cable, and component placement checks — without writing
anything. It is the acceptance check for any seeding path that bypasses the
loader, and it records a `verify-*` receipt under `build/load-receipts/`.
It does not check change history: a database-restored estate has none, and a
branch's review evidence remains the loading receipt's job.

## Enterprise: seed by database restore (official migration path)

NetBox Enterprise documents `pg_dump`/`psql` restore as its migration
mechanism, and an Enterprise admin has the required access (`netbox-enterprise
shell` for kubectl, the Crunchy PGO master pod for psql). The pipeline is
**load once through the loader, dump, restore N times** — never hand-craft SQL;
the estate must have been written through NetBox so trigger-maintained state
(ltree paths on 4.7) is correct.

1. On a scratch instance of the exact target NetBox `major.minor`, run
   `just load` (or a branch load merged to main) and keep its receipt.
2. Dump: `pg_dump -Fc --no-owner --no-privileges` of the netbox database,
   **excluding per-instance tables**: `users_user`, `users_token`,
   `users_userconfig`, `django_session`, `django_admin_log`,
   `social_auth_usersocialauth`, `extras_bookmark`, `extras_dashboard`,
   `core_objectchange`, `core_job`, `core_configrevision`. Search-index rows
   (`extras_cachedvalue`) and cable paths (`dcim_cablepath`) are plain data —
   ship them so no post-restore rebuild is needed.
3. Restore per the Enterprise backup documentation: Maintenance Mode on,
   cluster secrets restored **before** the data, then
   `pg_restore --exit-on-error` (validate `--jobs > 1` on a scratch instance
   first: parallel restore can reorder past FK-safe ordering), Maintenance
   Mode off. The dump's NetBox `major.minor` must match the deployment.
4. Prove it: `python3 manage.py rebuild_ltree_paths --check` (4.7+), then
   `just verify-target ARTIFACT https://the-instance` from this repository.

What a restore cannot carry: change history (the changelog is empty by
design), reviewable branch evidence, uploaded media, and anything in the
excluded per-instance tables. Those limits are inherent, not defects; the
verify receipt is the acceptance evidence that replaces the loading receipt.

## Cloud

Cloud tenants expose no database access. Seeding options, fastest first:

- Platform-side seed dumps (the pattern the trial-instance pool uses) require
  Cloud operations ownership; the generator's artifacts are compatible with
  that pipeline by construction, using the same dump rules as above.
- The loader against a tenant branch remains the only token-driven path and
  the only reviewable one; see the [loading guide](loading.md).
- The platform backup API can restore a seeded instance's backup into another
  existing instance in the same organization (API-only); verify the result
  with `just verify-target`.
