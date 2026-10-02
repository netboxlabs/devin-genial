"""The v0.18 lived-in carrier services and records (WP-C), builder side.

DESIGN.md §2, §4.2 and §6 (build/lived-in-design): the IX as a provider with
its one AS and an IPv6-only peering LAN, route-server sessions, the Milwaukee
relocation echo, the staged TMS and its controller pair, the M300, former
customers, the second NID generation, display renames, journal shape and the
carrier paperwork. Each check is a small function over a plan, paired with a
mutation it must catch. The independent validators are WP-D's.
"""

from collections import Counter
from copy import deepcopy
from datetime import date
from ipaddress import ip_interface, ip_network
from pathlib import Path
import re
import tomllib
import unittest

from estates import provider, timeline
from estates.generate import generate
from estates.model import DesignError, resolve_recipe
from estates.operations_context import NOTICE_JOURNALING, NOTICE_TYPES


SHOWCASE = Path(__file__).resolve().parent.parent / "profiles" / "showcase-provider.toml"
TITLE = re.compile(r"\*\*([^*\n]+)\*\* · (\d{4}-\d{2}-\d{2})\n\n\S")


def showcase():
    return tomllib.loads(SHOWCASE.read_text())


def index(plan):
    return {o["key"]: o for o in plan["objects"]}


def of_kind(objects, kind):
    return [o for o in objects.values() if o["kind"] == kind]


def cables_at(objects, end):
    return [o for o in of_kind(objects, "cable") if end in (o["refs"]["a"], o["refs"]["b"])]


# -- checks: each returns a list of problems ----------------------------------

def check_ix(objects, recipe):
    problems = []
    ports = [o for o in of_kind(objects, "circuit") if o["refs"].get("type") == "circuit-type/ix-port"]
    active = [o for o in ports if o["attrs"]["status"] == "active"]
    if sorted(o["meta"]["ix"] for o in active) != sorted({p["metro"] for p in recipe["pops"]}):
        problems.append("one active IX port per metro")
    for circuit in ports:
        provider_obj = objects[circuit["refs"]["provider"]]
        asns = provider_obj["refs"].get("asns", [])
        if len(asns) != 1 or not provider.IX_ASNS[0] <= objects[asns[0]]["attrs"]["asn"] <= provider.IX_ASNS[1]:
            problems.append(f"{circuit['key']}: IX provider needs one 32-bit documentation AS")
        if provider.IX_IPV4_NOTE not in circuit["attrs"].get("comments", ""):
            problems.append(f"{circuit['key']}: IPv4 declaration missing on the circuit")
        lan = objects[circuit["key"] + "/Z"]["refs"]["termination"]
        if provider.IX_IPV4_NOTE not in objects[lan]["attrs"]["description"]:
            problems.append(f"{lan}: IPv4 declaration missing on the peering LAN")
        port = f"device/{objects[circuit['key'] + '/A']['refs']['termination'].removeprefix('site/')}/pe-a/if/{provider.IX_PORT}"
        if not cables_at(objects, port):
            problems.append(f"{circuit['key']}: IX port not cabled from PE-A {provider.IX_PORT}")
        if not objects[circuit["key"] + "/A"]["attrs"].get("xconnect_id"):
            problems.append(f"{circuit['key']}: IX port without a cross-connect")
    # No IX tenant: the exchange's records name no tenant at all.
    for key, obj in objects.items():
        if (key.startswith("ix/") and (key.endswith("/lan") or "/rs-" in key)) and obj["refs"].get("tenant"):
            problems.append(f"{key}: exchange-held record carries a tenant")
        if obj["kind"] == "tenant" and any(name[0] in obj["attrs"]["name"] for name in provider.IXES.values()):
            problems.append(f"{key}: an IX became a tenant")
    return problems


def check_ix_sessions(objects, recipe):
    problems = []
    active = [o for o in of_kind(objects, "circuit") if o["meta"].get("ix") and o["attrs"]["status"] == "active"]
    sessions = [o for o in of_kind(objects, "bgp_session") if o["refs"].get("peer_group") == "bgp-peer-group/ix-route-servers"]
    expected = 2 * len(active) if "ipv6_pool" in recipe else 0
    if len(sessions) != expected:
        problems.append(f"{len(sessions)} route-server sessions, expected {expected}")
    for session in sessions:
        metro = session["key"].split("/")[2]
        if session["refs"]["remote_as"] != f"asn/ix/{metro}":
            problems.append(f"{session['key']}: route server does not share the exchange's AS")
        if ip_interface(objects[session["refs"]["remote_address"]]["attrs"]["address"]).version != 6:
            problems.append(f"{session['key']}: route-server peering is not on the IPv6-only LAN")
    if "ipv6_pool" not in recipe and any(provider.IX_NO_POOL_NOTE not in o["attrs"].get("comments", "") for o in active):
        problems.append("without ipv6_pool the circuit must record why sessions are omitted")
    return problems


