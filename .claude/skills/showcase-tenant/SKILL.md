---
name: showcase-tenant
description: Rebuild the NetBox Labs community showcase on a dedicated NetBox Cloud tenant end to end — tear down, generate, seed main, floorplans, procurement history, validation policies, the showcase tour (saved filters, dashboard), Assurance drift and live discovery, then the v0.18 tour and visual review. Use when (re)building or refreshing a demo/showcase tenant whose data does not matter.
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
   Sidecar rows PROTECT the estate, so delete them first with their seed
   receipts: `SHOWCASE_WRITES=1 just unseed-showcase RECEIPT $NETBOX_URL`
   (restores the token user's prior dashboard; teardown also retires the saved
   filters by name), `VALIDATION_WRITES=1 just unseed-validation RECEIPT $NETBOX_URL`,
   `LIFECYCLE_WRITES=1 just unseed-lifecycle RECEIPT $NETBOX_URL`,
   `GEOMETRY_WRITES=1 just unseed-geometry RECEIPT $NETBOX_URL`. `seed-main-explain` must show only
   allowlisted occupancy (builtin `module_type_profile`).
2. **Generate.** `just generate profiles/showcase-provider.toml build/showcase`.
   The recipe sets `tenancy = "dedicated"` (solo-tenant mode): owner, export
   template, webhook, event rule, custom-link and choice-set names carry no
   `inland-fiber ` prefix. Teardown reads the mode from each artifact's own
   plan, so tearing down an older shared-mode artifact still matches by prefix.
3. **Seed.** `TURBOBULK_WRITES=1 ALLOW_MAIN_WRITES=1 just seed-main
   build/showcase/plan.json $NETBOX_URL 10000` (explain first with the same
   flags — without them the explain only reports "flag not set"). Main seeds
   are changelog-free, so circuit-termination save hooks run and the WAN map
   draws arcs. CDC must be off (COVERAGE.md "Blocking platform
   incompatibility"): Analytics needs CDC, so enable Analytics only after
   seeding.
4. **Floorplans.** `just geometry build/showcase/plan.json build/showcase-geometry`,
   `GEOMETRY_WRITES=1 just seed-geometry build/showcase-geometry $NETBOX_URL`.
   The tour's floorplan shot is the founding PoP's cage (Cermak Cage G09),
   so confirm the cage location carries one before capturing.
5. **Procurement history.** `just lifecycle build/showcase/plan.json
   build/showcase-lifecycle`, `LIFECYCLE_WRITES=1 just seed-lifecycle
   build/showcase-lifecycle $NETBOX_URL`.
   v0.18 adds per-era orders, an `equipment on order` BOM (staged MX304 pair
   and TMS: POs `ordered`, deliveries received, nothing installed) and one
   NOC Field depot of boxed NID, CE and optic spares received through its own
   stock order. Racked `inventory` cold spares are devices only, never depot
   items; *Install* on a depot item creates a new device (a boxed spare
   deployed), so do it only when demonstrating exactly that.
6. **Validation.** `just validation build/showcase/plan.json
   build/showcase-validation`, `VALIDATION_WRITES=1 just seed-validation
   build/showcase-validation $NETBOX_URL`. The seed runs every policy once and
   records predicted-vs-actual per rule (engine output only — never POST
   results/findings); unpredicted or missing subjects are engine drift to
   reconcile in `estates/validation.py`, never to tune away. Starter packs stay
   uninstalled (their premises are a leaf/spine DC; docs/loading.md).
7. **Showcase tour.** Open Home once as the token's user (NetBox creates the
   dashboard on that visit), then `just showcase build/showcase/plan.json
   build/showcase-tour` (prints F1–F5) and `SHOWCASE_WRITES=1 just
   seed-showcase build/showcase-tour $NETBOX_URL`: nine shared saved filters,
   three bookmarks and the home dashboard for that user. Then the manual steps
   from `showcase.json` `manual_steps`: Admin → Configuration history → new
   revision with `BANNER_TOP` and `DEFAULT_USER_PREFERENCES` (sites and
   tenants ordered by group) — no REST endpoint exists for either.
8. **Assurance.** Ignore stale deviations from other instances first (the
   console list mixes instances and shows no instance column). Then the drift
   twin: `just drift build/showcase/plan.json build/showcase-drift`,
   `just drift-check`, `just drift-ingest` with the tenant's Diode env.
9. **Live discovery.** `lab/discovery/README.md`: Colima profile
   `genial-discovery`, SR Linux lab rendered from the plan, Fleet-managed
   orb-agent, Vault-referenced credential, Device discovery job.
10. **Visual Explorer.** Settings → Clear Cache / Refresh after any reseed.
11. **The v0.18 tour**, in this order, as the seeding user (blank canvas =
    re-capture):
    1. **Home dashboard** — the note names the carrier and its numbers; PoP,
       not-in-service and journal lists populated; three bookmarks.
    2. **Carrier PoPs** saved filter (or `/dcim/sites/` once the default
       preferences order by group) — the NOC and 12 PoPs first.
    3. **Cermak cage, rack R01** elevation front and rear, then R02: the
       stratigraphy (2011 OSP panels and time server, 2016 aggregation, 2019
       management, 2025 MX204s), blanking panels in the removal gaps, the
       `decommissioning` MX80 relics and the staged MX304 successor; then VE
       racks → Cermak → R01, unfitted and fitted, and Cage G09 floorplan.
    4. **TMS** (`chicago-cermak-ddos01`): staged, cabled to both PEs; its PO
       in Asset Lifecycle → equipment on order.
    5. **Journals** — `chicago-cermak-pe-a` journal (successor ordered, IX
       turned up, cut over from `chcgilcr-rtr1`, installed), the R01 rack
       journal (removed predecessors), then `/extras/journal-entries/`.
    6. **IX** — Internet exchanges saved filter, the CAL-IX port bookmark and
       its trace from the PE through the colo demarc panel.
    7. **Former customers** saved filter and `/circuits/circuits/?status=decommissioned`.
12. **Visual review** (`visual-review` skill) with critical reviewer agents;
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
- The platform session can bounce to `auth.netboxlabs.com` "Choose an
  Organization": pick the workspace (Devins Lab) and continue — the login
  itself is intact.
- The Assurance deviations table renders one page at a time; to bulk-act on a
  subset, set page size 10 and drive page-by-page from the shell (one short
  `eval` per page: tick matching rows, press the toolbar action). Long in-page
  async loops get the CDP client killed. Match evaluated JSON output in shell
  without quotes — the driver prints it escaped.
- Never run `discovery-lab-check` dry runs while an older Fleet agent backend
  owns port 8072 (fixed in `vm.sh`, but remember the failure mode: it ingests
  for real).
- Fleet needs the instance-level enablement before devices appear in the
  credential/job pickers (COVERAGE.md, "Open on crsk8600").
- Read deviations in NetBox's own Assurance → Deviations, not the platform
  console, and widen its date range (`?daterange=30days`): the default *Last
  24 hours* hid a working drift ingest for three days. After a reseed, Ignore
  the previous estate's deviations, then re-run `drift-ingest` (inside
  `devenv --profile diode shell`) with the tenant's Diode env; deviations land
  in seconds.

