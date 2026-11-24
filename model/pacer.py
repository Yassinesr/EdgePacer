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
from dataclasses import dataclass

G = 9.81
CAP = 50 / 3.6       # m/s, top speed on descents: corners and braking can't be modelled
MAX_P = 2000.0       # W, a plan needing more is out of reach
ANAEROBIC_S = 70.0   # W' expressed in seconds of FTP: about 20 kJ at FTP 300, a typical rider
MAX_FTP = 1.5        # never ask for more than 1.5 x FTP, whatever the reserve says
STEADY_S = 20.0      # s of steady riding on the climb before watts come from the rider's own power
RESTART_S = 10.0     # s after a stop with no watts shown, so nobody sprints away from a red light


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

    @property
    def w_prime(self):
        """J of work the rider can do above FTP when fresh."""
        return ANAEROBIC_S * self.ftp

    def curve(self, seconds, w_left=None):
        """Critical-power curve: FTP plus the reserve above it (full unless given) spread over the effort, FTP at one hour."""
        # shortcut: durations are floored at 60 s, where the curve overshoots; fit it to ride history later
        w = self.w_prime if w_left is None else max(0.0, w_left)
        return self.ftp + w * max(0.0, 1 / max(60.0, seconds) - 1 / 3600)

    def max_power(self, seconds, w_left=None):
        """Even power the rider can hold for this long with this much reserve left: the curve, never above 1.5 x FTP."""
        return min(MAX_FTP * self.ftp, self.curve(seconds, w_left))

    def w_after(self, w, watts, seconds):
        """Reserve left after riding at `watts` for `seconds` (Skiba): drains above FTP, refills below it."""
        if watts > self.ftp:
            return w - (watts - self.ftp) * seconds
        return self.w_prime - (self.w_prime - w) * math.exp(-(self.ftp - watts) * seconds / self.w_prime)


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


def fastest(lengths, ab, rider, w_left=None):
    """(watts, seconds): the fastest even-power ride the rider can hold on these sections with this much reserve left."""
    lo, hi = 1.0, 4 * 3600.0
    for _ in range(40):
        t = (lo + hi) / 2
        if ride_time(rider.max_power(t, w_left), lengths, ab) > t:
            lo = t
        else:
            hi = t
    return rider.max_power(hi, w_left), hi


def beat(record_s, margin=0.01, min_s=2.0):
    """Target time: the record minus 1 %, at least 2 s, since Strava's GPS timing differs from the Edge's by 1-3 s."""
    return record_s - max(min_s, margin * record_s)


