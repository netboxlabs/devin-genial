# Hardware catalog

`hardware.json` is a small, versioned inventory consumed by the estate builder.
Catalog **0.10** adds the selected optics and power policy described below.
Phase 5 generation, independent checks and all-five SDK exports are implemented
and tested. GOAL.md records review and pinned-target qualification separately;
catalog entries alone do not prove installed inventory.
PoE allocation and independent power/path checks passed scoped offline review
and final representative initial/repeat qualification on the pinned local target;
[the lab guide](../lab/README.md#current-v09-qualification) records exact scope.
The new catalog digest requires a fresh baseline; phase 4 and older saved plans
retain their historical catalog.
The five named vendor models preserve the interface names, types, management
flags, rack height, and depth classification from the community device-type
library at commit `72cc49fbb445f1e2f310d3b8dfef55e12d0b7138`. Each source entry
records its immutable URL, SHA-256, and CC0-1.0 license. The upstream license is
included in [LICENSE-upstream](LICENSE-upstream). Console ports also preserve the pinned source names/types. Images, fans,
and unused source fields remain outside this catalog's scope.

| Alias | Model and interface inventory | Power configuration |
| --- | --- | --- |
| `access` | Cisco Catalyst 9200L-24P-4X: `GigabitEthernet1/0/1`–`24` at 1G; `TenGigabitEthernet1/1/1`–`4` at 10G; `GigabitEthernet0/0` management; `StackPort1/1` and `1/2` | Two installed PWR-C5-600WAC supplies; `PS-1`, `PS-2`, C16 |
| `inherited-access` | Juniper EX3300-24P: `ge-0/0/0`–`23` at 1G; four uplink cages represented as `xe-0/1/0`–`3` at 10G; `me0` management at 1G; 1U | One fixed internal supply; `PSU0`, C14; no external redundant power system |
| `leaf` | Arista DCS-7050SX3-48C8-F: `Ethernet1`–`48` at 10G; `Ethernet49/1`–`56/1` at 100G; `Management1` at 1G | Two installed PWR-511-AC-RED supplies; `0`, `1`, C14 |
| `core` | Arista DCS-7060CX-32S-F: `Ethernet1/1`–`32/1` at 100G; `Ethernet33`, `Ethernet34` at 10G; `Management1` at 1G | Two installed PWR-500AC-F supplies; `0`, `1`, C14 |
| `edge` | Fortinet FortiGate 100F: full upstream inventory including `x1`, `x2` at 10G and `mgmt` at 1G | Source fixed inlets `PS1`, `PS2`, C14 |

The `access` model also serves management-switch roles; hardware is not
duplicated for a different role. The Fortinet source selects the SFP personality
for shared-media `port17`–`port20`; the catalog does not add duplicate copper
ports. No breakout interfaces or automatic speed negotiation are assumed.

Both access-switch aliases have ordered `access_ports` and `uplink_ports` lists
of interface names. Builders use these attachment maps instead of inferring a
vendor's numbering scheme. The management port is the interface marked
`mgmt_only`; it is not available for access or uplink allocation.

The Juniper source lists both `ge-0/1/*` and `xe-0/1/*` names for four shared
uplink cages. This catalog chooses the 10G personality and excludes the four
alternative 1G names so each physical port appears once. It explicitly models
all four as standalone network ports, including ports 2 and 3, which default to
Virtual Chassis ports. That configuration option is documented in the
[EX3300 datasheet](https://www.juniper.net/assets/br/pt/local/pdf/datasheets/1000389-en.pdf).
The [Juniper power-system guide](https://www.juniper.net/documentation/us/en/software/junos/high-availability/topics/topic-map/redundant-power-system-understanding.html)
confirms the single fixed internal supply. The optional external redundant power
system is not installed in this modeled configuration. `inherited-access` is a
fictional procurement-story label, with no vendor support or end-of-life claim.

The Cisco and Arista chassis sources define PSU **bays**, not fixed power
inlets. Their catalog entries describe explicitly populated configurations.
`configured_modules` preserves the bay, position, installed module, and resulting
port name. The `power_ports` list expands the upstream module's naming template
using that position. The equipment builder installs matching module types/modules into the source
bays and attaches these existing power ports to them. Module types share a
source-linked inventory profile and explicit bay form factors. No component
templates are generated or replicated.

The matching PSU families are listed in the [Cisco hardware guide](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9200/hardware/install/b-c9200-hig/product_overview.html),
[Arista 7050 guide](https://www.arista.com/en/qsg-7050-series-1ru-gen3/7050-series-1ru-gen3-overview),
and [Arista 7060 guide](https://www.arista.com/en/qsg-7060-series-1ru-gen3/7060-series-1ru-gen3-overview).
The exact inlet types come from the pinned module definitions linked in the
JSON. Populating both bays is a design choice, not a claim that every sold
chassis includes two supplies. Electrical load, PoE budgets, and cord connector
selection are not certified by this inventory.

## Fictional reference equipment

The remaining aliases are original example designs under manufacturer
`Devin Reference Designs`. Their dimensions, port counts, and supplies are
explicit assumptions; they do not describe any vendor product.
NetBox model labels use `Reference ...`; ordinary equipment and service text
uses operational wording. This catalog owns the original-design provenance.
The final v0.8 label revision changes the catalog fingerprint: build a new
baseline instead of growing or replaying an intermediate v0.8 artifact.

| Alias | Declared reference design |
| --- | --- |
| `server` | 1U; `eth0`, `eth1` at 10G; `mgmt0` at 1G; C14 `PSU1`, `PSU2` |
| `patch-panel` | Passive 1U panel; 24 paired 8P8C front/rear positions, including unused positions; no Ethernet interfaces or mains inlet |
| `wall-outlet` | Non-racked single-position 8P8C outlet; rear horizontal termination and front endpoint patch connection |
| `pdu` | Vertical 0U; C20 `Input`; 12 C13 outlets named `Outlet1`–`Outlet12`; no Ethernet management |
| `endpoint`, `atm` | Non-rack-mounted example appliances; 1G `eth0`; C14 `Input` |
| `ap` | Non-rack-mounted reference Type 2 PoE access point; 1G `eth0`; two 5 GHz 802.11ax radios, `wlan0` and `wlan1`; no separate mains inlet |
| `console-server` | 1U; 48 RJ45 asynchronous serial ports, separate 1G management, two C14 supplies |
| `liquid-chassis` | 2U parent enclosure; one blade bay, coolant intake/distribution, 1G management, two C14 supplies |
| `liquid-blade` | 0U child device; enclosure-powered, dedicated 1G management, cold-plate intake; fictional diagnostic device context |

PDU `power_outlets` are explicit catalog rows, so capacity can be checked rather
than inferred. A 0U value means the device consumes no rack-unit position; it
does not establish a device's physical placement or mounting method. The AP's
PoE source and two 5 GHz radios are explicit reference-design assumptions.
`wlan0` serves WLANs; `wlan1` can serve a second WLAN or the bank's separate
routed diagnostic link. The declared Type 2 power envelope is described below;
RF performance remains unqualified. Generated-estate PoE allocation and independent
path/supply checks have offline and SDK evidence; pinned-target delivery
qualification remains pending in GOAL.md.

Version 0.3 adds the outlet and explicit passive inventories. Electrical planning
allowances live in `estates/blocks.py`, separately from this hardware catalog:
they are fictional per-device budgets, not verified vendor consumption or PSU
ratings. Existing vendor definitions remain unchanged.

The physical enrichment also connects source RJ45 console ports to a same-room
console server; Cisco USB console ports and later-created management-switch
consoles remain present and spare. Two auxiliary Cisco access switches per DC
form a service Virtual Chassis using both catalog StackWise ports. This is not
an assertion that the existing Arista MLAG pairs are stacks.

Each DC has one explicitly fictional blade enclosure and its child. A native
CoolingFeed represents the complete supply/return loop from a 4 kW laboratory
chiller to the rack, with 1 kW reserved capacity and 12 L/min rated flow. The
component chain is acyclic: enclosure intake → distribution outflow → blade
intake. The blade cold plate and its coupling are nested, component-linked
inventory records. No electrical Cable record represents a coolant hose.
The 400 W chassis planning allowance includes its blade; it is not a vendor
specification. These demonstration devices receive ordinary management and
rack power, without claiming a production application fabric.

## PoE planning policy

Phase 4 added catalog inputs for the shared power allocator and independent
validator. Its offline and SDK checks passed; pinned-target initial/repeat
qualification remains pending. Declaring a port as PSE or PD alone does not
prove that its generated copper path supplies power.

The existing `ap` remains **Reference PoE access point** under Devin Reference
Designs. `poe_pd` names its `eth0` input, requires
`type2-ieee802.3at`, declares `max_input_mw: 25500`, and reserves
`pse_reservation_mw: 30000`. `policy_source: reference-poe-v1` identifies these
as authored limits. The reservation is at the supplying switch; it is neither
constant AP consumption nor a second load to add at the rack. Juniper's
[PoE tables 2–3](https://www.juniper.net/documentation/us/en/software/junos/poe/topics/concept/poe-overview.html)
distinguish a 30 W Type 2 PSE allocation from up to 25.5 W at the PD.

`radio_bands` explicitly maps both `wlan0` and `wlan1` to `5g`. These are
reference radios, with no Cisco product, country approval, measured RF or
interoperability certification implied. Existing interface names and the bank
diagnostic remain intact; this catalog addition creates no guest WLAN,
controller, third radio, injector or AP mains inlet.

Each access-switch model's `poe_pse` applies only to the existing ordered
`access_ports` inventory. Source-backed limits are separate from authored
allocation policy:

| Model | Per-port maximum | `budget_by_active_supplies_mw` | Actual supply requirement | Authored `planning_supply_losses` |
| --- | --- | --- | --- | --- |
| `access` | 30,000 mW | `0: 0`, `1: 370000`, `2: 740000` | `supply_model: PWR-C5-600WAC` in the installed bays | 1 |
| `inherited-access` | 30,000 mW | `0: 0`, `1: 405000` | `fixed_supply_port: PSU0`; no RPS | 0 |

The Cisco facts use `budget_source: cisco-psu-compatibility`, now covering the
[hardware guide's PoE ports and Table 4](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9200/hardware/install/b-c9200-hig/product_overview.html).
The two-supply total is additionally limited by the 24 physical ports to
720 W. The Juniper facts use `budget_source: juniper-ex3300-poe`, grounded in
the [EX3300-24P budget table](https://www.juniper.net/documentation/us/en/software/junos/poe/topics/concept/poe-overview.html).
Its 405 W PoE budget differs from its 550 W PSU rating. Removing its sole
powered supply leaves zero delivery capacity; no single-supply survival is
promised. Twelve 30 W reservations fit the Cisco one-supply budget, and
thirteen fit the EX3300 normal budget. Losing one Cisco supply with 360 W
reserved loses redundancy margin without itself exceeding remaining delivery
capacity. These calculations are policy limits, not live outage observations.

Both PSE entries declare `type: type2-ieee802.3at`,
`per_port_max_mw: 30000`, and `policy_source: reference-poe-v1`.
The common `upstream_ac_allowance_multiplier: "1.25"` is a decimal string
for exact arithmetic. It is an authored conservative planning multiplier,
not a vendor efficiency value. The intended allocator adds
`ceil(PSE reservation mW × 1.25 / 1000)` watts once to the existing switch
chassis allowance, then checks its real supply/feed paths. AC input planning,
PSE output reservation, PD input and PSU output ratings must remain distinct.
Power demand must consume its own budget without reducing available wired-only
ports, relocating existing endpoints or claiming RF coverage survives a fault.

Native `poe_mode`/`poe_type` attributes occur only on AP `eth0` (`pd`) and
the two switch models' actual access ports (`pse`), all using the declared
Type 2 value. Management/uplink/stacking interfaces and AP radios have no PoE
attributes. These spellings exist in the pinned
[NetBox 4.7 choices](https://raw.githubusercontent.com/netbox-community/netbox/v4.7.0/netbox/dcim/choices.py);
their presence here is not a claim of completed native round-trip testing.

## Installed optics policy

The top-level `optics.parts` map defines nine selected parts. Each stable part ID
records manufacturer/model, form factor, optical protocol, medium, connector,
rate in **kbps**, reach in metres, power reservation in integer **mW**, source
IDs and an explicit `compatible_interfaces` map from hardware alias to existing
port names. Host compatibility, physical cage shape and configured link rate
are separate obligations. Manufacturer participates in identity: Cisco and
Arista both sell a part named `SFP-10G-LR`.

| Part ID | Manufacturer / model | Selected host cages and mode | Reserved mW per module/end |
| --- | --- | --- | ---: |
| `cisco-10g-lr` | Cisco `SFP-10G-LR` | `access` fixed `TenGigabitEthernet1/1/1–4`, 10G | 1,000 |
| `juniper-10g-lr` | Juniper `EX-SFP-10GE-LR` | `inherited-access` `xe-0/1/0–3`; `provider-edge` `xe-0/1/0–7`, 10G | 1,000 |
| `arista-10g-lr` | Arista `SFP-10G-LR` | `leaf` `Ethernet1–48`, 10G | 2,000 authored |
| `arista-100g-lr4` | Arista `QSFP-100G-LR4` | `leaf` `Ethernet49/1–56/1`; `core` `Ethernet1/1–32/1`, 100G | 4,500 |
| `fortinet-10g-lr` | Fortinet `FN-TRAN-SFP+LR` | `edge` `x1/x2`, 10G | 1,000 authored |
| `juniper-1g-lx` | Juniper `SFP-1GE-LX` | `provider-edge` `xe-0/1/0–7` configured **1G** | 1,000 |
| `juniper-100g-lr4` | Juniper `JNP-QSFP-100G-LR4` | `provider-edge` enabled `et-0/0/0–2`, 100G | 3,500 |
| `reference-10g-lr` | Devin Reference Designs `Reference 10G-LR transceiver` | `server` `eth0/eth1`, 10G | 2,000 authored |
| `arista-100g-aoc-3m` | Arista `AOC-Q-Q-100G-3M` | `leaf` QSFP28 cages; existing peer uses `Ethernet49/1`, 100G | 3,500 authored per captive end |

The Cisco [TMG lookup](https://tmgmatrix.cisco.com/) and its fixed-onboard-uplink
note are archived JSON; the selected optic requires at least IOS XE 16.9.2.
Juniper's [EX3300 ordering table](https://www.juniper.net/assets/br/pt/local/pdf/datasheets/1000389-en.pdf)
and exact [MX204 HCT](https://apps.juniper.net/hct/product/MX204/100001) establish
the selected host fits. MX204 native 10G LR, native 1G LX and 100G LR4 first
appear at Junos 17.4R1, 18.1R1 and 18.2R1 respectively. The fourth 100G cage
remains disabled under the existing port mode. No QSA adapter, breakout, fixed
copper optic or inferred automatic negotiation is added.

Arista's [guide](https://www.arista.com/assets/data/pdf/Transceiver-Guide.pdf)
states platform applicability with restrictions and chassis/software minima;
its LR4 and AOC families require at least EOS 4.15.2. The exact 7050SX3 host
entries are also present in the [DMF HCL](https://www.arista.com/en/hcl-dmf/hcl-supported-transceivers-and-cables-for-arista-7050x3-and-7260x3-series-switches).
The latter's raw response was challenge HTML: its catalog source is explicitly
an indexed primary-page extraction, with no claimed original-HCL hash.
DMF FEC defaults do not establish EOS defaults. Compatible FEC/negotiation is a
planned configuration condition, not a setting applied or discovered here.

The Fortinet [100F ordering-table source](https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/ja_jp/FGT100F_DS.pdf)
is likewise an indexed primary extraction: direct Japanese/English PDF downloads
returned 404. Its original revision, original-byte hash and software minimum
remain unknown. The separate [transceiver datasheet](https://www.fortinet.com/content/dam/fortinet/assets/data-sheets/Fortinet_Transceivers.pdf)
is archived and supplies part specifications, but does not by itself prove a
host fit. Source entries preserve that distinction rather than presenting a
full archival or multivendor certification claim.

All selected independent modules use duplex LC SMF with matching LX, LR or
LR4 protocol and effective rate. The vendor parts have 10 km specified reach;
the reference module explicitly assumes only 100 m. The shared authored
`local_min_m: 3` / `local_max_m: 100` envelope applies to the **sum of the
complete known local cable path**, bounded by both endpoint reaches. It is a
conservative modeling limit, not a vendor minimum, computed loss budget or
receive-power measurement. Circuit terminations end local tracing: a three-metre
handoff says nothing about the carrier's intercity span or remote transceiver.
Single-lambda 100G LR, SR, BiDi, DWDM and breakout remain outside this selection.

The AOC entry has `assembly: true` and `assembly_length_m: 3`. It is one
preterminated optical cable with two captive ends of the **same part and
assembly**, not two independently purchased transceivers. It uses native `aoc`
media and `connector: captive`; no external LC/MPO connector, passive patch
panel or extension belongs inside its path. Independent optic serial numbers
must not be invented for its ends. Empty unused cages are allowed; only an
actually installed module/end receives a component power reservation.

`power_basis` distinguishes verified maxima from planning choices.
`vendor_max_power_mw` is present only for verified electrical maxima: Cisco
LR ([Table 8](https://www.cisco.com/c/en/us/products/collateral/interfaces-modules/transceiver-modules/data_sheet_c78-455693.html)),
Juniper [LR](https://apps.juniper.net/hct/model/EX-SFP-10GE-LR),
[LX](https://apps.juniper.net/hct/model/SFP-1GE-LX),
[LR4](https://apps.juniper.net/hct/model/JNP-QSFP-100G-LR4), and Arista LR4
([FAQ, page 5](https://www.arista.com/assets/data/pdf/Datasheets/Arista-100G_Optics_FAQ.pdf)).
Arista 10G reserves an authored 2 W because an explicit maximum was not found.
Fortinet's published 850 mW dissipation is rounded to 1 W for planning and is
not represented as a verified worst-case limit. The AOC FAQ's 3.5 W figure does
not explicitly distinguish one end from the assembly; the catalog conservatively
reserves the full figure at **each end**, 7 W total, with no extra cable charge.

The single authored optical power policy adds
`ceil(sum(installed module/end mW per host) × 1.25 / 1000)` watts alongside the
unchanged chassis allowance and the separately rounded PoE allowance. The
`upstream_ac_allowance_multiplier: "1.25"` string permits exact arithmetic;
it is planning margin, not PSU efficiency or measured AC conversion. Sum before
rounding, count each module/end once on its host, and recompute totals without
accumulating earlier enrichment. Do not subtract optics from published PoE
output budgets, count supply-nameplate watts as consumption, or erase an
installed optic's reserve merely because its interface is disconnected.

Catalog 0.10 leaves every existing interface, PSU, PoE fact and device model
unchanged. Phase 5 runtime evidence in `build/goal-connected-depth/phase5/`
qualifies its module/bay/interface and assembly relationships, repeat/growth
identities, passive paths and additive power checks offline. Live qualification
remains a separate gate; this catalog alone is not acceptance evidence.

## Refreshing

Update the library commit and source checksums deliberately. Compare all named
vendor interface records with the pinned YAML, including management and stacking
ports. Check modular PSU compatibility before changing installed configurations,
then run the project's checks against a regenerated estate. A catalog update
changes the hardware digest and may alter port capacity or stable allocations;
do not silently refresh this file during generation.

## Provider router

The `provider-edge` alias adds a Juniper MX204 using the same pinned
[device-type-library chassis](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/Juniper/MX204.yaml)
and [JPSU-650W-AC-AO module](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/module-types/Juniper/JPSU-650W-AC-AO.yaml)
commit. The 1U full-depth chassis retains `fxp0`, four `et-0/0/*` 100G ports,
eight `xe-0/1/*` 10G ports, and the RJ45 `Console`. Two populated PSU bays are
`Power Supply 0` and `Power Supply 1`; their positions and resulting C14 inlets
are exactly **`PEM 0`** and **`PEM 1`**, including the spaces. Fan population is
not modeled. Existing aliases and source definitions remain unchanged.

The provider profile selects the documented port-level mode with
100/100/100/0 Gbps and eight 10G ports; `et-0/0/3` stays disabled. Customer ports
retain their nominal 10G type with explicit configured 1G speed. Both choices
are grounded in the [Juniper port-speed guide](https://www.juniper.net/documentation/us/en/software/junos/interfaces-ethernet/topics/topic-map/port-speed-mx-routers.html).
No running Junos configuration or transceiver certification is claimed. The
[AC power guide](https://www.juniper.net/documentation/us/en/hardware/mx204/topics/topic-map/mx204-ac-power-system.html)
distinguishes chassis input from a PSU's 650W output rating. The generator's
320W chassis allowance is authored: 160W normally allocated per inlet, with
320W maximum on either feed during the modeled supply contingency.

PE management uses an in-band `lo0` /32. The dedicated `fxp0` inventory remains
unaddressed and uncabled; the profile does not route production traffic through
it or claim independent OOB reachability. See Juniper's
[management-interface guidance](https://www.juniper.net/documentation/us/en/software/junos/junos-getting-started/topics/concept/interfaces-understanding-management-ethernet-interfaces.html)
and [dedicated management-instance semantics](https://www.juniper.net/documentation/us/en/software/junos/cli-reference/topics/ref/statement/management-instance-edit-system.html).

This deliberate catalog addition changes the global hardware digest. Existing
saved plans remain historical artifacts; growing them with the current catalog
requires an explicit rebaseline. Do not silently substitute current hardware
for an earlier qualified plan.
