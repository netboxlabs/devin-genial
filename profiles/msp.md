# Managed service provider composition

`msp.toml` requests one managed service provider: a network operations center
and a set of managed customer accounts whose offices it operates under contract.
Every customer is a separate NetBox tenant. The demo question this profile
answers is *"can NetBox hold my customers' networks side by side without them
touching?"* — so the tenancy separation, not the office layout, is the point.

```sh
just plan profiles/msp.toml
just generate profiles/msp.toml build/msp-demo
just verify build/msp-demo/plan.json
just load-check build/msp-demo
```

## Demand and supported boundaries

| Key | Type | Default | Accepted values and bounds | Growth |
| --- | --- | --- | --- | --- |
| `customers` | array of tables | four authored accounts | `1`–`24` entries; each needs a unique hyphen-separated lowercase `key` of at most 20 characters (no leading, trailing or doubled hyphen), also distinct with hyphens removed | grow-only (append entries) |
| `customers[].offices` | integer | `1` | `1`–`4` managed premises for this customer | grow-only |
| `customers[].staff` | integer | `24` | `4`–`48` installed desk positions in **each** of that customer's offices | grow-only |
| `customers[].wireless` | table of tables | authored per zone | Existing zones only (`reception`, `pod-<nn>`); each `managed`/`guest` an integer `0`–`128` with a per-zone total of at most `128`, and at most `252` guest devices across the office | grow-only |
| `wan_tiers_mbps` | array of integers | `[50, 100, 200, 500, 1000]` | Increasing unique integers `1`–`1000`, last exactly `1000` | **rebaseline** |

