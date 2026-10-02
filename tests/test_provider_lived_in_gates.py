"""v0.18 lived-in carrier: the independent validators' gates (DESIGN §7), each with its failing mutation.

The validators re-derive the plant from the frozen provider timeline ledgers
and the graph (estates/validate_provider.py, validate_optics.py,
validate_datacenter.py); every test here holds on the generated showcase and
fails when the obligation it names is broken. Builder-side shape tests live
in test_lived_in_plant.py and test_lived_in_services.py.
"""

from copy import deepcopy
from datetime import date
from pathlib import Path
import tomllib
import unittest
from unittest.mock import patch

import estates.validate_provider as vp
from estates.generate import generate
from estates.validate import validate


ROOT = Path(__file__).resolve().parent.parent
CERMAK, PILSEN, OAK_CREEK = "pop-chicago-cermak", "pop-chicago-pilsen", "pop-milwaukee-oak-creek"
LEGACY_DIA = "ce-delta-river-casino-chicago-cermak-001"


def load(name):
    return tomllib.loads((ROOT / "profiles" / name).read_text())


class LivedInGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = generate(load("showcase-provider.toml"))

    def setUp(self):
        self.plan = deepcopy(self.baseline)
        self.o = {obj["key"]: obj for obj in self.plan["objects"]}

    def codes(self):
        return {finding["code"] for finding in validate(self.plan)}

    def check(self, code, mutate):
        """Mutate a fresh copy, require the finding, then restore."""
        self.setUp()
        mutate()
        self.assertIn(code, self.codes())
        self.setUp()

    def cable(self, end):
        return next(o for o in self.plan["objects"] if o["kind"] == "cable" and end in o["refs"].values())

    def drop(self, key):
        self.plan["objects"].remove(self.o[key])

    def test_the_showcase_and_provider_profiles_validate_clean(self):
        self.assertEqual(validate(self.plan), [])
        for name in ("provider-backbone.toml", "provider-maintenance.toml"):
            with self.subTest(name):
                self.assertEqual(validate(generate(load(name))), [])

    # --- Stratigraphy: units from the install ledger, gaps blanked and journaled ---

    def test_units_follow_the_frozen_install_ledger(self):
        ledger = self.plan["reservations"][f"provider-cabinet-u/rack/{CERMAK}/r01"]
        self.assertEqual(self.o[f"device/{CERMAK}/pe-a"]["attrs"]["position"], ledger["pe-a"])
        self.check("provider-device-inventory", lambda: self.o[f"device/{CERMAK}/pe-a"]["attrs"].update(position=20))

        def swap():  # two ledgered items trade places: no longer top-down in install order
            ledger = self.plan["reservations"][f"provider-cabinet-u/rack/{CERMAK}/r01"]
            ledger["agg-a"], ledger["mgmt-01"] = ledger["mgmt-01"], ledger["agg-a"]
        self.check("provider-rack-geometry", swap)
        self.check("provider-rack-geometry", lambda: self.plan["reservations"][f"provider-cabinet-u/rack/{CERMAK}/r01"]
                   .pop("original-agg-a"))

    def test_removed_devices_leave_blanked_gaps_with_a_dated_rack_journal(self):
        blank = next(k for k in self.o if k.startswith(f"device/{CERMAK}/r01-blank-"))
        self.check("provider-device-inventory", lambda: self.drop(blank))
        journal = f"journal/rack/{CERMAK}/r01/removed/original-agg-a"
        self.assertIn(journal, self.o)
        self.check("provider-rack-journal", lambda: self.drop(journal))
        self.check("provider-rack-journal", lambda: self.o[journal]["attrs"].update(created="2026-01-01T15:00:00Z"))

    def test_core_is_a_two_cabinet_cage_and_edge_one_cabinet(self):
        def third_cabinet():
            rack = deepcopy(self.o[f"rack/{CERMAK}/r02"])
            rack.update(key=f"rack/{CERMAK}/r03")
            rack["attrs"].update(name="R03", facility_id="G09-03", asset_tag="X-00999")
            self.plan["objects"].append(rack)
        self.check("provider-rack-geometry", third_cabinet)
        self.check("provider-room-geometry", lambda: self.o[f"location/{PILSEN}"]["attrs"].update(name="Cage B21"))
        self.check("provider-room-geometry", lambda: self.o[f"location/{CERMAK}"]["attrs"].update(description="Operator cage"))

    def test_history_devices_keep_status_legacy_name_and_tag(self):
        relic = f"device/{CERMAK}/legacy-pe-a"
        self.assertEqual(self.o[relic]["attrs"]["status"], "decommissioning")
        self.check("provider-device-inventory", lambda: self.o[relic]["attrs"].update(status="active"))
        self.check("provider-device-inventory", lambda: self.o[relic]["attrs"].update(name="chicago-cermak-rtr1"))
        self.check("provider-device-inventory", lambda: self.o[relic]["refs"].update(tags=[]))
        self.check("provider-device-inventory", lambda: self.o[f"device/{CERMAK}/pe-a"]["refs"].setdefault(
            "tags", []).append("tag/legacy-naming"))
        self.check("provider-device-inventory", lambda: self.o[f"device/{CERMAK}/agg-spare"]["attrs"].update(status="active"))

    def test_out_of_service_devices_are_uncabled_and_unpowered(self):
        def cable_the_spare():
            self.cable(f"device/{CERMAK}/agg-a/if/em0")["refs"]["a"] = f"device/{CERMAK}/agg-spare/if/em0"
        self.check("provider-out-of-service", cable_the_spare)

        def power_the_relic():
            cord = self.cable(f"device/{CERMAK}/agg-a/power/PSU 0")
            end = next(s for s in ("a", "b") if cord["refs"][s] == f"device/{CERMAK}/agg-a/power/PSU 0")
            cord["refs"][end] = f"device/{CERMAK}/legacy-pe-a/power/PEM0"
        self.check("provider-out-of-service", power_the_relic)
        self.check("provider-out-of-service",
                   lambda: self.o[f"device/{CERMAK}/legacy-pe-a/if/xe-0/0/0"]["attrs"].update(enabled=True))

        def strand_the_time_server():  # an active device with no cabled port
            self.drop(self.cable(f"device/{CERMAK}/ntp-01/if/lan1")["key"])
        self.check("provider-out-of-service", strand_the_time_server)

    def test_panel_ports_carry_their_tia_606_labels(self):
        self.assertEqual(self.o[f"device/{CERMAK}/r01-osp/front/1"]["attrs"]["label"], "G09-01.42:01")
        self.check("provider-panel-mapping", lambda: self.o[f"device/{CERMAK}/r01-osp/front/1"]["attrs"].update(label="P1"))

    # --- Services on the PoP management LAN, the successor and the PE ports ---

    def test_time_server_and_ddos_management(self):
        ntp = f"ip/device/{CERMAK}/ntp-01/if/lan1"
        self.check("provider-management-mode", lambda: self.o[ntp]["attrs"].update(status="reserved"))
        self.check("provider-management-mode",
                   lambda: self.cable(f"device/{CERMAK}/ddos-01/if/Management")["attrs"].update(status="connected"))

    def test_successor_pair_marks_port_exhaustion(self):
        for side in "ab":
            self.assertIn(self.o[f"device/{CERMAK}/pe-{side}2"]["attrs"]["status"], ("planned", "staged"))
        self.check("provider-device-inventory", lambda: self.drop(f"device/{CERMAK}/pe-b2"))
        # Free an SFP+ position: the MX304 trigger no longer holds.
        self.check("provider-successor", lambda: self.drop(self.cable(f"device/{CERMAK}/pe-a/if/xe-0/1/5")["key"]))
        self.check("provider-scope-text", lambda: self.o[f"device/{CERMAK}/pe-a"]["attrs"].update(
            description="Provider edge at Chicago Cermak Exchange; MX204 SFP+ ports exhausted at 8, next platform MX304"))

    def test_withdrawn_exchange_port_stays_cabled_and_shut(self):
        port = f"device/{OAK_CREEK}/pe-a/if/xe-0/1/5"
        self.assertIs(self.o[port]["attrs"]["enabled"], False)
        self.assertEqual(self.cable(port)["attrs"]["status"], "decommissioning")
        self.check("provider-port-mode", lambda: self.o[port]["attrs"].update(enabled=True))
        self.check("provider-port-use", lambda: self.drop(self.cable(port)["key"]))

    # --- Exchanges, former customers, the second NID generation ---

    def test_exchanges_are_providers_with_one_documentation_as(self):
        self.check("provider-exchange", lambda: self.o["asn/ix/chicago"]["attrs"].update(asn=64500))
        self.check("provider-exchange", lambda: self.o["provider-network/ix/chicago"]["attrs"].update(
            description="IPv6-only exchange peering LAN."))
        self.check("provider-exchange", lambda: self.o["circuit/ix/milwaukee/former"]["attrs"].pop("termination_date"))
        self.check("provider-exchange", lambda: self.o["asn/ix/detroit"]["refs"].update(tenant="tenant"))
        self.check("provider-routing-registry", lambda: self.o["asn-range/exchanges"]["attrs"].update(end=65600))
        self.check("provider-circuit-path", lambda: self.o["circuit/ix/chicago"]["attrs"].update(status="planned"))

    def test_former_customers_keep_only_tenant_account_and_a_closed_circuit(self):
        key = "brew-city-labs"
        circuit = f"circuit/former/{key}"
        self.assertEqual(self.o[circuit]["attrs"]["status"], "decommissioned")
        self.check("provider-former-customer", lambda: self.o[circuit]["attrs"].update(status="active"))

        def terminate():
            self.plan["objects"].append(dict(key=f"{circuit}/A", kind="circuit_termination", meta={},
                                             attrs={"term_side": "A"}, refs={"circuit": circuit, "termination": f"site/{CERMAK}"}))
        self.check("provider-former-customer", terminate)
        self.check("provider-former-customer", lambda: self.o[f"device/{CERMAK}/mgmt-01"]["refs"].update(
            tenant=f"tenant/cust-{key}"))
        self.check("provider-former-customer", lambda: self.o[circuit]["attrs"].update(termination_date="2027-01-01"))
        self.check("provider-circuit-inventory", lambda: self.drop(circuit))

    def test_first_nid_generation_stays_at_early_single_nid_premises(self):
        nid = f"device/{LEGACY_DIA}/nid-01"
        self.assertEqual(self.o[nid]["refs"]["device_type"], "hardware/nid-legacy")
        self.assertLess(self.o[f"circuit/customer/{LEGACY_DIA}"]["attrs"]["install_date"], "2016-01-01")
        self.check("provider-nid", lambda: self.o[nid]["refs"].update(device_type="hardware/nid"))
        self.check("provider-dia-address", lambda: self.o[f"{nid}/if/A_Client"]["attrs"].update(label="Customer-owned firewall"))

    # --- Timeline, workloads and the lab ---

    def test_onboarding_follows_its_frozen_ledger_and_spreads_over_the_years(self):
        def resign():
            scope = self.plan["reservations"]["provider-timeline/onboard/jetstream-cargo"]
            scope["day"] = date(2026, 1, 1).toordinal()
        self.check("provider-timeline", resign)
        self.check("provider-allocation", lambda: self.plan["reservations"].pop("provider-timeline/launch/chicago-loop"))
        with patch.object(vp, "ONBOARDING_YEAR_SHARE", 0.05):
            self.assertIn("provider-timeline", self.codes())

    def test_the_detection_controller_exists_only_beside_a_tms(self):
        def no_tms():
            for key in [k for k in self.o if k == f"device/{CERMAK}/ddos-01" or k.startswith(f"device/{CERMAK}/ddos-01/")]:
                self.drop(key)
        self.check("dc-workload-inventory", no_tms)

    def test_the_lab_mirrors_only_in_service_routers(self):
        lab = f"device/dc-01/network-lab/lab-chicago-cermak-pe-a"
        relic = self.o[f"device/{CERMAK}/legacy-pe-a"]["attrs"]["name"]
        self.check("lab-mirror", lambda: self.o[lab]["attrs"].update(name=f"lab-{relic}"))

    # --- Power and optics of the new hardware ---

    def test_new_models_carry_their_planning_allowances(self):
        self.check("dc-power-allocation", lambda: self.o[f"device/{CERMAK}/agg-a/power/PSU 0"]["attrs"].update(allocated_draw=150))
        self.check("dc-power-failover", lambda: self.o[f"device/{CERMAK}/ntp-01/power/PWR1"]["attrs"].update(maximum_draw=5))

    def test_optics_in_a_chassis_not_in_service_share_its_state(self):
        end = f"optics-module/device/{CERMAK}/pe-a2/if/et-0/0/1"
        self.assertIn(self.o[end]["attrs"]["status"], ("planned", "staged"))
        self.check("optics-module", lambda: self.o[end]["attrs"].update(status="active"))
        # A pre-cabled DDoS offramp stays planned until the cut-over.
        self.check("optics-port", lambda: self.cable(f"device/{CERMAK}/ddos-01/if/1")["attrs"].update(status="connected"))
        # The QSFP-DD cage is a reviewed host for the QSFP28 AOC at 100G only.
        self.check("optics-compatibility", lambda: self.o[f"device/{CERMAK}/pe-a2/if/et-0/0/1"]["attrs"].update(speed=400000000))
        # A withdrawn port's optic stays installed behind a decommissioning cable.
        self.check("optics-port", lambda: self.cable(f"device/{OAK_CREEK}/pe-a/if/xe-0/1/5")["attrs"].update(status="connected"))


if __name__ == "__main__":
    unittest.main()
