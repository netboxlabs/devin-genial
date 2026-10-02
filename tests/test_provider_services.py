"""Provider services and premises (v0.17 WP-C): the builder's own output.

Each structural check is a small predicate over a plan, proven against the
showcase build and then against a mutated copy that must make it fail.
Validator coverage of the same rules lives in tests/test_provider*.py.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import ipaddress
import math
from pathlib import Path
import tomllib
import unittest

from estates import provider
from estates.generate import generate
from estates.model import DesignError


ROOT = Path(__file__).parents[1]
SHOWCASE = tomllib.loads((ROOT / "profiles/showcase-provider.toml").read_text())
SMALL = dict(profile="provider-backbone", namespace="svc-test", name="Lakeside Carrier", seed=7, as_of="2026-09-01",
             pops=[dict(key="chicago-loop", metro="chicago"), dict(key="detroit-corktown", metro="detroit"),
                   dict(key="cleveland-flats", metro="cleveland")],
             customers=[dict(key="anchor-bank", name="Anchor Bank", hub_pop="chicago-loop", lan_endpoints=0,
                             sites=[dict(pop="chicago-loop", count=2), dict(pop="detroit-corktown")]),
                        dict(key="brightpath", name="Brightpath Media", service="dia", commit_mbps=200,
                             sites=[dict(pop="detroit-corktown")]),
                        dict(key="colo-large", name="Colo Large", service="dia", commit_mbps=5000,
                             sites=[dict(pop="cleveland-flats")]),
                        dict(key="alder-dental", name="Alder Dental", service="dia", managed=True, commit_mbps=50,
                             sites=[dict(pop="chicago-loop")]),
                        dict(key="calumet-steel", name="Calumet Steel", service="epl", rate_mbps=200,
                             sites=[dict(pop="chicago-loop"), dict(pop="cleveland-flats")])])


def index(plan):
    return {o["key"]: o for o in plan["objects"]}


def of(o, kind):
    return [x for x in o.values() if x["kind"] == kind]


def peers(o):
    result = {}
    for cable in of(o, "cable"):
        result[cable["refs"]["a"]], result[cable["refs"]["b"]] = cable["refs"]["b"], cable["refs"]["a"]
    return result


def device_role(o, port):
    return o.get(o[port]["refs"].get("device"), {}).get("refs", {}).get("role")


# --- Predicates: each returns the offending keys (empty when the rule holds) --

def home_side_violations(o):
    """Every service VLAN is tagged only on its home side's LAGs and nowhere else."""
    bad = []
    lags = {k: x for k, x in o.items() if x["kind"] == "interface" and x["attrs"].get("type") == "lag"}
    for sub in (x for x in of(o, "interface") if o.get(x["refs"].get("parent"), {}).get("attrs", {}).get("type") == "lag"):
        vlan = sub["refs"].get("untagged_vlan")
        if not str(vlan).endswith("/customer"):
            continue
        side = sub["refs"]["device"].rsplit("-", 1)[-1]
        carrying = {k.split("/if/")[0].rsplit("/", 1)[-1] for k, lag in lags.items() if vlan in lag["refs"].get("tagged_vlans", [])}
        if carrying != {f"pe-{side}", f"agg-{side}"}:
            bad.append(sub["key"])
    return bad


def handoff_violations(o):
    """Access circuit A -> NID network port; NID UNI -> CE WAN or a labelled demarcation."""
    p, bad = peers(o), []
    for circuit in (c for c in of(o, "circuit") if c["key"].startswith("circuit/customer/ce-")):
        term = f"{circuit['key']}/A"
        port = p.get(term)
        if (o[term]["refs"]["termination"] != o[o[port]["refs"]["device"]]["refs"]["site"] if port else True) or \
                device_role(o, port) != "role/nid":
            bad.append(circuit["key"])
    for nid in (d for d in of(o, "device") if d["refs"].get("role") == "role/nid"):
        uni = [x for x in of(o, "interface") if x["refs"]["device"] == nid["key"] and
               (x["attrs"].get("label") or (p.get(x["key"]) and device_role(o, p[x["key"]]) == "role/customer-edge"))]
        if len(uni) != 1 or (uni[0]["attrs"].get("mark_connected") and uni[0]["key"] in p):
            bad.append(nid["key"])
    return bad


