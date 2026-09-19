# University campus composition

`university-campus.toml` requests **one** university campus: keyed academic
buildings, keyed residence halls, a library and the single campus data center
they all depend on. This is what distinguishes it from the K-12 district, which
models many small campuses in one metro. Wireless is the centre of the story:
every building carries per-zone managed and visitor budgets at campus density.

```sh
just plan profiles/university-campus.toml
just generate profiles/university-campus.toml build/campus-demo
just verify build/campus-demo/plan.json
just load-check build/campus-demo
```

This profile is offline-checked, fits the TurboBulk contract, and holds a
recorded live qualification receipt on the pinned local 4.7.1 stack
(see [docs/qualification.md](../docs/qualification.md)).

## Demand and supported boundaries

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `buildings` | array of tables | four authored buildings | `1`–`16` entries, each with a unique lowercase `key` (1–20 chars) | grow-only (entries may be appended) |
| `buildings[].classrooms` | integer | `8` | `0`–`40` lecture halls | grow-only |
| `buildings[].lab_seats` | integer | `48` | `0`–`200`, filling 24-seat teaching/research labs | grow-only |
| `buildings[].offices` | integer | `24` | `0`–`60` desks, filling twelve-desk faculty pods | grow-only |
| `buildings[].wireless` | table of tables | authored per zone | Existing zones only, each `managed`/`guest` `0`–`128` with a zone total of at most `128` | grow-only |
| `residences` | array of tables | three authored halls | `0`–`16` entries, each with a unique lowercase `key` | grow-only |
| `residences[].rooms` | integer | `120` | `10`–`400` | grow-only |
| `residences[].wired_ports_per_room` | integer | `1` | `0`–`2` | **rebaseline** |
| `residences[].wireless` | table of tables | authored per floor | Same zone rules as buildings | grow-only |
| `library` | table | `{reading_seats = 160, aps = 8}` | `reading_seats` `24`–`300`; `aps` `1`–`16` | grow-only |
| `wan_peak_mbps` | integer | `4000` | `1`–`16000`; must cover the combined peak of academic buildings, residence halls **and** the library | **rebaseline** — size with headroom, not to today's demand: it is frozen during growth, and every appended hall or building raises the required peak |
| `wan_tiers_mbps` | array of integers | `[50, 100, 200, 500, 1000]` | Increasing unique integers `1`–`1000`, last exactly `1000` | **rebaseline** |

