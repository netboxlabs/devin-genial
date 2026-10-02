"""The frozen provider timeline (v0.18 lived-in carrier, DESIGN.md §4.1, K1).

One estate history for the provider backbone, from its founding to ``as_of``:
which PoP launched when, on what platform, when each generation was replaced,
when customers signed, and what is planned next. Every other module that
dates something about the provider plant reads it here; nothing re-derives
its own epoch.

ponytail: provider-only. Another profile earns its own call site later.

Frozen, append-only
-------------------
Dates that depend on the population (the launch burst schedule, the
complexity-ranked PE refresh, the onboarding S-curve) are written once, as
date ordinals, to the dated ledger: one reservation scope per event,
``provider-timeline/launch/<pop>``, ``provider-timeline/refresh/<pop>`` and
``provider-timeline/onboard/<customer>``, each ``{"day": ordinal}`` (the
``site-in-service/`` shape: reservation values must be unique per scope). Growth reads
them back and only appends: a PoP or customer that is new to an existing
ledger is a current-era event (launched / signed shortly before ``as_of``),
so it never re-dates, re-ranks or re-platforms an existing one. Everything
else is a pure function of those frozen dates, the recipe and stable
``World.choose`` variation. No recipe key selects history.

The ledger is dated local variation (it follows the seed), like
``site-in-service/``: a seed-invariance comparison of allocation ledgers must
exclude the ``provider-timeline/`` scopes too.

CONTRACT (WP-C services/records, WP-D validators, WP-E sidecars)
---------------------------------------------------------------
``tl = timeline.of(w)``: cached on the World; valid once
``provider-pop-order`` is reserved (``provider._generate`` does that before
the first ``_pop``). All dates are ``datetime.date``.

    tl.as_of, tl.founding                    estate dates
    tl.metro_entry[metro]                    first PoP launch per metro
    tl.launch[pop]                           PoP launch (the plant's install day)
    tl.metro[pop], tl.order                  metro per PoP; PoPs in launch order
    tl.founding_pop[metro]                   first PoP of each metro (IX host, cold spare)
    tl.tier[pop]                             "core" (cage R01+R02) | "edge" (one cabinet)
    tl.transit[pop]   -> "a" | "b" | None    transit handoff side (pop-order 0 -> a, 1 -> b)
    tl.noc[pop]       -> ("a",) ...          NOC handoff sides landing at this PoP
    tl.ix[pop]        -> bool                hosts its metro's IX port (PE-A xe-0/1/5)
    tl.dia[pop]       -> int                 DIA premises homed on this PoP
    tl.pe_at_launch[pop] -> "mx80" | "mx204"
    tl.refresh[pop]   -> date | None         MX80 -> MX204 cut-over (None: launched on MX204)
    tl.refresh_order  -> [pop]               complexity ascending; the most complex last
    tl.relic[pop]     -> bool                MX80 pair stays racked "decommissioning"
    tl.agg[pop]       -> "acx5048" | "acx5448m"   current aggregation model
    tl.agg_swap[pop]  -> date | None         original (not inventoried) AGG replaced by ACX5048
    tl.oob_swap[pop]  -> date | None         original console server replaced by EX3400 + OM2216-L
    tl.timing[pop]    -> date | None         Meinberg M300 install (NOC-handoff PoPs only)
    tl.spare[pop]     -> (model, date) | None   pre-racked cold-spare AGG (metro founding PoP, R02)
    tl.mx304[pop]     -> dict | None         planned successor pair:
                          ordered, received (date | None), status "planned" | "staged",
                          reasons (tuple of str: port exhaustion, TSB107750, DDoS offramp)
    tl.ddos[pop]      -> date | None         TMS racked ("staged", activation at MX304
                                             cut-over); only where tl.mx304 and transit and dia
    tl.onboard[customer] -> date             set by provider._onboarding (needs PoP readiness)
    tl.legacy(day) -> bool                   installed before the NS-2 naming standard

Plant devices carry their dates in meta (estates/fibre.py builds them):
    meta["installed"]    ISO install day (racked), every PoP plant device
    meta["timeline"]     dict(event, predecessor, change, ...) for history records
    meta["legacy_naming"] True on devices installed before NS-2 that still exist
Racks carry ``meta["removed"]``: [dict(label, units, text, installed, removed, change)].

Journal shape helpers (DESIGN.md P0-8), shared so every writer agrees:
    timeline.entry(title, day, body) -> markdown comments
    timeline.created(day, key, band)  -> ISO ``created`` (band "business" | "night")
    timeline.change(w, key, work="")  -> the operator's CHG number (as operations_context)
"""

from collections import Counter
from datetime import date, timedelta
import math

from .model import DesignError, digest


