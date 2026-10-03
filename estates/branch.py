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

from .showcase import FILTER_LABELS as SHOWCASE_FILTER_LABELS
from .turbobulk import Client, LoadError

BRANCHES = "/api/plugins/branching/branches/"


def _state(row):
    status = row.get("status")
    return status.get("value") if isinstance(status, dict) else status


def create_branch(client, name, *, timeout=300, poll_interval=2, sleep=time.sleep):
    _, page = client.request(
        BRANCHES + "?" + urllib.parse.urlencode({"name": name}), branch=False)
    if any(row.get("name") == name for row in page.get("results", [])):
        raise LoadError(
            f"branch {name!r} already exists. If your own create attempt just lost its "
            "response, inspect it: a ready branch with zero ChangeDiffs is yours to use "
            "directly. Otherwise pick a new name, or use just reset to replace a "
            "disposable branch")
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
        if state == "failed" or time.monotonic() >= deadline:
            _abandon(client, row["id"], name, state, timeout)
        sleep(poll_interval)


def _abandon(client, branch_id, name, state, timeout):
    """Delete the branch this invocation created, so its name is not burned.

    A branch stuck in 'new' usually means the target's RQ worker never ran the
    provisioning job; neither retrying this command nor just reset can clear
    that state, so clean up our own row and point at the real problem.
    """
    hint = ("a branch stuck before 'ready' usually means the target's RQ worker "
            "is dead or backlogged; check /api/core/jobs/ for a pending "
            "'Provision branch' job and restore the worker before retrying")
    problem = (f"branch {name!r} provisioning failed on the target" if state == "failed"
               else f"branch {name!r} is still {state!r} after {timeout}s")
    try:
        client.request(f"{BRANCHES}{branch_id}/", method="DELETE", branch=False)
    except LoadError:
        pass  # a 204 empty body reads as a parse failure; confirm by readback
    try:
        client.request(f"{BRANCHES}{branch_id}/", branch=False)
    except LoadError:
        raise LoadError(f"{problem}; the branch was deleted so the name is free — {hint}") from None
    raise LoadError(f"{problem}; deleting it also failed, so remove it manually with "
                    f"DELETE {BRANCHES}{branch_id}/ before reusing the name — {hint}")


def delete_branch(client, name, *, timeout=300, poll_interval=2, sleep=time.sleep):
    """Permanently delete one named branch — the retirement step of a demo.

    Unlike ``just reset`` this leaves nothing behind: no replacement branch,
    no receipt archive. The branch's ChangeDiffs go with it.
    """
    _, page = client.request(
        BRANCHES + "?" + urllib.parse.urlencode({"name": name}), branch=False)
    rows = [row for row in page.get("results", []) if row.get("name") == name]
    if len(rows) != 1:
        raise LoadError(f"expected exactly one branch named {name!r}; found {len(rows)}")
    row = rows[0]
    try:
        client.request(f"{BRANCHES}{row['id']}/", method="DELETE", branch=False)
    except LoadError:
        pass  # a 204 empty body reads as a parse failure; confirm by readback
    deadline = time.monotonic() + timeout
    while True:
        try:
            client.request(f"{BRANCHES}{row['id']}/", branch=False)
        except LoadError:
            return {"id": row["id"], "name": name, "schema_id": row.get("schema_id"),
                    "deleted": True}
        if time.monotonic() >= deadline:
            raise LoadError(f"branch {name!r} still exists after the delete request; "
                            "inspect the target before retrying")
        sleep(poll_interval)


# Main-scoped rows a namespace leaves behind, in deletion-dependency order:
# the event rule names its webhook, and links and fields reference choice sets;
# every row references the namespace's owner. custom_field names use
# the namespace's underscore form, where prefix matching is UNSAFE (namespace
# "cedar" would prefix-match "cedar_v7_…" belonging to namespace "cedar-v7"),
# so those rows match by exact generated name; the rest are "<namespace> "
# prefixed, which is safe because namespaces cannot contain spaces.
# The automation records are Branching-exempt for the same reason as the
# custom-field trio (estates/turbobulk.py BRANCH_EXEMPT_KINDS); config contexts
# are branch-scoped and go with the branch, so they are not listed here.
RETIREMENT_ENDPOINTS = (
    ("/api/extras/saved-filters/", "exact"),
    ("/api/extras/event-rules/", "exact"),
    ("/api/extras/webhooks/", "exact"),
    ("/api/extras/export-templates/", "exact"),
    ("/api/extras/custom-links/", "prefix"),
    ("/api/extras/custom-fields/", "exact"),
    ("/api/extras/custom-field-choice-sets/", "prefix"),
    ("/api/users/owners/", "prefix"),
    ("/api/users/owner-groups/", "prefix"),
)
# Every label the generator emits per endpoint, without the "<namespace> "
# prefix naming.main_scoped_name adds on a shared tenant; extend alongside
# estates/operations.py and estates/automation.py when a profile adds one
# (tests/test_branch.py proves every generated row resolves here, both modes).
# A shared tenant matches "exact" endpoints by "<namespace> <label>" — a prefix
# match could reach a customer's own live webhook — and "prefix" endpoints by
# the "<namespace> " prefix. A dedicated tenant has no prefix to match, so every
# endpoint matches these bare labels exactly. Custom-field names are an
# identifier that keeps the underscored namespace in both modes.
# The second entry of each pair is the pre-0.16 name, kept so an estate loaded
# before the service-class field replaced the operations tier still retires.
CUSTOM_FIELD_NAMES = ("{ns}_service_class", "{ns}_operations_tier")
RETIREMENT_LABELS = {
    # The showcase sidecar's shared saved filters (estates/showcase.py); main-scoped
    # rows nothing references, so they go first.
    "/api/extras/saved-filters/": SHOWCASE_FILTER_LABELS,
    "/api/extras/event-rules/": ("Device change notification",),
    "/api/extras/webhooks/": ("NetOps automation endpoint",),
    "/api/extras/export-templates/": ("Device inventory (CSV)", "Cable report (CSV)"),
    "/api/extras/custom-links/": ("Site equipment",),
    "/api/extras/custom-field-choice-sets/": ("Service classes", "Operations tiers"),
    "/api/users/owners/": ("Network operations", "Infrastructure operations"),
    "/api/users/owner-groups/": ("Infrastructure teams",),
}
RETIREMENT_PATHS = tuple(endpoint for endpoint, _ in RETIREMENT_ENDPOINTS)


