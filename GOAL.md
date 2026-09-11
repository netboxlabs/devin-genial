# Goal: connected operational and network depth

Started September 10, 2026. The complete user objective follows unchanged.
The completed predecessor goal is preserved in
`build/goal-connected-depth/previous-goal.md`, with its source archive and hashes
in `start.json`. Its receipts under `build/goal-richness/` remain historical.

## User objective

 Evolve /Users/dbernosky/Github/devin-generator into a richer, consistently
  believable estate generator across regional bank, enterprise data center,
  school, hospital/clinic, and provider backbone profiles.

  Work autonomously through design → build → test → independent review →
  revise. Continue until the final implementation meets the acceptance
  criteria below and independent reviewers accept it.

  The priority is sensible, connected data—not object counts or populated
  tables. An LLM may operate and review the generator, but generation,
  allocation, validation, and export must remain deterministic code.

  Start by reading CLAUDE.md, GOAL.md, CONTRACT.md, COVERAGE.md, and the
  existing implementation. Preserve the completed goal and its evidence
  before recording this new goal. Reuse existing builders and helpers;
  avoid a new framework, ingestion engine, or unnecessary dependencies.

  Implement in this order:

  1. Apply existing enrichment consistently across all five profiles.
     - Stable MAC identities for eligible physical and VM interfaces.
     - Correct provider-account relationships on circuits, preserving
       customer and operator boundaries.
     - Direct contact assignments on appropriate network equipment and
       hosts, reusing existing operations and specialist desks.
     - Derive eligibility and ownership from actual graph relationships,
       rather than copying bank-specific examples into other profiles.

  2. Give important equipment a bounded lifecycle story.
     - Add installation and maintenance-planning records tied to actual
       devices, racks, components, connections, and responsible contacts.
     - Include a focused PSU or optic replacement narrative as the relevant
       component modeling becomes available.
     - Preserve journal identities across replay and ordinary growth.
     - Clearly distinguish generated scenario history from actions actually
       performed by the tool. Keep volume proportional to useful stories,
       rather than writing repetitive notes on every object.

  3. Add shared dual-stack address planning, provider first.
     - Make IPv6 optional and usable across all five profiles.
     - Allocate persistent prefixes and addresses within the appropriate
       VRFs and alongside the existing interfaces and service segments.
     - Define explicit policies for applicable link and endpoint types.
     - Prove containment, uniqueness, stable growth, and correct export.
     - Preserve supported IPv4-only recipes; do not imply operational
       routing or application reachability from inventory alone.

  4. Complete the applicable wireless and PoE story.
     - Model supported power-source and powered-device hardware, per-port
       limits, whole-switch budgets, and relevant supply constraints.
     - Deepen hospital and school wireless with explicit client demand and
       optional guest segmentation; reuse the capability wherever another
       profile has a corresponding facility or service need.
     - Connect WLAN intent to the actual VLANs, address plans, support
       ownership, and modeled service dependencies.
     - Do not force wireless into a pure backbone or data-center fabric.
       Do not invent live associations, authentication results, or RF surveys.

  5. Add believable optics and physical-link compatibility.
     - Use source-backed hardware facts and explicit design assumptions.
     - Select compatible ports, optics/modules, media, speeds, and supported
       reach for the modeled links.
     - Validate what the model can establish; disclose anything requiring
       an unmodeled optical budget or field measurement.
     - Integrate replaceable components with the lifecycle/contact story.

  6. Deliver one focused provider service-resilience demonstration.
     - Model a dual-access customer or a span-maintenance scenario.
     - Identify actual affected premises, alternative attachments, capacity
       limits, and the restoration path.
     - Keep the traffic assumptions explicit and independently checked.
     - Distinguish offline scenario snapshots from transitions the current
       Diode loading surface can actually execute.

  Acceptance criteria:

  - All five profiles use the shared capabilities where appropriate.
    Demonstrate meaningful variation across seeds, demands, and scales.
  - Ordinary growth preserves existing identities and reservations.
    Explicitly document changes that require a new baseline.
  - Independent validators inspect the finished graph. Add targeted failing
    mutations for each new constraint; emitted metadata must not be able
    to excuse an invalid estate.
  - Run the repository checks and pinned SDK/export checks. Qualify final
    representative artifacts for all five profiles through Diode into the
    disposable local target, including readback and repeat-ingestion checks.
    Fix the generator, never manually repair target estate records.
  - Exercise at least 100,000 objects offline, recording runtime, memory,
    export size, and comparison with the previous baseline. Report offline
    scale evidence separately from actual live-ingestion scale.
  - Preserve evidence tied to the final source and artifact versions.
    Walk representative NetBox pages and demonstrate useful SE questions:
    Who owns this device? What powers this AP? Which optic fits this link?
    Which customers are affected by this maintenance?
  - Keep operation simple through the existing CLI/Justfile, with small
    documented recipes, actionable errors, and clear defaults.
  - Update operating docs and the coverage review in the same pass.
  - Obtain independent architecture, network-realism, and SE-usability
    reviews of the final revision. Ask reviewers to actively find flaws.
    Resolve all critical and major findings and repeat review as needed.
    “Impressive” must be supported by inspectable examples and evidence,
    not praise or a lowered acceptance bar.

  Keep images and the previously deferred site-name cleanup out of scope.
  Do not pursue every remaining NetBox model or specialist industry feature.

  Finish with the working examples, exact operating commands, review
  verdicts, validation evidence, and an honest account of remaining gaps.

