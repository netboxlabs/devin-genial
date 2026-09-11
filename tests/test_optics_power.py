"""Installed component reserves must reach real inlet budgets once per host."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from estates import poe
from estates.generate import generate
from estates.model import hardware_catalog


class OpticsPowerTests(unittest.TestCase):
    def fixture(self, installations, *, pd=False):
        """Small arithmetic fixture; topology health is covered by generated cases."""
        catalog=hardware_catalog();objects={};contracts=[{'power_redundancy':[]}]
        def add(key,kind,attrs,refs=None):
            objects[key]=dict(key=key,kind=kind,attrs=attrs,refs=refs or {},meta={})
        for alias,parts in installations.items():
            spec=catalog['models'][alias];device=f'device/{alias}'
            add(device,'device',{'name':alias},{'device_type':f'hardware/{alias}'})
            contracts[0]['power_redundancy'].append({'device':device,'planned_watts':0})
            for inlet in spec['power_ports']:
                add(f"{device}/power/{inlet['name']}",'power_port',dict(inlet),{'device':device})
            for n,part_id in enumerate(parts):
                part=catalog['optics']['parts'][part_id];manufacturer=f"manufacturer/{part['manufacturer']}"
                add(manufacturer,'manufacturer',{'name':part['manufacturer']})
                typ=f"module-type/{part['manufacturer']}/{part['model']}"
                add(typ,'module_type',{'model':part['model']},{'manufacturer':manufacturer})
                add(f'{device}/optic/{n}','module',{'status':'active'},{'module_type':typ,'device':device})
        if pd:
            add('device/ap','device',{'name':'ap'},{'device_type':'hardware/ap'})
            add('device/ap/if/eth0','interface',{'name':'eth0','type':'1000base-t'},{'device':'device/ap'})
            port=catalog['models']['access']['access_ports'][0]
            add(f'device/access/if/{port}','interface',{'name':port,'type':'1000base-t'},{'device':'device/access'})
            add('cable/pd','cable',{'type':'cat6','status':'connected'},{'a':'device/ap/if/eth0','b':f'device/access/if/{port}'})
        return SimpleNamespace(catalog=catalog,objects=objects,contracts=contracts)

    def draws(self,world,alias):
        device=f'device/{alias}'
        return [world.objects[f"{device}/power/{port['name']}"]['attrs']
                for port in world.catalog['models'][alias]['power_ports']]

    def test_exact_separate_rounding_and_odd_total_split(self):
        # 120 + ceil(30W*1.25) + ceil(1W*1.25) = 160, not 159.
        world=self.fixture({'access':['cisco-10g-lr']},pd=True)
        poe.enrich(world)
        self.assertEqual([p['allocated_draw'] for p in self.draws(world,'access')],[80,80])
        self.assertEqual(world.objects['device/access']['meta']['planned_watts'],160)
        self.assertEqual(world.contracts[0]['power_redundancy'][0]['planned_watts'],160)
        # Sum four modules before rounding: 120 +38 +5 =163, split82/81.
        world=self.fixture({'access':['cisco-10g-lr']*4},pd=True)
        poe.enrich(world)
        self.assertEqual([p['allocated_draw'] for p in self.draws(world,'access')],[82,81])
        self.assertEqual([p['maximum_draw'] for p in self.draws(world,'access')],[163,163])

    def test_manufacturer_disambiguation_and_captive_end_power(self):
        world=self.fixture({'access':['cisco-10g-lr']*2,'leaf':['arista-10g-lr']*2})
        poe.enrich(world)
        self.assertEqual(self.draws(world,'access')[0]['maximum_draw'],123)
        self.assertEqual(self.draws(world,'leaf')[0]['maximum_draw'],165)
        world=self.fixture({'leaf':['arista-10g-lr']*2+['arista-100g-lr4']*2+['arista-100g-aoc-3m']})
        poe.enrich(world)
        self.assertEqual([p['allocated_draw'] for p in self.draws(world,'leaf')],[91,90])
        self.assertEqual(self.draws(world,'leaf')[0]['maximum_draw'],181)

    def test_inactive_disconnected_installed_modules_retain_load_and_repeat_is_exact(self):
        world=self.fixture({'server':['reference-10g-lr']*2})
        for obj in world.objects.values():
            if obj['kind']=='module':obj['attrs']['status']='offline'
        poe.enrich(world)
        self.assertEqual(self.draws(world,'server')[0]['maximum_draw'],255)
        before=deepcopy((world.objects,world.contracts))
        poe.enrich(world)
        self.assertEqual((world.objects,world.contracts),before)

    def test_no_optics_preserves_previous_poe_output(self):
        world=self.fixture({'access':[]},pd=True)
        poe.enrich(world)
        self.assertEqual([p['allocated_draw'] for p in self.draws(world,'access')],[79,79])
        self.assertEqual(self.draws(world,'access')[0]['description'],
            'Chassis plus reserved PoE AC planning allowance; each supply reserves the full device total')
        empty=self.fixture({'server':[]})
        before=deepcopy((empty.objects,empty.contracts))
        poe.enrich(empty)
        self.assertEqual((empty.objects,empty.contracts),before)

    def test_generated_all_profiles_repeat_power_without_mutating_other_records(self):
        from estates.validate import validate
        for profile in ('regional-bank','enterprise-data-center','school-district','hospital-clinics','provider-backbone'):
            with self.subTest(profile=profile):
                plan=generate({'profile':profile})
                self.assertEqual(validate(plan),[])
                world=SimpleNamespace(catalog=hardware_catalog(),objects={o['key']:o for o in plan['objects']},contracts=plan['contracts'])
                before=deepcopy(plan)
                poe.enrich(world)
                self.assertEqual(plan,before)

    def test_independent_power_validation_rejects_old_total_swapped_split_and_extra_max(self):
        from estates.validate import validate
        for profile in ('regional-bank','enterprise-data-center','provider-backbone'):
            baseline=generate({'profile':profile})
            self.assertEqual(validate(baseline),[])
            objects={o['key']:o for o in baseline['objects']}
            if profile=='provider-backbone':
                owner=next(o for o in objects.values() if o['kind']=='device' and o['refs'].get('device_type')=='hardware/provider-edge')
            else:
                owner=next(o for o in objects.values() if o['kind']=='device' and o['refs'].get('device_type')=='hardware/server')
            ports=[o['key'] for o in objects.values() if o['kind']=='power_port' and o['refs'].get('device')==owner['key']]
            for mutation in ('old-total','unequal-split','extra-max'):
                plan=deepcopy(baseline);rows={o['key']:o for o in plan['objects']}
                if mutation=='old-total':
                    for key in ports:rows[key]['attrs']['allocated_draw']-=1
                elif mutation=='unequal-split':
                    rows[ports[0]]['attrs']['allocated_draw']+=1
                    rows[ports[1]]['attrs']['allocated_draw']-=1
                else:rows[ports[0]]['attrs']['maximum_draw']+=1
                plan['contracts']=[]
                for obj in plan['objects']:obj['meta']={}
                codes={f['code'] for f in validate(plan)}
                self.assertTrue(codes & {'power-allocation','power-failover','dc-power-allocation','dc-power-failover','provider-psu-inventory'},(profile,mutation,codes))

    def test_main_validation_analyzes_each_extra_once(self):
        from estates import validate as validation
        from estates.validate_optics import analyze as optical_analysis
        from estates.validate_poe import analyze as pd_analysis
        plan=generate({'profile':'hospital-clinics'})
        with patch.object(validation,'analyze_optics',wraps=optical_analysis) as optical, patch.object(validation,'analyze_poe',wraps=pd_analysis) as pd, \
             patch('estates.validate_hospital.analyze_optics',side_effect=AssertionError('duplicate hospital optics scan')), \
             patch('estates.validate_hospital.analyze_poe',side_effect=AssertionError('duplicate hospital PoE scan')), \
             patch('estates.validate_datacenter.analyze_optics',side_effect=AssertionError('duplicate DC optics scan')), \
             patch('estates.validate_datacenter.analyze_poe',side_effect=AssertionError('duplicate DC PoE scan')):
            self.assertEqual(validation.validate(plan),[])
            self.assertEqual(optical.call_count,1)
            self.assertEqual(pd.call_count,1)


if __name__=='__main__':unittest.main()
