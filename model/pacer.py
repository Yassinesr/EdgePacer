"""EdgePacer pacing model: watts for each part of a Strava climb to beat your PR or the KOM.

Reference for the Edge data field; the logic is explained in docs/PLAN.md.
Physics per section at steady speed, in still air (no wind):
    P_meter * eff * k = v * (m*g*(sin a + crr*cos a) + 0.5*rho*cda*v^2)

Usage:
    python3 pacer.py examples/test-climb.csv --rider 72 --cp 320 --kom 9:00 --pr 9:50
"""
import argparse
import bisect
import csv
import math
import statistics
from dataclasses import dataclass, replace

G = 9.81
CAP = 50 / 3.6   # m/s, top speed on descents: corners and braking can't be modelled
MAX_Q = 2000.0   # W, a plan needing more is out of reach


@dataclass
class Rider:
    rider_kg: float
    bike_kg: float = 9.0      # bike + kit
    cda: float = 0.32         # m^2, on the hoods
    crr: float = 0.004        # smooth tarmac
    eff: float = 0.975        # drivetrain
    k: float = 1.0            # calibration factor, see calibrate()
    temp_c: float = 15.0
    cp: float = 0.0           # critical power, W; 0 turns the "can you do it?" check off
    w_prime: float = 20000.0  # J of work you can do above CP


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
    scale = r.eff * r.k
    a = (r.rider_kg + r.bike_kg) * G * (math.sin(ang) + r.crr * math.cos(ang))
    return a / scale, 0.5 * air_density(s.alt, r.temp_c) * r.cda / scale


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


def ride(q, ab, even=False):
    """[(power, speed)] per section for reference power q.
    even: q everywhere. Otherwise the lowest-normalized-power rule P^3 * (3P + 8*P_air) = 3*q^4 (PLAN 4.2),
    so q is the power on a section where air drag doesn't matter. Coasts (0 W) where the rule says less."""
    goal, out = 3 * q ** 4, []
    for a, b in ab:
        if even:
            v = speed_for(q, a, b)
        else:
            p = a * CAP + b * CAP ** 3
            if p <= 0 or p ** 3 * (3 * p + 8 * b * CAP ** 3) <= goal:
                v = CAP
            else:
                lo, hi = 0.0, CAP
                for _ in range(40):
                    mid = (lo + hi) / 2
                    p = a * mid + b * mid ** 3
                    if p <= 0 or p ** 3 * (3 * p + 8 * b * mid ** 3) < goal:
                        lo = mid
                    else:
                        hi = mid
                v = hi
        out.append((max(0.0, a * v + b * v ** 3), v))
    return out


def solve(lengths, ab, time_left, even=False):
    """[(power, speed)] that rides these sections in time_left, or None if it needs more than MAX_Q."""
    # shortcut: no per-section cap of CP + W'/time (PLAN 4.2); the rule varies power so little that the W' check covers it.
    def ride_time(q):
        return sum(length / v for length, (_, v) in zip(lengths, ride(q, ab, even)))

    if not lengths or time_left <= 0 or ride_time(MAX_Q) > time_left:
        return None
    lo, hi = 0.0, MAX_Q
    for _ in range(40):
        q = (lo + hi) / 2
        if ride_time(q) > time_left:
            lo = q
        else:
            hi = q
    return ride(hi, ab, even)


def wbal_low(steps, cp, w_prime, w_start=None):
    """Lowest W' balance (J) over (watts, seconds) steps, Skiba's model (PLAN 7)."""
    w = low = w_prime if w_start is None else w_start
    for p, t in steps:
        if p > cp:
            w -= (p - cp) * t
        else:
            w = w_prime - (w_prime - w) * math.exp(-(cp - p) * t / w_prime)
        low = min(low, w)
    return low


def verdict(low, w_prime):
    return "comfortable" if low >= w_prime / 2 else "hard" if low >= 0 else "out of reach"


def beat(record_s, margin=0.01, min_s=2.0):
    """Target time: the record minus 1 %, at least 2 s, since Strava's GPS timing differs from the Edge's by 1-3 s."""
    return record_s - max(min_s, margin * record_s)


