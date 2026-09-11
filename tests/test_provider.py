"""Physical growth properties of the private-L3 provider composition."""

from collections import Counter, defaultdict
from copy import deepcopy
import ipaddress
from pathlib import Path
import tomllib
import unittest

from estates.validate_optics import analyze as analyze_optics

from estates.generate import generate
from estates.model import DesignError, canonical, hardware_catalog
from estates.provider import _capacity, resolve
from estates.validate import validate


ROOT = Path(__file__).parents[1]


def example(patching="direct"):
    raw = tomllib.loads((ROOT/"profiles/provider-backbone.toml").read_text())
    raw["patching"] = patching
    return raw


def physical_router_graph(plan):
    """Derive edges through actual cables and Circuit A/Z, without contracts."""
    objects = {o["key"]:o for o in plan["objects"]}
    routers = {k for k,o in objects.items() if o["kind"]=="device" and o["refs"].get("role")=="role/provider-edge"}
    owner = {k:o["refs"].get("device") for k,o in objects.items() if o["kind"]=="interface"}
    peers,edges = {},set()
    for o in objects.values():
        if o["kind"]=="cable" and o["attrs"]["status"]=="connected":
            a,b=o["refs"]["a"],o["refs"]["b"]; peers[a]=b;peers[b]=a
            if owner.get(a) in routers and owner.get(b) in routers:
                edges.add(tuple(sorted((owner[a],owner[b]))))
    for k,o in objects.items():
        if o["kind"]=="circuit":
            a,b=owner.get(peers.get(k+"/A")),owner.get(peers.get(k+"/Z"))
            if a in routers and b in routers: edges.add(tuple(sorted((a,b))))
    return routers,edges


def connected(nodes,edges):
    if not nodes:return False
    graph=defaultdict(set)
    for a,b in edges:
        if a in nodes and b in nodes:graph[a].add(b);graph[b].add(a)
    reached={next(iter(nodes))};front=list(reached)
    while front:
        for peer in graph[front.pop()]-reached: reached.add(peer);front.append(peer)
    return reached==nodes


