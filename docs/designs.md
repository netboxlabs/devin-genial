# NetBox Designs catalogue for a store format

`just designs PLAN OUT` exports one retail **store format** from a frozen
`plan.json` as a [NetBox Designs](https://netboxlabs.com/docs/designs/)
catalogue, and `just designs-check PLAN OUT` proves the export describes
exactly the plan's objects for that store. It is a sidecar in the same shape
as the drift twin and the geometry artifact: derived from a frozen plan, bound
to its canonical SHA-256, byte-deterministic, and never a canonical-graph
change, a recipe key, a version bump or a target write.

Nothing here runs NetBox Designs. The check expands the catalogue with
Genial's own reading of the published design reference and compares the
result with the plan. What Designs itself would plan or apply from these
files needs a live catalogue and a target, and that is out of scope.

## What is exported

The store is property-selected, like the drift twin's subjects: the first
store of the smallest store format present (`retail.STORES` order), by
permanent site allocation order, so growth never moves it. The output is:

| Path | Holds |
|------|-------|
| `catalogue/devices/<slug>.yaml` | One design per device type in the store: `name`, `site`, `role`, `location`, `status` plus optional description, platform, tenant, owner, rack, position and face as variables; `device_type` as a `dtl:device-types/<Vendor>/<file>.yaml@<commit>` pin at the catalog's devicetype-library commit; the pinned PSU modules through `nbd.nbt.get_module_bay` and a `dtl:module-types/...` pin. The Generic wired endpoint has no library source, so its design declares the `DeviceType`, `Manufacturer`, `eth0` interface, `Input` power port and the primary address in full instead. |
| `catalogue/retail/<format>_store.yaml` | The composed store: the Site with its locations, VLAN group and VLANs, prefixes (the `/16` site block and every `/24` segment), IP ranges, power panels and feeds, the rack (with an explicit `RackType`, since AR3104 is not a pinned library rack type) holding the switches, firewalls, console server and PDUs as `nbd:devices/...` includes, the unracked endpoints as includes, the SVIs and the addresses on template-created ports, the wireless LAN group and WLANs, both WAN circuits with their terminations, and every cable through `nbd.lookup.endpoint`. |
| `deploy-vars.json` | The selected store's values for the store design's `$USER` variables: site name, slug, facility, address, coordinates, time zone, region, hostname stem, rack asset tag, site block, one `<segment>_net` per `/24`, and each circuit's install date. `dns_domain` has a default. |
| `manifest.json` | Plan SHA, store, counts per `infra_type`, every omitted record grouped by reason, the fields never written, the estate records the design references without declaring, the devicetype-library commit, and the design schema digest. |
| `README.md`, `checks.json` | The human summary and the passed-check receipt. |

Site-level inputs are variables; everything the format repeats verbatim
(VLAN IDs, host numbers inside each `/24`, cable labels, positions, port
assignments) is literal. The check verifies the design not only for the
selected store but for every other store of the same format in the plan,
each with its own derived variables, so a literal that was really a
per-store value fails the check instead of hiding in the design.

Every reference to a record the store does not own (tenant, region, site
group, roles, platforms, VRFs, tags, providers, circuit type, provider
accounts and networks, the parent wireless LAN group, PSU module types) is
written as Designs resolves it: text matched against the key, then the name,
then the slug, or a mapping of key fields. The manifest lists them under
`referenced_not_declared`; a catalogue deployed into an estate that lacks
them would refuse at plan time, by Designs' documented behaviour.

## What is omitted, and why

The manifest carries the exact list with counts. In outline:

- **Access points and their ports, addresses, MACs and cables.** The
  catalog's AP source is `C9120AXI-E.yaml` while the plan names the
  9120AXI-B, and Genial normalises the AP line's interfaces to
  `eth0`/`wlan0`/`wlan1` (a declared deviation from the library template). A
  `dtl:` pin would describe different ports, so the slice leaves the APs out
  rather than mis-describe them. The WLANs themselves stay.
- **Template-created components** (interfaces, console ports, console server
  ports, power ports, power outlets, module bays on `dtl:`-pinned devices). A
  design must not declare a device and the components its type creates in one
  plan (Designs issue #1004), so the store design only references them from
  cables and addresses. This also means the access-port VLAN assignments,
  descriptions, PoE modes and MTUs on those ports are not expressed.
- **Installed optics and their bays.** Genial's optics bays are its own; the
  library template defines none.
- **Power cables into PSU-module inlets.** Designs brings in a module type's
  interfaces only, so the Cisco and Arista PSU inlets cannot be referenced;
  the FortiGate, Opengear and PDU power cables and both feeds are declared.
- **Journal entries** (Designs does not manage them), **MAC addresses**,
  **device and module serials** and the **AOC assembly serial** (unit
  identities, not a format), **device tags** (a list-valued template variable
  is undocumented), the site **custom field** (Designs does not write custom
  fields), **VLAN group and WLAN scopes** (no flattened reference in Designs'
  model), **contacts, contact assignments, the circuit group and its
  assignments** (generic object references with no documented design syntax).

Plugin records (BGP, lifecycle, geometry, validation) are not part of a
retail store and never reach the catalogue.

## What the check proves

`designs-check` runs, in order: manifest binding to the plan SHA and to the
design schema digest; every design against
[`catalog/designs/design.schema.json`](../catalog/designs/design.schema.json)
(a copy of the published schema, validated with a small stdlib validator for
the draft-2020 subset it uses); every `dtl:` pin against the hardware
catalog's pinned sources at the same commit; the expansion of the composed
design with `deploy-vars.json`, resolving `nbd:` includes, template
variables, `{{ parent }}` and the lookup functions, into a flat set of
objects, each matched to a plan object by its Designs natural key and
compared field by field (every declared field must equal the plan's value;
every plan object in the store must be declared or listed as omitted with its
reason; nothing may be declared that the plan lacks); the same expansion for
every other same-format store; and finally byte-identical regeneration of
every file. `tests/test_netbox_designs.py` pins the failing mutations: an
edited field, a changed included-device variable, a dropped object, an added
object, a changed deploy variable, a wrong pin, a wrong port name, a broken
binding and a missing device design each fail naming the object.

The expansion is deliberately narrow: it understands the constructs this
catalogue emits (no `nbd_loop`, no arithmetic). It is Genial's check, not the
Designs engine.

## Open questions for a live run

Where the design reference is silent the catalogue follows the published
examples, and these choices are unverified until someone plans the catalogue
in a real Designs instance:

- Addresses on template-created ports are written as
  `interface: {name, device: {name, site}}` nested under the Site, with
  `primary: true` for the device's primary address. The hotel example nests
  addresses under a declared interface; whether the mapping form resolves a
  port the library template creates in the same plan is not documented.
- The device designs take `rack: '{{ parent }}'` and a text `site` as
  template variables and build `location: {name, site}` from them. The
  compute-rack example passes `{{ parent }}` the same way; a mapping that
  mixes a text variable with a reference is only shown for `rack` in the
  hotel example.
- `nbd.nbt.get_module_bay(parent, '1')` uses the catalog's bay *position*
  (`1`, `2` for the Cisco PSUs, `0`, `1` for the Arista ones), as the
  function reference says; the examples use a Dell position label.
- VRF references are text names. Designs keys VRFs by `rd`; Genial's VRFs
  carry none, so text falls through to the name match. Two rd-less VRFs read
  as one key on Designs' side, which does not affect a design that only
  references them.
- `owner` is declared on every record the plan owns, which pins
  `netbox_versions` to `4.7.*`.
- `tenant` on `VLANGroup` is written because the plan carries it (NetBox
  4.3+); `mark_populated` on `IPRange` likewise.
