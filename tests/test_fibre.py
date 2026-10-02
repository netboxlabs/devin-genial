"""Fibre-plant delivery lanes: panels, port mappings, LAG and Q-in-Q (DESIGN L9).

Each test pins one loader obligation and fails if that obligation is removed:
front/rear ports compile as device components without the legacy mapping
column, the mapping travels as a receipt-first REST rear_ports PATCH, review
history expects one PortMapping create per mapped front port, readback traces
pass-through and circuit ends through /paths/, and the LAG / Q-in-Q
references compile to their native columns.
"""

import json
from pathlib import Path
import tempfile
import unittest

from estates.turbobulk import (DEFERRED, DIRECT_REFS, SUPPORTED_REFS, LoadError,
                               _candidate_bucket_key, _complete_rest,
                               _expected_change_diff_counts, _finalizer_plan, _matches,
                               _render, _rendered_columns, _rest_patch_preflight,
                               _row_bucket_keys, _verify_paths, rest_mutation_requirements)


def _obj(kind, key, attrs=None, refs=None):
    return {"kind": kind, "key": key, "attrs": attrs or {}, "refs": refs or {}, "meta": {}}


SITE = _obj("site", "site", {"name": "S", "slug": "s"})
DEVICE = _obj("device", "panel", {"name": "P1"}, {"site": "site"})
REAR = _obj("rear_port", "panel/rear/1", {"name": "R1", "type": "lc-apc", "positions": 1},
            {"device": "panel"})
FRONT = _obj("front_port", "panel/front/1",
             {"name": "F1", "type": "lc-apc", "rear_port_position": 1},
             {"device": "panel", "rear_port": "panel/rear/1"})
OBJECTS = {o["key"]: o for o in (SITE, DEVICE, REAR, FRONT)}
IDS = {"site": 2, "panel": 10, "panel/rear/1": 31, "panel/front/1": 41}


class PanelCompilation(unittest.TestCase):
    def test_front_port_row_drops_legacy_mapping_and_keeps_component_caches(self):
        row = _render(FRONT, OBJECTS, IDS, {})
        self.assertNotIn("rear_port", row)
        self.assertNotIn("rear_port_position", row)
        self.assertNotIn("rear_port_id", row)
        self.assertEqual(row["positions"], 1)
        self.assertEqual((row["device_id"], row["_site_id"], row["_location_id"], row["_rack_id"]),
                         (10, 2, None, None))
        self.assertEqual(set(row), _rendered_columns(FRONT))
        rear = _render(REAR, OBJECTS, IDS, {})
        self.assertEqual((rear["positions"], rear["_site_id"]), (1, 2))
        self.assertEqual(set(rear), _rendered_columns(REAR))

    def test_mapping_is_a_declared_rest_completion(self):
        self.assertIn("rear_port", DEFERRED)
        self.assertEqual(rest_mutation_requirements(OBJECTS)["patch_fields"],
                         ["front_port.rear_port"])

    def test_completion_patches_rear_ports_once_and_refuses_a_foreign_mapping(self):
        plan = {"objects": list(OBJECTS.values())}

        class Target:
            def __init__(self, mapping):
                self.mapping = mapping
                self.patches = []

            def all(self, path):
                assert path == "/api/dcim/front-ports/", path
                return [{"id": 41, "rear_ports": self.mapping}]

            def request(self, path, **kwargs):
                self.patches.append((path, json.loads(kwargs["body"])))
                return 200, []

        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            fresh = Target([])
            _complete_rest(fresh, plan, OBJECTS, IDS, {"rest_batches": []}, receipt_path)
            self.assertEqual(fresh.patches, [("/api/dcim/front-ports/", [
                {"id": 41, "rear_ports": [{"position": 1, "rear_port": 31,
                                           "rear_port_position": 1}]}])])
            done = Target([{"position": 1, "rear_port": {"id": 31}, "rear_port_position": 1}])
            _complete_rest(done, plan, OBJECTS, IDS, {"rest_batches": []}, receipt_path)
            self.assertEqual(done.patches, [])
            with self.assertRaisesRegex(LoadError, "rear_ports changed concurrently"):
                _complete_rest(Target([{"position": 1, "rear_port": 99, "rear_port_position": 1}]),
                               plan, OBJECTS, IDS, {"rest_batches": []}, receipt_path)

    def test_patch_preflight_requires_the_writable_rear_ports_field(self):
        class Target:
            def __init__(self, fields):
                self.fields = fields

            def request(self, path, **_kwargs):
                return 200, {"actions": {"PATCH": dict.fromkeys(self.fields)}}

        self.assertEqual(_rest_patch_preflight(Target({"rear_ports", "name"}), OBJECTS),
                         {"front_port": ["rear_ports"]})
        with self.assertRaisesRegex(LoadError, "cannot write: rear_ports"):
            _rest_patch_preflight(Target({"name"}), OBJECTS)

    def test_review_history_expects_one_portmapping_create_per_mapped_front_port(self):
        unmapped = _obj("front_port", "panel/front/2", {"name": "F2", "type": "lc-apc"},
                        {"device": "panel"})
        counts = _expected_change_diff_counts({**OBJECTS, unmapped["key"]: unmapped})
        self.assertEqual(counts["dcim.portmapping"], 1)
        self.assertEqual(counts["dcim.frontport"], 2)
        self.assertEqual(counts["dcim.rearport"], 1)

    def test_ports_match_by_name_and_device(self):
        for obj in (FRONT, REAR):
            row = {"id": IDS[obj["key"]], "name": obj["attrs"]["name"], "device": {"id": 10}}
            self.assertTrue(_matches(obj, row, IDS, OBJECTS))
            self.assertIn(_candidate_bucket_key(obj, IDS, OBJECTS),
                          _row_bucket_keys(obj["kind"], row))
            self.assertFalse(_matches(obj, {**row, "device": {"id": 11}}, IDS, OBJECTS))

    def test_finalizers_rebuild_links_paths_and_counters_for_cabled_panels(self):
        cable = _obj("cable", "c", {"label": "X1"}, {"a": "panel/rear/1", "b": "panel/front/1"})
        hooks = {(model, hook) for _purpose, model, hook in
                 _finalizer_plan({**OBJECTS, "c": cable})}
        self.assertLessEqual({("dcim.device", "fix_counters"),
                              ("dcim.cabletermination", "fix_cable_links"),
                              ("dcim.cabletermination", "rebuild_cable_paths")}, hooks)


