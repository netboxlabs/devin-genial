# Implementation contract (v1)

This file defines the small internal format shared by builders, validation, and
Diode export. It describes generated intent, not a new ingestion protocol.

Generator v0.9 is a new baseline. Version participates in deterministic choices
and previous-plan compatibility; v0.8 and earlier artifacts must not be reused as
growth inputs. Site display naming remains unchanged. In v0.8, site/address/facility
and procurement text uses normal operational descriptions; generated provenance
remains in package assumptions, geography metadata and the estate tag instead
of being repeated in every NetBox field.
The final v0.8 reference-model label revision changes the catalog digest; earlier
v0.8 milestone packages require a new baseline too. Recipe `name` is immutable
during growth because provider matching identities depend on it; all profiles
reject a rename with `--previous`.

The plan is a JSON object with `schema_version: 1`, `generator_version`,
`recipe` (resolved input), `hardware_digest`, `allocations` (stable site IDs to
integer slots), `reservations` (scope -> immutable key -> integer slot),
`design_assignments` (branch ID -> selected design), `objects` (list), and
`contracts` (profile assertions). Ledgers retain removed keys;
duplicate or negative slots are invalid. Capacity is checked before allocation.

Each object is `{key, kind, attrs, refs, meta}`. `key` is an immutable plan-local
identity. `kind` is a Diode snake_case entity type. `attrs` contains scalar/list
values using SDK field names. `refs` maps SDK field names to object keys (or lists
of keys). `meta` contains generator-only reasoning and assertions, never sent as
ordinary NetBox fields. Nested references must resolve to exactly one canonical
object. Builder output contains no database IDs.

Device/VM display names are site-local; their full Diode identity includes scoped
site/cluster and tenant references. DNS retains the complete namespace as a valid
DNS label (2–20 characters, ending alphanumeric). Canonical keys do not change
when display conventions change. Generic cable numbers use the existing
reservation mechanism; access-channel labels derive from immutable endpoint keys.
Generated devices omit asset tags; serials are descriptive, not matching keys.
Rack asset tags are globally unique. Tags exceeding the pinned native 50-character
limit retain 33 characters and a 16-hex SHA256 suffix of the full original label;
existing shorter tags are unchanged. The digest includes full namespace, site,
room, equipment group and ordinal, and validation rejects duplicate tags.

Units follow the target field contract: interface/circuit speeds and commit rates
are kb/s; VM `memory` and `disk` and host `memory_mb`/`disk_mb` budgets are MB
(memory sizing uses binary multiples). Recipe peak demand is Mb/s. Never compare
budgets across units without an explicit conversion.

Special cases: `cable.refs` uses `a` and `b`, each a single termination key;
the exporter maps these to typed `a_terminations` / `b_terminations`. IP addresses
use `refs.assigned_object` (interface, vm_interface or fhrp_group). Circuit terminations use
`refs.termination` (site or provider_network). Power feeds use normal panel/rack
refs. Other fields use SDK names, e.g. scope_site, rear_port, primary_ip4.
`patching = "direct"` emits continuous access channels without passive objects;
`"panels"` emits legacy front/rear mappings, with three cables per endpoint:
switch → cabinet panel → wall outlet → endpoint. The exporter detects those mappings
and records their incompatible NetBox version range rather than silently dropping
them. The graph validator establishes local correctness, not target support.

Other generic relationships use their base name (`object`, `member`, `component`,
`interface`, `assigned_object`, `termination`); the exporter selects the finite
typed SDK variant from the referenced object's kind. Back-references for primary
addresses, primary MACs, virtual-chassis masters and installed modules are emitted
after their members exist. Other dependency cycles are rejected.

Custom-field values use the SDK's explicit typed values, such as
`{"selection": "tier-1"}`. A consuming object's `meta.requires` lists the canonical
custom-field definitions that must reconcile first. Definitions are ordinary
generated objects; this ordering metadata is never sent as a NetBox field.

`meta.external = true` marks a matching dependency, currently only an existing
`user` with `attrs.username`. `reservation_user` binds that username explicitly;
an empty value omits rack reservations. No user is provisioned. Local replay checks
the account before submission, and readback checks its identity like any other ref.
The binding is immutable during ordinary growth.

Every build includes `coverage.json`, derived from the pinned type audit in
`catalog/type-coverage.json` and actual objects. Counts establish emission only;
SDK and live qualification have separate evidence. Computed cable paths and
termination rows, unsupported native templates, and runtime/admin types are
classified explicitly rather than counted as successfully ingested objects.
`intent.json` records resolved recipe values and their supplied/default provenance,
current whole-package `compatibility` and separately labeled `historical_assumptions`,
per-workload VM totals, assumptions and offline/live check boundaries. A build
from a frozen plan uses unknown input provenance; resolved values alone cannot
reconstruct which fields the original operator supplied.

