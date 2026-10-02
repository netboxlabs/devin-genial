"""Catalog 0.14: the regional-carrier footprint models, their pins, declared
deviations and optic selection on the new hosts (independent of any builder)."""

import copy
import unittest
from types import SimpleNamespace

from estates import optics
from estates.model import DesignError, hardware_catalog

COMMIT = "72cc49fbb445f1e2f310d3b8dfef55e12d0b7138"
# Library files pinned for this footprint and the SHA-256 of their raw bytes
# at COMMIT (the same method reproduces the existing AP9572 pin).
PINS = {
    "aggregation-chassis": ("device-types/Juniper/ACX5448-M.yaml", "8c6423438da7b24e03323b8be24ff7c2bd565fda8311bee1d3189696e0e3b2f0"),
    "nid-chassis": ("device-types/Ciena/3903-ac.yaml", "40f55fd4ef907a9c33f7e9011687e71b2cbbe9b870df15b2c7e47fc011fc8a64"),
    "nid-10g-chassis": ("device-types/RAD/ETX-2i-10G.yaml", "14fffdecffefb67aa36d74221f712a2360d3d0af30c5ec497f86a34e9307954d"),
    "ce-small-chassis": ("device-types/Juniper/SRX300.yaml", "3f4f7ae2d0683c981f513f0603b378c747d8513019c2554a9dc72002635234df"),
    "pop-mgmt-chassis": ("device-types/Juniper/EX3400-24T.yaml", "cf8d01d473417ae1a16c8a584da13b1512c55f9e68316a232439b1b87e46ab46"),
    "pop-mgmt-psu": ("module-types/Juniper/JPSU-150-AC-AFO.yaml", "45cc413953ae8d25b1b3b801ba030d516bec0559b4e0cd014c688ca7c4f920f3"),
    "oob-server-chassis": ("device-types/Opengear/OM2216-L.yaml", "1891adc7daa663105dcaaeccb0aba7ae30c417820f0816f70f54fe7154d551f1"),
    "pdu-switched-chassis": ("device-types/APC/AP8941.yml", "e3f4c80f1141cda14f97083a69c9f5dd1ec52fb81b5c590db91c2c5e94b3c9ab"),
    "osp-panel-chassis": ("device-types/Commscope/FMS-K2BI-L1A1-48-SP.yaml", "27206e797a3845c03e6778b27c425d0bc75ec50f46e2b8bb4f04f60dd2baea93"),
    "demarc-panel-chassis": ("device-types/Generic/LC-24-port-fiber-patch-panel.yaml", "c12c883190f9ae7e84ed2913d7d72275a2868a60a48bca5afa223196e448c8ee"),
    "cable-manager-1u-chassis": ("device-types/Generic/cable-management-panel-1u.yaml", "f39ede5d55d003a6da677d443bfdacb47f1fc42168fa021ac11d91af3bb4d866"),
    "cable-manager-2u-chassis": ("device-types/Generic/cable-management-panel-2u.yaml", "fe8c0484e3d11855638ae708992cfb028e62df1fcef0bfd07320eea0257422f4"),
    "blanking-1u-chassis": ("device-types/Generic/Blanking-Panel-1U.yaml", "5cede43816eca260dc7fd60c8df28c9e18562bdd48a4272c627d30c7f8f39684"),
    "blanking-2u-chassis": ("device-types/Generic/Blanking-Panel-2U.yaml", "347885d877e9f74b7404b20b1da494b668c5eda5502292febfc7efe1fce54076"),
    "pop-cabinet-rack": ("rack-types/APC/AR3100.yaml", "d383563da11c3e295a9a93222220b8e53688fa48c435611df8568f74428ff4d3"),
    "mpoe-cabinet-rack": ("rack-types/KOSCAB/kos-shts-9u55x45x50ds.yaml", "af9f4e312bd42c7ca0378fd87995c337c6e87463f674ac9608ca9d775781b233"),
}
ALIASES = ("aggregation", "nid", "nid-10g", "ce-small", "pop-mgmt", "oob-server", "pdu-switched",
           "osp-panel", "demarc-panel", "cable-manager-1u", "cable-manager-2u", "blanking-1u", "blanking-2u")


