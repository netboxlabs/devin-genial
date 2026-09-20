"""Utility composition must follow substation demand and hold the zone line.

The zone assertions get the adversarial treatment: every way a station segment
could leak into the corporate tier that this grammar can express is mutated here
and must be reported. The explicit non-claims — no NERC CIP or electronic
security perimeter, no SCADA/EMS execution, no utility protocol, no protection
logic, no grid semantics, no physical security — are asserted against the actual
emitted records, not against the prose that denies them.
"""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import re
import tempfile
import unittest

from estates.generate import generate
from estates.load import main as load_main
from estates.model import DesignError, ROOT, canonical, hardware_catalog, recipe_from_file
from estates.report import markdown
from estates.validate import validate
from estates.__main__ import main


SMALL = dict(control_centers=2,
             substations=[dict(key="oakridge", kind="transmission", bays=3),
                          dict(key="stonebrook", kind="distribution", bays=2)])
SITE = "sub-oakridge"
OTHER = "sub-stonebrook"
OT_VLANS = (f"vlan/{SITE}/protection", f"vlan/{SITE}/telemetry", f"vlan/{SITE}/station")


def plan_for(**changes):
    return generate({"profile": "utility"} | changes)


def small(**changes):
    return plan_for(**(deepcopy(SMALL) | changes))


