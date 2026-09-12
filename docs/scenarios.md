# Demonstrations from the estate

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

These stories operate on generated estates. Read each scenario's boundary before loading a changed snapshot.

Run commands from the repository root. Paths in code blocks are relative to that root.
Generated `build/` artifacts and qualification receipts are local outputs, not included in a clone.

- [Shared power-diversity demonstration](#shared-power-diversity-demonstration)
- [Acquisition and refresh walkthrough](#acquisition-and-refresh-walkthrough)
- [Provider span maintenance](#provider-span-maintenance)

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
