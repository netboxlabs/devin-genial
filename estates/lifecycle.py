"""Procurement-history sidecar for the NetBox Labs Asset Lifecycle plugin.

Like the geometry sidecar, this is a derived artifact over a frozen
``plan.json``, never a canonical graph change: ``build`` writes a
deterministic procurement story bound to the plan's canonical SHA-256,
``check`` recomputes and byte-compares it, and ``seed`` writes it through the
plugin's REST API (``LIFECYCLE_WRITES=1``) with a private receipt and exact
readback.

The story is derived from the finished graph, never invented per site: one
bill of materials per site covering the racked equipment (plus access points)
and every module installed in it, generated on the target by the plugin's own
scope rules so its assets are the estate's real devices and modules; one
purchase order per fictional vendor per BOM; one delivery per order to the
site's equipment room, dated from the estate's own equipment installation
journals; every asset installed from its delivery; and a spares pool in each
multi-cabinet equipment room stocked with the exact catalog parts installed
there. Couriers carry no tracking URL, so a tracking number never resolves to a
real parcel. Unit prices are deliberately absent: the plan holds no price
facts. Records are seeded and read back; nothing here claims a real purchase.
"""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import argparse
import hashlib
import json
import math
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .model import canonical, digest
from .turbobulk import Client, LoadError, _write_receipt

RECEIPT_VERSION = 1
WRITER_VERSION = "asset-lifecycle-2"
API = "/api/plugins/asset-lifecycle/"
NAME_LIMIT = 100
DESCRIPTION_LIMIT = 200
# Unracked devices that are still network-team procurement. Every other
# unracked device (workstations, cameras, medical and plant endpoints, wall
# outlets) is endpoint inventory and stays out of the BOM.
UNRACKED_ROLES = {"role/ap"}
# A type goes to the carrier distributor only when every installed instance
# is (or sits in) one of these devices; everything else is the regional VAR's.
CARRIER_ROLES = {"role/provider-edge"}
# ponytail: a spares pool per equipment room with two or more cabinets — the
# DC/NOC/PoP rooms in every current profile. A per-profile spares policy is
# the upgrade path if a profile grows multi-cabinet closets.
POOL_MIN_RACKS = 2
SHORT_ALLOCATIONS = 2
INSTALL_WORKERS = 8
VENDORS = {
    "regional": {"name": "Tallgrass Network Supply", "code": "TNS",
                 "description": "Regional reseller for switching, compute, power and optics",
                 "comments": ""},
    "carrier": {"name": "Ostrander Carrier Systems", "code": "OCS",
                "description": "Carrier-equipment distributor for provider-edge routing platforms",
                "comments": ""},
}
COURIER = {"name": "Tallgrass Freight", "code": "tallgrass-freight", "tracking_url": "",
           "description": "Regional freight carrier for equipment deliveries",
           "comments": ""}
# Equipment on order: planned while its purchase order is open, staged once
# that order's delivery is received (racked, not yet installed). Inventory is
# a pre-racked cold spare and lives in a Field depot pool, never in a BOM.
PENDING = ("planned", "staged")
SPARE = "inventory"
# Device journals that date an order and its delivery (the provider timeline).
ORDER_EVENTS = ("ordered",)
RECEIPT_EVENTS = ("received", "racked")
# Device journals that date an installed device's own installation; a device
# without one arrived with its site's first build.
INSTALL_EVENTS = ("equipment-record", "installed", "replaced")
LADDERS = {"boms/": ["draft", "approved", "ordered", "fulfilled"],
           "purchase-orders/": ["draft", "approved", "ordered", "fulfilled"],
           "shipments/": ["prepared", "shipped", "received"]}


class LifecycleError(RuntimeError):
    """The artifact cannot be built, verified or seeded faithfully."""


def _hash(*parts):
    return int(hashlib.sha256("\n".join(parts).encode()).hexdigest(), 16)


def _pick(key, event, low, high):
    """Stable choice in [low, high] from a stable key, never a global RNG."""
    return low + _hash(key, event) % (high - low + 1)


def _days(day, delta):
    return (date.fromisoformat(day) + timedelta(days=delta)).isoformat()


def _type_key(objects, obj):
    """(item_type, manufacturer name, manufacturer slug, model) of a device or module."""
    if obj["kind"] == "device":
        kind, dtype = "dcim.devicetype", objects[obj["refs"]["device_type"]]
    else:
        kind, dtype = "dcim.moduletype", objects[obj["refs"]["module_type"]]
    maker = objects[dtype["refs"]["manufacturer"]]["attrs"]
    return (kind, maker["name"], maker["slug"], dtype["attrs"]["model"])


def _journal_day(entry):
    """The event date of a journal: its ``created`` instant, else the first date in its text."""
    if entry["attrs"].get("created"):
        day = entry["attrs"]["created"][:10]
    else:
        found = re.search(r"\d{4}-\d{2}-\d{2}", entry["attrs"]["comments"])
        day = found.group(0) if found else entry["attrs"]["comments"][:10]
    date.fromisoformat(day)
    return day


def _install_dates(kinds, objects):
    """Earliest equipment installation journal date per site (its first build), from the plan."""
    dates = {}
    for entry in kinds["journal_entry"]:
        if entry["key"].rsplit("/", 1)[-1] not in INSTALL_EVENTS:
            continue
        target = objects.get(entry["refs"]["assigned_object"])
        if target is None or target["kind"] != "device":
            continue
        day = _journal_day(entry)
        site = target["refs"]["site"]
        dates[site] = min(dates.get(site, day), day)
    return dates


def _device_events(kinds, objects):
    """Per device, the date of each journal event named by its key's last segment."""
    events = defaultdict(dict)
    for entry in kinds["journal_entry"]:
        target = entry["refs"].get("assigned_object", "")
        if objects.get(target, {}).get("kind") == "device":
            events[target][entry["key"].rsplit("/", 1)[-1]] = _journal_day(entry)
    return events


