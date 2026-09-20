# Utility composition

`utility.toml` requests an electric utility's operations estate: one or two
control centers and the substations they document. The demo question this
profile answers is *"can NetBox hold my substation inventory next to my IT
estate, with the zone boundary visible and honest?"* — so the separated segments
and the conduit between them, not the office layout, are the point.

```sh
just plan profiles/utility.toml
just generate profiles/utility.toml build/utility-demo
just verify build/utility-demo/plan.json
just load-check build/utility-demo
```

## Demand and supported boundaries

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `control_centers` | integer | `2` | `1` (primary only) or `2` (primary and backup) | grow-only (1 → 2) |
| `substations` | array of tables | four authored substations | `1`–`24` entries; each needs a unique hyphen-separated lowercase `key` of at most 20 characters (no leading, trailing or doubled hyphen), also distinct with hyphens removed | grow-only (append entries) |
| `substations[].kind` | string | `distribution` | `transmission` or `distribution`. Selects the authored corporate presence and planning demand — **never a voltage class, electrical rating or bus arrangement** | **rebaseline** |
| `substations[].bays` | integer | `6` | `2`–`16` installed switchyard bay positions | grow-only |
| `wan_tiers_mbps` | array of integers | `[50, 100, 200, 500, 1000]` | Increasing unique integers `1`–`1000`, last exactly `1000` | **rebaseline** |

