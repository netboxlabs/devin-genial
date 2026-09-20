# First target: from an empty NetBox to a loaded demo branch

The walkthrough for an operator who has a generated artifact and a NetBox
target but has never loaded one. Commands are the Justfile surface; every step
before `just load` is read-only. [Loading](loading.md) holds the transport
detail and [seeding](seeding.md) the database-restore alternative.

## 1. What the target must have

- **NetBox 4.7 or newer for anything you generate today.** Every current
  profile emits module bay types (PSU bays and optic cages are unconditional
  enrichment), and that model does not exist before NetBox 4.7 — a 4.6 target
  cannot load any artifact this generator currently produces. Only historical
  pre-module-bay artifacts still load on 4.6. `just load-check` (below) names
  the requirement offline; check the target's `/api/status/` version before
  creating anything on it.
- **The Branching plugin**, for branch-scoped loads. Loading without a branch
  writes to main and requires the explicit `ALLOW_MAIN_WRITES=1` opt-in.
- **The TurboBulk plugin**, for bulk transport. Without it the loader falls
  back to Diode (which has its own pinned version gates) or refuses with the
  exact blockers per transport — `just load-explain` shows the decision.
  Dead-worker recovery additionally needs TurboBulk **0.4.0 or newer** (the
  opportunistic orphaned-job reaper): on older builds a load interrupted by a
  worker death leaves its branch permanently unrecoverable — fresh branch
  only. Source builds may report version `0.0.0` even when they carry the
  reaper, so confirm with whoever built the target.
- On **NetBox Cloud** these are managed platform plugins: enabling or
  upgrading them on a tenant is a NetBox Labs operations action, not a tenant
  setting — request it through your account or support channel. On
  **Enterprise / self-hosted**, an administrator installs them like any other
  plugin.
- **Sharing a target with other estates works, with one rule: one live
  namespace, one branch.** On NetBox 4.7, each estate's `owner`/`owner_group`
  rows — its custom-field, choice-set and custom-link definitions, and its
  automation export templates, webhook and event rule — live
  on main (Branching exempts them; they survive branch deletion). Its config
  contexts are branch-scoped and go with the branch. Rows from a *different* namespace are automatically allowlisted
  and recorded in the receipt — assessed at preflight and again at the final
  readback, so a neighbour loading concurrently does not fail your run. The *same* namespace cannot fresh-load a
  second time — into a new branch or after deleting the old one — until its
  own rows are removed (`/api/users/owners/`, `/api/users/owner-groups/`;
  delete only the colliding rows, which the refusal and `just load-explain`
  name exactly). **Deleting them retires any branch already loaded under the
  namespace**: its objects lose their owner and its receipt can never verify
  again (§7). To see which namespaces a shared target already carries before
  choosing yours (a **rebaseline**-frozen key), list `/api/users/owners/` —
  each estate's rows begin with its namespace. For a before/after two-branch
  demo, use two namespaces or the scenario snapshot flow.

## 2. The API token

Use a superuser token, or one with write permission to every model the
artifact emits plus the Branching and TurboBulk plugin endpoints. That is the
qualified shape; narrower grants fail mid-load at the first missing endpoint.

One optional extra: the loader's worker-death oracle reads
`/api/core/background-tasks/`, a staff-only endpoint. Without staff access the
loader still works — a dead worker is then detected at the poll timeout
instead of within ~66 seconds. Either way, resuming past a dead worker needs
the target's reaper to have marked the orphaned row first (TurboBulk ≥ 0.4.0;
the reap triggers when the next TurboBulk job runs, in practice your
fresh-branch load) — see §10 when a load fails.

Put credentials in `.env` (see `.env.example`) or export them. Justfile
recipes source `.env` only when `NETBOX_TOKEN` is not already exported, so an
exported environment always wins.

Writes are separately gated: TurboBulk loads require `TURBOBULK_WRITES=1`
alongside the token (`export NETBOX_TOKEN=… TURBOBULK_WRITES=1` in bash/zsh;
`set -x NETBOX_TOKEN …; set -x TURBOBULK_WRITES 1` in fish — or set both in
`.env`; the shipped `.env.example` deliberately has the gate off). Exporting
the token suppresses `.env`, so an exported environment must carry both.

The token drives every command here, but the demo itself (§6) is the NetBox
web UI — you also need a UI login on the target for the screen share.

## 3. Check the artifact offline

```
just load-check build/my-estate
```

