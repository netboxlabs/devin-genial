"""Mutations prove relationships, not merely presence of the new entity types."""

from copy import deepcopy
import unittest

from estates.bank import generate
from estates.scenarios import create
from estates.validate_networking import validate


class NetworkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = generate({"branches": {"small": 3}, "headquarters": 1, "headquarters_staff": 24,
                             "site_designs": {"br-s0001": "inherited", "br-s0002": "modern", "br-s0003": "refreshed"}})

    def mutation(self, edit, code):
        plan = deepcopy(self.plan)
        objects = {o["key"]: o for o in plan["objects"]}
        edit(objects, plan)
        self.assertIn(code, {f["code"] for f in validate(plan)})

    def test_all_families_are_connected(self):
        self.assertEqual(validate(self.plan), [])
        kinds = {o["kind"] for o in self.plan["objects"]}
        self.assertTrue({"asn", "asn_range", "aggregate", "rir", "ip_range", "role", "route_target", "vlan_group",
                         "fhrp_group", "fhrp_group_assignment", "ike_proposal", "ike_policy", "ip_sec_proposal",
                         "ip_sec_policy", "ip_sec_profile", "tunnel", "tunnel_group", "tunnel_termination",
                         "l2vpn", "l2vpn_termination", "vlan_translation_policy", "vlan_translation_rule",
                         "virtual_circuit", "virtual_circuit_type", "virtual_circuit_termination",
                         "wireless_lan", "wireless_lan_group", "wireless_link", "mac_address"} <= kinds)

    def test_panels_and_staff_boundary(self):
        self.assertEqual(validate(generate({"branches": {"small": 1}, "headquarters_staff": 192, "patching": "panels"})), [])
        self.assertEqual(validate(generate({"branches": {}, "headquarters": 0})), [])

    def test_missing_family_is_not_silent_success(self):
        self.mutation(lambda o, p: p["objects"].__setitem__(slice(None), [v for v in p["objects"] if v["kind"] != "fhrp_group"]), "network-family-missing")

    def test_asn_outside_allocation(self):
        self.mutation(lambda o, p: o["asn/bank"]["attrs"].update(asn=64512), "asn-allocation")

    def test_overlapping_custom_pool_collapses_with_inherited_allocation(self):
        plan = generate({"address_pool": "172.16.0.0/16", "branches": {"small": 1}, "headquarters": 0,
                         "site_designs": {"br-s0001": "inherited"}})
        self.assertEqual([o["attrs"]["prefix"] for o in plan["objects"] if o["kind"] == "aggregate"],
                         ["172.16.0.0/12"])
        self.assertEqual(validate(plan), [])

    def test_aggregate_overlap_is_global_across_registries(self):
        def overlap(objects, plan):
            registry = deepcopy(objects["rir/private"])
            registry["key"] = "rir/other"
            registry["attrs"].update(name="Other private registry", slug="other-private")
            aggregate = deepcopy(objects["aggregate/10.0.0.0/8"])
            aggregate["key"] = "aggregate/overlap"
            aggregate["attrs"]["prefix"] = "10.0.0.0/16"
            aggregate["refs"]["rir"] = registry["key"]
            plan["objects"].extend([registry, aggregate])
        self.mutation(overlap, "aggregate-overlap")

    def test_vlan_wrong_site_group(self):
        self.mutation(lambda o, p: o["vlan/br-s0001/users"]["refs"].update(group="vlan-group/dc-01"), "vlan-group-scope")

    def test_reserved_range_overlap(self):
        self.mutation(lambda o, p: o["ip-range/br-s0001/reserve"]["attrs"].update(
            start_address=o["ip/device/br-s0001/desk-001/if/eth0"]["attrs"]["address"]), "ip-range-occupied")

    def test_vrrp_duplicate_owner(self):
        self.mutation(lambda o, p: o["fhrp/dc-01/applications/member/2"]["refs"].update(
            interface=o["fhrp/dc-01/applications/member/1"]["refs"]["interface"]), "fhrp-members")

    def test_vrrp_protocol_range(self):
        self.mutation(lambda o, p: o["fhrp/dc-01/applications"]["attrs"].update(group_id=256), "fhrp-identity")

    def test_vrrp_vip_wrong_subnet(self):
        self.mutation(lambda o, p: o["ip/fhrp/dc-01/applications"]["attrs"].update(address="192.0.2.254/24"), "fhrp-subnet")

    def test_staff_vlan_missing_from_actual_ap_wire(self):
        self.mutation(lambda o, p: o["device/br-s0001/ap-001/if/eth0"]["refs"].update(tagged_vlans=[]), "wireless-uplink")

    def test_same_ssid_has_distinct_site_groups(self):
        wlans = [o for o in self.plan["objects"] if o["kind"] == "wireless_lan"]
        self.assertEqual(len({o["attrs"]["ssid"] for o in wlans}), 1)
        self.assertEqual(len({o["refs"]["group"] for o in wlans}), len(wlans))
        self.mutation(lambda o, p: o["wireless-lan/br-s0001/staff"]["refs"].update(
            group=o["wireless-lan/br-s0002/staff"]["refs"]["group"]), "wireless-lan-identity")

    def test_radio_channel_mismatch(self):
        self.mutation(lambda o, p: o["device/br-s0001/ap-002/if/wlan1"]["attrs"].update(rf_channel="5g-36-5180-20"), "wireless-link-channel")

    def test_radio_wrong_subnet_and_distance(self):
        self.mutation(lambda o, p: o["ip/device/br-s0001/ap-002/if/wlan1"]["attrs"].update(address="192.0.2.1/31"), "wireless-link-address")
        self.mutation(lambda o, p: o["wireless-link/br-s0001/diagnostic"]["attrs"].update(distance=0), "wireless-link-distance")

    def test_virtual_circuit_must_be_virtual_and_follow_provider(self):
        key = "virtual-circuit-termination/device/br-s0001/edge-a/if/wan1"
        self.mutation(lambda o, p: o[key]["refs"].update(interface="device/br-s0001/edge-a/if/wan1"), "virtual-wan-path")
        self.mutation(lambda o, p: o[key]["refs"].update(virtual_circuit="virtual-circuit/b"), "virtual-wan-path")

    def test_missing_virtual_circuit_attachment(self):
        key = "virtual-circuit-termination/device/br-s0001/edge-a/if/wan1"
        self.mutation(lambda o, p: p["objects"].remove(o[key]), "virtual-wan-missing")

    def test_tunnel_outside_ip_must_be_local(self):
        self.mutation(lambda o, p: o["tunnel/recovery/dc-01"]["refs"].update(outside_ip="ip/device/dc-02/edge-001-a/if/wan1"), "tunnel-outside-ip")

    def test_translation_matches_actual_remote_vlan(self):
        self.mutation(lambda o, p: o["vlan-translation/dc-01/rule"]["attrs"].update(remote_vid=999), "recovery-vlan-translation")

    def test_weak_ipsec_proposal(self):
        self.mutation(lambda o, p: o["ipsec-proposal/recovery"]["attrs"].update(encryption_algorithm="des-cbc"), "ipsec-proposal")

    def test_mac_must_be_locally_administered_unicast(self):
        self.mutation(lambda o, p: next(v for v in o.values() if v["kind"] == "mac_address")["attrs"].update(mac_address="01:00:00:00:00:01"), "mac-identity")

    def test_primary_mac_belongs_to_exact_interface(self):
        source = "device/br-s0001/ap-001/if/eth0"
        other = "mac/device/br-s0001/ap-002/if/eth0"
        self.mutation(lambda o, p: o[source]["refs"].update(primary_mac_address=other), "mac-primary-owner")
        self.mutation(lambda o, p: o[source]["refs"].pop("primary_mac_address"), "mac-primary-missing")

    def test_growth_keeps_prior_network_service_objects(self):
        before = generate({"branches": {"small": 1}, "headquarters": 0, "site_designs": {"br-s0001": "inherited"}})
        recipe = deepcopy(before["recipe"])
        recipe["branches"]["small"] = 2
        after = generate(recipe, previous=before)
        self.assertEqual(validate(after), [])
        old, new = ({v["key"]: v for v in p["objects"]} for p in (before, after))
        for key, obj in old.items():
            if key.startswith(("asn", "aggregate", "rir", "fhrp", "ip/fhrp", "route-target", "wireless-", "virtual-circuit", "ip-range", "tunnel", "ipsec", "ike", "l2vpn", "vlan-translation", "mac/")):
                self.assertEqual(obj, new[key], key)

    def test_acquisition_retains_network_identities_and_addressing(self):
        result = create(self.plan, "br-s0001")
        snapshots = result["plans"]
        for plan in snapshots.values():
            self.assertEqual(validate(plan), [])
        objects = [{o["key"]: o for o in plan["objects"]} for plan in snapshots.values()]
        for key in ("asn/birch", "fhrp/dc-01/applications", "mac/device/br-s0001/ap-001/if/eth0"):
            self.assertTrue(all(index[key] == objects[0][key] for index in objects))
        for key in ("ip/device/br-s0001/ap-001/if/wlan1", "ip-range/br-s0001/reserve"):
            self.assertTrue(all(index[key]["attrs"] == objects[0][key]["attrs"] for index in objects))


if __name__ == "__main__":
    unittest.main()
