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
# neighbourhood or suburb: the road OpenStreetMap Nominatim reverse-geocoded at
# the anchor centre plus the streets (with a house number, in that locality)
# that reverse geocoding returned at eight more points inside the anchor's
# jitter box (build/r3a/samples.json, 2026-10-02). A site hashes its id onto one
# street; its house number follows its position (see street_number), never a
# hash, so two nearby sites on one street get nearby numbers. Numbers are
# synthetic: never a surveyed or real premises.
ADDRESS_STREETS = {
    ('Chicago', 'Loop'): ('South LaSalle Street', 'West Adams Street', 'North Dearborn Street', 'North Franklin Street', 'West Jackson Boulevard', 'South Dearborn Street'),
    ('Chicago', 'Fulton Market'): ('West Fulton Market', 'West Lake Street', 'North Green Street', 'North Aberdeen Street', 'North Racine Avenue', 'West Washington Boulevard'),
    ('Chicago', 'Pilsen'): ('West 18th Street', 'West 18th Place', 'South Allport Street', 'West 17th Street', 'West 21st Street', 'West 19th Street'),
    ('Chicago', 'Cermak'): ('East Cermak Road', 'South Indiana Avenue', 'South Wabash Avenue'),
    ('Chicago', 'Ravenswood'): ('North Ravenswood Avenue', 'North Paulina Street', 'West Ainslie Street', 'West Lawrence Avenue', 'North Wolcott Avenue'),
    ('Chicago', 'Bridgeport'): ('South Halsted Street', 'South Keeley Street', 'West 31st Street', 'South Poplar Avenue', 'South Bonfield Street', 'South Lituanica Avenue'),
    ('Chicago', 'Logan Square'): ('West Belden Avenue', 'North Milwaukee Avenue', 'North Kedzie Boulevard', 'North Albany Avenue', 'North Spaulding Avenue', 'West Palmer Boulevard'),
    ('Chicago', 'Wicker Park'): ('North Hoyne Avenue', 'North Damen Avenue', 'West North Avenue', 'North Leavitt Street', 'West Schiller Street', 'West Hirsch Street'),
    ('Chicago', 'Garfield Park'): ('West Madison Street', 'West Wilcox Street'),
    ('Chicago', 'Bronzeville'): ('East 43rd Street', 'South Calumet Avenue', 'South Indiana Avenue', 'East 44th Street'),
    ('Chicago', 'Hyde Park'): ('South Woodlawn Avenue', 'South Greenwood Avenue', 'South Ingleside Avenue', 'East 53rd Street', 'South Drexel Avenue', 'East Hyde Park Boulevard'),
    ('Chicago', 'Lincoln Square'): ('West Foster Avenue', 'West Berwyn Avenue', 'North Oakley Avenue', 'North Lincoln Avenue', 'North Western Avenue'),
    ('Chicago', 'Chatham'): ('South Vernon Avenue', 'South Saint Lawrence Avenue', 'East 86th Street', 'East 85th Street', 'South Calumet Avenue', 'East 83rd Street'),
    ('Chicago', 'Lakeview'): ('West Belmont Avenue', 'North Seminary Avenue', 'North Racine Avenue', 'North Kenmore Avenue'),
    ('Chicago', 'Portage Park'): ('West Hutchinson Street', 'North Long Avenue', 'North Central Avenue', 'North Parkside Avenue', 'North Major Avenue', 'West Agatite Avenue'),
    ('Chicago', 'Little Village'): ('West 26th Street', 'South Homan Avenue', 'South Millard Avenue', 'West 27th Street', 'South Saint Louis Avenue', 'South Drake Avenue'),
    ('Chicago', 'Austin'): ('North Central Avenue', 'West Rice Street', 'North Waller Avenue', 'North Lotus Avenue', 'North Long Avenue', 'West Superior Street'),
    ('Elk Grove Village', 'Elk Grove'): ('Pratt Boulevard', 'Lively Boulevard'),
    ('Franklin Park', 'Franklin Park'): ('Edgington Street', 'Pacific Avenue', 'Schiller Boulevard', 'Belmont Avenue', 'Minneapolis Avenue', '25th Avenue'),
    ('Northlake', 'Northlake'): ('East Dickens Avenue', 'East Dewey Avenue', 'Village Drive', 'East Lyndale Avenue'),
    ('Oak Park', 'Oak Park'): ('South Ridgeland Avenue', 'South Harvey Avenue', 'Randolph Street', 'Lake Street'),
    ('Schaumburg', 'Schaumburg'): ('North Pleasant Drive',),
    ('Skokie', 'Skokie'): ('Kolmar Avenue', 'Dempster Street', 'Concord Lane', 'Cleveland Street', 'Kostner Avenue', 'Washington Street'),
    ('Chicago', 'Wabash'): ('South Wabash Avenue',),
    ('Chicago', 'Halsted'): ('South Halsted Street',),
    ('Chicago', 'Clark'): ('North Clark Street',),
    ('Chicago', 'Ashland'): ('South Ashland Avenue',),
    ('Chicago', 'Damen'): ('South Damen Avenue',),
    ('Chicago', 'Kedzie'): ('South Kedzie Avenue',),
    ('Chicago', 'Montrose'): ('West Montrose Avenue',),
    ('Chicago', 'Archer'): ('South Archer Avenue',),
    ('Detroit', 'Downtown'): ('Woodward Avenue', 'West Montcalm Street', 'East Grand River Avenue', 'Grand River Avenue', 'Cass Avenue', 'East Montcalm Street'),
    ('Detroit', 'Corktown'): ('Michigan Avenue', 'Cherry Street', '10th Street', 'Rosa Parks Boulevard'),
    ('Detroit', 'Eastern Market'): ('Orleans Street', 'Russell Street', 'Rivard Street', 'Erskine Street'),
    ('Detroit', 'New Center'): ('West Grand Boulevard', '2nd Avenue', 'West Baltimore Avenue', 'East Milwaukee Street', 'Lothrop Street'),
    ('Detroit', 'Midtown'): ('Cass Avenue', 'Woodward Avenue', 'West Forest Avenue', 'West Alexandrine Street', 'West Willis Street', 'West Canfield Street'),
    ('Detroit', 'Southwest Detroit'): ('Bivouac Street', 'Manson Street', 'Livernois Avenue', 'McMillan Street', 'Hammond Street'),
    ('Detroit', 'Palmer Park'): ('West 7 Mile Road', 'Pontchartrain Boulevard'),
    ('Detroit', 'Rosedale Park'): ('Southfield Road', 'Oakfield Avenue', 'Rosemont Avenue', 'Grand River Avenue', 'Penrod Street'),
    ('Detroit', 'East English Village'): ('Chatsworth Street', 'Haverhill Street', 'Balfour Road', 'Nottingham Road', 'East Warren Avenue', 'Buckingham Avenue'),
    ('Detroit', 'Boston Edison'): ('West Boston Boulevard', 'Hamilton Avenue', 'Atkinson Street', 'Longfellow Street', 'Edison Street'),
    ('Detroit', 'Woodbridge'): ('Trumbull Street', 'Commonwealth Street', 'West Warren Avenue', 'Merrick Street'),
    ('Detroit', 'Mexicantown'): ('Bagley Street', 'West Vernor Highway', 'Hubbard Street', 'Porter Street', 'Scotten Street', 'West Grand Boulevard'),
    ('Highland Park', 'Highland Park'): ('Gerald Street', 'Victor Street', 'Woodward Avenue', '2nd Avenue', 'East Grand Avenue', 'Manchester Parkway'),
    ('Dearborn', 'Dearborn'): ('Michigan Avenue', 'Bingham Street', 'Colson Street', 'Wellesley Street', 'Middlesex Street', 'Schaefer Road'),
    ('Southfield', 'Southfield'): ('West 10 Mile Road', 'Jeanette Street', 'Catalina Drive', 'George Washington Drive', 'Filmore Street', 'New Hampshire Drive'),
    ('Troy', 'Troy'): ('Livernois Road', 'Telford Drive', 'Niles Drive', 'Donaldson Drive'),
    ('Warren', 'Warren'): ('Campbell Avenue', 'Greentree Drive', 'Meadowbrook Drive', 'Lorraine Avenue', 'Hoover Road', 'Olive Street'),
    ('Livonia', 'Livonia'): ('Merriman Road', 'Denne Street', 'Elmira Street', 'Hubbell Street', 'Plymouth Road'),
    ('Royal Oak', 'Royal Oak'): ('South Main Street', 'East University Avenue', 'Oakland Avenue', 'West 4th Street', 'North Main Street', 'West 5th Street'),
    ('Novi', 'Novi'): ('Grand River Avenue', 'Flint Street', 'Stassen Avenue', 'West 11 Mile Road', 'Durson Street'),
    ('Detroit', 'Woodward'): ('Woodward Avenue',),
    ('Detroit', 'Gratiot'): ('Gratiot Avenue',),
    ('Detroit', 'Cass'): ('Cass Avenue',),
    ('Detroit', 'Livernois'): ('Livernois Avenue',),
    ('Detroit', 'Vernor'): ('West Vernor Highway',),
    ('Detroit', 'Bagley'): ('Bagley Street',),
    ('Detroit', 'Grand River'): ('Grand River Avenue',),
    ('Detroit', 'Mack'): ('Mack Avenue',),
    ('Cleveland', 'Downtown'): ('Ontario Street', 'Bolivar Road', 'Canal Road', 'Carnegie Avenue', 'East 9th Street'),
    ('Cleveland', 'Flats East'): ('West 3rd Street', 'Scranton Road'),
    ('Cleveland', 'Midtown'): ('Euclid Avenue', 'Chester Avenue', 'Carnegie Avenue', 'East 65th Street', 'Cedar Avenue'),
    ('Cleveland', 'University Circle'): ('Euclid Avenue', 'Bellflower Road'),
    ('Cleveland', 'Ohio City'): ('York Avenue', 'West 33rd Street', 'West 26th Street', 'Carroll Avenue', 'Monroe Avenue'),
    ('Cleveland', 'Tremont'): ('West 11th Street', 'Starkweather Avenue', 'West 14th Street'),
    ('Cleveland', 'Slavic Village'): ('Warsaw Avenue', 'Lansing Avenue', 'East 68th Street', 'Harvard Avenue', 'Ottawa Road', 'Hege Avenue'),
    ('Cleveland', 'West Park'): ('West 144th Street', 'Emery Avenue', 'Saint James Avenue', 'Liberty Avenue', 'West 150th Street', 'Albrus Avenue'),
    ('Cleveland', 'Buckeye Shaker'): ('East 126th Street', 'East 130th Street', 'Buckingham Avenue', 'East 122nd Street', 'East 120th Street'),
    ('Cleveland', 'Old Brooklyn'): ('Tate Avenue', 'Treadway Avenue', 'Searsdale Avenue', 'Broadview Road', 'Tampa Avenue', 'Saratoga Avenue'),
    ('Cleveland', 'Fairfax'): ('East 88th Street', 'East 93rd Street', 'East 85th Street', 'Folsom Avenue', 'East 95th Street', 'East 89th Street'),
    ('Lakewood', 'Lakewood'): ('Warren Road', 'Belle Avenue', 'Elmwood Avenue', 'Marlowe Avenue', 'Mars Avenue', 'Hilliard Road'),
    ('Westlake', 'Westlake'): ('Beechwood Drive', 'Berkeley Drive', 'Dover Center Road', 'Sleepy Hollow Drive', 'Maple Drive'),
    ('Independence', 'Independence'): ('Brecksville Road', 'Stone Road', 'Hemlock Road'),
    ('Parma', 'Parma'): ('Snow Road', 'Krueger Avenue', 'Gilbert Avenue', 'Wilber Avenue', 'West 54th Street'),
    ('Beachwood', 'Beachwood'): ('Chagrin Boulevard', 'Halburton Road', 'Letchworth Road'),
    ('Brooklyn', 'Brooklyn'): ('Ridge Road', 'North Amber Drive', 'Wefel Avenue', 'Memphis Avenue'),
    ('Shaker Heights', 'Shaker Heights'): ('South Woodland Road', 'Fayette Road', 'Falmouth Road', 'Almar Drive', 'Marchmont Road', 'Warrensville Center Road'),
    ('Cleveland', 'Euclid'): ('Euclid Avenue',),
    ('Cleveland', 'Broadway'): ('Broadway Avenue',),
    ('Cleveland', 'Lorain'): ('Lorain Avenue',),
    ('Cleveland', 'Prospect'): ('Prospect Avenue East',),
    ('Cleveland', 'Carnegie'): ('Carnegie Avenue',),
    ('Cleveland', 'Woodland'): ('Woodland Avenue',),
    ('Cleveland', 'Fleet'): ('Fleet Avenue',),
    ('Cleveland', 'Denison'): ('Denison Avenue',),
    ('Milwaukee', 'Downtown'): ('West Wells Street', 'West Wisconsin Avenue', 'North Doctor Martin Luther King Junior Drive', 'West State Street', 'West Michigan Street', 'North James Lovell Street'),
    ('Milwaukee', 'Third Ward'): ('North Broadway', 'East Buffalo Street', 'West Saint Paul Avenue', 'South Water Street', 'South 1st Street'),
    ('Milwaukee', 'Menomonee Valley'): ('West Canal Street', 'West Greves Street', 'South Layton Boulevard'),
    ('Milwaukee', 'Walkers Point'): ('West National Avenue', 'South 4th Street', 'West Bruce Street', 'West Washington Street', 'South 3rd Street', 'West Virginia Street'),
    ('Milwaukee', 'Bay View'): ('South Kinnickinnic Avenue', 'South Logan Avenue', 'South Adams Avenue', 'South Herman Street', 'South Howell Avenue', 'South Lenox Street'),
    ('Milwaukee', 'East Side'): ('North Newhall Street', 'North Bartlett Avenue', 'East Webster Place', 'North Dousman Street', 'East Park Place'),
    ('Milwaukee', 'Sherman Park'): ('West Burleigh Street', 'West Auer Avenue', 'North 39th Street', 'North 38th Street', 'North 42nd Street'),
    ('Milwaukee', 'Washington Heights'): ('North 53rd Street', 'West Washington Boulevard', 'North 55th Street', 'North 50th Street'),
    ('Milwaukee', 'Lincoln Village'): ('South 10th Street', 'South 9th Street', 'South 12th Street', 'South 8th Street', 'South 13th Street', 'West Lincoln Avenue'),
    ('Milwaukee', 'Riverwest'): ('North Weil Street', 'East Roadsmeet Street', 'North Fratney Street', 'North Pierce Street', 'East Chambers Street'),
    ('Milwaukee', 'Harambee'): ('West Center Street', 'North 2nd Street', 'North Dr. William Finlayson Street', 'West Clarke Street', 'East Hadley Street', 'North Vel R. Phillips Avenue'),
    ('Milwaukee', 'Clarke Square'): ('South 23rd Street', 'West Pierce Street', 'South 21st Street', 'South 25th Street', 'South 20th Street', 'West National Avenue'),
    ('West Allis', 'West Allis'): ('West Greenfield Avenue', 'South 74th Street', 'South 78th Street', 'South 76th Street'),
    ('Wauwatosa', 'Wauwatosa'): ('Harwood Avenue', 'Saint James Street', 'Milwaukee Avenue'),
    ('Oak Creek', 'Oak Creek'): ('South Howell Avenue', 'South Sunnyview Drive', 'East Centennial Drive', 'West Sunnyview Drive'),
    ('Brookfield', 'Brookfield'): ('West North Avenue', 'Pilgrim Parkway West', 'Pilgrim Road'),
    ('Glendale', 'Glendale'): ('West Brantwood Avenue', 'North Pine Shore Drive', 'North Green Bay Avenue', 'North Atwahl Drive', 'West Mill Road'),
    ('Milwaukee', 'Brady'): ('East Brady Street',),
    ('Milwaukee', 'Kilbourn'): ('West Kilbourn Avenue',),
    ('Milwaukee', 'Wells'): ('West Wells Street',),
    ('Milwaukee', 'Vliet'): ('West Vliet Street',),
    ('Milwaukee', 'Locust'): ('West Locust Street',),
    ('Milwaukee', 'Greenfield'): ('West Greenfield Avenue',),
    ('Milwaukee', 'Mitchell'): ('West Mitchell Street',),
    ('Milwaukee', 'Burleigh'): ('West Burleigh Street',),
}
# Numbering references for streets outside the Chicago and Milwaukee County
# grids: (latitude, longitude, house number) points OpenStreetMap Nominatim
# returned for that street in that locality (reverse samples inside the anchor
# boxes, plus forward lookups of a numbered address on each Detroit and
# Cleveland street run; build/r3a/geo-cache.json). Two well-separated points
# number a street linearly along the line through them; one point numbers it by
# distance from the locality's address origin (ADDRESS_ORIGINS).
STREET_REFS = {
    ('Beachwood', 'Chagrin Boulevard'): ((41.46579, -81.50581, 24601), (41.4652, -81.51295, 23511)),
    ('Beachwood', 'Halburton Road'): ((41.4666, -81.51139, 23748),),
    ('Beachwood', 'Letchworth Road'): ((41.46795, -81.50874, 24184),),
    ('Brookfield', 'Pilgrim Parkway West'): ((43.05812, -88.10959, 2115),),
    ('Brookfield', 'Pilgrim Road'): ((43.06347, -88.10587, 2440),),
    ('Brookfield', 'West North Avenue'): ((43.06085, -88.10284, 15300), (43.06068, -88.11055, 16005)),
    ('Brooklyn', 'Memphis Avenue'): ((41.43974, -81.73885, 7619),),
    ('Brooklyn', 'North Amber Drive'): ((41.4419, -81.73818, 4192),),
    ('Brooklyn', 'Ridge Road'): ((41.44296, -81.73538, 4152), (41.43667, -81.73543, 4406)),
    ('Brooklyn', 'Wefel Avenue'): ((41.43749, -81.73809, 7586),),
    ('Cleveland', 'Albrus Avenue'): ((41.44514, -81.79509, 14401),),
    ('Cleveland', 'Bellflower Road'): ((41.50805, -81.60875, 11038), (41.51149, -81.60491, 11424)),
    ('Cleveland', 'Bolivar Road'): ((41.49805, -81.68353, 1020),),
    ('Cleveland', 'Broadview Road'): ((41.43313, -81.69697, 4505),),
    ('Cleveland', 'Broadway Avenue'): ((41.451954, -81.631576, 8000),),
    ('Cleveland', 'Buckingham Avenue'): ((41.48516, -81.5977, 12191),),
    ('Cleveland', 'Canal Road'): ((41.49389, -81.68863, 2335),),
    ('Cleveland', 'Carnegie Avenue'): ((41.49638, -81.68179, 1101), (41.50154, -81.6473, 6312)),
    ('Cleveland', 'Carroll Avenue'): ((41.48499, -81.70616, 2700),),
    ('Cleveland', 'Cedar Avenue'): ((41.50047, -81.65001, 5798),),
    ('Cleveland', 'Chester Avenue'): ((41.50589, -81.65273, 5499), (41.50653, -81.64997, 5768)),
    ('Cleveland', 'Denison Avenue'): ((41.4514527, -81.7119644, 4000), (41.4617184, -81.7361156, 7000)),
    ('Cleveland', 'East 120th Street'): ((41.48299, -81.59883, 2718),),
    ('Cleveland', 'East 122nd Street'): ((41.4809, -81.5976, 2873),),
    ('Cleveland', 'East 126th Street'): ((41.48615, -81.59554, 2653), (41.47983, -81.59502, 2908)),
    ('Cleveland', 'East 130th Street'): ((41.48512, -81.592, 2670),),
    ('Cleveland', 'East 65th Street'): ((41.50352, -81.64591, 6401), (41.45315, -81.64503, 3864)),
    ('Cleveland', 'East 68th Street'): ((41.4479, -81.64226, 4064),),
    ('Cleveland', 'East 85th Street'): ((41.49709, -81.62767, 2253),),
    ('Cleveland', 'East 88th Street'): ((41.49494, -81.62529, 2301),),
    ('Cleveland', 'East 89th Street'): ((41.49816, -81.62464, 2170),),
    ('Cleveland', 'East 93rd Street'): ((41.49712, -81.6218, 2256),),
    ('Cleveland', 'East 95th Street'): ((41.49502, -81.62079, 2366),),
    ('Cleveland', 'East 9th Street'): ((41.4995, -81.68544, 2079),),
    ('Cleveland', 'Emery Avenue'): ((41.44412, -81.7923, 14163), (41.44416, -81.79769, 14671)),
    ('Cleveland', 'Euclid Avenue'): ((41.5038095, -81.6538212, 5000), (41.51053, -81.60253, 11610)),
    ('Cleveland', 'Fleet Avenue'): ((41.455742, -81.655359, 5000), (41.4559366, -81.6479495, 6000)),
    ('Cleveland', 'Folsom Avenue'): ((41.49254, -81.6223, 9221),),
    ('Cleveland', 'Harvard Avenue'): ((41.44827, -81.64761, 6022),),
    ('Cleveland', 'Hege Avenue'): ((41.4496, -81.64917, 5799),),
    ('Cleveland', 'Lansing Avenue'): ((41.45243, -81.64771, 6072),),
    ('Cleveland', 'Liberty Avenue'): ((41.44192, -81.79096, 14026),),
    ('Cleveland', 'Lorain Avenue'): ((41.477581, -81.72146, 5000), (41.467061, -81.753379, 10000)),
    ('Cleveland', 'Monroe Avenue'): ((41.47909, -81.70634, 3193),),
    ('Cleveland', 'Ontario Street'): ((41.49609, -81.68513, 2401),),
    ('Cleveland', 'Ottawa Road'): ((41.45027, -81.64101, 6920),),
    ('Cleveland', 'Prospect Avenue East'): ((41.501118, -81.66696, 3000),),
    ('Cleveland', 'Saint James Avenue'): ((41.4402, -81.79229, 14155), (41.4401, -81.7977, 14663)),
    ('Cleveland', 'Saratoga Avenue'): ((41.43191, -81.7, 2548),),
    ('Cleveland', 'Scranton Road'): ((41.4885, -81.69459, 2028),),
    ('Cleveland', 'Searsdale Avenue'): ((41.43712, -81.7027, 2850),),
    ('Cleveland', 'Starkweather Avenue'): ((41.47735, -81.68629, 929),),
    ('Cleveland', 'Tampa Avenue'): ((41.43265, -81.7027, 2954),),
    ('Cleveland', 'Tate Avenue'): ((41.43491, -81.69999, 2498), (41.43493, -81.69595, 2036)),
    ('Cleveland', 'Treadway Avenue'): ((41.43681, -81.69731, 2042),),
    ('Cleveland', 'Warsaw Avenue'): ((41.44963, -81.645, 6478),),
    ('Cleveland', 'West 11th Street'): ((41.47184, -81.68923, 2983),),
    ('Cleveland', 'West 144th Street'): ((41.44201, -81.79506, 4102), (41.43948, -81.79489, 4299)),
    ('Cleveland', 'West 14th Street'): ((41.47697, -81.69191, 2592), (41.47329, -81.69115, 2871)),
    ('Cleveland', 'West 150th Street'): ((41.44088, -81.79996, 4181),),
    ('Cleveland', 'West 26th Street'): ((41.48129, -81.70222, 2259),),
    ('Cleveland', 'West 33rd Street'): ((41.47955, -81.70853, 2168),),
    ('Cleveland', 'West 3rd Street'): ((41.49, -81.68744, 2003),),
    ('Cleveland', 'Woodland Avenue'): ((41.487942, -81.6485625, 6000), (41.488165, -81.623643, 9000)),
    ('Cleveland', 'York Avenue'): ((41.47939, -81.70324, 2899),),
    ('Dearborn', 'Bingham Street'): ((42.3244, -83.1737, 5067), (42.3202, -83.17357, 4374)),
    ('Dearborn', 'Colson Street'): ((42.32463, -83.179, 13849),),
    ('Dearborn', 'Michigan Avenue'): ((42.3211266, -83.1798482, 14000), (42.32234, -83.17638, 13620)),
    ('Dearborn', 'Middlesex Street'): ((42.3223, -83.18039, 4846),),
    ('Dearborn', 'Schaefer Road'): ((42.32543, -83.17644, 5141),),
    ('Dearborn', 'Wellesley Street'): ((42.32031, -83.17903, 13937),),
    ('Detroit', '10th Street'): ((42.32935, -83.06736, 1825),),
    ('Detroit', '2nd Avenue'): ((42.37142, -83.07821, 7601), (42.36702, -83.07502, 6310)),
    ('Detroit', 'Atkinson Street'): ((42.38105, -83.08935, 945), (42.38005, -83.09207, 1135)),
    ('Detroit', 'Bagley Street'): ((42.32441, -83.081618, 3000), (42.32148, -83.09011, 3900)),
    ('Detroit', 'Balfour Road'): ((42.40112, -82.94767, 5159),),
    ('Detroit', 'Bivouac Street'): ((42.31773, -83.10997, 6170),),
    ('Detroit', 'Buckingham Avenue'): ((42.40227, -82.94481, 5171),),
    ('Detroit', 'Cass Avenue'): ((42.356227, -83.066675, 5000), (42.33653, -83.05499, 2130)),
    ('Detroit', 'Chatsworth Street'): ((42.399, -82.94499, 4849),),
    ('Detroit', 'Cherry Street'): ((42.33384, -83.06747, 1426),),
    ('Detroit', 'Commonwealth Street'): ((42.35511, -83.08163, 5458), (42.34896, -83.07873, 4535)),
    ('Detroit', 'East Grand River Avenue'): ((42.3345, -83.04814, 25), (42.33637, -83.04682, 311)),
    ('Detroit', 'East Milwaukee Street'): ((42.36949, -83.07121, 60),),
    ('Detroit', 'East Montcalm Street'): ((42.33967, -83.0507, 119),),
    ('Detroit', 'East Warren Avenue'): ((42.39894, -82.94902, 15486),),
    ('Detroit', 'Edison Street'): ((42.38283, -83.0879, 730),),
    ('Detroit', 'Erskine Street'): ((42.35189, -83.0411, 1555),),
    ('Detroit', 'Grand River Avenue'): ((42.33448, -83.05372, 511), (42.39918, -83.21371, 17420)),
    ('Detroit', 'Gratiot Avenue'): ((42.3561031, -83.0293199, 3000), (42.378898, -83.014515, 8000)),
    ('Detroit', 'Hamilton Avenue'): ((42.38491, -83.09483, 9851),),
    ('Detroit', 'Hammond Street'): ((42.32138, -83.11001, 2500),),
    ('Detroit', 'Haverhill Street'): ((42.40095, -82.94252, 4962), (42.39887, -82.94115, 4704)),
    ('Detroit', 'Hubbard Street'): ((42.3235, -83.093, 2100),),
    ('Detroit', 'Livernois Avenue'): ((42.3693669, -83.1387181, 10000), (42.31493, -83.11011, 1807)),
    ('Detroit', 'Longfellow Street'): ((42.38122, -83.09482, 1317),),
    ('Detroit', 'Lothrop Street'): ((42.3698, -83.07959, 726),),
    ('Detroit', 'Mack Avenue'): ((42.3543003, -83.0393173, 2000), (42.366964, -83.005907, 8000)),
    ('Detroit', 'Manson Street'): ((42.32032, -83.10742, 2340),),
    ('Detroit', 'McMillan Street'): ((42.31804, -83.10596, 5921),),
    ('Detroit', 'Merrick Street'): ((42.35316, -83.08312, 1801),),
    ('Detroit', 'Michigan Avenue'): ((42.3316622, -83.0737382, 2000), (42.3312684, -83.1091863, 5000)),
    ('Detroit', 'Nottingham Road'): ((42.39699, -82.94756, 4759),),
    ('Detroit', 'Oakfield Avenue'): ((42.40159, -83.21437, 15319),),
    ('Detroit', 'Orleans Street'): ((42.34999, -83.03805, 2900),),
    ('Detroit', 'Penrod Street'): ((42.39949, -83.22154, 15072),),
    ('Detroit', 'Pontchartrain Boulevard'): ((42.4285, -83.12394, 17877),),
    ('Detroit', 'Porter Street'): ((42.31925, -83.08722, 3822),),
    ('Detroit', 'Rivard Street'): ((42.34617, -83.04319, 2735),),
    ('Detroit', 'Rosa Parks Boulevard'): ((42.32838, -83.06994, 1786),),
    ('Detroit', 'Rosemont Avenue'): ((42.39738, -83.22025, 14900),),
    ('Detroit', 'Russell Street'): ((42.34997, -83.04353, 3121),),
    ('Detroit', 'Scotten Street'): ((42.31913, -83.09172, 1500),),
    ('Detroit', 'Southfield Road'): ((42.39904, -83.21757, 15000),),
    ('Detroit', 'Trumbull Street'): ((42.35325, -83.07965, 5101),),
    ('Detroit', 'West 7 Mile Road'): ((42.4319193, -83.1284052, 2000),),
    ('Detroit', 'West Alexandrine Street'): ((42.34998, -83.06228, 79),),
    ('Detroit', 'West Baltimore Avenue'): ((42.36778, -83.07303, 65),),
    ('Detroit', 'West Boston Boulevard'): ((42.38543, -83.08942, 701),),
    ('Detroit', 'West Canfield Street'): ((42.35097, -83.06795, 674),),
    ('Detroit', 'West Forest Avenue'): ((42.35309, -83.06672, 477), (42.35374, -83.06382, 91)),
    ('Detroit', 'West Grand Boulevard'): ((42.37001, -83.07582, 3031), (42.32445, -83.09031, 532)),
    ('Detroit', 'West Montcalm Street'): ((42.33882, -83.05344, 50),),
    ('Detroit', 'West Vernor Highway'): ((42.3186696, -83.099994, 5000), (42.3237, -83.08766, 3554)),
    ('Detroit', 'West Warren Avenue'): ((42.35146, -83.08194, 1776),),
    ('Detroit', 'West Willis Street'): ((42.34917, -83.06652, 655),),
    ('Detroit', 'Woodward Avenue'): ((42.3324368, -83.0471077, 1000), (42.357104, -83.064348, 5000)),
    ('Elk Grove Village', 'Lively Boulevard'): ((42.00313, -87.97046, 1631), (41.99688, -87.97012, 2087)),
    ('Elk Grove Village', 'Pratt Boulevard'): ((41.99802, -87.9673, 1265), (41.99795, -87.97271, 957)),
    ('Franklin Park', '25th Avenue'): ((41.93792, -87.86579, 3234), (41.93215, -87.86544, 2962)),
    ('Franklin Park', 'Belmont Avenue'): ((41.93598, -87.86221, 9451),),
    ('Franklin Park', 'Edgington Street'): ((41.93741, -87.863, 3201),),
    ('Franklin Park', 'Minneapolis Avenue'): ((41.93498, -87.86953, 9766),),
    ('Franklin Park', 'Pacific Avenue'): ((41.93775, -87.86819, 9704),),
    ('Franklin Park', 'Schiller Boulevard'): ((41.93313, -87.86276, 9499), (41.93319, -87.8683, 9718)),
    ('Highland Park', '2nd Avenue'): ((42.40346, -83.09987, 13750),),
    ('Highland Park', 'East Grand Avenue'): ((42.40553, -83.09282, 99),),
    ('Highland Park', 'Gerald Street'): ((42.40557, -83.09688, 1),),
    ('Highland Park', 'Manchester Parkway'): ((42.40546, -83.10067, 99),),
    ('Highland Park', 'Victor Street'): ((42.40761, -83.09415, 97),),
    ('Highland Park', 'Woodward Avenue'): ((42.40794, -83.09953, 14301), (42.4083, -83.09646, 14100)),
    ('Independence', 'Brecksville Road'): ((41.37849, -81.63784, 6801),),
    ('Independence', 'Hemlock Road'): ((41.37515, -81.63851, 7189),),
    ('Independence', 'Stone Road'): ((41.38267, -81.6385, 7334),),
    ('Lakewood', 'Belle Avenue'): ((41.48109, -81.79713, 1564), (41.47691, -81.79722, 2006)),
    ('Lakewood', 'Elmwood Avenue'): ((41.48112, -81.80257, 1540), (41.47688, -81.80241, 2020)),
    ('Lakewood', 'Hilliard Road'): ((41.48221, -81.80004, 14804),),
    ('Lakewood', 'Marlowe Avenue'): ((41.479, -81.79574, 1814),),
    ('Lakewood', 'Mars Avenue'): ((41.47899, -81.80384, 1726),),
    ('Lakewood', 'Warren Road'): ((41.48179, -81.79997, 1500), (41.479, -81.80004, 1651)),
    ('Livonia', 'Denne Street'): ((42.36628, -83.35026, 11178),),
    ('Livonia', 'Elmira Street'): ((42.3663, -83.3554, 31644),),
    ('Livonia', 'Hubbell Street'): ((42.36839, -83.34869, 11559),),
    ('Livonia', 'Merriman Road'): ((42.3712, -83.35304, 11535), (42.36526, -83.35242, 10719)),
    ('Livonia', 'Plymouth Road'): ((42.36829, -83.35722, 31735),),
    ('Northlake', 'East Dewey Avenue'): ((41.91877, -87.89287, 320),),
    ('Northlake', 'East Dickens Avenue'): ((41.9169, -87.8956, 250), (41.91698, -87.89152, 348)),
    ('Northlake', 'East Lyndale Avenue'): ((41.91974, -87.89562, 254),),
    ('Northlake', 'Village Drive'): ((41.91495, -87.89287, 307), (41.91497, -87.89832, 198)),
    ('Novi', 'Durson Street'): ((42.47678, -83.48055, 43900),),
    ('Novi', 'Flint Street'): ((42.47912, -83.47665, 43447),),
    ('Novi', 'Grand River Avenue'): ((42.48249, -83.48275, 44020), (42.48046, -83.47591, 43407)),
    ('Novi', 'Stassen Avenue'): ((42.47831, -83.48269, 44080),),
    ('Novi', 'West 11 Mile Road'): ((42.48067, -83.48405, 44179),),
    ('Oak Park', 'Lake Street'): ((41.88803, -87.7843, 333),),
    ('Oak Park', 'Randolph Street'): ((41.88329, -87.78798, 501),),
    ('Oak Park', 'South Harvey Avenue'): ((41.88289, -87.78138, 325),),
    ('Oak Park', 'South Ridgeland Avenue'): ((41.88216, -87.78488, 331),),
    ('Parma', 'Gilbert Avenue'): ((41.40722, -81.7256, 5845),),
    ('Parma', 'Krueger Avenue'): ((41.40697, -81.7202, 5022),),
    ('Parma', 'Snow Road'): ((41.40482, -81.71885, 4825), (41.40481, -81.72695, 6027)),
    ('Parma', 'West 54th Street'): ((41.40794, -81.72267, 5667), (41.40166, -81.72287, 5922)),
    ('Parma', 'Wilber Avenue'): ((41.40292, -81.7256, 5869),),
    ('Royal Oak', 'East University Avenue'): ((42.49174, -83.14182, 303),),
    ('Royal Oak', 'North Main Street'): ((42.49267, -83.14482, 423),),
    ('Royal Oak', 'Oakland Avenue'): ((42.49147, -83.14727, 312),),
    ('Royal Oak', 'South Main Street'): ((42.48964, -83.14468, 100),),
    ('Royal Oak', 'West 4th Street'): ((42.48734, -83.14735, 316),),
    ('Royal Oak', 'West 5th Street'): ((42.48634, -83.1446, 155),),
    ('Schaumburg', 'North Pleasant Drive'): ((42.0330626, -88.0826975, 300),),
    ('Shaker Heights', 'Almar Drive'): ((41.4714, -81.5343, 20677),),
    ('Shaker Heights', 'Falmouth Road'): ((41.47601, -81.53925, 2978), (41.47745, -81.53706, 2811)),
    ('Shaker Heights', 'Fayette Road'): ((41.47614, -81.5342, 20730),),
    ('Shaker Heights', 'Marchmont Road'): ((41.47191, -81.53966, 19922),),
    ('Shaker Heights', 'South Woodland Road'): ((41.47389, -81.53295, 20789), (41.47398, -81.54105, 19749)),
    ('Shaker Heights', 'Warrensville Center Road'): ((41.47075, -81.53641, 3228),),
    ('Skokie', 'Cleveland Street'): ((42.02997, -87.74422, 4664),),
    ('Skokie', 'Concord Lane'): ((42.03037, -87.73892, 4448),),
    ('Skokie', 'Dempster Street'): ((42.03691, -87.74, 4401),),
    ('Skokie', 'Kolmar Avenue'): ((42.026828, -87.741563, 8000), (42.0324, -87.7414, 8328)),
    ('Skokie', 'Kostner Avenue'): ((42.03242, -87.73756, 8331),),
    ('Skokie', 'Washington Street'): ((42.03269, -87.74568, 4712),),
    ('Southfield', 'Catalina Drive'): ((42.47527, -83.22472, 25314),),
    ('Southfield', 'Filmore Street'): ((42.4769, -83.2219, 18133),),
    ('Southfield', 'George Washington Drive'): ((42.47153, -83.21921, 17741), (42.47131, -83.22459, 18389)),
    ('Southfield', 'Jeanette Street'): ((42.47534, -83.2192, 17698),),
    ('Southfield', 'New Hampshire Drive'): ((42.47052, -83.2219, 18173),),
    ('Southfield', 'West 10 Mile Road'): ((42.47371, -83.21751, 17418), (42.47336, -83.22596, 18540)),
    ('Troy', 'Donaldson Drive'): ((42.60375, -83.14638, 5835),),
    ('Troy', 'Livernois Road'): ((42.6061, -83.15009, 5977), (42.60326, -83.14971, 5783)),
    ('Troy', 'Niles Drive'): ((42.60853, -83.15213, 6145),),
    ('Troy', 'Telford Drive'): ((42.60849, -83.14741, 158),),
    ('Warren', 'Campbell Avenue'): ((42.5145, -83.01471, 30024),),
    ('Warren', 'Greentree Drive'): ((42.51653, -83.0117, 11377),),
    ('Warren', 'Hoover Road'): ((42.51762, -83.00954, 30333),),
    ('Warren', 'Lorraine Avenue'): ((42.51239, -83.01791, 29800),),
    ('Warren', 'Meadowbrook Drive'): ((42.51232, -83.012, 11321),),
    ('Warren', 'Olive Street'): ((42.5176, -83.0147, 11202),),
    ('Wauwatosa', 'Harwood Avenue'): ((43.04956, -88.00761, 7613), (43.04945, -88.01033, 7720)),
    ('Wauwatosa', 'Milwaukee Avenue'): ((43.05259, -88.00759, 7603),),
    ('Wauwatosa', 'Saint James Street'): ((43.04964, -88.00355, 7318),),
    ('Westlake', 'Beechwood Drive'): ((41.45527, -81.91742, 2334),),
    ('Westlake', 'Berkeley Drive'): ((41.45815, -81.91519, 2032),),
    ('Westlake', 'Dover Center Road'): ((41.45482, -81.92285, 2240),),
    ('Westlake', 'Maple Drive'): ((41.45313, -81.91784, 26757),),
    ('Westlake', 'Sleepy Hollow Drive'): ((41.45835, -81.9179, 26906),),
}
# Chicago's address grid: zero at State Street and Madison Street, 800 numbers
# to the mile (55,200 per degree of latitude; 41,200 per degree of longitude
# at 41.9 degrees north). South of Madison the grid is not uniform (twelve
# hundred numbers to Roosevelt Road's single mile, then named streets), so
# South numbers interpolate CHICAGO_SOUTH, read from the reverse samples.
CHICAGO_GRID = (41.8819, -87.6278, 55200, 41200)
CHICAGO_SOUTH = ((41.7378, 8617), (41.7969, 5432), (41.8190, 4139), (41.8360, 3200), (41.8424, 2702),
                 (41.8509, 2300), (41.8586, 1641), (41.8819, 0))