Optional IPv6 is a shared v0.9 feature qualified across five representative
artifacts on the pinned local stack (see `lab/README.md`). All five profiles accept `ipv6_pool` as an aligned /32–/40 subnet of
`2001:db8::/32` or `3fff::/20`. Absence retains IPv4-only output. Input text is
normalized; enabling, disabling or changing the pool requires a new baseline.
IPv6 allocation uses separate append-only reservations, independent of each
profile's IPv4 slot units. Sites receive /48s; ordinary LAN segments receive
/64s, routed router links /127s and provider PE loopbacks /128s. Reserve the
final /48 for routed infrastructure. The bank diagnostic radio segment uses a
/64. Existing bank FHRP groups and VIPs remain IPv4-only; this policy does not
model IPv6 FHRP or default-router execution.

`ipv6-sites` reserves canonical site keys; `ipv6-segments/<site-key>` reserves
existing IPv4 leaf-prefix keys. `ipv6-routed-links` and `ipv6-loopbacks` also
reserve leaf-prefix keys, using infrastructure /64 numbers zero and one. LAN
host IDs retain the IPv4 offset as an integer (`.10` becomes `::a`); diagnostic
radios add one to avoid IID zero. Routed slots start at address `2*(slot+1)`,
loopbacks at `slot+1`; each infrastructure ledger has a one-million-slot ceiling.
No reserved all-zero or high anycast host identifiers are assigned.

IPv6 objects retain the actual interface, VRF, VLAN, tenant and site ownership;
they do not create another physical estate. Eligible existing device/VM primary
interfaces gain `primary_ip6` alongside `primary_ip4`. Existing service objects
reference both families through `ipaddresses`; their inventory does not prove
listener execution. Unknown transit far ends remain unmodeled. Shared opaque
carrier WANs retain site /64 companions rather than invented remote router
owners. Allocation is arithmetic and proportional to emitted objects, never
iteration over IPv6 address space. Reports derive family counts and bounded
primary/service examples from actual graph references; IPv4-only reports retain
their previous behavior. No routing, RA, DHCPv6, SLAAC or application execution
is implied by address inventory or export.

Provider routed and loopback eligibility also requires its existing independently
checked IPv4 link/loopback reservation and actual interface ownership. A name
like `prefix/link/...` cannot turn a VM into a routed /127 endpoint. Validation
checks address tenants against the actual segment and, for VM interfaces, the
owning VM; copying the same wrong tenant into both families cannot bypass it.

Hardware is a versioned JSON catalog at `catalog/hardware.json`: top-level
`version`, `sources`, `models` and the shared `optics` policy. Each model indexed by alias has `manufacturer`,
`model`, `slug`, `u_height`, `is_full_depth`, `interfaces` (list of `{name,type,
mgmt_only?}`), `power_ports` (list of `{name,type}`), and optional `description`.
Branch access models additionally declare ordered `access_ports` and `uplink_ports`
lists naming unique entries in `interfaces`. Capacity uses these lists rather than
a hardcoded port count. All normalized data must retain sources and license provenance. Generic example
equipment is explicitly fictional. Optional `console_ports` and
`console_server_ports` list actual name/type pairs, just like power ports.
Installed components are explicit objects; native component templates have no
top-level message in the pinned SDK.

Passive reference models declare `passive_ports`; both sides of every position
are emitted, including unused ports. A front position maps to one rear position
on the same device. The local plugin bridge is an explicit qualification-only
translation to NetBox4.7 native mappings, not a change to the canonical format.

`places.foundation/locate/arrange/place_endpoint` separates authored geography
and room demand from hardware allocation. Locations carry `space_type`, floor
and local `position_m` metadata; endpoints carry their room, mounting position
and `access_channel_length_m`. Generated site contracts declare the equipment
room, floor height and maximum access-channel length. The validator inspects
actual containment, occupants, cable lengths and the panel/outlet sequence.

`placement.equipment_location` is the MDF/default equipment room.
`placement.equipment_locations` maps floor-number strings to equipment rooms.
For headquarters, the map covers occupied endpoint floors; each floor's actual
serving switches, patch panels, management connections and rack power must be
local to its room. Secondary room keys are `location/<site>/idf-02`, etc.
Device/rack reservation scopes and rack asset tags include that room; rack
display names can repeat across different locations. Direct-terminated fiber
uplinks join access/management switches to the MDF distribution devices.
Their lengths derive from room-coordinate Manhattan distance plus ten metres.
Independent checks inspect the real cable endpoints and geometry rather than
accepting a declared room mapping as proof of local connectivity.

`headquarters_staff` is an integer from 24 to 192, default 180. Each twelve staff
positions require an office pod; four pods occupy a floor. HQ demand adds one AP
per pod, two cameras per floor, and two Mb/s planned peak per staff position.
These are synthetic planning inputs. Headquarters has reception and no ATM
cohort. Changing its staff demand during ordinary growth is rejected; resizing
occupied floors requires a future explicit remodeling transition.

