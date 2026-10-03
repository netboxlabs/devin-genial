# Offline generation plus an opt-in disposable local Diode target.
local_docker := "docker --context colima-netbox-generator"
local_netbox := local_docker + " compose --project-directory build/local-target/netbox -f build/local-target/netbox/docker-compose.yml -f build/local-target/netbox/compose.local.json"
local_diode := local_docker + " compose --project-directory build/local-target/diode -f build/local-target/diode/docker-compose.yaml -f build/local-target/diode/compose.local.json"
# Real-discovery lab (lab/discovery/README.md): its own Colima profile. Without a
# system colima, run e.g. DISCOVERY_COLIMA='nix shell nixpkgs#colima -c colima'.
discovery_colima := env_var_or_default("DISCOVERY_COLIMA", "colima")
discovery_vm := discovery_colima + " ssh -p genial-discovery -- bash " + quote(justfile_directory() / "lab/discovery/vm.sh")

help:
    @just --list --unsorted

# FEATURES is a comma list of assurance,automation,scenario,maintenance
# (maintenance is provider-only). Supplying TARGET and BRANCH also creates the
# branch, loads and strictly verifies in the same run; omitting them stops
# after the offline gates and prints the go-live commands. `python3 -m estates demo --help` has the rest (--namespace, --seed,
# --sites, --out). Every step is the same entry point the recipes below run.

# Compose one customer demo: estate, feature packs and a DEMO.md cheat sheet
demo profile='regional-bank' name='Genial Demo Estate' vendor='default' features='' target='' branch='' out='':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates demo --profile {{quote(profile)}} --name {{quote(name)}} --vendor {{quote(vendor)}} --features {{quote(features)}} {{if out == '' { '' } else { '--out ' + quote(out) } }} {{if target == '' { '' } else { '--target ' + quote(target) + ' --branch ' + quote(branch) } }}

# ([hardware], [site_names], identity and demand all come from the recipe file;
# PREVIOUS grows from that frozen plan.json with a fresh cheat sheet.)
# Compose from the customer's own recipe — their shape, their names, growable
demo-recipe recipe features='' target='' branch='' previous='' out='':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates demo --recipe {{quote(recipe)}} --features {{quote(features)}} {{if previous == '' { '' } else { '--previous ' + quote(previous) } }} {{if out == '' { '' } else { '--out ' + quote(out) } }} {{if target == '' { '' } else { '--target ' + quote(target) + ' --branch ' + quote(branch) } }}

# Preview a validated estate without writing files
plan recipe='profiles/bank.toml':
    python3 -m estates plan {{quote(recipe)}}

# Generate a new build directory; optional third argument is the previous plan
generate recipe='profiles/bank.toml' output='build/bank-v9' previous='':
    python3 -m estates generate {{quote(recipe)}} --out {{quote(output)}} {{if previous == '' { '' } else { '--previous ' + quote(previous) }}}

# Check a previously generated plan
verify plan='build/bank-v9/plan.json':
    python3 -m estates check {{quote(plan)}}

# View the estate, address plan, rack elevations and demo questions
report plan='build/bank-v9/plan.json':
    python3 -m estates report {{quote(plan)}}

# Optional: validate wire data using installed netboxlabs-diode-sdk==1.14.0
sdk-check directory='build/bank-v9/diode':
    python3 -m estates sdk-check {{quote(directory)}}

# Target recipes source .env only when NETBOX_TOKEN is not already exported,
# so a caller-supplied environment (for example a local lab token) always wins.

# Inspect the target, choose a faithful transport, load, and strictly verify
load artifact target branch='' turbobulk_job_rows='2000' upload_format='auto':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} {{if branch == '' { '' } else { '--branch ' + quote(branch) }}} --delivery-policy reviewable --turbobulk-job-rows {{quote(turbobulk_job_rows)}} --upload-format {{quote(upload_format)}}

# Faster baseline for a fresh throwaway branch; it cannot be reviewed, merged, or reverted
load-disposable artifact target branch turbobulk_job_rows='2000' upload_format='auto':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} --branch {{quote(branch)}} --delivery-policy disposable-baseline --turbobulk-job-rows {{quote(turbobulk_job_rows)}} --upload-format {{quote(upload_format)}}

# Seed a dedicated tenant's main directly: no branch, no TurboBulk changelogs,
# no review history (REST-created records and completion PATCHes still write
# ordinary changelog entries). Requires ALLOW_MAIN_WRITES=1 and an empty main.
seed-main artifact target turbobulk_job_rows='2000' upload_format='auto':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} --delivery-policy main-seed --turbobulk-job-rows {{quote(turbobulk_job_rows)}} --upload-format {{quote(upload_format)}}

