# School district composition

`school-district.toml` requests two schools and a shared services DC. Classrooms,
enrollment, installed wired seats and planned wireless device concurrency are
separate inputs. Wireless demand creates connected APs, never fabricated client
devices, associations or cables.

```sh
just plan profiles/school-district.toml
just generate profiles/school-district.toml build/school-demo
just verify build/school-demo/plan.json
```

[`school-wireless.toml`](school-wireless.toml) adds explicit managed/guest zones,
patch panels and IPv6 for the wireless qualification example.

Each school has a stable key, 1–32 classrooms, 1–36 students per classroom,
0–12 wired seats per classroom, 0–48 administrative desks and 0–36 shared lab
seats. Wired seats must fit enrollment; lab seats do not add students. WAN peak
is explicitly 1–800 Mbps and must fit the selected handoff after reserve.

Eight classrooms occupy each permanent floor. Administration uses twelve-desk
pods; the shared computer lab is a separate room. Endpoints connect through
their floor closet, bounded catalog ports, optional patch panels and authored
copper routes of at most 80 metres. Paired access switches preserve each
endpoint's physical port as rooms and wireless demand grow.

## Local wireless demand

Add a `wireless` table under a particular `[[schools]]` entry:

```toml
[schools.wireless.classroom-001]
managed = 40
guest = 8
```

The fields count planned concurrent **devices**, not people, measured
associations or throughput. Both must be integers from 0 to 128, with a combined
maximum of 128 per zone. Unknown zone names are rejected. Legal keys follow the
existing rooms:

| Zone | Default managed devices | Default guests |
| --- | ---: | ---: |
| `classroom-001`, etc. | Students per classroom minus its wired seats, plus one teaching device | 0 |
| `admin-01`, etc. | Desks actually installed in that twelve-desk pod | 0 |
| `computer-lab`, when present | 0 | 0 |

All defaults become explicit in the frozen recipe. Classroom budgets do not
shrink when shared lab seats increase. Local peaks are provisioned independently;
their sum is a planning envelope, not a count of unique people.

Every zone retains one coverage AP. More planned devices add APs at an authored
32-device-per-AP threshold, up to four fixed mounts per room. The example above
requires two APs. Existing AP names, mounting coordinates, IPs, copper paths and
channels remain stable; new APs append local ordinal names. These thresholds and
coordinates are equipment planning rules, not RF survey or throughput ratings.

Any nonzero guest demand creates a local guest VLAN 130, its separate /20 and
VRF, two gateway SVIs, and a guest WLAN on `wlan0` alongside staff. Students
remain on `wlan1`. AP copper and upstream trunks carry the required VLANs while
AP management keeps its native VLAN. Managed WLANs have RADIUS service intent;
the guest WLAN uses open authentication, with no portal, guest credentials or
authentication outcome modeled. Segmentation does not establish enforced
isolation or cross-VRF application reachability. No wireless-client IP/MAC rows
are created; planned guests must fit the 4,084-address client envelope.

AP power follows the shared reference Type 2 policy: 30,000 mW reserved at the
actual PSE port, including the selected switch's total and supply-loss planning
limits. Copper-port headroom is separate from the PoE budget. If another AP
cannot fit the existing pair's power allowance, a new local pair is added while
unused copper ports remain available for later wired endpoints. Hardware,
38-switch distribution, management, rack and object ceilings still apply.

## Growth and qualification

Use `--previous` with an intact frozen plan to add rooms/sites or increase local
managed/guest counts. Newly added zones receive defaults; existing resolved
counts remain explicit. Removing zones, reducing demand, changing classroom
design, renewing WAN purchases or changing shared immutable settings requires a
new baseline. Guest can be enabled during growth without renaming WLAN groups
or moving earlier ports. The optional top-level
[`ipv6_pool`](../docs/modeling.md#optional-ipv6) adds prefixes and existing gateway/
interface addresses under the shared baseline policy.

The final school-wireless artifact passed [pinned local initial/repeat and
native UI qualification](../lab/README.md#current-v09-qualification), including
actual AP power paths, supply modules, guest segmentation and support. Offline
generation alone does not establish live acceptance on another target; use the
shared Diode/readback workflow for the intended stack and recipe.
