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
WAN procurement accounts retain bank design lineage across acquisition/refresh;
provider customer/NOC/transport accounts keep their separate authored policy.
Direct technical assignments follow equipment role and actual tenant. Biomedical
responsibility remains distinct; do not collide with its device-assignment key.
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
The target-aware loader is `just load ARTIFACT TARGET [BRANCH]`;
the normal read-only preflight is `just load-explain ARTIFACT TARGET [BRANCH]`.
`just load-check ARTIFACT` reports TurboBulk contract fit offline; `just branch
TARGET NAME` creates the ready branch a load requires and refuses existing names;
`just branch-delete TARGET NAME` permanently deletes one branch (reset replaces);
`just retire TARGET NAME NAMESPACE` also removes the namespace's main-scoped owner rows.
`just verify-target ARTIFACT TARGET [BRANCH]` runs the final strict gate with zero writes.
Target recipes source `.env` only when `NETBOX_TOKEN` is not already exported.
TurboBulk jobs default to at most 2,000 rows. Keep deterministic batch purposes,
receipt-bound request settings, one ID-resolution read per completed model, and
all global hooks out of data-bearing jobs. Run required maintenance, cable, and
search hooks as separate zero-row finalizers after data and REST completion. A
fourth `just load` argument changes the bound for measured qualification runs.
`estates.turbobulk.load` itself rejects a bound outside 1..10,000: TurboBulk's JSONL
reader fixes the column set from the first 10,000 rows, so a sparse payload spanning
chunks would silently drop later columns.
Compile device-component `_site_id`, `_location_id`, and `_rack_id` caches from
the parent device in the original TurboBulk row; derive the device location from
its rack when NetBox would inherit it on save. Require the corresponding REST
filters during preflight and prove exact component IDs by kind and placement at
final readback, including null location/rack placements. Do not add a repair
upsert: reviewable history must remain one create ChangeDiff per canonical object.
Those commands retain review and revert history; merging a TurboBulk branch to
main is currently blocked upstream. `just load-disposable ARTIFACT
TARGET BRANCH` is the explicit scale-only path: it requires a fresh empty branch
which cannot be reviewed, merged, or reverted and must be deleted after use. It
also requires a TurboBulk-only artifact with no REST create or completion writes;
the explain and load preflights reject other artifacts before target writes.
Reviewable TurboBulk loads require zero initial Branching ChangeDiffs and verify
the exact total and per-model create-ChangeDiff counts at the final readback boundary.
Pre-existing rows may be allowlisted only for declared kinds (`ALLOWLISTED_KINDS`:
builtin `module_type_profile` plus the Branching-exempt main-scoped kinds —
`owner`/`owner_group` and the custom_field/choice_set/custom_link definitions)
with plain-attribute identities disjoint from the plan, recorded exactly in the
receipt; a collision stays a hard block. The allowlist is assessed at preflight
and again at final readback, so concurrent neighbour loads are excused. Those
main-scoped rows survive branch deletion and create no branch ChangeDiffs
(`BRANCH_EXEMPT_KINDS` excludes the extras trio from the exact-count gate,
verified against Branching 1.2.1 EXEMPT_MODELS): disjoint namespaces coexist on
one target, and same-namespace leftovers need `just retire`.
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
- [docs/first-target.md](docs/first-target.md): operator runbook from empty target to loaded branch.
- [docs/recipes.md](docs/recipes.md): complete recipe key reference per profile.
- [docs/seeding.md](docs/seeding.md): zero-write target verification and restore-based seeding.
- [docs/qualification.md](docs/qualification.md): scale, limits and preserved history.
- [CONTRACT.md](CONTRACT.md): canonical graph, ledgers, units and module interfaces.
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
- `estates/equipment.py`, `networking.py`, `operations.py`: connected model families.
- `estates/optics.py`: reviewed installed optical parts and captive AOC ends;
  [optics policy](docs/modeling.md#installed-optics-policy), offline/SDK evidence in
  GOAL.md; pinned local qualification in `lab/README.md`.
- `estates/ipv6.py` and `validate_ipv6.py`: shared dual-stack allocation and
  independent finished-graph obligations; operation in
  [IPv6 guide](docs/modeling.md#optional-ipv6), pinned live receipts in `lab/README.md`.
- `estates/operations_context.py`: shared scoped contacts and immutable dated notes;
  `validate_operations.py` independently checks their actual scope and claims.
- `catalog/type-coverage.json`: pinned SDK/native audit; builds derive `coverage.json`.
- `estates/scenarios.py`: acquisition/refresh candidates and before/after checks.
- `estates/power_scenario.py`: property-selected shared defect, exact finding and restoration checks.
- `estates/span_scenario.py`: provider-only status maintenance, actual premise
  attribution and directed headroom; sample `profiles/provider-maintenance.toml`.
- `estates/validate.py`: independent assertions; add a failing mutation check when extending them.
- `estates/diode.py`: bounded wire export and optional SDK qualification.
- `estates/load.py`: target discovery, transport selection and remote Diode phase checkpoints;
  `estates/turbobulk.py`: the bounded TurboBulk/REST adapter, including the
  29-kind Cloud qualification and the full 98-kind contract (every current profile,
  the complete bank included), live-qualified
  only on the pinned local 4.7.1 stack; Cloud/Enterprise remain unqualified.
- [lab/README.md](lab/README.md): disposable Colima/Compose target and live checks.

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
  PoP without promising carrier diversity. External-transit journals must leave
  remote interface and owner unknown, unlike real two-site handoff records.
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
  grammar. The only modeled path between the tiers is the `conduit` segment
  trunked between the two distribution pairs, plus each device's own dedicated
  management port; validate both independently of emitted contracts, including
  that no record of any kind outside a zone names that zone's VLANs. Both tiers
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
  modeled path between the tiers is the `conduit` segment trunked between the two
  distribution pairs, plus each device's dedicated management port. Validate both
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
  real two-site circuits, in-band loopback management and permanent growth.
  Provider allocation slots count /24 units; the fixed NOC occupies the first
  /16 and routed links/loopbacks reserve the final /16. Existing profiles retain
  their allocation units. Shared DC WAN attachment is an internal builder hook;
  the independent provider validator must check every NOC /31/circuit/PE path.
  Customer spoke-to-hub capacity is a finite declared flow model, not total
  backbone/NOC/transit traffic. Catalog additions require an explicit baseline;
  preserve hospital source/artifacts and historical live evidence.
- Shared DC power checks derive hardware and supply allowances from actual
  device-type references; removing descriptive hardware metadata cannot skip them.
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
  Keep provenance and planning assumptions in the catalog/contracts/report;
  retain material limitations such as unverified RF and external ownership.
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
- Capture `just lab-bootstrap <new-receipt>` before the first load of a fresh
  prepared target. It permits only the nine expected bootstrap identities across
  all supported endpoints; keep its IDs fixed for initial/repeat readback and
  discard it after reset. A populated target must fail bootstrap capture.
- Local replay checks global failed ingestion logs; recovery requires correction,
  a new artifact, and the documented disposable reset/full-load workflow.
  Preserve failed receipts; do not clear logs or manually repair estate records.
- Short device names require site/tenant in every Diode reference; DNS retains
  the full namespace. Serial numbers are not a replacement for matching identity.
- Rack asset tags retain accepted short labels; longer labels use a readable
  prefix and stable digest of the full site/room identity to fit native 50-character
  limits. Validate global tag uniqueness before export; site names are unchanged.
- Site naming: authored display names, facility codes and metro-jittered
  synthetic coordinates are the default (`naming = "authored"`, since 0.10.0);
  `naming = "legacy"` restores namespace-ordinal names and `[site_names]`
  overrides any site by id. Slugs, DNS, device names and matching keys keep the
  stable namespace form; name pools hash the site id (never the seed or other
  sites), so growth cannot rename and seeds cannot reshuffle. Workspace.finish
  enforces global display-name uniqueness. Never rename objects in a running
  estate; naming keys are rebaseline-frozen.
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
  Rack names may repeat across rooms; rack references and asset tags retain room scope.
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
