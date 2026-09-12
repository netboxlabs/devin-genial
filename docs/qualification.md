# Qualification, scale and limits

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

This reference preserves implementation notes and recorded qualification history. The [local lab guide](../lab/README.md#current-v09-qualification) owns the latest representative live results. Older version-specific passages below are historical, not instructions to reproduce them with current code.

Run commands from the repository root. Paths in code blocks are relative to that root.
Generated `build/` artifacts and qualification receipts are local outputs, not included in a clone.

- [Version and qualification notes](#version-and-qualification-notes)
- [Checks and present limits](#checks-and-present-limits)
- [Current offline scale evidence](#current-offline-scale-evidence)

## Version and qualification notes

Version 0.9 implements shared MAC identities, circuit accounts, direct equipment
contacts, bounded lifecycle journals, optional IPv6, applicable PoE/wireless and
installed optics across five profiles. All five representative artifacts passed
fresh initial and repeat Diode loading, strict readback and rendered UI inspection
on the [pinned local target](../lab/README.md#current-v09-qualification). The provider
span-maintenance story adds actual customer attribution and directed capacity.
The objective and evidence index are in [GOAL.md](../GOAL.md). The completed v0.8 goal and source
are archived under `build/goal-connected-depth/`; its hospital/provider live
receipts remain historical and do not qualify new output. Images are deferred.
See the [modeling guide](modeling.md) for implemented capabilities.

Build a fictional, connected NetBox estate from a small recipe, then export it
for Diode. Regional banking, enterprise DC, school district, hospital/clinic and
provider backbone have generation profiles. Generation runs
offline using deterministic Python rules; no AI, API key, or server is needed.
Version 0.9 requires a new baseline. Do not pass a v0.8 or
earlier plan as `--previous` to v0.9. Preserve its source and receipts for historical
reproduction. Current qualification progress is recorded in [GOAL.md](../GOAL.md).
Version 0.7 expands the connected model across physical equipment, IPAM, wireless,
VPNs and operations records. The rules retain demand-sized WAN procurement and
floor-based headquarters allocation. See `coverage.json` in each build for the
actual emitted types and [the qualification notes](../lab/README.md) for live evidence.
The [community comparison](../COMPARISON.md) distinguishes breadth from fidelity.
The [hospital/provider gap review](../COVERAGE.md) shows what to deepen next.

All profiles use the same DC builder. Bank policy selects its services;
enterprise policy accepts workload groups, replica placement and resource demand;
school policy derives district services from enrollment and staffing. Bank and
school campuses also share aggregation/access construction. The qualified bank
canonical graph and Diode export were preserved during the completed v0.7 goal. Live qualification caught
a missing owner-group relationship in the shared enterprise/school records; the
corrected builder adds the group and reference. A shared power-diversity
scenario selects its subject from the graph; completed qualification and limits are tracked in
[GOAL.md](../GOAL.md).

Bank DC checks independently derive required service, compute and power
obligations; omitted contracts cannot hide a missing listener, overloaded host
or loss of power diversity. Required power and compute paths also check inventory
status, connected uplink cables and enabled service interfaces. Missing rack
placement cannot suppress power checks. These checks establish modeled baseline consistency, not running service
availability or application recovery.

A pinned local NetBox/Diode target is now available for live qualification:
see [the local lab instructions](../lab/README.md) for `just lab-up`, `lab-bootstrap`, `lab-load`,
`lab-verify`, `lab-down`, and the destructive `lab-reset`. It uses a dedicated
Colima VM on this Mac.

## Checks and present limits

`just check` runs independent graph mutation tests plus determinism, growth,
namespace isolation, capacity exhaustion, design-specific topology, acquisition/
refresh preservation, CLI artifact preservation, and a
250-branch regression exceeding 100,000 objects. SDK-specific tests explicitly
skip outside an environment with that optional dependency.
The [GitHub Actions workflow](../.github/workflows/check.yml) runs the suite,
profile/scenario build and SDK checks, and optional IPv6 across all five
profiles on Python 3.11 and 3.14. The first merged-main run
[passed](https://github.com/netboxlabs/devin-genial/actions/runs/34636919690).
This is an offline matrix, not a live NetBox matrix.

Qualification receipts are local build artifacts. SDK-specific checks record the
SDK version, entity/request counts, encoded sizes, and source-checked target;
these are not ingestion throughput or demo-fidelity measurements.

The v0.6 suite passed 166 generator/qualification tests and five setup tests with
SDK 1.14.0 on September 9, 2026 (`build/v06-full-check.log`). Seventeen WAN tests
cover tier minima, exact capacity boundaries, both handoff ends, disconnected
paths, malformed rates, procurement dates and growth/refresh preservation.
An independent sweep passed 360 configurations, 5,760 circuit checks, three growth
cases and 24 acquisition/refresh scenarios; a final validator follow-up passed
61 affected tests. Receipts are `build/v06-independent-review.json` and
`build/v06-independent-review-followup.json`.

The frozen `build/bank-wan` panel estate contains 12,219 objects and 20 circuits.
Generation/validation/export took 0.415s and SDK verification 3.307s locally
(`build/v06-build-timings.json`). An offline addition of 17 small branches grew
it to 27 sites, 21,829 objects and 58 circuits. Aggregate peak rose from 640 to
980 Mb/s, adding a second WAN edge pair at each DC. Every existing circuit and
termination remained unchanged; exactly 12 existing DC interfaces gained new
attachments. The reviewed candidate and SDK result are in
`build/bank-wan-growth/` and `build/v06-growth-plan-review.json`; that growth
candidate has not been ingested.

Initial v0.6 Diode replay completed 14 phases and 12,837 entities in 400.325s
across phases, with zero failures. Strict readback matched every object,
47,336 attributes, 27,177 references and 1,245 front/rear mapping tuples, with
zero mismatches or unexplained records (`build/v06-initial-replay.json` and
`build/v06-initial-readback.json`). The fixed pre-load inventory receipt is
`build/v05-before-v06-readback.json`. Native NetBox circuit views displayed the
50/200/500 Mb/s examples, their installation dates and purchasing explanations.
The HQ edge-to-circuit/provider-network trace completed with one 3 m cable;
`build/v06-ui-walkthrough.md` records the navigation and scope.
Identical replay then completed in 134.887s. All phases retained the initial
replay's final metrics (73,119 reconciled, zero failed/queued), with no additional
ingestion-log rows (`build/v06-repeat-replay.json`).
Repeat readback retained every ID and all 1,245 mapping tuples, with complete
emitted-field/reference coverage. Target inventory remained exactly 69,510 IDs.
The same paginated REST inventory was checked against all five older plans:
8,432 v0.2, 2,655 v0.3 pilot, 12,528 v0.3 depth, 21,541 v0.4 grown and 12,219
v0.5 objects. All retained their IDs, emitted data and mapping tuples.
`build/v06-final-comparison.json` links the six successful readback receipts and
records the shared inventory digest. The local-only driver
`build/v06-final-readbacks.py` reused `lab.verify.fetch_inventory` and
`verify_plan`; no loader or target API implementation changed. No target reset
or direct estate writes were used.

The v0.5 suite passed 149 generator/qualification tests and five setup tests with
SDK 1.14.0 on September 9, 2026 (`build/v05-full-check.log`). Fifteen new building
tests cover floor-local capacity and actual endpoint paths, fiber geometry,
management backbones, local power, rack name scope, independent demand checks,
and growth preservation. An additional review swept 72 combinations of staff,
reserve and cabling mode; all passed offline validation.

The frozen `build/bank-campus` panel estate has 12,219 objects. Its HQ contains
203 endpoints (180 staff workstations, 15 APs, eight cameras), four equipment
rooms, 12 access switches, five racks, and 21 inter-room fibers. Build and offline
validation/export took 0.438s; SDK verification took 3.449s locally
(`build/v05-build-timings.json`). Adding a second headquarters offline produced
16,072 objects, with no removals and all original HQ/branch objects unchanged.
Exactly 12 existing DC interfaces gained assignments for added WAN capacity;
`build/v05-growth-plan-review.json` and `build/bank-campus-growth/` preserve that
reviewed candidate. This HQ-addition candidate has not been ingested.

The v0.5 initial Diode replay completed 14 phases and 12,837 entities in 436.204s
across phases, with zero failures on the existing patched local target. Strict
readback matched every object, 47,296 attributes, 27,177 references and 1,245
front/rear mapping tuples with zero mismatches or unexplained records. Receipts
are `build/v05-initial-replay.json` and `build/v05-initial-readback.json`.
The fixed pre-load inventory was `build/v04-before-v05-readback.json`.
An identical replay completed in 154.294s; every phase retained the initial
replay's final ingestion metrics (60,299 reconciled, zero failed/queued), with no
additional ingestion-log rows (`build/v05-repeat-replay.json`).
Repeat strict readback retained every ID and all 1,245 mapping tuples, with
complete emitted-field coverage and no unexplained records. The captured target
inventory remained exactly 57,308 IDs (`build/v05-repeat-readback.json`).

Separate readbacks preserved all 8,432 v0.2 IDs, 2,655 v0.3 pilot IDs, 12,528 v0.3
depth IDs and 21,541 v0.4 grown-estate IDs, plus their emitted attributes and
references. All 1,224 v0.3 and 2,346 v0.4 mapping tuples were retained. Receipts
are `build/v02-after-v05-readback.json`, `build/pilot-after-v05-readback.json`,
`build/v03-depth-after-v05-readback.json`, and `build/v04-after-v05-readback.json`;
`build/v05-final-comparison.json` records the cross-receipt assertions. No target
reset or direct estate writes were used.

Native browser traces completed for a 26m fourth-floor workstation channel and
a 22m IDF04-to-MDF fiber. All four room-local `N01` racks resolved distinctly;
the IDF04 rack showed 16.7% space and 8.2% synthetic power utilization.
Navigation and interpretation are in `build/v05-ui-walkthrough.md`.

The v0.4 suite passed 134 generator/qualification checks and five setup checks
with the pinned SDK on September 9, 2026 (`build/v04-full-check.log`). The suite
includes a 250-branch panel estate exceeding 100,000 objects. New mutations reject
missing compact peer trunks, incorrectly wired edge bridges, detached gateways,
broken management paths and shared VLANs omitted from both cable ends.
The 11,967-object `bank-demo` baseline generated/validated/exported in 0.531s;
SDK verification took 3.245s locally (`build/v04-build-timings.json`).

The v0.4 initial load completed 14 Diode phases in 410.838s with zero failures on
the existing patched local target. Strict readback matched all 11,967 objects,
46,470 attributes, 26,780 references and 1,224 front/rear mapping tuples, allowing
only captured pre-v0.4 inventory (`build/v04-initial-replay.json` and
`build/v04-initial-readback.json`). The browser confirmed the gateway's bridge
parent relationship, a complete 39m ATM trace with readable labels, and 29.2%
space / 7.5% synthetic power utilization in the inherited small-branch rack.
Local URLs and scope are in `build/v04-ui-walkthrough.md`.

Identical replay completed in 140.503s without additional ingestion-log rows.
Repeat strict readback retained every object ID and all mapping tuples with zero
mismatches or unexplained records (`build/v04-repeat-replay.json` and
`build/v04-repeat-readback.json`). The updated acquisition/refresh candidate
walkthrough is `build/v04-acquisition-refresh/report.md`; it preserves all 18
endpoint names/addresses and explicitly distinguishes direct upstreams from the
inherited branch's peer-trunk path. Those transitions have not been applied.

Live additive growth then expanded this same estate from 10 to 27 sites and
11,967 to 21,541 objects. Increasing small branches from four to 21 raised modeled
peak demand from 480 to 820 Mb/s, so each DC gained a second WAN pair. The offline
comparison found 9,574 additions, no deletions, and exactly 12 existing interfaces
gaining VLAN assignments/descriptions; all previous cables, IPs, names and device
positions remained intact (`build/v04-growth-plan-review.json`). Diode reconciled
the grown artifact in 432.202s. Strict readback matched 82,509 attributes and
47,778 references, retained all 11,967 original IDs and 1,224 original mapping
tuples, and found zero mismatches or unexplained records. Receipts are
`build/v04-growth-replay.json` and `build/v04-growth-readback.json`. This qualifies
this additive capacity expansion on the pinned local target, not arbitrary
resizing, deletion, new observation timestamps or lifecycle transitions.

Final independent readbacks also retained every ID and all emitted fields and
references in the three older estates: 8,432 v0.2 objects, 2,655 v0.3 pilot objects,
and 12,528 v0.3 depth objects, including the depth estate's 1,224 mapping tuples.
All returned zero mismatches. Receipts are
`build/v02-after-v04-growth-readback.json`,
`build/pilot-after-v04-growth-readback.json`, and
`build/v03-depth-after-v04-growth-readback.json`. The target was not reset.

The v0.3 suite passed 122 generator/qualification checks and five setup checks
with the pinned SDK on September 9, 2026 (`build/depth-full-check.log`). Two
additional native Django tests passed in an isolated test database on the
patched target: mapping creation, fresh diff/replay without mapping-ID changes,
complete cable paths, and refusal of destructive mapping changes. Evidence is
`build/local-front-port-native-1.json` and its log.

The v0.3 direct-mode pilot loaded 2,655 objects through Diode into the unchanged
official NetBox 4.7/plugin1.17 stack. Strict readback checked 9,807 attributes and
4,808 references with zero mismatches, allowing only the previously captured
Cedar inventory (`build/v03-pilot-initial-replay.json` and
`build/v03-pilot-initial-readback.json`). The browser confirmed room hierarchy,
timezone display, and 12.9% rack power utilization from generated allocations.

The full `bank-depth` panel estate then loaded through Diode on NetBox 4.7.0
with the opt-in local plugin patch: 14 phases, 13,153 entities, 379.897 seconds
across phases, zero failures. Strict readback matched all 12,528 canonical
objects, 49,177 emitted attributes and 27,553 references, including 1,224 native
front/rear mapping tuples. Only previously captured estate IDs were allowed as
extra inventory. Receipts are `build/depth-initial-replay.json` and
`build/depth-initial-readback.json`.

Identical replay completed in 122.409 seconds with no additional ingestion-log
rows. Repeat strict readback retained every canonical object ID and all 1,224
mapping tuples, with the same complete field/reference coverage and zero
mismatches or unexplained records (`build/depth-repeat-replay.json` and
`build/depth-repeat-readback.json`). This is identical-artifact replay; it does
not test new observation timestamps, target edits, or scenario migrations.

Separate final readbacks also preserved all 8,432 earlier v0.2 IDs and all 2,655
pilot IDs, with full emitted-field/reference coverage and zero mismatches.
Evidence is `build/v02-after-depth-readback.json` and
`build/pilot-after-depth-readback.json`; neither earlier estate was reset.

The browser walkthrough confirmed an ATM-to-access-switch trace through its
room outlet and cabinet panel: three cables, 39 meters, **Trace Completed**.
The panel exposes all 24 front/rear pairs, including unused positions. A ledger
database service links to its listening IP, and that IP links back to the service.
Observed local URLs and remaining presentation gaps are recorded in
`build/depth-ui-walkthrough.md`. Long names still clip in rack and cable diagrams;
the generated relationships are more complete than the visual polish.

A v0.3 panel scale run generated 178,772 objects across 253 sites in 194 requests.
Generation, validation and export took 7.313 seconds; SDK verification took
41.653 seconds on local Python 3.14.7. Receipts are under `build/depth-scale/`.
This larger estate has not been ingested; these are offline timings only.

The earlier v0.2 suite's 96 checks passed locally on September 9, 2026 with Python 3.14.7 and the pinned
SDK installed: 93 generator/qualification tests and three local setup tests.
The local result is retained in `build/local-check.log`.

The September 8, 2026 v0.2 mixed scale run produced 128,932 canonical objects
across 253 sites in 141 requests. Generation, graph validation, and export took
3.9 seconds locally; the pinned SDK check took 29.0 seconds. These are single
Python 3.14.7 observations; `build/scale-v2/timings.json` and `sdk-checks.json`
retain the local receipts. Live ingestion was not run.

On September 9, 2026, the default mixed bank passed live ingestion into local
NetBox 4.7.0 with Diode 2.2.0 and plugin 1.17.0. Nine phases submitted 9,057
entities (8,432 canonical objects plus 625 deferred primary-address updates),
with all ingestion logs reconciled and no failures. Phase execution took
239.817 seconds on the 4-CPU / 8-GiB Colima VM, excluding setup and startup.
Strict REST readback matched all 8,432 objects, checked all 32,203 emitted
attributes and 17,783 references, and found zero mismatches or extra records.
Receipts are `build/local-bootstrap-fixed-replay.json`,
`build/local-initial-readback.json`, and `build/local-target-status.json`.
An identical replay completed in 81.634 seconds and retained every NetBox ID,
with the same field/reference coverage and zero extra objects. Its receipts are
`build/local-identical-replay.json` and `build/local-repeat-readback.json`.
The same observations were replayed and Diode's ingestion-log count stayed
unchanged; this checks identical-artifact replay, not changed timestamps, new
observations, or target-side matching after edits.
This is a single local observation, not a 100,000-object ingestion benchmark.

The validator checks reference closure, hardware inventory, rack occupancy,
port occupancy and media/speed compatibility, passive cable paths, redundant
attachments, addressing, VLAN continuity to access uplinks and gateway SVIs,
required service instances and ports, WAN demand, compute budgets, and A/B power.
These are finite modeled assertions, not a network simulator or a proof that
every realistic-world constraint is covered.

The full graph and indexes are currently held in memory. Export requests are
bounded, but generation is not streamed; memory grows with the estate. Recipes
fail explicitly when they exhaust a site address reservation, /24 or /20 host capacity,
256 inherited-site /20 reservations (including retired reservations),
eight management-switch uplinks per site (20 managed attachments per switch),
16 leaf-pair pods per DC, a service's
16-host reservation, or the configured object budget. Raising `max_objects`
does not bypass physical capacities. Each implemented profile has reviewed
construction limits; input bounds alone do not guarantee a fitting composition.

Routing protocols, firewall policy, carrier interiors, last-mile duct diversity,
RF coverage, measured PoE/electrical consumption, application replication, and recovery behavior
are not simulated. Management switches and endpoints are single-homed; modeled
network and power redundancy applies only to the contracted infrastructure.
One local NetBox/Diode version combination has been checked. Additional versions,
Cloud/Enterprise targets and transitions other than the documented local provider
status sequence remain unqualified. Live growth
has been checked only for the documented additive branch/DC-capacity expansion.
Use a disposable tenant to qualify the intended workflow before relying on a demo.
The unmodified official Diode plugin still needs mapping support for full passive
paths on current NetBox. The [opt-in local patch](../lab/README.md#opt-in-local-front-port-compatibility)
is deliberately limited to this pinned qualification target and one-position mappings.

## Current offline scale evidence

The corrected v0.9 source was measured on the same 64-PoP, 128-customer recipe
as the preserved v0.8 comparison run. Generation, independent checks, report,
intent, coverage and export share the same process scope:

| Run | Canonical objects | Seconds | Peak RSS bytes | Wire bytes | Requests |
| --- | ---: | ---: | ---: | ---: | ---: |
| v0.8 comparable predecessor | 220,308 | 9.86 | 1,810,972,672 | 96,316,765 | 244 |
| v0.9 same IPv4 recipe | 226,119 | 11.85 | 1,474,969,600 | 99,669,798 | 250 |
| v0.9 with optional IPv6 | 239,058 | 12.78 | 1,497,645,056 | 107,197,173 | 264 |

These are single observed local runs, not a controlled timing distribution or a
live-ingestion benchmark. Runtime excludes imports/startup; process peak memory
includes imports and the complete build. The full graph and indexes remain in
memory. The dual-stack export separately passed SDK1.14: 255,479 wire observations
in 264 requests, with a maximum actual protobuf size of 463,166 bytes.

Creating, independently verifying and reporting a span-maintenance scenario on
that 239,058-object graph took 57.33 seconds and 2,641,379,328 bytes peak RSS. It
checked 331 directed rows; the selected `circuit/backbone/pop-19/b` retained the
checked further-failure margin. This larger scenario was not exported as a second
wire snapshot or loaded live. Exact recipes, commands, source/artifact hashes and
the comparison are under `build/goal-connected-depth/final/scale/`, in the
`*-02*` receipts. Actual representative live qualification is separate in
[the lab guide](../lab/README.md).

### Historical v0.8 provider measurement

The final v0.8 provider run contains 220,308 canonical objects and 236,729 wire
observations in 244 bounded requests. Full CLI generation, independent validation,
report/intent/coverage generation and export took 13.10 seconds with
1,520,992,256 bytes peak process RSS on the recorded Darwin/Python environment.
The separate SDK pass took 54.07 seconds with 77,758,464 bytes peak RSS.
See `build/goal-richness/final-review/corrections/scale-summary.json` for exact
commands/logs, source and artifact bindings; the independent architecture review
bound every request and actual emitted kind count.

These are single local observations, with CLI startup included and light
reviewer/Docker activity present. They establish offline generation/export scale,
not a 220k live load or a memory bound for every recipe. The full graph remains
in memory. Historical v0.8 final live sizes were 4,383 hospital and 4,344 provider
objects; their exact scopes are in [the lab guide](../lab/README.md).

### Historical bank measurements (v0.7)

The measured bank runs contain 9,483, 37,213 and 128,663 canonical objects. The
largest generated, independently validated and wrote the full artifact in 7.182
seconds with 1,000.125 MiB process peak RSS on the recorded Mac/Python environment.
Its 149 Diode requests contain 141,663 wire observations; SDK 1.14 verification
passes separately. All three plans and every wire file remain byte-identical to
the previously independently reviewed scale artifacts. See
`build/goal-scale-final/README.md` and `summary.json` for archived-source commands,
exact inputs, timings, artifact comparisons and source hashes. The earlier
measurements and independent reviews remain under `build/goal-scale/`.

Those bank measurements are one observation per size. Timing excludes imports/startup; memory
includes interpreter/modules and all generation/validation/build phases. SDK and
live target resources are excluded. This is multi-site bank offline evidence,
not a 100k-object live ingestion benchmark or proof of large individual DC realism.
