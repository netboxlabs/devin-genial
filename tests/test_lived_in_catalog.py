"""Catalog 0.15: the v0.18 lived-in carrier models, their pins, vendor dates,
declared deviations and optic selection on the new hosts (independent of any builder)."""

import copy
import unittest
from types import SimpleNamespace

from estates import optics
from estates.model import DesignError, hardware_catalog

COMMIT = "72cc49fbb445f1e2f310d3b8dfef55e12d0b7138"
# SHA-256 of the raw library bytes at COMMIT (build/lived-in-design/VERIFICATION.md section 2).
PINS = {
    "aggregation-legacy-chassis": ("device-types/Juniper/ACX5048-AC.yaml", "287cd313cef390211cf2099ca07e972224f86b35463ea02f062243a665d0c03f"),
    "aggregation-legacy-psu": ("module-types/Juniper/JPSU-650W-AC-AFO.yaml", "5d811005eec2786980d8ac1ffaccecbf0c0e2e7e196ae184e3e2484839f22af5"),
    "provider-edge-legacy-chassis": ("device-types/Juniper/MX80.yaml", "cb297f96f8b2bd8794cf10e09673c3a9f6ad864fcba62212fb5053ef32604adb"),
    "provider-edge-successor-chassis": ("device-types/Juniper/MX304.yaml", "b6ad58a276c86a9e24eaebc1fde1d0b48d3497041269a2c8ce806548a7b4f764"),
    "provider-edge-successor-lmic": ("module-types/Juniper/JNP304-LMCIC16.yaml", "828de371116216b14b1dd4d202d039a998cdf7d29d8df2f6ce8b0635b36bc3f2"),
    "ddos-mitigation-chassis": ("device-types/Arbor/TMS-HD-1000.yaml", "6db7c55576498a50c21963e0db4614fdf2221c4c5466588874a6a3e4ead83f09"),
    "time-server-chassis": ("device-types/Meinberg/lantime-m300.yml", "bc78849116d701a742a0c6b24d320b0094df1530632518d04ada540a4efea6b7"),
    "nid-legacy-chassis": ("device-types/Accedian/MetroNID-TE.yaml", "d6c79051e0d3af17d6e9de2c5db2c8f4db0cc9b72c039e4593086fc557949f1e"),
}
ALIASES = ("aggregation-legacy", "provider-edge-legacy", "provider-edge-successor",
           "ddos-mitigation", "time-server", "nid-legacy")
# Vendor dates (cited) and authored milestones (labelled), DESIGN section 4.1.
DATES = {
    "provider-edge-legacy": {"last_order": "2021-06-30", "end_of_support": "2026-06-30"},
    "provider-edge": {"eol_announced": "2026-06-15", "last_order": "2027-06-15", "end_of_support": "2032-06-30",
                      "notice": "TSB107750", "available": "2018-01-01", "available_basis": "authored"},
    "aggregation-legacy": {"last_order": "2022-12-31", "end_of_support": "2027-12-31",
                           "available": "2015-01-01", "available_basis": "authored"},
    "aggregation": {"available": "2019-07-01", "available_basis": "authored"},
    "provider-edge-successor": {"available": "2022-07-01", "available_basis": "authored"},
    "nid-legacy": {"launched": "2008-04-15"},
    "time-server": {"discontinued": True, "successor": "LANTIME M320"},
}


