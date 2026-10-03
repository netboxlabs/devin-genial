---
name: visual-review
description: Look at a loaded estate the way a customer does — through the NetBox UI and Visual Explorer's rendered views — and turn what is ugly, empty, or unreadable into ranked generator fixes. The counterpart to cold-start-se: that one tests the docs, this one tests the output.
---

# Visual review of a generated estate

Every gate this repo already runs proves the estate is *correct*: validators
inspect the finished graph, strict readback proves every attribute landed,
exact ChangeDiff counts prove nothing was lost. None of them can tell you the
estate looks like a lab instead of a production network.

Rendered views can. A floorplan makes rack coordinates visible; a topology
graph makes naming legible or illegible; a rack elevation makes density
obvious. This skill runs that inspection and converts it into generator work.

## When to run

After a meaningful generation change, before showing an estate to anyone who
matters, and whenever a new rendering surface becomes available. It is cheap
compared to what it catches — the first run found a broken NetBox core
relationship that every offline gate had passed.

## What you need

A NetBox target that renders: NetBox Cloud with Visual Explorer entitled, the
`netbox_physical_geometry` plugin for floorplans, and an estate seeded where
the viewer can see it. Visual Explorer reads **main**, not branches — use
`just seed-main` onto a dedicated visualization tenant (see
[loading](../../../docs/loading.md#seeding-a-dedicated-tenants-main)), then
`just geometry` + `just seed-geometry` for floorplans.

Driving the browser is the SE vault's `drive-netbox-platform` skill: attach to
a logged-in Chromium over the DevTools protocol, screenshot, read the image.
Do not re-derive those mechanics here.

## The review

Walk every view and answer one question each: **would a network engineer
believe this is their network?** Record the status-bar numbers verbatim — they
are the cheapest evidence there is.

| Surface | Look for |
| --- | --- |
| WAN geo map | Do circuits draw as arcs? Are sites where their coordinates say? Does the provider legend read cleanly? |
| Floorplan (2D + 3D) | Is the room a plausible shape, or a corridor? Are racks in rows with aisles? Any rack "unplaced"? |
| Rack elevation | How full is the rack? Are 0U devices racked or floating? Do device names read like a network or like slugs? |
| Power chain | Panel → feed → PDU redundancy visible? Does allocated draw show real watts, or 0 W? |
| Cable topology | Do devices carry role colour? Are media types distinguishable? Is the graph legible at site scope? |
| L2/L3 dependency | Do VLANs connect to members, or float unattached? |
| IPAM radial / heatmap | Do prefixes show varied status and role, or one flat colour? Are labels distinct? |
| BGP topology | Any sessions at all? (Absent unless the estate models them.) |
| NetBox UI: a site | Related Objects complete? Contacts, journals, address, coordinates present? |
| NetBox UI: a list | Do the columns a customer filters on actually populate? |

Capture rules: a blank canvas is a re-capture, never evidence; a floorplan shot
needs geometry seeded at that cage or cabinet location first; and native
elevations colour by role only (status shows in the hover title and list
badges), so no shot may assert a status colour — Visual Explorer's status
rendering stays unverified until a planned device has been screenshotted.

Then check the questions a customer asks the API, because a broken denormalized
field is invisible in every offline gate:

```
/api/circuits/circuits/?site_id=<id>      # circuits terminating at a site
/api/dcim/cables/?site_id=<id>            # cables at a site
/api/ipam/prefixes/?scope_id=<id>&scope_type=dcim.site
```

A view that renders nothing has three possible causes, and you must separate
them before reporting: **wrong scope** (most common — heavy views also lie
while loading), **data genuinely absent**, or **the viewer is broken**. Query
the API for the same scope to decide which.

**The same rule applies to a view that renders something incomplete**, and that
case is more dangerous because it looks like a finding. In the 2026-09-30 run
the cable topology drew four PDUs as empty boxes with no ports and no cables,
while its legend advertised a red "Power" cable type that appeared nowhere. The
obvious conclusion — the generator does not cable power — was wrong. The API
said 108 power cables, 108 connected power ports, 82 connected power outlets.
The view simply renders interface-type ports only, which its own legend admits
if you read it (`PORT TYPES: Interface`). One API count separated a viewer
limitation from a generator defect, and without it a false bug report would
have gone out. **Never report an absence you have not confirmed against the
API**, even when a rendered view seems to show it plainly.

## Known findings and their shape

The first run (2026-09-29, Aurora Peak provider estate on a Cloud tenant)
produced these classes. Use them as a checklist, not a closed list.

- **Denormalized fields the bulk path skips.** `CircuitTermination.save()`
  back-fills `Circuit.termination_a/_z` and caches scope fields; TurboBulk runs
  without save hooks, so circuits rendered no arcs and `?site_id=` returned 0.
  Whenever a NetBox model maintains a cache in `save()`, the bulk path needs it
  compiled in or completed afterwards — go looking for the next one.
- **Coordinates authored for validation, not for rendering.** Rack
  `position_m` used a 30 m zone offset that read fine in tables and became a
  7.8 m × 37 m corridor on a floorplan.
- **Namespace prefixes in display names.** Graph views truncate labels, so four
  distinct power panels all rendered as `aurora-peak-pop-chica…`. Identity
  slugs should keep the namespace; display names should not.
- **Density.** Racks averaging 5 devices in 42U read as a lab. Fill, resize, or
  consolidate — but from correct modelling, never padding.
- **Null fields that power a feature.** Prefix roles, device-type airflow, rack
  type and facility ID were all absent; each silently disables a control in the
  viewer.

## Reporting

Rank by what it costs the demo:

1. **Breaks a view or a core NetBox question** — the estate is wrong, not ugly.
2. **Reads as synthetic** — density, naming, empty columns.
3. **Leaves a feature dark** — a null field that disables a control.

For each: the evidence (screenshot path, status-bar text, contradicting API
count), the suspected cause in the generator, and whether fixing it alters
every estate's canonical graph — if it does, it needs an `estates/__version__`
bump and a new baseline, which is a decision to surface rather than assume.

Separate **our** defects from **product** defects. Both are worth writing up;
they go to different places, and conflating them wastes everyone's time. A
product defect deserves the same rigour: root cause, exact repro, the state
that proves it.

## Related

- `.claude/skills/cold-start-se/SKILL.md` — tests whether the docs work
- [COVERAGE.md](../../../COVERAGE.md) — where ranked findings land
- [docs/qualification.md](../../../docs/qualification.md) — where live evidence lands