def create(plan):
    objects = {obj["key"]: obj for obj in plan["objects"]}
    kinds = defaultdict(list)
    for obj in plan["objects"]:
        kinds[obj["kind"]].append(obj)
    namespace = plan["recipe"]["namespace"]
    # Equipment not yet installed (a planned or staged premises) has no
    # installation to date a delivery from; it stays out of the BOMs.
    procured = {obj["key"] for obj in kinds["device"]
                if obj["refs"].get("rack") or obj["refs"].get("role") in UNRACKED_ROLES}
    selected = {key for key in procured
                if objects[key]["attrs"].get("status", "active") not in PENDING + (SPARE,)}
    # Equipment on order is dated by its own order/delivery journals; a planned
    # premises with no paperwork yet has nothing to order from and stays out.
    events = _device_events(kinds, objects)
    pending = {key for key in procured if objects[key]["attrs"].get("status") in PENDING
               and any(event in events[key] for event in ORDER_EVENTS + RECEIPT_EVENTS)}
    modules_by_device = defaultdict(list)
    for module in kinds["module"]:
        modules_by_device[module["refs"]["device"]].append(module)
    devices_by_site = defaultdict(list)
    for device in kinds["device"]:
        devices_by_site[device["refs"]["site"]].append(device)

    # Vendor per type, from every installed instance across the estate.
    on_carrier = defaultdict(set)
    for key in selected | pending:
        device = objects[key]
        carrier = device["refs"].get("role") in CARRIER_ROLES
        on_carrier[_type_key(objects, device)].add(carrier)
        for module in modules_by_device[key]:
            on_carrier[_type_key(objects, module)].add(carrier)
    vendor_of = {item: "carrier" if flags == {True} else "regional"
                 for item, flags in on_carrier.items()}

    install_dates = _install_dates(kinds, objects)
    boms = []
    for site_key in sorted({objects[key]["refs"]["site"] for key in selected},
                           key=lambda key: objects[key]["attrs"]["slug"]):
        site = objects[site_key]["attrs"]
        devices = sorted((objects[key] for key in selected
                          if objects[key]["refs"]["site"] == site_key),
                         key=lambda obj: obj["attrs"]["name"])
        roles = {device["refs"]["role"] for device in devices}
        # The target-side scope rules are site + role (devices) and site
        # (modules); refuse a site where they would select anything else.
        statuses = sorted({d["attrs"].get("status", "active") for d in devices})
        stray = [d["attrs"]["name"] for d in devices_by_site[site_key]
                 if d["refs"].get("role") in roles and d["key"] not in selected
                 and d["attrs"].get("status", "active") in statuses]
        # Modules of equipment outside this BOM (on order, a cold spare) are
        # excluded by device name, which the module filter accepts.
        foreign = sorted({d["attrs"]["name"] for d in devices_by_site[site_key]
                          if d["key"] not in selected and modules_by_device[d["key"]]})
        if set(foreign) & {d["attrs"]["name"] for d in devices}:
            stray += sorted(set(foreign) & {d["attrs"]["name"] for d in devices})
        if stray:
            raise LifecycleError(f"site {site['slug']!r}: the BOM scope rules would also select "
                                 f"{stray[:3]}; the site cannot be expressed as site/role scope")
        if site_key not in install_dates:
            raise LifecycleError(f"site {site['slug']!r} has BOM equipment but no equipment "
                                 "installation journal to date its delivery from")
        first_build = install_dates[site_key]
        rooms = Counter(device["refs"]["location"] for device in devices)
        room = objects[min(rooms, key=lambda key: (-rooms[key], objects[key]["attrs"]["slug"]))]

        # One delivery per vendor per installation date: a site that grew over
        # years (a refreshed PE, a replaced switch) carries an order per era.
        lines, assets, groups = Counter(), [], defaultdict(Counter)
        for device in devices:
            dated = events[device["key"]]
            installed = min([dated[e] for e in INSTALL_EVENTS if e in dated] or [first_build])
            for part in [device] + sorted(modules_by_device[device["key"]],
                                          key=lambda obj: objects[obj["refs"]["module_bay"]]["attrs"]["name"]):
                item = _type_key(objects, part)
                lines[item] += 1
                groups[(vendor_of[item], installed)][item] += 1
                assets.append({"object_type": f"dcim.{part['kind']}", "device": device["attrs"]["name"],
                               **({"module_bay": objects[part["refs"]["module_bay"]]["attrs"]["name"]}
                                  if part["kind"] == "module" else {}),
                               "vendor": vendor_of[item], "_group": (vendor_of[item], installed)})
        orders, order_of = [], {}
        for vendor, installed in sorted(groups):
            # The first build keeps its original stable keys, so a site with one
            # era reads exactly as before.
            stable = site_key if installed == first_build else f"{site_key}/{installed}"
            received = _days(installed, -_pick(stable, "received", 7, 21))
            shipped = _days(received, -_pick(stable, "transit", 2, 5))
            ordered = _days(installed, -_pick(stable, "ordered", 40, 50))
            approved = _days(ordered, -_pick(stable, "approved", 2, 6))
            code = VENDORS[vendor]["code"]
            label = "Initial equipment order" if installed == first_build else "Equipment order"
            orders.append({
                "vendor": vendor,
                "order_id": f"{code}-{ordered[2:4]}{ordered[5:7]}-{_hash(stable, vendor, 'po') % 16**6:06X}",
                "description": f"{label} for {site['name']}"[:DESCRIPTION_LIMIT],
                "comments": f"Approved {approved}; ordered {ordered}; fulfilled {received}.",
                "items": sorted([list(item), count] for item, count in groups[(vendor, installed)].items()),
                "target": "fulfilled", "installs": True,
                "shipment": {
                    "tracking_number": f"TGF{_hash(stable, vendor, 'tracking') % 10**10:010d}",
                    "date_shipped": shipped, "date_expected": _days(shipped, 3),
                    "date_received": received,
                    "description": f"Delivery to {room['attrs']['name']}, {site['name']}"[:DESCRIPTION_LIMIT],
                },
            })
            order_of[(vendor, installed)] = orders[-1]["order_id"]
        for asset in assets:
            asset["order_id"] = order_of[asset.pop("_group")]
        approved = min(o["comments"][9:19] for o in orders)
        installed = first_build
        boms.append({
            "site_slug": site["slug"], "site": site["name"],
            "location_slug": room["attrs"]["slug"],
            "name": f"{site['name']} — installed equipment",
            "description": f"Racked network, power and compute equipment with installed modules at {site['name']}"[:DESCRIPTION_LIMIT],
            "comments": (f"Approved {_days(approved, -_pick(site_key, 'bom', 1, 4))}; first installation "
                         f"{installed}.\nScope: racked equipment and access points at this site, "
                         "and every module installed in them; endpoints are excluded."),
            "rules": [{"object_types": ["dcim.device"],
                       "parameters": {"site": [site["slug"]],
                                      "role": sorted(objects[role]["attrs"]["slug"] for role in roles),
                                      **({"status": statuses} if statuses != ["active"] else {})}},
                      {"object_types": ["dcim.module"], "parameters": {"site": [site["slug"]]}}]
                     + ([{"object_types": ["dcim.module"], "action": "exclude",
                          "parameters": {"site": [site["slug"]], "device": foreign}}] if foreign else []),
            "target": "fulfilled",
            "lines": sorted([list(item), count] for item, count in lines.items()),
            "assets": assets,
            "orders": orders,
        })

    boms += _on_order(objects, devices_by_site, modules_by_device, pending, events, vendor_of)
    pools = _pools(objects, kinds, selected, modules_by_device) + _depots(objects, kinds)
    accounts = {vendor: f"{VENDORS[vendor]['code']}-{_hash(namespace, vendor, 'account') % 10**7:07d}"
                for vendor in sorted(set(vendor_of.values()))}
    return {
        "artifact": "asset-lifecycle",
        "schema_version": 1,
        "plan_sha256": digest(plan),
        "policy": {"scope": "racked devices plus access points, and every installed module",
                   "carrier_roles": sorted(CARRIER_ROLES),
                   "pool_min_racks": POOL_MIN_RACKS,
                   "dates": "received 7-21 days before the site's first equipment installation "
                            "journal, shipped 2-5 days earlier, ordered 40-50 days before installation; "
                            "equipment on order from its own ordered/received journals",
                   "on_order": "planned devices: purchase order ordered, no delivery; staged devices: "
                               "delivery received, assets not installed",
                   "cold_spares": "inventory devices: one serialized spare item each, in a Field depot "
                                  "pool at their own room, never a BOM asset",
                   "prices": "none: unit_price is left null"},
        "vendors": [{**VENDORS[vendor], "key": vendor, "account_number": accounts[vendor]}
                    for vendor in sorted(accounts)],
        "courier": COURIER,
        "boms": boms,
        "pools": pools,
    }


