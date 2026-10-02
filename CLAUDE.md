# Genial

An offline, deterministic generator of believable connected estates for NetBox,
exported through Diode. Regional bank, enterprise DC, school district,
hospital/clinic, provider backbone, retail chain, university campus, managed
service provider, manufacturing and electric utility have generation profiles.
No LLM calls belong in generation, allocation, validation, or export.

The product goal is a believable whole estate. Demo stories are views into that
connected estate; a library of isolated industry slices does not replace it.
Keep industry semantics and cross-site dependencies intact as demand grows.
Generation scale and successful target ingestion are separate qualification gates.

Version 0.9 implements shared enrichment, equipment history, dual-stack,
PoE/wireless, optics and a provider maintenance story. All five final representative
baselines passed initial/repeat Diode, strict readback and rendered UI inspection.
See GOAL.md for the objective and final evidence; milestones are not whole-goal
acceptance. Current receipts are under `build/goal-connected-depth/final/`.
Phase 5 is preserved as an accepted offline milestone. The final provider
status-only sequence passed baseline/repeat, offline/repeat and restore/repeat
through Diode on one pinned local database, with strict readback and stable IDs.
This qualifies inventory status reconciliation only; other target versions,
cable rewiring, forwarding and router execution remain outside that evidence.
The completed v0.8 goal/source are preserved in `build/goal-connected-depth/`;
its final live and review evidence below remains historical.
Images were explicitly deferred after assessing the separate renderer/REST path.
Do not add image dependencies, uploaders or write credentials for this goal.
v0.9 requires a new baseline: generator version participates in stable choices,
and previous-plan reuse rejects another generator version or hardware digest.
v0.11 is likewise a new baseline: every estate gained the six-record automation
pack, so 0.10 plans reject growth by version and must be regenerated. Any change
that alters every estate's canonical graph must bump `estates/__version__` so
those guards fire with their own messages, not a validator's.
v0.12 is a new baseline carrying two visual-realism changes found by rendering a
seeded estate. Display names across most object families became authored and
namespace-free (slugs and matching identities keep the namespace; the
main-scoped families in `NAMESPACED_KINDS` keep it in the name too) — graph
views truncate labels, so the prefix made distinct nodes indistinguishable.
That sweep was NOT exhaustive: it checked a curated opt-in list, and 0.13, 0.14
and 0.15 each had to finish families it missed. 0.15 completed it and inverted
the guard. And
the equipment-room cabinet grid was compacted into a room rather than a
corridor: cabinets became 24U four-post enclosures with a room-scoped
`facility_id`, lane members mount contiguously from the bottom rail instead of
every second unit, and device types carry `airflow` where a pinned source
declares it. Rack coordinates, rack heights, device positions, inter-rack cable
lengths and every display name move, so 0.11 plans reject growth by version and
must be regenerated.
v0.13 is a new baseline carrying two further defects that same rendering found.
Power feeds were the one family 0.12 missed: they kept the namespaced site stem
and truncated to one indistinguishable label per power chain, so they now emit
an authored `Network 01 A`-style name scoped by their panel. And each PDU's own
input port now carries the draw of the inlets cabled to its outlets — NetBox
does not roll that up, so an empty input made every feed report 0 W and left the
utilisation view dark. Both change every estate's canonical graph, so 0.12 plans
reject growth by version and must be regenerated.
v0.14 is a new baseline adding BGP inventory to the **provider backbone only**
(`estates/bgp.py`): four named routing policies, three peer groups and one
session per modeled adjacency — an iBGP route-reflector pair at the first PoP in
the permanent `provider-pop-order` ledger, an eBGP session per transit handoff
and one per private-L3 customer access circuit. A provider estate with zero BGP
sessions is the loudest synthetic tell to a network engineer, and both the
`netbox_bgp` tables and Visual Explorer's `bgp-topology` view rendered empty.
These are documentation records exactly like the inert webhook: nothing is
configured, applied or established, no session state, convergence or route
exchange is claimed, and the named policies deliberately carry no rules. Only
the provider profile changes, but the version bump is required so frozen plans
reject growth with their own message rather than a validator's — and, because
the generator version participates in stable choices, procurement dates and
serials reroll across every profile.
v0.15 is a new baseline that finishes the 0.12 display-naming sweep and makes
under-specifying it impossible. Every remaining family that interpolated the
namespace into a user-visible field is now authored: VRFs (estate, site, MSP
and provider-customer), FHRP groups, the recovery IKE/IPsec/tunnel/L2VPN/VLAN
records and their translation policy, config contexts, the per-tenant NOC and
per-workload service contacts, provider accounts, clusters, virtual chassis,
VLANs, VLAN groups, the site prefix descriptions, and — because NetBox gives
those models no `name` — the ASN and Aggregate descriptions, which now name the
provider or tenant that actually holds the record instead of a role token. The
guard in `tests/test_naming_policy.py` was inverted from an opt-in list to an
exhaustive sweep minus two reasoned exception sets, plus a namespace-invariance
check, a per-family uniqueness check across every profile, a native
name-limit check and a failing-mutation check. Identities are untouched: a
regenerated 0.14 plan differs only in `name`, `description` and `comments`
fields, with no object key and no identity attribute moved. Display names move
across every estate, so 0.14 plans reject growth by version and must be
regenerated, and the version bump rerolls procurement dates, serials and
scenario selections as usual.
v0.16 is a new baseline, "showcase realism", found by preparing a community
showcase tenant: every change targets what an engineer would read as fake.
Sites sit on land at real neighbourhood/street anchors (`places.ANCHORS`; about
half used to plot in the Great Lakes); the fictional "Devin Reference Designs"
manufacturer is gone — real devicetype-library models (Supermicro, APC, Panduit,
Opengear 16/48-port sized by console demand, Cisco 9120AXI default AP) plus
plainly-named Generic parts; serials follow vendor formats instead of `SYN-`;
network devices carry vendor platforms; every prefix and VLAN carries one of
sixteen authored IPAM roles; descriptions use operator wording and no record
carries a disclaimer (limitations live in docs/modeling.md and the report;
`record-disclaimer` refuses them); provider hostnames, customer VLANs, rates and
contact phones read like a production network. Physical sites read real too:
real-street addresses, per-metro and CLLI-style facility codes, flat location
trees (a PoP is a suite holding a cage), room-scoped cabinet codes, short
sequential asset tags, 208 V US feeds and a small-room kit for single-CE
premises. Identities, names and the
hardware digest move across every profile, so 0.15 plans reject growth by
version and must be regenerated.
The final reference-label revision also changes that digest; intermediate v0.8
packages remain historical evidence, alongside preserved v0.7 source/artifacts.
Final hospital and provider artifacts are under
`build/goal-richness/final-review/corrections/{hospital,provider}/`; their fresh
Diode/readback/replay/native/UI receipts are under `{hospital,provider}/live-final/`.
Shared context was first qualified in the preserved Harbor lifecycle. These
v0.8 target IDs are historical; the final v0.9 qualification used fresh target resets.
[COVERAGE.md](COVERAGE.md) is the required hospital/provider gap review, including
wireless relevance and ranked next additions. It does not authorize building
every gap. Whole-goal reviewer verdicts and completion audit are in
`build/goal-richness/final-review/`.

