#!/usr/bin/env bash
# Runs INSIDE the genial-discovery Colima VM (the Justfile calls it over colima ssh).
#   vm.sh up OUT       deploy/refresh the rendered lab, pin MACs, wait for SSH
#   vm.sh dry-run OUT  run orb-agent device discovery with dry_run, write OUT/dry-run/
#   vm.sh agent ENV    start the fleet-managed orb-agent with FLEET_* credentials
#   vm.sh status       containers and resource use
#   vm.sh down         destroy the lab and any agent
# OUT is the host-side render directory; the host home is mounted at the same path.
set -euo pipefail

CLAB_VERSION=0.79.0
SRL_IMAGE=ghcr.io/nokia/srlinux:26.7.2
SRL_DIGEST=sha256:0096fe3ebcafabb7253492e2060425fe027a168e0e066766d1e85efbb0b48be8
ORB_IMAGE=netboxlabs/orb-agent:2.15.0
ORB_DIGEST=sha256:c1764046059f8f0f34160975e0b12e024d1d8952dba22851a8ad125cde1d2b33
VAULT_IMAGE=hashicorp/vault:2.1.1
VAULT_DIGEST=sha256:47f14a6acb98f48d798a07df7c83f23a6e636e1cf724c5f8ff165cb32667a1e2
LAB=genial-discovery
WORK=$HOME/$LAB   # VM-local copy: the running lab never depends on a host checkout

pinned() {  # image, digest
  docker image inspect "$1" >/dev/null 2>&1 || docker pull -q "$1" >/dev/null
  docker image inspect --format '{{join .RepoDigests " "}}' "$1" | grep -q "$2" \
    || { echo "$1 does not resolve to $2; refusing to run an unpinned image" >&2; exit 1; }
}

nodes() { python3 -c 'import json,sys; [print(n["name"], n["mgmt"].split("/")[0], n["base_mac"]) for n in json.load(open(sys.argv[1]))["nodes"]]' "$WORK/manifest.json"; }

up() {
  local out=$1
  if [ "$(containerlab version 2>/dev/null | awk '/version:/ {print $2; exit}')" != "$CLAB_VERSION" ]; then
    curl -sL https://get.containerlab.dev -o /tmp/get-clab.sh
    sudo bash /tmp/get-clab.sh -v "$CLAB_VERSION"
  fi
  pinned "$SRL_IMAGE" "$SRL_DIGEST"
  pinned "$ORB_IMAGE" "$ORB_DIGEST"
  mkdir -p "$WORK"
  cp -R "$out"/. "$WORK"/
  cd "$WORK"
  sudo containerlab deploy -t "$LAB.clab.yml" --reconfigure
  # containerlab randomises the second byte of every SR Linux base MAC per
  # deploy; NetBox records carry the rendered MACs, so pin them and restart
  # (containerlab restart keeps the dataplane links).
  while read -r name _ mac; do
    sudo sed -i "s/\"base_mac\": \".*\"/\"base_mac\": \"$mac\"/" "clab-$LAB/$name/topology.yml"
  done < <(nodes)
  sudo containerlab restart -t "$LAB.clab.yml"
  while read -r name ip mac; do
    want=$(echo "${mac%:00:00:00}:ff:00:00" | tr a-f A-F)
    for _ in $(seq 1 60); do
      got=$(sudo docker exec "clab-$LAB-$name" sr_cli "show version" 2>/dev/null | awk -F': ' '/HW MAC/ {print $2}') || true
      [ "$got" = "$want" ] && break
      sleep 3
    done
    [ "$got" = "$want" ] || { echo "$name: base MAC $got, expected $want" >&2; exit 1; }
    # mgmt0 is the Docker endpoint, whose MAC containerlab also randomises and
    # nothing in the topology can set; re-apply the rendered one (lost again on
    # any container or VM restart - rerun `up`, it is idempotent).
    sudo docker exec "clab-$LAB-$name" ip link set dev mgmt0 address "$mac"
    sudo docker exec "clab-$LAB-$name" ip -n srbase-mgmt link set dev mgmt0.0 address "$mac"
    for _ in $(seq 1 20); do (exec 3<>"/dev/tcp/$ip/22") 2>/dev/null && break; sleep 3; done
    echo "$name up: mgmt $ip ssh/22 open, system MAC $got"
  done < <(nodes)
  vault
}