## Execution checkpoint

- Predecessor source, goal, and objective preserved before implementation.
- Phase 1 implemented and independently checked: all-five MAC obligations,
  circuit accounts (including retained Birch procurement lineage) and direct
  equipment contacts. Full gate passed 457 tests (four optional-SDK skips) plus
  five lab tests. Source, sample plans and bindings are in `phase1/`.
- Phase 2 accepted as an offline milestone: permanent rack-selected equipment
  history and installed-PSU replacement preparation. Final source passes 460
  repository tests (four optional-SDK skips), five lab tests, and the separate
  11-test SDK suite with no skips. All-five CLI builds and pinned SDK checks
  pass under `phase2/`; `artifacts.json` binds exact plans/wire/SDK receipts.
  Independent review found and closed PSU manufacturer, state, bay-fit and typed
  reference gaps in the shared equipment validator. `phase2/review.md` and its
  source-bound probes accept growth, acquisition, exact power restoration,
  bounded reports and 17 negative cases, with no unresolved major/critical issue.
  Gate log: `phase2-check-qualified.log`. No live target mutation in this goal.
- Phase 3 implemented and offline-qualified: optional shared IPv6 across all
  five profiles, exact IPv4-only preservation, permanent /48-/64-/127-/128
  reservations, primary/service bindings and independent family obligations.
  Final gate passes 488 repository tests (four optional-SDK skips), five lab
  tests and the separate 11-test SDK suite without skips. All-five CLI artifacts
  under `phase3/qualified/` are reproduced by the final runtime and exporter;
  `phase3/final-bindings.json` records exact graph/report/wire hashes and SDK,
  frozen-growth, predecessor-preservation and power-scenario checks. Seed and
  growth cases cover all five; inherited acquisition/refresh keeps both families.
  Adversarial review found and closed routed-name/VM eligibility, correlated
  tenant, unassigned-inventory and empty-radio-prefix gaps. Scoped offline review
  accepts the corrected implementation with no confirmed open findings;
  `phase3/review/review.json` binds its independent probes and final source.
  This reviewer authored adjacent IPv4 checks, so this is explicitly not one of
  the three fresh final whole-goal reviews. Qualified source is preserved as
  `phase3/source.tar.gz` with file/archive hashes in `phase3/source.json`.
  No live target mutation in this goal.
- Phase 4 is implemented and accepted in scoped offline review. Source-backed
  Cisco/Juniper PSE budgets and reference Type 2 PD reservations drive actual
  port flags, AC allowances and sparse stable access allocation. Independent
  checks follow copper/passive paths, compatible supplies and feed ownership.
  School/hospital managed/guest demand grows bounded AP mounts; guest uses
  existing radios plus actual VLANs, prefixes, gateways and trunks. WLAN context
  ties real technical ownership and bounded DNS/RADIUS listeners to available
  IPv4 capacity; inherited bank services remain an explicit external boundary.
  Growth preserves old physical identities and journals while actual PSE draws
  and report capacity can increase. Review found and closed a shared radio-band
  gap; the fresh reviewer accepts 52 negative mutations, ten exact PoE cases,
  six context checks, two growth sequences and three report/graph checks with
  no remaining confirmed findings. See `phase4/review/review-bindings.json`.
  Full gates pass 539 repository tests (four optional-SDK skips), five lab tests
  and eleven separate SDK tests without skips. All-five CLI/SDK artifacts are
  in `phase4/qualified/`; final source reproduces their exact graphs, reports and
  wire bytes and passes frozen replay/power-scenario checks in
  `phase4/final-bindings.json`. `phase4/checks.json` binds the repository gate
  to unchanged source/tests; `phase4/source.tar.gz` preserves this milestone
  before the next catalog change. No live target mutation in this goal.
  Final all-five live/replay/UI,
  100k+ scale and three fresh whole-goal reviews remain required. Never substitute
  predecessor or intermediate receipts for final evidence.