class CircuitTerminationFields(unittest.TestCase):
    def test_mark_connected_and_cross_connect_fields_compile_as_columns(self):
        # Cellular OOB term A and single-NID handoffs are mark_connected; the
        # colo cross-connect identity rides xconnect_id/pp_info. All three are
        # plain CircuitTermination columns, so preflight must demand them.
        circuit = _obj("circuit", "c", {"cid": "C1"}, {})
        term = _obj("circuit_termination", "t",
                    {"term_side": "A", "mark_connected": True, "xconnect_id": "XC-1",
                     "pp_info": "MMR 2 / 14"}, {"circuit": "c", "termination": "site"})
        objects = {"site": SITE, "c": circuit, "t": term}
        row = _render(term, objects, {"site": 2, "c": 7}, {"site": 31})
        self.assertEqual((row["mark_connected"], row["xconnect_id"], row["pp_info"]),
                         (True, "XC-1", "MMR 2 / 14"))
        self.assertEqual((row["termination_type_id"], row["termination_id"]), (31, 2))
        self.assertLessEqual({"mark_connected", "xconnect_id", "pp_info"}, _rendered_columns(term))


class PathReadback(unittest.TestCase):
    def _run(self, a_kind, b_kind):
        objects = {
            "a": _obj(a_kind, "a", {"name": "A"}, {}),
            "b": _obj(b_kind, "b", {"name": "B"}, {}),
            "c": _obj("cable", "c", {"label": "XC-1"}, {"a": "a", "b": "b"}),
        }
        requested = []

        class Target:
            def request(self, path, **_kwargs):
                requested.append(path)
                return 200, [{"path": [[{"id": 1}], [{"id": 5, "label": "XC-1"}]]}]

        result = _verify_paths(Target(), {"objects": list(objects.values())}, objects,
                               {"a": 1, "b": 2, "c": 5}, workers=1)
        self.assertEqual(result["failures"], [])
        return requested[0]

    def test_trace_prefers_a_path_endpoint_on_either_side(self):
        self.assertEqual(self._run("interface", "front_port"), "/api/dcim/interfaces/1/trace/")
        self.assertEqual(self._run("rear_port", "interface"), "/api/dcim/interfaces/2/trace/")

    def test_passive_and_circuit_cables_read_their_cable_paths(self):
        self.assertEqual(self._run("rear_port", "circuit_termination"),
                         "/api/dcim/rear-ports/1/paths/")
        self.assertEqual(self._run("circuit_termination", "front_port"),
                         "/api/circuits/circuit-terminations/1/paths/")


