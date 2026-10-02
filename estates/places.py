"""Authored Great Lakes geography and finite, fictional building layouts.

Time-zone labels follow IANA tzdb northamerica and zone1970.tab:
https://data.iana.org/time-zones/tzdb/northamerica
https://data.iana.org/time-zones/tzdb/zone1970.tab
Street addresses, rooms, dimensions and routes are design inputs, not surveyed
properties. Authored naming emits metro-centered synthetic coordinate offsets
for the map view — never a claim about a real premises location — and no
RF/electrical compliance is invented.
"""

import hashlib
import math

from .model import DesignError
from .naming import _ACRONYMS


METROS = (
    ("Chicago", "IL", "Illinois", "America/Chicago", 41.8781, -87.6298),
    ("Detroit", "MI", "Michigan", "America/Detroit", 42.3314, -83.0458),
    ("Cleveland", "OH", "Ohio", "America/New_York", 41.4993, -81.6944),
    ("Milwaukee", "WI", "Wisconsin", "America/Chicago", 43.0389, -87.9065),
)
# Authored display-name pools ("naming = 'authored'"). Streets are real metro
# geography; every composed site name stays a fictional design assumption.
# Every entry must name an ANCHORS row of its metro (tests pin that), so a site
# named after a street or campus is plotted on it.
STREETS = {
    "Chicago": ("Wabash", "Halsted", "Clark", "Ashland", "Damen", "Kedzie", "Montrose", "Archer"),
    "Detroit": ("Woodward", "Gratiot", "Cass", "Livernois", "Vernor", "Bagley", "Grand River", "Mack"),
    "Cleveland": ("Euclid", "Broadway", "Lorain", "Prospect", "Carnegie", "Woodland", "Fleet", "Denison"),
    "Milwaukee": ("Brady", "Kilbourn", "Wells", "Vliet", "Locust", "Greenfield", "Mitchell", "Burleigh"),
}
# Data-centre, distribution and office campus names: real industrial or
# carrier-hotel areas where one exists (Elk Grove Village and Franklin Park are
# Chicago's suburban data-centre belt; 350 E Cermak is its carrier hotel).
CAMPUSES = {
    "Chicago": ("Elk Grove", "Cermak", "Franklin Park", "Northlake"),
    "Detroit": ("Corktown", "Southfield", "Highland Park", "New Center"),
    "Cleveland": ("Flats East", "Midtown", "Independence", "University Circle"),
    "Milwaukee": ("Third Ward", "Menomonee Valley", "Walkers Point", "Bay View"),
}
# Authored map anchors: (names, locality, lat, lon[, end_lat, end_lon]).
# Points are real neighbourhood/suburb centroids, nudged inland where the real
# centroid sits within ~1 km of the lake or river; a six-tuple is a street run
# and a site is placed along it. Every point, segment end and its full jitter box
# was reverse-geocoded against OpenStreetMap Nominatim (2026-10-01) to a road in
# the stated locality, state and country — see docs/modeling.md#site-geography.
# A site whose display name mentions a name (last mention wins, then the longest)
# sits on that anchor; any other site takes a hashed in-city point anchor. The
# address locality follows the anchor. Order is rebaseline-frozen: appending an
# in-city point changes every hashed pick.
ANCHORS = {
    "Chicago": (
        (("Loop", "Downtown", "Central"), "Chicago", 41.8800, -87.6320),
        (("Fulton Market",), "Chicago", 41.8866, -87.6517),
        (("Pilsen",), "Chicago", 41.8566, -87.6600),
        (("Cermak",), "Chicago", 41.8530, -87.6250),
        (("Ravenswood",), "Chicago", 41.9686, -87.6742),
        (("Bridgeport",), "Chicago", 41.8381, -87.6513),
        (("Logan Square",), "Chicago", 41.9234, -87.7083),
        (("Wicker Park",), "Chicago", 41.9088, -87.6796),
        (("Garfield Park", "West Side", "West"), "Chicago", 41.8810, -87.7200),
        (("Bronzeville",), "Chicago", 41.8169, -87.6180),
        (("Hyde Park",), "Chicago", 41.7990, -87.6000),
        (("Lincoln Square", "North Side", "North"), "Chicago", 41.9759, -87.6890),
        (("Chatham", "South Side", "South"), "Chicago", 41.7409, -87.6127),
        (("Lakeview",), "Chicago", 41.9400, -87.6560),
        (("Portage Park",), "Chicago", 41.9580, -87.7650),
        (("Little Village",), "Chicago", 41.8445, -87.7130),
        (("Austin",), "Chicago", 41.8940, -87.7650),
        (("Elk Grove", "Elk Grove Village"), "Elk Grove Village", 42.0000, -87.9700),
        (("Franklin Park",), "Franklin Park", 41.9353, -87.8656),
        (("Northlake",), "Northlake", 41.9170, -87.8956),
        (("Oak Park",), "Oak Park", 41.8850, -87.7845),
        (("Schaumburg",), "Schaumburg", 42.0334, -88.0750),
        (("Skokie",), "Skokie", 42.0324, -87.7416),
        (("Wabash",), "Chicago", 41.7898, -87.6240, 41.8850, -87.6263),
        (("Halsted",), "Chicago", 41.7600, -87.6443, 41.9400, -87.6493),
        (("Clark",), "Chicago", 41.9200, -87.6373, 41.9672, -87.6672),
        (("Ashland",), "Chicago", 41.7799, -87.6642, 41.9876, -87.6698),
        (("Damen",), "Chicago", 41.7902, -87.6742, 41.9906, -87.6798),
        (("Kedzie",), "Chicago", 41.7800, -87.7032, 41.9698, -87.7085),
        (("Montrose",), "Chicago", 41.9606, -87.7600, 41.9617, -87.6603),
        (("Archer",), "Chicago", 41.8522, -87.6363, 41.8006, -87.7306),
    ),
    "Detroit": (
        (("Downtown", "Central"), "Detroit", 42.3365, -83.0507),
        (("Corktown",), "Detroit", 42.3315, -83.0700),
        (("Eastern Market",), "Detroit", 42.3480, -83.0410),
        (("New Center",), "Detroit", 42.3696, -83.0753),
        (("Midtown",), "Detroit", 42.3510, -83.0640),
        (("Southwest Detroit", "Southwest", "South"), "Detroit", 42.3180, -83.1100),
        (("Palmer Park", "North"), "Detroit", 42.4285, -83.1200),
        (("Rosedale Park", "West"), "Detroit", 42.3995, -83.2172),
        (("East English Village", "East"), "Detroit", 42.3990, -82.9450),
        (("Boston Edison",), "Detroit", 42.3830, -83.0920),
        (("Woodbridge",), "Detroit", 42.3530, -83.0790),
        (("Mexicantown",), "Detroit", 42.3215, -83.0900),
        (("Highland Park",), "Highland Park", 42.4056, -83.0969),
        (("Dearborn",), "Dearborn", 42.3223, -83.1763),
        (("Southfield",), "Southfield", 42.4734, -83.2219),
        (("Troy",), "Troy", 42.6064, -83.1498),
        (("Warren",), "Warren", 42.5145, -83.0147),
        (("Livonia",), "Livonia", 42.3684, -83.3527),
        (("Royal Oak",), "Royal Oak", 42.4895, -83.1446),
        (("Novi",), "Novi", 42.4806, -83.4800),
        (("Woodward",), "Detroit", 42.3401, -83.0530, 42.3850, -83.0832),
        (("Gratiot",), "Detroit", 42.3395, -83.0419, 42.4360, -82.9771),
        (("Cass",), "Detroit", 42.3348, -83.0546, 42.3698, -83.0749),
        (("Livernois",), "Detroit", 42.3506, -83.1347, 42.4399, -83.1418),
        (("Vernor",), "Detroit", 42.3105, -83.1295, 42.3297, -83.0781),
        (("Bagley",), "Detroit", 42.3203, -83.0935, 42.3243, -83.0818),
        (("Grand River",), "Detroit", 42.3378, -83.0625, 42.3922, -83.1973),
        (("Mack",), "Detroit", 42.3522, -83.0454, 42.3863, -82.9543),
    ),
    "Cleveland": (
        (("Downtown", "Central"), "Cleveland", 41.4960, -81.6860),
        (("Flats East", "Flats"), "Cleveland", 41.4885, -81.6905),
        (("Midtown",), "Cleveland", 41.5035, -81.6500),
        (("University Circle",), "Cleveland", 41.5085, -81.6050),
        (("Ohio City",), "Cleveland", 41.4815, -81.7060),
        (("Tremont",), "Cleveland", 41.4750, -81.6890),
        (("Slavic Village", "South"), "Cleveland", 41.4500, -81.6450),
        (("West Park", "West"), "Cleveland", 41.4420, -81.7950),
        (("Buckeye Shaker", "East"), "Cleveland", 41.4830, -81.5950),
        (("Old Brooklyn",), "Cleveland", 41.4350, -81.7000),
        (("Fairfax",), "Cleveland", 41.4950, -81.6250),
        (("Lakewood",), "Lakewood", 41.4790, -81.8000),
        (("Westlake",), "Westlake", 41.4553, -81.9179),
        (("Independence",), "Independence", 41.3787, -81.6385),
        (("Parma",), "Parma", 41.4048, -81.7229),
        (("Beachwood",), "Beachwood", 41.4645, -81.5087),
        (("Brooklyn",), "Brooklyn", 41.4398, -81.7354),
        (("Shaker Heights",), "Shaker Heights", 41.4739, -81.5370),
        (("Euclid",), "Cleveland", 41.5004, -81.6849, 41.5055, -81.6098),
        (("Broadway",), "Cleveland", 41.4909, -81.6778, 41.4511, -81.6304),
        (("Lorain",), "Cleveland", 41.4812, -81.7112, 41.4535, -81.7987),
        (("Prospect",), "Cleveland", 41.4987, -81.6878, 41.5025, -81.6551),
        (("Carnegie",), "Cleveland", 41.4986, -81.6759, 41.5011, -81.6152),
        (("Woodland",), "Cleveland", 41.4914, -81.6681, 41.4885, -81.6021),
        (("Fleet",), "Cleveland", 41.4563, -81.6500, 41.4579, -81.6359),
        (("Denison",), "Cleveland", 41.4667, -81.7542, 41.4507, -81.7016),
    ),
    "Milwaukee": (
        (("Downtown", "Central"), "Milwaukee", 43.0400, -87.9170),
        (("Third Ward",), "Milwaukee", 43.0320, -87.9110),
        (("Menomonee Valley", "Menomonee"), "Milwaukee", 43.0300, -87.9450),
        (("Walkers Point",), "Milwaukee", 43.0230, -87.9180),
        (("Bay View",), "Milwaukee", 42.9920, -87.9000),
        (("East Side", "East"), "Milwaukee", 43.0650, -87.8920),
        (("Sherman Park", "North"), "Milwaukee", 43.0750, -87.9650),
        (("Washington Heights", "West"), "Milwaukee", 43.0510, -87.9800),
        (("Lincoln Village", "South"), "Milwaukee", 43.0000, -87.9250),
        (("Riverwest",), "Milwaukee", 43.0700, -87.8990),
        (("Harambee",), "Milwaukee", 43.0680, -87.9150),
        (("Clarke Square",), "Milwaukee", 43.0220, -87.9420),
        (("West Allis",), "West Allis", 43.0167, -88.0070),
        (("Wauwatosa",), "Wauwatosa", 43.0495, -88.0076),
        (("Oak Creek",), "Oak Creek", 42.8840, -87.9120),
        (("Brookfield",), "Brookfield", 43.0606, -88.1065),
        (("Glendale",), "Glendale", 43.1353, -87.9356),
        (("Brady",), "Milwaukee", 43.0530, -87.9043, 43.0529, -87.8939),
        (("Kilbourn",), "Milwaukee", 43.0417, -87.9598, 43.0425, -87.9080),
        (("Wells",), "Milwaukee", 43.0398, -87.9800, 43.0410, -87.9101),
        (("Vliet",), "Milwaukee", 43.0500, -87.9800, 43.0486, -87.9300),
        (("Locust",), "Milwaukee", 43.0716, -87.9700, 43.0711, -87.8950),
        (("Greenfield",), "Milwaukee", 43.0170, -87.9500, 43.0171, -87.9252),
        (("Mitchell",), "Milwaukee", 43.0124, -87.9400, 43.0123, -87.9153),
        (("Burleigh",), "Milwaukee", 43.0753, -88.0002, 43.0748, -87.9101),
    ),
}
# Street-address pools, keyed (locality, first anchor name). A street run is
# addressed on its own street; a point anchor uses real streets inside its
# neighbourhood or suburb — the road OpenStreetMap Nominatim reverse-geocoded at
# the anchor centre (build/geo-verify, 2026-10-01) plus well-known main streets
# checked the same way. A site hashes its id onto one street and a house number;
# inside Chicago a directional street takes its grid number from the site's own
# coordinate (800 numbers a mile from State and Madison), so the number agrees
# with the map pin. Numbers are synthetic: never a surveyed or real premises.
ADDRESS_STREETS = {
    ("Chicago", "Loop"): ("South LaSalle Street", "West Adams Street"),
    ("Chicago", "Fulton Market"): ("West Fulton Market", "West Lake Street"),
    ("Chicago", "Pilsen"): ("West 18th Street", "West 18th Place"),
    ("Chicago", "Cermak"): ("East Cermak Road", "South Indiana Avenue"),
    ("Chicago", "Ravenswood"): ("North Ravenswood Avenue",),
    ("Chicago", "Bridgeport"): ("South Halsted Street", "South Keeley Street"),
    ("Chicago", "Logan Square"): ("West Belden Avenue", "North Milwaukee Avenue"),
    ("Chicago", "Wicker Park"): ("North Hoyne Avenue", "North Damen Avenue"),
    ("Chicago", "Garfield Park"): ("West Madison Street",),
    ("Chicago", "Bronzeville"): ("East 43rd Street",),
    ("Chicago", "Hyde Park"): ("South Woodlawn Avenue", "South Greenwood Avenue"),
    ("Chicago", "Lincoln Square"): ("West Foster Avenue",),
    ("Chicago", "Chatham"): ("South Vernon Avenue",),
    ("Chicago", "Lakeview"): ("West Belmont Avenue",),
    ("Chicago", "Portage Park"): ("West Hutchinson Street",),
    ("Chicago", "Little Village"): ("West 26th Street",),
    ("Chicago", "Austin"): ("North Central Avenue",),
    ("Elk Grove Village", "Elk Grove"): ("Arthur Avenue", "Morse Avenue"),
    ("Franklin Park", "Franklin Park"): ("Franklin Avenue",),
    ("Northlake", "Northlake"): ("East Dickens Avenue",),
    ("Oak Park", "Oak Park"): ("South Ridgeland Avenue",),
    ("Schaumburg", "Schaumburg"): ("North Pleasant Drive",),
    ("Skokie", "Skokie"): ("Kolmar Avenue",),
    ("Chicago", "Wabash"): ("South Wabash Avenue",),
    ("Chicago", "Halsted"): ("South Halsted Street",),
    ("Chicago", "Clark"): ("North Clark Street",),
    ("Chicago", "Ashland"): ("South Ashland Avenue",),
    ("Chicago", "Damen"): ("South Damen Avenue",),
    ("Chicago", "Kedzie"): ("South Kedzie Avenue",),
    ("Chicago", "Montrose"): ("West Montrose Avenue",),
    ("Chicago", "Archer"): ("South Archer Avenue",),
    ("Detroit", "Downtown"): ("Woodward Avenue",),
    ("Detroit", "Corktown"): ("Michigan Avenue",),
    ("Detroit", "Eastern Market"): ("Alfred Street",),
    ("Detroit", "New Center"): ("West Grand Boulevard",),
    ("Detroit", "Midtown"): ("Cass Avenue",),
    ("Detroit", "Southwest Detroit"): ("Bivouac Street",),
    ("Detroit", "Palmer Park"): ("West 7 Mile Road",),
    ("Detroit", "Rosedale Park"): ("Archdale Street",),
    ("Detroit", "East English Village"): ("Chatsworth Street",),
    ("Detroit", "Boston Edison"): ("Chicago Boulevard",),
    ("Detroit", "Woodbridge"): ("Trumbull Street",),
    ("Detroit", "Mexicantown"): ("Bagley Street",),
    ("Highland Park", "Highland Park"): ("Gerald Street",),
    ("Dearborn", "Dearborn"): ("Michigan Avenue",),
    ("Southfield", "Southfield"): ("West 10 Mile Road",),
    ("Troy", "Troy"): ("Livernois Road",),
    ("Warren", "Warren"): ("Campbell Avenue",),
    ("Livonia", "Livonia"): ("Merriman Road",),
    ("Royal Oak", "Royal Oak"): ("South Main Street",),
    ("Novi", "Novi"): ("Novi Road",),
    ("Detroit", "Woodward"): ("Woodward Avenue",),
    ("Detroit", "Gratiot"): ("Gratiot Avenue",),
    ("Detroit", "Cass"): ("Cass Avenue",),
    ("Detroit", "Livernois"): ("Livernois Avenue",),
    ("Detroit", "Vernor"): ("West Vernor Highway",),
    ("Detroit", "Bagley"): ("Bagley Street",),
    ("Detroit", "Grand River"): ("Grand River Avenue",),
    ("Detroit", "Mack"): ("Mack Avenue",),
    ("Cleveland", "Downtown"): ("Eagle Avenue",),
    ("Cleveland", "Flats East"): ("West 3rd Street",),
    ("Cleveland", "Midtown"): ("Euclid Avenue",),
    ("Cleveland", "University Circle"): ("Mayfield Road",),
    ("Cleveland", "Ohio City"): ("Chatham Avenue",),
    ("Cleveland", "Tremont"): ("West 11th Street",),
    ("Cleveland", "Slavic Village"): ("Warsaw Avenue",),
    ("Cleveland", "West Park"): ("West 144th Street",),
    ("Cleveland", "Buckeye Shaker"): ("East 126th Street",),
    ("Cleveland", "Old Brooklyn"): ("Tate Avenue",),
    ("Cleveland", "Fairfax"): ("East 88th Street",),
    ("Lakewood", "Lakewood"): ("Warren Road",),
    ("Westlake", "Westlake"): ("Beechwood Drive",),
    ("Independence", "Independence"): ("Brecksville Road",),
    ("Parma", "Parma"): ("Snow Road",),
    ("Beachwood", "Beachwood"): ("Chagrin Boulevard",),
    ("Brooklyn", "Brooklyn"): ("Ridge Road",),
    ("Shaker Heights", "Shaker Heights"): ("South Woodland Road",),
    ("Cleveland", "Euclid"): ("Euclid Avenue",),
    ("Cleveland", "Broadway"): ("Broadway Avenue",),
    ("Cleveland", "Lorain"): ("Lorain Avenue",),
    ("Cleveland", "Prospect"): ("Prospect Avenue East",),
    ("Cleveland", "Carnegie"): ("Carnegie Avenue",),
    ("Cleveland", "Woodland"): ("Woodland Avenue",),
    ("Cleveland", "Fleet"): ("Fleet Avenue",),
    ("Cleveland", "Denison"): ("Denison Avenue",),
    ("Milwaukee", "Downtown"): ("West Wells Street",),
    ("Milwaukee", "Third Ward"): ("North Broadway",),
    ("Milwaukee", "Menomonee Valley"): ("West Canal Street",),
    ("Milwaukee", "Walkers Point"): ("West National Avenue",),
    ("Milwaukee", "Bay View"): ("South Kinnickinnic Avenue",),
    ("Milwaukee", "East Side"): ("North Newhall Street",),
    ("Milwaukee", "Sherman Park"): ("West Burleigh Street",),
    ("Milwaukee", "Washington Heights"): ("North 53rd Street",),
    ("Milwaukee", "Lincoln Village"): ("South 10th Street",),
    ("Milwaukee", "Riverwest"): ("North Weil Street",),
    ("Milwaukee", "Harambee"): ("West Center Street",),
    ("Milwaukee", "Clarke Square"): ("South 23rd Street",),
    ("West Allis", "West Allis"): ("West Greenfield Avenue",),
    ("Wauwatosa", "Wauwatosa"): ("Harwood Avenue",),
    ("Oak Creek", "Oak Creek"): ("South Howell Avenue",),
    ("Brookfield", "Brookfield"): ("West North Avenue",),
    ("Glendale", "Glendale"): ("West Brantwood Avenue",),
    ("Milwaukee", "Brady"): ("East Brady Street",),
    ("Milwaukee", "Kilbourn"): ("West Kilbourn Avenue",),
    ("Milwaukee", "Wells"): ("West Wells Street",),
    ("Milwaukee", "Vliet"): ("West Vliet Street",),
    ("Milwaukee", "Locust"): ("West Locust Street",),
    ("Milwaukee", "Greenfield"): ("West Greenfield Avenue",),
    ("Milwaukee", "Mitchell"): ("West Mitchell Street",),
    ("Milwaukee", "Burleigh"): ("West Burleigh Street",),
}
# Chicago's address grid: zero at State Street and Madison Street, 800 numbers
# to the mile (55,200 per degree of latitude; 41,200 per degree of longitude
# at 41.9 degrees north).
CHICAGO_GRID = (41.8819, -87.6278, 55200, 41200)
# Jitter half-widths in degrees (~390 m north-south, ~370 m east-west at these
# latitudes): separates sites sharing an anchor without leaving its area.
JITTER_LAT, JITTER_LON = 0.0035, 0.0045
LOCALITIES = {anchor[1]: metro for metro, anchors in ANCHORS.items() for anchor in anchors}
GROUPS = {"branch": "Retail branches", "hq": "Headquarters", "dc": "Data centers", "school": "Schools",
          "hospital": "Hospitals", "clinic": "Outpatient clinics", "pop": "Provider PoPs", "customer": "Customer premises",
          "store": "Retail stores", "distribution": "Distribution centers",
          "academic": "Academic buildings", "residence": "Residence halls", "library": "Libraries",
          "office": "Managed customer offices", "plant": "Manufacturing plants",
          "substation": "Substations"}