`wan_tiers_mbps` is a nonempty, strictly increasing list of positive integer
Mb/s values ending in 1000, default `[50, 100, 200, 500, 1000]`. Branch/HQ CIR is
the smallest tier meeting whole-site peak demand after reserve and the maximum
of carrier minimum (A: 50, B: 100) and retained Birch minimum (200).
DC circuits remain 1000 Mb/s, with pair counts sized from aggregate peak demand.
CIR and physical handoff are separate: both termination `port_speed` values
remain 1,000,000 kb/s. Reserve comparisons use exact decimal arithmetic.

Circuit `install_date` is a canonical ISO date, converted by the existing exporter
to a protobuf timestamp. `comments` contains the procurement cohort and selection reason;
`meta.procurement` explains cohort, minimum commitment, per-circuit planning
peak, handoff and tier selection. Independent checks derive policy from recipe,
actual provider/termination/cable relationships and persisted design assignments,
not from these explanatory fields. Dates use stable keyed choices relative to
`as_of`; retained Birch lineage remains independent of current tenant/access
hardware. Changing tiers requires a new baseline. This does not emit lifecycle
change events or execute contract renewal.

Inlet `allocated_draw` and `maximum_draw` are integer watts. `planned_watts`
contracts distinguish synthetic planning allowances from hardware facts. Normal
inlet allocations sum to the device allowance; each supply reserves the whole
allowance for failover. Feed budgets use single-phase voltage × amperage × maximum
utilization. PDU inlet draw fields remain absent so NetBox aggregates its outlets.
Services use `refs.ipaddresses` and must bind an address owned by their parent VM.

`branch_architecture` is `compact-routed-edge` for small branches and
`distribution` for other branch/HQ footprints, independently of procurement
design. Compact branches have two WAN gateways, two access switches and no
distribution pair. The third catalog uplink on each access switch forms a peer
trunk. Modern/refreshed access has two direct edge attachments; inherited access
alternates one direct attachment and retains an indirect path through its peer.
Each compact edge has a logical `bridge` interface, physical `refs.bridge`
members and virtual VLAN interfaces using `refs.parent`; neither logical
interface can be cabled. VLAN membership and gateway addressing are independently
checked. Gateway primary management IPs belong to their VLAN interfaces; the
management builder skips devices that already have a primary IP. No forwarding,
firewall configuration, STP or HA execution is implied.

Validators expose `validate(plan) -> list[dict]`, each finding containing `code`,
`object`, `message`; empty means all implemented rules passed. The profile writes
per-site contracts and equipment metadata so independent checks can recompute
budgets and inspect physical edges rather than trusting builder success flags.

`datacenter.build(site, workloads=..., wan_peak_mbps=..., assumptions=...)` is the
shared construction boundary. Its caller resolves business demand. Each workload
requires `key`, `slot`, `instances`, `network`, `vcpus`, `memory_mb`, `disk_mb`,
`listeners`, `criticality`, and `replica_description`. Optional `replicas` and
`failure_domain` default to `1` and `none`, preserving bank placement. Host/rack
policies require 2–4 replicas and instances divisible by the replica count.
Workload keys and nonnegative
integer slots are unique. Workload combinations whose host/VM names collide under
the existing naming rules are rejected before allocation. Slots preserve physical port assignments independently
of input ordering; bank policy retains its original slots. Listener records have
`key` (stable suffix, optionally empty), `name`, `protocol` (`tcp` or `udp`), and
nonempty unique integer `ports`. Listener names must be unique within a VM even
when their keys or protocols differ. Resource units are MB at this boundary; the bank
adapter converts its authored GB disk assumptions before calling it.

The builder checks malformed or individually oversized workloads before allocating
DC objects, then allocates the fabric, WAN, hosts, VMs/listeners, management and
power using existing helpers. WAN demand is nonnegative integer Mbps and currently
uses paired 1 Gbps handoffs. Workloads use applications/database/backup networks.
Current limits remain explicit: sixteen hosts per workload pool, sixteen fabric
pods, 160 managed-device attachments per site and finite address reservations.
The opt-in replica allocator packs complete groups into stable host lanes using
exact decimal resource budgets. Rack lanes use independent reservation scopes;
paired fabric/WAN devices also occupy distinct racks when a rack policy is present.
The bank's default still permits replicas on one host. The optional
`include_equipment=False` suppresses demonstration equipment while retaining
management, serial access and power. Neither mode executes application replication.

`generate.generate` dispatches resolved recipes to `regional-bank`,
`enterprise-data-center`, `school-district`, `hospital-clinics` or
`provider-backbone`. Enterprise policy accepts 1–8 sites, 1–16 keyed workloads,
groups/replicas/resources/listeners, per-site WAN demand and baseline demo intent.
It reserves persistent `workload-slots` before calling the shared builder and
creates a non-bank foundation. Actual resource/port/rack/address capacity may
reject demands inside those input ranges. Site allocation uses `/16` reservations;
management/applications/database/backup/WAN/storage occupy `/20` role ranges at
stable offsets 0/6/7/8/9/10. Rack metadata defines a synthetic eight-column grid,
with 64 positions in each network/compute zone; lengths use actual rack positions.

