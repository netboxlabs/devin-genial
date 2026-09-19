"""Manufacturing composition must follow plant demand and hold the IT/OT line.

The zone assertions get the adversarial treatment: every way a plant-floor
segment could leak into the corporate tier that this grammar can express is
mutated here and must be reported.
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


SMALL = dict(plants=[dict(key="riverbend", production_lines=2, warehouse_docks=2, office_staff=14),
                     dict(key="kestrel-forge", production_lines=1, warehouse_docks=0, office_staff=4)])
SITE = "pl-riverbend"
OT_VLANS = (f"vlan/{SITE}/process", f"vlan/{SITE}/supervisory")


def plan_for(**changes):
    return generate({"profile": "manufacturing"} | changes)


def small(**changes):
    return plan_for(**(deepcopy(SMALL) | changes))


class ManufacturingResolverTests(unittest.TestCase):
    def test_unknown_and_out_of_range_requests_are_actionable(self):
        cases = [
            ({"schools": []}, "Unknown manufacturing recipe fields: schools"),
            ({"stores": {}}, "Unknown manufacturing recipe fields: stores"),
            ({"customers": []}, "Unknown manufacturing recipe fields: customers"),
            ({"headquarters_staff": 100}, "Unknown manufacturing recipe fields: headquarters_staff"),
            ({"design_mix": {"modern": 1}}, "Unknown manufacturing recipe fields: design_mix"),
            ({"acquired_sites": []}, "Unknown manufacturing recipe fields: acquired_sites"),
            ({"wan_peak_mbps": 2000}, "Unknown manufacturing recipe fields: wan_peak_mbps"),
            ({"plants": {}}, "plants must be a list"),
            ({"plants": []}, "plants must be a list"),
            ({"plants": [dict(key=f"p{n:02}") for n in range(9)]}, "plants must be a list"),
            ({"plants": [{"production_lines": 1}]}, "needs a stable key"),
            ({"plants": [dict(key="a", wireless={})]}, "only supported demand fields"),
            ({"plants": [dict(key="A-Corp")]}, "hyphen-separated lowercase identifiers"),
            ({"plants": [dict(key="acme-")]}, "no leading, trailing or doubled hyphen"),
            ({"plants": [dict(key="ac--me")]}, "no leading, trailing or doubled hyphen"),
            ({"plants": [dict(key="a"), dict(key="a")]}, "hyphen-separated lowercase identifiers"),
            ({"plants": [dict(key="a-b"), dict(key="ab")]}, "distinct without hyphens"),
            ({"plants": [dict(key="a", production_lines=0)]}, "production_lines must be an integer from 1 through 12"),
            ({"plants": [dict(key="a", production_lines=13)]}, "production_lines must be an integer from 1 through 12"),
            ({"plants": [dict(key="a", production_lines=2.0)]}, "production_lines must be an integer from 1 through 12"),
            ({"plants": [dict(key="a", warehouse_docks=-1)]}, "warehouse_docks must be an integer from 0 through 12"),
            ({"plants": [dict(key="a", warehouse_docks=13)]}, "warehouse_docks must be an integer from 0 through 12"),
            ({"plants": [dict(key="a", office_staff=3)]}, "office_staff must be an integer from 4 through 96"),
            ({"plants": [dict(key="a", office_staff=97)]}, "office_staff must be an integer from 4 through 96"),
            ({"plants": [dict(key="a", office_staff=True)]}, "office_staff must be an integer from 4 through 96"),
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
        widest = plan_for(plants=[dict(key="edge-works", production_lines=12, warehouse_docks=12,
                                       office_staff=96)])
        self.assertEqual(validate(widest), [])
        self.assertEqual(len([o for o in widest["objects"] if o["kind"] == "site"]), 3)
        narrowest = plan_for(plants=[dict(key="one", production_lines=1, warehouse_docks=0, office_staff=4)])
        self.assertEqual(validate(narrowest), [])
        self.assertEqual(len([o for o in narrowest["objects"] if o["kind"] == "site"]), 3)

    def test_the_widest_plant_still_fits_the_widest_reserve(self):
        # The two access zones share one 38-switch attachment budget; the
        # reviewed bounds must remain inside it at every supported reserve.
        for reserve in (0.1, 0.2, 0.4):
            with self.subTest(reserve=reserve):
                plan = plan_for(reserve_fraction=reserve,
                                plants=[dict(key="edge-works", production_lines=12,
                                             warehouse_docks=12, office_staff=96)])
                self.assertEqual(validate(plan), [])
                switches = [o for o in plan["objects"] if o["kind"] == "device"
                            and o["refs"].get("role") == "role/access"
                            and o["refs"]["site"] == "site/pl-edge-works"]
                self.assertLessEqual(len(switches), 38)

    def test_defaults_are_frozen_into_the_recipe(self):
        recipe = plan_for()["recipe"]
        self.assertEqual(recipe["profile"], "manufacturing")
        self.assertEqual(recipe["namespace"], "ironwood")
        self.assertEqual(recipe["name"], "Ironwood Manufacturing")
        self.assertEqual(recipe["demo"], "baseline")
        self.assertEqual([item["key"] for item in recipe["plants"]],
                         ["granite-harbor", "kestrel-forge", "riverbend"])
        riverbend = next(item for item in recipe["plants"] if item["key"] == "riverbend")
        self.assertEqual((riverbend["production_lines"], riverbend["warehouse_docks"],
                          riverbend["office_staff"]), (6, 6, 48))

    def test_omitted_plant_fields_take_the_authored_defaults(self):
        recipe = plan_for(plants=[dict(key="only")])["recipe"]
        self.assertEqual(recipe["plants"][0],
                         dict(key="only", production_lines=4, warehouse_docks=4, office_staff=36))

    def test_shipped_profile_resolves_and_validates(self):
        plan = generate(recipe_from_file(ROOT / "profiles/manufacturing.toml"))
        self.assertEqual(validate(plan), [])
        self.assertEqual(len(plan["recipe"]["plants"]), 3)
        self.assertEqual(len([o for o in plan["objects"] if o["kind"] == "site"]), 5)


class ManufacturingCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = small()

    def objects(self, plan=None):
        return {o["key"]: o for o in (plan or self.plan)["objects"]}

    def test_plant_estate_connects_its_own_demand_without_other_industries(self):
        self.assertEqual(validate(self.plan), [])
        self.assertEqual(canonical(generate(deepcopy(self.plan["recipe"]))["objects"]),
                         canonical(self.plan["objects"]))
        objects = self.objects()
        self.assertEqual({key for key, obj in objects.items() if obj["kind"] == "site"},
                         {"site/dc-01", "site/dc-02", "site/pl-riverbend", "site/pl-kestrel-forge"})
        services = {key.split("/")[2] for key, obj in objects.items() if obj["kind"] == "virtual_machine"}
        self.assertEqual(services, {"mes", "historian", "erp-gateway", "identity", "dns",
                                    "monitoring", "backup"})
        blob = canonical(self.plan).decode()
        for forbidden in ("teller", "classroom", "patient", "point-of-sale", "lecture hall",
                          "residence room", "ATM", "managed-network contract"):
            self.assertNotIn(forbidden, blob, forbidden)
        for required in ("Production line", "Loading dock", "Office pod", "conduit"):
            self.assertIn(required, blob, required)

    def test_no_industrial_protocol_or_compliance_claim_appears_in_a_record(self):
        # Protocol and compliance words exist in exactly one place: the contract
        # assumptions that deny them. No emitted record may name one, and no
        # service may listen on a well-known industrial control port.
        forbidden = ("modbus", "profinet", "ethernet/ip", "opc-ua", "opc ua", "dnp3",
                     "s7comm", "purdue", "62443", "air gap", "air-gapped")
        for obj in self.plan["objects"]:
            for field, value in obj["attrs"].items():
                if isinstance(value, str):
                    for term in forbidden:
                        self.assertNotIn(term, value.lower(), (obj["key"], field, term))
        industrial_ports = {502, 20000, 2222, 4840, 34962, 34963, 34964, 44818, 47808}
        for obj in self.plan["objects"]:
            if obj["kind"] == "service":
                self.assertEqual(set(obj["attrs"]["ports"]) & industrial_ports, set(), obj["key"])
        denials = {text for contract in self.plan["contracts"] for text in contract["assumptions"]}
        self.assertTrue(any("No industrial protocol is configured" in text for text in denials))
        self.assertTrue(any("IEC 62443 compliance state" in text for text in denials))

    def test_plant_places_lines_docks_and_pods_in_their_own_rooms(self):
        objects = self.objects()
        self.assertEqual(objects[f"device/{SITE}/desk-001"]["refs"]["location"],
                         f"location/{SITE}/pod-01")
        self.assertEqual(objects[f"device/{SITE}/desk-013"]["refs"]["location"],
                         f"location/{SITE}/pod-02")
        self.assertEqual(objects[f"device/{SITE}/plc-01"]["refs"]["location"], f"location/{SITE}/line-01")
        self.assertEqual(objects[f"device/{SITE}/hmi-02-2"]["refs"]["location"], f"location/{SITE}/line-02")
        self.assertEqual(objects[f"device/{SITE}/scan-003"]["refs"]["location"], f"location/{SITE}/dock-02")
        self.assertEqual(objects[f"device/{SITE}/ap-reception"]["refs"]["location"],
                         f"location/{SITE}/reception")
        for role in ("management", "office", "logistics", "wireless", "security",
                     "process", "supervisory", "conduit"):
            prefix = objects[f"prefix/{SITE}/{role}"]
            self.assertEqual(prefix["attrs"]["prefix"].split("/")[1], "24")
            self.assertEqual(prefix["refs"]["scope_site"], f"site/{SITE}")
        self.assertNotIn("prefix/dc-01/process", objects)

    def test_authored_ot_density_is_one_controller_two_panels_and_four_drops(self):
        objects = self.objects()
        roles = {}
        for key, obj in objects.items():
            if obj["kind"] == "device" and obj["refs"].get("site") == f"site/{SITE}":
                roles.setdefault(obj["refs"]["role"], []).append(key)
        self.assertEqual(len(roles["role/plc"]), 2)
        self.assertEqual(len(roles["role/hmi"]), 4)
        self.assertEqual(len(roles["role/field-device"]), 8)
        self.assertEqual(len(roles["role/scanner"]), 4)
        self.assertEqual(objects[f"device/{SITE}/plc-01"]["meta"]["network"], "process")
        self.assertEqual(objects[f"device/{SITE}/hmi-01-1"]["meta"]["network"], "supervisory")
        self.assertEqual(objects[f"device/{SITE}/field-01-1"]["meta"]["network"], "process")
        self.assertEqual(objects[f"device/{SITE}/scan-001"]["meta"]["network"], "logistics")

    def test_ot_endpoint_records_deny_control_function_and_protocols(self):
        objects = self.objects()
        for key in (f"device/{SITE}/plc-01", f"device/{SITE}/hmi-01-1", f"device/{SITE}/field-02-3"):
            description = objects[key]["attrs"]["description"]
            self.assertIn("Reference", description)
            self.assertIn("no control function, safety rating or industrial protocol", description)
        room = objects[f"location/{SITE}/line-01"]["attrs"]["description"]
        self.assertIn("no control function or industrial protocol is configured", room)

    def test_plant_endpoints_use_only_catalog_reference_hardware(self):
        aliases = {o["meta"]["hardware"] for o in self.plan["objects"] if o["kind"] == "device"}
        self.assertTrue(aliases <= set(hardware_catalog()["models"]))
        self.assertEqual(aliases & {"atm", "inherited-access", "liquid-blade", "liquid-chassis"}, set())
        for key in (f"device/{SITE}/plc-01", f"device/{SITE}/hmi-01-1", f"device/{SITE}/scan-001"):
            self.assertEqual(self.objects()[key]["meta"]["hardware"], "endpoint")
        roles = {o["refs"]["role"] for o in self.plan["objects"] if o["kind"] == "device"}
        self.assertEqual(roles & {"role/pos-terminal", "role/medical-device", "role/imaging-device",
                                  "role/atm", "role/provider-edge"}, set())

    def test_staff_wireless_serves_the_office_segment_and_never_the_production_floor(self):
        objects = self.objects()
        for sid in (SITE, "pl-kestrel-forge"):
            wlan = objects[f"wireless-lan/{sid}/staff"]
            self.assertEqual(wlan["refs"]["vlan"], f"vlan/{sid}/office")
            self.assertEqual(wlan["attrs"]["auth_type"], "wpa-enterprise")
        self.assertNotIn(f"wireless-lan/{SITE}/guest", objects)
        self.assertNotIn("wireless-lan/dc-01/staff", objects)
        for key, obj in objects.items():
            if obj["kind"] == "device" and obj["refs"].get("role") == "role/ap":
                self.assertNotEqual(objects[obj["refs"]["location"]]["meta"]["space_type"],
                                    "production_line", key)

    def test_growth_appends_plants_lines_docks_and_desks_without_moving_anything(self):
        before = small()
        recipe = deepcopy(before["recipe"])
        entry = next(item for item in recipe["plants"] if item["key"] == "riverbend")
        entry.update(production_lines=4, warehouse_docks=5, office_staff=30)
        recipe["plants"].append(dict(key="lakeside-works", production_lines=2,
                                     warehouse_docks=1, office_staff=12))
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
                self.assertGreaterEqual(new[key]["meta"].get("capacity", {}).get("workstations", 0),
                                        obj["meta"].get("capacity", {}).get("workstations", 0), key)
            elif obj["kind"] in {"site", "rack", "cable", "ip_address", "vlan", "prefix",
                                 "virtual_machine", "tenant", "vrf"} or key in occupied:
                self.assertEqual(new[key], obj, key)
            if obj["kind"] == "device":
                for field in ("location", "rack", "site", "tenant"):
                    self.assertEqual(new[key]["refs"].get(field), obj["refs"].get(field), key)
        for scope, items in before["reservations"].items():
            for key, slot in items.items():
                self.assertEqual(after["reservations"][scope][key], slot, (scope, key))

    def test_a_new_line_never_renumbers_an_existing_line_dock_or_pod(self):
        before = small()
        old = {o["key"]: o for o in before["objects"]}
        recipe = deepcopy(before["recipe"])
        entry = next(item for item in recipe["plants"] if item["key"] == "riverbend")
        entry.update(production_lines=6, warehouse_docks=6, office_staff=44)
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        new = {o["key"]: o for o in after["objects"]}
        for room in (f"location/{SITE}/line-01", f"location/{SITE}/line-02",
                     f"location/{SITE}/dock-01", f"location/{SITE}/pod-01", f"location/{SITE}/reception"):
            self.assertEqual(new[room]["meta"]["position_m"], old[room]["meta"]["position_m"], room)
        for device in (f"device/{SITE}/plc-01", f"device/{SITE}/desk-001", f"device/{SITE}/scan-001"):
            self.assertEqual(new[device]["meta"]["placement"], old[device]["meta"]["placement"], device)
        self.assertEqual(after["reservations"][f"plant-lines/{SITE}"]["line-01"], 0)
        self.assertEqual(len(after["reservations"][f"plant-lines/{SITE}"]), 6)
        self.assertEqual(len(after["reservations"][f"plant-docks/{SITE}"]), 6)

    def test_both_zone_port_ledgers_survive_growth_independently(self):
        before = small()
        recipe = deepcopy(before["recipe"])
        next(item for item in recipe["plants"] if item["key"] == "riverbend")["production_lines"] = 5
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        for scope, items in before["reservations"].items():
            if scope.startswith("access-endpoints/"):
                for key, slot in items.items():
                    self.assertEqual(after["reservations"][scope][key], slot, (scope, key))
        self.assertIn(f"access-endpoints/{SITE}/location/{SITE}/ot", after["reservations"])
        self.assertIn(f"access-endpoints/{SITE}/location/{SITE}", after["reservations"])

    def test_radio_channels_survive_growth(self):
        before = small()
        recipe = deepcopy(before["recipe"])
        next(item for item in recipe["plants"] if item["key"] == "kestrel-forge")["office_staff"] = 24
        after = generate(recipe, previous=before)
        old_rf = {o["key"]: o["attrs"]["rf_channel"] for o in before["objects"]
                  if o["kind"] == "interface" and o["attrs"].get("rf_channel")}
        new_rf = {o["key"]: o["attrs"]["rf_channel"] for o in after["objects"]
                  if o["kind"] == "interface" and o["attrs"].get("rf_channel")}
        self.assertTrue(old_rf)
        self.assertEqual({k: new_rf.get(k) for k in old_rf}, old_rf)

    def test_reductions_and_profile_changes_need_a_new_baseline(self):
        before = small()

        def entry(recipe, key):
            return next(item for item in recipe["plants"] if item["key"] == key)

        generate(deepcopy(before["recipe"]), previous=before)
        cases = [
            ("dropped plant", lambda r: r["plants"].remove(entry(r, "kestrel-forge"))),
            ("fewer lines", lambda r: entry(r, "riverbend").__setitem__("production_lines", 1)),
            ("fewer docks", lambda r: entry(r, "riverbend").__setitem__("warehouse_docks", 1)),
            ("fewer desks", lambda r: entry(r, "riverbend").__setitem__("office_staff", 8)),
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
        damaged["objects"] = [o for o in damaged["objects"] if o["key"] != f"device/{SITE}/plc-01"]
        with self.assertRaisesRegex(DesignError, "does not reproduce"):
            generate(deepcopy(before["recipe"]), previous=damaged)

    def test_report_explains_the_zones_and_their_limits(self):
        guide = markdown(self.plan)
        for phrase in ("Plant zones", "Line controllers", "Field devices", "Conduit trunks",
                       "Replica group placement"):
            self.assertIn(phrase, guide)
        for phrase in ("no firewall policy", "Purdue level", "no industrial protocol",
                       "not production output"):
            self.assertIn(phrase, guide.replace("\n", " "))


class ManufacturingZoneTests(unittest.TestCase):
    """The built graph must hold the IT/OT line before anyone mutates it."""

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

    def test_plant_floor_segments_live_only_in_their_own_zone(self):
        expected_infrastructure = {f"device/{SITE}/ot-access-01", f"device/{SITE}/ot-access-02",
                                   f"device/{SITE}/ot-dist-a", f"device/{SITE}/ot-dist-b"}
        for vlan, endpoints in ((OT_VLANS[0], {f"device/{SITE}/plc-{n+1:02}" for n in range(2)} |
                                 {f"device/{SITE}/field-{n+1:02}-{m+1}" for n in range(2) for m in range(4)}),
                                (OT_VLANS[1], {f"device/{SITE}/hmi-{n+1:02}-{m+1}"
                                               for n in range(2) for m in range(2)})):
            with self.subTest(vlan=vlan):
                self.assertEqual(self.carriers(vlan), expected_infrastructure | endpoints)

    def test_no_plant_floor_segment_touches_a_carrier_edge_or_corporate_switch(self):
        corporate = {f"device/{SITE}/edge-a", f"device/{SITE}/edge-b", f"device/{SITE}/dist-a",
                     f"device/{SITE}/dist-b", f"device/{SITE}/access-01", f"device/{SITE}/access-02",
                     f"device/{SITE}/mgmt-01"}
        for vlan in OT_VLANS:
            self.assertEqual(self.carriers(vlan) & corporate, set(), vlan)

    def test_no_circuit_is_reachable_from_the_plant_floor_distribution_pair(self):
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
            if len(owners) != 2:
                continue
            if not all(owner.startswith(f"device/{SITE}/") for owner in owners):
                continue
            tiers = {"/ot-dist-" in owner for owner in owners}
            if tiers == {True, False} and all("dist-" in owner for owner in owners):
                links.append(ends)
        self.assertEqual(len(links), 4)
        for ends in links:
            for end in ends:
                self.assertEqual(self.carried(end), {f"vlan/{SITE}/conduit"}, end)

    def test_plant_floor_equipment_carries_management_only_on_its_management_port(self):
        management = f"vlan/{SITE}/management"
        for device in (f"device/{SITE}/ot-dist-a", f"device/{SITE}/ot-access-01"):
            ports = [key for key, obj in self.objects.items()
                     if obj["kind"] == "interface" and obj["refs"].get("device") == device
                     and management in self.carried(key)]
            self.assertEqual(len(ports), 1, device)
            self.assertTrue(self.objects[ports[0]]["attrs"].get("mgmt_only"), ports[0])

    def test_plant_floor_segments_hold_their_own_routing_contexts(self):
        for network in ("process", "supervisory"):
            vrf = f"vrf/{network}"
            self.assertEqual(self.objects[vrf]["kind"], "vrf")
            self.assertTrue(self.objects[vrf]["attrs"]["enforce_unique"])
            users = {self.objects[key]["refs"].get("scope_site") for key, obj in self.objects.items()
                     if obj["kind"] == "prefix" and obj["refs"].get("vrf") == vrf
                     and obj["attrs"].get("status") == "active"}
            self.assertEqual(users, {f"site/{SITE}", "site/pl-kestrel-forge"})
        self.assertNotEqual(self.objects[f"prefix/{SITE}/process"]["refs"]["vrf"],
                            self.objects[f"prefix/{SITE}/office"]["refs"]["vrf"])

    def test_the_two_access_zones_never_share_a_switch(self):
        corporate, floor = set(), set()
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
            (floor if obj["meta"]["network"] in {"process", "supervisory"} else corporate).add(switch)
        self.assertTrue(all("/ot-access-" in switch for switch in floor), floor)
        self.assertTrue(all("/ot-access-" not in switch for switch in corporate), corporate)
        self.assertEqual(corporate & floor, set())


class ManufacturingNamingTests(unittest.TestCase):
    def test_site_names_are_unique_and_industry_flavoured(self):
        plan = plan_for()
        sites = [o for o in plan["objects"] if o["kind"] == "site"]
        names = [o["attrs"]["name"] for o in sites]
        self.assertEqual(len(set(names)), len(names))
        plants = [o for o in sites if o["key"].startswith("site/pl-")]
        for site in plants:
            self.assertTrue(site["attrs"]["name"].endswith(" Plant"), site["attrs"]["name"])
            self.assertTrue(site["attrs"]["facility"])
        self.assertIn("Riverbend Plant", names)
        self.assertEqual(len({o["attrs"]["physical_address"] for o in sites}), len(sites))

    def test_site_names_override_and_legacy_naming_stay_available(self):
        override = small(site_names={SITE: {"name": "Riverbend Works, Bay 3", "facility": "RV-3"}})
        self.assertEqual(validate(override), [])
        site = next(o for o in override["objects"] if o["key"] == f"site/{SITE}")
        self.assertEqual(site["attrs"]["name"], "Riverbend Works, Bay 3")
        self.assertEqual(site["attrs"]["facility"], "RV-3")
        legacy = small(naming="legacy")
        self.assertEqual(validate(legacy), [])
        site = next(o for o in legacy["objects"] if o["key"] == f"site/{SITE}")
        self.assertEqual(site["attrs"]["name"], f"ironwood-{SITE}")
        with self.assertRaisesRegex(DesignError, "unknown site ids"):
            small(site_names={"pl-nowhere": {"name": "Nowhere"}})


def _extra_room(plan, objects):
    plan["objects"].append({"key": f"location/{SITE}/annex", "kind": "location",
                            "attrs": {"name": "Annex", "slug": f"ironwood-{SITE}-annex", "status": "active"},
                            "refs": {"site": f"site/{SITE}", "tenant": "tenant",
                                     "parent": f"location/{SITE}/floor-01"},
                            "meta": {"space_type": "production_line", "floor": 1, "position_m": [40, 40, 0]}})


def _extra_switch(plan, objects):
    plan["objects"].append(dict(objects[f"device/{SITE}/ot-access-01"],
                                key=f"device/{SITE}/ot-access-09",
                                attrs=dict(objects[f"device/{SITE}/ot-access-01"]["attrs"],
                                           name="plriverbend-ot-as09", position=25)))


def _leak_to_corporate_access(plan, objects):
    objects[f"device/{SITE}/access-01/if/TenGigabitEthernet1/1/1"]["refs"]["tagged_vlans"].append(
        f"vlan/{SITE}/process")


def _leak_to_carrier_edge(plan, objects):
    objects[f"device/{SITE}/edge-a/if/x1"]["refs"]["tagged_vlans"].append(f"vlan/{SITE}/supervisory")


def _leak_untagged(plan, objects):
    objects[f"device/{SITE}/access-02/if/TenGigabitEthernet1/1/2"]["refs"]["untagged_vlan"] = \
        f"vlan/{SITE}/process"


def _leak_to_another_plant(plan, objects):
    objects["device/pl-kestrel-forge/access-01/if/TenGigabitEthernet1/1/3"]["refs"]["tagged_vlans"] = \
        [f"vlan/{SITE}/process"]


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


class ManufacturingValidatorTests(unittest.TestCase):
    """Every independent manufacturing assertion must reject a matching mutation."""

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
        for change in (lambda plan, objects: plan["recipe"].__setitem__("plants", []),
                       lambda plan, objects: plan["recipe"]["plants"][0].__setitem__("production_lines", 99),
                       lambda plan, objects: plan["recipe"]["plants"][0].__setitem__("warehouse_docks", -1),
                       lambda plan, objects: plan["recipe"]["plants"][0].__setitem__("office_staff", 400),
                       lambda plan, objects: plan["recipe"]["plants"][0].__setitem__("key", "Bad Key"),
                       lambda plan, objects: plan["recipe"].__setitem__("reserve_fraction", 0.9)):
            self.assertIn("mfg-recipe", self.mutated(change))
        self.assertIn("mfg-recipe", self.mutated(
            lambda plan, objects: plan["recipe"].__setitem__("wan_tiers_mbps", [100, 50])))

    def test_missing_or_extra_sites_are_reported(self):
        def add_plant(plan, objects):
            plan["recipe"]["plants"].append(dict(key="ghost", production_lines=1,
                                                 warehouse_docks=0, office_staff=4))
        self.assertIn("mfg-site-inventory", self.mutated(add_plant))

    def test_a_plant_floor_segment_on_a_corporate_switch_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(_leak_to_corporate_access))

    def test_a_plant_floor_segment_on_a_carrier_edge_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(_leak_to_carrier_edge))

    def test_an_untagged_plant_floor_leak_is_reported_like_a_tagged_one(self):
        self.assertIn("mfg-zone-isolation", self.mutated(_leak_untagged))

    def test_a_plant_floor_segment_named_at_another_plant_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(_leak_to_another_plant))

    def test_a_plant_floor_switch_carrying_a_corporate_segment_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-access-01/if/TenGigabitEthernet1/1/1"]
            ["refs"]["tagged_vlans"].append(f"vlan/{SITE}/office")))

    def test_the_conduit_appearing_on_an_access_switch_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(_conduit_on_an_access_switch))

    def test_a_removed_plant_floor_segment_trunk_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-dist-a/if/Vlan30"]["refs"].__setitem__(
                "untagged_vlan", f"vlan/{SITE}/office")))

    def test_a_direct_cable_from_the_plant_floor_to_a_corporate_switch_is_reported(self):
        def bridge(plan, objects):
            template = next(o for o in plan["objects"] if o["kind"] == "cable"
                            and o["attrs"].get("type") == "smf")
            plan["objects"].append(dict(template, key="cable/cross-zone", refs={
                "a": f"device/{SITE}/ot-access-01/if/TenGigabitEthernet1/1/3",
                "b": f"device/{SITE}/access-01/if/TenGigabitEthernet1/1/3"}))
        self.assertIn("mfg-zone-isolation", self.mutated(bridge))

    def test_a_plant_floor_gateway_borrowing_a_corporate_routing_context_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"prefix/{SITE}/office"]["refs"].__setitem__("vrf", "vrf/process")))

    def test_a_plant_floor_routing_context_without_uniqueness_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(
            lambda plan, objects: objects["vrf/process"]["attrs"].__setitem__("enforce_unique", False)))

    def test_a_plant_floor_distribution_uplink_to_a_carrier_edge_is_reported(self):
        def rewire(plan, objects):
            port = f"device/{SITE}/ot-dist-a/if/Ethernet50/1"
            for cable in plan["objects"]:
                if cable["kind"] != "cable" or port not in cable["refs"].values():
                    continue
                side = "b" if cable["refs"]["a"] == port else "a"
                cable["refs"][side] = f"device/{SITE}/edge-a/if/port1"
        self.assertIn("mfg-zone-isolation", self.mutated(rewire))

    def test_a_wlan_bound_to_a_plant_floor_segment_is_reported(self):
        def broadcast(plan, objects):
            wlan = deepcopy(objects[f"wireless-lan/{SITE}/staff"])
            wlan["key"] = f"wireless-lan/{SITE}/process"
            wlan["attrs"]["ssid"] = "ironwood-process"
            wlan["refs"]["vlan"] = f"vlan/{SITE}/process"
            plan["objects"].append(wlan)
        self.assertIn("mfg-zone-isolation", self.mutated(broadcast))

    def test_an_fhrp_group_on_a_plant_floor_segment_is_reported(self):
        def gateway(plan, objects):
            plan["objects"].append({"key": f"fhrp/{SITE}/process", "kind": "fhrp_group",
                                    "attrs": {"name": "ironwood-process", "protocol": "vrrp3",
                                              "group_id": 7},
                                    "refs": {"untagged_vlan": f"vlan/{SITE}/process"}, "meta": {}})
        self.assertIn("mfg-zone-isolation", self.mutated(gateway))

    def test_an_interface_bond_across_the_zone_boundary_is_reported(self):
        for field in ("lag", "bridge", "parent"):
            with self.subTest(field=field):
                self.assertIn("mfg-zone-isolation", self.mutated(
                    lambda plan, objects, f=field: objects[
                        f"device/{SITE}/ot-access-01/if/TenGigabitEthernet1/1/3"]["refs"].__setitem__(
                        f, f"device/{SITE}/access-01/if/TenGigabitEthernet1/1/3")))

    def test_a_record_naming_two_plants_zone_segments_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-dist-a/if/Ethernet49/1"]["refs"]
            .__setitem__("tagged_vlans", [f"vlan/{SITE}/process",
                                          "vlan/pl-kestrel-forge/process"])))

    def test_a_segment_prefix_pointing_at_another_plants_vlan_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"prefix/{SITE}/process"]["refs"].__setitem__(
                "vlan", "vlan/pl-kestrel-forge/process")))

    def test_the_conduit_named_by_an_endpoint_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/desk-001/if/eth0"]["refs"].__setitem__(
                "tagged_vlans", [f"vlan/{SITE}/conduit"])))

    def test_a_second_interface_bridging_a_plant_floor_endpoint_is_reported(self):
        def dual_home(plan, objects):
            plan["objects"].append({"key": f"device/{SITE}/plc-01/if/eth1", "kind": "interface",
                                    "attrs": {"name": "eth1", "type": "1000base-t",
                                              "enabled": True, "mode": "access"},
                                    "refs": {"device": f"device/{SITE}/plc-01",
                                             "untagged_vlan": f"vlan/{SITE}/office"}, "meta": {}})
        self.assertIn("mfg-zone-isolation", self.mutated(dual_home))

    def test_a_gateway_borrowing_another_segments_routing_context_is_reported(self):
        self.assertIn("mfg-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-dist-a/if/Vlan30"]["refs"]
            .__setitem__("vrf", "vrf/office")))
        self.assertIn("mfg-zone-isolation", self.mutated(
            lambda plan, objects: objects[f"ip/device/{SITE}/ot-dist-b/if/Vlan40"]["refs"]
            .__setitem__("vrf", "vrf/office")))

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
        self.assertIn("mfg-conduit-path", self.mutated(extra))

    def test_a_widened_conduit_trunk_is_reported(self):
        self.assertIn("mfg-conduit-path", self.mutated(_widen_the_conduit))

    def test_a_cut_conduit_trunk_is_reported(self):
        self.assertIn("mfg-conduit-path", self.mutated(_cut_the_conduit))

    def test_removed_endpoint_is_reported_even_when_the_contract_agrees(self):
        def drop(plan, objects):
            plan["objects"] = [o for o in plan["objects"]
                               if not o["key"].startswith(f"device/{SITE}/field-02-4")]
            contract = next(c for c in plan["contracts"] if c["site"] == f"site/{SITE}")
            contract["demand"]["field_devices"] = 7
        codes = self.mutated(drop)
        self.assertIn("mfg-endpoint-inventory", codes)
        self.assertIn("mfg-endpoint-demand", codes)
        self.assertIn("mfg-demand-report", codes)

    def test_a_controller_moved_to_another_line_is_reported(self):
        self.assertIn("mfg-endpoint-placement", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/plc-01"]["refs"].__setitem__(
                "location", f"location/{SITE}/line-02")))

    def test_an_endpoint_on_the_wrong_segment_is_reported(self):
        self.assertIn("mfg-endpoint-path", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/hmi-01-1/if/eth0"]["refs"].__setitem__(
                "untagged_vlan", f"vlan/{SITE}/process")))

    def test_a_plant_floor_endpoint_patched_onto_a_corporate_switch_is_reported(self):
        def repatch(plan, objects):
            for cable in plan["objects"]:
                if cable["kind"] == "cable" and cable["refs"].get("b") == f"device/{SITE}/plc-01/if/eth0":
                    cable["refs"]["a"] = f"device/{SITE}/access-01/if/GigabitEthernet1/0/24"
        codes = self.mutated(repatch)
        self.assertIn("mfg-endpoint-path", codes)

    def test_unplugged_endpoint_is_reported(self):
        def unplug(plan, objects):
            plan["objects"] = [o for o in plan["objects"]
                               if not (o["kind"] == "cable" and
                                       f"device/{SITE}/plc-01/if/eth0" in o["refs"].values())]
        self.assertIn("mfg-endpoint-path", self.mutated(unplug))

    def test_radio_cabled_to_an_uplink_port_is_reported(self):
        def relocate(plan, objects):
            for cable in plan["objects"]:
                if cable["kind"] == "cable" and cable["refs"].get("b") == \
                        f"device/{SITE}/ap-reception/if/eth0":
                    cable["refs"]["a"] = f"device/{SITE}/access-01/if/TenGigabitEthernet1/1/4"
        self.assertIn("mfg-ap-power", self.mutated(relocate))

    def test_moved_endpoint_mount_and_route_are_reported(self):
        self.assertIn("mfg-endpoint-route", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/plc-01"]["meta"]["placement"].__setitem__(
                "position_m", [12, 41, 0.8])))

    def test_renumbered_room_ledgers_are_reported(self):
        for scope in ("plant-lines", "plant-docks", "plant-pods"):
            with self.subTest(scope=scope):
                self.assertIn("mfg-room-allocation", self.mutated(
                    lambda plan, objects, s=scope: plan["reservations"].__setitem__(
                        f"{s}/{SITE}", {key: slot + 1 for key, slot
                                        in plan["reservations"][f"{s}/{SITE}"].items()})))

    def test_relocated_segment_prefix_is_reported(self):
        self.assertIn("mfg-prefix-policy", self.mutated(
            lambda plan, objects: objects[f"prefix/{SITE}/process"]["attrs"].__setitem__(
                "prefix", "10.9.9.0/24")))

    def test_reservation_container_leaving_its_segment_context_is_reported(self):
        self.assertIn("mfg-prefix-policy", self.mutated(
            lambda plan, objects: objects[f"prefix/{SITE}/process/reservation"]["refs"].__setitem__(
                "vrf", "vrf/office")))

    def test_undersized_plant_wan_commitment_is_reported(self):
        self.assertIn("mfg-wan-capacity", self.mutated(
            lambda plan, objects: objects[f"circuit/{SITE}/a/1"]["attrs"].__setitem__("commit_rate", 50000)))

    def test_both_carriers_on_one_edge_are_reported(self):
        def collapse(plan, objects):
            for cable in plan["objects"]:
                if cable["kind"] == "cable" and cable["refs"].get("b") == f"circuit/{SITE}/b/1/A":
                    cable["refs"]["a"] = f"device/{SITE}/edge-a/if/wan2"
        self.assertIn("mfg-wan-diversity", self.mutated(collapse))

    def test_missing_gateway_svi_is_reported(self):
        for port in ("dist-b/if/Vlan20", "ot-dist-b/if/Vlan30", "ot-dist-a/if/Vlan130"):
            with self.subTest(port=port):
                self.assertIn("mfg-gateway-inventory", self.mutated(
                    lambda plan, objects, p=port: objects[f"device/{SITE}/{p}"]["attrs"]
                    .__setitem__("enabled", False)))

    def test_infrastructure_moved_out_of_the_equipment_room_is_reported(self):
        self.assertIn("mfg-equipment-placement", self.mutated(
            lambda plan, objects: objects[f"device/{SITE}/ot-access-02"]["refs"].__setitem__(
                "location", f"location/{SITE}/line-01")))

    def test_reserved_access_slot_that_disagrees_with_the_graph_is_reported(self):
        def swap(plan, objects):
            scope = plan["reservations"][f"access-endpoints/{SITE}/location/{SITE}/ot"]
            a, b = f"device/{SITE}/plc-01", f"device/{SITE}/plc-02"
            scope[a], scope[b] = scope[b], scope[a]
        self.assertIn("mfg-access-allocation", self.mutated(swap))

    def test_missing_or_out_of_range_reservations_are_reported(self):
        self.assertIn("mfg-address-allocation", self.mutated(
            lambda plan, objects: plan["allocations"].pop(SITE)))
        for scope in (f"access-endpoints/{SITE}/location/{SITE}",
                      f"access-endpoints/{SITE}/location/{SITE}/ot"):
            with self.subTest(scope=scope):
                self.assertIn("mfg-access-allocation", self.mutated(
                    lambda plan, objects, s=scope: plan["reservations"][s].__setitem__(
                        next(iter(plan["reservations"][s])), 99999)))

    def test_removed_shared_service_replica_is_reported(self):
        self.assertIn("dc-workload-inventory", self.mutated(
            lambda plan, objects: plan.__setitem__(
                "objects", [o for o in plan["objects"] if not o["key"].startswith("vm/dc-01/historian/002")])))

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
        ("mfg-site-status",
         lambda plan, objects: objects[f"site/{SITE}"]["attrs"].__setitem__("status", "planned")),
        ("mfg-address-allocation",
         lambda plan, objects: plan["allocations"].__setitem__(SITE, 0)),
        ("mfg-contract-inventory", lambda plan, objects: plan.__setitem__(
            "contracts", [c for c in plan["contracts"] if c["site"] != f"site/{SITE}"])),
        ("mfg-demand-report", lambda plan, objects: next(
            c for c in plan["contracts"] if c["site"] == f"site/{SITE}").__setitem__("plant", "elsewhere")),
        ("mfg-room-inventory", _extra_room),
        ("mfg-room-allocation", lambda plan, objects: plan["reservations"].__setitem__(
            f"plant-lines/{SITE}", {"line-01": 0})),
        ("mfg-room-placement", lambda plan, objects: objects[f"location/{SITE}/line-01"]["meta"]
         .__setitem__("position_m", [9, 28, 0])),
        ("mfg-closet-inventory", lambda plan, objects: objects[f"location/{SITE}"]["meta"]
         .__setitem__("space_type", "office")),
        ("mfg-endpoint-inventory", lambda plan, objects: plan.__setitem__(
            "objects", [o for o in plan["objects"] if not o["key"].startswith(f"device/{SITE}/hmi-02-2")])),
        ("mfg-endpoint-demand", lambda plan, objects: objects[f"device/{SITE}/plc-02"]["refs"]
         .__setitem__("role", "role/hmi")),
        ("mfg-endpoint-placement", lambda plan, objects: objects[f"device/{SITE}/scan-001"]["meta"]
         .__setitem__("network", "office")),
        ("mfg-endpoint-path", lambda plan, objects: objects[f"device/{SITE}/ot-access-01"]["attrs"]
         .__setitem__("status", "offline")),
        ("mfg-endpoint-route", lambda plan, objects: objects[f"device/{SITE}/scan-002"]["meta"]["placement"]
         .__setitem__("position_m", [1, 1, 0.8])),
        ("mfg-endpoint-address", lambda plan, objects: objects[f"ip/device/{SITE}/plc-01/if/eth0"]["attrs"]
         .__setitem__("status", "reserved")),
        ("mfg-access-inventory", _extra_switch),
        ("mfg-access-capacity", lambda plan, objects: objects[f"device/{SITE}/ot-access-01"]["meta"]
         .__setitem__("hardware", "leaf")),
        ("mfg-core-inventory", lambda plan, objects: objects[f"device/{SITE}/ot-dist-b"]["refs"]
         .__setitem__("role", "role/access")),
        ("mfg-uplink-path", lambda plan, objects: objects[
            f"device/{SITE}/ot-access-01/if/TenGigabitEthernet1/1/1"]["attrs"].__setitem__("enabled", False)),
        ("mfg-gateway-inventory", lambda plan, objects: objects[f"device/{SITE}/ot-dist-a/if/Vlan40"]["attrs"]
         .__setitem__("enabled", False)),
        ("mfg-zone-isolation", _leak_to_corporate_access),
        ("mfg-conduit-path", _widen_the_conduit),
        ("mfg-prefix-policy", lambda plan, objects: objects[f"prefix/{SITE}/supervisory"]["attrs"]
         .__setitem__("status", "reserved")),
        ("mfg-wan-inventory", lambda plan, objects: objects[f"circuit/{SITE}/b/1"]["refs"]
         .__setitem__("provider", "provider/a")),
        ("mfg-wan-capacity", lambda plan, objects: objects[f"circuit/{SITE}/a/1"]["attrs"]
         .__setitem__("status", "offline")),
        ("mfg-wan-diversity", lambda plan, objects: [
            cable["refs"].__setitem__("a", f"device/{SITE}/edge-a/if/wan2")
            for cable in plan["objects"]
            if cable["kind"] == "cable" and cable["refs"].get("b") == f"circuit/{SITE}/b/1/A"]),
        ("mfg-equipment-role", lambda plan, objects: objects[f"device/{SITE}/console-01"]["refs"]
         .__setitem__("role", "role/workstation")),
        ("mfg-equipment-placement", lambda plan, objects: objects[f"device/{SITE}/mgmt-01"]["refs"]
         .__setitem__("location", f"location/{SITE}/reception")),
        ("mfg-access-allocation", lambda plan, objects: plan["reservations"][
            f"access-endpoints/{SITE}/location/{SITE}/ot"].pop(f"device/{SITE}/plc-01")),
        ("mfg-ap-power", lambda plan, objects: [
            cable["refs"].__setitem__("a", f"device/{SITE}/access-02/if/TenGigabitEthernet1/1/4")
            for cable in plan["objects"]
            if cable["kind"] == "cable" and cable["refs"].get("b") == f"device/{SITE}/ap-pod-01/if/eth0"]),
        ("mfg-recipe", lambda plan, objects: plan["recipe"].__setitem__("plants", [])),
        ("mfg-site-inventory", lambda plan, objects: plan["recipe"]["plants"].append(
            dict(key="ghost", production_lines=1, warehouse_docks=0, office_staff=4))),
    ]

    def test_every_manufacturing_assertion_rejects_its_own_mutation(self):
        for code, change in self.MUTATIONS:
            with self.subTest(code=code):
                self.assertIn(code, self.mutated(change))

    def test_every_finding_code_in_the_validator_has_a_mutation(self):
        source = (ROOT / "estates/validate_manufacturing.py").read_text()
        declared = set(re.findall(r'report\("(mfg-[a-z-]+)"', source))
        covered = {code for code, _ in self.MUTATIONS}
        self.assertEqual(declared - covered, set(),
                         "every manufacturing finding code needs a mutation that provokes it")
        self.assertEqual(covered - declared, set(), "mutation table names a code the validator never reports")


class ManufacturingArtifactTests(unittest.TestCase):
    def test_cli_preview_generation_and_turbobulk_load_check(self):
        def call(*args):
            output, error = io.StringIO(), io.StringIO()
            with redirect_stdout(output), redirect_stderr(error):
                status = main(["--json", *map(str, args)])
            self.assertEqual(status, 0, output.getvalue() + error.getvalue())
            return json.loads(output.getvalue())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipe = root / "manufacturing.toml"
            recipe.write_text('profile = "manufacturing"\n\n[[plants]]\nkey = "riverbend"\n'
                              'production_lines = 2\nwarehouse_docks = 2\noffice_staff = 14\n')
            preview = call("plan", recipe)["intent"]
            self.assertEqual(preview["profile"], "manufacturing")
            self.assertEqual(preview["resolved"]["plants"]["source"], "supplied")
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
        self.assertIn(f"ipv6/prefix/{SITE}/process", objects)
        self.assertIn(f"ipv6/ip/device/{SITE}/plc-01/if/eth0", objects)

    def test_alternate_vendor_lines_generate_and_validate(self):
        plan = small(hardware={"access": "juniper", "leaf": "juniper", "ap": "aruba"})
        self.assertEqual(validate(plan), [])
        models = {o["attrs"]["model"] for o in plan["objects"] if o["kind"] == "device_type"}
        self.assertIn("EX3400-24P", models)
        self.assertNotIn("Catalyst 9200L-24P-4X", models)


if __name__ == "__main__":
    unittest.main()