def problems(catalog):
    """Independent checks of the lived-in contract other packages code against."""
    out, m, src = [], catalog["models"], catalog["sources"]
    for key, (path, sha) in PINS.items():
        s = src.get(key, {})
        if (s.get("url") != f"https://raw.githubusercontent.com/netbox-community/devicetype-library/{COMMIT}/{path}"
                or s.get("sha256") != sha or s.get("commit") != COMMIT or s.get("license") != "CC0-1.0"):
            out.append(f"pin {key}")
    ports = {}
    for alias in ALIASES:
        model = m.get(alias, {})
        if not model or any(i not in src for i in model.get("source_ids", ["missing"])) or not model.get("serial_format"):
            out.append(f"model {alias}")
            continue
        ports[alias] = {i["name"]: i for i in model["interfaces"]}
        inlets = {p["name"] for p in model["power_ports"]}
        # Every configured supply owns an existing inlet; module serials need a format.
        for config in model.get("configured_modules", []):
            if config["power_port"] not in inlets or not model.get("module_serial_format"):
                out.append(f"{alias} psu")
    for alias, dates in DATES.items():
        life = m.get(alias, {}).get("lifecycle", {})
        if any(life.get(k) != v for k, v in dates.items()) or any(i not in src for i in life.get("source_ids", [])):
            out.append(f"dates {alias}")
        # A vendor date needs a cited source; an authored one says so.
        if set(life) - {"available", "available_basis", "source_ids"} and not life.get("source_ids"):
            out.append(f"dates {alias}")
        if "available" in life and life.get("available_basis") != "authored":
            out.append(f"dates {alias}")
    if m.get("aggregation-legacy", {}).get("lifecycle", {}).get("available", "9") >= "2022-12-31":
        out.append("dates aggregation-legacy")

    acx = m.get("aggregation-legacy", {})
    agg = ports.get("aggregation-legacy", {})
    uni, lag = acx.get("uni_ports", []), acx.get("lag_ports", [])
    if (uni != [f"xe-0/0/{i}" for i in range(40)] or lag != [f"xe-0/0/{i}" for i in range(40, 44)]
            or any(agg.get(p, {}).get("type") != "10gbase-x-sfpp" for p in uni + lag)
            # Nothing may plan 100G on an aggregation switch: the ACX5048 has none.
            or any(i["type"].startswith("100g") for i in agg.values())
            or [agg.get(f"et-0/0/{i}", {}).get("type") for i in range(48, 54)] != ["40gbase-x-qsfpp"] * 6
            or not agg.get("em0", {}).get("mgmt_only") or agg.get("em0", {}).get("type") != "1000base-t"
            or [c["power_port"] for c in acx.get("configured_modules", [])] != ["PSU 0", "PSU 1"]
            or {c["model"] for c in acx.get("configured_modules", [])} != {"JPSU-650W-AC-AFO"}
            or acx.get("console_ports") != [{"name": "Console", "type": "rj-45"}]):
        out.append("aggregation-legacy ports")
    mx80 = ports.get("provider-edge-legacy", {})
    if ([mx80.get(f"xe-0/0/{i}", {}).get("type") for i in range(4)] != ["10gbase-x-xfp"] * 4
            or not mx80.get("fxp0", {}).get("mgmt_only")
            or [p["name"] for p in m["provider-edge-legacy"]["power_ports"]] != ["PEM0", "PEM1"]):
        out.append("provider-edge-legacy ports")
    mx304 = m.get("provider-edge-successor", {})
    lmic = ports.get("provider-edge-successor", {})
    if (list(lmic) != [f"et-0/0/{i}" for i in range(16)] or {i["type"] for i in lmic.values()} != {"400gbase-x-qsfpdd"}
            or mx304.get("power_ports") or mx304.get("console_ports") or mx304.get("configured_modules")
            or mx304.get("pair_port") not in lmic or mx304.get("tms_port") not in lmic
            or mx304.get("pair_port") == mx304.get("tms_port")
            or mx304.get("line_card", {}).get("model") != "JNP304-LMCIC16"):
        out.append("provider-edge-successor ports")
    tms = m.get("ddos-mitigation", {})
    tports = ports.get("ddos-mitigation", {})
    if (any(tports.get(p, {}).get("type") != "100gbase-x-qsfp28" for p in tms.get("offramp_ports", [])) or
            len(tms.get("offramp_ports", [])) != 2 or not tports.get("Management", {}).get("mgmt_only")
            # Labelled fiction: the unpinned supply is declared by its authored
            # source; its record keeps an operational name (record-disclaimer).
            or not all(c["model"] == "TMS AC PSU" for c in tms.get("configured_modules", [{"model": ""}]))
            or "authored-tms-psu" not in tms.get("source_ids", [])):
        out.append("ddos-mitigation ports")
    ts = m.get("time-server", {})
    if (ts.get("management_port") not in ports.get("time-server", {})
            or [p["name"] for p in ts.get("power_ports", [])] != ["PWR1"]
            or ts.get("platform", {}).get("name") != "LANTIME OS"):
        out.append("time-server ports")
    nid = m.get("nid-legacy", {})
    nports = ports.get("nid-legacy", {})
    if (nports.get(nid.get("nni_port"), {}).get("type") != "1000base-x-sfp"
            or nports.get(nid.get("uni_port"), {}).get("type") != "1000base-x-sfp"
            # Virtual interfaces are builder-created, never hardware inventory.
            or nid.get("management_interface") in nports or "management_interface" not in nid
            or {p["type"] for p in nid.get("power_ports", [])} != {"dc-terminal"}):
        out.append("nid-legacy ports")
    parts = catalog["optics"]["parts"]
    # MX204 port-speed limit: no part may light the disabled fourth 100G cage,
    # and the TMS never attaches to an MX204.
    for pid, part in parts.items():
        if "et-0/0/3" in part["compatible_interfaces"].get("provider-edge", []):
            out.append(f"mx204 et-0/0/3 {pid}")
        hosts = set(part["compatible_interfaces"])
        if "ddos-mitigation" in hosts and "provider-edge" in hosts:
            out.append(f"tms on mx204 {pid}")
        # L12: a part on a host without a published qualified-optics list is Generic.
        if hosts & {"ddos-mitigation", "nid-legacy"} and part["manufacturer"] != "Generic":
            out.append(f"l12 {pid}")
    return out


class LivedInCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = hardware_catalog()

    def test_contract_holds(self):
        self.assertEqual(self.catalog["version"], "0.15")
        self.assertEqual(problems(self.catalog), [])

    def test_failing_mutations_are_caught(self):
        m = lambda c, alias: c["models"][alias]
        mutations = {
            "pin ddos-mitigation-chassis": lambda c: c["sources"]["ddos-mitigation-chassis"].__setitem__("sha256", "0" * 64),
            "pin time-server-chassis": lambda c: c["sources"]["time-server-chassis"].__setitem__(
                "url", c["sources"]["time-server-chassis"]["url"].replace(".yml", ".yaml")),
            "model nid-legacy": lambda c: m(c, "nid-legacy")["source_ids"].append("no-such-source"),
            "aggregation-legacy psu": lambda c: m(c, "aggregation-legacy")["configured_modules"][0].__setitem__("power_port", "PEM 0"),
            "aggregation-legacy ports": lambda c: m(c, "aggregation-legacy")["interfaces"][48].__setitem__("type", "100gbase-x-qsfp28"),
            "provider-edge-legacy ports": lambda c: m(c, "provider-edge-legacy")["interfaces"][1].__setitem__("type", "10gbase-x-sfpp"),
            "provider-edge-successor ports": lambda c: m(c, "provider-edge-successor")["power_ports"].append(
                {"name": "PEM0", "type": "iec-60320-c14"}),
            "ddos-mitigation ports": lambda c: m(c, "ddos-mitigation")["configured_modules"][0].__setitem__("model", "TMS-PSU-AC"),
            "time-server ports": lambda c: m(c, "time-server").__setitem__("management_port", "eth0"),
            "nid-legacy ports": lambda c: m(c, "nid-legacy")["interfaces"].append({"name": "Management", "type": "virtual"}),
            "dates provider-edge": lambda c: m(c, "provider-edge")["lifecycle"].__setitem__("end_of_support", "2031-06-30"),
            "dates aggregation-legacy": lambda c: m(c, "aggregation-legacy")["lifecycle"].__setitem__("available", "2023-01-01"),
            "dates provider-edge-successor": lambda c: m(c, "provider-edge-successor")["lifecycle"].pop("available_basis"),
            "dates provider-edge-legacy": lambda c: m(c, "provider-edge-legacy")["lifecycle"].__setitem__("source_ids", []),
            "mx204 et-0/0/3 juniper-100g-lr4": lambda c: c["optics"]["parts"]["juniper-100g-lr4"]["compatible_interfaces"][
                "provider-edge"].append("et-0/0/3"),
            "tms on mx204 generic-100g-aoc-3m": lambda c: c["optics"]["parts"]["generic-100g-aoc-3m"]["compatible_interfaces"].__setitem__(
                "provider-edge", ["et-0/0/2"]),
            "l12 juniper-100g-aoc-3m": lambda c: c["optics"]["parts"]["juniper-100g-aoc-3m"]["compatible_interfaces"].__setitem__(
                "ddos-mitigation", ["1"]),
        }
        for expected, mutate in mutations.items():
            with self.subTest(expected):
                catalog = copy.deepcopy(self.catalog)
                mutate(catalog)
                self.assertIn(expected, problems(catalog))

    def test_every_new_host_cage_is_a_known_form_factor(self):
        models = self.catalog["models"]
        for pid, part in self.catalog["optics"]["parts"].items():
            self.assertTrue(set(part["source_ids"]) <= set(self.catalog["sources"]), pid)
            for alias, names in part["compatible_interfaces"].items():
                cages = {i["name"]: i["type"] for i in models[alias]["interfaces"]}
                self.assertTrue(all(cages.get(n) in optics._CAGES for n in names), (pid, alias))


