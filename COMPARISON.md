# Community demo data comparison

Compared on September 8, 2026 against the community **v4.7 SQL dump**, commit
[`faeae590`](https://github.com/netbox-community/netbox-demo-data/commit/faeae5904a29ce6ad519a3de270ca218a7b050f9),
published September 3. Community counts below come from actual `COPY` rows in
the [pinned SQL](https://github.com/netbox-community/netbox-demo-data/blob/faeae5904a29ce6ad519a3de270ca218a7b050f9/sql/netbox-demo-v4.7.sql).
Generator counts below are the **v0.1 default baseline**, with direct access
channels, preserved for comparison. They are not counts for the v0.2 mixed estate.

## Inventory, with context

| Object | Community v4.7 | v0.1 default bank |
| --- | ---: | ---: |
| Sites | 24 | 10 |
| Racks | 42 | 16 |
| Devices | 72 | 625 |
| Interfaces | 1,586 | 4,433 |
| Cables | 108 | 1,045 |
| Circuits / providers | 29 / 9 | 20 / 2 |
| VMs / clusters | 180 / 32 | 32 / 2 |
| Prefixes / IP addresses | 90 / 180 | 131 / 745 |
| Service objects | 0 | 36 |

480 of our 625 devices are workstations, ATMs, APs, or cameras. The remaining
145 include infrastructure and power equipment. Our cable total includes power
connections. These larger counts do not establish better demo coverage.

## Original v0.1 assessment: where community was ahead

The community fixture has greater curated model breadth: 912 front ports, 630
rear ports, 154 stored cable paths, four modules, 28 module bays, four virtual
chassis, two rack reservations, and 11 tenants. All 72 devices have rack positions.
Of the stored cable paths, 141 are marked complete and 13 incomplete; this quick
comparison did not investigate why those paths are incomplete.

Its conventions show authored structure: names such as `dmi01-akron-rtr01`, a
`10.112.0.0/15` hierarchy with separate /17 headquarters and branch allocations,
and VRF-scoped networks. Multiple organizations and provider arrangements give
the fixed estate more variety than our current repeated bank blocks.

It also has a maintenance advantage: rebuilding demo data is part of NetBox's
[release checklist](https://netboxlabs.com/docs/netbox/development/release-checklist/).
Our local tests and prepared CI workflow do not establish equivalent product
release coverage. The v0.2 default bank subsequently passed local Diode ingestion
and strict NetBox readback on September 9; the [README](README.md#checks-and-present-limits)
records the exact coverage and target versions. That does not establish a release
matrix or equal curated model breadth.

## Where the generator adds something useful

Our rules explicitly size access ports, WAN attachments and compute from bank
demand, reserve capacity, and preserve allocations as sites are added. Services
have named functions, actual VM/host/cluster placement, resource budgets, IPs,
and application ports. The community dump has more VMs, but CPU, memory, disk,
and host-device placement are unpopulated on all 180 VM rows.

These are useful foundations for repeatable capacity and application stories.
In v0.1, lifecycle cohorts were labels. The v0.2 [design and scenario
walkthrough](README.md#procedural-design-without-ai) adds different access hardware,
uplink/supply counts, inherited names and addressing, and independently checked
acquisition/refresh candidates. These address one specific curation gap; they do
not establish broad industry variation, live migration, or equivalent community
model coverage. Neither asset provides a complete planted-defect and
lifecycle-history system in this comparison.

## Fidelity gap and next quality bar

The default generator abstracts passive patching into continuous channels.
Panel mode retains real front/rear paths locally, but SDK1.14/plugin1.17 lack
the mapping fields introduced in NetBox4.5. The [README](README.md#start) records
that concrete limitation. A current-version conversion of the community's mapped
ports through the same Diode path would encounter the same missing capability.

At the original v0.1 comparison, community was the stronger polished-demo reference. Our 128,967-object
scale build establishes capacity to generate and validate a graph; it does not
establish comparable demo quality. Prioritize one small, convincing bank estate:

1. Curate its branch, headquarters and DC designs against community physical detail.
2. Introduce justified variation, such as an acquired branch or a specific refresh,
   that changes equipment and connections rather than only labels.
3. Define a few end-to-end demo questions and known expected answers.
4. Qualify those answers and paths after live Diode reconciliation and replay.

Use that small estate as the quality reference before extending industry coverage.

## September 9 procedural depth iteration

The historical counts above remain unchanged. Version 0.3's panel recipe now
generates 12,528 objects at the default bank demand: 10 sites, 19 racks, 1,142 devices,
2,001 cables, and 32 VMs. The additional devices include explicit room outlets and
cabinet panels; higher counts alone are still not the quality measure.

The changes address specific walkthrough gaps: geographic hierarchy and time
zones, building/floor/room placement, demand-sized office pods, complete unused
panel inventories, three-cable endpoint paths, synthetic power allocations and
service listening-address relationships. Independent mutations now reject
public-room rack placement, outlet bypass, wrong endpoint rooms, floor mismatch,
impossible cable lengths, and invalid power budgets. A 178,772-object panel estate
passed offline generation/validation/SDK checks; live scale is unqualified.

The official Diode mapping gap remains. An opt-in, source-hash-guarded local plugin
bridge uses native NetBox 4.7 serializers; isolated native tests establish complete
paths and mapping-preserving replay. It is not an official compatibility claim.
The full 12,528-object panel estate also passed live Diode reconciliation and
strict REST readback with zero mismatches on that patched target. The browser
confirmed a complete ATM-to-switch trace through its outlet and cabinet panel,
with three cables totaling 39 meters, plus service-to-IP navigation in both
directions. This qualifies those modeled relationships, not overall parity with
the community estate or an official current-version Diode path.
See the [current checks and live evidence](README.md#checks-and-present-limits).
Modules, console wiring, hardware images/weights, richer circuit operations, and
broader topology/service narratives remain worthwhile community reference gaps.

## September 9 compact branches and live growth

Version 0.4 keeps the same default bank demand with 11,967 objects. Small
branches route at the WAN edges and retain two access switches plus an explicit
peer trunk; larger footprints retain distribution pairs. Device names are
site-scoped and cable labels are short, resolving the observed clipping in the
reviewed rack and ATM trace. Namespace isolation remains in site/tenant/VRF
references and DNS. These changes improve the authored bank, not industry breadth.

The 139 automated checks passed. More significantly, live additive growth added
17 branches and triggered an additional WAN pair at each DC. All 21,541 resulting
objects passed strict readback; the original 11,967 IDs and 1,224 mapping tuples
survived. Only 12 existing interfaces gained connection descriptions and VLAN
assignments. This is now measured incremental generation and ingestion, alongside
the earlier offline scale checks. It still uses the disclosed local mapping patch
and does not establish general migration, failover behavior or a release matrix.
The [README evidence](README.md#checks-and-present-limits) records exact receipts.

## September 9 building-driven headquarters

Version 0.5 adds floor-local HQ access, management and power, with fiber links
back to the MDF. One headquarters_staff input drives office pods, occupied floors,
closets, access capacity and WAN planning demand. The default HQ has four equipment
rooms, 12 access switches, five racks and 21 inter-room fiber links. Endpoint
copper paths terminate at the serving floor's equipment room; room labels alone
are insufficient to pass validation.

The report now derives floor coverage and backbone tables from actual connections
and chooses a workstation on the highest HQ floor for the representative path.
This broadens the estate's physical structure and walkthrough beyond a single
endpoint type. It does not yet add a campus across several buildings, new WAN
services, procurement libraries, routing protocols or general lifecycle execution.
The 12,219-object mixed bank passed initial Diode ingestion and identical replay
on the patched local target. Strict readback retained every ID and all 1,245
front/rear mappings, and separate readbacks preserved the four older estates.
The [README](README.md#building-driven-headquarters) describes the rules and
records offline versus live qualification separately.

## September 9 demand-sized WAN procurement

Version 0.6 separates purchased circuit capacity from physical handoff speed.
Site demand, reserve, carrier minimums and retained Birch contracts determine
CIR tiers; DC aggregation grows with estate demand. Installation dates and
fictional procurement reasons are portable circuit fields, and access refresh
preserves the inherited WAN contract. The physical handoff remains 1 Gb/s.

This gives the same connected bank more useful differences to inspect: a small
modern branch can buy less bandwidth than an inherited branch, while HQ and DC
contracts serve larger demands. Actual connected handoffs determine the report's
capacity totals. It does not add Internet service, regional provider interiors,
real carrier offers or historical NetBox audit events. The README records
qualification evidence for the exact artifact and target. The 12,219-object
v0.6 estate passed initial ingestion, identical replay and strict readback on the
patched local stack. All prior five estates retained their IDs and emitted data.
The additional 27-site growth candidate is qualified offline only.

## September 9 connected type coverage

Version 0.7 generates connected examples for all **101 estate candidate kinds**
in the pinned SDK1.14/plugin1.17/NetBox4.7 audit, plus an existing-user binding for
rack reservations. The representative `cedar-complete` panel plan contains
14,725 canonical objects. This replaces the original model-breadth deficit above;
it does not establish that every model is equally detailed or that all native
NetBox types can be loaded through Diode.

New connections include installed PSU modules and their real power ports,
room-local serial access, a physical StackWise pair and master, an explicitly
fictional blade/cooling loop, routed wireless diagnostics, staff SSIDs on tagged
access paths, a real VRRP gateway example, managed virtual circuits, planned
IPsec/VXLAN recovery, contacts/owners/accounts, VM disks, rack reservations,
custom choices and a synthetic journal entry. The generator's bank story is no
longer primarily endpoint counts.

Community remains a useful curation reference for the variety and presentation
of a fixed estate, and its product release checklist remains an advantage. Our
coverage is tied to one audited stack; component templates, ServiceTemplate and
configuration contexts/templates have no top-level message in the pinned SDK.
Computed CablePath/CableTermination records arise from native processing, and
the panel path still requires the disclosed local compatibility bridge.
See [local qualification](lab/README.md#observed-qualification) for live evidence;
SDK acceptance and offline graph checks alone do not establish it.