TIMELINE_LEDGER = "provider-timeline"
FOUNDING_YEARS = 15
QUIET_YEARS = 3                       # no new PoP in the last three calendar years plus this one

# Vendor facts (VERIFICATION.md §1), cited "per vendor notice" wherever quoted.
MX80_END_OF_SALE = date(2021, 6, 30)
MX80_END_OF_SUPPORT = date(2026, 6, 30)
ACX5048_LAST_ORDER = date(2022, 12, 31)
ACX5048_END_OF_SUPPORT = date(2027, 12, 31)
MX204_EOL_ANNOUNCED = date(2026, 6, 15)    # TSB107750
MX204_LAST_ORDER = date(2027, 6, 15)
MX204_END_OF_SUPPORT = date(2032, 6, 30)
MX204_TSB = "TSB107750"
# Authored milestones [A] (labelled "authored" wherever quoted).
MX204_AVAILABLE = date(2018, 1, 1)         # first orderable on our books (Junos 17.4R1 era)
ACX5048_AVAILABLE = date(2015, 1, 1)
ACX5448M_CUTOVER = date(2019, 7, 1)
MX304_AVAILABLE = date(2022, 7, 1)         # orderable 1H2022 per Juniper
NS2_ADOPTED = date(2018, 3, 1)
NOTICES_BEGIN = date(2025, 4, 1)
AGG_SWAP_START = date(2016, 3, 1)          # original aggregation -> ACX5048 programme
OOB_SWAP_START = date(2019, 4, 1)          # original console server -> EX3400 + OM2216-L
REFRESH_START, REFRESH_END = date(2019, 3, 1), date(2025, 6, 30)
RELIC_DAYS = 730                           # MX80 stays racked if cut over within 24 months
SFP_PLUS_PORTS = 8                         # MX204 xe-0/1/0-7
SFP_PLUS_BASE = 5                          # 4 LAG members + management uplink
BUSINESS_HOURS, NIGHT_HOURS = (14, 22), (4, 9)  # UTC, [start, end)


class _Ledger:
    """The dated ledger as one scope per event, ``provider-timeline/<event>/<subject>``
    holding {"day": ordinal}: reservation scopes need unique values, dates repeat."""

    def __init__(self, reservations):
        self.reservations = reservations

    def __contains__(self, key):
        return f"{TIMELINE_LEDGER}/{key}" in self.reservations

    def __getitem__(self, key):
        return self.reservations[f"{TIMELINE_LEDGER}/{key}"]["day"]

    def __setitem__(self, key, ordinal):
        self.reservations[f"{TIMELINE_LEDGER}/{key}"] = {"day": ordinal}

    def __iter__(self):
        prefix = f"{TIMELINE_LEDGER}/"
        return (scope.removeprefix(prefix) for scope in list(self.reservations) if scope.startswith(prefix))


def _years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