# Explain a main seed with zero writes (transport fit and main occupancy)
seed-main-explain artifact target:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} --delivery-policy main-seed --explain

# Preview a main teardown with zero writes: what this artifact would delete
teardown-main-explain artifact target:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.teardown {{quote(artifact)}} {{quote(target)}} --explain

# PERMANENTLY delete this artifact's estate from a dedicated tenant's main.
# Requires ALLOW_MAIN_TEARDOWN=1; rows the artifact does not claim are left
# alone. Run teardown-main-explain first.
teardown-main artifact target:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.teardown {{quote(artifact)}} {{quote(target)}} --confirm

# Derive deterministic floorplan geometry (physical-geometry plugin records)
# from a frozen plan — a sidecar artifact bound to the plan's SHA, like drift
geometry plan out:
    python3 -m estates.geometry build {{quote(plan)}} --out {{quote(out)}}

# Recompute a saved geometry artifact from its bound plan (operands match `geometry`)
geometry-check plan out:
    python3 -m estates.geometry check {{quote(out)}} --plan {{quote(plan)}}

# Write the geometry records through the physical-geometry REST API and read
# them back exactly. Requires GEOMETRY_WRITES=1 and a seeded estate on the target.
seed-geometry out target receipt='':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.geometry seed {{quote(out)}} {{quote(target)}} {{if receipt == '' { '' } else { '--receipt ' + quote(receipt) }}}

# Derive a deterministic procurement story (Asset Lifecycle plugin records:
# BOMs, purchase orders, deliveries, installs, spares) from a frozen plan
lifecycle plan out:
    python3 -m estates.lifecycle build {{quote(plan)}} --out {{quote(out)}}

# Recompute a saved lifecycle artifact from its bound plan (operands match `lifecycle`)
lifecycle-check plan out:
    python3 -m estates.lifecycle check {{quote(out)}} --plan {{quote(plan)}}

# Write the procurement story through the Asset Lifecycle REST API and read it
# back exactly. Requires LIFECYCLE_WRITES=1 and the estate seeded on main.
seed-lifecycle out target receipt='':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.lifecycle seed {{quote(out)}} {{quote(target)}} {{if receipt == '' { '' } else { '--receipt ' + quote(receipt) }}}

# Per-rule prediction of findings and causes rides along in the artifact.
# Derive NetBox Validation policies whose parameters come from a frozen plan
validation plan out:
    python3 -m estates.validation build {{quote(plan)}} --out {{quote(out)}}

# Recompute a saved validation artifact from its bound plan (operands match `validation`)
validation-check plan out:
    python3 -m estates.validation check {{quote(out)}} --plan {{quote(plan)}}

# Requires VALIDATION_WRITES=1 and the estate on main; differences are recorded.
# Create and run the validation policies, then compare findings with the prediction
seed-validation out target receipt='':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.validation seed {{quote(out)}} {{quote(target)}} {{if receipt == '' { '' } else { '--receipt ' + quote(receipt) }}}

# Remove exactly the validation policies and runs a seed receipt recorded
unseed-validation receipt target:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.validation unseed {{quote(receipt)}} {{quote(target)}}

# Remove exactly the floorplan rows a seed receipt recorded (before teardown-main)
unseed-geometry receipt target:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.geometry unseed {{quote(receipt)}} {{quote(target)}}

# Remove exactly the procurement rows a seed receipt recorded (before teardown-main)
unseed-lifecycle receipt target:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.lifecycle unseed {{quote(receipt)}} {{quote(target)}}

# Derive the showcase tour (saved filters, bookmarks, home dashboard) and the
# offline F1-F5 first-impression numbers from a frozen carrier plan
showcase plan out:
    python3 -m estates.showcase build {{quote(plan)}} --out {{quote(out)}}

# Recompute a saved showcase artifact from its bound plan (operands match `showcase`)
showcase-check plan out:
    python3 -m estates.showcase check {{quote(out)}} --plan {{quote(plan)}}

# Write the tour over REST and read it back exactly. Requires SHOWCASE_WRITES=1,
# the estate on main, and one Home visit by the token's user (creates its dashboard).
seed-showcase out target receipt='':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.showcase seed {{quote(out)}} {{quote(target)}} {{if receipt == '' { '' } else { '--receipt ' + quote(receipt) }}}

# Restore the prior dashboard and delete exactly the filters and bookmarks a receipt created
unseed-showcase receipt target:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.showcase unseed {{quote(receipt)}} {{quote(target)}}

