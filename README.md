# devin-generator

Version 0.9 implements shared MAC identities, circuit accounts, direct equipment
contacts, bounded lifecycle journals, optional IPv6, applicable PoE/wireless and
installed optics across five profiles. All five representative artifacts passed
fresh initial and repeat Diode loading, strict readback and rendered UI inspection
on the [pinned local target](lab/README.md#current-v09-qualification). The provider
span-maintenance story adds actual customer attribution and directed capacity.
The objective and evidence index are in [GOAL.md](GOAL.md). The completed v0.8 goal and source
are archived under `build/goal-connected-depth/`; its hospital/provider live
receipts remain historical and do not qualify new output. Images are deferred.
Only capabilities documented below as implemented are available today.

Build a fictional, connected NetBox estate from a small recipe, then export it
for Diode. Regional banking, enterprise DC, school district, hospital/clinic and
provider backbone have generation profiles. Generation runs
offline using deterministic Python rules; no AI, API key, or server is needed.
Version 0.9 requires a new baseline. Do not pass a v0.8 or
earlier plan as `--previous` to v0.9. Preserve its source and receipts for historical
reproduction. Current qualification progress is recorded in [GOAL.md](GOAL.md).
Version 0.7 expands the connected model across physical equipment, IPAM, wireless,
VPNs and operations records. The rules retain demand-sized WAN procurement and
floor-based headquarters allocation. See `coverage.json` in each build for the
actual emitted types and [the qualification notes](lab/README.md) for live evidence.
The [community comparison](COMPARISON.md) distinguishes breadth from fidelity.
The [hospital/provider gap review](COVERAGE.md) shows what to deepen next.

All profiles use the same DC builder. Bank policy selects its services;
enterprise policy accepts workload groups, replica placement and resource demand;
school policy derives district services from enrollment and staffing. Bank and
school campuses also share aggregation/access construction. The qualified bank
canonical graph and Diode export were preserved during the completed v0.7 goal. Live qualification caught
a missing owner-group relationship in the shared enterprise/school records; the
corrected builder adds the group and reference. A shared power-diversity
scenario selects its subject from the graph; completed qualification and limits are tracked in
[GOAL.md](GOAL.md).

Bank DC checks independently derive required service, compute and power
obligations; omitted contracts cannot hide a missing listener, overloaded host
or loss of power diversity. Required power and compute paths also check inventory
status, connected uplink cables and enabled service interfaces. Missing rack
placement cannot suppress power checks. These checks establish modeled baseline consistency, not running service
availability or application recovery.

A pinned local NetBox/Diode target is now available for live qualification:
see [the local lab instructions](lab/README.md) for `just lab-up`, `lab-bootstrap`, `lab-load`,
`lab-verify`, `lab-down`, and the destructive `lab-reset`. It uses a dedicated
Colima VM on this Mac.

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
Change [profiles/bank.toml](profiles/bank.toml) to choose the branch mix, namespace,
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
official compatibility claim. See [lab/README.md](lab/README.md) for the clean
rebuild and preserved earlier failed replay.

## Optional IPv6

Optional IPv6 is implemented and qualified on the pinned local stack for all five
profiles; see [the receipts and limits](lab/README.md#current-v09-qualification).
In a copy of any recipe, add this **top-level** field before any TOML tables:

```toml
ipv6_pool = "2001:db8::/32"
```

Run `just plan <your-recipe>` and `just generate <your-recipe> <new-output>` as
usual. Leave the field absent for IPv4-only output. The pool must be an aligned
/32 through /40 inside the documentation ranges `2001:db8::/32` or `3fff::/20`;
for distinct estates, examples include `3fff:10::/32` and `3fff:20::/32`.
These are documentation addresses, not Internet allocations. Enabling,
disabling or changing the pool requires a new baseline: omit `--previous` and
keep the old artifact. Within that baseline, supported growth retains addresses.

The shared policy assigns /48 facilities, /64 LANs, /127 routed router links and
/128 PE loopbacks. Bank diagnostic radios use a /64; bank FHRP VIPs remain
IPv4-only. Unknown transit far ends remain unknown. Existing interfaces gain
IPv6 addresses and eligible device/VM primaries; services list both families on
the same object. `report.md` shows bounded primary and listener examples from
those actual references. This models address and listener intent, not executed
routing, RA/DHCPv6, IPv6 default-router failover or application configuration.

## Shared power-diversity demonstration

Generate a healthy estate using any implemented profile, then select a suitable
service host and build the demonstration:

```sh
just power-scenario build/enterprise-demo/plan.json build/enterprise-power
just scenario-check build/enterprise-power/scenario.json
devenv --profile diode shell -- just sdk-check build/enterprise-power/baseline/diode
devenv --profile diode shell -- just sdk-check build/enterprise-power/changed/diode
```

Alternatively set top-level `demo = "loss-of-power-diversity"` in a recipe.
`just plan <recipe>` previews a healthy baseline and the selected demonstration;
`just generate <recipe> <new-directory>` creates both snapshots and the scenario
report. `demo = "baseline"` remains the default. A frozen plan retains its demo
intent when passed to `python3 -m estates build`. Unsupported objectives fail with
the available choices. The existing acquisition/refresh command is unchanged.

Selection follows physical power paths and active VM/service consumers. The chosen
host must have two independent modeled supply paths and a compatible spare outlet.
The change moves one cord so both supplies share a PDU and upstream panel. Both
remain connected: inspect the wiring to find the hidden shared failure domain.
The report identifies the affected VMs, listeners and addresses from the graph.
An optional site filter uses `just power-scenario <plan> <output> <site-id>`.
If that site has no eligible host, generation explains the missing prerequisites.

Each scenario contains `baseline/` and `changed/` complete Diode artifacts,
`scenario.json`, `checks.json`, and a parent `report.md`. Scenario checking requires
exactly the expected code/object findings and proves that the inverse cable change
restores the frozen baseline. It deterministically re-exports both snapshots and
compares every Diode file, so a schema-valid payload cannot be substituted for
the checked graph. Any unrelated finding or package mismatch fails. Ordinary `just verify`
must reject `changed/plan.json`; it is deliberately defective. This snapshot is
also rejected as an ordinary growth seed.

**Use separate fresh disposable targets for the two snapshots.** Changing a cable's
termination changes its Diode matching identity; replay does not retire the old
cable. These artifacts do not implement an in-place rewiring or rollback command.
Offline restoration is proven against the frozen baseline; live restoration means
a fresh target loaded from that healthy snapshot until an actual transition is
qualified. Branching, approval, drift detection and running service failover are
not executed by this demonstration. Keep live receipts separate from offline checks.

## Enterprise data center

For a customer-shaped SE example, use [Harbor Supply: peak-season readiness](profiles/harbor-supply.md)
and its [recipe](profiles/harbor-supply.toml). It generates a distributor's two-DC
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
procedure](lab/README.md); the default bank aggregate overlaps this profile, so a
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
authentication intent. See [local wireless demand](profiles/school-district.md)
and the shared power policy below. RF coverage and running authentication are
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

## Wireless demand and PoE

Version 0.9 implements this shared capability. The bank, school and hospital
representatives passed [pinned local qualification](lab/README.md#current-v09-qualification),
including actual AP/power navigation. Existing bank APs receive the same power treatment.
Pure data centers and provider backbones receive no added wireless equipment.

School, hospital and clinic recipes accept counts within existing local zones:

```sh
just generate profiles/school-wireless.toml build/school-wireless
just generate profiles/hospital-wireless.toml build/hospital-wireless
```

These examples include passive copper paths and optional IPv6. To adjust one
existing zone, change only its counts:

```toml
# Under one [[schools]] entry, for an existing classroom:
[schools.wireless.classroom-001]
managed = 40
guest = 8
```

This requests concurrent device planning capacity. The zone needs two APs under
the authored 32-device/AP threshold, retains its first AP, and adds a guest VLAN,
prefix, gateway interfaces and trunk membership. Counts are 0–128 with a combined
maximum of 128 per zone; the current geometry supports at most four AP mounts.
Omitted zones receive explicit local defaults in the resolved recipe. Unknown
zones and decreasing frozen demand fail with guidance. See the
[school](profiles/school-district.md) and [hospital](profiles/hospital-clinics.md)
guides for legal zones and defaults. No wireless client rows or associations are
fabricated from these counts.
Managed demand sizes AP equipment in aggregate across managed WLANs, without a
per-SSID address-admission check. Guest demand maps to one guest segment per
facility and must fit its available IPv4 capacity after existing allocations.

The reference AP reserves 30 W at its actual serving Type 2 PSE port. Cisco
C9200L access switches plan against 370 W after losing one of two compatible
600 W supplies; EX3300-24P uses its 405 W fixed-supply PoE budget and claims no
supply redundancy. These are separate from copper port headroom. New demand can
therefore add a switch pair when power binds, while wired devices can still use
unused copper ports. Existing endpoint reservations never move.

Inlet planning draws add connected PD reservations once, with an explicit 1.25
upstream AC allowance. Independent checks trace copper/passive paths, port
eligibility, installed supplies, per-port/whole-switch limits and feed diversity.
[The catalog](catalog/README.md) distinguishes vendor limits from reference
hardware and authored planning allowances. Neither these values nor AP locations
are measured power, RF coverage or throughput guarantees.

Open a WLAN to find its client prefix, technical desk and specific DNS/RADIUS
service VMs; the generated report also shows remaining address capacity.
Managed WLANs declare authentication
dependencies; open guests do not. Inherited bank sites keep their own external
service boundary until acquisition. These references guide a demo walkthrough;
they do not configure DHCP, routing, authentication or guest isolation.

## Installed optics policy

Optics are generated automatically from the actual host, named cage, configured
speed and connected medium across all five profiles. No new recipe field or
manual NetBox insertion is needed. For example:

```sh
just generate profiles/school-wireless.toml build/school-optics
```

Use `report.md`'s **Installed optics and local paths** section to answer “Which
part is installed here, what does it connect to, and who would coordinate work?”
Open the named interface, follow its installed module and bay, then inspect the
module type's source-linked attributes. The report shows actual support contacts
and complete known local cable paths, including passive ports when present.
All five final representatives passed initial/repeat live ingestion and native
inspection on the pinned stack. The final offline scale and SDK results are
indexed in [GOAL.md](GOAL.md); these do not prove optical loss budgets or a
large live load.

The finite [catalog](catalog/README.md) selects LR/LX/LR4 parts for reviewed
Cisco, Juniper, Arista and Fortinet cages, plus an explicit reference-server
transceiver. A configured 1G MX204 handoff receives LX even though its cage can
also carry 10G. Existing three-meter Arista peer links use `AOC-Q-Q-100G-3M`:
one active optical cable, two captive end modules, one shared assembly serial.
The existing cable label remains stable and its comments show the assembly
serial. The two end records are not two independently replaceable purchases.
Unused cages remain empty; fixed copper ports do not receive optical modules.

Source-backed host fit, protocol and medium are separate checks. The authored
local SMF envelope is 3–100m; catalog reach is not an optical loss budget or
measured receive power. A circuit termination ends the local path: it does not
reveal the carrier's intercity span or remote optic. Source records retain
indexed-only retrieval limits, software/FEC conditions and reference-host
assumptions. No additional RF, breakout, DWDM or applied routing behavior is
implied.

Power includes all installed optical modules, even when inactive or disconnected.
For each host, add `ceil(sum(module/end mW) × 1.25 / 1000)` watts once to the
separate chassis and PoE allowances. The catalog distinguishes vendor maxima
from conservative authored reservations; each AOC end reserves 3.5W rather than
claiming a verified whole-assembly power split. This is planning reserve, not
measured consumption or an efficiency calculation.

Optical preparation journals use a fixed catalog cage on each rack's permanent
equipment anchor. They identify the installed part and stable facilities desk;
new occupied ports do not rewrite older journal text. Native
[`Interface.module` ownership](https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/models/device_components.py)
uses cascading deletion. Preserve these records during growth: no module removal,
hot-swap or executed optic replacement is modeled.

## Hospital and clinics

[The hospital example](profiles/hospital-clinics.md) composes a hospital with
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
The [profile guide](profiles/hospital-clinics.md) owns the exact input bounds,
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
202,033-object build is offline evidence only. Exact limits remain in [GOAL.md](GOAL.md).

## Provider backbone

[Great Lakes Fiber](profiles/provider-backbone.md) models a private-L3 operator:
paired PoP routers, physical inter-site circuits, customer tenants/VRFs and
premises, and a NOC with two PoP handoffs. The preserved v0.8 representative baseline
passed fresh initial/repeat strict readback of 4,344 objects, with unchanged
identities, 338 native GETs/93 traces and rendered UI inspection. The final-source
v0.8 220,308-object generation/export/SDK run is offline scale only. The final
v0.9 provider representative separately passed baseline/repeat and in-place
status-change/restoration qualification. Its 239,058-object measurement remains
offline only. [The current walkthrough](lab/README.md#current-v09-qualification)
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
The [hospital/provider coverage review](COVERAGE.md) distinguishes included, partial,
intentionally omitted and unsupported capabilities. It ranks useful additions
and explains where wireless belongs; it is a backlog, not implemented scope.

## Provider span maintenance

Use the [maintenance recipe](profiles/provider-maintenance.toml)
for an SE story about a regional operator taking one leased inter-PoP span out
of service while supporting Harbor Logistics and Cedar Retail:

```sh
just plan profiles/provider-maintenance.toml
just generate profiles/provider-maintenance.toml build/provider-maintenance
just scenario-check build/provider-maintenance/scenario.json
```

The three PoPs, two customer hubs, IPv6 addresses and panel-cabled office access
are ordinary generated inventory. Offered customer traffic is intentionally
modest: 50 Mbps per Harbor spoke and 30 Mbps per Cedar spoke. The useful question
is **which premises reroute, and what protection is lost during maintenance?**
The example does not manufacture congestion.
Its panel access channels use the existing [local front-port compatibility
bridge](lab/README.md#opt-in-local-front-port-compatibility) on the pinned NetBox 4.7 target. Choose `patching = "direct"`
for a new baseline when that mapping support is unavailable.

Generation selects an existing active leased span used by modeled customer
traffic. It writes `baseline/` and `changed/` complete artifacts, `scenario.json`,
`checks.json`, and a parent `report.md`. The change is exactly one Circuit's
status, `active` → `offline`. Its matching identity, A/Z terminations, cables,
interfaces, optical modules, power reservations, contacts and journals remain
unchanged. Set `demo = "provider-span-maintenance"` only on provider recipes.

For an existing healthy provider plan, the same operation is:

```sh
just span-scenario build/provider-demo/plan.json build/span-maintenance
just scenario-check build/span-maintenance/scenario.json
```

An optional third argument pins a canonical span key from the graph, such as
`circuit/backbone/seed-01`. Unused spans, customer/transit circuits and missing or
unavailable subjects are rejected. Saved scenario checking retains its selected
span; creating a new scenario after growth can choose a different one.

Walk the parent report from maintained Circuit and carrier contact to the actual
rerouted customer premises, their hubs, baseline and alternate PE paths, and
per-direction load/headroom. Follow the customer, operator and facilities
contacts on those real records. The report distinguishes rerouted premises from
unchanged routes sharing additional load. Capacity covers declared customer
spoke-to-hub traffic only; it excludes return, arbitrary peer-to-peer, NOC and
transit demand. This is modeled routing, not measured forwarding or convergence.
Carrier-wide failures are outside the checks. Per-PoP carrier diversity is not
guaranteed; the baseline report shows each PoP's actual span providers, including
shared-carrier exposure. Different provider names do not establish duct diversity.

`scenario-check` verifies the exact status-only diff, recomputed paths/capacity,
expected findings and byte-identical inverse restoration. Its changed-state
label is `expected-maintenance verified`. Ordinary verification of
`build/provider-maintenance/changed/plan.json` must fail: the baseline's active-span
and resilience obligations are deliberately unmet. In this three-PoP story,
the current declared flows still fit; further link/router failures can now break
connectivity. Those findings describe lost additional-failure protection, not a
present modeled customer outage. Their count and affected objects come from the
graph, not a fixed example list.
Protection depends on the selected span and finished topology. Independent
five-PoP examples include a span that retains every checked further-failure
witness and another in the same mesh that loses two; mesh size alone is not a
guarantee. Use the scenario's actual result.

**The status-only sequence passed on the pinned local stack.** Baseline/repeat
→ offline/repeat → baseline restore/repeat retained all 4,350 object IDs and
target inventory, with zero mismatches across 14,953 attributes and 8,571
references at each readback. Actual NetBox pages showed Offline, then Active,
on the same Circuit. See [execution commands and receipts](lab/README.md#provider-status-sequence).
This is inventory status reconciliation; no router configuration, approval,
forwarding or failover execution is added. Other stacks and cable rewiring
remain unqualified. Generated scenario envelopes conservatively describe what
generation itself proved; this separate receipt establishes the local execution.

## Connected model families

The detail profile exercises each of the 101 estate candidate kinds in the
pinned type audit. These are linked examples with different depths, not 101
complete simulations:

| Family | Connected example |
| --- | --- |
| Physical equipment | Source-backed PSU and optical modules in compatible bays, linked to existing power/interface records; room-local serial consoles; real StackWise ports and a master |
| Cooling and serviceable parts | Explicitly fictional analytics enclosure and blade, replaceable cold plate, rack coolant feed, source and acyclic intake/outflow chain |
| IPAM and HA | Private ASN ranges and site/provider ASNs, aggregates, roles, route targets, reserved ranges, and one valid VRRPv3 gateway pair with its shared address |
| Wireless | Site-scoped staff WLAN groups, actual radio interfaces and tagged user-VLAN access paths; a separate routed diagnostic hop |
| Carrier and recovery services | Virtual circuits over real WAN handoffs; planned IKE/IPsec tunnel and translated VXLAN recovery segment between the DCs |
| Operations | Contacts/owners, commercial accounts, restoration groups, VM disks/types, cabinet types/groups/reservations, a typed custom choice, journal entry and contextual link |

`report.md` gives graph-derived starting questions. `coverage.json` lists every
kind and an example key, including the remaining native/SDK gaps. The planned
recovery design has no production workloads or deployed encryption. The wireless
hop is a diagnostic example, not an RF survey. These distinctions are explicit
in the generated records.

NetBox4.7's aggregate overlap constraint is global across RIRs and tenants.
The generator collapses overlapping owned allocation pools; local preflight
rejects overlap with a different existing aggregate. A fresh namespace alone
cannot make global aggregates independent. Sharing existing aggregate definitions
across arbitrary POC inventories needs an explicit binding design.

## Contact and journal context

All five profiles derive operational context from the same rules. Technical
desks follow tenant ownership; site facilities desks handle local access and
power-work coordination; carrier desks follow each circuit's provider; service
desks follow the VM's workload and tenant. Distinct responsibilities have separate
primary assignments. The directory uses descriptive `.example` mailboxes and
does not create user accounts or configure notifications, coverage hours or SLAs.
Network infrastructure, APs and hosts expose their actual tenant's technical desk
directly on the device. Hospital medical/imaging equipment retains its distinct
site biomedical responsibility. Addressed physical and VM interfaces receive
stable primary MAC identities; virtual/bridge interfaces are excluded.

Every circuit has its appropriate provider account. Bank procurement distinguishes
the retained Birch portfolio even after acquisition or access-hardware refresh;
provider customers, NOC, transit and transport retain separate accounts. Changing
technical ownership does not silently renew a commercial contract.

Each site has a dated site record and access-coordination note; each circuit has
a capacity-request and handoff-plan note; the first VM in each site/workload has
resource and listener plans. Facts come from the actual address, provider,
capacity, host and service records. The dates describe authored planning history;
NetBox's native journal creation timestamps describe ingestion. No entry asserts
that a change, acceptance test or application health check was executed.

The first eligible infrastructure device by permanent U position in each rack
has an installation record and maintenance plan. Where that device has an
installed PSU module, a third entry identifies its model, serial, bay and owned
power port for replacement preparation. These records refer to stable placement
and the site facilities desk; the current technical owner and upstream power
paths are consulted when work is planned. They do not invent a spare or promise
hot replacement. New racks gain stories without rewriting existing rack history.
When that device's first fixed optical cage is occupied, a separate optical
preparation entry names its interface, installed part, serial and bay. The fixed
cage is chosen before occupancy; later port growth cannot change the note's
subject. See [installed optics](#installed-optics-policy) for the assembly and
native deletion limits.

Ordinary growth retains existing journal text and contact identities. This matters
because Diode matches a journal by its subject and exact comments. Inventory totals
and current headroom stay in the report, where they can change without rewriting
history. Acquisition remains a reviewed snapshot change; updating responsibility
does not imply Diode will retire the former contact assignment in place.

`report.md` includes a bounded contact/journal tour with actual object references.
Open a site's Contacts and Journal views, follow a circuit to its carrier desk,
then inspect a workload VM's service desk and resource/listener notes. The full
directory and notes remain in the canonical plan and normal Diode package.
The shared bank/DC/school milestone passed independent offline review and live
Harbor qualification: 3,331 objects match initial/repeat strict readback with
unchanged IDs. See `build/goal-richness/live/summary.json` and `walkthrough.md`
for historical source/receipt bindings. Final hospital and provider evidence is
under their respective `live-final/` directories, indexed in [GOAL.md](GOAL.md).
Images are deferred; no image renderer, uploader or additional credential is needed.
The [coverage review](COVERAGE.md) explains wireless relevance and ranks the next
useful additions across physical, network, service and operational depth.

## Physical-detail iteration

`profiles/bank-depth.toml` builds the same mixed bank demand under the separate
`cedar-complete` namespace with full cabinet-panel/wall-outlet paths:

```sh
just generate profiles/bank-depth.toml build/bank-complete
devenv --profile diode shell -- just sdk-check build/bank-complete/diode
```

The placement grammar supplies United States → Great Lakes → state regions,
site groups and IANA time zones. Building → floor → room locations place staff
in 12-desk office pods, retail counters on the ground floor, and branch devices
in their appropriate spaces. Headquarters has reception and office floors. Addresses,
premises and dimensions are explicitly fictional. `estates/places.py` owns these
authored rules; extending an industry or footprint means adding reviewed rules
and constraints, not requesting AI to fill individual records.

Every endpoint has a room and local position. Copper routes run to its serving
equipment room; fiber backbone routes connect equipment rooms. In panel mode the path is switch → 24-position cabinet panel →
single-port room outlet → endpoint, with three cables, two passive mappings and
all unused panel positions present. The independent checks reject wrong rooms,
cross-site placement, room overcapacity, floor disagreement, bypassed outlets,
and cables outside the authored route limits. Stable site IDs determine geography;
growing the estate or refreshing access hardware leaves placement intact.

Rack roles and device-role colors make elevations easier to interpret. Racked
infrastructure carries explicit synthetic power allowances, split across its
installed supplies, with full allowance on either feed for failover. These are
planning inputs; connected PoE reservations additionally use the catalog supply
budgets and an authored upstream AC allowance. They are not measured consumption. Service
VMs have roles and a fictional Linux platform; services bind their own VM's IP.
Descriptions use readable object names and purposes instead of internal keys.

**NetBox 4.7 panel qualification requires an opt-in local Diode plugin patch.**
It translates the SDK's existing one-position mapping through Diode's normal
reference resolution and NetBox's native serializer. It refuses mapping removal
or replacement. The exporter continues to report the unmodified official plugin's
incompatibility; the local harness verifies the exact patched sources before
allowing panel replay. This is not an official support claim or a second loader.
See [lab/README.md](lab/README.md) and [the patch boundary](lab/patches/README.md).

Version 0.4 requires a new baseline: names and small-branch connections changed.
Keep older artifacts with their original code/catalog; archived local sources are
`build/generator-v0.2-source.tar.gz` and `build/generator-v0.3-source.tar.gz`.
Use a fresh namespace or fresh disposable target when loading the new layout.
Version 0.5 also requires a new baseline because headquarters demand, placement,
and attachments changed. Its archived predecessor is `build/generator-v0.4-source.tar.gz`.
The older `build/bank-depth` receipts describe frozen v0.3 `cedar-depth`;
`build/bank-demo` and `build/bank-demo-growth` describe v0.4 `cedar-demo`.
The frozen `build/bank-campus` estate describes v0.5 `cedar-campus`.
Version 0.6 also requires a new baseline: WAN commitments, procurement dates and
provider descriptions changed. Keep `build/generator-v0.5-source.tar.gz` with
that predecessor. Version 0.7 also requires a new baseline; its detail profile uses
`cedar-complete`, preserving the earlier `cedar-wan` namespace.

Device names are site-local: `s0002-atm01`, `birch-s0002-as01`, and replacement
`s0002-asr01`. `s`/`m`/`l`, `dc`, and `hq` preserve site identity; canonical keys
and allocation ledgers remain separate from display names. Diode references
retain both site and tenant, matching its
[scoped device criteria](https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/docs/matching-criteria-documentation.md).
DNS retains the full namespace, such as `birch-s0002-atm01.cedar-demo.example`.
Synthetic device asset tags are omitted because
[NetBox displays them beside the name](https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/models/devices.py),
which previously duplicated long labels. Serial numbers remain descriptive data;
they are not Diode matching identities. Acquisition still requires explicit target
reconciliation, particularly when an unracked device's tenant changes.

Panel cable labels use `P` for cabinet patch cord, `H` for horizontal run, `R`
for room cord, and `D` for an abstracted direct channel. Other cable numbers use
persisted per-site reservations. Room and purpose descriptions retain the detail.
`MDF`, `N01` and `C01` shorten equipment-room and rack breadcrumbs. Native trace
SVGs still have a fixed default width; long vendor/model or site text can clip.

## Building-driven headquarters

`headquarters_staff` accepts 24–192 staff positions; the default is 180. One
12-desk office pod is added for each started group of twelve, and four pods fit
on a floor. The footprint therefore determines one to four occupied floors.
Each occupied floor has its own equipment room: the ground-floor MDF and
`IDF 02`, `IDF 03`, and `IDF 04` as needed. Each pod gets one modeled AP, each floor
two cameras, and headquarters WAN planning demand is two Mb/s per staff position.
These are authored sizing assumptions, not measured traffic or RF coverage.

Access capacity is allocated per equipment room, with at least two switches and
the recipe's spare-port budget. Endpoints connect to switches and optional patch
panels in their floor's room. Every access switch has two direct fiber uplinks to
the MDF distribution pair. Management switches serve local equipment and use
fiber back to the MDF; their uplinks and the endpoints themselves remain
single-homed. Inter-room fiber lengths follow room coordinates plus ten metres
of modeled routing/slack. These are direct-terminated links; fiber distribution
panels, strand bundles, separate risers and optical-loss budgets are not modeled.

Each room has rack-local PDUs and separate modeled A/B distribution panels.
The validator checks device/PDU/rack locality and panel-room consistency as well
as capacity. Separate panel objects do not establish independent utility feeds.
Rack names such as `N01` repeat in different rooms; references and asset tags
retain room identity. Equipment names include the room, e.g. `hq01-idf02-as01`.

To compare different footprints, copy a profile and change `headquarters_staff`.
Changing staff in an existing headquarters requires a new baseline; ordinary
`--previous` growth retains that demand and can add sites. In-place remodeling
needs its own transition rules. The report derives room coverage, serving access
switches and fiber backbone from the actual generated connections.

## Demand and WAN purchasing

`wan_tiers_mbps = [50, 100, 200, 500, 1000]` declares fictional commercial tiers.
For each branch or HQ circuit, choose the smallest tier whose capacity after the
recipe's reserve covers the whole site's peak demand. Carrier A has a 50 Mb/s
minimum; carrier B has a 100 Mb/s minimum. Birch-lineage branches retain a
200 Mb/s bulk-contract minimum on both carriers, including after acquisition
and access-switch refresh. These are authored procurement conventions.

With the default 20% reserve, a 20 Mb/s modern branch buys 50/100 Mb/s; a Birch
branch buys 200/200 Mb/s. A medium branch buys 100/100 Mb/s, a large branch
200/200 Mb/s, and the 180-person HQ buys 500/500 Mb/s. Both terminations and the
edge's physical handoff stay at 1 Gb/s. CIR is purchased bandwidth, not port
speed. Each DC retains full-rate aggregation circuits and adds edge pairs when
aggregate branch/HQ demand requires them. Tier selection and DC pair sizing use
exact decimal reserve arithmetic at capacity boundaries.

Every circuit carries a deterministic installation date and fictional purchasing
reason in native NetBox fields. Dates fall 1–3 years before `as_of` for Cedar
contracts, 6–8 years for retained Birch contracts, and 4–5 years for DC aggregation
(using 365-day planning years). They describe a synthetic baseline history;
they do not create historical NetBox change events or real carrier quotations.
Dates, rates, identities and procurement descriptions survive ordinary growth.
An access-hardware refresh leaves WAN procurement intact.

Tiers must be positive, strictly increasing integer Mb/s values ending in 1000,
the supported DC handoff ceiling. Changing tiers in an existing plan requires a
new baseline; in-place contract renewal is a future explicit transition.
The report shows purchased capacity, the actual connected handoff bottleneck,
installation dates and procurement reasons. Independent checks reject
undersized/off-tier contracts, invalid dates and disconnected handoffs even when
metadata claims enough capacity. The two provider interiors remain abstracted;
Internet access, new regional carrier domains, forwarding and last-mile diversity
need additional rules before they can be represented coherently.

## What makes it a bank

| Site block | Declared demand and resulting design |
| --- | --- |
| Small branch | 12 workstations, 2 ATMs, 2 APs, 2 cameras; 20 Mb/s peak |
| Medium branch | 36 workstations, 4 ATMs, 4 APs, 4 cameras; 50 Mb/s peak |
| Large branch | 84 workstations, 6 ATMs, 8 APs, 8 cameras; 100 Mb/s peak |
| Headquarters | Default 180 workstations, 15 APs, 8 cameras; four floors; 360 Mb/s planned peak |
| Each data center | Dual spines, paired leaves, demand-sized WAN access and compute pools; eight service families represented in both DCs |

These are authored example design assumptions, not banking standards or vendor
performance specifications. Small branches use two WAN edges as their VLAN
gateways and two access switches, with a third-uplink trunk between the access
switches. Medium/large branches and headquarters retain a separate distribution
pair and size the access layer from endpoint demand with spare ports. Both
architectures use two fictional carriers. Panel mode routes endpoint cables through front/rear
patch ports; direct mode preserves the channel's endpoints without passive hops.
Every declared physical hardware interface is present, including unused ports.
Modern access and other contracted dual-supply infrastructure have A/B power
paths through modeled inlets, outlets, feeds, and separate panels. Inherited
access deliberately has one supply and one direct upstream per switch. In a
small branch, its peer trunk also provides an indirect edge path; the refresh
adds direct attachments and a second supply rather than claiming that no backup
path previously existed.

Small-branch gateway interfaces have explicit logical bridge membership and
VLAN-interface parents. The validator checks these relationships alongside the
physical peer trunk, carried VLANs, gateway addresses and direct upstream count.
These express network intent, not a FortiOS configuration or measured forwarding,
STP, firewall or HA behavior. NetBox's
[grouped bridge model](https://netboxlabs.com/docs/netbox/models/dcim/interface/#bridged-interface)
does not extend physical cable traces through the gateway. Gateway management IPs
live on their management VLAN interfaces; their dedicated management ports remain
unused. Other infrastructure keeps its separately connected management ports.

Addressing uses stable /20 reservations per site and /24 allocations per network
role, with scoped VLANs and VRFs. DC demand grows WAN attachments and service
replicas, then the equipment and fabric needed to carry them. VMs have explicit
hosts, clusters, IPs, CPU, memory, disk, and service endpoints. DNS has TCP/UDP 53;
ledger databases have TCP 5432. Other application endpoints are illustrative.
VM disk values use NetBox's MB unit, consistent with its
[virtual disk model](https://github.com/netbox-community/netbox/blob/v4.7.0/docs/models/virtualization/virtualdisk.md)
and [VM aggregate disk validation](https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/virtualization/models/virtualmachines.py).

The [hardware catalog](catalog/README.md) distinguishes pinned community vendor
definitions from fictional reference servers, PDUs, panels, and endpoints.

## Procedural design, without AI

We author reusable rules and hardware inventories; the program expands and
connects them. This borrows from Minecraft's [jigsaw templates and weighted
pools](https://learn.microsoft.com/en-us/minecraft/creator/documents/structures/introductiontojigsawstructures?view=minecraft-bedrock-stable)
and [hierarchical building grammars](https://doi.org/10.1145/1179352.1141931).
It is currently two branch access architectures plus a refresh transition,
not a general city generator or a library of arbitrary network topologies.

The process is recipe → persistent site/design assignments → endpoint and service
demand → equipment capacity → real ports/racks/addresses/cables → independent
graph checks → Diode export. For example, the three branch sizes demand 18, 48,
and 106 endpoints. At the default spare-port budget, that produces two, three,
and six access switches. Each switch contributes its actual catalog port names;
the builders allocate connections to those ports and fail on exhaustion.

| Design | Equipment and connectivity | Naming, addressing, and ownership |
| --- | --- | --- |
| `modern` | Cisco C9200L-24P-4X; two direct upstream neighbors and two supplies | Site-local names and corporate address plan |
| `inherited` | Juniper EX3300-24P; one direct upstream neighbor and one supply per switch | Birch names, separately scoped VRFs and /20 reservations from 172.16.0.0/12; independent Birch ownership until acquisition |
| `refreshed` | New Cisco access identities, two direct upstream neighbors and two supplies | Acquired Cedar ownership; Birch endpoint names and addressing retained |

These are fictional procurement designs, not vendor lifecycle or performance
claims. Both switch models have 24 access ports in the selected configuration.
Small branches route at the WAN edge; larger footprints use distribution pairs.
This architecture follows immutable site size, while the procurement design
controls access hardware, direct attachments, supply count and retained lineage.

`[design_mix]` supplies weights for a deterministic per-site choice; weights are
not exact estate-wide quotas. `[site_designs]` pins particular examples:

```toml
[design_mix]
modern = 60
inherited = 25
refreshed = 15

[site_designs]
br-s0002 = "inherited"
```

IDs encode immutable size and ordinal (`br-s0002`, `br-m0001`, `br-l0001`). The
default recipe pins one branch of each design so a small walkthrough always has
useful examples. New-site choices are recorded in `design_assignments`; adding
branches does not reroll the old estate. Broadening the library means adding
reviewed connected designs and their checks, not randomly mixing vendor names.

## Acquisition and refresh walkthrough

After `just generate`, run:

```sh
just scenario
```

This selects the default inherited branch `br-s0002` and writes
`build/acquisition-refresh/report.md`, three complete frozen snapshots under
`before/`, `acquired/`, and `refreshed/`, plus `scenario.json` and `checks.json`.
`scenario.json` contains the create/update/delete records, ownership changes,
expected answers, exact physical-path evidence, and snapshot file names/digests.
Other eligible branches can be selected explicitly:

```sh
python3 -m estates scenario build/bank-v9/plan.json \
  --site br-s0002 --out build/another-walkthrough
```

Acquisition changes ownership while preserving hardware and connections. Refresh
replaces access devices with new identities, reconnects the same endpoints, and
checks their names, IPs, DNS, VRFs, and assignments against the baseline. It also
checks that every changed record belongs exclusively to the selected branch;
shared definitions and objects at other sites remain unchanged. Old equipment's
allocation slots stay reserved, so replacing it does not move existing equipment.
The report answers which endpoints depend on each replaced switch and shows the
new upstream and supply paths by inspecting the finished graph.

**These are candidate snapshots and changes, not an applied migration.** Tenant
changes can affect Diode natural matching identity, and replay does not delete
the retired records. The scenario command therefore writes review artifacts,
not a transition replay script. A frozen snapshot can be exported with `build`
for qualification in a fresh disposable instance; that does not qualify applying
the transition to an already populated target. No synthetic audit log, NetBox
branch, approval, or live lifecycle history is created.

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

## Artifacts and Diode

| File | Purpose |
| --- | --- |
| `plan.json` | Frozen resolved recipe, graph, allocation/design ledgers, hardware digest, and profile assertions |
| `report.md` | Human walkthrough derived from that graph |
| `intent.json` | Resolved inputs, supplied/default provenance and explicit planning assumptions |
| `coverage.json` | Pinned model coverage, actual counts/example keys, external bindings and native SDK gaps |
| `checks.json` | Offline validator result and plan digest; explicitly records live ingestion as untested |
| `diode/manifest.json` | Checksums, counts, deterministic request IDs, dependency phases, and replay notes |
| `diode/phase-*.json` | Diode dry-run replay requests; at most 1,000 entities and 2,500,000 JSON bytes each |

The export contract is pinned to **netboxlabs-diode-sdk 1.14.0**. The optional
devenv `diode` profile installs it in a devenv-managed environment:

```sh
devenv --profile diode shell
just sdk-check build/bank-v9/diode
just check
```

The SDK check verifies manifest integrity, protobuf parsing, actual encoded
request sizes, and the validation rules embedded in the pinned descriptors.
It reports the known target limitations from the manifest. It does not contact
Diode or NetBox; a passing SDK check does not resolve the panel mapping gap.

For a target already configured with Diode, use the SDK's existing
[dry-run replay helper](https://github.com/netboxlabs/diode-sdk-python/blob/v1.14.0/netboxlabs/diode/scripts/dryrun_replay.py).
Supply `DIODE_CLIENT_ID` and `DIODE_CLIENT_SECRET` through your existing credential
environment, and `DIODE_TARGET` for that target. A plain NetBox URL/token alone
is not this helper's authentication contract.

```sh
python3 -m netboxlabs.diode.scripts.dryrun_replay \
  --target "$DIODE_TARGET" --app-name devin-generator --app-version 0.9.0 \
  build/bank-v9/diode/phase-001-*.json
```

Replay one manifest phase at a time, checking **successful reconciliation**
before proceeding to the next. The helper reports ingest responses; it does not
wait for all NetBox changes to complete. Primary IP assignments are a final phase
because their interfaces and addresses must already exist. Saved phase files can
be resubmitted by the SDK helper, but target recovery is a separate concern.
The pinned local `lab-load` harness rejects historical failed ingestion logs;
its [recovery procedure](lab/README.md#recover-a-failed-local-ingestion) uses a
corrected artifact, disposable reset, and new bootstrap receipt before a full load.
Qualify reconciliation and replay on the intended
target; the local result below covers one pinned combination. Replaying a
baseline can reverse demo edits.
The saved request IDs identify reproducible artifacts; the replay helper rebuilds
its own request envelope from the saved entities.

The recipe's `as_of` is a synthetic observation date, preserved in replay. It
must be in the past for SDK timestamp validation. Do not silently replace it
with the current time. Wire format and limits are based on the
[Diode protocol](https://github.com/netboxlabs/diode/blob/diode-reconciler/v2.2.0/diode-proto/diode/v1/ingester.proto);
scoped references follow the plugin's
[matching criteria](https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/docs/matching-criteria-documentation.md).

## Checks and present limits

`just check` runs independent graph mutation tests plus determinism, growth,
namespace isolation, capacity exhaustion, design-specific topology, acquisition/
refresh preservation, CLI artifact preservation, and a
250-branch regression exceeding 100,000 objects. SDK-specific tests explicitly
skip outside an environment with that optional dependency.
The included GitHub Actions workflow runs this suite and complete default and
panel build/SDK checks on Python 3.11 and 3.14 when the repository is published;
it has not yet run on GitHub. This is an offline matrix, not a live NetBox matrix.

Qualification receipts are local build artifacts. SDK-specific checks record the
SDK version, entity/request counts, encoded sizes, and source-checked target;
these are not ingestion throughput or demo-fidelity measurements.

The v0.6 suite passed 166 generator/qualification tests and five setup tests with
SDK 1.14.0 on September 9, 2026 (`build/v06-full-check.log`). Seventeen WAN tests
cover tier minima, exact capacity boundaries, both handoff ends, disconnected
paths, malformed rates, procurement dates and growth/refresh preservation.
An independent sweep passed 360 configurations, 5,760 circuit checks, three growth
cases and 24 acquisition/refresh scenarios; a final validator follow-up passed
61 affected tests. Receipts are `build/v06-independent-review.json` and
`build/v06-independent-review-followup.json`.

The frozen `build/bank-wan` panel estate contains 12,219 objects and 20 circuits.
Generation/validation/export took 0.415s and SDK verification 3.307s locally
(`build/v06-build-timings.json`). An offline addition of 17 small branches grew
it to 27 sites, 21,829 objects and 58 circuits. Aggregate peak rose from 640 to
980 Mb/s, adding a second WAN edge pair at each DC. Every existing circuit and
termination remained unchanged; exactly 12 existing DC interfaces gained new
attachments. The reviewed candidate and SDK result are in
`build/bank-wan-growth/` and `build/v06-growth-plan-review.json`; that growth
candidate has not been ingested.

Initial v0.6 Diode replay completed 14 phases and 12,837 entities in 400.325s
across phases, with zero failures. Strict readback matched every object,
47,336 attributes, 27,177 references and 1,245 front/rear mapping tuples, with
zero mismatches or unexplained records (`build/v06-initial-replay.json` and
`build/v06-initial-readback.json`). The fixed pre-load inventory receipt is
`build/v05-before-v06-readback.json`. Native NetBox circuit views displayed the
50/200/500 Mb/s examples, their installation dates and purchasing explanations.
The HQ edge-to-circuit/provider-network trace completed with one 3 m cable;
`build/v06-ui-walkthrough.md` records the navigation and scope.
Identical replay then completed in 134.887s. All phases retained the initial
replay's final metrics (73,119 reconciled, zero failed/queued), with no additional
ingestion-log rows (`build/v06-repeat-replay.json`).
Repeat readback retained every ID and all 1,245 mapping tuples, with complete
emitted-field/reference coverage. Target inventory remained exactly 69,510 IDs.
The same paginated REST inventory was checked against all five older plans:
8,432 v0.2, 2,655 v0.3 pilot, 12,528 v0.3 depth, 21,541 v0.4 grown and 12,219
v0.5 objects. All retained their IDs, emitted data and mapping tuples.
`build/v06-final-comparison.json` links the six successful readback receipts and
records the shared inventory digest. The local-only driver
`build/v06-final-readbacks.py` reused `lab.verify.fetch_inventory` and
`verify_plan`; no loader or target API implementation changed. No target reset
or direct estate writes were used.

The v0.5 suite passed 149 generator/qualification tests and five setup tests with
SDK 1.14.0 on September 9, 2026 (`build/v05-full-check.log`). Fifteen new building
tests cover floor-local capacity and actual endpoint paths, fiber geometry,
management backbones, local power, rack name scope, independent demand checks,
and growth preservation. An additional review swept 72 combinations of staff,
reserve and cabling mode; all passed offline validation.

The frozen `build/bank-campus` panel estate has 12,219 objects. Its HQ contains
203 endpoints (180 staff workstations, 15 APs, eight cameras), four equipment
rooms, 12 access switches, five racks, and 21 inter-room fibers. Build and offline
validation/export took 0.438s; SDK verification took 3.449s locally
(`build/v05-build-timings.json`). Adding a second headquarters offline produced
16,072 objects, with no removals and all original HQ/branch objects unchanged.
Exactly 12 existing DC interfaces gained assignments for added WAN capacity;
`build/v05-growth-plan-review.json` and `build/bank-campus-growth/` preserve that
reviewed candidate. This HQ-addition candidate has not been ingested.

The v0.5 initial Diode replay completed 14 phases and 12,837 entities in 436.204s
across phases, with zero failures on the existing patched local target. Strict
readback matched every object, 47,296 attributes, 27,177 references and 1,245
front/rear mapping tuples with zero mismatches or unexplained records. Receipts
are `build/v05-initial-replay.json` and `build/v05-initial-readback.json`.
The fixed pre-load inventory was `build/v04-before-v05-readback.json`.
An identical replay completed in 154.294s; every phase retained the initial
replay's final ingestion metrics (60,299 reconciled, zero failed/queued), with no
additional ingestion-log rows (`build/v05-repeat-replay.json`).
Repeat strict readback retained every ID and all 1,245 mapping tuples, with
complete emitted-field coverage and no unexplained records. The captured target
inventory remained exactly 57,308 IDs (`build/v05-repeat-readback.json`).

Separate readbacks preserved all 8,432 v0.2 IDs, 2,655 v0.3 pilot IDs, 12,528 v0.3
depth IDs and 21,541 v0.4 grown-estate IDs, plus their emitted attributes and
references. All 1,224 v0.3 and 2,346 v0.4 mapping tuples were retained. Receipts
are `build/v02-after-v05-readback.json`, `build/pilot-after-v05-readback.json`,
`build/v03-depth-after-v05-readback.json`, and `build/v04-after-v05-readback.json`;
`build/v05-final-comparison.json` records the cross-receipt assertions. No target
reset or direct estate writes were used.

Native browser traces completed for a 26m fourth-floor workstation channel and
a 22m IDF04-to-MDF fiber. All four room-local `N01` racks resolved distinctly;
the IDF04 rack showed 16.7% space and 8.2% synthetic power utilization.
Navigation and interpretation are in `build/v05-ui-walkthrough.md`.

The v0.4 suite passed 134 generator/qualification checks and five setup checks
with the pinned SDK on September 9, 2026 (`build/v04-full-check.log`). The suite
includes a 250-branch panel estate exceeding 100,000 objects. New mutations reject
missing compact peer trunks, incorrectly wired edge bridges, detached gateways,
broken management paths and shared VLANs omitted from both cable ends.
The 11,967-object `bank-demo` baseline generated/validated/exported in 0.531s;
SDK verification took 3.245s locally (`build/v04-build-timings.json`).

The v0.4 initial load completed 14 Diode phases in 410.838s with zero failures on
the existing patched local target. Strict readback matched all 11,967 objects,
46,470 attributes, 26,780 references and 1,224 front/rear mapping tuples, allowing
only captured pre-v0.4 inventory (`build/v04-initial-replay.json` and
`build/v04-initial-readback.json`). The browser confirmed the gateway's bridge
parent relationship, a complete 39m ATM trace with readable labels, and 29.2%
space / 7.5% synthetic power utilization in the inherited small-branch rack.
Local URLs and scope are in `build/v04-ui-walkthrough.md`.

Identical replay completed in 140.503s without additional ingestion-log rows.
Repeat strict readback retained every object ID and all mapping tuples with zero
mismatches or unexplained records (`build/v04-repeat-replay.json` and
`build/v04-repeat-readback.json`). The updated acquisition/refresh candidate
walkthrough is `build/v04-acquisition-refresh/report.md`; it preserves all 18
endpoint names/addresses and explicitly distinguishes direct upstreams from the
inherited branch's peer-trunk path. Those transitions have not been applied.

Live additive growth then expanded this same estate from 10 to 27 sites and
11,967 to 21,541 objects. Increasing small branches from four to 21 raised modeled
peak demand from 480 to 820 Mb/s, so each DC gained a second WAN pair. The offline
comparison found 9,574 additions, no deletions, and exactly 12 existing interfaces
gaining VLAN assignments/descriptions; all previous cables, IPs, names and device
positions remained intact (`build/v04-growth-plan-review.json`). Diode reconciled
the grown artifact in 432.202s. Strict readback matched 82,509 attributes and
47,778 references, retained all 11,967 original IDs and 1,224 original mapping
tuples, and found zero mismatches or unexplained records. Receipts are
`build/v04-growth-replay.json` and `build/v04-growth-readback.json`. This qualifies
this additive capacity expansion on the pinned local target, not arbitrary
resizing, deletion, new observation timestamps or lifecycle transitions.

Final independent readbacks also retained every ID and all emitted fields and
references in the three older estates: 8,432 v0.2 objects, 2,655 v0.3 pilot objects,
and 12,528 v0.3 depth objects, including the depth estate's 1,224 mapping tuples.
All returned zero mismatches. Receipts are
`build/v02-after-v04-growth-readback.json`,
`build/pilot-after-v04-growth-readback.json`, and
`build/v03-depth-after-v04-growth-readback.json`. The target was not reset.

The v0.3 suite passed 122 generator/qualification checks and five setup checks
with the pinned SDK on September 9, 2026 (`build/depth-full-check.log`). Two
additional native Django tests passed in an isolated test database on the
patched target: mapping creation, fresh diff/replay without mapping-ID changes,
complete cable paths, and refusal of destructive mapping changes. Evidence is
`build/local-front-port-native-1.json` and its log.

The v0.3 direct-mode pilot loaded 2,655 objects through Diode into the unchanged
official NetBox 4.7/plugin1.17 stack. Strict readback checked 9,807 attributes and
4,808 references with zero mismatches, allowing only the previously captured
Cedar inventory (`build/v03-pilot-initial-replay.json` and
`build/v03-pilot-initial-readback.json`). The browser confirmed room hierarchy,
timezone display, and 12.9% rack power utilization from generated allocations.

The full `bank-depth` panel estate then loaded through Diode on NetBox 4.7.0
with the opt-in local plugin patch: 14 phases, 13,153 entities, 379.897 seconds
across phases, zero failures. Strict readback matched all 12,528 canonical
objects, 49,177 emitted attributes and 27,553 references, including 1,224 native
front/rear mapping tuples. Only previously captured estate IDs were allowed as
extra inventory. Receipts are `build/depth-initial-replay.json` and
`build/depth-initial-readback.json`.

Identical replay completed in 122.409 seconds with no additional ingestion-log
rows. Repeat strict readback retained every canonical object ID and all 1,224
mapping tuples, with the same complete field/reference coverage and zero
mismatches or unexplained records (`build/depth-repeat-replay.json` and
`build/depth-repeat-readback.json`). This is identical-artifact replay; it does
not test new observation timestamps, target edits, or scenario migrations.

Separate final readbacks also preserved all 8,432 earlier v0.2 IDs and all 2,655
pilot IDs, with full emitted-field/reference coverage and zero mismatches.
Evidence is `build/v02-after-depth-readback.json` and
`build/pilot-after-depth-readback.json`; neither earlier estate was reset.

The browser walkthrough confirmed an ATM-to-access-switch trace through its
room outlet and cabinet panel: three cables, 39 meters, **Trace Completed**.
The panel exposes all 24 front/rear pairs, including unused positions. A ledger
database service links to its listening IP, and that IP links back to the service.
Observed local URLs and remaining presentation gaps are recorded in
`build/depth-ui-walkthrough.md`. Long names still clip in rack and cable diagrams;
the generated relationships are more complete than the visual polish.

A v0.3 panel scale run generated 178,772 objects across 253 sites in 194 requests.
Generation, validation and export took 7.313 seconds; SDK verification took
41.653 seconds on local Python 3.14.7. Receipts are under `build/depth-scale/`.
This larger estate has not been ingested; these are offline timings only.

The earlier v0.2 suite's 96 checks passed locally on September 9, 2026 with Python 3.14.7 and the pinned
SDK installed: 93 generator/qualification tests and three local setup tests.
The local result is retained in `build/local-check.log`.

The September 8, 2026 v0.2 mixed scale run produced 128,932 canonical objects
across 253 sites in 141 requests. Generation, graph validation, and export took
3.9 seconds locally; the pinned SDK check took 29.0 seconds. These are single
Python 3.14.7 observations; `build/scale-v2/timings.json` and `sdk-checks.json`
retain the local receipts. Live ingestion was not run.

On September 9, 2026, the default mixed bank passed live ingestion into local
NetBox 4.7.0 with Diode 2.2.0 and plugin 1.17.0. Nine phases submitted 9,057
entities (8,432 canonical objects plus 625 deferred primary-address updates),
with all ingestion logs reconciled and no failures. Phase execution took
239.817 seconds on the 4-CPU / 8-GiB Colima VM, excluding setup and startup.
Strict REST readback matched all 8,432 objects, checked all 32,203 emitted
attributes and 17,783 references, and found zero mismatches or extra records.
Receipts are `build/local-bootstrap-fixed-replay.json`,
`build/local-initial-readback.json`, and `build/local-target-status.json`.
An identical replay completed in 81.634 seconds and retained every NetBox ID,
with the same field/reference coverage and zero extra objects. Its receipts are
`build/local-identical-replay.json` and `build/local-repeat-readback.json`.
The same observations were replayed and Diode's ingestion-log count stayed
unchanged; this checks identical-artifact replay, not changed timestamps, new
observations, or target-side matching after edits.
This is a single local observation, not a 100,000-object ingestion benchmark.

The validator checks reference closure, hardware inventory, rack occupancy,
port occupancy and media/speed compatibility, passive cable paths, redundant
attachments, addressing, VLAN continuity to access uplinks and gateway SVIs,
required service instances and ports, WAN demand, compute budgets, and A/B power.
These are finite modeled assertions, not a network simulator or a proof that
every realistic-world constraint is covered.

The full graph and indexes are currently held in memory. Export requests are
bounded, but generation is not streamed; memory grows with the estate. Recipes
fail explicitly when they exhaust a site address reservation, /24 or /20 host capacity,
256 inherited-site /20 reservations (including retired reservations),
eight management-switch uplinks per site (20 managed attachments per switch),
16 leaf-pair pods per DC, a service's
16-host reservation, or the configured object budget. Raising `max_objects`
does not bypass physical capacities. Each implemented profile has reviewed
construction limits; input bounds alone do not guarantee a fitting composition.

Routing protocols, firewall policy, carrier interiors, last-mile duct diversity,
RF coverage, measured PoE/electrical consumption, application replication, and recovery behavior
are not simulated. Management switches and endpoints are single-homed; modeled
network and power redundancy applies only to the contracted infrastructure.
One local NetBox/Diode version combination has been checked. Additional versions,
Cloud/Enterprise targets and transitions other than the documented local provider
status sequence remain unqualified. Live growth
has been checked only for the documented additive branch/DC-capacity expansion.
Use a disposable tenant to qualify the intended workflow before relying on a demo.
The unmodified official Diode plugin still needs mapping support for full passive
paths on current NetBox. The opt-in local patch described above is deliberately
limited to this pinned qualification target and one-position mappings.

## Current offline scale evidence

The corrected v0.9 source was measured on the same 64-PoP, 128-customer recipe
as the preserved v0.8 comparison run. Generation, independent checks, report,
intent, coverage and export share the same process scope:

| Run | Canonical objects | Seconds | Peak RSS bytes | Wire bytes | Requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| v0.8 comparable predecessor | 220,308 | 9.86 | 1,810,972,672 | 96,316,765 | 244 |
| v0.9 same IPv4 recipe | 226,119 | 11.85 | 1,474,969,600 | 99,669,798 | 250 |
| v0.9 with optional IPv6 | 239,058 | 12.78 | 1,497,645,056 | 107,197,173 | 264 |

These are single observed local runs, not a controlled timing distribution or a
live-ingestion benchmark. Runtime excludes imports/startup; process peak memory
includes imports and the complete build. The full graph and indexes remain in
memory. The dual-stack export separately passed SDK1.14: 255,479 wire observations
in 264 requests, with a maximum actual protobuf size of 463,166 bytes.

Creating, independently verifying and reporting a span-maintenance scenario on
that 239,058-object graph took 57.33 seconds and 2,641,379,328 bytes peak RSS. It
checked 331 directed rows; the selected `circuit/backbone/pop-19/b` retained the
checked further-failure margin. This larger scenario was not exported as a second
wire snapshot or loaded live. Exact recipes, commands, source/artifact hashes and
the comparison are under `build/goal-connected-depth/final/scale/`, in the
`*-02*` receipts. Actual representative live qualification is separate in
[the lab guide](lab/README.md).

### Historical v0.8 provider measurement

The final v0.8 provider run contains 220,308 canonical objects and 236,729 wire
observations in 244 bounded requests. Full CLI generation, independent validation,
report/intent/coverage generation and export took 13.10 seconds with
1,520,992,256 bytes peak process RSS on the recorded Darwin/Python environment.
The separate SDK pass took 54.07 seconds with 77,758,464 bytes peak RSS.
See `build/goal-richness/final-review/corrections/scale-summary.json` for exact
commands/logs, source and artifact bindings; the independent architecture review
bound every request and actual emitted kind count.

These are single local observations, with CLI startup included and light
reviewer/Docker activity present. They establish offline generation/export scale,
not a 220k live load or a memory bound for every recipe. The full graph remains
in memory. Historical v0.8 final live sizes were 4,383 hospital and 4,344 provider
objects; their exact scopes are in [the lab guide](lab/README.md).

### Historical bank measurements (v0.7)

The measured bank runs contain 9,483, 37,213 and 128,663 canonical objects. The
largest generated, independently validated and wrote the full artifact in 7.182
seconds with 1,000.125 MiB process peak RSS on the recorded Mac/Python environment.
Its 149 Diode requests contain 141,663 wire observations; SDK 1.14 verification
passes separately. All three plans and every wire file remain byte-identical to
the previously independently reviewed scale artifacts. See
`build/goal-scale-final/README.md` and `summary.json` for archived-source commands,
exact inputs, timings, artifact comparisons and source hashes. The earlier
measurements and independent reviews remain under `build/goal-scale/`.

Those bank measurements are one observation per size. Timing excludes imports/startup; memory
includes interpreter/modules and all generation/validation/build phases. SDK and
live target resources are excluded. This is multi-site bank offline evidence,
not a 100k-object live ingestion benchmark or proof of large individual DC realism.

## Extending it

`bank.py`, `enterprise.py`, `school.py`, `hospital.py` and `provider.py` express
industry demand. `campus.py`,
`datacenter.py`, `places.py` and `blocks.py` allocate reusable physical structures;
`model.py` owns identity and reservations; the validators independently check the
result; `diode.py` exports it. The small graph format is documented in
[CONTRACT.md](CONTRACT.md). Another industry should add reviewed demand and design
rules using those shared blocks, plus independent semantic tests. There is no
plugin framework or unrestricted template language to maintain.

`scenarios.py` creates explicit acquisition/refresh candidates and checks changes
against the original graph. `power_scenario.py` selects a dual-supply service host, plants one power-diversity
defect and checks exact findings and restoration. Additional industries, general
scenario composition and transitions beyond the pinned provider status sequence
remain future work. `equipment.py`, `networking.py` and `operations.py` attach reviewed families
to actual sites, devices, interfaces and workloads. Additional breadth needs
its own semantic checks and live qualification, not just more entity types. Each
extension must remain explainable from demand, geography or history and export
useful meaning to NetBox. Additional industries should reuse those proven rules;
failure scenarios are one consumer of the estate, not its organizing principle.
Additional target-side transitions and NetBox/Diode versions need their own live gates.
