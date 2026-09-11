"""Care-unit composition, actual inventory and ordinary-growth guarantees."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
import tomllib
import unittest

from estates.validate_optics import analyze as analyze_optics
from estates.validate_poe import analyze as analyze_poe

from estates.generate import generate
from estates.model import DesignError, canonical, hardware_catalog
from estates.validate import validate


def compact(patching="direct"):
    return dict(profile="hospital-clinics", patching=patching,
        hospitals=[dict(key="central", administrative_desks=0, imaging_rooms=0, wan_peak_mbps=300,
                        wards=[dict(key="medical", beds=4, clinical_desks=2)])],
        clinics=[dict(key="west", exam_rooms=4, administrative_desks=0, imaging_rooms=0, wan_peak_mbps=100)])


class HospitalTests(unittest.TestCase):
    def test_example_is_connected_healthcare_inventory_and_frozen_reproduces(self):
        raw = tomllib.loads((Path(__file__).parents[1]/"profiles/hospital-clinics.toml").read_text())
        plan = generate(raw)
        self.assertEqual(validate(plan), [])
        self.assertEqual(canonical(generate(plan["recipe"])),canonical(plan))
        self.assertEqual(canonical(generate(plan["recipe"], previous=plan)),canonical(plan))
        kinds = Counter(obj["kind"] for obj in plan["objects"])
        self.assertEqual(kinds["site"],4)
        self.assertEqual(kinds["wireless_lan"],3)
        actual = {obj["key"]:obj for obj in plan["objects"]}
        monitors = [obj for obj in actual.values() if obj["kind"] == "device" and obj["refs"]["role"] == "role/medical-device"]
        modalities = [obj for obj in actual.values() if obj["kind"] == "device" and obj["refs"]["role"] == "role/imaging-device"]
        self.assertEqual(len(monitors),28)
        self.assertEqual(len(modalities),3)
        for obj in monitors+modalities:
            self.assertEqual(obj["refs"]["device_type"],"hardware/endpoint")
            self.assertIn("Reference ",obj["attrs"]["description"])
            ip = actual[obj["refs"]["primary_ip4"]]
            self.assertEqual(ip["refs"]["assigned_object"],f"{obj['key']}/if/eth0")
        cameras = [obj for obj in actual.values() if obj["kind"] == "device" and obj["refs"]["role"] == "role/camera"]
        self.assertEqual(len(cameras),12)
        self.assertTrue(all(actual[obj["refs"]["location"]]["meta"]["space_type"] in {"reception","corridor"} for obj in cameras))
        sites = [obj for obj in actual.values() if obj["kind"] == "site"]
        self.assertEqual(len({(obj["refs"]["region"],obj["attrs"]["time_zone"],obj["meta"]["geography"]["city"]) for obj in sites}),1)
        self.assertEqual(len({obj["attrs"]["physical_address"] for obj in sites}),4)
        for forbidden in ("role/atm", "teller-api", "Birch Bank", "Banking and business"):
            self.assertNotIn(forbidden,canonical(plan).decode())

    def test_clinical_and_imaging_demand_cross_actual_service_boundaries(self):
        hospitals = [dict(key=key, wards=[dict(key=f"ward-{i}",beds=16,clinical_desks=8) for i in range(7)],
                         administrative_desks=48,imaging_rooms=8,wan_peak_mbps=600) for key in ("north","south")]
        plan = generate(dict(profile="hospital-clinics",hospitals=hospitals,
                             clinics=[dict(key="imaging",exam_rooms=1,administrative_desks=0,imaging_rooms=1)]))
        self.assertEqual(validate(plan), [])
        vms = [obj for obj in plan["objects"] if obj["kind"] == "virtual_machine"]
        self.assertEqual(Counter(obj["meta"]["service"] for obj in vms),
                         {"identity":2,"dns":2,"clinical-records":4,"imaging-archive":4,"monitoring":4})
        archives = [obj for obj in plan["objects"] if obj["kind"] == "service" and obj["attrs"]["name"] == "imaging-archive"]
        self.assertEqual(len(archives),4)
        self.assertTrue(all(obj["attrs"]["protocol"] == "tcp" and obj["attrs"]["ports"] == [11112] and obj["refs"].get("ipaddresses") for obj in archives))

    def test_growth_preserves_old_rooms_ports_addresses_and_dated_records(self):
        for patching in ("direct","panels"):
            with self.subTest(patching=patching):
                before = generate(compact(patching))
                raw = deepcopy(before["recipe"])
                raw["hospitals"][0].update(administrative_desks=13,imaging_rooms=2)
                raw["hospitals"][0]["wards"][0].update(beds=16,clinical_desks=8)
                raw["hospitals"][0]["wards"].insert(0,dict(key="aaa-new",beds=12,clinical_desks=6))
                raw["clinics"][0].update(exam_rooms=17,administrative_desks=13,imaging_rooms=2)
                raw["clinics"].insert(0,dict(key="aaa-new",exam_rooms=1,administrative_desks=0,imaging_rooms=0))
                after = generate(raw,previous=before)
                self.assertEqual(validate(after), [])
                old,new = ({obj["key"]:obj for obj in plan["objects"]} for plan in (before,after))
                self.assertTrue(old.keys() <= new.keys())
                occupied = {end for obj in before["objects"] if obj["kind"] == "cable" for end in obj["refs"].values()}
                extras = []
                for plan in (before, after):
                    _, optics = analyze_optics(plan)
                    _, pd = analyze_poe(plan, hardware_catalog())
                    extras.append({key: optics.get(key, 0) + pd.get(key, 0) for key in optics.keys() | pd.keys()})
                for key,obj in old.items():
                    owner = obj["refs"].get("device")
                    delta = extras[1].get(owner, 0) - extras[0].get(owner, 0)
                    if obj["kind"] == "power_port" and delta > 0:
                        actual, expected = deepcopy(new[key]), deepcopy(obj)
                        self.assertEqual(actual["attrs"]["maximum_draw"] - expected["attrs"]["maximum_draw"], delta)
                        for field in ("allocated_draw", "maximum_draw"):
                            self.assertGreaterEqual(actual["attrs"][field], expected["attrs"][field], key)
                            actual["attrs"].pop(field); expected["attrs"].pop(field)
                        actual["attrs"].pop("description", None); expected["attrs"].pop("description", None)
                        self.assertEqual(actual, expected, key)
                    elif obj["kind"] in {"location","rack","cable","ip_address","virtual_machine","wireless_lan","wireless_lan_group","journal_entry","contact","contact_assignment"} or key in occupied or obj["attrs"].get("rf_role") == "ap":
                        self.assertEqual(new[key],obj,key)
                    if obj["kind"] == "device":
                        for field in ("location","rack"):
                            self.assertEqual(new[key]["refs"].get(field),obj["refs"].get(field),key)
                self.assertEqual(after["reservations"]["healthcare-wards/hospital-central"],{"medical":0,"aaa-new":1})
                for scope,items in before["reservations"].items():
                    for key,slot in items.items():
                        self.assertEqual(after["reservations"][scope][key],slot)
                self.assertEqual(canonical(generate(after["recipe"],previous=after)),canonical(after))

    def test_seed_and_input_order_preserve_local_allocation_policy(self):
        plan = generate({"profile":"hospital-clinics"})
        raw = deepcopy(plan["recipe"])
        raw["hospitals"][0]["wards"].reverse(); raw["clinics"].reverse()
        self.assertEqual(canonical(generate(raw)),canonical(plan))
        for seed in (0,7,12345):
            variant = generate(raw | {"seed":seed})
            self.assertEqual(validate(variant), [])
            self.assertEqual(variant["reservations"],plan["reservations"])
            self.assertEqual(canonical(generate(variant["recipe"])),canonical(variant))
            self.assertNotEqual(canonical(variant),canonical(plan))

    def test_full_facility_bounds_and_maximum_names_fit_real_capacity(self):
        # Device.name=64 and PrimaryModel.description=200 in pinned NetBox4.7:
        # netbox/dcim/models/devices.py; netbox/netbox/models/__init__.py.
        for patching,reserve in (("direct",.1),("panels",.4)):
            raw = dict(profile="hospital-clinics",patching=patching,reserve_fraction=reserve,namespace="n"*20,
                hospitals=[dict(key="h"*20,administrative_desks=48,imaging_rooms=8,wan_peak_mbps=600,
                    wards=[dict(key=("w"*18)+f"{i:02}",beds=16,clinical_desks=8) for i in range(7)])],
                clinics=[dict(key="c"*20,exam_rooms=24,administrative_desks=24,imaging_rooms=4,wan_peak_mbps=600)])
            plan = generate(raw)
            self.assertEqual(validate(plan), [])
            for obj in plan["objects"]:
                if obj["kind"] == "device":
                    self.assertLessEqual(len(obj["attrs"]["name"]),64,obj["key"])
                    self.assertLessEqual(len(obj["attrs"]["description"]),200,obj["key"])
                elif obj["kind"] == "location":
                    self.assertNotIn("/",obj["attrs"]["slug"])
                    self.assertLessEqual(len(obj["attrs"]["slug"]),100,obj["key"])
                elif obj["kind"] == "ip_address":
                    self.assertTrue(all(len(label)<=63 for label in obj["attrs"].get("dns_name","").split(".")),obj["key"])

    def test_invalid_demands_and_impossible_combined_requests_fail_explicitly(self):
        cases = [({"hospitals":[]},"1–8"), ({"hospitals":[{"key":"x","wards":[]}]},"1–7"),
                 ({"hospitals":[{"key":"x","wards":[{"key":f"w{i}"} for i in range(8)]}]},"1–7"),
                 ({"hospitals":[{"key":"x","wards":[{"key":"a","beds":True}]}]},"beds"),
                 ({"hospitals":[{"key":"x","wards":[{"key":"a","clinical_desks":10**1000}]}]},"clinical_desks"),
                 ({"hospitals":[{"key":"x","wards":[{"key":"north-west"},{"key":"northwest"}]}]},"distinct without hyphens"),
                 ({"hospitals":[{"key":"x","wards":[{"key":"access"},{"key":"as"}]}]},"device names collide"),
                 ({"clinics":[{"key":"x","exam_rooms":25}]},"exam_rooms"),
                 ({"clinics":[{"key":"x","imaging_rooms":False}]},"imaging_rooms"),
                 ({"clinics":None},"0–64"), ({"clinics":[{"key":"x","beds":8}]},"supported demand"),
                 ({"reserve_fraction":.4,"hospitals":[{"key":"x","wan_peak_mbps":800}]},"1 Gbps"),
                 ({"address_pool":"192.168.0.0/16"},"site reservations"),
                 ({"clinics":[{"key":f"c{i}","wan_peak_mbps":800} for i in range(21)]},"16,000 Mbps"),
                 ({"demo":"unimplemented"},"not yet implemented")]
        for changes,message in cases:
            with self.subTest(changes=changes),self.assertRaisesRegex(DesignError,message):
                generate({"profile":"hospital-clinics"} | changes)

    def test_removal_reduction_renewal_and_forged_previous_do_not_masquerade_as_growth(self):
        before = generate({"profile":"hospital-clinics"})
        variants = []
        for field,value in (("administrative_desks",0),("imaging_rooms",0),("wan_peak_mbps",500)):
            raw = deepcopy(before["recipe"]); raw["hospitals"][0][field] = value; variants.append(raw)
        for field,value in (("beds",4),("clinical_desks",2)):
            raw = deepcopy(before["recipe"]); raw["hospitals"][0]["wards"][0][field] = value; variants.append(raw)
        raw = deepcopy(before["recipe"]); raw["hospitals"][0]["wards"].pop(); variants.append(raw)
        raw = deepcopy(before["recipe"]); raw["clinics"].pop(); variants.append(raw)
        raw = deepcopy(before["recipe"]); raw["clinics"][0]["exam_rooms"] = 1; variants.append(raw)
        raw = deepcopy(before["recipe"]); raw["clinics"][0]["wan_peak_mbps"] = 300; variants.append(raw)
        for raw in variants:
            with self.subTest(raw=raw),self.assertRaisesRegex(DesignError,"new baseline"):
                generate(raw,previous=before)
        broken = deepcopy(before)
        next(obj for obj in broken["objects"] if obj["kind"] == "device" and obj["refs"]["role"] == "role/medical-device")["refs"]["location"] = "location/hospital-central/reception"
        with self.assertRaisesRegex(DesignError,"does not reproduce"):
            generate(before["recipe"],previous=broken)


if __name__ == "__main__":
    unittest.main()
