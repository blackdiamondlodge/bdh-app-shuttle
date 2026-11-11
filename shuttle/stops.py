"""East loop stops and route. Positions are placed on OSM streets from the
resort's stop map; they're good to ~30 m, enough for "near <stop>"."""
import json
import math
from functools import lru_cache
from pathlib import Path

OUR_STOP = "E7"

# Loop order. ``label``: name drawn on the map (others show on tap).
STOPS = (
    {"id": "VDL", "name": "Village Day Lodge", "lat": 50.88405, "lon": -119.88640, "label": True},
    {"id": "E1", "name": "Ice Rink", "lat": 50.88520, "lon": -119.87815, "label": True},
    {"id": "E2", "name": "McGillivray Creek", "lat": 50.88345, "lon": -119.87470, "label": False},
    {"id": "E3", "name": "Orient Chair", "lat": 50.88300, "lon": -119.87130, "label": True},
    {"id": "E4", "name": "P5 Entrance", "lat": 50.88360, "lon": -119.86800, "label": False},
    {"id": "E5", "name": "Switchback Creek", "lat": 50.88505, "lon": -119.86560, "label": "left"},
    {"id": "E6", "name": "Altitude", "lat": 50.88345, "lon": -119.86200, "label": True},
    {"id": "E7", "name": "Lookout Ridge", "lat": 50.88512, "lon": -119.86467, "label": True},
    {"id": "E8", "name": "Stones Throw", "lat": 50.88405, "lon": -119.86790, "label": False},
    {"id": "E9", "name": "Trappers Landing", "lat": 50.88370, "lon": -119.87330, "label": False},
)


@lru_cache(maxsize=1)
def route():
    """[[lat, lon], ...] of the East loop, VDL round to VDL."""
    path = Path(__file__).with_name("data") / "east_loop.json"
    return json.loads(path.read_text())["route"]


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest(lat, lon):
    """(stop, metres) for the closest stop."""
    best = min(STOPS, key=lambda s: distance_m(lat, lon, s["lat"], s["lon"]))
    return best, distance_m(lat, lon, best["lat"], best["lon"])
