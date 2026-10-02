# Hospital and provider: coverage and next additions

The tables below preserve the completed v0.8 review and its exact sample counts.
Current v0.9 adds these shared capabilities, with representative initial/repeat
Diode, strict readback and rendered UI qualification across all five profiles:

- MAC identities, circuit/provider accounts and direct infrastructure support.
- Bounded installation, maintenance, PSU and optic preparation notes tied to
  actual equipment, parts, placement and contacts.
- Optional dual-stack planning alongside existing interfaces and services.
- Applicable school/hospital managed/guest demand, stable AP mounts, actual
  VLAN/trunk/service context and source-backed PSE/supply budgets. Guest demand
  must fit its local IPv4 segment. Wireless is not forced into a pure fabric or
  provider backbone.
- Source-backed SR/SR4 multimode modules on in-room jumpers, LR/LX/LR4 single-mode beyond the room (LH/ER4 Lite where the operator's own fiber runs past 10 km), generic server optics and two captive ends
  per actual Arista AOC assembly, preserving existing interface ownership and
  including installed optical power reserves.

[The lab guide](lab/README.md#current-v09-qualification) records exact live scope;
[GOAL.md](GOAL.md) indexes source, variation/growth/mutation checks, scale and
independent reviews. These additions close the corresponding basic inventory
and scenario gaps below. They do not implement optical loss budgets, breakout,
DWDM, observed RF/associations, measured consumption or executed replacements.
Native module deletion can cascade to its interface. The historical tables are
not current omission claims; their counts remain intact for reproducibility.

Version 0.9 implements the focused [provider span-maintenance story](docs/scenarios.md#provider-span-maintenance).
One used leased Circuit changes from active to offline, with actual customer
premise/hub attribution, alternate PE paths, directed offered load/headroom and
support contacts. The three-PoP sample stays lightly loaded; its lost margin is
protection against another failure during maintenance, not manufactured
congestion or a current modeled outage. Offline checking must establish the
exact finding multiset and inverse restoration. This addresses the scenario
gap below without adding dual-homed customer access or total-backbone traffic.
The final pinned local baseline/repeat, change/repeat and restore/repeat Diode
sequence passed with stable IDs and complete strict readback; see
[the exact execution scope](lab/README.md#provider-status-sequence). Historical
table counts remain unchanged. Other stacks and rewired scenarios are unqualified.
The final review makes two capacity boundaries explicit: carrier-wide failures
are not checked, and per-PoP carrier diversity is not guaranteed; the report now
shows actual span providers. Managed wireless demand sizes APs in aggregate,
without per-SSID address admission, while explicit guest-segment demand is checked
against free IPv4 capacity. External-transit journals retain the unknown remote
interface/owner boundary. Wireless example tables disclose truncation.

Review of generated inventory and its rules, dated September 9, 2026 Pacific.
Suggestions below are a ranked backlog, not implemented capabilities. The final
sample graphs and source fingerprints are recorded under
`build/goal-richness/coverage-final/`; [GOAL.md](GOAL.md) and
[the lab guide](lab/README.md) own live qualification and its limits.

The strongest next step is to deepen a few connected stories. Hospital wireless
already exists. Provider wireless should be an optional managed-premises or
wireless-access composition, not something inserted into every backbone PoP.

## What was actually inspected

`python3 build/goal-richness/coverage-final/probe.py` rereads the final
canonical graphs, checks hashes and kind counts, and writes `evidence.json`.
The original independent review, its source snapshots and eight fetched primary
sources remain under `build/goal-richness/coverage-review/`. These sample recipes
live at `build/goal-richness/final-review/corrections/{hospital,provider}/`.
The final text revision retains every physical field, reference, reservation,
contact assignment and journal; its comparison is `build/goal-richness/final-review/corrections/impact.json`.

| Sample | Scope | Canonical objects | Populated candidate kinds |
| --- | --- | ---: | ---: |
| Hospital final sample | One hospital, two wards, two clinics, service DC; direct access cabling | 4,383 | 53 |
| Provider final sample | Four PoPs, two customers, six premises, NOC; panel access cabling | 4,344 | 61 |

The denominator of 101 is this repository's **pinned SDK/native estate-candidate
audit**, not all NetBox models, a completeness score or a target for every
industry. Different recipes deliberately produce different kinds.

## Included, partial, omitted and unsupported

| Area | Hospital/clinic | Provider backbone | Judgment |
| --- | --- | --- | --- |
| Connected estate | Included: care rooms, local IDFs/access, 212 devices, 451 cables, 10 circuits, 10 service VMs | Included: 8 PEs, 6 CEs, real A/Z access and span terminations, NOC handoffs; 180 devices, 410 cables, 15 circuits, 8 VMs | A useful baseline in both. Richness is the linked paths, not only these counts. |
| WLAN | Partial: 3 site-scoped staff WLANs, 19 APs and 19 serving radios; VLAN separated from AP management; RADIUS listeners | Intentionally omitted: no APs, WLANs or wireless links | Hospital needs deeper client/service/power intent. Provider needs a reason to add it. |
| IP/service intent | Partial: 256 IPv4 addresses, 72 IPv4 prefixes, 12 VRFs; clinical, imaging, staff and management segments | Partial: 170 IPv4 addresses, 79 IPv4 prefixes, 8 VRFs, 5 ASNs, 2 route targets, 2 customer virtual circuits | Neither emits IPv6 or IP ranges. Native first-hop groups are absent. Routing/security execution is outside this generator. |
| Physical detail | Installed PSU modules, serial access, rack/feed/panel paths; medical devices remain generic reference NIC endpoints | Same shared detail plus sourced MX204 chassis/PSUs, finite port allocation and local patch lengths | No installed optic/transceiver selection or link budget. Endpoint wall power and PoE loads are excluded. |
| Passive cabling | Absent in this direct-cabling sample; profile accepts panel mode | 186 front and 186 rear ports in this panel sample | Hospital panel omission is a recipe choice, not a missing generator capability. Native 4.7 mapping still depends on the disclosed local bridge. |
| Contacts | 14 contacts, 70 assignments, including site-scoped biomedical desks on 31 medical/imaging devices | 23 contacts, 63 assignments, customer/operator/carrier/facilities/service desks; 7 provider accounts | Included but bounded: all assignments are primary; no backup/on-call rota or coverage-hours story; 0.16 added fictional 555-01xx phone lines. Hospital lacks provider-account records. |
| Journal/history | 38 entries | 60 entries | Partial: all `info`, on sites/circuits/one VM per workload. Six finite planning-note titles; no device repair, incident, retirement or completed-change narrative. Native creation dates remain ingestion time. |
| Resilience | Paired infrastructure and rack-separated service replicas; endpoints single-homed | Backbone connectivity and scoped span-loss traffic witness; customer spokes single-homed, hubs dual-homed into both PEs of their PoP (one CE); PE fxp0 and console out-of-band management | Physical resilience is modeled. It does not prove clinical/application recovery, customer availability, independent ducts, UPS runtime or routing convergence. |
| Variation | One authored metro; ward/clinic demand changes geometry and inventory, but all wards use one care-unit grammar | Four authored metro choices; one private-L3 topology/service and one small wired-office grammar | Industry labels do not yet compose arbitrary facility types. A provider customer named for healthcare still has office desks, not the hospital care-unit builder. |

Source anchors: `estates/hospital.py:146–233`, `estates/provider.py:49–139,
312–524`, `estates/networking.py:205–255`, `estates/operations_context.py:13–141`,
`estates/model.py:95–96`, and the two profile guides. Exact example keys and
complete counts are in `evidence.json`. The provider's capacity scope is declared
at `provider.py:26–30`: directed spoke-to-hub traffic under normal operation and
one inter-PoP span loss; return, arbitrary peer-to-peer, NOC and transit traffic
are excluded. Router/local-pair loss has a connectivity witness only.

Some empty tables are **intentional scope choices**: liquid cooling, diagnostic
wireless hops, device bays, stacks/virtual chassis, auxiliary VPN demonstrations,
custom links and decorative group types are not part of these profiles' core
stories. Both call the shared DC builder with equipment demonstrations disabled.
Do not copy the bank's fixed illustrative objects into every industry. They can
be revisited when an actual facility or service demand calls for them.

**Unsupported in the pinned loading surface** is a different category.
`catalog/type-coverage.json` and SDK 1.14's Entity definition distinguish native
component/service templates, configuration templates/data files and
image attachments from directly supported estate entities. Four models in that
audit are nevertheless *generated* since phase 11: config contexts, export
templates, the webhook and its event rule (still `native_without_sdk`, now
marked `loader_only`). No Diode request can carry them, so the wire package
omits them and `just load` is the only transport that delivers them. The
provider profile's three `netbox_bgp` kinds joined that set in 0.14.0 for the
same reason — the SDK carries no plugin Entity at all — and are likewise
loader-only; that is a loader delivery, never a promise of core Diode support. `device_config` has an SDK
entry but the audited native/plugin pair does not provide a matching model.
Users are existing-identity bindings, not generated accounts. Cable paths and
termination rows are derived, not missing seed records. Images remain explicitly
deferred. These are pinned-surface conclusions, not claims that NetBox cannot
represent the corresponding concepts. See the [SDK 1.14 schema](https://github.com/netboxlabs/diode-sdk-python/blob/v1.14.0/netboxlabs/diode/sdk/diode/v1/ingester_pb2.pyi)
and the source URLs recorded by the local type audit.

## Where wireless belongs

NetBox's [Wireless LAN](https://netboxlabs.com/docs/netbox/models/wireless/wirelesslan/)
represents a shared SSID/authentication segment with interface associations,
optional VLAN and geographic scope. Its [Wireless Link](https://netboxlabs.com/docs/netbox/models/wireless/wirelesslink/)
connects exactly two wireless interfaces. These serve different stories.

For **hospital and clinics**, deepen the existing WLAN first: explicit installed
mobile workstations or other authored client demand, a separate optional guest
service, planned client address pools, and traceable identity/DNS/service
dependencies. Existing identity VMs already list UDP 1812/1813, but no native
relationship proves a WLAN uses those particular servers or that authentication
works. Do not manufacture dynamic association telemetry or assign every bed a
wireless endpoint. Medical endpoints can remain wired unless the requested
care-unit design actually calls for mobility.

PoE is the conspicuous physical gap: **neither graph sets interface `poe_mode`
or `poe_type`**, and hospital AP power is an assumption. These fields are present
in both the [native interface model](https://netboxlabs.com/docs/netbox/models/dcim/interface/)
and the pinned SDK. A worthwhile extension selects supported PSE/PD hardware,
allocates endpoint draw against per-port and whole-switch budgets, and checks
the surviving power-feed allowance. RF channel choices currently draw from three
stable 5-GHz examples; room-count AP placement is not coverage, interference or
roaming qualification. Keep those claims separate even after adding budgets.

For **provider PoPs**, office/technician WLAN may be useful when there are staff
spaces or a management-access story; it is not required to make the wired core
credible. For **customer premises**, an optional operator-managed WLAN naturally
extends the existing customer ownership, VRF, handoff and support relationships.
For **wireless access/backhaul**, add a separate radio pair/site design only when
the scenario calls for that transport: actual endpoints, local power/data paths,
distance/geometry, source-backed radios and stated capacity/RF assumptions.
Inventing a link between ordinary indoor APs would recreate the table-filling
problem rather than improve this provider example.

## Suggested order of work

These are proposed next investments, not a new completion bar for this goal.

1. **Finish the hospital access story.** Add bounded client/guest demand and PoE
   allocation first, reusing the WLAN, campus and operations builders. A good
   demo question is “Which ward loses wireless service when this closet exhausts
   its power budget, and who owns it?” Acceptance needs an independent failing
   mutation for wrong VLAN, missing source or excess budget, plus stable growth.
   Small adjacent omissions are hospital MAC identities and carrier accounts;
   provider already creates both, so reuse focused helpers instead of importing
   the bank's entire enrichment routine.
2. **Add dual-stack address planning, provider first.** Both profiles reject
   IPv6 recipe pools today, although [native prefixes](https://netboxlabs.com/docs/netbox/models/ipam/prefix/)
   support IPv4 and IPv6. Add an explicit IPv6 allocation policy and persistent
   reservations linked to the same VRFs/interfaces/services; prove containment,
   uniqueness, growth stability and live matching. That enables a concrete
   customer migration story without claiming working routing.
3. **Add one provider service-resilience story.** A dual-access customer or an
   offline span-maintenance candidate should identify actual affected premises,
   alternative physical attachments, purchased headroom and restoration. Extend
   the declared traffic witness only as far as its demand inputs support; do not
   silently reinterpret the existing directed flows as total backbone load.
4. **Deepen replaceable parts and lifecycle together.** Source-backed local
   optics/module selection plus a PSU/optic replacement record would make ports,
   power, contacts and journals tell one story. Prefer modules: NetBox's
   [InventoryItem documentation](https://netboxlabs.com/docs/netbox/models/dcim/inventoryitem/)
   deprecates inventory items from 4.3 and recommends modules/module types.
   Do not introduce deprecated records just to improve the kind count.
   [Native journals](https://netboxlabs.com/docs/netbox/models/extras/journalentry/)
   can hold chronological notes, but generated past/future events must remain
   distinct from actions actually executed by this tool. Preserve immutable
   journal identity and verify replay does not duplicate events.
5. **Then broaden the customer request vocabulary.** Add requested facility
   roles/geographies or a provider Internet/L2VPN service as individually reviewed
   demand policies. A small healthcare premises should be composable from a
   clinic grammar when requested. Do not build every specialist department,
   radio transport, protocol plugin or new service before there is an SE story.

The review also caught repeated fake-data labels in ordinary display text.
The shared generator now uses operational descriptions and `Reference ...`
hardware labels. Catalog provenance and material limits remain explicit;
site display-name cleanup is still deferred. All changes came from regeneration,
with no manual target renaming.

The proposed order prioritizes questions an SE can answer by following real
relationships. It deliberately leaves specialized cooling, every available VPN
model, runtime audit rows and images out of the default expansion backlog.

## Expansion campaign roadmap (adopted 2026-09-19)

The Top-10 industry set below was adopted from pipeline/won-revenue evidence
(HubSpot industry aggregates, Pylon active accounts, public case studies) and is
being executed in least-resistance order. This section is the campaign's durable
state: update the Status column as phases complete, and record decisions here.

Existing profiles: enterprise DC, provider backbone, regional bank, hospital,
K-12 school district, retail chain, university campus, MSP/managed services,
manufacturing/industrial, utility/energy. The Top-10 industry set is now
covered. Deliberate folds, not
profiles: GPU/AI cloud (an enterprise-DC workload/hardware flavor) and
government/defense (enterprise/campus plus `site_names`).

| Phase | Work | Status |
| --- | --- | --- |
| 1 | Close the cold-start loop (two consecutive cleans) | **complete 2026-09-20** — runs #34 and #35 are consecutive zero-S1/S2 cleans on the finished composer-era surface (22 runs this era; every sheet command line literally tested; loop log in `.claude/skills/cold-start-se/SKILL.md`) |
| 2 | Bank kinds (loader-only) | **complete 2026-09-19** — all 37 remaining kinds + 12 refs landed in one wave; full 98-kind bank loaded/verified/repeated live (11,801 objects, 0 mismatches). No hard residual. |
| 3 | Retail chain profile | **complete 2026-09-19** — 55-kind profile merged (789 tests); first live load on the pinned 4.7.1 stack: 13,005/13,005 objects, 0 mismatches, 1,265/1,265 cables, verify + idempotent repeat clean |
| 4 | University campus profile | **complete 2026-09-19** — merged (832 tests); first live load: 21,970/21,970 objects, 0 mismatches, 2,380/2,380 cables, 26,730/26,730 ChangeDiffs, verify + repeat clean (largest single estate loaded to date) |
| 5 | Vendor lever | **complete 2026-09-19** — `[hardware]` selects access/leaf/ap lines (Juniper EX3400/QFX5120, Aruba AP-505); 56-cell fit matrix + adversarial review hardening (854 tests); all-Juniper bank live: 10,587/10,587 objects, 0 mismatches, verify + idempotent repeat clean |
| 6 | MSP profile (one NOC operating N customer tenants) | **complete 2026-09-19** — merged and review-hardened (912 tests); 56 kinds with independent tenancy-isolation and ownership-versus-operation checks; four-customer estate live on the pinned 4.7.1 stack: 7,068/7,068 objects, 0 mismatches, verify + idempotent repeat clean. |
| 7 | Bank hard-kinds decision point | **dissolved** — phase 2 left no residual; the extras trio went via REST-create and the exempt-count gate |
| 8 | Manufacturing profile (IT/OT zones; pays for the OT concept) | **complete 2026-09-19** — merged and review-hardened (994 tests; panels collision and the console-server third crossing fixed and declared); three-plant estate live: 9,352/9,352 objects, 0 mismatches, verify + idempotent repeat clean |
| 9 | Utility profile (control centers + substations; reuses phase 8 OT semantics) | **complete 2026-09-19** — merged and review-hardened (1,096 tests; console crossing declared + pinned, mgmt_only gating, two shared engine gaps closed); two-center/six-substation estate live: 9,364/9,364 objects, 0 mismatches, verify + idempotent repeat clean |
| 10 | Assurance drift twin: baseline + believably-drifted observed Diode payload with an expected-deviation manifest; live-prove via Diode ingestion on the Assurance-equipped Cloud instance (4.6-compatible subset) | **merged offline 2026-09-19** — `just drift` / `just drift-check` over any healthy frozen plan (a subcommand, not a `demo` recipe key); 14 property-selected items across all four advertised detections, 15 observed records projected out of the plan, restricted to the reviewed NetBox 4.6 kind set, 67 new tests, CI-qualified on all eight access-bearing profiles. **Live gate still open**: no Assurance-equipped target has ever seen this payload. Two corrections from the source review — Diode has no delete/tombstone change type, so documented-but-missing emits nothing and is declared `requires-target-side-comparison`; and there is no fixed four-name product deviation taxonomy, so the manifest predicts wire-level create/update and uses its own class labels. |
| 11 | Automation demo pack: config contexts, export templates and event-rule/webhook inventory as new loader kinds, so the Ansible/ServiceNow talk track has real objects | **complete 2026-09-19** — merged and review-hardened (1,189 tests, generator 0.11.0 baseline); full lifecycle live incl. exact-name retirement: 3,881/3,881 objects, 0 mismatches, verify + repeat + retire clean |
| 12 | Demo composer: one command assembling profile × vendor × feature packs × customer names into a loaded, verified branch plus a DEMO.md talk track with deep links | **complete 2026-09-20** — `just demo` / `python3 -m estates demo` over an authored demand template per profile (2,967–9,971 objects; sizes in [docs/demo.md](docs/demo.md#template-sizes)), the `juniper`/`aruba` `[hardware]` shorthands, name-derived namespace and namespace-derived seed, `--sites` overrides, and the `assurance`/`automation`/`scenario` feature packs. Pure orchestration: it calls the same entry points the Justfile recipes run and skips no gate. DEMO.md is assembled from the finished graph — real site, rack, device, contact and service identities, branch-activated deep links, the MSP tenancy curls bound to this namespace, the zone routing contexts for manufacturing/utility — with bound artifacts (`drift.md`) linked, never restated. 28 new tests including a byte-identical recompose, a per-profile compose sweep and a mocked branch/load/verify argv check. Adversarial review closed fifteen findings, the sharpest being a walkthrough that named the shared NOC desk instead of the device's actual per-account desk (inverting the MSP story), a `--name` newline that could inject recipe keys, and `ui_url`/readback fallbacks that would have rendered "verified None objects" or a main-scoped link under a branch-activation claim. **Live gate closed 2026-09-20**: `just demo regional-bank "First Cedar Bank" juniper assurance,automation <target> <branch>` composed, branched, loaded and strictly verified 8,474 objects with 0 mismatches in 227 s on the pinned 4.7.1 stack, drift twin and DEMO.md included (receipt `estate-First-Cedar-Bank-Demo-d76f317008ff.json`). Merged onto the 0.11.0 automation baseline: the automation section narrates the real config contexts, export templates and inert webhook/event rule the estate ships. |

Demand evidence (2026-09-19 review of live sales calls, community feature
requests, plugin adoption and the official demo dataset): the official demo
data ships 72 devices with zero rows for wireless, VPN, journals, FHRP,
services, config contexts and change history — exactly the differentiators our
estates already fill; demos are path-traversal heavy (trace, rack, power chain,
topology) which rewards our cable density; Assurance/Analytics demo poorly on
flawless data, which phase 10 addresses deliberately; NetBox Labs segments by
topology (AI-DC, campus, branch, ISP, OT, hybrid cloud), not industry — an
AI/GPU-datacenter profile is the one hard-sold topology we lack (candidate
phase after 12; currently a declared fold). Cheap wins to fold into scheduled
phases: pinned real end-of-life dates on catalog device types (4.7 core field;
feeds lifecycle/Analytics stories — next catalog rebaseline wave), a free-tier
sized recipe per profile (NetBox Cloud free tier caps at 100 devices/500 IPs),
and indexing README profiles by topology as well as industry. NetBox Labs'
own SE org ships an internal "Demo Data Loader" (July 2026 community meetup,
C. Beye) with branch-scoped industry demo data and air-gapped bundles —
overlapping framing; the no-other-teams gate stays closed.

**Retired 2026-09-29 — "region display names leak the namespace."** The leak was
never confined to regions: tenants, providers, device and rack roles, circuit
types, cluster types, RIRs, IPAM roles, tags, VLAN/wireless groups and power
panels all rendered as `<namespace> <thing>`. Visual Explorer truncates graph
node labels, so four distinct power panels all drew as `aurora-peak-pop-chica…`
— the leak was a functional defect in the rendered estate, not cosmetics.
Fixed in 0.12.0 (a new baseline): `estates/naming.py` holds the policy, display
names are authored and namespace-free, identities keep the namespace, and the
two documented exceptions (main-scoped retirement-matched records, and the root
contact group whose slug is derived from its name) keep the prefix. Pinned by
`tests/test_naming_policy.py`.

Depth backlog (from cold-start run #16, bank): FHRP groups, VPN tunnels and
inventory items are modeled as single instances in a DC pair — enough to show
the object type, not a per-branch redundancy *pattern*. Candidate enrichment:
per-branch FHRP gateway pairs, per-acquired-site tunnels, per-host inventory.
From the phase-5 adversarial review: WLANs attach to one radio slot each, so a
dual-band AP line puts the `wlan1` cohort on 2.4 GHz only — dual-band WLAN
membership (one SSID on both radios) is the honest upgrade; and no check mates
PDU outlet connectors to chassis inlets (the default bank cables C13 outlets to
the C9200L's C16 inlets, a pre-existing physical impossibility the Juniper line
does not share). Not scheduled; weigh against phases 3–9.

Loader backlog (from the 2026-09-22 Cloud scale work): a lighter spot-readback
verify lane for large demo loads — the full strict readback over 85k+ objects
costs 5–10 minutes per run and each resume re-verifies resolved IDs for ~20
more; on an already-qualified path a demo tenant needs the load's own per-job
verification plus a bounded sample, not the full sweep. Also: a
skip-search-reindex option for >100K-row loads (the public TurboBulk guide
recommends it; deprioritized since the reindex passes on a properly sized
tenant), and a purpose-built TurboBulk-only large artifact to test whether
changelog-free branches at scale are API-deletable (self-service cleanup).

## Visual review, 2026-09-29 (first run)

The first [visual review](.claude/skills/visual-review/SKILL.md) — the
17,389-object `Aurora Peak Networks` provider estate seeded onto a NetBox Cloud
tenant's main and inspected through Visual Explorer's nine views and the NetBox
UI. Every offline gate had passed; none of them could see any of this. Ranked
by what it costs a demo.

**1. Breaks a view or a core NetBox question.**

- *Circuits have no termination pointers.* `CircuitTermination.save()` caches
  scope fields on the termination and back-fills `Circuit.termination_a/_z`;
  TurboBulk runs without save hooks and its `fix_denormalized` map covers only
  device components. Result: every circuit's SIDE A/SIDE Z reads "—" in NetBox,
  `/api/circuits/circuits/?site_id=<id>` returns **0** for a site that
  terminates many, and Visual Explorer's WAN map draws **0 circuits** over 61
  correctly-placed sites. The general lesson is broader than circuits: wherever
  a NetBox model maintains a cache inside `save()`, the bulk path must compile
  it in or complete it afterwards. Cables were checked and are fine.
- *Rooms render as corridors.* Rack `position_m` coordinates were authored for
  validation with a 30 m compute/network zone offset. Rendered as an actual
  floorplan the NOC data hall is **7.8 m × 37 m** with two racks at each end and
  thirty metres of empty floor between — correct by every offline check, absurd
  on screen.

**2. Reads as synthetic.**

- *Namespace prefixes in display names.* Sites got authored names in 0.10.0;
  regions, site groups, tenants, providers, device and rack roles, circuit
  types, owners, contact groups and tags did not. In graph views labels
  truncate, so four distinct power panels all render as
  `aurora-peak-pop-chica…` — indistinguishable. This is a readability defect,
  not only a cosmetic one. (Supersedes the earlier "region display names leak
  namespace" note above.) *Closed across 0.12.0 → 0.15.0; 0.15.0 finished the
  last families and replaced the opt-in check with an exhaustive sweep, so this
  class of defect is now a test failure rather than a rendering discovery.*
- *Racks are ~93% empty.* 70 racks averaging 4.9 devices in 42U; the racks list
  SPACE column reads 7.1%, 2.4%, 9.5%, 16.7% down the page. 0U PDUs carry no
  rack position and render as "Non-racked" beside the elevation.
- *Device names are raw slugs* in every rendered label
  (`popchicagoloop-console01`), and circuit IDs read
  `aurora-peak-customer-ce-harbor-energy-chicago-loop-001`. *0.16 renamed the
  provider's PoP and customer-premises hostnames (`chicago-loop-console01`,
  `harbor-energy-chi0300-gw01`); circuit IDs are matching identities and keep
  the namespace.*

**3. Leaves a feature dark** (a null field that disables a control):

- Measured across 300 devices: `platform` **0/300** (one platform record exists,
  `aurora-peak Service Linux`, assigned to nothing — real estates carry Junos /
  IOS-XE / EOS / FortiOS per vendor and NetBox uses it for config rendering),
  `asset_tag` 0/300, `oob_ip` 0/300, `airflow` 0/300, `config_template` 0/300.
  *Addressed in catalog 0.12:* network models now reference their vendor
  platform (Cisco IOS XE / AP-COS, Arista EOS, Juniper Junos, Fortinet
  FortiOS, ArubaOS) and serials follow vendor-shaped fictional formats; see
  [catalog platforms](catalog/README.md#platforms).
  Healthy by contrast: `serial` 300/300, `description` 300/300, `primary_ip4`
  255/300, plus 340 modules, 18 VRFs and 12 services.
- Only **114 of 300 devices sit in a rack and 69 carry a U position** — the rest
  float. Much of that is legitimate (endpoints, wall outlets), but it is also
  why elevations look thin and PDUs render as "Non-racked".
- Prefix `role` is null on all 299 prefixes — the IPAM views have no semantic
  grouping to colour by. `ip-ranges` and `inventory-items` are both 0.
- Device-type `airflow` is null on all 11 types — the rack elevation's Airflow
  toggle does nothing.
- Rack `type` and `facility_id` are null on all 70 racks.
- Device types carry no front/rear images, so elevations are role-coloured
  rectangles rather than recognisable hardware (NDX is the intended source).
- No BGP sessions, so the BGP topology view is empty by construction; the
  plugin is installed and the estate models ASNs, so this is reachable scope
  rather than a limitation — it would need a new canonical kind and a
  rebaseline. (Fixed in 0.14.0 for the provider backbone only; see item 5
  below. Other profiles still render that view empty, by design.)
- Power chain reports **0 W allocated against 11.8 kW** (fixed in 0.13.0; see
  the 2026-09-30 review below). Traced: the feed
  connects to the PDU's own `Input` power port, and that port carries
  `maximum_draw: null` / `allocated_draw: null`, so any consumer reading the
  feed's connected endpoint sees nothing. Device-side ports are complete
  (1,680 outlets, cabled, traced, `allocated_draw` set), and the feed's 2,944 W
  capacity computes correctly from amperage/voltage/max-utilization — only the
  PDU input is unmodelled. A real PDU input has a rated draw; setting it is
  correct modelling, not decoration. Related: each 12-outlet PDU has only 3
  outlets cabled, which is the density finding again.
- VLANs render disconnected in the L2/L3 view at NOC-datahall scope. Membership
  does exist (69 of 200 sampled interfaces carry an untagged VLAN, 5 carry
  tagged VLANs) but those interfaces are customer-site access switches, while
  the NOC data hall's devices interconnect over routed links. The view is
  probably telling the truth; confirm before treating it as a defect.

**Product defects found, not ours** (route via the SE vault's
`capture-product-feedback`): Visual Explorer's Settings **Save silently
persists an empty instance** when the pre-selected dropdown default is never
changed — the store starts at `selectedNetboxId: ""` while the native `<select>`
displays option[0], so the token saves and the instance does not, with no
error; the documented "switch instances" workaround (ENGHLP-1677) is the
symptom of exactly this. Its API token is also stored in plaintext
`localStorage` in two places. Separately: the IPAM views give no VRF context,
so our eighteen per-VRF `10.0.0.0/8` containers — correct modelling, one per
routing table, each with a distinguishing description — render as eighteen
identical "10.0.0.0/8" entries in the prefix picker and as uniformly-coloured
tiles in the treemap despite varied statuses (216 active / 83 container); and a
floorplan scoped to a parent location renders an empty canvas with no hint that
the leaf carries the plan. Worth asking on our side whether eighteen /8
containers earn their place in a demo even though they are defensible.

**Demo hygiene:** the org's Assurance queue holds 51 stale OPEN deviations from
retired `devin-generator` probes; they are noise in front of a customer.

### Visual review, 2026-09-30 (provider estate, `crsk8600` main, CDC enabled)

Ranked by what each costs a demo. Evidence is the view's own status bar plus a
contradicting or confirming API count, per the `visual-review` skill.

Items 1 and 2 are **fixed in 0.13.0** (one new baseline, both change the
canonical graph): PDU input ports now carry the totalled draw of the inlets
cabled to their outlets, computed after PoE/optics enrichment; power feeds now
emit an authored `Network 01 A`-style name scoped by their panel. Pinned by
`tests/test_depth.py::test_pdu_input_must_carry_the_load_cabled_to_its_outlets`
and `tests/test_naming_policy.py::test_power_feed_names_are_readable_and_unique_within_their_panel`.
Items 3 and 4 remain open.

**1. PDU input power ports carry no draw, so the power chain reads 0 W.**
The power-chain view renders the A/B tree correctly (`2 panels | 4 feeds |
4 PDUs`) and then reports `Power: 0 W / 11.8 kW | Util: 0.0%`. Device power
ports are fine - `maximum_draw: 120, allocated_draw: 60` on real devices. The
break is the PDU's own `Input` port: it is *connected* to its feed
(`connected_endpoints` resolves) but has `maximum_draw: None` and
`allocated_draw: None`, so nothing propagates up and every feed computes zero.
Utilisation is the entire point of that view. Fix is to populate the PDU input
port's draw from the outlets it serves. Class: a null field that leaves a
feature dark.

**2. Power feed names still carry the namespace.** Feeds are authored
`lakes-fiber-dc-01-compute-01-a`; in the power-chain graph all four truncate to
`lakes-fiber-pop-chica…` and are indistinguishable. This is precisely the
defect 0.12.0 fixed for other families - power *panels* are correctly `Supply A`
/ `Supply B` - and `power_feed` is not in `NAMESPACED_KINDS`, so it should have
been authored namespace-free too. The family was missed. Changing it alters
display names across every estate, so it needs an `estates/__version__` bump
and a new baseline.

**3. Rack density: withdrawn after a survey.** The first observation was
`24U Rack | 21% utilized | 5 devices | 19U available` at a PoP, which read as a
lab. Surveying all thirteen racks shows that was the smallest rack in the
estate: NOC racks carry 6, 6, 7 and 9 devices, PoP racks 3 to 5, customer sites
5. Cabinets are sized to a ten-device lane, so 9-in-24U is essentially full, and
a small provider exchange genuinely holds three racked devices plus two 0U PDUs.
This is correct modelling, not sparseness, and padding it would be the wrong
fix. Recorded as withdrawn rather than deleted so the reasoning survives.
(Caveat on method: the per-rack utilisation percentage computed during the
survey was wrong - the device list serializer does not return `u_height`, so it
read 0% everywhere. The device counts are the load-bearing evidence.)

**4. Circuit IDs render as long slugs.** The circuits list shows `cid` as its
primary column, and ours read
`lakes-fiber-customer-ce-harbor-logistics-chicago-west-001`, wrapping to two
lines. Circuits have no separate display name, so the identity-keeps-the-
namespace rule lands badly here; a carrier reference looks like
`ATLS-10G-CHI-001`. Rebaseline-frozen (the provider uses `cid` in Diode
matching identities), so this is a decision to surface, not to take.

What is demonstrably right, worth keeping: authored site names with facility
codes, regions, tenants and descriptions all populated; metro-accurate
coordinates; site popups carrying real street addresses; **Side A and Side Z
populated on every circuit** (tonight's save-hook fix, visible in the UI);
device types reading as real hardware (`MX204`, `Catalyst 9200L-24P-4X`);
interface names in real vendor form (`xe-0/1/6`, `TenGigabitEthernet1/1/1`).

**5. BGP is not modelled at all, and it leaves a whole plugin and a whole
Visual Explorer view dark.** `netbox_bgp` 0.20.1 is installed on the tenant and
its API answers (`/api/plugins/bgp/session/`, `routing-policy/`,
`bgppeergroup/` all return HTTP 200) with `count=0` for every one. The string
"bgp" appears nowhere in the generator except an acronym entry in
`estates/naming.py`. Interface evidence: the `bgp-topology` view renders
"No BGP Sessions" with `0 sessions | 0 active | 0 iBGP | 0 eBGP | 0 ASNs`,
and every site detail page carries an empty "BGP Sessions" tab.

This is the largest believability gap found. The view is fully featured -
iBGP/eBGP filtering, peer groups, colour by type, AS-number labels, force
layout - and a provider backbone is precisely the estate a customer expects to
populate it: we already emit ASNs, PE routers, loopbacks, transit circuits to
two upstreams and private-L3 customer services, which is the exact scaffolding
BGP sessions attach to. A provider network with zero BGP sessions is the single
most obvious "this is synthetic" tell to a network engineer.

Feasibility notes gathered while reviewing: TurboBulk's `_resolve_model`
(`jobs/base.py`) has no model allowlist, so plugin models are loadable through
the existing bulk path; the Diode SDK has no entity for them, so they would be
`LOADER_ONLY_KINDS` like the automation pack, omitted from the wire package and
delivered only by `just load`. Scope would be new canonical kinds, loader
support, independent validators, docs and a new baseline - a reviewed addition,
not a quick fix, and per this file's own standing rule it is backlog until
explicitly authorised.

> **IMPLEMENTED in 0.14.0** (explicitly authorised). `estates/bgp.py` gives the
> provider backbone — and only the provider backbone — four named routing
> policies, three peer groups and one session per modeled adjacency: an iBGP
> route-reflector pair at the first PoP in the permanent `provider-pop-order`
> ledger with every other PE as a client, one eBGP session per transit handoff
> attributed from that circuit's own termination, and one per private-L3
> customer access circuit. The default profile emits 4 + 3 + 14 records.
>
> Two corrections to the feasibility note above. The models load over the
> **REST create path**, not a TurboBulk job: peer groups and sessions carry
> many-to-many routing-policy lists the raw bulk path cannot express, and
> `/api/plugins/turbobulk/models/` on a pinned 4.7.1 tenant listed 172 writable
> models with no `netbox_bgp` among them, so a bulk job would fail preflight on
> a target whose REST API is writable. And the target must have the plugin
> installed; without it the REST schema preflight refuses before any write.
>
> Scope limit, restated because it is load-bearing: these are documentation
> records in exactly the sense the inert webhook is. Nothing is configured,
> applied or established; no convergence, session state, route exchange or
> policy evaluation is claimed; the named policies carry no rules, and the
> reviewed kind set is closed to those three. The independent checks live in
> `estates/validate_provider.py` (`provider-bgp-inventory`, `-policy`,
> `-group`, `-session`, `-scope-text`) and are pinned by
> `tests/test_provider_bgp.py` — in particular
> `ProviderBgpValidationTests.test_removing_the_bgp_inventory_is_rejected`
> (the failing-mutation check for a revert),
> `test_a_misattributed_peering_is_rejected`,
> `test_dropping_the_inventory_only_note_is_rejected`,
> `ProviderBgpGrowthTests.test_appending_a_pop_and_a_customer_never_moves_an_existing_session`
> and `ProviderBgpScopeTests.test_every_record_states_that_nothing_is_configured_or_established`.
>
> **Still owed: live evidence.** Offline generation, validation and
> `just load-check` pass. No load receipt yet covers these three plugin models
> against a `netbox_bgp`-equipped target, and no rendered `bgp-topology`
> screenshot has been taken. Until both exist, this entry claims implementation
> only, not target behaviour.

### Second visual pass, 2026-09-30 (v0.14.0 reseed, `crsk8600` main)

Verified through the interface after reseeding at v0.14.0 (3,064/3,064 objects,
0 mismatches, plus 7 floorplans and 13 rack shapes):

- **Power chain fixed, proven in the view.** Status bar went from
  `Power: 0 W / 11.8 kW | Util: 0.0%` to **`Power: 828 W / 11.8 kW | Util: 7.0%`**,
  feeds report `249 W / 3.7 kW` and PDUs `249 W / 496 W` with live utilisation
  bars. This is the claim the offline work could not settle: NetBox does consume
  a populated PDU input port.
- **Feed names fixed, proven in the view.** Four identical
  `lakes-fiber-pop-chica…` labels are now `Network 01 A`, `Network 02 A`,
  `Network 01 B`, `Network 02 B`.
- **BGP renders.** `bgp-topology` scoped to a PoP shows
  `3 sessions | 3 active | 1 iBGP | 2 eBGP | 3 ASNs`, iBGP purple, eBGP blue,
  external ASes as dashed nodes with health status.
- **Floorplans render.** `MDF | 2 racks | 48 RU | 8% avg util`.

Two new findings, both surfaced only by rendering:

**6. ASN labels carry the namespace (ours). — FIXED in 0.15.0.** NetBox's ASN
model has no `name` field, so Visual Explorer labels external AS nodes from
`description`, and ours read `lakes-fiber transit-a routing identity`. Same
truncation-and-prefix defect as power feeds, on a third family. Worse, the label
did not match the provider name shown everywhere else in the estate: the node
for Atlas Upstream's AS said "transit-a".

Fixed in 0.15.0 (a new baseline): ASN and Aggregate descriptions are authored,
namespace-free and name the party that actually holds the record — that AS node
now reads `Atlas Upstream routing identity`, and the bank's carrier ASes read
`Northstar Transit routing domain` / `Meridian Carrier routing domain`. Pinned
by `tests/test_naming_policy.py`:
`GeneratedEstateNames.test_asn_descriptions_name_the_party_that_holds_the_as`
(the description must start with the linked provider's own display name),
`GeneratedEstateNames.test_label_bearing_descriptions_are_namespace_free` and
`NameUniquenessAcrossProfiles.test_label_bearing_descriptions_do_not_move_when_the_namespace_does`
(every profile, proven by regenerating under a second namespace).

The same release finished the whole 0.12 sweep — VRFs, FHRP groups, the
recovery crypto/tunnel/L2VPN records, config contexts, contacts, provider
accounts, clusters, virtual chassis, VLANs and VLAN groups were all still
namespaced — and inverted the guard so the next missed family fails a test
instead of waiting for a rendering session. See CLAUDE.md's display-naming
entry. Rendering evidence for the fixed labels has not been recaptured; the
claim above is an offline check, not a fresh screenshot.

**7. iBGP remote endpoints render as a bare database ID (product, not ours).**
One node in the topology renders as `1177`, which is the NetBox id of IP address
`10.255.128.1/32` - pe-b's loopback. `BGPSession` carries a single `device`
field for the local end only, so the viewer resolves the remote end from
`remote_address` and, failing to find a device, falls back to the IP object's
**id** rather than its address or its `dns_name` (which we do populate). Our
data is correct and complete; nothing on our side can improve that label.

**Product-side observations (not ours, not filed).** The WAN geo map never fits
the viewport to the data: it opens at full globe and stays there whether scoped
by URL or by clicking a marker, so a regional estate is one indistinct dot with
overlapping labels until a human zooms. And the cable-topology view renders
interface-type ports only, so PDUs appear as empty boxes with no ports while the
legend still advertises a red "Power" cable type; the estate in fact has 108
power cables and 108 connected power ports. That second one nearly became a
false bug report against our own generator - see the skill's false-absence rule.

**Not a defect, an action item:** this estate has no floorplan records, so the
floorplan view is empty until `just geometry` + `just seed-geometry` run
against it.

**Blocking platform incompatibility found while reseeding (2026-09-30):**
TurboBulk main-scoped loading stopped working on the visualization tenant. Every
job that creates its staging table in schema `public` fails with

```
cannot add relation "_turbobulk_staging_circuits_circuittype_..." to publication
DETAIL:  This operation is not supported for unlogged tables.
CONTEXT:  ALTER PUBLICATION dbz_pub_nb_46fca0360cbb ADD TABLE ...
```

**Root cause, confirmed by reading the platform source** (clone of
`netboxlabs/platform-monorepo`, `services/controlplane/core-lambda`):

CDC provisioning installs a Postgres event trigger,
`cdc_set_replica_identity_on_create`, on `ddl_command_end` for `CREATE TABLE`
(`libs/cdc/database.go:436-441`). Its function `public.set_replica_identity_full()`
(`database.go:378-425`) walks every newly created `public` table, skips names
matching a baked-in exclude list, sets `REPLICA IDENTITY FULL`, and then adds
the table to every `dbz_pub_%` publication (`database.go:417`). **Its only
exception handler is `duplicate_object`** (`database.go:419`, SQLSTATE 42710).
Postgres raises **`22023 invalid_parameter_value`** for an unlogged relation
("cannot add relation ... to publication ... This operation is not supported for
unlogged tables", `pg_publication.c`), which that handler cannot catch, so it
propagates out of the trigger and aborts the transaction that created the table.
Reproduced independently on throwaway PostgreSQL 16.15 and 18.6 by rendering the
trigger through the real Go `Sprintf` from `database.go` rather than by
transcription, and the non-catching was proven by raising a synthetic 22023
under the shipped handler. The abort is fatal rather than noisy: everything else
in the caller's transaction rolls back with it.

TurboBulk creates `public._turbobulk_staging_<model>_<hash>` as
`CREATE UNLOGGED TABLE` (`netbox-turbobulk/engine/staging.py`, hard-coded), and
`_turbobulk_staging_%` is not among the fourteen patterns in
`DefaultCDCTableExcludeList` (`libs/configuration/configuration.go:522`) -
confirmed by running all fourteen through the real `debeziumPatternToSQL`
transform against an actual staging-table name, zero matches. So on
any instance with CDC provisioned, TurboBulk's first staging-table creation
aborts and the job dies. Observed exactly that, reproducibly, first job every
time; the same artifact loads normally on a sibling tenant.

**Correcting an earlier inference in this file:** enabling Analytics does not
provision CDC. `libs/analytics/enable.go:184-194` shows Analytics is *gated on*
CDC already existing and in the right mode (it refuses with `StatusBlockedOnCDC`
otherwise). So "Analytics breaks TurboBulk" is wrong. **CDC breaks TurboBulk**;
Analytics merely requires CDC. What enabled CDC on this instance, and when, is
still unknown from our side and answerable only from the platform.

Three qualifications from the reproduction, all narrowing or widening the claim:
the trigger only acts on schema `public`, so branch-schema creates are untouched
(and CDC correspondingly never captures branch data); `CREATE TABLE AS` and
`SELECT INTO` carry different command tags and bypass it entirely; and creating
the table logged then `SET UNLOGGED` is closed off, failing with 55000 precisely
because the trigger already published it.

**Blast radius, now measured rather than inferred (2026-09-30):** a full
`just load ARTIFACT TARGET BRANCH` against a ready branch on the affected tenant
ran 39 jobs with **no publication error anywhere**, including the exact
`phase-1:circuit_type` job that fails instantly against main. Branch-schema
staging confirms the `public`-only reading of the trigger. So the outage is
**`main-seed` only**: the documented reviewable path, the branch gallery, exports
are unaffected, because `create_staging_table` is called only from
`jobs/load.py`. **Maintenance finalizers are not exempt**, though: staging-table
creation there is unconditional, so a zero-row job that only runs a post-hook
fails identically - observed on `crsk8600` with a zero-row `dcim.site`
`rebuild_search_index` job. That is also the cleanest reproduction of the whole
defect, since it carries no rows, changelogs or save hooks at all. An earlier
claim here and in NBC-7787 that maintenance jobs were unaffected was wrong and
has been corrected in both. An earlier claim in this file that TurboBulk
"stopped working entirely" was wrong and is corrected above.

**A second, unrelated defect was hiding inside that run, and it was ours.** The
one job that did fail on the branch failed differently: after inserting its 6
rows and applying its 6 save hooks it errored with `relation
"postchange__turbobulk_staging_circuits_circuittermination_<id>" already exists`
and wrote 0 changelogs. That reproduces identically on the healthy sibling tenant
and on the pinned local 4.7.1 stack, so it is not a Cloud or CDC symptom.
TurboBulk 0.4.0 through 0.4.2 cannot combine `apply_save_hooks` with
`create_changelogs` on an insert whose model table name is 24+ characters:
`refresh_postchange_table` (`engine/postmerge.py`) issues `CREATE TEMP TABLE
"<postchange>_refresh"` before its own drop and rename, PostgreSQL truncates
identifiers at 63 bytes, and the postchange name is `39 + len(db_table)` bytes,
so the `_refresh` name truncates onto the postchange name itself and that
`CREATE` fails. The drop and rename never run. 28 of NetBox 4.7's ~147 models
cross the threshold; upsert never builds a postchange table and is unaffected.
**The whole transaction rolls back, so no rows survive** - verified live with a
`virtualization.clustertype` insert that reported `rows_inserted: 1` and left
zero rows. The receipt's row and save-hook counters are in-memory values written
after the rollback, and an earlier claim in this file that the rows "landed" was
wrong.
The v0.12.0 circuit-termination fix requested both, which broke the primary
reviewable load path on every target. Fixed by requesting save hooks only for
changelog-free jobs (`_batch_request_settings`), pinned by test, and verified by
a clean local branch load (5247/5247 create ChangeDiffs, 348/348 cables, 0
mismatches). Consequence to state plainly at demo time: **reviewable loads leave
`Circuit.termination_a/_z` unset**, so circuits read as unterminated there and
`?site_id=` returns nothing; `main-seed` and `disposable-baseline` loads are
unaffected and do terminate correctly. The readback records the skip and its
cause rather than asserting caches the load never asked NetBox to build.

**Fix, smallest correct form:** have the trigger skip non-permanent relations
(`relpersistence <> 'p'`), which is semantically right because an unlogged table
can never be replicated - verified to work while leaving normal tables published.
Broadening the exception handler also works, but it should be `WHEN OTHERS`
rather than a 22023 special case: a `FOR ALL TABLES` publication raises 55000
instead and would abort *every* `CREATE TABLE`, not just unlogged ones. adding `_turbobulk_staging_%` to
the exclude list works but needs a release (DATA-74, Backlog, would make that
list dynamic). Operator-side, disabling CDC on the instance is a complete
removal rather than a partial one: the cleanup path drops the publication, the
`cdc_set_replica_identity_on_create` event trigger and the
`public.set_replica_identity_full()` function together (`cdc/database.go:578-592`),
so nothing is left to fire. That is the only lever available today that does not
require a release from another team, and testing it is itself strong
confirmation of the diagnosis. It costs Analytics on that instance, which is
gated on CDC; Visual Explorer does not use CDC and is unaffected.

**Causation proven by a completed same-instance A -> B -> A (2026-09-30).**
CDC was later re-enabled on that instance and the failure returned immediately,
so the sequence is fail / 152 clean jobs / fail, one instance throughout with
artifact and command unchanged. Enabling CDC on an already-populated database is
clean: after the successful seed and the re-enable, a full strict readback of
all 3,043 objects passed with 0 mismatches, 231/231 cables and 10/10 circuit
terminations resolved. The usable interim workaround is therefore to seed `main`
with CDC off and enable it afterwards, then load into branches from then on;
`main` cannot be re-seeded without disabling CDC again. With CDC enabled, the
`phase-1:circuit_type` job errored immediately on three separate attempts with
the publication error above. CDC was then disabled on that instance and the
identical artifact, command and delivery policy were rerun against the same
main: **152 jobs, zero errored, zero publication errors, 4,555 objects matched
with 0 mismatches, 348/348 cables traced, 16 circuit terminations verified.**
One variable changed. The failing receipt is preserved beside the passing one as
`archived-cdc-failure-circuit-probe-main-*.json`. A cross-instance control
already existed - the same artifact main-seeds normally on a sibling tenant
without CDC - but the same-instance toggle removes every remaining alternative
explanation. Note the first rerun attempt was invalid and was discarded: the
loader resumed from the stale receipt and replayed the recorded failure without
submitting a new job, so the receipt was archived and the load rerun from
scratch.

**Prior art: none (checked 2026-09-30).** Three independent read-only sweeps of
Linear, Pylon, Slack and GitHub found no report of this interaction anywhere,
and confirmed both defects are live on `main` in the platform monorepo and in
TurboBulk as of the most recent commits. The trigger function still catches only
`duplicate_object` and still has no `relpersistence` guard; the exclude list
still has no staging-table pattern; TurboBulk still creates unschema-qualified
`CREATE UNLOGGED TABLE` staging tables in three places. The mechanism is
documented by its author (NBF-251 introduced the trigger; NBF-512 made the
per-table `ALTER PUBLICATION` path load-bearing), and NBC-6574 states that any
plugin's tables are captured by default - but it anticipates downstream schema
incompatibility, never a transaction abort inside the plugin emitting the DDL.
NBC-7786 is the same trigger breaking `pg_restore`, so this defect class already
has one open ticket. Operationally relevant: NBC-6875 records that manual
`ALTER PUBLICATION` edits are auto-reverted when the platform reconciles the
publication, so hand-patching a tenant is not a durable mitigation - only
disabling CDC is. Platform PR #6670 is open now and rewrites both affected
files without addressing either gap, which makes it the cheapest landing spot
for the one-line fix.

**Filed 2026-09-30, with explicit approval:** NBC-7787 (Eng - Cloud Delivery,
High, in Triage under the NBC-7290 CDC lifecycle epic), carrying the root cause,
the self-contained reproduction, the same-instance A/B and the proposed
`relpersistence` guard, and related to NBC-7786 / NBC-6574 / DATA-74. A comment
was also left on platform PR #6670, which rewrites that function while it is
open. The draft, the reproduction script and both Postgres runs are preserved
under `build/cdc-bug/`. The "no other teams" gate returns to closed: this
approval covered these two actions only.

Standing gates per phase: full offline suite, live load/verify/repeat on the
pinned 4.7.1 stack, docs in the same pass, adversarial review before push, and
cold-start-se runs to two consecutive cleans whenever the loading surface or
operator docs change. The "no other teams" gate remains closed. Live gates
apply to new code paths and target versions; already-qualified paths on demo
targets run once per load with no repeat gate (see CLAUDE.md verification-depth
policy).

## Community showcase build, 2026-10-01/02 (v0.16)

Built the `profiles/showcase-provider.toml` estate onto the dedicated
`crsk8600` tenant through four critic/fix rounds (carrier-engineer and NetBox
data-model reviewers; see the v0.16 paragraph in CLAUDE.md). Final verdicts
before round five: engineer "mostly", data model "good, not yet beautiful".

**Reviewed backlog, deliberately not built yet** (architectural; each needs its
own design pass and touches every provider builder):

1. **Access/aggregation layer.** Customer fibre lands directly on MX204 SFP+
   ports with no premises NID, so 24 PEs serve ~60 sites and PE density caps
   the estate. A per-PoP aggregation pair (ACX/EX) plus a premises NID would
   let the customer count grow to a believable carrier scale.
2. **Hostname convention.** Names mix `chicago-cermak-pe-a`, `dc01-cp01-lf-a`
   and `cedar-regional-bank-chi11-gw01`; CLLI-style facility codes exist but no
   hostname uses them. Identities move, so it is a rebaseline.
3. **NOC VRF set.** The NOC inherits the enterprise DC's
   Applications/Database/Backup/Storage VRFs; a carrier NOC would collapse them
   into one services VRF and use Carrier Management for management.
4. **Cabinet height.** DC/PoP cabinets stay 24U sized to content; 42U needs
   denser rack lanes across profiles.

**Product findings (ours to report, not generator defects):**

- Validation 1.14.1: results of a check are attributed to the *first* rule
  using that check in a policy (each rule's parameters still apply); graph
  checks ignore rule-level role scoping; `runs/` POST requires `status` though
  the official skill omits it; several graph checks
  (`site_redundant_paths`, `dual_homed_circuits`, `device_single_point_of_failure`,
  `shared_failure_domain`) report results that contradict the graph — reasons
  are recorded per excluded check in `estates/validation.py`.
- Asset Lifecycle 0.3.1: BOM/PO creates require `status` and shipments require
  `courier_account` despite the schema marking them optional.
- Visual Explorer: the WAN map opens at globe zoom and never fits to data;
  at site scope the BGP topology force layout stacks every node on one point.
- Diode SDK 1.14.0 has no `created` on JournalEntry, so dated journals reach a
  target only through `just load`.

**Open on `crsk8600` after the final seed (2026-10-02), needs the operator:**

- **Fleet device inventory is empty.** Orb agents, credentials and the agent's
  MQTT session work (agent ONLINE, Vault-referenced credential created), but
  the credential's "Add device" picker and the job wizard list no devices even
  though 133 NetBox devices have primary IPs. Slack reports a second,
  instance-level switch (`NETBOX_FLEET_ENABLED`) besides the org preview flag;
  it is set by NetBox Labs staff, not from this repo. Until it is on, a Device
  discovery job cannot be created, so Jobs/Run history stay empty.
- **Direct Diode ingest produces no Assurance deviations here.** The drift
  twin's 12 entities were acknowledged twice by `crsk8600`'s Diode
  (`genial-showcase` client) yet no deviation appeared and no NetBox row
  changed. The same path created deviations on `rksd1051` in September, and the
  Fleet agent's OTLP bridge path did create deviations on `crsk8600` (see
  next item), so the difference is per-instance Diode/Assurance configuration
  we cannot read; Cloud exposes no reconciler ingestion logs to tenants.
- **Accidental real ingest, cleaned up.** The first `discovery-lab-check`
  collided with the running Fleet agent on `127.0.0.1:8072`, so its dry-run
  policy ran on the Fleet agent's backend and ingested ~200 create deviations
  (old lab hostnames, pre-seed). Fixed in `lab/discovery/vm.sh` (the dry run
  pauses the Fleet agent); all were marked Ignored.
