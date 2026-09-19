# Recipe key reference

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

A recipe is the TOML file passed to `plan`, `generate` and `build`. It expresses
demand: how many branches, workloads, classrooms, wards, PoPs and customers, plus
the shared identity and addressing settings. Shared builders turn that demand into
equipment, ports, addresses, racks and cables; nothing in this file selects a
vendor, a device model or a rack position.

Every profile hard-rejects unknown keys. A misspelled or unsupported key raises
`Unknown recipe fields: …` before any object is allocated, and the same rejection
applies to nested tables (workloads, schools, wards, customers, wireless zones).
There is no permissive mode: a key not listed here is not accepted.

A **baseline** is a plan generated without `--previous`. **Growth** reuses a frozen
plan as an allocation ledger (`--previous plan.json`, or the third `just generate`
argument) so existing site IDs, ports, addresses and rack positions survive; a key
that is frozen, or a value that moves in an unsupported direction, is rejected
rather than silently renumbering the estate.

- [Reading the tables](#reading-the-tables)
- [Common keys](#common-keys)
- [Regional bank](#regional-bank)
- [Enterprise data center](#enterprise-data-center)
- [School district](#school-district)
- [Hospital and clinics](#hospital-and-clinics)
- [Provider backbone](#provider-backbone)
- [Worked example](#worked-example)
- [Not expressible in a recipe](#not-expressible-in-a-recipe)

## Reading the tables

The **Growth** column has three values:

| Value | Meaning |
| --- | --- |
| mutable | May change freely between a plan and its grown successor. |
| grow-only | May increase or gain entries. Decreasing or removing requires a new baseline; where growth is unchecked, a smaller artifact simply omits records and never deletes target objects. |
| **rebaseline** | Frozen during growth. Any change is rejected; generate a new baseline without `--previous`. |

Combined demand can fail even when every field is inside its own bounds. Port,
rack, power, address and object ceilings are checked separately during allocation;
choosing the maximum of every input is not a supported composition.

## Common keys

These are top-level keys accepted by more than one profile. They are validated in
`resolve_bank_recipe` (`estates/model.py:61`) and re-exported to the other four
profiles through each module's `COMMON` set. The thirteen keys frozen for growth
are enforced at `estates/model.py:146`.

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `profile` | string | `regional-bank` | `regional-bank`, `enterprise-data-center`, `school-district`, `hospital-clinics`, `provider-backbone`. Omitting the key selects the bank. | **rebaseline** |
| `namespace` | string | per profile | 2–20 character DNS label: `[a-z][a-z0-9-]*[a-z0-9]`. Separates estate identities and VRFs; it is not a target-side access boundary. | **rebaseline** |
| `name` | string | per profile | 1–80 characters. Participates in Diode matching identities for the provider. | **rebaseline** |
| `seed` | integer | `42` | `0` ≤ seed < 2^63. Drives bounded local variation (serials, procurement dates, design-pool choices) only. | **rebaseline** |
| `as_of` | string | `2026-09-01` | ISO date. Observation date for authored history. | **rebaseline** |
| `address_pool` | string | per profile | Aligned RFC1918 IPv4 network, `/8` through `/20`, further restricted per profile (see below). | **rebaseline** |
| `ipv6_pool` | string | absent | Aligned IPv6 documentation network `/32`–`/40` inside `2001:db8::/32` or `3fff::/20`. Absent means IPv4-only. | **rebaseline** |
| `reserve_fraction` | float | `0.2` | `0.1`–`0.4`. Exact decimal headroom, not a spare-capacity recovery guarantee. | **rebaseline** |
| `patching` | string | `direct` | `direct` (continuous channels) or `panels` (front/rear mappings; needs the local NetBox 4.7 bridge). | **rebaseline** |
| `reservation_user` | string | `""` | Empty, or an existing NetBox username, 1–150 of `[\w.@+-]`. Binds an existing account; Diode never creates one. Must be empty on every profile except the bank. | **rebaseline** |
| `wan_tiers_mbps` | array of integers | `[50, 100, 200, 500, 1000]` | Strictly increasing unique integers, each 1–1000, last element exactly `1000`. Purchased tiers; the physical handoff stays 1 Gb/s. Not accepted by the enterprise profile. | **rebaseline** |
| `max_objects` | integer | `500000` | `100`–`2000000`. Generation fails when the budget is exceeded. | mutable |
| `demo` | string | `baseline` | `baseline` or `loss-of-power-diversity` on all profiles; the provider additionally accepts `provider-span-maintenance`. `plan` always previews the healthy baseline. The bank omits the key entirely when it is not supplied; absence is read as `baseline`. | mutable |

Per-profile `address_pool` ceilings and per-site reservation sizes:

| Profile | Accepted prefix | Reservation per site | Default |
| --- | --- | --- | --- |
| `regional-bank` | `/8`–`/20` | `/20` | `10.0.0.0/8` |
| `enterprise-data-center` | `/8`–`/16` | `/16` | `10.64.0.0/12` |
| `school-district` | `/8`–`/16` | `/16` | `10.128.0.0/12` |
| `hospital-clinics` | `/8`–`/16` | `/16` | `10.128.0.0/12` |
| `provider-backbone` | `/8`–`/12` | `/24` (first `/16` NOC, last `/16` infrastructure) | `10.0.0.0/8` |

Default `namespace` / `name`: bank `cedar` / `Cedar Regional Bank`; enterprise
`summit` / `Summit Enterprise Infrastructure`; school `maple` / `Maple School
District`; hospital `lakeshore` / `Lakeshore Health System`; provider
`lakes-fiber` / `Great Lakes Fiber`.

## Regional bank

Allowlist: `estates/model.py:62`. Accepts every [common key](#common-keys),
including a non-empty `reservation_user`, plus:

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `data_centers` | integer | `2` | Exactly `2`. The paired-DC service design is fixed. | mutable (single legal value) |
| `headquarters` | integer | `1` | `0`–`2` | grow-only |
| `headquarters_staff` | integer | `180` | `24`–`192`. Determines office floors, equipment rooms and HQ WAN demand. | **rebaseline** |
| `branches` | table | `{small = 4, medium = 2, large = 1}` | Only `small`, `medium`, `large`; each an integer `0`–`2000`. Omitted sizes count zero. | grow-only |
| `design_mix` | table | `{modern = 100, inherited = 0, refreshed = 0}` | Only `modern`, `inherited`, `refreshed`; each an integer weight `0`–`100`, at least one positive. Applies to newly added branches. | **rebaseline** |
| `site_designs` | table | `{}` | Maps a branch ID to `modern`, `inherited` or `refreshed`. IDs are `br-s0001`, `br-m0001`, `br-l0001`, … and must exist. | grow-only for new branches; changing an existing assignment needs the scenario transition or a new baseline |
| `acquired_sites` | array of strings | `[]` | Existing branch IDs, no duplicates. The branch must have inherited lineage — an acquired `modern` branch is rejected. | grow-only; changing existing ownership needs the acquisition transition |

Branch counts drive fixed per-size demand: small 12 workstations / 2 ATMs / 2 APs
/ 2 cameras / 20 Mbps, medium 36 / 4 / 4 / 4 / 50, large 84 / 6 / 8 / 8 / 100.
Those figures are authored, not recipe inputs.

Reducing a branch count is accepted by the resolver but is not growth: the vacated
site keeps its address reservation, the records are omitted from the new artifact,
and nothing is deleted from NetBox. Changing a branch's size means retiring one
immutable size/ordinal identity and creating another.

`acquired_sites` and `site_designs` are also the inputs the acquisition and refresh
scenarios drive; see [docs/scenarios.md](scenarios.md).

## Enterprise data center

Allowlist: `estates/enterprise.py:27`, with `COMMON` at `estates/enterprise.py:12`.
Accepts the common keys **except `wan_tiers_mbps`**; `patching` must be `direct`
and `reservation_user` must be empty.

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `data_centers` | integer | `2` | `1`–`8`. Each site receives the full workload demand. | grow-only |
| `wan_peak_mbps` | integer | `1200` | `1`–`16000`, per data center. Sizes parallel pairs of 1-Gbps handoffs; it does not create faster ports. | grow-only |
| `workloads` | array of tables | three-workload example | `1`–`16` entries. | grow-only (keys may be added; removal needs a new baseline) |

Each `[[workloads]]` entry (`estates/enterprise.py:52`):

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `key` | string | required | Unique, `[a-z][a-z0-9-]{0,39}` | identity |
| `groups` | integer | `1` | `1`–`512` synthetic shards | grow-only |
| `replicas` | integer | `2` | `2`–`4` per group | **rebaseline** |
| `failure_domain` | string | `rack` | `host` (separate physical hosts) or `rack` (also separate compute racks and paired fabric/WAN devices) | **rebaseline** |
| `network` | string | `applications` | `applications`, `database`, `backup` | **rebaseline** |
| `vcpus` | integer | `4` | `1`–`64` | **rebaseline** |
| `memory_mb` | integer | `8192` | `1`–`262144` | **rebaseline** |
| `disk_mb` | integer | `100000` | `1`–`8000000` | **rebaseline** |
| `criticality` | string | `tier-2` | `tier-1` or `tier-2` | **rebaseline** |
| `listeners` | array of tables | one TCP/443 listener named after the key | Non-empty | **rebaseline** |

Each listener (`estates/datacenter.py:91`) requires exactly `key`, `name`,
`protocol` and `ports`: `key` is `""` or a unique `[a-z][a-z0-9-]{0,39}`, `name` is
unique non-empty text within its VM, `protocol` is `tcp` or `udp`, and `ports` is a
non-empty list of unique integers `1`–`65535`.

Adding or removing rack-diversity entirely — flipping whether *any* workload uses
`failure_domain = "rack"` — also requires a new baseline.

## School district

Allowlist: `estates/school.py:24`, with `COMMON` at `estates/school.py:13`. Accepts
every common key; `reservation_user` must be empty.

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `schools` | array of tables | two-campus example (`oak`, `ridge`) | `1`–`64` keyed campuses | grow-only (removal needs a new baseline) |

Each `[[schools]]` entry (`estates/school.py:43`):

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `key` | string | required | Unique `[a-z][a-z0-9-]{0,19}`, also unique after removing hyphens (device DNS names) | identity |
| `classrooms` | integer | `8` | `1`–`32`; eight per floor, each occupied floor gets a closet | grow-only |
| `students_per_classroom` | integer | `24` | `1`–`36` | **rebaseline** |
| `wired_seats_per_classroom` | integer | `4` | `0`–`12`, not more than `students_per_classroom` | **rebaseline** |
| `administrative_staff` | integer | `12` | `0`–`48`, in twelve-desk pods | grow-only |
| `lab_seats` | integer | `0` | `0`–`36`, within enrollment left after classroom wired seats | grow-only |
| `wan_peak_mbps` | integer | `200` | `1`–`800`, and ≤ `1000 × (1 − reserve_fraction)`. District total across campuses ≤ `16000`. | **rebaseline** (WAN renewal) |
| `wireless` | table of zone tables | derived defaults | See below | grow-only |

`[schools.wireless.<zone>]` (`estates/places.py:279`) accepts only `managed` and
`guest`, each an integer `0`–`128` with a per-zone total ≤ `128`; planned guests
across a campus must fit 4,084 client addresses. Legal zone names are the existing
rooms: `classroom-001`…`classroom-NNN`, `admin-01`…`admin-NN` (one per twelve
desks), and `computer-lab` when `lab_seats` is non-zero. Unknown zone names are
rejected. Defaults: a classroom's managed budget is its enrollment minus wired
seats plus one teaching device, an admin pod's is the desks actually installed, the
lab's is `0`; `guest` defaults to `0` everywhere.

## Hospital and clinics

Allowlist: `estates/hospital.py:51`, with `COMMON` at `estates/hospital.py:13`.
Accepts every common key; `reservation_user` must be empty.

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `hospitals` | array of tables | one `central` hospital with two wards | `1`–`8` keyed entries | grow-only |
| `clinics` | array of tables | `west` and `north` | `0`–`64` keyed entries; use `clinics = []` for none | grow-only |

Each `[[hospitals]]` entry (`estates/hospital.py:33`):

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `key` | string | required | Unique `[a-z][a-z0-9-]{0,19}`, also unique without hyphens | identity |
| `administrative_desks` | integer | `12` | `0`–`48`, in twelve-desk ground-floor pods | grow-only |
| `imaging_rooms` | integer | `1` | `0`–`8` | grow-only |
| `wan_peak_mbps` | integer | `300` | `1`–`800`, ≤ `1000 × (1 − reserve_fraction)`; system total ≤ `16000` | **rebaseline** (WAN renewal) |
| `wards` | array of tables | one `medical` ward | `1`–`7` keyed wards; seven plus ground consume the eight management attachment positions | grow-only |
| `wireless` | table of zone tables | derived defaults | See below | grow-only |

Each `[[hospitals.wards]]` entry:

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `key` | string | required | Unique `[a-z][a-z0-9-]{0,19}` within its hospital | identity |
| `beds` | integer | `8` | `4`–`16` bed stations (installed capacity, not patient volume) | grow-only |
| `clinical_desks` | integer | `4` | `2`–`8` workstations | grow-only |

Each `[[clinics]]` entry:

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `key` | string | required | Unique `[a-z][a-z0-9-]{0,19}`, also unique without hyphens | identity |
| `exam_rooms` | integer | `6` | `1`–`24`; eight per floor | grow-only |
| `administrative_desks` | integer | `4` | `0`–`24` | grow-only |
| `imaging_rooms` | integer | `0` | `0`–`4` | grow-only |
| `wan_peak_mbps` | integer | `100` | `1`–`800`, ≤ `1000 × (1 − reserve_fraction)`; system total ≤ `16000` | **rebaseline** (WAN renewal) |
| `wireless` | table of zone tables | derived defaults | See below | grow-only |

Hospitals have no `exam_rooms` key and clinics have no `wards` key; either is
rejected as unknown. `[hospitals.wireless.<zone>]` and `[clinics.wireless.<zone>]`
use the same `managed`/`guest` bounds as the school profile. Legal zone names:
`ward-<ward key>` (hospitals), `exam-001`… (one per four exam rooms),
`admin-01`… (one per twelve desks), `imaging-01`… (one per imaging room), and
`reception`. Ward zones default to 24 managed devices, exam zones to four per room
in the group, admin zones to the desks installed, imaging zones to four, reception
to four; `guest` defaults to `0`.

## Provider backbone

Allowlist: `estates/provider.py:50`, with `COMMON` at `estates/provider.py:20`.
Accepts every common key including `wan_tiers_mbps`; `reservation_user` must be
empty. `demo` additionally accepts `provider-span-maintenance`.

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `topology` | string | `incremental-mesh` | `incremental-mesh` only | **rebaseline** |
| `pops` | array of tables | three PoPs in Chicago, Detroit, Cleveland | `3`–`64` entries spanning at least three distinct metros | grow-only; an existing entry must stay byte-identical |
| `customers` | array of tables | one `harbor-logistics` customer | `1`–`256` entries | grow-only |
| `noc_pop_a` | string | first PoP key in sorted order | An existing PoP key, distinct from `noc_pop_b` | **rebaseline** |
| `noc_pop_b` | string | second PoP key in sorted order | An existing PoP key, distinct from `noc_pop_a` | **rebaseline** |
| `noc_peak_mbps` | integer | `100` | `1`–`800`, and ≤ `1000 × (1 − reserve_fraction)`. Excluded from backbone offered-load accounting. | **rebaseline** |
| `asn_base` | integer | namespace-derived | `4200000000`–`4294966271`, aligned to a 1024-number block from `4200000000`. Global target ASN conflict preflight is still required. | **rebaseline** |

Each `[[pops]]` entry (`estates/provider.py:70`) requires exactly two keys:

| Key | Type | Default | Accepted values and bounds |
| --- | --- | --- | --- |
| `key` | string | required | Unique `[a-z][a-z0-9-]{0,19}`, also unique after removing hyphens |
| `metro` | string | required | `chicago`, `detroit`, `cleveland` or `milwaukee` |

Each `[[customers]]` entry (`estates/provider.py:95`) requires `key`, `hub_pop` and
`sites`:

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `key` | string | required | Unique `[a-z][a-z0-9-]{0,19}` | identity |
| `service` | string | `private-l3` | `private-l3` only | **rebaseline** |
| `hub_pop` | string | required | One of this customer's own attachment PoPs; ordinal `001` there is the hub premises | **rebaseline** |
| `sites` | array of tables | required | `2`–`len(pops)` entries, each a distinct known PoP | grow-only |
| `site_peak_mbps` | integer | `50` | `1`–`800` directed traffic from each non-hub premises toward its hub; ≤ `1000 × (1 − reserve_fraction)` | **rebaseline** |
| `hub_commit_mbps` | integer | `1000` | `1`–`1000`, must be a member of `wan_tiers_mbps`, and must cover `(premises − 1) × site_peak_mbps` after reserve | **rebaseline** (bandwidth renewal) |
| `lan_endpoints` | integer | `4` | `1`–`12` wired office desks at each premises | grow-only |

Each `sites` entry (`estates/provider.py:116`) accepts `pop` (required, a known PoP
key, unique within the customer) and `count` (optional, default `1`, `1`–`12`).
Combined customer and NOC attachments cannot exceed twelve per PoP. Composed site
identities (`ce-<customer>-<pop>-<nnn>`) must not collide.

## Worked example

Start from a shipped profile and edit a copy — never the shipped file, and never a
generated `plan.json`:

```sh
cp profiles/school-district.toml profiles/greenfield-district.toml
```

Three realistic edits, from safest to most disruptive.

**1. Add a campus (grow-only).** Append a new `[[schools]]` entry. Existing campus
records, ports and addresses are untouched:

```toml
[[schools]]
key = "birchwood"
classrooms = 16
students_per_classroom = 28
wired_seats_per_classroom = 6
administrative_staff = 20
lab_seats = 24
wan_peak_mbps = 400
```

```sh
just generate profiles/greenfield-district.toml build/district-v2 build/district-v1/plan.json
```

**2. Grow an existing campus (grow-only).** Raise `classrooms`, `administrative_staff`
or `lab_seats` on a campus that already exists, and raise a wireless zone's device
budget. New rooms and APs append; old endpoint ports, IPs and channels stay put:

```toml
[schools.wireless.classroom-001]
managed = 40
guest = 8
```

Leave `wan_peak_mbps` alone while doing this: raising it is WAN renewal, which the
resolver rejects during growth.

**3. Enable dual-stack (new baseline).** Adding `ipv6_pool` to a recipe that did not
have one is a frozen-key change:

```toml
ipv6_pool = "2001:db8::/32"
```

Generate it without `--previous`, into a fresh output directory and against a fresh
target — the previous estate's identities are not carried forward:

```sh
just generate profiles/greenfield-district.toml build/district-v6
```

The same applies to scaling the bank by changing `headquarters_staff`, retiering
`wan_tiers_mbps`, changing `design_mix`, switching `patching`, renaming the estate
or moving the address pool: each is a new baseline, not growth. Increasing
`branches`, enterprise `data_centers` / workload `groups`, hospital wards or
provider PoPs and customers is ordinary growth.

Before reusing a saved plan the generator reproduces it from its own recipe and
ledgers and compares objects and contracts; an edited or stale plan is rejected
rather than silently renumbering.

## Not expressible in a recipe

Recipes size demand. They do not select:

- **Vendors, device types or models.** Hardware comes from `catalog/hardware.json`
  and each profile's authored role-to-alias mapping.
- **Site names, facility codes, geography, addresses or coordinates.** Site display
  names are derived from the namespace and ordinal (`cedar-complete-hq-01`), and
  metros, rooms and rack geometry are authored in `estates/places.py`.
- **VLAN IDs, subnet layout within a site, interface names, rack units or cable
  lengths.** These are allocator outputs bound to stable keys.
- **Custom fields, tags, tenant hierarchies or NetBox config contexts.**
- **Per-object descriptions, comments or journal text.**
- **Object counts directly.** `max_objects` is a ceiling that fails the build, not a
  target.

All of the above are code-level changes in `estates/` or `catalog/`, and each one
changes the hardware digest or generated identities, so each needs a new baseline.
Meaningful site names and stable facility codes are a known deferred item recorded
in [CLAUDE.md](../CLAUDE.md); do not work around it by renaming objects in a running
estate.
