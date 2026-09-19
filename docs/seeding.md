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
comparison against the plan's attributes and references, then, only if that
comparison passes, a native cable trace per generated cable (asserting the cable
appears in its A-side trace) and component placement queries by kind and
placement — without writing anything. It applies the same builtin allowlist a
load does, computed per row against the already-loaded target, so a stock NetBox
4.7 instance's factory ModuleTypeProfiles do not fail the run; the allowed ids are
recorded in the receipt along with `receipt_version`, `compiler_version` and the
full mismatch list. It is the acceptance check for any seeding path that bypasses
the loader, records a `verify-*` receipt under `build/load-receipts/`, exits 2 on
mismatch, and refuses to overwrite anything but a prior verify receipt.
It does not check change history: a database-restored estate has none, and a
branch's review evidence remains the loading receipt's job. It is also distinct
from `just verify`, the offline plan check that never contacts a target.

## Enterprise: seed by database restore (database-restore path)

**Designed, not executed.** No dump/restore cycle in this runbook has been run
against a NetBox Enterprise instance. Each step is sourced below, but the
pipeline as a whole is unqualified; treat the first run as a qualification
exercise on a scratch instance.

An Enterprise admin has the required access (`netbox-enterprise shell` for
kubectl, the Crunchy PGO master pod for psql). The pipeline is
**load once through the loader, dump, restore N times** — never hand-craft SQL;
the estate must have been written through NetBox so trigger-maintained state
(ltree paths on 4.7) is correct.

1. On a scratch instance of the exact target NetBox `major.minor`, run
   `just load` against main (with `ALLOW_MAIN_WRITES=1`, since a TurboBulk branch
   cannot currently be merged) and keep its receipt.
2. Dump: `pg_dump -Fc --no-owner --no-privileges` of the netbox database,
   **excluding per-instance tables** (this list is authored for this pipeline,
   not taken from upstream documentation; validate it on a scratch instance):
   `users_token`, `users_userconfig`, `django_session`, `django_admin_log`,
   `social_auth_usersocialauth`, `extras_bookmark`, `extras_dashboard`,
   `core_objectchange`, `core_job`, `core_configrevision`. Keep `users_user`:
   `dcim_rackreservation.user` is a required PROTECTed FK, so an estate built
   with `reservation_user` set will not restore without it. Recreate or
   reconcile accounts on the target instead. Search-index rows
   (`extras_cachedvalue`) and cable paths (`dcim_cablepath`) are plain data, so
   shipping them should avoid a post-restore rebuild — confirm on the first run.
3. Restore with Maintenance Mode on and cluster secrets restored **before** the
   data, per the Enterprise backup documentation — note that document prescribes
   `pg_dumpall | psql`, so a `-Fc` dump departs from it deliberately, to get
   `pg_restore`'s `--exit-on-error` (recommended by NetBox's *Repairing
   Hierarchical Paths* guide) and selective table exclusion. `--jobs` requires
   the custom or directory format and cannot be combined with
   `--single-transaction`; since this runbook uses `--exit-on-error` rather than
   a single transaction, a failed restore leaves partial data — drop and recreate
   the database before retrying. Maintenance Mode off afterwards. The dump's
   NetBox `major.minor` must match the deployment.
4. Prove it: `python3 manage.py rebuild_ltree_paths --check` (4.7+), then
   `just verify-target ARTIFACT https://the-instance` from this repository.

On NetBox 4.7.0 the ltree cascade triggers cannot be recreated when restoring a
`pg_dump`, regardless of how correct the source estate is (fixed in 4.7.1). Run
the `--check` above on every restored 4.7.0 instance and repair before use.

What a restore cannot carry: change history (the changelog is empty by
design), reviewable branch evidence, uploaded media, and anything in the
excluded per-instance tables. Those limits are inherent, not defects; the
verify receipt is the acceptance evidence that replaces the loading receipt.

## Cloud

Cloud tenants expose no database access. Seeding options, fastest first:

- Platform-side seed dumps (the pattern the trial-instance pool uses) require
  Cloud operations ownership. Nothing about the generator's artifacts should
  obstruct that pipeline — they are ordinary NetBox rows written through NetBox —
  but this has not been tried, and the dump rules above are unexecuted.
- The loader against a tenant branch remains the only token-driven path and
  the only reviewable one; see the [loading guide](loading.md).
- The platform backup API is reported to restore a seeded instance's backup into
  another existing instance in the same organization (API-only). The public Cloud
  backup documentation describes restoring an instance from its own backup history
  via the Console and says nothing about cross-instance restore, so treat this as
  reported, not verified against public documentation; verify any result with
  `just verify-target`.
