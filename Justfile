# Offline generation plus an opt-in disposable local Diode target.
local_docker := "docker --context colima-netbox-generator"
local_netbox := local_docker + " compose --project-directory build/local-target/netbox -f build/local-target/netbox/docker-compose.yml -f build/local-target/netbox/compose.local.json"
local_diode := local_docker + " compose --project-directory build/local-target/diode -f build/local-target/diode/docker-compose.yaml -f build/local-target/diode/compose.local.json"

help:
    @just --list --unsorted

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

# Inspect the target, choose a faithful transport, load, and strictly verify
load artifact target branch='' turbobulk_job_rows='2000':
    @set -a; [ ! -f .env ] || . ./.env; set +a; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} {{if branch == '' { '' } else { '--branch ' + quote(branch) }}} --delivery-policy reviewable --turbobulk-job-rows {{quote(turbobulk_job_rows)}}

# Faster baseline for a fresh throwaway branch; it cannot be reviewed, merged, or reverted
load-disposable artifact target branch turbobulk_job_rows='2000':
    @set -a; [ ! -f .env ] || . ./.env; set +a; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} --branch {{quote(branch)}} --delivery-policy disposable-baseline --turbobulk-job-rows {{quote(turbobulk_job_rows)}}

# Explain the transport choice and compatibility blockers without writing
load-explain artifact target branch='':
    @set -a; [ ! -f .env ] || . ./.env; set +a; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} {{if branch == '' { '' } else { '--branch ' + quote(branch) }}} --delivery-policy reviewable --explain

# Explain disposable-baseline selection without writing
load-explain-disposable artifact target branch:
    @set -a; [ ! -f .env ] || . ./.env; set +a; python3 -m estates.load {{quote(artifact)}} {{quote(target)}} --branch {{quote(branch)}} --delivery-policy disposable-baseline --explain

# Permanently replace one named disposable branch with a uniquely named branch
reset target branch:
    @set -a; [ ! -f .env ] || . ./.env; set +a; python3 -m estates.reset {{quote(target)}} {{quote(branch)}}

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

# Independent mutation tests and growth/determinism/scale regressions
check:
    python3 -m unittest discover -s tests -v
    python3 -m unittest lab.test_setup -v
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

# Compare the live graph; an optional prior successful receipt checks stable IDs
lab-verify plan receipt previous='' existing='':
    python3 -m lab.verify {{quote(plan)}} --url http://127.0.0.1:8000 --token-file build/local-target/netbox-token --receipt {{quote(receipt)}} --strict-inventory {{if previous == '' { '' } else { '--previous-receipt ' + quote(previous) }}} {{if existing == '' { '' } else { '--allow-existing-receipt ' + quote(existing) }}}

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
