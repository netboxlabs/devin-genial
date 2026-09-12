# From a believable graph to NetBox

[Back to the quick start](../README.md) · [Loading details](loading.md) · [Qualification evidence](qualification.md)

Genial has two independent responsibilities. It first designs and verifies a
believable organization as a canonical graph. It then delivers that graph to a
specific NetBox target without changing what the organization means.

Keeping those responsibilities separate is essential. A bank does not become a
different bank because one target has TurboBulk and another has Diode. Target
database IDs, plugin versions, and reconciliation modes belong in load receipts,
not in the generated estate.

## The three layers

1. **Design:** a recipe describes demand, footprint, services, conventions, and
   customer-specific constraints. Deterministic rules construct the sites,
   equipment, capacity, addressing, connectivity, operations, and narrative.
2. **Canonical graph:** `plan.json` contains stable natural identities,
   attributes, references, design ledgers, and assertions. Independent checks
   reject hard reference errors and modeled semantic failures before a target is
   touched.
3. **Delivery:** a target-aware loader compiles that graph for Diode, TurboBulk,
   REST, or a combination, records checkpoints, and proves the resulting NetBox
   state through strict readback.

An agent may turn a customer conversation into a proposed recipe and operate the
loader. The rules and validators still produce the graph. This keeps generation
repeatable and makes a surprising result explainable.

## What each transport does

| Concern | Diode | TurboBulk | REST |
| --- | --- | --- | --- |
| Input identity | Nested natural keys | Numeric database IDs | Numeric IDs for relations; natural-key filters for lookup |
| Unit of work | Bounded graph observation | One model per asynchronous job | One object or one model-specific bulk request |
| Dependency handling | Reconciler resolves references; Genial still submits phases | Genial orders jobs and resolves IDs | Genial orders requests and resolves IDs |
| Repeated input | Reconciliation uses observations and matching | Upsert only where a safe database constraint exists | Explicit create/update behavior |
| Cables | One graph entity with typed ends | Cable row plus two termination rows | Native API representation |
| Completion signal | SDK acceptance, then reconciliation or deviation/application, then readback | Terminal job, hook inspection, then readback | HTTP result, then readback |
| Best fit | Portable source-of-truth ingestion and repeated observations | High-volume initial population on an enabled target | Compatibility work and bounded relationships other transports cannot express |

