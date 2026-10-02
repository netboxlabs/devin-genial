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
- [List-view hygiene](#list-view-hygiene)
- [Contact and journal context](#contact-and-journal-context)
  - [Automation records](#automation-records)
- [Provider backbone geography and numbering](#provider-backbone-geography-and-numbering)
- [Provider BGP inventory](#provider-bgp-inventory)
- [Provider network lab](#provider-network-lab)
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
| `modern` | The selected `access` line; two direct upstream neighbors and two supplies | Site-local names and corporate address plan |
| `inherited` | Juniper EX3300-24P; one direct upstream neighbor and one supply per switch | Birch names, separately scoped VRFs and /20 reservations from 172.16.0.0/12; independent Birch ownership until acquisition |
| `refreshed` | New access identities on the selected line, two direct upstream neighbors and two supplies | Acquired Cedar ownership; Birch endpoint names and addressing retained |

These are fictional procurement designs, not vendor lifecycle or performance
claims. `modern` and `refreshed` follow the `[hardware]` recipe key's `access`
line — Cisco C9200L-24P-4X by default, Juniper EX3400-24P when selected —
while `inherited` stays bound to the acquisition story's EX3300-24P. Every one
of these models has 24 access ports in the selected configuration.
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
definitions from the few plainly named `Generic` parts (endpoints, ATMs, the
liquid-cooled lab pair, server optics). Servers, PDUs, patch panels, wall boxes,
console servers and APs are pinned real models; serials follow fictional
vendor-shaped formats and network gear carries its vendor platform
([serials](../catalog/README.md#serial-numbers),
[platforms](../catalog/README.md#platforms)).

## Physical-detail iteration

`profiles/bank-depth.toml` builds the same mixed bank demand under the separate
`cedar-complete` namespace with full cabinet-panel/wall-outlet paths:

```sh
just generate profiles/bank-depth.toml build/bank-complete
devenv --profile diode shell -- just sdk-check build/bank-complete/diode
```

The placement grammar supplies United States → state → metro regions (a site
hangs off its metro, `Chicago metro`; states and metros no site uses are pruned,
and there is no pass-through level between the country and its states), site
groups and IANA time zones. Building → floor → room locations place staff
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

### Site geography

Map coordinates are placed, not scattered. `estates/places.py` `ANCHORS` holds
authored points for each metro — real neighbourhood and suburb centroids
(Pilsen, Corktown, the Flats, West Allis, Elk Grove Village…) and straight runs
of the real streets the branch and store names use (Halsted, Woodward, Euclid,
Burleigh). A site whose display name mentions an anchor sits on it: `Chicago
Pilsen Exchange` in Pilsen, `Wabash and 9th Branch` along Wabash Avenue,
`Franklin Park Data Center` in Franklin Park, a customer premises at its PoP's
neighbourhood (the last mention in the name wins, then the longest), and
`North`/`South`/`East`/`West`/`Central` resolve to a real neighbourhood on that
side of the city where the city has one (Chicago has no `East` and Cleveland
no `North` on land). HQs default to downtown; every university building shares one
campus neighbourhood; any other site takes an in-city neighbourhood hashed from
its site id. A hash of the site id then offsets it at most ±0.0035° latitude
and ±0.0045° longitude (about 390 × 370 m), and for a street run chooses the
point along it, so growth never moves an existing site and seeds never reshuffle
positions. The street address names the anchor's municipality — `Dearborn,
Michigan`, `Lakewood, Ohio` — while region, time zone and the metro in
`meta.geography` stay the metro's; the hospital, school and provider checks
accept any authored locality of the expected metro (`places.LOCALITIES`).

Every anchor point, both ends and the middle of every street run, and the four
corners of their full offset box were reverse-geocoded against OpenStreetMap
Nominatim on 2026-10-01 to a road in the stated municipality, state and the
United States, and every street run lies within 0.3 km of the real street of that
name (OpenStreetMap Overpass geometry). Points within about a kilometre of Lake
Michigan, Lake Erie or the Detroit River were nudged inland, and campus names
were moved to real industrial or carrier areas (350 E Cermak, Elk Grove
Village, Franklin Park, Northlake, Southfield, Independence). The old ±0.12°
metro jitter plotted about half the lakeshore sites in the lakes or in Windsor,
Ontario. `tests/test_places.py` keeps coarse water/Canada exclusion polygons
offshore of each metro and fails if any anchor offset box or any site in a
sample recipe falls inside one, or off the anchor its name and address imply.
The coordinates remain synthetic: a position, never a premises claim. Adding
an in-city neighbourhood changes every hashed pick, so the anchor table is
rebaseline-frozen like the other naming pools.

Street addresses follow the same anchor (since 0.16.0). `places.ADDRESS_STREETS`
names, per anchor, real streets inside it: a street run is addressed on its own
street; a point anchor on the road Nominatim reverse-geocoded at its centre plus
the numbered streets reverse geocoding returned at eight more points inside its
jitter box (2026-10-02, cached under ignored `build/geo-verify/`). A hash of the site
id picks the street; the **house number follows the site's position**, never a
hash (only the odd/even side is hashed), so two sites near each other on one
street read near each other:

- **Chicago** directional streets use the city grid (800 numbers a mile from
  State and Madison; south of Madison through `CHICAGO_SOUTH`, read from the
  samples, because twelve hundred numbers fit Roosevelt Road's first mile).
- **Milwaukee County** (Milwaukee, West Allis, Wauwatosa, Oak Creek, Glendale)
  directional streets interpolate `MILWAUKEE_NS`/`MILWAUKEE_EW`, fitted to the
  samples: North/South divides near the Menomonee Valley and East/West at the
  Milwaukee River, so Oak Creek's Howell Avenue reads ~8,900 South.
- **Every other street** (Detroit, Cleveland, their suburbs, the Chicago
  suburbs) interpolates `STREET_REFS`: real numbered points on that street.
  Two well-separated points number it linearly along the line through them;
  one point numbers it by distance from the locality's address origin
  (`ADDRESS_ORIGINS`: Woodward at the river for Detroit, Dearborn and Highland
  Park; Public Square for Cleveland; otherwise the suburb's anchor centre).

In both grids a directional prefix follows the side of the grid the site is on.
`Workspace.finish` (`places.unique_addresses`) keeps every full address unique:
an earlier-allocated site keeps its number and a later one on the same street
steps two numbers along, and `validate` reports `site-address-unique`. Numbers
are synthetic, never a surveyed premises, and no ZIP code is claimed.
`naming = "legacy"` keeps the pre-0.16 sequential street lines.

Facility codes are per metro (`CHI01`, `DET03`, `CLE02`, `MIL04`), numbered by a
permanent `facility-codes/<metro>` ledger in creation order, so growth appends a
code and never renumbers one. The provider's own buildings — PoPs and the NOC —
carry fictional CLLI-style codes instead: four letters of the municipality
(initial plus its next three consonants: `CHCG`, `DTRT`, `DRBR`, `LKWD`), the
state and a two-character building code from the PoP's place words (`CR` for
Cermak, `NC` for New Center or the NOC), falling back to the ledger slot number
when the words only repeat the municipality or clash (`LKWDOH01`). They imitate
the shape of a CLLI code; none is a code assigned to a real building.

Location trees carry no pass-through levels. Site kinds whose grammar is one
ground floor or one data hall (`places.FLAT_KINDS`: branches, stores,
distribution centres, managed offices, plants, substations, data centres and
provider customer premises) hang their rooms directly from the site; a single
`Main building` holding a single `Floor 01` only lengthened every breadcrumb.
Kinds that can grow floors (HQ, schools, hospitals, clinics and university
buildings) keep the building and its floors, so growth never reparents a room.
A provider PoP is a leased carrier-hotel suite holding the provider's cage —
`Suite 317` → `Cage G09`, both hashed from the site id — and the cage is the
PoP's equipment room.

### Equipment-room cabinet layout

Data-hall profiles author a cabinet grid in metres on each rack
(`meta.position_m`), and the independent DC checks read it back from the
finished graph. Cabinets are 0.6 m wide and bayed contiguously along a row of
four; rows repeat every 2.4 m, which is the cabinet depth plus a working aisle;
the network and compute zones stand side by side across one 1.5 m main aisle;
the whole grid starts 1 m inside the room. Zone origins come from the reviewed
row length rather than from how many cabinets are installed, so appending a
cabinet to either zone never moves an existing one — the same append-only
guarantee the [floorplan geometry sidecar](loading.md#floorplan-geometry-for-visual-explorer)
depends on when it renders these coordinates.

The checks reject more than duplicate coordinates: two cabinets whose 0.6 m ×
1.07 m footprints intersect are a placement error even at distinct points, and
a room whose cabinets sprawl down one axis is reported as a corridor rather
than a room. Cabinet distance also sets modeled inter-rack cable length, so
compacting the grid shortens those runs.

Cabinets are sized to their content. A rack lane admits ten devices, each
mounted in a single rack unit from the bottom rail upward, so a lane can never
need more than eleven units: an enclosed four-post 24U cabinet (APC AR3104) is
the honest size for it, where a 42U cabinet would be three-quarters empty by
construction — a rendered PoP cabinet already read `21% utilized`. A provider
PoP cabinet holds no fibre enclosure of its own: one with no ports or cables
was a prop, and a carrier handoff's patch position is the carrier hotel's
meet-me-room panel, recorded on the termination's `pp_info` (see the
provider section). A
single-CE premises — a provider customer office, one CE and one switch — takes
the small-room kit instead: a 13U Panduit R2P26 two-post rack, one 1U APC AP9563
120 V PDU at the top unit on one 120 V / 20 A branch circuit, and no console
server (the two consoles stay local spares). The `facility_id` is a room-scoped
cabinet code — room tag, row, zone letter and bay, such as `DH-02-C03` in a data
hall, `G09-01-N02` in PoP cage G09, `IDF02-01-N01` in a closet — and the rack
name stays the short `N01`/`C01` breadcrumb. Asset tags are short and estate
sequential (`INLAND-FIBER-00042`): the uppercase namespace keeps NetBox's global
uniqueness across coexisting estates and a permanent `asset-tags` ledger numbers
racks in creation order. Zero-height equipment (0U PDUs, access points, wall
outlets) is assigned to its cabinet without a mounting position, which is how
NetBox itself models 0U devices — they appear under the rack's non-racked
devices rather than in the elevation. The geometry sidecar draws a two-post rack
at its real 0.52 × 0.28 m footprint and every cabinet at 0.6 × 1.07 m.

Rack roles and device-role colors make elevations easier to interpret. Racked
infrastructure carries explicit synthetic power allowances, split across its
installed supplies, with full allowance on either feed for failover. Each PDU's
own input port carries the total of the inlets cabled to its outlets — the
normal splits in `allocated_draw`, the full device allowances in `maximum_draw` —
so a power chain reports real utilisation at the feed and panel above it; NetBox
does not compute that upward, and an empty input makes the whole chain read 0 W.
These are
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
and allocation ledgers remain separate from display names. Every other family's
`name` is an authored label with no namespace in it — the policy and its two
reasoned exception sets live in `estates/naming.py`, and anything composed from
a site reads `Site.display`, never the namespaced `Site.name` slug stem. Since
0.15.0 `tests/test_naming_policy.py` sweeps every emitted kind that has a
`name`, so a new family is covered without being added to a list. Diode references
retain both site and tenant, matching its
[scoped device criteria](https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/docs/matching-criteria-documentation.md).
DNS retains the full namespace, such as `birch-s0002-atm01.cedar-demo.example`.
Synthetic device asset tags are omitted because
[NetBox displays them beside the name](https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/models/devices.py),
which previously duplicated long labels. Serial numbers remain descriptive data;
they are not Diode matching identities. Acquisition still requires explicit target
reconciliation, particularly when an unracked device's tenant changes.

Provider hostnames are readable stems rather than site-id digests: a PoP's
routers are named from its key (`chicago-cermak-pe-a`), and a customer premises
from its customer key and the site's own facility code
(`lakeshore-health-cle03-gw01` at facility `CLE03`), so the hostname matches
the site record and stays unique and growth-stable. PoP keys shaped like those stems or like the
NOC's `dc01` are refused. Every VLAN in every profile is named for its segment
(`Clients`, `Management`, `POS`) inside its site-scoped VLAN group: NetBox holds
VLAN names unique per group (`unique_group_name`, pinned 4.7.2
`ipam/models/vlans.py`), so the site prefix only repeated the group.

Every prefix and VLAN carries an IPAM role (`ipam.Role`) so the Role column,
prefix heatmap and IPAM radial map colour by purpose. One authored set lives in
`naming.IPAM_ROLES` — Allocation pools, Backbone, Transit, Loopbacks, Management,
Users, Voice, Wireless, Guest, Servers, Storage, Security, Payments, Clinical,
Operational technology, Customer, DHCP pools and Reserved, weighted in that order — and an estate emits
only the roles it uses. `naming.prefix_role` derives each prefix's role from
the finished graph: host routes are Loopbacks and /31 or /127 links Transit;
a prefix bound to a VLAN shares that segment's role (`SEGMENT_ROLES`); any
other prefix follows its VRF (a provider customer VPN is Customer, a
per-segment context its segment; the one VRF-less pool container is
Allocation pools). IP ranges are DHCP pools when active and Reserved when held.
A role that holds the estate's own VLAN segments is described by their
purposes (a carrier's Servers role reads `Application servers; Database
servers`, never the shared vocabulary's students or research compute); the
others keep their stock description.

The address pool is one global container under its aggregate; a global
container parents prefixes in every VRF, so the per-VRF copies of the pool are
gone. Each site's block is likewise **one global container**
(`prefix/<site>/reservation`, scoped to the site, with the site's tenant; the
IPv6 /48 is `ipv6/reservation/<site>`) parenting that site's segments in every
VRF — since 0.16.0 it is no longer repeated once per VRF (the provider NOC's
`10.0.0.0/16` used to appear five times). Being VRF-less it carries the
Allocation pools role.
`networking.address_ranges` fills every VLAN-bound IPv4 LAN of /26 or larger
so a client segment reads top to bottom with no unexplained gap: a Reserved
infrastructure block between the gateways and `.10` (where `Site.address`
starts endpoints), then on client segments (Users, Wireless, Guest, Voice
roles) an active **static** range from `.10` (the segment's own role), the
**DHCP scope** from the first quarter mark clear of every static assignment
(normally the upper half; guest segments, which have no static clients, from
`.10`), and a Reserved headroom range holding back `reserve_fraction` of the
scope at the top. A dense-static segment whose assignments pass three quarters
has no DHCP scope, and growth that pushes static clients past a quarter mark
moves the scope up (a `ponytail:` note in `networking.py`); a held range is
emitted only while no assigned address occupies it. WLAN capacity
(`wireless_context`) counts only held (reserved) ranges and actual addresses
as consumed; DHCP and static pools are client capacity.
`validate_networking` re-derives and enforces every assignment (`ipam-role`).
Role names are namespace-free like device roles; Role is a branch-scoped
OrganizationalModel and the loader's occupancy gate keeps two estates out of
one scope.

Descriptions use the words an engineer would put on the record, from shared
helpers in `estates/naming.py`: device roles read as `Provider edge router` or
`Rack PDU` (`ROLE_LABELS`), segments as `Office workstations` or `Point-of-sale
lanes` (`SEGMENT_PURPOSES`), committed rates and handoffs as `1 Gbps access
committed on a 1G handoff` (`bandwidth`/`port_speed`), and routed /31s name
both ends. **No record carries a disclaimer.** Names, descriptions, comments,
labels, module attributes and journals hold operational data only; the
modeling limitations are documented once, below and in the generated
`report.md`, never restated per record (both showcase reviewers called
per-record disclaimers the loudest synthetic tell). `naming.DISCLAIMER` backs a
`record-disclaimer` validation finding that refuses phrases such as "not
verified", "no … is claimed", "documentation inventory", "fictional",
"placeholder", "planning intent" or "unknown". Endpoint descriptions read in sentence case with
acronyms intact — `Classroom AP`, `Point-of-sale lane`, `Station HMI`,
`Bedside monitor`. Tags are graph-derived (see
[list-view hygiene](#list-view-hygiene)); nothing selects rows by them.
Journals state rates in operator units (`Ordered 100 Gbps from …`, `naming.rate_kbps`), WAN
circuit comments name their procurement cohort in words (`Standard branch
order: …`, `naming.COHORT_LABELS`), service desks name their workload
(`Teller API service desk`), and sites carry no boilerplate comment — bank
branches keep a one-line lineage note. The four automation records (config
contexts, export templates, webhook, event rule) have no REST-writable
`comments` in the pinned 4.7 serializers; their descriptions read
operationally (`Notify NetOps automation of device changes; disabled until the
receiver is live`) while the webhook stays on a reserved `.invalid` host and
its rule ships disabled — structure `validate_operations` enforces.

### Documented limitations

These hold for every estate and are stated here and in the report instead of on
the records they concern:

- **Execution.** Nothing is configured, applied, executed or measured: VM
  replication and recovery, routing, forwarding, VRRP, IPsec and the recovery
  L2VPN, wireless authentication, DHCP, captive portals, RF coverage and the
  diagnostic radio hop are inventory. AP mount positions are planned, not
  surveyed.
- **BGP.** Provider sessions, peer groups and policies document intended
  peerings; no session state, route exchange or policy evaluation is claimed
  (see [provider BGP inventory](#provider-bgp-inventory)).
- **External parties.** Transit interiors, a transit peer's remote interface
  and owner, carrier interiors and duct diversity are unknown; separate
  providers do not establish diverse ducts. External bank DNS/RADIUS endpoints
  are unknown.
- **Commercial records.** Provider accounts hold no credentials and record no
  live purchase; circuits are purchased capacity without an acceptance test;
  the future WAN rack reservation records no purchase or installation; the
  Asset Lifecycle sidecar's vendors and courier are fictional and its orders
  carry no prices.
- **Industry endpoints.** Clinical, imaging, OT plant-floor and utility station
  endpoints are reference inventory with no clinical function, certification,
  control function, protection setting or industrial/utility protocol.
  Residence-room ports are installed capacity, not resident devices; MSP sites
  carry no SLA, remote-access path or ticketing workflow.
- **Automation.** The webhook's `.invalid` endpoint is a placeholder and its
  event rule is disabled; config contexts are documentation intent.
- **Addressing and hardware.** The IPv6 registry allocates documentation space;
  optic `power_reservation_mw` values follow the catalog's `power_basis`
  (vendor maxima or authored conservative reservations, see
  [the catalog](../catalog/README.md)).

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
US feeds are 208 V / 20 A single-phase circuits — the AP9572 is a 16 A 208/230 V
PDU, the 80% continuous load of a 20 A breaker — and a single-CE premises runs
on one 120 V / 20 A circuit with one PDU and one panel; every supply still needs
its own active local path (`validate_power`'s `single_feed` sites come from the
provider validator's own premises list). The validator checks device/PDU/rack
locality and panel-room consistency as well as capacity. Separate panel objects
do not establish independent utility feeds. Rack names such as `N01` repeat in
different rooms; references and the `facility_id` cabinet code retain room
identity. Equipment names include the room, e.g. `hq01-idf02-as01`.

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
| Carrier and recovery services | Virtual circuits over real WAN handoffs (hub and spoke terminations); planned IKE/IPsec tunnel and translated VXLAN recovery segment between the DCs |
| Operations | Contacts/owners, commercial accounts, tenant groups, A/B circuit groups, cluster groups, per-role VM types and disks, cabinet types, the site service-class field, journal entries and the site-equipment link — every profile; the bank adds its DC01 rack reservation and cable bundle |
| Automation | A global config context carrying this estate's own service endpoints and a role-weighted switching context; CSV export templates for devices and cables; an inert `.invalid` webhook with its disabled device-change event rule (loader-only: no Diode entity exists for these four kinds) |

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
the same object. Services carry NetBox 4.7 `port_mappings` (`tcp/53`,
`udp/53`) rather than the deprecated single `protocol`/`ports` pair, so one DNS
service lists both transports: a listener named `<service>-<protocol>`
(`dns-udp` beside `dns`) folds into its service (`datacenter.services`). The
loader writes `port_mappings` on a 4.7 target and falls back to
`protocol`/`ports` only for a single-protocol service on an older one. No
ServiceTemplate kind is emitted. `report.md` shows bounded primary and listener examples from
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

Either AP line reserves 30 W at its actual serving Type 2 PSE port. Cisco
C9200L access switches plan against 370 W after losing one of two compatible
600 W supplies, and the Juniper EX3400-24P line plans against the same 370 W
after losing one of its two 600 W supplies; EX3300-24P uses its 405 W
fixed-supply PoE budget and claims no supply redundancy. These are separate from copper port headroom. New demand can
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
module type's datasheet attributes. The report shows actual support contacts
and complete known local cable paths, including passive ports when present.
All five final representatives passed initial/repeat live ingestion and native
inspection on the pinned stack. The final offline scale and SDK results are
indexed in [GOAL.md](../GOAL.md); these do not prove optical loss budgets or a
large live load.

The finite [catalog](../catalog/README.md) selects SR/SR4 multimode parts for
in-room jumpers and LR/LX/LR4 (and long-reach LH/ER4 Lite) single-mode parts
for everything that leaves the room, for reviewed Cisco, Juniper, Arista and
Fortinet cages, plus an explicit generic server SR transceiver. The alternate Juniper access and leaf lines carry their own
reviewed parts, including a `JNP-100G-AOC-3M` peer assembly. A configured 1G MX204 handoff receives a 1G part (LX, or LH past 10 km of owned
fiber) even though its cage can also carry 10G. Existing three-meter Arista peer links use `AOC-Q-Q-100G-3M`:
one active optical cable, two captive end modules, one shared assembly serial.
The existing cable label remains stable and its comments show the assembly
serial. The two end records are not two independently replaceable purchases.
Unused cages remain empty; fixed copper ports do not receive optical modules.

Reach follows the run, not the cage (since 0.16.0). A direct jumper between
two cages in one room, no longer than the shortest reviewed multimode reach
(100 m, SR4 on OM4), runs OM4 multimode with SR (duplex LC) or SR4 (MPO-12)
optics when both cages take a reviewed multimode part: PE pairs, PE-to-
management-switch uplinks, NOC leaf-server and leaf-spine links. Building
backbones between rooms, carrier handoffs and owned spans stay single-mode. A
cage with no reviewed multimode part keeps single-mode on its jumper — the
FortiGate 100F, because no primary source confirms `FN-TRAN-SFP+SR` on it —
and a channel through LC patch panels stays single-mode until the passive lane
model covers MPO. `optics-reach-class` refuses a 10 km part on a jumper a
reviewed short-reach pair would serve; the medium decides the part, so the
multimode cable and both SR ends always agree.

Source-backed host fit, protocol and medium are separate checks. The authored
local envelope is 3–100 m; catalog reach is not an optical loss budget or
measured receive power. A third-party carrier's circuit termination ends the
local path: it does not reveal the carrier's intercity span or remote optic.
The operator's own fiber does not end there (since 0.16.0): on a circuit the
estate's operator provides, the optic must reach the circuit's `distance` (or
its two sites' route distance), so owned dark-fiber spans past 10 km carry
`QSFP-100G-ER4L` and premises past 10 km `SFP-1GE-LH` (with the vendor's
short-link attenuator noted on the module); `optics-span-reach` re-derives it
([catalog](../catalog/README.md#installed-optics-policy)). Source records retain
indexed-only retrieval limits, software/FEC conditions and reference-host
assumptions. No additional RF, breakout, DWDM or applied routing behavior is
implied.

Power includes all installed optical modules, even when inactive or disconnected.
For each host, add `ceil(sum(module/end mW) × 1.25 / 1000)` watts once to the
separate chassis and PoE allowances. The catalog distinguishes vendor maxima
from conservative authored reservations; each AOC end reserves 3.5W rather than
claiming a verified whole-assembly power split. This is planning reserve, not
measured consumption or an efficiency calculation.

No journal restates an installed optic: its part, serial, bay and interface
are the module's own fields (the 0.16 optic and PSU replacement notes were
dropped for that reason). Native
[`Interface.module` ownership](https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/models/device_components.py)
uses cascading deletion. Preserve these records during growth: no module removal,
hot-swap or executed optic replacement is modeled.

## List-view hygiene

A list view should tell an engineer something. Since 0.16.0 every profile's
finished graph passes through `operations.finalize` (registered by the shared
operations builders and run first by `World.finish`, after BGP and the lab),
and `validate_operations` re-derives each rule independently with a failing
mutation in `tests/test_estate_hygiene.py`.

- **Statuses follow the ledgers.** Infrastructure keeps `active` (independent
  checks never count an inactive path as healthy capacity), but the estate now
  shows the states its own plan implies: DHCP scopes (`active`) and held
  infrastructure/headroom ranges (`reserved`) in every LAN; unused access and
  leaf switch ports `enabled: false`, matching the switch baseline context's
  `disable_unused` (a cabled port is always up); and one `planned` cabinet at
  the next compute position of every gridded equipment room. That cabinet takes
  the key, name, facility ID and grid coordinate the next compute lane will
  use, so growth turns the same record `active`; nothing is installed in it or
  powered. Journals carry `success`/`warning`/`info` by event and contact
  assignments `primary`/`secondary`/`tertiary` by desk order
  ([below](#contact-and-journal-context)). Devices, sites and circuits stay
  `active` unless the recipe says otherwise: only the provider's customer
  lifecycle keys ([below](#provider-customer-lifecycle)) set planned, staged or
  decommissioning premises; the acquisition/refresh, power and span-maintenance
  snapshots carry their own states.
- **Tags name properties the graph shows** (`naming.TAGS`): `Hub site` (hosts a
  cluster or a private-WAN hub), `Dual-homed` (active WAN access from two
  different carriers — a provider's own circuits are not a carrier and console
  broadband is not WAN access — or access circuits into two different provider
  edges, as a provider customer hub has), `Acquired` (Birch lineage sites and devices), `Route reflector`
  (the remote end of iBGP client sessions), `Transit edge`, `Managed CE`, and
  `PCI scope`, `Clinical` and `OT zone` on the payment, clinical and
  operational-technology VLANs, their prefixes and every device that carries or
  attaches to them. A tag lands only on its declared kinds, every emitted tag
  is used, and a tag that would label every candidate of its kinds is dropped
  as uninformative. NetBox's `Tag.object_types` restriction is declared in
  `naming.TAGS` and enforced offline; it is not yet written to the target (the
  TurboBulk path cannot carry that many-to-many).
- **Taxonomy lists only what is used.** Device and rack roles, regions, device
  types, module types, module-bay types, module-type profiles and makers
  nothing references are dropped (since 0.16.0 for hardware types): a spare
  console-server size, the generic endpoint in a carrier that inventories no
  customer desks, or an AOC no cage holds is shelf clutter. Growth that later
  installs a pruned type creates it again. One lineage is kept: while a bank
  carries its acquired-branch line (`inherited-access`), that device type and
  the part definitions it and the access line a refresh installs can take
  stay, so the refresh snapshot neither adds nor deletes a shared type
  (`optics.RETAINED`). PSU module types carry no profile: the catalog pins
  only each supply's model, and a profile whose one attribute was a source URL
  read as provenance, not a spec sheet. NetBox's builtin `Power supply`
  profile wants voltage and wattage the catalog does not pin, and the loader
  cannot reference a pre-existing builtin row (the allowlist excuses one only
  when disjoint from the plan).
- **The switch baseline targets switches that exist.** The `Switch platform
  baseline` config context scopes every switching role some device holds
  (access, management, distribution, stack, leaf, spine), never a role with
  no devices; `automation-context` refuses an idle one.
- **One colour palette.** Device roles, rack roles, tags, module-bay types,
  inventory-item roles and circuit/virtual-circuit types share one authored
  palette (`naming.PALETTE`): no two coloured records in an estate repeat a hex,
  so a role badge, a tag pill and a rack-role fill never read as the same thing.
  A record the palette does not list takes the first unused `SPARE_COLORS`
  entry. Cables carry their jacket colour by medium instead (`CABLE_COLORS`):
  single-mode yellow, multimode and AOC aqua, copper blue, serial console cyan,
  and power black on the A feed and red on the B feed (an equipment cord moved
  between PDUs keeps its jacket, which is how the power-diversity defect looks
  on the floor). Every RJ45 console run is typed Cat 6.
- **One owner.** Sites, clusters, circuits, devices, racks, prefixes, IP
  addresses and VLANs all name the estate's accountable owner (NetBox 4.5+
  object ownership), not just the sites and circuits.
- **Device types carry sourced physical facts.** `part_number`, `weight` /
  `weight_unit` and `airflow` come from each model's SHA-pinned
  devicetype-library file and nowhere else; a fact the source omits is left
  empty (the AP's source is the -E part, so the installed -B part carries no
  part number).
- **Groups and types on every profile.** Tenant groups (provider customers join
  `Customers`), A/B circuit pairs keyed from the builders' `…/a` and `…/b`
  circuits plus a provider `Backbone spans` group, rack types by height, a
  cluster group, one VM type per VM role, and the site `Service class` custom
  field with the site-equipment custom link. The service class is the band of
  the site's summed committed circuit bandwidth (Essential ≤100 Mbps, Standard
  ≤500 Mbps, Enhanced ≤2 Gbps, Aggregation ≤10 Gbps, Backbone above; an owned
  fiber span counts its handoff port) — information the hub-site and
  dual-homed tags do not already carry. Rack groups are not emitted: grouped by
  room kind they only mirrored the site groups (data-model review HIER-6).
- **Racks take their type.** Every rack references the catalog rack type for its height and carries no per-rack
  `form_factor` or `width` (deprecated in NetBox 4.7, removed in 5.0).
  `Rack.save()` copies a type's physical fields onto the rack; TurboBulk skips
  save, so the loader performs that copy at insert time
  (`turbobulk._save_copies`), and also stores a device type's weight in grams
  the way `WeightMixin` would. Module-bay types are classes per maker form
  factor (`Arista AC PSU bay`, `Juniper SFP+ optic cage`); which supply a
  chassis takes is still checked against that device type's own catalog entry,
  never the class name.
- **Interfaces read like the platform.** A gateway interface keyed `VlanN`
  takes the platform's routed-VLAN name (`irb.N` on Junos via the catalog's
  `svi_format`, `VlanN` on EOS/IOS XE; `blocks.svi_name`). A routed VLAN
  interface carries no 802.1Q mode or untagged VLAN — NetBox clears an
  untagged VLAN from a modeless interface — so its VLAN is the one its own
  address's prefix is bound to; the independent checks re-derive it that way
  (`validate_networking.routed_vlan_view`) and refuse an SVI that claims a mode.
  The same holds for a host's own port (since 0.16.0): only a switch's
  non-management port (`role/access`, `management`, `distribution`, `stack`,
  `leaf`, `spine`) carries an access-mode VLAN. A PE `fxp0`, console-server
  `NET1`, server `BMC`, a switch's own `Management1`, a firewall `mgmt`, a
  CE `port1` and an endpoint's `eth0` carry no mode; their segment is their
  address's prefix VLAN (a Junos port's, through its untagged `.0` unit), read
  through the same view (`interface-host-mode`
  refuses one that claims access mode). A host's tagged trunk (hypervisor
  uplink, firewall or AP trunk) does tag frames and keeps `tagged`; a virtual
  child of a tagged parent is an 802.1Q unit and keeps its access VLAN, and an
  L2VPN attachment with a VLAN translation policy keeps its mode. A child of
  an untagged parent (the CE `Clients` unit on `port1`) is routed and
  carries none.
  Junos addresses the loopback on logical unit 0: the PE's `lo0` carries a
  virtual `lo0.0` child that holds its IPv4/IPv6 loopbacks, and BGP peers from
  it. Every other addressed physical port of a PoP's Junos device — PE data
  ports, `fxp0`, a Junos management switch's routed uplinks — is addressed the
  same way, on a virtual `<port>.0` child that carries the address and routing
  context (`provider._junos_units`); the physical port keeps its cable, optic,
  speed and MAC, and BGP local addresses cite the unit's address. Like-for-like optical links carry their cage rate as `speed`. Links
  between network roles (spine, leaf, core, distribution, WAN edge, access,
  provider edge) and over the operator's own backbone and dark-fiber spans carry
  jumbo MTU at the smaller of the two platforms' ceilings (Junos 9192, EOS
  9214, IOS XE 9198, otherwise 9000), so both ends agree; management, access
  ports and customer handoffs keep the default. No LAG is emitted: no two
  network devices in any estate share parallel same-type links.
- **One unused-port rule.** Every physical port that nothing names — no cable,
  address, unit, LAG, termination, VLAN or WLAN — is disabled, on every role.
  A port in service with no modeled cable (a carrier CE hands its LAN to the
  customer's own gear) is `mark_connected` instead.
- **Addresses don't repeat their interface.** IP addresses carry no
  description. DNS names are published only where an operator would publish
  them (since 0.16.0): primaries (`<device>.<domain>`), VM addresses, and
  loopbacks and dedicated management ports, interface-qualified
  (`lo0.<device>.<domain>`, `fxp0.<device>.<domain>`). Routed /31 and /127
  links, gateways, VRRP virtual addresses and service attachments carry none.
  Loopback addresses carry the `loopback` IP role.
- **Fingerprints look like hardware.** Vendor-OUI MACs (above), optic serials in
  the maker's label shape (`optics.serial_formats`, authored fiction), and
  optic module types carrying only datasheet facts — provenance and the
  planning power reservation stay in the catalog.

## Contact and journal context

All five profiles derive operational context from the same rules. Technical
desks follow tenant ownership; site facilities desks handle local access and
power-work coordination; carrier desks follow each circuit's provider; service
desks follow the VM's workload and tenant. Assignment priority follows desk
order: the technical (or service) desk is primary, the site facilities desk,
carrier desk and a VM's tenant technical desk are secondary, and a hospital
site's biomedical desk is tertiary. The directory uses descriptive `.example` mailboxes and
fictional phone lines: the real area code of the metro the desk serves (site
desks) or of the estate's first allocated site (tenant, carrier and service
desks), with a line in the 555-0100..0199 block reserved for fiction, picked by
a stable hash of the contact key. It does not create user accounts, coverage
hours or SLAs. The estate's one webhook
and event rule are automation *inventory* (below), not configured notification:
the endpoint is unreachable by construction and the rule is disabled.
Network infrastructure, APs and hosts expose their actual tenant's technical desk
directly on the device. Hospital medical/imaging equipment retains its distinct
site biomedical responsibility. Addressed physical and VM interfaces receive
stable primary MAC identities; virtual/bridge interfaces are excluded. A MAC
starts with its maker's public IEEE OUI (`catalog/hardware.json` `mac_ouis`;
VM interfaces use the QEMU/KVM `52:54:00` prefix) followed by a tail scattered
from an append-only per-OUI ledger slot, so tails stay unique and stable under
growth; makers without a declared OUI (generic endpoints, lab simulators) keep
a locally administered address. MACs carry no description.

Every circuit has its appropriate provider account. Bank procurement distinguishes
the retained Birch portfolio even after acquisition or access-hardware refresh;
provider customers, NOC, transit and transport retain separate accounts. Changing
technical ownership does not silently renew a commercial contract.

Journals carry only what their record cannot show (since 0.16.0): the change
ticket an event ran under and who ordered, confirmed or escalated it — never a
cid, termination, rate, date field, serial, cabinet, U position or module the
record already holds. Their kind follows the event: completed events are
`success`, a problem or open action is `warning`, everything else `info`. Each
site has a `Site access` note (book visits through the facilities desk, info).
Each in-service circuit has one `Handed over` note (success): `Accepted into
service under change CHGnnnnnnn.`, plus, for a third-party carrier, that
carrier's support desk confirming the handover and closing its ticket. A
provider backbone's own circuits are its products: one it sells carries a
`Service order` note (`Ordered by <buyer>; provisioning tracked under change
…`, info) and no carrier-escalation assignment. A withdrawing circuit carries a
`Disconnect order` (its own disconnect change; recover the handoff optics and
cabling, warning). One third-party delivery in five, chosen per circuit by a
namespace-keyed hash (so neither a reseed nor growth moves one), also has a
`Delivery slipped` warning 5–20 days before its handover: the carrier missed
the committed date and was escalated. The first VM in each site/workload has a
`First instance placed` note naming its host (success). Change tickets are
seeded per-subject choices. Each entry's
`created` timestamp is its own event date at 15:00 UTC, so the Journal list
reads as history rather than as the day it was loaded: TurboBulk inserts a
supplied `created` column as-is (it skips only auto primary keys; a missing
nullable timestamp would stay empty). The Diode SDK's JournalEntry has no
`created` field, so a Diode-replayed journal is stamped at ingestion
(`diode.LOADER_ONLY_FIELDS`). No entry asserts an acceptance test or
application health check.

Dates follow **one timeline** (since 0.16.0), derived from the graph's circuit
install dates: a site's service day is its first circuit (a PoP's first span, a
premises' access circuit) — the earliest service its equipment carries. Every
device there is installed 7–37 days before it, so a PE pair arrives together;
every serial's date code is a manufacture week 30–180 days before that install
(an optic serving a later circuit is made before its own port's circuit). The `Site access` note is written 40–70
days before the service day, ahead of any equipment. `validate_operations`
re-derives the chain and reports `operations-serial-date` and
`operations-journal-date`.

The first eligible infrastructure device by permanent U position in each rack
has an `Installed` note (success): racked and cabled under its change ticket,
the visit booked through the site's facilities desk (a PoP's carrier-hotel
remote hands). It is dated by that device's own install, 7–37 days before the
site's service day; site and VM notes never predate the service day either,
and a site without circuits keeps the `as_of`-anchored window. New racks gain
stories without rewriting existing rack history. The 0.15 `Order placed`,
`Circuit handoff plan`, `Keep a spare PSU` and `Optic replacement note`
entries restated their record's own fields and were dropped; see [installed
optics](#installed-optics-policy) for the assembly and
native deletion limits.

Ordinary growth retains existing journal text and contact identities. This matters
because Diode matches a journal by its subject and exact comments. Inventory totals
and current headroom stay in the report, where they can change without rewriting
history. Acquisition remains a reviewed snapshot change; updating responsibility
does not imply Diode will retire the former contact assignment in place.

`report.md` includes a bounded contact/journal tour with actual object references.
Open a site's Contacts and Journal views, follow a circuit to its carrier desk,
then inspect a workload VM's service desk and placement note. The full
directory and notes remain in the canonical plan and normal Diode package.

### Automation records

Every estate also carries a small automation inventory, unconditionally and
with no recipe key: two config contexts, two CSV export templates, one webhook
and one event rule.

The global config context is derived from the finished graph, not authored: its
`service_endpoints` (and the `dns_servers` resolver list) are exactly the
addresses this estate's own service listeners bind, so a demo can trace a value
in the context back to the VM that serves it. The second context is weighted
above it and scoped to the estate's switching roles; its data is explicitly
reference intent. Neither context configures a device, and Genial renders no
configuration. Growth may append a new workload's endpoints; it never rerolls an
existing list or renames a record, because these names are matching identities
on the target.

The export templates are CSV Jinja2 for NetBox to render on request from the
estate's own devices and cables — template text we emit, not code Genial runs.
Their syntax and the model attributes they read were checked offline against the
pinned 4.7.1 Jinja environment and models; the offline validator only checks
structure, and a live render belongs to the owed target qualification. The
webhook's endpoint is a host in the reserved `.invalid` top-level domain
(RFC 2606) and its device-change event rule ships **disabled**, so the estate
dispatches nothing. That is the point: the objects show the automation wiring an
operator would build, without asserting that any integration ran.

The pinned Diode SDK 1.14.0 has no ingest entity for any of these four kinds, so
the wire package omits them and names the omission in its manifest; only
`just load` delivers them (see [loading](loading.md#artifacts-and-diode)).

## Provider backbone geography and numbering

The provider backbone (`estates/provider.py`) is built from the map, not from
recipe order. `validate_provider.py` re-derives every rule below independently.

**Topology.** Inside a metro, the operator's own dark fiber rings the PoPs in
nearest-neighbour order (two PoPs get one span). Between metros, the chain is
the nearest-neighbour spanning tree of the metro centres — for the four authored
metros, Milwaukee–Chicago–Detroit–Cleveland, so nothing crosses Lake Michigan
and Cleveland never skips Detroit. Each chain adjacency gets two spans on
PoP-diverse ends where the metros allow it, one from each transport carrier.
Every PE keeps its local pair link plus at most two 100G transport ports. The
validator derives the chain separately (metros by longitude), rejects an
inter-metro span between non-neighbours, and requires two spans from two
carriers on two different PEs at each end of every adjacency.

**Growth.** The `provider-backbone-spans` ledger keys each span by both PE ends
(`circuit/backbone/<pop>-<side>/<pop>-<side>`) and is append-only. A new PoP
dual-homes to its nearest PoPs with a free transport port, in its own metro or a
neighbouring one; existing spans never move. A new metro that would sit
*between* two metros already joined by spans is refused: rebaseline. The
`provider-pop-launch` ledger fixes a breadth-first launch order along the
backbone.

**Capacity.** Owned fiber is lit at 100G and purchases nothing (`commit_rate` is
omitted). Leased inter-metro spans are **100G wavelengths** committed at the
full 100G of the `et-` port they land on: a port-based service matches the port
that terminates it, so a 10 Gbps private line handed off on a 100G port is
never emitted. (The retired `provider-span-upgrades` ledger must be absent.)
The declared spoke-to-hub flows must still fit after any single span loss and
the reserve. Each span carries `distance` in km: the great-circle distance
between its PoPs times a 1.3 route factor.

**Port naming.** The MX204's eight SFP+ ports keep their `xe-0/1/N` names when
a customer handoff runs at 1G: on this platform the 1G speed is set under the
`xe-` interface (`gigether-options speed 1g`) with the chassis port left at
10G, and Junos does not rename it `ge-` the way EX/QFX switches follow the
inserted optic ([Juniper port-speed guide](https://www.juniper.net/documentation/us/en/software/junos/interfaces-ethernet/topics/topic-map/port-speed-mx-routers.html);
[juniper-nsp, "MX204 port 1G", Oct 2020](https://puck.nether.net/pipermail/juniper-nsp/2020-October/038472.html)).
The interface `speed` field carries the 1G operating rate.

**NOC handoffs.** The NOC sits in the metro of `noc_pop_a`. A NOC handoff to a
PoP in that metro is the operator's own 1G access tail (`ILF-NOC-0001`); one to
a PoP in another metro is a leased, port-based **1G Ethernet private line**
from a transport carrier (NOC A from Ridgeline, B from Ironwood), with the route
`distance` recorded — never an operator access circuit stretched across the
region on a 10 km optic. Both handoff /31s sit in Carrier Management.

**Carriers.** Ridgeline Lightwave and Ironwood Fiber sell the inter-metro
transport; Corvane Global IP and Halyard Internet sell transit. None shares a
first word with another carrier or a customer, and each support desk answers
from the carrier's own `.example` domain. Third-party circuit IDs follow each
carrier's order shape (`RLW-WAV-104882`, `IWT/WAV/214682`, private lines `IWT/EPL/365642`, `CVN-IPT-2524750`,
`HAL-DIA-813166`, `BWB-31840274`), seeded by the namespace; the operator's own services use its
initials (`ILF-DF-0001`, `ILF-PL3-00315`, `ILF-NOC-0001`, `ILF-VPN-0001`).
Carrier accounts carry ten-digit numbers; customer accounts read `ILF-C00001`.

**Numbering.** The operator and both upstreams hold distinct RFC 5398
documentation ASNs (64496–64511) under an RIR named `ARIN`, chosen by namespace.
Customer VPN ASNs stay in the private 32-bit `asn_base` block. PE loopbacks
(192.0.2.0/24, from host `.1` — never the network or broadcast address of the
/24), PoP pair links (198.51.100.0/25) and inter-PoP spans (203.0.113.0/24) are
carrier-owned RFC 5737 space under three ARIN aggregates, alongside the IPv6
pool. Each **transit /31 is numbered by its upstream** from that upstream's own
recorded assignment (Corvane 198.51.100.224/28, Halyard 198.51.100.240/28):
the container carries no operator tenancy, sits outside every operator
aggregate, the upstream holds the even address and the operator the odd one.
With IPv6 the transit /127 likewise comes from an upstream documentation /64
outside the operator pool (`3fff:fff:1::/64`, `3fff:fff:2::/64` for a
2001:db8 pool). Management, NOC and customer access links stay in the private
address pool.

**Routing contexts.** The backbone core — PE `lo0.0`, pair and span /31s and the
transit /31s, each on its `.0` unit — is the **global table** (no VRF), as `inet.0` is on Junos.
PoP management switches, console servers, PE `fxp0`, the switch-to-PE uplinks
and the NOC handoffs are the **Carrier Management** VRF (`<ASN>:9000`). Each
customer VRF imports its own target plus the management **hub** target
(`<ASN>:9000`) and exports its own plus the CE management **spoke** target
(`<ASN>:9001`); Carrier Management imports hub and spoke and exports hub only.
That hub-and-spoke extranet is how the NOC reaches CE management addresses
that live in customer VRFs without customers reaching each other. With IPv6,
each routing context owns its own routed /64 (the global table the first,
loopbacks the second, every VRF its own after that), so no /64 is declared once
per VRF. The out-of-band broadband handoffs sit in a separate `Out-of-Band
Broadband` VRF with no route targets and stay IPv4-only.

**Out-of-band.** Each PE's dedicated `fxp0` is cabled to its own PoP
management-switch port and addressed in the Carrier Management /26 (hosts .4
and .5). The console server keeps NET1 on that LAN and takes an **independent
uplink** on its catalog NET2 port: a best-effort business-broadband circuit
(`circuit/oob/<pop>`, Brightwire Business Broadband, no committed rate) whose
far end is the ISP's provider network, addressed from a per-PoP /30 of RFC 6598
shared space (100.64.0.0/24, ledger `provider-oob-links`). Only inventory is
recorded: no VPN, tunnel or console configuration.
Recipe order is onboarding order: customer slots, ASNs, route distinguishers
(`<operator ASN>:<1001+slot>`) and accounts follow it.

**Timeline.** PoPs launch in backbone order from the first PoP; a span enters
service shortly before the later of its two PoPs launches, so every PoP's first
span precedes its customers. Customers onboard months apart in slot order, each
starting with its hub circuit; other premises follow their PoP's readiness.
PoP, premises and NOC sites carry `meta.in_service`, the date shared enrichment
should use for site-level history.

**Premises.** The recipe homes each premises on a PoP (`sites = [{pop=…,
count=…}]`), so placement follows that PoP: a premises takes an authored
neighbourhood or suburb anchor in the PoP's **service area** — within 25 km of
it and nearer it than any other same-metro PoP that existed when the premises
was ordered — and is named after it (`Cedar Regional Bank Oak Creek` is homed
on the Oak Creek PoP). Earlier site-allocation slots mean earlier PoPs, so a
PoP appended later never pulls an existing premises out of its area; among the
eligible anchors a hash of the site id picks, in allocation-slot order, so
growth never moves or renames one. A PoP whose area holds no anchor is refused
with an actionable error.

**Premises equipment.** With `lan_endpoints > 0` a premises is a CE trunking
management and client VLANs to a same-room access switch that serves the office
pod, racked and powered by the carrier. With `lan_endpoints = 0` it is the **CE
only**, the way a carrier actually inventories a managed router at a customer:

- The CE stands **unracked** in the customer's equipment room (site and
  location, no rack): the customer's cabinet, PDU and power are not the
  carrier's inventory, so the premises has no carrier rack, PDU, power panel or
  feed. The CE's supplies are `mark_connected` (customer power) and carry
  `Customer-provided power in the customer's rack`; power checks cover only
  power the estate inventories, and the validation sidecar omits power rules for
  a site group whose every device runs on customer power.
- **Customer LAN space is the customer's.** `port1` is a routed handoff into the
  customer's own LAN, numbered from a per-customer RFC1918 plan
  (`provider.CUSTOMER_LAN_PLANS`: 172.20/16, 192.168/16, 172.24/16, 10.10/16,
  skipping any plan inside the carrier's `address_pool`, chosen by the
  customer's permanent slot). Plans repeat across customers on purpose — several
  customers number from the same /16 and their premises LANs overlap exactly —
  which is safe because each customer VRF enforces uniqueness only inside itself.
  The carrier records only each premises' routed LAN `/24` (`prefix/<sid>/lan`,
  IPAM role `Customer`, ledger `provider-customer-lans/<customer>`) and the CE's
  `.1` on `port1`; no carrier-pool site block, VLAN, DHCP/static range or desk
  wording. Customer LANs are IPv4-only: the carrier assigns them no IPv6.
- **Carrier space** at the premises is only the CE management `/32` loopback
  (host .1 of the site's allocation slot) in the customer VRF, exported to
  Carrier Management through the spoke target, and the PE-CE `/31`.

Growing a CE-only customer to `lan_endpoints > 0` moves CE management and needs a
new baseline.

**Hubs are dual-homed.** Each customer's hub premises takes a second access
circuit (`circuit/customer/<sid>/b`, CID suffix `-2`) from its CE's `wan2` into
the **other PE** of its PoP: the two service positions are reserved back to
back, so they always land on opposite PEs. The second attachment has its own
`/31`, BGP session and `hub` virtual-circuit termination (`PrivateL3-2`), and
counts against the PoP's twelve service positions. It is one CE, so a CE
failure can still isolate the hub; spokes stay single-homed. The declared
spoke-to-hub flow model keeps the primary attachment.

**Contacts.** A customer's NOC and premises facilities desks answer from the
customer's own domain (`noc@cedar-regional-bank.example`). A PoP cage's
facilities desk is the remote-hands desk of the carrier hotel's operator — one
invented colocation company per metro (Windward Interconnect, Motorline Data
Centers, Cuyahoga Colocation, Kinnickinnic Colocation) — not carrier staff.

**Terminations.** A local handoff terminates on its **site** and names the
room its equipment stands in — the PoP cage, the premises or NOC equipment
room — in its description (`Local routed handoff, Cage G09`); a fibre handoff
into a PoP also carries its meet-me-room position in `pp_info` (below). A far
end NetBox cannot see stays on the carrier's provider network. Why site scope:
R4a (0.16 development) terminated these on the room's Location, and a live
visual review on NetBox Cloud (2026-10-02) found Visual Explorer's WAN geo map
drawing "75 sites | 0 circuits": VE resolves a circuit end's site only when
`termination_type` is `dcim.site` — it fetches locations but never maps a
Location termination to its site (a product finding, COVERAGE.md). The WAN map
is the showcase's headline view, so the room moved into the description, which
also stopped the circuit list's Side Z column reading only `MDF`. The loader,
strict readback and shared checks still accept a Location-scoped termination. Every carrier
handoff into a PoP (leased spans, transit, NOC private lines and out-of-band
broadband) records the carrier hotel's cross-connect order (`xconnect_id`,
`XC-` plus seven digits, ledger `provider-cross-connects`), and a fibre handoff
its position on the carrier hotel's meet-me-room panel — the hotel's panel, not
the operator's (`pp_info`, `Meet-me room panel MMR-17, port 4`; ledger
`provider-mmr-positions/<pop site>` from a stable per-PoP start, 48 ports per
panel). The operator models no fibre enclosure: 0.16 builds before this one
racked a 1U ODF per PE cabinet with no ports or cables, a prop. The
operator's own circuits carry neither. Access circuits record `distance`: the
premises-to-PoP great-circle distance times the 1.3 route factor, which the
optic chooser reads directly.

**Customer services.** Each customer's private-L3 virtual circuit terminates
as `hub` at its hub premises and `spoke` elsewhere. A customer's private ASN
carries the customer tenant; the operator's documentation ASN the operator
tenant; the upstreams' none.

### Provider customer lifecycle

A provider recipe can show a carrier mid-motion, honestly. A customer may be
onboarding (`status = "planned"`); a premises entry may be `"planned"`
(provisioning under an active customer) or `"decommissioning"`. Every record
the premises owns — site and rooms, rack, devices, cables, access circuit,
addresses, prefixes, VLAN, the serving PE port's /31 end and the BGP session —
moves together:

| Premises | Site | Devices | Access circuit | Cables | Addresses / prefixes | BGP session |
| --- | --- | --- | --- | --- | --- | --- |
| onboarding customer | planned | planned | planned | planned | reserved | planned |
| planned entry | staging | staged | provisioning | planned | reserved | planned |
| decommissioning entry | decommissioning | decommissioning | deprovisioning | decommissioning | deprecated | offline |

An onboarding customer's virtual circuit is `planned`. A circuit not yet in
service has no `install_date` and no dated order or handoff history; equipment
not yet installed gets no installation journal and stays out of the asset
lifecycle BOMs. Nothing not yet in service looks installed: the serving PE
port is shut (`enabled = false`, on its unit too), and its optic is `planned`
with no serial for an onboarding customer, or `staged` with a recently dated
serial for a provisioning entry; a planned CE and its supplies carry no serial
until the unit ships. A deprovisioning access circuit carries its scheduled
`termination_date` (21–60 days after `as_of`) and a `Disconnect order` journal. None of these paths counts as healthy capacity: only active
premises offer spoke-to-hub traffic, and power validation covers in-service
equipment only. An active customer's hub entry must stay active. Growth may
move a premises forward — planned to provisioning or active, provisioning to
active, active to decommissioning; anything else (including removing a
decommissioned premises) needs a new baseline. Activation dates the circuit and
re-dates that premises' own equipment history and serial date codes; nothing
else moves.

**Optic reach.** Intra-metro dark fiber and access tails are the operator's own
fiber, so each optic is chosen by the run it lights (since 0.16.0): spans up to
10 km keep JNP-QSFP-100G-LR4, longer ones take QSFP-100G-ER4L (30 km without
host FEC); premises tails up to 10 km keep SFP-1GE-LX, longer ones SFP-1GE-LH
(70 km, with the vendor's short-link attenuator noted on the module). See the
[installed optics policy](#installed-optics-policy).

## Provider BGP inventory

The provider backbone — and only the provider backbone — also carries BGP
records for the `netbox_bgp` plugin, since 0.14.0. A provider network with no
BGP is the loudest synthetic tell a network engineer can spot, and both the
plugin's own tables and Visual Explorer's `bgp-topology` view rendered empty
without them.

**They are documentation, not configuration.** Genial generates no device
configuration, applies nothing to any device, establishes no session and claims
no protocol state — no convergence, no route exchange, no policy evaluation.
A session's `status` is the plugin's inventory status for an *intended*
peering, not observed state. That limitation is documented here and in the
report, not on the records: they carry name, description, status and weight
only, and the independent validator refuses any other field (a rule,
community, prefix list or session-state field would read as configuration).

What the estate emits:

| Kind | Count | Content |
| --- | --- | --- |
| Routing policy | 4 | `Transit Import/Export`, `Customer Import/Export`, each weighted and described as reference intent. **No rules**: a named policy is inventory, a rule set would read as configuration. |
| Peer group | 3 | `iBGP Core`, `Transit Upstream`, `Customer Private L3`. Each carries the operator's own ASN as `local_as`; the transit and customer groups bind the matching import/export policies. |
| Session | (1 + 2(2N−2) + T + C) × F | One record per modeled adjacency, for N PoPs, T transit handoffs, C customer access circuits (one per premises plus each hub's second) and F address families (2 with `ipv6_pool`). |

Sessions come in three families, and every field is attributed from the
finished graph rather than authored per site:

- **iBGP** runs over the PEs' global-table `lo0.0` loopbacks, as a **route-reflector
  pair** rather than a full mesh. The reflectors are PE A at the first PoP in
  the permanent `provider-pop-order` ledger and PE A at the first later PoP in
  a different metro, so no single metro holds both; every other PE peers with
  both, and the reflectors peer with each other. That is linear in PoP count,
  so the 64-PoP recipe ceiling stays bounded — a full mesh would be 8,128
  sessions there — and it is how a regional backbone of this size is actually
  built. Exactly one record per adjacency; there is no reversed duplicate.
- **eBGP transit** is attributed from the transit circuit's own termination and
  cable: the PE that really hosts the handoff, its `/31` address, and the
  upstream provider's own ASN. The far end stays `remote_prefix` on that real
  `/31` rather than an invented remote address, because the remote interface
  and its owner belong to the upstream and are not modeled.
- **eBGP customer** is attributed from each private-L3 access circuit: the
  serving PE and its `/31` address as local, the CE's address as remote, the
  customer's own ASN from its site, and the customer tenant.
- **Dual-stack.** With `ipv6_pool`, every session above gains an IPv6 twin
  (`…/ipv6` key, `… IPv6` name) on the same endpoints' IPv6 companions: `/128`
  loopbacks for iBGP, the `/127` link addresses for customers and the `/127`
  prefix as transit's remote prefix.

Growth is stable. Appending a PoP or a customer appends sessions and never
moves an existing one: the reflector pair is chosen by a permanent ordinal, and
every other session is keyed by the circuit or device it documents.

Transport: the three `netbox_bgp` models have no Diode SDK entity — the SDK
carries no plugin entity at all — so they are loader-only like the automation
pack, and they take the bounded REST create path
(see [loading](loading.md#artifacts-and-diode)).

## Provider network lab

`discovery_lab = true` in a provider recipe (off by default; `{ nodes = 4 }`
for four routers) adds a small staging lab that a real orb-agent can discover
(`estates/discovery_lab.py`, since 0.16.0). It is what
[lab/discovery](../lab/discovery/README.md) runs as Nokia SR Linux containers:
`render.py` reads the lab records from the plan, so the plan is the single
source of truth for what discovery must find.

| Record | What it is |
| --- | --- |
| Room | `Network Lab` under the NOC's floor. The routers stand there unracked: a container occupies no rack unit. They stay devices with primary IPs (not VMs) so Orb device discovery matches them. |
| Lab routers | `lab-<production name>` for the first PoP's PE pair (permanent `provider-pop-order` ledger) plus PE A's first-ordered backbone neighbour; a fourth node adds PE B's. Device type `Nokia 7220 IXR-D2L`, platform `NOKIA_SRL v26.7.2`, role `Lab Router`, serial `Sim Serial No.`. |
| Ports | All 58 front-panel ports from the pinned devicetype-library file plus `mgmt0`, `system0` and `.0` subinterfaces, each physical port with its SR Linux MAC. |
| Links | One cable per routed /31 adjacency among the mirrored production routers; 100G production ports map onto the D2L's QSFP28 cages 49–56. |
| Addresses | RFC 2544 benchmarking space only: management `198.18.0.0/24`, links `198.19.0.0/24`, loopbacks `198.19.255.0/24`, no VRF. |

The lab is deliberately **not** the production routers. A container reports a
7220 IXR-D2L, `ethernet-1/N` ports and a simulator serial; matched against the
MX204 records it would produce dozens of deviations that are demo artefacts.
So every lab record states what discovery truly reports — model, serial and
platform strings verbatim (the devicetype-library model string `7220 IXR-D2L
25/100GE` is the labelled deviation) — and each device's comments name the
production router whose wiring it mirrors. Nothing claims physical hardware,
optics, power draw or customer traffic.

`validate_provider.discovery_lab` checks the lab as a closed slice, then
removes it so every production check sees exactly the estate it would see with
the lab off. Membership comes from the lab role, references and addresses, not
from meta, so a production device given the lab role must pass the lab rules
instead of escaping the production ones. Codes: `lab-recipe` (records exist
exactly when the key enables them, with the requested node count),
`lab-isolation` (no production record references a lab record; no VRF,
circuit, power, console or module; lab cables join only lab ports),
`lab-address` (lab addresses only from 198.18.0.0/15, that space only in the
lab, every address in a lab prefix), `lab-hardware` (model, serial, platform,
the complete front panel and MACs), `lab-placement` (the NOC's Network Lab room,
located but never racked) and `lab-mirror` (the first PoP's PE pair and neighbours, cabled
exactly as their routed /31 adjacencies). `tests/test_discovery_lab.py` holds a
failing mutation for each. Turning the lab on or off, or changing its size,
requires a new baseline; with it off no lab record is emitted, and turning it
on only appends records.

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
