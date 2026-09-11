"""Offline checks for the preparation boundary; no Docker or network calls."""

import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from lab.setup import IMAGES, ROOT, _front_port_patch, env_text, patched_bootstrap, prepare


BOOTSTRAP = '''#!/bin/sh
    client_output=$(hydra create oauth2-client --endpoint $HYDRA_ADMIN_URL \\
      --secret $client_secret \\
      --format json)
'''


def fake_source(url, timeout):
    if url.endswith("bootstrap-clients.sh"):
        text = BOOTSTRAP
    elif url.endswith("sample.env"):
        text = "POSTGRES_PASSWORD=<PLACEHOLDER_SECRET>\nDIODE_TO_NETBOX_CLIENT_SECRET=<PLACEHOLDER_DIODE_TO_NETBOX_CLIENT_SECRET>\n"
    else:
        text = "# upstream test source\n"
    return io.BytesIO(text.encode())


class SetupTests(unittest.TestCase):
    @patch("lab.setup.urllib.request.urlopen", side_effect=fake_source)
    def test_private_output_and_safe_commands(self, fetch):
        with tempfile.TemporaryDirectory() as temp:
            target = prepare(Path(temp) / "target")
            private = [target / "credentials.json", target / "sdk.env", target / "diode/.env",
                       target / "diode/oauth2/client/client-credentials.json", target / "netbox/.env",
                       *list((target / "netbox/env").glob("*.env"))]
            self.assertEqual(target.stat().st_mode & 0o777, 0o700)
            for path in private:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600, path.name)
                self.assertNotIn("<PLACEHOLDER", path.read_text())
            clients = json.loads(private[3].read_text())
            self.assertEqual(len({c["client_secret"] for c in clients}), 3)
            self.assertTrue(all(c["token_endpoint_auth_method"] == "client_secret_post" for c in clients))
            bootstrap = (target / "diode/oauth2/bootstrap-clients.sh").read_text()
            self.assertIn("--file -", bootstrap)
            self.assertNotIn("--secret", bootstrap)
            for stack in ("netbox", "diode"):
                compose = json.loads((target / stack / "compose.local.json").read_text())
                for name, service in compose["services"].items():
                    if name in ("postgres", "redis", "redis-cache"):
                        self.assertNotIn("networks", service)
                    if name.startswith("redis"):
                        self.assertIn("$$REDIS_PASSWORD", " ".join(service["command"]))
                        self.assertNotIn("--requirepass", " ".join(service["command"]))
            self.assertIn(IMAGES["netbox"], (ROOT / "lab/Dockerfile").read_text())
            before = private[0].read_bytes()
            with self.assertRaisesRegex(ValueError, "already exists"):
                prepare(target)
            self.assertEqual(private[0].read_bytes(), before)
            self.assertEqual(json.loads((target / "target.json").read_text())["diode_target"], "grpc://127.0.0.1:18080/diode")
            self.assertNotIn("local_compatibility", json.loads((target / "target.json").read_text()))

    @patch("lab.setup.urllib.request.urlopen", side_effect=fake_source)
    def test_opt_in_stages_build_without_rotating_existing_credentials(self, fetch):
        record = dict(patch_id="test-front-ports", sha256="a" * 64,
                      applier_sha256="b" * 64, helper_sha256="c" * 64,
                      plugin_version="1.17.0", source_sha256={"api/transformer.py": "d" * 64})
        with tempfile.TemporaryDirectory() as temp:
            target = prepare(Path(temp) / "target")
            private = {path: (path.read_bytes(), path.stat().st_mode & 0o777)
                       for path in target.rglob("*") if path.is_file() and path.stat().st_mode & 0o777 == 0o600}
            with patch("lab.setup._front_port_patch", return_value=(record, "# applier\n", "# helper\n")):
                prepare(target, front_port_compat=True)
                after = {path: path.read_bytes() for path in target.rglob("*") if path.is_file()}
                prepare(target, front_port_compat=True)
            self.assertEqual(after, {path: path.read_bytes() for path in target.rglob("*") if path.is_file()})
            for path, value in private.items():
                self.assertEqual((path.read_bytes(), path.stat().st_mode & 0o777), value)
            config = json.loads((target / "target.json").read_text())
            self.assertEqual(config["local_compatibility"]["front_port_mapping"], record)
            compose = json.loads((target / "netbox/compose.local.json").read_text())
            for service in ("netbox", "netbox-worker"):
                self.assertTrue(compose["services"][service]["image"].endswith("-front-ports-" + "a" * 12))
            dockerfile = (target / "netbox/Dockerfile").read_text()
            self.assertIn("--plugin-root", dockerfile)
            self.assertIn("--helper /build/front_port_compat_helper.py", dockerfile)
            self.assertIn('front-port-compat="test-front-ports"', dockerfile)
            self.assertEqual((target / "netbox/front_port_compat_helper.py").stat().st_mode & 0o777, 0o644)
            # Refuse a locally edited Dockerfile before modifying anything else.
            with (target / "netbox/Dockerfile").open("a") as output:
                output.write("# unreviewed local edit\n")
            before_rejection = {path: path.read_bytes() for path in target.rglob("*") if path.is_file()}
            with patch("lab.setup._front_port_patch", return_value=(record, "# applier\n", "# helper\n")):
                with self.assertRaisesRegex(ValueError, "differs"):
                    prepare(target, front_port_compat=True)
            self.assertEqual(before_rejection, {path: path.read_bytes() for path in target.rglob("*") if path.is_file()})

    def test_compatibility_helper_is_hash_guarded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "lab/patches").mkdir(parents=True)
            helper = root / "lab/patches/front_port_compat.py"
            helper.write_text("# checked helper\n")
            helper_hash = hashlib.sha256(helper.read_bytes()).hexdigest()
            (root / "lab/front_port_compat.py").write_text(
                f"PATCH_ID = 'test-front-ports'\nPLUGIN_VERSION = '1.17.0'\nNETBOX_VERSION = '4.7.0'\n"
                f"HELPER_SHA256 = '{helper_hash}'\nSOURCE_SHA256 = {{'api/transformer.py': 'pinned-upstream-hash'}}\n"
                "OUTPUT_SHA256 = {'api/transformer.py': 'pinned-patched-hash'}\n"
                "SOURCE_URL = 'https://example.invalid/pinned-source'\n")
            with patch("lab.setup.ROOT", root):
                record, _, _ = _front_port_patch()
                self.assertEqual(record["helper_sha256"], helper_hash)
                self.assertEqual(record["output_sha256"], {"api/transformer.py": "pinned-patched-hash"})
                helper.write_text("# unexpected helper change\n")
                with self.assertRaisesRegex(ValueError, "pinned patch hash"):
                    _front_port_patch()

    def test_reject_changed_bootstrap_and_multiline_environment(self):
        with self.assertRaisesRegex(ValueError, "shape changed"):
            patched_bootstrap("unrecognized upstream script")
        with self.assertRaises(ValueError):
            env_text({"VALUE": "unexpected\nNEW_VARIABLE=value"})

    def test_bootstrap_preserves_named_client_without_secret_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            hydra = Path(temp) / "hydra"
            hydra.write_text(f"#!{sys.executable}\n" + '''import json, sys
client = json.load(sys.stdin)
args = sys.argv[1:]
# Hydra 26.2 applies --id after reading --file, even when JSON has client_id.
effective_id = args[args.index("--id") + 1] if "--id" in args else ""
assert effective_id == client["client_id"]
assert args[args.index("--file") + 1] == "-"
assert client["client_secret"] not in args
''')
            hydra.chmod(0o700)
            environment = dict(os.environ, PATH=temp + os.pathsep + os.environ.get("PATH", ""),
                               client_id="diode-ingest", HYDRA_ADMIN_URL="http://hydra:4445",
                               client=json.dumps({"client_id": "diode-ingest", "client_secret": "test-only-secret"}))
            completed = subprocess.run(["sh"], input=patched_bootstrap(BOOTSTRAP),
                                       text=True, env=environment, capture_output=True)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