class LagAndQinq(unittest.TestCase):
    def test_lag_and_service_vlan_compile_to_native_columns(self):
        self.assertEqual((DIRECT_REFS["lag"], DIRECT_REFS["qinq_svlan"]), ("lag_id", "qinq_svlan_id"))
        self.assertLessEqual({"lag", "qinq_svlan"}, SUPPORTED_REFS["interface"])
        self.assertIn("qinq_svlan", SUPPORTED_REFS["vlan"])
        objects = dict(OBJECTS)
        objects.update({o["key"]: o for o in (
            _obj("vlan", "svlan", {"vid": 3001, "name": "S", "qinq_role": "svlan"}, {"site": "site"}),
            _obj("vlan", "cvlan", {"vid": 10, "name": "C", "qinq_role": "cvlan"},
                 {"qinq_svlan": "svlan"}),
            _obj("interface", "ae0", {"name": "ae0", "type": "lag"}, {"device": "panel"}),
            _obj("interface", "xe0", {"name": "xe-0/0/40", "type": "10gbase-x-sfpp"},
                 {"device": "panel", "lag": "ae0"}),
            _obj("interface", "uni", {"name": "xe-0/0/1", "type": "10gbase-x-sfpp", "mode": "q-in-q"},
                 {"device": "panel", "qinq_svlan": "svlan"}),
        )})
        ids = {**IDS, "svlan": 70, "cvlan": 71, "ae0": 80, "xe0": 81, "uni": 82}
        self.assertEqual(_render(objects["xe0"], objects, ids, {})["lag_id"], 80)
        uni = _render(objects["uni"], objects, ids, {})
        self.assertEqual((uni["qinq_svlan_id"], uni["mode"]), (70, "q-in-q"))
        self.assertEqual(_render(objects["cvlan"], objects, ids, {})["qinq_svlan_id"], 70)
        self.assertIn("lag_id", _rendered_columns(objects["xe0"]))

    def test_vlan_identity_honours_site_group_and_service_vlan_scopes(self):
        objects = {o["key"]: o for o in (
            _obj("vlan", "grouped", {"vid": 4001, "name": "N"}, {"group": "g", "site": "site"}),
            _obj("vlan", "inner", {"vid": 10, "name": "C"}, {"qinq_svlan": "svlan"}),
        )}
        ids = {"g": 5, "site": 2, "svlan": 70}
        grouped, inner = objects["grouped"], objects["inner"]
        self.assertTrue(_matches(grouped, {"vid": 4001, "group": {"id": 5}, "site": {"id": 2}}, ids))
        self.assertFalse(_matches(grouped, {"vid": 4001, "group": {"id": 6}, "site": {"id": 2}}, ids))
        self.assertTrue(_matches(inner, {"vid": 10, "qinq_svlan": {"id": 70}}, ids))
        # The same VID under another S-VLAN, or unscoped, is a different VLAN.
        self.assertFalse(_matches(inner, {"vid": 10, "qinq_svlan": {"id": 71}}, ids))
        self.assertFalse(_matches(inner, {"vid": 10, "qinq_svlan": None, "site": {"id": 2}}, ids))
        self.assertIn(_candidate_bucket_key(inner, ids), _row_bucket_keys("vlan", {"vid": 10}))


# --- DESIGN §7 plan-level gates: independent validators with failing mutations ---