The [Diode protocol](https://github.com/netboxlabs/diode/blob/diode-reconciler/v2.2.0/diode-proto/diode/v1/ingester.proto)
accepts natural-key graph entities. Genial can render the same canonical
relationships without learning target primary keys. Submission acknowledgement
only means the Ingester accepted the request. With Assurance, differences become
deviations until applied; direct auto-apply removes that review step. In both
modes, final REST readback is a separate proof. See the [Assurance deviation
workflow](https://netboxlabs.com/docs/assurance/deviations/) and the published
[Diode data flow](https://netboxlabs.com/docs/enterprise/helm/configuration/diode/).

The [TurboBulk API](https://netboxlabs.com/docs/turbobulk/api-reference/) accepts
JSONL or Parquet for one database model at a time. It bypasses normal per-object
REST creation, which makes large table loads attractive, but moves more work into
Genial. The loader must inspect target schemas, supply target defaults, translate
foreign keys to IDs, split cables from their terminations, run dependency barriers,
inspect post hooks, and finish relationships the advertised model schema cannot
write. Each model job is transactional; the full estate is not one transaction.
A disposable [NetBox branch](https://netboxlabs.com/docs/turbobulk/branching/) is
the practical whole-run rollback boundary.

REST is the compatibility layer and the universal fallback. NetBox supports
[atomic bulk updates within one model](https://netbox.readthedocs.io/en/stable/integrations/rest-api/#updating-multiple-objects), so Genial should batch bounded completion
work rather than issue one request per object. REST remains slower for creating a
large connected estate, but it exposes normal model validation and is available
on every supported edition.

## One command, several internal steps

The supported operator interface is the existing Justfile:

```sh
just load build/bank-v2 https://netbox.example "Generator Benchmark"
```

Use the shorter read-only explanation recipe before a write:

```sh
just load-explain build/bank-v2 https://netbox.example "Generator Benchmark"
```

This is preliminary target and transport discovery. The selected adapter performs
its complete package/schema preflight again before submission; that deeper check
can still reject the run without target writes.

The command chooses a stable private receipt path from the artifact, target and
branch, prints it before loading, and reuses it when the same operation resumes.
Advanced troubleshooting can invoke `python3 -m estates.load --help` to override
the receipt, transport or timeout without expanding the ordinary SE command.

The frozen `build/bank-v2` artifact is preserved local qualification evidence;
it is not shipped in a fresh clone. `TARGET` is the non-secret NetBox root origin;
the loader rejects API/plugin paths, embedded credentials, queries and fragments.
The command reads `NETBOX_TOKEN` from the environment or ignored `.env`. Store
the raw token value there without an authorization prefix. `DIODE_TARGET` is the
separate ingestion endpoint and never substitutes for the NetBox target argument.
The loader resolves the branch schema ID from its human name and writes a private
checkpoint/receipt after every job and REST batch. Repeating the command is a
verified no-op when strict readback already matches the artifact. Interrupted
runs resume only from a receipt bound to the same artifact, target, and branch.
After target binding and initial readback, the current receipt format retains
each invocation's outcome and wall time, so future successful resumes do not
erase the failed attempt that preceded them. The historical recovery receipt
below predates this attempt-history field.
If submission reaches the server but its job ID never reaches the receipt, the
command refuses to adopt those identities; discard that branch and begin with a
new branch and receipt.
For a new receipt, every inventory represented by the artifact must be empty;
otherwise the command stops before writes. Treat the branch as exclusively owned
by that receipt until loading and verification finish.
Never pass a token on the command line or store one in a recipe, artifact,
receipt, source file or shell history.

Transport selection is deterministic. TurboBulk is eligible only when the plugin
is present, `TURBOBULK_WRITES=1`, a disposable branch was named, and the entire
artifact fits the qualified compiler. Diode is eligible only with complete
credentials, explicit write enablement, externally confirmed direct auto-apply
and branch scope from the last 24 hours, matching source-checked target versions,
and working REST endpoints for every emitted kind. The Diode ingestion API does
not control or introspect its downstream NetBox branch or Assurance mode, so the
loader records that operator confirmation as an external boundary. It does not
execute Assurance-review mode. A standalone REST loader is not implemented yet,
so the selector reports that gap rather than silently omitting unsupported objects.

The frozen v0.2 bank qualifies 29 canonical kinds through TurboBulk on Cloud.
The compiler now covers all 53 kinds in the current enterprise data center,
including REST relationship completion and resumable REST creation for a model
absent from TurboBulk. The configured NetBox 4.6.8 target lacks that 4.7 model
entirely, so it rejects the artifact before any write. The complete rich path
needs live qualification on a target that exposes the model. The remote Diode
adapter can execute the richer generated package, checkpoint each dependency
phase, wait for target visibility, and strictly read back the final graph. Its
live remote run also remains unqualified. A general REST-only loader remains
future work.

The command performs these steps internally:

1. Inspect NetBox and installed TurboBulk and Branching versions, the ready
   target branch, writable TurboBulk models, and relevant schema fields.
2. Compare every canonical field and relationship in the supported 29-kind
   contract with an explicit translation.
   Fail before writes if any intent would be silently dropped.
3. Select TurboBulk plus bounded REST completion for the currently qualified
   model set, or Diode when its complete target contract is satisfied.
4. Execute dependency barriers. After each job, verify counts, hook results, and
   canonical-key-to-target-ID mappings, then save a checkpoint.
5. Close cycles such as device and VM primary IPs. Expand every cable into two
   typed terminations and rebuild cable paths. Complete many-to-many relations in
   bounded bulk REST requests.
6. Fetch complete inventories for every emitted kind with stable pagination,
   compare every emitted attribute and reference with `plan.json`, and trace one
   origin for every generated cable through the native REST action. Success
   requires zero unexplained objects of those kinds, omissions, duplicates, or
   relationship mismatches.

The operator gets one command and one final verdict. The private receipt keeps the
internal steps visible so an interrupted run can resume from a proven checkpoint
or explain why a fresh branch is required. A literal single HTTP upload cannot
provide these guarantees across all three transports.

## What the first Cloud run taught us

On September 12, 2026, the exact v0.2 regional-bank artifact was qualified on
NetBox Cloud 4.6.8 with TurboBulk 0.3.0 and a disposable branch. The artifact has
8,432 canonical objects across 29 model families, including 4,429 interfaces,
1,041 cables, and 1,588 tagged-VLAN memberships.

TurboBulk created 10,514 rows in 38 successful jobs: 8,432 canonical objects and
2,082 cable terminations. The supported-loader receipt records 1.005 seconds total
queue time, 131.244 seconds total server execution, and 132.249 seconds from job
creation to completion. These are summed job lifecycles, not end-to-end loader
time.

The run found several correctness traps:

- Writes are disabled by default and must be enabled before a load benchmark.
- A branch-scoped dry run in this installed version processed its row but reported
  a rollback error because it set the rollback flag on the wrong database alias.
  The row did not persist.
- Enabling save hooks and changelogs together failed for a long model table name
  because generated temporary names collided after PostgreSQL identifier
  truncation. The experiment disabled save hooks, supplied required relationships
  directly, and retained full validation, changelogs, and post hooks.
- Raw VM insertion bypassed the NetBox
  [`start_on_boot=off` model default](https://github.com/netbox-community/netbox/blob/v4.6.8/netbox/virtualization/models/virtualmachines.py)
  and left
  null values that later blocked primary-IP updates. A target-aware compiler must
  materialize required defaults instead of trusting database nullability.
- `_tags` was accepted but produced no branch tag associations. Strict readback
  found exactly 667 missing tag references, which bounded bulk REST repaired.
- The live interface schema had no writable `tagged_vlans` field. REST completed
  those memberships. A provisional one-object loop took minutes; bounded bulk
  requests reduced similar completion work to seconds per batch.
- Loading cable rows alone linked no ports. Loading 2,082 termination rows then
  updated 2,062 endpoint caches and rebuilt paths for 2,062 endpoints. Terminal
  job status alone would not have proved those hook results.
- Default REST ordering was not unique enough for safe offset pagination. Explicit
  `ordering=id` removed duplicate-page identities during recovery.

The final strict branch readback matched all 8,432 canonical objects, all 32,203
expected attributes, and all 17,783 expected references with zero mismatches. The
supported loader's readback took 72.214 seconds, followed by native traces of all
1,041 generated cables in 34.987 seconds. Receipts remain private, ignored build
artifacts because they contain target identifiers.

The supported loader also proved its recovery boundary. During development, a
fresh write completed all 38 TurboBulk jobs and then received HTTP 400 because
its first REST completion payload referenced a tag by slug instead of numeric
ID. That error was observed live but is absent from the older success receipt.
After correcting the compiler, the same `just load` command and receipt
revalidated the recorded IDs, submitted no duplicate TurboBulk jobs, completed
1,031 REST object updates, and reached exact readback in 435.128 seconds. That is
a recovery-attempt wall time, not a clean initial-load measurement.

A separate fresh branch exposed another operational failure. Its first one-row
TurboBulk job started immediately but remained `running` with zero rows processed.
The loader stopped after its configured 900-second polling window; total attempt
time was 919.739 seconds including preflight. A later single API inspection,
stored in the receipt, still reported `running` and zero rows; a live OPTIONS
response advertised no job actions. The loader now records the last observation,
uses a capped polling backoff, and tells the operator to inspect once. A job that
remains stuck requires service-side cleanup or a new branch and receipt. The
compiler contract changed after this experiment, so this old branch is retained
as failure evidence rather than resumed. This is why clean end-to-end throughput
remains unqualified.

These results prove that a faithful mixed TurboBulk/REST load and safe checkpoint
recovery are feasible. They do
not yet prove that TurboBulk is faster than Diode end to end. The existing local
Diode initial load took 239.817 seconds and its repeat took 81.634 seconds, but it
ran against a different NetBox version and local hardware. The next clean test
must run a checkpointed one-command loader against fresh branches and measure the
same boundaries on both paths.

## Path beyond the qualification prototype

The prototype keeps `plan.json` and the current generator unchanged,
and reuses the canonical dependency scheduler and strict verifier. It already
generates compressed JSONL, checks live model columns, stores target IDs after
dependency barriers, batches REST completion, and records strict readback.
Remaining work is to:

- expand compiled model coverage from v0.2 to current rich artifacts;
- add explicit coexistence allowlists if customer POC branches must retain
  existing objects; the prototype requires empty emitted-kind inventories;
- make individual REST batches independently resumable instead of repeating the
  already idempotent completion comparison after interruption;
- retain clean-run evidence for compilation, upload, queue, job, REST completion,
  readback, and cable-trace timings after the stuck-job issue is resolved; and
- qualify the rich TurboBulk/REST and remote Diode direct/recovery paths on a matching 4.7 target;
- implement canonical REST creation for targets without a qualified plugin path.

Qualification should next resolve or operationally handle the stuck TurboBulk
job and prove a clean 8,432-object run, then a current rich
estate, then a believable estate above 50,000 canonical objects. Scale success is
the time to a verified usable organization, not a table-row rate.
