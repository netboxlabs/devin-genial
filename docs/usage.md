# Generate and grow an estate

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

Choose a recipe, inspect its output, and grow it while retaining existing allocations.

Run commands from the repository root. Paths in code blocks are relative to that root.
Generated `build/` artifacts and qualification receipts are local outputs, not included in a clone.

- [Start](#start)
- [Enterprise data center](#enterprise-data-center)
- [School district](#school-district)
- [Hospital and clinics](#hospital-and-clinics)
- [Provider backbone](#provider-backbone)
- [Repeatability and growth](#repeatability-and-growth)

## Start

Clone [netboxlabs/devin-genial](https://github.com/netboxlabs/devin-genial):

```sh
git clone https://github.com/netboxlabs/devin-genial.git
cd devin-genial
```

Generated artifacts, credentials and recorded qualification evidence under
`build/` are local outputs and are not included in the Git repository. Historical
receipt paths below refer to those preserved local runs. Use the recipes and
checks to reproduce a build; the local lab guide covers target qualification.

From this directory, with devenv and direnv installed:

```sh
direnv allow
devenv shell
just plan
just generate
```

`just plan` previews a validated inventory without writing files. `just generate`
builds the default bank under `build/bank-v9/`. Open `build/bank-v9/report.md` for its
topology, IP plan, rack elevations, service placement, and questions to explore.
Build destinations must be new; rerunning never overwrites an existing artifact.
Each build also includes `intent.json`: resolved values, supplied/default provenance
and planning assumptions. Rebuilding a frozen plan labels input provenance unknown;
it cannot recover which fields the original operator explicitly supplied.
For the machine-readable preview, use `python3 -m estates --json plan profiles/bank.toml`;
`--json` belongs before the subcommand.

The default recipe has two data centers, one headquarters, and seven branches.
Change [profiles/bank.toml](../profiles/bank.toml) to choose the branch mix, namespace,
seed, headquarters staff demand, WAN purchasing tiers, private address pool,
and reserved capacity. A larger example is included:

```sh
just generate profiles/bank-scale.toml build/scale-v8
just verify build/scale-v8/plan.json
```

Python 3.11+ alone can run the generator without devenv or Just:
`python3 -m estates generate profiles/bank.toml --out build/example`.
No third-party packages are imported on that path. Each build includes
`coverage.json`: counts and example keys for all 106 pinned SDK entity kinds,
plus explicit classifications for native types without a top-level SDK message.
Generated counts do not establish successful ingestion.

For the broadest local walkthrough, use `profiles/bank-depth.toml`. Its
`reservation_user = "admin"` binds the account created by this disposable lab;
change it to an existing target username, or leave it empty to omit rack
reservations. Diode does not create users. Local replay checks the binding before
submitting any data.

The preserved qualified bank artifact is `build/bank-fresh/`: 14,725 canonical objects
with examples of all 101 audited estate candidate kinds. On September 9, 2026,
the user requested a full local reset. Both Compose projects' volumes were
removed, the bank was freshly generated from `profiles/bank-depth.toml`, and all
estate data was loaded through Diode. Initial and identical-repeat strict
readbacks passed with zero mismatches; all object IDs and 1,245 panel mapping
tuples survived replay. `build/fresh-summary.json` indexes the evidence. This is
qualification of the pinned local stack with its explicit panel patch, not an
official compatibility claim. See [lab/README.md](../lab/README.md) for the clean
rebuild and preserved earlier failed replay.

## Enterprise data center

For a customer-shaped SE example, use [Harbor Supply: peak-season readiness](../profiles/harbor-supply.md)
and its [recipe](../profiles/harbor-supply.toml). It generates a distributor's two-DC
service estate, a single planted power-dependency defect, and an evidence-backed
walkthrough from service listeners to physical hosts, racks, and supply paths.

```sh
just plan profiles/enterprise-dc.toml
just generate profiles/enterprise-dc.toml build/enterprise-demo
just verify build/enterprise-demo/plan.json
devenv --profile diode shell -- just sdk-check build/enterprise-demo/diode
```

The example requests two DCs with inventory API, order database and build-artifact
workloads: 28 VMs in total, with 40 bound service listeners. Change workload
`groups`, `replicas`, `vcpus`, `memory_mb`, `disk_mb`, network and listeners to
describe different demand. Each group is a synthetic shard; each replica receives
the complete declared VM resources. The same workload demand is applied at each
site. Adding a second site does not establish cross-site application replication.

`failure_domain = "host"` separates a group's replicas across physical hosts;
`"rack"` additionally separates their compute racks and the paired fabric/WAN
devices. Capacity determines host count and leaf placement. The report follows
real graph links from each workload's VM, listener and address through its host,
rack, data uplinks and power feeds. Its replica table computes surviving VM
inventory after loss of one host or compute rack. These are inventory and path
checks; no running application, failover protocol or restart capacity is modeled.
Management and serial access have active connected paths, but are single-homed.

The enterprise foundation uses one organization, six network roles, site VLAN
groups, owner/contact assignments, provisioned VM disks and installed PSU modules.
It omits the bank acquisition story and optional laboratory/cooling/stack examples.
Private addressing reserves a `/16` per DC and a `/20` per network role. A single
DC can use an entire `/16` pool: its scoped site reservations serve as the root
containers, without duplicating equal global prefixes. Racks use
a bounded synthetic row grid; inter-rack route length includes three metres of
service slack. Reference host resources and geography are declared assumptions.

Explicit limits are 1–8 DCs, 1–16 workloads, 2–4 replicas, and 1–512 groups per
workload. All demands must also fit physical capacity: 16 hosts per workload,
16 fabric pods, 160 managed-device attachments per site, address reservations,
and 64 cabinet positions in each network/compute zone. These ceilings are checked
separately; choosing the maximum of every input is not a supported composition.
CPU, memory and disk packing use exact decimal reserve arithmetic. Per-site
`wan_peak_mbps` sizes parallel pairs of 1-Gbps handoffs, with each carrier covering
the requested peak plus reserve; it does not manufacture higher-speed ports.

Use `--previous` (or the third `just generate` argument) for growth. Existing
workload groups, site count and WAN demand may increase, and new workload keys may
be added without reallocating existing equipment, cables or addresses. Removed
workloads, reduced demand, changed resource/listener/replica policy and changes to
the network rack-diversity policy require a new baseline. An artifact with fewer
objects is not a deletion instruction for Diode.

Baseline and power-diversity demo intent, direct fabric cabling and no rack-user reservation are
implemented for this profile. Unsupported requests fail with an explanation.
The corrected example has 2,042 canonical objects, 2,106 wire entities and 15
requests. It passes SDK 1.14.0, initial and identical-repeat local Diode ingestion,
and strict readback: 7,340 attributes and 3,809 references, zero mismatches, all IDs
unchanged. Evidence is under `build/goal-live/enterprise/corrected/`. Actual path
inspection is recorded separately. This qualifies the pinned local stack with
its explicit patch, not every NetBox edition/version. Use the [local target
procedure](../lab/README.md); the default bank aggregate overlaps this profile, so a
new namespace alone does not permit coexistence.

## School district

```sh
just plan profiles/school-district.toml
just generate profiles/school-district.toml build/school-demo
just verify build/school-demo/plan.json
devenv --profile diode shell -- just sdk-check build/school-demo/diode
```

The example has a district service DC and two schools: 432 enrolled students,
42 staff, 124 installed student PCs, 21 APs and 10 service VMs. The report separates
people and planned wireless demand from actual wired inventory. A student using a
shared lab seat is already enrolled; wireless headcounts never create fake cables.

Each school has a stable `key`, six structural demand fields and an optional
local `wireless` demand map:

| Field | Meaning and bounds |
| --- | --- |
| `classrooms` | 1–32 rooms; eight per floor with a local closet on every occupied floor |
| `students_per_classroom` | 1–36 enrolled students per room |
| `wired_seats_per_classroom` | 0–12 student PCs, no more than enrolled students |
| `administrative_staff` | 0–48 staff in separate twelve-desk rooms; each classroom also has one teacher desk |
| `lab_seats` | 0–36 shared PCs, within enrollment remaining after classroom wired seats |
| `wan_peak_mbps` | Explicit per-campus peak, 1–800 Mbps, subject to reserve and the 1-Gbps edge limit |

Each classroom, administration pod and occupied lab retains a coverage AP;
additional local demand can add APs. Staff and students have separate WLANs/radios
and client VLANs; AP Ethernet management uses its own native VLAN. Guest demand
adds an open guest WLAN on the existing staff radio and a separate client segment.
Client VLANs traverse real access and distribution links to addressed gateways.
RADIUS UDP1812/1813 listeners in the district identity service anchor managed
authentication intent. See [local wireless demand](../profiles/school-district.md)
and the [shared power policy](modeling.md#wireless-demand-and-poe). RF coverage and running authentication are
outside the model.

Enrollment/staffing size complete replica groups for identity and the learning
portal; enrollment sizes file services; school count sizes DNS and monitoring.
The preview exposes the fictional thresholds. The shared DC allocator supplies
host/rack separation, capacity budgets, physical uplinks, power, serial access and
VM disks. Campus WAN uses purchased tiers while district DC WAN covers the sum of
campus peaks. The district shares one authored metro; premises and geometry are
fictional, with stable unique campus addresses.

School access grows in switch pairs per serving closet, retaining existing
endpoint port reservations. Radio channels derive from AP identities. Add keyed
schools or increase classroom, administrative-staff and lab counts with
`--previous`; growth across floors and service thresholds preserves old records.
Changing classroom design, reducing demand, removing schools or renewing campus
WAN procurement requires a new baseline. Keep the explicit WAN peak unchanged
when demonstrating campus growth under an existing purchased-capacity assumption.

Limits also include 64 keyed campuses, the selected private pool's `/16` site
reservations, `/20` network segments, 38 distribution attachments per campus,
eight management switch blocks per site, reviewed room/cable bounds and 16,000 Mbps
aggregate district WAN demand. Input maxima do not guarantee a fitting composition.
Unsupported demand produces an explanation before a complete artifact is published.

School supports baseline and power-diversity demo intent. Direct and panel
cabling use the shared paths; panels retain the pinned local compatibility limit
described below. The 3,716-object example passed local initial Diode ingestion,
strict readback and identical replay: 14,273 attributes and 8,175 references,
zero mismatches, all IDs retained. Its 3,967 wire observations use 15 requests.
Actual classroom, WLAN, VM/service, WAN and power paths were inspected through
native REST relationships and cable traces. Evidence is indexed in
`build/goal-live/school/summary.json`; this qualifies the same pinned local stack.

**Access cabling is explicit.** The default `patching = "direct"` represents
continuous switch-to-endpoint channels, abstracting intermediate passive panels.
`profiles/bank-panels.toml` includes full front/rear patch paths. The legacy
mapping fields previously had a source-checked NetBox **4.4.10** target; the whole
expanded estate is source-checked only against **4.7.0**. SDK 1.14/plugin 1.17
cannot preserve those mappings on an unpatched **NetBox 4.5–4.7** target. Use direct mode for current-version
qualification. This is a known integration gap, not a successful compatibility
test: [NetBox's new serializer](https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/api/serializers_/device_components.py),
[Diode field handling](https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/netbox_diode_plugin/api/transformer.py).

## Hospital and clinics

[The hospital example](../profiles/hospital-clinics.md) composes a hospital with
two wards, two unequal outpatient clinics and one service DC. Its recipe declares
bed stations, installed clinical/admin desks, exam/imaging rooms and facility
WAN peaks. Shared builders allocate the actual access, fabric, power and compute.

```sh
just plan profiles/hospital-clinics.toml
just generate profiles/hospital-clinics.toml build/hospital-demo
just verify build/hospital-demo/plan.json
devenv --profile diode shell -- just sdk-check build/hospital-demo/diode
```

Use `intent.json` to inspect supplied/default values down to each ward, and
`report.md` to follow medical endpoints, biomedical contacts, floor closets,
carrier escalation and the clinical-records/imaging-archive service hosts.
The [profile guide](../profiles/hospital-clinics.md) owns the exact input bounds,
authored sizing ratios and growth rules. One hospital supports up to seven
permanently assigned ward floors plus ground within the current management
hardware limit. Added demand retains existing rooms, attachments and history;
reductions or WAN renewal require a new baseline.

This composition models installed inventory and planning policy. It does not
run clinical applications or turn reference NIC endpoints into medical devices.
The example passed fresh Diode load, strict readback and identical replay of
4,383 objects with unchanged IDs, plus 44 native traces and rendered UI inspection.
The independent live audit accepted the milestone; source/receipt bindings and
walkthrough for the final source are in `build/goal-richness/hospital/live-final/`. The measured
202,033-object build is offline evidence only. Exact limits remain in [GOAL.md](../GOAL.md).

## Provider backbone

[Great Lakes Fiber](../profiles/provider-backbone.md) models a private-L3 operator:
paired PoP routers, physical inter-site circuits, customer tenants/VRFs and
premises, and a NOC with two PoP handoffs. The preserved v0.8 representative baseline
passed fresh initial/repeat strict readback of 4,344 objects, with unchanged
identities, 338 native GETs/93 traces and rendered UI inspection. The final-source
v0.8 220,308-object generation/export/SDK run is offline scale only. The final
v0.9 provider representative separately passed baseline/repeat and in-place
status-change/restoration qualification. Its 239,058-object measurement remains
offline only. [The current walkthrough](../lab/README.md#current-v09-qualification)
records exact scope; new recipes and target stacks still need their own checks.

```sh
just plan profiles/provider-backbone.toml
just generate profiles/provider-backbone.toml build/provider-demo
just power-scenario build/provider-demo/plan.json build/provider-power
```

Edit keyed PoPs and customer demand; ordinary growth appends equipment while
preserving occupied ports, links, addresses and journals. The recipe supports
3–64 PoPs and up to twelve direct customer/NOC attachments per PoP. Combined
demand must fit those ports and purchased headroom; unsupported demand fails
with an explanation. The profile guide owns the complete limits.

The report follows actual A/Z handoffs, service membership and support contacts.
PE management is in-band. Backbone connectivity and scoped spoke-to-hub traffic
checks do not establish customer access redundancy, routing convergence, or
total backbone capacity. Wireless is outside this initial wired composition.
The [hospital/provider coverage review](../COVERAGE.md) distinguishes included, partial,
intentionally omitted and unsupported capabilities. It ranks useful additions
and explains where wireless belongs; it is a backlog, not implemented scope.

## Repeatability and growth

An identical resolved recipe, seed, generator version, hardware catalog and
starting allocation/reservation/design ledgers produce identical plan and export
bytes. A fresh baseline and a grown estate can intentionally differ: growth keeps
existing placements and reservations instead of allocating again from an empty world. The seed changes bounded local details
(serial numbers, procurement dates and choices from the authored design pool);
it does not invent
unconstrained topology.

For an existing estate, edit a copy of its recipe to add sites, then use its
saved plan as the allocation ledger:

```sh
python3 -m estates generate profiles/bank-grown.toml \
  --previous build/bank-v9/plan.json --out build/grown
```

`bank-grown.toml` is your edited copy, not a shipped example. Existing site IDs,
addresses, rack positions, and physical attachments survive supported growth.
Removed sites keep their reservations. Decreasing counts omits records from the
new artifact; it does **not** delete them from NetBox. Changing a branch's size
means removing one immutable size/ordinal identity and adding another.

Changing the namespace, customer name, seed, address pool, optional IPv6 pool, reserve fraction, WAN tiers, design
mix, headquarters staff, patching mode, profile, observation date, generator
version, or hardware catalog requires an explicit new baseline. Provider matching
identities include the customer name; the same rename boundary applies to all
profiles. The final v0.8 operational-label cleanup changes the hardware catalog
fingerprint. Regenerate intermediate v0.8 examples as new baselines.
Omit `--previous` to start one. Keep old artifacts for exact reproduction:

```sh
python3 -m estates build build/bank-v9/plan.json --out build/reproduced
```

Existing design or ownership changes require the explicit scenario transition or
a new baseline. Before reusing a saved plan, the generator reproduces it from its
recipe and ledgers and checks that the objects and contracts match. This catches
edited allocation slots that could otherwise silently renumber an estate. Treat
plans as frozen outputs; edit recipes, not the saved graph. This integrity check
adds one reconstruction of the prior estate during growth or each transition.

Version 0.2.0 adds a catalog model and design ledger; v0.1 plans
must remain with their original generator/catalog for reproduction. Generate a
new baseline for v0.2 rather than feeding those old plans to `--previous`.

Namespaces separate estate identities and VRFs. Manufacturers and device types
are intentionally shared reference data; a namespace is not a target-side access
control or conflict-prevention boundary. ASNs and FHRP group numbers have global
matching identities. Local replay checks existing ownership before submission;
it refuses conflicts rather than renumbering. That observation is not a
concurrency lock: use an isolated target or reserve the namespace and numeric
identities with the instance owner when sharing a POC.
