# How the estate is built

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

The reusable construction rules, industry assumptions and connected detail are documented here. Profile guides own their exact input bounds.

Run commands from the repository root. Paths in code blocks are relative to that root.
Generated `build/` artifacts and qualification receipts are local outputs, not included in a clone.

- [Procedural design, without AI](#procedural-design-without-ai)
- [What makes it a bank](#what-makes-it-a-bank)
- [Physical-detail iteration](#physical-detail-iteration)
- [Building-driven headquarters](#building-driven-headquarters)
- [Demand and WAN purchasing](#demand-and-wan-purchasing)
- [Connected model families](#connected-model-families)
- [Optional IPv6](#optional-ipv6)
- [Wireless demand and PoE](#wireless-demand-and-poe)
- [Installed optics policy](#installed-optics-policy)
- [Contact and journal context](#contact-and-journal-context)
- [Extending it](#extending-it)

## Procedural design, without AI

We author reusable rules and hardware inventories; the program expands and
connects them. This borrows from Minecraft's [jigsaw templates and weighted
pools](https://learn.microsoft.com/en-us/minecraft/creator/documents/structures/introductiontojigsawstructures?view=minecraft-bedrock-stable)
and [hierarchical building grammars](https://doi.org/10.1145/1179352.1141931).
The bank rules currently include two branch access architectures plus a refresh
transition. The generator is not a general city generator or a library of
arbitrary network topologies.

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

The [hardware catalog](../catalog/README.md) distinguishes pinned community vendor
definitions from fictional reference servers, PDUs, panels, and endpoints.

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
See [lab/README.md](../lab/README.md) and [the patch boundary](../lab/patches/README.md).

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

## Optional IPv6

Optional IPv6 is implemented and qualified on the pinned local stack for all five
profiles; see [the receipts and limits](../lab/README.md#current-v09-qualification).
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

## Wireless demand and PoE

Version 0.9 implements this shared capability. The bank, school and hospital
representatives passed [pinned local qualification](../lab/README.md#current-v09-qualification),
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
[school](../profiles/school-district.md) and [hospital](../profiles/hospital-clinics.md)
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
[The catalog](../catalog/README.md) distinguishes vendor limits from reference
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
indexed in [GOAL.md](../GOAL.md); these do not prove optical loss budgets or a
large live load.

The finite [catalog](../catalog/README.md) selects LR/LX/LR4 parts for reviewed
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
under their respective `live-final/` directories, indexed in [GOAL.md](../GOAL.md).
Images are deferred; no image renderer, uploader or additional credential is needed.
The [coverage review](../COVERAGE.md) explains wireless relevance and ranks the next
useful additions across physical, network, service and operational depth.

## Extending it

`bank.py`, `enterprise.py`, `school.py`, `hospital.py` and `provider.py` express
industry demand. `campus.py`,
`datacenter.py`, `places.py` and `blocks.py` allocate reusable physical structures;
`model.py` owns identity and reservations; the validators independently check the
result; `diode.py` exports it. The small graph format is documented in
[CONTRACT.md](../CONTRACT.md). Another industry should add reviewed demand and design
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