# Regenerate docs/schema-map.md (kind -> NetBox model -> endpoint -> identity -> delivery)
schema-map:
    python3 -m estates.schema_map

# Offline: does this artifact fit the TurboBulk compiler contract? (no target, no token)
load-check artifact:
    python3 -m estates.load {{quote(artifact)}} --load-check

# Create one named ready branch on the target (the prerequisite for just load)
branch target name timeout='300':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.branch {{quote(target)}} {{quote(name)}} --timeout {{quote(timeout)}}

# Permanently delete one named branch and everything in it (demo retirement); reset instead REPLACES a branch
branch-delete target name:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.branch {{quote(target)}} {{quote(name)}} --delete

# Full demo retirement: delete the branch AND the namespace's main-scoped rows
# (event rule, webhook, export templates, custom-field trio, owners/owner groups)
retire target name namespace tenancy="shared":
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.branch {{quote(target)}} {{quote(name)}} --delete --retire-namespace {{quote(namespace)}} --tenancy {{quote(tenancy)}}

# Strictly verify a target against an artifact with zero writes (any seeding path)
verify-target artifact target branch='':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} {{if branch == '' { '' } else { '--branch ' + quote(branch) }}} --verify-only

# Explain the transport choice and compatibility blockers without writing
load-explain artifact target branch='':
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} {{if branch == '' { '' } else { '--branch ' + quote(branch) }}} --delivery-policy reviewable --explain

# Explain disposable-baseline selection without writing
load-explain-disposable artifact target branch:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} --branch {{quote(branch)}} --delivery-policy disposable-baseline --explain

