# Hardware catalog

`hardware.json` is a small, versioned inventory consumed by the estate builder.
Catalog **0.12** replaces every authored "reference" model with a pinned real
one or a plainly named `Generic` part, adds vendor-shaped
[serial formats](#serial-numbers) and [platforms](#platforms), and makes the
Cisco Catalyst 9120 the default `ap` line ([real and generic
equipment](#real-and-generic-equipment)). Catalog 0.11 added the
[selectable vendor lines](#selectable-vendor-lines) the `[hardware]` recipe key
chooses between; catalog 0.10 added the selected optics and power policy
described below.
Phase 5 generation, independent checks and all-five SDK exports are implemented
and tested. GOAL.md records review and pinned-target qualification separately;
catalog entries alone do not prove installed inventory.
PoE allocation and independent power/path checks passed scoped offline review
and final representative initial/repeat qualification on the pinned local target;
[the lab guide](../lab/README.md#current-v09-qualification) records exact scope.
The new catalog digest requires a fresh baseline; phase 4 and older saved plans
retain their historical catalog.
The named vendor models preserve the interface names, types, management
flags, rack height, and depth classification from the community device-type
library at commit `72cc49fbb445f1e2f310d3b8dfef55e12d0b7138`, except where a
model's own section below declares a deliberate deviation (the EX3400 VCP
type, the QFX management port, the AP interface normalization, the 9120's
regulatory variant). Each source entry
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

The `access`, `leaf` and `ap` rows are **role families**, not fixed models: the
recipe's `[hardware]` table selects which vendor line each one resolves to. See
[Selectable vendor lines](#selectable-vendor-lines). The table above lists the
default line for each family.

Whichever line is selected, that model also serves management-switch roles;
hardware is not duplicated for a different role. The Fortinet source selects the SFP personality
for shared-media `port17`–`port20`; the catalog does not add duplicate copper
ports. No breakout interfaces or automatic speed negotiation are assumed.

All three access-switch aliases have ordered `access_ports` and `uplink_ports` lists
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
bays and attaches these existing power ports to them. PSU module types carry
their model and explicit bay form factors but no module-type profile (since
0.16.0): the catalog pins no wattage or input voltage, so a profile could only
have repeated the source URL, which stays here as provenance. No component
templates are generated or replicated.

The matching PSU families are listed in the [Cisco hardware guide](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9200/hardware/install/b-c9200-hig/product_overview.html),
[Arista 7050 guide](https://www.arista.com/en/qsg-7050-series-1ru-gen3/7050-series-1ru-gen3-overview),
and [Arista 7060 guide](https://www.arista.com/en/qsg-7060-series-1ru-gen3/7060-series-1ru-gen3-overview).
The exact inlet types come from the pinned module definitions linked in the
JSON. Populating both bays is a design choice, not a claim that every sold
chassis includes two supplies. Electrical load, PoE budgets, and cord connector
selection are not certified by this inventory.

## Regional-carrier footprint aliases (catalog 0.14, generator 0.17)

Stable aliases the provider footprint builders read. Every entry is pinned to
the library commit above unless marked; port names are the exact catalog
names. Deviations and evidence are in
[Regional-carrier footprint models](#regional-carrier-footprint-models).

| Alias | Model | Ports the builders use |
| --- | --- | --- |
| `aggregation` | Juniper ACX5448-M, 1U | `xe-0/0/0`–`43` 10G SFP+ (1G UNIs keep the `xe-` name with `speed` 1000000); `uni_ports` = `xe-0/0/0`–`39`; `lag_ports` = `xe-0/0/40`–`43`; `et-0/1/0`–`5` 100G unused; `em0` mgmt; RJ45 `console0`; fixed C14 `power0`/`power1` |
| `nid` | Ciena 3903 AC, 1U | `nni_port` `1` (1G SFP); `spare_port` `2` (1G SFP); `uni_port` `3` typed `1000base-t` (declared deviation); virtual `Management`; RJ45 `CONSOLE`; C14 `PSA`/`PSB` |
| `nid-10g` | RAD ETX-2i-10G, 1U | `nni_port` `ETH-1/1` (10G SFP+); `ETH-1/2`–`4` 10G SFP+; `ETH-1/5`–`8` 1G SFP; `uni_port` `ETH-1/9` (1000BASE-T), `ETH-1/10`–`12` 1000BASE-T; `MNG-ETH` mgmt; USB `control`; C14 `power` |
| `ce-small` | Juniper SRX300, 1U | `wan_ports` [`ge-0/0/0`]; `lan_ports` `ge-0/0/1`–`5`; `ge-0/0/6`/`7` 1G SFP spare; RJ45 `Console`; C14 `PSU0` |
| `edge` (existing) | Fortinet FortiGate 100F | gains `wan_ports` [`wan1`, `wan2`] and `lan_ports` `port1`–`port12` |
| `pop-mgmt` | Juniper EX3400-24T, 1U, no PoE | `access_ports` `ge-0/0/0`–`23`; `uplink_ports` `xe-0/2/0`–`3`; `stack_ports` `et-0/1/0`/`1`; `me0` mgmt; two JPSU-150-AC-AFO → C14 `Power Supply 0`/`1` |
| `oob-server` | Opengear OM2216-L, 1U | `eth0` mgmt (cabled), `eth1` spare, `Cellular Interface (LTE)` type `lte` (uncableable); `Port 1`–`16` RJ45 console-server ports; C14 `PS1`/`PS2` |
| `pdu-switched` | APC AP8941 Switched Rack PDU 2G, 0U, 208 V 30 A | NEMA L6-30P `Power Port 1`; `Power Outlet 1`–`24` (C13, C19 at 8/16/24); `Network` 100BASE-TX mgmt; RJ12 `Serial` |
| `osp-panel` | CommScope FMS-K2BI-L1A1-48-SP, 1U | front `Port 1`–`48` (`lc`, declared deviation from `lc-apc`) ↔ rear `Port 1`–`48` (`splice`), 1:1 |
| `demarc-panel` | Generic LC-24-port Fiber Patch Panel, 1U | front `Port 1`–`24` `lc` ↔ rear `Port 1`–`24` `lc`, 1:1 |
| `cable-manager-1u` / `cable-manager-2u` | Generic Cable Management Panel 1U / 2U | no ports; counts toward utilization |
| `blanking-1u` / `blanking-2u` | Generic Blanking Panel 1U / 2U | no ports; `exclude_from_utilization: true` |

Panel models carry `front_ports` / `rear_ports` lists and the mapping is
position 1:1 (front `Port n` ↔ rear `Port n`). Passive models carry
`is_powered: false`. Rack types live in the new top-level `rack_types` map:

| Rack-type alias | Model |
| --- | --- |
| `pop-cabinet` | APC AR3100 NetShelter SX 42U, 600 × 1070 mm, `4-post-cabinet` |
| `mpoe-cabinet` | KOSCAB kos-shts-9u55x45x50ds 9U wall cabinet, `wall-cabinet` |

## Selectable vendor lines

`hardware_lines` declares the three role families whose model the recipe may
choose, the default vendor for each, and the catalog alias every vendor line
resolves to. Nothing else in the catalog is selectable; `core`, `edge`,
`server`, `provider-edge`, `pdu`, `patch-panel`, the console servers and the
generic endpoints are fixed. `inherited-access` stays bound to the bank's acquisition story and is
never substituted.

| Family | Default line | Alternate line |
| --- | --- | --- |
| `access` | `cisco` → alias `access`, Cisco Catalyst 9200L-24P-4X | `juniper` → alias `access-juniper`, Juniper EX3400-24P |
| `leaf` | `arista` → alias `leaf`, Arista DCS-7050SX3-48C8-F | `juniper` → alias `leaf-juniper`, Juniper QFX5120-48Y-AFO2 |
| `ap` | `cisco` → alias `ap`, Cisco Catalyst 9120AXI-B | `aruba` → alias `ap-aruba`, HPE Aruba AP-505 |

Every builder names a family; `estates/model.py` `World.hardware_alias` is the
single point where a family becomes a model, and `selected_alias` gives the
independent checkers the same answer from the declared recipe alone. Adding a
line here changes the hardware digest, so every profile needs a new baseline.

### Juniper EX3400-24P (`access-juniper`)

`ge-0/0/0`–`23` at 1G with `poe_mode: pse` and `type2-ieee802.3at`; the four
shared uplink cages selected at 10G as `xe-0/2/0`–`3`; `me0` management at 1G;
1U. The two rear QSFP+ ports `et-0/1/0` and `et-0/1/1` are the platform's
default Virtual Chassis ports and carry NetBox type `juniper-vcp` — a
deliberate deviation from the pinned library's `40gbase-x-qsfpp`, declared
here because Juniper ships these ports as VCPs by default and the DC service
stack must classify them as stacking media, not optical cages.
Two installed `JPSU-600-AC-AFO` supplies occupy bays `PSU0`/`PSU1`, producing
C14 inlets `Power Supply 0` and `Power Supply 1`.

Sources: pinned [EX3400-24P device type](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/Juniper/EX3400-24P.yaml)
and [JPSU-600-AC-AFO module type](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/module-types/Juniper/JPSU-600-AC-AFO.yaml)
at the same library commit; Juniper's
[EX3400 system overview](https://www.juniper.net/documentation/us/en/hardware/ex3400/topics/topic-map/ex3400-system-overview.html)
for the PIC 0/PIC 2 port layout, the default VCPs and both console ports; and the
[EX3400 power system](https://www.juniper.net/documentation/us/en/hardware/ex3400/topics/topic-map/ex3400-power-system.html)
for the PoE budget, the supply models and bay labels. Airflow variants, the
150 W and 920 W supplies, DC power and Virtual Chassis membership beyond two
members are not modeled.

### Juniper QFX5120-48Y-AFO2 (`leaf-juniper`)

48 SFP28 cages `et-0/0/0`–`47` and eight QSFP28 uplinks `et-0/0/48`–`55`; one
management port `em0` at 1G; one RJ45 `Console`; 1U full depth. Two installed
`JPSU-650W-AC-AO` supplies occupy bays `PSU 0`/`PSU 1`, producing C14 inlets `0`
and `1` — the 650 W module this catalog already pins. Juniper's QFX5120 power
page names `JPSU-650W-AC-AO` explicitly; the module's MX204 attribution is this
catalog's earlier assumption, so the shared source is reused on the QFX's own
evidence, not the MX204's.

Two deliberate deviations are recorded rather than hidden. First, the SFP28
cages keep the platform's documented default 10-Gbps port mode, written as an
explicit `speed` on each interface; Junos would surface a 10G port with the
`xe-` prefix, and this catalog keeps the pinned library's `et-0/0/N` names
instead of asserting a name no single Juniper document states. Second, Juniper
documents **two** rear RJ45 management ports, `em0` (C0) and `em1` (C1), where
the community library declares a single `fxp0`; this catalog models only `em0`
and leaves C1 unmodeled, matching the one-management-port assumption every
shared management builder makes.

Sources: pinned [QFX5120-48Y-AFO2 device type](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/Juniper/QFX5120-48Y-AFO2.yaml);
Juniper's [QFX5120 system overview](https://www.juniper.net/documentation/us/en/hardware/qfx5120/topics/topic-map/qfx5120-system-overview.html)
for the 48 SFP28 + 8 QSFP28 layout, the quad-grouped 10-Gbps default and the
rear C0/C1/CON labels; the
[QFX5120 day-one guide](https://www.juniper.net/documentation/us/en/day-one-plus/qfx5120/id-step-2-up-and-running.html)
for the `em0`/`em1` names; and the
[QFX5120 power system](https://www.juniper.net/documentation/us/en/hardware/qfx5120/topics/topic-map/qfx5120-power-system.html)
for the 650 W AC supply models, the slot labels and the C13 cord coupler.
Channelized breakout, 25G operation and DC supplies are not modeled.

### HPE Aruba AP-505 (`ap-aruba`)

One 1000BASE-T Type 2 powered port and two 802.11ax radios, one 5 GHz and one
2.4 GHz. Source: the pinned
[Aruba AP-505 device type](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/HPE/Aruba-AP-505.yaml).

Interface names follow this generator's portable endpoint convention — `eth0`,
`wlan0`, `wlan1` — so the shared access, PoE and WLAN builders address an AP the
same way on either line. The vendor labels the wired port **E0** and identifies
radios by band; no vendor interface-naming claim is made here, and the verified
facts are the port count, media type, PoE class and radio count/bands. Unlike
the default 9120 line's dual 5-GHz mode, `radio_bands` records the real
`5g`/`2.4g` split, so `wlan1` takes a 2.4 GHz channel. The `max_input_mw: 25500`
and `pse_reservation_mw: 30000` values are the IEEE class-4 PD maximum and PSE
reservation from `reference-poe-v1`, not a measured or vendor consumption figure
for this model: every HPE-served datasheet URL returns 403 to automated
retrieval, so no model-specific watt figure is asserted. The optional 12 Vdc
input, the USB port, the serial console and the Bluetooth/Zigbee radio are not
modeled.

### Meeting or beating the default

`tests/test_hardware_lines.py` recomputes this claim from the catalog rather
than trusting the prose. Each alternate has at least as many access, uplink,
stacking, fabric, console and power ports, at least as many PSU bays, the same
PoE type and per-port maximum, the same planning supply-loss policy, the same
configured link rate on every indexed port, and a reviewed optic for every
optical cage it exposes.

One raw number is smaller and is deliberately allowed: the EX3400-24P's
two-supply PoE budget is 720 W against the C9200L's 740 W. Both switches have 24
ports at 30 W, so the deliverable budget — the supply figure capped by that port
limit — is 720 W on either line, and the binding N-1 planning figure is 370 W on
both. The test asserts the capped figure, not the raw one.

## Real and generic equipment

Catalog 0.12 retired the authored "reference" designs: every model is either a
pinned real product or a plainly named `Generic` part, the way production
NetBox instances record unbranded equipment. Library-pinned entries copy the
interface, console, power and passive-port names and types from the device-type
library commit above, each source carrying its URL, SHA-256 and CC0-1.0 license.

| Alias | Model | Pinned source and inventory |
| --- | --- | --- |
| `server` | Supermicro SuperServer 1029U-E1CRTP2 | [`Supermicro/SYS-1029U-E1CRTP2.yaml`](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/Supermicro/SYS-1029U-E1CRTP2.yaml) (`a7c2c39a…dc873`): `BMC` management; onboard `eth1`, `eth2` 10G SFP+ (the `data_ports` the DC builder cables) and `eth3`, `eth4` 1GbE left spare; DE-9 `Serial`. Two installed PWS-751P-1R supplies in bays `PSU1`/`PSU2` materialize C14 inlets `PSU1`/`PSU2` |
| `pdu` | APC AP9572 | [`APC/AP9572.yaml`](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/APC/AP9572.yaml) (`251027449…72c8472`): Basic Rack PDU, Zero U, 16A 208/230V; C20 `Power Port 1`; C13 `Outlet 1`–`Outlet 15`; no network management |
| `pdu-120` | APC AP9563 | [`APC/AP9563.yaml`](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/APC/AP9563.yaml) (`7e46d35c…84ff`): Basic Rack PDU, 1U, 20A 120V; NEMA 5-20P `Source`; NEMA 5-20R `Outlet 1`–`Outlet 10`; no network management. The small-room kit's PDU (provider customer premises), mounted at the rack's top unit |
| `patch-panel` | Panduit DP24688TGY (Cat 6 Punchdown Patch Panel, 24 Ports, 1 RU) | [`Panduit/DP24688TGY.yaml`](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/Panduit/DP24688TGY.yaml) (`b5299f24…ccd5`): front `01`–`24` 8P8C, rear `01`–`24` 110 punchdown, mapped one-to-one |
| `wall-outlet` | Generic "Wall box, 1 UTP plug" | [`Generic/wall-box-1-utp-plug.yaml`](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/Generic/wall-box-1-utp-plug.yaml) (`8138ca1f…bcaa1`): front and rear `Port 1`, 8P8C |
| `console-server` | Opengear CM8116 | [`Opengear/CM8116.yaml`](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/Opengear/CM8116.yaml) (`ae6718cc…de174`): RJ45 `Port 1`–`Port 16`; `NET1`/`NET2` management; own `Console` plus `USB A`/`USB B`; C14 `PS1`/`PS2` |
| `console-server-48` | Opengear CM8148 | [`Opengear/CM8148.yaml`](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/Opengear/CM8148.yaml) (`4a1403b1…ab636`): as the CM8116 with RJ45 `Port 1`–`Port 48` |
| `ap` | Cisco Catalyst 9120AXI-B | [`Cisco/C9120AXI-E.yaml`](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/device-types/Cisco/C9120AXI-E.yaml) (`89959cff…afb1`) plus the [9120AX data sheet](https://www.cisco.com/c/en/us/products/collateral/wireless/catalyst-9120ax-series-access-points/datasheet-c78-742115.html): 2.5GBASE-T Type 2 PD uplink and two 802.11ax radios. Default line of the `ap` family |
| `endpoint` | Generic "Wired network endpoint, 1GbE" | Authored: non-racked appliance, 1G `eth0`, C14 `Input` |
| `atm` | Generic "Automated teller machine, 1GbE" | Authored: non-racked ATM, 1G `eth0`, C14 `Input` |
| `liquid-chassis` | Generic "2U liquid-cooled blade enclosure" | Authored: one blade bay, coolant intake/distribution, `mgmt0`, C14 `PSU1`/`PSU2` |
| `liquid-blade` | Generic "Liquid-cooled analytics blade" | Authored: 0U enclosure-powered child, `mgmt0`, cold-plate intake, diagnostic device context |

Declared deviations and selections:

- **Server supplies.** The library declares the 1029U's PSU bays but has no
  PWS-751P-1R module type. Supermicro's
  [SYS-1029U-E1CRTP2 page](https://www.supermicro.com/en/products/system/1U/1029/SYS-1029U-E1CRTP2.php)
  lists two PWS-751P-1R 750 W redundant Platinum supplies, the two 10G SFP+
  and two 1GbE onboard ports and the dedicated IPMI port; the module model comes
  from that page (an indexed extract, no page hash), and its C14 inlet is the
  type the library's other 1U Supermicro PWS modules declare. PCI-E and drive
  bays are not modeled.
- **Console-server size.** Each equipment room gets the 16-port CM8116 when its
  RJ45 console demand fits one, otherwise the 48-port CM8148. The first build
  records the choice in the `console-server-size/<site>/<room>` reservation
  ledger; growth past sixteen adds another unit of the recorded size and never
  swaps a model. `NET1` is the cabled management port and `NET2` stays spare;
  the shared management builder cables only the first `mgmt_only` port of any
  model. A console server's own `Console` port stays uncabled.
- **AP regulatory variant and names.** The library pins only the -E (ETSI)
  9120AXI; these US estates install the -B (FCC) part, which Cisco
  differentiates by regulatory domain only. The data sheet documents Flexible
  Radio Assignment's dual 5-GHz mode, which `radio_bands` records as two `5g`
  radios, and 25.5 W maximum draw on 802.3at PoE+, which matches the
  `reference-poe-v1` PD envelope. The source names `GigabitEthernet0` and
  `Dot11Radio0/1` are normalized to `eth0`, `wlan0`, `wlan1` like the Aruba line;
  the console and USB ports are not modeled, so no AP joins a console server.
  The 2.5GBASE-T uplink attaches to a 1G PoE+ access port, so the configured link
  rate is 1G on either `ap` line.
- **Patch-panel rear.** The DP24688TGY's 110 punchdown rear terminates the same
  four-pair horizontal cable as an 8P8C rear; the PoE copper-path check accepts
  either.
- **Generic parts** carry manufacturer `Generic` and plain model names. Their
  dimensions, ports and supplies are explicit design assumptions
  (`generic-design-v1`), not a vendor product.

`airflow` is carried only where a source states it: `front-to-rear` on the
Arista, Juniper and Supermicro chassis and `passive` on the AP9572, AP9563,
CM8148 and Aruba AP-505, copied unchanged. `part_number`, `weight` and
`weight_unit` follow the same rule (0.16): each is copied from the model's
SHA-pinned devicetype-library file and is absent where the file is silent
(EX3300, FG-100F weight, Panduit DP24688TGY weight, Opengear, wall box). The
Cisco AP's file is the -E part, so the installed -B variant takes its weight
but no part number; the SR Linux lab router keeps its part number but no weight
(the source weight is a fully populated hardware chassis, not the container). The pinned Cisco C9200L, C9120, Fortinet 100F, CM8116,
Panduit and wall-box sources declare none, so none is invented. The generic
liquid-cooled enclosure states `front-to-rear` as an authored assumption.

PDU `power_outlets` are explicit catalog rows, so capacity can be checked rather
than inferred; the power builder reads the PDU's inlet and outlet names from the
catalog. A 0U value means the device consumes no rack-unit position; it
does not establish a device's physical placement or mounting method.
`wlan0` serves WLANs; `wlan1` can serve a second WLAN or the bank's separate
routed diagnostic link. The declared Type 2 power envelope is described below;
RF performance remains unqualified.

Cabinets reference rack type APC **AR3104** (NetShelter SX 24U, 600 mm wide x
1070 mm deep, with sides), the enclosure the 24U cabinet grid is authored
around. The pinned library has no 24U SX rack type; the model and dimensions
come from APC's
[AR3104 product page](https://www.se.com/us/en/product/AR3104/netshelter-sx-server-rack-enclosure-24u-600mm-wide-x-1070mm-deep-with-sides-black/)
(checked 2026-10-01). A provider customer premises takes the small-room kit's
Panduit **R2P26** two-post rack (13RU, 516 x 279 mm), pinned to
[`rack-types/Panduit/R2P26.yaml`](https://raw.githubusercontent.com/netbox-community/devicetype-library/72cc49fbb445f1e2f310d3b8dfef55e12d0b7138/rack-types/Panduit/R2P26.yaml)
(`d6c46294…c8e7`). The bank's rack-type records (`operations.RACK_TYPES`) carry
both.

## Serial numbers

Every catalog model declares a `serial_format` (and a `module_serial_format`
where it installs PSU modules). The formats are **fictional imitations of each
vendor's printed convention, not real units**: Cisco `FOC`/`FJC` plus year-week
plus four characters, Arista `JPE`/`SSJ`, twelve-character Juniper, Fortinet
`FG100FTK…`, Supermicro `S…X…`, APC `5A…E…`, Opengear numeric, Aruba `CN…`,
and an alphanumeric OEM style for Generic and Panduit parts. Template grammar:
`#` digit, `@` letter (I and O excluded), `*` either, `{yy}` a year,
`{yyww}` that year plus an ISO week. The date code is the unit's manufacture
week on the estate's one timeline (since 0.16.0): `vendor_serial` fills a
placeholder and `operations_context.timeline` re-dates it 30–180 days before
the unit's install (a device 7–37 days before its site's first circuit; an
optic before its own port's circuit),
stepping a detachable optic one week earlier when two would otherwise print the
same serial. `validate_operations` re-derives the install and reports
`operations-serial-date` when a date code does not precede it. `estates/model.py` `vendor_serial` expands a
template from the namespace plus the same stable per-device key the old `SYN-`
serial used (so estates with the same seed do not repeat each other's serials), so
serials remain deterministic and growth-stable. The independent check
`hardware-serial` requires every device serial to match its model's template.
Formats are under the native 50-character limit. Device serials are not
enforced unique (NetBox does not either); the per-format spaces are large
enough that sample estates carry no duplicates. Installed optics use the same
template grammar from `optics.serial_formats`, one authored label shape per
maker (Cisco `FNS…`, Juniper `1A…`, Arista `XKT…`, Fortinet `FNT…`, Generic
`G…`) — fiction shaped like a transceiver label, not a vendor-verified format —
expanded from a hash of the namespace and the interface key (an AOC uses its
cable key, so both captive ends share one assembly serial; see the installed
optics policy). Asset tags are separate.

### MAC OUIs and routed-VLAN names

`mac_ouis.manufacturers` maps each maker that addresses interfaces in this
catalog to its public IEEE MA-L assignment, read from the registry text
(source `ieee-oui`, <https://standards-oui.ieee.org/oui/oui.txt>, fetched
2026-10-02): Cisco `00:00:0C`, Juniper `00:05:85`, Arista `00:1C:73`, Fortinet
`00:09:0F`, Supermicro `AC:1F:6B`, Opengear `00:13:C6`, APC `00:C0:B7`, HPE
(Aruba) `00:0B:86`. `mac_ouis.virtual_machine` is QEMU/KVM's conventional
locally administered `52:54:00`, not an IEEE assignment. Generated tails are
fictional. A platform's `svi_format` (Junos: `irb.{vid}`) names its routed
VLAN interface; platforms without one use `Vlan{vid}`.

## Platforms

Network models declare a `platform`: Cisco IOS XE (C9200L), Cisco AP-COS
(Catalyst 9120), Arista EOS, Juniper Junos, Fortinet FortiOS and ArubaOS
(AP-505). Each becomes one NetBox platform per estate, linked to its
manufacturer, with a namespaced slug exactly like the existing Service Linux
platform; devices reference it. Servers, PDUs, console servers, passive gear and
generic endpoints declare none. The check `hardware-platform` requires a
device's platform to match its model's declaration. A platform names an
operating-system family only; no software version, image or running
configuration is claimed. Generated-estate PoE allocation and independent
path/supply checks have offline and SDK evidence; pinned-target delivery
qualification remains pending in GOAL.md.

Version 0.3 adds the outlet and explicit passive inventories. Electrical planning
allowances live in `estates/blocks.py`, separately from this hardware catalog:
they are fictional per-device budgets, not verified vendor consumption or PSU
ratings. Existing vendor definitions remain unchanged.

The physical enrichment also connects source RJ45 console ports to a same-room
console server; the selected access line's USB console ports and later-created
management-switch consoles remain present and spare. Two auxiliary access
switches per DC form a service Virtual Chassis using both of that model's
catalog stacking ports — Cisco StackWise on the default line, the EX3400's
default QSFP+ Virtual Chassis ports on the Juniper line. This is not an
assertion that the existing fabric MLAG pairs are stacks.

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

The default `ap` line is the **Cisco Catalyst 9120AXI-B**. `poe_pd` names its `eth0` input, requires
`type2-ieee802.3at`, declares `max_input_mw: 25500`, and reserves
`pse_reservation_mw: 30000`. `policy_source: reference-poe-v1` identifies these
as authored limits; the 25.5 W figure also matches Cisco's published 9120
maximum on 802.3at. The reservation is at the supplying switch; it is neither
constant AP consumption nor a second load to add at the rack. Juniper's
[PoE tables 2–3](https://www.juniper.net/documentation/us/en/software/junos/poe/topics/concept/poe-overview.html)
distinguish a 30 W Type 2 PSE allocation from up to 25.5 W at the PD.

`radio_bands` declares each radio's band: the 9120 line maps both `wlan0`
and `wlan1` to `5g` (its documented dual 5-GHz mode), while the Aruba line
records its real `5g`/`2.4g` split.
The shared builder picks that band's authored non-overlapping channel plan —
36/44/157 at 20 MHz for 5 GHz, 1/6/11 at 22 MHz for 2.4 GHz — and the
independent checker restates it. With only three non-overlapping 2.4 GHz
channels the bank's diagnostic hop shares channel 11 instead of adding a
fourth. These are planning assignments, with no country approval, measured RF or
interoperability certification implied. Existing interface names and the bank
diagnostic remain intact; this catalog addition creates no guest WLAN,
controller, third radio, injector or AP mains inlet.

Each access-switch model's `poe_pse` applies only to the existing ordered
`access_ports` inventory. Source-backed limits are separate from authored
allocation policy:

| Model | Per-port maximum | `budget_by_active_supplies_mw` | Actual supply requirement | Authored `planning_supply_losses` |
| --- | --- | --- | --- | --- |
| `access` | 30,000 mW | `0: 0`, `1: 370000`, `2: 740000` | `supply_model: PWR-C5-600WAC` in the installed bays | 1 |
| `access-juniper` | 30,000 mW | `0: 0`, `1: 370000`, `2: 720000` | `supply_model: JPSU-600-AC-AFO` in the installed bays | 1 |
| `inherited-access` | 30,000 mW | `0: 0`, `1: 405000` | `fixed_supply_port: PSU0`; no RPS | 0 |

The Cisco facts use `budget_source: cisco-psu-compatibility`, now covering the
[hardware guide's PoE ports and Table 4](https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9200/hardware/install/b-c9200-hig/product_overview.html).
The two-supply total is additionally limited by the 24 physical ports to
720 W. The Juniper facts use `budget_source: juniper-ex3300-poe`, grounded in
the [EX3300-24P budget table](https://www.juniper.net/documentation/us/en/software/junos/poe/topics/concept/poe-overview.html).
Its 405 W PoE budget differs from its 550 W PSU rating. Removing its sole
powered supply leaves zero delivery capacity; no single-supply survival is
promised. Twelve 30 W reservations fit the Cisco one-supply budget, and
thirteen fit the EX3300 normal budget. The EX3400-24P's `budget_source:
juniper-ex3400-power-poe` quotes the same Juniper table family: 370 W with one
600 W supply, 720 W with two. Twelve 30 W reservations fit its one-supply budget
too, which is why selecting the Juniper access line moves no endpoint. Losing one Cisco supply with 360 W
reserved loses redundancy margin without itself exceeding remaining delivery
capacity. These calculations are policy limits, not live outage observations.

All three PSE entries declare `type: type2-ieee802.3at`,
`per_port_max_mw: 30000`, and `policy_source: reference-poe-v1`.
The common `upstream_ac_allowance_multiplier: "1.25"` is a decimal string
for exact arithmetic. It is an authored conservative planning multiplier,
not a vendor efficiency value. The intended allocator adds
`ceil(PSE reservation mW × 1.25 / 1000)` watts once to the existing switch
chassis allowance, then checks its real supply/feed paths. AC input planning,
PSE output reservation, PD input and PSU output ratings must remain distinct.
Power demand must consume its own budget without reducing available wired-only
ports, relocating existing endpoints or claiming RF coverage survives a fault.

Native `poe_mode`/`poe_type` attributes occur only on AP `eth0` (`pd`, BASE-T copper) and
the three switch models' actual access ports (`pse`), all using the declared
Type 2 value. Management/uplink/stacking interfaces and AP radios have no PoE
attributes. These spellings exist in the pinned
[NetBox 4.7 choices](https://raw.githubusercontent.com/netbox-community/netbox/v4.7.0/netbox/dcim/choices.py);
their presence here is not a claim of completed native round-trip testing.

## Installed optics policy

The top-level `optics.parts` map defines sixteen selected parts. Each stable part ID
records manufacturer/model, form factor, optical protocol, medium, connector,
rate in **kbps**, reach in metres, power reservation in integer **mW**, source
IDs and an explicit `compatible_interfaces` map from hardware alias to existing
port names. Host compatibility, physical cage shape and configured link rate
are separate obligations. Manufacturer participates in identity: Cisco and
Arista both sell a part named `SFP-10G-LR`.

| Part ID | Manufacturer / model | Selected host cages and mode | Reserved mW per module/end |
| --- | --- | --- | ---: |
| `cisco-10g-lr` | Cisco `SFP-10G-LR` | `access` fixed `TenGigabitEthernet1/1/1–4`, 10G | 1,000 |
| `cisco-10g-sr` | Cisco `SFP-10G-SR` (MMF, LC, 400 m OM4) | same cages as `cisco-10g-lr` | 1,000 |
| `juniper-10g-lr` | Juniper `EX-SFP-10GE-LR` | `inherited-access` `xe-0/1/0–3`; `provider-edge` `xe-0/1/0–7`; `access-juniper` `xe-0/2/0–3`; `leaf-juniper` `et-0/0/0–47`, 10G | 1,000 |
| `juniper-10g-sr` | Juniper `EX-SFP-10GE-SR` (MMF, LC, 400 m OM4) | same cages as `juniper-10g-lr` | 1,000 |
| `arista-10g-lr` | Arista `SFP-10G-LR` | `leaf` `Ethernet1–48`, 10G | 2,000 authored |
| `arista-10g-sr` | Arista `SFP-10G-SR` (MMF, LC, 400 m OM4) | `leaf` `Ethernet1–48`, 10G | 1,000 |
| `arista-100g-sr4` | Arista `QSFP-100G-SR4` (MMF, MPO-12, 100 m OM4) | `leaf` `Ethernet49/1–56/1`; `core` `Ethernet1/1–32/1`, 100G | 3,500 |
| `fortinet-10g-lr` | Fortinet `FN-TRAN-SFP+LR` | `edge` `x1/x2`, 10G | 1,000 authored |
| `juniper-1g-lx` | Juniper `SFP-1GE-LX` | `provider-edge` `xe-0/1/0–7` configured **1G** | 1,000 |
| `juniper-100g-lr4` | Juniper `JNP-QSFP-100G-LR4` | `provider-edge` enabled `et-0/0/0–2`; `leaf-juniper` `et-0/0/48–55`, 100G | 3,500 |
| `juniper-100g-sr4` | Juniper `JNP-QSFP-100G-SR4` (MMF, MPO-12, 100 m OM4) | same cages as `juniper-100g-lr4` | 3,500 |
| `generic-10g-sr` | Generic `SFP-10G-SR` (third-party compatible) | `server` `eth1/eth2`, 10G | 1,000 authored |
| `arista-100g-aoc-3m` | Arista `AOC-Q-Q-100G-3M` | `leaf` QSFP28 cages; existing peer uses `Ethernet49/1`, 100G | 3,500 authored per captive end |
| `juniper-100g-aoc-3m` | Juniper `JNP-100G-AOC-3M` | `leaf-juniper` QSFP28 cages `et-0/0/48–55`, 100G | 3,500 authored per captive end |

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
its AOC family requires at least EOS 4.15.2. The exact 7050SX3 host
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

Single-mode parts use duplex LC with matching LX, LR, LH, LR4 or ER4 Lite
protocol and effective rate, 10 km nominal reach or more. **Short-reach parts**
(since 0.16.0) are multimode: SR on duplex LC (400 m on OM4) and SR4 on MPO-12
(100 m on OM4). `estates/optics.py` selects by the cable's medium, and turns a
direct single-mode jumper into OM4 multimode when both cages sit in one room,
the run is within the shortest multimode reach (100 m) and both cages take a
reviewed multimode part; a 10 km optic on a 3 m in-room jumper was a tell.
Building backbones, carrier handoffs and owned spans stay single-mode, as does
any jumper with a FortiGate 100F end: no primary source confirms
`FN-TRAN-SFP+SR` on that host (its 100F ordering extract lists only the LR
part, and the Fortinet transceiver sheet alone does not establish host fit).
`validate_optics` refuses LR on such a jumper (`optics-reach-class`) and
requires both ends of a multimode channel to share the connector. SR host fit:
Juniper's HCT pages for `EX-SFP-10GE-SR` and `JNP-QSFP-100G-SR4` (MX204,
QFX5120-48Y, EX3400; EX3300 by its datasheet's ordering table), Cisco's TMG
notes for `SFP-10G-SR` on the C9200L fixed uplinks, and Arista's transceiver
guide (Tables 10/12 and 6/14.0, no platform restriction on either SR row).
Two 0.15 parts left the catalog because no estate can reach them any more:
Arista `QSFP-100G-LR4` (every Arista 100G link is an in-room jumper) and the
generic server `SFP-10G-LR` (a server always sits in the room of its leaf). A
future link that needs one fails generation with the actionable no-reviewed-
optic error rather than silently installing a 10 km part. The shared authored
`local_min_m: 3` / `local_max_m: 100` envelope applies to the **sum of the
complete known local cable path**, bounded by both endpoint reaches. It is a
conservative modeling limit, not a vendor minimum, computed loss budget or
receive-power measurement. A third-party carrier's circuit termination ends
local tracing: a three-metre handoff says nothing about the carrier's intercity
span or remote transceiver.

The operator's **own** fiber is different (since 0.16.0): a circuit whose
provider is the estate's operator (`provider/operator` — the provider
backbone's owned dark-fiber spans, customer access circuits and same-metro NOC
access) carries the optic's light end to end, so the selected part's reach must
cover the circuit's recorded `distance`, or, where the circuit records none,
the two terminating sites' great-circle distance times the authored 1.3 route
factor. `estates/optics.py` picks the **shortest** reviewed reach that covers
the run from the parts sharing that host cage, rate and medium, so local links
and short spans keep LX/LR4. Two long-reach MX204 parts are pinned for this
(Juniper HCT, checked 2026-10-02; the MX204 product list names both):

| Part | Reach used | Power reserved | Source note |
| --- | --- | --- | --- |
| `SFP-1GE-LH` (740-031849) | 70 km | 1000 mW (vendor maximum 1 W) | High Tx power: the vendor requires optical attenuation on shorter links, so the installed module's description records an in-line attenuator |
| `QSFP-100G-ER4L` (740-071175) | 30 km | 5500 mW (vendor maximum 5.5 W at 0-75 C) | 40 km needs host FEC; FEC is not modeled, so the catalog uses the 30 km no-FEC reach |

`validate_optics` re-derives the owned run independently (the larger of the
recorded distance and the site-coordinate route) and reports
`optics-span-reach` when the installed part falls short. An owned circuit longer
than every reviewed part is a generation error, never a silently short optic.
Single-lambda 100G LR, SR BiDi, DWDM, amplified line systems, DAC and breakout
remain outside this selection.

The AOC entry has `assembly: true` and `assembly_length_m: 3`. It is one
preterminated optical cable with two captive ends of the **same part and
assembly**, not two independently purchased transceivers. It uses native `aoc`
media and `connector: captive`; no external LC/MPO connector, passive patch
panel or extension belongs inside its path. Independent optic serial numbers
must not be invented for its ends. Empty unused cages are allowed; only an
actually installed module/end receives a component power reservation.

`power_basis` distinguishes verified maxima from planning choices. It, the
source list and the planning power reservation are catalog provenance only:
emitted NetBox optic module types carry just the datasheet facts (protocol,
medium, connector, rate and reach).
`vendor_max_power_mw` is present only for verified electrical maxima: Cisco
LR and SR ([Table 8](https://www.cisco.com/c/en/us/products/collateral/interfaces-modules/transceiver-modules/data_sheet_c78-455693.html)),
Juniper [LR](https://apps.juniper.net/hct/model/EX-SFP-10GE-LR),
[SR](https://apps.juniper.net/hct/model/EX-SFP-10GE-SR),
[SR4](https://apps.juniper.net/hct/model/JNP-QSFP-100G-SR4),
[LX](https://apps.juniper.net/hct/model/SFP-1GE-LX),
[LR4](https://apps.juniper.net/hct/model/JNP-QSFP-100G-LR4), Arista SR4
([FAQ, page 5](https://www.arista.com/assets/data/pdf/Datasheets/Arista-100G_Optics_FAQ.pdf))
and Arista SR (transceiver datasheet page 6, 1 W for SFP+ optics).
Arista 10G LR keeps its authored 2 W reserve; the same datasheet line would
support 1 W, a revision left for a later baseline.
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

Catalog 0.13 adds the two long-reach MX204 optics above (`SFP-1GE-LH`, `QSFP-100G-ER4L`)
for the operator's own fiber. Catalog 0.11 adds the three alternate lines and their reviewed optics; catalog
0.10 left every existing interface, PSU, PoE fact and device model unchanged. Phase 5 runtime evidence in `build/goal-connected-depth/phase5/`
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