class ProviderTests(unittest.TestCase):
    def test_report_counts_actual_pop_carriers_without_metadata(self):
        from estates.report import markdown

        plan = generate(example())
        objects = {obj['key']: obj for obj in plan['objects']}
        plan['contracts'] = []
        for obj in objects.values():
            obj['meta'] = {}
        site = objects['circuit/backbone/seed-01/A']['refs']['termination']
        site_name = objects[site]['attrs']['name']
        provider = objects['circuit/backbone/seed-01']['refs']['provider']
        provider_name = objects[provider]['attrs']['name']

        def pop_row():
            table = markdown(plan).split('Actual inter-PoP carriers', 1)[1].split('Private service', 1)[0]
            return next(row for row in table.splitlines() if row.startswith(f'| {site_name} |'))

        self.assertIn(f'{provider_name} (2 spans)', pop_row())
        # Moving only the real procurement reference must change the count;
        # neither seed ordinals nor absent emitted contracts determine it.
        other = objects['circuit/backbone/seed-02']['refs']['provider']
        objects['circuit/backbone/seed-03']['refs']['provider'] = other
        self.assertIn(f'{provider_name} (1 span)', pop_row())
        self.assertIn(f"{objects[other]['attrs']['name']} (1 span)", pop_row())
        self.assertIn('Per-PoP carrier diversity is not guaranteed', markdown(plan))

    def test_capacity_core_keeps_opposing_full_duplex_flows_separate(self):
        # Aggregate mathematical flows test the shared capacity core, not a
        # public recipe with impossible 60G individual customer attachments.
        graph=[(f"circuit/backbone/{i}",a,b,100000) for i,(a,b) in enumerate((("a","b"),("b","c"),("c","a")))]
        attachments=[dict(customer="east",router="a",hub=False,peak_mbps=60000),
                     dict(customer="east",router="b",hub=True,peak_mbps=0),
                     dict(customer="west",router="b",hub=False,peak_mbps=60000),
                     dict(customer="west",router="a",hub=True,peak_mbps=0)]
        result=_capacity(graph,attachments,.2)
        self.assertEqual(result['normal_load_mbps']['circuit/backbone/0'],60000)
        self.assertEqual(set(result['worst_load_mbps'].values()),{60000})
        self.assertIn('independent full-duplex',result['load_metric'])
        attachments[0]['peak_mbps']=80001
        with self.assertRaises(DesignError):_capacity(graph,attachments,.2)

    def test_example_has_actual_transport_customer_service_and_noc(self):
        plan=generate(example())
        self.assertEqual(validate(plan),[])
        self.assertEqual(canonical(generate(plan["recipe"])),canonical(plan))
        self.assertEqual(canonical(generate(plan["recipe"],previous=plan)),canonical(plan))
        kinds=Counter(o["kind"] for o in plan["objects"])
        self.assertEqual(kinds["site"],7)
        self.assertEqual(kinds["virtual_circuit"],1)
        self.assertEqual(kinds["virtual_circuit_termination"],3)
        self.assertEqual(kinds["circuit"],10)
        objects={o["key"]:o for o in plan["objects"]}
        for key in ("circuit/backbone/seed-01","circuit/backbone/seed-02","circuit/backbone/seed-03","circuit/noc/a","circuit/noc/b"):
            ends=[objects[key+'/'+side]['refs']['termination'] for side in ('A','Z')]
            self.assertNotEqual(*ends)
            self.assertTrue(all(objects[e]['kind']=='site' for e in ends))
        for side in ('a','b'):
            self.assertEqual(objects[f'circuit/transit/{side}/Z']['refs']['termination'],f'provider-network/transit/{side}')
        for banned in ('wireless_lan','tunnel','ike_policy','l2vpn'):
            self.assertEqual(kinds[banned],0)
        self.assertEqual(Counter(o['attrs']['name'] for o in plan['objects'] if o['kind']=='service'),
                         {'identity':2,'radius':2,'dns':2,'dns-udp':2,'monitoring':2,'provisioning':2})

    def test_seeded_physical_graph_survives_each_router_or_link_removal(self):
        for seed in (0,1,17,42,500):
            for count in (3,4,8):
                raw=example();raw['seed']=seed
                raw['pops'] += [dict(key=f'new-{i:02}',metro=('chicago','milwaukee')[i%2]) for i in range(count-3)]
                plan=generate(raw)
                nodes,edges=physical_router_graph(plan)
                self.assertEqual(len(nodes),2*count)
                self.assertEqual(len(edges),3*count-3)
                self.assertTrue(connected(nodes,edges))
                for node in nodes:self.assertTrue(connected(nodes-{node},edges),(seed,count,node))
                for edge in edges:self.assertTrue(connected(nodes,edges-{edge}),(seed,count,edge))
                degree=Counter(n for e in edges for n in e)
                self.assertLessEqual(max(degree.values()),3)
                self.assertEqual(sum(3-degree[n] for n in nodes),6)

    def test_growth_preserves_occupied_inventory_addresses_and_journals(self):
        for mode in ('direct','panels'):
            before=generate(example(mode));raw=deepcopy(before['recipe'])
            raw['pops'].insert(0,dict(key='aaa-new',metro='milwaukee'))
            raw['customers'][0]['lan_endpoints']=12
            raw['customers'][0]['sites'].append(dict(pop='aaa-new',count=2))
            raw['customers'].insert(0,dict(key='aaa-customer',hub_pop='chicago-west',sites=[dict(pop='chicago-west'),dict(pop='detroit-south')]))
            after=generate(raw,previous=before)
            self.assertEqual(validate(after),[])
            old,new=({o['key']:o for o in p['objects']} for p in (before,after))
            self.assertTrue(old.keys()<=new.keys())
            occupied={e for o in before['objects'] if o['kind']=='cable' for e in o['refs'].values()}
            _, old_optics = analyze_optics(before)
            _, new_optics = analyze_optics(after)
            for key,obj in old.items():
                owner = obj["refs"].get("device")
                delta = new_optics.get(owner, 0) - old_optics.get(owner, 0)
                if obj["kind"] == "power_port" and delta > 0:
                    actual, expected = deepcopy(new[key]), deepcopy(obj)
                    self.assertEqual(actual["attrs"]["maximum_draw"] - expected["attrs"]["maximum_draw"], delta)
                    for field in ("allocated_draw", "maximum_draw"):
                        self.assertGreaterEqual(actual["attrs"].pop(field), expected["attrs"].pop(field))
                    actual["attrs"].pop("description", None); expected["attrs"].pop("description", None)
                    self.assertEqual(actual, expected)
                elif obj['kind'] in {'cable','ip_address','virtual_machine','location','rack','journal_entry','contact','contact_assignment','circuit','circuit_termination','route_target','asn'} or key in occupied:
                    self.assertEqual(obj,new[key],key)
                if obj['kind']=='device':
                    for field in ('rack','location','primary_ip4'):self.assertEqual(obj['refs'].get(field),new[key]['refs'].get(field),key)
            for scope,items in before['reservations'].items():
                for key,slot in items.items():self.assertEqual(after['reservations'][scope][key],slot,(scope,key))
            self.assertEqual(after['reservations']['provider-pop-order']['aaa-new'],3)
            self.assertEqual(after['reservations']['provider-customers']['aaa-customer'],1)
            self.assertEqual(canonical(generate(after['recipe'],previous=after)),canonical(after))
            self.assertEqual(canonical(generate(after['recipe']|{'pops':list(reversed(after['recipe']['pops']))},previous=after)),canonical(after))

    def test_address_reservations_are_separated_and_external_owner_is_unknown(self):
        p=generate(example());objects={o['key']:o for o in p['objects']}
        prefix={o['key']:ipaddress.ip_network(o['attrs']['prefix']) for o in p['objects'] if o['kind']=='prefix'}
        dc=prefix['prefix/dc-01/management/reservation']
        sites=[net for key,net in prefix.items() if key.startswith('prefix/') and key.endswith('/reservation') and not key.startswith('prefix/dc-01/')]
        infrastructure=[net for key,net in prefix.items() if key.startswith(('prefix/link/','prefix/loopback/'))]
        self.assertEqual(dc.prefixlen,16)
        self.assertTrue(all(n.prefixlen==24 and not n.overlaps(dc) for n in sites))
        self.assertTrue(all(not a.overlaps(b) for i,a in enumerate(sites+infrastructure) for b in (sites+infrastructure)[i+1:]))
        assigned=[o for o in p['objects'] if o['kind']=='ip_address']
        for side in ('a','b'):
            net=prefix[f'prefix/link/circuit/transit/{side}']
            known=[o for o in assigned if ipaddress.ip_interface(o['attrs']['address']).ip in net]
            self.assertEqual(len(known),1)
            self.assertEqual(ipaddress.ip_interface(known[0]['attrs']['address']).ip,net[0])
        for o in p['objects']:
            if o['kind']=='device' and o['refs'].get('role')=='role/provider-edge':
                primary=objects[o['refs']['primary_ip4']]
                self.assertEqual(primary['refs']['assigned_object'],o['key']+'/if/lo0')
                self.assertEqual(ipaddress.ip_interface(primary['attrs']['address']).network.prefixlen,32)
                fxp=o['key']+'/if/fxp0'
                self.assertFalse(any(i['refs']['assigned_object']==fxp for i in assigned))
                self.assertFalse(any(fxp in c['refs'].values() for c in p['objects'] if c['kind']=='cable'))

    def test_catalog_mode_is_bounded_and_psu_names_keep_source_spaces(self):
        spec=hardware_catalog()['models']['provider-edge']
        self.assertEqual([p['name'] for p in spec['power_ports']],['PEM 0','PEM 1'])
        self.assertEqual([(m['bay'],m['position'],m['model']) for m in spec['configured_modules']],
                         [('Power Supply 0','PEM 0','JPSU-650W-AC-AO'),('Power Supply 1','PEM 1','JPSU-650W-AC-AO')])
        p=generate(example());obj={o['key']:o for o in p['objects']}
        _, optical_draw = analyze_optics(p)
        for d in p['objects']:
            if d['kind']!='device' or d['refs'].get('device_type')!='hardware/provider-edge':continue
            ports=[o for o in p['objects'] if o['kind']=='interface' and o['refs'].get('device')==d['key']]
            self.assertEqual(sum(o['attrs']['enabled'] for o in ports if o['attrs']['type']=='100gbase-x-qsfp28'),3)
            for n in (0,1):
                port=obj[f"{d['key']}/power/PEM {n}"]
                self.assertEqual(port['attrs']['allocated_draw'], (320 + optical_draw[d['key']]) // 2 + (n < (320 + optical_draw[d['key']]) % 2))
                self.assertEqual(port['attrs']['maximum_draw'],320 + optical_draw[d['key']])
                self.assertIn('module',port['refs'])

    def test_public_boundary_rejects_malformed_and_combined_infeasible_demand(self):
        cases=[]
        for name,value in [('pops',None),('pops',[]),('customers',None),('asn_base',True),('asn_base',4200000001),
                           ('address_pool','10.0.0.0/16'),('noc_peak_mbps',1001),('topology','magic'),('noc_pop_a','unknown')]:
            cases.append(example()|{name:value})
        for field,value in [('lan_endpoints',13),('lan_endpoints',True),('site_peak_mbps',10**1000),('service','internet'),('hub_pop','missing')]:
            raw=example();raw['customers'][0][field]=value;cases.append(raw)
        raw=example();raw['customers'][0]['site_peak_mbps']=500;cases.append(raw)
        raw=example();raw['reserve_fraction']=.4;raw['noc_peak_mbps']=800;cases.append(raw)
        raw=example();raw['customers'][0]['sites'][0]['count']=12;cases.append(raw)
        raw=example()
        for pop in raw['pops']:pop['metro']='chicago'
        cases.append(raw)
        for i,raw in enumerate(cases):
            with self.subTest(case=i),self.assertRaises(DesignError):resolve(raw)

    def test_customer_noc_key_has_separate_native_account_identity(self):
        raw=example();raw['customers'][0]['key']='noc'
        plan=generate(raw);objects={o['key']:o for o in plan['objects']}
        customer=objects['provider-account/customer/noc'];operator=objects['provider-account/operator/noc']
        self.assertNotEqual(customer['attrs']['account'],operator['attrs']['account'])
        self.assertEqual(objects['virtual-circuit/customer/noc']['refs']['provider_account'],customer['key'])
        self.assertNotIn('tenant',customer['refs'])

    def test_growth_rejects_removed_demand_and_altered_frozen_evidence(self):
        p=generate(example())
        for change in ('rate','hub','metro','endpoints','premises','noc'):
            raw=deepcopy(p['recipe'])
            if change=='rate':raw['customers'][0]['site_peak_mbps']=25
            elif change=='hub':raw['customers'][0]['hub_pop']='detroit-south'
            elif change=='metro':raw['pops'][0]['metro']='milwaukee'
            elif change=='endpoints':raw['customers'][0]['lan_endpoints']=3
            elif change=='premises':raw['customers'][0]['sites']=raw['customers'][0]['sites'][:2]
            elif change=='noc':raw['noc_peak_mbps']=50
            with self.subTest(change=change),self.assertRaises(DesignError):generate(raw,previous=p)
        for mutation in ('cable','ledger','catalog'):
            changed=deepcopy(p)
            if mutation=='cable':next(o for o in changed['objects'] if o['kind']=='cable')['attrs']['status']='planned'
            elif mutation=='ledger':changed['reservations']['provider-service-ports/chicago-west']['noc/a']=11
            else:changed['hardware_digest']='wrong'
            with self.subTest(mutation=mutation),self.assertRaises(DesignError):generate(p['recipe'],previous=changed)

    def test_maximum_identity_lengths_fit_device_and_dns_labels(self):
        raw=example();raw['namespace']='n'*20;raw['name']='N'*80
        mapping={p['key']:chr(ord('a')+i)*20 for i,p in enumerate(raw['pops'])}
        for p in raw['pops']:p['key']=mapping[p['key']]
        for field in ('noc_pop_a','noc_pop_b'):raw[field]=mapping[raw[field]]
        c=raw['customers'][0];c['key']='z'*20;c['hub_pop']=mapping[c['hub_pop']]
        for entry in c['sites']:entry['pop']=mapping[entry['pop']]
        plan=generate(raw)
        self.assertEqual(validate(plan),[])
        for obj in plan['objects']:
            if obj['kind']=='device':self.assertLessEqual(len(obj['attrs']['name']),64,obj['key'])
            if obj['kind']=='ip_address':self.assertTrue(all(len(label)<=63 for label in obj['attrs']['dns_name'].split('.')),obj['key'])
            if obj['kind']=='rack':self.assertLessEqual(len(obj['attrs']['asset_tag']),50,obj['key'])
            if obj['kind']=='vlan':self.assertLessEqual(len(obj['attrs']['name']),64,obj['key'])
            if obj['kind'] in {'provider','contact'}:
                self.assertLessEqual(len(obj['attrs']['name']),100,obj['key'])
                self.assertLessEqual(len(obj['attrs'].get('description','')),200,obj['key'])


if __name__=='__main__':unittest.main()
