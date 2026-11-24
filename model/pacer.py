"""Power targets for each part of a climb, to ride it in a target time (beat a PR or the KOM).

Physics per section at steady speed, in still air (no wind):
    power * efficiency = (m*g*(sin a + crr*cos a) + 0.5*rho*cda*v^2) * v

This is the reference model the Edge data field will port to Monkey C.

Usage:
    python3 pacer.py examples/test-climb.csv 9:30 --rider 72 --bike 8
"""
import argparse
import bisect
import csv
import math
from dataclasses import dataclass

G = 9.81


@dataclass
class Rider:
    rider_kg: float
    bike_kg: float = 8.0
    cda: float = 0.32    # m^2, on the hoods
    crr: float = 0.004   # good tyres on smooth tarmac
    eff: float = 0.97    # drivetrain
    temp_c: float = 15.0

    @property
    def mass(self):
        return self.rider_kg + self.bike_kg


@dataclass
class Section:
    length: float  # m
    grade: float   # rise / run
    alt: float     # m, middle of the section


def air_density(alt_m, temp_c):
    pressure = 101325 * (1 - 2.25577e-5 * alt_m) ** 5.25588
    return pressure / (287.05 * (temp_c + 273.15))


def power_at(v, s, r):
    a = math.atan(s.grade)
    force = r.mass * G * (math.sin(a) + r.crr * math.cos(a)) + 0.5 * air_density(s.alt, r.temp_c) * r.cda * v * v
    return force * v / r.eff


def speed_at(p, s, r, vmax=25.0):
    """Steady speed (m/s) for power p on section s. Power rises with speed past any dip, so bisection is safe."""
    lo, hi = 0.0, vmax
    if power_at(hi, s, r) <= p:
        return hi
    for _ in range(50):
        mid = (lo + hi) / 2
        if power_at(mid, s, r) < p:
            lo = mid
        else:
            hi = mid
    return hi


def sections_from_profile(dist, alt, step=100.0):
    """Resample a (distance, altitude) profile into sections of about `step` metres."""
    total = dist[-1]
    marks = [i * step for i in range(int(total // step) + 1)]
    if total - marks[-1] < step / 2 and len(marks) > 1:
        marks[-1] = total  # fold a short tail into the last section
    else:
        marks.append(total)

    def elev(d):
        i = min(max(bisect.bisect_right(dist, d), 1), len(dist) - 1)
        d0, d1 = dist[i - 1], dist[i]
        return alt[i - 1] + (alt[i] - alt[i - 1]) * (d - d0) / (d1 - d0)

    e = [elev(m) for m in marks]
    return [Section(b - a, (e1 - e0) / (b - a), (e0 + e1) / 2)
            for a, b, e0, e1 in zip(marks, marks[1:], e, e[1:])]


class Pacer:
    """bias: extra power share per unit of grade above the climb's average.
    1.5 means +1.5 % power for each 1 % of grade steeper than average (capped at +/-20 %).
    Pushing a bit harder where you are slow saves time; 0 means even power."""

    def __init__(self, sections, target_s, rider, bias=1.5):
        self.sections, self.target_s, self.rider = sections, target_s, rider
        mean = sum(s.grade * s.length for s in sections) / sum(s.length for s in sections)
        self.factors = [min(1.2, max(0.8, 1 + bias * (s.grade - mean))) for s in sections]

    def _ride_time(self, base, rest):
        return sum(s.length / speed_at(base * k, s, self.rider) for s, k in rest)

    def targets(self, done_m=0.0, elapsed_s=0.0):
        """Power for each remaining section so you finish on target from here.
        Returns [(section, power_w)], or None if the target is out of reach (over 2000 W average)."""
        rest, start = [], 0.0
        for s, k in zip(self.sections, self.factors):
            left = start + s.length - done_m
            if left > 1e-9:
                rest.append((Section(min(left, s.length), s.grade, s.alt), k))
            start += s.length
        time_left = self.target_s - elapsed_s
        if not rest or time_left <= 0 or self._ride_time(2000, rest) > time_left:
            return None
        lo, hi = 0.0, 2000.0
        for _ in range(50):
            mid = (lo + hi) / 2
            if self._ride_time(mid, rest) > time_left:
                lo = mid
            else:
                hi = mid
        return [(s, hi * k) for s, k in rest]


def parse_time(text):
    secs = 0
    for part in text.split(":"):
        secs = secs * 60 + float(part)
    return secs


def fmt_time(secs):
    return f"{int(secs // 60)}:{secs % 60:04.1f}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile", help="CSV with distance_m,altitude_m columns")
    ap.add_argument("target", help="target time, e.g. 9:30")
    ap.add_argument("--rider", type=float, required=True, help="rider weight, kg")
    ap.add_argument("--bike", type=float, default=8.0, help="bike + kit weight, kg")
    ap.add_argument("--cda", type=float, default=0.32)
    ap.add_argument("--crr", type=float, default=0.004)
    ap.add_argument("--temp", type=float, default=15.0, help="air temperature, C")
    ap.add_argument("--bias", type=float, default=1.5, help="0 = even power")
    ap.add_argument("--step", type=float, default=100.0, help="section length, m")
    a = ap.parse_args()

    with open(a.profile, newline="") as f:
        rows = [(float(r["distance_m"]), float(r["altitude_m"])) for r in csv.DictReader(f)]
    secs = sections_from_profile([d for d, _ in rows], [h for _, h in rows], a.step)
    rider = Rider(a.rider, a.bike, a.cda, a.crr, temp_c=a.temp)
    target = parse_time(a.target)
    plan = Pacer(secs, target, rider, a.bias).targets()
    if plan is None:
        raise SystemExit(f"{fmt_time(target)} is out of reach on this climb.")

    print(f"{'km':>5} {'grade':>6} {'target W':>9} {'km/h':>6} {'time':>7}")
    done = clock = work = 0.0
    for s, p in plan:
        v = speed_at(p, s, rider)
        done, clock, work = done + s.length, clock + s.length / v, work + p * s.length / v
        print(f"{done / 1000:5.2f} {s.grade * 100:5.1f}% {p:9.0f} {v * 3.6:6.1f} {fmt_time(clock):>7}")
    avg = work / clock
    print(f"\nAverage {avg:.0f} W ({avg / a.rider:.2f} W/kg) to ride it in {fmt_time(clock)}.")


if __name__ == "__main__":
    main()