Enterprise ordinary growth permits added workloads/sites and increased groups or
WAN demand. Existing policies/resources/listeners and network rack-diversity mode
are immutable; reductions require a new baseline because export does not delete.
Reproduction from the previous recipe and ledgers must succeed before growth.
`validate_datacenter.py` independently derives required inventory, capacity,
replica placement, gateways, VLAN paths, management, console, rack geometry and
power paths from recipe and observed objects. It checks recipe bounds before
expanding expected inventory; emitted contracts and explanatory VM metadata cannot
silently redefine those obligations. Catalog facts and synthetic planning budgets
remain separate. This establishes modeled connectivity, not executed service HA.

`campus.aggregation(site, network_roles, wan_peak_mbps, compact=False)` returns
distribution devices, WAN edges and upstreams. `campus.access(site, endpoints,
upstreams, network_roles, design, compact=False, stable=False)` consumes placed
endpoint keys/segments and returns actual access-device/capacity facts. Bank mode
retains legacy ordering and allocation; school `stable=True` reserves endpoint
slots per serving room. Slots fill two switches in alternating lanes up to each
switch's reserved usable-port budget, then add another pair. Existing port and
uplink reservations survive growth; maximum 38 distribution attachments applies
to the whole campus. Profile policy must reject retirements or room remapping.

School room policy uses eight classrooms per floor, up to four floors, with
separate ground-floor administration pods and an optional shared lab. Endpoint
keys encode classroom/seat or stable staff/lab ordinals, and metadata records
cohort, room, position and serving closet. A school district shares one metro
selected from its seed; campus street numbers use retained site allocations.
Facility kind, not a generic non-bank test, selects DC rack grids. School
network-role offsets are management=0, staff=1, students=2, wireless=3, security=4,
with shared DC offsets applications=6, database=7, backup=8, WAN=9, storage=10,
and optional local guest=12.
All school sites reserve `/16`, with `/20` role segments; bank reservations stay
unchanged. Campus geometry uses per-floor closets rather than data-hall rack lanes.

School policy derives district workloads from declared enrollment and staffing;
the independent school validator resolves the same policy without importing its
builder. Both industry validators call shared DC graph/power checks. The school
validator reuses common cable-component indexes for actual floor-local paths,
uplinks, reserved capacity and power; missing contracts or descriptive metadata
must not remove obligations. Input bounds precede expected-inventory expansion.

Hospital policy expresses installed care-unit demand. Each keyed ward retains a
`healthcare-wards/<site>` reservation (slots 0–6, floors 2–8); ground is floor 1.
Two-bed rooms fill stable positions, and new ward keys append floors irrespective
of sort order. Clinics fill eight exam rooms per floor. Each care floor has local
access/management/power. The shared eight-port management fanout bounds hospital
ward count; no additional ports are invented. Actual medical/imaging/workstation
roles and room functions are checked independently in `validate_hospital.py`.
The complete bounds, resource ratios and workload listener policy live in
[profiles/hospital-clinics.md](profiles/hospital-clinics.md).

Hospital site blocks are `/16`; `/20` offsets are management=0, clinical=1,
medical=2, wireless=3, security=4, applications=5, database=6, backup=7, WAN=8,
storage=9, staff=10, imaging=11 and optional local guest=12. Its validator passes independently declared
DC offsets to the shared checker; previous profiles retain their original maps.
Growth must reproduce the frozen recipe/ledgers before adding inventory; beds,
desks, rooms, wards and sites may increase while previous paths and dated records
remain fixed. Decreases, renames and WAN renewal require a new baseline.
Device-type references, rather than optional metadata, select the shared power
checker's supply and allocation obligations.
Generated VLAN-bound addresses also use their actual containing segment's mask:
an address merely falling inside the prefix cannot qualify a /32 or over-broad
gateway. Unbound routed/loopback and FHRP records keep their separate semantics.

`networking.wireless(world, sites, lan_roles=..., diagnostic=..., stable_channels=..., guest_sites=())`
consumes canonical site objects. Bank defaults preserve the original one-staff-LAN
and diagnostic example. School passes staff/students on wlan0/wlan1 with diagnostic
disabled and stable channels enabled. Client VLANs are tagged over AP Ethernet;
the distinct wireless-management VLAN stays native. Several WLANs can share one
actual radio, whose complete `wireless_lans` list carries membership; the radio
has no VLAN/mode fields. Each WLAN owns its VLAN. Optional guest sites are
canonical site keys, use open authentication on wlan0, and preserve the base
group identity. Radio choices derive from the AP key and base radio index in
stable mode. Independent checks inspect radios, site-scoped WLAN groups, actual
VLAN carriage and active addressed gateway paths.