MAX_CHANNEL_M = 80
# Site kinds whose authored grammar is a single ground floor (or one data hall):
# their rooms hang directly from the site, with no pass-through building/floor.
FLAT_KINDS = {"branch", "store", "distribution", "office", "plant", "substation", "customer", "pop", "dc"}
FLOOR_HEIGHT_M = 4
OFFICE_DESKS = 12
# Authored university building grammar. Eight rooms per academic or library
# floor, fifty residence rooms per floor, and eight floors per building: the
# same ceiling the shared eight-block management uplink pool supports.
ACADEMIC_ROOMS_PER_FLOOR = 8
DORM_ROOMS_PER_FLOOR = 50
LAB_SEATS_PER_ROOM = 24
READING_SEATS_PER_ROOM = 24
MAX_BUILDING_FLOORS = 8
# Authored managed-office grammar: one floor, a reception and up to four staff
# pods of twelve desk positions each. A larger customer premises needs a
# reviewed riser and closet layout before demand can exceed it.
MAX_OFFICE_PODS = 4
# Authored manufacturing-plant grammar: one ground floor holding a reception,
# office pods, warehouse loading docks and production line cells, all served by
# the single plant equipment room. Every position is reserved permanently and
# every bay sits inside the 80 m copper ceiling measured from that room.
MAX_PLANT_LINES, MAX_PLANT_DOCKS, MAX_PLANT_PODS = 12, 12, 8
# Authored utility-substation grammar: one control house on one ground floor
# holding a control room and the switchyard bay positions it serves, all cabled
# from the single control-house equipment room. Every bay position is reserved
# permanently and sits inside the 80 m copper ceiling measured from that room.
MAX_SUBSTATION_BAYS = 16
SPACE_DESCRIPTIONS = {
    "building": "Banking and business operations premises",
    "floor": "Customer or staff floor with assigned equipment-room service",
    "equipment_room": "Restricted network equipment and cable termination",
    "suite": "Leased carrier-hotel suite holding the provider cage",
    "atm_lobby": "Public self-service banking lobby",
    "teller_hall": "Customer-facing teller and service counters",
    "office": "Private staff work area with twelve desk positions",
    "reception": "Visitor reception and building entrance",
    "classroom": "Teaching room with declared student seats and a teacher position",
    "computer_lab": "Shared student computer lab",
    "patient_room": "Two bed stations with networked monitoring endpoints",
    "nurse_station": "Clinical workstation area supporting the assigned ward",
    "exam_room": "Outpatient examination room with an installed clinical workstation",
    "imaging_room": "Imaging modality and diagnostic workstation",
    "corridor": "Circulation space with access-point and security-camera locations",
    "sales_floor": "Customer sales floor with point-of-sale lane positions",
    "back_office": "Store back office with staff workstation positions",
    "stockroom": "Receiving and stock storage area",
    "warehouse_floor": "Distribution warehouse floor with handheld scanner positions",
    "shipping_dock": "Shipping and receiving dock",
    "lecture_hall": "Teaching room with an installed instructor position and coverage radio",
    "teaching_lab": "Instructional and research computing lab with installed workstation seats",
    "dorm_room": "Residence room with installed wired data ports",
    "reading_room": "Library reading area with installed study workstation positions",
    "staff_office": "Customer staff office pod with installed workstation positions",
    "production_line": "Production line cell with installed controller, operator-panel and field-device positions",
    "loading_dock": "Warehouse loading dock with installed scanner-station positions",
    "control_room": "Substation control-house room with installed station HMI, gateway and corporate desk positions",
    "switchyard_bay": "Switchyard bay position with an installed remote terminal unit and protection relay",
}
# Operational room notes. Modeling limitations (RF, clinical, OT, utility)
# belong in docs/modeling.md and the report, never on the record.
SPACE_COMMENTS = {
    "computer_lab": "Lab seats are shared across classes.",
}