def _on_order(objects, devices_by_site, modules_by_device, pending, events, vendor_of):
    """One BOM per site for its equipment on order, dated from the devices' own journals."""
    boms = []
    for site_key in sorted({objects[key]["refs"]["site"] for key in pending},
                           key=lambda key: objects[key]["attrs"]["slug"]):
        site = objects[site_key]["attrs"]
        devices = sorted((objects[key] for key in pending if objects[key]["refs"]["site"] == site_key),
                         key=lambda obj: obj["attrs"]["name"])
        roles = {d["refs"]["role"] for d in devices}
        statuses = sorted({d["attrs"]["status"] for d in devices})
        stray = [d["attrs"]["name"] for d in devices_by_site[site_key]
                 if d["refs"].get("role") in roles and d["attrs"].get("status") in statuses
                 and d["key"] not in pending]
        if stray:
            raise LifecycleError(f"site {site['slug']!r}: equipment on order without an order journal "
                                 f"{stray[:3]} shares role and status with dated equipment; the BOM "
                                 "scope rules cannot separate them")
        rooms = Counter(d["refs"]["location"] for d in devices)
        room = objects[min(rooms, key=lambda key: (-rooms[key], objects[key]["attrs"]["slug"]))]
        # One purchase order per vendor per order/delivery date pair: a captive
        # AOC end installed in a successor ships with that successor.
        lines, assets, groups = Counter(), [], defaultdict(Counter)
        for device in devices:
            dated = events[device["key"]]
            received = next((dated[e] for e in RECEIPT_EVENTS if e in dated), None)
            if (device["attrs"]["status"] == "staged") != (received is not None):
                raise LifecycleError(f"{device['attrs']['name']}: a staged device needs a received "
                                     "journal and a planned one must not have one")
            ordered = next((dated[e] for e in ORDER_EVENTS if e in dated), None) \
                or _days(received, -_pick(device["key"], "ordered", 40, 50))
            parts = [device] + sorted(modules_by_device[device["key"]],
                                      key=lambda m: objects[m["refs"]["module_bay"]]["attrs"]["name"])
            for part in parts:
                item = _type_key(objects, part)
                lines[item] += 1
                group = (vendor_of[item], ordered, received or "")
                groups[group][item] += 1
                assets.append({"object_type": f"dcim.{part['kind']}", "device": device["attrs"]["name"],
                               **({"module_bay": objects[part["refs"]["module_bay"]]["attrs"]["name"]}
                                  if part["kind"] == "module" else {}),
                               "vendor": vendor_of[item], "_group": group})
        orders, order_of = [], {}
        for group in sorted(groups):
            vendor, ordered, received = group
            code = VENDORS[vendor]["code"]
            stable = "/".join((site_key, vendor, ordered, received))
            approved = _days(ordered, -_pick(stable, "on-order-approved", 2, 6))
            order = {"vendor": vendor,
                     "order_id": f"{code}-{ordered[2:4]}{ordered[5:7]}-{_hash(stable, 'on-order-po') % 16**6:06X}",
                     "description": f"Equipment on order for {site['name']}"[:DESCRIPTION_LIMIT],
                     "comments": f"Approved {approved}; ordered {ordered}."
                                 + (f" Received {received}; awaiting installation." if received else ""),
                     "items": sorted([list(item), count] for item, count in groups[group].items()),
                     "target": "ordered", "installs": False, "shipment": None}
            if received:
                shipped = _days(received, -_pick(stable, "on-order-transit", 2, 5))
                order["shipment"] = {
                    "tracking_number": f"TGF{_hash(stable, 'on-order-tracking') % 10**10:010d}",
                    "date_shipped": shipped, "date_expected": _days(shipped, 3), "date_received": received,
                    "description": f"Delivery to {room['attrs']['name']}, {site['name']}"[:DESCRIPTION_LIMIT]}
            order_of[group] = order["order_id"]
            orders.append(order)
        for asset in assets:
            asset["order_id"] = order_of[asset.pop("_group")]
        first = min(ordered for _, ordered, _ in groups)
        modules = sorted({d["attrs"]["name"] for d in devices if modules_by_device[d["key"]]})
        boms.append({
            "site_slug": site["slug"], "site": site["name"], "location_slug": room["attrs"]["slug"],
            "name": f"{site['name']} — equipment on order",
            "description": f"Equipment ordered for {site['name']} and not yet in service"[:DESCRIPTION_LIMIT],
            "comments": f"Approved {_days(first, -_pick(site_key, 'on-order-bom', 3, 7))}.\n"
                        "Scope: planned and staged equipment at this site, and every module installed in it.",
            "rules": [{"object_types": ["dcim.device"],
                       "parameters": {"site": [site["slug"]],
                                      "role": sorted(objects[r]["attrs"]["slug"] for r in roles),
                                      "status": statuses}}]
                     + ([{"object_types": ["dcim.module"],
                          "parameters": {"site": [site["slug"]], "device": modules}}] if modules else []),
            "target": "ordered",
            "lines": sorted([list(item), count] for item, count in lines.items()),
            "assets": assets,
            "orders": orders,
        })
    return boms


def _depots(objects, kinds):
    """A Field depot pool per room holding pre-racked cold spares: one serialized
    spare item per inventory device, matched to it by (device type, serial)."""
    spares = defaultdict(list)
    for device in kinds["device"]:
        if device["attrs"].get("status") == SPARE:
            if not device["attrs"].get("serial") or not device["refs"].get("location"):
                raise LifecycleError(f"cold spare {device['attrs']['name']} needs a serial and a room")
            spares[device["refs"]["location"]].append(device)
    pools = []
    for location_key in sorted(spares, key=lambda key: objects[key]["attrs"]["slug"]):
        location = objects[location_key]
        site = objects[location["refs"]["site"]]["attrs"]
        devices = sorted(spares[location_key], key=lambda d: d["attrs"]["name"])
        items = Counter(_type_key(objects, d) for d in devices)
        pools.append({
            "name": f"{site['name']} — Field depot",
            "site_slug": site["slug"], "location_slug": location["attrs"]["slug"],
            "description": f"Pre-racked cold spares at {site['name']}"[:DESCRIPTION_LIMIT],
            "spares": [{"item": list(_type_key(objects, d)), "serial": d["attrs"]["serial"],
                        "device": d["attrs"]["name"]} for d in devices],
            "allocations": [{"item": list(item), "installed": 0, "min_quantity": count,
                             "max_quantity": count, "serviceable": count, "damaged": 0,
                             "below_minimum": False} for item, count in sorted(items.items())]})
    return pools


