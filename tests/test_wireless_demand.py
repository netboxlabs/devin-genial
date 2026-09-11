"""Local wireless demand must grow real APs without moving earlier inventory."""
from copy import deepcopy
import unittest

from estates.generate import generate
from estates.model import DesignError, resolve_recipe
from estates.validate import validate


class WirelessDemandTests(unittest.TestCase):
    def school(self, **item):
        return dict(profile="school-district", schools=[dict(key="oak", classrooms=2,
                    administrative_staff=0, lab_seats=0) | item])

    def hospital(self, **item):
        return dict(profile="hospital-clinics", clinics=[], hospitals=[dict(key="central",
                    administrative_desks=0, imaging_rooms=0, wards=[dict(key="medical",beds=8,clinical_desks=4)]) | item])

    def test_local_defaults_are_explicit_and_resolve_idempotently(self):
        school = resolve_recipe(self.school(administrative_staff=13, lab_seats=4))
        zones = school["schools"][0]["wireless"]
        self.assertEqual(zones, {"classroom-001":dict(managed=21,guest=0),
            "classroom-002":dict(managed=21,guest=0), "admin-01":dict(managed=12,guest=0),
            "admin-02":dict(managed=1,guest=0), "computer-lab":dict(managed=0,guest=0)})
        hospital = resolve_recipe(self.hospital())
        self.assertEqual(hospital["hospitals"][0]["wireless"],
                         {"ward-medical":dict(managed=24,guest=0),"reception":dict(managed=4,guest=0)})
        clinic = resolve_recipe(self.hospital() | {"clinics":[dict(key="west",exam_rooms=5,administrative_desks=0,imaging_rooms=0)]})
        self.assertEqual(clinic["clinics"][0]["wireless"], {"exam-001":dict(managed=16,guest=0),
            "exam-002":dict(managed=4,guest=0),"reception":dict(managed=4,guest=0)})
        for recipe in (school,hospital,clinic):
            self.assertEqual(resolve_recipe(recipe), recipe)

    def test_bad_inputs_and_unknown_zone_keys_fail_before_expansion(self):
        cases = [None, [], {"absent":{}}, {"classroom-001":{"clients":2}},
                 {"classroom-001":{"managed":True}}, {"classroom-001":{"guest":-1}},
                 {"classroom-001":{"managed":129}}, {"classroom-001":{"managed":128,"guest":1}},
                 {"classroom-001":{"managed":1.5}}, {"classroom-001":[]}]
        for value in cases:
            with self.subTest(value=value), self.assertRaisesRegex(DesignError,"wireless"):
                generate(self.school(wireless=value))
        with self.assertRaisesRegex(DesignError,"4084"):
            resolve_recipe(self.school(classrooms=32,wireless={f"classroom-{n:03}":dict(managed=0,guest=128) for n in range(1,33)}))
        for value in (None, {"ward-absent":{}}, {"ward-medical":{"managed":True}}):
            with self.subTest(hospital=value), self.assertRaisesRegex(DesignError,"wireless"):
                generate(self.hospital(wireless=value))

    def test_power_boundary_adds_pair_and_later_wired_desk_uses_a_free_old_slot(self):
        previous = generate(self.school(classrooms=8, wired_seats_per_classroom=0,
                            wireless={f"classroom-{n:03}":dict(managed=96) for n in range(1,9)}))
        self.assertEqual(validate(previous), [])
        recipe = deepcopy(previous["recipe"])
        recipe["schools"][0]["wireless"]["classroom-001"]["managed"] = 97
        grown = generate(recipe,previous=previous)
        self.assertEqual(validate(grown), [])
        for plan, aps, switches in ((previous,24,2),(grown,25,4)):
            self.assertEqual(sum(o["refs"].get("role")=="role/ap" for o in plan["objects"]),aps)
            self.assertEqual(sum(o["refs"].get("role")=="role/access" for o in plan["objects"]),switches)
        scope = "access-endpoints/school-oak/location/school-oak"
        self.assertEqual(grown["reservations"][scope]["device/school-oak/ap-classroom-001-04"],38)
        recipe = deepcopy(grown["recipe"]);recipe["schools"][0]["administrative_staff"] = 1
        later = generate(recipe,previous=grown)
        self.assertEqual(validate(later), [])
        self.assertLess(later["reservations"][scope]["device/school-oak/admin-001"],38)
        for old,new in ((previous,grown),(grown,later)):
            objects = {o["key"]:o for o in new["objects"]}
            for obj in old["objects"]:
                if obj["kind"] in {"cable","ip_address","mac_address","journal_entry"} or obj["refs"].get("role")=="role/ap":
                    self.assertEqual(objects[obj["key"]],obj,obj["key"])
            for key,slot in old["reservations"][scope].items():
                self.assertEqual(new["reservations"][scope][key],slot)

    def test_local_guest_enable_preserves_group_channels_and_physical_identity(self):
        for raw,field,zone,sid in ((self.school(),"schools","classroom-001","school-oak"),
                                  (self.hospital(),"hospitals","ward-medical","hospital-central")):
            for ipv6 in (False,True):
                with self.subTest(profile=raw["profile"],ipv6=ipv6):
                    previous=generate(raw | ({"ipv6_pool":"3fff:a::/40"} if ipv6 else {}))
                    recipe=deepcopy(previous["recipe"])
                    recipe[field][0]["wireless"][zone]["guest"]=1
                    grown=generate(recipe,previous=previous)
                    self.assertEqual(validate(grown),[])
                    old,new=({o["key"]:o for o in plan["objects"]} for plan in (previous,grown))
                    self.assertLessEqual(old.keys(),new.keys())
                    guest=new[f"wireless-lan/{sid}/guest"]
                    self.assertEqual(guest["attrs"]["auth_type"],"open")
                    self.assertNotIn(guest["attrs"].get("auth_cipher"),{"aes","tkip"})
                    self.assertEqual(new[f"vlan/{sid}/guest"]["attrs"]["vid"],130)
                    self.assertEqual(new[f"prefix/{sid}/guest"]["refs"]["vrf"],"vrf/guest")
                    self.assertEqual(sum(o["kind"]=="ip_address" and o["refs"].get("vrf")=="vrf/guest"
                                         for o in new.values()),4 if ipv6 else 2)
                    self.assertFalse(any("guest" in o["key"] for o in new.values() if o["kind"]=="virtual_machine"))
                    for key,obj in old.items():
                        if obj["kind"] in {"wireless_lan_group","cable","ip_address","journal_entry"}:
                            self.assertEqual(new[key],obj,key)
                        if obj["kind"]=="interface" and obj["attrs"].get("rf_role")=="ap":
                            self.assertEqual(new[key]["attrs"]["rf_channel"],obj["attrs"]["rf_channel"],key)
                    self.assertEqual(generate(grown["recipe"],previous=grown),grown)

    def test_lab_growth_and_earlier_ward_do_not_remove_or_move_existing_aps(self):
        for raw,field in ((self.school(wireless={"classroom-001":dict(managed=40)}),"schools"),
                          (self.hospital(wireless={"ward-medical":dict(managed=97)}),"hospitals")):
            previous=generate(raw);recipe=deepcopy(previous["recipe"])
            if field=="schools":recipe[field][0]["lab_seats"]=4
            else:recipe[field][0]["wards"].insert(0,dict(key="aaa-new",beds=4,clinical_desks=2))
            grown=generate(recipe,previous=previous)
            self.assertEqual(validate(grown),[])
            new={o["key"]:o for o in grown["objects"]}
            for obj in previous["objects"]:
                if obj["refs"].get("role")=="role/ap":self.assertEqual(new[obj["key"]],obj)
            for zone,value in previous["recipe"][field][0]["wireless"].items():
                self.assertEqual(grown["recipe"][field][0]["wireless"][zone],value)

    def test_clinic_corridor_mounts_are_distinct_and_keep_both_pod_anchors(self):
        raw=self.hospital() | {"clinics":[dict(key="west",exam_rooms=8,administrative_desks=0,imaging_rooms=0)]}
        previous=generate(raw);recipe=deepcopy(previous["recipe"])
        recipe["clinics"][0]["wireless"].update({f"exam-{n:03}":dict(managed=128,guest=0) for n in (1,2)})
        grown=generate(recipe,previous=previous)
        self.assertEqual(validate(grown),[])
        old,new=({o["key"]:o for o in p["objects"]} for p in (previous,grown))
        aps=[o for o in new.values() if o["key"].startswith("device/clinic-west/ap-exam-") and o["kind"]=="device"]
        self.assertEqual(len(aps),8)
        self.assertEqual(len({tuple(o["meta"]["placement"]["position_m"]) for o in aps}),8)
        for key in ("device/clinic-west/ap-exam-001","device/clinic-west/ap-exam-002"):
            self.assertEqual(new[key],old[key])

    def test_wireless_reduction_and_frozen_graph_tampering_fail(self):
        for raw,field,zone in ((self.school(wireless={"classroom-001":dict(managed=40,guest=8)}),"schools","classroom-001"),
                              (self.hospital(wireless={"ward-medical":dict(managed=80,guest=8)}),"hospitals","ward-medical")):
            previous=generate(raw)
            for count in ("managed","guest"):
                recipe=deepcopy(previous["recipe"]);recipe[field][0]["wireless"][zone][count]-=1
                with self.assertRaisesRegex(DesignError,"new baseline"):generate(recipe,previous=previous)
            bad=deepcopy(previous)
            next(o for o in bad["objects"] if o["refs"].get("role")=="role/ap")["attrs"]["description"]="edited"
            with self.assertRaisesRegex(DesignError,"does not reproduce"):generate(bad["recipe"],previous=bad)

    def test_independent_checks_reject_missing_aps_slots_mounts_and_guest_prefix(self):
        for raw,field,sid,code in ((self.school(wireless={"classroom-001":dict(managed=40,guest=8)}),"schools","school-oak","school"),
                                  (self.hospital(wireless={"ward-medical":dict(managed=80,guest=8)}),"hospitals","hospital-central","hospital")):
            baseline=generate(raw)
            for mutation in ("missing-ap","slot","mount","prefix","vlan","unrequested-guest","zone"):
                with self.subTest(profile=code,mutation=mutation):
                    p=deepcopy(baseline);objects={o["key"]:o for o in p["objects"]}
                    ap=next(o for o in p["objects"] if o["kind"]=="device" and o["refs"].get("role")=="role/ap")
                    if mutation=="missing-ap":
                        p["objects"].remove(ap);p["contracts"]=[]
                        for o in p["objects"]:o["meta"]={}
                        expected=f"{code}-endpoint-inventory"
                    elif mutation=="slot":
                        scope=f"access-endpoints/{sid}/{ap['meta']['placement']['cable_origin']}"
                        p["reservations"][scope].pop(ap["key"]);expected=f"{code}-access-allocation"
                    elif mutation=="mount":
                        ap["meta"]["placement"]["position_m"][0]+=1
                        expected="school-ap-mount" if code=="school" else "hospital-endpoint-route"
                    elif mutation=="prefix":
                        objects[f"prefix/{sid}/guest"]["attrs"]["prefix"]="10.250.0.0/20";expected=f"{code}-prefix-policy"
                    elif mutation=="vlan":
                        objects[f"vlan/{sid}/guest"]["attrs"]["vid"]=131;expected=f"{code}-prefix-policy"
                    elif mutation=="unrequested-guest":
                        for value in p["recipe"][field][0]["wireless"].values():value["guest"]=0
                        expected=f"{code}-guest-unrequested"
                    else:
                        p["recipe"][field][0]["wireless"].pop(next(iter(p["recipe"][field][0]["wireless"])))
                        expected="school-wireless-demand" if code=="school" else "hospital-recipe"
                    self.assertIn(expected,{f["code"] for f in validate(p)})


if __name__=="__main__":unittest.main()