def cabinet_violations(o):
    """Multi-device premises rack everything in one MPOE cabinet; single-NID premises have no rack."""
    bad = []
    devices = defaultdict(list)
    for d in of(o, "device"):
        if d["refs"]["site"].startswith("site/ce-"):
            devices[d["refs"]["site"]].append(d)
    for site, members in devices.items():
        racks = {d["refs"].get("rack") for d in members}
        if len(members) > 1:
            if len(racks) != 1 or None in racks or o[racks.pop()]["meta"].get("rack_type") != provider.MPOE_RACK_TYPE:
                bad.append(site)
        elif racks != {None}:
            bad.append(site)
    return bad


def spacing_violations(o, metres=300):
    points = sorted((x["attrs"]["latitude"], x["attrs"]["longitude"], x["key"]) for x in of(o, "site")
                    if x["key"].startswith("site/ce-") and "latitude" in x["attrs"])
    bad = []
    for i, (la, lo, key) in enumerate(points):
        for lb, lob, other in points[i + 1:]:
            if lb - la > 0.01:
                break
            if math.hypot((la - lb) * 111.0, (lo - lob) * 111.0 * math.cos(math.radians(la))) * 1000 < metres - 1:
                bad.append(key)
    return bad


def ibgp_mirror_violations(o):
    pairs = {(x["refs"]["device"], o[o[x["refs"]["remote_address"]]["refs"]["assigned_object"]]["refs"]["device"],
              x["key"].endswith("/ipv6"))
             for x in of(o, "bgp_session") if x["refs"].get("peer_group") == "bgp-peer-group/ibgp-core"}
    return sorted(f"{a}->{b}" for a, b, v6 in pairs if (b, a, v6) not in pairs)


