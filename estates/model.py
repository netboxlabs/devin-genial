"""Small canonical graph, input validation, and persistent address reservations."""

import hashlib
import ipaddress
import json
import re
import tomllib
from datetime import date
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parent.parent


class DesignError(ValueError):
    """A request cannot be satisfied by the supported design."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def hardware_catalog():
    return json.loads((ROOT / "catalog/hardware.json").read_text())


def recipe_from_file(path):
    with open(path, "rb") as handle:
        return resolve_recipe(tomllib.load(handle))


def resolve_recipe(raw):
    if not isinstance(raw, dict):
        raise DesignError("Recipe must be an object")
    if raw.get("profile") == "enterprise-data-center":
        from .enterprise import resolve
        return resolve(raw)
    if raw.get("profile") == "school-district":
        from .school import resolve
        return resolve(raw)
    if raw.get("profile") == "hospital-clinics":
        from .hospital import resolve
        return resolve(raw)
    if raw.get("profile") == "provider-backbone":
        from .provider import resolve
        return resolve(raw)
    return resolve_bank_recipe(raw)


def resolve_demo(value):
    if value not in ("baseline", "loss-of-power-diversity"):
        raise DesignError("demo supports baseline or loss-of-power-diversity; other objectives are not yet implemented")
    return value


def resolve_bank_recipe(raw):
    allowed = {"profile", "namespace", "name", "seed", "as_of", "address_pool", "ipv6_pool",
               "data_centers", "headquarters", "branches", "reserve_fraction", "max_objects", "patching",
               "design_mix", "site_designs", "acquired_sites", "headquarters_staff", "wan_tiers_mbps", "reservation_user", "demo"}
    if unknown := raw.keys() - allowed:
        raise DesignError(f"Unknown recipe fields: {', '.join(sorted(unknown))}")
    r = dict(profile="regional-bank", namespace="cedar", name="Cedar Regional Bank",
             seed=42, as_of="2026-09-01", address_pool="10.0.0.0/8",
             data_centers=2, headquarters=1, headquarters_staff=180, branches={"small": 4, "medium": 2, "large": 1},
             reserve_fraction=0.2, max_objects=500000, patching="direct",
             design_mix={"modern": 100, "inherited": 0, "refreshed": 0}, site_designs={}, acquired_sites=[],
             wan_tiers_mbps=[50, 100, 200, 500, 1000], reservation_user="")
    r.update(raw)
    if "demo" in r:
        r["demo"] = resolve_demo(r["demo"])
    if r["profile"] != "regional-bank":
        raise DesignError("Supported profiles are 'regional-bank', 'enterprise-data-center', 'school-district', 'hospital-clinics' and 'provider-backbone'. Add a reviewed profile for a new industry.")
    if r["patching"] not in ("direct", "panels"):
        raise DesignError("patching must be 'direct' (continuous channels) or 'panels' (legacy NetBox 4.4.10 mapping)")
    if not isinstance(r["namespace"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,18}[a-z0-9]", r["namespace"]):
        raise DesignError("namespace must be a 2–20 character DNS label: lowercase letters, digits or hyphens, starting with a letter and ending with a letter or digit")
    if not isinstance(r["name"], str) or not 1 <= len(r["name"]) <= 80:
        raise DesignError("name must be 1–80 characters")
    if not isinstance(r["reservation_user"], str) or (r["reservation_user"] and
            not re.fullmatch(r"[\w.@+-]{1,150}", r["reservation_user"])):
        raise DesignError("reservation_user must be empty or an existing NetBox username (1–150 letters, digits, @/./+/-/_)")
    if type(r["seed"]) is not int or not 0 <= r["seed"] < 2**63:
        raise DesignError("seed must be an integer in [0, 2^63)")
    try:
        r["as_of"] = date.fromisoformat(str(r["as_of"])).isoformat()
        pool = ipaddress.ip_network(r["address_pool"], strict=True)
    except (ValueError, TypeError) as exc:
        raise DesignError(f"Invalid as_of date or address_pool: {exc}") from exc
    private = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if pool.version != 4 or pool.prefixlen > 20 or not any(pool.subnet_of(p) for p in private):
        raise DesignError("address_pool must be an aligned RFC1918 IPv4 network /8 through /20; each site reserves /20")
    if "ipv6_pool" in r:
        from .ipv6 import resolve_pool
        r["ipv6_pool"] = resolve_pool(r["ipv6_pool"])
    for key, low, high in (("data_centers", 2, 2), ("headquarters", 0, 2), ("headquarters_staff", 24, 192), ("max_objects", 100, 2000000)):
        if type(r[key]) is not int or not low <= r[key] <= high:
            raise DesignError(f"{key} must be an integer between {low} and {high} for this profile")
    if not isinstance(r["branches"], dict) or r["branches"].keys() - {"small", "medium", "large"}:
        raise DesignError("branches must contain only small, medium and large counts")
    r["branches"] = {size: r["branches"].get(size, 0) for size in ("small", "medium", "large")}
    if any(type(n) is not int or not 0 <= n <= 2000 for n in r["branches"].values()):
        raise DesignError("Each branch count must be an integer between 0 and 2000")
    if type(r["reserve_fraction"]) not in (float, int) or not 0.1 <= r["reserve_fraction"] <= 0.4:
        raise DesignError("reserve_fraction must be between 0.1 and 0.4")
    tiers = r["wan_tiers_mbps"]
    if (not isinstance(tiers, list) or not tiers or
            any(type(rate) is not int or not 1 <= rate <= 1000 for rate in tiers) or
            tiers != sorted(set(tiers)) or tiers[-1] != 1000):
        raise DesignError("wan_tiers_mbps must be strictly increasing integer Mbps tiers from 1 to 1000, ending in 1000 for the supported DC handoff")
    r["wan_tiers_mbps"] = list(tiers)
    designs = ("modern", "inherited", "refreshed")
    if not isinstance(r["design_mix"], dict) or r["design_mix"].keys() - set(designs):
        raise DesignError("design_mix accepts only modern, inherited and refreshed integer weights")
    r["design_mix"] = {name: r["design_mix"].get(name, 0) for name in designs}
    if any(type(n) is not int or not 0 <= n <= 100 for n in r["design_mix"].values()) or not sum(r["design_mix"].values()):
        raise DesignError("design_mix weights must be 0–100 with at least one positive weight")
    if not isinstance(r["site_designs"], dict) or any(not isinstance(k, str) or v not in designs for k, v in r["site_designs"].items()):
        raise DesignError("site_designs maps branch IDs to modern, inherited or refreshed")
    if not isinstance(r["acquired_sites"], list) or any(not isinstance(k, str) for k in r["acquired_sites"]):
        raise DesignError("acquired_sites must be a list of branch IDs")
    if len(r["acquired_sites"]) != len(set(r["acquired_sites"])):
        raise DesignError("acquired_sites cannot contain duplicate branch IDs")
    r["acquired_sites"] = sorted(r["acquired_sites"])
    return r


class World:
    def __init__(self, recipe, previous=None):
        self.recipe = resolve_recipe(recipe)
        self.catalog = hardware_catalog()
        self.objects = {}
        self.contracts = []
        self.allocations = {}
        self.reservations = {}
        self.design_assignments = {}
        if previous is not None:
            if previous.get("schema_version") != 1 or previous.get("generator_version") != __version__:
                raise DesignError("Previous plan has a different schema/generator version; explicit rebaseline required")
            if previous.get("hardware_digest") != digest(self.catalog):
                raise DesignError("Hardware catalog changed since previous plan; explicit rebaseline required")
            for k in ("namespace", "name", "seed", "address_pool", "ipv6_pool", "profile", "as_of", "reserve_fraction", "patching", "design_mix", "headquarters_staff", "wan_tiers_mbps", "reservation_user"):
                if previous["recipe"].get(k) != self.recipe.get(k):
                    raise DesignError(f"Changing {k} requires a new estate; omit --previous for an explicit rebaseline")
            self.allocations = dict(previous["allocations"])
            self.design_assignments = dict(previous.get("design_assignments", {}))
            if any(v not in ("modern", "inherited", "refreshed") for v in self.design_assignments.values()):
                raise DesignError("Previous design assignment ledger contains an unknown design")
            self.reservations = {scope: dict(items) for scope, items in previous.get("reservations", {}).items()}
            slots = list(self.allocations.values())
            if any(type(v) is not int or v < 0 for v in slots) or len(slots) != len(set(slots)):
                raise DesignError("Previous allocation ledger contains invalid or duplicate site slots")
            for scope, items in self.reservations.items():
                slots = list(items.values())
                if any(type(v) is not int or v < 0 for v in slots) or len(slots) != len(set(slots)):
                    raise DesignError(f"Previous reservation ledger {scope} contains invalid or duplicate slots")
        self.pool = ipaddress.ip_network(self.recipe["address_pool"])
        self.site_prefixlen = {"regional-bank": 20, "enterprise-data-center": 16, "school-district": 16,
                              "hospital-clinics": 16, "provider-backbone": 24}[self.recipe["profile"]]
        self._next = {scope: max(items.values(), default=-1) + 1 for scope, items in self.reservations.items()}

    def reserve(self, scope, key, capacity):
        items = self.reservations.setdefault(scope, {})
        if key not in items:
            slot = self._next.get(scope, 0)
            if slot >= capacity:
                raise DesignError(f"{scope}: capacity {capacity} exhausted by {key}; expand the supported block or rebaseline")
            items[key] = slot
            self._next[scope] = slot + 1
        if items[key] >= capacity:
            raise DesignError(f"{scope}: existing reservation exceeds new capacity {capacity}")
        return items[key]

    def choose(self, key, label, choices):
        # Stable local variation: an unrelated new object never consumes this choice.
        n = int(digest([self.recipe["seed"], key, label, __version__]), 16)
        return choices[n % len(choices)]

    def add(self, kind, key, attrs=None, refs=None, meta=None):
        if key in self.objects:
            raise DesignError(f"Duplicate object identity: {key}")
        if len(self.objects) >= self.recipe["max_objects"]:
            raise DesignError(f"Object budget {self.recipe['max_objects']} exceeded; increase max_objects or reduce demand")
        self.objects[key] = dict(key=key, kind=kind, attrs=attrs or {}, refs=refs or {}, meta=meta or {})
        return key

    def obj(self, key):
        return self.objects[key]

    def reserve_sites(self, site_ids):
        ceiling = 1 << (self.site_prefixlen - self.pool.prefixlen)
        if self.recipe["profile"] == "provider-backbone":
            # Provider slots count /24 units: fixed NOC consumes the first /16;
            # the final /16 belongs to routed links and loopbacks, never sites.
            if "dc-01" not in site_ids or self.allocations.get("dc-01", 0) != 0:
                raise DesignError("Provider NOC must retain dc-01 at the first /16; rebaseline invalid allocations")
            if any(not 256 <= slot < ceiling - 256 for key, slot in self.allocations.items() if key != "dc-01"):
                raise DesignError("Provider small-site reservations must avoid the NOC and infrastructure /16 pools")
            self.allocations.setdefault("dc-01", 0)
            ceiling -= 256
            next_slot = max(255, *self.allocations.values()) + 1
        else:
            next_slot = max(self.allocations.values(), default=-1) + 1
        for site in site_ids:
            if site not in self.allocations:
                self.allocations[site] = next_slot
                next_slot += 1
        if self.allocations and max(self.allocations.values()) >= ceiling:
            raise DesignError(f"Address pool {self.pool} holds {ceiling} /{self.site_prefixlen} site reservations; "
                              f"need {next_slot}. Use a larger pool in a new estate. Old reservations are retained.")

    def site_network(self, site_id):
        if self.recipe["profile"] == "provider-backbone" and site_id == "dc-01":
            return ipaddress.ip_network((int(self.pool.network_address), 16))
        return ipaddress.ip_network((int(self.pool.network_address) + self.allocations[site_id] * (1 << (32-self.site_prefixlen)), self.site_prefixlen))

    def finish(self):
        return dict(schema_version=1, generator_version=__version__, recipe=self.recipe,
                    hardware_digest=digest(self.catalog), allocations=self.allocations,
                    reservations=self.reservations,
                    design_assignments=self.design_assignments,
                    objects=sorted(self.objects.values(), key=lambda o: o["key"]),
                    contracts=self.contracts)