def problems(catalog):
    """Independent checks of the footprint contract other packages code against."""
    out, m, src = [], catalog["models"], catalog["sources"]
    for key, (path, sha) in PINS.items():
        s = src.get(key, {})
        if (s.get("url") != f"https://raw.githubusercontent.com/netbox-community/devicetype-library/{COMMIT}/{path}"
                or s.get("sha256") != sha or s.get("commit") != COMMIT or s.get("license") != "CC0-1.0"):
            out.append(f"pin {key}")
    for alias in ALIASES:
        model = m.get(alias, {})
        if not model or any(i not in src for i in model.get("source_ids", ["missing"])) or not model.get("serial_format"):
            out.append(f"model {alias}")
    for alias, rack in catalog.get("rack_types", {}).items():
        if any(i not in src for i in rack.get("source_ids", ["missing"])):
            out.append(f"rack {alias}")
    ports = {a: {i["name"]: i for i in m[a]["interfaces"]} for a in ALIASES if a in m}
    nid = ports.get("nid", {})
    # Declared deviation: the combo UNI in its RJ-45 personality; 1 and 2 stay SFP.
    if ([nid.get(n, {}).get("type") for n in ("1", "2", "3")] != ["1000base-x-sfp", "1000base-x-sfp", "1000base-t"]
            or nid.get("Management", {}).get("type") != "virtual"
            or (m["nid"]["nni_port"], m["nid"]["uni_port"], m["nid"]["spare_port"]) != ("1", "3", "2")):
        out.append("nid ports")
    rad = ports.get("nid-10g", {})
    if (rad.get(m["nid-10g"]["nni_port"], {}).get("type") != "10gbase-x-sfpp"
            or rad.get(m["nid-10g"]["uni_port"], {}).get("type") != "10gbase-x-sfpp"):
        out.append("nid-10g ports")
    agg = ports.get("aggregation", {})
    uni, lag = m["aggregation"]["uni_ports"], m["aggregation"]["lag_ports"]
    if (len(uni) != 40 or len(lag) != 4 or set(uni) & set(lag)
            or any(agg.get(p, {}).get("type") != "10gbase-x-sfpp" for p in uni + lag)
            or not agg.get("em0", {}).get("mgmt_only")):
        out.append("aggregation ports")
    for alias in ("ce-small", "edge"):
        names = {i["name"]: i for i in m[alias]["interfaces"]}
        wan, lan = m[alias].get("wan_ports", []), m[alias].get("lan_ports", [])
        if not wan or set(wan) & set(lan) or any(names.get(p, {}).get("type") != "1000base-t" for p in wan + lan):
            out.append(f"{alias} wan/lan")
    if "poe_pse" in m["pop-mgmt"] or any(i.get("poe_mode") for i in m["pop-mgmt"]["interfaces"]):
        out.append("pop-mgmt poe")
    oob = ports.get("oob-server", {})
    if oob.get(m["oob-server"]["lte_interface"], {}).get("type") != "lte" or not oob.get("eth0", {}).get("mgmt_only"):
        out.append("oob lte")
    if not any(i.get("mgmt_only") for i in m["pdu-switched"]["interfaces"]) or len(m["pdu-switched"]["power_outlets"]) != 24:
        out.append("pdu network")
    for alias, count, rear in (("osp-panel", 48, "splice"), ("demarc-panel", 24, "lc")):
        p = m[alias]
        fronts, rears = p["front_ports"], p["rear_ports"]
        # Declared deviation: CommScope fronts are LC/UPC (`lc`), not the library's lc-apc.
        if (p["passive_ports"] != count or len(fronts) != count or [f["name"] for f in fronts] != [r["name"] for r in rears]
                or {f["type"] for f in fronts} != {"lc"} or {r["type"] for r in rears} != {rear} or p.get("is_powered") is not False):
            out.append(f"{alias} ports")
    for alias in ("blanking-1u", "blanking-2u", "cable-manager-1u", "cable-manager-2u"):
        if m[alias].get("exclude_from_utilization", False) is not alias.startswith("blanking"):
            out.append(f"{alias} utilization")
    racks = catalog.get("rack_types", {})
    if (racks.get("pop-cabinet", {}).get("u_height"), racks.get("mpoe-cabinet", {}).get("u_height"),
            racks.get("mpoe-cabinet", {}).get("form_factor")) != (42, 9, "wall-cabinet"):
        out.append("rack types")
    return out


class FootprintCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = hardware_catalog()

    def test_footprint_contract_holds(self):
        self.assertEqual(problems(self.catalog), [])

    def test_failing_mutations_are_caught(self):
        mutations = {
            "pin osp-panel-chassis": lambda c: c["sources"]["osp-panel-chassis"].__setitem__("sha256", "0" * 64),
            "model nid": lambda c: c["models"]["nid"]["source_ids"].append("no-such-source"),
            "nid ports": lambda c: c["models"]["nid"]["interfaces"][2].__setitem__("type", "1000base-x-sfp"),
            "aggregation ports": lambda c: c["models"]["aggregation"]["lag_ports"].append("xe-0/0/0"),
            "ce-small wan/lan": lambda c: c["models"]["ce-small"].__setitem__("wan_ports", ["ge-0/0/7"]),
            "osp-panel ports": lambda c: c["models"]["osp-panel"]["front_ports"][0].__setitem__("type", "lc-apc"),
            "blanking-1u utilization": lambda c: c["models"]["blanking-1u"].pop("exclude_from_utilization"),
            "oob lte": lambda c: c["models"]["oob-server"]["interfaces"][2].__setitem__("type", "other"),
            "rack types": lambda c: c["rack_types"]["mpoe-cabinet"].__setitem__("u_height", 6),
        }
        for expected, mutate in mutations.items():
            with self.subTest(expected):
                catalog = copy.deepcopy(self.catalog)
                mutate(catalog)
                self.assertIn(expected, problems(catalog))

    def test_every_new_optic_cites_a_source_and_fits_a_real_cage(self):
        sources, models = self.catalog["sources"], self.catalog["models"]
        for pid, part in self.catalog["optics"]["parts"].items():
            self.assertTrue(set(part["source_ids"]) <= set(sources), pid)
            for alias, names in part["compatible_interfaces"].items():
                cages = {i["name"]: i["type"] for i in models[alias]["interfaces"]}
                self.assertTrue(all(cages.get(n) in optics._CAGES for n in names), (pid, alias))
            # L12: a vendor-branded part needs a vendor document; otherwise it is Generic.
            if part["manufacturer"] != "Generic":
                self.assertTrue(any(sources[s].get("url", "").startswith("https://") for s in part["source_ids"]), pid)