# Milwaukee County's grid (Milwaukee, West Allis, Wauwatosa, Oak Creek and
# Glendale share it): signed North/South numbers by latitude and East/West by
# longitude, interpolated through the reverse samples. North/South divides near
# the Menomonee Valley and East/West at the Milwaukee River.
MILWAUKEE_COUNTY = {"Milwaukee", "West Allis", "Wauwatosa", "Oak Creek", "Glendale"}
MILWAUKEE_NS = ((42.8809, -8956), (42.9900, -2986), (43.0000, -2456), (43.0136, -1564), (43.0199, -1110),
                (43.0288, -224), (43.0321, 172), (43.0419, 910), (43.0509, 1524), (43.0629, 2467),
                (43.0782, 3252), (43.1331, 6369))
MILWAUKEE_EW = ((-88.0110, -7909), (-87.9827, -5518), (-87.9690, -4409), (-87.9512, -3001), (-87.9250, -1004),
                (-87.9150, -322), (-87.9109, 121), (-87.8992, 1014), (-87.8878, 1802))
# Single-reference streets number outward from their locality's address origin
# at a numbers-per-km rate fitted to the forward lookups: Detroit's Woodward at
# the river (Dearborn's Michigan Avenue and Highland Park continue Detroit's
# numbering) and Cleveland's Public Square. Any other suburb numbers from its
# own anchor centre at 1,000 to the mile.
ADDRESS_ORIGINS = {"Detroit": (42.3289, -83.0453, 1300), "Dearborn": (42.3289, -83.0453, 1300),
                   "Highland Park": (42.3289, -83.0453, 1300), "Cleveland": (41.4995, -81.6937, 1500)}
