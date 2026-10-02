"""Offline checks for the discovery-lab renderer: python3 -m unittest lab.discovery.test_render"""

import ipaddress
import json
from pathlib import Path
import tempfile
import tomllib
import unittest

from estates.generate import generate
from lab.discovery import render

ROOT = Path(__file__).parents[2]


class DiscoveryLabRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.plan = Path(cls.tmp.name) / "plan.json"
        cls.plan.write_text(json.dumps(generate(tomllib.loads((ROOT / "profiles/provider-backbone.toml").read_text()))))
        cls.out = Path(cls.tmp.name) / "lab"
        render.render(cls.plan, cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_rendering_is_byte_deterministic(self):
        again = Path(self.tmp.name) / "again"
        render.render(self.plan, again)
        for path in sorted(self.out.rglob("*")):
            if path.is_file() and path.name != "manifest.json":  # manifest names the output's own plan path
                self.assertEqual(path.read_bytes(), (again / path.relative_to(self.out)).read_bytes(), path.name)

    def test_lab_addresses_never_touch_the_estate(self):
        plan = json.loads(self.plan.read_text())
        estate = [ipaddress.ip_network(o["attrs"]["prefix"]) for o in plan["objects"] if o["kind"] == "prefix"]
        lab = json.loads((self.out / "lab-slice.json").read_text())
        for obj in lab:
            if obj["kind"] == "ip_address":
                address = ipaddress.ip_interface(obj["attrs"]["address"])
                self.assertIn(address, render.LAB_POOL)
                self.assertFalse(any(address.network.overlaps(n) for n in estate if n.version == 4))

    def test_every_lab_device_is_the_hardware_discovery_reports(self):
        lab = {o["key"]: o for o in json.loads((self.out / "lab-slice.json").read_text())}
        devices = [o for o in lab.values() if o["kind"] == "device"]
        self.assertEqual(len(devices), 3)
        for device in devices:
            self.assertEqual(lab[device["refs"]["device_type"]]["attrs"]["model"], render.MODEL)
            self.assertEqual(lab[device["refs"]["platform"]]["attrs"]["name"], render.PLATFORM)
            ports = [o for o in lab.values() if o["kind"] == "interface" and o["refs"]["device"] == device["key"]
                     and o["attrs"]["name"].count(".") == 0 and o["attrs"]["name"].startswith("ethernet-")]
            self.assertEqual(len(ports), 58)

    def test_check_flags_a_fact_the_slice_does_not_document(self):
        # A discovered model the slice does not carry must surface as a deviation.
        slice_ = json.loads((self.out / "lab-slice.json").read_text())
        device = next(o for o in slice_ if o["kind"] == "device")
        dry = Path(self.tmp.name) / "dry"
        dry.mkdir()
        entity = {"device": {"name": device["attrs"]["name"],
                             "device_type": {"model": "MX204", "manufacturer": {"name": "Juniper"}}}}
        (dry / "one.json").write_text(json.dumps({"entities": [entity]}))
        rows = render.predicted(self.out, dry)
        self.assertIn(("update", "device", device["attrs"]["name"], "model", render.MODEL, "MX204"), rows)
        self.assertEqual(len(render.expected(self.out)), 6)


if __name__ == "__main__":
    unittest.main()
