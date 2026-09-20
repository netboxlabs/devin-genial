# Demonstrations from the estate

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

These stories operate on generated estates. Read each scenario's boundary before loading a changed snapshot.

Run commands from the repository root. Paths in code blocks are relative to that root.
Generated `build/` artifacts and qualification receipts are local outputs, not included in a clone.

- [Shared power-diversity demonstration](#shared-power-diversity-demonstration)
- [Acquisition and refresh walkthrough](#acquisition-and-refresh-walkthrough)
- [Provider span maintenance](#provider-span-maintenance)
- [Assurance discovery drift](#assurance-discovery-drift)

## Shared power-diversity demonstration

Generate a healthy estate using any implemented profile, then select a suitable
service host and build the demonstration:

```sh
just power-scenario build/enterprise-demo/plan.json build/enterprise-power
just scenario-check build/enterprise-power/scenario.json
devenv --profile diode shell -- just sdk-check build/enterprise-power/baseline/diode
devenv --profile diode shell -- just sdk-check build/enterprise-power/changed/diode
```

Alternatively set top-level `demo = "loss-of-power-diversity"` in a recipe.
`just plan <recipe>` previews a healthy baseline and the selected demonstration;
`just generate <recipe> <new-directory>` creates both snapshots and the scenario
report. `demo = "baseline"` remains the default. A frozen plan retains its demo
intent when passed to `python3 -m estates build`. Unsupported objectives fail with
the available choices. The existing acquisition/refresh command is unchanged.

Selection follows physical power paths and active VM/service consumers. The chosen
host must have two independent modeled supply paths and a compatible spare outlet.
The change moves one cord so both supplies share a PDU and upstream panel. Both
remain connected: inspect the wiring to find the hidden shared failure domain.
The report identifies the affected VMs, listeners and addresses from the graph.
An optional site filter uses `just power-scenario <plan> <output> <site-id>`.
If that site has no eligible host, generation explains the missing prerequisites.

Each scenario contains `baseline/` and `changed/` complete Diode artifacts,
`scenario.json`, `checks.json`, and a parent `report.md`. Scenario checking requires
exactly the expected code/object findings and proves that the inverse cable change
restores the frozen baseline. It deterministically re-exports both snapshots and
compares every Diode file, so a schema-valid payload cannot be substituted for
the checked graph. Any unrelated finding or package mismatch fails. Ordinary `just verify`
must reject `changed/plan.json`; it is deliberately defective. This snapshot is
also rejected as an ordinary growth seed.

**Use separate fresh disposable targets for the two snapshots.** Changing a cable's
termination changes its Diode matching identity; replay does not retire the old
cable. These artifacts do not implement an in-place rewiring or rollback command.
The `just load` path refuses the `changed/` snapshot by design — its `checks.json`
records `expected-defect verified`, not a passing plan — so the live before/after
is qualified only on the Diode lab path. On a TurboBulk target, load the healthy
baseline and demonstrate the defect from the scenario's findings and `report.md`.
Offline restoration is proven against the frozen baseline; live restoration means
a fresh target loaded from that healthy snapshot until an actual transition is
qualified. Branching, approval, drift detection and running service failover are
not executed by this demonstration. Keep live receipts separate from offline checks.

## Acquisition and refresh walkthrough

After `just generate`, run:

```sh
just scenario
```

This selects the default inherited branch `br-s0002` and writes
`build/acquisition-refresh/report.md`, three complete frozen snapshots under
`before/`, `acquired/`, and `refreshed/`, plus `scenario.json` and `checks.json`.
`scenario.json` contains the create/update/delete records, ownership changes,
expected answers, exact physical-path evidence, and snapshot file names/digests.
Other eligible branches can be selected explicitly:

```sh
python3 -m estates scenario build/bank-v9/plan.json \
  --site br-s0002 --out build/another-walkthrough
```

Acquisition changes ownership while preserving hardware and connections. Refresh
replaces access devices with new identities, reconnects the same endpoints, and
checks their names, IPs, DNS, VRFs, and assignments against the baseline. It also
checks that every changed record belongs exclusively to the selected branch;
shared definitions and objects at other sites remain unchanged. Old equipment's
allocation slots stay reserved, so replacing it does not move existing equipment.
The report answers which endpoints depend on each replaced switch and shows the
new upstream and supply paths by inspecting the finished graph.

**These are candidate snapshots and changes, not an applied migration.** Tenant
changes can affect Diode natural matching identity, and replay does not delete
the retired records. The scenario command therefore writes review artifacts,
not a transition replay script. A frozen snapshot can be exported with `build`
for qualification in a fresh disposable instance; that does not qualify applying
the transition to an already populated target. No synthetic audit log, NetBox
branch, approval, or live lifecycle history is created.

## Provider span maintenance

Use the [maintenance recipe](../profiles/provider-maintenance.toml)
for an SE story about a regional operator taking one leased inter-PoP span out
of service while supporting Harbor Logistics and Cedar Retail:

```sh
just plan profiles/provider-maintenance.toml
just generate profiles/provider-maintenance.toml build/provider-maintenance
just scenario-check build/provider-maintenance/scenario.json
```

The three PoPs, two customer hubs, IPv6 addresses and panel-cabled office access
are ordinary generated inventory. Offered customer traffic is intentionally
modest: 50 Mbps per Harbor spoke and 30 Mbps per Cedar spoke. The useful question
is **which premises reroute, and what protection is lost during maintenance?**
The example does not manufacture congestion.
Its panel access channels use the existing [local front-port compatibility
bridge](../lab/README.md#opt-in-local-front-port-compatibility) on the pinned NetBox 4.7 target. Choose `patching = "direct"`
for a new baseline when that mapping support is unavailable.

Generation selects an existing active leased span used by modeled customer
traffic. It writes `baseline/` and `changed/` complete artifacts, `scenario.json`,
`checks.json`, and a parent `report.md`. The change is exactly one Circuit's
status, `active` → `offline`. Its matching identity, A/Z terminations, cables,
interfaces, optical modules, power reservations, contacts and journals remain
unchanged. Set `demo = "provider-span-maintenance"` only on provider recipes.

For an existing healthy provider plan, the same operation is:

```sh
just span-scenario build/provider-demo/plan.json build/span-maintenance
just scenario-check build/span-maintenance/scenario.json
```

An optional third argument pins a canonical span key from the graph, such as
`circuit/backbone/seed-01`. Unused spans, customer/transit circuits and missing or
unavailable subjects are rejected. Saved scenario checking retains its selected
span; creating a new scenario after growth can choose a different one.

Walk the parent report from maintained Circuit and carrier contact to the actual
rerouted customer premises, their hubs, baseline and alternate PE paths, and
per-direction load/headroom. Follow the customer, operator and facilities
contacts on those real records. The report distinguishes rerouted premises from
unchanged routes sharing additional load. Capacity covers declared customer
spoke-to-hub traffic only; it excludes return, arbitrary peer-to-peer, NOC and
transit demand. This is modeled routing, not measured forwarding or convergence.
Carrier-wide failures are outside the checks. Per-PoP carrier diversity is not
guaranteed; the baseline report shows each PoP's actual span providers, including
shared-carrier exposure. Different provider names do not establish duct diversity.

`scenario-check` verifies the exact status-only diff, recomputed paths/capacity,
expected findings and byte-identical inverse restoration. Its changed-state
label is `expected-maintenance verified`. Ordinary verification of
`build/provider-maintenance/changed/plan.json` must fail: the baseline's active-span
and resilience obligations are deliberately unmet. In this three-PoP story,
the current declared flows still fit; further link/router failures can now break
connectivity. Those findings describe lost additional-failure protection, not a
present modeled customer outage. Their count and affected objects come from the
graph, not a fixed example list.
Protection depends on the selected span and finished topology. Independent
five-PoP examples include a span that retains every checked further-failure
witness and another in the same mesh that loses two; mesh size alone is not a
guarantee. Use the scenario's actual result.

**The status-only sequence passed on the pinned local stack.** Baseline/repeat
→ offline/repeat → baseline restore/repeat retained all 4,350 object IDs and
target inventory, with zero mismatches across 14,953 attributes and 8,571
references at each readback. Actual NetBox pages showed Offline, then Active,
on the same Circuit. See [execution commands and receipts](../lab/README.md#provider-status-sequence).
This is inventory status reconciliation; no router configuration, approval,
forwarding or failover execution is added. Other stacks and cable rewiring
remain unqualified. Generated scenario envelopes conservatively describe what
generation itself proved; this separate receipt establishes the local execution.

## Assurance discovery drift

NetBox Assurance compares Diode-ingested observation against documented NetBox
state, so it demonstrates nothing on flawless data. The drift twin supplies the
missing half: keep a generated estate as documented truth, then ingest a bounded,
believably drifted "observed" snapshot and let the review screen do the talking.

```sh
just generate profiles/bank.toml build/bank-v9
just drift build/bank-v9/plan.json build/discovery-drift
just drift-check build/discovery-drift
devenv --profile diode shell -- just sdk-check build/discovery-drift/observed
```

The artifact holds `baseline/plan.json` (a frozen copy of the bound baseline),
`observed/` (the incremental Diode payload), `manifest.json` (the exact expected
deviation set), `drift.md` (the operator talk track) and `checks.json`. There is
no `demo` recipe key and no resolver change: `drift` is a subcommand over an
already healthy frozen plan, like `power-scenario`.

Fourteen drift items land on one selected site, covering the four behaviours
NetBox Labs advertises Assurance detecting: undocumented objects, drift against
documented intent, documented infrastructure discovery never saw, and data-quality
damage. Every subject is property-selected from stable keys — the site is the
first eligible one in permanent allocation order, then the first eligible switch,
ports and endpoints by canonical key — so growing the estate keeps the same
subjects rather than reshuffling them. On the default bank estate that anchor is
`hq-01`, and the set drifts a replaced chassis serial, a repurposed port
description, a port moved onto an undocumented VLAN, a shut port, a re-addressed
access point, a blank serial, an upper-cased DNS name, a re-addressed IP
appearing on a second device and a lower-cased serial, alongside an undocumented
switch with its uplink and management address. Two stories deliberately link up:
the port moved onto the VLAN that the undocumented-VLAN item creates, and the
old address of the re-addressed endpoint reappearing on a neighbour.

`observed/` is a **projection** of the plan: only the drifted records are emitted
in full, and every other record exists to resolve nested matching identities. So
the payload is fifteen records, not a whole estate, and each one carries the exact
documented site, tenant and device identities Assurance matches on. Changing a
matching identity is a hard error — that would create a duplicate object rather
than a field deviation. The payload is restricted to models that exist on NetBox
4.6, which is what an Assurance-equipped target runs here; the 4.7-only additions
in this generator's Diode coverage (cooling records, module bay types) are
excluded by an allowlist checked against the emitted **and** nested kinds.

**Absence cannot be ingested.** Diode's reconciler defines create, update and
noop change types and its `IngestRequest` carries no tombstone, so the two
documented-not-observed items are named in the manifest and in `drift.md` with
`detection: requires-target-side-comparison`, and deliberately emit nothing. The
manifest does not fake a deviation it cannot produce.

`just drift-check` recomputes the entire manifest from the bound baseline plan,
re-exports the observed payload and compares every wire file byte for byte, then
re-renders `drift.md` and compares that too. A tampered manifest, baseline copy,
request file or walkthrough fails. The recorded inverse proves that replacing
every observed record with its documented record restores the exact frozen
baseline, so re-ingesting the baseline's own Diode files resolves every changed
field; the undocumented objects are listed separately as
`not_cleared_by_ingest`, because no ingest withdraws them.

**No live Assurance evidence exists.** Expected deviations are what this ingest
*should* produce against the bound baseline, derived from the public Diode
changeset contract. Whether a target opens, diffs, applies or clears them — and
whether it runs in review (`autoApplyChangesets: false`, which the operator
forces when Assurance is licensed) or auto-apply — requires target-side evidence
under the Cloud qualification protocol. This repository ships no Cloud ingest
recipe; use the target instance's own Diode client, then open
**Assurance → Deviations → Active Deviations**. Exact screen labels are
version-dependent; check them against the target build before a call.

The observed payload is an ingest artifact, not an estate. It has no `plan.json`,
fails ordinary validation by construction, and must never be pushed through the
TurboBulk loader. Ordinary `just verify` on the baseline is untouched: drift
artifacts are gated by `just drift-check` alone.
