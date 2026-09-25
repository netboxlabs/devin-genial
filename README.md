# Genial

Generate a believable, connected NetBox estate from a small recipe, then load
it into a live NetBox branch and verify every object made it exactly. Start
with a regional bank, enterprise data center, school district, hospital
network, provider backbone, retail chain, university campus, managed service
provider, manufacturer or electric utility and change the demand to suit your
customer.

**The goal is the whole estate.** Sites, rooms, racks, devices, ports, cables,
address plans, services, operational context and automation inventory (config
contexts carrying the estate's own service endpoints, CSV export templates, and
an inert webhook with its disabled event rule) should make sense together.
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

Copy a recipe and change its demand to create your own estate. Sites carry
readable authored names, facility codes and map coordinates by default,
`[site_names]` overrides any site with the customer's real names, and
`[hardware]` selects the vendor line for the access, leaf and AP roles — see the
[recipe reference](docs/recipes.md#common-keys). Use a new output directory for
each build; existing artifacts are never overwritten. The
[generation guide](docs/usage.md) covers recipe options, repeatable growth and
optional devenv/Just setup. All commands run from the repository root.

## One command, twenty minutes before the call

You know the industry, the vendor in their racks and which product story is
being sold. `just demo` turns those three into a validated estate plus a
`DEMO.md` cheat sheet written from that estate's own data — and, given a target
and branch, a loaded and strictly verified branch in the same run:

```sh
just demo regional-bank "Acme Regional Bank" juniper assurance,automation
# …and to go live in the same run:
just demo msp "Acme Managed" default automation https://netbox.example demo-acme
```

Without a target it stops after the offline gates and the cheat sheet prints
the exact go-live commands. It composes existing commands and skips no gate:
generate, check, load-check, the feature packs, then branch, load and
verify-target. The [demo composer guide](docs/demo.md) has the flags, the
per-profile template sizes and the feature packs; it is the fastest path
through everything below.

## Choose an industry

| Estate | Start with | What shapes it | Loadable to a target today? |
| --- | --- | --- | --- |
| Regional bank | [bank.toml](profiles/bank.toml) · [guide](docs/modeling.md#what-makes-it-a-bank) | Branch mix, headquarters staffing, inherited equipment and shared DC services | **Yes** (NetBox 4.7+) |
| Enterprise data center | [enterprise-dc.toml](profiles/enterprise-dc.toml) · [guide](docs/usage.md#enterprise-data-center) | Workload demand, replicas, placement and compute capacity | **Yes** (NetBox 4.7+) |
| School district | [school-district.toml](profiles/school-district.toml) · [guide](profiles/school-district.md) | Classrooms, enrollment, wired seats, wireless demand and district services | **Yes** (NetBox 4.7+) |
| Hospital and clinics | [hospital-clinics.toml](profiles/hospital-clinics.toml) · [guide](profiles/hospital-clinics.md) | Wards, clinics, medical endpoints, support responsibilities and shared services | **Yes** (NetBox 4.7+) |
| Provider backbone | [provider-backbone.toml](profiles/provider-backbone.toml) · [guide](profiles/provider-backbone.md) | PoPs, customer premises, private-L3 services and purchased transport | **Yes** (NetBox 4.7+) |
| Retail chain | [retail-chain.toml](profiles/retail-chain.toml) · [guide](profiles/retail-chain.md) | Store formats, point-of-sale lanes, distribution centres and shared commerce services | **Yes** (NetBox 4.7+) |
| University campus | [university-campus.toml](profiles/university-campus.toml) · [guide](profiles/university-campus.md) | Academic buildings, residence halls, a library, dense per-zone wireless and shared campus services | **Yes** (NetBox 4.7+) |
| Managed service provider | [msp.toml](profiles/msp.toml) · [guide](profiles/msp.md) | One NOC operating many customer accounts, each its own tenant, with nothing shared between them | **Yes** (NetBox 4.7+) |
| Manufacturing | [manufacturing.toml](profiles/manufacturing.toml) · [guide](profiles/manufacturing.md) | Plants with separated plant-floor (OT) and corporate (IT) zones, a modeled conduit between them, and shared manufacturing services | **Yes** (NetBox 4.7+) |
| Utility | [utility.toml](profiles/utility.toml) · [guide](profiles/utility.md) | Control centers and substations, with the station (OT) zone separated from the corporate tier by one modeled conduit | **Yes** (NetBox 4.7+) |

Every profile generates, validates and reports offline. "Loadable" means the
whole estate loads into a live NetBox through TurboBulk today — run
`just load-check build/…` for the exact per-artifact verdict before picking a
demo profile.

School and hospital also have a locally qualified **Diode** procedure — see the
[local lab guide](lab/README.md) — which, unlike the TurboBulk branch-per-demo
pattern, needs a Diode-equipped target and one estate per target.

**Target still on NetBox 4.6 or older?** A working demo still has three shapes:
ship the generated `report.md` and a scenario walkthrough as the offline
industry deliverable; run the Diode lab path against a clean Diode-equipped
target for real screens; or load a TurboBulk-clean profile (enterprise-dc or
provider-backbone) shaped to the customer — for example an enterprise DC whose
workloads carry the customer's application names — and pair it with the
industry report.

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
New to loading? [First target](docs/first-target.md) is the start-to-finish
runbook: prerequisites, token shape, branch creation, load, verify.

Every profile loads completely on a NetBox 4.7+ target (module bay types
are unconditional), and `just load-check` remains the required first step —
it verifies your exact artifact against the compiler contract offline. Check
the artifact, then inspect the target without writing:

```sh
just generate profiles/enterprise-dc.toml build/my-dc
just load-check build/my-dc          # offline: does it fit the TurboBulk contract?
cp -n .env.example .env   # -n: never overwrite an existing .env
# Put the raw token value in NETBOX_TOKEN (Cloud tokens look like nbt_...;
# self-hosted tokens are plain hex). Do not include "Bearer".
just branch https://netbox.example "Generator Review"   # create the ready branch
just load-explain build/my-dc https://netbox.example "Generator Review"
```

The target recipes read `.env` only when `NETBOX_TOKEN` is not already exported, so
an exported caller environment wins over the file.

The target is the NetBox root URL, without `/api/`, a plugin path, credentials,
query parameters or fragments. In `.env`, enable `TURBOBULK_WRITES=1` or fill
the documented Diode attestation for the transport that target actually uses.
The result gives a preliminary transport choice and known compatibility blockers.
Run `just load build/my-dc https://netbox.example "Generator Review"` to perform the adapter's full
read-only schema/package preflight, then load it and write the default private
receipt under `build/`. That second preflight can find additional blockers and
still stops before target writes.

`just load` uses the reviewable policy. TurboBulk retains the ObjectChanges and
ChangeDiffs needed to review the branch and to revert it. Merging a TurboBulk
branch to main is currently blocked upstream; see the
[merge findings](docs/qualification.md#rich-contract-live-qualification-and-merge-findings).
It requires a dedicated branch with zero initial ChangeDiffs. After strict graph
readback, the loader verifies the exact total and create-ChangeDiff counts by
model, including cable terminations, and records that evidence in the receipt.
For a load-only scale test on a newly reset, empty branch, the explicit
disposable command exists — but note it accepts only TurboBulk-only artifacts,
and **no currently generated profile qualifies** (they all need REST creation
or completion, `build/my-dc` included), so today this path applies to trimmed
or historical artifacts:

```sh
just load-explain-disposable build/my-dc https://netbox.example "Scale baseline"
just load-disposable build/my-dc https://netbox.example "Scale baseline"
```

That branch cannot be reviewed, merged, or reverted. The command says so before
writes; use the data in place and delete the branch afterward. If any object or
relationship needs REST creation or completion, `load-explain-disposable`
reports it and the loader refuses before target writes.

For a dedicated visualization/analytics tenant whose data should live on main
rather than behind a branch selector, `ALLOW_MAIN_WRITES=1 just seed-main
ARTIFACT TARGET` loads a complete estate directly onto an empty main
(`seed-main-explain` is the zero-write preflight) — see
[seeding a dedicated tenant's main](docs/loading.md#seeding-a-dedicated-tenants-main)
for the guardrails and the essentially-permanent caveat. To give such a tenant
floorplan rack placements too, `just geometry PLAN OUT` derives a layout from
the same frozen plan — the estate's own authored rack coordinates where it has
them, a deterministic row layout elsewhere — and `seed-geometry` writes it
through the physical-geometry plugin — see
[floorplan geometry](docs/loading.md#floorplan-geometry-for-visual-explorer).

A few behaviors worth knowing before your first load; the
[loading guide](docs/loading.md) has the full mechanics:

- **Data uploads use Parquet automatically** when pyarrow is available (the
  devenv shell provides it), falling back to gzipped JSONL otherwise. Each data
  job defaults to 2,000 rows; an optional fourth `just load` argument raises it
  (up to 50,000 under Parquet, 10,000 under JSONL), and a fifth forces the
  format.
- **Reviewable loads over 100,000 rows are refused** with the reasoning and the
  remedies in the error — see the
  [scale-load risks](docs/loading.md#scale-load-risks--read-before-any-load-over-30k-rows)
  before planning anything that size.
- **Every load is checkpointed and resumable.** The private receipt under
  `build/` binds every job, payload and setting; a killed or interrupted load
  resumes losslessly with the same command, and nothing is ever blindly resent.
- **Success means strict readback passed**: every object, attribute and
  reference compared against the artifact, plus exact per-model change-record
  counts. The result line says how many objects matched and how many cables
  traced.

These setup steps should take less than ten minutes; target processing time is
separate and is recorded in the private receipt.

To retire a finished demo — delete its branch and its namespace's main-scoped
rows (owners, custom-field definitions and the automation export templates,
webhook and event rule), leaving the target as found — use
`just retire https://netbox.example "Demo branch" NAMESPACE`
(see [first target §8](docs/first-target.md#8-retiring-a-demo)).
To instead empty a disposable Cloud branch for another run, replace that
branch and wait for its new schema to become ready:

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
The TurboBulk adapter compiles the full 102-kind contract — every kind any
current profile emits, the complete bank included — and the complete path is
live-qualified on both the pinned local 4.7.1 stack and a NetBox Cloud 4.7.1
tenant (full-contract estates and a 128,932-object load, each to exact strict
readback). Enterprise remains unqualified. The remote Diode
adapter is implemented for
externally confirmed direct auto-apply, with request checkpoints and strict REST
visibility barriers, but still needs a live qualification run. Assurance review
is not executable because the ingestion
API cannot enforce that tenant mode. General REST-only loading remains work in
progress. See the [transport model](docs/transports.md#one-command-several-internal-steps)
for configuration, safety boundaries, and evidence.

Existing [scenario guides](docs/scenarios.md) cover branch acquisition and refresh,
a power-diversity defect, provider span maintenance, and an
[Assurance discovery-drift twin](docs/scenarios.md#assurance-discovery-drift) —
`just drift PLAN OUT` writes a believably drifted "observed" Diode payload plus
the exact expected deviation set, so Assurance has something other than flawless
data to review. Each derives its subjects and relationships from the estate and
explains which changes have been qualified for live replay.
For a worked customer story, try [Harbor Supply](profiles/harbor-supply.md).

**Scale has separate generation and loading proofs.** Recorded offline generation
reaches 239,058 objects. A 12,702-object estate has passed local Diode
qualification; 128,932-object reviewable TurboBulk loads have passed strict
readback and exact ChangeDiff verification on **both** the pinned local 4.7.1
stack and a NetBox Cloud 4.7.1 tenant (initial and repeat), and an 85,081-object
current-generation estate loaded on that tenant in a single uninterrupted
attempt. Enterprise remains unqualified at size. See the
[scale measurements](docs/qualification.md#current-offline-scale-evidence),
[Parquet and Cloud results](docs/qualification.md#parquet-upload-qualification)
and [live results](lab/README.md#current-v09-qualification) for scope and limits.

## Documentation

| I want to… | Read |
| --- | --- |
| Build a customer demo in one command | [Demo composer](docs/demo.md) |
| Load my first estate into a target, start to finish | [First target](docs/first-target.md) |
| Grow a loaded estate and get the new version live | [First target §7](docs/first-target.md#7-growing-a-loaded-estate) |
| Generate, configure or grow an estate | [Usage](docs/usage.md) · [Recipe reference](docs/recipes.md) |
| Understand the rules and connected detail | [Modeling](docs/modeling.md) |
| Run a change or defect demonstration | [Scenarios](docs/scenarios.md) |
| Choose Diode, TurboBulk or REST | [Transport model](docs/transports.md) · [Loading](docs/loading.md) |
| Seed environments and verify without loading | [Seeding](docs/seeding.md) |
| Check scale, compatibility and historical evidence | [Qualification](docs/qualification.md) · [Community comparison](COMPARISON.md) |
| Extend the generator | [Development](CLAUDE.md) · [Graph contract](CONTRACT.md) · [Hardware catalog](catalog/README.md) |
| See remaining gaps or prior acceptance criteria | [Coverage](COVERAGE.md) · [Completed goal](GOAL.md) |

Generated datasets, credentials and historical receipts under `build/` are local
outputs and are not shipped in the repository.
