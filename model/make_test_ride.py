"""Write a simulator test ride over the made-up test climb (export_edge.py --example), with power and real momentum.

    pip install fit-tool
    python3 make_test_ride.py                 # 280 W on the climb: P, falling behind slowly
    python3 make_test_ride.py --watts 240     # the PR goes out of reach: the switch to "best"

600 m run-in at FTP 300 W (the approach screen shows for the last 500 m), the climb at --watts, 400 m past the top at
200 W; 68 kg rider. Speed follows the physics, so the run-in speed carries over the start line.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.profile_type import Event, EventType, FileType, Manufacturer

import pacer

TEST = Path(__file__).parent.parent / "edge" / "test"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watts", type=int, default=280, help="power on the climb")
    a = ap.parse_args()
    d, h = pacer.read_csv(Path(__file__).parent / "examples" / "test-climb.csv", "distance_m", "altitude_m")
    r = pacer.Rider(68, 300)

    def alt(x):
        return pacer.interp(min(max(x, 0), d[-1]), d, h)

    def ab(x):
        g = (alt(x + 10) - alt(x - 10)) / 20 if 0 < x < d[-1] else 0.0
        return pacer.coeffs(pacer.Section(25, g, alt(x)), r)

    t0 = int(datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
    b = FitFileBuilder(auto_define=True)
    fid = FileIdMessage()
    fid.type, fid.manufacturer, fid.product, fid.serial_number, fid.time_created = (
        FileType.ACTIVITY, Manufacturer.DEVELOPMENT.value, 0, 1, t0)
    b.add(fid)
    ev = EventMessage()
    ev.event, ev.event_type, ev.timestamp = Event.TIMER, EventType.START, t0
    b.add(ev)
    x, t, v, cross, top = -600.0, 0, pacer.speed_for(300, *ab(-600.0)), None, None
    while x <= d[-1] + 400:
        watts = 300 if x < 0 else (a.watts if x <= d[-1] else 200)
        m = RecordMessage()
        m.timestamp, m.position_lat, m.position_long = t0 + t * 1000, 32.0 + x / 111320, 118.8
        m.altitude, m.distance, m.speed, m.power = alt(x), x + 600, v, watts
        b.add(m)
        for _ in range(20):  # 1 s in 0.05 s steps: P = a v + b v^3 + inertia v dv/dt
            ca, cb = ab(x)
            x0 = x
            v = max(v + (watts - ca * v - cb * v ** 3) / (r.inertia() * v) * 0.05, 0.5)
            x += v * 0.05
            cross = t if cross is None and x0 < 0 <= x else cross
            top = t if top is None and x0 <= d[-1] < x else top
        t += 1
    ev = EventMessage()
    ev.event, ev.event_type, ev.timestamp = Event.TIMER, EventType.STOP_ALL, t0 + t * 1000
    b.add(ev)
    out = TEST / ("test-climb-ride.fit" if a.watts == 280 else f"test-climb-ride-{a.watts}w.fit")
    b.build().to_file(str(out))
    print(f"{out.name}: climb in about {top - cross} s at {a.watts} W, {t} s in all")


if __name__ == "__main__":
    main()
