"""School composition must reflect demand and preserve an inspectable estate."""

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from estates.validate_optics import analyze as analyze_optics
from estates.validate_poe import analyze as analyze_poe

from estates.__main__ import main
from estates.generate import generate
from estates.model import DesignError, canonical, hardware_catalog
from estates.report import markdown
from estates.validate import validate


class SchoolTests(unittest.TestCase):
    def test_default_profile_connects_school_demand_without_bank_story(self):
        plan = generate({"profile":"school-district"})
        self.assertEqual(validate(plan), [])
        self.assertEqual(canonical(generate(plan["recipe"])), canonical(plan))
        objects = plan["objects"]
        self.assertEqual(len([o for o in objects if o["kind"] == "site"]), 3)
        self.assertEqual(len([o for o in objects if o["kind"] == "wireless_lan"]), 4)
        sites = [o for o in objects if o["kind"] == "site"]
        self.assertEqual(len({(o["meta"]["geography"]["city"],o["refs"]["region"],o["attrs"]["time_zone"]) for o in sites}),1)
        self.assertEqual(len({o["attrs"]["physical_address"] for o in sites}),len(sites))
        self.assertNotIn("role/atm", {o["key"] for o in objects})
        self.assertEqual({o["meta"]["service"] for o in objects if o["kind"] == "virtual_machine"},
                         {"identity","dns","learning-portal","files","monitoring"})
        for forbidden in ("Birch Bank", "teller-api", "atm-switch", "Banking and business"):
            self.assertNotIn(forbidden, canonical(plan).decode())
        guide = markdown(plan)
        for phrase in ("School population and access", "Planned wireless students", "RADIUS", "Replica group placement"):
            self.assertIn(phrase, guide)

    def test_population_drives_district_service_groups(self):
        plan = generate({"profile":"school-district","schools":[
            {"key":key,"classrooms":16,"students_per_classroom":36,"wired_seats_per_classroom":8,
             "administrative_staff":12,"lab_seats":24,"wan_peak_mbps":300} for key in ("north","south")]})
        self.assertEqual(validate(plan), [])
        counts = {service:sum(o["kind"] == "virtual_machine" and o["meta"].get("service") == service for o in plan["objects"])
                  for service in ("identity","dns","learning-portal","files","monitoring")}
        self.assertEqual(counts, {"identity":6,"dns":2,"learning-portal":6,"files":4,"monitoring":2})
        radius = [o for o in plan["objects"] if o["kind"] == "service" and o["attrs"]["name"] == "radius"]
        self.assertEqual(len(radius), 6)
        self.assertTrue(all(o["attrs"]["protocol"] == "udp" and o["attrs"]["ports"] == [1812,1813] for o in radius))

    def test_growth_across_switch_floor_and_service_boundaries_preserves_allocations(self):
        for patching in ("direct","panels"):
            with self.subTest(patching=patching):
                before = generate({"profile":"school-district","patching":patching,"schools":[
                    {"key":"oak","classrooms":8,"students_per_classroom":36,"wired_seats_per_classroom":4,
                     "administrative_staff":0,"lab_seats":0,"wan_peak_mbps":300}]})
                recipe = deepcopy(before["recipe"])
                recipe["schools"][0].update(classrooms=16,administrative_staff=13,lab_seats=24)
                recipe["schools"].insert(0,{"key":"aaa-new","classrooms":1,"administrative_staff":0,"lab_seats":0})
                after = generate(recipe, previous=before)
                self.assertEqual(validate(after), [])
                old,new = ({o["key"]:o for o in p["objects"]} for p in (before,after))
                self.assertTrue(old.keys() <= new.keys())
                occupied = {end for o in before["objects"] if o["kind"] == "cable" for end in o["refs"].values()}
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
                    elif obj["kind"] in {"virtual_machine","ip_address","cable","rack","location","wireless_lan","wireless_lan_group"} or key in occupied or obj["attrs"].get("rf_role") == "ap":
                        self.assertEqual(new[key],obj,key)
                    if obj["kind"] == "device":
                        for field in ("location","rack"):
                            self.assertEqual(new[key]["refs"].get(field),obj["refs"].get(field),key)
                for scope,items in before["reservations"].items():
                    for key,slot in items.items():
                        self.assertEqual(after["reservations"][scope][key],slot)

    def test_order_and_seed_do_not_reroll_unrelated_allocations(self):
        plan = generate({"profile":"school-district"})
        recipe = deepcopy(plan["recipe"]); recipe["schools"].reverse()
        self.assertEqual(canonical(generate(recipe)),canonical(plan))
        for seed in (0,7,12345):
            variant = generate(recipe | {"seed":seed})
            self.assertEqual(validate(variant), [])
            sites = [o for o in variant["objects"] if o["kind"] == "site"]
            self.assertEqual(len({o["refs"]["region"] for o in sites}),1)
            self.assertEqual(variant["reservations"],plan["reservations"])
            self.assertEqual(canonical(generate(variant["recipe"])),canonical(variant))
            self.assertNotEqual(canonical(variant),canonical(plan))

    def test_invalid_requests_and_retirements_are_actionable(self):
        cases = [({"schools":[]}, "1–64"), ({"schools":[{"key":"x","classrooms":True}]}, "classrooms"),
                 ({"schools":[{"key":"x","classrooms":10**100}]}, "classrooms"),
                 ({"schools":[{"key":"x","students_per_classroom":2}]}, "wired classroom seats"),
                 ({"schools":[{"key":"x","classrooms":1,"lab_seats":36}]}, "remaining enrollment"),
                 ({"schools":[{"key":"north-west"},{"key":"northwest"}]}, "distinct without hyphens"),
                 ({"schools":[{"key":"x","wan_peak_mbps":800}],"reserve_fraction":.4}, "1 Gbps"),
                 ({"address_pool":"192.168.0.0/16"}, "/16 site reservations"),
                 ({"demo":"invent-a-workflow"}, "not yet implemented")]
        for changes,message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(DesignError,message):
                generate({"profile":"school-district"} | changes)
        before = generate({"profile":"school-district"})
        for field,value in (("classrooms",1),("administrative_staff",0),("lab_seats",0),
                            ("wan_peak_mbps",200),("wired_seats_per_classroom",5)):
            recipe = deepcopy(before["recipe"]); recipe["schools"][0][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(DesignError,"new baseline"):
                generate(recipe, previous=before)
        recipe = deepcopy(before["recipe"]); recipe["schools"].pop()
        with self.assertRaisesRegex(DesignError,"new baseline"):
            generate(recipe,previous=before)

    def test_cli_preview_and_artifact_show_nested_defaults_and_real_headcounts(self):
        def call(*args):
            output,error = io.StringIO(),io.StringIO()
            with redirect_stdout(output),redirect_stderr(error):
                status = main(["--json",*map(str,args)])
            self.assertEqual(status,0,output.getvalue()+error.getvalue())
            return json.loads(output.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); recipe = root/'school.toml'
            recipe.write_text('profile="school-district"\n[[schools]]\nkey="oak"\nclassrooms=2\n')
            preview = call('plan',recipe)['intent']
            school = preview['schools'][0]
            self.assertEqual(school['fields']['classrooms']['source'],'supplied')
            self.assertEqual(school['fields']['administrative_staff']['source'],'default')
            self.assertEqual(school['demand']['enrollment'],48)
            self.assertEqual(school['demand']['wired_student_seats'],8)
            call('generate',recipe,'--out',root/'build')
            self.assertEqual(json.loads((root/'build/intent.json').read_text()),preview)
            call('check',root/'build/plan.json')
            call('build',root/'build/plan.json','--out',root/'frozen')
            frozen = json.loads((root/'frozen/intent.json').read_text())
            self.assertEqual(frozen['schools'][0]['fields']['classrooms']['source'],'unknown (frozen plan)')


if __name__ == "__main__":
    unittest.main()