SUBURB_NUMBERS_PER_KM = 620
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


def _interpolate(table, value):
    """Piecewise-linear lookup in an ascending (coordinate, number) table, extrapolating the end segments."""
    pairs = list(zip(table, table[1:]))
    (x0, y0), (x1, y1) = next(((a, b) for a, b in pairs if value <= b[0]), pairs[-1])
    return y0 + (value - x0) * (y1 - y0) / (x1 - x0)


def _km(a, b):
    return math.hypot((a[0] - b[0]) * 111.0, (a[1] - b[1]) * 111.0 * math.cos(math.radians(a[0])))


def street_number(locality, street, latitude, longitude, anchor=None):
    """Signed house number for a point on a street: a pure function of position.

    Chicago and Milwaukee County directional streets read their grid (the sign
    picks North/South or East/West); every other street interpolates its
    STREET_REFS points. Nearby sites on one street get nearby numbers.
    """
    direction = street.partition(" ")[0]
    if locality == "Chicago" and direction in {"North", "South", "East", "West"}:
        lat0, lon0, per_lat, per_lon = CHICAGO_GRID
        if direction in {"North", "South"}:
            return (latitude - lat0) * per_lat if latitude >= lat0 else -_interpolate(CHICAGO_SOUTH, latitude)
        return (longitude - lon0) * per_lon
    if locality in MILWAUKEE_COUNTY and direction in {"North", "South", "East", "West"}:
        return (_interpolate(MILWAUKEE_NS, latitude) if direction in {"North", "South"}
                else _interpolate(MILWAUKEE_EW, longitude))
    refs = STREET_REFS[(locality, street)]
    point = (latitude, longitude)
    if len(refs) == 2:
        (a_lat, a_lon, a_n), (b_lat, b_lon, b_n) = refs
        scale = math.cos(math.radians(a_lat))
        ax, ay = (b_lon - a_lon) * scale, b_lat - a_lat
        t = ((longitude - a_lon) * scale * ax + (latitude - a_lat) * ay) / (ax * ax + ay * ay)
        return a_n + t * (b_n - a_n)
    lat, lon, number = refs[0]
    if locality in ADDRESS_ORIGINS:
        o_lat, o_lon, rate = ADDRESS_ORIGINS[locality]
    else:
        (o_lat, o_lon), rate = anchor[2:4], SUBURB_NUMBERS_PER_KM
    return number + rate * (_km(point, (o_lat, o_lon)) - _km((lat, lon), (o_lat, o_lon)))


