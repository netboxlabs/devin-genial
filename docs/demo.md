# Demo composer: one command, twenty minutes before the call

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

You know the industry, the vendor in their racks and which product story is
being sold. `just demo` turns those three into a generated, validated estate, a
cheat sheet written from that estate's own data, and — if you give it a target
— a loaded, strictly verified branch.

```sh
just demo regional-bank "Acme Regional Bank" juniper assurance,automation
```

It composes and **orchestrates nothing new**. Every step is the same entry
point the other Justfile recipes run: `generate`, `check`, `load-check`,
`drift`/`drift-check`, `scenario`/`scenario-check`, `branch`, `load`,
`verify-target`. No gate is skipped or softened, and a non-zero exit from any
of them stops the compose before it writes a cheat sheet you could read on a
call.

- [What it writes](#what-it-writes)
- [Flags](#flags)
- [Template sizes](#template-sizes)
- [Feature packs](#feature-packs)
- [Going live in the same run](#going-live-in-the-same-run)
- [Determinism](#determinism)
- [What it does not do](#what-it-does-not-do)

## What it writes

Into `--out` (default `build/demos/<namespace>/`):

| File | What it is |
| --- | --- |
| `DEMO.md` | **The deliverable.** The cheat sheet: opening line, walkthrough over real objects, per-feature click paths, API proofs, the go-live/repeat/retire commands and the honest lines |
| `recipe.toml` | An ordinary commented recipe. Edit it, re-generate it, grow it with `--previous` |
| `estate/` | The generated artifact — `plan.json`, `report.md`, `coverage.json`, `intent.json`, `diode/` |
| `drift/` | The Assurance drift twin, with `--features assurance` |
| `scenario/` | The power-diversity snapshots, with `--features scenario` |
| `compose.json` | Machine-readable receipt: resolved flags, the exact command of every step, wall seconds, per-kind counts, the TurboBulk verdict and the live branch evidence |

An existing `--out` is refused; earlier demos are never overwritten. A *failed*
compose leaves its partial directory in place for inspection — remove it
deliberately before retrying.

## Flags

`just demo PROFILE NAME VENDOR FEATURES [TARGET BRANCH]` covers the common
shape. `python3 -m estates demo --help` has the rest.

| Flag | Default | Accepted |
| --- | --- | --- |
| `--profile` | `regional-bank` | Any of the ten profile names ([recipe reference](recipes.md#common-keys)) |
| `--vendor` | `default` | `default`, `juniper` (access + leaf), `aruba` (AP). See [the catalog](../catalog/README.md#selectable-vendor-lines) |
| `--name` | `Genial Demo Estate` | 1–80 characters of customer-visible text; it becomes the recipe `name` |
| `--namespace` | derived from `--name` | 2–20 character DNS label. Derivation lowercases, hyphenates and truncates; a name that cannot yield one (`"1234"`) fails and tells you to pass this flag |
| `--seed` | derived from the namespace | `0 ≤ seed < 2^63`. Derived so two composes for one customer agree without anybody remembering a number |
| `--features` | none | Comma list of `assurance`, `automation`, `scenario` |
| `--sites` | none | TOML or JSON file of `[site_names]` overrides — the customer's real site names. Either the bare mapping or a file wrapping it in `site_names` |
| `--out` | `build/demos/<namespace>/` | New directory |
| `--target` / `--branch` | none | Both or neither. Supplying both goes live in the same run, and requires `NETBOX_TOKEN` (checked before anything is generated) |

`--vendor` moves the families it actually covers: `juniper` has no AP line in
the catalog, `aruba` has no switch line. `aruba` also declares the AP-505's real
5 GHz + 2.4 GHz split, so whichever WLAN rides `wlan1` changes band — the cheat
sheet says so, and says so again when the profile models no wireless at all.

## Template sizes

One authored demand template per profile: moderate on purpose — believable
enough to walk for twenty minutes, small enough to load in minutes. When a
customer needs a different shape, write an ordinary recipe (the
[recipe reference](recipes.md) documents every demand key) and compose from it:

```sh
just demo-recipe build/lakeshore/recipe.toml scenario https://netbox.example demo-lakeshore
```

`--recipe` takes the estate's identity and shape from the file — profile,
`name`, `namespace`, `seed`, `[hardware]` and `[site_names]` — copies it
verbatim into the output, and writes a cheat sheet that follows the customer's
shape instead of the stock template. The identity flags conflict with it by
design: edit the recipe, not the command line.

Counts below are measured at the default `--name` (`Genial Demo Estate` →
namespace `genial-demo-estate`) with the default vendor line, and reproduce
exactly: `python3 -m estates demo --profile PROFILE --out build/demos/measure`.
Nine profiles are seed-stable, so any customer name gives these numbers. The
**bank is the exception**: its unassigned branches draw modern / inherited /
refreshed from the seeded `design_mix` pool, and inherited branches carry more
equipment, so a different customer name moves it by up to about 120 objects.
Changing `--vendor` moves counts similarly.

| Profile | Shape | Objects | Sites | Devices | Cables |
| --- | --- | ---: | ---: | ---: | ---: |
| `regional-bank` | 2 DCs, HQ (48 staff), 5 branches — 3 modern, 1 inherited, 1 refreshed | 8,428 | 8 | 390 | 774 |
| `enterprise-data-center` | 2 DCs, 4 workloads, 36 rack-diverse VMs | 2,967 | 2 | 60 | 248 |
| `school-district` | 1 district DC, 2 campuses, 20 classrooms, 1 lab | 4,360 | 3 | 241 | 437 |
| `hospital-clinics` | 1 DC, 1 hospital (3 wards, 2 imaging), 2 clinics | 4,676 | 4 | 177 | 402 |
| `provider-backbone` | 3 PoPs in 3 metros, 2 customers, 5 premises | 3,526 | 9 | 97 | 267 |
| `retail-chain` | 2 commerce DCs, support centre, 1 DC, 6 stores | 9,971 | 10 | 396 | 926 |
| `university-campus` | Campus DC, 2 academic buildings, 1 hall, library | 8,484 | 5 | 491 | 881 |
| `msp` | 1 NOC, 3 accounts, 5 managed offices | 5,617 | 6 | 225 | 508 |
| `manufacturing` | 2 corporate DCs, 2 plants with IT/OT zones | 6,773 | 4 | 299 | 669 |
| `utility` | 1 control center, 3 substations, 24 bays | 4,798 | 4 | 145 | 411 |

Load time scales with objects: expect a minute or two per few thousand on a
local target, plus a silent several-minute finalizer window on the larger ones.

## Feature packs

The **records** a feature talks about are in every estate. A feature flag
decides what gets built beside the artifact and what the cheat sheet narrates.

- **`assurance`** builds the [drift twin](scenarios.md#assurance-discovery-drift)
  into `drift/` and re-checks it. `DEMO.md` links `drift/drift.md` and never
  restates it — that walkthrough is byte-bound to its manifest, and
  `just drift-check` re-renders and compares it. Needs a profile with campus
  access; `enterprise-data-center` models fabric only and is refused at the
  flag.
- **`automation`** adds narration only, no files — since 0.11.0 the records are
  already in every estate. It points at the inventory a playbook reads, the
  service records a template renders, the custom field and custom link where
  the profile authors them (today the bank's operations tier; the sheet lists
  only what the estate actually carries), the two config contexts whose
  ntp/syslog/dns values are the estate's own service VM addresses, the two
  rendering export templates, and
  the deliberately inert webhook + disabled event rule (the ServiceNow/ITSM
  hook shape, with nothing firing and the records saying so).
- **`scenario`** builds the loss-of-power-diversity snapshots into `scenario/`
  and scenario-checks them. Those two snapshots are separate estates for
  separate fresh targets, never a transition on the loaded branch.

## Going live in the same run

```sh
just demo msp "Acme Managed" default automation https://netbox.example demo-acme
```

With `--target` and `--branch` the composer additionally runs `just branch`,
`just load` and `just verify-target`, in that order, through the loader's own
gated path — so [first target](first-target.md)'s prerequisites still apply in
full: NetBox 4.7+, the Branching and TurboBulk plugins, a token with
`TURBOBULK_WRITES=1`, and one live namespace per branch. **The composer
automates [§3 to §6](first-target.md#3-check-the-artifact-offline); read §1, §2
and §10 before you point it at a customer target.** The Justfile recipe sources
`.env` only when `NETBOX_TOKEN` is not already exported, exactly like `just
load`.

Without a target it stops after the offline gates and `DEMO.md` prints the exact
go-live commands, plus the one-line recompose that goes live directly.

Retiring is one command and it is in the cheat sheet:
`just retire TARGET BRANCH NAMESPACE`.

The composed live path is qualified: on September 20, 2026 one command took a
bank + Juniper + assurance + automation compose from nothing to a loaded,
strictly verified branch (8,474/8,474 objects, 0 mismatches) in 227 seconds on
the pinned local stack ([COVERAGE.md](../COVERAGE.md) phase 12). If anything
surprises you, fall back to running the three commands yourself; the artifact
is already on disk and the cheat sheet prints them.

## Determinism

The same inputs reproduce `recipe.toml` byte for byte and `DEMO.md` byte for
byte outside its fenced `<!-- timings:start -->` section. Wall clock and the
compose timestamp live there and in `compose.json`; nothing else varies.

"Same inputs" includes the `--out` string and the directory you ran from:
paths in `DEMO.md` are rendered from `--out` exactly as you typed it, and links
back to the repository's own docs are relative when the demo sits inside the
repository (the default) and point at the upstream repository when it does not.

## What it does not do

- It does not add a loader, a kind, a validation path or a recipe key. Every
  artifact it writes is one an existing command already produced.
- It does not relax a gate. `load-check` failing, validation failing, a drift
  re-check failing or a strict readback mismatch all stop the compose.
- It does not claim live behaviour. Offline checks are offline checks; what is
  proven about a target is exactly what the load receipt and the strict
  readback record, and no Assurance-equipped target has ever seen a drift
  payload.
- It does not rename or edit objects in a running estate. `namespace`, `name`,
  `seed` and the vendor line are rebaseline-frozen: changing one means a fresh
  compose, a fresh output and a fresh branch.

One live-demo caution that is NetBox's, not this repo's: the REST API silently
ignores an unknown query parameter (HTTP 200, unfiltered results). When you
prove an isolation or scoping claim live, sanity-check that the filtered count
is well below the unfiltered total — a typo'd filter renders as a
catastrophic-looking leak that is pure query error.
