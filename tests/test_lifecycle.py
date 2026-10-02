"""The asset-lifecycle sidecar must be derived, deterministic and honestly seeded."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from estates.lifecycle import LifecycleError, LoadError, build, check, create, seed, verify
from estates.model import canonical


def _plan(prefix="acme", sites=("one",)):
    """A tiny estate: per site a two-cabinet room, a switch, a PE router, a workstation."""
    objects = [
        {"kind": "manufacturer", "key": "manufacturer/m", "attrs": {"name": f"{prefix} mfr", "slug": f"{prefix}-mfr"}, "refs": {}, "meta": {}},
        {"kind": "device_type", "key": "dt/sw", "attrs": {"model": f"{prefix} sw"}, "refs": {"manufacturer": "manufacturer/m"}, "meta": {}},
        {"kind": "device_type", "key": "dt/rtr", "attrs": {"model": f"{prefix} rtr"}, "refs": {"manufacturer": "manufacturer/m"}, "meta": {}},
        {"kind": "module_type", "key": "mt/psu", "attrs": {"model": f"{prefix} psu"}, "refs": {"manufacturer": "manufacturer/m"}, "meta": {}},
        {"kind": "device_role", "key": "role/access", "attrs": {"slug": f"{prefix}-role"}, "refs": {}, "meta": {}},
        {"kind": "device_role", "key": "role/provider-edge", "attrs": {"slug": f"{prefix}-pe"}, "refs": {}, "meta": {}},
        {"kind": "device_role", "key": "role/workstation", "attrs": {"slug": f"{prefix}-endpoint"}, "refs": {}, "meta": {}},
    ]
    for index, name in enumerate(sites):
        site, room = f"site/{name}", f"location/{name}"
        stem = prefix if index == 0 else f"{prefix}-{name}"
        objects += [
            {"kind": "site", "key": site, "attrs": {"name": f"{stem} site", "slug": f"{stem}-site"}, "refs": {}, "meta": {}},
            {"kind": "location", "key": room, "attrs": {"name": f"{stem} room", "slug": f"{stem}-room"}, "refs": {"site": site}, "meta": {}},
        ]
        for rack in ("r1", "r2"):
            objects.append({"kind": "rack", "key": f"rack/{name}/{rack}", "attrs": {"name": rack.upper()},
                            "refs": {"site": site, "location": room}, "meta": {}})
        for number, (dtype, role, rack) in enumerate((("dt/sw", "role/access", "r1"),
                                                      ("dt/rtr", "role/provider-edge", "r2"),
                                                      ("dt/sw", "role/workstation", None))):
            device = f"device/{name}/dev{number}"
            refs = {"site": site, "location": room, "device_type": dtype, "role": role}
            if rack:
                refs["rack"] = f"rack/{name}/{rack}"
            objects.append({"kind": "device", "key": device, "attrs": {"name": f"{stem}-dev{number}"},
                            "refs": refs, "meta": {}})
            if rack:
                objects += [
                    {"kind": "module_bay", "key": f"{device}/bay", "attrs": {"name": "PSU0"}, "refs": {"device": device}, "meta": {}},
                    {"kind": "module", "key": f"{device}/psu", "attrs": {"serial": "X"},
                     "refs": {"device": device, "module_bay": f"{device}/bay", "module_type": "mt/psu"}, "meta": {}},
                ]
        objects.append({"kind": "journal_entry", "key": f"journal/device/{name}/dev0/equipment-record",
                        "attrs": {"comments": "2026-06-20 — Equipment installation record"},
                        "refs": {"assigned_object": f"device/{name}/dev0"}, "meta": {}})
    return {"schema_version": 1, "recipe": {"namespace": prefix}, "objects": objects}


class Story(unittest.TestCase):
    def test_same_plan_builds_byte_identical_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(_plan()) + b"\n")
            build(root / "plan.json", root / "one")
            build(root / "plan.json", root / "two")
            self.assertEqual((root / "one/lifecycle.json").read_bytes(),
                             (root / "two/lifecycle.json").read_bytes())
            self.assertEqual(check(root / "one", root / "plan.json")["assets"], 4)

    def test_story_is_derived_from_the_graph(self):
        artifact = create(_plan())
        bom = artifact["boms"][0]
        # Racked devices and their modules only; the workstation stays out.
        self.assertEqual(sorted(a["device"] for a in bom["assets"]),
                         ["acme-dev0", "acme-dev0", "acme-dev1", "acme-dev1"])
        self.assertEqual(bom["rules"][0]["parameters"],
                         {"site": ["acme-site"], "role": ["acme-pe", "acme-role"]})
        # The PE router goes to the carrier distributor; the PSU it shares
        # with the switch stays with the regional reseller.
        orders = {order["vendor"]: sorted(item[3] for item, _ in order["items"]) for order in bom["orders"]}
        self.assertEqual(orders, {"carrier": ["acme rtr"], "regional": ["acme psu", "acme sw"]})
        shipment = bom["orders"][0]["shipment"]
        self.assertTrue(shipment["date_shipped"] < shipment["date_received"] < "2026-06-20")
        self.assertEqual(artifact["courier"]["tracking_url"], "")
        self.assertEqual(len(artifact["pools"]), 1)

    def test_appending_a_site_never_moves_an_existing_story(self):
        before = create(_plan(sites=("one",)))["boms"][0]
        grown = create(_plan(sites=("one", "two")))["boms"]
        self.assertEqual(next(b for b in grown if b["site_slug"] == before["site_slug"]), before)

    def test_a_site_the_scope_rules_cannot_express_is_refused(self):
        plan = _plan()
        workstation = next(o for o in plan["objects"] if o["key"] == "device/one/dev2")
        workstation["refs"]["role"] = "role/access"
        with self.assertRaisesRegex(LifecycleError, "scope rules would also select"):
            create(plan)

    def test_verify_rejects_mutations_and_a_foreign_plan(self):
        plan = _plan()
        artifact = create(plan)
        for mutate in (lambda a: a["boms"][0]["lines"][0].__setitem__(1, 9),
                       lambda a: a["boms"][0]["assets"].pop(),
                       lambda a: a["courier"].__setitem__("tracking_url", "https://example.com/?t="),
                       lambda a: a["pools"][0]["allocations"][0].update(below_minimum=not a["pools"][0]["allocations"][0]["below_minimum"])):
            mutated = copy.deepcopy(artifact)
            mutate(mutated)
            with self.assertRaises(LifecycleError):
                verify(mutated, plan)
        with self.assertRaisesRegex(LifecycleError, "not bound"):
            verify(artifact, _plan(sites=("one", "two")))

    def test_check_rejects_tampered_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(_plan()) + b"\n")
            build(root / "plan.json", root / "out")
            artifact = json.loads((root / "out/lifecycle.json").read_text())
            artifact["boms"][0]["orders"][0]["shipment"]["date_received"] = "2026-01-01"
            (root / "out/lifecycle.json").write_text(json.dumps(artifact))
            with self.assertRaises(LifecycleError):
                check(root / "out", root / "plan.json")

    def test_write_gate_refuses_before_any_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(_plan()) + b"\n")
            build(root / "plan.json", root / "out")
            with patch("estates.lifecycle.Client") as client, \
                    patch.dict("os.environ", {"LIFECYCLE_WRITES": ""}):
                with self.assertRaisesRegex(LoadError, "LIFECYCLE_WRITES"):
                    seed(root / "out", url="http://t.example", token="x",
                         receipt_path=root / "receipt.json")
            client.assert_not_called()


if __name__ == "__main__":
    unittest.main()
