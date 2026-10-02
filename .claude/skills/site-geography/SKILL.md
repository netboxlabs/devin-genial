---
name: site-geography
description: Add or change a metro, neighbourhood anchor or street run in estates/places.py without spending hours on geocoder rate limits — offline test first, verify only what changed, reuse cached OpenStreetMap results.
---

# Site geography

Since v0.16 every site sits at a real anchor (`places.ANCHORS`: neighbourhood /
suburb centres and straight runs of real streets), nudged by at most ~390 × 370 m
from a hash of the site id. `tests/test_places.py` proves every anchor's offset
box and every `profiles/*.toml` site is on land, using committed offshore
water/Canada outlines — it is fully offline. See docs/modeling.md "Site geography".

## The time trap (2026-10-01: 1.5 hours)

The code change was small; verification was not. OpenStreetMap Nominatim allows
~1 request/second and Overpass times out under load, and a full pass over every
anchor corner is ~1,000 lookups. Three full passes ate the evening.

Do this instead:

1. **Change anchors, run `python3 -m unittest tests.test_places`.** Offline, seconds.
   If it passes, positions are on land in the right metro.
2. **Verify only what you touched.** The one-off scripts accept anchor names:
   `python3 build/geo-verify/nominatim.py "Schaumburg" "Oak Creek"` (reverse-geocodes
   the centre and box corners; checks road, town, state, country). They are kept
   under ignored `build/geo-verify/` with caches (`ways.json` street geometry,
   `coast.json` shorelines); copy them back to `build/geo/` (the scripts' cache
   path) before running so cached results are reused. If the directory is gone,
   rewrite the ~60-line checker rather than re-running a full sweep.
3. **Cache every response keyed by query** and never re-request a cached key;
   sleep ≥1.1 s between Nominatim calls and send a real User-Agent (policy).
4. **Run long verification in the background** and keep working; never block an
   integration on a full-corner sweep — centre + water checks are enough to ship,
   corners are a follow-up.

## Rules that stay

- Anchor tables are rebaseline-frozen like other name pools: adding an in-city
  neighbourhood moves every unnamed site that hashes onto it.
- A named site goes where its name says; North/South/East/West map to a real
  on-land neighbourhood on that side.
- Cite sources for new anchors (OSM is fine); keep coordinates synthetic-precision,
  never a real customer's address.