# Permanently replace one named disposable branch with a uniquely named branch
reset target branch:
    @[ -n "${NETBOX_TOKEN:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; python3 -m estates.reset {{quote(target)}} {{quote(branch)}}

# Review an inherited branch's acquisition and refresh; no target writes
scenario plan='build/bank-v9/plan.json' site='br-s0002' output='build/acquisition-refresh':
    python3 -m estates scenario {{quote(plan)}} --site {{quote(site)}} --out {{quote(output)}}

# Select a dual-supply service host and export a deliberate power-diversity defect
power-scenario plan='build/bank-v9/plan.json' output='build/power-diversity' site='':
    python3 -m estates scenario {{quote(plan)}} --kind loss-of-power-diversity --out {{quote(output)}} {{if site == '' { '' } else { '--site ' + quote(site) }}}

# Take one modeled leased span offline and inspect actual alternate customer paths
span-scenario plan='build/provider-v9/plan.json' output='build/span-maintenance' span='':
    python3 -m estates scenario {{quote(plan)}} --kind provider-span-maintenance --out {{quote(output)}} {{if span == '' { '' } else { '--span ' + quote(span) }}}

# Check exact expected findings and offline restoration from saved snapshots
scenario-check scenario='build/power-diversity/scenario.json':
    python3 -m estates scenario-check {{quote(scenario)}}

# Build the Assurance drift twin: an observed Diode payload plus its exact deviation manifest
drift plan='build/bank-v9/plan.json' output='build/discovery-drift':
    python3 -m estates drift {{quote(plan)}} --out {{quote(output)}}

# Recompute a saved drift twin from its bound baseline and rebind the observed payload
drift-check output='build/discovery-drift':
    python3 -m estates drift-check {{quote(output)}}

# Ingest a checked drift payload into a Diode-configured target, one phase at a
# time in manifest order. Requires the devenv diode profile and the .env Diode
# credentials; DIODE_WRITES=1 is the operator attestation. Acknowledgement is
# acceptance only: on an Assurance-review tenant the records become pending
# deviations, and rendering them is UI-side evidence this repo does not claim.
drift-ingest output='build/discovery-drift':
    python3 -m estates drift-check {{quote(output)}}
    [ -n "${DIODE_TARGET:-}" ] || { set -a; [ ! -f .env ] || . ./.env; set +a; }; \
    echo "Diode target: $DIODE_TARGET" >&2; \
    [ "$DIODE_WRITES" = "1" ] || { echo "DIODE_WRITES=1 not attested" >&2; exit 2; }; \
    for phase in {{quote(output)}}/observed/phase-*.json; do \
      python3 -m netboxlabs.diode.scripts.dryrun_replay --target "$DIODE_TARGET" \
        --app-name devin-generator --app-version "$(python3 -c 'import estates; print(estates.__version__)')" \
        "$phase" || exit 2; \
    done

# Independent mutation tests and growth/determinism/scale regressions
check:
    python3 -m unittest discover -s tests -v
    python3 -m unittest lab.test_setup -v
    python3 -m unittest lab.discovery.test_render -v
    git diff --check

# Prepare private local target configuration once; existing targets are preserved
lab-prepare:
    python3 -m lab.setup

# Opt in to the pinned local panel-mapping bridge; then rebuild with lab-up
lab-front-ports:
    python3 -m lab.setup --front-port-compat

# Start the dedicated macOS VM and pinned NetBox/Diode services
lab-up:
    colima start netbox-generator --cpus 4 --memory 8 --disk 60 --vm-type vz --runtime docker --activate=false --ssh-config=false --mount {{quote(justfile_directory() + ':w')}}
    {{local_docker}} network inspect netbox-generator-link >/dev/null 2>&1 || {{local_docker}} network create netbox-generator-link
    {{local_netbox}} build netbox
    {{local_netbox}} up -d --wait --wait-timeout 600
    {{local_diode}} up -d --pull missing
    python3 -m lab.token

# Show only this lab's service status
lab-status:
    {{local_netbox}} ps
    {{local_diode}} ps

# Run native SDK replay with local reconciliation barriers; use the diode profile
lab-load directory receipt compatibility='official':
    python3 -m lab.replay {{quote(directory)}} --receipt {{quote(receipt)}} --phase-timeout 600 {{if compatibility == 'front-ports' { '--front-port-compat' } else { '' }}}

# Idempotent: rerun after any VM or container restart (it re-pins MACs and re-seeds Vault).
# state=clean renders the documented state without drift (Day-1 seeding route).
# Render the SR Linux discovery lab a provider plan carries (recipe discovery_lab) and
# run it in its own Colima VM; a 4-node lab gets a 10 GiB VM, otherwise 8 GiB
discovery-lab-up plan out='build/discovery-lab' state='drift':
    python3 lab/discovery/render.py {{quote(plan)}} {{quote(out)}} {{if state == 'clean' { '--clean' } else { '' } }}
    {{discovery_colima}} start -p genial-discovery --cpu 4 --memory "$(python3 -c 'import json,sys; print(10 if len(json.load(open(sys.argv[1]))["nodes"]) > 3 else 8)' {{quote(out / 'manifest.json')}})" --disk 40 --vm-type vz --runtime docker --activate=false
    {{discovery_vm}} up {{quote(absolute_path(out))}}

# Dry-run real orb-agent discovery (no Diode writes); fail unless it differs only by drift.json
discovery-lab-check out='build/discovery-lab':
    {{discovery_vm}} dry-run {{quote(absolute_path(out))}}
    python3 lab/discovery/render.py --check {{quote(out)}} {{quote(out + '/dry-run')}}

# Start the fleet-managed orb-agent; ENV holds the New Orb agent form's FLEET_* values
discovery-agent-up env='build/discovery-lab/fleet.env':
    {{discovery_vm}} agent {{quote(absolute_path(env))}}

discovery-agent-logs lines='50':
    {{discovery_vm}} agent-logs {{lines}}

discovery-agent-down:
    {{discovery_vm}} agent-stop

discovery-lab-status:
    {{discovery_vm}} status

# Destroy the lab, agent and dev Vault; `colima stop -p genial-discovery` also frees the VM
discovery-lab-down:
    {{discovery_vm}} down

# Compare the live graph after a Diode replay; an optional prior successful receipt checks stable IDs.
# The pinned SDK cannot carry the plan's automation records, so this lane compares
# the delivered scope and records the exclusion; just verify-target is the complete gate.
lab-verify plan receipt previous='' existing='':
    python3 -m lab.verify {{quote(plan)}} --url http://127.0.0.1:8000 --token-file build/local-target/netbox-token --receipt {{quote(receipt)}} --strict-inventory --diode-delivered-only {{if previous == '' { '' } else { '--previous-receipt ' + quote(previous) }}} {{if existing == '' { '' } else { '--allow-existing-receipt ' + quote(existing) }}}

# Capture the pinned target's built-in identities before any estate ingestion
lab-bootstrap receipt:
    python3 -m lab.verify --bootstrap --url http://127.0.0.1:8000 --token-file build/local-target/netbox-token --receipt {{quote(receipt)}}

# Stop this lab's VM, retaining containers, databases, and credentials for restart
lab-down:
    colima stop netbox-generator

# Permanently clear this lab's NetBox/Diode data, then start empty services
lab-reset:
    {{local_diode}} down --volumes --remove-orphans
    {{local_netbox}} down --volumes --remove-orphans
    rm -f build/local-target/netbox-token
    just lab-up
