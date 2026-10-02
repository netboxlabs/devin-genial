"""Catalog-bound PoE and installed-optics upstream AC planning allowances.

PD load follows the actual copper path. It is counted once at the serving PSE,
not as a second wall-power feed on the AP. Independent validation checks actual
installed supplies and paths; these allocations do not establish measured draw.

This module also owns the final roll-up onto each PDU's own input port, because
that sum is only correct once every member inlet carries its final allowance.
"""

from collections import defaultdict
from decimal import Decimal, ROUND_CEILING

from .model import DesignError
from .networking import _physical_peers


def planning_budget(hardware):
    """Available PD milliwatts after the catalog's planned supply loss."""
    policy = hardware.get("poe_pse", {})
    supplies = len(hardware.get("power_ports", [])) - policy.get("planning_supply_losses", 0)
    return policy.get("budget_by_active_supplies_mw", {}).get(str(supplies), 0)


def aggregate_pdu_inputs(world):
    """Roll each PDU's downstream inlet allocations onto its own input port.

    A PDU input left empty makes every upstream feed compute zero utilisation,
    so the whole power chain reads 0 W. Mirrors the member semantics exactly:
    ``allocated_draw`` totals the normal per-port splits actually cabled to this
    PDU, ``maximum_draw`` totals their full device allowances, so the input
    still reserves single-feed failover for the devices it serves.

    Must run last: ``enrich`` above rewrites member inlet draws with their PoE
    and optics allowances, and an earlier sum would undercount both.
    """
    objects = world.objects
    peers = {}
    for obj in objects.values():
        if obj["kind"] == "cable" and obj["attrs"].get("type") == "power":
            a, b = obj["refs"]["a"], obj["refs"]["b"]
            peers[a], peers[b] = b, a
    totals = defaultdict(lambda: [0, 0])
    for outlet in objects.values():
        if outlet["kind"] != "power_outlet":
            continue
        inlet = outlet["refs"].get("power_port")
        load = objects.get(peers.get(outlet["key"]), {})
        if objects.get(inlet, {}).get("kind") != "power_port" or load.get("kind") != "power_port":
            continue  # Spare outlets carry no load; a broken link is an independent finding.
        totals[inlet][0] += load["attrs"].get("allocated_draw") or 0
        totals[inlet][1] += load["attrs"].get("maximum_draw") or 0
    for inlet, (normal, failover) in totals.items():
        if not normal:
            continue  # Native PowerPort draws are positive integers or unset.
        objects[inlet]["attrs"].update(allocated_draw=normal, maximum_draw=failover,
            description="Sum of the connected inlet budgets; maximum covers single-feed failover")


