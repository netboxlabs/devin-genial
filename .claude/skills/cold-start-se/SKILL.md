---
name: cold-start-se
description: Run a fresh-eyes "Sales Engineer who has never seen this repo" walkthrough that follows only the documentation to design, generate, and load a demo estate onto a live NetBox target, then reports every stumble. The repo's demo-readiness acceptance test; loop it after every fix wave until two consecutive clean verdicts.
---

# Cold-start SE walkthrough

The strongest test of "ready for people to use for demos" is a stranger
following only the docs. This skill runs that stranger as a subagent with no
repo context beyond what the documentation provides, against a live target,
and turns their stumbles into a ranked fix list.

## When to run

- After any change to the loading surface (Justfile recipes, `estates/load.py`,
  `estates/turbobulk.py`, `estates/branch.py`) or the operator docs
  (README, `docs/first-target.md`, `docs/recipes.md`, `docs/loading.md`).
- As a loop: fix the findings, rerun with a *different* scenario, repeat.
  **Exit criterion: two consecutive runs with verdict "yes, unassisted" and
  zero S1/S2 findings.** A run that repeats a prior scenario proves nothing —
  vary it (below).

## How to run

Spawn one subagent (fresh context — never a fork; the whole point is that it
knows nothing this session knows). Fill the bracketed slots in the prompt
template below. Prefer `model: opus` for fan-out.

### Variation rules (pick differently each run)

- **Scenario/industry**: rotate through every shipped profile (all ten load
  completely as of the 102-kind contract; check README's industry table for
  the current set). Vary the *journey* too: first load, growth act,
  retirement act, customer-named sites, merger/acquired lineage.
- **Customization**: the agent must author its own recipe from
  `docs/recipes.md` (own namespace, name, seed, demand numbers) — never a
  straight copy of a shipped profile.
- **Target state**: alternate between a box-fresh branch and a target that
  already hosts another estate (the shared-box case exercises the
  owner/owner_group coexistence path).
- **Stack**: the local disposable stacks are NetBox 4.6.8 (port 8968) and
  4.7.1 (port 8969); artifacts with module bay types need 4.7.
- **Names**: fresh namespace and fresh branch name every run.

### Prompt template