- Phase 5 is implemented and accepted in scoped independent offline review.
  Catalog 0.10 supplies nine source-linked optical parts and explicit reference
  or conservative power reservations. Occupied actual cages receive modules;
  existing interfaces remain cable endpoints. Local SMF/LX/LR/LR4 checks use
  actual host fit, configured rate, complete media/path length and endpoint
  reach. Three-meter AOCs remain one cable assembly with two captive ends.
  Fixed chassis cages cannot acquire parent modules; their position is the
  actual port name. Native part/assembly descriptions must agree with inventory.
  Power recomputes chassis plus separate PoE and installed-optics allowances;
  ordinary growth can raise inlet totals while retaining physical identities.
  Shared part definitions survive replacement of their last installed example.
  Bounded fixed-cage device journals and reports connect parts, support and
  preparation intent; no module deletion or executed hot-swap is claimed.
  The fresh reviewer closed major P5-A2 (nested optical cages) and minor P5-A1
  (contradictory AOC prose), then accepted 59 mutations, ten baseline variations,
  five growth sequences, acquisition/refresh retention and exact combined inlet
  accounting for 239 optical hosts across all five profiles, with no open findings.
  See `phase5/review/adversarial-review.md` and its source-bound probes.
  Full gates pass 586 repository tests (four optional-SDK skips), five lab tests
  and eleven separate SDK tests without skips. `phase5/qualified/` contains
  all-five CLI/SDK artifacts with seed variations, frozen replay and power
  scenarios. `phase5/final-bindings.json` verifies exact final generation,
  reports and wire bytes; `phase5/checks.json` binds the complete repository gate.
  Source is preserved in `phase5/source.tar.gz` before phase 6 edits. Native
  feasibility remains a SELECT-only probe; all-five final live/replay/UI, 100k+
  scale and three fresh final reviews remain required. The full goal is active.
- Evidence root: `build/goal-connected-depth/`. Record offline, SDK, live
  readback/replay, rendered UI, and final-review evidence separately.
- No commit or push authority is inferred. Preserve the existing worktree.

## Final qualification and review index

The historical checkpoints above retain their status at the time. The final
implementation includes all six phases. Completion is determined by the final
independent verdicts and `final/completion.json`, not a historical milestone.
Paths below are relative to `build/goal-connected-depth/`.

- `final/qualified-02/` and its `artifacts.json`: all-five representative graphs,
  bounded wire files and pinned SDK checks, seed variations, exact frozen replay
  and shared power scenarios. Provider includes baseline and changed snapshots.
- `final/candidate-02-impact.json`: four profile graphs/wire exports remain exact
  from candidate01; only two provider external-transit journal comments change.
- `final/checks-03.json`: 610 repository tests (four optional SDK skips), five lab
  tests and eleven separate pinned SDK tests pass. Later source changes are docs
  only; failed predecessor logs remain preserved.
- `final/live/<profile>/summary.json`: all five fresh initial/repeat Diode,
  strict readback, stable IDs/inventory, native GET/traces and actual CUA UI.
  Representative sizes range from 2,616 to 12,702 canonical objects. These use
  NetBox4.7.0/Diode2.2.0/plugin1.17.0/SDK1.14 with the explicit local panel bridge.
- `final/live/provider-backbone/transition-summary.json`: baseline/repeat,
  offline/repeat and restore/repeat pass on the same database through Diode.
  All 4,350 IDs and inventory remain exact; complete field/reference readbacks
  and rendered Offline/Active pages pass. No estate repair or intermediate reset.
- `final/scale/*-02*`: comparable 226,119-object IPv4 build and 239,058-object
  dual-stack build, observed runtime/RSS/export bytes and preserved v0.8
  comparison; separate pinned SDK and large span-scenario checks. These are
  single offline observations, not a large live benchmark.
- `final/review/corrections/`: actual five-PoP retained/lost further-failure
  examples checked by the independent heap oracle, plus lifecycle-anchor
  primary ownership and failing mutations across five profiles. Margin depends
  on selected span and actual topology, not mesh size alone.
- `final/source-02.tar.gz`, `source-02.json` and `qualification-02.json`: final source,
  artifact, gate, live, transition and scale bindings for reviewer inspection.
  The first final archive remains preserved; revision 02 corrects only residual
  documentation status and image-format wording found during final review.
- `final/review/{architecture,se}/` and `final/review/realism/`: independent
  architecture, SE-usability and Opus5 network-realism reviews. Original findings,
  corrections and final verdicts are separate preserved evidence. All final
  critical/major findings must be resolved before recording completion.

The final review corrected actual per-PoP carrier exposure reporting, external
transit handoff truthfulness, wireless table truncation and managed-demand scope.
Carrier-wide failure, per-managed-SSID admission, return/NOC/transit traffic,
optical-loss budgets, measured RF/power, forwarding and router execution remain
outside the model. Wireless is not forced into pure fabrics or backbone PoPs.
Images and deferred site naming remain out of scope. Other stack/version/edition
loads and cable-rewiring transitions need separate qualification. No commit or
push was performed; the worktree and all predecessor evidence remain preserved.
