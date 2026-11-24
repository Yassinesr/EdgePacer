"""EdgePacer pacing model: on a Strava climb, the gap to your PR and the watts to beat it.

Reference for the Edge data field; the logic is explained in docs/PLAN.md.
Zero input: FTP and weight come from the rider's Garmin profile, everything else is a fixed default.
The gap is measured against the rider's own PR ride when there is one, so it needs no physics at all.
Physics per section at steady speed, in still air (no wind):
    P_meter * eff = v * (m*g*(sin a + crr*cos a) + 0.5*rho*cda*v^2)

Usage:
    python3 pacer.py --segments data/segments.json --segment "last climb" --weight 100 --ftp 300
"""
import argparse
import bisect
import csv
import json
import math
from dataclasses import dataclass, replace

G = 9.81
CAP = 50 / 3.6       # m/s, top speed on descents: corners and braking can't be modelled
MAX_P = 2000.0       # W, a plan needing more is out of reach
ANAEROBIC_S = 70.0   # W' expressed in seconds of FTP: about 20 kJ at FTP 300, a typical rider


@dataclass
class Rider:
    """What the Edge reads from the Garmin profile (weight, FTP), plus fixed defaults for everything else."""
    rider_kg: float
    ftp: float
    bike_kg: float = 9.0      # bike + kit
    crr: float = 0.005        # average road
    eff: float = 0.975        # drivetrain
    temp_c: float = 15.0
    cda: float = 0.0          # 0: estimated from weight

    def __post_init__(self):
        if not (30 <= self.rider_kg <= 200 and 50 <= self.ftp <= 700):
            # the Edge blanks the field rather than pace off a default, stale or unconverted (grams) profile
            raise ValueError(f"Garmin profile looks wrong: {self.rider_kg} kg, FTP {self.ftp} W")
        if not self.cda:
            # frontal area grows with body mass^0.425 (Heil 2001); 0.32 m^2 is a 72 kg rider on the hoods
            self.cda = 0.32 * (self.rider_kg / 72) ** 0.425

    def max_power(self, seconds):
        """Even power the rider can hold for this long, from FTP alone: a critical-power curve that reaches FTP at one hour."""
        # shortcut: the curve overshoots below a minute, so durations are floored at 60 s; fit it to ride history later
        return self.ftp * (1 + ANAEROBIC_S * max(0.0, 1 / max(60.0, seconds) - 1 / 3600))


@dataclass
class Section:
    length: float  # m
    grade: float   # rise / run
    alt: float     # m


def clean(xs, ys):
    """Keep points where x increases: recordings repeat the distance while you stand still."""
    keep = []
    for x, y in zip(xs, ys):
        if not keep or x > keep[-1][0]:
            keep.append((x, y))
    if len(keep) < 2:
        raise ValueError("need at least 2 points with increasing distance")
    return [x for x, _ in keep], [y for _, y in keep]


def interp(x, xs, ys):
    """Linear interpolation; the end pieces extend past the data."""
    i = min(max(bisect.bisect_right(xs, x), 1), len(xs) - 1)
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1])