class LivedInOpticsSelectionTests(unittest.TestCase):
    """optics.enrich on a minimal synthetic world holding only the new hosts."""

    def world(self, catalog=None):
        objects = {}

        def add(kind, key, attrs=None, refs=None, meta=None):
            objects[key] = dict(key=key, kind=kind, attrs=attrs or {}, refs=refs or {}, meta=meta or {})
            return key

        w = SimpleNamespace(objects=objects, catalog=catalog or hardware_catalog(), recipe={"namespace": "li"}, add=add)
        for dev, alias, room in (("agg", "aggregation-legacy", "cage"), ("pe", "provider-edge", "cage"),
                                 ("pe2", "provider-edge-successor", "cage"), ("pe2b", "provider-edge-successor", "cage"),
                                 ("tms", "ddos-mitigation", "cage"), ("nid", "nid-legacy", "mpoe")):
            add("device_type", f"hardware/{alias}")
            add("device", f"device/{dev}", {}, {"device_type": f"hardware/{alias}", "location": f"location/{room}"})

        def port(dev, name, kind, speed=None):
            key = f"interface/{dev}/{name}"
            add("interface", key, dict(name=name, type=kind, **({"speed": speed} if speed else {})), {"device": f"device/{dev}"})
            return key

        def circuit(cid, km):
            add("circuit", f"circuit/{cid}", {"distance": km, "distance_unit": "km"}, {"provider": "provider/operator"})
            return [add("circuit_termination", f"circuit/{cid}/{s}", {}, {"circuit": f"circuit/{cid}"}) for s in "AZ"]

        def cable(name, a, b, kind="smf", length=3, status="connected"):
            add("cable", f"cable/{name}", {"type": kind, "length": length, "length_unit": "m", "status": status}, {"a": a, "b": b})

        cable("lag", port("agg", "xe-0/0/40", "10gbase-x-sfpp"), port("pe", "xe-0/1/0", "10gbase-x-sfpp"))
        near_a, near_z = circuit("near", 5)
        far_a, far_z = circuit("far", 27)
        teng_a, teng_z = circuit("teng", 25)
        cable("uni-near", port("agg", "xe-0/0/0", "10gbase-x-sfpp", 1000000), near_z, length=10)
        cable("nni-near", port("nid", "B_Network", "1000base-x-sfp"), near_a, length=10)
        cable("uni-far", port("agg", "xe-0/0/1", "10gbase-x-sfpp", 1000000), far_z, length=10)
        cable("uni-10g", port("agg", "xe-0/0/2", "10gbase-x-sfpp"), teng_z, length=10)
        # Planned offramp: the TMS rides the MX304 successor, never the MX204.
        cable("offramp", port("pe2", "et-0/0/1", "400gbase-x-qsfpdd", 100000000), port("tms", "1", "100gbase-x-qsfp28"),
              kind="aoc", status="planned")
        cable("pair", port("pe2", "et-0/0/0", "400gbase-x-qsfpdd", 100000000),
              port("pe2b", "et-0/0/0", "400gbase-x-qsfpdd", 100000000), status="planned")
        return w

    def installed(self, w):
        return {o["key"].removeprefix("optics-module/interface/"): w.objects[o["refs"]["module_type"]]["key"].removeprefix("module-type/")
                for o in w.objects.values() if o["kind"] == "module"}

    def test_new_hosts_get_reach_and_media_correct_optics(self):
        w = self.world()
        optics.enrich(w)
        self.assertEqual(self.installed(w), {
            "agg/xe-0/0/40": "Juniper/EX-SFP-10GE-SR",      # in-rack LAG on the ACX5048: multimode SR
            "pe/xe-0/1/0": "Juniper/EX-SFP-10GE-SR",
            "agg/xe-0/0/0": "Juniper/SFP-1GE-LX",           # 1G UNI keeps xe-, 5 km owned access
            "nid/B_Network": "Generic/SFP-1G-LX",           # L12: no Accedian optics list
            "agg/xe-0/0/1": "Juniper/SFP-1GE-LH",           # 27 km: shortest covering reach
            "agg/xe-0/0/2": "Juniper/EX-SFP-10GE-ER",       # 10G tier at 25 km
            "pe2/et-0/0/1": "Generic/QSFP28-100G-AOC-3M",   # QSFP28 AOC in the LMIC's QSFP-DD cage
            "tms/1": "Generic/QSFP28-100G-AOC-3M",
            "pe2/et-0/0/0": "Juniper/JNP-QSFP-100G-SR4",    # MX304 pair, in-room multimode
            "pe2b/et-0/0/0": "Juniper/JNP-QSFP-100G-SR4",
        })
        self.assertEqual(w.objects["cable/lag"]["attrs"]["type"], "mmf")
        # One assembly: both captive ends share the cable-derived serial.
        ends = [w.objects[f"optics-module/interface/{k}"]["attrs"]["serial"] for k in ("pe2/et-0/0/1", "tms/1")]
        self.assertEqual(ends[0], ends[1])
        self.assertIn("optics-bay-type/Juniper/qsfpdd", w.objects)

    def test_failing_mutations(self):
        # Without the ACX5000 ER part a 25 km 10G tier is refused, never a 10 km optic.
        catalog = hardware_catalog()
        del catalog["optics"]["parts"]["juniper-10g-er"]
        with self.assertRaisesRegex(DesignError, "no reviewed optic"):
            optics.enrich(self.world(catalog))
        # An MX304 port left at its native 400G finds no part: builders must configure 100G.
        w = self.world()
        del w.objects["interface/pe2/et-0/0/1"]["attrs"]["speed"]
        with self.assertRaisesRegex(DesignError, "no reviewed optic"):
            optics.enrich(w)
        # Without the QSFP-DD cage mapping the LMIC host cannot be indexed at all.
        saved = optics._CAGES.pop("400gbase-x-qsfpdd")
        try:
            with self.assertRaises(KeyError):
                optics.enrich(self.world())
        finally:
            optics._CAGES["400gbase-x-qsfpdd"] = saved


if __name__ == "__main__":
    unittest.main()