def check_relocation(objects):
    problems = []
    former = [o for o in of_kind(objects, "circuit") if o["key"].endswith("/former") and o["meta"].get("ix")]
    for circuit in former:
        site = objects[circuit["key"] + "/A"]["refs"]["termination"].removeprefix("site/")
        port = f"device/{site}/pe-a/if/{provider.IX_PORT}"
        if circuit["attrs"]["status"] != "deprovisioning" or not circuit["attrs"].get("termination_date"):
            problems.append(f"{circuit['key']}: relocated port must be deprovisioning with a termination date")
        if objects[port]["attrs"].get("enabled") is not False:
            problems.append(f"{port}: former IX port must be shut")
        if any(c["attrs"].get("status") != "decommissioning" for c in cables_at(objects, port) + cables_at(objects, circuit["key"] + "/A")):
            problems.append(f"{circuit['key']}: relocated port cables must be decommissioning")
    return problems


def check_services(objects, tl):
    problems = []
    for pop in tl.metro:
        tms, ntp = f"device/pop-{pop}/ddos-01", f"device/pop-{pop}/ntp-01"
        if (tms in objects) != bool(tl.ddos[pop]):
            problems.append(f"{pop}: TMS iff the timeline racks one")
        if (ntp in objects) != bool(tl.timing[pop]):
            problems.append(f"{pop}: M300 iff a NOC handoff")
        if tms in objects:
            device = objects[tms]
            if device["attrs"]["status"] != "staged" or "well above current DIA commit" not in device["attrs"]["description"]:
                problems.append(f"{tms}: staged, with the declared oversize")
            ends = {c["refs"]["b" if c["refs"]["a"].startswith(tms) else "a"] for c in of_kind(objects, "cable")
                    if tms + "/if/" in c["refs"]["a"] + c["refs"]["b"] and c["attrs"].get("type") == "aoc"}
            if ends != {f"device/pop-{pop}/pe-{s}2/if/et-0/0/1" for s in "ab"}:
                problems.append(f"{tms}: offramp must reach both MX304 successors' tms_port")
            if any(c["attrs"].get("status") != "planned" for c in of_kind(objects, "cable")
                   if tms + "/" in c["refs"]["a"] + " " + c["refs"]["b"]):
                problems.append(f"{tms}: a staged appliance's cables are planned")
        if ntp in objects:
            lan1 = f"{ntp}/if/lan1"
            peers = [c["refs"]["b" if c["refs"]["a"] == lan1 else "a"] for c in cables_at(objects, lan1)]
            if len(peers) != 1 or not peers[0].startswith(f"device/pop-{pop}/mgmt-01/"):
                problems.append(f"{ntp}: lan1 must be cabled to the PoP management switch")
            if objects[ntp]["refs"].get("primary_ip4") != f"ip/{lan1}":
                problems.append(f"{ntp}: primary address on lan1")
    controllers = [o for o in of_kind(objects, "virtual_machine") if o["key"].startswith(f"vm/dc-01/{provider.CONTROLLER}/")]
    if len(controllers) != (2 if any(tl.ddos.values()) else 0):
        problems.append("controller VM pair iff any TMS")
    if any(not o["attrs"]["description"].startswith(provider.CONTROLLER_DESCRIPTION) for o in controllers):
        problems.append("controller VMs carry the labelled-fiction description")
    return problems


