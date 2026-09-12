# Disposable local target

## Current v0.9 qualification

On September 10, 2026, all five representative v0.9 artifacts passed fresh
initial and identical-repeat Diode ingestion, strict readback, unchanged object
IDs and target inventory, native GET/trace checks and actual Chrome inspection:

| Profile | Canonical objects | Attributes checked | References checked | UI views |
| --- | ---: | ---: | ---: | ---: |
| bank | 12,702 | 47,888 | 29,565 | 5 |
| enterprise-dc | 2,616 | 8,968 | 5,375 | 4 |
| school-district | 3,681 | 13,666 | 8,191 | 4 |
| hospital-clinics | 4,120 | 14,936 | 8,771 | 4 |
| provider-backbone | 4,350 | 14,953 | 8,571 | 4 |

All readbacks had zero mismatches. Each profile used a separate disposable
reset/bootstrap lifecycle; only the provider remains on the current target.
The stack is NetBox 4.7.0 / Diode 2.2.0 / plugin 1.17.0 / SDK 1.14.0, with the
explicit source-guarded front-port bridge below. This does not qualify other
versions, Cloud/Enterprise deployments or large live loads.

Artifacts are in `build/goal-connected-depth/final/qualified-02/` (provider uses
`baseline/` and `changed/`). `artifacts.json` binds the recipes, runtime, graphs
and wire files. Each `final/live/<profile>/summary.json` binds the load/readback
receipts, native records and rendered image/accessibility captures. Bank's live
paths reference candidate01; `final/candidate-02-impact.json` proves its graph
and every wire file are byte-identical to candidate02. Historical failed probes
and candidate checks are preserved, with their correction explained. CUA captures
retain the original `.png` filenames but contain JPEG bytes returned by the
browser tool; the receipts hash those exact rendered bytes.

