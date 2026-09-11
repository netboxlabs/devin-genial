# Provider backbone

`provider-backbone.toml` describes a regional private-L3 service: three PoPs,
a customer hub and two remote offices, and a NOC data center. Change demand in
the recipe and regenerate. The first three PoPs form a six-router cycle; every
new PoP adds a pair of routers and attaches them to free ports at two distinct
older PoPs. Existing circuits, ports, addresses and procurement records remain
reserved. No LLM runs in the generator.

```sh
python3 -m estates generate profiles/provider-backbone.toml --out build/provider-demo
python3 -m estates check build/provider-demo/plan.json
```

Use `--previous build/provider-demo/plan.json` with an expanded recipe to add
PoPs, customers, premises or wired desks. Keep the previous output intact and
write the expanded artifact to a new directory. A frozen plan must reproduce
exactly from its recipe and ledgers before ordinary growth can use it.

## Demand and supported boundaries

| Input | Meaning and bounds |
| --- | --- |
| `pops` | 3–64 keyed entries across at least three distinct metros; explicit metro: chicago, detroit, cleveland or milwaukee |
| `customers` | 1–256 private-L3 customers, each spanning at least two modeled PoPs |
| `sites` | Per-customer keyed PoP entries with `count` 1–12; combined customer and NOC attachments cannot exceed twelve per PoP |
| `lan_endpoints` | 1–12 wired office desks at each customer premises |
| `site_peak_mbps` | 1–800 Mbps of directed traffic from each non-hub premises toward its hub |
| `hub_pop` | Explicit hub location; ordinal 001 at that PoP is the hub premises |
| `hub_commit_mbps` | Fixed purchased tier from `wan_tiers_mbps`; must cover the sum of spoke demand after reserve |
| `noc_pop_a`, `noc_pop_b` | Two distinct PoPs for the NOC's physical handoffs; set these explicitly as in the example |
| `noc_peak_mbps` | 1–800 Mbps, subject to the local 1G handoff's reserve; excluded from backbone offered-load accounting |
| `address_pool` | Aligned RFC1918 /8 through /12; first /16 NOC, last /16 infrastructure, middle /24 site reservations |
| `ipv6_pool` | Optional common dual-stack pool; [operation and current qualification](../README.md#optional-ipv6). Absent means IPv4-only; toggling or changing it needs a new baseline |
| `reserve_fraction` | 0.1–0.4, exact decimal headroom; combined demands can fail even when every field is within its individual bounds |
| `asn_base` | Optional private 1024-ASN block aligned from 4200000000; default is namespace-derived. Global target conflict preflight is still required |
| `demo` | `baseline`, shared `loss-of-power-diversity`, or provider-only `provider-span-maintenance`; planning builds a healthy baseline, generation dispatches narrative intent |

Keys are lowercase, 1–20 characters, starting with a letter. PoP keys must also
remain unique after removing hyphens for device names. Composed customer/PoP
site-key collisions are rejected. Moving or removing a PoP, reducing demand,
changing a customer hub or traffic rate, renewing purchased bandwidth, moving
NOC attachments, or changing the address pool requires an explicit new baseline.
The operator's native name retains the namespace and supplied estate name. If
their combined display exceeds 87 characters, a readable prefix plus a digest
preserves distinct identities while leaving room for the native support-desk
suffix; the full name remains in comments and the recipe/report. Small-site VLAN
names use compact scoped facility codes so maximum accepted customer and PoP
keys fit native name limits without truncating canonical identity.
Adding the MX204 catalog changes the global hardware digest: preserved older
bank, DC, school and hospital plans need an explicit current-catalog baseline
before growth. Historical qualification remains tied to its original source.

## What is physically represented

Every PoP has two [MX204 routers](../catalog/README.md#provider-router), in
separate racks, with two installed source-backed AC PSU modules each. Each PE
has three enabled 100G ports: one local router-pair link and two finite transport
positions. The fourth 100G port remains present and disabled. Six 10G cages per
router are configured at 1G for customer/NOC handoffs; one 10G port serves local
management and one is reserved for external transit.

Inter-PoP circuits have actual A and Z site terminations, each physically cabled
to a catalog port. /31s belong to the two real interfaces. Transit circuits are
fixed at founder slot 0 PE-a and founder slot 1 PE-b on `xe-0/1/7`, with a 10G
handoff and commitment. Only their known local IP is created; the remote owner
is unknown and the whole /31 stays reserved. Separate carrier names do not
establish duct diversity. Per-PoP carrier diversity is not guaranteed: two spans
can use one carrier. The report counts actual inter-PoP span providers per PoP;
carrier-wide failure is outside the resilience checks. External-transit journals
name only the local physical handoff and the unknown remote network boundary.
Local cable lengths describe local patches, not
invented long-distance optical paths or certified transceiver selections.
The two PoP network racks occupy adjacent positions at (4,4,0) and (5.2,4,0)
metres. Where both rack ends are known, the patch uses the rounded-up Manhattan
distance plus three metres of service slack: five metres between those racks.
Same-rack links and circuit handoff tails use three-metre local patches;
the external circuit's geographic route remains unknown.

PE `lo0` /32 addresses are in-band management. `fxp0` is present, unaddressed and
uncabled. A management switch has its own /26 SVI and a console server; two
routed /31 links connect it to the PE data ports. Serial console cables are
separate from Ethernet. This is not an independent out-of-band network.
With optional IPv6, numbered router links gain /127s and `lo0` gains a /128
primary; LANs use /64s. Transit still has only its known local owner. These are
inventory additions qualified in the [pinned local provider example](../lab/README.md#current-v09-qualification),
not executed router configuration.

Each customer has a native tenant, private ASN, VRF with import/export route
target, operator account and private-L3 virtual circuit.
Account association to a customer is represented by its circuit/service account
reference and tenant reference; the pinned native/SDK ProviderAccount has no
tenant field. Customer and NOC account numbers have separate identity domains.
Native peer
terminations refer to virtual CE interfaces parented to actual physical WAN
ports. The physical access circuit has two site ends: CE copper patch and
provider optical patch. Customer LANs use one CE gateway, one local access
switch, a management /26, a clients /25 and fixed office desk positions. Direct
or full panel/outlet access channels use the same demand. Customer access is
single-homed. Wireless is omitted in this wired private-L3 service scope.

The NOC shares the existing rack-separated service DC builder. Identity and
provisioning use one synthetic two-replica group per 128 customer premises;
DNS and monitoring use one per 16 PoPs, minimum one each. These are authored
inventory budgets, not measured throughput. Actual listeners, VM addresses,
host/rack placement, fabric, management, serial access and power remain linked.

## Exact capacity and narrative scope

The capacity witness routes **directed customer spoke-to-hub** offered load by
stable shortest-hop choices in normal operation and after each single
inter-PoP span loss. It checks local pair and inter-PoP links against their
capacity with reserve independently in each full-duplex direction. Per-link
load figures show the larger of the two directed loads, never their sum.
Return traffic, arbitrary peer-to-peer traffic, NOC
traffic and external transit traffic are excluded. This is not a total
backbone-capacity guarantee. NOC peak only establishes local purchased handoff
headroom. Router and local-pair failures have a connectivity witness only, not
capacity or single-homed customer-availability guarantees.

No BGP sessions, MPLS labels, firewall policies, running routing configuration,
packet forwarding, public Internet service or application recovery are
executed. Private ASN and route-target inventory express intended routing
relationships. Use the common `loss-of-power-diversity` demo for a real NOC
service-host power-path change, exact expected findings and inverse restoration;
the provider-specific span-maintenance scenario below changes one inventory
status while retaining all physical records.

## Planned span maintenance

The scenario is implemented; its local execution scope is recorded below.
The [maintenance recipe](provider-maintenance.toml) uses three PoPs, Harbor
Logistics and Cedar Retail, IPv6, and passive office-access channels:

```sh
just generate profiles/provider-maintenance.toml build/provider-maintenance
just scenario-check build/provider-maintenance/scenario.json
```

Panel access uses the existing [local front-port bridge](../lab/README.md#opt-in-local-front-port-compatibility) on
the pinned NetBox 4.7 target; a new direct-mode baseline avoids that mapping
requirement. This is separate from qualifying the status transition itself.

It selects an active inter-PoP leased span carrying an actual customer-to-hub
path. Selection prefers the most rerouted premises, then the canonical span key,
and requires current maintenance traffic to remain reachable within directed
usable capacity. Harbor's spokes offer 50 Mbps each and Cedar's 30 Mbps each;
the 1G hub commitments are purchased limits, not offered traffic. This is a
light-load resilience discussion, not congestion or proof of customer uptime.

The parent `report.md` identifies the maintained Circuit, actual A/Z PE ports,
customer premises and hubs, alternate paths, directional load changes and
remaining headroom. It follows the real customer, operator, carrier and site
facilities assignments. Complete attribution stays in `scenario.json`; the
report uses bounded examples and reports truncation. Native cable traces still
show physical connections because this scenario does not unplug anything.

Only the selected Circuit's `status` changes from `active` to `offline`.
`baseline/` and `changed/` retain the same canonical keys, A/Z terms, cables, IPs,
modules, installed power reservations, accounts, contacts and dated notes.
The changed snapshot is `expected-maintenance verified` after scenario checking.
Ordinary baseline validation intentionally fails on the unavailable span and
any lost protection against a further failure; this does not mean its current
modeled customer paths are unreachable. Never waive unrelated findings.
The selected span and actual topology determine further-failure protection.
Independent five-PoP examples retain all checked margin for one span and lose
two witnesses for another in the same mesh; the report reflects each result.

From an existing healthy plan, run `just span-scenario <plan.json> <new-output>`;
an optional third argument pins the canonical span key. An unused, inactive,
customer or transit circuit is not a maintenance subject. Saved verification
rechecks the pinned subject and every wire file; it does not select again.
Growth may change a newly created scenario's default selection. Keep the old
scenario and use an explicit key when continuing the same customer story.

Offline restoration changes that one status back to its original value and
must reproduce the exact baseline bytes. The final local artifact also passed
actual baseline/repeat, changed/repeat and restore/repeat through Diode with
unchanged IDs and complete readback on the same database;
[commands and receipts](../lab/README.md#provider-status-sequence) define the
pinned scope. Generated scenario flags remain offline claims, separate from
that live receipt. Route tables are not executed IGP/BGP convergence; no other
stack or rewired-snapshot rollback is qualified.

Shared contacts and immutable journals attach support responsibilities and
dated planning context to the actual estate. Procurement dates describe modeled
records, never a completed installation or acceptance test. Physical A/Z
endpoints and provider ownership drive the circuit narrative. See the generated
report and coverage artifact for the current output, and the separately saved
live receipts for target qualification.
