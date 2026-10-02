"""Provider customer-edge realism: the showcase carrier's own records read like a carrier's."""

from collections import defaultdict
from copy import deepcopy
import ipaddress
from pathlib import Path
import tomllib
import unittest

from estates.generate import generate
from estates.validate import validate


ROOT = Path(__file__).parents[1]


class ShowcaseCustomerEdgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate(tomllib.loads((ROOT / "profiles/showcase-provider.toml").read_text()))

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.o = {obj["key"]: obj for obj in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def of(self, kind):
        return [obj for obj in self.o.values() if obj["kind"] == kind]

    def test_baseline_validates(self):
        self.assertEqual(validate(self.plan), [])

    # --- The carrier is not its own supplier ---------------------------------

    def test_operator_circuits_carry_service_orders_and_no_self_escalation(self):
        own = {c["key"] for c in self.of("circuit") if c["refs"]["provider"] == "provider/operator"}
        self.assertTrue(own)
        carrier_desk = {a["refs"]["object"] for a in self.of("contact_assignment")
                        if a["refs"]["role"] == "contact-role/carrier"}
        self.assertFalse(own & carrier_desk)
        self.assertNotIn("contact/provider/operator", self.o)
        for key in own:
            self.assertNotIn(f"journal/{key}/capacity-request", self.o)
        orders = [self.o[f"journal/{key}/service-order"] for key in own if f"journal/{key}/service-order" in self.o]
        self.assertTrue(orders)
        for journal in orders:
            self.assertNotIn("carrier", journal["attrs"]["comments"])
            self.assertIn("Service order for", journal["attrs"]["comments"])
        # Third-party carriers still escalate through their own desks.
        leased = {c["key"] for c in self.of("circuit") if c["refs"]["provider"] != "provider/operator"}
        self.assertEqual(leased, leased & carrier_desk)

    def test_self_escalation_is_refused(self):
        circuit = next(c["key"] for c in self.of("circuit") if c["refs"]["provider"] == "provider/operator")
        desk = next(a["refs"]["contact"] for a in self.of("contact_assignment") if a["refs"]["role"] == "contact-role/carrier")
        self.plan["objects"].append({"key": f"contact-assignment/{circuit}/carrier", "kind": "contact_assignment",
                                     "attrs": {"priority": "secondary"}, "meta": {},
                                     "refs": {"object": circuit, "contact": desk, "role": "contact-role/carrier"}})
        self.assertIn("operations-contact", self.codes())

    def test_dual_homed_tag_is_truthful(self):
        tagged = {s["key"] for s in self.of("site") if "tag/dual-homed" in s["refs"].get("tags", [])}
        pops = {s["key"] for s in self.of("site") if s["key"].startswith("site/pop-")}
        # Only PoPs where two different third-party carriers actually land.
        self.assertTrue(tagged & pops)
        self.assertLess(len(tagged & pops), len(pops))
        for site in tagged & pops:
            carriers = {self.o[t["refs"]["circuit"]]["refs"]["provider"] for t in self.of("circuit_termination")
                        if self.o.get(t["refs"]["termination"], {}).get("refs", {}).get("site") == site
                        and self.o[t["refs"]["circuit"]]["refs"]["type"] != "circuit-type/out-of-band"
                        and self.o[t["refs"]["circuit"]]["refs"]["provider"] != "provider/operator"}
            self.assertGreaterEqual(len(carriers), 2, site)

    # --- Customers bring their own LAN space; the carrier owns only the CE --

    def ce_only(self):
        return sorted(s["key"].removeprefix("site/") for s in self.of("site") if s["key"].startswith("site/ce-"))

    def test_customer_lans_are_customer_space_reused_across_vrfs(self):
        lans = [self.o[f"prefix/{sid}/lan"] for sid in self.ce_only()]
        self.assertEqual(len(lans), len(self.ce_only()))
        for prefix in lans:
            net = ipaddress.ip_network(prefix["attrs"]["prefix"])
            self.assertFalse(net.overlaps(ipaddress.ip_network(self.plan["recipe"]["address_pool"])))
            self.assertTrue(prefix["refs"]["vrf"].startswith("vrf/customer/"))
            self.assertEqual(prefix["refs"]["role"], "ip-role/customer")
            self.assertNotIn("vlan", prefix["refs"])
        # Several customers number from the same space, each inside its own VRF.
        by_prefix = defaultdict(set)
        for prefix in lans:
            by_prefix[prefix["attrs"]["prefix"]].add(prefix["refs"]["vrf"])
        self.assertTrue(any(len(vrfs) >= 3 for vrfs in by_prefix.values()))
        self.assertTrue(all(self.o[v]["attrs"]["enforce_unique"] for vrfs in by_prefix.values() for v in vrfs))
        # No carrier-pool segment, VLAN, DHCP/static range or desk wording for a LAN it does not run.
        for sid in self.ce_only():
            for key in (f"prefix/{sid}/clients", f"prefix/{sid}/reservation", f"vlan/{sid}/clients"):
                self.assertNotIn(key, self.o)
        self.assertFalse([r for r in self.of("ip_range") if "/ce-" in r["key"]])
        self.assertFalse([p for p in self.of("prefix") if "orkstation" in p["attrs"].get("description", "")])

    def test_ce_only_premises_hold_no_carrier_rack_or_power(self):
        for sid in self.ce_only():
            site = f"site/{sid}"
            edge = self.o[f"device/{sid}/edge-01"]
            self.assertNotIn("rack", edge["refs"])
            self.assertEqual(edge["refs"]["location"], f"location/{sid}")
            for kind in ("rack", "power_panel"):
                self.assertFalse([x for x in self.of(kind) if x["refs"].get("site") == site])
            self.assertFalse([d for d in self.of("device") if d["refs"]["site"] == site and d["key"] != edge["key"]])
            supplies = [p for p in self.of("power_port") if p["refs"]["device"] == edge["key"]]
            self.assertTrue(supplies and all(p["attrs"]["mark_connected"] for p in supplies))

    def test_customer_lan_counterexamples_are_refused(self):
        sid = self.ce_only()[0]

        def carrier_pool():
            self.o[f"prefix/{sid}/lan"]["attrs"]["prefix"] = "10.1.0.0/24"

        def vlan_back():
            self.o[f"device/{sid}/edge-01/if/port1"]["attrs"]["mode"] = "access"

        def racked():
            rack = next(r for r in self.of("rack"))
            self.o[f"device/{sid}/edge-01"]["refs"]["rack"] = rack["key"]

        def cabled_power():
            port = next(p for p in self.of("power_port") if p["refs"]["device"] == f"device/{sid}/edge-01")
            port["attrs"].pop("mark_connected")

        for mutate, code in ((carrier_pool, "provider-customer-lan"), (vlan_back, "provider-customer-lan"),
                             (racked, "provider-rack-placement"), (cabled_power, "provider-rack-placement")):
            with self.subTest(mutate.__name__):
                self.setUp()
                mutate()
                self.assertIn(code, self.codes())

    # --- Hubs are dual-homed to both PEs of their PoP -----------------------

    def test_hubs_take_two_circuits_into_both_pes(self):
        hubs = {t["refs"]["interface"].split("/")[1] for t in self.of("virtual_circuit_termination")
                if t["attrs"]["role"] == "hub"}
        self.assertEqual(len(hubs), len(self.plan["recipe"]["customers"]))
        cabled = {}
        for cable in self.of("cable"):
            cabled[cable["refs"]["a"]], cabled[cable["refs"]["b"]] = cable["refs"]["b"], cable["refs"]["a"]
        for sid in hubs:
            pes = set()
            for key in (f"circuit/customer/{sid}", f"circuit/customer/{sid}/b"):
                self.assertIn(key, self.o)
                pes.add(self.o[cabled[f"{key}/Z"]]["refs"]["device"])
                self.assertIn(f"bgp-session/{key.removeprefix('circuit/')}", self.o)
            self.assertEqual(len(pes), 2, sid)
            self.assertEqual(self.o[f"virtual-circuit-termination/{sid}/b"]["attrs"]["role"], "hub")
            tagged = "tag/dual-homed" in self.o[f"site/{sid}"]["refs"].get("tags", [])
            self.assertEqual(tagged, self.o[f"circuit/customer/{sid}"]["attrs"]["status"] == "active", sid)

    def test_hub_homing_counterexamples_are_refused(self):
        hub = next(t["refs"]["interface"].split("/")[1] for t in self.of("virtual_circuit_termination")
                   if t["attrs"]["role"] == "hub")
        cases = ((lambda: self.plan["objects"].remove(self.o[f"bgp-session/customer/{hub}/b"]), "provider-bgp-inventory"),
                 (lambda: self.o[f"virtual-circuit-termination/{hub}/b"]["attrs"].update(role="spoke"),
                  "provider-virtual-membership"))
        for mutate, code in cases:
            with self.subTest(code):
                self.setUp()
                mutate()
                self.assertIn(code, self.codes())

    # --- Junos addresses logical units, never bare ports -------------------

    def test_pe_addresses_live_on_unit_zero(self):
        pes = {d["key"] for d in self.of("device") if d["refs"]["role"] == "role/provider-edge"}
        for ip in self.of("ip_address"):
            port = self.o[ip["refs"]["assigned_object"]]
            if port.get("refs", {}).get("device") in pes:
                self.assertEqual(port["attrs"]["type"], "virtual", port["key"])
                self.assertTrue(port["attrs"]["name"].endswith(".0"), port["key"])
                parent = self.o[port["refs"]["parent"]]
                self.assertEqual(port["attrs"]["name"], parent["attrs"]["name"] + ".0")
                self.assertNotIn("vrf", parent["refs"])
        # Customer handoff units carry the customer VRF; BGP cites the unit address.
        session = self.o["bgp-session/customer/ce-lakeshore-health-cleveland-flats-001/b"]
        unit = self.o[self.o[session["refs"]["local_address"]]["refs"]["assigned_object"]]
        self.assertTrue(unit["attrs"]["name"].startswith("xe-0/1/") and unit["attrs"]["name"].endswith(".0"))
        self.assertEqual(unit["refs"]["vrf"], "vrf/customer/lakeshore-health")

    def test_bare_port_address_is_refused(self):
        unit = next(i for i in self.of("interface") if i["attrs"]["name"] == "et-0/0/1.0")
        ip = next(a for a in self.of("ip_address") if a["refs"]["assigned_object"] == unit["key"])
        ip["refs"]["assigned_object"] = unit["refs"]["parent"]
        self.assertIn("provider-routed-address", self.codes())

    def test_untruthful_dual_homed_tag_is_refused(self):
        site = next(s for s in self.of("site") if s["key"].startswith("site/pop-")
                    and "tag/dual-homed" not in s["refs"].get("tags", []))
        site["refs"]["tags"] = ["tag/dual-homed"]
        self.assertIn("provider-dual-homed", self.codes())


LIFECYCLE_RECIPE = {"profile": "provider-backbone", "customers": [
    dict(key="harbor-logistics", hub_pop="chicago-west", lan_endpoints=0, sites=[
        dict(pop="chicago-west"), dict(pop="detroit-south", status="planned"),
        dict(pop="cleveland-east", status="decommissioning")]),
    dict(key="maple-schools", hub_pop="detroit-south", status="planned", lan_endpoints=0,
         sites=[dict(pop="detroit-south"), dict(pop="cleveland-east")])]}


class LifecycleEquipmentTests(unittest.TestCase):
    """Nothing not yet in service looks installed; a withdrawing circuit names its disconnect."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = generate(LIFECYCLE_RECIPE)

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.o = {obj["key"]: obj for obj in self.plan["objects"]}

    def pe_port(self, sid):
        circuit = f"circuit/customer/{sid}/Z"
        return next(c["refs"]["a" if c["refs"]["b"] == circuit else "b"] for c in self.plan["objects"]
                    if c["kind"] == "cable" and circuit in (c["refs"]["a"], c["refs"]["b"]))

    def test_pending_handoffs_are_shut_and_their_optics_not_installed(self):
        self.assertEqual(validate(self.plan), [])
        for sid, optic in (("ce-maple-schools-detroit-south-001", "planned"),
                           ("ce-harbor-logistics-detroit-south-001", "staged"),
                           ("ce-harbor-logistics-chicago-west-001", "active")):
            with self.subTest(sid=sid):
                port = self.o[self.pe_port(sid)]
                self.assertEqual(port["attrs"]["enabled"], optic == "active")
                module = self.o[port["refs"]["module"]]
                self.assertEqual(module["attrs"]["status"], optic)
                self.assertEqual("serial" in module["attrs"], optic != "planned")
        # A planned CE has not shipped; a staged one carries its serial.
        self.assertNotIn("serial", self.o["device/ce-maple-schools-detroit-south-001/edge-01"]["attrs"])
        self.assertIn("serial", self.o["device/ce-harbor-logistics-detroit-south-001/edge-01"]["attrs"])

    def test_withdrawing_circuit_carries_its_disconnect(self):
        circuit = self.o["circuit/customer/ce-harbor-logistics-cleveland-east-001"]
        self.assertEqual(circuit["attrs"]["status"], "deprovisioning")
        self.assertGreater(circuit["attrs"]["termination_date"], self.plan["recipe"]["as_of"])
        journal = self.o["journal/circuit/customer/ce-harbor-logistics-cleveland-east-001/disconnect-order"]
        self.assertIn(circuit["attrs"]["termination_date"], journal["attrs"]["comments"])
        self.assertEqual(journal["attrs"]["kind"], "warning")

    def test_lifecycle_equipment_counterexamples_are_refused(self):
        planned, staged = "ce-maple-schools-detroit-south-001", "ce-harbor-logistics-detroit-south-001"

        def enabled():
            self.o[self.pe_port(planned)]["attrs"]["enabled"] = True

        def installed_optic():
            self.o[self.o[self.pe_port(planned)]["refs"]["module"]]["attrs"].update(status="active")

        def shipped_ce():
            self.o[f"device/{planned}/edge-01"]["attrs"]["serial"] = self.o[f"device/{staged}/edge-01"]["attrs"]["serial"]

        def no_disconnect():
            self.o["circuit/customer/ce-harbor-logistics-cleveland-east-001"]["attrs"].pop("termination_date")

        for mutate, code in ((enabled, "provider-port-mode"), (installed_optic, "provider-lifecycle-equipment"),
                             (shipped_ce, "provider-lifecycle-equipment"), (no_disconnect, "provider-lifecycle-equipment")):
            with self.subTest(mutate.__name__):
                self.setUp()
                mutate()
                self.assertIn(code, {finding["code"] for finding in validate(self.plan)})


if __name__ == "__main__":
    unittest.main()