class ShowcaseServices(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate(SHOWCASE)
        cls.o = index(cls.plan)

    def mutated(self):
        return index(deepcopy(self.plan))

    def test_scale_and_service_mix(self):
        customers = Counter(c["service"] for c in self.plan["recipe"]["customers"])
        self.assertEqual(customers, {"private-l3": 36, "dia": 60, "epl": 12})
        self.assertEqual(sum(c["service"] == "dia" and c["managed"] for c in self.plan["recipe"]["customers"]), 20)
        # v0.17 footprint plus v0.18's thirty branches at the founding PoPs.
        self.assertEqual(len(provider.premises(self.plan["recipe"])), 305)
        access = [c for c in of(self.o, "circuit") if c["key"].startswith("circuit/customer/ce-")]
        # One attachment per premises, plus the second of each dual-homed VPN hub.
        self.assertEqual(len(access), 305 + 36)
        self.assertEqual(Counter(c["refs"]["type"] for c in access),
                         {"circuit-type/private-l3-access": 221 + 36, "circuit-type/dia-access": 60, "circuit-type/epl-access": 24})
        self.assertLessEqual(len(self.plan["objects"]), 40000)
        large = [d for d in of(self.o, "device") if d["refs"]["device_type"] == "hardware/nid-10g"]
        self.assertEqual(len(large), 4)

    def test_service_vlans_ride_only_their_home_side(self):
        self.assertEqual(home_side_violations(self.o), [])
        o = self.mutated()
        lag = next(x for x in of(o, "interface") if x["key"].endswith("pe-b/if/ae1"))
        lag["refs"]["tagged_vlans"].append(next(v["key"] for v in of(o, "vlan") if v["key"].endswith("/customer")))
        self.assertTrue(home_side_violations(o))

    def test_home_side_ledger_alternates_and_hubs_split_sides(self):
        for scope, items in self.plan["reservations"].items():
            if scope.startswith("provider-agg-home/"):
                self.assertEqual(sorted(items.values()), list(range(len(items))))
        for hub in (k for k in self.plan["reservations"]["provider-agg-home/chicago-cermak"] if k.endswith("/b")):
            sides = {self.o[f"circuit/customer/{t}/Z"]["attrs"]["description"].split("AGG-")[1][0] for t in (hub[:-2], hub)}
            self.assertEqual(sides, {"A", "B"})

    def test_handoffs_go_through_the_nid(self):
        self.assertEqual(handoff_violations(self.o), [])
        o = self.mutated()
        uni = next(end for c in of(o, "cable") for end in (c["refs"]["a"], c["refs"]["b"])
                   if end.endswith("nid-01/if/3") and "edge-01" in c["refs"]["a"] + c["refs"]["b"])
        o[uni]["attrs"].update(mark_connected=True, label="Customer-owned firewall")
        self.assertTrue(handoff_violations(o))

    def test_mpoe_cabinets_only_where_there_are_two_devices(self):
        self.assertEqual(cabinet_violations(self.o), [])
        racks = [r for r in of(self.o, "rack") if r["meta"].get("rack_type") == provider.MPOE_RACK_TYPE]
        self.assertTrue(racks and all(r["attrs"]["u_height"] == 9 and r["refs"]["tenant"] == "tenant" for r in racks))
        o = self.mutated()
        nid = next(d for d in of(o, "device") if d["refs"]["site"].startswith("site/ce-brightpath") and d["refs"].get("role") == "role/nid")
        nid["refs"]["rack"] = racks[0]["key"]
        self.assertTrue(cabinet_violations(o))

    def test_dia_addressing_fits_the_documentation_repack(self):
        pools = [ipaddress.ip_network(p) for p in provider.DIA_POOLS]
        link = ipaddress.ip_network(provider.DIA_LINK_POOL)
        blocks = [ipaddress.ip_network(x["attrs"]["prefix"]) for k, x in self.o.items()
                  if k.startswith("prefix/dia/ce-")]
        self.assertEqual(len(blocks), 60)
        self.assertEqual(len(set(blocks)), 60)
        self.assertTrue(all(b.prefixlen == 29 and any(b.subnet_of(p) for p in pools) and not b.overlaps(link) for b in blocks))
        links = [ipaddress.ip_network(x["attrs"]["prefix"]) for k, x in self.o.items() if k.startswith("prefix/dia/link/")]
        self.assertEqual(len(links), 20)
        self.assertTrue(all(n.prefixlen == 31 and n.subnet_of(link) for n in links))
        self.assertEqual(len(provider.dia_networks()), 64)
        roles = {self.o[k]["refs"].get("role") for k in self.o if k.startswith("prefix/dia/")}
        self.assertEqual(roles, {"ip-role/customer-dia"} | ({"ip-role/transit"} if links else set()))
        aggregates = {x["attrs"]["prefix"] for x in of(self.o, "aggregate") if x["key"].startswith("aggregate/public/")}
        self.assertEqual(aggregates, {"192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24"})

    def test_epl_is_native_qinq_with_two_terminations_and_unique_vcids(self):
        l2vpns = [x for x in of(self.o, "l2vpn") if x["key"].startswith("l2vpn/epl/")]
        self.assertEqual(len(l2vpns), 12)
        self.assertEqual(len({x["attrs"]["identifier"] for x in l2vpns}), 12)
        self.assertTrue(all(x["attrs"]["type"] == "epl" and x["attrs"]["identifier"] >= provider.EPL_VCID_BASE
                            and x["refs"]["tenant"].startswith("tenant/cust-") for x in l2vpns))
        terms = defaultdict(list)
        for t in of(self.o, "l2vpn_termination"):
            terms[t["refs"]["l2vpn"]].append(self.o[t["refs"]["assigned_object"]])
        for l2vpn in l2vpns:
            subs = terms[l2vpn["key"]]
            self.assertEqual(len(subs), 2)
            self.assertEqual(len({s["refs"]["device"].split("/")[1] for s in subs}), 2)
            for sub in subs:
                vlan = self.o[sub["refs"]["untagged_vlan"]]
                self.assertEqual(vlan["attrs"].get("qinq_role"), "svlan")
                self.assertTrue(any(x["attrs"].get("mode") == "q-in-q" and x["refs"].get("qinq_svlan") == vlan["key"]
                                    for x in of(self.o, "interface")))

    def test_bgp_customer_sessions_and_mirrored_ibgp(self):
        sessions = [x for x in of(self.o, "bgp_session") if x["refs"].get("peer_group") == "bgp-peer-group/customer"]
        # In-service and pending attachments; a former customer's decommissioned circuit peers with nothing.
        vpn = [c for c in of(self.o, "circuit") if c["refs"]["type"] == "circuit-type/private-l3-access"
               and c["key"].startswith("circuit/customer/")]
        self.assertEqual(len(sessions), 2 * len(vpn))
        self.assertTrue(all(self.o[x["refs"]["device"]]["refs"]["role"] == "role/provider-edge" and
                            self.o[self.o[x["refs"]["local_address"]]["refs"]["assigned_object"]]["attrs"]["name"].startswith("ae1.")
                            for x in sessions))
        self.assertEqual(ibgp_mirror_violations(self.o), [])
        o = self.mutated()
        del o[next(x["key"] for x in of(o, "bgp_session") if x["meta"].get("mirror"))]
        self.assertTrue(ibgp_mirror_violations(o))
        reflectors = [x["key"] for x in of(self.o, "device") if "tag/route-reflector" in x["refs"].get("tags", [])]
        self.assertEqual(len(reflectors), 2)

    def test_asn_descriptions_name_the_holder_within_thirty_characters(self):
        asns = of(self.o, "asn")
        self.assertTrue(asns and all(len(x["attrs"]["description"]) <= 30 for x in asns))
        self.assertEqual(provider.holder_label("Northshore Federal Credit Union"), "Northshore Federal Credit")
        self.assertEqual(provider.holder_label("Cedar Regional Bank"), "Cedar Regional Bank")

    def test_premises_placement_keeps_300_m_spacing(self):
        self.assertEqual(spacing_violations(self.o), [])
        o = self.mutated()
        sites = [x for x in of(o, "site") if x["key"].startswith("site/ce-")]
        sites[1]["attrs"].update(latitude=sites[0]["attrs"]["latitude"] + 0.001, longitude=sites[0]["attrs"]["longitude"])
        self.assertTrue(spacing_violations(o))

    def test_sites_page_one_and_descriptions(self):
        sites = sorted((x["attrs"]["name"], x["key"]) for x in of(self.o, "site"))
        nids = Counter(d["refs"]["site"] for d in of(self.o, "device"))
        self.assertTrue(all(nids[key] >= 2 or not key.startswith("site/ce-") for _, key in sites[:10]))
        descriptions = defaultdict(set)
        for x in of(self.o, "site"):
            if x["key"].startswith("site/ce-"):
                descriptions[x["refs"]["tenant"]].add(x["attrs"]["description"])
        self.assertGreaterEqual(len(set().union(*descriptions.values())), 4)
        counts = Counter(x["refs"]["tenant"] for x in of(self.o, "site") if x["key"].startswith("site/ce-"))
        self.assertTrue(all(len(descriptions[t]) >= 2 for t, n in counts.items() if n > 1))

    def test_planned_premises_shut_their_pop_side_service(self):
        subs = [x for x in of(self.o, "interface") if x["key"].startswith("device/pop-") and ".10" in x["attrs"]["name"]
                and x["refs"].get("untagged_vlan", "").endswith("/customer")]
        planned = [s for s in subs if self.o[s["refs"]["untagged_vlan"]]["attrs"]["status"] == "reserved"]
        self.assertTrue(planned)
        self.assertTrue(all(s["attrs"]["enabled"] is False for s in planned))


class ServiceGrammar(unittest.TestCase):
    def test_growth_appends_without_moving_existing_premises(self):
        before = generate(SMALL)
        grown = deepcopy(SMALL)
        grown["customers"][0]["sites"][1]["count"] = 2
        grown["customers"] += [dict(key="beacon-law", service="dia", commit_mbps=100, sites=[dict(pop="chicago-loop")]),
                               dict(key="bluebird", service="dia", managed=True, commit_mbps=50, sites=[dict(pop="detroit-corktown")]),
                               dict(key="dresden", service="epl", rate_mbps=100,
                                    sites=[dict(pop="detroit-corktown"), dict(pop="cleveland-flats")]),
                               dict(key="westbrook", hub_pop="cleveland-flats", lan_endpoints=0,
                                    sites=[dict(pop="cleveland-flats"), dict(pop="chicago-loop")])]
        after = generate(grown, previous=before)
        old, new = index(before), index(after)
        for scope, items in before["reservations"].items():
            self.assertEqual({k: after["reservations"][scope][k] for k in items}, items, scope)
        for key, obj in old.items():
            if obj["kind"] in ("site", "device", "circuit", "vlan", "l2vpn", "prefix") and key.startswith(
                    ("site/ce-", "device/ce-", "circuit/customer/", "l2vpn/", "prefix/dia/")):
                for field in ("latitude", "longitude", "name", "cid", "vid", "identifier", "prefix"):
                    self.assertEqual(obj["attrs"].get(field), new[key]["attrs"].get(field), (key, field))
        self.assertIn("l2vpn/epl/dresden", new)

    def test_ten_gig_tier_and_managed_cap(self):
        o = index(generate(SMALL))
        self.assertEqual(o["device/ce-colo-large-cleveland-flats-001/nid-01"]["refs"]["device_type"], "hardware/nid-10g")
        self.assertEqual(o["device/ce-brightpath-detroit-corktown-001/nid-01"]["refs"]["device_type"], "hardware/nid")
        bad = deepcopy(SMALL)
        bad["customers"][3]["commit_mbps"] = 1000
        with self.assertRaisesRegex(DesignError, "managed DIA plus reserve exceeds"):
            generate(bad)

    def test_grammar_refusals(self):
        cases = [
            (lambda r: r["customers"][1]["sites"].append(dict(pop="chicago-loop")), "exactly one sites entry"),
            (lambda r: r["customers"][4]["sites"].pop(), "exactly two sites entries"),
            (lambda r: r["customers"][1].update(commit_mbps=300), "commit_mbps must be one of"),
            (lambda r: r["customers"][1].update(hub_pop="chicago-loop"), "only these optional fields"),
            (lambda r: r["customers"][2].update(name="Anchor Bank"), "already used by another customer"),
            (lambda r: r["customers"].extend(dict(key=f"dia-{n}", service="dia", sites=[dict(pop="chicago-loop")])
                                             for n in range(80)), "aggregation attachments"),
        ]
        for mutate, message in cases:
            recipe = deepcopy(SMALL)
            mutate(recipe)
            with self.subTest(message=message), self.assertRaisesRegex(DesignError, message):
                generate(recipe)

    def test_dia_ceiling_is_sixty_four(self):
        recipe = deepcopy(SMALL)
        pops = ["chicago-loop", "detroit-corktown", "cleveland-flats"]
        recipe["customers"] += [dict(key=f"dia-{n}", service="dia", sites=[dict(pop=pops[n % 3])]) for n in range(62)]
        with self.assertRaisesRegex(DesignError, "documentation /29s"):
            provider.resolve(recipe)


if __name__ == "__main__":
    unittest.main()