class Pacer:
    """Plan for one segment and target time. plan is [(power, speed)] per section, or None if out of reach.
    hold: when ahead, keep the original plan's watts to bank the time (PLAN 6.3)."""

    def __init__(self, sections, rider, target_s, even=False, hold=True):
        self.sections, self.rider, self.target_s, self.even, self.hold = sections, rider, target_s, even, hold
        self.ab = [coeffs(s, rider) for s in sections]
        self.plan = solve([s.length for s in sections], self.ab, target_s, even)

    def _find(self, done_m):
        """Index of the section at done_m, and where it starts."""
        start = 0.0
        for i, s in enumerate(self.sections[:-1]):
            if done_m < start + s.length:
                return i, start
            start += s.length
        return len(self.sections) - 1, start

    def planned_time(self, done_m):
        """Seconds the plan takes to reach done_m."""
        i, start = self._find(done_m)
        before = sum(s.length / v for s, (_, v) in zip(self.sections[:i], self.plan))
        return before + (min(done_m, start + self.sections[i].length) - start) / self.plan[i][1]

    def targets(self, done_m=0.0, elapsed_s=0.0):
        """[(section, watts)] for the rest of the climb to finish on target from here, or None if out of reach."""
        rest, start = [], 0.0
        for s, ab in zip(self.sections, self.ab):
            left = start + s.length - done_m
            if left > 1e-6:
                rest.append((Section(min(left, s.length), s.grade, s.alt), ab))
            start += s.length
        plan = solve([s.length for s, _ in rest], [ab for _, ab in rest], self.target_s - elapsed_s, self.even)
        return None if plan is None else [(s, p) for (s, _), (p, _) in zip(rest, plan)]

    def now(self, done_m, elapsed_s):
        """(target watts here, gap in s: + ahead, - behind). Watts are None once the target is out of reach."""
        gap = self.planned_time(done_m) - elapsed_s
        if gap >= 0 and self.hold:
            return self.plan[self._find(done_m)[0]][0], gap
        rest = self.targets(done_m, elapsed_s)
        return (rest[0][1] if rest else None), gap

    def w_low(self, w_start=None):
        steps = [(p, s.length / v) for s, (p, v) in zip(self.sections, self.plan)]
        return wbal_low(steps, self.rider.cp, self.rider.w_prime, w_start)


def best_possible(sections, rider, even=False):
    """Fastest plan whose W' balance never drops below 0. Needs rider.cp."""
    ab = [coeffs(s, rider) for s in sections]
    lo, hi = 0.0, MAX_Q
    for _ in range(40):
        q = (lo + hi) / 2
        steps = [(p, s.length / v) for s, (p, v) in zip(sections, ride(q, ab, even))]
        if wbal_low(steps, rider.cp, rider.w_prime) >= 0:
            lo = q
        else:
            hi = q
    return Pacer(sections, rider, sum(s.length / v for s, (_, v) in zip(sections, ride(lo, ab, even))), even)


def choose(sections, rider, records, even=False):
    """Target ladder (PLAN 5): the first record, in order like [("KOM", s), ("PR", s)], you can beat.
    Falls back to ("Best possible", pacer) when rider.cp is set, else (None, None)."""
    for name, record in records:
        p = Pacer(sections, rider, beat(record), even)
        if p.plan and (not rider.cp or p.w_low() >= 0):
            return name, p
    return ("Best possible", best_possible(sections, rider, even)) if rider.cp else (None, None)


def effort_sections(sections, t, d, p):
    """Per-section (seconds, average watts) of a recorded effort.
    t, d, p: time (s), distance from the segment start (m) and power (W) samples."""
    energy = [0.0]
    for i in range(1, len(t)):
        energy.append(energy[-1] + p[i] * (t[i] - t[i - 1]))  # a sample covers the time since the one before
    td, et = clean(t, energy)
    dd, dt = clean(d, t)  # time at a distance: the first moment you got there
    out, start = [], 0.0
    for s in sections:
        t0, t1 = interp(start, dd, dt), interp(start + s.length, dd, dt)
        out.append((t1 - t0, (interp(t1, td, et) - interp(t0, td, et)) / (t1 - t0)))
        start += s.length
    return out