def _site_display(site):
    """The emitted display name (authored, overridden, or legacy)."""
    return site.w.obj(site.key)["attrs"]["name"]


def _ordinal(number):
    if 10 <= number % 100 <= 20:
        return f"{number}th"
    return f"{number}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th') }"


def endpoint_label(cohort):
    """Sentence-case operational label: 'classroom-ap' -> 'Classroom AP'."""
    text = " ".join(_ACRONYMS.get(word, word) for word in cohort.split("-")).replace("point of sale", "point-of-sale")
    return text[:1].upper() + text[1:]


def _describe(site, node, space, cohort, detail=""):
    node["attrs"]["description"] = (f"{endpoint_label(cohort)} in {space['attrs']['name']} at {_site_display(site)}"
                                    + (f"; {detail}" if detail else ""))


def _authored_identity(site, city):
    """Deterministic, unique-by-construction display name for one site.

    Every template embeds a component that is unique within its kind (the
    branch ordinal, the user-authored campus/PoP/customer key, or the fixed
    DC metro), so growth never renames an existing site and collisions cannot
    depend on generation order. Workspace.finish() still enforces global
    uniqueness as the belt.
    """
    w, sid, kind = site.w, site.id, site.contract["kind"]
    profile = w.recipe["profile"]
    # Pool picks hash the site id alone (not the seed): display names embed in
    # journals and descriptions estate-wide, and the declared seed variation
    # stays bounded to serials, dates and design-pool choices.
    pick = lambda pool: pool[int.from_bytes(
        hashlib.sha256(f"display/{sid}".encode()).digest()[:4], "big") % len(pool)]
    if kind == "branch":
        # The address-allocation slot is unique across every site and stable
        # under growth, so street & number can never collide or get renamed.
        # "and", not "&": report tables HTML-escape ampersands, and every name
        # must appear verbatim in the rendered report.
        return f"{pick(STREETS[city])} and {_ordinal(getattr(w, 'allocations', {}).get(sid, 0))} Branch"
    if kind == "store":
        # Same construction as a branch: the address-allocation slot is unique
        # across every site and frozen by growth, so no store can be renamed.
        return f"{pick(STREETS[city])} and {_ordinal(getattr(w, 'allocations', {}).get(sid, 0))} Store"
    if kind == "distribution":
        # The di- ordinal is unique among distribution centers by construction,
        # so two same-metro campuses cannot resolve to one display name.
        return f"{pick(CAMPUSES[city])} Distribution Center {int(sid.rsplit('-', 1)[-1] or 0):02}"
    if kind in {"academic", "residence"}:
        # Building keys are unique within the recipe and frozen by growth, and
        # the two kinds use different suffixes, so one key cannot name two sites.
        title = sid.split("-", 1)[1].replace("-", " ").title()
        return f"{title} {'Hall' if kind == 'academic' else 'House'}"
    if kind == "library":
        return f"{_campus_anchor(w, city)[0][0]} Library"
    if kind == "office":
        # The customer key and its office ordinal are unique by construction and
        # frozen by growth, so two managed offices can never resolve to one name.
        stem, ordinal = sid.removeprefix("off-").rsplit("-", 1)
        return f"{stem.replace('-', ' ').title()} {pick(CAMPUSES[city])} Office {int(ordinal):02}"
    if kind == "plant":
        # Plant keys are unique within the recipe and frozen by growth, so one
        # key can never name two plants.
        title = sid.split("-", 1)[1].replace("-", " ").title()
        return f"{title} Plant"
    if kind == "substation":
        # Substation keys are unique within the recipe and frozen by growth, so
        # one key can never name two substations.
        title = sid.split("-", 1)[1].replace("-", " ").title()
        return f"{title} Substation"
    if kind == "hq":
        return f"{city} Headquarters"
    if kind == "dc":
        if profile == "provider-backbone":
            return f"{city} NOC Campus"
        if profile == "university-campus":
            return f"{city} Campus Data Center"
        if profile == "msp":
            return f"{pick(CAMPUSES[city])} Operations Center"
        if profile == "utility":
            return f"{pick(CAMPUSES[city])} Control Center"
        return f"{pick(CAMPUSES[city])} Data Center"
    if kind in {"school", "hospital", "clinic"}:
        title = sid.split("-", 1)[1].replace("-", " ").title()
        return f"{title} {'School' if kind == 'school' else kind.title()}"
    if kind == "pop":
        # Never drop tokens: PoP keys are globally unique, so full-token
        # titles are too (chicago-east and cleveland-east must not both
        # become "East Exchange").
        tokens = sid.removeprefix("pop-").split("-")
        return f"{' '.join(token.title() for token in tokens)} Exchange"
    if kind == "customer":
        base = sid.removeprefix("ce-")
        stem, ordinal = base.rsplit("-", 1)
        pop_keys = sorted((p.removeprefix("pop-") for p in getattr(w, "provider_metros", {})
                           if p.startswith("pop-")), key=len, reverse=True)
        pop_title = ""
        for pop_key in pop_keys:
            if stem.endswith("-" + pop_key):
                stem = stem.removesuffix("-" + pop_key)
                pop_title = " ".join(token.title() for token in pop_key.split("-"))
                break
        customer = stem.replace("-", " ").title()
        return " ".join(part for part in (customer, "-", pop_title, f"{int(ordinal):02}") if part)
    return f"{sid.replace('-', ' ').title()} Site"


