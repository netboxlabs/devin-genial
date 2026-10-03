"""v0.18 lived-in procurement: eras, equipment on order, cold spares, and an honest seed."""

import copy
import json
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from estates import lifecycle
from estates.lifecycle import LifecycleError, build, create, seed, verify
from estates.model import canonical
from tests.test_lifecycle import _plan


def _lived_in(plan):
    """Add equipment on order, a decommissioning relic, a cold spare and a later-era switch."""
    objects = plan["objects"]
    for name, status, events in (("dev3", "staged", {"ordered": "2026-07-08", "received": "2026-09-15"}),
                                 ("dev4", "planned", {"ordered": "2026-08-01"}),
                                 ("dev5", "inventory", {}),
                                 ("dev6", "decommissioning", {"installed": "2011-05-02"})):
        role = "role/access" if status == "inventory" else "role/provider-edge"
        attrs = {"name": f"acme-{name}", "status": status}
        if status != "planned":
            attrs["serial"] = f"SN-{name}"
        objects.append({"kind": "device", "key": f"device/one/{name}", "attrs": attrs, "meta": {},
                        "refs": {"site": "site/one", "location": "location/one", "rack": "rack/one/r2",
                                 "device_type": "dt/rtr" if role.endswith("edge") else "dt/sw", "role": role}})
        if name in ("dev3", "dev5"):
            device = f"device/one/{name}"
            objects += [
                {"kind": "module_bay", "key": f"{device}/bay", "attrs": {"name": "PSU0"},
                 "refs": {"device": device}, "meta": {}},
                {"kind": "module", "key": f"{device}/psu", "attrs": {"serial": "X"},
                 "refs": {"device": device, "module_bay": f"{device}/bay", "module_type": "mt/psu"}, "meta": {}}]
        for event, day in events.items():
            objects.append({"kind": "journal_entry", "key": f"journal/device/one/{name}/{event}",
                            "attrs": {"comments": f"**{event}** · {day}", "created": f"{day}T15:00:00Z"},
                            "refs": {"assigned_object": f"device/one/{name}"}, "meta": {}})
    objects.append({"kind": "journal_entry", "key": "journal/device/one/dev0/replaced",
                    "attrs": {"comments": "**Replaced predecessor** · 2016-07-06",
                              "created": "2016-07-06T19:43:00Z"},
                    "refs": {"assigned_object": "device/one/dev0"}, "meta": {}})
    # A carrier: site one is the NOC, and one customer premises holds a NID.
    objects += [
        {"kind": "site_group", "key": "site-group/acme/dc", "attrs": {"slug": "acme-dc"}, "refs": {}, "meta": {}},
        {"kind": "manufacturer", "key": "manufacturer/Ciena", "attrs": {"name": "Ciena", "slug": "ciena"},
         "refs": {}, "meta": {}},
        {"kind": "device_type", "key": "hardware/nid", "attrs": {"model": "3903 AC"},
         "refs": {"manufacturer": "manufacturer/Ciena"}, "meta": {}},
        {"kind": "device_role", "key": "role/nid", "attrs": {"slug": "acme-nid"}, "refs": {}, "meta": {}},
        {"kind": "site", "key": "site/prem", "attrs": {"name": "acme premises", "slug": "acme-prem"},
         "refs": {}, "meta": {}},
        {"kind": "device", "key": "device/prem/nid", "attrs": {"name": "acme-nid1", "serial": "M21A000001"},
         "refs": {"site": "site/prem", "device_type": "hardware/nid", "role": "role/nid"}, "meta": {}},
        {"kind": "journal_entry", "key": "journal/device/prem/nid/equipment-record",
         "attrs": {"comments": "Installed", "created": "2026-05-01T15:00:00Z"},
         "refs": {"assigned_object": "device/prem/nid"}, "meta": {}},
    ]
    next(o for o in objects if o["key"] == "site/one")["refs"]["group"] = "site-group/acme/dc"
    return plan


