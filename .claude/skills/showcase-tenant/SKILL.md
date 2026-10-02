---
name: showcase-tenant
description: Rebuild the NetBox Labs community showcase on a dedicated NetBox Cloud tenant end to end — tear down, generate, seed main, floorplans, procurement history, validation packs, Assurance drift and live discovery, then visual review. Use when (re)building or refreshing a demo/showcase tenant whose data does not matter.
---

# Rebuild the showcase tenant

The showcase is one connected estate (`profiles/showcase-provider.toml`, a
Great Lakes regional carrier with 12 PoPs and 12 enterprise customers) seeded
onto a **dedicated** tenant's main so Visual Explorer and every plugin see it,
then layered with plugin sidecars. First run: 2026-10-01/02 on `crsk8600`
("Devin's Lab (Main)" in the Devins Lab workspace). Everything here is
destructive on that tenant and only that tenant.

Related skills: `drive-browser` (UI steps), `netbox-labs-skills` (official
product skills to read per feature), `visual-review` (the acceptance pass),
`site-geography`.

## Credentials (all under ignored `build/`)

- `build/<tenant>.env`: `NETBOX_URL`, `NETBOX_TOKEN` (REST, write).
- `build/<tenant>-diode.env`: `DIODE_TARGET`, `DIODE_CLIENT_ID`,
  `DIODE_CLIENT_SECRET`. Create in NetBox → Diode → Client Credentials → Add
  (the secret is shown once — scrape it into the file via `drive-browser`,
  never print it, then navigate away from the secret page).

`set -a; . build/<tenant>.env; set +a` before target recipes.

## Order (each step's output is the next step's input)

1. **Empty main.** For each previous artifact: `just teardown-main-explain`,
   then `ALLOW_MAIN_TEARDOWN=1 just teardown-main ARTIFACT $NETBOX_URL`.
   Sidecar rows PROTECT the estate, so delete them first: physical-geometry
   shapes → paths → zones → layers → floorplans; asset-lifecycle rows per
   docs/loading.md ("Procurement history"). `seed-main-explain` must show only
   allowlisted occupancy (builtin `module_type_profile`).
2. **Generate.** `just generate profiles/showcase-provider.toml build/showcase`.
3. **Seed.** `TURBOBULK_WRITES=1 ALLOW_MAIN_WRITES=1 just seed-main
   build/showcase/plan.json $NETBOX_URL 10000` (explain first with the same
   flags — without them the explain only reports "flag not set"). Main seeds
   are changelog-free, so circuit-termination save hooks run and the WAN map
   draws arcs. CDC must be off (COVERAGE.md "Blocking platform
   incompatibility"): Analytics needs CDC, so enable Analytics only after
   seeding.
4. **Floorplans.** `just geometry build/showcase/plan.json build/showcase-geometry`,
   `GEOMETRY_WRITES=1 just seed-geometry build/showcase-geometry $NETBOX_URL`.
5. **Procurement history.** `just lifecycle build/showcase/plan.json
   build/showcase-lifecycle`, `LIFECYCLE_WRITES=1 just seed-lifecycle
   build/showcase-lifecycle $NETBOX_URL`.
6. **Validation.** Install starter packs and run them on the target (engine
   output only — never POST results/findings). Avoid packs whose premises the
   estate does not meet (see the plugin research in docs/schema-map.md tail).
7. **Assurance.** Ignore stale deviations from other instances first (the
   console list mixes instances and shows no instance column). Then the drift
   twin: `just drift build/showcase/plan.json build/showcase-drift`,
   `just drift-check`, `just drift-ingest` with the tenant's Diode env.
8. **Live discovery.** `lab/discovery/README.md`: Colima profile
   `genial-discovery`, SR Linux lab rendered from the plan, Fleet-managed
   orb-agent, Vault-referenced credential, Device discovery job.
9. **Visual Explorer.** Settings → Clear Cache / Refresh after any reseed.
10. **Visual review** (`visual-review` skill) with critical reviewer agents;
    fix in the generator, regenerate, reseed — never hand-edit the tenant.

## Lessons from the first build

- One estate per main: the fresh-load occupancy gate refuses a second, and
  data-center sites hash to the same display names across estates anyway.
- Fan out generator fixes to parallel worktree agents by file ownership and
  merge yourself; expect small conflicts in `blocks.py`, `docs/modeling.md`
  and the provider validator.
- Geography verification against OpenStreetMap is rate-limited (~1 req/s):
  see `site-geography` before touching anchors.
- Visual Explorer reads through TurboBulk export jobs and caches in the
  browser: stale views after a reseed are the cache, not the data.