def _words(text):
    return " " + " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split()) + " "


def _campus_anchor(w, city):
    """One university campus is one in-city neighbourhood; every building shares it."""
    return w.choose("campus", "anchor", [a for a in ANCHORS[city] if len(a) == 4 and a[1] == city])


def _anchor(site, city, name):
    """The authored map anchor a site's display name implies, else a hashed in-city one."""
    w, anchors = site.w, ANCHORS[city]
    if w.recipe["profile"] == "university-campus":
        return _campus_anchor(w, city)
    text = _words(name)
    hits = [(text.rfind(_words(alias)), len(alias), anchor)
            for anchor in anchors for alias in anchor[0] if _words(alias) in text]
    if hits:
        return max(hits, key=lambda hit: hit[:2])[2]
    if site.contract["kind"] == "hq":
        return anchors[0]
    general = [a for a in anchors if len(a) == 4 and a[1] == city]
    return general[int.from_bytes(hashlib.sha256(f"anchor/{site.id}".encode()).digest()[:4], "big") % len(general)]


def street_address(site_id, anchor, latitude, longitude):
    """Street line for one site: a real street at its anchor, a synthetic number.

    The street and number hash the site id alone, so growth and seeds never
    readdress a site. Inside Chicago a North/South street is numbered from the
    site's latitude and an East/West street from its longitude on the city grid.
    """
    pool = ADDRESS_STREETS[(anchor[1], anchor[0][0])]
    digest = hashlib.sha256(f"address/{site_id}".encode()).digest()
    street = pool[digest[0] % len(pool)]
    direction, _, rest = street.partition(" ")
    if anchor[1] == "Chicago" and direction in {"North", "South", "East", "West"}:
        lat0, lon0, per_lat, per_lon = CHICAGO_GRID
        north_south = direction in {"North", "South"}
        offset = (latitude - lat0) * per_lat if north_south else (longitude - lon0) * per_lon
        direction = ("North" if offset >= 0 else "South") if north_south else ("East" if offset >= 0 else "West")
        # Even or odd side of the street is the only hashed part of the number.
        return f"{max(1, int(abs(offset)) // 2 * 2 + digest[1] % 2)} {direction} {rest}"
    return f"{100 + int.from_bytes(digest[1:3], 'big') % 9800} {street}"


def clli_place(locality, state_code):
    """Six-character CLLI-style place: city initial plus its next three consonants, then state."""
    letters = locality.upper().replace(" ", "")
    return (letters[0] + "".join(c for c in letters[1:] if c not in "AEIOU") + letters[1:])[:4] + state_code


def _clli_building(w, site_id, locality):
    """Two-letter building code from the PoP's own place words ('new-center' -> 'NC').

    None when the words only repeat the municipality ('lakewood' in Lakewood):
    the caller then numbers the building instead of stuttering the place code.
    """
    if not site_id.startswith("pop-"):
        return "NC"  # the provider's own NOC building
    words = site_id.removeprefix("pop-").split("-")
    if len(words) > 1 and words[0] == w.provider_metros[site_id]:
        words = words[1:]
    if "".join(words) == locality.lower().replace(" ", ""):
        return None
    if len(words) > 1:
        return (words[0][0] + words[1][0]).upper()
    word = words[0].upper()
    return word[0] + next((c for c in word[1:] if c not in "AEIOU"), word[-1])


def _facility(site, city, state_code, locality):
    """Per-metro site code, or a CLLI-style code for the provider's own buildings.

    Both ride permanent ledgers in creation order, so growth appends a code and
    never renumbers an existing site.
    """
    w = site.w
    if w.recipe["profile"] == "provider-backbone" and site.contract["kind"] in {"pop", "dc"}:
        place = clli_place(locality, state_code)
        scope = f"clli/{place}"
        w.reserve(scope, site.id, 100)
        taken = set()
        # A clash with an earlier building in the same place takes its slot
        # number instead; letters and digits never collide.
        for other, slot in sorted(w.reservations[scope].items(), key=lambda item: item[1]):
            code = _clli_building(w, other, locality)
            code = f"{slot + 1:02}" if code is None or code in taken else code
            if other == site.id:
                return place + code
            taken.add(code)
    # One estate-wide ledger (its scope must not name a seed-chosen metro);
    # the number counts only the earlier entries in the same metro.
    # ponytail: O(sites^2) recount per site; fine at the reviewed few thousand
    # sites. Cache per-metro counts on the World if a profile grows past that.
    slot = w.reserve("facility-codes", site.id, 10000)
    number = 1 + sum(1 for other, earlier in w.reservations["facility-codes"].items()
                     if earlier < slot and _metro(w, other)[0] == city)
    return f"{city[:3].upper()}{number:02}"


def _display_site(site, node, row):
    """Apply the naming policy: authored identity, overrides, facility, geo.

    Returns the address locality and street line: the anchor's municipality and
    one of its real streets when authored, otherwise the metro city and no
    street (legacy naming emits no coordinates).
    """
    w = site.w
    city, state_code = row[0], row[1]
    authored = w.recipe.get("naming", "authored") == "authored"
    name = _authored_identity(site, city) if authored else node["attrs"]["name"]
    override = w.recipe.get("site_names", {}).get(site.id) or {}
    if override:
        name = override.get("name", name)
        w.consumed_site_names.add(site.id)
    node["attrs"]["name"] = name
    if not authored:
        if override.get("facility"):
            node["attrs"]["facility"] = override["facility"]
        return city, None
    # Anchor-relative synthetic positions: on land in the named neighbourhood,
    # suburb or street run, never a claim about a real premises. The site id
    # alone drives the offset, so growth and seeds cannot move a site.
    anchor = _anchor(site, city, name)
    jitter = hashlib.sha256(f"geo/{site.id}".encode()).digest()
    along = jitter[2] / 255
    start, end = anchor[2:4], anchor[-2:]
    latitude = round(start[0] + along * (end[0] - start[0]) + (jitter[0] / 255 - 0.5) * 2 * JITTER_LAT, 6)
    longitude = round(start[1] + along * (end[1] - start[1]) + (jitter[1] / 255 - 0.5) * 2 * JITTER_LON, 6)
    node["attrs"].update(latitude=latitude, longitude=longitude,
                         facility=override.get("facility", _facility(site, city, state_code, anchor[1])))
    return anchor[1], street_address(site.id, anchor, latitude, longitude)


def foundation(w, *, site_kinds=None):
    """Publish namespaced shared geography before any site references it."""
    ns = w.recipe["namespace"]
    root = f"region/{ns}/us"
    lakes = f"{root}/great-lakes"
    for key, name, slug, parent in ((root, "United States", "us", None),
                                    (lakes, "Great Lakes", "great-lakes", root)):
        w.add("region", key, {"name": name, "slug": f"{ns}-{slug}"},
              {"parent": parent} if parent else {})
    for _, code, state, _, _, _ in METROS:
        w.add("region", f"{root}/{code.lower()}",
              {"name": state, "slug": f"{ns}-{code.lower()}"}, {"parent": lakes})
    for kind, name in GROUPS.items():
        if site_kinds is None and kind in {"school", "hospital", "clinic", "pop", "customer",
                                           "store", "distribution", "academic", "residence", "library",
                                           "office", "plant", "substation"}:
            continue
        if site_kinds is not None and kind not in site_kinds:
            continue
        w.add("site_group", f"site-group/{ns}/{kind}",
              {"name": name, "slug": f"{ns}-{kind}"})