class LivedIn(unittest.TestCase):
    def setUp(self):
        self.plan = _lived_in(_plan())
        self.artifact = create(self.plan)

    def _bom(self, suffix):
        return next(b for b in self.artifact["boms"] if b["name"].endswith(suffix))

    def test_installed_equipment_orders_follow_each_device_era(self):
        bom = self._bom("installed equipment")
        self.assertEqual(bom["rules"][0]["parameters"]["status"], ["active", "decommissioning"])
        self.assertIn("acme-dev6", {a["device"] for a in bom["assets"]})
        years = sorted(o["comments"].split("ordered ")[1][:4] for o in bom["orders"])
        # The relic's own 2011 order, the switch's 2016 replacement, and the
        # remaining first-build equipment dated from the site's 2011 first build.
        self.assertEqual(years[0], "2011")
        self.assertIn("2016", years)
        self.assertTrue(all(o["installs"] and o["target"] == "fulfilled" for o in bom["orders"]))

    def test_equipment_on_order_is_ordered_and_received_not_installed(self):
        bom = self._bom("equipment on order")
        self.assertEqual(bom["target"], "ordered")
        self.assertEqual(sorted({a["device"] for a in bom["assets"]}), ["acme-dev3", "acme-dev4"])
        staged = [o for o in bom["orders"] if o["shipment"]]
        planned = [o for o in bom["orders"] if not o["shipment"]]
        self.assertEqual({o["shipment"]["date_received"] for o in staged}, {"2026-09-15"})
        self.assertEqual(len(planned), 1)
        self.assertIn("ordered 2026-08-01", planned[0]["comments"])
        self.assertTrue(all(not o["installs"] and o["target"] == "ordered" for o in bom["orders"]))

    def test_a_cold_spare_is_a_device_only_never_an_asset_or_spare_item(self):
        # Design K10: the racked inventory chassis stays a NetBox device; the
        # NOC Field depot holds boxed spares that are not devices. Never both.
        self.assertNotIn("SN-dev5", {s["serial"] for p in self.artifact["pools"] for s in p["spares"]})
        self.assertNotIn("acme-dev5", {a["device"] for b in self.artifact["boms"] for a in b["assets"]})
        depot = next(p for p in self.artifact["pools"] if p["name"].endswith("Field depot"))
        self.assertEqual(depot["site_slug"], "acme-site")
        self.assertEqual([s["item"][3] for s in depot["spares"]], ["3903 AC"])
        # Its modules are excluded from the installed BOM by device name.
        installed = self._bom("installed equipment")
        self.assertIn({"object_types": ["dcim.module"], "action": "exclude",
                       "parameters": {"site": ["acme-site"], "device": ["acme-dev3", "acme-dev5"]}},
                      installed["rules"])

    def test_mutations_are_refused(self):
        def repeat_serial(a):
            depot = next(p for p in a["pools"] if p["spares"])
            depot["spares"].append(dict(depot["spares"][0]))

        def install_on_order(a):
            next(o for b in a["boms"] for o in b["orders"] if not o["installs"]).update(installs=True)

        for mutate, pattern in ((repeat_serial, "serial"), (install_on_order, "misstates")):
            mutated = copy.deepcopy(self.artifact)
            mutate(mutated)
            with self.assertRaisesRegex(LifecycleError, pattern):
                verify(mutated, self.plan)
        plan = copy.deepcopy(self.plan)
        next(o for o in plan["objects"] if o["key"] == "journal/device/one/dev3/received")["key"] += "-x"
        with self.assertRaisesRegex(LifecycleError, "staged device needs a received"):
            create(plan)

    def test_seed_writes_and_reads_back_the_lived_in_story(self):
        fake = _FakePlugin(self.plan)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(self.plan) + b"\n")
            build(root / "plan.json", root / "out")
            with patch.object(lifecycle, "Client", return_value=fake), \
                    patch.dict("os.environ", {"LIFECYCLE_WRITES": "1"}):
                result = seed(root / "out", url="http://t.example", token="x",
                              receipt_path=root / "r.json")
        self.assertTrue(result["success"])
        statuses = {r["name"]: r["status"] for r in fake.rows["boms/"]}
        self.assertEqual(statuses, {"acme site — installed equipment": "fulfilled",
                                    "acme site — equipment on order": "ordered",
                                    "acme site — Field depot stock": "fulfilled"})
        on_order = {r["id"] for r in fake.rows["boms/"] if r["status"] == "ordered"}
        self.assertTrue(all(not a["installed"] for a in fake.rows["assets/"] if a["bom"] in on_order))
        self.assertTrue(all(a["installed"] for a in fake.rows["assets/"] if a["bom"] not in on_order))
        # The depot stock order: manual lines, no scope rules, no assets, received.
        stock = next(r["id"] for r in fake.rows["boms/"] if r["name"].endswith("Field depot stock"))
        self.assertFalse([r for r in fake.rows["bom-scope-rules/"] if r["bom"] == stock])
        self.assertFalse([a for a in fake.rows["assets/"] if a["bom"] == stock])
        depot = next(p for p in self.artifact["pools"] if p["name"].endswith("Field depot"))
        self.assertEqual([(s["serial"], s["quantity"]) for s in fake.rows["spare-items/"] if s.get("serial")],
                         [(depot["spares"][0]["serial"], 1)])


    def test_a_bom_failing_mid_seed_stays_bound_to_the_receipt(self):
        # Live (crsk8600): an unscoped rule generated one extra asset, the seed
        # stopped, and the unbound draft BOM blocked both unseed and a reseed.
        fake = _FakePlugin(self.plan)
        fake.devices = fake.devices + [fake.devices[0]]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_bytes(canonical(self.plan) + b"\n")
            build(root / "plan.json", root / "out")
            with patch.object(lifecycle, "Client", return_value=fake), \
                    patch.dict("os.environ", {"LIFECYCLE_WRITES": "1"}), self.assertRaises(Exception):
                seed(root / "out", url="http://t.example", token="x", receipt_path=root / "r.json")
            bound = json.loads((root / "r.json").read_text())["boms"]
        self.assertEqual({r["name"]: r["id"] for r in fake.rows["boms/"]},
                         {name: record["bom"] for name, record in bound.items()})

