"""Provision a read-only token for the disposable local NetBox target."""

import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def provision(target):
    path = target / "netbox-token"
    if path.exists():
        return path
    credentials = json.loads((target / "credentials.json").read_text())
    url = credentials["netbox_url"]
    if url != "http://127.0.0.1:8000":
        raise ValueError("Token provisioning is restricted to this disposable loopback target")
    body = {key: credentials[key] for key in ("username", "password")}
    body.update(version=2, write_enabled=False, description="Generator local readback")
    request = urllib.request.Request(url + "/api/users/tokens/provision/",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        token = json.load(response)
    if (token.get("version") != 2 or token.get("write_enabled") is not False
            or not token.get("token") or not token.get("key")):
        raise ValueError("NetBox did not return the requested read-only v2 token")
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
        stream.write(f"nbt_{token['key']}.{token['token']}\n")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-dir", type=Path, default=Path("build/local-target"))
    args = parser.parse_args()
    try:
        path = provision(args.target_dir)
    except urllib.error.HTTPError as exc:
        parser.exit(1, f"Token provisioning returned HTTP {exc.code}; no credential values printed.\n")
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"Token provisioning failed: {type(exc).__name__}\n")
    print(f"Read-only token available in private file {path}")


if __name__ == "__main__":
    main()
