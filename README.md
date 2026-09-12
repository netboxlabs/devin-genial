# Genial

Generate a believable, connected NetBox estate from a small recipe. Target-aware
loading is in active qualification. Start with a regional bank, data center,
school district, hospital network or provider backbone and change the demand to
suit your customer.

**The goal is the whole estate.** Sites, rooms, racks, devices, ports, cables,
address plans, services and operational context should make sense together.
A demo can follow one branch, workload or customer circuit through that larger
world. The relationships give the story substance.

Generation uses deterministic Python rules. Demand drives equipment and capacity;
shared builders place and connect it; independent checks inspect the finished
graph. No AI or API key is needed to generate. An agent can help operate the tool
and edit recipes.

## Generate your first estate

With Python 3.11+ installed:

```sh
git clone https://github.com/netboxlabs/devin-genial.git
cd devin-genial
python3 -m estates plan profiles/bank.toml
python3 -m estates generate profiles/bank.toml --out build/my-bank
python3 -m estates check build/my-bank/plan.json
```

Open **`build/my-bank/report.md`**. It walks through the topology, address plan,
rack elevations, service placement and questions to explore. The default bank
has two data centers, a headquarters and seven branches.

Copy a recipe and change its demand to create your own estate. Use a new output
directory for each build; existing artifacts are never overwritten. The
[generation guide](docs/usage.md) covers recipe options, repeatable growth and
optional devenv/Just setup. All commands run from the repository root.

## Choose an industry

