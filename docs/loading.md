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
just load-explain build/my-estate https://netbox.example "Generator Review"
just load build/my-estate https://netbox.example "Generator Review"
```

The target argument is the NetBox root origin. The optional branch is required
when Branching is installed; a target without Branching requires the explicit
`ALLOW_MAIN_WRITES=1` guard. The loader prints its stable private checkpoint
receipt before writes so the same command can resume safely.
Read-only target discovery, pagination, and job polling retry transient connection
failures with bounded backoff. Write requests are never retried after an ambiguous
transport failure; their recorded intent must be inspected or resumed instead.

The default `just load` policy retains the TurboBulk changelogs and branch diffs
needed for review, merge, and post-merge revert. It requires zero ChangeDiffs on
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
jobs are limited to 2,000 rows. Intermediate batches explicitly skip all table-wide
hooks; the last batch for each model runs denormalization, search indexing, and
counters; cable-link repair and cable-path rebuilding run only after the last
cable-termination batch. The loader requires an exact result for every enabled or
skipped hook and requires reviewable insert jobs to report one changelog per row.
Receipts bind the selected policy, row bound, payloads, and per-job request settings
and reject a resume under different settings.

Device components need one extra invariant. TurboBulk's device denormalization
hook can run before later component phases, so Genial writes `_site_id`,
`_location_id`, and `_rack_id` into each component's original insert from its
parent device. Preflight requires `site_id`, `location_id`, and `rack_id` filters
on every emitted component REST endpoint. Strict readback groups expected IDs by
component kind and placement and makes one query per group, including `null`
location and rack values. This proves the caches without another mutation, so
reviewable ChangeDiff counts remain exactly one create per canonical object.

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
archives matching local load receipts under `build/load-receipts/history/`, creates
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

The export contract is pinned to **netboxlabs-diode-sdk 1.14.0**. The optional
devenv `diode` profile installs it in a devenv-managed environment:

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
also covers all 53 kinds and references in the current enterprise data center,
including content-type-safe generic relationships, deferred many-to-many fields,
and resumable REST creation when a required model is absent from TurboBulk. A fresh write
reached all 38 successful TurboBulk jobs, then failed on a REST tag payload; the
same command and receipt resumed without duplicate jobs and reached exact strict
readback. A separate clean attempt stopped at the 900-second bound when its first
one-row TurboBulk job remained `running` with zero rows processed. A clean
end-to-end timing and the current rich graph remain unqualified. The configured
Cloud tenant runs NetBox 4.6.8. Its API cannot represent `module_bay_type` or the
related compatibility fields, which NetBox [introduced in 4.7](https://github.com/netbox-community/netbox/discussions/22950).
Read-only preflight therefore rejects the exact 53-kind artifact before writes.
The 4.7 rich path is implemented and offline-tested but remains live-unqualified.
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
different bound requires a fresh branch and receipt.

Read-only API requests retry transient disconnects four times with bounded
backoff. Mutating requests are never retried automatically; their durable intent
and returned job ID remain the resume boundary.

TurboBulk supports branch-targeted jobs, making a disposable branch the rollback
boundary for a multi-model estate. It does not make every model idempotent:
upsert depends on target database constraints, and some generated identities do
not have a suitable unique constraint. Resume only from verified completed-job
receipts; restart an ambiguous phase on a fresh branch. The qualification
prototype requires all emitted-kind inventories to be empty before a new receipt
and assumes exclusive use of that disposable branch while the receipt is active.

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
poll window. A later one-shot inspection stored that state in the receipt. Do not
blindly repoll a stuck job: inspect it once, and if it has not progressed, arrange
service-side cleanup or start with a new branch and receipt.

The work exposed target-specific TurboBulk 0.3.0 issues around
branch dry-run rollback, simultaneous save hooks and changelogs, branch tag
associations, and a model default bypassed by raw insertion. See the complete
[transport findings and implementation path](transports.md#what-the-first-cloud-run-taught-us).

Start with compressed JSONL. The public guide recommends Parquet primarily for
larger repeated loads; add that dependency only if measurements show file parsing
is material. Record job queue time, server duration, relationship completion,
cable-path rebuilding, and final readback separately. A high table-row rate by
itself is not a successful connected-estate load. See the [TurboBulk branching
guide](https://netboxlabs.com/docs/turbobulk/branching/).
