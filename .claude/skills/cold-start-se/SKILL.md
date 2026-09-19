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

- **Scenario/industry**: rotate through provider backbone, enterprise DC,
  regional bank, school district, hospital — including at least sometimes a
  profile that is *not* TurboBulk-loadable (school/hospital emit wireless
  kinds), because discovering that via `just load-check` is part of the
  journey being tested.
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