Every [common key](../docs/recipes.md#common-keys) is accepted except
`headquarters_staff`, which is an office-sizing input for the bank and retail
profiles; `reservation_user` must stay empty. `address_pool` must be an aligned
private `/8` through `/16` because each site reserves a `/16`.

Site ids are `noc-01` and `off-<customer key>-<nn>`; each office segment is a
`/24` inside that office's `/16`. A `/8` pool holds 256 site reservations, which
covers the 97-site maximum (24 accounts × 4 offices, plus the NOC) with room to
spare. Allocation fails with the exact reservation arithmetic if a smaller pool
runs out.

There is no `wan_peak_mbps` key. The NOC edge is sized from the resolved managed
demand on every generation, so appending customers never collides with a frozen
purchase. The reviewed bounds cap that demand at 11,136 Mbps — inside both the
1 Gb/s office handoff after the widest supported `reserve_fraction` and the
shared data-center aggregation, which answers it with fourteen carrier edge
pairs. The widest recipe this profile accepts (24 accounts × 4 offices × 48
desks) generates 97 sites and 104,793 objects and passes offline validation;
that is a generation-scale figure, not a live ingestion result.

## One operations site, not two

The provider's own estate is a single `dc`-kind site, `noc-01`: the NOC
*contains* its machine room. A separate small DC was the alternative. One site
was chosen because the second would need its own `/16`, its own carrier edge
pair and an inter-site link, and this grammar has no owned-fiber or campus
interconnect concept to model that link honestly — the same reasoning the
[university profile](university-campus.md) records for its single campus DC. The
provider's staff are represented by contacts and journals, not desk inventory;
the operations estate is its service platform.

## Office demand

Per-office demand is authored policy derived from `staff`, not extra recipe
inputs. Counts are installed inventory: they are not headcount, seat licences,
managed agent counts, ticket volume or measured utilisation.

| Quantity | Rule |
| --- | --- |
| Staff pods | `ceil(staff / 12)`, up to four, each with a permanent reserved ground-floor position |
| Workstations | one per installed desk position, on the `staff` segment |
| Coverage radios | one per zone at 32 devices per radio, up to four mounts per zone |
| Cameras | one in reception and one over each staff pod |
| Peak Mbps | `2 × staff + 20` |

Default wireless zone budgets are `reception` at 6 managed and 12 guest devices,
and each `pod-<nn>` at two managed devices per installed desk with no guest
service. Those defaults apply when the zone budget is first resolved and are
then frozen into the plan: growing `staff` from a frozen recipe fills the last
pod with desks but leaves its recorded device budget where it was, so raise the
budget explicitly if you want it to keep tracking desks. An office whose
customer declares no guest devices anywhere gets no guest VLAN, prefix or WLAN
at all.

## What is physically represented

Each managed office has one authored ground floor: a reception, up to four
twelve-desk staff pods, and a ground-floor equipment room holding every rack,
access pair, management switch, console server, distribution pair and carrier
edge. Copper routes stay inside an 80 m ceiling.

Access switches grow in pairs and hold each endpoint's reserved physical port as
demand grows, so hiring never reroutes an existing desk, radio or camera.
Endpoints remain single-homed. Every pod keeps a permanent reserved position, so
filling one and adding the next never renumbers the floor underneath.

Client segments per office are `management`, `staff`, `wireless`, `security` and
`guest` (only where visitor devices were declared), plus a `wan` segment that
addresses the two carrier handoffs and carries no client and no gateway SVI.
Each office attaches through the shared two-carrier edge grammar with a
purchased tier that covers its peak
after `reserve_fraction`. Segmentation expresses intended boundaries only — no
firewall policy, access control or compliance state is demonstrated.

Every office publishes a staff WLAN (WPA-Enterprise intent) and, where visitor
devices were declared, an open guest WLAN, both on `wlan0`. The provider's own
DNS and RADIUS inventory in the NOC serves those WLANs. That is the one
dependency that crosses from the provider to a customer, it is named in the WLAN
comments, and no customer ever depends on another customer's inventory. No
captive portal, authentication result, association or RF survey is represented.

## Ownership versus operation

This is the modeling claim worth walking through in a demo, and
`validate_msp.py` checks all of it independently of what any contract says.

**Owned by the customer.** Each customer is one NetBox tenant inside the
`<namespace> customers` tenant group, with its own segment routing contexts
(`<namespace>-cust-<key>-<segment>`). The office site, its rooms, racks,
equipment, VLANs, prefixes, addresses, WLANs and access circuits all carry that
customer's tenant. Nothing — no cable, segment, VLAN, prefix, address, routing
context or service — joins two customers, and the provider's own operations site
carries no customer tenancy.

**Operated by the provider.** Ownership and operation are separate facts on the
same records, using the machinery the estate already has rather than a new
concept:

- every operated network device — the access pair, the distribution pair, the
  carrier edges, the management switch and the console server — carries a direct
  technical assignment chosen by equipment role and actual tenant: the
  provider's own NOC duty desk for that account, in the provider's operations
  contact group. Endpoints and PDUs carry none; they are installed inventory,
  not operated equipment;
- the per-customer `management` segment carries the operated equipment's
  addresses, and each office record names that segment;
- the shared infrastructure owner and the carrier accounts for every office
  access circuit belong to the provider, not to the customers.

## Shared managed services

The NOC runs seven service families, each as complete two-replica groups
separated across compute racks:

| Service | Segment | One group covers | Listener |
| --- | --- | --- | --- |
| `monitoring` | applications | 1,500 installed managed endpoints | TCP/443 |
| `rmm` | applications | 800 installed managed endpoints | TCP/443 |
| `helpdesk` | applications | 8 contracted accounts | TCP/443 |
| `identity` | applications | 2,000 installed managed endpoints | TCP/443, RADIUS UDP/1812,1813 |
| `dns` | applications | 16 sites | TCP/53, UDP/53 |
| `inventory-db` | database | 40 managed offices | TCP/5432 |
| `backup` | backup | 24 managed offices | TCP/443 |

These thresholds are fictional planning assumptions, not ticket volume, agent
counts, licence entitlements, retention sizing or vendor performance claims.

## What is explicitly not asserted

- **No service level.** Nothing in this dataset records an SLA, a response
  target, an uptime commitment, a renewal date or a price. The only commercial
  facts present are each access circuit's purchased commit rate and the carrier
  account that holds it; the managed-service relationship itself has no terms.
- **No ticketing.** `helpdesk` is inventory for a service family. No ticket,
  queue, escalation policy, on-call rotation or workflow exists.
- **No RMM execution.** `rmm` is inventory for a service family. No agent is
  installed anywhere, no device is enrolled, no patch, script, policy or
  configuration is pushed, and no device posture is reported.
- **No remote access.** There is no modeled path of any kind between the NOC and
  a customer office: no VPN, tunnel, jump host, out-of-band network, monitoring
  session or management overlay. "Managed" is expressed by contacts, ownership
  and the local management segment — not by reachability.
- **No enforced isolation.** Customer separation here is modeled separation:
  distinct tenants, distinct address space, distinct routing contexts, no shared
  links. No firewall rule, ACL, route filter or policy enforces it, and no
  authorisation boundary or permission model is represented.
- **No inventory sync.** `inventory-db` is the provider's own asset service. It
  does not connect to, synchronise with or reconcile against this NetBox
  instance or any other system.
- **No authentication.** Identity includes RADIUS listener inventory for WLAN
  authentication intent. No authentication server, directory, realm or
  credential is configured, running or certified.
- **No measured anything.** Desks, radios, cameras, peaks and thresholds are
  authored design inputs. No traffic, RF coverage, association, utilisation or
  capacity measurement is represented.

## Growth and rebaseline

Appending customers, raising a customer's `offices` or `staff`, raising a
wireless zone budget, and adding `site_names` entries for new sites all grow in
place. Removing a customer, lowering `offices`, `staff` or a zone budget,
changing WAN tiers and renaming an existing site require a new baseline without
`--previous`. There is no acquisition, refresh, office-remodel or
customer-offboarding transition in this profile: no `design_mix`,
`site_designs` or `acquired_sites` key is accepted, and every site is modern.

`--kind loss-of-power-diversity` works on a generated MSP plan, like the other
profiles. See [scenarios](../docs/scenarios.md) for its boundaries.