Every [common key](../docs/recipes.md#common-keys) is accepted except
`headquarters_staff`, which is an office-sizing input for the bank and retail
profiles; `reservation_user` must stay empty. `address_pool` must be an aligned
private `/8` through `/16` because each site reserves a `/16`.

Site ids are `dc-01`, optionally `dc-02`, and `sub-<substation key>`; each
substation segment is a `/24` inside that substation's `/16`. A `/8` pool holds
256 site reservations, which covers the 26-site maximum (24 substations plus two
control centers) many times over. Allocation fails with the exact reservation
arithmetic if a smaller pool runs out.

There is no `wan_peak_mbps` key. The control-center edge is sized from the
resolved substation demand on every generation, so appending substations never
collides with a frozen purchase. Peak is `base + 3 × bays + 2 × corporate
workstations` per substation, where base is 20 Mbps for a transmission
substation and 10 for a distribution one — authored planning demand, not a
measured traffic model, and not telemetry volume. The reviewed bounds cap one
substation at 72 Mbps, inside the 1 Gb/s substation handoff after the widest
supported `reserve_fraction`, and the fleet at 1,728 Mbps, which the shared
control-center aggregation answers with three carrier edge pairs. The widest
recipe this profile accepts (24 substations × 16 bays, two control centers)
generates 26 sites and about 31,400 objects and passes offline validation; that
is a generation-scale figure, not a live ingestion result.

## Control centers, not substation-local services

The shared services sit in the paired `dc-01`/`dc-02` grammar every multi-site
profile in this repository uses. With `control_centers = 2` both centers run the
same service families, each as complete two-replica groups separated across
compute racks. That is **inventory of a second site, not a demonstrated
failover**: no replication, failover, control-center transfer or recovery is
executed or claimed.

Each control center is a dc-kind site whose machine room (the data hall) holds
the services. The operations room is named in the site record and is
deliberately **not** populated: operator consoles, a video wall and shift
positions are outside this dataset. A substation-local historian or front-end
deployment is a real-world variant this profile does **not** model, and no claim
is made about which is correct for any operator.

## Substation demand

Per-substation demand is authored policy derived from the two inputs, not extra
recipe keys. Counts are installed inventory: they are not telemetry points,
measurements, protection settings, breaker positions, switching states,
generation, load, outage figures or staffing.

| Quantity | Rule |
| --- | --- |
| Switchyard bays | one room per `bays`, each with a permanent reserved ground-floor position |
| Remote terminal units | one per bay (`role/rtu`), on the `telemetry` segment |
| Protection relays | one per bay (`role/protection-relay`), on the `protection` segment |
| Station HMIs | two per substation (`role/hmi`), on the `station` segment |
| Station gateway | one per substation (`role/station-gateway`), on the `telemetry` segment |
| Corporate workstations | two at a transmission substation, one at a distribution substation (`role/workstation`), on the `office` segment |
| Peak Mbps | `20 (transmission) or 10 (distribution) + 3 × bays + 2 × corporate workstations` |

## What is physically represented

Each substation has one authored control house on one ground floor holding a
control room, up to sixteen switchyard bay positions, and one equipment room
that serves all of them. Every bay keeps a permanent reserved position, so
commissioning a bay appends a position beside the existing ones instead of
renumbering the switchyard. Copper routes stay inside an 80 m ceiling measured
from that equipment room.

The two zones keep **separate access populations with separate port ledgers**.
Corporate endpoints reach `access-NN` switches; station endpoints reach
`ot-access-NN` switches. Each pair grows independently and retains every
endpoint's reserved physical port as demand grows, so commissioning a bay never
reroutes an existing relay, unit or desk. Endpoints remain single-homed. The two
zones share one finite 38-switch distribution attachment budget; exceeding it
fails at resolve time with the exact arithmetic.

Segments per substation:

| Segment | Zone | Holds |
| --- | --- | --- |
| `management` | corporate | operated equipment's management addresses |
| `office` | corporate | corporate workstations |
| `protection` | station | protection relays |
| `telemetry` | station | remote terminal units and the station gateway |
| `station` | station | station HMIs |
| `conduit` | boundary | the four distribution gateways, and nothing else |
| `wan` | corporate | the two carrier handoffs; no client, no gateway SVI |

Each substation attaches through the shared two-carrier edge grammar with a
purchased tier covering its peak after `reserve_fraction`. The actual span
provider on each attachment is shown per substation; **no carrier diversity,
duct separation or separate right of way is promised**. There is no wireless
coverage of any kind at a substation: no radio, WLAN, SSID or mobile endpoint
exists anywhere in this profile.

## The station/corporate boundary, precisely

This is the modeling claim worth walking through in a demo, and
`validate_utility.py` checks all of it independently of what any contract says —
including by walking each station VLAN's actual cabled flood domain, not by
trusting the builder's trunk lists.

**What is separated.** The station endpoints sit on the `protection`,
`telemetry` and `station` segments, in their own `northgate-protection`,
`northgate-telemetry` and `northgate-station` routing contexts, behind their own
`ot-access-NN` pair and their own `ot-dist-a`/`ot-dist-b` distribution pair.
Those segments' gateway SVIs exist only on that station pair. No station VLAN
appears on a corporate access switch, on the corporate distribution pair, on a
carrier edge device, or on any cable that reaches one, and no record outside the
zone — service, FHRP group, VM interface or anything else — may bind a station
port or address.

**What crosses.** Exactly two modeled paths, both deliberate and both recorded:

- the `conduit` segment, a fully meshed four-trunk link between the station and
  corporate distribution pairs that carries the conduit VLAN and nothing else.
  All four distribution switches hold an addressed gateway in it. This is the
  "actual routed path toward the control centers" the zone story needs, and it
  is the only one;
- each station switch's own dedicated management port, untagged into the
  substation `management` segment and cabled to the equipment room's management
  switch. Equipment has to be manageable; hiding that would be dishonest.

**What is not claimed.**

- **No enforcement, no ESP.** Separation here is modeled separation: distinct
  segments, distinct routing contexts, distinct switches, one declared transit
  VLAN. No firewall rule, access control list, route filter, data diode or
  authorisation boundary exists, and no electronic security perimeter is
  defined, drawn or implied by any record.
- **No NERC CIP.** No compliance state, asset categorisation, impact rating,
  certification, audit evidence or assessment exists. Nothing in this dataset
  supports a regulatory claim of any kind.
- **No air gap.** The conduit and the management path are real modeled links.
  This dataset does not represent an isolated station network, and saying it
  does would be false.
- **No SCADA or EMS execution.** The SCADA front end, historian and EMS gateway
  are inventory for a service family. No telemetry point, measurement, tag,
  sample, scan, control action, setpoint, switching command or energy management
  function exists, and nothing connects those services to a station endpoint.
- **No utility protocol.** DNP3, IEC 61850, Modbus, ICCP, IEC 60870-5-104 — none
  of them is configured, carried, listed or asserted anywhere, and no service
  listens on their well-known ports. The station endpoints carry ordinary IP
  inventory records only.
- **No protection logic.** Relays are reference endpoint inventory. There is no
  protection scheme, relay setting, trip logic, coordination study, CT/PT ratio,
  safety rating, certification, firmware version or vendor platform.
- **No grid semantics.** Bays are installed equipment positions. They are not
  voltage classes, electrical ratings, bus arrangements or breaker positions,
  and there is no one-line diagram, grid topology, connectivity model,
  power-flow, state-estimation, generation or load figure anywhere.
- **No physical security.** No fence, gate, door controller, badge reader,
  intrusion detection, camera or guard position is represented.
- **No physical separation.** Both distribution tiers share the one control-house
  equipment room. The boundary is modeled in the routing and VLAN graph, not by
  separate rooms, cabinets or enclosures.
- **No substation-rated hardware.** The station tier installs the same reviewed
  catalog access and distribution models as every other profile. No DIN-rail,
  fanless, hardened, extended-temperature or IEEE 1613 qualified model is
  represented, and no catalog model was added for this profile.
- **No critical-infrastructure claim.** This estate is documentation inventory
  for a fictional operator. Nothing here supports a claim about operating an
  actual grid, a real substation, or any critical-infrastructure obligation.

## Shared control-center services

Each requested control center runs six service families, each as complete
two-replica groups separated across compute racks:

| Service | Segment | One group covers | Listener |
| --- | --- | --- | --- |
| `scada-front-end` | applications | 120 installed remote terminal units | TCP/443 |
| `historian` | database | 400 installed station endpoints | TCP/5432 |
| `ems-gateway` | applications | 16 substations | TCP/443 |
| `monitoring` | applications | 2,000 installed endpoints | TCP/443 |
| `dns` | applications | 16 sites | TCP/53, UDP/53 |
| `backup` | backup | 12 substations | TCP/443 |

These thresholds are fictional planning assumptions, not telemetry point counts,
scan rates, sample intervals, retention sizing or vendor performance claims. The
listener ports are ordinary web, database and DNS ports chosen deliberately:
**no service listens on 102, 502, 2404, 20000 or any other industrial control
port**, and a test asserts that.

## Growth and rebaseline

Appending substations, raising a substation's `bays`, adding the backup control
center (`control_centers = 1` → `2`) and adding `site_names` entries for new
sites all grow in place. Removing a substation, lowering `bays`, changing a
substation's `kind`, dropping a control center, changing WAN tiers and renaming
an existing site require a new baseline without `--previous`. There is no
acquisition, refresh, control-house remodel or decommissioning transition in
this profile: no `design_mix`, `site_designs` or `acquired_sites` key is
accepted, and every site is modern.

`--kind loss-of-power-diversity` works on a generated utility plan, like the
other profiles. See [scenarios](../docs/scenarios.md) for its boundaries.