def enrich(world):
    """Recompute chassis plus independently rounded PD and optical reserves."""
    from .blocks import PLANNED_WATTS

    models = world.catalog["models"]
    objects, loads = world.objects, defaultdict(int)
    peers = _physical_peers(objects)
    for device in list(objects.values()):
        if device["kind"] != "device":
            continue
        hardware = models[device["refs"]["device_type"].removeprefix("hardware/")]
        pd = hardware.get("poe_pd")
        if not pd:
            continue
        port = f"{device['key']}/if/{pd['interface']}"
        source = objects.get(peers.get(port), {})
        pse = objects.get(source.get("refs", {}).get("device"), {})
        source_model = models.get(pse.get("refs", {}).get("device_type", "").removeprefix("hardware/"), {})
        policy = source_model.get("poe_pse", {})
        reservation = pd["pse_reservation_mw"]
        if (source.get("kind") != "interface" or source.get("attrs", {}).get("name") not in source_model.get("access_ports", [])
                or policy.get("type") != pd["required_type"] or reservation > policy.get("per_port_max_mw", 0)):
            raise DesignError(f"{port}: powered device requires a compatible connected copper PSE access port")
        loads[pse["key"]] += reservation
    optical_loads = defaultdict(int)
    optics = world.catalog.get("optics", {})
    optical_parts = {(part["manufacturer"], part["model"]): part
                     for part in optics.get("parts", {}).values()}
    for module in objects.values():
        if module["kind"] != "module":
            continue
        module_type = objects.get(module["refs"].get("module_type"), {})
        manufacturer = objects.get(module_type.get("refs", {}).get("manufacturer"), {})
        part = optical_parts.get((manufacturer.get("attrs", {}).get("name"), module_type.get("attrs", {}).get("model")))
        if part is None:
            continue  # PSU inventory is checked separately; it is not an optical load.
        owner = module["refs"].get("device")
        if (module_type.get("kind") != "module_type" or manufacturer.get("kind") != "manufacturer" or
                objects.get(owner, {}).get("kind") != "device" or type(part["power_reservation_mw"]) is not int or
                part["power_reservation_mw"] <= 0):
            raise DesignError(f"{module['key']}: installed optical power requires a typed owner and positive catalog reservation")
        # Installed inventory reserves power even when inactive or disconnected.
        optical_loads[owner] += part["power_reservation_mw"]
    power_contracts = {entry["device"]: entry for contract in world.contracts
                       for entry in contract.get("power_redundancy", [])}
    for key in loads.keys() | optical_loads.keys():
        milliwatts = loads.get(key, 0)
        device = objects[key]
        if device["attrs"].get("status", "active") != "active":
            continue  # a staged, planned or decommissioning chassis is unpowered: nothing to budget
        alias = device["refs"]["device_type"].removeprefix("hardware/")
        hardware = models[alias]
        if milliwatts > planning_budget(hardware):
            raise DesignError(f"{key}: {milliwatts} mW PoE reservation exceeds {planning_budget(hardware)} mW supply-loss planning budget; add access capacity")
        extra = (int((Decimal(milliwatts) * Decimal(hardware["poe_pse"]["upstream_ac_allowance_multiplier"]) / 1000).to_integral_value(rounding=ROUND_CEILING))
                 if milliwatts else 0)
        optical_extra = (int((Decimal(optical_loads[key]) * Decimal(optics["upstream_ac_allowance_multiplier"]) / 1000).to_integral_value(rounding=ROUND_CEILING))
                         if optical_loads.get(key) else 0)
        allowance = PLANNED_WATTS[alias] + extra + optical_extra
        ports = hardware["power_ports"]
        # Equipment on customer power (every inlet mark_connected, such as a
        # provider NID in a customer MPOE) budgets its draw with no PDU contract.
        customer_power = bool(ports) and all(objects[f"{key}/power/{port['name']}"]["attrs"].get("mark_connected") for port in ports)
        if not ports or (key not in power_contracts and not customer_power):
            raise DesignError(f"{key}: installed component load needs actual infrastructure inlets and a power contract")
        description = ("Budgeted draw: chassis and PoE; each supply sized for the full load"
                       if not optical_extra else
                       "Budgeted draw: chassis, optics" + (" and PoE" if extra else "") +
                       "; each supply sized for the full load")
        quotient, remainder = divmod(allowance, len(ports))
        for index, port in enumerate(ports):
            inlet = objects[f"{key}/power/{port['name']}"]
            inlet["attrs"].update(allocated_draw=quotient + (index < remainder), maximum_draw=allowance,
                description=description + ("; customer-provided power" if customer_power else ""))
        device["meta"]["planned_watts"] = allowance
        if key in power_contracts:
            power_contracts[key]["planned_watts"] = allowance
    for contract in world.contracts:
        if any(entry["device"] in optical_loads for entry in contract.get("power_redundancy", [])) and "assumptions" in contract:
            contract["assumptions"] = [text.replace(
                "plus connected PD reservations with a 1.25 upstream AC allowance",
                "plus separately rounded connected PD and installed optical module/end reservations, each with a 1.25 upstream AC allowance")
                for text in contract["assumptions"]]
    aggregate_pdu_inputs(world)