def calibrate(sections, rider, efforts):
    """Factor k that makes the model ride your recorded efforts in their real time; median over efforts (PLAN 8).
    efforts: effort_sections() results."""
    # shortcut: fits k only; also fit CdA once the efforts cover both slow and fast sections.
    ks = []
    for effort in efforts:
        actual = sum(t for t, _ in effort)
        lo, hi = 0.5, 2.0
        for _ in range(40):
            k = (lo + hi) / 2
            r = replace(rider, k=k)
            predicted = sum(s.length / speed_for(w, *coeffs(s, r)) for s, (_, w) in zip(sections, effort))
            if predicted > actual:
                lo = k
            else:
                hi = k
        ks.append(hi)
    return statistics.median(ks)


def parse_time(text):
    secs = 0
    for part in text.split(":"):
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
    ap.add_argument("profile", help="CSV with distance_m,altitude_m columns")
    ap.add_argument("--rider", type=float, required=True, help="rider weight, kg")
    ap.add_argument("--bike", type=float, default=9.0, help="bike + kit, kg")
    ap.add_argument("--cda", type=float, default=0.32)
    ap.add_argument("--crr", type=float, default=0.004)
    ap.add_argument("--temp", type=float, default=15.0, help="air temperature, C")
    ap.add_argument("--cp", type=float, default=0.0, help="critical power, about your FTP, W")
    ap.add_argument("--wprime", type=float, default=20.0, help="W', kJ")
    ap.add_argument("--kom", help="KOM or QOM time, e.g. 9:00")
    ap.add_argument("--pr", help="your PR time")
    ap.add_argument("--target", help="custom target time, used instead of KOM and PR")
    ap.add_argument("--even", action="store_true", help="even power instead of the lowest-effort rule")
    ap.add_argument("--effort", action="append", default=[],
                    help="CSV with time_s,distance_m,power_w of a past effort, to calibrate; repeat for more")
    a = ap.parse_args()

    secs = sections_from_profile(*read_csv(a.profile, "distance_m", "altitude_m"))
    rider = Rider(a.rider, a.bike, a.cda, a.crr, temp_c=a.temp, cp=a.cp, w_prime=a.wprime * 1000)
    if a.effort:
        efforts = [effort_sections(secs, *read_csv(f, "time_s", "distance_m", "power_w")) for f in a.effort]
        rider.k = calibrate(secs, rider, efforts)
        print(f"Calibration factor k = {rider.k:.3f} from {len(efforts)} effort(s)")

    if a.target:
        records = [("Target", parse_time(a.target))]
    else:
        records = [(name, parse_time(t)) for name, t in (("KOM", a.kom), ("PR", a.pr)) if t]
    if not records and not rider.cp:
        raise SystemExit("Give --kom, --pr or --target, or --cp to get your best possible time.")
    for name, record in records:
        p = Pacer(secs, rider, beat(record), a.even)
        if not p.plan:
            check = "out of reach"
        elif rider.cp:
            check = f"{verdict(p.w_low(), rider.w_prime)} (lowest W' {p.w_low() / 1000:.1f} kJ)"
        else:
            check = "possible (no W' check without --cp)"
        print(f"{name} {fmt_time(record)} -> aim for {fmt_time(beat(record))}: {check}")

    name, pacer = choose(secs, rider, records, a.even)
    if not pacer:
        raise SystemExit("Out of reach. Add --cp to get your best possible time.")
    print(f"\nPacing for {name}: {fmt_time(pacer.target_s)}")
    print(f"{'km':>5} {'grade':>6} {'target W':>9} {'km/h':>6} {'time':>7}")
    done = clock = work = 0.0
    for s, (p, v) in zip(pacer.sections, pacer.plan):
        done, clock, work = done + s.length, clock + s.length / v, work + p * s.length / v
        print(f"{done / 1000:5.2f} {s.grade * 100:5.1f}% {p:9.0f} {v * 3.6:6.1f} {fmt_time(clock):>7}")
    print(f"\nAverage {work / clock:.0f} W ({work / clock / a.rider:.2f} W/kg)")


if __name__ == "__main__":
    main()
