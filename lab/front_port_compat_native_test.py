"""Run explicitly with NetBox's Django test runner, against its isolated test DB.

This is deliberately outside ordinary stdlib test discovery. Copy this file to
an importable temporary module in the patched image and run `manage.py test`
for that module. It creates test fixtures, never uses the saved demo inventory.
The fixture/apply pattern follows plugin v1.17.0's test_applier_cable.py.
"""

import copy

from dcim.models import Device, DeviceRole, DeviceType, FrontPort, Interface, Manufacturer, PortMapping, RearPort, Site
from django.db import transaction
from django.test import TestCase

from netbox_diode_plugin.api.applier import apply_changeset
from netbox_diode_plugin.api.common import ChangeSetException
from netbox_diode_plugin.api.differ import generate_changeset
from netbox_diode_plugin.api.supported_models import get_serializer_for_model


class NativeFrontPortBridgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        manufacturer = Manufacturer.objects.create(name="Local bridge test", slug="local-bridge-test")
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model="Local bridge test", slug="local-bridge-test")
        role = DeviceRole.objects.create(name="Local bridge test", slug="local-bridge-test")
        cls.site = Site.objects.create(name="Local bridge test", slug="local-bridge-test")
        cls.devices = [Device.objects.create(name=name, device_type=device_type, role=role, site=cls.site)
                       for name in ("bridge-panel", "bridge-left", "bridge-right")]
        cls.interfaces = [Interface.objects.create(device=device, name="eth0", type="1000base-t")
                          for device in cls.devices[1:]]

    def ref(self, device):
        return {"name": device.name, "site": {"name": self.site.name}}

    def payload(self, rear="R1"):
        return {"name": "F1", "type": "8p8c", "positions": 1, "device": self.ref(self.devices[0]),
                "rear_port": {"name": rear, "type": "8p8c", "positions": 1, "device": self.ref(self.devices[0])},
                "rear_port_position": 1}

    def apply(self, payload, kind):
        result = generate_changeset(copy.deepcopy(payload), kind)
        self.assertFalse(result.change_set.warnings)
        if result.change_set.changes:
            apply_changeset(result.change_set, request=None)
        return result

    def test_create_references_replay_readback_and_complete_trace(self):
        result = self.apply(self.payload(), "dcim.frontport")
        front_change = next(change for change in result.change_set.changes if change.object_type == "dcim.frontport")
        self.assertIn("rear_ports.0.rear_port", front_change.new_refs)
        front = FrontPort.objects.get(device=self.devices[0], name="F1")
        rear = RearPort.objects.get(device=self.devices[0], name="R1")
        mapping = PortMapping.objects.get(front_port=front)
        self.assertEqual((mapping.front_port_position, mapping.rear_port_id, mapping.rear_port_position), (1, rear.pk, 1))
        serializer = get_serializer_for_model(FrontPort)(front, context={"request": None})
        self.assertEqual(serializer.data["rear_ports"], [{"position": 1, "rear_port": rear.pk, "rear_port_position": 1}])
        self.assertEqual(self.apply(self.payload(), "dcim.frontport").change_set.changes, [])
        self.assertEqual(PortMapping.objects.get(front_port=front).pk, mapping.pk)

        for index, (interface, kind, port) in enumerate(zip(self.interfaces, ("front_port", "rear_port"), (front, rear))):
            self.apply({"label": f"local-bridge-{index}", "status": "connected", "type": "cat6a",
                        "a_terminations": [{"object_interface": {"name": interface.name, "type": interface.type,
                                                                  "device": self.ref(interface.device)}}],
                        "b_terminations": [{f"object_{kind}": {"name": port.name, "type": port.type,
                                                                "device": self.ref(port.device)}}]}, "dcim.cable")
        origin = Interface.objects.get(pk=self.interfaces[0].pk)
        self.assertTrue(origin.path.is_complete)
        self.assertEqual([obj.pk for obj in origin.path.destinations], [self.interfaces[1].pk])
        traced = [obj for segment in origin.trace() for group in segment for obj in group]
        self.assertIn(front, traced)
        self.assertIn(rear, traced)

    def test_native_validation_preserves_existing_mapping(self):
        self.apply(self.payload(), "dcim.frontport")
        front = FrontPort.objects.get(device=self.devices[0], name="F1")
        mapping_id = PortMapping.objects.get(front_port=front).pk
        with self.assertRaises(ChangeSetException), transaction.atomic():
            self.apply(self.payload("replacement-rear"), "dcim.frontport")
        self.assertEqual(PortMapping.objects.get(front_port=front).pk, mapping_id)
        serializer_class = get_serializer_for_model(FrontPort)
        foreign = RearPort.objects.create(device=self.devices[1], name="foreign", type="8p8c", positions=1)
        rear = RearPort.objects.get(device=self.devices[0], name="R1")
        for rows in ([], [{"position": 1, "rear_port": foreign.pk, "rear_port_position": 1}],
                     [{"position": 1, "rear_port": rear.pk, "rear_port_position": 2}]):
            serializer = serializer_class(front, data={"rear_ports": rows}, partial=True, context={"request": None})
            self.assertFalse(serializer.is_valid(), serializer.errors)
        serializer = serializer_class(front, data={"description": "Mapping retained"}, partial=True, context={"request": None})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        self.assertEqual(PortMapping.objects.get(front_port=front).pk, mapping_id)
