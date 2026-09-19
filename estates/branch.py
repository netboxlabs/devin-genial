"""Create one named, ready Branching branch on a target.

Every branch-scoped load requires an existing ready branch; NetBox Branching
creates them asynchronously. This is the missing first step for an operator:
``just branch TARGET NAME`` creates the branch and waits for readiness.
``just reset`` replaces an existing disposable branch; this command only
creates a new one and refuses a name that already exists.
"""

import argparse
import json
import os
import time
import urllib.parse

from .turbobulk import Client, LoadError

BRANCHES = "/api/plugins/branching/branches/"


def _state(row):
    status = row.get("status")
    return status.get("value") if isinstance(status, dict) else status


def create_branch(client, name, *, timeout=300, poll_interval=2, sleep=time.sleep):
    _, page = client.request(
        BRANCHES + "?" + urllib.parse.urlencode({"name": name}), branch=False)
    if any(row.get("name") == name for row in page.get("results", [])):
        raise LoadError(f"branch {name!r} already exists; pick a new name, or use "
                        "just reset to replace a disposable branch")
    body = json.dumps({"name": name}).encode()
    _, row = client.request(BRANCHES, method="POST", body=body,
                            headers={"Content-Type": "application/json"}, branch=False)
    if not isinstance(row, dict) or not row.get("id") or row.get("name") != name:
        raise LoadError("branch create response did not identify the requested branch; "
                        "inspect the target before retrying")
    deadline = time.monotonic() + timeout
    while True:
        _, current = client.request(f"{BRANCHES}{row['id']}/", branch=False)
        state = _state(current)
        if state == "ready":
            return current
        if state == "failed":
            raise LoadError(f"branch {name!r} provisioning failed on the target")
        if time.monotonic() >= deadline:
            raise LoadError(f"branch {name!r} is still {state!r} after {timeout}s; "
                            "inspect the target before retrying")
        sleep(poll_interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create one named ready Branching branch")
    parser.add_argument("target", help="NetBox http(s) origin")
    parser.add_argument("name", help="new branch name")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    token = os.environ.get("NETBOX_TOKEN")
    if not token:
        parser.error("NETBOX_TOKEN is required")
    try:
        row = create_branch(Client(args.target, token), args.name, timeout=args.timeout)
    except LoadError as exc:
        print(f"Branch creation failed: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps({"id": row["id"], "name": row["name"],
                      "schema_id": row.get("schema_id"), "status": _state(row)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
