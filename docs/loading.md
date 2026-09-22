# Artifacts and loading

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

Generation writes a complete artifact; loading it into NetBox is a separate operation.
Read the [transport model](transports.md) before choosing Diode, TurboBulk, or a
mixed path, and verify the completed graph on the intended target.

Run commands from the repository root. Paths in code blocks are relative to that root.
Generated `build/` artifacts and qualification receipts are local outputs, not included in a clone.

The ordinary target workflow is:

```sh
cp .env.example .env
# Add the raw NETBOX_TOKEN and the documented write-mode settings to .env.
# The recipes source .env only when NETBOX_TOKEN is not already exported.
just load-explain build/my-estate https://netbox.example "Generator Review"
just load build/my-estate https://netbox.example "Generator Review"
```

The target argument is the NetBox root origin. The optional branch is required
when Branching is installed; a target without Branching requires the explicit
`ALLOW_MAIN_WRITES=1` guard. The loader prints its stable private checkpoint
receipt before writes so the same command can resume safely.
Read-only target discovery, pagination, and job polling retry transient connection
failures with bounded backoff. After a TurboBulk job has been nonterminal for 60
seconds, polling also consults the target's core background-tasks record: when RQ
reports the job failed, stopped, or canceled while its row is still nonterminal —
twice consecutively — the worker died mid-job and nothing can finalize the row, so
the loader stops immediately with that diagnosis instead of waiting out the full
polling bound. A missing RQ record stays inconclusive (a live worker can still
finish without it), and an absent or erroring oracle endpoint leaves the original
timeout behavior unchanged. Rows from such a job may have committed; the recorded
guidance is the same as a timeout: never resubmit, use a new branch and receipt, or
resume only after the row reaches a terminal state (for example once TurboBulk
0.4.0's opportunistic reaper marks it errored).

Arbitration runs on a resume, and only after the reaper has marked the row: the run
in which the worker dies always aborts, and reruns keep aborting until the row is
terminal. Once its row is `errored` and its error contains the reaper's `Job
orphaned:` message, a resume arbitrates the dead job from exact branch evidence
instead of abandoning the branch. Reviewable inserts create one create-ChangeDiff
per row in the same transaction as the rows, and the receipt's exclusive ownership
of the branch means no other writer touches these models — an invariant the
preflight establishes rather than one arbitration re-checks — so the model's
create-diff count equals the rows of previously verified entries plus this job's
rows exactly when its transaction committed before the kill. A committed job is
adopted read-only as a verified checkpoint; an adopted entry skips the per-job
request contract, so its rows are proven only by ID resolution and the final exact
total and per-model ChangeDiff gate. A rolled-back job is marked superseded in the
receipt and its rows are submitted as a fresh job — a new mutation, never a resend
of the dead one; before that resubmit the loader cross-checks RQ's own record and
refuses when RQ affirmatively reports the job still alive. Any other count is
unexplained state and still requires a fresh branch, as does an orphaned job on a
receipt without the reviewable history preflight. The same arbitration runs on the
already-matched fast path, so an exactly matching target is not failed by a dead
job row. Orphaned zero-row finalizers follow the existing retry-once rule. Write
requests are never retried after an ambiguous transport failure; their recorded
intent must be inspected or resumed instead.

The default `just load` policy retains the TurboBulk changelogs and branch diffs
needed for review and revert; merging a TurboBulk branch to main is currently
blocked upstream (see the
[merge findings](qualification.md#rich-contract-live-qualification-and-merge-findings)).
It requires zero ChangeDiffs on
the branch before the first write. Final success requires an exact total and exact
create-ChangeDiff counts for every artifact model plus cable terminations after strict graph
readback; the receipt records the initial-zero and final count evidence. For an
initial-load performance test, a separate command makes the tradeoff explicit:

```sh
just load-explain-disposable build/my-estate https://netbox.example "Scale baseline"
just load-disposable build/my-estate https://netbox.example "Scale baseline"
```

`load-disposable` requires a fresh empty branch and disables changelog creation.
The resulting baseline can be demonstrated only inside that branch: it cannot be
reviewed, merged, or reverted. Delete the branch after use. It is available only
for artifacts requiring no REST creation or completion PATCH; the explain command
lists those blockers, and the load command repeats that check before target writes.
The current rich and scale artifacts therefore remain on the reviewable path.
It still requests full validation and schedules every required post-hook. TurboBulk
data jobs are limited to 2,000 rows and explicitly skip all global hooks. After
all rows and bounded REST completion, zero-row finalizer jobs run denormalization,
counters, cable-link repair, cable-path rebuilding, and search indexing one hook
at a time. Denormalization and counter jobs target only the parent models whose
derived fields the emitted graph can change; search still runs once per searchable
model. The loader requires an exact result for every enabled or skipped hook,
zero TurboBulk input-row changes and changelogs from finalizers, and one changelog
per reviewable data row. A hook may still repair derived rows internally; its
reported result is retained in the receipt. A terminal zero-row finalizer whose
job or hook result fails its contract is recorded and retried once; a second failure
requires a new receipt and fresh branch. A nonterminal job remains the exclusive
checkpoint and is never duplicated.
Receipts bind the selected policy, row bound, payloads, and per-job request settings
and reject a resume under different settings, including the compiler version: a
receipt written by an older compiler is refused with "different compiler_version;
choose a new receipt and fresh branch" rather than resumed under changed render
semantics.

Rendering is exact for every emitted field, with one qualification: the loader
additionally supplies three model defaults the raw bulk path would otherwise
manufacture as invalid empty strings — `location.status`, `power_outlet.status`,
and `rack.starting_unit`. REST and Diode apply these server-side. Strict readback
compares only the plan's emitted fields, so it does not verify them.

REST completion uses 100-row synchronous PATCH batches (NetBox's bulk PATCH is one
atomic transaction; 100 rows is a client choice measured on the local NetBox 4.6.8
harness, not a documented server limit). Before each request, the receipt stores
its endpoint, target IDs, exact payload, hash, and an attempt record. After a lost
response, a resume reads the target before doing more work. An exact match marks
the existing batch recovered without another write. An unchanged, partial, or conflicting target remains ambiguous and
requires a fresh branch because the request might still be executing. HTTP 4xx
responses are recorded as definite rejections and are never replayed. The receipt
reports completed logical rows, write-intent rows, and readback-recovered rows
separately.

The same rule applies to a TurboBulk POST whose response is lost. Data-bearing jobs
cannot be adopted without a returned job ID. A zero-row finalizer can be adopted only
when the core job history contains exactly one otherwise-unbound job inside the
recorded request window with the same branch, model, mode, and zero-row result. Zero
or multiple matches leave the operation ambiguous.

Device components need one extra invariant. Genial writes `_site_id`,
`_location_id`, and `_rack_id` into each component's original insert from its
parent device so the row is correct before final maintenance. Preflight requires
`site_id`, `location_id`, and `rack_id` filters
on every emitted component REST endpoint. Strict readback groups expected IDs by
component kind and placement and makes one query per group, including `null`
location and rack values. This proves the caches without another mutation, so
reviewable ChangeDiff counts remain exactly one create per canonical object.

## Verify without loading

`just verify-target ARTIFACT TARGET [BRANCH]` runs the loader's final strict
gate with zero writes, against main or one branch: full inventory comparison
against the plan's attributes and references, then — only if that comparison
passes — a native cable trace per generated cable (asserting the cable appears
in its A-side trace) and component placement queries by kind and placement. It
applies the same builtin allowlist a load does, computed per row against the
already-loaded target, and records the allowed ids in the receipt alongside
`receipt_version`, `compiler_version` and the full mismatch list (not a sample).
Unlike a load it does not run the component-filter preflight, so it does not
re-prove that the target honors the `site_id`/`location_id`/`rack_id` filters.
The command exits 2 on mismatch, and refuses to overwrite anything but a prior
verify receipt. See the [seeding guide](seeding.md) for restore-based seeding
and its acceptance evidence.

## Reset a disposable branch

```sh
just reset https://netbox.example "Disposable branch"
```

Reset is deliberately branch-scoped and destructive. It requires the exact branch
to be `ready`, verifies read-only API metadata for create permission and PATCH/DELETE
capability, and checks `/api/core/jobs/`. Pending TurboBulk jobs have no reliable
branch metadata yet, so any pending Bulk Load, Bulk Delete or Bulk Export job
blocks. A running job blocks when it names the exact branch or lacks branch
metadata. Reset first renames the ready branch to a unique quarantine name by its
immutable ID. It then waits for visible jobs to reach a terminal status; completed,
errored and failed jobs do not block. TurboBulk 0.3.0 accepts a branch string before
its worker resolves that name, so an original-name job can still enter the queue
after the final jobs scan. Reset therefore never reuses the original name: after
the final scan it deletes the quarantined branch by ID and removes its schema,
archives matching local load receipts under `build/load-receipts/history/`
(verify-only receipts are re-runnable evidence and are left in place), creates
a receipt-bound uniquely named replacement, and waits for it to become ready. It refuses
blank and `main`. The reset receipt under `build/reset-receipts/` allows safe
inspection or continuation after interruption. A definite HTTP rejection is
reported separately from an ambiguous connection failure; after an ambiguous
delete, reset checks the checkpointed ID and refuses a renamed survivor. The API
metadata establishes that the routes expose PATCH and DELETE; it does not guarantee
that the destructive request will be authorized, so the actual DELETE may still
return a definite permission rejection before anything is recreated.
Branching 1.1.2 exposes no status transition for this purpose, so the reversible
rename is the coordination boundary. If job draining times out, the old branch
stays under the quarantine name recorded in the receipt; rerunning the same
command recovers the rename by old branch ID and continues waiting before delete.
An HTTP 5xx or connection loss during PATCH, DELETE, or POST is an ambiguous
mutation: a proxy response cannot prove whether the origin committed it. Rerunning
the same reset receipt inspects the immutable branch ID before retrying or advancing.
A very large Cloud branch can still require operator cleanup if its synchronous
schema deletion repeatedly exceeds the service gateway.
Use the printed replacement name for the next load and as the input to the next
reset. Because the replacement also has a new schema ID, update `DIODE_BRANCH` and
reconfirm the external Diode routing evidence before a Diode load. Pass the printed
replacement name to TurboBulk loads.

## Artifacts and Diode

| File | Purpose |
| --- | --- |
| `plan.json` | Frozen resolved recipe, graph, allocation/design ledgers, hardware digest, and profile assertions |
| `report.md` | Human walkthrough derived from that graph |
| `intent.json` | Resolved inputs, supplied/default provenance and explicit planning assumptions |
| `coverage.json` | Pinned model coverage, actual counts/example keys, external bindings and native SDK gaps |
| `checks.json` | Offline validator result and plan digest; explicitly records live ingestion as untested |
| `diode/manifest.json` | Checksums, counts, deterministic request IDs, dependency phases, and replay notes |
| `diode/phase-*.json` | Diode dry-run replay requests; at most 1,000 entities and 2,500,000 JSON bytes each |

The export contract is pinned to **netboxlabs-diode-sdk 1.14.0**, whose
`IngestRequest` has no entity for four kinds every estate carries: the
automation records (`config_context`, `export_template`, `webhook`,
`event_rule`). The wire package therefore omits them and records exactly what it
left out in `diode/manifest.json` under `loader_only_records`; only
`just load` (TurboBulk plus bounded REST) delivers them. A Diode-seeded target
holds the estate without them, so verify it with `just lab-verify` (which passes
`--diode-delivered-only`: the same restriction, recorded in its receipt).
`just verify-target` is the complete gate and expects every record, including
these.

The optional devenv `diode` profile installs the SDK in a devenv-managed
environment:

```sh
devenv --profile diode shell
just sdk-check build/bank-v9/diode
just check
```

The SDK check verifies manifest integrity, protobuf parsing, actual encoded
request sizes, and the validation rules embedded in the pinned descriptors.
It reports the known target limitations from the manifest. It does not contact
Diode or NetBox; a passing SDK check does not resolve the panel mapping gap.

For a target already configured with Diode, `just load` invokes the SDK's existing
[dry-run replay helper](https://github.com/netboxlabs/diode-sdk-python/blob/v1.14.0/netboxlabs/diode/scripts/dryrun_replay.py)
inside the pinned devenv profile. Supply `DIODE_CLIENT_ID`,
`DIODE_CLIENT_SECRET`, and `DIODE_TARGET` through the ignored `.env`.

The ingestion API does **not** choose or report the NetBox branch, and it does
not control Assurance versus auto-apply. Those are externally managed tenant
settings. Before a remote write, verify the effective setting in its authoritative
UI or operations record, then set `DIODE_MODE=direct`, `DIODE_BRANCH` to the
branch schema ID, `DIODE_CONFIG_SOURCE` to that record, and
`DIODE_CONFIG_CONFIRMED_AT` to a timezone-aware timestamp from the last 24 hours.
Finally set `DIODE_WRITES=1`. These values are a fresh operator attestation; the
loader records and binds them but cannot introspect them. Use `main` only for a
target without Branching and explicitly set `ALLOW_MAIN_WRITES=1`.

```sh
python3 -m netboxlabs.diode.scripts.dryrun_replay \
  --target "$DIODE_TARGET" --app-name devin-generator --app-version 0.9.0 \
  build/bank-v9/diode/phase-001-*.json
```

The loader currently executes only externally confirmed direct auto-apply. It
replays one manifest request at a time, checkpoints SDK acceptance, and requires
exact REST visibility before proceeding to the next dependency phase. Assurance
review remains a documented manual workflow until its effective mode and
application state can be verified safely. Primary IP assignments are a final phase
because their interfaces and addresses must already exist. If the process stops
before SDK acceptance is checkpointed, the loader checks visibility once and
requires a fresh branch when acceptance remains ambiguous. It never blindly
resubmits that phase.
The pinned local `lab-load` harness rejects historical failed ingestion logs;
its [recovery procedure](../lab/README.md#recover-a-failed-local-ingestion) uses a
corrected artifact, disposable reset, and new bootstrap receipt before a full load.
Qualify reconciliation and replay on the intended
target; the [local results](../lab/README.md#current-v09-qualification) cover one pinned combination. Replaying a
baseline can reverse demo edits.
The saved request IDs identify reproducible artifacts; the replay helper rebuilds
its own request envelope from the saved entities.

The recipe's `as_of` is a synthetic observation date, preserved in replay. It
must be in the past for SDK timestamp validation. Do not silently replace it
with the current time. Wire format and limits are based on the
[Diode protocol](https://github.com/netboxlabs/diode/blob/diode-reconciler/v2.2.0/diode-proto/diode/v1/ingester.proto);
scoped references follow the plugin's
[matching criteria](https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/docs/matching-criteria-documentation.md).

## Cloud Diode execution modes

Diode acceptance is not completion. The SDK's ingest response contains an error
list; an empty list means the Ingester accepted the request. It does not establish
that reconciliation finished or that NetBox was changed.

Establish the Cloud tenant's execution mode before timing a load:

- With **Assurance review**, ingested differences become deviations. NetBox is
  unchanged until an operator applies them. The documented workflow supports
  bulk actions, but submission, deviation creation, application, and REST
  visibility are distinct measurements. See [review and resolve deviations](https://netboxlabs.com/docs/assurance/deviations/).
- With **direct auto-apply**, the reconciler applies change sets without the
  Assurance review step. Measure SDK submission and final REST visibility
  separately. Diode exposes an `autoApplyChangesets` deployment mode, but Cloud
  is managed; do not assume that a customer or API client can change it. See the
  published [Diode configuration and data flow](https://netboxlabs.com/docs/enterprise/helm/configuration/diode/).

Record the mode alongside target versions and results. After a mode change, use
a fresh disposable branch and re-ingest from the first dependency phase; do not
assume that previously open deviations will be applied or replayed automatically.

## Cloud qualification plan

One complete mixed TurboBulk/REST estate has passed strict Cloud readback. Its
29-kind qualification compiler is callable through `just load`. The compiler now
covers 102 kinds — every kind any current profile emits, the complete bank included — with all 57 kinds and references in the current enterprise
data center artifact, with content-type-safe generic relationships, deferred
many-to-many fields,
and resumable REST creation when a required model is absent from TurboBulk. A fresh write
reached all 38 successful TurboBulk jobs, then failed on a REST tag payload; the
same command and receipt resumed without duplicate jobs and reached exact strict
readback. A separate clean attempt stopped at the 900-second bound when its first
one-row TurboBulk job remained `running` with zero rows processed; that behavior
was later diagnosed as worker death and is now detected and arbitrated. Clean
end-to-end timing and the rich graph have since been measured on the pinned local
stack; both remain unqualified on Cloud. The configured
Cloud tenant runs NetBox 4.6.8. Its API cannot represent `module_bay_type` or the
related compatibility fields, which NetBox [introduced in 4.7](https://github.com/netbox-community/netbox/discussions/22950).
Read-only preflight therefore rejects the exact 57-kind artifact before writes.
The 4.7 rich path is live-qualified only on the pinned local 4.7.1 stack
(see [the rich-contract qualification](qualification.md#rich-contract-live-qualification-and-merge-findings));
Cloud and Enterprise remain unqualified.
The target-aware selector and checkpointed remote Diode adapter are implemented,
but Cloud Diode completion remains unqualified. The
first Diode probe established that the tenant execution mode is a prerequisite
for meaningful timing. The [Cloud setup guide](https://netboxlabs.com/docs/discovery/getting-started/#netbox-cloud-setup)
documents managed Diode, enablement and client credentials. Confirm access on the
chosen disposable tenant and copy its actual Diode target from its settings.
Use Diode credentials for ingestion and a read-only NetBox API token for readback.
The selector currently requires the target NetBox and Diode plugin versions to
match the versions recorded in the artifact's source-checked manifest. Endpoint
probes also reject missing model families before writes. This is deliberately
conservative until version-specific projections have their own validation.

1. **Compatibility:** record the target NetBox/Diode/plugin versions and check
   the exported model families and matching behavior. Test passive port mappings
   early: local qualification used an [explicit compatibility patch](../lab/README.md#opt-in-local-front-port-compatibility).
   Cloud support for those relationships must be established on its actual stack.
   Record unsupported features explicitly; direct cabling can qualify that mode,
   but cannot establish support for panel paths.
2. **A complete representative estate:** generate a fresh baseline and ingest
   every dependency phase. Read back emitted attributes and relationships, inspect
   cable paths and service placement, and walk the estate in the Cloud UI.
   Repeat the same artifact and check stable IDs and absence of duplicates.
3. **A complete estate above 50,000 objects:** increase industry demand within
   the reviewed construction limits, retaining its meaningful connected detail.
   Measure submission, reconciliation and readback separately, through completion;
   retain object/wire counts, target versions, Assurance-review versus direct
   auto-apply mode, timings and any errors.
4. **Operational reliability:** test interruption and recovery using the existing
   replay path, then verify inventory and the cross-site walkthroughs again.
   Treat safe recovery as unproven until that target test passes.

These gates qualify the whole estate as both a coherent model and usable Cloud
inventory. Smaller compatibility probes are preparation for that result.

## TurboBulk as a scale transport

TurboBulk is a candidate accelerator for initial population, not another
canonical dataset. Keep `plan.json` as the source and derive temporary,
target-specific TurboBulk files at load time. The [TurboBulk API](https://netboxlabs.com/docs/turbobulk/api-reference/)
accepts one NetBox model per asynchronous JSONL or Parquet job. Foreign keys are
integer target IDs, so an adapter must load in dependency order, resolve IDs after
each completed job, and translate generic relationships into object and content
type IDs. Cables require separate cable and termination rows; see the
[official cable example](https://github.com/netboxlabs/netbox-turbobulk-public/blob/a8ec11ca55894f37524de40d2dfaf42efd9748cd/examples/05_cable_connections.py).

Genial submits at most 2,000 rows per TurboBulk job. The client boundary protects
a model group even when it is smaller than the plugin's server-side chunk setting.
Every deterministic batch has its own purpose, key set, payload hash, job ID,
timings, and hook request in the receipt. A resume polls an acknowledged job to a
verified terminal result and skips already verified jobs. Target IDs are resolved
once after the model's batches, avoiding a whole-table REST scan after every batch,
then become the dependency checkpoint for later models. Table-wide hooks do not run
on intermediate batches, and cable-link and path rebuilding wait until every
generated termination is present. For an evidence-driven experiment, the optional
fourth argument changes the bound: `just load ARTIFACT TARGET BRANCH 1000`. A
different bound requires a fresh branch and receipt. For JSONL the loader rejects
any bound outside 1..10,000: TurboBulk's JSONL reader fixes the column set from
the first 10,000 rows, so a sparse payload spanning chunks would silently drop
later columns.

Data-bearing jobs upload Parquet when pyarrow is available (the devenv shell
provides it); the optional fifth argument forces a format:
`just load ARTIFACT TARGET BRANCH 2000 jsonl`. TurboBulk's Parquet reader takes
the column set from file metadata, so the JSONL constraint does not apply and the
bound may rise to 1..50,000 — a ceiling on server memory at job end, not a format
rule. The client compiles the column union over every row explicitly and lets
PyArrow apply the same type inference the server's own JSONL reader uses, so a
Parquet job is behaviorally the JSONL job — and allows fewer, larger batches
when the bound is raised; the server routes both formats into the identical
staging COPY. Zero-row finalizers always
stay JSONL, each job's receipt entry records its `upload_format`, and the receipt
binds the format for the whole load — resuming under a different format requires
a fresh branch and receipt (receipts written before the Parquet writer count as
JSONL). Rows carrying `_tags` or JSON objects outside `custom_field_data` are
refused rather than silently reshaped; none of the current contract's TurboBulk
kinds emit either.

### Scale-load risks — read before any load over ~30k rows

Every item below was learned the hard way on a live Cloud tenant (2026-09-21);
the loader now enforces the first one.

1. **Changelogs at scale are a one-way door.** TurboBulk's own user guide says
   to disable changelogs for "large imports (>100K rows) where changelog table
   growth is a concern" and for "ephemeral or test data". A reviewable load
   writes one Branching ChangeDiff per row; on Cloud, branch deletion performs
   `DROP SCHEMA … CASCADE` plus that diff cascade in one synchronous HTTP
   request, which dies at scale and rolls back — the branch then **cannot be
   deleted through the API at all** and needs platform-side removal (four
   stranded branches proved it). The loader refuses a reviewable load over
   100,000 rows; use `just load-disposable` for throwaway scale loads, or set
   `GENIAL_REVIEWABLE_SCALE=1` only for deliberate qualification on a target
   you control end to end. Deletes were observed to succeed up to ~21k rows
   and fail from ~104k on one Cloud tenant; between is unmeasured.
2. **Search reindex finalizers can OOM small containers.** The public guide
   recommends disabling `rebuild_search_index` above 100K rows; a zero-row
   reindex finalizer over 13,383 cables in a 155,698-row schema was killed at
   107s on a 2000Mi Cloud worker. No skip flag exists in the loader yet; size
   the container or keep scale loads off small tenants.
3. **Branches that exist across a NetBox upgrade go `pending-migrations`** and
   large ones can then neither migrate nor be deleted. Delete branches before
   upgrading the target.
4. **Never delete a job row in the NetBox Jobs UI.** The bound job ID is the
   loader's only proof of what a killed job committed; deleting the row makes
   the receipt unresumable and forces a fresh branch.
5. **A stalled loader with near-zero CPU may be your network, not the target.**
   Python's urllib tries IPv6 first with no happy-eyeballs fallback and no
   connection reuse; a broken IPv6 path taxes every request with a full connect
   timeout. Set `GENIAL_FORCE_IPV4=1` to route the loader over IPv4 (compare
   `curl -6`/`curl -4` against the target to confirm the diagnosis first).

Read-only API requests retry transient disconnects four times with bounded
backoff. Mutating requests are never retried automatically; their durable intent
and returned job ID remain the resume boundary.

The first bounded 128,932-object Cloud attempt still stranded. Seventy-three
bounded jobs completed and verified 104,119 objects. A final 52-row power-port
batch committed every row, passed validation, and created every requested
changelog, then remained `running` without post-hook results. This was read at
the time as an unbounded model-wide post-hook; the later
[worker-death investigation](qualification.md#cloud-worker-death-investigation-and-loader-hardening)
disproved that — the worker container was killed mid-job, and the byte-identical
batch completed in 1.5 seconds on a fresh branch. Genial adopted the zero-row
finalizer split anyway. Recovery no longer means abandoning the branch: polling
now reaches a worker-death diagnosis from the target's RQ record, and once the
reaper marks the row a resume arbitrates it from exact create-ChangeDiff counts.
Do not mark committed rows complete solely from counters.

TurboBulk supports branch-targeted jobs, making a disposable branch the rollback
boundary for a multi-model estate — except the Branching-exempt main-scoped rows,
which a branch-scoped load writes to main and which survive branch deletion and
`just reset`: NetBox 4.7 `owner`/`owner_group`, the custom-field, choice-set and
custom-link definitions, and the automation export templates, webhook and event
rule. (Config contexts are *not* exempt — `get_branchable_object_types()` lists
`extras.configcontext` — so they live in the branch and go with it.)
TurboBulk does not make every model idempotent:
upsert depends on target database constraints, and some generated identities do
not have a suitable unique constraint. Resume only from verified completed-job
receipts; restart an ambiguous phase on a fresh branch. A new receipt requires all
emitted-kind inventories to be empty, with two recorded exceptions whose
plain-attribute identities must all be disjoint from the plan's: declared builtin
kinds (currently `module_type_profile`) and the main-scoped rows listed above
that another estate left on a shared 4.7 target. Both are allowlisted by exact
id and identity in the receipt and honored by strict readback, so distinct
namespaces coexist on one target. An identity collision is still a hard block —
clear the same namespace's leftovers with `just retire`, which walks those
endpoints in dependency order (event rules, webhooks and export templates first,
then any custom-field/choice-set/custom-link definitions the profile emits (bank only today), then `/api/users/owners/` and
`/api/users/owner-groups/`). `just load-explain` reports the target's occupancy
with this exact allowlist assessment before any write. The loader assumes
exclusive use of that disposable branch while the receipt is active.

The September 12, 2026 Cloud qualification found TurboBulk 0.3.0 and 165
discoverable models on NetBox 4.6.8. After writes were enabled, all 8,432
canonical v0.2 objects were loaded into a disposable branch. TurboBulk created
10,514 database rows in 38 successful model jobs: the canonical objects plus
2,082 cable-termination rows. In the supported-loader receipt, their recorded
server durations total 131.244 seconds and their job lifecycles total 132.249
seconds. REST completed 1,031 object updates for primary IPs, tags, and 1,588
tagged-VLAN memberships across 364 interfaces. Strict branch readback then
matched all 8,432 objects, 32,203 attributes, and 17,783 references with zero
mismatches, and all 1,041 generated cables passed native path traces.

The first experiment used manual restarts. The supported loader then proved
checkpoint recovery after its REST tag payload was rejected: rerunning the same
`just load` command reused all 38 terminal jobs, completed the remaining REST
work, and reached exact readback. That successful recovery invocation took
435.128 seconds, including ID revalidation, REST completion, full readback, and
all cable traces; it is not a clean fresh-load throughput result. A later fresh
branch attempt timed out safely after 919.739 seconds because its first one-row
job remained server-side `running` with zero rows processed for the 900-second
poll window. A later one-shot inspection stored that state in the receipt. That
class of strand is now diagnosed from the target's RQ record after 60 seconds and
arbitrated on resume once the reaper marks the row, so it no longer requires
service-side cleanup or a new branch by default.

The work exposed target-specific TurboBulk 0.3.0 issues around
branch dry-run rollback, simultaneous save hooks and changelogs, branch tag
associations, and a model default bypassed by raw insertion. See the complete
[transport findings and implementation path](transports.md#what-the-first-cloud-run-taught-us).

The public guide names Parquet the fastest format for 100K+ rows, and the
loader now defaults to it whenever pyarrow is available (see above); JSONL
remains the dependency-free fallback and the zero-row finalizer format.
Record job queue time, server duration, relationship completion,
cable-path rebuilding, and final readback separately. A high table-row rate by
itself is not a successful connected-estate load. See the [TurboBulk branching
guide](https://netboxlabs.com/docs/turbobulk/branching/).