Useful inspected examples include school/hospital AP → copper path → PSE and
supply ownership, hospital clinical WLAN → VLAN/prefix/services and biomedical
support, device → installed optic → port/bay/source, equipment lifecycle notes,
and provider transport/account → customer private-L3 membership.
The provider maintenance execution result is recorded separately below.
The 239,058-object scale run is offline evidence only; see the
[comparison and scope](../docs/qualification.md#current-offline-scale-evidence).

Current provider starting views (IDs expire on the next reset):

- [Maintained span](http://127.0.0.1:8000/circuits/circuits/3/): Detroit–Chicago 100G, actual transport account and A/Z PE handoffs.
- [Installed 100G optic](http://127.0.0.1:8000/dcim/modules/144/): MX204 et-0/0/1, source-backed LR4 module, native bay/interface ownership.
- [Cedar Retail service](http://127.0.0.1:8000/circuits/virtual-circuits/1/): customer tenant/account and three CE memberships.
- [Transit handoff notes](http://127.0.0.1:8000/circuits/circuits/8/journal/): local 10G boundary, remote interface and owner unknown.

## Provider status sequence

`final/live/provider-backbone/transition-summary.json` under
`build/goal-connected-depth/` binds the completed six-load sequence on September
10: baseline, identical baseline, offline, identical offline, original baseline
restoration and identical restoration. Every strict readback matched all 4,350
objects, 14,953 attributes and 8,571 references, with unchanged IDs and target
inventory. No reset or manual estate repair occurred between these stages.
The existing `lab-load` invoked the upstream SDK helper for every load; artifact
bytes and observation timestamps were preserved. Circuit 3 visibly changed to
Offline and back to Active while its physical handoffs remained installed.

The selected Detroit–Chicago span reroutes four spoke premises across Harbor
Logistics and Cedar Retail toward their two hubs. Current declared traffic fits;
the story concerns reduced protection against another failure. The parent
scenario report owns computed alternate paths, directed load and contact
attribution. NetBox records the status and connected inventory; it does not run
routing convergence or prove customer uptime. The common rewired power defect
remains an offline/fresh-target snapshot, not this qualified status workflow.

To reproduce using the existing CLI on this disposable target, first generate
and check a new artifact, then use the documented reset/bootstrap procedure.
The reset clears this lab's data. Run each command only after the previous one
passes, using new output/receipt paths:

```sh
devenv --profile diode shell
just generate profiles/provider-maintenance.toml build/provider-status-demo
just scenario-check build/provider-status-demo/scenario.json
just lab-reset
just lab-bootstrap build/provider-status-bootstrap.json
```

Then load/read back these stages in order. The loop stops on any failed load or
readback; keep its failed receipt and use the recovery procedure below.

```sh
provider_previous=''
for provider_stage in initial repeat offline offline-repeat restore restore-repeat; do
  case "$provider_stage" in
    offline*) provider_snapshot=changed ;;
    *) provider_snapshot=baseline ;;
  esac
  just lab-load "build/provider-status-demo/$provider_snapshot/diode" "build/provider-status-$provider_stage-ingest.json" front-ports || break
  just lab-verify "build/provider-status-demo/$provider_snapshot/plan.json" "build/provider-status-$provider_stage-readback.json" "$provider_previous" build/provider-status-bootstrap.json || break
  provider_previous="build/provider-status-$provider_stage-readback.json"
done
```

Only a successful `restore-repeat-readback.json` completes this sequence. For a
paced demo, run the corresponding `lab-load`/`lab-verify` pair individually and
inspect the selected Circuit after offline and restore. The provider stays
active after the complete sequence. General scenario envelopes retain their
conservative offline qualification labels; the separate live receipt is the
source for this exact local transition claim.

## Historical v0.8 provider walkthrough

Great Lakes Fiber was loaded from
`build/goal-richness/final-review/corrections/provider/`. Fresh initial and
identical-repeat Diode loads pass strict readback: 4,344 objects, 14,863
attributes and 8,285 references; zero mismatches and unchanged IDs/inventory.
Native inspection passes 338 GETs/93 traces, with 72 additional fresh GETs by
an independent reviewer. Seven rendered views were inspected; source, receipts
and screenshots are bound in `build/goal-richness/provider/live-final/summary.json`.

Five starting views were inspected in Chrome; these IDs are now historical:

- [Chicago PoP](http://127.0.0.1:8000/dcim/sites/7/): regions, cabinets and handoffs.
- [PE power](http://127.0.0.1:8000/dcim/devices/155/power-ports/): real PSU modules and separate A/B paths.
- [Harbor Logistics service](http://127.0.0.1:8000/circuits/virtual-circuits/1/): customer scope, membership and support contact.
- [DNS journal](http://127.0.0.1:8000/virtualization/virtual-machines/3/journal/): actual host, resource and listener plans.
- [Backbone trace](http://127.0.0.1:8000/circuits/circuit-terminations/9/trace/): Chicago PE → local patch → A/Z circuit → local patch → Cleveland PE.

`build/goal-richness/provider/live-final/walkthrough.md` explains each path and
its limits. The final 220,308-object build is offline evidence only. This local
qualification uses the pinned front-port bridge described below; it does not
establish official Cloud/Enterprise compatibility. The power defect remains a
separate offline snapshot. All target IDs are valid only until the next reset.

## Historical final hospital walkthrough

The final hospital artifact is
`build/goal-richness/final-review/corrections/hospital/`. Its fresh initial/repeat
loads matched all 4,383 objects, 15,944 attributes and 8,802 references, with
unchanged IDs/inventory. Native inspection passed 158 GETs/44 traces and 39
additional independent GETs. The site, bedside model and biomedical contact,
clinic floor, clinical VM/journal and staff WLAN were inspected in Chrome.
See `build/goal-richness/hospital/live-final/summary.json` and `walkthrough.md`.
Those hospital IDs are now historical after the provider reset.

Earlier candidate02 hospital/provider milestones and failed predecessors remain
preserved. The first hospital attempt exposed oversized rack tags; the shared
generator now bounds/checks them. The final review also corrected shared display
wording and made customer-name changes require an explicit new baseline. These
were generator fixes followed by fresh full loads, not manual target repairs.

## Historical Harbor Supply walkthrough

Harbor's enriched v0.8 baseline was loaded from `build/goal-richness/harbor-initial/baseline/`:
two DCs, ten racks, 72 devices, 104 VMs and 296 cables. The user-authorized fresh
reset/bootstrap preceded native Diode loading. Initial and identical-repeat
strict readbacks match all 3,331 objects, 11,822 attributes and 6,718 references,
with zero mismatches and unchanged IDs. The inventory API host's native path
walk passes 75 GETs and 16 traces. Nine desks have 134 scoped assignments;
44 journal entries describe actual sites, circuits and service VMs. Receipts and
the inspected contact/site/VM/circuit Chrome views are indexed in
`build/goal-richness/live/summary.json` and `walkthrough.md`.
See [the SE scenario](../profiles/harbor-supply.md) for the customer story.

This replaced the earlier v0.7 Harbor, Summit and school lifecycles. All their saved
artifacts and receipts, including these Harbor IDs, are now historical.
The changed power-defect and growth snapshots remain offline. Do not replay the
rewired snapshot into this healthy baseline.

## Operate it

On this Mac, Homebrew provides Colima 0.10.3, Docker CLI 29.8.0, Compose 5.5.1,
Bash 5.3.15, and jq 1.8.2 (installed September 9, 2026). The install command is:

```sh
brew install colima docker docker-compose bash jq
```

Homebrew's Compose plugin directory `/opt/homebrew/lib/docker/cli-plugins` is
included in `cliPluginsExtraDirs` in `~/.docker/config.json`. Merge that setting
with existing configuration when setting up another Mac; do not replace it.
See [Homebrew's Compose instructions](https://formulae.brew.sh/formula/docker-compose)
and [Colima's installation guide](https://github.com/abiosoft/colima#installation).
Python and the pinned SDK remain in the project's devenv environment.

From the repository root:

```sh
devenv --profile diode shell
just lab-prepare    # once; already prepared on this workstation
just lab-up
just lab-status
```

`lab-up` starts a dedicated `netbox-generator` Colima VM with 4 CPUs, 8 GiB RAM,
and a 60 GiB disk. It explicitly selects `colima-netbox-generator` for Docker
commands and leaves the global Docker context unchanged. NetBox is at
**http://127.0.0.1:8000** and Diode at **grpc://127.0.0.1:18080/diode**.
The admin login is in private `build/local-target/credentials.json`.
`lab-up` provisions a separate read-only v2 API token in `netbox-token` for
verification. Keep this directory when stopping/restarting so credentials and
databases remain paired.

For a fresh target on this workstation, the complete school example is:

```sh
just plan profiles/school-district.toml
just generate profiles/school-district.toml build/school-demo
just sdk-check build/school-demo/diode
just lab-bootstrap build/school-bootstrap.json
just lab-load build/school-demo/diode build/school-initial-ingest.json front-ports
just lab-verify build/school-demo/plan.json build/school-initial-readback.json '' build/school-bootstrap.json
just lab-load build/school-demo/diode build/school-repeat-ingest.json front-ports
just lab-verify build/school-demo/plan.json build/school-repeat-readback.json build/school-initial-readback.json build/school-bootstrap.json
```

Open `build/school-demo/report.md` alongside NetBox to walk the classrooms,
district service VMs, addressing, and power paths. Substitute another profile
and new build/receipt names for the bank or enterprise DC.

`lab-bootstrap` reads all 102 supported endpoints and requires exactly the two
prepared users (`admin`, `diode`) and seven native module type profiles. It fails
if an estate or unexpected record already exists, or a required bootstrap record
is absent. It captures current IDs; it does not load or modify records. The profile
names come from NetBox's pinned
[initial-data migration](https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/migrations/0206_load_module_type_profiles.py).
This command is scoped to this prepared local target, not arbitrary customer databases.
If the local target is populated, use the reset procedure below or the explicit
coexistence procedure. Keep the bootstrap receipt fixed for both readbacks.

Use new receipt paths for each attempt. The readback check compares canonical
objects, emitted attributes, references, cable endpoints, and primary IPs with
NetBox. Strict inventory is appropriate here because this is an isolated target.
The final command also requires existing canonical identities to retain their
NetBox IDs, with no unexplained new objects. An ingest acknowledgement alone
does not pass this check.

This workstation uses the opt-in panel bridge. The `front-ports` load
option is required on **every load** here, including direct-mode school and
enterprise estates. For a separately prepared official, unpatched target, omit
that option and use direct cabling. Existing build and receipt paths are never
overwritten. Older coexistence receipts below describe the target before its
user-requested clean reset; their IDs must not be allowed on a rebuilt database.

Stop the VM and retain its data for the next session with `just lab-down`.
Restart with `just lab-up`. Stopping the VM makes both local URLs unavailable.

## Reset the local lab

With the lab VM running, `just lab-reset` permanently removes containers and
volumes for this lab's NetBox and Diode Compose projects, then starts empty services. This clears both
databases, queues/caches, NetBox history, and uploaded media/scripts/reports.
It retains the dedicated Colima VM, cached images, private configuration/login,
generator source, and saved build artifacts. The obsolete readback token is
removed and `lab-up` provisions its replacement. Browser sessions need a new login.
If the VM is stopped, run `just lab-up` before resetting it.

Generate and check the desired artifact first; use new output/receipt paths:

```sh
devenv --profile diode shell
just generate profiles/bank-depth.toml build/bank-fresh
just sdk-check build/bank-fresh/diode
just lab-reset
just lab-bootstrap build/bank-fresh-bootstrap.json
just lab-load build/bank-fresh/diode build/bank-fresh-initial-ingest.json front-ports
just lab-verify build/bank-fresh/plan.json build/bank-fresh-initial-readback.json '' build/bank-fresh-bootstrap.json
just lab-load build/bank-fresh/diode build/bank-fresh-repeat-ingest.json front-ports
just lab-verify build/bank-fresh/plan.json build/bank-fresh-repeat-readback.json build/bank-fresh-initial-readback.json build/bank-fresh-bootstrap.json
```

Before loading, inventory the fresh database. NetBox bootstraps users and module
type profiles, so an empty estate does not mean every API endpoint has zero rows.
`lab-bootstrap` captures and verifies those records for the new readback allowlist;
never carry an allowlist or previous-ID receipt across a database reset. Then
load through Diode, compare all generated fields/references, replay the same
artifact, and compare again using the new initial readback's IDs. The observed
clean-load receipts below record this sequence for `build/bank-fresh/`.

## Recover a failed local ingestion

Keep the failed replay receipt and inspect the failed entity/error in NetBox's
Diode ingestion logs. Correct the generating rule or target configuration, then
generate into a new directory and run `just verify` and `just sdk-check` on it.
For this disposable local harness, follow the reset, bootstrap, full load and
two readback commands above with new paths. A reset destroys this lab's data;
keep the saved artifacts and failure evidence for diagnosis.

The pinned `lab-load` reconciliation barrier checks global failed-log counts.
An old failed ingestion log continues to fail that barrier; this harness has no
resume-from-phase command. Rerunning a corrected file alone is therefore not
its recovery workflow. Do not delete failure logs or manually repair estate
records to obtain a passing receipt. A remote target needs its own supported
Diode recovery process; the local reset instructions do not apply to it.

## Qualify a new namespace alongside an existing estate

For a generator release that changes layout, generate a new baseline with a
distinct recipe namespace, such as `cedar-v03a`, and a fixed observation date
in the past. Do not use the v0.2 plan as `--previous`, replay a new layout into
the old namespace, or delete the old target objects. Keep shared manufacturer
and device-type definitions unchanged. Regions, site groups, rack roles, and
platforms are now included in readback; their emitted fields and parent or
manufacturer references are checked too.

Before loading the new baseline, take a successful strict readback of the old
plan, for example `build/v02-before-v03.json`. That receipt captures the exact
existing IDs that the new verification may allow. After the new namespace has
reconciled through `lab-load`, run:

```sh
python3 -m lab.verify build/bank-v7/plan.json \
  --url http://127.0.0.1:8000 --token-file build/local-target/netbox-token \
  --strict-inventory --allow-existing-receipt build/v02-before-v03.json \
  --receipt build/v03-initial-readback.json
```

`--allow-existing-receipt` requires a successful receipt from the same target
URL and strict inventory mode. Only captured kind/ID pairs are exempted from
unmatched-inventory and unexplained-addition findings. They remain visible in
the receipt. Desired shared objects still undergo full attribute/reference
checks, and uncaptured extra rows still fail. This is an inventory exception,
not evidence that the older estate stayed unchanged.

Replay the identical v0.3 artifact, then repeat that readback command with a new
receipt path and `--previous-receipt build/v03-initial-readback.json`, keeping
the same v0.2 allowlist. The previous receipt must belong to this same baseline:
canonical keys are local to each plan and cannot identify different namespaces.
Finally, verify the frozen v0.2 plan again with its own previous receipt and the
successful v0.3 receipt as the allowed inventory:

```sh
python3 -m lab.verify build/bank-v2/plan.json \
  --url http://127.0.0.1:8000 --token-file build/local-target/netbox-token \
  --strict-inventory --previous-receipt build/v02-before-v03.json \
  --allow-existing-receipt build/v03-initial-readback.json \
  --receipt build/v02-after-v03.json
```

That last check establishes preservation of the old plan's emitted fields,
references, and IDs. New geographic objects absent from the old plan are checked
by the v0.3 readback. These commands only read NetBox; all estate loading remains
through Diode. Reuse identical observation values for identical replay. A newer
observation date alone does not bypass Diode deduplication: the pinned
[entity fingerprinter](https://github.com/netboxlabs/diode/blob/960fcb418892c36cbee4d4faad0e29b9b0bcd2e2/diode-server/entityhash/entityhash.go)
excludes timestamps and metadata from its content hash. A new namespace changes
the actual object identities; shared unchanged definitions can still deduplicate.

## Opt-in local front-port compatibility

The official SDK1.14/plugin1.17 path cannot preserve legacy front/rear mappings
on NetBox4.7. `lab/patches/` contains a narrowly scoped local bridge. It uses
Diode's existing nested references and NetBox's native serializer, supports one
front position, and refuses replacing or clearing existing mappings. It is not
an official release or a general migration mechanism.

```sh
just lab-front-ports     # stage public build inputs, preserving existing secrets
just lab-up             # build and start the opt-in image
just generate profiles/bank-depth.toml build/bank-complete
just lab-load build/bank-complete/diode build/v07-initial-replay.json front-ports
```

The explicit `front-ports` option is required for every load into the patched
target, including direct-mode artifacts. Replay verifies all patched source
hashes in both running NetBox containers before submitting data, and records
that evidence in the receipt. Unknown or changed patch inputs fail closed.
The manifest still describes the official plugin's incompatibility; the exception
belongs to the local qualification harness.

When keeping existing estates in this target, verify the detail build using the
most recent successful same-target inventory receipt as an explicit allowlist:

```sh
just lab-verify build/bank-wan/plan.json build/v06-initial-readback.json '' build/v05-before-v06-readback.json
just lab-load build/bank-wan/diode build/v06-repeat-replay.json front-ports
just lab-verify build/bank-wan/plan.json build/v06-repeat-readback.json build/v06-initial-readback.json build/v05-before-v06-readback.json
```

The example pre-v0.6 allowlist is a local receipt, not a shipped prerequisite. Use
your own successful baseline receipt, or a fresh `lab-bootstrap` receipt for an empty estate.
Use a receipt that inventories every existing kind: a newer direct-only receipt
can omit the existing panel ports because readback inventories only planned kinds.
Keep that pre-load allowlist fixed for initial load, replay and growth; allowing
newly ingested IDs as pre-existing could hide unintended extra records.
Readback asserts each native front/rear mapping tuple as well as physical object
IDs. Mapping row IDs are private to NetBox; the separate isolated native test
checks their preservation and complete cable traces through the serializer.

To compare fresh headquarters footprints, edit `headquarters_staff` in a copy
of the detail recipe, keeping each independently loaded estate in a distinct
namespace. Increasing it creates more pods, floors and local infrastructure.
Changing an existing HQ's staff count is deliberately rejected with `--previous`;
that requires a future remodeling transition. Additive site growth retains the
saved headquarters demand and all existing allocation ledgers.
`wan_tiers_mbps` similarly changes purchasing policy only for a new baseline.
Native circuit fields carry CIR, installation date and fictional procurement
comments; readback compares each emitted value. Handoff speed remains separate
from purchased CIR.

## Scope of the live check

`lab/replay.py` invokes the upstream SDK's dry-run replay helper once per manifest
phase, with credentials in the subprocess environment. It waits for the pinned
local Diode Redis stream to drain, then requires all ingestion logs to reach
successful terminal states before submitting the next phase. It uses upstream
grpcurl for the reconciliation diagnostic; it does not implement another loader.
Each phase has a finite deadline and failures leave a partial receipt.

These global queue/log checks require an otherwise idle disposable target.
They are not a portable monitoring contract for shared or customer systems.
Use the separate `lab/verify.py` readback to establish the resulting graph.
The current checks qualify initial load, identical replay, and the documented
additive branch/DC-capacity expansion. They do not qualify arbitrary resizing,
deletions, new observation timestamps, scenario transitions, every edition, or
100,000-object ingestion throughput.

## v0.7 coverage and binding checks

The expanded detail profile uses `cedar-complete` and emits the 101 audited estate
candidate types, plus an existing-user reference. `reservation_user` must name an
account already present on the target. The local harness checks it before any
submission; no account is created. An empty binding omits rack reservations.

`global_numeric_preflight` in replay receipts records observed ownership checks
for the generated ASN numbers and VRRP group ID. It rejects foreign or ambiguous
matches without submitting a phase. This neither allocates numbers nor locks the
instance against concurrent writers. `aggregate_preflight` separately rejects
overlapping target aggregates, allowing only an exact prefix with matching RIR
name and description. NetBox4.7 applies this overlap constraint globally across
RIRs and tenants. These checks are local harness protections;
raw upstream SDK replay does not perform them.

`build/v07-preload-readback.json` is historical evidence from before the local
reset: it captured 69,519 existing IDs and rechecked the v0.6 estate. Never use
that allowlist on a rebuilt target. Each fresh qualification captures and uses
its own verified bootstrap inventory from the new database lifecycle.

## Enterprise qualification during the shared-profile goal

The corrected `build/goal-live/enterprise/artifact/` estate has 2,042 objects.
An earlier attempt failed because the shared builder omitted owner.group; the
source now emits a real owner group and independent validation rejects omission.
The failed receipt is retained at `build/goal-live/enterprise/initial-replay.json`.
No manual estate repair was used. A fresh local reset preceded corrected loading.

`build/goal-live/enterprise/corrected/summary.json` indexes:

- Initial Diode load: 2,106 wire observations, 15 requests, 82.367 seconds across phases.
- Strict readback: 2,042 objects, 7,340 attributes and 3,809 references, zero mismatches.
- Identical replay: 44.223 seconds across phases; all IDs and inventoried rows unchanged.
- A fresh nine-record bootstrap inventory, never an allowlist carried across resets.

This estate uses direct cables on the pinned NetBox4.7/Diode2.2/plugin1.17 local
stack with the explicitly verified panel bridge installed. Its 73 GETs and 16
native traces walk actual host/leaf/spine, VM/listener/IP, WAN/carrier and both
PDU/feed/panel paths; see `path-inspection-r4.json` and `path-walkthrough.md` in
that evidence directory. These results do not qualify live scenario
rewiring, additional target versions, or 100k-object ingestion.

## School qualification during the shared-profile goal

`build/goal-live/school/summary.json` indexes the 3,716-object school baseline:

- Initial Diode load: 3,967 observations, 15 requests, 120.512 seconds across phases.
- Strict readback: 14,273 attributes and 8,175 references, zero mismatches.
- Identical replay: 58.750 seconds; all IDs and inventoried rows unchanged.
- Actual path inspection: 92 GETs and 22 native traces covering classroom copper,
  both distribution uplinks, WLAN/VLAN binding, service host/fabric, WAN and power.

`path-walkthrough.md` and `path-inspection.json` in that directory preserve the
actual returned paths. Separate fresh reviewers checked the highest-floor student
and DNS service paths. This used the same explicitly patched pinned local stack.
The later public quickstart qualification below replaces that target lifecycle;
these retained receipts remain evidence of the earlier load, not current IDs.

## Public quickstart qualification

The short school procedure at the top of this guide was executed exactly after
a fresh disposable reset. That qualification loaded `build/school-demo/`;
the later data center walkthrough above replaces its database lifecycle.
`build/goal-operator/summary.json` indexes its command logs, receipts and paths:

- `build/school-bootstrap.json`: exactly nine expected identities, all 102
  supported endpoints inspected before ingestion.
- `build/school-initial-ingest.json`: 3,967 observations, 15 requests,
  120.265 seconds across successfully reconciled phases.
- `build/school-initial-readback.json`: all 3,716 objects, 14,273 attributes and
  8,175 references matched, zero mismatches.
- `build/school-repeat-ingest.json` and `build/school-repeat-readback.json`:
  identical replay in 60.903 seconds across phases; all IDs and inventory retained.
- `build/goal-operator/path-inspection.json`: 92 current GETs and 22 native cable
  traces, bound to the new repeat receipt and exact plan file.

The independent SE reviewer checked exact command logs, receipt/manifest/plan
digests, temporal order, strictness, phase metrics and stable IDs in
`build/goal-final-review/se/public-journey-retest.json`. It also challenged
bootstrap with missing, duplicate and extra records; a real capture attempted
against the prior populated school failed as intended. These results close the
fresh-start operator gap without weakening strict inventory. The final readback
was recorded at `2026-09-10T00:08:18Z`; these IDs belong only to this lifecycle.

## Clean rebuild qualification

On September 9, 2026, the user explicitly requested a full local reset. Both
Compose projects were brought down with `--volumes --remove-orphans`; all eight
lab volumes and all lab containers were confirmed absent before `lab-up`.
The obsolete readback token was removed and provisioned again. The existing
private login/configuration, images, generator source and artifacts were kept.
`build/fresh-reset.json` records the deletion boundary. The `lab-reset` recipe
contains the same exercised command sequence; its expansion was checked with
`just --dry-run lab-reset` without resetting the populated target again.

The newly generated `build/bank-fresh/` exactly reproduces the corrected canonical
plan and Diode requests in `build/bank-complete-final/`; graph and pinned SDK
checks passed. Before ingestion, a read-only inventory of all 102 generator
endpoints found only users `admin`/`diode` and seven built-in module type profiles.
`build/fresh-bootstrap-readback.json` captures these nine records. Eight remain
allowed extras because `admin` is also a canonical external reference. No old
estate IDs were carried across the reset.

| Check | Observed result | Receipt |
| --- | --- | --- |
| Initial Diode load | 16,010 entities, 28 requests, 15 phases; 441.754 seconds across phases; zero failed/queued | `build/fresh-initial-replay.json` |
| Initial strict readback | All 14,725 objects, 54,814 attributes, 32,125 reference fields and 1,245 panel mapping tuples matched | `build/fresh-initial-readback.json` |
| Identical Diode replay | 166.488 seconds across phases; ingestion metrics unchanged | `build/fresh-repeat-replay.json` |
| Repeat strict readback | Zero mismatches; every canonical ID, emitted attribute/reference, mapping tuple and all 14,733 inventoried target IDs retained | `build/fresh-repeat-readback.json` |

`build/fresh-summary.json` indexes these checks; `build/fresh-summary.py` asserts
the receipt/identity/manifest comparisons. `build/fresh-bootstrap.py` records the
pre-ingest inventory method. These are local run artifacts, not portable receipts
for a different database. The completed load/readback commands were:

```sh
just lab-load build/bank-fresh/diode build/fresh-initial-replay.json front-ports
just lab-verify build/bank-fresh/plan.json build/fresh-initial-readback.json '' build/fresh-bootstrap-readback.json
just lab-load build/bank-fresh/diode build/fresh-repeat-replay.json front-ports
just lab-verify build/bank-fresh/plan.json build/fresh-repeat-readback.json build/fresh-initial-readback.json build/fresh-bootstrap-readback.json
```

The Chrome walkthrough checked the ten sites, headquarters rack elevations,
linked switch components, and a complete workstation cable trace through a
patch panel and wall outlet (three segments, 26 meters). Screenshots are in
`build/fresh-rack-elevations.png` and `build/fresh-cable-trace.png`. Names remain
generated as before; meaningful site names/facility codes are explicitly deferred
to a generator change, with no manual target renames.

All estate seeding went through Diode. These results apply only to the pinned
NetBox 4.7.0 / Diode 2.2.0 / plugin 1.17.0 local stack with its source-verified
front-port bridge. They do not establish unpatched, Cloud or Enterprise support.

## Earlier coexistence qualification

On September 9, 2026, v0.7 `build/bank-complete` passed initial reconciliation
and strict REST readback on the pinned patched local target. The artifact has
14,725 canonical objects across 101 estate candidate kinds plus one external
user kind, with 16,010 ingestion entities in 28 requests and 15 phases. Initial
processing took 477.491 seconds across phases, with no failures. Readback checked
54,814 attributes and 32,125 reference fields, including 1,245 front/rear mapping
tuples; all 14,725 objects matched with zero mismatches.

The first identical replay subsequently failed strict readback: the explicit
root ContactGroup slug bypassed the pinned plugin auto-slug matcher and created
one unused duplicate group. This attempt remains in
`build/v07-repeat-replay.json` and `build/v07-repeat-readback.json`; initial
acceptance alone is not repeat-load qualification.

The corrected wire artifact is `build/bank-complete-r2/`: it has the identical
canonical graph and changes only the root ContactGroup request. The exporter
omits its name-derived slug to enable the plugin auto-slug matcher; a different
intended root slug fails instead of being silently changed. SDK verification now
refuses the old explicit-slug artifact before submission. The source diagnosis,
focused native match test and unused-duplicate dependency check are preserved in
`build/v07-repeat-log-investigation.json`.

The repeat also issued a Site custom-field update despite the stored value
remaining correct. A fresh native diff finds no changes; a diagnostic empty
custom-field-definition cache reproduces the update. A stale worker cache is the
supported explanation, not a directly observed worker-memory fact. Replay
qualification therefore asserts stored object identities and emitted values;
it does not promise zero extra runtime logs or changelog entries.

The corrected artifact reconciled twice through Diode in 164.191 and 164.573
seconds across phases. The second corrected replay added no ingestion-log rows
and created no further ContactGroups; the contact still points to original group
ID1. Receipts: `build/v07-r2-initial-replay.json`,
`build/v07-r2-repeat-replay.json`, and `build/v07-r2-targeted-replay-check.json`.
That coexistence run stopped short of final strict qualification because the
single unused duplicate ID2 remained. The user's subsequent full local reset
removed it together with the older estates. The failed receipt remains evidence;
the duplicate was never waived or added to the original fixed allowlist.

A separate strict readback of all six older estates passed after both corrected
replays: every emitted attribute/reference, object ID and existing mapping tuple
was preserved, with zero mismatches. `build/v07-older-estates-pending-cleanup.json`
indexes those receipts. They inventory the kinds present in the older plans, so
they do not waive the new ContactGroup duplicate or establish the final v0.7 gate.

The delivered `build/bank-complete-final/` refreshes coverage audit notes and has
byte-identical canonical plan and Diode files to the corrected tested artifact
(`build/v07-final-artifact-equivalence.json`). All estate seeding uses Diode.

Initial evidence: `build/v07-initial-replay.json` and
`build/v07-initial-readback-normalized.json`. The earlier failed
`build/v07-initial-readback.json` is retained: it found three native response
normalizations (two integer DH-group choices and a typed custom-field choice).
The verifier now checks those source-confirmed shapes explicitly, without
changing the artifact or target data. Wrong or missing emitted values still fail.

Offline qualification passed 222 tests, a 30-profile variation check, six stable
growth cases and a 110,595-object plan through validation and SDK export checks.
That large plan generated in 0.419 seconds, validated in 1.444 seconds and
exported in 1.661 seconds; SDK checking took 28.121 seconds. These are local
observations, not a large live-ingestion benchmark. Evidence is in
`build/v07-r2-check.log`, `build/v07-connected-check.log`, and
`build/v07-r2-scale-review.json`. The connected-profile check also requires one
reference/dependency graph without relying on its shared generated tag.

`build/v07-ui-walkthrough.md` records a Chrome walkthrough of the site, contacts,
journal, service VM, installed PSU, switch stack, WLAN, VRRP gateway, virtual
circuit, planned recovery tunnel and cooling feed. The current source reproduces
the live plan exactly (`build/v07-reproduction-check.json`).

On September 9, 2026, the v0.6 `build/bank-wan` panel estate passed initial
reconciliation, identical replay and strict readback on the same opt-in patched local stack:

| Check | Observed result | Local receipt |
| --- | --- | --- |
| Initial Diode replay | 14 phases; 12,837 entities; zero failed; 400.325 seconds across phases | `build/v06-initial-replay.json` |
| Strict NetBox readback | 12,219 objects, 47,336 attributes, 27,177 references and 1,245 mapping tuples; zero mismatches | `build/v06-initial-readback.json` |
| Identical Diode replay | 134.887 seconds; all phases passed; no additional ingestion-log rows | `build/v06-repeat-replay.json` |
| Repeat strict readback | Every ID and all 1,245 mapping tuples retained; 69,510 target IDs unchanged; zero mismatches | `build/v06-repeat-readback.json` |
| Browser walkthrough | Native 50/200/500 Mb/s CIR, dates and procurement comments; complete HQ handoff trace with 3 m cable and opaque provider network | `build/v06-ui-walkthrough.md` |

The fixed pre-load allowlist is `build/v05-before-v06-readback.json`.
A single paginated inventory pass was compared against the new plan and all
five older plans using the existing verifier functions. All 8,432 v0.2, 2,655
v0.3 pilot, 12,528 v0.3 depth, 21,541 v0.4 grown and 12,219 v0.5 objects retained
their IDs and emitted fields/references; their mapping tuples were unchanged.
The local `build/v06-final-readbacks.py` driver writes separate receipts sharing
an inventory digest. `build/v06-final-comparison.json` links all six successful
comparisons, including the unchanged target inventory and fixed replay allowlist.
No target reset or direct estate writes were used.

The separately generated 27-site/21,829-object growth candidate passed offline
SDK verification and preserves existing circuit rates/dates/terminations;
`build/v06-growth-plan-review.json` records it. This candidate was not ingested.
These results qualify the pinned patched target; they do not establish official
current-plugin or Cloud/Enterprise compatibility.

On September 9, 2026, the v0.5 `build/bank-campus` panel estate passed initial
reconciliation, identical replay and strict readback on the same opt-in patched
local stack:

| Check | Observed result | Local receipt |
| --- | --- | --- |
| Initial Diode replay | 14 phases; 12,837 entities; zero failed; 436.204 seconds across phases | `build/v05-initial-replay.json` |
| Strict NetBox readback | 12,219 objects, 47,296 attributes, 27,177 references, 1,245 mapping tuples; zero mismatches | `build/v05-initial-readback.json` |
| Identical Diode replay | 154.294 seconds; all phases passed; no additional ingestion-log rows | `build/v05-repeat-replay.json` |
| Repeat strict readback | Every ID and all 1,245 mapping tuples retained; 57,308 target IDs unchanged; zero mismatches | `build/v05-repeat-readback.json` |
| Browser walkthrough | Four distinct room-local N01 racks; completed 26m workstation and 22m inter-room fiber traces; local power panel/feed/PDU navigation | `build/v05-ui-walkthrough.md` |

The HQ has four equipment rooms, 12 access switches, five racks and 21 inter-room
fiber links. The pre-load inventory receipt remains fixed for replay checks.
This qualifies the emitted building relationships on this patched target;
it does not establish facility independence, routing behavior or official
Cloud/Enterprise support. Offline HQ-addition growth is recorded separately in
`build/v05-growth-plan-review.json` and has not been ingested.

Separate final readbacks checked all emitted fields and preserved every ID in
the four older estates: 8,432 v0.2 objects, 2,655 v0.3 pilot objects, 12,528 v0.3
depth objects and 21,541 v0.4 grown-estate objects. The 1,224 older v0.3 mappings
and 2,346 v0.4 mappings were unchanged. Their receipts are
`build/v02-after-v05-readback.json`, `build/pilot-after-v05-readback.json`,
`build/v03-depth-after-v05-readback.json`, and `build/v04-after-v05-readback.json`.
`build/v05-final-comparison.json` records the successful cross-receipt assertions,
including identical target inventory and fixed allowlist for the v0.5 replay.

On September 9, 2026, the v0.4 `build/bank-demo` panel estate passed on this stack
with SDK 1.14.0 and the same explicitly opt-in local plugin compatibility patch:

| Check | Observed result | Local receipt |
| --- | --- | --- |
| Initial Diode replay | 14 phases; 12,584 entities; zero failed; 410.838 seconds across phases | `build/v04-initial-replay.json` |
| Strict NetBox readback | 11,967 objects, 46,470 attributes, 26,780 references, 1,224 mapping tuples; zero mismatches | `build/v04-initial-readback.json` |
| Identical Diode replay | 140.503 seconds; no additional ingestion-log rows | `build/v04-repeat-replay.json` |
| Repeat strict readback | Every object ID and mapping tuple retained; no unexplained records | `build/v04-repeat-readback.json` |
| Additive growth replay | 27 sites; 22,553 entities; zero failed; 432.202 seconds across phases | `build/v04-growth-replay.json` |
| Growth strict readback | 21,541 objects, 82,509 attributes, 47,778 references; all original IDs and mappings retained; zero mismatches | `build/v04-growth-readback.json` |
| Browser walkthrough | Readable three-cable ATM path, 39m; compact rack; gateway bridge/SVI relationships; four circuits at the expanded DC | `build/v04-ui-walkthrough.md` |

The pre-v0.4 inventory receipt stayed fixed throughout initial load, repeat and
growth. Separate final readbacks preserved all 8,432 v0.2 IDs, 2,655 v0.3 pilot
IDs and 12,528 v0.3 depth IDs, with complete emitted field/reference coverage and
all 1,224 older front/rear mapping tuples unchanged. Receipts are
`build/v02-after-v04-growth-readback.json`,
`build/pilot-after-v04-growth-readback.json`, and
`build/v03-depth-after-v04-growth-readback.json`. No target reset was used.
This remains qualification of the patched local stack, not official plugin
compatibility or Cloud/Enterprise support.

On September 9, 2026, the v0.3 `build/bank-depth` panel estate passed on this
stack with SDK 1.14.0 and the explicitly opt-in local plugin compatibility patch:

| Check | Observed result | Local receipt |
| --- | --- | --- |
| Initial Diode replay | 14 phases; 13,153 entities; zero failed; 379.897 seconds across phases | `build/depth-initial-replay.json` |
| Strict NetBox readback | 12,528 objects, 49,177 attributes, 27,553 references, 1,224 front/rear mappings; zero mismatches | `build/depth-initial-readback.json` |
| Identical Diode replay | 122.409 seconds; no additional ingestion-log rows | `build/depth-repeat-replay.json` |
| Repeat strict readback | Same object IDs and mapping tuples, complete coverage, no unexplained records | `build/depth-repeat-readback.json` |
| Browser walkthrough | Complete three-cable ATM path, 39m; all 24 panel positions; service/IP navigation | `build/depth-ui-walkthrough.md` |

The full panel inventory includes 10 sites, six regions, three site groups,
80 locations, 19 racks, 1,142 devices, 4,423 interfaces, 2,001 cables, 1,224 front
ports and 1,224 rear ports, and 32 VMs. The existing v0.2 estate and v0.3 direct
pilot were explicitly allowlisted by captured target IDs. This result does not
establish official plugin compatibility; both replay receipts record the source
hashes verified in the running app and worker. Isolated native tests additionally
check preservation of private mapping row IDs, which REST does not expose.

Final independent readbacks of the older plans retained all 8,432 v0.2 IDs and
all 2,655 pilot IDs, with full emitted attribute/reference coverage and zero
mismatches (`build/v02-after-depth-readback.json` and
`build/pilot-after-depth-readback.json`). The target was not reset to run this
qualification.

On September 9, 2026, the default `build/bank-v2` estate passed the whole path on
the earlier unmodified local stack using SDK 1.14.0:

| Check | Observed result | Local receipt |
| --- | --- | --- |
| Initial Diode replay | 9 phases; 9,057 entities reconciled; zero failed; 239.817 seconds across phases | `build/local-bootstrap-fixed-replay.json` |
| Strict NetBox readback | 8,432 objects, 32,203 attributes, 17,783 references matched; zero extras | `build/local-initial-readback.json` |
| Identical Diode replay | All phases passed in 81.634 seconds; no additional ingestion-log rows | `build/local-identical-replay.json` |
| Repeat strict readback | Same 8,432 IDs, full field/reference coverage, no extra objects | `build/local-repeat-readback.json` |

The inventory includes 10 sites, 625 devices, 4,429 interfaces, 1,041 cables,
20 circuits, and 32 VMs. The additional 625 submitted entities are deferred
primary-address assignments, not extra canonical objects. Readback receipts
include the exact plan-file SHA-256, URL, and observation times. Observed NetBox
and plugin versions are in `build/local-target-status.json`.

The failed first authentication attempt remains in `build/local-initial-replay.json`.
It submitted no data. The bootstrap fix is included in setup and its tests.
The successful repeat reused identical observations, so it does not establish
matching behavior for changed observations or applying the refresh transition.

## How the fixture is prepared

`python3 lab/setup.py` prepares NetBox 4.7.0 / netbox-docker 5.1.0, Diode 2.2.0,
and Diode plugin 1.17.0 under ignored `build/local-target/`. It downloads source
files at immutable commits, records their SHA-256 values in `sources.json`, and
uses pinned image manifest digests. It neither starts Docker nor changes a
Docker context. Existing destinations are rejected so setup cannot rotate the
credentials of an existing database.

The target directory is private (0700). Runtime environment files, OAuth client
JSON, `credentials.json` (NetBox admin login), and `sdk.env` (SDK client) are
0600. Setup does not print credential values. Do not print resolved Compose
configuration, environment files, or container environments into shared logs.
`.dockerignore` excludes credentials from the NetBox image build context.

Both stacks need the external network `netbox-generator-link`. Run Compose with
the intended Docker context explicitly. For this workstation that is
`colima-netbox-generator`. The two Compose file pairs are:

- `netbox/docker-compose.yml` + `netbox/compose.local.json`, project directory
  `build/local-target/netbox`.
- `diode/docker-compose.yaml` + `diode/compose.local.json`, project directory
  `build/local-target/diode`.

The projects have distinct names. Only the applications that communicate across
stacks join the shared network; databases and Redis remain on their own default
networks. NetBox publishes `127.0.0.1:8000`; Diode publishes `127.0.0.1:18080`.
The plugin uses service DNS internally, and displays the host Diode address.
The NetBox entrypoint performs migrations and creates the admin from the private
environment. Provision a separate read-only verification API token after it is
healthy; setup intentionally creates no all-powerful API token.

The upstream Diode quickstart is replaced only for file preparation: Python's
`secrets` module creates the same three OAuth clients and fills the official
environment template. The upstream bootstrap keeps its existence/readiness
logic, but sends each client's JSON to Hydra using `--file -` and suppresses
the response. It also passes the non-secret client ID through `--id`, because
Hydra 26.2 overwrites the JSON ID with that flag after reading the file.
The bootstrap alone runs as root to read its 0600 credentials
mount. Redis/Valkey read their passwords from private temporary configuration
files instead of command arguments. These adjustments do not change ingestion,
reconciliation, application matching, or database schemas.

Preserved upstream files and licenses are under `upstream/`. Sources:
[netbox-docker 5.1.0](https://github.com/netbox-community/netbox-docker/tree/c7092cd0a729ebfbd1c6bce59e09df4953face24),
[Diode 2.2.0 deployment](https://github.com/netboxlabs/diode/tree/960fcb418892c36cbee4d4faad0e29b9b0bcd2e2/diode-server/docker),
[Hydra file/stdin input](https://github.com/ory/hydra/blob/v26.2.0/cmd/cmd_helper_client.go),
[plugin configuration](https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/netbox_diode_plugin/__init__.py).

Preparation and valid Compose configuration are not evidence of a healthy stack
or accepted ingestion. Record those separately after startup and REST readback.
Run `python3 -m unittest lab.test_setup` for the offline preparation checks.
`target.json` records the local URLs, context/network, and requested product
versions for the qualification tools; it contains no credentials.