class Pacer:
    """Live pacing on one segment at even power, toward one target.
    The reference curve says where the rider must be at each metre to finish exactly at target_s: the PR ride (ghost,
    {"distance": [...], "time": [...]} from the segment start) when given, else this plan, either one scaled to
    end at target_s. Every gap reads from it. The watts never exceed what the rider can hold (Rider.max_power).
    next: the target to fall back to, for good, once this one is out of reach. w_left: the reserve at the start line."""

    def __init__(self, sections, rider, target_s, ghost=None, name="Target", next=None, w_left=None):
        self.rider, self.target_s, self.name, self.next, self.dropped = rider, target_s, name, next, False
        self.lengths = [s.length for s in sections]
        self.ab = [coeffs(s, rider) for s in sections]
        self.power = self._watts(self.lengths, self.ab, target_s, w_left)
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

    def _need_and_cap(self, lengths, ab, time_left, w_left=None):
        cap, _ = fastest(lengths, ab, self.rider, w_left)  # one ceiling for the watts shown and for "out of reach"
        return solve(lengths, ab, time_left), cap

    def _watts(self, lengths, ab, time_left, w_left=None):
        need, cap = self._need_and_cap(lengths, ab, time_left, w_left)
        return cap if need is None else min(need, cap)

    def planned_time(self, done_m):
        """Seconds the reference curve takes to reach done_m."""
        return interp(done_m, *self.curve)

    def now(self, done_m, elapsed_s, w_left=None, power=None, speed=None, steady_s=0.0, since_stop_s=None):
        """(target name, watts to hold or None, gap in s: + ahead of the target pace, - behind).
        elapsed_s runs from the start line and keeps running through stops, like Strava's elapsed time.
        w_left: the rider's reserve right now (Rider.w_after), full if unknown.
        Ahead: the plan's watts, to bank the time. Behind: the even power that still finishes on time; once even the
        most the rider can hold can't make it, drop to the next target for good.
        power, speed, steady_s: the rider's smoothed power and speed and how long they have ridden steadily; after
        STEADY_S the watts are their own power scaled to the speed needed, which cancels the model's defaults.
        since_stop_s: seconds since the rider last got going after a stop on this climb; no watts for RESTART_S."""
        if self.dropped:
            return self.next.now(done_m, elapsed_s, w_left, power, speed, steady_s, since_stop_s)
        gap = self.planned_time(done_m) - elapsed_s
        lengths, ab, start = [], [], 0.0
        for length, coef in zip(self.lengths, self.ab):
            left = start + length - done_m
            if left > 1e-6:
                lengths.append(min(left, length))
                ab.append(coef)
            start += length
        if not lengths:
            return self.name, None, gap
        need, cap = self._need_and_cap(lengths, ab, self.target_s - elapsed_s, w_left)
        if gap >= 0:
            watts = min(self.power, cap)
        elif self.next and (need is None or need > cap):
            self.dropped = True
            return self.next.now(done_m, elapsed_s, w_left, power, speed, steady_s, since_stop_s)
        else:
            watts = cap if need is None else min(need, cap)
        if since_stop_s is not None and since_stop_s < RESTART_S:
            return self.name, None, gap
        a, b = ab[0]
        if power and speed and steady_s >= STEADY_S and a * speed + b * speed ** 3 > 0:
            # what the model says this rider needs, relative to what it says their current speed costs
            watts = min(cap, power * watts / (a * speed + b * speed ** 3))
        return self.name, watts, gap


def choose(sections, rider, kom_s=None, pr_s=None, ghost=None, w_left=None):
    """What to chase, with no input from the rider: the KOM (or QOM) if they can beat it, then their PR if they can,
    then the fastest they can hold. Returns the first Pacer; the others are its fallbacks."""
    lengths, ab = [s.length for s in sections], [coeffs(s, rider) for s in sections]
    _, best = fastest(lengths, ab, rider, w_left)
    pacer = Pacer(sections, rider, best, name="Fastest", w_left=w_left)
    if pr_s and best <= beat(pr_s):
        pacer = Pacer(sections, rider, beat(pr_s), ghost, "PR", pacer, w_left)
    if kom_s and best <= beat(kom_s):
        pacer = Pacer(sections, rider, beat(kom_s), None, "KOM", pacer, w_left)
    return pacer


def ftp_out_of_date(rider, pr_s, pr_watts):
    """True when the PR ride's measured average power beats what the Garmin FTP says is possible for that long.
    Shown before the climb as a warning only: one ride (a tailwind, a one-sided meter) never changes the FTP."""
    return bool(pr_s and pr_watts and pr_watts > rider.curve(pr_s))


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
    ap.add_argument("--gender", choices=["male", "female"], help="from the Garmin profile: chase the KOM or the QOM; "
                    "unset chases no course record")
    ap.add_argument("--kom", help="KOM or QOM time for a CSV profile, e.g. 9:00")
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
        if seg.get("hazardous"):
            raise SystemExit(f"Strava marks {seg['name']} as hazardous: EdgePacer doesn't pace it.")
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
    record = {"male": "kom_s", "female": "qom_s"}.get(a.gender)
    kom = parse_time(a.kom) if a.kom else seg and record and seg[record]
    pr = parse_time(a.pr) if a.pr else seg and seg["pr_s"]
    if seg and ftp_out_of_date(rider, pr, seg.get("pr_watts")):
        print(f"FTP looks out of date: your PR ride here averaged {seg['pr_watts']:.0f} W. Update it in Garmin Connect.")
    pacer = choose(secs, rider, kom, pr, seg and seg.get("ghost"))
    _, best = fastest(pacer.lengths, pacer.ab, rider)
    chain, p = [], pacer
    while p:
        chain.append(p.name)
        p = p.next

    print(f"Fastest your FTP allows: {fmt_time(best)}" + (f" (record {fmt_time(kom)})" if kom else ""))
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