def _pools(objects, kinds, selected, modules_by_device):
    racks = Counter(rack["refs"]["location"] for rack in kinds["rack"])
    pools, allocations = [], []
    for location_key in sorted((key for key, count in racks.items() if count >= POOL_MIN_RACKS),
                               key=lambda key: objects[key]["attrs"]["slug"]):
        location = objects[location_key]
        site = objects[location["refs"]["site"]]["attrs"]
        installed = Counter()
        for device_key in selected:
            if objects[device_key]["refs"].get("location") == location_key:
                for module in modules_by_device[device_key]:
                    installed[_type_key(objects, module)] += 1
        if not installed:
            continue
        pool = {"name": f"{site['name']} — {location['attrs']['name']} spares",
                "site_slug": site["slug"], "location_slug": location["attrs"]["slug"],
                "description": f"Field-replaceable spares for {location['attrs']['name']} at {site['name']}"[:DESCRIPTION_LIMIT],
                "spares": [], "allocations": []}
        for item in sorted(installed):
            minimum = max(1, math.ceil(installed[item] / 8))
            maximum = minimum + max(2, minimum)
            stable = f"{location['attrs']['slug']}/{item[3]}"
            allocation = {"item": list(item), "installed": installed[item],
                          "min_quantity": minimum, "max_quantity": maximum,
                          "serviceable": _pick(stable, "stock", minimum, maximum),
                          "damaged": 0, "_key": stable}
            pool["allocations"].append(allocation)
            allocations.append(allocation)
        pools.append(pool)
    # The stock story: the first allocations by stable hash run short and the
    # next one holds a part flagged damaged at audit. Hash order, not plan
    # order, so appending a room rarely moves an existing story.
    ranked = sorted(allocations, key=lambda allocation: _hash(allocation["_key"], "audit"))
    for allocation in ranked[:SHORT_ALLOCATIONS]:
        allocation["serviceable"] = allocation["min_quantity"] - 1
    for allocation in ranked[SHORT_ALLOCATIONS:SHORT_ALLOCATIONS + 1]:
        allocation["damaged"] = 1
    for allocation in allocations:
        del allocation["_key"]
        allocation["below_minimum"] = allocation["serviceable"] < allocation["min_quantity"]
    return pools


def _intrinsic(artifact):
    """Plan-free invariants; also run before any seed write."""
    if artifact.get("artifact") != "asset-lifecycle":
        raise LifecycleError("not an asset-lifecycle artifact")
    names, order_ids, tracking = set(), set(), set()
    vendor_keys = {vendor["key"] for vendor in artifact["vendors"]}
    if artifact["courier"].get("tracking_url"):
        raise LifecycleError("the courier must carry no tracking URL; tracking numbers are fictional")
    for bom in artifact["boms"]:
        label = bom["site_slug"]
        if len(bom["name"]) > NAME_LIMIT or bom["name"] in names:
            raise LifecycleError(f"BOM name {bom['name']!r} is duplicated or too long")
        names.add(bom["name"])
        identities = [(a["object_type"], a["device"], a.get("module_bay")) for a in bom["assets"]]
        if len(set(identities)) != len(identities):
            raise LifecycleError(f"BOM {label!r} repeats an asset")
        counted = Counter()
        for asset in bom["assets"]:
            if asset["vendor"] not in vendor_keys:
                raise LifecycleError(f"BOM {label!r} asset names an undeclared vendor")
            counted[asset["object_type"] + "type"] += 1
        line_total = Counter()
        for item, count in bom["lines"]:
            line_total[item[0]] += count
        if line_total != counted:
            raise LifecycleError(f"BOM {label!r} line quantities do not equal its assets")
        ordered = Counter()
        for order in bom["orders"]:
            if order["vendor"] not in vendor_keys:
                raise LifecycleError(f"BOM {label!r} orders from an undeclared vendor")
            if (order["vendor"], order["order_id"]) in order_ids:
                raise LifecycleError(f"order id {order['order_id']!r} is duplicated")
            order_ids.add((order["vendor"], order["order_id"]))
            shipment = order["shipment"]
            if order["target"] not in ("ordered", "fulfilled") or (order["target"] == "fulfilled") != order["installs"]:
                raise LifecycleError(f"BOM {label!r} order {order['order_id']!r} misstates its state")
            if shipment is None:
                if order["installs"]:
                    raise LifecycleError(f"BOM {label!r} installs assets without a delivery")
                for item, count in order["items"]:
                    ordered[tuple(item)] += count
                continue
            if shipment["tracking_number"] in tracking:
                raise LifecycleError(f"tracking number {shipment['tracking_number']!r} is duplicated")
            tracking.add(shipment["tracking_number"])
            if not (shipment["date_shipped"] <= shipment["date_expected"]
                    and shipment["date_shipped"] <= shipment["date_received"]):
                raise LifecycleError(f"BOM {label!r} delivery dates run backwards")
            for item, count in order["items"]:
                ordered[tuple(item)] += count
        if ordered != Counter({tuple(item): count for item, count in bom["lines"]}):
            raise LifecycleError(f"BOM {label!r} orders do not cover its lines exactly once")
    serials = set()
    for pool in artifact["pools"]:
        for spare in pool["spares"]:
            identity = (spare["item"][0], spare["item"][2], spare["item"][3], spare["serial"])
            if not spare["serial"] or identity in serials:
                raise LifecycleError(f"spare {spare['serial']!r} is unserialized or repeats its "
                                     "(type, serial)")
            serials.add(identity)
        if len(pool["name"]) > NAME_LIMIT or pool["name"] in names:
            raise LifecycleError(f"spares pool name {pool['name']!r} is duplicated or too long")
        names.add(pool["name"])
        for allocation in pool["allocations"]:
            if not (0 <= allocation["min_quantity"] <= allocation["max_quantity"]):
                raise LifecycleError(f"pool {pool['name']!r} has an inverted allocation")
            if allocation["below_minimum"] != (allocation["serviceable"] < allocation["min_quantity"]):
                raise LifecycleError(f"pool {pool['name']!r} misstates a stock shortfall")


def verify(artifact, plan):
    _intrinsic(artifact)
    if artifact.get("plan_sha256") != digest(plan):
        raise LifecycleError("lifecycle artifact is not bound to this canonical plan")
    if canonical(artifact) != canonical(create(plan)):
        raise LifecycleError("lifecycle artifact differs from the story recomputed from the "
                             "bound plan; rebuild it")
    return _summary(artifact)


def _summary(artifact):
    allocations = [a for pool in artifact["pools"] for a in pool["allocations"]]
    return {"boms": len(artifact["boms"]),
            "assets": sum(len(bom["assets"]) for bom in artifact["boms"]),
            "purchase_orders": sum(len(bom["orders"]) for bom in artifact["boms"]),
            "shipments": sum(1 for bom in artifact["boms"] for o in bom["orders"] if o["shipment"]),
            "on_order_boms": sum(1 for bom in artifact["boms"] if bom["target"] != "fulfilled"),
            "cold_spares": sum(len(pool["spares"]) for pool in artifact["pools"]),
            "spares_pools": len(artifact["pools"]),
            "allocations": len(allocations),
            "below_minimum": sum(a["below_minimum"] for a in allocations),
            "damaged": sum(a["damaged"] for a in allocations)}


def build(plan_path, out):
    plan = json.loads(Path(plan_path).read_text())
    artifact = create(plan)
    evidence = verify(artifact, plan)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    (out / "lifecycle.json").write_bytes(canonical(artifact) + b"\n")
    (out / "checks.json").write_bytes(canonical({
        "status": "passed", "scope": "offline procurement story",
        "plan_sha256": artifact["plan_sha256"], **evidence}) + b"\n")
    return {"artifact": artifact["artifact"], "output": str(out), **evidence}


