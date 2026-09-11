"""Local verification credentials stay read-only, private, and reusable."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from lab.token import _NoRedirect, provision


class LocalTokenTests(unittest.TestCase):
    @patch("lab.token.urllib.request.build_opener")
    def test_provisioning_boundary_and_reuse(self, build_opener):
        opener = Mock()
        build_opener.return_value = opener
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            credentials = target / "credentials.json"
            credentials.write_text(json.dumps({"netbox_url": "https://example.invalid",
                                               "username": "admin", "password": "test-only"}))
            with self.assertRaisesRegex(ValueError, "loopback"):
                provision(target)
            opener.open.assert_not_called()
            credentials.write_text(json.dumps({"netbox_url": "http://127.0.0.1:8000",
                                               "username": "admin", "password": "test-only"}))
            for write_enabled in (True, False):
                opener.open.return_value = io.BytesIO(json.dumps({"version": 2,
                    "write_enabled": write_enabled, "key": "example", "token": "test-token"}).encode())
                if write_enabled:
                    with self.assertRaisesRegex(ValueError, "read-only"):
                        provision(target)
                    self.assertFalse((target / "netbox-token").exists())
                else:
                    path = provision(target)
            self.assertEqual(path.read_text(), "nbt_example.test-token\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            request = opener.open.call_args.args[0]
            self.assertIs(json.loads(request.data)["write_enabled"], False)
            opener.open.reset_mock()
            self.assertEqual(provision(target), path)
            opener.open.assert_not_called()
            self.assertTrue(any(isinstance(handler, _NoRedirect)
                                for handler in build_opener.call_args.args))


if __name__ == "__main__":
    unittest.main()
