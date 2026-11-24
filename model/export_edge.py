"""Write edge/resources/jsonData/climbs.json, the climbs built into the Edge app.

    python3 export_edge.py            # from data/segments.json, after strava_sync.py
    python3 export_edge.py --example  # the made-up test climb, to build without Strava

The file holds other riders' KOM times, so git ignores it. Arrays are flat to save memory on the Edge:
path = [east m, north m, ...] from o = [lat, lon] in millionths of a degree (the Edge's 32-bit floats would blur
raw degrees by a metre), sec = [length, grade, altitude, ...], ghost = [distance, time, ...].
"""
import argparse
import json
import math
import time
from pathlib import Path

from pacer import clean, every, read_csv, sections_from_profile
from strava_sync import MIN_GRADE, OUT as SEGMENTS

EDGE = Path(__file__).parent.parent / "edge" / "resources" / "jsonData" / "climbs.json"
STEP_M = 25.0
M_PER_DEG = 111320.0  # metres per degree of latitude; the Edge uses the same flat-earth numbers


def climb(s):
    """One segment as the Edge needs it, or None if it can't be paced."""
    prof = s["profile"]
    if s.get("hazardous") or s["avg_grade"] < MIN_GRADE or not prof.get("latlng"):
        return None
    d, ll = clean(prof["distance"], prof["latlng"])
    _, alt = clean(prof["distance"], prof["altitude"])
    secs = sections_from_profile(d, alt)
    idx = every(d, STEP_M)
    lat0, lon0 = round(ll[0][0] * 1e6), round(ll[0][1] * 1e6)
    kx = M_PER_DEG * math.cos(math.radians(lat0 / 1e6))
    ghost = None
    if s.get("ghost"):
        gd, gt = clean(s["ghost"]["distance"], s["ghost"]["time"])
        ghost = [round(v, 2) for i in every(gd, STEP_M) for v in (gd[i], gt[i])]
    return {
        "name": s["name"], "kom": s.get("kom_s"), "qom": s.get("qom_s"), "pr": s.get("pr_s"), "prw": s.get("pr_watts"),
        "o": [lat0, lon0],
        "path": [round(v, 1) for i in idx for v in ((ll[i][1] - lon0 / 1e6) * kx, (ll[i][0] - lat0 / 1e6) * M_PER_DEG)],
        "pd": [round(d[i] - d[0], 1) for i in idx],
        "sec": [round(v, 4) for x in secs for v in (x.length, x.grade, x.alt)],
        "ghost": ghost,
    }


def export(segments, synced):
    climbs = [c for c in map(climb, segments.values()) if c]
    return {"synced": int(synced), "climbs": climbs}


def example():
    """The made-up 3 km test climb, laid due north from an arbitrary point, with a 10:30 PR and a 9:00 KOM."""
    d, h = read_csv(Path(__file__).parent / "examples" / "test-climb.csv", "distance_m", "altitude_m")
    return {"test": {"name": "Test climb", "avg_grade": (h[-1] - h[0]) / d[-1] * 100, "kom_s": 540, "qom_s": 640,
                     "pr_s": 630, "profile": {"distance": d, "altitude": h, "latlng": [[32.0 + x / 111320, 118.8] for x in d]}}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--example", action="store_true", help="export the made-up test climb instead of your Strava climbs")
    a = ap.parse_args()
    if a.example:
        segments = example()
    else:
        segments = json.loads(SEGMENTS.read_text(encoding="utf-8"))["segments"]
    out = export(segments, time.time())
    EDGE.parent.mkdir(parents=True, exist_ok=True)
    EDGE.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out['climbs'])} climbs, {EDGE.stat().st_size / 1024:.1f} KB -> {EDGE}")


if __name__ == "__main__":
    main()