> You are simulating a NetBox Labs Sales Engineer who has NEVER used this
> repo: [REPO PATH] (Genial, a deterministic NetBox estate generator). Follow
> the documentation alone — as a stranger would — to stand up a custom demo
> estate on a NetBox target, and report every place the docs are wrong,
> missing, or high-friction. This is the acceptance test for "ready for
> people to use for demos".
>
> Your scenario: [ONE-SENTENCE CUSTOMER DEMO NEED, e.g. "a school district
> with 6 campuses" or "a service provider with 5 PoPs and 3 customers"].
>
> Rules of engagement:
> - Start from README.md like a new user. Follow docs/first-target.md and
>   docs/recipes.md where they lead. Use the Justfile recipes as documented.
>   When a doc tells you to do something that fails, record it verbatim, then
>   find the workaround — both go in the report.
> - Write your own recipe TOML from docs/recipes.md (customize namespace,
>   name, and at least two demand values). Keep it and all outputs under the
>   gitignored build/. Generate and validate offline first.
> - Your NetBox target: [TARGET URL]. Export NETBOX_TOKEN=[TOKEN] and
>   TURBOBULK_WRITES=1 in your command environment. NEVER read or modify the
>   repo's .env (it holds unrelated credentials); exported env wins
>   automatically. Use a branch name of your choosing that does not exist.
> - Full journey: offline check (just load-check), branch creation (just
>   branch), preflight (just load-explain), load (just load), zero-write
>   acceptance (just verify-target), and one repeat run of just load. Note
>   wall-clock times.
> - If something is truly broken, stop that thread, record it, continue the
>   rest of the journey however you can.
> - Do NOT edit any tracked file. Do not delete branches you didn't create.
>   Do not touch these pre-existing branches or instances: [PROTECTED LIST].
>
> Report format:
> 1. Verdict: could a real SE do this unassisted? (yes / yes-with-papercuts / no)
> 2. Timeline with times.
> 3. Friction log ordered by severity (S1 = blocked, S2 = misled then
>    recovered, S3 = slowed down) — quote the doc text that misled you and
>    say what would have prevented it.
> 4. Doc bugs you can prove (file:line, actual behavior).
> 5. What worked exactly as documented — so we don't fix what isn't broken.
> NO NITPICKING — a wording preference is not a finding; a stranger getting
> stuck is.

## After the run

- Fix S1/S2 findings and provable doc bugs in the same pass (code where the
  code is wrong, docs where the docs are wrong — prefer making documented
  remedies true over documenting workarounds).
- Record the verdict and scenario in the loop log below.
- Rerun with the next variation.

## Loop log

| Date | Scenario | Target | Verdict | S1/S2 findings |
| --- | --- | --- | --- | --- |
| 2026-09-19 | Provider, 5 PoPs / 3 customers, shared box | 4.7.1 local | no (blocked at write boundary) | owner/owner_group shared-box block; load-explain missed it; wrong documented remedy; phantom receipt message |
| 2026-09-19 | Enterprise DC, 3 workloads, manufacturing | 4.6.8 local | no (4.6 cannot load any current profile) | docs claimed "4.6 or newer"; load-explain occupancy probe crashed on the absent 4.7 endpoint; verify-target raw traceback; README walkthrough used the unloadable bank artifact |
| 2026-09-19 | Enterprise DC, 3 workloads, manufacturing (retake) | 4.7.1 local, shared with 2 estates | yes-with-papercuts (3,194 objects live+verified in 3m22s; coexistence held) | README orders bank-first with no loadability column; usage.md falsely required devenv/direnv |
| 2026-09-19 | Provider ISP, 4 PoPs / 2 customers, shared box | 4.7.1 local, shared with 3 estates | yes-with-papercuts (4,537 objects in ~2.5 min of commands; occupancy preflight truthful) | branch stuck in 'new' (dead RQ worker from PG connection exhaustion) unrecoverable by any documented command; occupancy "clear the listed endpoints" would delete other namespaces' rows; one-live-branch-per-namespace rule undocumented |
| 2026-09-19 | School district ask (pivot journey), shared box | 4.7.1 local, shared with 4 estates | yes-with-papercuts (district report + loadable core estate live in ~9 min; zero S1) | README's "No" hid the qualified Diode school path in lab/README.md; load-explain exited 0 on selected:null; load-check verdict buried under 140 duplicate refs on stderr; mergeable:true output vs merge-blocked docs |
| 2026-09-19 | Hospital network ask (pivot journey), shared box | 4.7.1 local, shared with 5 estates | yes-with-papercuts (hospital report + power scenario + hospital-shaped DC live in ~8 min of commands; zero S1) | conflicting_rows listed ALL owner rows on a same-namespace collision (would delete 5 protected demos); scenarios.md implied the defective snapshot loads to a fresh target; no "profile not loadable — what instead" guidance |
| 2026-09-18 | Enterprise DC + growth act (logistics) | 4.7.1 local, shared with 5 estates | Act 1: yes (2m13s, zero findings). Act 2: no | growth→load journey documented nowhere; deleting the namespace's owner rows silently retired the verified v1 branch; same-branch grown-artifact explain listed the SE's own estate as 53 blocking kinds |
| 2026-09-18 | Provider + growth act (Larkspur, 3 PoPs → +1 customer) | 4.7.1 local, shared with 7 estates | Both acts yes-with-papercuts (~8m20s total; §7's destructive warning matched reality exactly) | TURBOBULK_WRITES missing from the runbook (exported-token path never sets it); "delete the v1 branch" had no command (reset replaces, leaving an orphan); load-explain's blocking buried mid-JSON under selected:turbobulk |
| 2026-09-18 | Enterprise DC, load + grow + full retirement (Meridian) | 4.7.1 local, shared with 6 estates | Act 1 clean yes; Acts 2-3 yes-with-papercuts (~6 min hands-on; target left exactly as found) | retirement was a fact, not a task (no §, no command for the owner rows); usage.md aggregate-overlap sentence contradicted the coexistence rule unscoped; load-explain exit semantics undocumented; load-check stdout not jq-parseable |
| 2026-09-18 | Provider full lifecycle, 5 PoPs / 3→4 customers (Thornwood) | 4.7.1 local, shared with 6 estates | Acts 1 & 3 clean yes; Act 2 yes-with-papercuts (7m39s end to end; end state byte-identical to pre-run) | §7 ordered the destructive row deletion without referencing just retire (documented one section later, absent from README) |
| 2026-09-18 | Enterprise CUSO full lifecycle (culedger) | 4.7.1 local, shared with 6 estates | Acts 2 & 3 clean yes; Act 1 no (target Postgres saturated → worker death; docs overpromised resume) | resume-after-worker-death claim unscoped to the ≥0.4.0 reaper (and its opportunistic trigger); no failure-triage section; environment: stacks' max_connections raised 100→300 |
| 2026-09-18 | Provider MSP full lifecycle (kestrel, 4 PoPs / 3→4 customers) | 4.7.1 local, shared with 8 namespaces | **CLEAN #1: yes / yes / yes, zero S1/S2** (~8.5 min incl. deliberate failure tests; end state identical) | S3s only: namespace pre-check line, end-state check one-liners, resuming-receipt wording; recipes.md wrongly listed `build` as recipe-taking |
| 2026-09-18 | Enterprise pharma full lifecycle (helix, 2 DCs / 3→4 workloads) | 4.7.1 local, shared with 8 namespaces | **CLEAN #2: zero S1/S2 — exit bar met** (~3.5 min load time; target restored identically) | S3 polish applied post-run: retire tolerates an already-deleted branch; end-state check paginates and survives set -e; load-explain always prints a verdict line; disposable example labeled; full fish syntax |

| 2026-09-19 | Hospital with customer-named sites (Mercy), full lifecycle | 4.7.1 local, shared with 9 estates | Acts 1 & 3 clean; Act 2 yes-with-papercuts | site_names froze wholesale, cancelling growth for named estates (fixed: append-only under growth); unknown-id error showed foreign examples instead of the estate's ids; naming feature invisible from README/first-target |
| 2026-09-19 | Regional bank with merger + named sites, full lifecycle (Pinnacle) | 4.7.1 local, shared with 12 namespaces | All three acts yes; yes-with-papercuts overall (17,302 objects; zero S1; depth kinds confirmed via API) | Birch lineage brand undocumented where recipes are written; site_designs error hid a TOML-scoping trap; blocking's endpoint row count read as "rows blocking you" |
| 2026-09-19 | School district, named campuses, full lifecycle (Cedarbrook) | 4.7.1 local, shared with 11 namespaces | Acts 1 & 3 yes; Act 2 yes-with-papercuts | a CONCURRENT neighbour load failed the final readback (fixed: allowlist re-assessed at readback); branch-scoped API reads undocumented; site ids not discoverable pre-generate (fixed: plan prints them); §8 grep rationale unexplained with authored names |

| 2026-09-19 | Retail grocery chain, full lifecycle (Bluebird, day-one profile) | 4.7.1 local, shared with 14 branches | **CLEAN: yes / yes / yes, zero S1/S2** (16,098→18,243 objects; POS/guest segmentation confirmed; target byte-identical after) | S3 only: recipes.md TOC missed the retail section; §7 display-only clause needed scoping |
| 2026-09-19 | University campus, customer-named buildings, full lifecycle (Winslow, day-one profile) | 4.7.1 local, shared with 14 namespaces | Acts 1 & 3 yes; Act 2 yes-with-papercuts (26,188→29,594 objects, 0 mismatches, dorm IP survived growth, end state byte-identical) | `wan_peak_mbps` sized to demand made documented growth impossible and the error prescribed the forbidden remedy (fixed: growth-aware error + headroom guidance in both key tables); profile guide contradicted recipes.md on zone totals; recipes.md university section sat after the closing sections |
| 2026-09-19 | Hospital "Juniper shop" via [hardware], customer names, full lifecycle (Aurora, day-one lever) | 4.7.1 local, shared with 16 branches | Acts 2 & 3 yes; Act 1 yes-with-papercuts (8m53s total; Juniper/Aruba hardware confirmed live via API; end state identical) | recipes.md site_names row listed fabricated site ids, which also implied a hospital dc-02 (fixed: real ids + "just plan prints them" + per-profile DC counts); namespace shared-target uniqueness only discoverable in §1 (fixed: namespace row clause) |
| 2026-09-19 | MSP, 5→6 customer tenants, tenancy/desk API proofs, full lifecycle (Beacon, day-one profile) | 4.7.1 local, shared with 17 branches | **CLEAN #1: zero S1/S2** (~14.5 min; 9,208→10,936 objects, 0 mismatches; tenancy and per-account desk proofs confirmed live; target as found) | S3 polish applied post-run: API proof recipe + tenant slug shape in profiles/msp.md (verified live); README opening names all eight profiles; CLI SHA line labeled canonical |
| 2026-09-20 | Manufacturing under a 25-min deadline via `just demo` (Hartwell, Juniper + drift talk track), growth + retirement | 4.7.1 local, shared with 20 branches | Prep & retirement yes; growth yes-with-papercuts (call-ready at T+8:00 incl. API-verified OT isolation; all acts in 12m49s) | docs/demo.md's automation paragraph survived the phase-11 merge stale — it told the SE to say "no config contexts" while the cheat sheet showed them (fixed, with the stale live-gate paragraph); DEMO.md growth snippet was a no-op as printed (fixed: full ordered sequence + drift regen), table autolinks HTML-escaped `&` dropping `_branch` on copy (fixed: markdown links), rack sentence overclaimed (fixed: racked vs room-endpoint counts) |
| 2026-09-20 | Utility co-op, customer-named substations + power what-if, full lifecycle (Lakeshore) | 4.7.1 local, shared with 20 branches | Prep & what-if yes-with-papercuts; retirement yes (10m22s of a 30-min budget; zone isolation + names verified live; honest what-if answer) | docs contradicted each other on custom fields — demo.md/recipes.md promised them in every estate while only the bank authors them (fixed everywhere incl. the retire prose); a custom shape cost the cheat sheet outright (fixed: `--recipe`/`just demo-recipe` compose from the customer's own recipe file); utility power-scenario subject scoping + query-param caution documented |
| 2026-09-20 | Bank with an insisted-upon footprint via `just demo-recipe` (Summit Ridge, Juniper + drift + change-review ask) | 4.7.1 local, shared with 20 branches | Prep & change-review yes-with-papercuts; retirement yes (8m58s of a 30-min budget; all nine customer names + Juniper verified live; 11,999 reviewable branch changes as the change story) | the stock Birch-merger walkthrough step rendered for a recipe-shaped bank with no merger (fixed: the step is a graph fact — real site name when an inherited branch exists, omitted otherwise, with a docs caveat); "where to show a change" was undiscoverable (fixed: first-target §6 paragraph); drift.md's baseline placeholder now resolved concretely in composed sheets |
| 2026-09-20 | Hospital, Aruba shop, wireless + drift, growth per the cheat sheet's own block, retirement (Riverbend) | 4.7.1 local, shared with 20 branches | All acts yes-with-papercuts (call-ready 2m54s; whole exercise 8m31s; growth block ran verbatim first-try; end state byte-identical) | the sheet's closing retire still named v1's branch after its own growth block retired it — exit 0, rows deleted, live v2 unverifiable (fixed: scoped retire lines + a stderr WARNING when retiring rows under an absent branch); the aruba 2.4 GHz line implied an inspectable radio nothing rides (fixed: graph-fact conditional); the hospital step promised guest wireless the recipe defaults to zero (fixed: graph-fact conditional); wireless got its own walkthrough step; growth's no-new-cheat-sheet reality stated in the block |
| 2026-09-20 | School district, wireless + power what-if, growth AND closeout per the cheat sheet verbatim (Maplewood) | 4.7.1 local, shared with 20 branches | **CLEAN #1: zero S1/S2** (call-ready T+4:21, full lifecycle 9m40s; wireless + power-path claims exact under API check; target byte-identical) | S3 polish applied post-run: growth block is feature-aware (drift line only with assurance, power-scenario regen line with scenario), the Open-at cell is a copyable markdown link, and the grown demo closes out inside its own block with the v2 branch |
| 2026-09-20 | Provider ISP, backbone + planned-maintenance-window ask, full lifecycle (Great Plains) | 4.7.1 local, shared with 20 branches | Prep & retirement yes; maintenance story yes-with-papercuts (~6 min to call-ready; circuit terminations and the alternate ring verified live; honest narration line established) | count restarts: the TurboBulk what-to-do guidance was written for the defect scenario only and the changed/ refusal read as a corrupt artifact (fixed: the refusal names the policy with its status label; scenarios.md repeats the guidance in the provider section); the composer gained a provider-only `maintenance` feature pack so the flagship provider story is reachable from the flagship command |
| 2026-09-20 | Retail grocery, Aruba shop, POS/guest segmentation + drift, growth AND closeout per the cheat sheet verbatim (Cornerstone) | 4.7.1 local, shared with 20 branches | **CLEAN #1: zero S1/S2** (call-ready ~7 min, full lifecycle 13 min; no failure, no workaround anywhere; segmentation provable in two clicks; target as found) | S3 polish applied post-run: the growth block's narration path dropped its stray `/estate`, the wireless step prefers a guest-carrying store AP on estates that have one, and the query caution covers the silent-empty direction (`?site=` on scope-based prefixes) |
| 2026-09-20 | University, Juniper shop, dorm ports + wireless + drift, growth and closeout per the sheet (Ashford) | 4.7.1 local, shared with 20 branches | Prep yes-with-papercuts; growth & final state yes (call-ready 5.5 min, lifecycle 11m17s; dorm-port and Juniper claims exact; identities survived growth) | count restarts: docs promised "API proofs" the sheet didn't carry (fixed: every sheet now carries graph-derived curl proofs; MSP keeps its richer pair); the headline dorm ask was the one step without a click path (fixed: graph-fact hall/device/link); growth now composes — `--previous` with `--recipe` yields a fresh v2 cheat sheet, ending the three-run-old no-second-sheet gap; the two SHA vocabularies documented in usage.md |

**Exit bar met 2026-09-18** after 13 runs (2 no → 5 yes-with-papercuts → mixed
→ 2 consecutive clean). The wireless/naming surface change of 2026-09-19
restarted the count (run #14 above). Keep running this skill after any change
to the loading surface or operator docs; a new finding restarts the count.