Every [common key](../docs/recipes.md#common-keys) is accepted except
`headquarters_staff`, which is a bank/retail office-sizing input and is rejected
here. `reservation_user` must stay empty. There is no `design_mix`,
`site_designs` or `acquired_sites`: every building is modern, and no
acquisition, refresh or remodel transition exists in this profile.

`address_pool` must be an aligned private `/8` through `/16`; each site reserves
a `/16` and each segment inside it is a `/22`, because a 400-room hall with two
ports per room addresses 800 installed ports on one student segment.

Building keys must also stay distinct after hyphens are removed, so generated
device DNS names remain unique.

## Site ids

| Site | Id | Kind |
| --- | --- | --- |
| Campus data center | `dc-01` | `dc` |
| Academic building | `bldg-<key>` | `academic` |
| Residence hall | `hall-<key>` | `residence` |
| Library | `library-01` | `library` |

There is one data center, not the shared two-DC pair: a campus runs its own
machine room. Every site sits in one authored metro — this is a single campus,
not a fleet — but the established grammar has no sub-site campus container, so
each building is its own NetBox site.

## What is physically represented

Every building holds a permanent, reserved position per room. Academic buildings
and the library place eight rooms per floor; residence halls place fifty rooms
along one corridor floor. Eight floors per building is the ceiling, matching the
eight management-uplink blocks the shared allocator supports.

| Building | Rooms | Wired endpoints | Radios | Cameras |
| --- | --- | --- | --- | --- |
| Academic | lecture halls, 24-seat labs, 12-desk office pods, one corridor per floor | one instructor position per hall, one per lab seat, one per office desk | per-zone mounts in each room | two per corridor |
| Residence | rooms, one corridor per floor | `wired_ports_per_room` per room | per-floor mounts in the corridor | two per corridor |
| Library | 24-seat reading rooms, a corridor per floor, a ground entrance | one per reading seat | `aps` explicit mounts: entrance first, then reading rooms | two per corridor |

Floor one holds the MDF; every floor above it gets its own IDF, and each room's
endpoints reach an access pair in **its own floor's** equipment room over a
bounded local copper route inside the authored 80 m ceiling. Access switches
grow in pairs and retain each endpoint's reserved physical port.

Segments per site are `management`, `staff`, `students`, `wireless`, `security`
and `guest`, plus `research` in academic buildings only. Instructor positions and
faculty desks sit on `staff`; lab seats on `research`; residence-room ports and
library study positions on `students`.

## Wireless

Each academic room and each residence floor is one zone with an explicit
concurrent device budget. Radios are sized at 32 devices per mount, up to four
mounts per zone — an authored equipment envelope, **not** surveyed RF capacity,
measured throughput or observed associations.

| Zone | Default managed | Default guest |
| --- | ---: | ---: |
| `lecture-<nnn>` | 28 | 4 |
| `lab-<nn>` | 24 | 0 |
| `office-<nn>` | 12 | 0 |
| `floor-<nn>` (residence) | 2 per room on that floor | 2 |

Staff WLANs ride `wlan0` and student WLANs `wlan1` (2.4 GHz channels when the
recipe selects `ap = "aruba"`, whose model declares a real dual-band split);
visitor service rides
`wlan0` at every campus building and never at the data center. Planned visitor
devices must fit the building's own guest `/22`.

The library is the exception: its `aps` key is an explicit authored mount count,
not a zone-budget derivation.

## Campus services

Six services run in the campus data center, sized from installed inventory:

| Service | Segment | Group threshold | Listener |
| --- | --- | --- | --- |
| `learning-portal` | applications | 2,000 installed endpoints | TCP/443 |
| `identity` | applications | 1,500 installed endpoints | TCP/443 + RADIUS UDP/1812,1813 |
| `dns` | applications | 16 campus buildings | TCP/53 + UDP/53 |
| `research-storage` | database | 120 installed lab seats | TCP/2049 |
| `monitoring` | applications | 4,000 installed endpoints | TCP/443 |
| `backup` | backup | 8 campus buildings | TCP/443 |

Each group is two rack-separated replicas. The thresholds are fictional planning
assumptions, not enrollment, course load, throughput ratings or retention sizing.

## What is explicitly not asserted

- **No enrollment, occupancy or staffing.** Lecture halls, lab seats, faculty
  desks, residence rooms and study positions are installed capacity only.
- **eduroam-style naming only.** The identity service carries RADIUS port
  inventory and the WLAN records use campus naming conventions. No
  authentication protocol, RADIUS realm, roaming federation, directory or
  certificate is configured, running or certified anywhere in this dataset.
- **No resident devices.** Each residence-room wired port is one installed
  endpoint record representing the port, never a student-owned machine.
- **No RF survey.** Mount counts and positions are an authored envelope.
- **No campus fiber.** Each building attaches through the shared two-carrier
  edge grammar. A real single campus would interconnect its buildings over owned
  fiber; no campus-owned dark fiber, duct diversity or running routing,
  firewall or HA behaviour is represented.
- **No segmentation enforcement.** Separate segments express intended
  boundaries, not firewall policy, NAC or a compliance state.

## Growth and rebaseline

Ordinary growth appends buildings, halls, rooms, seats, desks, reading seats,
radios and wireless budgets while preserving every existing room position, rack,
port, address, cable and journal. Adding lecture halls appends new rooms **above**
the existing labs and offices rather than renumbering the building underneath
them, because each room holds a permanent reserved position.

A new baseline is required to reduce any count, remove a building or hall,
change `wired_ports_per_room`, renew `wan_peak_mbps` or `wan_tiers_mbps`, or
reduce a wireless zone.

Because `wan_peak_mbps` is frozen while every appended building or hall raises
the required campus peak, size it at baseline with headroom for the growth you
expect — a value sized exactly to today's demand makes ordinary growth
impossible without a new baseline (regenerate, retire the live estate, and
reload from scratch).

## Scale ceiling

The input bounds top out near a campus of roughly eight thousand students —
sixteen academic buildings and sixteen 400-room halls. That figure is an
authored scale statement, not enrollment data: the generator only models
installed rooms, seats and ports. Combined physical capacity is checked
separately, and a building whose floors would need more than 38 access switches
is rejected with the exact arithmetic.
