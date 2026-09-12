# Hospital and clinic composition

`hospital-clinics.toml` requests a health system with one hospital, two wards,
two unequal outpatient clinics and a central services DC. All premises, installed
capacity, resource budgets and traffic peaks are synthetic planning inputs.

```sh
python3 -m estates plan profiles/hospital-clinics.toml
python3 -m estates generate profiles/hospital-clinics.toml --out build/hospital-demo
python3 -m estates check build/hospital-demo/plan.json
```

[`hospital-wireless.toml`](hospital-wireless.toml) adds explicit managed/guest
budgets, patch panels and IPv6 for the wireless qualification example.

The generated guide explains actual care-unit, access, service and power
relationships. Generation is offline; a generated artifact alone does not prove
live NetBox acceptance. Use the shared Diode and local qualification workflow
in the [loading guide](../docs/loading.md) for a live target.
The optional top-level [`ipv6_pool`](../docs/modeling.md#optional-ipv6) field uses the
same dual-stack policy as the other four profiles: /48 facilities and /64 LANs,
with existing interface, primary and service references. Absence means IPv4-only;
enabling or changing it needs a new baseline. The hospital-wireless example passed
[pinned local initial/repeat qualification](../lab/README.md#current-v09-qualification).

## Demand and physical limits

Hospitals have stable keys, 1–7 keyed wards, 0–48 administrative desks and 0–8
imaging rooms. Each ward requests 4–16 bed stations and 2–8 clinical workstations.
The profile supports 1–8 hospitals and 0–64 clinics. Clinics request 1–24 exam
rooms, 0–24 administrative desks and 0–4 imaging rooms. Omitted hospital wards
default to one `medical` ward with eight bed stations and four workstations.
Omitted hospital and clinic lists use the example estate; use `clinics=[]` for
an explicit estate without clinics.

Each ward gets a permanent floor above ground, a local IDF, two-bed patient
rooms, a nurse station and a corridor. Clinics fit eight exam rooms per floor.
Ground floors hold reception, administrative pods of twelve desks and imaging
rooms. Permanent floor/room/endpoint keys retain existing physical and IP
allocations when demand grows. Copper routes use authored room coordinates,
local equipment rooms and an 80-metre planning ceiling; these are not surveyed
buildings or cabling certifications.
Seven ward floors plus ground use the shared eight management-uplink positions;
an eighth ward requires a reviewed expansion of that physical port pool.

Each bed station has a reference networked monitor. Every imaging room has a
reference modality and a diagnostic workstation. Every clinic exam room has a
clinical workstation. Reference medical devices use fictional NIC endpoint
hardware; they have no clinical function or medical certification. Bed counts
do not imply daily patient volume, and installed desks do not imply headcount.

Access points retain two coverage anchors per ward, one per four exam rooms,
one per administrative pod, one per imaging room and reception. Explicit local
managed/guest device budgets can add APs within those zones. Managed WLANs retain
enterprise authentication intent and RADIUS service inventory. Radio assignments
and mounting coordinates are authored examples, not an RF survey or measured
wireless capacity. Two cameras occupy each floor's
corridor or reception; patient, examination and imaging rooms have no cameras.

Clinical workstations, medical devices, imaging devices, administrative/staff
clients, AP management, security and infrastructure management use distinct
segments. Inventory segmentation does not execute firewall policy or establish
clinical safety, regulatory compliance or application availability.

Per-facility WAN peak is explicit, 1–800 Mbps, and must fit the 1-Gbps handoff
after the configured reserve. The centralized DC accepts at most 16,000 Mbps
combined declared peak. The allocator additionally checks real access-port,
management, rack, power, host and address capacity; the upper field limits are
not a promise that every combination of maxima fits. The default /12 pool holds
sixteen /16 site reservations including the DC; choose a larger aligned private
pool before adding more sites. Unsupported demands fail rather than inventing
ports, reducing reserve or pretending local caches solve WAN capacity.

## Local wireless demand

Each hospital or clinic accepts one optional `wireless` map. For example, under
the existing `[[hospitals]]` entry containing ward `medical-a`:

```toml
[hospitals.wireless.ward-medical-a]
managed = 80
guest = 16

[hospitals.wireless.reception]
managed = 8
guest = 24
```

These are planned concurrent **devices**, not bed occupancy, patient volume,
staff headcount or observed associations. Existing wired medical devices remain
wired. Each field must be an integer from 0 to 128 and the combined zone budget
must not exceed 128. Unknown or nonexistent zones are rejected.

| Zone key | Default managed devices | Default guests | Coverage AP minimum |
| --- | ---: | ---: | ---: |
| `ward-<ward-key>` | 24 | 0 | 2 |
| Clinic `exam-001`, `exam-002`, etc. | Four per existing exam room in each permanent group of up to four rooms | 0 | 1 |
| `admin-01`, etc. | Desks actually installed in that twelve-desk pod | 0 | 1 |
| `imaging-01`, etc. | 4 | 0 | 1 |
| `reception` | 4 | 0 | 1 |

The numerical defaults are authored planning allowances. All become explicit in
the frozen recipe. APs grow at an authored 32-device-per-AP threshold above the
coverage minimum, up to four fixed mounts per zone; the ward example requires
three APs. These counts do not claim a hardware throughput or RF capacity rating.
Two exam pods sharing a corridor retain distinct mounting lanes, including both
original AP anchors. No client device, association or per-client IP/MAC rows are
created.

Nonzero guest demand enables that facility's open guest WLAN alongside staff on
`wlan0`. The second radio stays spare. Guest has its own VLAN 130, /20 prefix,
VRF, two gateway SVIs and actual AP/upstream trunk membership. Managed WLANs
reference the modeled DNS/RADIUS services; open guest intent does not claim a
portal, credentials or authentication result. Inventory segmentation does not
execute isolation or routing. Planned guests must fit the 4,084-address client
envelope. Optional IPv6 adds the guest prefix and gateway companions through the
shared address-plan pass.

APs use the shared reference Type 2 envelope, reserving 30,000 mW at their actual
PSE ports. Access growth accounts separately for copper-port headroom and
whole-switch/supply-loss PoE budgets. A power-constrained pair grows another
pair without moving previous endpoints; later wired equipment can still use
unused copper ports on an earlier switch. The final hospital-wireless artifact
passed [pinned local initial/repeat and native UI qualification](../lab/README.md#current-v09-qualification),
including its AP power path, staff WLAN and biomedical support. RF, measured
consumption and other target stacks remain outside that evidence.

## Shared services

Each service group has two complete VM replicas on separate compute racks.
The following thresholds and budgets are authored demo policy, not medical
performance, storage-retention or redundancy requirements. Every workload has
at least one group, including the imaging archive when no modality is installed.

| Workload | One group per | vCPU / memory MB / disk MB per replica | Modeled listeners |
| --- | --- | --- | --- |
| Identity | 250 installed workstations | 4 / 8192 / 100000 | TCP443; UDP1812,1813 |
| DNS | 16 facilities | 2 / 4096 / 40000 | TCP53; UDP53 |
| Clinical records | 100 nurse/exam/diagnostic workstations | 8 / 16384 / 200000 | TCP443 |
| Imaging archive | 16 imaging rooms | 8 / 32768 / 2000000 | TCP11112 |
| Monitoring | 500 campus endpoints | 4 / 16384 / 200000 | TCP443 |

Services occupy the applications segment and include listener addresses, VM
disks, hosts, cluster membership, power paths and service-team contacts.
TCP11112 is a modeled DICOM transport convention from the pinned
[DICOM PS3.8 2018b §9.1.1](https://dicom.nema.org/medical/Dicom/2018b/output/pdf/part08.pdf).
No clinical application, DICOM transfer, authentication, routing, replication
or recovery is executed by this generator.

## Growing the estate

Increase bed stations, nurse desks, exam rooms, administrative desks and imaging
rooms; increase local managed/guest budgets or add wards/sites with new stable
keys. New wireless zones receive defaults; existing resolved budgets remain
explicit. Generate with `--previous` set
to the intact previous `plan.json`. The previous snapshot must reproduce from
its frozen recipe and ledgers before new allocation. Adding a ward whose key
sorts before existing wards still appends its floor and preserves existing
care-unit paths. Enabling guest preserves earlier group, channel, port and AP
identities. Removing/renaming sites or wards, reducing wireless or other demand, changing
WAN purchases or changing the shared immutable profile settings requires a new
baseline. The shared power-diversity story selects a real service-DC host; it
does not claim to simulate clinical impact or execute a live rewire.