GATE_RECIPE = dict(profile="provider-backbone", namespace="fibre-gates", name="Lakeside Carrier", seed=7,
                   as_of="2026-09-01",
                   pops=[dict(key="chicago-loop", metro="chicago"), dict(key="detroit-corktown", metro="detroit"),
                         dict(key="cleveland-flats", metro="cleveland"), dict(key="chicago-pilsen", metro="chicago")],
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
POP, HUB, SPOKE = "pop-chicago-loop", "ce-anchor-bank-chicago-loop-001", "ce-anchor-bank-chicago-loop-002"
DIA, MANAGED, EPL = "ce-brightpath-detroit-corktown-001", "ce-alder-dental-chicago-loop-001", "ce-calumet-steel-chicago-loop-001"


class FootprintGates(unittest.TestCase):
    """Each §7 gate holds on a generated estate and fails when its obligation is broken."""

    @classmethod
    def setUpClass(cls):
        from estates.generate import generate
        cls.baseline = generate(GATE_RECIPE)

    def setUp(self):
        from copy import deepcopy
        self.plan = deepcopy(self.baseline)
        self.o = {obj["key"]: obj for obj in self.plan["objects"]}

    def codes(self):
        from estates.validate import validate
        return {finding["code"] for finding in validate(self.plan)}

    def cable(self, end):
        return next(o for o in self.plan["objects"] if o["kind"] == "cable" and end in o["refs"].values())

    def peer(self, end):
        cable = self.cable(end)
        return next(v for v in cable["refs"].values() if v != end)

    def front(self, rear):
        return next(k for k, o in self.o.items() if o["kind"] == "front_port" and o["refs"].get("rear_port") == rear)

    def check(self, code, mutate):
        """Mutate the current plan, require the finding, then restore a fresh copy."""
        mutate()
        self.assertIn(code, self.codes())
        self.setUp()

    def test_the_gate_estate_validates_clean(self):
        from estates.validate import validate
        self.assertEqual(validate(self.plan), [])

    def test_aggregation_lag_is_straight_intra_cabinet_and_four_wide(self):
        agg, pe = f"device/{POP}/agg-a", f"device/{POP}/pe-a"
        members = [k for k, o in self.o.items() if o["kind"] == "interface" and o["refs"].get("lag") == f"{agg}/if/ae0"]
        self.assertEqual(len(members), 4)
        for member in members:
            self.assertEqual(self.o[self.o[self.peer(member)]["refs"]["device"]]["refs"]["rack"], self.o[agg]["refs"]["rack"])
        self.check("provider-aggregation-lag", lambda: self.o[members[0]]["refs"].pop("lag"))
        self.check("provider-aggregation-lag", lambda: self.o[f"{pe}/if/xe-0/1/3"]["refs"].pop("lag"))

    def test_service_vlans_ride_only_their_home_side(self):
        att = self.o[f"device/{POP}/agg-a/if/ae0"]["refs"]["tagged_vlans"]
        homed = next(v for v in att if v.endswith("/customer"))
        self.assertNotIn(homed, self.o[f"device/{POP}/agg-b/if/ae0"]["refs"]["tagged_vlans"])
        self.check("provider-home-vlans", lambda: self.o[f"device/{POP}/pe-b/if/ae1"]["refs"]["tagged_vlans"].append(homed))
        self.check("provider-home-vlans", lambda: self.o[f"device/{POP}/agg-a/if/ae0"]["refs"]["tagged_vlans"].remove(homed))

    def test_nid_and_ce_kits_follow_the_service(self):
        # A hub has two NIDs; single-NID services carry no CE.
        self.assertIn(f"device/{HUB}/nid-02", self.o)
        self.assertNotIn(f"device/{DIA}/edge-01", self.o)
        self.check("provider-nid", lambda: self.o[f"device/{DIA}/nid-01"]["refs"].update(rack=f"rack/{MANAGED}/network-01"))
        self.check("provider-nid", lambda: self.o[f"ip/device/{HUB}/nid-01/if/Management"]["attrs"].update(address="10.255.255.2/25"))
        self.check("provider-customer-edge", lambda: self.o[f"device/{SPOKE}/edge-01"]["refs"].update(device_type="hardware/edge"))
        self.check("provider-device-inventory", lambda: self.plan["objects"].remove(self.o[f"device/{HUB}/nid-02"]))

    def test_multi_device_premises_use_one_mpoe_cabinet(self):
        rack = f"rack/{HUB}/network-01"
        self.assertEqual((self.o[rack]["attrs"]["name"], self.o[rack]["refs"]["rack_type"]), ("MPOE-1", "rack-type/mpoe-cabinet"))
        self.check("provider-mpoe-cabinet", lambda: self.o[rack]["refs"].update(rack_type="rack-type/42u"))
        self.check("provider-mpoe-cabinet", lambda: self.o[f"rack/{MANAGED}/network-01"]["refs"].update(tenant="tenant/cust-alder-dental"))

    def test_panels_map_one_to_one(self):
        front = f"device/{POP}/r01-osp/front/1"
        self.assertEqual(self.o[front]["refs"]["rear_port"], f"device/{POP}/r01-osp/rear/1")
        self.check("provider-panel-mapping", lambda: self.o[front]["refs"].update(rear_port=f"device/{POP}/r01-osp/rear/2"))

    def test_trace_halves_s6a_and_s6b(self):
        # S6a: CE wan1 -> NID port 3, one labelled hop.
        wan, uni = f"device/{HUB}/edge-01/if/wan1", f"device/{HUB}/nid-01/if/3"
        self.assertEqual(self.peer(wan), uni)
        self.assertTrue(self.cable(wan)["attrs"]["label"])
        # S6b: NID port 1 -> term A, term Z -> OSP rear -> front -> AGG UNI.
        circuit = f"circuit/customer/{HUB}"
        self.assertEqual(self.peer(f"device/{HUB}/nid-01/if/1"), f"{circuit}/A")
        rear = self.peer(f"{circuit}/Z")
        self.assertEqual(self.o[self.o[rear]["refs"]["device"]]["refs"]["device_type"], "hardware/osp-panel")
        uni = self.peer(self.front(rear))
        self.assertEqual(self.o[uni]["refs"]["device"], f"device/{POP}/agg-a")

        def skip_panel():
            self.cable(f"{circuit}/Z")["refs"].update(a=f"{circuit}/Z", b=uni)
            self.plan["objects"].remove(self.cable(self.front(rear)))
        self.check("provider-circuit-path", skip_panel)
        self.check("provider-customer-handoff", lambda: self.plan["objects"].remove(self.cable(wan)))

    def test_every_local_circuit_end_has_a_complete_path(self):
        rear = self.peer(f"circuit/customer/{SPOKE}/Z")
        self.check("provider-path-complete", lambda: self.plan["objects"].remove(self.cable(self.front(rear))))

    def test_colo_demarc_is_the_hotels_and_lands_only_carrier_cross_connects(self):
        demarc = f"device/{POP}/r01-demarc"
        self.assertTrue(self.o[demarc]["refs"]["tenant"].startswith("tenant/colo/"))
        self.check("provider-device-inventory", lambda: self.o[demarc]["refs"].update(tenant="tenant"))
        transit = next(t for t in ("circuit/transit/a/A", "circuit/transit/b/A")
                       if self.o[t]["refs"]["termination"] == f"site/{POP}")
        self.assertEqual(self.cable(transit)["attrs"]["label"], self.o[transit]["attrs"]["xconnect_id"])
        self.check("provider-cross-connect", lambda: self.cable(transit)["attrs"].update(label="CHI-R01-999"))

    def test_owned_spans_land_on_the_osp_panel(self):
        span = next(k for k, o in self.o.items() if o["kind"] == "circuit" and k.startswith("circuit/backbone/")
                    and o["refs"]["provider"] == "provider/operator")
        term = f"{span}/A"
        rear = self.peer(term)
        self.assertEqual(self.o[self.o[rear]["refs"]["device"]]["refs"]["device_type"], "hardware/osp-panel")

        def to_demarc():
            # Re-land the owned fibre on the same position of the colo panel.
            panel = self.o[rear]["refs"]["device"].replace("-osp", "-demarc")
            position = rear.rsplit("/", 1)[1]
            port = self.peer(self.front(rear))
            self.cable(term)["refs"].update(a=f"{panel}/rear/{position}", b=term)
            self.cable(self.front(rear))["refs"].update(a=f"{panel}/front/{position}", b=port)
        self.check("provider-circuit-path", to_demarc)

    def test_epl_is_port_based_qinq_with_exactly_two_terminations(self):
        l2vpn = "l2vpn/epl/calumet-steel"
        self.assertEqual(self.o[l2vpn]["attrs"]["type"], "epl")
        uni = self.o[f"device/{EPL}/nid-01"]
        subif = self.o[f"l2vpn/epl/calumet-steel/{EPL}"]["refs"]["assigned_object"]
        agg_uni = next(k for k, o in self.o.items() if o["kind"] == "interface" and o["attrs"].get("mode") == "q-in-q")
        self.assertEqual(self.o[self.o[agg_uni]["refs"]["qinq_svlan"]]["attrs"]["qinq_role"], "svlan")
        self.check("provider-epl", lambda: self.plan["objects"].remove(self.o[f"l2vpn/epl/calumet-steel/{EPL}"]))
        self.check("provider-attachment", lambda: self.o[agg_uni]["attrs"].update(mode="tagged"))
        self.check("provider-epl", lambda: self.o[l2vpn]["attrs"].update(identifier=1))
        self.assertTrue(uni and subif)

    def test_lte_modem_is_uncabled_and_its_circuit_end_marked_connected(self):
        modem = f"device/{POP}/console-01/if/Cellular Interface (LTE)"
        self.assertEqual(self.o[modem]["attrs"]["type"], "lte")
        self.check("provider-oob", lambda: self.o[f"circuit/oob/chicago-loop/A"]["attrs"].pop("mark_connected"))
        self.check("provider-oob", lambda: self.o[f"ip/{modem}"]["refs"].update(vrf="vrf/provider"))

    def test_cabinet_elevation_holds_exact_units(self):
        self.assertEqual(self.o[f"device/{POP}/pe-a"]["attrs"]["position"], 37)
        self.assertEqual(self.o[f"device/{POP}/r01-osp"]["attrs"]["position"], 42)
        self.check("provider-device-inventory", lambda: self.o[f"device/{POP}/agg-a"]["attrs"].update(position=30))
        self.check("provider-rack-geometry", lambda: self.o[f"device/{POP}/console-01"]["refs"].update(rack=f"rack/{POP}/r03"))

    def test_every_pop_cable_follows_the_cable_policy(self):
        fxp0 = f"device/{POP}/pe-a/if/fxp0"
        self.check("provider-cable-policy", lambda: self.cable(fxp0)["attrs"].pop("label"))
        self.check("operations-cable-colour", lambda: self.cable(fxp0)["attrs"].update(color="2196f3"))

    def test_site_scoped_two_site_circuits_are_the_expected_arcs(self):
        term = f"circuit/customer/{SPOKE}/A"
        self.check("provider-wan-arcs", lambda: self.o[term]["refs"].update(termination=f"location/{SPOKE}"))

    def test_asn_labels_name_their_holder_in_thirty_characters(self):
        self.assertLessEqual(max(len(o["attrs"]["description"]) for o in self.o.values() if o["kind"] == "asn"), 30)
        self.check("provider-asn-text", lambda: self.o["asn/customer/anchor-bank"]["attrs"].update(
            description="Anchor Bank private VPN routing identity"))

    def test_ibgp_is_recorded_from_both_ends(self):
        mirror = "bgp-session/ibgp/pop-cleveland-flats/pe-a/pop-chicago-loop/pe-a"
        self.assertIn(mirror, self.o)
        self.check("provider-bgp-inventory", lambda: self.plan["objects"].remove(self.o[mirror]))

    def test_premises_stand_three_hundred_metres_apart(self):
        def stack():
            a, b = self.o[f"site/{HUB}"]["attrs"], self.o[f"site/{SPOKE}"]["attrs"]
            b.update(latitude=a["latitude"] + 0.001, longitude=a["longitude"])  # about 111 m north
        self.check("provider-premises-spacing", stack)

    def test_object_ceiling(self):
        from unittest.mock import patch
        import estates.validate_provider as vp
        with patch.object(vp, "OBJECT_CEILING", len(self.plan["objects"]) - 1):
            self.assertIn("provider-object-ceiling", self.codes())

    def test_lag_capacity_holds_after_one_member_loss(self):
        from unittest.mock import patch
        import estates.validate_provider as vp
        # The 5 Gbps DIA's side, re-run with 2 Gbps members: four carry
        # 8 Gbps x 0.8 = 6.4 Gbps (steady state holds), three only 4.8 Gbps,
        # so the loss of one member is the finding.
        from estates.validate import validate
        with patch.object(vp, "LAG_MEMBER_KBPS", 2000000):
            findings = validate(self.plan)
        capacity = [f for f in findings if f["code"] == "provider-aggregation-capacity"]
        self.assertTrue(capacity)
        self.assertTrue(all("after one member loss" in f["message"] for f in capacity))

    def test_dia_addressing_and_customer_owned_handoffs(self):
        self.check("provider-dia-address", lambda: self.o[f"device/{DIA}/nid-01/if/3"]["attrs"].pop("mark_connected"))
        self.check("provider-dia-address", lambda: self.o[f"prefix/dia/{DIA}"]["attrs"].update(prefix="10.9.9.0/29"))

    def test_no_access_attachment_on_a_pe_physical_port(self):
        def attach_on_pe():
            rear = self.peer(f"circuit/customer/{SPOKE}/Z")
            self.cable(self.front(rear))["refs"].update(b=f"device/{POP}/pe-a/if/xe-0/1/5")
        self.check("provider-port-use", attach_on_pe)


class ShowcaseProxies(unittest.TestCase):
    """DESIGN §7 offline plan proxies, measured on the showcase recipe before any screen is judged."""

    @classmethod
    def setUpClass(cls):
        import tomllib
        from estates.generate import generate
        cls.plan = generate(tomllib.loads((Path(__file__).parents[1] / "profiles/showcase-provider.toml").read_text()))
        cls.o = {obj["key"]: obj for obj in cls.plan["objects"]}

    def of(self, kind):
        return [obj for obj in self.plan["objects"] if obj["kind"] == kind]

    def test_scale_density_and_variety(self):
        from collections import Counter
        premises = [s for s in self.of("site") if s["key"].startswith("site/ce-")]
        pes = [d for d in self.of("device") if d["refs"].get("role") == "role/provider-edge"]
        self.assertLessEqual(len(self.plan["objects"]), 40000)
        self.assertGreaterEqual(len(premises) / len(pes), 10)
        # About 9-10 private-L3 CE peers per PE, recomputed from the sessions.
        peers = Counter(s["refs"]["device"] for s in self.of("bgp_session")
                        if s["key"].startswith("bgp-session/customer/") and not s["key"].endswith("/ipv6"))
        self.assertTrue(9 <= sum(peers.values()) / len(pes) <= 10)
        self.assertGreaterEqual(len({s["attrs"]["description"] for s in premises}), 4)

    def test_s1_s3_cabinet_fill_and_pop_inventory(self):
        from collections import Counter
        site = "site/pop-chicago-cermak"
        fill = {rack: sum(self.o[d["refs"]["device_type"]]["attrs"]["u_height"] for d in self.of("device")
                          if d["refs"].get("rack") == rack and d["attrs"].get("position") is not None)
                for rack in (f"rack/pop-chicago-cermak/r0{n}" for n in (1, 2))}
        self.assertEqual([round(100 * u / 42, 1) for u in fill.values()], [23.8, 19.0])
        roles = Counter(d["refs"]["role"] for d in self.of("device") if d["refs"].get("site") == site)
        self.assertEqual(roles, Counter({"role/provider-edge": 2, "role/aggregation": 2, "role/patch-panel": 4,
                                         "role/cable-management": 6, "role/management": 1, "role/console-server": 1,
                                         "role/pdu": 4}))

    def test_s5_wan_map_arcs(self):
        terms = {}
        for t in self.of("circuit_termination"):
            terms.setdefault(t["refs"]["circuit"], set()).add(self.o[t["refs"]["termination"]]["kind"])
        arcs = sum(kinds == {"site"} for kinds in terms.values())
        self.assertTrue(320 <= arcs <= 340, arcs)

    def test_s8_s9_first_page_order(self):
        from collections import Counter
        # NetBox orders names with its natural_sort collation: case is not a
        # primary key, so "R01 CM-36" does not lead lowercase hostnames.
        sites = sorted(self.of("site"), key=lambda s: s["attrs"]["name"].casefold())[:10]
        with_ce = {d["refs"]["site"] for d in self.of("device") if d["refs"].get("role") == "role/customer-edge"}
        self.assertFalse([s for s in sites if s["key"].startswith("site/ce-") and s["key"] not in with_ce])
        devices = sorted(self.of("device"), key=lambda d: d["attrs"]["name"].casefold())[:25]
        makers = {self.o[self.o[d["refs"]["device_type"]]["refs"]["manufacturer"]]["attrs"]["name"] for d in devices}
        self.assertGreaterEqual(len(makers), 3)
        self.assertGreaterEqual(len(Counter(d["refs"]["role"] for d in devices)), 2)
        passive = sum(d["refs"]["role"] in ("role/patch-panel", "role/cable-management", "role/pdu") for d in devices)
        self.assertLessEqual(passive, 5)


if __name__ == "__main__":
    unittest.main()