| Estate | Start with | What shapes it |
| --- | --- | --- |
| Regional bank | [bank.toml](profiles/bank.toml) · [guide](docs/modeling.md#what-makes-it-a-bank) | Branch mix, headquarters staffing, inherited equipment and shared DC services |
| Enterprise data center | [enterprise-dc.toml](profiles/enterprise-dc.toml) · [guide](docs/usage.md#enterprise-data-center) | Workload demand, replicas, placement and compute capacity |
| School district | [school-district.toml](profiles/school-district.toml) · [guide](profiles/school-district.md) | Classrooms, enrollment, wired seats, wireless demand and district services |
| Hospital and clinics | [hospital-clinics.toml](profiles/hospital-clinics.toml) · [guide](profiles/hospital-clinics.md) | Wards, clinics, medical endpoints, support responsibilities and shared services |
| Provider backbone | [provider-backbone.toml](profiles/provider-backbone.toml) · [guide](profiles/provider-backbone.md) | PoPs, customer premises, private-L3 services and purchased transport |

These are configurable industry models with explicit construction limits.
Additional industries need reviewed rules and checks. See [how the generator
works](docs/modeling.md#procedural-design-without-ai) and the
[coverage review](COVERAGE.md) for current depth and gaps.

## Load it and tell the story

Each build includes the frozen graph, a walkthrough, validation results and a
phased Diode export. Follow the [Diode loading guide](docs/loading.md) for a
configured target, or the [local lab guide](lab/README.md) to use the disposable
NetBox/Diode environment. Generation itself does not write to NetBox.

The Justfile is also the loading interface. The public recipes require `just`;
a Diode load additionally requires the
devenv-managed SDK environment described in the [loading guide](docs/loading.md).
Inspect a target without writing:

```sh
cp .env.example .env
# Put the raw nbt_... value in NETBOX_TOKEN; do not include "Bearer".
just load-explain build/my-bank https://netbox.example "Generator Review"
```

The target is the NetBox root URL, without `/api/`, a plugin path, credentials,
query parameters or fragments. In `.env`, enable `TURBOBULK_WRITES=1` or fill
the documented Diode attestation for the transport that target actually uses.
The result gives a preliminary transport choice and known compatibility blockers.
Run `just load build/my-bank https://netbox.example "Generator Review"` to perform the adapter's full
read-only schema/package preflight, then load it and write the default private
receipt under `build/`. That second preflight can find additional blockers and
still stops before target writes.

`just load` uses the reviewable policy. TurboBulk retains ObjectChanges and
ChangeDiffs needed to review and merge the branch and to revert it after merge.
It requires a dedicated branch with zero initial ChangeDiffs. After strict graph
readback, the loader verifies the exact total and create-ChangeDiff counts by
model, including cable terminations, and records that evidence in the receipt.
For a load-only scale test on a newly reset, empty branch, use the explicit
disposable command:

```sh
just load-explain-disposable build/my-bank https://netbox.example "Scale baseline"
just load-disposable build/my-bank https://netbox.example "Scale baseline"
```

That branch cannot be reviewed, merged, or reverted. The command says so before
writes; use the data in place and delete the branch afterward. This mode accepts
only artifacts that TurboBulk can write completely: if any object or relationship
needs REST creation or completion, `load-explain-disposable` reports it and the
loader refuses before target writes. The current rich and scale artifacts need
REST completion, so they must use the reviewable policy until NetBox provides a
way to make those REST writes non-reviewable under the same branch contract. Both
policies keep full validation and schedule every required TurboBulk post-hook at
its safe dependency boundary. Genial limits each job to 2,000 rows, skips
table-wide hooks on intermediate batches, runs denormalization, search, and
counters on the last batch for each model, and runs cable-link and cable-path
work only after the final cable-termination batch. A completed job is accepted
only when it reports the exact enabled or explicitly skipped hook results and,
for reviewable inserts, one changelog per inserted row. The policy, row bound,
and exact per-job settings are bound to the receipt, so changing them requires a
new receipt and fresh branch. Keep the default unless a measured qualification
run justifies the optional fourth `turbobulk_job_rows` argument to `just load`.
Device-component placement caches are compiled into the original insert from
the parent device, because the device denormalization hook runs before later
interfaces, ports, outlets, and bays exist. Before writes, the target OpenAPI
schema must expose the three cache-backed placement filters on every emitted
component kind. Final readback then compares exact component IDs with one query
per distinct component-kind and site/location/rack placement, including explicit
null location and rack placement. This adds bounded readback requests without
adding repair rows, jobs, ObjectChanges, or ChangeDiffs.
These setup steps should take less than ten minutes; target processing time is
separate and is recorded in the private receipt.

To empty a disposable Cloud branch for another run, replace that branch and wait
for its new schema to become ready:

```sh
just reset https://netbox.example "Disposable branch"
```

This permanently deletes only an exact named `ready` branch after a read-only
create-permission and rename/delete-capability preflight. It refuses to delete while a
pending TurboBulk job exists, or while a running job targets that exact branch or
lacks branch metadata. It first moves the old branch to a receipt-bound quarantine
name, waits for those jobs to drain, then deletes it, creates a uniquely named
replacement, and archives its matching local load receipts as qualification history. It refuses
a blank or `main` scope. If draining times out, the receipt preserves the
quarantine name and the same command resumes by the old immutable branch ID.
Use the printed replacement name for the next load and future reset. If Diode routes
to the branch schema ID, copy the new ID into `DIODE_BRANCH` and refresh the
configuration attestation before loading.
The TurboBulk adapter is Cloud-qualified for the frozen 29-kind contract and now
compiles the current 53-kind enterprise data center contract. The configured
NetBox 4.6.8 tenant cannot represent the 4.7-only module-bay compatibility model,
so preflight rejects that exact rich artifact before writes; its complete 4.7
path still needs live qualification. The remote Diode adapter is implemented for
externally confirmed direct auto-apply, with request checkpoints and strict REST
visibility barriers, but still needs a live qualification run. Assurance review
is not executable because the ingestion
API cannot enforce that tenant mode. General REST-only loading remains work in
progress. See the [transport model](docs/transports.md#one-command-several-internal-steps)
for configuration, safety boundaries, and evidence.

Existing [scenario guides](docs/scenarios.md) cover branch acquisition and refresh,
a power-diversity defect, and provider span maintenance. Each derives its subjects
and relationships from the estate and explains which changes have been qualified
for live replay.
For a worked customer story, try [Harbor Supply](profiles/harbor-supply.md).

**Scale has separate generation and loading proofs.** Recorded offline generation
reaches 239,058 objects. A 12,702-object estate has passed local Diode qualification,
and an 8,432-object estate has passed strict readback after a Cloud TurboBulk load.
Larger live loads and Enterprise targets remain unqualified. See the
[scale measurements](docs/qualification.md#current-offline-scale-evidence) and
[live results](lab/README.md#current-v09-qualification) for scope and limits.
The next gate is resolving the observed stuck-job behavior and obtaining a clean
one-command Cloud run, followed by an estate above 50,000 objects; see the
[qualification plan](docs/loading.md#cloud-qualification-plan).

## Documentation

| I want to… | Read |
| --- | --- |
| Generate, configure or grow an estate | [Usage](docs/usage.md) |
| Understand the rules and connected detail | [Modeling](docs/modeling.md) |
| Run a change or defect demonstration | [Scenarios](docs/scenarios.md) |
| Choose Diode, TurboBulk or REST | [Transport model](docs/transports.md) · [Loading](docs/loading.md) |
| Check scale, compatibility and historical evidence | [Qualification](docs/qualification.md) · [Community comparison](COMPARISON.md) |
| Extend the generator | [Development](CLAUDE.md) · [Graph contract](CONTRACT.md) · [Hardware catalog](catalog/README.md) |
| See remaining gaps or prior acceptance criteria | [Coverage](COVERAGE.md) · [Completed goal](GOAL.md) |

Generated datasets, credentials and historical receipts under `build/` are local
outputs and are not shipped in the repository.