class FootprintOpticsSelectionTests(unittest.TestCase):
    """Optics.enrich on a minimal synthetic world holding only the new hosts."""

    def world(self, catalog=None):
        objects = {}

        def add(kind, key, attrs=None, refs=None, meta=None):
            objects[key] = dict(key=key, kind=kind, attrs=attrs or {}, refs=refs or {}, meta=meta or {})
            return key

        w = SimpleNamespace(objects=objects, catalog=catalog or hardware_catalog(),
                            recipe={"namespace": "fp"}, add=add)
        for alias in ("aggregation", "provider-edge", "nid", "nid-10g", "ce-small"):
            add("device_type", f"hardware/{alias}")
        for dev, alias, room in (("agg", "aggregation", "cage"), ("pe", "provider-edge", "cage"),
                                 ("nid", "nid", "mpoe"), ("nid10", "nid-10g", "mpoe2"), ("ce", "ce-small", "mpoe")):
            add("device", f"device/{dev}", {}, {"device_type": f"hardware/{alias}", "location": f"location/{room}"})
        cables = []

        def port(dev, name, kind, speed=None):
            key = f"interface/{dev}/{name}"
            add("interface", key, dict(name=name, type=kind, **({"speed": speed} if speed else {})), {"device": f"device/{dev}"})
            return key

        def circuit(cid, km):
            add("circuit", f"circuit/{cid}", {"distance": km, "distance_unit": "km"}, {"provider": "provider/operator"})
            return [add("circuit_termination", f"circuit/{cid}/{s}", {}, {"circuit": f"circuit/{cid}"}) for s in "AZ"]

        def cable(name, a, b, kind="smf", length=3):
            cables.append(add("cable", f"cable/{name}", {"type": kind, "length": length, "length_unit": "m"}, {"a": a, "b": b}))

        cable("lag", port("agg", "xe-0/0/40", "10gbase-x-sfpp"), port("pe", "xe-0/1/0", "10gbase-x-sfpp"))
        add("device_type", "hardware/pop-mgmt")
        add("device", "device/mgmt", {}, {"device_type": "hardware/pop-mgmt", "location": "location/cage"})
        cable("mgmt-uplink", port("mgmt", "xe-0/2/0", "10gbase-x-sfpp"), port("pe", "xe-0/1/6", "10gbase-x-sfpp"))
        near_a, near_z = circuit("near", 5)
        far_a, far_z = circuit("far", 27)
        tenG_a, tenG_z = circuit("teng", 25)
        cable("uni-near", port("agg", "xe-0/0/0", "10gbase-x-sfpp", 1000000), near_z, length=10)
        cable("nni-near", port("nid", "1", "1000base-x-sfp"), near_a, length=10)
        cable("uni-far", port("agg", "xe-0/0/1", "10gbase-x-sfpp", 1000000), far_z, length=10)
        cable("uni-10g", port("agg", "xe-0/0/2", "10gbase-x-sfpp"), tenG_z, length=10)
        cable("nni-10g", port("nid10", "ETH-1/1", "10gbase-x-sfpp"), tenG_a, length=10)
        cable("handoff", port("nid", "3", m_type(w, "nid", "3")), port("ce", "ge-0/0/0", "1000base-t"), kind="cat6")
        # A UNI patched through the OSP panel (front 1 <-> rear 1) to a 27 km owned access circuit.
        add("device", "device/osp", {}, {"device_type": "hardware/osp-panel", "location": "location/cage"})
        rear = add("rear_port", "device/osp/rear/1", {"name": "Port 1", "type": "splice", "positions": 1}, {"device": "device/osp"})
        front = add("front_port", "device/osp/front/1", {"name": "Port 1", "type": "lc", "rear_port_position": 1},
                    {"device": "device/osp", "rear_port": rear})
        _, osp_z = circuit("via-osp", 27)
        cable("uni-osp", port("agg", "xe-0/0/3", "10gbase-x-sfpp", 1000000), front, length=4)
        cable("osp-term", rear, osp_z, length=2)
        return w

    def installed(self, w):
        out = {}
        for o in w.objects.values():
            if o["kind"] == "module":
                mt = w.objects[o["refs"]["module_type"]]
                out[o["key"].removeprefix("optics-module/interface/")] = mt["key"].removeprefix("module-type/")
        return out

    def test_new_hosts_get_reach_and_media_correct_optics(self):
        w = self.world()
        optics.enrich(w)
        self.assertEqual(self.installed(w), {
            "agg/xe-0/0/40": "Juniper/SFPP-10GE-SR",       # in-rack LAG member: multimode SR
            "pe/xe-0/1/0": "Juniper/EX-SFP-10GE-SR",
            "mgmt/xe-0/2/0": "Juniper/EX-SFP-10GE-SR",      # PoP mgmt uplink, in-room SR
            "pe/xe-0/1/6": "Juniper/EX-SFP-10GE-SR",
            "agg/xe-0/0/0": "Juniper/SFP-1GE-LX",          # 1G UNI, 5 km owned access fiber
            "nid/1": "Generic/SFP-1G-LX",
            "agg/xe-0/0/1": "Juniper/SFP-1GE-LH",          # 27 km: shortest covering reach
            "agg/xe-0/0/2": "Juniper/SFPP-10GE-ER",        # 10G tier, 25 km
            "nid10/ETH-1/1": "RAD/SFP-P-3DH",
            "agg/xe-0/0/3": "Juniper/SFP-1GE-LH",          # 27 km traced through the OSP panel
        })
        self.assertEqual(w.objects["cable/lag"]["attrs"]["type"], "mmf")
        self.assertNotIn("optics-module/interface/nid/3", w.objects)  # copper RJ-45 UNI takes no optic

    def test_failing_mutations(self):
        # Without a reviewed multimode part on the ACX the in-rack LAG keeps a 10 km optic.
        catalog = hardware_catalog()
        del catalog["optics"]["parts"]["juniper-sfpp-10g-sr"]
        w = self.world(catalog)
        optics.enrich(w)
        self.assertEqual(w.objects["cable/lag"]["attrs"]["type"], "smf")
        self.assertEqual(self.installed(w)["agg/xe-0/0/40"], "Juniper/SFPP-10GE-LR")
        # Without a long-reach NID part a 27 km owned run is refused, never a short optic.
        catalog = hardware_catalog()
        del catalog["optics"]["parts"]["generic-1g-zx"]
        w = self.world(catalog)
        w.objects["cable/nni-near"]["refs"]["b"] = "circuit/far/A"
        w.objects["cable/uni-far"]["refs"]["b"] = "circuit/near/A"
        with self.assertRaisesRegex(DesignError, "no reviewed optic"):
            optics.enrich(w)
        # A panel whose rear is not patched onward ends the trace: no owned span, a 10 km part.
        w = self.world()
        del w.objects["cable/osp-term"]
        optics.enrich(w)
        self.assertEqual(self.installed(w)["agg/xe-0/0/3"], "Juniper/SFP-1GE-LX")
        # The library's SFP typing of the Ciena combo port would demand an optic on a copper handoff.
        catalog = hardware_catalog()
        catalog["models"]["nid"]["interfaces"][2]["type"] = "1000base-x-sfp"
        with self.assertRaisesRegex(DesignError, "no reviewed optic"):
            optics.enrich(self.world(catalog))


def m_type(w, alias, name):
    return next(i["type"] for i in w.catalog["models"][alias]["interfaces"] if i["name"] == name)


if __name__ == "__main__":
    unittest.main()