def check(directory, plan_path):
    directory = Path(directory)
    raw = (directory / "lifecycle.json").read_bytes()
    artifact = json.loads(raw)
    evidence = verify(artifact, json.loads(Path(plan_path).read_text()))
    if raw != canonical(artifact) + b"\n":
        raise LifecycleError("lifecycle.json bytes differ from their canonical form; rebuild")
    try:
        checks = json.loads((directory / "checks.json").read_text())
    except (OSError, ValueError) as exc:
        raise LifecycleError(f"checks.json is missing or unreadable: {exc}")
    if checks.get("status") != "passed" or checks.get("plan_sha256") != artifact["plan_sha256"]:
        raise LifecycleError("checks.json does not record a passed check bound to this plan")
    return {"artifact": artifact["artifact"], "checks": "story verified", **evidence}


# --- seeding -----------------------------------------------------------------

def _id(reference):
    return reference.get("id") if isinstance(reference, dict) else reference


def _value(field):
    return field.get("value") if isinstance(field, dict) else field


def _item(row):
    """(item_type, manufacturer name, model) of a live line/spare/allocation row."""
    item = row.get("item") or {}
    return (row.get("item_type"), (item.get("manufacturer") or {}).get("name"), item.get("model"))


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


class _Writer:
    def __init__(self, client):
        self.client, self.writes, self.lock = client, 0, threading.Lock()

    def send(self, method, path, payload):
        _, body = self.client.request(API + path, method=method,
                                      body=json.dumps(payload).encode(),
                                      headers={"Content-Type": "application/json"},
                                      branch=False)
        with self.lock:
            self.writes += 1
        return body

    def rows(self, path):
        return self.client.all(API + path)


def _lookup_one(client, path, params, what):
    _, page = client.request(f"{path}?{params}", branch=False)
    if page.get("count") != 1 or len(page.get("results", [])) != 1:
        raise LoadError(f"expected exactly one {what} on the target, found {page.get('count')}; "
                        "seed the estate first and keep identities unique")
    return page["results"][0]


def _advance(writer, path, row, target, extra=None):
    """Walk a rule-governed status one permitted step at a time (each a changelog entry)."""
    ladder = LADDERS[path]
    while ladder.index(_value(row["status"])) < ladder.index(target):
        step = ladder[ladder.index(_value(row["status"])) + 1]
        # A PO needs its order_id on every step from ordered on.
        carries = step == target or (path == "purchase-orders/" and step in ("ordered", "fulfilled"))
        row = writer.send("PATCH", f"{path}{row['id']}/",
                          {"status": step, **(extra or {} if carries else {})})
    return row


def seed(artifact_dir, *, url, token, receipt_path):
    if os.environ.get("LIFECYCLE_WRITES", "").strip().lower() not in ("1", "true", "yes", "on"):
        raise LoadError("writing the procurement story requires LIFECYCLE_WRITES=1 in the environment")
    directory = Path(artifact_dir)
    raw = (directory / "lifecycle.json").read_bytes()
    artifact = json.loads(raw)
    _intrinsic(artifact)
    client = Client(url, token)
    _, status = client.request("/api/status/", branch=False)
    plugin = status.get("plugins", {}).get("netbox_asset_lifecycle")
    if not plugin:
        raise LoadError("the target does not run the netbox_asset_lifecycle plugin")
    binding = {"receipt_version": RECEIPT_VERSION, "writer_version": WRITER_VERSION,
               "artifact": str(directory), "lifecycle_sha256": hashlib.sha256(raw).hexdigest(),
               "plan_sha256": artifact["plan_sha256"], "target": client.base,
               "plugin_version": plugin}
    receipt_path = Path(receipt_path)
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    if receipt is not None:
        for key, value in binding.items():
            if receipt.get(key) != value:
                raise LoadError(f"receipt {receipt_path} has different {key}; "
                                "choose a new receipt or rebuild the artifact")
    resuming = receipt is not None
    receipt = receipt or {**binding, "started_at": _now(), "boms": {}, "pools": {}}
    writer = _Writer(client)

    # Resolve every estate identity read-only before any write.
    sites, rooms, devices, modules, module_types = {}, {}, {}, {}, {}
    for slug in sorted({bom["site_slug"] for bom in artifact["boms"]}
                       | {pool["site_slug"] for pool in artifact["pools"]}):
        sites[slug] = _lookup_one(client, "/api/dcim/sites/", "slug=" + urllib.parse.quote(slug),
                                  f"site {slug!r}")["id"]
    for owner in artifact["boms"] + artifact["pools"]:
        slug = owner["location_slug"]
        rooms[slug] = _lookup_one(client, "/api/dcim/locations/",
                                  f"site_id={sites[owner['site_slug']]}&slug=" + urllib.parse.quote(slug),
                                  f"location {slug!r}")["id"]
    for bom in artifact["boms"]:
        site_id = sites[bom["site_slug"]]
        named = {row["name"]: row["id"] for row in client.all(f"/api/dcim/devices/?site_id={site_id}")}
        bays = {(row["device"]["name"], row["module_bay"]["name"]): row["id"]
                for row in client.all(f"/api/dcim/modules/?site_id={site_id}")}
        expected = {}
        for asset in bom["assets"]:
            ident = named.get(asset["device"]) if asset["object_type"] == "dcim.device" else \
                bays.get((asset["device"], asset["module_bay"]))
            if ident is None:
                raise LoadError(f"{asset['object_type']} {asset['device']} "
                                f"{asset.get('module_bay') or ''} is missing at {bom['site_slug']!r}; "
                                "seed the estate first")
            expected[(asset["object_type"], ident)] = asset["order_id"]
        devices[bom["name"]] = expected
    for pool in artifact["pools"]:
        for entry in pool["allocations"] + pool["spares"]:
            kind, _, maker_slug, model = entry["item"]
            key = (kind, maker_slug, model)
            if key not in module_types:
                endpoint = "device-types" if kind == "dcim.devicetype" else "module-types"
                module_types[key] = _lookup_one(
                    client, f"/api/dcim/{endpoint}/",
                    f"manufacturer={urllib.parse.quote(maker_slug)}&model={urllib.parse.quote(model)}",
                    f"{kind} {model!r}")["id"]

    # Snapshot the plugin once; every step below adopts by natural key, so a
    # resume after a lost response never resends a mutation that landed.
    live = {path: writer.rows(path) for path in
            ("vendors/", "vendor-accounts/", "couriers/", "boms/", "purchase-orders/",
             "shipments/", "spares-pools/")}
    ours = {bom["name"] for bom in artifact["boms"]} | {pool["name"] for pool in artifact["pools"]}
    if not resuming:
        taken = sorted(row["name"] for path in ("boms/", "spares-pools/") for row in live[path]
                       if row["name"] in ours)
        if taken:
            raise LoadError("these BOMs or spares pools already exist and this receipt did not "
                            "create them; lifecycle seeding is fresh-only (delete them first): "
                            + ", ".join(taken[:10]))
    receipt["resumed"] = resuming
    _write_receipt(receipt_path, receipt)
    started = time.monotonic()

    def adopt_or_create(path, match, payload):
        row = next((row for row in live[path] if match(row)), None)
        if row is None:
            row = writer.send("POST", path, payload)
            live[path].append(row)
        return row

    vendor_ids, account_ids = {}, {}
    for vendor in artifact["vendors"]:
        row = adopt_or_create("vendors/", lambda r, v=vendor: r["name"] == v["name"],
                              {k: vendor[k] for k in ("name", "code", "description", "comments")})
        if row.get("code") != vendor["code"]:
            raise LoadError(f"vendor {vendor['name']!r} exists with a different code")
        vendor_ids[vendor["key"]] = row["id"]
        account_ids[vendor["key"]] = adopt_or_create(
            "vendor-accounts/",
            lambda r, v=vendor: _id(r["vendor"]) == vendor_ids[v["key"]]
            and r["account_number"] == v["account_number"],
            {"vendor": row["id"], "account_number": vendor["account_number"],
             "description": "Equipment purchasing account"})["id"]
    courier = adopt_or_create("couriers/", lambda r: r["name"] == COURIER["name"],
                              {k: artifact["courier"][k] for k in
                               ("name", "code", "tracking_url", "description", "comments")})
    if courier.get("tracking_url"):
        raise LoadError(f"courier {COURIER['name']!r} exists with a tracking URL; refusing to "
                        "attach fictional tracking numbers to it")
    receipt.update(vendors=vendor_ids, vendor_accounts=account_ids, courier=courier["id"])

    for bom in artifact["boms"]:
        receipt["boms"][bom["name"]] = _seed_bom(writer, live, bom, devices[bom["name"]], sites, rooms,
                                                 vendor_ids, account_ids, courier["id"])
        _write_receipt(receipt_path, receipt)
    for pool in artifact["pools"]:
        receipt["pools"][pool["name"]] = _seed_pool(writer, live, pool, sites, rooms, module_types)
        _write_receipt(receipt_path, receipt)

    verification = _readback(writer, artifact, receipt, devices, sites, rooms, module_types)
    receipt.update(success=verification["mismatches"] == 0, completed_at=_now(),
                   wall_seconds=round(time.monotonic() - started, 3), writes=writer.writes,
                   verification=verification)
    _write_receipt(receipt_path, receipt)
    if verification["mismatches"]:
        raise LoadError(f"lifecycle readback found {verification['mismatches']} mismatches; "
                        f"receipt: {receipt_path}")
    return {"success": True, "writes": writer.writes, "receipt": str(receipt_path),
            **{key: verification[key] for key in ("boms", "assets_installed", "pools")}}


