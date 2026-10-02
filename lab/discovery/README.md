# Real-discovery lab

A small, simulated network that a **real orb-agent** discovers, so NetBox Labs
Assurance's own Fleet management pages (Orb agents, Credentials, Discovery
jobs, Run history) and the resulting Deviations can be shown without physical
hardware. Everything runs in a dedicated Colima VM; nothing here writes to a
NetBox target.

Unlike the [drift twin](../../docs/scenarios.md#assurance-discovery-drift),
which writes a synthetic Diode payload, every observation here is produced by
the shipped orb-agent device-discovery backend over SSH to a real network OS.

## Design: the lab is its own, honest slice

The provider estate's PoP routers are Juniper MX204s. A Nokia SR Linux
container discovered at one of their addresses would report `7220 IXR-D2L`,
`NOKIA_SRL v26.7.2`, `ethernet-1/N` ports and a simulator serial. Assurance
would then show "device type changed" and dozens of "new interface" deviations
that are artefacts of the demo, not drift. So the lab is **not** matched
against the production records.

Instead `render.py` renders a **staging replica** of the first PoP in the
permanent `provider-pop-order` ledger:

- **Nodes.** The PoP's PE pair plus PE A's first-ordered backbone neighbour
  (`--nodes 4` also adds PE B's neighbour; needs a 10 GB VM). Names are
  `lab-<production name>`, for example `lab-popchicagocermak-pe-a`.
- **Wiring.** Lab links follow the routed /31 adjacencies among exactly those
  routers in the plan. 100G production ports map to D2L QSFP28 ports 49–56.
- **Records.** `lab-slice.json` lists the NetBox records in the plan's own
  object grammar. There is a `Network Lab` room and rack `L01` at the NOC site.
  Devices use device type `Nokia 7220 IXR-D2L`, platform `NOKIA_SRL v26.7.2`
  and role `Lab Router`. Each device has all 58 front-panel ports, typed from
  devicetype-library, plus `mgmt0`, `system0`, subinterfaces, MACs and IPs.
  Descriptions and comments say each device is a container that mirrors a
  named production router's wiring, not its hardware.
- **Addressing.** The lab uses RFC 2544 benchmarking space, `198.18.0.0/15`,
  which is reserved for device-test labs and disjoint from the estate's
  `10.0.0.0/8` and `2001:db8::/32`. Management is `198.18.0.0/24`, links come
  from `198.19.0.0/24` and loopbacks from `198.19.255.0/24`.
- **MACs.** Each SR Linux base MAC is `1a:<sha256(lab/namespace)>:<index>:00:00:00`.
  containerlab randomises base MACs on every deploy, so `vm.sh up` pins the
  rendered ones.
- **Drift.** `drift.json` lists three deliberate on-box changes. The startup
  configs apply them, and the records never include them.

With `--clean`, the lab matches the slice exactly. A real orb-agent dry run of
the clean lab produces **0** predicted deviations across every emitted entity.
With the drift (213 entities) it produces exactly these **6** predicted
deviations:

| Drift item | Expected deviation(s) |
|---|---|
| `port-shut-on-box`: `lab-popchicagocermak-pe-a ethernet-1/50` admin-shut | interface update `enabled true → false` |
| `loopback-renumbered-on-box`: `lab-popchicagocermak-pe-b system0.0` 198.19.255.2 → .102 | ip_address create `198.19.255.102/32` (the old address is simply unobserved; Diode has no deletes) |
| `undocumented-test-port`: `lab-popchicagopilsen-pe-b ethernet-1/1` enabled + addressed | interface update `enabled false → true`; interface create `ethernet-1/1.0`; ip_address create `198.19.0.254/31`; prefix create `198.19.0.254/31` |

`just discovery-lab-check` proves this table against the agent's real dry-run
output. The 6 rows are a prediction from comparing that output with the slice.
They are **not** observed Assurance behaviour. Assurance's matcher may still
differ, for example on fields the dry run omits.

**Descriptions cannot drift here.** The bundled `nokia_srl` driver (orb-agent
2.15.0) parses `show interface all`, which on SR Linux 26.7 prints no
descriptions, so no description reaches Diode. Description drift was
therefore dropped. That is an upstream driver limitation worth reporting.

## Pinned versions (verified 2026-10-01)

| Component | Version | Digest / note |
|---|---|---|
| Colima | 0.10.1 (nixpkgs) | profile `genial-discovery`, vz, aarch64, 4 CPU, 8 GiB, 40 GiB |
| Docker Engine in VM | 29.2.1 | Ubuntu 24.04.4, kernel 6.8 |
| containerlab | 0.79.0 | installed in the VM by `get.containerlab.dev -v 0.79.0` |
| Nokia SR Linux | `ghcr.io/nokia/srlinux:26.7.2` (build 26.7.2-519) | `sha256:0096fe3ebcafabb7253492e2060425fe027a168e0e066766d1e85efbb0b48be8` (arm64) |
| orb-agent | `netboxlabs/orb-agent:2.15.0` (device-discovery 1.33.0, napalm 5.2.0, diode-sdk-python 1.14.1) | `sha256:c1764046059f8f0f34160975e0b12e024d1d8952dba22851a8ad125cde1d2b33` (arm64) |
| Vault (dev mode, VM-local) | `hashicorp/vault:2.1.1` | `sha256:47f14a6acb98f48d798a07df7c83f23a6e636e1cf724c5f8ff165cb32667a1e2` (arm64) |

`vm.sh` refuses to run if an image tag does not resolve to its digest.

## Runbook

Prerequisites: macOS with `colima` on `PATH`, or no system install and the
ephemeral form `export DISCOVERY_COLIMA='nix shell nixpkgs#colima -c colima'`.
The repository must be under your home directory, which Colima mounts at the
same path in the VM. Do not touch other Colima profiles; this lab only uses
`genial-discovery`.

```sh
just discovery-lab-up build/<estate>/plan.json   # render, start VM, deploy, pin MACs, seed Vault (~2 min)
just discovery-lab-check                         # real orb-agent dry run + exact-drift proof
just discovery-lab-status                        # nodes, agent, memory
```

`discovery-lab-up` is idempotent. Rerun it after any VM or container restart,
because Docker restores the random `mgmt0` MAC and dev-mode Vault forgets its
secret. Use `state=clean` to deploy without drift:
`just discovery-lab-up PLAN build/discovery-lab 3 clean`.

Inside the VM (`colima ssh -p genial-discovery`), the nodes are reachable:

- SSH: `ssh admin@198.18.0.11`, password `NokiaSrl1!`, the containerlab default.
- JSON-RPC over HTTP and HTTPS:
  `curl -u admin:NokiaSrl1! http://198.18.0.11/jsonrpc -d '{"jsonrpc":"2.0","id":1,"method":"get","params":{"commands":[{"path":"/system/name/host-name","datastore":"state"}]}}'`
- gNMI on `:57400`: `docker run --rm --net=host ghcr.io/openconfig/gnmic -a 198.18.0.13:57400 -u admin -p 'NokiaSrl1!' --skip-verify -e json_ietf get --path /system/name/host-name`

All three were verified.

### Teardown

```sh
just discovery-lab-down                  # destroy nodes, agent, Vault and the VM-local copy
colima stop -p genial-discovery          # free the VM's 8 GiB (keeps images)
colima delete -p genial-discovery        # remove the VM entirely
```

## Platform side (NetBox Cloud, Fleet management)

These steps are not yet run. They need the lab records in NetBox first.

### 0. Get the lab records into NetBox

Device jobs scope NetBox **devices**, so the lab devices must exist first.

- **Route A (preferred, needs the generator change below).** The estate plan
  carries the slice, so the normal `just load` or `seed-main` delivers it with
  everything else.
- **Route B (tonight, no generator change; Day-1 discovery).**
  1. Run `just discovery-lab-up PLAN build/discovery-lab 3 clean` and
     `just discovery-lab-check`. The check must report 0 deviations.
  2. Replay `build/discovery-lab/dry-run/*.json` through Diode. These files
     are the SDK's dry-run format; use the drift-ingest pattern:
     `python3 -m netboxlabs.diode.scripts.dryrun_replay --target "$DIODE_TARGET" --app-name genial-discovery --app-version 1 FILE`
     in the devenv `diode` profile with `DIODE_WRITES=1` attested.
     - The orb-agent image's own diode-sdk-python 1.14.1 parses these files
       with `load_dryrun_entities` (verified).
     - The replay to a target is not yet run.
  3. Apply the resulting *create* deviations in Assurance. Bulk Apply is the
     Day-1 story.
  4. Run `just discovery-lab-up PLAN` again to re-render with drift.

  Route B creates what discovery reports and nothing more. The rack, cables,
  pool prefix and descriptions from the slice are not created.

### 1. Orb agent

1. Go to **Fleet management → Orb agents → New Agent**.
2. Set Name to `genial-discovery-lab` and Labels to `lab=genial-discovery`.
3. Select Save & Continue, then copy `FLEET_AUTH_URL`, `FLEET_CLIENT_ID` and
   `FLEET_CLIENT_SECRET`. The secret is shown once.
4. Write those three lines, and only those, into `build/discovery-lab/fleet.env`
   (ignored), then `chmod 600` it.
5. If the form asks for Vault settings, enter:
   - address `http://127.0.0.1:8200`
   - auth `token`
   - mount `secret`
   - token from `colima ssh -p genial-discovery -- grep TOKEN ~/genial-discovery/vault.env`

   The lab already passes these as `ORB_SECRETS_MANAGER__*` from that file.
   Do not paste them into `fleet.env`: a later `--env-file` overrides the
   earlier one.
6. Run `just discovery-agent-up`. This starts `netboxlabs/orb-agent:2.15.0 run`
   in the VM with `--net=host --restart unless-stopped`, the fleet default
   config and the lab Vault env. The agent should show **Online** (5 s
   heartbeats). Watch it with `just discovery-agent-logs`.

### 2. Credential

1. Go to **Fleet management → Credentials → New Credential**.
2. Set Name to `genial-discovery-srl`, Type to `Username + Password` and
   Target to `Device`.
3. For Username, **uncheck** "Is Vault reference" and enter `admin`.
4. For Password, enter the Vault reference as a bare path:
   `secret//genial-discovery/srl/password`.
   - The short form `genial-discovery/srl/password` also resolves because the
     mount is set.
   - Both forms were verified through the agent's Vault resolver in dry run.
5. Bind the credential to the 3 lab devices.

### 3. Discovery job

1. Set Type to **Device**, Agent to `genial-discovery-lab` and Schedule to
   empty (one-time) or e.g. `*/30 * * * *`.
2. Leave "Capture running/saved configuration" **off**. A DeviceConfig
   deviation is untested here.
3. Set Scope to the 3 `lab-…` devices and bind the credential.

When the job has run, Run history should show it and Deviations should show
the 6 rows above.

### Open risks to check on the first run

1. **Driver.** The policy must carry `driver: nokia_srl`, a bundled custom
   driver.
   - Without it, auto-detection tries only the 7 standard NAPALM drivers. All
     of them fail on SR Linux, taking about 85 s per host (measured).
   - The documented wizard has no driver field. Check the agent log for
     `Get driver 'nokia_srl'` versus `Driver not informed`.
   - Fallback (partially tested): `INSTALL_DRIVERS_PATH` with
     `napalm-srlinux==0.3.1` adds an `srlinux` driver to auto-detection. It is
     reached but fails TLS verification against containerlab's self-signed
     JSON-RPC certificate. It would also report a different platform name.
2. **Defaults.** The policy must also carry the defaults in
   `manifest.json` → `job_defaults`:
   - site `Chicago NOC Campus`, location `Network Lab` and role `Lab Router`;
   - `interface_patterns`: 25G, 100G, 10G, `mgmt0` and `system0` → `virtual`.

   The documented wizard exposes none of them. Without the patterns, every
   `ethernet-1/N` is reported `1000base-t` (about 174 type deviations) and
   `system0` is reported `other`. If the console derives site and role from
   the NetBox record, those match. The interface types are the real risk.
3. **Matcher.** The 6-row prediction compares the discovered fields only.
   Assurance may compare others (e.g. device site or tenant).

If 1 or 2 bite, the honest outcome is to show the noise as a known preview
limitation. Alternatively, drive the same agent with `config_manager: local`
and `agent.dry-run.json` without `dry_run`. That runs real discovery, real
Diode and real deviations, but the Discovery jobs and Run history pages would
not list it.

## Resource use (measured)

| What | Usage |
|---|---|
| VM | 8 GiB; about 5.8 GiB used with 3 nodes, Vault and no agent |
| Each SR Linux node | about 1.8 GiB |
| Vault | about 31 MiB |
| orb-agent | 1.5–2 GiB recommended (vendor guidance) |

Four nodes in 8 GiB ran out of memory: about 190 MiB free. Netmiko read
timeouts then failed discovery, so 3 nodes is the default and `--nodes 4`
selects a 10 GiB VM. The host also runs `netbox-generator`, another 8 GiB VM.

## Generator proposal: emit the lab slice in the estate (v0.17+)

The integrator owns estate generation. This is a proposal, not a change made
here.

- **New `estates/discovery_lab.py`**: `add_discovery_lab(world)`.
  - Provider-only, called from `estates/provider.py` after BGP.
  - Port `render.select`, `build` and `slice_objects` unchanged. They already
    emit plan-grammar objects with stable keys
    (`device/<noc>/network-lab/<name>`, `hardware/lab-router`,
    `platform/lab-srl`, `role/lab-router`, `location/<noc>/network-lab`,
    `rack/<noc>/network-lab/lab-01`, `prefix/lab/...`).
  - Selection already uses the permanent `provider-pop-order` ledger and
    numeric-aware ordering, so growth appends PoPs without moving the lab.
- **Recipe key** `discovery_lab = true`, off by default. The lab adds 392
  objects and a new manufacturer.
  - Turning it on changes the canonical graph: bump `estates/__version__`,
    which makes this a new baseline.
  - Store the RFC 2544 pool as an aggregate under a new private RIR `Benchmarking (RFC 2544)`.
- **Catalog.** Add a `lab-router` hardware entry for the Nokia 7220 IXR-D2L.
  - Model string as SR Linux reports it, part `3HE17645AA`.
  - 48×SFP28, 8×QSFP28 and 2×SFP+ from devicetype-library
    `Nokia/7220-IXR-D2L-25-100GE.yaml`, plus `mgmt0`.
  - Label it a container emulation and give it no PSU or power draw. The
    devices draw no modeled power and get no PDU cabling.
- **Validator** `validate_provider.py`: an independent check that lab devices
  - reference only lab-pool addresses;
  - carry no production cable, circuit, VRF or BGP session;
  - have every interface the D2L type defines.

  Add a failing mutation for each.
- **Naming.** Device names keep the `lab-<device>` identity form. Location,
  rack, role and platform names are authored. Add `NOKIA_SRL v26.7.2` to the
  naming sweep's reasoned exceptions: it is the exact string discovery
  reports, kept verbatim so the platform matches.
- **Loader.** No new kinds. Every slice kind is already in the 105-kind
  TurboBulk contract and the Diode SDK.
- **Tooling.** Keep `lab/discovery/render.py` as the containerlab/config
  renderer. Once the plan carries `device/*/network-lab/*`, read the slice
  from the plan instead of `build()`.
