"""Offline boundary checks for the opt-in local plugin patch; no target writes."""

import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lab.front_port_compat import SOURCE_SHA256, apply_patch
from lab.patches.front_port_compat import guard_mapping


class LocalFrontPortBridgeTests(unittest.TestCase):
    def test_existing_mapping_and_position_guards(self):
        rear = SimpleNamespace(pk=12, device_id=7, positions=4)
        args = dict(mappings=[{"front_port_position": 1, "rear_port": rear, "rear_port_position": 2}],
                    device_id=7, positions=1)
        guard_mapping(**args)
        same = [{"position": 1, "rear_port": 12, "rear_port_position": 2}]
        guard_mapping(**args, existing_positions=1, existing=same)
        for override in (
            {"device_id": 8}, {"positions": 2}, {"existing_positions": 2},
            {"existing": [{"position": 1, "rear_port": 13, "rear_port_position": 2}]},
            {"mappings": []},
            {"mappings": [{"front_port_position": 1, "rear_port": rear, "rear_port_position": 5}]},
        ):
            with self.subTest(override=override), self.assertRaises(ValueError):
                guard_mapping(**(args | override))

    def test_guard_checks_all_sources_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "plugin"
            api = root / "api"
            api.mkdir(parents=True)
            helper = Path(temp) / "helper.py"
            helper.write_text("# helper\n")
            originals = {name: b"# unchanged\n" for name in SOURCE_SHA256}
            for name, content in originals.items():
                (api / name).write_bytes(content)
            hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in originals.items()}
            hashes["plugin_utils.py"] = "0" * 64
            replacements = {name: [("# unchanged", "# updated")] for name in hashes}
            with patch("lab.front_port_compat.SOURCE_SHA256", hashes), patch("lab.front_port_compat.REPLACEMENTS", replacements):
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    apply_patch(root, helper)
            self.assertEqual({name: (api / name).read_bytes() for name in originals}, originals)
            self.assertFalse((api / "front_port_compat.py").exists())


if __name__ == "__main__":
    unittest.main()