Shared MAC policy covers addressed device/VM interfaces except virtual/bridge
interfaces. Every profile must validate missing identities independently.
MACs start with the maker's public IEEE OUI (`catalog/hardware.json` `mac_ouis`,
QEMU/KVM `52:54:00` for VMs) and take their tail from the append-only `mac/<OUI>`
ledger; never borrow an OUI for a maker the catalog does not declare.
List-view hygiene (0.16, `operations.finalize`, run first by `World.finish` after
every builder): tags, taxonomy pruning, unused-port state, planned cabinets, DNS,
MTU and loopback roles are derived from the finished graph and re-derived by
`validate_operations._shared`. A tag must be discriminating (never on every
candidate of its kinds) and land only on `naming.TAGS` kinds. Prune only roles
and passive cabling types: device types, platforms and makers are a fixed library
because growth and scenario snapshots must never delete one. Statuses other than
`active` come only from ledgers (IP-range geometry, unused ports, the next grid
cabinet), never invented events; required paths stay `active`.
Journals are short operational lines whose kind follows the event (completed
success, open action warning, else info); never restate the record's own
fields; dates follow the site's service day (its first circuit's install date,
read from the graph). Contact priority follows desk order (technical primary, local or
commercial secondary, specialist tertiary).
WAN procurement accounts retain bank design lineage across acquisition/refresh;
provider customer/NOC/transport accounts keep their separate authored policy.
Direct technical assignments follow equipment role and actual tenant. Biomedical
responsibility remains distinct; do not collide with its device-assignment key.
One timeline: a device installs 7-37 days before the earliest circuit on its own
ports (else its site's first circuit) and every serial date code is a manufacture
week 30-180 days before that (`operations_context.timeline`, re-derived by
`validate_operations`). Never date a record from `as_of` when a circuit dates it.
Equipment journals select the first eligible device per actual rack by permanent
U position. Keep installed component and facilities facts stable; never embed
current cable peers or mutable tenant desk names in immutable journal comments.
Optical journals choose that device's first catalog physical optical cage before
considering occupancy; an occupied cage adds one note without a new ledger.
Preserve its interface, module/bay and exact journal identities during growth.
Native interface-module ownership cascades on deletion: no module removal,
hot-swap or executed replacement workflow is implemented.
Acquisition and power-defect scenarios must retain their exact existing invariants.
Configured PSU validation derives manufacturer/model and required bays from actual
device-type references. Active modules, enabled bays, source-backed fit and owned
inlets are mandatory; stripping descriptive metadata cannot skip these checks.

## Development

The shared source repository is [netboxlabs/devin-genial](https://github.com/netboxlabs/devin-genial).
Publish source, recipes, tests and operating docs. Keep generated artifacts,
local credentials and historical qualification receipts under ignored `build/`;
those receipt paths are local evidence, not files shipped in a clone.

Use the standalone devenv shell (`direnv allow`). The runtime is Python 3.11+
standard library; the pinned Diode SDK is an optional export-verification tool.
The Justfile is the human CLI. Run `just check` before committing.
`just demo PROFILE NAME VENDOR FEATURES [TARGET BRANCH]` is the composed
operator path: it assembles a per-profile demo recipe, generates, checks,
load-checks, builds the requested feature packs and — given a target and branch
— branches, loads and verifies, writing `DEMO.md` beside the artifact.
The target-aware loader is `just load ARTIFACT TARGET [BRANCH]`;
the normal read-only preflight is `just load-explain ARTIFACT TARGET [BRANCH]`.
`just load-check ARTIFACT` reports TurboBulk contract fit offline; `just branch
TARGET NAME` creates the ready branch a load requires and refuses existing names;
`just branch-delete TARGET NAME` permanently deletes one branch (reset replaces);
`just retire TARGET NAME NAMESPACE [TENANCY]` also removes the namespace's main-scoped owner rows
(`dedicated` matches the bare labels a `tenancy = "dedicated"` recipe emits).
`just verify-target ARTIFACT TARGET [BRANCH]` runs the final strict gate with zero writes.
Target recipes source `.env` only when `NETBOX_TOKEN` is not already exported.
TurboBulk jobs default to at most 2,000 rows. Keep deterministic batch purposes,
receipt-bound request settings, one ID-resolution read per completed model, and
all global hooks out of data-bearing jobs. Run required maintenance, cable, and
search hooks as separate zero-row finalizers after data and REST completion. A
fourth `just load` argument changes the bound for measured qualification runs;
a fifth forces the upload format (`auto`/`jsonl`/`parquet`, default auto).
Data jobs upload Parquet when pyarrow is available (devenv provides it; generation
stays stdlib) with a 1..50,000 bound; JSONL keeps 1..10,000 because TurboBulk's
JSONL reader fixes the column set from the first 10,000 rows, so a sparse payload
spanning chunks would silently drop later columns. The Parquet writer compiles the
column union explicitly, refuses `_tags` and non-custom_field_data JSON objects,
keeps zero-row finalizers JSONL, and binds the format into the receipt.
Reviewable loads over 100,000 rows are refused (`GENIAL_REVIEWABLE_SCALE=1`
overrides, only for qualification on a target controlled end to end): the
TurboBulk user guide says large/ephemeral imports must disable changelogs, and
on a degraded tenant the per-row ChangeDiffs made branches undeletable through
the API — deletion drops the schema plus the diff cascade in one synchronous
request, which died at scale until the tenant was resized and decluttered (a
155,698-row branch then deleted normally). Throwaway scale loads use
`load-disposable`; the deletion wall returns whenever a tenant degrades.
Never delete a job row in the NetBox Jobs UI: it destroys the receipt's proof
and forces a fresh branch. Delete branches before upgrading a target, or they
strand in pending-migrations. `GENIAL_FORCE_IPV4=1` routes the loader over
IPv4 when a broken local IPv6 path stalls every urllib request; see
[scale-load risks](docs/loading.md#scale-load-risks--read-before-any-load-over-30k-rows).
Verification depth follows purpose. Full qualification rigor — initial plus
repeat runs, resume drills, belt-over-belt cross-checks — is paid ONCE per new
code path or target version, on the pinned local stack first, and its receipts
live in docs/qualification.md. On demo targets, an already-qualified path runs
each load exactly once: no repeat gates, no extra verification loads, no
re-proving what a recorded receipt already proves. Demo speed is the product;
do not instruct or schedule redundant passes there. (Each load's own built-in
strict readback stays — it is the load's success gate, not an extra pass; a
lighter spot-readback lane for large demo loads is reviewed backlog in
COVERAGE.md.)
Compile device-component `_site_id`, `_location_id`, and `_rack_id` caches from
the parent device in the original TurboBulk row; derive the device location from
its rack when NetBox would inherit it on save. Require the corresponding REST
filters during preflight and prove exact component IDs by kind and placement at
final readback, including null location/rack placements. Do not add a repair
upsert: reviewable history must remain one create ChangeDiff per canonical object.
Circuit terminations invert that rule (`SAVE_HOOK_KINDS`): their state lives off
the inserted row — `CircuitTermination.save()` caches the termination's scope and
back-fills `Circuit.termination_a/_z`, both `editable=False` and read-only in REST,
so no PATCH can reach them. That one job requests TurboBulk's bounded
`apply_save_hooks`; preflight requires the cache columns on both circuit models,
the job contract requires `save_hooks_applied` for every submitted row, and
readback proves the resolved pointers and a live `?site_id=` filter. Without it
circuits read as unterminated and WAN maps draw no arcs. Request those hooks only
for changelog-free jobs: on an insert whose model table name is 24+ characters,
TurboBulk 0.4.0-0.4.2 combined with `create_changelogs` builds a `_refresh`
identifier that truncates at 63 bytes onto the postchange table's own name, so
the job errors and rolls back entirely (no rows survive; the receipt's row
counters are written after the rollback). Reviewable loads therefore accept
unterminated circuits and record the skipped check with its cause.
Those commands retain review and revert history; merging a TurboBulk branch to
main is currently blocked upstream. `just load-disposable ARTIFACT
TARGET BRANCH` is the explicit scale-only path: it requires a fresh empty branch
which cannot be reviewed, merged, or reverted and must be deleted after use. It
also requires a TurboBulk-only artifact with no REST create or completion writes;
the explain and load preflights reject other artifacts before target writes.
`ALLOW_MAIN_WRITES=1 just seed-main ARTIFACT TARGET` is the explicit main-seed
policy for dedicated visualization/analytics tenants (`seed-main-explain` is its
zero-write preflight): no branch, TurboBulk changelogs off (REST records and
completion PATCHes still write ordinary changelog entries), REST writes allowed,
strict readback kept, ChangeDiff gates exempt by policy (they are a Branching
branch concept), empty-main occupancy required — never on a tenant whose
branches or history matter. Its inverse is
`ALLOW_MAIN_TEARDOWN=1 just teardown-main ARTIFACT TARGET`
(`teardown-main-explain` previews with zero writes): artifact-scoped, so only
rows the plan's own strict-readback identities resolve are deleted and every
foreign row is reported and left alone; refuses a target that does not look
like the artifact, requires `--confirm`, and refuses a branch. It deletes in
reverse dependency order because NetBox PROTECTs referenced rows, retires the
main-scoped extras/owners last through `retire_namespace_rows`, re-resolves
each bounded batch before deleting (the bulk endpoint rejects a batch naming an
absent row, which is also what makes a resume safe), proves each delete by
re-reading rather than by the 204's empty body, and fails loudly naming any
survivor. It is destructive and has no undo. A main-seed
worker-death resume arbitrates from the model's live row count on main
(allowlisted `target_ids` plus verified rows; only the two exact counts decide),
which assumes no other writer touches main mid-load — keep humans out during a
seed.
Floorplan geometry (`just geometry PLAN OUT`, `geometry-check PLAN OUT`, and
`GEOMETRY_WRITES=1 just seed-geometry OUT TARGET [RECEIPT]`) is a drift-style
sidecar over a frozen plan for the physical-geometry plugin: no canonical-graph
change, no rebaseline. Positions are authored-first — racks carrying
`meta.position_m` keep the estate's own validated cabinet-grid coordinates (cm
from a fixed origin, ordering-independent); only unpositioned rooms use the
derived row fallback, and mixing the two in one location is refused. Seeding is
fresh-only on resume too (only receipt-created or name-adopted rows are
tolerated), re-runs the plan-free invariants before any write, and the exact
readback verifies each shape's `dcim.rack` object_type, resolved rack id and
layer — never a claim about what any visualization renders.
Procurement history (`just lifecycle PLAN OUT`, `lifecycle-check PLAN OUT`, and
`LIFECYCLE_WRITES=1 just seed-lifecycle OUT TARGET [RECEIPT]`) is the same
sidecar shape for the Asset Lifecycle plugin: a BOM per site generated on the
target by site/role scope rules, POs from two fictional vendors with null unit
prices, deliveries dated from the equipment journals via a courier with no
tracking URL (never the builtin UPS/FedEx/DHL), plugin-action installs, and
spares pools in multi-cabinet rooms. Lifecycle rows protect sites and
locations and geometry shapes protect racks: remove them first with
`just unseed-lifecycle RECEIPT TARGET` / `just unseed-geometry RECEIPT TARGET`
(receipt-scoped, `*_WRITES=1`), then `teardown-main`. See docs/loading.md.
Reviewable TurboBulk loads require zero initial Branching ChangeDiffs and verify
the exact total and per-model create-ChangeDiff counts at the final readback boundary.
Pre-existing rows may be allowlisted only for declared kinds (`ALLOWLISTED_KINDS`:
builtin `module_type_profile` plus the Branching-exempt main-scoped kinds —
`owner`/`owner_group`, the custom_field/choice_set/custom_link definitions and
the automation export_template/webhook/event_rule records)
with plain-attribute identities disjoint from the plan, recorded exactly in the
receipt; a collision stays a hard block. The allowlist is assessed at preflight
and again at final readback, so concurrent neighbour loads are excused. Those
main-scoped rows survive branch deletion and create no branch ChangeDiffs
(`BRANCH_EXEMPT_KINDS` excludes those six kinds from the exact-count gate,
verified against the running Branching 1.2.1 EXEMPT_MODELS): disjoint namespaces
coexist on one target, and same-namespace leftovers need `just retire`, which
deletes them before the owner rows. `config_context` is deliberately absent from
both sets: `get_branchable_object_types()` lists `extras.configcontext`, so
config contexts are branch-scoped and keep their exact create-ChangeDiff count.
Automation records (config contexts, export templates, the webhook and its event
rule) have no Diode SDK 1.14.0 entity (`LOADER_ONLY_KINDS`), so the wire package
omits them, records the omission in its manifest, and only `just load` delivers
them; the Diode lane prints and records that partial delivery while still
binding the whole artifact's digest, and `just lab-verify`
(`--diode-delivered-only`) verifies a replayed target under the same
restriction. Keep the webhook endpoint reserved-invalid and its rule disabled:
this is inert inventory, never an executed integration.
The provider's three `netbox_bgp` records join that set for the same reason —
the SDK carries no plugin entity at all — and additionally take the REST create
path rather than a TurboBulk job: their routing-policy lists are many-to-many,
and an installed TurboBulk model registry need not expose a plugin's models
(read live from a pinned 4.7.1 tenant: 172 writable models, none of them
`netbox_bgp`). A target without the plugin refuses the load at the REST schema
preflight, before any write. No live receipt covers these three models yet.
The loader supplies three model defaults the raw bulk path would otherwise
manufacture invalidly (`location.status`, `power_outlet.status`, `rack.starting_unit`);
strict readback compares only emitted fields and does not verify them.
Write each bounded REST completion payload to the receipt before PATCH. Recover a
lost response only from exact target readback; never resend an unresolved mutation.
A lost zero-row finalizer response may adopt one exact core-job match inside its
recorded request window, but an unbound data-bearing job always requires a fresh branch.
A data job the orphaned-job reaper marked errored after worker death is arbitrated
on resume from exact per-model create-ChangeDiff counts: adopt when its transaction
provably committed, supersede and submit fresh rows when it provably rolled back,
and require a fresh branch for anything unexplained.
`just reset TARGET BRANCH` replaces only an exact disposable Branching branch and
archives its bound private load receipts.
Keep transport orchestration behind that recipe rather than adding an installed CLI.
Use `devenv --profile diode shell` to install/run the pinned SDK checks.
CI runs the full suite on Python 3.11/3.14, then generates and SDK-checks each
implemented composition, its scenario, the alternate vendor lines and optional
dual-stack across every profile. These offline jobs do not substitute
for the separately recorded pinned-target live qualification.

## Navigation

- [GOAL.md](GOAL.md): preserved objective, design/build/review milestones and final evidence index.
- [README.md](README.md): human quick start and complete documentation map.
- [docs/usage.md](docs/usage.md): generation, profiles and supported growth.
- [docs/modeling.md](docs/modeling.md): construction rules and connected detail.
- [docs/scenarios.md](docs/scenarios.md): change/defect walkthroughs and boundaries.
- [docs/loading.md](docs/loading.md): artifacts and Diode handoff.
- [docs/demo.md](docs/demo.md): the demo composer — one command from industry,
  vendor and feature packs to a loaded branch plus a DEMO.md cheat sheet.
- [docs/first-target.md](docs/first-target.md): operator runbook from empty target to loaded branch.
- [docs/recipes.md](docs/recipes.md): complete recipe key reference per profile.
- [docs/seeding.md](docs/seeding.md): zero-write target verification and restore-based seeding.
- [docs/qualification.md](docs/qualification.md): scale, limits and preserved history.
- [CONTRACT.md](CONTRACT.md): canonical graph, ledgers, units and module interfaces.
- [docs/schema-map.md](docs/schema-map.md): generated map of every kind to its NetBox
  model, endpoint, references, Diode identity and delivery path, plus the plugin
  sidecars; `just schema-map` regenerates it and a test fails when it is stale.
- [COVERAGE.md](COVERAGE.md): hospital/provider omissions and ranked next work;
  a reviewed backlog, not implemented scope.
- [catalog/README.md](catalog/README.md): pinned vendor sources, fictional hardware
  and the selectable vendor lines the `[hardware]` recipe key chooses between.
- `estates/bank.py`: bank demand and service policy; `estates/datacenter.py`:
  shared DC construction from resolved workloads; `blocks.py`: physical allocators.
- `estates/generate.py`: profile dispatch; `enterprise.py`: workload/replica policy;
  `intent.py`: resolved input provenance; `validate_datacenter.py`: independent DC checks.
- `estates/school.py`: district/classroom demand; `campus.py`: shared aggregation
  and room-local access; `validate_school.py`: independent district obligations.
- `estates/hospital.py`: keyed wards/clinics and demand-derived shared services;
  `validate_hospital.py`: independent care-unit and actual physical obligations.
- `estates/provider.py`: finite PoP growth, customer private-L3 services and real
  NOC handoffs; `validate_provider.py`: independent topology, ownership and flow checks.
  The backbone follows the map: owned metro dark-fiber rings plus two diverse
  leased spans per neighbouring metro (Milwaukee–Chicago–Detroit–Cleveland),
  append-only in the `provider-backbone-spans` ledger; carrier-owned numbering is
  RFC 5398/5737 documentation space under `ARIN`; one launch/onboarding
  timeline. See [geography and numbering](docs/modeling.md#provider-backbone-geography-and-numbering).
- `estates/bgp.py`: the provider-only BGP inventory — named routing policies,
  peer groups and one session per modeled adjacency (iBGP route-reflector pair
  in two metros, eBGP transit and eBGP customer, each with an IPv6 twin when
  dual-stack), all attributed from the finished graph and
  all documentation records; `validate_provider.py` independently re-derives
  them and refuses any record that claims configured or established routing.
  Pinned by `tests/test_provider_bgp.py`.
- `estates/discovery_lab.py`: the provider-only network lab (`discovery_lab`
  recipe key, off by default) — Nokia 7220 IXR-D2L lab routers in a NOC
  Network Lab room mirroring the first PoP's wiring, which
  `lab/discovery/render.py` reads from the plan; `validate_provider.discovery_lab`
  checks it as a closed slice. Pinned by `tests/test_discovery_lab.py`.
- `estates/retail.py`: store-format fleet, distribution centres and shared commerce
  services; `validate_retail.py`: independent format, segment, radio and WAN checks.
- `estates/university.py`: one campus of keyed academic buildings, residence halls
  and a library with permanent room positions and dense per-zone wireless;
  `validate_university.py`: independent room-ledger, endpoint, radio and WAN checks.
- `estates/msp.py`: one NOC operating keyed customer accounts, each its own
  tenant with its own offices, address space and segment routing contexts;
  `validate_msp.py`: independent pod-ledger, endpoint, tenancy-isolation and
  ownership-versus-operation checks.
- `estates/manufacturing.py`: keyed plants with separated plant-floor (OT) and
  corporate (IT) zones, their own distribution tiers and one modeled conduit;
  `validate_manufacturing.py`: independent room-ledger, endpoint, zone-isolation
  and conduit checks.
- `estates/utility.py`: keyed substations with a separated station (OT) zone and
  corporate (IT) tier behind one modeled conduit, plus one or two control centers;
  `validate_utility.py`: independent bay-ledger, endpoint, zone-isolation and
  conduit checks.
- `estates/places.py`: authored geography, building/room placement and cable routes.
- `estates/equipment.py`, `networking.py`, `operations.py`: connected model families;
  `operations.finalize` is the graph-wide list-view hygiene pass
  ([modeling](docs/modeling.md#list-view-hygiene)), pinned by `tests/test_estate_hygiene.py`.
- `estates/optics.py`: reviewed installed optical parts and captive AOC ends;
  [optics policy](docs/modeling.md#installed-optics-policy), offline/SDK evidence in
  GOAL.md; pinned local qualification in `lab/README.md`.
- `estates/ipv6.py` and `validate_ipv6.py`: shared dual-stack allocation and
  independent finished-graph obligations; operation in
  [IPv6 guide](docs/modeling.md#optional-ipv6), pinned live receipts in `lab/README.md`.
- `estates/operations_context.py`: shared scoped contacts and immutable dated notes;
  `validate_operations.py` independently checks their actual scope and claims.
- `estates/automation.py`: the shared automation pack every estate carries —
  two config contexts (one carrying the estate's own service endpoints), two CSV
  export templates, and an inert webhook with its disabled event rule;
  `validate_operations.py` independently checks the endpoints against the
  services that bind them, the scopes, the template shape and the inert wiring.
- `catalog/type-coverage.json`: pinned SDK/native audit; builds derive `coverage.json`.
- `estates/scenarios.py`: acquisition/refresh candidates and before/after checks.
- `estates/power_scenario.py`: property-selected shared defect, exact finding and restoration checks.
- `estates/span_scenario.py`: provider-only status maintenance, actual premise
  attribution and directed headroom; sample `profiles/provider-maintenance.toml`.
- `estates/demo.py`: the demo composer — per-profile demand templates, vendor
  shorthands, feature packs and the DEMO.md cheat sheet; `just demo`.
- `estates/drift.py`: the Assurance discovery-drift twin — property-selected
  observed drift, its exact expected deviation manifest and the SE walkthrough;
  `just drift PLAN OUT` and `just drift-check OUT`.
- `estates/geometry.py`: the floorplan-geometry sidecar for the
  physical-geometry plugin — authored-first (`meta.position_m`) rack layouts
  with a deterministic row fallback, bound to a plan's SHA, plus the
  fresh-only REST seeder with exact protected-field readback;
  `just geometry PLAN OUT`, `just geometry-check PLAN OUT`,
  `just seed-geometry OUT TARGET [RECEIPT]`.
- `estates/lifecycle.py`: the Asset Lifecycle procurement sidecar — BOMs,
  purchase orders, deliveries, installs and spares pools derived from a frozen
  plan and bound to its SHA, plus the resumable REST seeder with exact
  readback; `just lifecycle`, `lifecycle-check`, `seed-lifecycle`. Pinned by
  `tests/test_lifecycle.py`.
- `estates/validate.py`: independent assertions; add a failing mutation check when extending them.
- `estates/diode.py`: bounded wire export and optional SDK qualification.
- `estates/load.py`: target discovery, transport selection and remote Diode phase checkpoints;
  `estates/turbobulk.py`: the bounded TurboBulk/REST adapter, including the
  29-kind Cloud qualification and the full 105-kind contract (every current profile,
  the complete bank included), live-qualified
  only on the pinned local 4.7.1 stack; Cloud/Enterprise remain unqualified.
- [lab/README.md](lab/README.md): disposable Colima/Compose target and live checks.
- [lab/discovery/README.md](lab/discovery/README.md): real-discovery lab — the
  plan's own network lab (`discovery_lab = true`) rendered as SR Linux containers
  (own `genial-discovery` Colima VM) for a real orb-agent; the lab is its own
  honestly-typed slice, never matched against the MX204 records it mirrors.
  `render.py --check` proves an orb-agent dry run differs only by its documented
  `drift.json`.

## Design boundaries

- Profiles express demand. Shared builders allocate equipment, ports, addresses,
  racks, and connections; independent checks inspect the finished graph.
- DC workload slots are explicit and independent of input ordering. Bank slots
  stay fixed; enterprise workload slots persist in the reservation ledger.
  Enterprise groups can grow and new workloads/sites can be added; reductions
  or changes to existing resource/replica policy require a new baseline.
- Enterprise replicas use explicit host/rack lanes. Rack policy also separates
  paired fabric/WAN devices. Validate actual paths independently of contracts.
  Resource reserve is exact decimal headroom, not a spare-host recovery guarantee.
- All profiles support baseline and loss-of-power-diversity demo intent. Planning
  produces a healthy baseline; generation/build creates both scenario snapshots.
  `intent.json` labels supplied/default values, or unknown frozen-plan provenance.
- Provider additionally supports `provider-span-maintenance`. Only one used
  active leased Circuit becomes offline; every other record, ledger and physical
  identity stays exact. Keep installed optics and their power reserves present.
  Attribute customer-to-hub paths and directed kbps from actual references; do
  not copy capacity metadata, combine opposite directions or substitute CIR for
  offered traffic. Current maintenance flows must fit; further-failure findings
  express reduced protection, not a present outage. Recompute their exact
  code/object multiset and inverse; an empty further-failure set must not become
  an unconditional reduced-protection claim. The existing writer/checker dispatches this
  envelope without a new loader or graph framework. Status updates require a
  baseline/change/repeat/restore/repeat target sequence before live claims.
  Carrier-wide failure is outside this contract; show actual span providers per
  PoP without promising carrier diversity. External-transit journals must not invent
  a remote interface or owner, unlike real two-site handoff records.
- The demo composer orchestrates and never bypasses. `estates/demo.py` calls
  the same entry points the Justfile recipes run — generate, check, load-check,
  drift/drift-check, scenario/scenario-check, branch, load, verify-target —
  with the argv those recipes build, and any non-zero exit stops the compose
  before a cheat sheet exists. It adds no loader, kind, validation path or
  recipe key, and its per-profile templates are ordinary recipes an operator
  can edit and grow. DEMO.md is assembled only from the finished graph, the
  step receipts and the profile guides' own anchors; bound artifacts such as
  `drift.md` are linked, never restated. Keep it byte-deterministic for
  identical inputs, with wall clock confined to a fenced timings section and
  `compose.json`. `--features assurance` requires campus access, so the
  enterprise data center is refused at the flag. Never let it claim live
  behaviour an offline check did not prove.
- The Assurance drift twin is a subcommand over a frozen healthy plan, never a
  recipe key: `drift` writes a separate artifact bound to that plan's SHA and
  never mutates the baseline. Drift subjects are property-selected — the first
  eligible site in permanent allocation order, then the first eligible switch,
  ports in numeric-aware name order and endpoints by append-only room-ledger
  slot — so growth, in place or site-appending, keeps the same subjects.
  `observed/` is a projection of the plan: only drifted records are emitted, and
  every other record exists to resolve nested identities. A drifted record must
  keep its documented Diode matching identity, a created record must not reuse
  one, and every emitted *and nested* kind must sit inside the reviewed NetBox
  4.6 allowlist. Diode expresses create and update only and its IngestRequest
  carries no tombstone, so documented-not-observed items emit nothing and are
  declared `requires-target-side-comparison` rather than faked. Expected
  deviations are a prediction from the public changeset contract; never claim
  live Assurance behaviour, review-versus-auto-apply mode or matching outcomes
  without target-side evidence. `drift-check` recomputes the whole manifest from
  the bound baseline, rebinds every observed wire byte and re-renders the
  walkthrough. The observed payload is an ingest artifact, not a loadable estate.
- Scenario verification checks the exact code/object findings and inverse change.
  Saved scenario checking re-exports both plans and binds every wire file to its graph.
  Ordinary validation must reject the defective snapshot; never add a blanket
  ignore-errors path. Cable rewiring is a fresh-target snapshot, not an executable
  Diode transition. Keep live receipts separate from offline restoration proof.
- School access uses persistent endpoint ports in switch pairs per serving room.
  School radio channels use stable AP keys. Growth must not reroute existing
  endpoints or reroll channels. Reductions, classroom design changes and campus
  WAN renewal need a new baseline; classroom/admin/lab counts can grow.
- One university campus is one estate: buildings, halls and the library are
  separate sites inside a single authored metro, served by one campus DC, not the
  shared two-DC pair. Each building room holds a permanent reserved position, so
  appending lecture halls never renumbers existing labs or offices. Eight floors
  and 38 access switches per building are the reviewed ceilings. Residence-room
  ports are installed capacity, never resident-owned devices; identity and WLAN
  records use eduroam-style naming only, with no authentication protocol
  configured. Growth may append buildings, halls, rooms, seats and radio budgets;
  reductions, room-port design changes and campus WAN renewal need a new baseline.
- One manufacturing plant is one site with two forwarding zones. Plant-floor
  (OT) endpoints sit on their own `process` and `supervisory` segments, in
  their own routing contexts, behind their own access pair and their own
  distribution pair; corporate (IT) endpoints keep the ordinary campus
  grammar. The only modeled forwarding path between the tiers is the `conduit`
  segment trunked between the two distribution pairs; each device's dedicated
  management port and the room's shared serial console server are the two other
  declared crossings. Validate all three independently of emitted contracts —
  per-port allow-lists (mgmt_only gating), console cables pinned to the room's
  own console server, and no record of any kind outside a zone naming that
  zone's VLANs, interfaces or addresses. Both tiers
  share the one plant equipment room: the boundary is modeled in the routing
  and VLAN graph, never physical, never enforced, and never a Purdue-model or
  IEC 62443 claim. No industrial protocol is configured, carried or asserted
  anywhere, and plant-floor endpoints are reference inventory with no control
  function, safety rating, certification or firmware. Line, dock and desk
  counts are installed capacity, not output, throughput, OEE or takt time.
  Every line cell, loading dock and office pod holds a permanent reserved
  ground-floor position, and the two access zones keep separate port ledgers
  while sharing one finite distribution attachment budget. Growth may append
  plants, lines, docks and desks; reductions and WAN renewal require a new
  baseline. The data-center edge is sized from resolved demand every
  generation, so it is never a frozen purchase that blocks growth.
- One electric utility is one or two control centers plus N substations. A
  substation is one control house with two forwarding zones: station (OT)
  endpoints — one remote terminal unit and one protection relay per bay, two
  station HMIs and a gateway — sit on their own `protection`, `telemetry` and
  `station` segments, in their own routing contexts, behind their own access
  pair and their own distribution pair; a minimal corporate (IT) presence keeps
  the ordinary campus grammar. Reuse the manufacturing zone machinery: the only
  modeled forwarding path between the tiers is the `conduit` segment trunked
  between the two distribution pairs, with the dedicated management ports and
  the control house's shared serial console server as the two other declared,
  validated crossings. Validate all three
  independently of emitted contracts, including that no record of any kind
  outside a zone names that zone's VLANs or binds its ports and addresses. Bays
  hold permanent reserved positions and are installed equipment positions only:
  never voltage classes, electrical ratings, bus arrangements or breaker
  positions. Nothing claims NERC CIP compliance, an electronic security
  perimeter, SCADA/EMS execution, any utility protocol (DNP3, IEC 61850, Modbus,
  ICCP, IEC 60870-5), protection logic or relay settings, grid topology,
  power-flow, generation/load figures, or physical security; the estate is
  documentation inventory, not a critical-infrastructure claim. No wireless of
  any kind is modeled. Two control centers are the existing multi-DC replica
  grammar, never a demonstrated failover. Show actual span providers per
  substation without promising carrier diversity. Growth may append substations,
  bays and the backup control center; reductions, kind changes and WAN renewal
  require a new baseline. The control-center edge is sized from resolved demand
  every generation, so it is never a frozen purchase that blocks growth.
- One managed service provider is one NOC plus N separate customer estates. The
  NOC contains its own machine room; a second operations DC is deliberately not
  modeled, because this grammar has no owned-fiber interconnect to join it
  honestly. Each customer is one tenant inside one customer tenant group and
  owns its office sites, rooms, racks, equipment, VLANs, prefixes, addresses,
  WLANs and access circuits, plus its own per-segment routing contexts. No
  cable, segment, VLAN, prefix, address, routing context or service may join two
  customers, and the NOC carries no customer tenancy; validate that isolation
  independently of emitted contracts. Operation is expressed only through
  records that already carry it: the direct technical assignment chosen by
  equipment role and actual tenant, the per-customer management segment, the
  shared infrastructure owner and the provider-held carrier accounts. Do not add
  a remote-access path, management overlay, SLA, ticket, agent or entitlement
  record. Managed-office WLANs depend on the provider's own DNS/RADIUS listener
  inventory; that serving-tenant indirection is the only cross-tenant reference.
  Staff pods hold permanent reserved ground-floor positions, so hiring appends
  desks and then a pod without renumbering. Growth may append customers,
  offices, desks and radio budgets; reductions and WAN renewal require a new
  baseline. The NOC edge is sized from resolved demand every generation, so it
  is never a frozen purchase that blocks growth.
- Facility kind controls rack geometry; school campuses must not inherit DC
  cabinet grids. District sites share one authored metro. Explicit role offsets
  distinguish staff, students and AP management without changing bank addresses.
- Equipment rooms are rooms, not corridors. The authored cabinet grid bays
  0.6 m cabinets four to a row, repeats rows every 2.4 m, and separates the
  network and compute zones by one 1.5 m aisle from fixed per-zone origins, so
  appending a cabinet never moves an existing one. Independent DC checks reject
  intersecting 0.6 × 1.07 m footprints and disproportionate room layouts, not
  just duplicate coordinates; coordinates are rounded so the plan, the checks
  and the geometry sidecar's centimetre conversion all agree. Cabinets are
  sized to content: enclosed four-post 24U (AR3104) for the ten-device lane that
  mounts contiguously from the bottom rail — 42U would read three-quarters
  empty — and the small-room kit (13U Panduit R2P26 two-post, one 1U 120 V
  AP9563 at the top unit, one 120 V / 20 A circuit, no console server) for a
  single-CE premises (`Site.small_kit`, provider customers). PoP cabinets add
  real passive content, one FCE1U fibre enclosure per PE cabinet — never
  padding. `facility_id` is a room-scoped cabinet code (`DH-02-C03`,
  `G09-01-N02`); 0U equipment is racked
  without a position, exactly as NetBox models it. US feeds are 208 V / 20 A
  single-phase (the AP9572 is a 16 A PDU); only the provider validator's own
  premises list may pass `single_feed` to `validate_power`. Device types carry `airflow`
  only where the pinned source declares it (Cisco C9200L-24P-4X and FortiGate
  100F declare none); our own reference designs state it as authored fiction.
- Hospital wards occupy permanent reserved floors; beds and desks are installed
  capacity, not patient throughput or staffing counts. Keep medical, imaging and
  clinical roles/segments distinct, with site-scoped biomedical contacts. Seven
  wards plus ground consume the current eight management attachment limit.
  Growth may append wards/rooms/endpoints; reductions and WAN renewal require a
  new baseline. Local managed/guest device demand grows stable AP mounts and
  power-aware access pairs. Guest uses open access intent, with no portal or
  clinical execution.
- Provider work follows the independently reviewed contract under
  `build/goal-richness/provider/design-contract.md`: finite physical PoP ports,
  real two-site circuits and permanent growth.
  Provider allocation slots count /24 units; the fixed NOC occupies the first
  /16 and routed links/loopbacks reserve the final /16. Existing profiles retain
  their allocation units. Shared DC WAN attachment is an internal builder hook;
  the independent provider validator must check every NOC /31/circuit/PE path.
  Customer spoke-to-hub capacity is a finite declared flow model, not total
  backbone/NOC/transit traffic. Since 0.16 PE loopbacks (from host .1, never a
  /24's network address), PoP pair links and inter-PoP spans use carrier-owned
  RFC 5737 space and sit in the global table (no VRF, as Junos inet.0); each
  transit /31 is numbered by its upstream from a recorded assignment outside
  every operator aggregate. The final /16 holds only management, NOC and
  customer access /31s. Management is the Carrier Management VRF (`vrf/provider`,
  `<ASN>:9000`) joined to every customer VRF by a hub/spoke extranet
  (`<ASN>:9000`/`:9001`) so the NOC reaches CE management; PE `fxp0` is cabled
  and addressed there, and each PoP console server has an independent broadband
  out-of-band circuit on NET2 in its own IPv4-only VRF. With IPv6 every routing
  context owns its own routed /64. Same-metro spans are owned dark fiber (no
  commit); inter-metro spans are 100G wavelengths on the 100G port; a NOC
  handoff into another metro is a leased 1G private line. MX204 1G handoffs keep
  `xe-` names (docs/modeling.md cites the source). Premises are placed in their
  serving PoP's area (within 25 km, nearer it than any same-metro PoP allocated
  before them), so name, address and homing agree. `lan_endpoints = 0` is a
  CE-only premises managed on a /32 loopback; growing it to desks needs a new
  baseline. Customer desks answer from the customer's own domain and PoP
  facilities desks are the carrier hotel's remote hands. Catalog additions require an explicit baseline;
  preserve hospital source/artifacts and historical live evidence.
- Provider BGP records (`estates/bgp.py`, since 0.14.0) are inventory, never
  execution. They document intended peerings so the `netbox_bgp` tables and the
  `bgp-topology` view describe the same estate the rest of the graph does.
  Nothing is configured, applied or established: no convergence, session state,
  route exchange or policy evaluation may be claimed in code, descriptions,
  docs or contracts. The records themselves carry operational fields only — no
  disclaimer prose (see the record-text rule below); the limitation is stated
  in docs/modeling.md and the report. The reviewed kind set is closed to
  routing policies, peer groups and sessions and the field set to name,
  description, status and weight (`validate_provider.BGP_FIELDS`); a policy
  *rule*, community, prefix list or session-state field would read as
  configuration and the validator refuses it, and the named policies therefore
  carry no rules. Every field is attributed from the
  finished graph — PE `lo0` addresses, the transit and access circuits' own
  terminations and cables, and the ASNs sites already reference — never
  invented per site. A transit peer keeps `remote_prefix` on the real /31
  rather than an invented remote address, because the remote interface and its
  owner belong to the upstream. iBGP is a route-reflector pair — PE A at the
  first PoP in the permanent `provider-pop-order` ledger and PE A at the first
  later PoP in another metro (since 0.16) — not a full mesh: linear growth keeps
  the 64-PoP ceiling bounded (a full mesh would be 8,128 sessions there) and
  appending a PoP or customer appends sessions without moving an existing one.
  The three `netbox_bgp` models have no Diode SDK entity, so they are
  `LOADER_ONLY_KINDS` delivered only by `just load`, over the REST create path.
  Live loading of these plugin models is unqualified; see docs/loading.md.
- The provider network lab (`estates/discovery_lab.py`, `discovery_lab` recipe
  key, off by default, since 0.16.0) is a staging replica, never the production
  routers: Nokia 7220 IXR-D2L records whose model, serial (`Sim Serial No.`)
  and platform (`NOKIA_SRL v26.7.2`) are exactly what the SR Linux container
  reports, so real discovery matches them, with comments naming the production
  router each mirrors. It lives in a NOC `Network Lab` room, uses only RFC 2544
  `198.18.0.0/15` addresses with no VRF, and carries no circuit, BGP session,
  power, console or production cable. It is selected from the permanent
  `provider-pop-order` ledger and cabled from the finished graph's routed /31
  adjacencies, never invented. `validate_provider.discovery_lab` checks it as a
  closed slice by role, references and addresses (never meta), then removes it
  so production checks see the estate exactly as with the lab off; extend that
  check and its failing mutations rather than exempting lab records elsewhere.
  `lab/discovery/render.py` reads the lab FROM THE PLAN — never re-derive it
  there. Enabling, disabling or resizing the lab requires a new baseline.
- Shared DC power checks derive hardware and supply allowances from actual
  device-type references; removing descriptive hardware metadata cannot skip them.
  A PDU's own input port carries the totalled draw of the inlets cabled to its
  outlets, computed after PoE and optics enrichment so it includes those
  allowances. Independent checks compare those declared totals at the rack
  boundary, not per PDU, so the power-diversity defect's single-cord move stays
  a redistribution rather than a new finding; reports must not count a PDU input
  again in its rack or PSE totals. Growth may raise a PDU input, never lower it.
- Shared PoE checks follow actual PD copper paths, compatible supply modules and
  feed ownership. Keep port headroom separate from surviving-supply power budgets.
  Growth may increase PSE inlet draws; it must retain old port/cable identities.
  Local wireless zone counts use explicit frozen defaults and an authored
  32-device/AP threshold, not RF or association evidence. Radios carry WLAN
  membership and take their authored non-overlapping channel plan from the
  catalog's declared band for that radio, never from a fixed 5 GHz assumption. Managed demand is aggregate AP sizing, with no per-SSID address
  admission check; guest demand must fit its explicit local segment. VLANs belong
  to WLANs and actual wired trunks.
- Optical fit derives from actual device type, named physical cage, configured
  speed and complete local media path. Module-type attributes and contracts are
  explanatory only. Empty types must not replicate interface templates. AOC end
  records share one cable-derived serial, with the existing cable label/key
  preserved and assembly serial in comments. Never count two end records as two
  assemblies. Native InterfaceSerializer accepts foreign-device modules and the
  SDK permits overlong serials: independent checks must enforce ownership and
  native field limits. Count all installed optic/end power reservations once per
  host, separately from chassis and PD allowance. Stop tracing at carrier handoffs;
  no optical-loss, observed FEC or remote-hardware claim follows from local reach.
  The operator's own circuits (`provider/operator`) are not a handoff: the optic
  must reach the circuit `distance` (else its two sites' route distance) and the
  builder picks the shortest reviewed reach that covers it (`optics-span-reach`).
- Shared campus/DC physical checks receive independently resolved school demand.
  Descriptive metadata and omitted contracts cannot erase required infrastructure.
- Bank DC validation derives complete power/compute/service obligations from
  recipe and observed inventory. Keep emitted contracts accountable; omissions
  must not disable checks. Inactive required paths cannot count as healthy capacity.
- Preserve stable identities and reservations when growing an existing plan.
- Optional `ipv6_pool` is common to every profile; absence preserves IPv4-only
  output. Accept aligned documentation /32–/40 pools only. Enabling, disabling
  or changing the pool requires a new baseline. Allocate separate stable IPv6
  slots: /48 sites, /64 LANs, /127 router links, /128 PE loopbacks. Bank radio
  diagnostics use /64; FHRP stays IPv4-only and unknown transit peers stay unknown.
  Primary/service references express inventory, not executed IPv6 configuration.
  Keep IPv4-only report output unchanged and qualify dual-stack loading separately.
- Recipe `name` is fixed during growth: the provider uses it in Diode matching
  identities. Renaming requires a new baseline for every profile.
- Ordinary descriptions use operational wording and reference hardware labels.
  Keep provenance, planning assumptions and material limitations (unverified RF,
  external ownership, inert automation, documentation-only BGP, OT/clinical
  reference inventory) in the catalog/contracts/report and docs/modeling.md.
  The final v0.8 reference-label revision changes the catalog digest and requires
  a new baseline; archived intermediate v0.8 plans remain historical evidence.
- Verify a reused frozen plan reproduces from its recipe and ledgers before
  allocation; a well-formed ledger can still disagree with its saved graph.
- Persist design assignments; explicit acquisition/refresh transitions must retain
  endpoint addressing and leave other sites unchanged. Do not disguise identity
  changes or candidate deletions as an executable Diode migration.
- Derive local variation from stable keys; never use a process-global RNG stream.
- Fail unsupported demands with actionable errors. Never wrap allocations, invent
  hardware ports, or quietly reduce requested resilience.
- Separate verified hardware facts from explicit fictional design assumptions.
- Export bounded Diode requests. No custom ingestion/reconciliation engine.
- Default to direct access channels. The v0.7 whole-package target is NetBox4.7;
  full patch mappings need the local bridge below until upstream support exists.
- Full NetBox4.7 panel qualification may use only the explicitly opt-in,
  source-hash-guarded local plugin bridge in `lab/patches/`. Keep its patch identity
  in receipts and verify running sources; do not imply official compatibility.
- v0.7 is a new baseline: connected equipment, networking and operations families
  broaden the demand-sized WAN and building model. Preserve older estates with
  separate namespaces and strict readback allowlists unless the user requests a
  clean local reset. `just lab-reset` clears both Compose projects' volumes and
  replaces the readback token; saved artifacts and configuration remain. Never
  reuse old target-ID receipts after a reset. Archive predecessor source before edits.
- Shared operational owners need a real owner_group reference; the pinned REST
  serializer rejects omission. Validate that obligation without emitted contracts.
- Automation records are unconditional shared enrichment, like contacts and
  journals: no recipe key selects them. A config context may cite only addresses
  the estate's own service listeners bind, derived from the finished graph and
  rechecked independently; growth may append a workload's endpoints but must
  never reroll an existing one or rename a record. Export templates must render
  on object types the estate actually emits. The webhook is inventory only — an
  https reserved `.invalid` endpoint whose event rule ships disabled — so nothing
  is ever sent; never point one at a reachable host or enable the rule. Config
  contexts, export templates, webhooks and event rules configure nothing: they
  are documentation intent, not applied device or NetBox behavior.
- Capture `just lab-bootstrap <new-receipt>` before the first load of a fresh
  prepared target. It permits only the nine expected bootstrap identities across
  all supported endpoints; keep its IDs fixed for initial/repeat readback and
  discard it after reset. A populated target must fail bootstrap capture.
- Local replay checks global failed ingestion logs; recovery requires correction,
  a new artifact, and the documented disposable reset/full-load workflow.
  Preserve failed receipts; do not clear logs or manually repair estate records.
- Short device names require site/tenant in every Diode reference; DNS retains
  the full namespace. Serial numbers are not a replacement for matching identity.
- Rack asset tags (since 0.16) are `<NAMESPACE>-<nnnnn>`: the uppercase
  namespace (at most 20 characters) keeps NetBox's global uniqueness across
  coexisting estates and the permanent `asset-tags` ledger numbers racks in
  creation order, so growth appends. Room scope lives in the `facility_id`
  cabinet code. Validate global tag uniqueness before export.
- Display naming (`estates/naming.py` is the single policy home since 0.12.0;
  the sweep became exhaustive in 0.15.0): every object family emits an authored,
  namespace-free `name`; identities — slugs, object keys, device names, circuit
  `cid`s, asset tags, DNS, `account` numbers, WLAN `ssid`s and the virtual-chassis
  `domain` — keep the stable `<namespace>-…` form and remain the only cross-estate
  separator. The visualization layer truncates graph node labels, so a prefixed
  display name rendered several distinct objects identically; readable names are
  functional, not decoration. Where NetBox gives a model no `name` at all (ASN,
  Aggregate) the rendered label is the `description`, so that is authored too and
  names the party that actually holds the record — a carrier AS whose label read
  `transit-a` contradicted the provider named everywhere else.
  Two exception sets keep the prefix, each entry carrying its reason in
  `naming.py`: `NAMESPACED_KINDS` (owner/owner_group and the automation
  export_template/webhook/event_rule plus custom_field/choice_set/custom_link
  records), because those main-scoped rows coexist across namespaces on one main
  and `just retire` matches them by exact `"<namespace> …"` prefix — except
  under the recipe's `tenancy = "dedicated"` (solo-tenant mode,
  `naming.main_scoped_name`), which drops the visible prefix for a tenant one
  estate owns and retires/tears down by the exact bare labels in
  `branch.RETIREMENT_LABELS` (custom-field names and root ContactGroups keep
  the namespace: both are identities); and
  `IDENTITY_NAMED_KINDS` — the root ContactGroup, whose canonical slug is derived
  from its name and omitted on the wire for the auto-slug matcher, and
  `route_target`, whose name IS the `<asn>:<n>` route distinguisher NetBox holds
  globally unique. Config contexts are branch-scoped
  (`get_branchable_object_types()` lists `extras.configcontext`), so unlike the
  other automation records they carry an authored name.
  Power panels are named from their room and side, not the namespaced site stem;
  power feeds (since 0.13.0) are named from their cabinet and side, which is
  unique inside the room-and-side-scoped panel NetBox keys them on. Everything
  built from a site reads `Site.display` / `naming.site_display`, never
  `Site.name` — that attribute is the namespaced slug stem.
  `tests/test_naming_policy.py` pins every half of the split and is INVERTED:
  it sweeps every emitted kind that has a `name`, minus the two exception sets,
  so a family added later is covered by default. 0.12's curated opt-in tuple is
  what let VRFs, FHRP groups, tunnels, IKE/IPsec records, config contexts,
  contacts, clusters, VLANs and the ASN/aggregate descriptions leak for three
  releases. A prefix check alone cannot separate the namespace `northgate` from
  the authored tenant `Northgate Power and Light`, so the all-profile sweep
  regenerates each estate under a second namespace and requires every authored
  name to be byte-identical: a leaked label moves with the namespace, an
  authored one cannot. Authored names are composed from tenant and site display
  names a recipe controls, so `Workspace.finish` rejects any name over its
  native limit (`naming.NAME_LIMITS`: 64 for `vlan` and `virtual_chassis`,
  read back from the pinned 4.7.1 source; 100 otherwise) naming the object,
  rather than letting a target reject the row mid-load.
- Records carry operational data only (0.16). No NetBox record — name,
  description, comments, label, module attributes or journal — may carry a
  disclaimer, provenance note or self-reference ("not verified", "no … is
  claimed", "documentation inventory", "fictional", "placeholder", "planning
  intent", "unknown owner"…): both showcase reviewers called those the loudest
  synthetic tell. Limitations are documented in the repo (docs/modeling.md,
  profile guides) and the generated report; `naming.DISCLAIMER` backs the
  `record-disclaimer` validation finding with a failing-mutation test. The
  substantive safety properties stay enforced as structure, not prose: the
  webhook endpoint stays reserved `.invalid` and its rule disabled, the BGP
  kind and field sets stay closed, and nothing models session state. Device
  roles, segment purposes and rates come from `naming.ROLE_LABELS`,
  `SEGMENT_PURPOSES`, `bandwidth` and `port_speed`. Provider premises and PoP
  hostnames are readable stems (`<customer>-<metro3><slot>`, the PoP key);
  provider customer VRFs carry an RD equal to their `<asn>:<n>` route target;
  contacts carry 555-0100..0199 lines in their metro's real area code; the
  estate tag is `Managed`. The private-L3 virtual-circuit note points at the
  CE-to-PE BGP sessions and must never deny them. Every VLAN is
  named for its segment alone (names are unique per VLAN group, and every VLAN
  sits in its site's group). Every prefix and VLAN carries an `ipam.Role` from
  `naming.IPAM_ROLES`, derived by `naming.prefix_role`/`SEGMENT_ROLES` and
  re-checked (`ipam-role`); a new segment must be mapped there or generation
  fails. Journals state rates via `naming.rate_kbps`, never raw kbps.
- Site naming: authored display names, facility codes and anchor-placed
  synthetic coordinates are the default (`naming = "authored"`, since 0.10.0;
  anchors since 0.16). Addresses (0.16) use a real street of the site's anchor
  (`places.ADDRESS_STREETS`) and a site-id-hashed number — Chicago's grid numbers
  it from the coordinate — never a sequential street. Facility codes are per
  metro from a permanent ledger (`CHI01`); provider PoPs and the NOC carry
  fictional CLLI-style codes (`CHCGILCR`). Rooms of single-floor kinds
  (`places.FLAT_KINDS`) hang from the site without pass-through building/floor
  levels; a PoP is `Suite NNN` → `Cage X00`. Coordinates sit at most ~400 m from an authored
  `places.ANCHORS` point or street run that was verified on land in its
  municipality; a site named after a neighbourhood, suburb or street sits there
  and its address names that municipality. Never reintroduce free metro-wide
  jitter: it put lakeshore sites in the lakes and in Windsor, Ontario;
  `tests/test_places.py` water polygons guard it;
  `naming = "legacy"` restores namespace-ordinal names and `[site_names]`
  overrides any site by id. Slugs, DNS, device names and matching keys keep the
  stable namespace form; name pools hash the site id (never the seed or other
  sites), so growth cannot rename and seeds cannot reshuffle. Workspace.finish
  enforces global display-name uniqueness. House numbers follow position, never a
  hash: the Chicago and Milwaukee County grids, else real `places.STREET_REFS`
  points; `unique_addresses` keeps full addresses unique by allocation order.
  Never rename objects in a running estate; naming keys are rebaseline-frozen.
- Recipe `hardware` selects the vendor line for exactly three role families:
  `access`, `leaf` and `ap`. `catalog/hardware_lines` declares each family's
  default and alternates; an unknown family or vendor is a hard error listing the
  real choices. Resolution happens in one place — `World.hardware_alias` for
  builders, `selected_alias` for independent checkers — so profiles keep naming
  families and never a vendor. Builders and checkers must read switch port, PSU,
  PoE, stacking and optical-cage names from the resolved catalog entry, never
  from a literal vendor interface name. The `ap` family is the exception: shared
  builders address every AP line as `eth0`/`wlan0`/`wlan1`, a declared
  normalization of vendor labels pinned by test, and `ap = "aruba"` carries the
  AP-505's real 5 GHz + 2.4 GHz split, so the `wlan1` WLAN moves bands — say so
  at every operator decision point. An alternate line must meet or beat the model it
  substitutes on every quantity a resolver binds, proven from the catalog in
  `tests/test_hardware_lines.py`, and must carry complete equivalent PSU, PoE and
  reviewed-optics data. The selection is rebaseline-frozen; adding a line changes
  the hardware digest and needs a new baseline for every profile. New catalog
  models still require pinned real sources or clearly labeled fiction, and
  normalized interface names are a labeled deviation, never a vendor claim.
- HQ staff demand determines office floors and equipment rooms. Allocate access,
  management and power locally; check the actual copper paths and fiber backbone.
  Rack names may repeat across rooms; rack references and `facility_id` retain room scope.
- Keep ordinary growth stable; changing headquarters_staff needs a new baseline
  until an explicit building-remodel transition is implemented.
- WAN CIR is purchased capacity; the physical handoff is separate. Choose tiers
  with exact decimal reserve arithmetic, and verify actual connected capacity.
  Retained Birch WAN contracts survive access-hardware refresh. Changing WAN
  tiers requires a new baseline until a renewal transition is implemented.
- `reservation_user` binds an existing target account; it is never provisioned.
  Check numeric global identities (ASNs, FHRP groups) and aggregate overlaps
  before coexistence loads. Expanding an aggregate requires a new baseline
  until an explicit aggregate expansion transition exists.
- Keep root ContactGroup slugs canonical but omit them on wire to enable the
  pinned plugin auto-slug matcher; reject conflicting slug intent and old faulty
  exports. Matchers may choose the first duplicate: strict readback must reject ambiguity.
- Record what was checked; offline checks do not prove live NetBox acceptance.
- Report/intent compatibility follows the pinned export policy. Keep historical
  front-port mapping evidence separate from current whole-package assumptions;
  render clearer guidance without mutating preserved canonical plans or exports.
- Local qualification uses the native SDK replay helper, bounded reconciliation
  barriers, and separate REST readback. Keep this harness scoped to the pinned
  disposable target; never infer Cloud/Enterprise compatibility from its result.
- For Cloud qualification, record Assurance review versus direct auto-apply mode.
  Treat an ingest acknowledgement as acceptance only; require deviation evidence
  or REST readback before classifying downstream behavior.
- Keep runtime credentials under ignored private `build/local-target/`; do not
  print resolved Compose configuration or credential-bearing environments.
- Keep source data licensed and attributed. Do not copy internal Atlas/Lumon code.

`AGENTS.md` points here. `.claude/rules/` and skills, if added, remain readable by
all agents. Keep this file an index rather than duplicating the design docs.