School/hospital `wireless` maps freeze `managed`/`guest` counts per existing local
zone. The authored 32-device/AP threshold retains coverage anchors and permits
at most four stable mounts. Counts grow monotonically without redistributing
earlier zones. Guest demand conditionally adds offset12 (VLAN130) in the site
/16; no existing offset changes. Counts are device concurrency planning demand,
not enrollment, patient throughput or association evidence.
Managed demand sizes AP equipment in aggregate across managed WLANs; it has no
per-SSID allocation or address-admission guarantee. Guest demand maps to one
explicit guest segment per facility and must fit its available IPv4 capacity.
Installed addresses and held ranges consume capacity in every WLAN prefix.

`campus.access(..., stable=True)` retains the existing integer physical-port
encoding in `access-endpoints/<site>/<room>`. It preloads every incumbent's port
and PD load before first-fit allocation into unreserved positions. A position
must fit both copper headroom and the catalog supply-loss PoE budget. Skipped
copper holes remain available to later non-PD endpoints. Whole switch pairs
through the maximum occupied slot are materialized, subject to existing room,
rack, distribution and management ceilings; `_next` remains above used slots.

`optics.enrich(world)` runs after physical construction/equipment and before PoE
and operations context. Each occupied reviewed optical interface retains its key,
name, device and cable termination, gaining only a `module` reference. Its
`optics-module/<interface-key>` references `optics-bay/<interface-key>` and the
manufacturer/model module type. Bay name is `Optic <actual interface name>`;
position is the same actual interface name (reviewed names fit the native
30-character limit). These are fixed chassis cages, without a parent module.
Shared part definitions follow the profile's supported hardware-type library,
so refreshing the last chassis using a part retains its catalog definition.
Types have no replicated interface templates. Installed ownership and compatible
bay types are native references; descriptive type attributes are not validation
authority. Selection reads actual cage name, configured speed and local media.

An AOC is one `aoc` cable with two captive modules. Their shared `AOC-` plus
24-hex serial derives from namespace/cable key. The original cable key/label
remain stable; native comments expose the assembly serial. There is no invented
module-to-cable FK, duplicated module asset tag, detachable AOC end or remote
carrier optic. Independent checks bind both endpoint modules through the actual
interfaces and cable, including exact medium, length and assembly identity.
All installed optical module/end reservations contribute once per host as
`ceil(sum(mW) × 1.25 / 1000)` watts, separately from chassis and PoE allowances.
Inactive/disconnected installation still consumes reserve. The catalog records
the distinction between vendor maxima and authored per-end allowances.

Native `Interface.module` has `on_delete=CASCADE`: deleting a module can delete
the associated interface. This ownership graph supports baseline inventory and
stable growth; no removal/hot-swap transition is implemented. SDK/native surface
feasibility is recorded in `build/goal-connected-depth/design/phase5-surface.md`;
all-five final initial/repeat live qualification is indexed in GOAL.md and `lab/README.md`.

`poe.enrich(world)` runs after physical construction and equipment/optical modules, before
operations context. It follows actual PD copper paths and adds the summed integer
PSE reservation, multiplied by the explicit 1.25 AC allowance and rounded upward
to whole watts, once to the chassis allowance. Normal inlet draws sum exactly;
each supply reserves the whole total. `validate_poe.analyze(plan,catalog)` imports
no emitter and derives extra watts independently from actual types, ports, copper
paths and supplies. Existing power validators use its independently computed
extra watts and still check actual inlet totals/feed capacity. Catalog0.9 is an
explicit new baseline; the qualified phase-3 archive retains catalog0.8.

At the end of operations enrichment, `wireless_context.enrich(world)` adds each
site contract's `wireless_services` list: WLAN, actual IPv4 prefix, existing
technical contact, unallocated IPv4 count and bounded `dns_tcp`, `dns_udp` and
`radius_udp` service keys. Selection prefers distinct actual service sites, then
replicas, with at most two listeners per family. Capacity excludes actual IPs and
held ranges within the prefix/VRF; no DHCP execution is modeled. Enterprise WLANs
require identity listeners, while open guests omit RADIUS. Unacquired bank sites
explicitly declare external DNS/authentication requirements with unknown endpoints.
The independent checker requires entries and validates their graph dependencies;
missing contracts, metadata or explanatory text cannot excuse missing obligations.
Native WLAN comments expose the names for navigation. NetBox4.7 WLANs do not
inherit ContactsMixin, so support follows real site/AP contact assignments; no
native WLAN contact or WLAN-to-Service field is invented.

`operations.supporting_records(world)` attaches common ownership, contact
assignments, site VLAN groups and provisioned VM disks to observed infrastructure.
Enterprise and school use this shared helper; bank retains its richer authored
operations enrichment. None of these helpers load or manually repair target data.

In v0.8, both call `operations_context.enrich(world)`. Contact responsibility is
scoped by actual tenant, site, provider and workload references. All modeled
tenants retain technical desk identities, including an inherited tenant after
its last independent branch is acquired. This preserves global definitions;
selected-site assignments may change only during that explicit transition.