No target or token needed. It reports whether every emitted kind and
reference fits the TurboBulk compiler contract, lists the gaps when not, and
names the NetBox 4.7 requirement when the artifact carries module bay types.
(This is also the moment to make the estate carry the customer's real site
names — the `[site_names]` recipe key in the
[recipe reference](recipes.md#common-keys) — before anything goes live.)
Run this before asking anyone for tenant access — and when it names a version
requirement, confirm the target's `/api/status/` version before creating a
branch on it.

## 4. Create the branch

Branch-scoped loads require an existing *ready* branch, and creating one is
asynchronous:

```
just branch https://target.example demo-acme
```

This creates the branch and waits until it is ready (default 300 s; a third
argument changes the bound), refusing a name that already exists. If the
branch never leaves `new` — which means the target's RQ worker is dead or
backlogged, not that you did anything wrong — the command deletes its own
stuck branch so the name stays free, and tells you to check
`/api/core/jobs/` for a pending `Provision branch` job before retrying.
`just reset TARGET BRANCH` is the separate command that *replaces* an
existing ready disposable branch.

## 5. Preflight, then load

```
just load-explain build/my-estate https://target.example demo-acme
just load        build/my-estate https://target.example demo-acme
```

`load-explain` is read-only: it inspects the target, chooses the transport,
prints every transport blocker if no faithful loader fits, and reports
`fresh_load_occupancy` — which emitted kinds already hold rows on the target,
which of those the loader will allowlist, and the exact endpoints to clear
for any that block a fresh load. Exit codes: `load-explain` exits 2 only when
no transport fits at all; fresh-load blockers print a `NOTE:` but keep exit 0,
because a resume against its existing receipt is unaffected by them — read
`fresh_load_occupancy.blocking` before a first load. `just load` loads and
then strictly reads the estate back — attribute-exact, reference-exact, with
native cable traces and component-placement checks. Expect on the order of a
minute or two per few thousand objects on a local target; the streaming row
and `finalize:` progress lines are normal. After the last `phase-` line the
cable-path and counter finalizers run silently on the target — on a 25k-object
estate expect several minutes with **no output** before the `finalize:` lines
appear in one burst; the target's job queue showing pending jobs during this
window is normal, not a hang. The final summary line is the
acceptance evidence — objects matched, mismatches, create-ChangeDiff and
cable-trace counts, components checked — plus `ui_url`, the branch-activated
NetBox page the demo starts from.

**The receipt, in five sentences.** Every load writes a private receipt under
`build/load-receipts/`, bound to the artifact, target, branch, delivery
policy, row bound, and compiler version. It is the only resume and recovery
checkpoint: interrupt the load cleanly and rerunning the same command
continues from the receipt (a load interrupted by a *worker death* resumes
only after the target's reaper marks the orphaned job — §10). Never delete or edit it while its branch is in use — a lost
receipt makes the branch unrecoverable, and the remedy is `just reset` plus a
fresh load. A changed artifact, policy, or loader version refuses the old
receipt loudly and needs a fresh branch. Verification receipts
(`verify-*.json`) are separate, re-runnable evidence and are safe to
overwrite.

## 6. What a demo looks like

The demo lives in its branch. Activate it in the NetBox UI with the branch
selector in the top bar, or open any page with `?_branch=<schema_id>` appended
— the schema id is printed by `just branch` and recorded in the load receipt —
and Branching keeps it active for the session (an `active_branch` cookie).
The same parameter works on the REST API for post-load confirmation:
`curl …/api/dcim/sites/?_branch=<schema_id>`.
The whole estate is there; main stays clean. Two boundaries to know before a
screen share:

- **Merging a TurboBulk-loaded branch to main is currently blocked** by
  upstream defects (see [qualification](qualification.md)); the working
  pattern is branch-per-demo, then `just reset` or branch deletion.
- On NetBox 4.7, `owner`/`owner_group` rows, the custom-field trio and the
  automation export templates, webhook and event rule are **not
  branch-isolated**: a branch load writes them to main and branch deletion does
  not remove them. Other namespaces coexist over them automatically (§1);
  re-loading the *same* namespace fresh refuses until its leftovers are removed.
- The estate's **automation records** are on the branch tour too: its config
  contexts (Customization → Config Contexts — the global one lists this
  estate's own DNS and service addresses), its two CSV export templates (NetBox
  offers a loaded template in the Export menu of the object list it is bound to
  — devices and cables here), and its webhook with the event rule bound to it.
  The webhook points at a reserved `.invalid` host and the
  rule ships **disabled**: inventory to demonstrate the wiring, never a live
  integration. Enabling it during a demo is a deliberate act.

## 7. Growing a loaded estate

Growth is a generator feature, not a target feature: `just generate RECIPE
build/v2 build/v1/plan.json` reuses the frozen plan as the allocation ledger,
so every existing identity survives — but **the grown artifact is a full
fresh load of the whole estate into a new branch, not an incremental update
of the live one**. There is no in-place load mode. The sequence:

```
just generate RECIPE build/v2 build/v1/plan.json
just load-check build/v2
just branch https://target.example demo-acme-v2
# retire v1 (below), then:
just load build/v2 https://target.example demo-acme-v2
just verify-target build/v2 https://target.example demo-acme-v2
```

An estate using `[site_names]` (the customer's real site names) grows the same
way: append entries for the new sites in the same recipe change. Changing or
removing an *existing* entry is a rename and needs a new baseline.

**Retiring v1 is a deliberate, destructive step.** The namespace's main-scoped
rows — `owner`/`owner_group`, the custom-field trio and the automation export
templates, webhook and event rule — block the v2 load; deleting them (the
refusal names the exact rows) also nulls `owner` across the already-loaded v1
branch and permanently invalidates its receipt — v1 becomes display-only and
can no longer pass `verify-target`. (That display-only state only arises if
you delete the rows by hand; `just retire` below deletes the v1 branch in the
same step, which is the normal path.) One command performs the whole retirement
(the branch and the rows):

```
just retire https://target.example demo-acme acme
```

(`just branch-delete` removes only the branch; `just reset` is not a
retirement command at all — it *replaces* the branch with a fresh empty one.)
One namespace has one verifiable branch at a time; a side-by-side
before/after demo therefore needs **two namespaces planned from the start**
(or the scenario snapshot flow on fresh targets).

## 8. Retiring a demo

When the demo cycle ends, two things carry your namespace on the target: the
branch (which holds the estate, including its config contexts), and the
namespace's main-scoped rows — `owner`/`owner_group`, the custom-field trio and
the automation export templates, webhook and event rule. One command removes
both:

```
just retire https://target.example demo-acme acme
```

It deletes the named branch, then deletes exactly the main-scoped rows
carrying your namespace — event rules, webhooks, export templates, custom
links, custom fields, choice sets, owners and owner groups, in that dependency
order — reporting each. (Separately,
`just branch-delete` removes only the branch — and if the branch is already
gone, `just retire` still clears the namespace's owner rows, reporting the
branch as already absent. `just reset` *replaces* a branch with a fresh empty
one.) End-state check — nothing carrying your namespace remains (`?limit=0`
returns every row; `|| true` because zero matches is the success case):

```
curl -s -H "Authorization: Token $NETBOX_TOKEN" \
  "https://target.example/api/plugins/branching/branches/?limit=0" \
  "https://target.example/api/users/owners/?limit=0" \
  "https://target.example/api/users/owner-groups/?limit=0" \
  "https://target.example/api/extras/webhooks/?limit=0" \
  "https://target.example/api/extras/event-rules/?limit=0" \
  "https://target.example/api/extras/export-templates/?limit=0" \
  | { grep -c acme || true; }   # expect 0
```

and a main-side search (`/api/dcim/sites/?q=acme`) returns none of your
objects. Grepping for the namespace stays sound even with customer-named
sites: slugs keep the `namespace-…` form, and `?q=` matches slugs. (The
changelog keeps the branch create/delete audit rows; that is NetBox's audit
trail, not estate data.)

## 9. Verify anytime, write nothing

```
just verify-target build/my-estate https://target.example demo-acme
```

Re-runs the full acceptance readback with zero writes — against a loaded
branch, or against main on an instance seeded some other way (for example a
database restore). Exit code 2 and a full mismatch list in the `verify-*`
receipt when the target differs.

## 10. When a load fails

Every refusal is one of these shapes, and each names itself:

- **Transport blocker** ("no faithful loader is available…"): fix what the
  per-transport reasons name, re-check with `just load-explain`. Nothing was
  written.
- **Fresh-load occupancy** ("fresh load requires empty inventories…"): the
  refusal names the exact colliding rows; retire your namespace (§8) or pick
  a new one. Nothing was written.
- **Guardrail failure** (a failed job whose error names a `guardrail`): the
  target operator enabled TurboBulk 0.4.0's server-side resource limits
  (statement/lock timeouts, max rows per operation, concurrency caps) and this
  job exceeded one. Nothing resumes past it: ask the operator what is
  configured, or lower the job row bound (the fourth `just load` argument)
  and start a fresh branch.
- **Readback mismatch** ("strict readback found N mismatches"): the loaded
  data did not verify. When the mismatches are `unmatched_target_objects` on
  `owner`/`owner_group` names that are not your namespace, another estate
  loaded onto the shared target *during* your run; the loader excuses current
  foreign rows at readback, so rerunning the same command resumes and
  verifies. Any other mismatch is real: the receipt holds the list.
- **Worker death** ("abandoned by a dead worker…"): the *target's* background
  worker died mid-job — commonly resource exhaustion (memory, database
  connections) on the target, which loading again will not fix and which an
  operator without target access must escalate. On TurboBulk ≥ 0.4.0 the
  orphaned row is reaped when the next TurboBulk job runs, after which a
  resume arbitrates it from exact ChangeDiff evidence; until then — and
  always on older TurboBulk — start over on a fresh branch, once the target
  itself is healthy again. A fresh branch alone does not fix a saturated
  target.