def _location(site, suffix, name, space_type, floor, position, parent=None, capacity=None):
    key = f"location/{site.id}" + (f"/{suffix}" if suffix else "")
    refs = {"site": site.key, "tenant": site.tenant}
    if parent:
        refs["parent"] = parent
    meta = {"space_type": space_type, "floor": floor, "position_m": list(position)}
    if capacity:
        meta["capacity"] = capacity
    description = SPACE_DESCRIPTIONS[space_type]
    if site.w.recipe["profile"] != "regional-bank" and space_type in {"building", "floor"}:
        description = "Network, compute and facilities building" if space_type == "building" else "Equipment and facilities floor"
        if site.w.recipe["profile"] == "school-district":
            description = "Education and district services building" if space_type == "building" else "Teaching, staff and equipment floor"
        elif site.w.recipe["profile"] == "hospital-clinics":
            description = "Care and health-system services building" if space_type == "building" else "Care, clinical staff and equipment floor"
        elif site.w.recipe["profile"] == "university-campus":
            description = "Campus teaching, residential and services building" if space_type == "building" else "Teaching, residential and equipment floor"
        elif site.w.recipe["profile"] == "msp":
            description = "Managed office or operations building" if space_type == "building" else "Staff and equipment floor"
        elif site.w.recipe["profile"] == "manufacturing":
            description = ("Production, warehouse and office building" if space_type == "building"
                           else "Production, warehouse, office and equipment floor")
        elif site.w.recipe["profile"] == "utility":
            description = ("Control house, operations and services building" if space_type == "building"
                           else "Control, equipment and facilities floor")
    if site.contract["kind"] == "dc" and space_type == "equipment_room":
        description = "Restricted data hall with compute, network and power distribution"
    attrs = {"name": name, "slug": f"{site.name}-{suffix.replace('/', '-') or 'equipment'}", "status": "active",
             "description": description}
    if space_type in SPACE_COMMENTS:
        attrs["comments"] = SPACE_COMMENTS[space_type]
    site.w.add("location", key, attrs, refs, meta)
    return key


def _floor(site, number):
    """The floor location rooms on ``number`` hang from; None on a flat site."""
    if site.contract["kind"] in FLAT_KINDS:
        if number != 1:
            raise DesignError(f"{site.id}: {site.contract['kind']} premises have one authored floor")
        return None
    key = f"location/{site.id}/floor-{number:02}"
    if key not in site.w.objects:
        _location(site, f"floor-{number:02}", f"Floor {number:02}", "floor", number,
                  (0, 0, (number - 1) * FLOOR_HEIGHT_M), f"location/{site.id}/building")
    return key


def _metro(w, site_id):
    """A site's metro row; site IDs, unlike a global enumeration or RNG, survive growth."""
    profile = w.recipe["profile"]
    if profile == "provider-backbone":
        return next(row for row in METROS if row[0].lower() == w.provider_metros[site_id])
    if profile == "school-district":
        return METROS[w.choose("district", "metro", range(len(METROS)))]
    if profile == "hospital-clinics":
        return METROS[w.choose("health-system", "metro", range(len(METROS)))]
    if profile == "university-campus":
        # One campus, one metro: every building, hall, library and the campus
        # DC share it. This is a single-site estate, not a multi-metro fleet.
        return METROS[w.choose("campus", "metro", range(len(METROS)))]
    if site_id in {"dc-01", "dc-02"}:
        return METROS[int(site_id[-2:]) - 1]
    return METROS[int.from_bytes(hashlib.sha256(site_id.encode()).digest()[:4], "big") % len(METROS)]


def _legacy_street(site):
    """The pre-0.16 sequential street line, kept only for ``naming = "legacy"``."""
    w, kind = site.w, site.contract["kind"]
    suffix = "".join(character for character in site.id if character.isdigit())
    number = 100 + 4 * int(suffix or "0")
    if w.recipe["profile"] == "provider-backbone" or kind in {
            "school", "hospital", "clinic", "store", "distribution", "academic", "residence",
            "library", "office", "plant", "substation"}:
        number = 100 + 4 * w.allocations[site.id]
    streets = {"br-s": "Market Street", "br-m": "Commerce Drive", "br-l": "Harbor Avenue",
               "hq": "Lakefront Boulevard", "dc": "Technology Way", "school-": "Learning Way",
               "hospital-": "Care Avenue", "clinic-": "Community Way",
               "st-s": "Market Square", "st-m": "Retail Parkway", "st-l": "Galleria Drive",
               "di-": "Distribution Parkway", "bldg-": "University Quadrangle",
               "hall-": "Residence Row", "library-": "Library Green",
               "noc-": "Operations Parkway", "off-": "Enterprise Parkway",
               "pl-": "Industrial Parkway", "sub-": "Switchyard Road",
               "pop-": "Exchange Avenue", "ce-": "Business Way"}
    return f"{number} {next((name for prefix, name in streets.items() if site.id.startswith(prefix)), 'Commerce Way')}"


def carrier_suite(site_id):
    """Authored carrier-hotel suite and cage names for one provider PoP.

    Both hash the site id alone, so growth never renames a PoP's space.
    """
    digest = hashlib.sha256(f"suite/{site_id}".encode()).digest()
    return (f"Suite {2 + digest[0] % 8}{10 + digest[1] % 40:02}",
            f"Cage {'ABCDEFGH'[digest[2] % 8]}{1 + digest[3] % 24:02}")


def locate(site):
    """Place an existing site and retain location/<site id> for its equipment.

    Site kinds whose authored grammar is one ground floor (FLAT_KINDS) place
    their rooms directly under the site: a single building and floor would only
    be pass-through levels. A provider PoP is a leased carrier-hotel suite
    holding the provider's cage. Multi-floor kinds keep building and floors.
    """
    w, kind = site.w, site.contract["kind"]
    if kind not in GROUPS:
        raise DesignError(f"{site.id}: no building layout for site kind {kind!r}")
    row = _metro(w, site.id)
    city, state_code, state, zone, _, _ = row
    node = w.obj(site.key)
    locality, street = _display_site(site, node, row)
    node["attrs"].update(time_zone=zone, physical_address=(
        f"{street or _legacy_street(site)}\n{locality}, {state}\nUnited States"))
    node["refs"].update(region=f"region/{w.recipe['namespace']}/us/{state_code.lower()}",
                        group=f"site-group/{w.recipe['namespace']}/{kind}")
    node["meta"]["geography"] = {"country": "US", "state": state_code, "city": city, "synthetic": True}
    building, parent, name = None, None, "Data hall" if kind == "dc" else "MDF"
    if kind == "pop":
        suite, name = carrier_suite(site.id)
        parent = _location(site, "suite", suite, "suite", 1, (0, 0, 0))
    elif kind not in FLAT_KINDS:
        building = _location(site, "building", "Main building", "building", 0, (0, 0, 0))
        parent = _floor(site, 1)
    equipment = _location(site, "", name, "equipment_room", 1, (24, 18, 0), parent)
    site.equipment_location = equipment
    site.contract["placement"] = {"equipment_location": equipment, "building": building,
                                  "equipment_locations": {"1": equipment},
                                  "max_access_channel_m": MAX_CHANNEL_M, "floor_height_m": FLOOR_HEIGHT_M}
    site.contract["assumptions"].append(
        "Metro and time-zone names are real; addresses, premises and local metre routes are authored. "
        "Carrier span routes and duct diversity are unknown." if w.recipe["profile"] == "provider-backbone" else
        "Geography names and time zones are real; premises and room geometry are synthetic. "
        "Access routes use local metres and an authored 80 m channel ceiling, not a surveyed cabling or RF design.")
    return equipment


def provider_office(site):
    return _location(site,"office-01","Customer office pod","office",1,(8,18,0),None,{"workstations":12})