class _FakePlugin:
    """An in-memory Asset Lifecycle API, enough for seed and readback (not a claim about the plugin)."""
    base = "http://t.example"

    def __init__(self, plan):
        self.objects = {o["key"]: o for o in plan["objects"]}
        self.ids = {key: n for n, key in enumerate(sorted(self.objects), start=1)}
        self.rows, self.next = defaultdict(list), 1000
        self.devices = [o for o in plan["objects"] if o["kind"] == "device"]
        self.modules = [o for o in plan["objects"] if o["kind"] == "module"]

    def _new(self, path, row):
        self.next += 1
        row = {"id": self.next, **row}
        self.rows[path].append(row)
        return row

    def _type(self, obj):
        kind = "dcim.devicetype" if obj["kind"] == "device" else "dcim.moduletype"
        dtype = self.objects[obj["refs"]["device_type" if obj["kind"] == "device" else "module_type"]]
        maker = self.objects[dtype["refs"]["manufacturer"]]["attrs"]
        return kind, {"manufacturer": {"name": maker["name"]}, "model": dtype["attrs"]["model"]}

    def _match(self, obj, params):
        device = obj if obj["kind"] == "device" else self.objects[obj["refs"]["device"]]
        facts = {"site": self.objects[device["refs"]["site"]]["attrs"]["slug"],
                 "role": self.objects[device["refs"]["role"]]["attrs"]["slug"],
                 "status": obj["attrs"].get("status", "active"), "device": device["attrs"]["name"]}
        return all(facts[k] in v for k, v in params.items())

    def _generate(self, bom):
        rules = [r for r in self.rows["bom-scope-rules/"] if r["bom"] == bom]
        chosen = []
        for kind, pool in (("dcim.device", self.devices), ("dcim.module", self.modules)):
            mine = [r for r in rules if kind in r["object_types"]]
            chosen += [(kind, o) for o in pool
                       if any(self._match(o, r["parameters"]) for r in mine if r["action"] == "include")
                       and not any(self._match(o, r["parameters"]) for r in mine if r["action"] == "exclude")]
        lines = Counter()
        for kind, obj in chosen:
            self._new("assets/", {"bom": bom, "assigned_object_type": kind,
                                  "assigned_object_id": self.ids[obj["key"]], "installed": None, "shipment": None})
            item_type, item = self._type(obj)
            lines[(item_type, item["manufacturer"]["name"], item["model"])] += 1
        for (item_type, maker, model), count in lines.items():
            self._new("bom-line-items/", {"bom": bom, "item_type": item_type, "quantity": count,
                                          "item": {"manufacturer": {"name": maker}, "model": model}})
        row = next(r for r in self.rows["boms/"] if r["id"] == bom)
        row.update(last_generated="now", is_current=True)
        return row

    def request(self, path, method="GET", body=None, headers=None, branch=True):
        url = urlparse(path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/api/status/":
            return 200, {"plugins": {"netbox_asset_lifecycle": "0.3.1"}}
        if url.path.startswith("/api/dcim/"):
            kind = {"sites": "site", "locations": "location", "device-types": "device_type",
                    "module-types": "module_type"}[url.path.split("/")[3]]
            found = [o for o in self.objects.values() if o["kind"] == kind and (
                ("slug" in query and o["attrs"].get("slug") == query["slug"])
                or ("model" in query and o["attrs"].get("model") == query["model"]))]
            return 200, {"count": len(found), "results": [{"id": self.ids[o["key"]]} for o in found]}
        parts = url.path.split("/api/plugins/asset-lifecycle/")[1].strip("/").split("/")
        where, payload = parts[0] + "/", json.loads(body) if body else None
        if method == "POST" and parts[-1] == "generate":
            return 200, self._generate(int(parts[1]))
        if method == "POST" and parts[-1] == "install":
            asset = next(a for a in self.rows["assets/"] if a["id"] == int(parts[1]))
            asset.update(installed="now", shipment=payload["shipment"])
            return 200, asset
        if method == "POST" and where == "bom-line-items/":
            # Manual lines: the plugin resolves the type into its read-only item.
            keys = {n: key for key, n in self.ids.items()}
            rows = []
            for line in payload:
                dtype = self.objects[keys[line["item_id"]]]
                maker = self.objects[dtype["refs"]["manufacturer"]]["attrs"]["name"]
                rows.append(self._new(where, {**line, "item": {"manufacturer": {"name": maker},
                                                               "model": dtype["attrs"]["model"]}}))
            return 201, rows
        if method == "POST":
            if isinstance(payload, list):
                return 201, [self._new(where, {**p, "qty_received": None} if where == "shipment-line-items/"
                                       else p) for p in payload]
            return 201, self._new(where, payload)
        if method == "PATCH":
            changes = payload if isinstance(payload, list) else [{**payload, "id": int(parts[1])}]
            for change in changes:
                next(r for r in self.rows[where] if r["id"] == change["id"]).update(change)
            return 200, (payload if isinstance(payload, list) else
                         next(r for r in self.rows[where] if r["id"] == int(parts[1])))
        raise AssertionError(f"unexpected {method} {path}")

    def all(self, path):
        url = urlparse(path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path.startswith("/api/dcim/"):
            out = []
            for obj in self.devices if "devices" in url.path else self.modules:
                device = obj if obj["kind"] == "device" else self.objects[obj["refs"]["device"]]
                if self.ids[device["refs"]["site"]] != int(query["site_id"]):
                    continue
                if obj["kind"] == "device":
                    out.append({"id": self.ids[obj["key"]], "name": device["attrs"]["name"]})
                else:
                    out.append({"id": self.ids[obj["key"]], "device": {"name": device["attrs"]["name"]},
                                "module_bay": {"name": self.objects[obj["refs"]["module_bay"]]["attrs"]["name"]}})
            return out
        where = url.path.split("/api/plugins/asset-lifecycle/")[1]
        rows = self.rows[where]
        if where == "spare-item-allocations/":
            for row in rows:
                stock = sum(i["quantity"] for i in self.rows["spare-items/"]
                            if (i["pool"], i["item_type"], i["item_id"]) == (row["pool"], row["item_type"], row["item_id"])
                            and i["status"] == "serviceable")
                row["below_minimum"] = stock < row["min_quantity"]
        for key, value in query.items():
            rows = [r for r in rows if r.get(key.removesuffix("_id")) == int(value)]
        return rows


if __name__ == "__main__":
    unittest.main()
