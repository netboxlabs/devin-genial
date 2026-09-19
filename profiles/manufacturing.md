# Manufacturing composition

`manufacturing.toml` requests a manufacturer: a set of plants, each with a
plant-floor (OT) zone and a corporate (IT) zone, plus the paired data centers
that hold the shared manufacturing services. The demo question this profile
answers is *"can NetBox hold my plant-floor inventory next to my IT estate, with
the zone boundary visible?"* — so the separated segments and the conduit between
them, not the office layout, are the point.

```sh
just plan profiles/manufacturing.toml
just generate profiles/manufacturing.toml build/manufacturing-demo
just verify build/manufacturing-demo/plan.json
just load-check build/manufacturing-demo
```

## Demand and supported boundaries

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `plants` | array of tables | three authored plants | `1`–`8` entries; each needs a unique hyphen-separated lowercase `key` of at most 20 characters (no leading, trailing or doubled hyphen), also distinct with hyphens removed | grow-only (append entries) |
| `plants[].production_lines` | integer | `4` | `1`–`12` installed line cells on that plant's production floor | grow-only |
| `plants[].warehouse_docks` | integer | `4` | `0`–`12` installed loading docks in that plant's warehouse | grow-only |
| `plants[].office_staff` | integer | `36` | `4`–`96` installed desk positions in that plant's office block, filling twelve-desk pods | grow-only |
| `wan_tiers_mbps` | array of integers | `[50, 100, 200, 500, 1000]` | Increasing unique integers `1`–`1000`, last exactly `1000` | **rebaseline** |

