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
- Source-backed LR/LX/LR4 modules, reference-server optics and two captive ends
  per actual Arista AOC assembly, preserving existing interface ownership and
  including installed optical power reserves.

[The lab guide](lab/README.md#current-v09-qualification) records exact live scope;
[GOAL.md](GOAL.md) indexes source, variation/growth/mutation checks, scale and
independent reviews. These additions close the corresponding basic inventory
and scenario gaps below. They do not implement optical loss budgets, breakout,
DWDM, observed RF/associations, measured consumption or executed replacements.
Native module deletion can cascade to its interface. The historical tables are
not current omission claims; their counts remain intact for reproducibility.

Version 0.9 implements the focused [provider span-maintenance story](README.md#provider-span-maintenance).
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
| Contacts | 14 contacts, 70 assignments, including site-scoped biomedical desks on 31 medical/imaging devices | 23 contacts, 63 assignments, customer/operator/carrier/facilities/service desks; 7 provider accounts | Included but bounded: all assignments are primary; no backup/on-call rota, phone or coverage-hours story. Hospital lacks provider-account records. |
| Journal/history | 38 entries | 60 entries | Partial: all `info`, on sites/circuits/one VM per workload. Six finite planning-note titles; no device repair, incident, retirement or completed-change narrative. Native creation dates remain ingestion time. |
| Resilience | Paired infrastructure and rack-separated service replicas; endpoints single-homed | Backbone connectivity and scoped span-loss traffic witness; customers single-homed; in-band PE management | Physical resilience is modeled. It does not prove clinical/application recovery, customer availability, independent ducts, UPS runtime or routing convergence. |
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
component/service templates, configuration contexts/templates/data files and
image attachments from directly supported estate entities. BGP-session objects
are not an SDK Entity either; that needs an explicitly researched optional
integration, not a promise of core Diode support. `device_config` has an SDK
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