def check_former(objects, recipe):
    problems = []
    for f in recipe["former_customers"]:
        tenant, circuit = f"tenant/cust-{f['key']}", objects.get(f"circuit/former/{f['key']}")
        if not objects[tenant]["attrs"]["description"].startswith("Former "):
            problems.append(f"{tenant}: description says Former")
        if circuit is None or circuit["attrs"]["status"] != "decommissioned" or not (
                circuit["attrs"].get("install_date") and circuit["attrs"].get("termination_date")):
            problems.append(f"{f['key']}: one decommissioned circuit with both dates")
        if any(o["kind"] == "circuit_termination" and o["refs"]["circuit"] == f"circuit/former/{f['key']}" for o in objects.values()):
            problems.append(f"{f['key']}: a former circuit has no terminations")
        if any(o["kind"] in ("device", "site") and o["refs"].get("tenant") == tenant for o in objects.values()):
            problems.append(f"{f['key']}: a former customer keeps no device or site")
        note = objects.get(f"journal/circuit/former/{f['key']}/service-ceased")
        if not note or "NID not recovered" not in note["attrs"]["comments"]:
            problems.append(f"{f['key']}: NID-not-recovered journal")
    return problems


def check_first_impression(objects, recipe):
    problems = []
    name, ns = recipe["name"], recipe["namespace"]
    expected = {f"site-group/{ns}/pop": f"Carrier PoPs — {name}", f"site-group/{ns}/dc": f"Carrier NOC — {name}",
                f"site-group/{ns}/customer": "Customer premises", "tenant-group/operator": "Carrier",
                "tenant-group/colocation": "Colocation providers", "tenant-group/customers": f"Customers of {name}"}
    for key, label in expected.items():
        if objects[key]["attrs"]["name"] != label:
            problems.append(f"{key}: {objects[key]['attrs']['name']!r} != {label!r}")
    for tenant in of_kind(objects, "tenant"):
        if tenant["key"].startswith("tenant/cust-") and not tenant["attrs"]["description"].endswith(f"customer of {name}"):
            problems.append(f"{tenant['key']}: description must end 'customer of {name}'")
    return problems


def check_journals(objects):
    problems = []
    for note in of_kind(objects, "journal_entry"):
        match = TITLE.match(note["attrs"]["comments"])
        created = note["attrs"]["created"]
        if not match or created[:10] != match[2]:
            problems.append(f"{note['key']}: bold title line with the event date")
            continue
        hour = int(created[11:13])
        night = "/maintenance-" in note["key"]
        if not (4 <= hour < 9 if night else 14 <= hour < 22):
            problems.append(f"{note['key']}: time of day outside its band")
        if match[1] == "Site access":
            problems.append(f"{note['key']}: site access is site comments, not a journal")
        if night and match[2] < NOTICE_JOURNALING.isoformat():
            problems.append(f"{note['key']}: maintenance notice before notice journaling began")
    return problems