def _counts(order):
    """An order's quantities keyed like a live BOM line: (item_type, manufacturer name, model)."""
    return Counter({(item[0], item[1], item[3]): count for item, count in order["items"]})


def _seed_bom(writer, live, bom, expected, sites, rooms, vendor_ids, account_ids, courier_id):
    row = next((r for r in live["boms/"] if r["name"] == bom["name"]), None)
    if row is None:
        row = writer.send("POST", "boms/", {"name": bom["name"], "status": "draft",
                                            "description": bom["description"],
                                            "comments": bom["comments"]})
    bom_id = row["id"]
    if _value(row["status"]) == "draft":
        rules = writer.rows(f"bom-scope-rules/?bom_id={bom_id}")
        wanted = [{"bom": bom_id, "action": "include", "enabled": True, **rule} for rule in bom["rules"]]
        if not rules:
            writer.send("POST", "bom-scope-rules/", wanted)
        elif sorted(canonical([r["object_types"], r["parameters"], _value(r["action"])]) for r in rules) != \
                sorted(canonical([r["object_types"], r["parameters"], r["action"]]) for r in wanted):
            raise LoadError(f"BOM {bom['name']!r} carries scope rules this artifact did not write")
        if not row.get("last_generated") or not row.get("is_current"):
            row = writer.send("POST", f"boms/{bom_id}/generate/", {})
    assets = writer.rows(f"assets/?bom_id={bom_id}")
    found = {(a["assigned_object_type"], a["assigned_object_id"]) for a in assets}
    if found != set(expected):
        raise LoadError(f"BOM {bom['name']!r} generated {len(found)} assets, expected "
                        f"{len(expected)} (missing {len(set(expected) - found)}, unexpected "
                        f"{len(found - set(expected))}); the target diverges from the plan — the "
                        "BOM is left in draft for review")
    lines = writer.rows(f"bom-line-items/?bom_id={bom_id}")
    totals = Counter()
    for line in lines:
        totals[_item(line)] += line["quantity"]
    if totals != Counter({(item[0], item[1], item[3]): count for item, count in bom["lines"]}):
        raise LoadError(f"BOM {bom['name']!r} generated line items that differ from the plan")
    line_of = {_item(line): line["id"] for line in lines}
    row = _advance(writer, "boms/", row, "approved")

    orders = {}
    for order in bom["orders"]:
        vendor = vendor_ids[order["vendor"]]
        po = next((r for r in live["purchase-orders/"] if _id(r["bom"]) == bom_id
                   and _id(r["vendor"]) == vendor and r.get("order_id") == order["order_id"]), None)
        if po is None:
            po = writer.send("POST", "purchase-orders/", {
                "vendor": vendor, "vendor_account": account_ids[order["vendor"]], "bom": bom_id,
                "order_id": order["order_id"], "currency": "USD", "status": "draft",
                "description": order["description"], "comments": order["comments"]})
            live["purchase-orders/"].append(po)
        counts = _counts(order)
        if _value(po["status"]) == "draft" and not writer.rows(f"po-line-items/?purchase_order_id={po['id']}"):
            writer.send("POST", "po-line-items/", [
                {"purchase_order": po["id"], "bom_line_item": line_of[item],
                 "qty_ordered": count, "unit_price": None}
                for item, count in sorted(counts.items())])
        po = _advance(writer, "purchase-orders/", po, "ordered", {"order_id": order["order_id"]})
        orders[order["order_id"]] = (po, order, counts)
    row = _advance(writer, "boms/", row, "ordered")

    shipments = {}
    for order_id, (po, order, counts) in orders.items():
        spec = order["shipment"]
        if spec is None:
            continue  # on order, not yet shipped
        shipment = next((r for r in live["shipments/"] if _id(r["courier"]) == courier_id
                         and r["tracking_number"] == spec["tracking_number"]), None)
        if shipment is None:
            shipment = writer.send("POST", "shipments/", {
                "purchase_order": po["id"], "courier": courier_id, "courier_account": None,
                "tracking_number": spec["tracking_number"], "status": "shipped",
                "site": sites[bom["site_slug"]], "location": rooms[bom["location_slug"]],
                "date_shipped": spec["date_shipped"], "date_expected": spec["date_expected"],
                "description": spec["description"]})
            live["shipments/"].append(shipment)
        shipped_lines = writer.rows(f"shipment-line-items/?shipment_id={shipment['id']}")
        if not shipped_lines:
            shipped_lines = writer.send("POST", "shipment-line-items/", [
                {"shipment": shipment["id"], "bom_line_item": line_of[item], "qty_shipped": count}
                for item, count in sorted(counts.items())])
        shipment = _advance(writer, "shipments/", shipment, "received",
                            {"date_received": spec["date_received"]})
        pending = [{"id": line["id"], "qty_received": line["qty_shipped"]}
                   for line in shipped_lines if line.get("qty_received") is None]
        if pending:
            writer.send("PATCH", "shipment-line-items/", pending)
        shipments[order_id] = shipment["id"]

    # Install through the plugin's own action so every asset carries its
    # delivery; the action never touches the already-existing DCIM object.
    # Equipment on order (received or not) stays uninstalled.
    todo = [(a["id"], shipments[expected[(a["assigned_object_type"], a["assigned_object_id"])]])
            for a in assets if not a.get("installed")
            and orders[expected[(a["assigned_object_type"], a["assigned_object_id"])]][1]["installs"]]
    with ThreadPoolExecutor(INSTALL_WORKERS) as pool:
        list(pool.map(lambda job: writer.send("POST", f"assets/{job[0]}/install/",
                                              {"shipment": job[1]}), todo))
    for order_id, (po, order, _) in orders.items():
        _advance(writer, "purchase-orders/", po, order["target"], {"order_id": order_id})
    _advance(writer, "boms/", row, bom["target"])
    return {"bom": bom_id, "purchase_orders": {k: o[0]["id"] for k, o in orders.items()},
            "shipments": shipments, "installed_now": len(todo)}


