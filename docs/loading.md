# Artifacts and Diode loading

[Back to the quick start](../README.md) · [Documentation map](../README.md#documentation)

Generation writes a complete artifact; loading it into NetBox is a separate operation. Use Diode and verify reconciliation on the intended target.

Run commands from the repository root. Paths in code blocks are relative to that root.
Generated `build/` artifacts and qualification receipts are local outputs, not included in a clone.

## Artifacts and Diode

| File | Purpose |
| --- | --- |
| `plan.json` | Frozen resolved recipe, graph, allocation/design ledgers, hardware digest, and profile assertions |
| `report.md` | Human walkthrough derived from that graph |
| `intent.json` | Resolved inputs, supplied/default provenance and explicit planning assumptions |
| `coverage.json` | Pinned model coverage, actual counts/example keys, external bindings and native SDK gaps |
| `checks.json` | Offline validator result and plan digest; explicitly records live ingestion as untested |
| `diode/manifest.json` | Checksums, counts, deterministic request IDs, dependency phases, and replay notes |
| `diode/phase-*.json` | Diode dry-run replay requests; at most 1,000 entities and 2,500,000 JSON bytes each |

The export contract is pinned to **netboxlabs-diode-sdk 1.14.0**. The optional
devenv `diode` profile installs it in a devenv-managed environment:

```sh
devenv --profile diode shell
just sdk-check build/bank-v9/diode
just check
```

The SDK check verifies manifest integrity, protobuf parsing, actual encoded
request sizes, and the validation rules embedded in the pinned descriptors.
It reports the known target limitations from the manifest. It does not contact
Diode or NetBox; a passing SDK check does not resolve the panel mapping gap.

For a target already configured with Diode, use the SDK's existing
[dry-run replay helper](https://github.com/netboxlabs/diode-sdk-python/blob/v1.14.0/netboxlabs/diode/scripts/dryrun_replay.py).
Supply `DIODE_CLIENT_ID` and `DIODE_CLIENT_SECRET` through your existing credential
environment, and `DIODE_TARGET` for that target. A plain NetBox URL/token alone
is not this helper's authentication contract.

```sh
python3 -m netboxlabs.diode.scripts.dryrun_replay \
  --target "$DIODE_TARGET" --app-name devin-generator --app-version 0.9.0 \
  build/bank-v9/diode/phase-001-*.json
```

Replay one manifest phase at a time, checking **successful reconciliation**
before proceeding to the next. The helper reports ingest responses; it does not
wait for all NetBox changes to complete. Primary IP assignments are a final phase
because their interfaces and addresses must already exist. Saved phase files can
be resubmitted by the SDK helper, but target recovery is a separate concern.
The pinned local `lab-load` harness rejects historical failed ingestion logs;
its [recovery procedure](../lab/README.md#recover-a-failed-local-ingestion) uses a
corrected artifact, disposable reset, and new bootstrap receipt before a full load.
Qualify reconciliation and replay on the intended
target; the [local results](../lab/README.md#current-v09-qualification) cover one pinned combination. Replaying a
baseline can reverse demo edits.
The saved request IDs identify reproducible artifacts; the replay helper rebuilds
its own request envelope from the saved entities.

The recipe's `as_of` is a synthetic observation date, preserved in replay. It
must be in the past for SDK timestamp validation. Do not silently replace it
with the current time. Wire format and limits are based on the
[Diode protocol](https://github.com/netboxlabs/diode/blob/diode-reconciler/v2.2.0/diode-proto/diode/v1/ingester.proto);
scoped references follow the plugin's
[matching criteria](https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/docs/matching-criteria-documentation.md).

## Cloud qualification plan

Cloud is the recommended next qualification target. This is a test plan, not a
record of successful Cloud ingestion. The [Cloud setup guide](https://netboxlabs.com/docs/discovery/getting-started/#netbox-cloud-setup)
documents managed Diode, enablement and client credentials. Confirm access on the
chosen disposable tenant and copy its actual Diode target from its settings.
Use Diode credentials for ingestion and a read-only NetBox API token for readback.

1. **Compatibility:** record the target NetBox/Diode/plugin versions and check
   the exported model families and matching behavior. Test passive port mappings
   early: local qualification used an [explicit compatibility patch](../lab/README.md#opt-in-local-front-port-compatibility).
   Cloud support for those relationships must be established on its actual stack.
   Record unsupported features explicitly; direct cabling can qualify that mode,
   but cannot establish support for panel paths.
2. **A complete representative estate:** generate a fresh baseline and ingest
   every dependency phase. Read back emitted attributes and relationships, inspect
   cable paths and service placement, and walk the estate in the Cloud UI.
   Repeat the same artifact and check stable IDs and absence of duplicates.
3. **A complete estate above 50,000 objects:** increase industry demand within
   the reviewed construction limits, retaining its meaningful connected detail.
   Measure submission, reconciliation and readback separately, through completion;
   retain object/wire counts, target versions, timings and any errors.
4. **Operational reliability:** test interruption and recovery using the existing
   replay path, then verify inventory and the cross-site walkthroughs again.
   Treat safe recovery as unproven until that target test passes.

These gates qualify the whole estate as both a coherent model and usable Cloud
inventory. Smaller compatibility probes are preparation for that result.