def check_paperwork(objects, recipe):
    problems = []
    as_of = date.fromisoformat(recipe["as_of"])
    for note in of_kind(objects, "journal_entry"):
        target = objects[note["refs"]["assigned_object"]]
        if "/maintenance-" in note["key"]:
            if target["refs"].get("type") not in NOTICE_TYPES or target["refs"].get("provider") == "provider/operator":
                problems.append(f"{note['key']}: notices only on leased third-party circuits")
        if "/cir-upgrade-" in note["key"]:
            installed = date.fromisoformat(target["attrs"]["install_date"])
            if (as_of - installed).days < 365 * 5:
                problems.append(f"{note['key']}: CIR upgrade on a circuit in service under five years")
            rates = re.search(r"from (.+?) to (.+?) under", note["attrs"]["comments"])
            if not rates:
                problems.append(f"{note['key']}: names both rates")
    for circuit in of_kind(objects, "circuit"):
        steps = sorted(k for k in objects if k.startswith(f"journal/{circuit['key']}/cir-upgrade-"))
        if steps:
            last = re.search(r" to (.+?) under", objects[steps[-1]]["attrs"]["comments"])[1]
            if last != provider.bandwidth(circuit["attrs"]["commit_rate"] // 1000):
                problems.append(f"{circuit['key']}: the last upgrade reaches the current committed rate")
    for term in of_kind(objects, "circuit_termination"):
        if term["attrs"].get("xconnect_id") and "install_date" in objects[term["refs"]["circuit"]]["attrs"]:
            note = objects.get(f"journal/{term['key']}/cross-connect-order")
            if not note or "LOA-" not in note["attrs"]["comments"] or "MMR-only jumper" not in note["attrs"]["comments"]:
                problems.append(f"{term['key']}: cross-connect order with its LOA")
    return problems


def check_legacy_nids(objects, recipe):
    problems = []
    customers = {c["key"]: c for c in recipe["customers"]}
    for device in of_kind(objects, "device"):
        site = objects[device["refs"]["site"]]
        if not site["key"].startswith("site/ce-") or device["refs"]["role"] != "role/nid":
            continue
        c = customers[next(k for k in customers if site["key"].startswith(f"site/ce-{k}-"))]
        early = site["meta"].get("in_service", "9999") < provider.LEGACY_NID_BEFORE.isoformat()
        single = c["service"] == "epl" or c["service"] == "dia" and not c["managed"]
        one_gig = device["refs"]["device_type"] != f"hardware/{provider.NID_10G_ALIAS}"
        legacy = device["refs"]["device_type"] == f"hardware/{provider.LEGACY_NID_ALIAS}"
        if legacy != (early and single and one_gig) and not (device["refs"]["device_type"] == f"hardware/{provider.NID_10G_ALIAS}"):
            problems.append(f"{device['key']}: MetroNID only and always at pre-2016 single-NID 1G premises")
    return problems


class LivedInServicesTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.raw = showcase()
        cls.recipe = resolve_recipe(cls.raw)
        cls.plan = generate(cls.recipe)
        cls.objects = index(cls.plan)
        cls.resolved = cls.plan["recipe"]

        # Re-derive the timeline from the plan's own frozen ledgers.
        from estates.model import World
        w = World(cls.plan["recipe"], cls.plan)
        cls.tl = timeline.of(w)

    def assertCatches(self, check, mutate, *args):
        objects = deepcopy(self.objects)
        mutate(objects)
        self.assertTrue(check(objects, *args), f"{check.__name__} missed the mutation")

    def test_ix(self):
        self.assertEqual(check_ix(self.objects, self.resolved), [])
        self.assertCatches(check_ix, lambda o: o["ix/chicago/rs-1"]["refs"].update(tenant="tenant"), self.resolved)
        self.assertCatches(check_ix, lambda o: o["circuit/ix/detroit"]["attrs"].update(comments=""), self.resolved)
        self.assertCatches(check_ix, lambda o: o["provider/ix-chicago"]["refs"]["asns"].append("asn/operator"), self.resolved)

    def test_ix_sessions(self):
        self.assertEqual(check_ix_sessions(self.objects, self.resolved), [])
        self.assertCatches(check_ix_sessions, lambda o: o["bgp-session/ix/chicago/rs-2"]["refs"].update(remote_as="asn/operator"), self.resolved)
        self.assertCatches(check_ix_sessions, lambda o: o.pop("bgp-session/ix/cleveland/rs-1"), self.resolved)

    def test_relocation_echo(self):
        self.assertTrue([k for k in self.objects if k.endswith("/former") and k.startswith("circuit/ix/")])
        self.assertEqual(check_relocation(self.objects), [])
        key = next(k for k in self.objects if k.startswith("circuit/ix/") and k.endswith("/former"))
        self.assertCatches(lambda o: check_relocation(o), lambda o: o[key]["attrs"].update(status="active"))

    def test_pop_services(self):
        self.assertTrue(any(self.tl.ddos.values()) and any(self.tl.timing.values()))
        self.assertEqual(check_services(self.objects, self.tl), [])
        tms = next(f"device/pop-{p}/ddos-01" for p, d in self.tl.ddos.items() if d)
        self.assertCatches(check_services, lambda o: o[tms]["attrs"].update(status="active"), self.tl)
        ntp = next(f"device/pop-{p}/ntp-01" for p, d in self.tl.timing.items() if d)
        self.assertCatches(check_services, lambda o: o[ntp]["refs"].pop("primary_ip4"), self.tl)
        self.assertEqual(self.objects["role/ddos-mitigation"]["attrs"]["name"], "DDoS Mitigation")
        self.assertEqual(self.objects["role/time-server"]["attrs"]["name"], "Time Server")

    def test_former_customers(self):
        self.assertTrue(8 <= len(self.resolved["former_customers"]) <= 12)
        self.assertEqual(check_former(self.objects, self.resolved), [])
        key = self.resolved["former_customers"][0]["key"]
        self.assertCatches(check_former, lambda o: o.update({f"circuit/former/{key}/A": dict(
            key=f"circuit/former/{key}/A", kind="circuit_termination", attrs={}, refs={"circuit": f"circuit/former/{key}"}, meta={})}),
            self.resolved)

    def test_former_customer_recipe_errors(self):
        for row, message in ((dict(key="anchor-insurance"), "unique"), (dict(pop="nowhere"), "known PoP"),
                             (dict(start=2030), "start"), (dict(extra=1), "exactly")):
            raw = deepcopy(self.raw)
            raw["former_customers"] = [dict(raw["former_customers"][0], **row)]
            with self.assertRaisesRegex(DesignError, message):
                provider.resolve(raw)

    def test_first_impression(self):
        self.assertEqual(check_first_impression(self.objects, self.resolved), [])
        self.assertCatches(check_first_impression, lambda o: o["tenant/cust-anchor-insurance"]["attrs"].update(
            description="Private L3 VPN customer"), self.resolved)

    def test_journal_shape(self):
        self.assertEqual(check_journals(self.objects), [])
        note = next(k for k in self.objects if "/maintenance-" in k)
        self.assertCatches(lambda o: check_journals(o), lambda o: o[note]["attrs"].update(
            created=o[note]["attrs"]["created"][:11] + "15:00:00Z"))
        site = "site/pop-chicago-cermak"
        self.assertIn("**Site access**", self.objects[site]["attrs"]["comments"])
        self.assertFalse([k for k in self.objects if k.endswith("/access-plan")])

    def test_paperwork(self):
        notes = Counter(k.rsplit("/", 1)[1].rstrip("0123456789-") for k in self.objects if k.startswith("journal/"))
        for event in ("cross-connect-order", "cir-upgrade", "maintenance", "term-renewal", "cage-audit"):
            self.assertGreater(notes[event], 0, event)
        self.assertEqual(check_paperwork(self.objects, self.resolved), [])
        upgrade = next(k for k in self.objects if "/cir-upgrade-" in k)
        circuit = self.objects[upgrade]["refs"]["assigned_object"]
        self.assertCatches(lambda o, r: check_paperwork(o, r), lambda o: o[circuit]["attrs"].update(
            install_date=self.resolved["as_of"]), self.resolved)

    def test_second_nid_generation(self):
        legacy = [o for o in of_kind(self.objects, "device") if o["refs"]["device_type"] == f"hardware/{provider.LEGACY_NID_ALIAS}"]
        self.assertGreaterEqual(len(legacy), 4)
        self.assertEqual(check_legacy_nids(self.objects, self.resolved), [])
        nid = next(o["key"] for o in of_kind(self.objects, "device") if o["refs"]["role"] == "role/nid"
                   and o["refs"]["device_type"] == f"hardware/{provider.NID_ALIAS}")
        self.assertCatches(check_legacy_nids, lambda o: o[nid]["refs"].update(device_type=f"hardware/{provider.LEGACY_NID_ALIAS}"),
                           self.resolved)

    def test_object_ceiling(self):
        self.assertLessEqual(len(self.plan["objects"]), 40000)


class IPv4OnlyExchangeTest(unittest.TestCase):
    """Without ipv6_pool the IX circuit stays and the sessions are omitted, with the reason."""

    def test_sessions_omitted(self):
        raw = showcase()
        raw.pop("ipv6_pool")
        plan = generate(resolve_recipe(raw))
        objects = index(plan)
        self.assertEqual(check_ix(objects, plan["recipe"]), [])
        self.assertEqual(check_ix_sessions(objects, plan["recipe"]), [])
        self.assertFalse([k for k in objects if k.startswith("ix/")])


class FormerCustomerGrowthTest(unittest.TestCase):
    """Appending a former customer moves no existing record; removing one needs a rebaseline."""

    def test_growth(self):
        raw = showcase()
        before = generate(resolve_recipe(raw))
        grown = deepcopy(raw)
        grown["former_customers"].append(dict(key="lantern-books", name="Lantern Books", service="dia",
                                              pop="chicago-loop", start=2014, end=2018))
        after = index(generate(resolve_recipe(grown), before))
        for key, obj in index(before).items():
            if key.startswith(("circuit/former/", "tenant/cust-", "journal/circuit/former/")):
                self.assertEqual(after[key], obj, key)
        self.assertIn("circuit/former/lantern-books", after)
        shrunk = deepcopy(raw)
        shrunk["former_customers"].pop()
        with self.assertRaisesRegex(DesignError, "former customer"):
            generate(resolve_recipe(shrunk), before)


if __name__ == "__main__":
    unittest.main()
