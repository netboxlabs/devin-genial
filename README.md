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

The Justfile is also the loading interface. Inspect a target without writing:

```sh
just load-explain build/my-bank "Disposable branch"
```

The result gives a preliminary transport choice and known compatibility blockers.
Run `just load build/my-bank "Disposable branch"` to perform the adapter's full
read-only schema/package preflight, then load it and write the default private
receipt under `build/`. That second preflight can find additional blockers and
still stops before target writes.
The current TurboBulk adapter is qualified for its frozen 29-kind contract. The
remote Diode adapter is implemented for externally confirmed direct auto-apply,
with request checkpoints and strict REST visibility barriers, but still needs a
live qualification run. Assurance review is not executable because the ingestion
API cannot enforce that tenant mode. REST creation and
rich TurboBulk coverage remain work in progress. See the [transport model](docs/transports.md#one-command-several-internal-steps)
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