Journal identity is `(assigned_object, comments)` on wire. Fixed seeded dates
and immutable object facts produce two site notes, two circuit notes and two
notes on the lowest existing VM ordinal per cluster/workload. Appended inventory
does not rewrite existing notes. The dated body is authored history, not a native
event timestamp or evidence of execution. Independent checks derive required
contacts, assignments and bounded note forms from graph/recipe, outside the
legacy bank operations-contract gate. Emitted metadata cannot suppress them.

v0.9 applies primary MAC identities to eligible addressed physical/VM interfaces
in all profiles; virtual/bridge interfaces are excluded. Independent checks
require missing identities and their stable namespace/interface-derived values.
Direct technical assignments on infrastructure roles use the device's actual
tenant desk. Bank WAN accounts distinguish permanent procurement lineages through
actual circuit A-side sites and retained design assignments. Acquiring or
refreshing Birch changes technical responsibility, not the retained account.
Enterprise, school and hospital purchase through their estate accounts; provider
service policy retains separate customer/NOC/transit/transport account identities.

Equipment history is bounded to the first eligible infrastructure device per
actual rack, ordered by permanent U position and then canonical key. Two dated
records describe initial chassis placement and maintenance coordination. A third
PSU preparation record follows an installed module through its own power port,
module bay and type when present on the selected device. Facts exclude mutable
upstream wiring, inventory totals and current tenant desk names; the stable site
facilities desk coordinates access, while current technical assignments remain
separate. New racks gain records and ordinary growth preserves old subjects and
comments. These records do not provision spares or execute component replacement.
An additional optical preparation note is required when the selected device's first
fixed catalog optical cage is occupied. Selection is independent of occupancy,
so new occupied ports cannot change the anchor; a formerly empty fixed cage may
append its first note. Its 40–59-day chronology is disjoint from the existing
equipment/maintenance/PSU bands. Exact body facts follow the actual module, bay,
type and facilities contact. The independent checker derives that obligation
without metadata/contracts and rejects a missing or foreign-device association.
Shared equipment validation derives configured PSU requirements from the actual
device type and catalog, requiring the correct manufacturer/SKU, active installed
module, enabled bay at its declared position, explicit compatible bay type, and
ownership of the configured inlet. Optional descriptive metadata is not authority.

Hospital development additionally assigns medical-device and imaging-device
roles to a biomedical support desk scoped to their actual site. This is an
explicit inventory/maintenance responsibility; it does not imply medical
certification, clinical operation or an executed maintenance event. Existing
profiles retain their four original contact responsibilities.

Scenario scope inference follows a ContactAssignment's actual `object` reference;
it does not infer local ownership of the shared contact/group/role directory.

For bank DCs, validation derives service counts from recipe demand, compute hosts
from actual cluster membership, and power obligations from installed catalog
supplies. Emitted contracts must match those obligations; removing a contract or
weakening its budget/minimum cannot suppress the underlying check. Host resources
and reserve are independently checked against bank planning policy. Required
power paths count only active devices/PDUs/feeds connected by cables marked
connected. Required compute hosts, clusters and VMs must be active to satisfy
baseline availability. DC equipment rack placement is checked independently;
missing placement cannot erase power obligations. Contracted DC uplinks require
connected cables, enabled endpoint interfaces and active endpoint devices. A
service VM's primary address must belong to its enabled VM interface.
Unused planned clusters remain valid inventory. These are
inventory-state checks, not proof of operational service reachability or recovery.

The regional-bank recipe accepts weighted `design_mix`, explicit `site_designs`,
and `acquired_sites`. `generate(recipe, previous=None, transition=None)` freezes
per-site design choices before physical allocation. An existing site's design
and ownership cannot change during ordinary growth. A transition is exactly
`{"site": branch_id, "operation": "acquire" | "refresh"}` and must be the only
recipe change: acquire an independent inherited branch, then refresh it.
Lineage determines retained naming/VRF/address reservations independently of its
current access hardware and tenant. Refreshed access devices have new keys;
retired device/management/rack slots are not recycled.
Before reuse, the generator reconstructs the prior plan with its own ledgers and
compares objects/contracts. Valid slot types alone do not prove consistency with
existing addresses, ports, or rack placements. Manually edited graphs are outside
the supported previous-plan contract; the integrity check fails before growth.

`scenarios.create(plan, site_id)` returns `plans` (before/acquired/refreshed),
`changes` (acquire/refresh, with complete create/update/delete objects), graph-derived
`answers`, `checks`, and `limitations`. Independent comparisons establish endpoint
identity/address preservation and unchanged other-site objects; validating one
snapshot alone cannot establish those properties. The CLI serializes snapshots
separately and replaces `plans` with `plan_files` in `scenario.json`. Its changes
are review data, not a new target reconciliation or deletion protocol.
Changed/created/deleted records must belong exclusively to the selected site.
Unscoped shared definitions remain immutable. A VRF can inherit site scope only
from all of its actual consumers; a global or shared consumer prevents treating
the VRF as exclusive branch property.