def retirement_matcher(endpoint, namespace, *, dedicated=False):
    """Return ``name -> bool`` selecting this namespace's rows at one endpoint."""
    if endpoint == "/api/extras/custom-fields/":
        ns = namespace.replace("-", "_")
        names = {pattern.format(ns=ns) for pattern in CUSTOM_FIELD_NAMES}
        return names.__contains__
    labels = RETIREMENT_LABELS.get(endpoint, ())
    if dedicated:
        return set(labels).__contains__
    if dict(RETIREMENT_ENDPOINTS).get(endpoint, "prefix") == "exact":
        return {f"{namespace} {label}" for label in labels}.__contains__
    return lambda name: name.startswith(namespace + " ")


def retire_namespace_rows(client, namespace, *, dedicated=False, endpoints=RETIREMENT_PATHS,
                          attempts=8, poll_interval=3, sleep=time.sleep):
    """Delete the namespace's own main-scoped rows.

    These are the Branching-exempt records a branch load writes to main (and
    the showcase sidecar's shared saved filters): the
    automation event rule, webhook and export templates, the custom-link,
    custom-field and choice-set definitions, and the owner/owner_group pair.
    Branch deletion leaves them behind and they block the namespace's next fresh
    load. On a shared tenant names are authored as "<namespace> …", so an exact
    prefix match selects only this estate's rows; ``dedicated`` (the recipe's
    ``tenancy = "dedicated"``) matches the generator's bare labels exactly.
    A just-deleted branch's schema drop can briefly hold PROTECT references to
    these rows, so a still-present row is retried within a bounded window.
    """
    deleted = []
    for endpoint in endpoints:
        selected = retirement_matcher(endpoint, namespace, dedicated=dedicated)
        for row in client.all(endpoint):
            name = row.get("name") or ""
            if not selected(name):
                continue
            for attempt in range(attempts):
                try:
                    client.request(f"{endpoint}{row['id']}/", method="DELETE", branch=False)
                except LoadError:
                    pass  # a 204 empty body reads as a parse failure; confirm below
                try:
                    client.request(f"{endpoint}{row['id']}/", branch=False)
                except LoadError:
                    deleted.append({"endpoint": endpoint, "id": row["id"], "name": name})
                    break
                if attempt + 1 == attempts:
                    raise LoadError(
                        f"row {row['id']} ({name!r}) at {endpoint} survived deletion "
                        f"after {attempts} attempts — a branch schema drop may still "
                        "reference it; wait for the drop to finish and retry")
                sleep(poll_interval)
    return deleted


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create, delete, or retire one named Branching branch")
    parser.add_argument("target", help="NetBox http(s) origin")
    parser.add_argument("name", help="branch name")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--delete", action="store_true",
                        help="permanently delete the named branch instead of creating one")
    parser.add_argument("--retire-namespace",
                        help="after deleting the branch, also delete this namespace's "
                             "main-scoped rows — automation event rule, webhook and export "
                             "templates, custom links/fields/choice sets, owners and owner "
                             "groups (full demo retirement)")
    parser.add_argument("--tenancy", choices=("shared", "dedicated"), default="shared",
                        help="the estate recipe's tenancy: dedicated rows carry no namespace "
                             "prefix and are matched by the generator's exact labels")
    args = parser.parse_args(argv)
    token = os.environ.get("NETBOX_TOKEN")
    if not token:
        parser.error("NETBOX_TOKEN is required")
    if args.retire_namespace and not args.delete:
        parser.error("--retire-namespace requires --delete")
    try:
        if args.delete:
            client = Client(args.target, token)
            try:
                row = delete_branch(client, args.name, timeout=args.timeout)
            except LoadError as exc:
                # Retirement must still clear the namespace's main-scoped rows
                # when the branch was already deleted some other way.
                if not (args.retire_namespace and "found 0" in str(exc)):
                    raise
                row = {"name": args.name, "deleted": False, "branch": "already absent"}
            if args.retire_namespace:
                row["retired_rows"] = retire_namespace_rows(client, args.retire_namespace,
                                                            dedicated=args.tenancy == "dedicated")
                if row.get("branch") == "already absent" and row["retired_rows"]:
                    # The named branch was gone but rows still existed — likely a
                    # second live branch under this namespace, whose strict
                    # readback these deletions have just broken. Say so loudly.
                    print(f"WARNING: branch {args.name!r} was already absent, yet "
                          f"{len(row['retired_rows'])} main-scoped rows for namespace "
                          f"{args.retire_namespace!r} were deleted. If another live branch "
                          "holds this namespace's estate, its readback can no longer verify.",
                          file=os.sys.stderr)
            print(json.dumps(row, sort_keys=True))
            return 0
        row = create_branch(Client(args.target, token), args.name, timeout=args.timeout)
    except LoadError as exc:
        verb = "deletion" if args.delete else "creation"
        print(f"Branch {verb} failed: {exc}", file=os.sys.stderr)
        return 1
    print(json.dumps({"id": row["id"], "name": row["name"],
                      "schema_id": row.get("schema_id"), "status": _state(row)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