def street_address(site_id, anchor, latitude, longitude):
    """Street line for one site: a real street at its anchor, numbered by position.

    The street hashes the site id; the number follows the site's coordinates
    along that street (street_number), with only the odd/even side hashed, so
    growth and seeds never readdress a site and nearby sites read nearby.
    Inside Chicago and Milwaukee County a directional street's prefix follows
    the grid side the site is on. Workspace.finish makes full addresses unique.
    """
    pool = ADDRESS_STREETS[(anchor[1], anchor[0][0])]
    digest = hashlib.sha256(f"address/{site_id}".encode()).digest()
    street = pool[digest[0] % len(pool)]
    number = street_number(anchor[1], street, latitude, longitude, anchor)
    direction, _, rest = street.partition(" ")
    if direction in {"North", "South", "East", "West"} and (anchor[1] == "Chicago" or anchor[1] in MILWAUKEE_COUNTY):
        north_south = direction in {"North", "South"}
        street = f"{('North' if number >= 0 else 'South') if north_south else ('East' if number >= 0 else 'West')} {rest}"
    # Even or odd side of the street is the only hashed part of the number.
    return f"{max(1, int(abs(number)) // 2 * 2 + digest[1] % 2)} {street}"


def unique_addresses(sites, allocations):
    """Make every site's street line unique within its locality, in place.

    An earlier-allocated site keeps its number; a later site on the same street
    and side steps two numbers along until free. Allocation slots are permanent,
    so growth never readdresses an existing site.
    """
    taken = set()
    for site in sorted(sites, key=lambda s: (allocations.get(s["key"].removeprefix("site/"), 1 << 62), s["key"])):
        lines = site["attrs"].get("physical_address", "").split("\n")
        number, _, street = lines[0].partition(" ")
        if not number.isdecimal():
            continue
        n = int(number)
        while (n, street, tuple(lines[1:])) in taken:
            n += 2
        taken.add((n, street, tuple(lines[1:])))
        site["attrs"]["physical_address"] = "\n".join([f"{n} {street}", *lines[1:]])


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