Exporter exposes `export(plan, output_dir) -> dict` and emits bounded replay JSON
requests plus a manifest. It must be pure stdlib and deterministic. SDK validation
is a separate optional check with the pinned SDK. It must preserve immutable
canonical identity while building thin, scoped SDK references. Exported timestamps
are explicit synthetic observation time from recipe `as_of`; report that replay
preserves them and that replay acceptance does not establish reconciliation.

See [modeling](docs/modeling.md) for supported scope and
[usage](docs/usage.md) for generation commands.


Aggregate prefixes must be globally disjoint in one generated plan. RIR identity
scopes Diode matching, but does not relax NetBox4.7's native overlap prohibition.
Collapse overlapping private allocation inputs to their covering aggregate;
keep actual per-VRF prefixes and address reservations unchanged. Local replay
checks foreign target overlap before submission. It observes existing state;
it does not allocate, renumber or lock shared resources.

Ordinary growth must retain existing aggregate keys. A new site allocation that
would collapse a retained aggregate into a larger prefix requires a new baseline
or an explicit future aggregate-expansion transition; it cannot silently delete
the old root. Target coexistence also requires disjoint aggregate ranges or an
exact owned match (prefix, RIR name and description). A namespace does not bypass
this global constraint. The local receipt records `aggregate_preflight` separately
from global ASN/FHRP identities and external-user bindings.

Root ContactGroup names must derive their intended slugs using Django's default
ASCII slug rule. On plugin1.17, the wire omits that slug to activate its fallback
matcher; an explicit slug bypasses it and can create duplicates. The canonical
slug remains checked by REST readback, and SDK verification rejects old exports
with explicit root-group slugs. Child groups retain their parent/name identity.


## Provider composition

`provider.py` resolves finite PoP and private-L3 customer demand. The independent
`validate_provider.py` derives its obligations from the bounded recipe, actual
references and reservation ledgers; removing descriptive contracts cannot erase
required customer, transport or NOC paths. The full policy and sourced hardware
mode are documented in [profiles/provider-backbone.md](profiles/provider-backbone.md).

Provider site allocations use /24 units. NOC `dc-01` occupies slot 0 and the first
/16; small-site slots start at 256. The final /16 is reserved for routed /31s and
PE /32 loopbacks. Neither small sites nor their growth may consume these reserved
ranges. Other profiles retain their original allocation units. Customer, PoP
order, transport-parent/port, service-port, link-prefix and loopback reservations
are persistent; additions do not rewrite existing physical attachments.

Physical circuits have actual cabled A/Z site ends. A customer virtual service
termination belongs to an enabled virtual CE interface whose physical parent
owns the real access handoff. Customer VRFs, route targets, ASNs, accounts and
contacts retain their tenant relationships. Opaque external transit has only a
known local IP; its far endpoint owner remains unknown. The whole /31 is reserved.
PE primary IPs belong to in-band `lo0`; dedicated `fxp0` stays unaddressed and
uncabled. Management switches reach the PE data plane through actual /31 links.

The shared DC builder accepts an internal `wan_attachment` callable. Provider
policy uses it to attach both NOC edges to distinct requested PoPs. The shared
DC checker retains fabric, service, compute, management and power obligations;
only its legacy opaque-carrier WAN branch is replaced by the independent
provider checks of actual NOC circuits, addresses, ports and purchased headroom.
There is no user-facing validator skip switch.

The capacity witness is a bounded directed customer spoke-to-hub flow model
under normal operation and each single inter-PoP span loss. NOC/background/
transit/return demand is excluded. Router and local-pair failures have a topology
connectivity witness only. Customer access is single-homed, and no routing or
application execution follows from these inventory checks. Carrier-wide failures
are excluded, and per-PoP carrier diversity is not guaranteed. Reports count the
actual providers of inter-PoP spans through their cabled A/Z router attachments;
multiple spans at a PoP may share one carrier. Different carriers do not establish
independent underlying infrastructure. Two-site circuit journals derive both real
termination names and speeds. External-transit journals name the local handoff and
provider-network boundary, leaving the remote interface and owner unknown;
ordinary growth retains the
purchased CIR and dated record. Reports keep two-site handoffs separate from
the other profiles' additive opaque-carrier WAN budgets.

## Shared power-diversity scenario

Top-level `demo` accepts `baseline` and `loss-of-power-diversity` across all five
profiles. Bank's absent default remains absent in frozen recipe bytes to preserve
qualified artifacts. Generation constructs the healthy baseline; CLI generation
and frozen build dispatch narrative intent to checked scenario artifacts.

