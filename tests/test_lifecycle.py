"""The asset-lifecycle sidecar must be derived, deterministic and honestly seeded."""

import copy
from collections import Counter
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
                         {"site": ["acme-site"], "role": ["acme-pe", "acme-role"], "status": ["active"]})
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


class FieldDepot(unittest.TestCase):
    """Design K10: boxed spares in one NOC Field depot; the racked cold spare is a device only."""

    @classmethod
    def setUpClass(cls):
        import tomllib
        from estates.generate import generate
        root = Path(__file__).parents[1]
        cls.plan = generate(tomllib.loads((root / "profiles/showcase-provider.toml").read_text()))
        cls.objects = {o["key"]: o for o in cls.plan["objects"]}
        cls.artifact = create(cls.plan)
        cls.depots = [p for p in cls.artifact["pools"] if p["name"].endswith("Field depot")]

    def _type(self, obj):
        dtype = self.objects[obj["refs"]["device_type" if obj["kind"] == "device" else "module_type"]]
        maker = self.objects[dtype["refs"]["manufacturer"]]["attrs"]["slug"]
        return ("dcim.devicetype" if obj["kind"] == "device" else "dcim.moduletype", maker, dtype["attrs"]["model"])

    def test_device_rules_select_exactly_the_bom_devices(self):
        # The target applies a rule's parameters as plain filters: a rule with
        # no status selected the racked cold spare live (crsk8600, 2026-10-02).
        devices = [o for o in self.plan["objects"] if o["kind"] == "device"]
        slug = lambda key: self.objects[key]["attrs"]["slug"]
        for bom in self.artifact["boms"]:
            for rule in bom["rules"]:
                if rule["object_types"] != ["dcim.device"] or rule.get("action") == "exclude":
                    continue
                params = rule["parameters"]
                self.assertIn("status", params, bom["name"])
                chosen = {d["attrs"]["name"] for d in devices
                          if slug(d["refs"]["site"]) in params["site"] and slug(d["refs"]["role"]) in params["role"]
                          and d["attrs"].get("status", "active") in params["status"]}
                wanted = {a["device"] for a in bom["assets"] if a["object_type"] == "dcim.device"}
                self.assertEqual(chosen, wanted, bom["name"])

    def test_one_depot_at_the_noc(self):
        self.assertEqual(len(self.depots), 1)
        depot = self.depots[0]
        noc = [o for o in self.plan["objects"] if o["kind"] == "site"
               and o["refs"].get("group", "").endswith("/dc")]
        self.assertEqual([depot["site_slug"]], [s["attrs"]["slug"] for s in noc])
        self.assertEqual(depot["name"], f"{noc[0]['attrs']['name']} — Field depot")

    def test_no_spare_shares_type_and_serial_with_a_plan_device(self):
        planned = {(*self._type(o), o["attrs"]["serial"]) for o in self.plan["objects"]
                   if o["kind"] in ("device", "module") and o["attrs"].get("serial")}
        spares = [(s["item"][0], s["item"][2], s["item"][3], s["serial"])
                  for pool in self.artifact["pools"] for s in pool["spares"]]
        self.assertTrue(spares)
        self.assertFalse(set(spares) & planned)
        self.assertEqual(len(set(spares)), len(spares))
        # The racked inventory chassis is neither a spare item nor a BOM asset.
        cold = [o for o in self.plan["objects"] if o["kind"] == "device" and o["attrs"].get("status") == "inventory"]
        self.assertTrue(cold)
        assets = {a["device"] for bom in self.artifact["boms"] for a in bom["assets"]}
        self.assertFalse({o["attrs"]["name"] for o in cold} & assets)
        self.assertFalse({self._type(o)[2] for o in cold} & {s[2] for s in spares})

    def test_stock_is_sized_from_the_installed_base_and_skips_a_superseded_model(self):
        depot = self.depots[0]
        stocked = Counter(s["item"][3] for s in depot["spares"])
        self.assertNotIn("MetroNID TE", stocked)
        for allocation in depot["allocations"]:
            self.assertEqual(stocked[allocation["item"][3]], -(-allocation["installed"] // 20))
        stock = next(b for b in self.artifact["boms"] if b.get("stock") == depot["name"])
        self.assertEqual((stock["rules"], stock["assets"]), ([], []))
        self.assertTrue(all(not o["installs"] and o["shipment"]["date_received"] <= self.plan["recipe"]["as_of"]
                            for o in stock["orders"]))

    def test_a_spare_reusing_a_device_serial_is_refused(self):
        mutated = copy.deepcopy(self.artifact)
        spare = next(s for s in mutated["pools"][-1]["spares"] if s["item"][0] == "dcim.devicetype")
        spare["serial"] = next(o["attrs"]["serial"] for o in self.plan["objects"] if o["kind"] == "device"
                               and o["attrs"].get("serial") and self._type(o)[2] == spare["item"][3])
        with self.assertRaises(LifecycleError):
            verify(mutated, self.plan)


if __name__ == "__main__":
    unittest.main()


class UnseedOrderTest(unittest.TestCase):
    """Pool contents and pools go before BOMs; vendors and courier last; nothing unrecorded."""

    def test_deletes_recorded_rows_in_protection_order(self):
        import json, os, tempfile
        from unittest import mock
        from estates import lifecycle
        receipt = {"target": "https://t.example", "courier": 9,
                   "pools": {"p": {"pool": 5}}, "vendors": {"r": 7}, "vendor_accounts": {"r": 8},
                   "boms": {"b": {"bom": 4, "purchase_orders": {"r": 6}, "shipments": {"r": 3}}}}
        calls = []
        client = mock.Mock(base="https://t.example")
        client.all.side_effect = lambda path: [{"id": 1}] if "pool_id=5" in path else []
        client.request.return_value = (200, {"count": 0})
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(receipt, handle)
        with mock.patch.object(lifecycle, "Client", return_value=client), \
             mock.patch.object(lifecycle, "_delete", side_effect=lambda c, p: calls.append(p) or True), \
             mock.patch.dict(os.environ, {"LIFECYCLE_WRITES": "1"}):
            lifecycle.unseed(handle.name, url="https://t.example", token="x")
        self.assertEqual(calls, ["spare-item-allocations/1/", "spare-items/1/", "spares-pools/5/",
                                 "shipments/3/", "purchase-orders/6/", "boms/4/",
                                 "vendor-accounts/8/", "vendors/7/", "couriers/9/"])
        with mock.patch.dict(os.environ, {"LIFECYCLE_WRITES": ""}):
            with self.assertRaises(lifecycle.LoadError):
                lifecycle.unseed(handle.name, url="https://t.example", token="x")