class UtilityResolverTests(unittest.TestCase):
    def test_unknown_and_out_of_range_requests_are_actionable(self):
        cases = [
            ({"plants": []}, "Unknown utility recipe fields: plants"),
            ({"schools": []}, "Unknown utility recipe fields: schools"),
            ({"customers": []}, "Unknown utility recipe fields: customers"),
            ({"headquarters_staff": 100}, "Unknown utility recipe fields: headquarters_staff"),
            ({"design_mix": {"modern": 1}}, "Unknown utility recipe fields: design_mix"),
            ({"acquired_sites": []}, "Unknown utility recipe fields: acquired_sites"),
            ({"wan_peak_mbps": 2000}, "Unknown utility recipe fields: wan_peak_mbps"),
            ({"substations": {}}, "substations must be a list"),
            ({"substations": []}, "substations must be a list"),
            ({"substations": [dict(key=f"s{n:02}") for n in range(25)]}, "substations must be a list"),
            ({"substations": [{"bays": 4}]}, "needs a stable key"),
            ({"substations": [dict(key="a", wireless={})]}, "only supported demand fields"),
            ({"substations": [dict(key="A-Corp")]}, "hyphen-separated lowercase identifiers"),
            ({"substations": [dict(key="acme-")]}, "no leading, trailing or doubled hyphen"),
            ({"substations": [dict(key="ac--me")]}, "no leading, trailing or doubled hyphen"),
            ({"substations": [dict(key="a"), dict(key="a")]}, "hyphen-separated lowercase identifiers"),
            ({"substations": [dict(key="a-b"), dict(key="ab")]}, "distinct without hyphens"),
            ({"substations": [dict(key="a", kind="collector")]}, "kind must be 'transmission' or 'distribution'"),
            ({"substations": [dict(key="a", kind="TRANSMISSION")]}, "kind must be 'transmission' or 'distribution'"),
            ({"substations": [dict(key="a", bays=1)]}, "bays must be an integer from 2 through 16"),
            ({"substations": [dict(key="a", bays=17)]}, "bays must be an integer from 2 through 16"),
            ({"substations": [dict(key="a", bays=4.0)]}, "bays must be an integer from 2 through 16"),
            ({"substations": [dict(key="a", bays=True)]}, "bays must be an integer from 2 through 16"),
            ({"control_centers": 0}, "control_centers must be 1"),
            ({"control_centers": 3}, "control_centers must be 1"),
            ({"control_centers": "two"}, "control_centers must be 1"),
            ({"control_centers": 2.0}, "control_centers must be 1"),
            ({"address_pool": "10.0.0.0/21"}, "address_pool must be an aligned RFC1918"),
            ({"address_pool": "10.1.0.0/18"}, "must hold /16 site reservations"),
            ({"reservation_user": "someone"}, "rack-user reservations are not implemented"),
            ({"demo": "provider-span-maintenance"}, "demo supports baseline"),
            ({"namespace": "X"}, "namespace must be a 2"),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(DesignError, message):
                plan_for(**changes)

    def test_supported_bounds_are_accepted_at_their_edges(self):
        widest = plan_for(control_centers=2,
                          substations=[dict(key="edge-yard", kind="transmission", bays=16)])
        self.assertEqual(validate(widest), [])
        self.assertEqual(len([o for o in widest["objects"] if o["kind"] == "site"]), 3)
        narrowest = plan_for(control_centers=1,
                             substations=[dict(key="one", kind="distribution", bays=2)])
        self.assertEqual(validate(narrowest), [])
        self.assertEqual(len([o for o in narrowest["objects"] if o["kind"] == "site"]), 2)

    def test_the_widest_substation_still_fits_the_widest_reserve(self):
        # The two access zones share one 38-switch attachment budget; the
        # reviewed bounds must remain inside it at every supported reserve.
        for reserve in (0.1, 0.2, 0.4):
            with self.subTest(reserve=reserve):
                plan = plan_for(reserve_fraction=reserve,
                                substations=[dict(key="edge-yard", kind="transmission", bays=16)])
                self.assertEqual(validate(plan), [])
                switches = [o for o in plan["objects"] if o["kind"] == "device"
                            and o["refs"].get("role") == "role/access"
                            and o["refs"]["site"] == "site/sub-edge-yard"]
                self.assertLessEqual(len(switches), 38)

    def test_the_widest_fleet_generates_and_validates(self):
        plan = plan_for(substations=[dict(key=f"yard-{n:02}", kind="transmission", bays=16)
                                     for n in range(24)])
        self.assertEqual(validate(plan), [])
        self.assertEqual(len([o for o in plan["objects"] if o["kind"] == "site"]), 26)

    def test_defaults_are_frozen_into_the_recipe(self):
        recipe = plan_for()["recipe"]
        self.assertEqual(recipe["profile"], "utility")
        self.assertEqual(recipe["namespace"], "northgate")
        self.assertEqual(recipe["name"], "Northgate Power and Light")
        self.assertEqual(recipe["demo"], "baseline")
        self.assertEqual(recipe["control_centers"], 2)
        self.assertEqual([item["key"] for item in recipe["substations"]],
                         ["fairhaven", "milldam", "oakridge", "stonebrook"])
        oakridge = next(item for item in recipe["substations"] if item["key"] == "oakridge")
        self.assertEqual((oakridge["kind"], oakridge["bays"]), ("transmission", 10))

    def test_omitted_substation_fields_take_the_authored_defaults(self):
        recipe = plan_for(substations=[dict(key="only")])["recipe"]
        self.assertEqual(recipe["plants" if False else "substations"][0],
                         dict(key="only", kind="distribution", bays=6))

    def test_shipped_profile_resolves_and_validates(self):
        plan = generate(recipe_from_file(ROOT / "profiles/utility.toml"))
        self.assertEqual(validate(plan), [])
        self.assertEqual(len(plan["recipe"]["substations"]), 4)
        self.assertEqual(len([o for o in plan["objects"] if o["kind"] == "site"]), 6)

    def test_substation_kind_selects_the_authored_corporate_presence_and_peak(self):
        plan = small()
        contracts = {c["site"]: c for c in plan["contracts"]}
        transmission = contracts[f"site/{SITE}"]
        distribution = contracts[f"site/{OTHER}"]
        self.assertEqual(transmission["demand"]["workstations"], 2)
        self.assertEqual(distribution["demand"]["workstations"], 1)
        self.assertEqual(transmission["demand"]["peak_mbps"], 20 + 3*3 + 2*2)
        self.assertEqual(distribution["demand"]["peak_mbps"], 10 + 3*2 + 2*1)
        self.assertEqual(transmission["substation_kind"], "transmission")


class UtilityCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = small()

    def objects(self, plan=None):
        return {o["key"]: o for o in (plan or self.plan)["objects"]}

    def test_utility_estate_connects_its_own_demand_without_other_industries(self):
        self.assertEqual(validate(self.plan), [])
        self.assertEqual(canonical(generate(deepcopy(self.plan["recipe"]))["objects"]),
                         canonical(self.plan["objects"]))
        objects = self.objects()
        self.assertEqual({key for key, obj in objects.items() if obj["kind"] == "site"},
                         {"site/dc-01", "site/dc-02", f"site/{SITE}", f"site/{OTHER}"})
        services = {key.split("/")[2] for key, obj in objects.items() if obj["kind"] == "virtual_machine"}
        self.assertEqual(services, {"scada-front-end", "historian", "ems-gateway",
                                    "monitoring", "dns", "backup"})
        blob = canonical(self.plan).decode()
        for forbidden in ("teller", "classroom", "patient", "point-of-sale", "lecture hall",
                          "residence room", "ATM", "managed-network contract", "production line",
                          "loading dock"):
            self.assertNotIn(forbidden, blob, forbidden)
        for required in ("Switchyard bay", "Control room", "conduit", "Substation"):
            self.assertIn(required, blob, required)

    def test_single_control_center_builds_one_operations_site(self):
        plan = small(control_centers=1)
        self.assertEqual(validate(plan), [])
        objects = self.objects(plan)
        self.assertNotIn("site/dc-02", objects)
        self.assertIn("site/dc-01", objects)
        self.assertNotIn("vm/dc-02/historian/001", objects)

    def test_both_control_centers_carry_the_same_service_families(self):
        objects = self.objects()
        for site in ("dc-01", "dc-02"):
            services = {key.split("/")[2] for key, obj in objects.items()
                        if obj["kind"] == "virtual_machine" and key.startswith(f"vm/{site}/")}
            self.assertEqual(services, {"scada-front-end", "historian", "ems-gateway",
                                        "monitoring", "dns", "backup"})

    def test_no_failover_or_transfer_is_claimed_for_the_backup_center(self):
        denials = {text for contract in self.plan["contracts"] for text in contract["assumptions"]}
        self.assertTrue(any("a second control center is inventory, not a demonstrated failover" in text
                            for text in denials))
        self.assertTrue(any("control-center transfer" in text for text in denials))

    def test_no_utility_protocol_or_compliance_claim_appears_in_a_record(self):
        # Protocol and compliance words exist in exactly one place: the contract
        # assumptions that deny them. No emitted record may name one, and no
        # service may listen on a well-known utility or industrial control port.
        forbidden = ("dnp3", "61850", "modbus", "iccp", "60870", "purdue", "62443",
                     "air gap", "air-gapped", "electronic security perimeter",
                     "one-line", "power flow", "power-flow", "setpoint", "trip scheme",
                     "critical infrastructure")
        words = (r"\bnerc\b", r"\bcip\b", r"\besp\b")
        for obj in self.plan["objects"]:
            for field, value in obj["attrs"].items():
                if not isinstance(value, str):
                    continue
                lowered = value.lower()
                for term in forbidden:
                    self.assertNotIn(term, lowered, (obj["key"], field, term))
                for pattern in words:
                    self.assertIsNone(re.search(pattern, lowered), (obj["key"], field, pattern))
        control_ports = {102, 502, 2222, 2404, 4840, 20000, 34962, 34963, 34964, 44818, 47808}
        for obj in self.plan["objects"]:
            if obj["kind"] == "service":
                self.assertEqual(set(obj["attrs"]["ports"]) & control_ports, set(), obj["key"])

    def test_every_explicit_non_claim_is_written_into_the_contracts(self):
        denials = {text for contract in self.plan["contracts"] for text in contract["assumptions"]}
        for phrase in ("No utility protocol is configured, carried or asserted",
                       "NERC CIP compliance state",
                       "electronic security perimeter",
                       "no firewall policy, access control list, route filter, data diode or air gap exists",
                       "They are not voltage classes",
                       "No grid",
                       "no fence, gate, door controller, badge reader, intrusion detection, camera",
                       "No control function, protection logic, relay setting",
                       "No wireless coverage of any kind is modeled",
                       "documentation inventory for a fictional operator",
                       "without any carrier diversity promise",
                       "telemetry point, measurement, tag, sample, control action, setpoint"):
            self.assertTrue(any(phrase in text for text in denials), phrase)

    def test_substation_places_bays_and_the_control_room_in_their_own_rooms(self):
        objects = self.objects()
        self.assertEqual(objects[f"device/{SITE}/rtu-01"]["refs"]["location"], f"location/{SITE}/bay-01")
        self.assertEqual(objects[f"device/{SITE}/relay-03"]["refs"]["location"], f"location/{SITE}/bay-03")
        for label in ("hmi-01", "hmi-02", "gateway-01", "desk-01", "desk-02"):
            self.assertEqual(objects[f"device/{SITE}/{label}"]["refs"]["location"],
                             f"location/{SITE}/control-room")
        for role in ("management", "office", "protection", "telemetry", "station", "conduit"):
            prefix = objects[f"prefix/{SITE}/{role}"]
            self.assertEqual(prefix["attrs"]["prefix"].split("/")[1], "24")
            self.assertEqual(prefix["refs"]["scope_site"], f"site/{SITE}")
        self.assertNotIn("prefix/dc-01/protection", objects)

    def test_authored_station_density_is_one_rtu_and_one_relay_per_bay(self):
        objects = self.objects()
        roles = {}
        for key, obj in objects.items():
            if obj["kind"] == "device" and obj["refs"].get("site") == f"site/{SITE}":
                roles.setdefault(obj["refs"]["role"], []).append(key)
        self.assertEqual(len(roles["role/rtu"]), 3)
        self.assertEqual(len(roles["role/protection-relay"]), 3)
        self.assertEqual(len(roles["role/hmi"]), 2)
        self.assertEqual(len(roles["role/station-gateway"]), 1)
        self.assertEqual(len(roles["role/workstation"]), 2)
        self.assertEqual(objects[f"device/{SITE}/rtu-01"]["meta"]["network"], "telemetry")
        self.assertEqual(objects[f"device/{SITE}/relay-01"]["meta"]["network"], "protection")
        self.assertEqual(objects[f"device/{SITE}/hmi-01"]["meta"]["network"], "station")
        self.assertEqual(objects[f"device/{SITE}/gateway-01"]["meta"]["network"], "telemetry")
        self.assertEqual(objects[f"device/{SITE}/desk-01"]["meta"]["network"], "office")

    def test_station_endpoint_records_deny_control_function_and_protocols(self):
        objects = self.objects()
        for key in (f"device/{SITE}/rtu-01", f"device/{SITE}/relay-02",
                    f"device/{SITE}/hmi-01", f"device/{SITE}/gateway-01"):
            description = objects[key]["attrs"]["description"]
            self.assertIn("Reference", description)
            self.assertIn("no telemetry point, protection setting, control action or utility protocol",
                          description)
        room = objects[f"location/{SITE}/bay-01"]["attrs"]["description"]
        self.assertIn("no electrical rating, protection setting or control function is configured", room)

    def test_substation_uses_only_catalog_reference_hardware(self):
        aliases = {o["meta"]["hardware"] for o in self.plan["objects"] if o["kind"] == "device"}
        self.assertTrue(aliases <= set(hardware_catalog()["models"]))
        self.assertEqual(aliases & {"atm", "inherited-access", "liquid-blade", "liquid-chassis",
                                    "ap", "ap-aruba"}, set())
        for key in (f"device/{SITE}/rtu-01", f"device/{SITE}/relay-01", f"device/{SITE}/hmi-01",
                    f"device/{SITE}/gateway-01", f"device/{SITE}/desk-01"):
            self.assertEqual(self.objects()[key]["meta"]["hardware"], "endpoint")
        roles = {o["refs"]["role"] for o in self.plan["objects"] if o["kind"] == "device"}
        self.assertEqual(roles & {"role/pos-terminal", "role/medical-device", "role/imaging-device",
                                  "role/atm", "role/provider-edge", "role/ap", "role/camera",
                                  "role/plc", "role/field-device"}, set())

    def test_no_wireless_or_physical_security_record_exists_anywhere(self):
        kinds = {o["kind"] for o in self.plan["objects"]}
        for kind in ("wireless_lan", "wireless_lan_group", "wireless_link"):
            self.assertNotIn(kind, kinds, kind)
        objects = self.objects()
        self.assertFalse([k for k, o in objects.items() if o["kind"] == "interface"
                          and o["attrs"].get("rf_channel")])
        self.assertFalse([k for k, o in objects.items() if o["kind"] == "device"
                          and o["refs"].get("role") in {"role/ap", "role/camera"}])
        self.assertFalse([k for k, o in objects.items() if o["kind"] == "vlan"
                          and k.endswith("/wireless") or o["kind"] == "vlan" and k.endswith("/security")])

    def test_substation_segments_hold_their_own_gateway_tiers(self):
        objects = self.objects()
        for network, vid in (("protection", 30), ("telemetry", 40), ("station", 50)):
            self.assertEqual(objects[f"vlan/{SITE}/{network}"]["attrs"]["vid"], vid)
            for side in ("a", "b"):
                svi = f"device/{SITE}/ot-dist-{side}/if/Vlan{vid}"
                self.assertEqual(objects[svi]["refs"]["vrf"], f"vrf/{network}")
                self.assertEqual(objects[f"ip/{svi}"]["attrs"]["status"], "active")
        for side in ("a", "b"):
            self.assertIn(f"device/{SITE}/dist-{side}/if/Vlan20", objects)
            self.assertNotIn(f"device/{SITE}/dist-{side}/if/Vlan30", objects)

    def test_each_substation_attaches_to_both_declared_carriers(self):
        objects = self.objects()
        for sid in (SITE, OTHER):
            for side in ("a", "b"):
                circuit = objects[f"circuit/{sid}/{side}/1"]
                self.assertEqual(circuit["refs"]["provider"], f"provider/{side}")
                self.assertEqual(circuit["attrs"]["status"], "active")
                self.assertEqual(circuit["meta"]["procurement"]["cohort"], "substation-standard")

    def test_growth_appends_substations_and_bays_without_moving_anything(self):
        before = small()
        recipe = deepcopy(before["recipe"])
        next(item for item in recipe["substations"] if item["key"] == "oakridge")["bays"] = 7
        recipe["substations"].append(dict(key="brightwater", kind="distribution", bays=5))
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        old = {o["key"]: o for o in before["objects"]}
        new = {o["key"]: o for o in after["objects"]}
        self.assertTrue(old.keys() <= new.keys())
        occupied = {end for o in before["objects"] if o["kind"] == "cable" for end in o["refs"].values()}
        for key, obj in old.items():
            if obj["kind"] == "power_port":
                self.assertGreaterEqual(new[key]["attrs"].get("maximum_draw", 0),
                                        obj["attrs"].get("maximum_draw", 0), key)
                self.assertEqual(new[key]["refs"], obj["refs"], key)
            elif obj["kind"] == "location":
                self.assertEqual(new[key]["attrs"], obj["attrs"], key)
                self.assertEqual(new[key]["refs"], obj["refs"], key)
                for field in ("space_type", "floor", "position_m"):
                    self.assertEqual(new[key]["meta"].get(field), obj["meta"].get(field), key)
            elif obj["kind"] in {"site", "rack", "cable", "ip_address", "vlan", "prefix",
                                 "virtual_machine", "tenant", "vrf"} or key in occupied:
                self.assertEqual(new[key], obj, key)
            if obj["kind"] == "device":
                for field in ("location", "rack", "site", "tenant"):
                    self.assertEqual(new[key]["refs"].get(field), obj["refs"].get(field), key)
        for scope, items in before["reservations"].items():
            for key, slot in items.items():
                self.assertEqual(after["reservations"][scope][key], slot, (scope, key))

    def test_a_new_bay_never_renumbers_an_existing_bay(self):
        before = small()
        old = {o["key"]: o for o in before["objects"]}
        recipe = deepcopy(before["recipe"])
        next(item for item in recipe["substations"] if item["key"] == "oakridge")["bays"] = 9
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        new = {o["key"]: o for o in after["objects"]}
        for room in (f"location/{SITE}/bay-01", f"location/{SITE}/bay-03",
                     f"location/{SITE}/control-room"):
            self.assertEqual(new[room]["meta"]["position_m"], old[room]["meta"]["position_m"], room)
        for device in (f"device/{SITE}/rtu-01", f"device/{SITE}/relay-01", f"device/{SITE}/hmi-01"):
            self.assertEqual(new[device]["meta"]["placement"], old[device]["meta"]["placement"], device)
        self.assertEqual(after["reservations"][f"substation-bays/{SITE}"]["bay-01"], 0)
        self.assertEqual(len(after["reservations"][f"substation-bays/{SITE}"]), 9)

    def test_both_zone_port_ledgers_survive_growth_independently(self):
        before = small()
        recipe = deepcopy(before["recipe"])
        next(item for item in recipe["substations"] if item["key"] == "oakridge")["bays"] = 6
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        for scope, items in before["reservations"].items():
            if scope.startswith("access-endpoints/"):
                for key, slot in items.items():
                    self.assertEqual(after["reservations"][scope][key], slot, (scope, key))
        self.assertIn(f"access-endpoints/{SITE}/location/{SITE}/ot", after["reservations"])
        self.assertIn(f"access-endpoints/{SITE}/location/{SITE}", after["reservations"])

    def test_appending_a_backup_control_center_is_growth(self):
        before = small(control_centers=1)
        recipe = deepcopy(before["recipe"])
        recipe["control_centers"] = 2
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        old = {o["key"]: o for o in before["objects"]}
        new = {o["key"]: o for o in after["objects"]}
        self.assertTrue(old.keys() <= new.keys())
        self.assertIn("site/dc-02", new)

    def test_reductions_kind_changes_and_profile_changes_need_a_new_baseline(self):
        before = small()

        def entry(recipe, key):
            return next(item for item in recipe["substations"] if item["key"] == key)

        generate(deepcopy(before["recipe"]), previous=before)
        cases = [
            ("dropped substation", lambda r: r["substations"].remove(entry(r, "stonebrook"))),
            ("fewer bays", lambda r: entry(r, "oakridge").__setitem__("bays", 2)),
            ("changed kind", lambda r: entry(r, "oakridge").__setitem__("kind", "distribution")),
            ("dropped control center", lambda r: r.__setitem__("control_centers", 1)),
        ]
        for label, change in cases:
            recipe = deepcopy(before["recipe"])
            change(recipe)
            with self.subTest(label=label), self.assertRaisesRegex(DesignError, "new baseline"):
                generate(recipe, previous=before)
        with self.assertRaisesRegex(DesignError, "new estate|new baseline"):
            generate(deepcopy(before["recipe"]) | {"namespace": "other"}, previous=before)

    def test_damaged_previous_ledger_is_rejected_before_allocation(self):
        before = small()
        damaged = deepcopy(before)
        damaged["objects"] = [o for o in damaged["objects"] if o["key"] != f"device/{SITE}/rtu-01"]
        with self.assertRaisesRegex(DesignError, "does not reproduce"):
            generate(deepcopy(before["recipe"]), previous=damaged)

    def test_report_explains_the_zones_and_their_limits(self):
        guide = markdown(self.plan)
        for phrase in ("Substation zones", "Remote terminal units", "Protection relays",
                       "Conduit trunks", "Replica group placement"):
            self.assertIn(phrase, guide)
        for phrase in ("no firewall policy", "NERC CIP state", "no utility protocol",
                       "not voltage classes", "no SCADA or EMS function is executed"):
            self.assertIn(phrase, guide.replace("\n", " "))


class UtilityZoneTests(unittest.TestCase):
    """The built graph must hold the station line before anyone mutates it."""

    @classmethod
    def setUpClass(cls):
        cls.plan = small()
        cls.objects = {o["key"]: o for o in cls.plan["objects"]}

    def carried(self, port):
        refs = self.objects[port]["refs"]
        carried = set(refs.get("tagged_vlans", []))
        if refs.get("untagged_vlan"):
            carried.add(refs["untagged_vlan"])
        return carried

    def carriers(self, vlan):
        return {obj["refs"]["device"] for key, obj in self.objects.items()
                if obj["kind"] == "interface" and vlan in self.carried(key)}

    def test_station_segments_live_only_in_their_own_zone(self):
        infrastructure = {f"device/{SITE}/ot-access-01", f"device/{SITE}/ot-access-02",
                          f"device/{SITE}/ot-dist-a", f"device/{SITE}/ot-dist-b"}
        for vlan, endpoints in (
                (f"vlan/{SITE}/protection", {f"device/{SITE}/relay-{n+1:02}" for n in range(3)}),
                (f"vlan/{SITE}/telemetry", {f"device/{SITE}/rtu-{n+1:02}" for n in range(3)} |
                 {f"device/{SITE}/gateway-01"}),
                (f"vlan/{SITE}/station", {f"device/{SITE}/hmi-{n+1:02}" for n in range(2)})):
            with self.subTest(vlan=vlan):
                self.assertEqual(self.carriers(vlan), infrastructure | endpoints)

    def test_no_station_segment_touches_a_carrier_edge_or_corporate_switch(self):
        corporate = {f"device/{SITE}/edge-a", f"device/{SITE}/edge-b", f"device/{SITE}/dist-a",
                     f"device/{SITE}/dist-b", f"device/{SITE}/access-01", f"device/{SITE}/access-02",
                     f"device/{SITE}/mgmt-01"}
        for vlan in OT_VLANS:
            self.assertEqual(self.carriers(vlan) & corporate, set(), vlan)

    def test_no_circuit_is_reachable_from_the_station_distribution_pair(self):
        cables = {}
        for obj in self.plan["objects"]:
            if obj["kind"] == "cable":
                cables[obj["refs"]["a"]] = obj["refs"]["b"]
                cables[obj["refs"]["b"]] = obj["refs"]["a"]
        for device in (f"device/{SITE}/ot-dist-a", f"device/{SITE}/ot-dist-b"):
            for key, obj in self.objects.items():
                if obj["kind"] != "interface" or obj["refs"].get("device") != device:
                    continue
                peer = cables.get(key)
                if peer is None:
                    continue
                self.assertEqual(self.objects[peer]["kind"], "interface", key)
                owner = self.objects[peer]["refs"]["device"]
                self.assertIn(self.objects[owner]["refs"]["role"],
                              {"role/distribution", "role/access", "role/management"}, key)

    def test_the_conduit_joins_only_the_four_distribution_gateways(self):
        conduit = f"vlan/{SITE}/conduit"
        self.assertEqual(self.carriers(conduit),
                         {f"device/{SITE}/dist-a", f"device/{SITE}/dist-b",
                          f"device/{SITE}/ot-dist-a", f"device/{SITE}/ot-dist-b"})
        gateways = [key for key, obj in self.objects.items()
                    if obj["kind"] == "interface" and obj["attrs"].get("type") == "virtual"
                    and obj["refs"].get("untagged_vlan") == conduit]
        self.assertEqual(len(gateways), 4)
        addresses = sorted(self.objects[f"ip/{key}"]["attrs"]["address"] for key in gateways)
        self.assertEqual(len(set(addresses)), 4)

    def test_every_inter_tier_trunk_carries_the_conduit_and_nothing_else(self):
        links = []
        for obj in self.plan["objects"]:
            if obj["kind"] != "cable":
                continue
            ends = [obj["refs"][side] for side in ("a", "b")]
            owners = [self.objects[end]["refs"].get("device") for end in ends
                      if self.objects[end]["kind"] == "interface"]
            if len(owners) != 2 or not all(owner.startswith(f"device/{SITE}/") for owner in owners):
                continue
            tiers = {"/ot-dist-" in owner for owner in owners}
            if tiers == {True, False} and all("dist-" in owner for owner in owners):
                links.append(ends)
        self.assertEqual(len(links), 4)
        for ends in links:
            for end in ends:
                self.assertEqual(self.carried(end), {f"vlan/{SITE}/conduit"}, end)

    def test_station_equipment_carries_management_only_on_its_management_port(self):
        management = f"vlan/{SITE}/management"
        for device in (f"device/{SITE}/ot-dist-a", f"device/{SITE}/ot-access-01"):
            ports = [key for key, obj in self.objects.items()
                     if obj["kind"] == "interface" and obj["refs"].get("device") == device
                     and management in self.carried(key)]
            self.assertEqual(len(ports), 1, device)
            self.assertTrue(self.objects[ports[0]]["attrs"].get("mgmt_only"), ports[0])

    def test_station_segments_hold_their_own_routing_contexts(self):
        for network in ("protection", "telemetry", "station"):
            vrf = f"vrf/{network}"
            self.assertEqual(self.objects[vrf]["kind"], "vrf")
            self.assertTrue(self.objects[vrf]["attrs"]["enforce_unique"])
            users = {self.objects[key]["refs"].get("scope_site") for key, obj in self.objects.items()
                     if obj["kind"] == "prefix" and obj["refs"].get("vrf") == vrf
                     and obj["attrs"].get("status") == "active"}
            self.assertEqual(users, {f"site/{SITE}", f"site/{OTHER}"})
        self.assertNotEqual(self.objects[f"prefix/{SITE}/telemetry"]["refs"]["vrf"],
                            self.objects[f"prefix/{SITE}/office"]["refs"]["vrf"])

    def test_the_two_access_zones_never_share_a_switch(self):
        corporate, station = set(), set()
        cables = {}
        for obj in self.plan["objects"]:
            if obj["kind"] == "cable":
                cables[obj["refs"]["a"]] = obj["refs"]["b"]
                cables[obj["refs"]["b"]] = obj["refs"]["a"]
        for key, obj in self.objects.items():
            if obj["kind"] != "device" or not obj["meta"].get("endpoint") or obj["refs"]["site"] != f"site/{SITE}":
                continue
            peer = cables.get(f"{key}/if/eth0")
            switch = self.objects[peer]["refs"]["device"]
            (station if obj["meta"]["network"] in {"protection", "telemetry", "station"}
             else corporate).add(switch)
        self.assertTrue(all("/ot-access-" in switch for switch in station), station)
        self.assertTrue(all("/ot-access-" not in switch for switch in corporate), corporate)
        self.assertEqual(corporate & station, set())

    def test_no_service_binds_a_station_address(self):
        station_vrfs = {"vrf/protection", "vrf/telemetry", "vrf/station"}
        station_addresses = {key for key, obj in self.objects.items()
                             if obj["kind"] == "ip_address" and obj["refs"].get("vrf") in station_vrfs}
        self.assertTrue(station_addresses)
        for key, obj in self.objects.items():
            if obj["kind"] != "service":
                continue
            bound = set(obj["refs"].get("ip_addresses", []))
            self.assertEqual(bound & station_addresses, set(), key)

    def test_no_ipv6_companion_record_crosses_the_zone_boundary(self):
        plan = small(ipv6_pool="2001:db8::/32")
        self.assertEqual(validate(plan), [])
        objects = {o["key"]: o for o in plan["objects"]}
        ot_devices = {f"device/{SITE}/ot-dist-a", f"device/{SITE}/ot-dist-b",
                      f"device/{SITE}/ot-access-01", f"device/{SITE}/ot-access-02"}
        ot_devices |= {f"device/{SITE}/rtu-{n+1:02}" for n in range(3)}
        ot_devices |= {f"device/{SITE}/relay-{n+1:02}" for n in range(3)}
        ot_devices |= {f"device/{SITE}/hmi-{n+1:02}" for n in range(2)} | {f"device/{SITE}/gateway-01"}
        for key, obj in objects.items():
            if obj["kind"] != "prefix" or not key.startswith("ipv6/"):
                continue
            for network in ("protection", "telemetry", "station"):
                if key.endswith(f"/{network}"):
                    self.assertIn(obj["refs"].get("scope_site"), {f"site/{SITE}", f"site/{OTHER}"}, key)
        for network in ("protection", "telemetry", "station"):
            self.assertIn(f"ipv6/prefix/{SITE}/{network}", objects)
        self.assertIn(f"ipv6/ip/device/{SITE}/rtu-01/if/eth0", objects)


class UtilityNamingTests(unittest.TestCase):
    def test_site_names_are_unique_and_industry_flavoured(self):
        plan = plan_for()
        sites = [o for o in plan["objects"] if o["kind"] == "site"]
        names = [o["attrs"]["name"] for o in sites]
        self.assertEqual(len(set(names)), len(names))
        for site in [o for o in sites if o["key"].startswith("site/sub-")]:
            self.assertTrue(site["attrs"]["name"].endswith(" Substation"), site["attrs"]["name"])
            self.assertTrue(site["attrs"]["facility"])
        for site in [o for o in sites if o["key"].startswith("site/dc-")]:
            self.assertTrue(site["attrs"]["name"].endswith(" Control Center"), site["attrs"]["name"])
        self.assertIn("Oakridge Substation", names)
        self.assertEqual(len({o["attrs"]["physical_address"] for o in sites}), len(sites))

    def test_site_names_override_and_legacy_naming_stay_available(self):
        override = small(site_names={SITE: {"name": "Oakridge Switching Station", "facility": "OAK-1"}})
        self.assertEqual(validate(override), [])
        site = next(o for o in override["objects"] if o["key"] == f"site/{SITE}")
        self.assertEqual(site["attrs"]["name"], "Oakridge Switching Station")
        self.assertEqual(site["attrs"]["facility"], "OAK-1")
        legacy = small(naming="legacy")
        self.assertEqual(validate(legacy), [])
        site = next(o for o in legacy["objects"] if o["key"] == f"site/{SITE}")
        self.assertEqual(site["attrs"]["name"], f"northgate-{SITE}")
        with self.assertRaisesRegex(DesignError, "unknown site ids"):
            small(site_names={"sub-nowhere": {"name": "Nowhere"}})


def _extra_room(plan, objects):
    plan["objects"].append({"key": f"location/{SITE}/annex", "kind": "location",
                            "attrs": {"name": "Annex", "slug": f"northgate-{SITE}-annex", "status": "active"},
                            "refs": {"site": f"site/{SITE}", "tenant": "tenant",
                                     "parent": f"location/{SITE}/floor-01"},
                            "meta": {"space_type": "switchyard_bay", "floor": 1, "position_m": [40, 40, 0]}})


def _extra_switch(plan, objects):
    plan["objects"].append(dict(objects[f"device/{SITE}/ot-access-01"],
                                key=f"device/{SITE}/ot-access-09",
                                attrs=dict(objects[f"device/{SITE}/ot-access-01"]["attrs"],
                                           name="suboakridge-ot-as09", position=25)))


def _leak_to_corporate_access(plan, objects):
    objects[f"device/{SITE}/access-01/if/TenGigabitEthernet1/1/1"]["refs"]["tagged_vlans"].append(
        f"vlan/{SITE}/protection")


def _leak_to_carrier_edge(plan, objects):
    objects[f"device/{SITE}/edge-a/if/x1"]["refs"]["tagged_vlans"].append(f"vlan/{SITE}/station")


def _leak_untagged(plan, objects):
    objects[f"device/{SITE}/access-02/if/TenGigabitEthernet1/1/2"]["refs"]["untagged_vlan"] = \
        f"vlan/{SITE}/telemetry"


def _leak_to_another_substation(plan, objects):
    objects[f"device/{OTHER}/access-01/if/TenGigabitEthernet1/1/3"]["refs"]["tagged_vlans"] = \
        [f"vlan/{SITE}/protection"]


def _widen_the_conduit(plan, objects):
    for side in ("a", "b"):
        objects[f"device/{SITE}/ot-dist-{side}/if/Ethernet50/1"]["refs"]["tagged_vlans"] = \
            [f"vlan/{SITE}/conduit", f"vlan/{SITE}/office"]
        objects[f"device/{SITE}/dist-{side}/if/Ethernet50/1"]["refs"]["tagged_vlans"] = \
            [f"vlan/{SITE}/conduit", f"vlan/{SITE}/office"]


def _cut_the_conduit(plan, objects):
    ends = {f"device/{SITE}/ot-dist-a/if/Ethernet50/1", f"device/{SITE}/dist-a/if/Ethernet50/1"}
    plan["objects"] = [o for o in plan["objects"]
                       if not (o["kind"] == "cable" and set(o["refs"].values()) & ends)]


def _conduit_on_an_access_switch(plan, objects):
    objects[f"device/{SITE}/access-01/if/TenGigabitEthernet1/1/1"]["refs"]["tagged_vlans"].append(
        f"vlan/{SITE}/conduit")


class UtilityValidatorTests(unittest.TestCase):
    """Every independent utility assertion must reject a matching mutation."""

    @classmethod
    def setUpClass(cls):
        cls.baseline = small()
        assert validate(cls.baseline) == []

    def mutated(self, change):
        plan = deepcopy(self.baseline)
        objects = {o["key"]: o for o in plan["objects"]}
        change(plan, objects)
        return {finding["code"] for finding in validate(plan)}

    def test_recipe_bounds_are_restated_independently(self):
        for change in (lambda plan, objects: plan["recipe"].__setitem__("substations", []),
                       lambda plan, objects: plan["recipe"]["substations"][0].__setitem__("bays", 99),
                       lambda plan, objects: plan["recipe"]["substations"][0].__setitem__("bays", 1),
                       lambda plan, objects: plan["recipe"]["substations"][0].__setitem__("kind", "collector"),
                       lambda plan, objects: plan["recipe"]["substations"][0].__setitem__("key", "Bad Key"),
                       lambda plan, objects: plan["recipe"].__setitem__("control_centers", 0),
                       lambda plan, objects: plan["recipe"].__setitem__("control_centers", 5),
                       lambda plan, objects: plan["recipe"].__setitem__("reserve_fraction", 0.9)):
            self.assertIn("utl-recipe", self.mutated(change))
        self.assertIn("utl-recipe", self.mutated(
            lambda plan, objects: plan["recipe"].__setitem__("wan_tiers_mbps", [100, 50])))

    def test_missing_or_extra_sites_are_reported(self):
        def add_station(plan, objects):
            plan["recipe"]["substations"].append(dict(key="ghost", kind="distribution", bays=2))
        self.assertIn("utl-site-inventory", self.mutated(add_station))

    def test_a_dropped_control_center_site_is_reported(self):
        self.assertIn("utl-site-inventory", self.mutated(
            lambda plan, objects: plan.__setitem__(
                "objects", [o for o in plan["objects"] if o["key"] != "site/dc-02"])))

    def test_a_station_segment_on_a_corporate_switch_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(_leak_to_corporate_access))

    def test_a_station_segment_on_a_carrier_edge_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(_leak_to_carrier_edge))

    def test_an_untagged_station_leak_is_reported_like_a_tagged_one(self):
        self.assertIn("utl-zone-isolation", self.mutated(_leak_untagged))

    def test_a_station_segment_named_at_another_substation_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(_leak_to_another_substation))

    def test_a_station_switch_carrying_a_corporate_segment_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-access-01/if/TenGigabitEthernet1/1/1"]
            ["refs"]["tagged_vlans"].append(f"vlan/{SITE}/office")))

    def test_the_conduit_appearing_on_an_access_switch_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(_conduit_on_an_access_switch))

    def test_a_removed_station_segment_trunk_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-dist-a/if/Vlan30"]["refs"].__setitem__(
                "untagged_vlan", f"vlan/{SITE}/office")))

    def test_a_direct_cable_from_the_station_zone_to_a_corporate_switch_is_reported(self):
        def bridge(plan, objects):
            template = next(o for o in plan["objects"] if o["kind"] == "cable"
                            and o["attrs"].get("type") == "smf")
            plan["objects"].append(dict(template, key="cable/cross-zone", refs={
                "a": f"device/{SITE}/ot-access-01/if/TenGigabitEthernet1/1/3",
                "b": f"device/{SITE}/access-01/if/TenGigabitEthernet1/1/3"}))
        self.assertIn("utl-zone-isolation", self.mutated(bridge))

    def test_a_station_gateway_borrowing_a_corporate_routing_context_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"prefix/{SITE}/office"]["refs"].__setitem__("vrf", "vrf/telemetry")))

    def test_a_station_routing_context_without_uniqueness_is_reported(self):
        for network in ("protection", "telemetry", "station"):
            with self.subTest(network=network):
                self.assertIn("utl-zone-isolation", self.mutated(
                    lambda plan, objects, n=network: objects[f"vrf/{n}"]["attrs"].__setitem__(
                        "enforce_unique", False)))

    def test_a_station_distribution_uplink_to_a_carrier_edge_is_reported(self):
        def rewire(plan, objects):
            port = f"device/{SITE}/ot-dist-a/if/Ethernet50/1"
            for cable in plan["objects"]:
                if cable["kind"] != "cable" or port not in cable["refs"].values():
                    continue
                side = "b" if cable["refs"]["a"] == port else "a"
                cable["refs"][side] = f"device/{SITE}/edge-a/if/port1"
        self.assertIn("utl-zone-isolation", self.mutated(rewire))

    def test_an_fhrp_group_on_a_station_segment_is_reported(self):
        def gateway(plan, objects):
            plan["objects"].append({"key": f"fhrp/{SITE}/telemetry", "kind": "fhrp_group",
                                    "attrs": {"name": "northgate-telemetry", "protocol": "vrrp3",
                                              "group_id": 7},
                                    "refs": {"untagged_vlan": f"vlan/{SITE}/telemetry"}, "meta": {}})
        self.assertIn("utl-zone-isolation", self.mutated(gateway))

    def test_an_interface_bond_across_the_zone_boundary_is_reported(self):
        for field in ("lag", "bridge", "parent"):
            with self.subTest(field=field):
                self.assertIn("utl-zone-isolation", self.mutated(
                    lambda plan, objects, f=field: objects[
                        f"device/{SITE}/ot-access-01/if/TenGigabitEthernet1/1/3"]["refs"].__setitem__(
                        f, f"device/{SITE}/access-01/if/TenGigabitEthernet1/1/3")))

    def test_a_record_naming_two_substations_zone_segments_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-dist-a/if/Ethernet49/1"]["refs"]
            .__setitem__("tagged_vlans", [f"vlan/{SITE}/protection",
                                          f"vlan/{OTHER}/protection"])))

    def test_a_segment_prefix_pointing_at_another_substations_vlan_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"prefix/{SITE}/protection"]["refs"].__setitem__(
                "vlan", f"vlan/{OTHER}/protection")))

    def test_the_conduit_named_by_an_endpoint_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/desk-01/if/eth0"]["refs"].__setitem__(
                "tagged_vlans", [f"vlan/{SITE}/conduit"])))

    def test_a_second_interface_bridging_a_station_endpoint_is_reported(self):
        def dual_home(plan, objects):
            plan["objects"].append({"key": f"device/{SITE}/rtu-01/if/eth1", "kind": "interface",
                                    "attrs": {"name": "eth1", "type": "1000base-t",
                                              "enabled": True, "mode": "access"},
                                    "refs": {"device": f"device/{SITE}/rtu-01",
                                             "untagged_vlan": f"vlan/{SITE}/office"}, "meta": {}})
        self.assertIn("utl-zone-isolation", self.mutated(dual_home))

    def test_a_gateway_borrowing_another_segments_routing_context_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-dist-a/if/Vlan30"]["refs"]
            .__setitem__("vrf", "vrf/office")))
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"ip/device/{SITE}/ot-dist-b/if/Vlan40"]["refs"]
            .__setitem__("vrf", "vrf/office")))

    def test_a_station_address_reassigned_to_a_corporate_device_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"ip/device/{SITE}/rtu-01/if/eth0"]["refs"].__setitem__(
                "assigned_object", f"device/{SITE}/access-01/if/TenGigabitEthernet1/1/3")))

    def test_a_service_binding_a_station_address_is_reported(self):
        def bind(plan, objects):
            service = next(o for o in plan["objects"] if o["kind"] == "service")
            service["refs"]["ipaddresses"] = [f"ip/device/{SITE}/rtu-01/if/eth0"]
        self.assertIn("utl-zone-isolation", self.mutated(bind))

    def test_a_cable_between_two_substations_station_zones_is_reported(self):
        def join(plan, objects):
            template = next(o for o in plan["objects"] if o["kind"] == "cable"
                            and o["attrs"].get("type") == "smf")
            plan["objects"].append(dict(template, key="cable/cross-substation", refs={
                "a": f"device/{SITE}/ot-access-01/if/TenGigabitEthernet1/1/3",
                "b": f"device/{OTHER}/ot-access-01/if/TenGigabitEthernet1/1/3"}))
        self.assertIn("utl-zone-isolation", self.mutated(join))

    def test_a_station_distribution_uplink_to_another_substation_is_reported(self):
        def rewire(plan, objects):
            port = f"device/{SITE}/ot-dist-a/if/Ethernet50/1"
            for cable in plan["objects"]:
                if cable["kind"] != "cable" or port not in cable["refs"].values():
                    continue
                side = "b" if cable["refs"]["a"] == port else "a"
                cable["refs"][side] = f"device/{OTHER}/dist-a/if/Ethernet52/1"
        self.assertIn("utl-zone-isolation", self.mutated(rewire))

    def test_a_vm_interface_in_a_station_routing_context_is_reported(self):
        self.assertIn("utl-zone-isolation", self.mutated(
            lambda plan, objects: objects["vm/dc-01/historian/001/eth0"]["refs"].__setitem__(
                "vrf", "vrf/telemetry")))

    def test_a_control_center_prefix_in_a_station_routing_context_is_reported(self):
        def borrow(plan, objects):
            plan["objects"].append({"key": "prefix/dc-01/telemetry", "kind": "prefix",
                                    "attrs": {"prefix": "10.0.13.0/24", "status": "active"},
                                    "refs": {"vrf": "vrf/telemetry", "scope_site": "site/dc-01",
                                             "tenant": "tenant"}, "meta": {}})
        self.assertIn("utl-zone-isolation", self.mutated(borrow))

    def test_a_station_endpoint_addressed_in_a_corporate_context_is_reported(self):
        self.assertIn("utl-endpoint-address", self.mutated(
            lambda plan, objects: objects[f"ip/device/{SITE}/relay-01/if/eth0"]["refs"].__setitem__(
                "vrf", "vrf/office")))

    def test_a_station_switch_losing_a_station_segment_is_reported(self):
        def strip(plan, objects):
            port = objects[f"device/{SITE}/ot-access-02/if/TenGigabitEthernet1/1/1"]
            port["refs"]["tagged_vlans"] = [v for v in port["refs"]["tagged_vlans"]
                                            if not v.endswith("/station")]
            port = objects[f"device/{SITE}/ot-access-02/if/TenGigabitEthernet1/1/2"]
            port["refs"]["tagged_vlans"] = [v for v in port["refs"]["tagged_vlans"]
                                            if not v.endswith("/station")]
        codes = self.mutated(strip)
        self.assertTrue({"utl-uplink-path", "utl-zone-isolation"} & codes, codes)

    def test_a_mac_record_moved_onto_a_corporate_port_is_not_mistaken_for_a_leak(self):
        # The zone sweep must not fire on the ordinary records that legitimately
        # bind a station port: its own address, its own MAC and its own cables.
        self.assertEqual(validate(self.baseline), [])

    def test_an_extra_inter_tier_link_is_reported_even_when_it_carries_only_the_conduit(self):
        def extra(plan, objects):
            ends = (f"device/{SITE}/ot-dist-a/if/Ethernet52/1", f"device/{SITE}/dist-a/if/Ethernet52/1")
            for end in ends:
                objects[end]["refs"]["tagged_vlans"] = [f"vlan/{SITE}/conduit"]
                objects[end]["attrs"]["mode"] = "tagged"
            template = next(o for o in plan["objects"] if o["kind"] == "cable"
                            and o["attrs"].get("type") == "smf")
            plan["objects"].append(dict(deepcopy(template), key="cable/extra-conduit",
                                        refs={"a": ends[0], "b": ends[1]}))
        self.assertIn("utl-conduit-path", self.mutated(extra))

    def test_a_widened_conduit_trunk_is_reported(self):
        self.assertIn("utl-conduit-path", self.mutated(_widen_the_conduit))

    def test_a_cut_conduit_trunk_is_reported(self):
        self.assertIn("utl-conduit-path", self.mutated(_cut_the_conduit))

    def test_removed_endpoint_is_reported_even_when_the_contract_agrees(self):
        def drop(plan, objects):
            plan["objects"] = [o for o in plan["objects"]
                               if not o["key"].startswith(f"device/{SITE}/relay-03")]
            contract = next(c for c in plan["contracts"] if c["site"] == f"site/{SITE}")
            contract["demand"]["protection_relays"] = 2
        codes = self.mutated(drop)
        self.assertIn("utl-endpoint-inventory", codes)
        self.assertIn("utl-endpoint-demand", codes)
        self.assertIn("utl-demand-report", codes)

    def test_a_relay_moved_to_another_bay_is_reported(self):
        self.assertIn("utl-endpoint-placement", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/relay-01"]["refs"].__setitem__(
                "location", f"location/{SITE}/bay-02")))

    def test_an_endpoint_on_the_wrong_segment_is_reported(self):
        self.assertIn("utl-endpoint-path", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/relay-01/if/eth0"]["refs"].__setitem__(
                "untagged_vlan", f"vlan/{SITE}/telemetry")))

    def test_a_station_endpoint_patched_onto_a_corporate_switch_is_reported(self):
        def repatch(plan, objects):
            for cable in plan["objects"]:
                if cable["kind"] == "cable" and f"device/{SITE}/rtu-01/if/eth0" in cable["refs"].values():
                    side = "a" if cable["refs"]["b"] == f"device/{SITE}/rtu-01/if/eth0" else "b"
                    cable["refs"][side] = f"device/{SITE}/access-01/if/GigabitEthernet1/0/24"
        self.assertIn("utl-endpoint-path", self.mutated(repatch))

    def test_unplugged_endpoint_is_reported(self):
        def unplug(plan, objects):
            plan["objects"] = [o for o in plan["objects"]
                               if not (o["kind"] == "cable" and
                                       f"device/{SITE}/rtu-01/if/eth0" in o["refs"].values())]
        self.assertIn("utl-endpoint-path", self.mutated(unplug))

    def test_moved_endpoint_mount_and_route_are_reported(self):
        self.assertIn("utl-endpoint-route", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/rtu-01"]["meta"]["placement"].__setitem__(
                "position_m", [12, 41, 0.8])))

    def test_renumbered_bay_ledger_is_reported(self):
        self.assertIn("utl-room-allocation", self.mutated(
            lambda plan, objects: plan["reservations"].__setitem__(
                f"substation-bays/{SITE}", {key: slot + 1 for key, slot
                                            in plan["reservations"][f"substation-bays/{SITE}"].items()})))

    def test_relocated_segment_prefix_is_reported(self):
        self.assertIn("utl-prefix-policy", self.mutated(
            lambda plan, objects: objects[f"prefix/{SITE}/protection"]["attrs"].__setitem__(
                "prefix", "10.9.9.0/24")))

    def test_reservation_container_leaving_its_segment_context_is_reported(self):
        self.assertIn("utl-prefix-policy", self.mutated(
            lambda plan, objects: objects[f"prefix/{SITE}/telemetry/reservation"]["refs"].__setitem__(
                "vrf", "vrf/office")))

    def test_undersized_substation_wan_commitment_is_reported(self):
        self.assertIn("utl-wan-capacity", self.mutated(
            lambda plan, objects: objects[f"circuit/{SITE}/a/1"]["attrs"].__setitem__("commit_rate", 1000)))

    def test_both_carriers_on_one_edge_are_reported(self):
        def collapse(plan, objects):
            for cable in plan["objects"]:
                if cable["kind"] == "cable" and cable["refs"].get("b") == f"circuit/{SITE}/b/1/A":
                    cable["refs"]["a"] = f"device/{SITE}/edge-a/if/wan2"
        self.assertIn("utl-wan-diversity", self.mutated(collapse))

    def test_missing_gateway_svi_is_reported(self):
        for port in ("dist-b/if/Vlan20", "ot-dist-b/if/Vlan30", "ot-dist-a/if/Vlan50",
                     "ot-dist-a/if/Vlan130"):
            with self.subTest(port=port):
                self.assertIn("utl-gateway-inventory", self.mutated(
                    lambda plan, objects, p=port: objects[f"device/{SITE}/{p}"]["attrs"]
                    .__setitem__("enabled", False)))

    def test_infrastructure_moved_out_of_the_equipment_room_is_reported(self):
        self.assertIn("utl-equipment-placement", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-access-02"]["refs"].__setitem__(
                "location", f"location/{SITE}/bay-01")))

    def test_reserved_access_slot_that_disagrees_with_the_graph_is_reported(self):
        def swap(plan, objects):
            scope = plan["reservations"][f"access-endpoints/{SITE}/location/{SITE}/ot"]
            a, b = f"device/{SITE}/rtu-01", f"device/{SITE}/rtu-02"
            scope[a], scope[b] = scope[b], scope[a]
        self.assertIn("utl-access-allocation", self.mutated(swap))

    def test_missing_or_out_of_range_reservations_are_reported(self):
        self.assertIn("utl-address-allocation", self.mutated(
            lambda plan, objects: plan["allocations"].pop(SITE)))
        for scope in (f"access-endpoints/{SITE}/location/{SITE}",
                      f"access-endpoints/{SITE}/location/{SITE}/ot"):
            with self.subTest(scope=scope):
                self.assertIn("utl-access-allocation", self.mutated(
                    lambda plan, objects, s=scope: plan["reservations"][s].__setitem__(
                        next(iter(plan["reservations"][s])), 99999)))

    def test_removed_shared_service_replica_is_reported(self):
        self.assertIn("dc-workload-inventory", self.mutated(
            lambda plan, objects: plan.__setitem__(
                "objects", [o for o in plan["objects"] if not o["key"].startswith("vm/dc-02/historian/002")])))

    def test_wrong_dc_prefix_offset_is_reported(self):
        self.assertIn("dc-prefix-policy", self.mutated(
            lambda plan, objects: objects["prefix/dc-01/applications"]["attrs"].__setitem__(
                "prefix", "10.0.240.0/20")))

    def test_power_path_break_is_reported(self):
        def cut(plan, objects):
            for cable in plan["objects"]:
                if cable["kind"] == "cable" and cable["attrs"].get("type") == "power":
                    cable["attrs"]["status"] = "planned"
                    break
        self.assertTrue({"dc-power-path", "power-path-status"} & self.mutated(cut))

    MUTATIONS = [
        ("utl-site-status",
         lambda plan, objects: objects[f"site/{SITE}"]["attrs"].__setitem__("status", "planned")),
        ("utl-address-allocation",
         lambda plan, objects: plan["allocations"].__setitem__(SITE, 0)),
        ("utl-contract-inventory", lambda plan, objects: plan.__setitem__(
            "contracts", [c for c in plan["contracts"] if c["site"] != f"site/{SITE}"])),
        ("utl-demand-report", lambda plan, objects: next(
            c for c in plan["contracts"] if c["site"] == f"site/{SITE}").__setitem__("substation", "elsewhere")),
        ("utl-demand-report", lambda plan, objects: next(
            c for c in plan["contracts"] if c["site"] == f"site/{SITE}").__setitem__(
            "substation_kind", "distribution")),
        ("utl-room-inventory", _extra_room),
        ("utl-room-allocation", lambda plan, objects: plan["reservations"].__setitem__(
            f"substation-bays/{SITE}", {"bay-01": 0})),
        ("utl-room-placement", lambda plan, objects: objects[f"location/{SITE}/bay-01"]["meta"]
         .__setitem__("position_m", [9, 30, 0])),
        ("utl-room-placement", lambda plan, objects: objects[f"location/{SITE}/control-room"]["meta"]
         .__setitem__("capacity", {"workstations": 9})),
        ("utl-closet-inventory", lambda plan, objects: objects[f"location/{SITE}"]["meta"]
         .__setitem__("space_type", "control_room")),
        ("utl-endpoint-inventory", lambda plan, objects: plan.__setitem__(
            "objects", [o for o in plan["objects"] if not o["key"].startswith(f"device/{SITE}/hmi-02")])),
        ("utl-endpoint-demand", lambda plan, objects: objects[f"device/{SITE}/rtu-02"]["refs"]
         .__setitem__("role", "role/hmi")),
        ("utl-endpoint-placement", lambda plan, objects: objects[f"device/{SITE}/rtu-01"]["meta"]
         .__setitem__("network", "office")),
        ("utl-endpoint-path", lambda plan, objects: objects[f"device/{SITE}/ot-access-01"]["attrs"]
         .__setitem__("status", "offline")),
        ("utl-endpoint-route", lambda plan, objects: objects[f"device/{SITE}/relay-02"]["meta"]["placement"]
         .__setitem__("position_m", [1, 1, 0.8])),
        ("utl-endpoint-address", lambda plan, objects: objects[f"ip/device/{SITE}/rtu-01/if/eth0"]["attrs"]
         .__setitem__("status", "reserved")),
        ("utl-access-inventory", _extra_switch),
        ("utl-access-capacity", lambda plan, objects: objects[f"device/{SITE}/ot-access-01"]["meta"]
         .__setitem__("hardware", "leaf")),
        ("utl-core-inventory", lambda plan, objects: objects[f"device/{SITE}/ot-dist-b"]["refs"]
         .__setitem__("role", "role/access")),
        ("utl-uplink-path", lambda plan, objects: objects[
            f"device/{SITE}/ot-access-01/if/TenGigabitEthernet1/1/1"]["attrs"].__setitem__("enabled", False)),
        ("utl-gateway-inventory", lambda plan, objects: objects[f"device/{SITE}/ot-dist-a/if/Vlan40"]["attrs"]
         .__setitem__("enabled", False)),
        ("utl-zone-isolation", _leak_to_corporate_access),
        ("utl-conduit-path", _widen_the_conduit),
        ("utl-prefix-policy", lambda plan, objects: objects[f"prefix/{SITE}/station"]["attrs"]
         .__setitem__("status", "reserved")),
        ("utl-wan-inventory", lambda plan, objects: objects[f"circuit/{SITE}/b/1"]["refs"]
         .__setitem__("provider", "provider/a")),
        ("utl-wan-capacity", lambda plan, objects: objects[f"circuit/{SITE}/a/1"]["attrs"]
         .__setitem__("status", "offline")),
        ("utl-wan-diversity", lambda plan, objects: [
            cable["refs"].__setitem__("a", f"device/{SITE}/edge-a/if/wan2")
            for cable in plan["objects"]
            if cable["kind"] == "cable" and cable["refs"].get("b") == f"circuit/{SITE}/b/1/A"]),
        ("utl-equipment-role", lambda plan, objects: objects[f"device/{SITE}/console-01"]["refs"]
         .__setitem__("role", "role/workstation")),
        ("utl-equipment-placement", lambda plan, objects: objects[f"device/{SITE}/mgmt-01"]["refs"]
         .__setitem__("location", f"location/{SITE}/control-room")),
        ("utl-access-allocation", lambda plan, objects: plan["reservations"][
            f"access-endpoints/{SITE}/location/{SITE}/ot"].pop(f"device/{SITE}/rtu-01")),
        ("utl-recipe", lambda plan, objects: plan["recipe"].__setitem__("substations", [])),
        ("utl-site-inventory", lambda plan, objects: plan["recipe"]["substations"].append(
            dict(key="ghost", kind="distribution", bays=2))),
    ]

    def test_every_utility_assertion_rejects_its_own_mutation(self):
        for code, change in self.MUTATIONS:
            with self.subTest(code=code):
                self.assertIn(code, self.mutated(change))

    def test_every_finding_code_in_the_validator_has_a_mutation(self):
        source = (ROOT / "estates/validate_utility.py").read_text()
        declared = set(re.findall(r'report\("(utl-[a-z-]+)"', source))
        covered = {code for code, _ in self.MUTATIONS}
        self.assertEqual(declared - covered, set(),
                         "every utility finding code needs a mutation that provokes it")
        self.assertEqual(covered - declared, set(), "mutation table names a code the validator never reports")