`power_scenario.create(plan, site_id=None)` returns a versioned envelope containing
baseline/changed plans, the graph-selected subject, physical paths, affected
consumers, exact expected findings, cable diff, inverse restoration and execution
limits. `verify(envelope)` rederives claims from the plans, validates the healthy
baseline, requires the precise code/object finding multiset on the changed graph,
and establishes that the inverse change restores the exact baseline. Unrelated
records and ledgers remain unchanged. Selection uses actual active VM/service and
power relationships, not device names or completeness-contract declarations.

The CLI writes separate snapshot artifacts and replaces embedded `plans` with
fixed `plan_files` entries. `scenario-check <scenario.json>` hydrates those fixed
relative files and reruns verification, then deterministically re-exports each
snapshot and requires identical manifest/request bytes and file inventory. This
binds the checked graph to loadable payloads, beyond SDK schema acceptance.
Ordinary `check` retains its zero-finding
requirement. The changed snapshot is labeled `expected-defect verified`, never
healthy, and must not be used for ordinary growth.

Both snapshots use the existing Diode exporter. The envelope is not an ingestion
or deletion protocol. A changed cable termination has a different matching identity;
upsert replay does not retire the previous cable. Qualify snapshots on separate
fresh disposable targets and preserve per-target receipts. Offline inverse
restoration is distinct from an unverified target-side rollback.

## Provider span-maintenance scenario

Provider alone additionally accepts `demo = "provider-span-maintenance"`.
`span_scenario.create(plan, span=None)` uses a fully validated healthy provider
baseline and an actual active inter-PoP leased span carrying declared customer
traffic. Automatic selection orders by affected premise count then canonical
span key, and requires all normal-maintenance flows to remain reachable within
directed usable capacity. An explicit subject must meet the same eligibility.
The saved envelope pins its subject; verification never silently reselects it.

The only permitted graph mutation is the selected Circuit's `attrs.status` from
`active` to `offline`. Non-object plan content, object key/count sets and every
other attribute/reference remain exact, including installed optical power and
immutable journals. Restoration reverses that same field, reproduces canonical
baseline bytes/hash and passes ordinary validation. A maintenance state is not
an instruction to delete a cable, module, contact or record.

Evidence is derived from actual tenant/premise/VC/access-circuit/PE attachment
and complete A/Z transport witnesses. Shared private BFS predecessor trees
retain the provider validator's shortest-hop/canonical-key tie behavior; the
builder stays independent. Per-premise path attribution is distinct from
aggregate directed load. Machine evidence uses integer kbps and exact decimal
usable capacity. Opposite full-duplex directions are never added together;
offered peak is never replaced by purchased commitment. Return, arbitrary
customer peer-to-peer, NOC and transit traffic remain outside the demand model.
Carrier-wide failures are also outside the checked failure set. Per-PoP carrier
diversity is not guaranteed; two spans can share one provider. Carrier interior,
shared ducts and optical-loss budgets remain unknown.

`verify` recomputes paths, contacts, directional loads, normal-maintenance
headroom and the exact ordinary-validation `(code, object)` finding multiset,
including multiplicity for further-failure conditions. The unavailable span is
expected; secondary failures may now partition the graph or break/overload
declared routes. Those findings do not imply present maintenance-state
unreachability. Any unrelated finding or altered claim fails verification.
An empty secondary-failure finding set is valid when the maintenance graph
retains all checked protection; neither the envelope nor report may invent loss.

The existing two-snapshot writer and `scenario-check` dispatch between power
and span envelopes. Both saved plans and every deterministic wire file are
checked. Span's changed-state label is `expected-maintenance verified`; ordinary
`check` still requires zero findings and rejects it as a healthy growth seed.
The parent report contains bounded affected-premise/path/direction examples,
total counts and truncation notices; complete attribution remains machine-readable.

This status-only transition retains native CID/provider matching identity. The
final representative baseline/repeat → changed/repeat → baseline restore/repeat
sequence passed through Diode on the same pinned local database, with exact
field/reference checks, stable IDs/inventory and rendered Offline/Active views.
`final/live/provider-backbone/transition-summary.json` under the goal evidence
root binds the result; `lab/README.md` gives the commands and stack scope.
The generated envelope itself proves only offline behavior, so its conservative
execution labels remain distinct from this external live qualification. Other
stacks and rewired-cable transitions need their own evidence. No routing,
traffic, approval or completed-work journal is executed by this scenario.

Shared enterprise/school operational records include an infrastructure owner group
and owner reference. The pinned NetBox4.7 OwnerSerializer requires `group` when
creating an owner even though its model permits null. This was found in live Diode
qualification, and an independent missing-contract mutation check now rejects
omitted owner groups. Pre-correction enterprise/school artifacts are preserved as
predecessor evidence and must be regenerated; the qualified bank is unchanged.

When the estate pool is exactly one site reservation, foundation omits equal
per-VRF global roots. The scoped site containers remain authoritative parents of
active role subnets. This permits the documented enterprise one-DC /16 boundary
without duplicate prefix identity; larger pools retain the hierarchy unchanged.
