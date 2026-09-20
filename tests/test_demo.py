"""The demo composer: flags, templates, determinism, feature packs and go-live.

Nothing here contacts a target. The one `--target` path is exercised against
mocked loader entry points that assert the exact argv the Justfile recipes
build, so a regression in the orchestration is caught without a live NetBox.
"""

from contextlib import contextmanager, redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from estates import demo
from estates.__main__ import main
from estates.model import DesignError


ROOT = Path(__file__).resolve().parents[1]
# Cheapest complete estate in the set: nine sites, every family, ~3.5k objects.
CHEAP = "provider-backbone"


@contextmanager
def working_directory(path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield Path(path)
    finally:
        os.chdir(previous)


class ComposerTests(unittest.TestCase):
    def call(self, *args):
        """Run the CLI exactly as `python3 -m estates` would, capturing output."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main([str(arg) for arg in args])
        return status, out.getvalue(), err.getvalue()

    def compose(self, out, *args, code=0, name="Acme Bank", profile=CHEAP):
        status, text, _ = self.call("--json", "demo", "--profile", profile, "--name", name,
                                    "--out", out, *args)
        self.assertEqual(status, code, text)
        return json.loads(text.strip().splitlines()[-1])

    def failure(self, *args, profile=CHEAP):
        status, text, _ = self.call("--json", "demo", "--profile", profile, *args)
        self.assertEqual(status, 2, text)
        payload = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(payload.get("error"), "DesignError", payload)
        return payload["message"]

    def test_growth_composes_with_previous_and_keeps_identities(self):
        # Cold-start run #28: the second call deserves a fresh cheat sheet.
        base = ('profile = "utility"\nnamespace = "growco"\nname = "Growco Power"\n'
                'control_centers = 1\n\n[[substations]]\nkey = "alpha"\n'
                'kind = "distribution"\nbays = 4\n')
        grown = base + '\n[[substations]]\nkey = "briar"\nkind = "distribution"\nbays = 4\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "v1.toml").write_text(base)
            (root / "v2.toml").write_text(grown)
            s, text, _ = self.call("--json", "demo", "--recipe", root / "v1.toml",
                                   "--out", root / "one")
            self.assertEqual(s, 0, text)
            s, text, _ = self.call("--json", "demo", "--recipe", root / "v2.toml",
                                   "--previous", root / "one/estate/plan.json",
                                   "--out", root / "two")
            self.assertEqual(s, 0, text)
            v1 = {o["key"]: o for o in json.loads((root / "one/estate/plan.json").read_text())["objects"]}
            v2 = {o["key"]: o for o in json.loads((root / "two/estate/plan.json").read_text())["objects"]}
            self.assertLessEqual(set(v1), set(v2))
            self.assertIn("sub-briar", {k.removeprefix("site/") for k in v2 if k.startswith("site/")})
            self.assertTrue((root / "two/DEMO.md").exists())
            grown_sheet = (root / "two/DEMO.md").read_text()
            # Run #30: the recompose one-liner must reproduce THIS estate.
            self.assertIn("--recipe", grown_sheet)
            self.assertIn("--previous", grown_sheet)
            self.assertNotIn("--profile utility --name", grown_sheet)
            self.assertIn("New in this growth", grown_sheet)
            self.assertIn("growco-v2", grown_sheet)  # distinct suggested branch
            # --previous without --recipe is a named refusal.
            s, text, _ = self.call("--json", "demo", "--previous", root / "one/estate/plan.json",
                                   "--out", root / "three")
            self.assertEqual(s, 2, text)
            self.assertIn("--recipe", text)

    def test_the_maintenance_pack_is_provider_only_and_builds_the_span_story(self):
        # Cold-start run #26: the flagship provider scenario is reachable from
        # the flagship command, with the same snapshot boundary stated.
        message = self.failure("--features", "maintenance", "--out", "unused",
                               profile="hospital-clinics")
        self.assertIn("provider-backbone", message)
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            self.compose(out, "--features", "maintenance", profile="provider-backbone",
                         name="Great Plains Fiber")
            self.assertTrue((out / "maintenance" / "scenario.json").exists())
            self.assertTrue((out / "maintenance" / "report.md").exists())
            sheet = (out / "DEMO.md").read_text()
            self.assertIn("Maintenance — a planned span window", sheet)
            self.assertIn("refuses to load `changed/`", sheet)
            self.assertIn("just demo-recipe", sheet)
            self.assertIn("recipe-v2.toml maintenance", sheet)
            self.assertNotIn("(**None**)", sheet)

    def test_a_scenario_snapshot_refusal_names_the_policy(self):
        # Run #26's second S2: the guardrail must name itself, not read as a
        # corrupt artifact.
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            self.compose(out, "--features", "scenario")
            from estates.turbobulk import LoadError, _artifact
            with self.assertRaises(LoadError) as caught:
                _artifact(out / "scenario" / "changed")
            message = str(caught.exception)
            self.assertIn("deliberate scenario snapshot", message)
            self.assertIn("expected-defect verified", message)
            self.assertIn("baseline/", message)
            self.assertIn("docs/scenarios.md", message)

    def test_wireless_claims_are_graph_facts(self):
        # Cold-start run #24: no guest promise without a guest SSID, no 2.4 GHz
        # promise when nothing rides wlan1, and a wireless step when APs exist.
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            self.compose(out, "--vendor", "aruba", profile="hospital-clinics",
                         name="Riverbend Health")
            sheet = (out / "DEMO.md").read_text()
            self.assertIn("Guest wireless is not requested in this recipe", sheet)
            self.assertNotIn("Guest wireless is open access intent", sheet)
            self.assertIn("nothing", sheet)
            self.assertIn("rides `wlan1` in this profile", sheet)
            self.assertIn("Show the wireless story.", sheet)
            self.assertIn("/wireless/wireless-lans/", sheet)
            # The closing block scopes its retire to whichever branch is live.
            self.assertIn("offline compose: identities survive", sheet)
            self.assertIn("nothing is retired", sheet)
            self.assertIn("fresh cheat", sheet)
            # Feature-aware growth block: scenario regen, no drift line here.
            self.assertNotIn("just drift ", sheet)

    def test_the_merger_step_is_a_graph_fact_not_a_template_assumption(self):
        # Cold-start run #23: a custom bank without design_mix has no merger,
        # so the sheet must not send the SE to a site with nothing to show.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stock = root / "stock"
            self.compose(stock, profile="regional-bank", name="Stock Bank")
            stock_sheet = (stock / "DEMO.md").read_text()
            self.assertIn("Show the merger.", stock_sheet)
            self.assertNotIn("br-s0002", stock_sheet)  # display name, not the internal id
            recipe = root / "plain.toml"
            recipe.write_text('profile = "regional-bank"\nnamespace = "plainbank"\n'
                              'name = "Plain Bank"\nheadquarters = 0\n\n[branches]\nsmall = 1\n')
            out = root / "plain"
            status, text, _ = self.call("--json", "demo", "--recipe", recipe, "--out", out)
            self.assertEqual(status, 0, text)
            sheet = (out / "DEMO.md").read_text()
            self.assertNotIn("Show the merger.", sheet)
            self.assertNotIn("Birch", sheet)

    def test_a_customer_shaped_recipe_composes_with_its_own_identity(self):
        # Cold-start run #22: taking the customer's real shape must not cost
        # the cheat sheet.
        recipe = ('profile = "utility"\nnamespace = "laketest"\nname = "Laketest Electric"\n'
                  'control_centers = 2\n\n[[substations]]\nkey = "harbor-point"\n'
                  'kind = "transmission"\nbays = 6\n\n[[substations]]\nkey = "millbrook"\n'
                  'kind = "distribution"\nbays = 4\n\n'
                  '[site_names."sub-harbor-point"]\nname = "Harbor Point Substation"\n')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "customer.toml"
            path.write_text(recipe)
            out = Path(temporary) / "demo"
            status, text, _ = self.call("--json", "demo", "--recipe", path, "--out", out)
            self.assertEqual(status, 0, text)
            result = json.loads(text.strip().splitlines()[-1])
            self.assertEqual(result["name"], "Laketest Electric")
            self.assertEqual(result["sites"], 4)
            # The recipe is copied verbatim and the sheet is customer-shaped.
            self.assertEqual((out / "recipe.toml").read_text(), recipe)
            sheet = (out / "DEMO.md").read_text()
            self.assertIn("Harbor Point Substation", sheet)
            self.assertIn("custom shape from", sheet)
            self.assertIn("station (OT) zone", sheet)  # the profile hook survives --recipe
            self.assertNotIn("Fairhaven", sheet)  # no stock-template leakage
            # Identity flags conflict with --recipe and say why.
            status, text, _ = self.call("--json", "demo", "--recipe", path,
                                        "--name", "Someone Else")
            self.assertEqual(status, 2, text)
            self.assertIn("edit the recipe instead", text)

    # -- flags ------------------------------------------------------------

    def test_unknown_profile_vendor_and_feature_name_the_real_choices(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            for flag, value, expected in (("--profile", "bank", "regional-bank"),
                                          ("--vendor", "cisco", "juniper"),
                                          ("--features", "assurance,telemetry", "scenario")):
                message = self.failure(flag, value, "--out", str(out))
                self.assertIn(expected, message)
                self.assertIn(value.split(",")[-1], message)
            self.assertFalse(out.exists(), "a rejected flag must not create the demo directory")

    def test_assurance_refuses_the_one_profile_with_no_campus_access(self):
        message = self.failure("--features", "assurance", "--out", "unused",
                               profile="enterprise-data-center")
        self.assertIn("campus access", message)
        self.assertIn("enterprise-data-center models fabric only", message)
        for profile in demo.PROFILES:
            self.assertEqual(profile in message.split("choose one of: ")[1],
                             profile not in demo.NO_DRIFT, profile)

    def test_target_and_branch_are_actionable_and_inseparable(self):
        for args in (("--target", "https://netbox.example"), ("--branch", "demo-acme")):
            self.assertIn("go together", self.failure(*args, "--out", "unused"))
        for bad in ("https://netbox.example/api/", "https://netbox.example/?x=1",
                    "https://netbox.example/#f"):
            message = self.failure("--target", bad, "--branch", "b", "--out", "unused")
            self.assertIn("root URL", message)

    def test_namespace_and_seed_validation_points_at_the_flag_that_fixes_it(self):
        self.assertIn("DNS label", self.failure("--namespace", "Acme_Bank", "--out", "unused"))
        self.assertIn("Pass --namespace explicitly",
                      self.failure("--name", "1234", "--out", "unused"))
        self.assertIn("2**63", self.failure("--seed", "-1", "--out", "unused"))
        self.assertIn("1–80 characters", self.failure("--name", "x" * 81, "--out", "unused"))

    def test_namespace_derivation_is_a_dns_label_and_the_seed_follows_it(self):
        self.assertEqual(demo.namespace_for("Acme Bank & Trust"), "acme-bank-trust")
        self.assertEqual(demo.namespace_for("3M Corporation"), "m-corporation")
        self.assertEqual(demo.namespace_for("Consolidated Industrial Holdings"),
                         "consolidated-industr")
        self.assertRegex(demo.namespace_for("Consolidated Industrial Holdings"),
                         r"^[a-z][a-z0-9-]{0,18}[a-z0-9]$")
        for bad in ("", "1", "-", "12 34"):
            with self.assertRaises(DesignError):
                demo.namespace_for(bad)
        seed = demo.seed_for("acme-bank-trust")
        self.assertEqual(seed, demo.seed_for("acme-bank-trust"))
        self.assertNotEqual(seed, demo.seed_for("acme-bank"))
        self.assertTrue(0 <= seed < 2 ** 63)

    def test_site_overrides_accept_toml_json_and_reject_junk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bare = root / "sites.toml"
            bare.write_text('["pop-chicago-west"]\nname = "Chicago West"\nfacility = "CHI1"\n')
            wrapped = root / "wrapped.json"
            wrapped.write_text(json.dumps({"site_names": {"pop-chicago-west": {"name": "CW"}}}))
            for path in (bare, wrapped):
                self.assertIn("pop-chicago-west", demo._site_overrides(path))
            for content, suffix, expected in (
                    ("not = [", ".toml", "not readable as TOML"),
                    ("{", ".json", "not readable as JSON"),
                    ("[]", ".json", "non-empty table"),
                    (json.dumps({"s": {"colour": "red"}}), ".json", "only 'name' and/or 'facility'"),
                    (json.dumps({"s": {"name": "  "}}), ".json", "must be non-empty text")):
                path = root / f"bad{suffix}"
                path.write_text(content)
                with self.assertRaises(DesignError) as caught:
                    demo._site_overrides(path)
                self.assertIn(expected, str(caught.exception))

    def test_an_existing_output_is_preserved_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            self.compose(out)
            marker = (out / "DEMO.md").read_bytes()
            self.assertIn("already exists", self.failure("--name", "Acme Bank", "--out", str(out)))
            self.assertEqual((out / "DEMO.md").read_bytes(), marker)

    # -- the recipe -------------------------------------------------------

    def test_the_recipe_is_an_ordinary_toml_recipe_that_rebuilds_the_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            self.compose(out, "--vendor", "juniper")
            recipe = tomllib.loads((out / "recipe.toml").read_text())
            self.assertEqual(recipe["profile"], CHEAP)
            self.assertEqual(recipe["namespace"], "acme-bank")
            self.assertEqual(recipe["seed"], demo.seed_for("acme-bank"))
            self.assertEqual(recipe["hardware"], {"access": "juniper", "leaf": "juniper"})
            # The generated plan is exactly what this recipe produces: the
            # composer added no identity the recipe does not carry. (Nested
            # demand tables are normalised by the resolver, so compare scalars.)
            plan = json.loads((out / "estate" / "plan.json").read_text())
            for key, value in recipe.items():
                if isinstance(value, (str, int, float)):
                    self.assertEqual(plan["recipe"][key], value, key)
            self.assertLessEqual(recipe["hardware"].items(), plan["recipe"]["hardware"].items())
            rebuilt = Path(temporary) / "rebuilt"
            status, text, _ = self.call("--json", "generate", out / "recipe.toml", "--out", rebuilt)
            self.assertEqual(status, 0, text)
            self.assertEqual((rebuilt / "plan.json").read_bytes(),
                             (out / "estate" / "plan.json").read_bytes())

    def test_every_vendor_line_places_hardware_after_the_top_level_scalars(self):
        for vendor, (families, _) in demo.VENDORS.items():
            spec = demo.resolve(profile="regional-bank", vendor=vendor, name="Acme Bank",
                                namespace="", seed=None, features="", sites=None, out="x",
                                target="", branch="")
            recipe = tomllib.loads(demo.recipe_text(spec))
            self.assertEqual(recipe.get("hardware", {}), families, vendor)
            # TOML scoping: a scalar after a table header would land inside it.
            self.assertEqual(recipe["headquarters_staff"], 48, vendor)
            self.assertEqual(recipe["namespace"], "acme-bank", vendor)

    def test_site_overrides_reach_the_recipe_and_the_generated_site_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sites = root / "sites.toml"
            sites.write_text('["pop-chicago-west"]\nname = "Acme Chicago West"\n')
            out = root / "demo"
            self.compose(out, "--sites", sites)
            recipe = tomllib.loads((out / "recipe.toml").read_text())
            self.assertEqual(recipe["site_names"], {"pop-chicago-west": {"name": "Acme Chicago West"}})
            plan = json.loads((out / "estate" / "plan.json").read_text())
            site = next(obj for obj in plan["objects"] if obj["key"] == "site/pop-chicago-west")
            self.assertEqual(site["attrs"]["name"], "Acme Chicago West")

    # -- determinism ------------------------------------------------------

    def test_recomposing_the_same_inputs_reproduces_every_byte_but_the_timings(self):
        produced = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as temporary, working_directory(temporary):
                self.compose("demo", "--features", "assurance,automation,scenario")
                produced.append(((Path("demo") / "recipe.toml").read_bytes(),
                                 (Path("demo") / "DEMO.md").read_text(),
                                 json.loads((Path("demo") / "compose.json").read_text())))
        first, second = produced
        self.assertEqual(first[0], second[0], "the recipe must be byte-identical")
        bodies = []
        for _, sheet, _ in produced:
            body, marker, timings = sheet.partition("<!-- timings:start -->")
            self.assertTrue(marker, "timings must be fenced off so the rest is comparable")
            self.assertIn("<!-- timings:end -->", timings)
            self.assertIn("Seconds", timings)
            bodies.append(body)
        self.assertEqual(bodies[0], bodies[1], "DEMO.md must be identical outside the timings")
        self.assertNotIn("Composed 20", bodies[0], "no timestamp may leak into the stable body")
        # compose.json holds the varying evidence, and the same steps in order.
        self.assertEqual([step["command"] for step in first[2]["steps"]],
                         [step["command"] for step in second[2]["steps"]])
        self.assertEqual(first[2]["counts"], second[2]["counts"])

    def test_every_profile_composes_offline_and_fits_the_turbobulk_contract(self):
        for profile in demo.PROFILES:
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                out = Path(temporary) / "demo"
                result = self.compose(out, profile=profile, name=f"Acme {profile}")
                self.assertGreater(result["objects"], 2000, profile)
                self.assertFalse(result["loaded"])
                self.assertFalse(result["applied_to_target"])
                receipt = json.loads((out / "compose.json").read_text())
                self.assertIn("Loadable", receipt["turbobulk_verdict"], profile)
                self.assertIsNone(receipt["live"])
                for name in ("recipe.toml", "DEMO.md", "compose.json", "estate/plan.json",
                             "estate/report.md"):
                    self.assertTrue((out / name).is_file(), f"{profile}: {name}")

    # -- feature packs ----------------------------------------------------

    def test_assurance_builds_a_drift_twin_that_independently_re_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            self.compose(out, "--features", "assurance")
            self.assertTrue((out / "drift" / "drift.md").is_file())
            status, text, _ = self.call("--json", "drift-check", out / "drift")
            self.assertEqual(status, 0, text)
            self.assertTrue(json.loads(text)["observed_payload_matches_manifest"])
            sheet = (out / "DEMO.md").read_text()
            self.assertIn("drift/drift.md", sheet)
            self.assertIn("No Assurance-equipped target has seen this payload", sheet)
            # The walkthrough is bound to the manifest; linking is the contract,
            # restating it would drift from `just drift-check`.
            walkthrough = (out / "drift" / "drift.md").read_text()
            body = walkthrough.split("## The talk track", 1)[1][:400]
            self.assertNotIn(body, sheet)

    def test_scenario_builds_snapshots_that_independently_scenario_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            self.compose(out, "--features", "scenario")
            status, text, _ = self.call("--json", "scenario-check", out / "scenario" / "scenario.json")
            self.assertEqual(status, 0, text)
            self.assertEqual(json.loads(text)["checks"], "expected-defect verified")
            sheet = (out / "DEMO.md").read_text()
            self.assertIn("loss of power diversity", sheet.lower())
            self.assertIn("separate fresh targets", sheet)

    def test_automation_is_a_talk_track_over_records_that_are_always_present(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plain, flagged = root / "plain", root / "flagged"
            self.compose(plain)
            self.compose(flagged, "--features", "automation")
            self.assertEqual(sorted(path.name for path in plain.iterdir()),
                             sorted(path.name for path in flagged.iterdir()),
                             "automation must add narration, never artifacts")
            # The records the section points at exist either way (the pack is
            # unconditional enrichment since 0.11.0).
            for directory in (plain, flagged):
                plan = json.loads((directory / "estate" / "plan.json").read_text())
                self.assertTrue([obj for obj in plan["objects"] if obj["kind"] == "service"])
                kinds = {obj["kind"] for obj in plan["objects"]}
                self.assertLessEqual({"config_context", "export_template", "webhook", "event_rule"}, kinds)
            sheet, bare = (flagged / "DEMO.md").read_text(), (plain / "DEMO.md").read_text()
            self.assertIn("Automation — the data the tooling consumes", sheet)
            self.assertNotIn("Automation — the data the tooling consumes", bare)
            self.assertIn("/extras/config-contexts/", sheet)
            self.assertIn("/extras/export-templates/", sheet)
            self.assertIn("Global service baseline", sheet)
            self.assertIn("inert by design", sheet)
            self.assertIn("Nothing here executes", sheet)

    def test_the_zone_profiles_quote_their_own_contexts_and_distribution_pair(self):
        for profile, contexts in (("manufacturing", ("-process", "-supervisory")),
                                  ("utility", ("-protection", "-telemetry", "-station"))):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                out = Path(temporary) / "demo"
                self.compose(out, profile=profile, name=f"Zone {profile}")
                sheet = (out / "DEMO.md").read_text()
                namespace = f"zone-{profile}"[:20]
                self.assertIn("## The zone boundary, on screen", sheet)
                for suffix in contexts + ("-conduit",):
                    self.assertIn(f"`{namespace}{suffix}`", sheet)
                self.assertIn("modeled, never enforced", sheet)
                # The quoted OT pair must belong to the site the walkthrough opened.
                plan = json.loads((out / "estate" / "plan.json").read_text())
                site = next(obj for obj in plan["objects"] if obj["kind"] == "site"
                            and not obj["key"].startswith("site/dc-"))
                pair = [obj["attrs"]["name"] for obj in plan["objects"]
                        if obj["kind"] == "device" and "-ot-ds-" in obj["attrs"]["name"]
                        and obj["refs"]["site"] == site["key"]]
                self.assertEqual(len(pair), 2, profile)
                for name in pair:
                    self.assertIn(f"`{name}`", sheet)

    def test_the_composed_estate_carries_the_automation_pack_it_narrates(self):
        # Inverted from the pre-0.11 guard: the pack is unconditional now, and
        # the section must never claim emptiness again.
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            self.compose(out, "--features", "automation")
            plan = json.loads((out / "estate" / "plan.json").read_text())
            kinds = {obj["kind"] for obj in plan["objects"]}
            self.assertLessEqual({"config_context", "export_template", "webhook", "event_rule"}, kinds)
            sheet = (out / "DEMO.md").read_text()
            self.assertNotIn("Empty in this estate", sheet)
            self.assertNotIn("emits no config context", sheet)

    # -- the cheat sheet --------------------------------------------------

    def test_the_cheat_sheet_opens_with_counts_taken_from_the_real_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            result = self.compose(out)
            plan = json.loads((out / "estate" / "plan.json").read_text())
            actual = {kind: sum(1 for obj in plan["objects"] if obj["kind"] == kind)
                      for kind in ("site", "device", "cable")}
            self.assertEqual([result["sites"], result["devices"], result["cables"]],
                             list(actual.values()))
            sheet = (out / "DEMO.md").read_text()
            self.assertIn(f"**{actual['site']} sites, {actual['device']} devices, "
                          f"{actual['cable']} cables — all connected.**", sheet)
            # Every quoted identity is a real object, not a placeholder.
            names = {obj["attrs"].get("name") for obj in plan["objects"]}
            site = next(obj for obj in plan["objects"]
                        if obj["kind"] == "site" and not obj["key"].startswith(("site/dc-", "site/noc-")))
            self.assertIn(site["attrs"]["name"], sheet)
            self.assertIn(site["attrs"]["slug"], sheet)
            self.assertTrue(any(name and name in sheet for name in names))

    def test_the_walkthrough_names_that_device_s_own_desks_not_the_shared_one(self):
        # Several profiles escalate to a per-account or per-lineage desk. Naming
        # the shared operations desk instead would send the SE to the wrong row —
        # and on the MSP it would invert the profile's whole point.
        for profile in ("msp", "provider-backbone", "regional-bank", "utility"):
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as temporary:
                out = Path(temporary) / "demo"
                self.compose(out, profile=profile, name=f"Desk {profile}")
                plan = json.loads((out / "estate" / "plan.json").read_text())
                index = {obj["key"]: obj for obj in plan["objects"]}
                sheet = (out / "DEMO.md").read_text()
                device = next(obj for obj in plan["objects"] if obj["kind"] == "device"
                              and f"**{obj['attrs']['name']}** → *Interfaces*" in sheet)
                desks = [index[obj["refs"]["contact"]]["attrs"]["name"]
                         for obj in plan["objects"] if obj["kind"] == "contact_assignment"
                         and obj["refs"].get("object") == device["key"]]
                self.assertTrue(desks, f"{profile}: the chosen device has no contact assignment")
                for desk in desks:
                    self.assertIn(f"**{desk}**", sheet)
                # A switch, not whichever device happens to sort first.
                self.assertIn(index[device["refs"]["role"]]["key"],
                              ("role/access", "role/leaf", "role/distribution"), profile)

    def test_without_a_target_it_prints_the_exact_go_live_commands_and_claims_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            result = self.compose(out)
            self.assertIsNone(result["ui_url"])
            sheet = (out / "DEMO.md").read_text()
            estate = f"{out}/estate"
            for command in (f"just branch https://netbox.example acme-bank",
                            f"just load {estate} https://netbox.example acme-bank",
                            f"just verify-target {estate} https://netbox.example acme-bank",
                            "just retire https://netbox.example acme-bank acme-bank"):
                self.assertIn(command, sheet)
            self.assertIn("Nothing has been written to a NetBox target", sheet)
            self.assertIn("no NetBox target has seen this estate", sheet)
            self.assertIn("TURBOBULK_WRITES=1", sheet)
            # The API-proof placeholders are fine; a real schema id is not.
            import re as _re
            for match in _re.findall(r"_branch=([^&\"\s)]+)", sheet):
                self.assertEqual(match, "<schema-id>", "no branch can be active before a load")

    def test_compose_json_records_the_entry_points_that_actually_ran(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            self.compose(out, "--features", "scenario")
            receipt = json.loads((out / "compose.json").read_text())
            commands = [step["command"] for step in receipt["steps"]]
            self.assertTrue(commands[0].startswith("python3 -m estates --json generate "))
            self.assertIn("python3 -m estates.load", " ".join(commands))
            self.assertIn("--load-check", " ".join(commands))
            self.assertTrue(any("scenario-check" in command for command in commands))
            self.assertEqual(receipt["spec"]["profile"], CHEAP)
            self.assertEqual(receipt["artifact"], "demo-compose")
            self.assertTrue(receipt["compose_command"].startswith("python3 -m estates demo "))

    # -- going live, without a target -------------------------------------

    def test_a_target_run_drives_branch_load_and_verify_with_the_justfile_argv(self):
        calls = []

        def branch_main(argv):
            calls.append(("branch", list(argv)))
            print(json.dumps({"id": 7, "name": argv[1], "schema_id": "br_7", "status": "ready"}))
            return 0

        def load_main(argv):
            calls.append(("load", list(argv)))
            if "--verify-only" in argv:
                print(json.dumps({"success": True, "result": "verified", "mismatches": 0}))
            else:
                print(json.dumps({"success": True, "result": "loaded", "transport": "turbobulk",
                                  "objects_matched": 3526, "mismatches": 0,
                                  "ui_url": "https://netbox.example/dcim/sites/?_branch=br_7"}))
            return 0

        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            with patch("estates.branch.main", branch_main), patch("estates.load.main", load_main), \
                 patch.dict(os.environ, {"NETBOX_TOKEN": "not-a-real-token"}):
                result = self.compose(out, "--target", "https://netbox.example/",
                                      "--branch", "demo-acme")
            self.assertTrue(result["loaded"])
            self.assertTrue(result["applied_to_target"])
            self.assertEqual(result["ui_url"], "https://netbox.example/dcim/sites/?_branch=br_7")
            estate = str(out / "estate")
            self.assertEqual([argv for entry, argv in calls if entry == "branch"],
                             [["https://netbox.example/", "demo-acme"]])
            self.assertEqual([argv for entry, argv in calls if entry == "load"], [
                [estate, "--load-check"],
                [estate, "https://netbox.example/", "--branch", "demo-acme",
                 "--delivery-policy", "reviewable"],
                [estate, "https://netbox.example/", "--branch", "demo-acme", "--verify-only"]])
            sheet = (out / "DEMO.md").read_text()
            self.assertIn("https://netbox.example/dcim/sites/?_branch=br_7", sheet)
            self.assertIn("&_branch=br_7", sheet, "every UI link must stay inside the branch")
            self.assertIn("verified 3526 objects with 0 mismatches", sheet)
            self.assertIn("just retire https://netbox.example demo-acme acme-bank", sheet)
            self.assertNotIn("Nothing has been written to a NetBox target", sheet)
            self.assertEqual(json.loads((out / "compose.json").read_text())["live"]["schema"], "br_7")

    def branch_ok(self, argv):
        print(json.dumps({"id": 1, "name": argv[1], "schema_id": "br_1", "status": "ready"}))
        return 0

    def test_a_failing_load_stops_the_compose_and_names_the_command(self):
        def load_main(argv):
            if "--load-check" in argv:
                print(json.dumps({"verdict": "Loadable via TurboBulk+REST."}))
                return 0
            # Where the real loader prints it: stderr, not stdout.
            print("Load failed: no faithful loader is available", file=sys.stderr)
            return 2

        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            with patch("estates.branch.main", self.branch_ok), \
                 patch("estates.load.main", load_main), \
                 patch.dict(os.environ, {"NETBOX_TOKEN": "not-a-real-token"}):
                message = self.failure("--name", "Acme Bank", "--out", str(out),
                                       "--target", "https://netbox.example", "--branch", "b")
            self.assertIn("load and strictly read back failed", message)
            self.assertIn("no faithful loader is available", message)
            self.assertIn("python3 -m estates.load", message)
            self.assertFalse((out / "DEMO.md").exists(),
                             "a failed compose writes no cheat sheet that could be read on a call")

    def test_a_load_without_readback_evidence_refuses_to_claim_verification(self):
        """Absent evidence must never render as "verified None objects"."""
        loaded = {"success": True, "result": "loaded",
                  "ui_url": "https://netbox.example/dcim/sites/?_branch=br_1",
                  "objects_matched": 10, "mismatches": 0}
        cases = (
            # A branch with no schema id: every deep link would address main.
            ("no schema id", {"id": 1, "name": "b"}, loaded),
            # A load with no verification block in its summary.
            ("no strict-readback evidence", {"id": 1, "name": "b", "schema_id": "br_1"},
             {key: value for key, value in loaded.items() if key != "objects_matched"}),
        )
        for expected, branch, body in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temporary:
                out = Path(temporary) / "demo"

                def load_main(argv, body=body):
                    print(json.dumps({"verdict": "Loadable."} if "--load-check" in argv else body))
                    return 0

                with patch("estates.branch.main",
                           lambda argv, row=branch: (print(json.dumps(row)), 0)[1]), \
                     patch("estates.load.main", load_main), \
                     patch.dict(os.environ, {"NETBOX_TOKEN": "not-a-real-token"}):
                    message = self.failure("--name", "Acme Bank", "--out", str(out),
                                           "--target", "https://netbox.example", "--branch", "b")
                self.assertIn(expected, message)
                self.assertFalse((out / "DEMO.md").exists())

    def test_going_live_needs_the_token_before_anything_is_generated(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            environment = {key: value for key, value in os.environ.items() if key != "NETBOX_TOKEN"}
            with patch.dict(os.environ, environment, clear=True):
                message = self.failure("--name", "Acme Bank", "--out", str(out),
                                       "--target", "https://netbox.example", "--branch", "b")
            self.assertIn("NETBOX_TOKEN is required to go live", message)
            self.assertIn("TURBOBULK_WRITES=1", message)
            self.assertFalse(out.exists(), "nothing may be generated before the flags resolve")

    def test_an_offline_gate_failure_stops_before_any_target_work(self):
        reached = []
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            with patch("estates.load.main", lambda argv: (reached.append(argv), 2)[1]):
                message = self.failure("--name", "Acme Bank", "--out", str(out))
        self.assertIn("check the TurboBulk contract offline failed", message)
        self.assertEqual(len(reached), 1, "nothing may run after a failed gate")

    def test_a_nested_argparse_exit_keeps_the_json_failure_contract(self):
        def load_main(argv):
            if "--load-check" in argv:
                print(json.dumps({"verdict": "Loadable."}))
                return 0
            print("python3 -m estates.load: error: target and token are required", file=sys.stderr)
            raise SystemExit(2)

        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "demo"
            with patch("estates.branch.main", self.branch_ok), \
                 patch("estates.load.main", load_main), \
                 patch.dict(os.environ, {"NETBOX_TOKEN": "not-a-real-token"}):
                message = self.failure("--name", "Acme Bank", "--out", str(out),
                                       "--target", "https://netbox.example", "--branch", "b")
            self.assertIn("target and token are required", message)

    def test_a_name_cannot_smuggle_extra_keys_into_the_generated_recipe(self):
        message = self.failure("--name", 'Acme\nnaming = "legacy"', "--out", "unused")
        self.assertIn("control characters", message)


if __name__ == "__main__":
    unittest.main()