def provider_endpoint(site,key,room,ordinal):
    """Twelve fixed desk positions; growing inventory leaves earlier routes intact."""
    if type(ordinal) is not int or not 1 <= ordinal <= 12:
        raise DesignError(f"{site.id}: customer office supports twelve wired desks")
    slot = ordinal-1
    point = [10+2*(slot%4),19+2*(slot//4),0.8]
    route = math.ceil(sum(abs(a-b) for a,b in zip(point,(24,18,0)))+10)
    node = site.w.obj(key)
    node["refs"]["location"] = room
    node["attrs"]["description"] = f"Customer office workstation {ordinal:03}"
    node["meta"].update(placement=dict(room=room,function="office",floor=1,position_m=point,cable_origin=site.equipment_location),
                        access_channel_length_m=route,cohort="customer-office")


def arrange(site, demand):
    """Grow office pods from demand without moving earlier pods or desks.

    Retail uses four permanent teller positions, then 12-desk private office
    pods on one floor. HQ uses four pods per floor and a local equipment room
    on each occupied floor, with fiber risers back to the MDF.
    ponytail: the finite footprint holds eight retail pods or four HQ floors;
    extend the reviewed layout before admitting larger per-building demand.
    """
    kind = site.contract["kind"]
    if kind not in {"branch", "hq"}:
        raise DesignError(f"{site.id}: endpoint rooms require a branch or headquarters")
    for field in ("workstations", "atms", "aps", "cameras"):
        if type(demand.get(field)) is not int or demand[field] < 0:
            raise DesignError(f"{site.id}: {field} must be a nonnegative integer")
    teller_desks = min(4, demand["workstations"]) if kind == "branch" else 0
    office_count = math.ceil((demand["workstations"] - teller_desks) / OFFICE_DESKS)
    maximum = 8 if kind == "branch" else 16
    if office_count > maximum or demand["atms"] > 8:
        raise DesignError(f"{site.id}: endpoint demand exceeds the authored building footprint; extend its layout")
    ground = _floor(site, 1)
    reception = kind == "hq" and demand["atms"] == 0
    lobby = _location(site, "reception" if reception else "atm-lobby",
                      "Reception" if reception else "ATM lobby", "reception" if reception else "atm_lobby", 1, (6, 6, 0), ground,
                      {"atms": 8})
    teller = None
    if kind == "branch":
        teller = _location(site, "teller-hall", "Teller hall", "teller_hall", 1, (24, 6, 0), ground,
                           {"workstations": 4})
    offices = []
    for index in range(office_count):
        floor = index // 4 + 1 if kind == "hq" else 1
        column, row = index % 4, 0 if kind == "hq" else index // 4
        offices.append(_location(site, f"office-{index+1:02}", f"Private office pod {index+1:02}",
                                  "office", floor, (8 + 12 * column, 18 + 12 * row, (floor - 1) * FLOOR_HEIGHT_M),
                                  _floor(site, floor), {"workstations": OFFICE_DESKS}))
    if kind == "hq":
        for floor in range(2, math.ceil(office_count / 4) + 1):
            equipment = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room",
                                  floor, (24, 18, (floor - 1) * FLOOR_HEIGHT_M), _floor(site, floor))
            site.contract["placement"]["equipment_locations"][str(floor)] = equipment
    site.places = {"lobby": lobby, "teller": teller, "offices": offices,
                   "teller_desks": teller_desks, "demand": dict(demand)}


def place_endpoint(site, key, role, ordinal):
    """Assign a 1-based endpoint ordinal to an actual room and bounded route."""
    fields = {"workstation": "workstations", "atm": "atms", "ap": "aps", "camera": "cameras"}
    if role not in fields or type(ordinal) is not int or not 1 <= ordinal <= site.places["demand"][fields[role]]:
        raise DesignError(f"{site.id}: invalid {role} endpoint ordinal {ordinal!r}")
    rooms = site.places
    hq = site.contract["kind"] == "hq"
    index = ordinal - 1
    if role == "atm":
        room, slot, purpose = rooms["lobby"], index, "Public self-service ATM"
    elif role == "workstation":
        if index < rooms["teller_desks"]:
            room, slot, purpose = rooms["teller"], index, "Teller customer-service workstation"
        else:
            office, slot = divmod(index - rooms["teller_desks"], OFFICE_DESKS)
            room, purpose = rooms["offices"][office], "Staff workstation"
    else:
        purpose = "Ceiling access point" if role == "ap" else "Security camera"
        slot = index
        if hq:
            if role == "camera" and index == 0:
                room = rooms["lobby"]
            else:
                office = index if role == "ap" else (index // 2) * 4
                if office >= len(rooms["offices"]):
                    raise DesignError(f"{site.id}: {role} {ordinal} has no occupied office zone; review endpoint demand")
                room = rooms["offices"][office]
        elif index < (2 if role == "ap" else 4):
            room = rooms["lobby"] if index < (1 if role == "ap" else 2) else rooms["teller"]
        else:
            office = index - 2 if role == "ap" else (index - 4) // 2
            if office >= len(rooms["offices"]):
                raise DesignError(f"{site.id}: {role} {ordinal} has no private office zone; review endpoint demand")
            room = rooms["offices"][office]
    space = site.w.obj(room)
    origin = space["meta"]["position_m"]
    mounting_height = {"workstation": 0.8, "atm": 1.2, "ap": 2.8, "camera": 2.5}[role]
    position = [origin[0] + 2 + 2 * (slot % 4), origin[1] + 1 + 2 * ((slot // 4) % 3),
                origin[2] + mounting_height]
    origin_room = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    equipment = site.w.obj(origin_room)["meta"]["position_m"]
    route = math.ceil(sum(abs(a - b) for a, b in zip(position, equipment)) + 10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: authored access channel needs {route} m; limit is {MAX_CHANNEL_M} m")
    node = site.w.obj(key)
    node["refs"]["location"] = room
    node["attrs"]["description"] = f"{purpose} {ordinal:03} in {space['attrs']['name']} at {_site_display(site)}"
    node["meta"].update(placement={"room": room, "function": space["meta"]["space_type"],
                                  "floor": space["meta"]["floor"], "position_m": position,
                                  "cable_origin": origin_room}, access_channel_length_m=route)


def resolve_wireless(defaults, supplied, label):
    """Freeze bounded concurrent device budgets for existing local zones."""
    if not isinstance(supplied, dict) or supplied.keys() - defaults.keys():
        raise DesignError(f"{label}: wireless must map existing zones to managed/guest counts; zones: {', '.join(sorted(defaults))}. Removing zones requires a new baseline")
    resolved = {}
    for key, managed in sorted(defaults.items()):
        value = supplied.get(key, {})
        if not isinstance(value, dict) or value.keys() - {"managed", "guest"}:
            raise DesignError(f"{label} wireless {key}: only managed and guest device counts are supported")
        value = dict(managed=managed, guest=0) | value
        if any(type(n) is not int or not 0 <= n <= 128 for n in value.values()) or sum(value.values()) > 128:
            raise DesignError(f"{label} wireless {key}: managed/guest must be integers 0–128 with total at most 128; the zone has four AP mounts")
        resolved[key] = value
    if sum(zone["guest"] for zone in resolved.values()) > 4084:
        raise DesignError(f"{label}: planned guest clients exceed 4084 addresses in the reserved /20 client range")
    return resolved


def wireless_aps(zone, minimum=1):
    # Authored inventory planning envelope, not an RF/throughput rating.
    return max(minimum, (zone["managed"] + zone["guest"] + 31) // 32)


def _ap_offset(cohort, ordinal):
    limit = 8 if cohort == "exam-ap" else 4
    if type(ordinal) is not int or not 1 <= ordinal <= limit:
        raise DesignError(f"{cohort}: AP mount ordinal must be 1–{limit}")
    row, lane = divmod(ordinal - 1, 2)
    if cohort == "exam-ap":
        return 4 + 8*lane, 4 + 4*row
    if cohort == "ward-ap":
        return 4 + 8*lane, 4 - 8*row
    return 4 + 4*lane, 4 + 4*row


def school_rooms(site, demand):
    """Finite classroom grammar with permanent room identities and floor closets."""
    classrooms, offices = [], []
    for index in range(demand["classrooms"]):
        floor = index // 8 + 1
        classrooms.append(_location(site, f"classroom-{index+1:03}", f"Classroom {index+1:03}",
            "classroom", floor, (6 + 12*(index % 4), 4 + 24*((index % 8)//4), (floor-1)*FLOOR_HEIGHT_M),
            _floor(site, floor), {"students": demand["students_per_classroom"],
                                  "workstations": demand["wired_seats_per_classroom"]+1}))
    for index in range(math.ceil(demand["administrative_staff"]/12)):
        offices.append(_location(site, f"admin-{index+1:02}", f"Administration {index+1:02}",
            "office", 1, (6+12*index, 50, 0), _floor(site, 1), {"workstations": 12}))
    lab = _location(site, "computer-lab", "Shared computer lab", "computer_lab", 1,
                    (6, 40, 0), _floor(site, 1), {"workstations": 36}) if demand["lab_seats"] else None
    for floor in range(2, math.ceil(demand["classrooms"]/8)+1):
        room = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room", floor,
                         (24, 18, (floor-1)*FLOOR_HEIGHT_M), _floor(site, floor))
        site.contract["placement"]["equipment_locations"][str(floor)] = room
    return classrooms, offices, lab


def school_endpoint(site, key, room, cohort, ordinal):
    """Place a wired endpoint; wireless headcounts never enter this allocator."""
    space = site.w.obj(room)
    origin = space["meta"]["position_m"]
    role = site.w.obj(key)["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    if cohort == "teacher":
        offset = (8, 0)
    elif role == "role/ap":
        offset = _ap_offset(cohort, ordinal)
    elif role == "role/camera":
        offset = (1+8*(ordinal-1), 0)
    else:
        offset = (1+1.2*((ordinal-1) % 6), 1+1.2*((ordinal-1)//6))
    point = [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a,b in zip(point,site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: school access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node = site.w.obj(key)
    node["refs"]["location"] = room
    ap = role == "role/ap"
    _describe(site, node, space, cohort, ap and "staff on wlan0, students on wlan1")
    node["meta"].update(cohort=cohort, placement={"room": room, "function": space["meta"]["space_type"],
        "floor": space["meta"]["floor"], "position_m": point, "cable_origin": serving}, access_channel_length_m=route)


def retail_rooms(site):
    """Permanent single-floor trading or warehouse grammar; no surveyed premises.

    ponytail: one authored floor per store and distribution centre. A multi-level
    store needs a reviewed riser and closet layout before demand can exceed it.
    """
    kind = site.contract["kind"]
    if kind not in {"store", "distribution"}:
        raise DesignError(f"{site.id}: retail rooms require a store or distribution centre")
    ground = _floor(site, 1)
    if kind == "store":
        return dict(
            selling=_location(site, "sales-floor", "Sales floor", "sales_floor", 1, (6, 4, 0), ground),
            office=_location(site, "back-office", "Back office", "back_office", 1, (6, 30, 0), ground,
                             {"workstations": OFFICE_DESKS}),
            storage=_location(site, "stockroom", "Stockroom", "stockroom", 1, (30, 30, 0), ground))
    return dict(
        selling=_location(site, "warehouse-floor", "Warehouse floor", "warehouse_floor", 1, (6, 4, 0), ground),
        office=_location(site, "office-01", "Distribution office", "office", 1, (6, 30, 0), ground,
                         {"workstations": OFFICE_DESKS}),
        storage=_location(site, "shipping-dock", "Shipping dock", "shipping_dock", 1, (30, 30, 0), ground))


def retail_endpoint(site, key, room, cohort, ordinal):
    """Place one store or warehouse endpoint on its bounded local copper route."""
    space, node = site.w.obj(room), site.w.obj(key)
    origin, role = space["meta"]["position_m"], node["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    # Mount pitches: sparse ceiling grids for radios and cameras, dense lanes
    # for floor equipment. These are authored layouts, not a surveyed plan.
    span, columns = (8, 2) if role == "role/ap" else (4, 4) if role == "role/camera" else (1.2, 6)
    if type(ordinal) is not int or ordinal < 1:
        raise DesignError(f"{key}: retail endpoint ordinal must be a positive integer")
    point = [origin[0] + 1 + span*((ordinal-1) % columns), origin[1] + 1 + span*((ordinal-1)//columns),
             origin[2] + height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a, b in zip(point, site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: retail access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node["refs"]["location"] = room
    ap = role == "role/ap"
    _describe(site, node, space, cohort, ap and "staff WLAN on wlan0")
    node["meta"].update(cohort=cohort, placement={"room": room, "function": space["meta"]["space_type"],
        "floor": space["meta"]["floor"], "position_m": point, "cable_origin": serving},
        access_channel_length_m=route)


def msp_office_pods(staff):
    """Authored pod layout for one managed office: twelve desk positions each."""
    return [(f"pod-{n+1:02}", min(OFFICE_DESKS, staff - OFFICE_DESKS*n))
            for n in range(math.ceil(staff / OFFICE_DESKS))]


def msp_office_rooms(site, staff):
    """Permanent managed-office rooms: one reception and reserved staff pods.

    Every pod holds a reserved ground-floor position for the life of the estate,
    so hiring into a customer office appends desks and, when a pod fills, a new
    pod beside the existing ones instead of renumbering the floor underneath.

    ponytail: one authored floor and four pods per managed office — the finite
    footprint this profile reviews. A multi-floor customer premises needs a
    reviewed riser and closet layout before demand can exceed it.
    """
    if site.contract["kind"] != "office":
        raise DesignError(f"{site.id}: managed-office rooms require a customer office site")
    ground = _floor(site, 1)
    reception = _location(site, "reception", "Reception", "reception", 1, (6, 6, 0), ground)
    pods = []
    for suffix, desks in msp_office_pods(staff):
        slot = site.w.reserve(f"office-pods/{site.id}", suffix, MAX_OFFICE_PODS)
        pods.append(_location(site, suffix, f"Staff office pod {suffix.removeprefix('pod-')}",
                              "staff_office", 1, (8 + 12*slot, 18, 0), ground,
                              {"workstations": desks}))
    return dict(reception=reception, pods=pods)


def msp_endpoint(site, key, room, cohort, ordinal):
    """Place one managed-office endpoint on its permanent local copper route."""
    space, node = site.w.obj(room), site.w.obj(key)
    origin, role = space["meta"]["position_m"], node["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    if role == "role/ap":
        offset = _ap_offset(cohort, ordinal)
    elif type(ordinal) is not int or ordinal < 1:
        raise DesignError(f"{key}: managed-office endpoint ordinal must be a positive integer")
    elif role == "role/camera":
        offset = (1 + 8*(ordinal-1), 0)
    else:
        offset = (1 + 1.2*((ordinal-1) % 6), 1 + 1.2*((ordinal-1)//6))
    point = [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a, b in zip(point, site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: managed-office access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node["refs"]["location"] = room
    ap = role == "role/ap"
    _describe(site, node, space, cohort, ap and "staff and any requested guest WLAN ride wlan0")
    node["meta"].update(cohort=cohort, placement={"room": room, "function": space["meta"]["space_type"],
        "floor": space["meta"]["floor"], "position_m": point, "cable_origin": serving},
        access_channel_length_m=route)


def plant_pods(staff):
    """Authored office-pod layout for one plant: twelve desk positions each."""
    return [(f"pod-{n+1:02}", min(OFFICE_DESKS, staff - OFFICE_DESKS*n))
            for n in range(math.ceil(staff / OFFICE_DESKS))]


def plant_rooms(site, item):
    """Permanent plant rooms: reception, office pods, loading docks, line cells.

    Every room holds a reserved ground-floor position for the life of the
    estate, so commissioning a line, opening a dock or hiring into the office
    appends a room beside the existing ones instead of renumbering the floor.

    ponytail: one authored ground floor per plant, with the reviewed ceilings of
    twelve production lines, twelve loading docks and eight office pods. The
    positions below are chosen so every bay stays inside the 80 m copper ceiling
    measured from the single plant equipment room; a larger site needs a
    reviewed multi-room riser layout before demand can exceed them.
    """
    if site.contract["kind"] != "plant":
        raise DesignError(f"{site.id}: plant rooms require a manufacturing plant site")
    ground = _floor(site, 1)
    rooms = dict(reception=_location(site, "reception", "Reception", "reception", 1, (48, 10, 0), ground),
                 pods=[], docks=[], lines=[])
    for suffix, desks in plant_pods(item["office_staff"]):
        slot = site.w.reserve(f"plant-pods/{site.id}", suffix, MAX_PLANT_PODS)
        rooms["pods"].append(_location(site, suffix, f"Office pod {suffix.removeprefix('pod-')}",
                                       "office", 1, (6 + 10*(slot % 4), 2 + 8*(slot // 4), 0),
                                       ground, {"workstations": desks}))
    for index in range(item["warehouse_docks"]):
        suffix = f"dock-{index+1:02}"
        slot = site.w.reserve(f"plant-docks/{site.id}", suffix, MAX_PLANT_DOCKS)
        rooms["docks"].append(_location(site, suffix, f"Loading dock {index+1:02}", "loading_dock", 1,
                                        (32 + 6*(slot % 4), 28 + 6*(slot // 4), 0), ground))
    for index in range(item["production_lines"]):
        suffix = f"line-{index+1:02}"
        slot = site.w.reserve(f"plant-lines/{site.id}", suffix, MAX_PLANT_LINES)
        rooms["lines"].append(_location(site, suffix, f"Production line {index+1:02}", "production_line", 1,
                                        (6 + 6*(slot % 4), 28 + 6*(slot // 4), 0), ground))
    return rooms


def plant_endpoint(site, key, room, cohort, ordinal):
    """Place one plant endpoint on its permanent mount and local copper route."""
    space, node = site.w.obj(room), site.w.obj(key)
    origin, role = space["meta"]["position_m"], node["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    if role == "role/ap":
        offset = _ap_offset(cohort, ordinal)
    elif type(ordinal) is not int or ordinal < 1:
        raise DesignError(f"{key}: plant endpoint ordinal must be a positive integer")
    elif role == "role/camera":
        offset = (1 + 8*(ordinal-1), 0)
    else:
        offset = (1 + 1.2*((ordinal-1) % 6), 1 + 1.2*((ordinal-1)//6))
    point = [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a, b in zip(point, site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: plant access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node["refs"]["location"] = room
    ap = role == "role/ap"
    _describe(site, node, space, cohort, ap and "staff WLAN on wlan0")
    node["meta"].update(cohort=cohort, placement={"room": room, "function": space["meta"]["space_type"],
        "floor": space["meta"]["floor"], "position_m": point, "cable_origin": serving},
        access_channel_length_m=route)


def substation_rooms(site, bays, workstations):
    """Permanent substation rooms: one control room and its switchyard bays.

    Every bay holds a reserved ground-floor position for the life of the estate,
    so commissioning a bay appends a position beside the existing ones instead of
    renumbering the switchyard. Bay positions are installed equipment locations
    in an authored layout: never a voltage class, electrical rating or bus
    arrangement.

    ponytail: one authored control house per substation, with the reviewed
    ceiling of sixteen bay positions. The positions below are chosen so every
    bay stays inside the 80 m copper ceiling measured from the single
    control-house equipment room; a larger switchyard needs a reviewed
    multi-room or fibre-riser layout before demand can exceed it.
    """
    if site.contract["kind"] != "substation":
        raise DesignError(f"{site.id}: substation rooms require a utility substation site")
    ground = _floor(site, 1)
    rooms = dict(control=_location(site, "control-room", "Control room", "control_room", 1, (48, 10, 0),
                                   ground, {"workstations": workstations}),
                 bays=[])
    for index in range(bays):
        suffix = f"bay-{index+1:02}"
        slot = site.w.reserve(f"substation-bays/{site.id}", suffix, MAX_SUBSTATION_BAYS)
        rooms["bays"].append(_location(site, suffix, f"Switchyard bay {index+1:02}", "switchyard_bay", 1,
                                       (6 + 8*(slot % 4), 30 + 8*(slot // 4), 0), ground))
    return rooms


def substation_endpoint(site, key, room, cohort, ordinal):
    """Place one substation endpoint on its permanent mount and copper route."""
    space, node = site.w.obj(room), site.w.obj(key)
    origin, role = space["meta"]["position_m"], node["refs"]["role"]
    if type(ordinal) is not int or ordinal < 1:
        raise DesignError(f"{key}: substation endpoint ordinal must be a positive integer")
    offset, height = (1 + 1.2*((ordinal-1) % 6), 1 + 1.2*((ordinal-1)//6)), 0.8
    point = [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a, b in zip(point, site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: substation access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node["refs"]["location"] = room
    _describe(site, node, space, cohort)
    node["meta"].update(cohort=cohort, placement={"room": room, "function": space["meta"]["space_type"],
        "floor": space["meta"]["floor"], "position_m": point, "cable_origin": serving},
        access_channel_length_m=route)


def _campus_floor(site, floor):
    """Create one building floor and, above ground, the IDF that serves it."""
    parent = _floor(site, floor)
    rooms = site.contract["placement"]["equipment_locations"]
    if floor > 1 and str(floor) not in rooms:
        rooms[str(floor)] = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room",
                                      floor, (24, 18, (floor-1)*FLOOR_HEIGHT_M), parent)
    return parent


def university_floors(item, kind):
    """Authored floor count for one campus building; no surveyed premises."""
    if kind == "residence":
        return max(1, math.ceil(item["rooms"] / DORM_ROOMS_PER_FLOOR))
    if kind == "library":
        rooms = math.ceil(item["reading_seats"] / READING_SEATS_PER_ROOM)
    else:
        rooms = (item["classrooms"] + math.ceil(item["lab_seats"] / LAB_SEATS_PER_ROOM)
                 + math.ceil(item["offices"] / OFFICE_DESKS))
    return max(1, math.ceil(rooms / ACADEMIC_ROOMS_PER_FLOOR))


def university_spaces(item, kind):
    """Ordered (suffix, name, space_type, bucket, capacity) rooms of one building.

    The order is creation order, which is what binds a room to its permanent
    reserved building position. New rooms append; existing rooms never move.
    """
    if kind == "residence":
        return [(f"room-{n+1:03}", f"Residence room {n+1:03}", "dorm_room", "residence",
                 {"workstations": item["wired_ports_per_room"]} if item["wired_ports_per_room"] else None)
                for n in range(item["rooms"])]
    if kind == "library":
        return [(f"reading-{n+1:02}", f"Reading room {n+1:02}", "reading_room", "reading",
                 {"workstations": min(READING_SEATS_PER_ROOM, item["reading_seats"] - READING_SEATS_PER_ROOM*n)})
                for n in range(math.ceil(item["reading_seats"] / READING_SEATS_PER_ROOM))]
    spaces = [(f"lecture-{n+1:03}", f"Lecture hall {n+1:03}", "lecture_hall", "halls",
               {"workstations": 1}) for n in range(item["classrooms"])]
    spaces += [(f"lab-{n+1:02}", f"Teaching lab {n+1:02}", "teaching_lab", "labs",
                {"workstations": min(LAB_SEATS_PER_ROOM, item["lab_seats"] - LAB_SEATS_PER_ROOM*n)})
               for n in range(math.ceil(item["lab_seats"] / LAB_SEATS_PER_ROOM))]
    spaces += [(f"office-{n+1:02}", f"Faculty office pod {n+1:02}", "office", "offices",
                {"workstations": min(OFFICE_DESKS, item["offices"] - OFFICE_DESKS*n)})
               for n in range(math.ceil(item["offices"] / OFFICE_DESKS))]
    return spaces


def university_slot(kind, slot):
    """Map one permanent room reservation to its floor and local position."""
    if kind == "residence":
        floor, place = slot // DORM_ROOMS_PER_FLOOR + 1, slot % DORM_ROOMS_PER_FLOOR
        return floor, (2 + 2*(place % 25), 2 + 10*(place//25), (floor-1)*FLOOR_HEIGHT_M)
    floor, place = slot // ACADEMIC_ROOMS_PER_FLOOR + 1, slot % ACADEMIC_ROOMS_PER_FLOOR
    return floor, (6 + 12*(place % 4), 4 + 24*(place//4), (floor-1)*FLOOR_HEIGHT_M)


def university_rooms(site, item):
    """Finite campus building grammar with permanent rooms, IDFs and corridors.

    Every room holds a reserved building position for the life of the estate, so
    adding lecture halls appends new rooms above the existing labs and offices
    instead of renumbering the building underneath them.

    ponytail: eight rooms per academic or library floor, fifty rooms per
    residence floor, and eight floors per building — the ceiling the shared
    eight-block management uplink pool supports. Raise that reviewed pool
    before admitting a taller building.
    """
    kind = site.contract["kind"]
    if kind not in {"academic", "residence", "library"}:
        raise DesignError(f"{site.id}: campus rooms require an academic, residence or library building")
    per_floor = DORM_ROOMS_PER_FLOOR if kind == "residence" else ACADEMIC_ROOMS_PER_FLOOR
    spaces = university_spaces(item, kind)
    placed = [(space, site.w.reserve(f"campus-rooms/{site.id}", space[0], MAX_BUILDING_FLOORS*per_floor))
              for space in spaces]
    floors = max((slot for _, slot in placed), default=0) // per_floor + 1
    if floors > MAX_BUILDING_FLOORS:
        raise DesignError(f"{site.id}: {floors} floors exceed the reviewed {MAX_BUILDING_FLOORS}-floor "
                          "building layout and its management attachment pool; split the demand across buildings")
    rooms = dict(halls=[], labs=[], offices=[], residence=[], reading=[], corridors={},
                 reception=None, floors=floors)
    for floor in range(1, floors + 1):
        _campus_floor(site, floor)
    for (suffix, name, space_type, bucket, capacity), slot in placed:
        floor, position = university_slot(kind, slot)
        rooms[bucket].append(_location(site, suffix, name, space_type, floor, position,
                                       _floor(site, floor), capacity))
    for floor in range(1, floors + 1):
        origin = (24, 8, (floor-1)*FLOOR_HEIGHT_M) if kind == "residence" else (24, 40, (floor-1)*FLOOR_HEIGHT_M)
        rooms["corridors"][floor] = _location(site, f"corridor-{floor:02}", f"Floor {floor:02} corridor",
                                              "corridor", floor, origin, _floor(site, floor))
    if kind == "library":
        rooms["reception"] = _location(site, "reception", "Library entrance", "reception", 1,
                                       (6, 40, 0), _floor(site, 1))
    return rooms


def university_endpoint(site, key, room, cohort, ordinal):
    """Place one campus endpoint on its permanent mount and floor-local route."""
    space, node = site.w.obj(room), site.w.obj(key)
    origin, role = space["meta"]["position_m"], node["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    if role == "role/ap":
        offset = _ap_offset(cohort, ordinal)
    elif type(ordinal) is not int or ordinal < 1:
        raise DesignError(f"{key}: campus endpoint ordinal must be a positive integer")
    elif role == "role/camera":
        offset = (1 + 8*(ordinal-1), 0)
    else:
        offset = (1 + 1.2*((ordinal-1) % 6), 1 + 1.2*((ordinal-1)//6))
    point = [origin[0]+offset[0], origin[1]+offset[1], origin[2]+height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a, b in zip(point, site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: campus access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node["refs"]["location"] = room
    ap = role == "role/ap"
    _describe(site, node, space, cohort, ap and "staff on wlan0, students on wlan1")
    node["meta"].update(cohort=cohort, placement={"room": room, "function": space["meta"]["space_type"],
        "floor": space["meta"]["floor"], "position_m": point, "cable_origin": serving}, access_channel_length_m=route)


def hospital_rooms(site, demand):
    """Permanent ward floors and finite clinical rooms; no surveyed premises."""
    rooms = dict(wards={}, exams=[], offices=[], imaging=[], corridors={})
    ground = _floor(site, 1)
    rooms["reception"] = _location(site, "reception", "Reception", "reception", 1, (24, 36, 0), ground)
    for ward in demand.get("wards", []):
        key = ward["key"]
        floor = 2 + site.w.reserve(f"healthcare-wards/{site.id}", key, 7)
        parent, height = _floor(site, floor), (floor-1)*FLOOR_HEIGHT_M
        prefix = f"ward-{key}"
        patients = [_location(site, f"{prefix}/patient-{index+1:02}", f"{key} patient room {index+1:02}",
            "patient_room", floor, (6+12*(index%4), 4+24*(index//4), height), parent, {"bed_stations":2})
            for index in range((ward["beds"]+1)//2)]
        nurse = _location(site, f"{prefix}/nurse-station", f"{key} nurse station", "nurse_station", floor,
                          (6,44,height), parent, {"workstations":8})
        corridor = _location(site, f"{prefix}/corridor", f"{key} corridor", "corridor", floor, (24,40,height), parent)
        rooms["wards"][key] = dict(patients=patients,nurse=nurse,corridor=corridor)
        equipment = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room", floor,
                              (24,18,height), parent)
        site.contract["placement"]["equipment_locations"][str(floor)] = equipment
    for index in range(demand.get("exam_rooms",0)):
        floor = index//8+1
        rooms["exams"].append(_location(site, f"exam-{index+1:03}", f"Exam room {index+1:03}", "exam_room", floor,
            (6+12*(index%4),4+24*((index%8)//4),(floor-1)*FLOOR_HEIGHT_M), _floor(site,floor), {"workstations":1}))
    for floor in range(1,(demand.get("exam_rooms",0)+7)//8+1):
        height, parent = (floor-1)*FLOOR_HEIGHT_M, _floor(site,floor)
        rooms["corridors"][floor] = _location(site, f"corridor-floor-{floor:02}", f"Floor {floor:02} corridor", "corridor",
                                             floor,(24,40,height),parent)
        if floor > 1:
            equipment = _location(site, f"idf-{floor:02}", f"IDF {floor:02}", "equipment_room", floor,(24,18,height),parent)
            site.contract["placement"]["equipment_locations"][str(floor)] = equipment
    clinic = site.contract["kind"] == "clinic"
    for index in range((demand["administrative_desks"]+11)//12):
        rooms["offices"].append(_location(site, f"admin-{index+1:02}", f"Administration {index+1:02}", "office",1,
            (6+12*index,60 if clinic else 50,0),ground,{"workstations":12}))
    for index in range(demand["imaging_rooms"]):
        rooms["imaging"].append(_location(site, f"imaging-{index+1:02}", f"Imaging room {index+1:02}", "imaging_room",1,
            (6+12*(index%4),44 if clinic else 4+24*(index//4),0),ground,{"modalities":1,"workstations":1}))
    return rooms


def hospital_endpoint(site, key, room, cohort, ordinal):
    """Assign clinical inventory to its room and bounded local copper route."""
    space, node = site.w.obj(room), site.w.obj(key)
    origin, role = space["meta"]["position_m"], node["refs"]["role"]
    height = 2.8 if role == "role/ap" else 2.5 if role == "role/camera" else 0.8
    if role == "role/ap":
        offset = _ap_offset(cohort, ordinal)
    elif role == "role/camera":
        offset = (1+8*(ordinal-1),0)
    else:
        offset = (1+1.2*((ordinal-1)%6),1+1.2*((ordinal-1)//6))
    point = [origin[0]+offset[0],origin[1]+offset[1],origin[2]+height]
    serving = site.contract["placement"]["equipment_locations"][str(space["meta"]["floor"])]
    route = math.ceil(sum(abs(a-b) for a,b in zip(point,site.w.obj(serving)["meta"]["position_m"]))+10)
    if route > MAX_CHANNEL_M:
        raise DesignError(f"{key}: clinical access channel needs {route} m; reviewed limit is {MAX_CHANNEL_M} m")
    node["refs"]["location"] = room
    ap = role == "role/ap"
    _describe(site, node, space, cohort, ap and "staff WLAN on wlan0")
    node["meta"].update(cohort=cohort,placement={"room":room,"function":space["meta"]["space_type"],
        "floor":space["meta"]["floor"],"position_m":point,"cable_origin":serving},access_channel_length_m=route)