dry_run() {
  local out=$1
  pinned "$ORB_IMAGE" "$ORB_DIGEST"
  mkdir -p "$WORK/orb"
  sudo rm -rf "$WORK/orb/out"
  cp "$out/agent.dry-run.json" "$WORK/orb/agent.yaml"
  docker rm -f "$LAB-dry-run" >/dev/null 2>&1 || true
  # Same credential path as the fleet job: the password is a Vault reference.
  docker run -d --name "$LAB-dry-run" --net=host --env-file "$WORK/vault.env" -v "$WORK/orb:/opt/orb" \
    "$ORB_IMAGE" run -c /opt/orb/agent.yaml >/dev/null
  want=$(nodes | wc -l)
  for _ in $(seq 1 60); do
    [ "$(ls "$WORK/orb/out" 2>/dev/null | wc -l)" -ge "$want" ] && break
    sleep 2
  done
  docker logs "$LAB-dry-run" 2>&1 | grep -E 'ingested|ERROR|error' || true
  docker rm -f "$LAB-dry-run" >/dev/null
  rm -rf "$out/dry-run"; mkdir -p "$out/dry-run"
  cp "$WORK"/orb/out/*.json "$out/dry-run/"
  echo "$(ls "$out/dry-run" | wc -l) dry-run files -> $out/dry-run"
}

vault() {
  # Fleet-management credentials store the device password only as a HashiCorp
  # Vault/OpenBao reference (docs: assurance/fleet-management/credentials), so
  # the lab carries a VM-local dev-mode Vault on 127.0.0.1:8200 holding it.
  # Dev mode is in-memory: every `up` re-seeds it under the same private token.
  pinned "$VAULT_IMAGE" "$VAULT_DIGEST"
  [ -s "$WORK/vault.env" ] || {
    umask 077
    token=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
    printf '%s\n' ORB_SECRETS_MANAGER__ACTIVE=vault \
      ORB_SECRETS_MANAGER__SOURCES__VAULT__ADDRESS=http://127.0.0.1:8200 \
      ORB_SECRETS_MANAGER__SOURCES__VAULT__AUTH=token \
      "ORB_SECRETS_MANAGER__SOURCES__VAULT__AUTH_ARGS__TOKEN=$token" > "$WORK/vault.env"
  }
  # Default KV-v2 mount, so both secret//genial-discovery/srl/password and the
  # short genial-discovery/srl/password reference resolve.
  grep -q '__MOUNT=' "$WORK/vault.env" || echo ORB_SECRETS_MANAGER__SOURCES__VAULT__MOUNT=secret >> "$WORK/vault.env"
  token=$(sed -n 's/^ORB_SECRETS_MANAGER__SOURCES__VAULT__AUTH_ARGS__TOKEN=//p' "$WORK/vault.env")
  if ! docker ps --format '{{.Names}}' | grep -qx "$LAB-vault"; then
    docker rm -f "$LAB-vault" >/dev/null 2>&1 || true
    docker run -d --name "$LAB-vault" --restart unless-stopped --net=host --cap-add IPC_LOCK \
      -e VAULT_DEV_ROOT_TOKEN_ID="$token" -e VAULT_DEV_LISTEN_ADDRESS=127.0.0.1:8200 \
      "$VAULT_IMAGE" server -dev >/dev/null
    sleep 3
  fi
  pw=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["credential"]["password"])' "$WORK/manifest.json")
  docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN="$token" "$LAB-vault" \
    vault kv put -mount=secret "$LAB/srl" username=admin password="$pw" >/dev/null
  echo "vault: secret/$LAB/srl {username,password} seeded; reference secret//$LAB/srl/password"
}

agent() {  # the real, fleet-managed agent; credentials come from the platform's New Orb agent form
  local env=$1
  grep -q '^FLEET_CLIENT_ID=.' "$env" && grep -q '^FLEET_CLIENT_SECRET=.' "$env" && grep -q '^FLEET_AUTH_URL=.' "$env" \
    || { echo "$env must set FLEET_AUTH_URL, FLEET_CLIENT_ID and FLEET_CLIENT_SECRET" >&2; exit 2; }
  pinned "$ORB_IMAGE" "$ORB_DIGEST"
  docker rm -f "$LAB-orb-agent" >/dev/null 2>&1 || true
  # No -c: with FLEET_* set the entrypoint uses the image's fleet default config,
  # so policies (Discovery jobs) and credentials arrive from the platform.
  docker run -d --name "$LAB-orb-agent" --restart unless-stopped --stop-timeout 60 --log-driver local \
    --net=host --env-file "$WORK/vault.env" --env-file "$env" "$ORB_IMAGE" run >/dev/null
  sleep 10
  docker logs "$LAB-orb-agent" 2>&1 | tail -15
}

case "${1:-}" in
  up) up "$2" ;;
  dry-run) dry_run "$2" ;;
  agent) agent "$2" ;;
  agent-logs) docker logs --tail "${2:-50}" "$LAB-orb-agent" ;;
  agent-stop) docker stop -t 60 "$LAB-orb-agent" && docker rm "$LAB-orb-agent" ;;
  status)
    sudo containerlab inspect -t "$WORK/$LAB.clab.yml" || true
    docker ps --format '{{.Names}}\t{{.Status}}' | grep -E "$LAB|orb" || true
    docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}'
    free -m ;;
  down)
    docker rm -f "$LAB-dry-run" >/dev/null 2>&1 || true
    docker stop -t 60 "$LAB-orb-agent" >/dev/null 2>&1 && docker rm "$LAB-orb-agent" >/dev/null || true
    docker rm -f "$LAB-vault" >/dev/null 2>&1 || true
    [ ! -f "$WORK/$LAB.clab.yml" ] || sudo containerlab destroy -t "$WORK/$LAB.clab.yml" --cleanup
    sudo rm -rf "$WORK" ;;
  *) echo "usage: vm.sh up OUT | dry-run OUT | agent ENV | agent-logs [N] | agent-stop | status | down" >&2; exit 2 ;;
esac
