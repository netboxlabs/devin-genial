The front-port helper is an **opt-in local qualification patch**, limited to
NetBox 4.7.0 with Diode plugin 1.17.0. It is not an official compatibility claim.

The pinned SDK has `FrontPort.rear_port` and `rear_port_position`, but no physical
`PortMapping` entity or `rear_ports` field. The helper translates the legacy
one-position mapping inside Diode's normal transformer. Normal nested reference
resolution supplies rear-port IDs; NetBox's native FrontPort serializer creates
the actual mapping rows. Diode snapshots those mappings for comparison on replay.

The helper accepts exactly one front position and one mapping. It checks port
ownership and rear position bounds, and refuses changing or clearing existing
mappings. Ordinary updates that omit mappings retain native behavior. It does not
implement many-to-many patching, a REST data loader, or a replacement reconciler.

`lab/front_port_compat.py` guards the exact upstream source and helper SHA256
values before changing anything. Run it only while building the disposable
target image, after installing the pinned plugin. `lab.setup` owns packaging;
do not patch a running container. A changed or already-patched source is refused.

Offline checks: `python3 -m unittest tests.test_front_port_compat`. These checks
cover input and preservation boundaries; they do not prove native serializer,
Diode ingestion, mapping readback, or cable-trace behavior. Those require the
separate live qualification receipts recorded by the local lab workflow.

The explicit native test module is `lab/front_port_compat_native_test.py`. Copy
it into an importable temporary location in the patched app container and run
NetBox's Django test runner for `front_port_compat_native_test`, with that location
on `PYTHONPATH`. Use the test runner, not `manage.py shell`: it creates and destroys
an isolated `test_netbox` database. Its two tests passed locally on September 9,
2026, checking actual plugin apply/diff, native serializer readback, unchanged
mapping row IDs, complete cable traces, and the preservation guards. The local
evidence is recorded in `build/local-front-port-native-qualification.json`.
This native test gate is separate from full-estate SDK replay and REST readback.

Primary sources:

- [Diode v1.17.0 transformation](https://github.com/netboxlabs/diode-netbox-plugin/blob/v1.17.0/netbox_diode_plugin/api/transformer.py)
- [NetBox 4.7.0 front-port serializer](https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/api/serializers_/device_components.py)
- [NetBox 4.7.0 native mapping reconciliation](https://github.com/netbox-community/netbox/blob/v4.7.0/netbox/dcim/api/serializers_/base.py)
