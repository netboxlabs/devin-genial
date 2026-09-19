# Retail chain composition

`retail-chain.toml` requests a fleet of stores, one support centre, one
distribution centre and two paired commerce data centers. Store formats are
authored equipment profiles: back-office workstations, point-of-sale lanes,
coverage radios and security cameras. Distribution centres replace the lanes
with handheld scanner stations and carry heavier camera and radio coverage.

```sh
just plan profiles/retail-chain.toml
just generate profiles/retail-chain.toml build/retail-demo
just verify build/retail-demo/plan.json
just load-check build/retail-demo
```

## Demand and supported boundaries

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `stores` | table of integers | `{small = 4, medium = 2, large = 1}` | `small`, `medium`, `large` only, each `0`–`2000`, with at least one store overall | grow-only per format |
| `headquarters` | integer | `1` | `0` or `1` | grow-only |
| `headquarters_staff` | integer | `180` | `24`–`192`; sizes office pods, radios and cameras | **rebaseline** |
| `distribution_centers` | integer | `1` | `0`–`6` | grow-only |
| `wan_tiers_mbps` | array of integers | `[50, 100, 200, 500, 1000]` | Increasing unique integers `1`–`1000`, last exactly `1000` | **rebaseline** |

Every [common key](../docs/recipes.md#common-keys) is accepted;
`reservation_user` must stay empty. The two commerce data centers are fixed at
two — the pair is the point of the story — and `address_pool` must be an aligned
private `/8` through `/16` because each site reserves a `/16`.

A `/8` pool holds 256 site reservations, so the practical fleet is a few hundred
sites even though each format accepts up to 2,000. Allocation fails with the
exact reservation arithmetic when a pool runs out; the per-format bound is an
input check, not a promise that any combination fits.

Chain WAN peak across every store, distribution centre and the support centre
must stay at or under 16,000 Mbps, and each site's own peak must fit the 1 Gb/s
handoff after `reserve_fraction`.

## Store and warehouse formats

Format demand is fixed authored equipment policy, not a recipe input. Counts are
installed inventory: they are not staff rosters, shopper counts, transaction
volume or measured utilisation.

| Format | Workstations | Lanes / scanners | Radios | Cameras | Peak Mbps |
| --- | ---: | ---: | ---: | ---: | ---: |
| `small` store | 2 | 4 lanes | 2 | 4 | 20 |
| `medium` store | 4 | 8 lanes | 3 | 8 | 50 |
| `large` store | 8 | 16 lanes | 5 | 12 | 100 |
| Distribution centre | 12 | 24 scanners | 8 | 24 | 200 |

## What is physically represented

Each store has one authored floor with a sales floor, a back office and a
stockroom. Each distribution centre has a warehouse floor, an office and a
shipping dock. The ground-floor equipment room holds every rack, access pair,
management switch, console server, distribution pair and carrier edge.

Radio one covers the staffed room; the remainder cover the trading or warehouse
floor. Half of the cameras watch the trading or warehouse floor, half the stock
or dock area. Mounting positions are an authored equipment envelope, not a
surveyed RF or CCTV design, and copper routes stay inside an 80 m ceiling.

Access switches grow in pairs and hold each endpoint's reserved physical port as
demand grows, so adding stores never reroutes an existing lane, desk, scanner,
radio or camera. Endpoints remain single-homed.

Segments are separate per site: `management`, `backoffice`, `pos` (stores only),
`wireless`, `security` and `guest` (stores and the support centre). A
distribution centre has no lane segment and no public guest service. Scanner
stations share the back-office segment. Segmentation expresses intended
boundaries only — no firewall policy, payment application, card-data scope or
compliance state is demonstrated or certified.

Every store and the support centre publish a staff WLAN (WPA-Enterprise intent)
and an open guest WLAN on `wlan0`. Distribution centres publish the staff WLAN
only. No captive portal, authentication result, association or RF survey is
represented.

The support centre reuses the shared staffed-building grammar: twelve-desk
office pods, four pods and a local equipment room per floor, one radio per pod,
two cameras per floor and direct-terminated fiber risers to the MDF.

## Shared commerce services

Both data centers run the same eight service families, each as complete
two-replica groups separated across compute racks:

| Service | Segment | One group covers | Listener |
| --- | --- | --- | --- |
| `commerce-api` | applications | 40 stores | TCP/443 |
| `pos-gateway` | applications | 30 stores | TCP/443 |
| `inventory-db` | database | 100 stores | TCP/5432 |
| `loyalty` | applications | 120 stores | TCP/443 |
| `identity` | applications | 2,000 installed endpoints | TCP/443, RADIUS UDP/1812,1813 |
| `dns` | applications | 16 sites | TCP/53, UDP/53 |
| `monitoring` | applications | 4,000 installed endpoints | TCP/443 |
| `backup` | backup | 100 stores | TCP/443 |

These thresholds are fictional planning assumptions, not transaction ratings or
vendor performance claims. Application replication, failover, payment
processing and recovery are not executed. Each DC WAN budget independently
covers the whole declared chain peak; that is a modeled capacity assumption, not
measured trading throughput.

## Growth and rebaseline

Store counts, `headquarters`, `distribution_centers` and `site_names` entries for
new sites may grow in place. Reductions, `headquarters_staff` changes, WAN tier
changes and renaming an existing site require a new baseline without
`--previous`. There is no acquisition, refresh or store-remodel transition in
this profile: no `design_mix`, `site_designs` or `acquired_sites` key is
accepted, and every site is modern.

`--kind loss-of-power-diversity` works on a generated retail plan, like the other
profiles. See [scenarios](../docs/scenarios.md) for its boundaries.
