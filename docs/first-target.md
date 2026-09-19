# First target: from an empty NetBox to a loaded demo branch

The walkthrough for an operator who has a generated artifact and a NetBox
target but has never loaded one. Commands are the Justfile surface; every step
before `just load` is read-only. [Loading](loading.md) holds the transport
detail and [seeding](seeding.md) the database-restore alternative.

## 1. What the target must have

- **NetBox 4.6 or newer.** Artifacts that emit module bay types (for example
  the 59-kind rich provider artifact) require NetBox 4.7: 4.6 has no
  module-bay-type model at all. `just load-check` (below) tells you offline
  whether your artifact needs it.
- **The Branching plugin**, for branch-scoped loads. Loading without a branch
  writes to main and requires the explicit `ALLOW_MAIN_WRITES=1` opt-in.
- **The TurboBulk plugin**, for bulk transport. Without it the loader falls
  back to Diode (which has its own pinned version gates) or refuses with the
  exact blockers per transport — `just load-explain` shows the decision.
- On **NetBox Cloud** these are managed platform plugins: enabling or
  upgrading them on a tenant is a NetBox Labs operations action, not a tenant
  setting — request it through your account or support channel. On
  **Enterprise / self-hosted**, an administrator installs them like any other
  plugin.

## 2. The API token

Use a superuser token, or one with write permission to every model the
artifact emits plus the Branching and TurboBulk plugin endpoints. That is the
qualified shape; narrower grants fail mid-load at the first missing endpoint.

One optional extra: the loader's worker-death oracle reads
`/api/core/background-tasks/`, a staff-only endpoint. Without staff access the
loader still works — a dead worker is then detected at the poll timeout and
resolved on the next resume instead of within ~66 seconds.

Put credentials in `.env` (see `.env.example`) or export them. Justfile
recipes source `.env` only when `NETBOX_TOKEN` is not already exported, so an
exported environment always wins.

## 3. Check the artifact offline

```
just load-check build/my-estate
```

No target or token needed. It reports whether every emitted kind fits the
TurboBulk compiler contract, lists the uncovered kinds when not, and notes
when the artifact needs a NetBox 4.7 target. Run this before asking anyone
for tenant access.

## 4. Create the branch

Branch-scoped loads require an existing *ready* branch, and creating one is
asynchronous:

```
just branch https://target.example demo-acme
```

This creates the branch and waits until it is ready, refusing a name that
already exists. `just reset TARGET BRANCH` is the separate command that
*replaces* an existing disposable branch.

## 5. Preflight, then load

```
just load-explain build/my-estate https://target.example demo-acme
just load        build/my-estate https://target.example demo-acme
```

`load-explain` is read-only: it inspects the target, chooses the transport,
and prints every blocker if no faithful loader fits. `just load` loads and
then strictly reads the estate back — attribute-exact, reference-exact, with
native cable traces and component-placement checks.

**The receipt, in five sentences.** Every load writes a private receipt under
`build/load-receipts/`, bound to the artifact, target, branch, delivery
policy, row bound, and compiler version. It is the only resume and recovery
checkpoint: interrupt the load and rerunning the same command continues from
the receipt. Never delete or edit it while its branch is in use — a lost
receipt makes the branch unrecoverable, and the remedy is `just reset` plus a
fresh load. A changed artifact, policy, or loader version refuses the old
receipt loudly and needs a fresh branch. Verification receipts
(`verify-*.json`) are separate, re-runnable evidence and are safe to
overwrite.

## 6. What a demo looks like

The demo lives in its branch. Activate the branch in the NetBox UI and the
whole estate is there; main stays clean. Two boundaries to know before a
screen share:

- **Merging a TurboBulk-loaded branch to main is currently blocked** by
  upstream defects (see [qualification](qualification.md)); the working
  pattern is branch-per-demo, then `just reset` or branch deletion.
- On NetBox 4.7, `owner`/`owner_group` rows are **not branch-isolated**: a
  branch load writes them to main and branch deletion does not remove them.
  A later fresh load of the same estate name will refuse until they are
  removed.

## 7. Verify anytime, write nothing

```
just verify-target build/my-estate https://target.example demo-acme
```

Re-runs the full acceptance readback with zero writes — against a loaded
branch, or against main on an instance seeded some other way (for example a
database restore). Exit code 2 and a full mismatch list in the `verify-*`
receipt when the target differs.