def _seed_pool(writer, live, pool, sites, rooms, types):
    row = next((r for r in live["spares-pools/"] if r["name"] == pool["name"]), None)
    if row is None:
        row = writer.send("POST", "spares-pools/", {
            "name": pool["name"], "site": sites[pool["site_slug"]],
            "location": rooms[pool["location_slug"]], "description": pool["description"]})
    pool_id = row["id"]
    items, allocations = [], []
    for spare in pool["spares"]:
        # A pre-racked cold spare: one serialized item matching its device by (type, serial).
        items.append({"pool": pool_id, "item_type": spare["item"][0],
                      "item_id": types[(spare["item"][0], spare["item"][2], spare["item"][3])],
                      "status": "serviceable", "quantity": 1, "serial": spare["serial"]})
    for allocation in pool["allocations"]:
        kind, _, maker_slug, model = allocation["item"]
        base = {"pool": pool_id, "item_type": kind, "item_id": types[(kind, maker_slug, model)]}
        if not pool["spares"] and allocation["serviceable"]:
            items.append({**base, "status": "serviceable", "quantity": allocation["serviceable"]})
        if allocation["damaged"]:
            items.append({**base, "status": "damaged", "quantity": allocation["damaged"],
                          "description": "Flagged damaged at the last inventory audit"})
        allocations.append({**base, "min_quantity": allocation["min_quantity"],
                            "max_quantity": allocation["max_quantity"]})
    # Bulk creates are one transaction each: a pool holds all of a kind or none.
    if items and not writer.rows(f"spare-items/?pool_id={pool_id}"):
        writer.send("POST", "spare-items/", items)
    if not writer.rows(f"spare-item-allocations/?pool_id={pool_id}"):
        writer.send("POST", "spare-item-allocations/", allocations)
    return {"pool": pool_id}


def _readback(writer, artifact, receipt, devices, sites, rooms, types):
    """Compare every emitted record against the live plugin rows exactly."""
    mismatches = []
    live = {path: {row["id"]: row for row in writer.rows(path)} for path in
            ("boms/", "purchase-orders/", "shipments/", "assets/", "bom-line-items/",
             "po-line-items/", "shipment-line-items/", "spares-pools/", "spare-items/",
             "spare-item-allocations/")}
    installed = 0
    for bom in artifact["boms"]:
        record = receipt["boms"][bom["name"]]
        row = live["boms/"].get(record["bom"])
        if row is None or row["name"] != bom["name"] or _value(row["status"]) != bom["target"]:
            mismatches.append(f"bom {bom['name']}")
            continue
        installs = {o["order_id"]: o["installs"] for o in bom["orders"]}
        assets = [a for a in live["assets/"].values() if _id(a["bom"]) == row["id"]]
        expected = devices[bom["name"]]
        for asset in assets:
            order_id = expected.get((asset["assigned_object_type"], asset["assigned_object_id"]))
            if order_id is None or bool(asset.get("installed")) != installs[order_id] or \
                    _id(asset.get("shipment")) != (record["shipments"].get(order_id) if installs[order_id] else None):
                mismatches.append(f"asset {asset['id']} on {bom['name']}")
            elif installs[order_id]:
                installed += 1
        if len(assets) != len(expected):
            mismatches.append(f"asset count on {bom['name']}")
        lines = {line["id"]: line for line in live["bom-line-items/"].values() if _id(line["bom"]) == row["id"]}
        for order in bom["orders"]:
            po = live["purchase-orders/"].get(record["purchase_orders"].get(order["order_id"]))
            if (po is None or _value(po["status"]) != order["target"] or po.get("order_id") != order["order_id"]
                    or _id(po["vendor"]) != receipt["vendors"][order["vendor"]]
                    or _value(po.get("currency")) != "USD"):
                mismatches.append(f"purchase order {order['order_id']}")
                continue
            wanted = _counts(order)
            got = Counter()
            for line in live["po-line-items/"].values():
                if _id(line["purchase_order"]) == po["id"]:
                    bom_line = lines.get(_id(line["bom_line_item"]))
                    if bom_line is None or line.get("unit_price") is not None:
                        mismatches.append(f"po line {line['id']}")
                    else:
                        got[_item(bom_line)] += line["qty_ordered"]
            if got != wanted:
                mismatches.append(f"po lines on {order['order_id']}")
            spec = order["shipment"]
            shipment_id = record["shipments"].get(order["order_id"])
            if spec is None:
                if shipment_id is not None or any(_id(r["purchase_order"]) == po["id"]
                                                   for r in live["shipments/"].values()):
                    mismatches.append(f"unexpected shipment on {order['order_id']}")
                continue
            shipment = live["shipments/"].get(shipment_id)
            if (shipment is None or _value(shipment["status"]) != "received"
                    or _id(shipment["purchase_order"]) != po["id"]
                    or _id(shipment["courier"]) != receipt["courier"]
                    or shipment["tracking_number"] != spec["tracking_number"]
                    or _id(shipment["site"]) != sites[bom["site_slug"]]
                    or _id(shipment["location"]) != rooms[bom["location_slug"]]
                    or any(shipment.get(f) != spec[f] for f in
                           ("date_shipped", "date_expected", "date_received"))):
                mismatches.append(f"shipment {spec['tracking_number']}")
                continue
            got = Counter()
            for line in live["shipment-line-items/"].values():
                if _id(line["shipment"]) == shipment["id"]:
                    bom_line = lines.get(_id(line["bom_line_item"]))
                    if bom_line is None or line["qty_received"] != line["qty_shipped"]:
                        mismatches.append(f"shipment line {line['id']}")
                    else:
                        got[_item(bom_line)] += line["qty_shipped"]
            if got != wanted:
                mismatches.append(f"shipment lines on {spec['tracking_number']}")
    for pool in artifact["pools"]:
        row = live["spares-pools/"].get(receipt["pools"][pool["name"]]["pool"])
        if (row is None or row["name"] != pool["name"] or _id(row["site"]) != sites[pool["site_slug"]]
                or _id(row["location"]) != rooms[pool["location_slug"]]):
            mismatches.append(f"pool {pool['name']}")
            continue
        stock = Counter()
        for item in live["spare-items/"].values():
            if _id(item["pool"]) == row["id"]:
                stock[(item["item_type"], item["item_id"], _value(item["status"]),
                       item.get("serial") or "")] += item["quantity"]
        allocations = {(a["item_type"], a["item_id"]): a for a in live["spare-item-allocations/"].values()
                       if _id(a["pool"]) == row["id"]}
        wanted_stock = Counter()
        for spare in pool["spares"]:
            kind, _, maker_slug, model = spare["item"]
            wanted_stock[(kind, types[(kind, maker_slug, model)], "serviceable", spare["serial"])] += 1
        for allocation in pool["allocations"]:
            kind, _, maker_slug, model = allocation["item"]
            ident = (kind, types[(kind, maker_slug, model)])
            if not pool["spares"]:
                wanted_stock[(*ident, "serviceable", "")] += allocation["serviceable"]
            wanted_stock[(*ident, "damaged", "")] += allocation["damaged"]
            live_allocation = allocations.pop(ident, None)
            if (live_allocation is None
                    or live_allocation["min_quantity"] != allocation["min_quantity"]
                    or live_allocation["max_quantity"] != allocation["max_quantity"]
                    or live_allocation.get("below_minimum") != allocation["below_minimum"]):
                mismatches.append(f"allocation {allocation['item'][3]} in {pool['name']}")
        if +stock != +wanted_stock or allocations:
            mismatches.append(f"stock in {pool['name']}")
    return {"mismatches": len(mismatches), "mismatch_sample": mismatches[:20],
            "boms": len(artifact["boms"]), "assets_installed": installed,
            "pools": len(artifact["pools"]), "read_at": _now()}