class Timeline:
    """Computed once per World; see the module contract."""

    def __init__(self, w):
        from .provider import premises  # provider imports this module; resolve late
        self.w, recipe = w, w.recipe
        self.as_of = date.fromisoformat(recipe["as_of"])
        self.founding = _years_before(self.as_of, FOUNDING_YEARS)
        self.quiet = date(self.as_of.year - QUIET_YEARS, 1, 1)
        if self.quiet - self.founding < timedelta(days=365 * 5):
            raise DesignError("as_of is too early for the authored provider history")
        position = w.reservations["provider-pop-order"]
        pops = sorted(recipe["pops"], key=lambda p: position[p["key"]])
        self.metro = {p["key"]: p["metro"] for p in pops}
        # Metro entry order: the founding metro (PoP order 0) first, then by
        # the demand homed there (premises), the business case for entering it.
        metro_of = {p["key"]: p["metro"] for p in pops}
        demand = Counter(metro_of[pop] for _, _, pop, _ in premises(recipe))
        metros = sorted(dict.fromkeys(p["metro"] for p in pops),
                        key=lambda m: (m != pops[0]["metro"], -demand[m], m))
        self.founding_pop = {m: next(p["key"] for p in pops if p["metro"] == m) for m in metros}
        self.ledger = _Ledger(w.reservations)
        self.launch = self._launches(pops, metros)
        self.order = sorted(self.launch, key=lambda p: (self.launch[p], position[p]))
        self.metro_entry = {m: min(self.launch[p] for p in self.launch if self.metro[p] == m) for m in metros}

        self.transit = {p: None for p in self.metro}
        for side, p in zip("ab", pops):
            self.transit[p["key"]] = side
        self.noc = {p: tuple(s for s in "ab" if recipe.get(f"noc_pop_{s}") == p) for p in self.metro}
        self.ix = {p: p in self.founding_pop.values() for p in self.metro}
        self.dia, self.attachments = {p: 0 for p in self.metro}, {p: 0 for p in self.metro}
        for _, customer, pop, _ in premises(recipe):
            self.attachments[pop] += 1
            self.dia[pop] += customer.get("service") == "dia"
        self.tier = {p: "core" if self.noc[p] or self.transit[p] or self.ix[p] else "edge" for p in self.metro}

        self.pe_at_launch = {p: "mx80" if d < MX204_AVAILABLE else "mx204" for p, d in self.launch.items()}
        self.refresh, self.refresh_order = self._refresh()
        self.relic = {p: d is not None and (self.as_of - d).days <= RELIC_DAYS for p, d in self.refresh.items()}
        self.agg = {p: "acx5048" if d < ACX5448M_CUTOVER else "acx5448m" for p, d in self.launch.items()}
        self.agg_swap = {p: self._after(p, "agg-swap", max(AGG_SWAP_START, d + timedelta(days=60)), 240)
                         if d < ACX5048_AVAILABLE else None for p, d in self.launch.items()}
        self.oob_swap = {p: self._after(p, "oob-swap", max(OOB_SWAP_START, d + timedelta(days=60)), 240)
                         if d < OOB_SWAP_START else None for p, d in self.launch.items()}
        self.timing = {p: self.launch[p] if self.noc[p] else None for p in self.metro}
        self.spare = {p: None for p in self.metro}
        for m, p in self.founding_pop.items():
            if self.agg[p] == "acx5048":
                # Bought before the 2022-12-31 last order date, per vendor notice.
                day = self._after(p, "cold-spare", max(date(2022, 3, 1), self.launch[p]), 240)
                day = min(day, ACX5048_LAST_ORDER - timedelta(days=14))
            else:
                day = self._after(p, "cold-spare", self.launch[p] + timedelta(days=120), 280)
            if day <= self.as_of - timedelta(days=30):
                self.spare[p] = (self.agg[p], day)
        self.mx304 = {p: self._mx304(p) for p in self.metro}
        self.ddos = {p: (min(self.as_of - timedelta(days=3), self._after(p, "ddos-racked", self.mx304[p]["ordered"], 26, 20))
                         if self.mx304[p] and self.transit[p] and self.dia[p] else None) for p in self.metro}
        self.onboard = {}

    # -- helpers ---------------------------------------------------------
    def _after(self, key, label, day, spread, minimum=0):
        return day + timedelta(days=self.w.choose(key, f"timeline-{label}", range(minimum, minimum + spread)))

    def _frozen(self, key, derive):
        if key not in self.ledger:
            self.ledger[key] = derive().toordinal()
        return date.fromordinal(self.ledger[key])

    def legacy(self, day):
        return day < NS2_ADOPTED

    # -- launches ----------------------------------------------------------
    def _launches(self, pops, metros):
        """Metro-entry bursts on a fresh ledger; current-era launches for appended PoPs."""
        fresh = not any(k.startswith("launch/") for k in self.ledger)
        span = (self.quiet - self.founding).days
        result, previous = {}, {}
        for p in pops:
            key, metro = p["key"], p["metro"]
            if not fresh and f"launch/{key}" not in self.ledger:
                derive = lambda key=key: self.as_of - timedelta(days=self.w.choose(key, "timeline-launch", range(45, 151)))
            elif metro not in previous:
                index = metros.index(metro)
                entry = self.founding + timedelta(days=span * index // len(metros))
                derive = lambda key=key, entry=entry: self._after(key, "metro-entry", entry, 45)
            else:
                last = previous[metro]
                def derive(key=key, last=last):
                    day = self._after(key, "launch-gap", last, 426, 183)  # 6-20 months
                    ceiling = self.quiet - timedelta(days=30)
                    return day if day < ceiling else last + (ceiling - last) // 2
            result[key] = previous[metro] = self._frozen(f"launch/{key}", derive)
        return result

    # -- PE refresh ----------------------------------------------------------
    def complexity(self, pop):
        """Refresh ranking: carrier handoffs on the PE pair, then attachments, then PoP order."""
        handoffs = len(self.noc[pop]) + (self.transit[pop] is not None) + self.ix[pop]
        return (handoffs, self.attachments[pop], self.w.reservations["provider-pop-order"][pop])

    def _refresh(self):
        mx80 = [p for p, kind in self.pe_at_launch.items() if kind == "mx80"]
        fresh = [p for p in mx80 if f"refresh/{p}" not in self.ledger]
        if fresh:
            if len(fresh) != len(mx80):
                raise DesignError("provider-timeline: an MX80-era PoP is missing its frozen refresh date; rebaseline")
            end = min(REFRESH_END, self.as_of - timedelta(days=120))
            if end <= REFRESH_START:
                raise DesignError("as_of is too early for the authored MX80 -> MX204 refresh programme")
            ranked = sorted(mx80, key=self.complexity)
            for i, p in enumerate(ranked):
                fraction = i / (len(ranked) - 1) if len(ranked) > 1 else 1
                day = REFRESH_START + timedelta(days=round((end - REFRESH_START).days * fraction))
                day = max(REFRESH_START, day - timedelta(days=self.w.choose(p, "timeline-refresh", range(0, 21))))
                self.ledger[f"refresh/{p}"] = max(day, self.launch[p] + timedelta(days=365)).toordinal()
        refresh = {p: date.fromordinal(self.ledger[f"refresh/{p}"]) if f"refresh/{p}" in self.ledger else None
                   for p in self.metro}
        return refresh, sorted(mx80, key=lambda p: (refresh[p], p))

    # -- planned successor -----------------------------------------------------
    def sfp_plus_used(self, pop, side):
        """MX204 SFP+ positions a PE uses: LAG x4 and the management uplink, then handoffs."""
        return (SFP_PLUS_BASE + (side in self.noc[pop]) + (self.transit[pop] == side)
                + (self.ix[pop] and side == "a"))

    def _mx304(self, pop):
        exhausted = [s for s in "ab" if self.sfp_plus_used(pop, s) >= SFP_PLUS_PORTS]
        if not exhausted:
            return None
        ordered = self._after(pop, "mx304-ordered", max(MX204_EOL_ANNOUNCED, MX304_AVAILABLE), 30, 10)
        if ordered > self.as_of - timedelta(days=3):
            return None
        received = self._after(pop, "mx304-received", ordered, 31, 60)
        received = received if received <= self.as_of else None
        reasons = (f"PE-{'/'.join(s.upper() for s in exhausted)} SFP+ positions exhausted ({SFP_PLUS_PORTS}/{SFP_PLUS_PORTS})",
                   f"MX204 end of life announced {MX204_EOL_ANNOUNCED} ({MX204_TSB}), last order {MX204_LAST_ORDER}, "
                   f"end of support {MX204_END_OF_SUPPORT}, per vendor notice",
                   "DDoS mitigation offramp needs 100G positions beyond the MX204's three active 100G ports")
        return dict(ordered=ordered, received=received, status="staged" if received else "planned", reasons=reasons)


def of(w):
    """The World's timeline, computed once."""
    tl = getattr(w, "provider_timeline", None)
    if tl is None:
        tl = w.provider_timeline = Timeline(w)
    return tl


def onboarding(w, customers, slots, ready, anchor):
    """Customer signing days: a slot-ordered S-curve on a fresh ledger, frozen.

    From founding + 1 year to ``as_of`` - 7 days, dense in the middle (the
    raised-cosine quantile peaks at about 1.6x uniform, so no year holds much
    more than a tenth of a fifteen-year book); never before the anchor PoP is
    ready + 30 days. A customer new to an existing ledger signs in the last few
    months. ``ready`` maps a PoP to its ready day; ``anchor(c)`` names the PoP.
    """
    tl = of(w)
    start, end = tl.founding + timedelta(days=365), tl.as_of - timedelta(days=7)
    ordered = sorted(customers, key=lambda c: slots[c["key"]])
    fresh = not any(k.startswith("onboard/") for k in tl.ledger)
    for i, (c, floor) in enumerate((c, ready[anchor(c)] + timedelta(days=30)) for c in ordered):
        key = c["key"]
        if fresh:
            u = (i + 0.5) / len(ordered)
            # Half S-curve, half uniform: dense mid-book, peak ~1.3x a uniform year.
            day = start + timedelta(days=round((end - start).days * (u + math.acos(1 - 2 * u) / math.pi) / 2))
            day += timedelta(days=w.choose(key, "timeline-onboard", range(-20, 21)))
        elif f"onboard/{key}" not in tl.ledger:
            day = tl.as_of - timedelta(days=w.choose(key, "timeline-onboard-late", range(14, 121)))
        tl.onboard[key] = tl._frozen(f"onboard/{key}", lambda: min(max(day, floor), end))
    return tl.onboard


# -- journal shape (DESIGN.md P0-8) ------------------------------------------
def entry(title, day, body):
    """Markdown journal comments: bold title and date, a blank line, the body."""
    return f"**{title}** · {day}\n\n{body}"


def created(day, key, band="business"):
    """A deterministic ``created`` instant on ``day``: business hours or the night window (UTC)."""
    low, high = BUSINESS_HOURS if band == "business" else NIGHT_HOURS
    n = int(digest(["journal-time", key]), 16)
    return f"{day}T{low + n % (high - low):02}:{(n // 97) % 60:02}:00Z"


def change(w, key, work=""):
    """The operator's change ticket for one subject's work; the operations_context formula."""
    return f"CHG{w.choose(key, 'journal-change' + work, range(1000000, 10000000)):07d}"