Every [common key](../docs/recipes.md#common-keys) is accepted except
`headquarters_staff`, which is an office-sizing input for the bank and retail
profiles; `reservation_user` must stay empty. `address_pool` must be an aligned
private `/8` through `/16` because each site reserves a `/16`.

Site ids are `dc-01`, `dc-02` and `pl-<plant key>`; each plant segment is a
`/24` inside that plant's `/16`. A `/8` pool holds 256 site reservations, which
covers the 10-site maximum (8 plants plus the two data centers) many times over.
Allocation fails with the exact reservation arithmetic if a smaller pool runs
out.

There is no `wan_peak_mbps` key. The data-center edge is sized from the resolved
plant demand on every generation, so appending plants never collides with a
frozen purchase. Peak is `20 + 2 × office_staff + 5 × production_lines + 3 ×
warehouse_docks` per plant — authored planning demand, not a measured traffic
model. The reviewed bounds cap one plant at 308 Mbps, inside the 1 Gb/s plant
handoff after the widest supported `reserve_fraction`, and the fleet at 2,464
Mbps, which the shared data-center aggregation answers with four carrier edge
pairs (five at `reserve_fraction = 0.4`). The widest recipe this profile accepts
(8 plants × 12 lines × 12 docks × 96 desks) generates 10 sites and 28,159
objects and passes offline validation; that is a generation-scale figure, not a
live ingestion result.

## Two data centers, not plant-local services

The shared services sit in the paired `dc-01`/`dc-02` grammar every multi-site
profile in this repository uses, not in each plant. That follows the bank,
retail and hospital precedent: services sized from the whole fleet's inventory
belong where the fleet's WAN aggregation already is. A plant-local MES or
historian deployment is a real-world variant this profile does **not** model,
and no claim is made about which is correct for any customer.

## Plant demand

Per-plant demand is authored policy derived from the three inputs, not extra
recipe keys. Counts are installed inventory: they are not production output,
throughput, OEE, takt time, cycle counts, shift rosters or measured utilisation.

| Quantity | Rule |
| --- | --- |
| Office pods | `ceil(office_staff / 12)`, up to eight, each with a permanent reserved ground-floor position |
| Workstations | one per installed desk position, on the `office` segment |
| Line cells | one room per `production_lines`, each with a permanent reserved position |
| Line controllers | one per line cell (`role/plc`), on the `process` segment |
| Operator panels | two per line cell (`role/hmi`), on the `supervisory` segment |
| Field-device drops | four per line cell (`role/field-device`), on the `process` segment |
| Loading docks | one room per `warehouse_docks`, each with a permanent reserved position |
| Scanner stations | two per dock (`role/scanner`), on the `logistics` segment |
| Coverage radios | one in reception, one per office pod, one per two docks |
| Cameras | one in reception and one over each dock |
| Peak Mbps | `20 + 2 × office_staff + 5 × production_lines + 3 × warehouse_docks` |

## What is physically represented

Each plant has one authored ground floor holding reception, up to eight
twelve-desk office pods, up to twelve loading docks, up to twelve production
line cells, and one equipment room that serves all of them. Every room keeps a
permanent reserved position, so commissioning a line, opening a dock or hiring
appends rooms beside the existing ones instead of renumbering the floor. Copper
routes stay inside an 80 m ceiling measured from that equipment room.

The two zones keep **separate access populations with separate port ledgers**.
Corporate endpoints reach `access-NN` switches; plant-floor endpoints reach
`ot-access-NN` switches. Each pair grows independently and retains every
endpoint's reserved physical port as demand grows, so commissioning a line never
reroutes an existing desk, drop or camera. Endpoints remain single-homed. The
two zones share one finite 38-switch distribution attachment budget; exceeding
it fails at resolve time with the exact arithmetic.

Segments per plant:

| Segment | Zone | Holds |
| --- | --- | --- |
| `management` | corporate | operated equipment's management addresses |
| `office` | corporate | staff workstations and the staff WLAN |
| `logistics` | corporate | wired dock scanner stations |
| `wireless` | corporate | AP management |
| `security` | corporate | cameras |
| `process` | plant floor | line controllers and field-device drops |
| `supervisory` | plant floor | operator panels |
| `conduit` | boundary | the four distribution gateways, and nothing else |
| `wan` | corporate | the two carrier handoffs; no client, no gateway SVI |

Each plant attaches through the shared two-carrier edge grammar with a purchased
tier covering its peak after `reserve_fraction`. Every plant publishes one staff
WLAN (WPA-Enterprise intent) on `wlan0`, served by the corporate data centers'
own DNS and RADIUS listener inventory. There is no guest WLAN and no wireless
coverage of the production floor at all.

## The IT/OT boundary, precisely

This is the modeling claim worth walking through in a demo, and
`validate_manufacturing.py` checks all of it independently of what any contract
says — including by walking each plant-floor VLAN's actual cabled flood domain,
not by trusting the builder's trunk lists.

**What is separated.** The plant-floor endpoints sit on the `process` and
`supervisory` segments, in their own `ironwood-process` and
`ironwood-supervisory` routing contexts, behind their own `ot-access-NN` pair
and their own `ot-dist-a`/`ot-dist-b` distribution pair. Those segments' gateway
SVIs exist only on that plant-floor pair. No `process` or `supervisory` VLAN
appears on a corporate access switch, on the corporate distribution pair, on a
carrier edge device, or on any cable that reaches one.

**What crosses.** Exactly two modeled paths, both deliberate and both recorded:

- the `conduit` segment, a fully meshed four-trunk link between the plant-floor
  and corporate distribution pairs that carries the conduit VLAN and nothing
  else. All four distribution switches hold an addressed gateway in it. This is
  the "actual routed path through the plant's distribution" the zone story
  needs, and it is the only one;
- each plant-floor switch's own dedicated management port, untagged into the
  plant `management` segment and cabled to the equipment room's management
  switch. Equipment has to be manageable; hiding that would be dishonest.

**What is not claimed.**

- **No enforcement.** Separation here is modeled separation: distinct segments,
  distinct routing contexts, distinct switches, one declared transit VLAN. No
  firewall rule, access control list, route filter, data diode or authorisation
  boundary exists, and none is implied by any record.
- **No air gap.** The conduit and the management path are real modeled links.
  This dataset does not represent an isolated plant-floor network, and saying it
  does would be false.
- **No Purdue model, no IEC 62443.** No level is assigned to any record, no zone
  or conduit definition in the standard's sense is represented, and no
  compliance, certification or assessment state exists.
- **No industrial protocol.** Modbus, PROFINET, EtherNet/IP, OPC-UA, DNP3,
  MQTT-SN — none of them is configured, carried, listed or asserted anywhere.
  The plant-floor endpoints carry ordinary IP inventory records only.
- **No control function.** Line controllers, operator panels and field devices
  are reference endpoint inventory. There is no control logic, I/O mapping,
  safety integrity level, hazardous-area rating, certification, firmware version
  or vendor control platform.
- **No physical separation.** Both distribution tiers share the one plant
  equipment room. The boundary is modeled in the routing and VLAN graph, not by
  separate rooms, cabinets or enclosures.
- **No industrial hardware.** The plant-floor tier installs the same reviewed
  catalog access and distribution models as every other profile. No DIN-rail,
  fanless, hardened, extended-temperature or industrially rated model is
  represented, and no catalog model was added for this profile.

## Shared corporate services

Both data centers run seven service families, each as complete two-replica
groups separated across compute racks:

| Service | Segment | One group covers | Listener |
| --- | --- | --- | --- |
| `mes` | applications | 24 installed production lines | TCP/443 |
| `historian` | database | 480 installed plant-floor endpoints | TCP/5432 |
| `erp-gateway` | applications | 8 plants | TCP/443 |
| `identity` | applications | 2,000 installed endpoints | TCP/443, RADIUS UDP/1812,1813 |
| `dns` | applications | 16 sites | TCP/53, UDP/53 |
| `monitoring` | applications | 3,000 installed endpoints | TCP/443 |
| `backup` | backup | 6 plants | TCP/443 |

These thresholds are fictional planning assumptions, not production rates, tag
counts, sample intervals, retention sizing or vendor performance claims. The
manufacturing-execution and historian services are inventory for a service
family: no production order, recipe, batch record, process tag, sample or
control action exists, and nothing connects them to a plant-floor endpoint.

## Growth and rebaseline

Appending plants, raising a plant's `production_lines`, `warehouse_docks` or
`office_staff`, and adding `site_names` entries for new sites all grow in place.
Removing a plant, lowering any of the three counts, changing WAN tiers and
renaming an existing site require a new baseline without `--previous`. There is
no acquisition, refresh, plant-remodel or decommissioning transition in this
profile: no `design_mix`, `site_designs` or `acquired_sites` key is accepted,
and every site is modern.

`--kind loss-of-power-diversity` works on a generated manufacturing plan, like
the other profiles. See [scenarios](../docs/scenarios.md) for its boundaries.