def _delete(client, path):
    """DELETE one plugin row; True when it is gone (a 404 means already gone)."""
    request = urllib.request.Request(client.base + API + path, headers=client.headers, method="DELETE")
    try:
        with client.opener.open(request, timeout=120):
            return True
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True
        raise LoadError(f"DELETE {API}{path} returned HTTP {exc.code}: "
                        f"{exc.read(2000).decode(errors='replace')}") from exc


def unseed(receipt_path, *, url, token):
    """Delete exactly the rows a seed receipt recorded, in protection order.

    Allocations and spare items protect a pool, pools protect their site and
    location, and BOM/PO children cascade with their parent, so: pool
    contents -> pools -> shipments -> purchase orders -> BOMs (assets and
    line items cascade) -> vendor accounts -> vendors -> courier. Rows the
    receipt does not name are never touched; rerunning is safe.
    """
    if os.environ.get("LIFECYCLE_WRITES", "").strip().lower() not in ("1", "true", "yes", "on"):
        raise LoadError("removing the procurement story requires LIFECYCLE_WRITES=1 in the environment")
    receipt = json.loads(Path(receipt_path).read_text())
    client = Client(url, token)
    if receipt.get("target") != client.base:
        raise LoadError(f"receipt {receipt_path} belongs to {receipt.get('target')}, not {client.base}")
    pools = [row["pool"] for row in receipt.get("pools", {}).values()]
    boms = list(receipt.get("boms", {}).values())
    deleted = Counter()
    for pool in pools:
        for path in ("spare-item-allocations/", "spare-items/"):
            for row in client.all(f"{API}{path}?pool_id={pool}"):
                deleted[path] += _delete(client, f"{path}{row['id']}/")
    plan = ([("spares-pools/", pool) for pool in pools]
            + [("shipments/", i) for bom in boms for i in bom.get("shipments", {}).values()]
            + [("purchase-orders/", i) for bom in boms for i in bom.get("purchase_orders", {}).values()]
            + [("boms/", bom["bom"]) for bom in boms]
            + [("vendor-accounts/", i) for i in receipt.get("vendor_accounts", {}).values()]
            + [("vendors/", i) for i in receipt.get("vendors", {}).values()]
            + ([("couriers/", receipt["courier"])] if receipt.get("courier") else []))
    for path, row_id in plan:
        deleted[path] += _delete(client, f"{path}{row_id}/")
    survivors = [f"{path}{row_id}" for path, row_id in plan
                 if client.request(f"{API}{path}?id={row_id}", branch=False)[1].get("count")]
    if survivors:
        raise LoadError(f"lifecycle rows survived removal: {survivors[:10]}")
    return {"deleted": dict(sorted(deleted.items())), "receipt": str(receipt_path), "success": True}


def default_receipt(artifact_dir, target):
    slug = Path(artifact_dir).name or "lifecycle"
    suffix = hashlib.sha256(f"{slug}\n{target.rstrip('/')}".encode()).hexdigest()[:12]
    return Path("build/load-receipts") / f"{slug}-lifecycle-{suffix}.json"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Derive, verify and seed a deterministic procurement story for Asset Lifecycle")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("build", help="derive the lifecycle artifact from a frozen plan")
    p.add_argument("plan", type=Path)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("check", help="recompute a saved lifecycle artifact from its bound plan")
    p.add_argument("directory", type=Path)
    p.add_argument("--plan", type=Path, required=True)
    p = sub.add_parser("seed", help="write the artifact through the plugin REST API (LIFECYCLE_WRITES=1)")
    p.add_argument("directory", type=Path)
    p.add_argument("target")
    p.add_argument("--receipt", type=Path)
    p = sub.add_parser("unseed", help="delete exactly the rows a seed receipt recorded (LIFECYCLE_WRITES=1)")
    p.add_argument("receipt", type=Path)
    p.add_argument("target")
    args = parser.parse_args(argv)
    try:
        if args.command in ("build", "check"):
            result = (build(args.plan, args.out) if args.command == "build"
                      else check(args.directory, args.plan))
            print(f"{result['artifact']}: {result['boms']} BOMs, {result['assets']} assets, "
                  f"{result['purchase_orders']} purchase orders, {result['shipments']} shipments, "
                  f"{result['spares_pools']} spares pools ({result['allocations']} allocations, "
                  f"{result['below_minimum']} below minimum, {result['damaged']} damaged); "
                  f"{result['on_order_boms']} BOMs on order, {result['cold_spares']} cold spares"
                  + (f" -> {result['output']}" if args.command == "build" else ""))
        elif args.command == "unseed":
            token = os.environ.get("NETBOX_TOKEN") or parser.error("NETBOX_TOKEN is required")
            print(json.dumps(unseed(args.receipt, url=args.target, token=token), sort_keys=True))
        else:
            token = os.environ.get("NETBOX_TOKEN")
            if not token:
                parser.error("NETBOX_TOKEN is required")
            receipt = args.receipt or default_receipt(args.directory, args.target)
            receipt.parent.mkdir(parents=True, exist_ok=True)
            print(f"Receipt: {receipt}", flush=True)
            print(json.dumps(seed(args.directory, url=args.target, token=token,
                                  receipt_path=receipt), sort_keys=True))
        return 0
    except (LifecycleError, LoadError, OSError, ValueError, KeyError) as exc:
        import sys
        print(f"Lifecycle failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
