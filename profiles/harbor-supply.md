# Harbor Supply: peak-season readiness

Harbor Supply is a fictional regional distributor. Its infrastructure lead is
preparing the inventory and ordering platform for peak season and a maintenance
window. The facilities team can identify power panels; the application team can
name services. The demo connects those views so they can identify infrastructure
dependencies and decide what needs inspection before the window.

**Opening question:** “Both server power cords are connected. How would you prove
they do not share the same upstream failure domain—and identify the services
exposed if they do?”

## Discovery and scope

The intended audience is an infrastructure lead, a facilities engineer, and an
application owner. Before adapting this to a real prospect, establish their
critical services, authoritative inventory sources, required failure domains,
actual VM sizing, and what decision they need to make from the demonstration.

For this example, those answers are explicit fictional assumptions in
[harbor-supply.toml](harbor-supply.toml). We model the distributor's two data
centers in Chicago and Detroit. Warehouse buildings, scanners, shipment traffic,
and application-to-application dependencies are outside this estate. Service
names provide business context; they do not create an application dependency map.

| Workload | Synthetic groups per DC | Replicas per group | Placement policy | Listeners |
| --- | ---: | ---: | --- | --- |
| Inventory API | 12 | 2 | Separate hosts and racks | TCP 443 and metrics 9090 |
| Orders database | 6 | 2 | Separate hosts and racks | TCP 5432 |
| Warehouse event service | 4 | 3 | Three hosts in three racks | TCP 5671 |
| Release artifact repository | 2 | 2 | Separate hosts | TCP 443 |

Each group represents a synthetic shard, with the complete declared resources
assigned to each VM replica. Demand is repeated at each DC; cross-site
replication is not modeled. The 20% resource reserve and 2,400-Mbps WAN peak per
site are sizing inputs, not measured demand or an application performance claim.

## Generate and qualify

From the repository's development shell:

```sh
just plan profiles/harbor-supply.toml
just generate profiles/harbor-supply.toml build/harbor-supply
just verify build/harbor-supply/baseline/plan.json
just scenario-check build/harbor-supply/scenario.json
devenv --profile diode shell -- just sdk-check build/harbor-supply/baseline/diode
devenv --profile diode shell -- just sdk-check build/harbor-supply/changed/diode
```

Choose a new output directory on subsequent runs; existing artifacts are
preserved. This recipe uses the shared enterprise generator and graph-selected
power scenario. It contains no hand-authored devices, interfaces, addresses,
cables, or defect subject. Changing the demand regenerates those relationships.

The initial generated estate contains 3,154 canonical objects: two sites, ten
racks, 72 devices (including 18 compute hosts), 104 VMs, 152 service listeners,
1,364 device interfaces, 296 cables, twelve circuits, twenty power feeds, and
four power panels. The generated `baseline/report.md` supplies the complete
inventory, resource budgets, physical paths, placement and addressing tables.
Use its results as the authority after changing the recipe.

`baseline/` and `changed/` each contain a frozen graph and complete Diode export.
The parent `report.md` is the answer key; `scenario.json` carries the selected
subject, before/after paths, affected services, exact findings, and inverse change.

The initial healthy Harbor artifact is now qualified on the pinned local target:
fresh reset and native Diode load, strict readback of all 3,154 objects, identical
replay with unchanged IDs, and sixteen representative native cable traces.
[Live receipts and browser walkthrough](../build/harbor-supply-live/walkthrough.md)
are separate from the offline checks. The changed and growth snapshots have not
been loaded.

## Ten-minute walkthrough

1. **Start with the decision.** Ask the opening question. Explain that success
   means identifying a physical dependency and its modeled service consumers,
   with enough evidence to direct a maintenance inspection.
2. **Establish the healthy design.** Open `harbor-supply-dc-01` and its Data hall
   rack elevations after loading the baseline. Show network racks N01/N02 and
   compute racks C01/C02/C03. Follow `dc01-inventory-api01`, its TCP 443 listener
   and `10.96.96.10/20` address to physical host `dc01-inventory-api-h01` in C01.
   Follow the group's other replica to its different host and rack.
3. **Trace the physical dependencies.** On that host, follow `eth0` and `eth1` to
   the two leaf switches. Then follow PSU1 and PSU2 through their actual PDU
   outlets and feeds to panels A and B. Two labels alone are insufficient;
   the relationships are the evidence.
4. **Reveal the inspection finding.** Use the generated scenario report, or a
   separately loaded changed snapshot. Both cords are still connected, but
   PSU2 now uses Outlet10 on `dc01-pdu-cp01-a`. Both paths converge on that PDU,
   its feed, and panel A. Ask the application owner to identify the exposed
   consumers before opening the answer key.
5. **Translate the finding.** The selected host carries twelve inventory API
   VMs and 24 bound listeners: API and metrics on each VM. Its peer replicas
   remain on another host/rack. These are exposed inventory dependencies;
   they do not establish twelve service outages or successful runtime failover.
6. **Close with a decision.** For the fictional maintenance plan, request a
   physical inspection and correction of the misplaced cord, then reconcile
   the observed state. Show that the scenario's inverse cable change restores
   the exact healthy graph offline. Agree which real inventory sources and
   acceptance checks would be needed for a customer POC.

For another useful discovery question, inspect `release-artifacts`. Its two
replica hosts share C01, which satisfies its explicit **host** policy. A rack
loss can remove both modeled replicas. That is an accepted policy tradeoff in
this baseline, separate from the planted wiring defect. Ask whether the
customer wants a stronger requirement before changing the recipe.

## Success criteria and operating boundary

- Healthy baseline: zero independent validation findings.
- Changed snapshot: exactly `power-redundancy` and `dc-power-diversity` on
  `device/dc-01/inventory-api-host-01`; both describe the same single cord move.
- Every other object and all allocation ledgers remain unchanged. The inverse
  cable replacement restores the exact baseline; scenario checking also binds
  both exported Diode packages to their checked graphs.
- Ordinary `just verify build/harbor-supply/changed/plan.json` must fail.
  Use the parent `scenario-check` to accept precisely the known defect.

Use the [local target procedure](../lab/README.md) for ingestion and separate
strict live readback. **Load the snapshots into separate fresh targets.** Diode
upserts do not retire the replaced cable identity; do not load one snapshot over
the other. Generator/SDK checks alone do not establish live acceptance.

This demo inspects modeled inventory. It does not execute application failover,
cross-site recovery, product drift detection, branching, approval, or a live
rewiring/rollback workflow. Separate panels do not prove separate utility supplies.

## Adapt the customer's demand

Copy the recipe, change the declared demand, and pass the healthy baseline as
the third argument to `just generate` to preserve prior reservations. For a
peak-season capacity exercise, increase inventory API groups from 12 to 18,
orders database groups from 6 to 8, and WAN peak from 2,400 to 3,200 Mbps.
The resulting report explains the added equipment and capacity. Keep resource
and replica policies unchanged for ordinary growth; changing a workload from
host to rack separation requires a new baseline.

This exercise was generated with the healthy baseline's reservations. It grew
from 3,154 to 3,680 objects, 104 to 136 VMs, 18 to 26 compute hosts, ten to twelve
racks, and twelve to sixteen circuits. All existing devices, VMs, VM interfaces,
services, IP addresses, racks, and cables remained byte-for-byte unchanged;
36 existing device-interface records changed as capacity was attached. No object
identities were removed. The two healthy graphs passed independent validation.

Use the new generated scenario's answer key after any recipe change. Subjects
and paths are selected from the resulting graph, never assumed from this script.