class UtilityArtifactTests(unittest.TestCase):
    def test_cli_preview_generation_and_turbobulk_load_check(self):
        def call(*args):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = main(["--json", *map(str, args)])
            self.assertEqual(status, 0, output.getvalue() + error.getvalue())
            return json.loads(output.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = root / "utility.toml"
            recipe.write_text('profile = "utility"\ncontrol_centers = 1\n\n[[substations]]\n'
                              'key = "oakridge"\nkind = "transmission"\nbays = 3\n')
            preview = call("plan", recipe)["intent"]
            self.assertEqual(preview["profile"], "utility")
            self.assertEqual(preview["resolved"]["substations"]["source"], "supplied")
            self.assertEqual(preview["resolved"]["control_centers"]["source"], "supplied")
            self.assertEqual(preview["resolved"]["reserve_fraction"]["source"], "default")
            call("generate", recipe, "--out", root / "build")
            self.assertEqual(json.loads((root / "build/intent.json").read_text()), preview)
            call("check", root / "build/plan.json")
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = load_main([str(root / "build"), "--load-check"])
            self.assertEqual(status, 0, error.getvalue())
            verdict = json.loads(output.getvalue())
            self.assertTrue(verdict["turbobulk_loadable"], verdict)
            self.assertEqual(verdict["turbobulk_uncovered"], [])
            self.assertEqual(verdict["turbobulk_unsupported_refs"], [])

    def test_optional_dual_stack_generates_and_validates(self):
        plan = small(ipv6_pool="2001:db8::/32")
        self.assertEqual(validate(plan), [])
        objects = {o["key"]: o for o in plan["objects"]}
        self.assertIn(f"ipv6/prefix/{SITE}/protection", objects)
        self.assertIn(f"ipv6/ip/device/{SITE}/relay-01/if/eth0", objects)

    def test_alternate_vendor_lines_generate_and_validate(self):
        plan = small(hardware={"access": "juniper", "leaf": "juniper", "ap": "aruba"})
        self.assertEqual(validate(plan), [])
        models = {o["attrs"]["model"] for o in plan["objects"] if o["kind"] == "device_type"}
        self.assertIn("EX3400-24P", models)
        self.assertNotIn("Catalyst 9200L-24P-4X", models)

    def test_legacy_panel_patching_keeps_the_two_zones_apart(self):
        plan = small(patching="panels")
        self.assertEqual(validate(plan), [])
        objects = {o["key"]: o for o in plan["objects"]}
        self.assertIn(f"device/{SITE}/patch-01", objects)
        self.assertIn(f"device/{SITE}/ot-patch-01", objects)

    def test_loss_of_power_diversity_scenario_runs_on_a_utility_plan(self):
        from estates.power_scenario import create, verify
        envelope = create(small())
        self.assertTrue(envelope["expected_findings"])
        self.assertEqual(validate(envelope["plans"]["baseline"]), [])
        self.assertTrue(validate(envelope["plans"]["changed"]))
        verify(envelope)


if __name__ == "__main__":
    unittest.main()