def sections_from_profile(dist, alt):
    """Sections of 50-200 m (25 m minimum under 1 km) in which the grade stays within 1 point (PLAN 4.1)."""
    # shortcut: no extra altitude smoothing, merging into 50 m+ sections averages the noise; add it if real data is jumpy.
    dist, alt = clean(dist, alt)
    min_len = 25.0 if dist[-1] - dist[0] < 1000 else 50.0
    marks = [dist[0] + i * 25.0 for i in range(int((dist[-1] - dist[0]) // 25.0) + 1)]
    if dist[-1] - marks[-1] > 1e-6:
        marks.append(dist[-1])
    e = [interp(m, dist, alt) for m in marks]
    parts = []  # [length, rise, altitude * length]
    for x0, x1, e0, e1 in zip(marks, marks[1:], e, e[1:]):
        length, rise, alt_len = x1 - x0, e1 - e0, (e0 + e1) / 2 * (x1 - x0)
        if parts:
            pl, pr, pa = parts[-1]
            must = pl < min_len or length < 12.5  # grow a short section, absorb a short tail
            fits = abs(rise / length - pr / pl) < 0.01 and pl + length <= 200
            if must or fits:
                parts[-1] = [pl + length, pr + rise, pa + alt_len]
                continue
        parts.append([length, rise, alt_len])
    if len(parts) > 1 and parts[-1][0] < min_len:
        last = parts.pop()
        parts[-1] = [a + b for a, b in zip(parts[-1], last)]
    return [Section(l, max(-0.25, min(0.25, r / l)), a / l) for l, r, a in parts]


def air_density(alt_m, temp_c):
    pressure = 101325 * (1 - 2.25577e-5 * alt_m) ** 5.25588
    return pressure / (287.05 * (temp_c + 273.15))


def coeffs(s, r):
    """(a, b) with meter power = a*v + b*v^3 on section s; b*v^3 is the part lost to air drag."""
    ang = math.atan(s.grade)
    a = (r.rider_kg + r.bike_kg) * G * (math.sin(ang) + r.crr * math.cos(ang))
    return a / r.eff, 0.5 * air_density(s.alt, r.temp_c) * r.cda / r.eff


def speed_for(p, a, b):
    """Steady speed (m/s) for meter power p, capped at CAP. Power rises with speed past any dip, so bisection is safe."""
    if a * CAP + b * CAP ** 3 <= p:
        return CAP
    lo, hi = 0.0, CAP
    for _ in range(40):
        mid = (lo + hi) / 2
        if a * mid + b * mid ** 3 < p:
            lo = mid
        else:
            hi = mid
    return hi


def ride_time(p, lengths, ab):
    """Seconds to ride these sections at even power p."""
    return sum(length / speed_for(p, a, b) for length, (a, b) in zip(lengths, ab))


def solve(lengths, ab, time_left):
    """Even power that rides these sections in time_left, or None if it needs more than MAX_P."""
    if not lengths or time_left <= 0 or ride_time(MAX_P, lengths, ab) > time_left:
        return None
    lo, hi = 0.0, MAX_P
    for _ in range(40):
        p = (lo + hi) / 2
        if ride_time(p, lengths, ab) > time_left:
            lo = p
        else:
            hi = p
    return hi


def fastest(lengths, ab, rider):
    """(watts, seconds): the fastest even-power ride the rider's FTP allows on these sections."""
    lo, hi = 1.0, 4 * 3600.0
    for _ in range(40):
        t = (lo + hi) / 2
        if ride_time(rider.max_power(t), lengths, ab) > t:
            lo = t
        else:
            hi = t
    return rider.max_power(hi), hi


def beat(record_s, margin=0.01, min_s=2.0):
    """Target time: the record minus 1 %, at least 2 s, since Strava's GPS timing differs from the Edge's by 1-3 s."""
    return record_s - max(min_s, margin * record_s)


class Pacer:
    """Live pacing on one segment at even power, toward one target.
    The reference curve says where the rider must be at each metre to finish exactly at target_s: the PR ride (ghost,
    {"distance": [...], "time": [...]} from the segment start) when given, else this plan, either one scaled to
    end at target_s. Every gap reads from it. The watts never exceed what the rider's FTP allows.
    next: the target to fall back to, for good, once this one is out of reach."""

    def __init__(self, sections, rider, target_s, ghost=None, name="Target", next=None):
        self.rider, self.target_s, self.name, self.next, self.dropped = rider, target_s, name, next, False
        self.lengths = [s.length for s in sections]
        self.ab = [coeffs(s, rider) for s in sections]
        self.power = self._watts(self.lengths, self.ab, target_s)
        if ghost:
            d, t = clean(ghost["distance"], ghost["time"])
        else:
            d, t = [0.0], [0.0]
            for length, (a, b) in zip(self.lengths, self.ab):
                d.append(d[-1] + length)
                t.append(t[-1] + length / speed_for(self.power, a, b))
        scale_d, scale_t = sum(self.lengths) / d[-1], target_s / t[-1]  # the recording measures the climb a little differently
        self.curve = ([x * scale_d for x in d], [x * scale_t for x in t])
        self.ghost = bool(ghost)

    def _need_and_cap(self, lengths, ab, time_left):
        cap, _ = fastest(lengths, ab, self.rider)
        return solve(lengths, ab, time_left), cap

    def _watts(self, lengths, ab, time_left):
        need, cap = self._need_and_cap(lengths, ab, time_left)
        return cap if need is None else min(need, cap)

    def planned_time(self, done_m):
        """Seconds the reference curve takes to reach done_m."""
        return interp(done_m, *self.curve)

    def now(self, done_m, elapsed_s):
        """(target name, watts to hold here, gap in s: + ahead of the target pace, - behind).
        elapsed_s runs from the start line and keeps running through stops, like Strava's elapsed time.
        Ahead: the plan's watts, to bank the time. Behind: the even power that still finishes on time, capped at
        what the FTP allows for the time left; once even that can't make it, drop to the next target."""
        if self.dropped:
            return self.next.now(done_m, elapsed_s)
        gap = self.planned_time(done_m) - elapsed_s
        lengths, ab, start = [], [], 0.0
        for length, coef in zip(self.lengths, self.ab):
            left = start + length - done_m
            if left > 1e-6:
                lengths.append(min(left, length))
                ab.append(coef)
            start += length
        if gap >= 0 or not lengths:
            return self.name, self.power, gap
        need, cap = self._need_and_cap(lengths, ab, self.target_s - elapsed_s)
        if self.next and (need is None or need > cap):
            self.dropped = True
            return self.next.now(done_m, elapsed_s)
        return self.name, cap if need is None else min(need, cap), gap


def choose(sections, rider, kom_s=None, pr_s=None, ghost=None, pr_watts=None):
    """What to chase, with no input from the rider: the KOM if their FTP can beat it, then their PR if it can,
    then the fastest their FTP allows. Returns the first Pacer; the others are its fallbacks.
    pr_watts: average power measured on the PR ride. If it beats what the Garmin FTP allows for that long,
    the FTP is out of date and is raised to match (the returned Pacer's rider.ftp shows it)."""
    if pr_s and pr_watts and pr_watts > rider.max_power(pr_s):
        rider = replace(rider, ftp=pr_watts * rider.ftp / rider.max_power(pr_s))
    lengths, ab = [s.length for s in sections], [coeffs(s, rider) for s in sections]
    _, best = fastest(lengths, ab, rider)
    pacer = Pacer(sections, rider, best, name="Fastest")
    if pr_s and best <= beat(pr_s):
        pacer = Pacer(sections, rider, beat(pr_s), ghost, "PR", pacer)
    if kom_s and best <= beat(kom_s):
        pacer = Pacer(sections, rider, beat(kom_s), None, "KOM", pacer)
    return pacer


def parse_time(text):
    """'9:30', '1:02:03' or Strava's '45s', in seconds."""
    secs = 0
    for part in text.strip().rstrip("s").split(":"):
        secs = secs * 60 + float(part)
    return secs


def fmt_time(secs):
    return f"{int(secs // 60)}:{secs % 60:04.1f}"


def read_csv(path, *columns):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    return [[float(r[c]) for r in rows] for c in columns]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile", nargs="?", help="CSV with distance_m,altitude_m columns, or use --segments")
    ap.add_argument("--segments", help="data/segments.json from strava_sync.py; alone it lists the segments")
    ap.add_argument("--segment", help="with --segments: segment id or part of its name")
    ap.add_argument("--weight", type=float, help="rider weight, kg (the Edge reads it from the Garmin profile)")
    ap.add_argument("--ftp", type=float, help="FTP, W (the Edge reads it from the Garmin profile)")
    ap.add_argument("--kom", help="KOM time for a CSV profile, e.g. 9:00")
    ap.add_argument("--pr", help="your PR time for a CSV profile")
    a = ap.parse_args()

    seg = None
    if a.segments:
        with open(a.segments, encoding="utf-8") as f:
            segments = json.load(f)["segments"]
        hits = [s for s in segments.values() if a.segment and (a.segment == s["id"] or a.segment.lower() in s["name"].lower())]
        if len(hits) != 1:
            for s in hits or segments.values():
                kom = fmt_time(s["kom_s"]) if s["kom_s"] else "-"
                pr = fmt_time(s["pr_s"]) if s["pr_s"] else "-"
                ghost = "ghost" if s.get("ghost") else ""
                print(f"{s['id']:>12}  {s['distance_m']:6.0f} m {s['avg_grade']:5.1f}%  KOM {kom:>7}  PR {pr:>7} {ghost:5}  {s['name']}")
            raise SystemExit(0 if not a.segment else "Pick one with --segment <id>.")
        seg = hits[0]
        dist, alt = seg["profile"]["distance"], seg["profile"]["altitude"]
        print(f"{seg['name']}: {seg['distance_m']:.0f} m at {seg['avg_grade']:.1f}%")
    elif a.profile:
        dist, alt = read_csv(a.profile, "distance_m", "altitude_m")
    else:
        ap.error("give a profile CSV or --segments")
    if a.weight is None or a.ftp is None:
        ap.error("--weight and --ftp are required to make a plan")

    secs = sections_from_profile(dist, alt)
    rider = Rider(a.weight, a.ftp)
    kom = parse_time(a.kom) if a.kom else seg and seg["kom_s"]
    pr = parse_time(a.pr) if a.pr else seg and seg["pr_s"]
    pacer = choose(secs, rider, kom, pr, seg and seg.get("ghost"), seg and seg.get("pr_watts"))
    if pacer.rider.ftp > rider.ftp:
        print(f"Your FTP looks out of date: your PR ride implies about {pacer.rider.ftp:.0f} W")
    _, best = fastest(pacer.lengths, pacer.ab, pacer.rider)
    chain, p = [], pacer
    while p:
        chain.append(p.name)
        p = p.next

    print(f"Fastest your FTP allows: {fmt_time(best)}" + (f" (KOM {fmt_time(kom)})" if kom else ""))
    print(f"Chasing {' -> '.join(chain)}: aim {fmt_time(pacer.target_s)}, hold {pacer.power:.0f} W "
          f"({pacer.power / a.weight:.2f} W/kg)" + (", measured against your PR ride" if pacer.ghost else ""))
    print(f"{'km':>5} {'grade':>6} {'km/h':>6} {'be here at':>10}")
    done = 0.0
    for s, coef in zip(secs, pacer.ab):
        done += s.length
        v = speed_for(pacer.power, *coef)
        print(f"{done / 1000:5.2f} {s.grade * 100:5.1f}% {v * 3.6:6.1f} {fmt_time(pacer.planned_time(done)):>10}")


if __name__ == "__main__":
    main()
