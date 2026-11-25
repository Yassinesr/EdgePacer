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
MAX_FTP = 1.5        # never ask for more than 1.5 x FTP, whatever the reserve says
STEADY_S = 20.0      # s of steady riding on the climb before watts come from the rider's own power
RESTART_S = 10.0     # s after a stop with no watts shown, so nobody sprints away from a red light
NEWTON = 8           # Newton steps in speed_for: converged to a mm/s from CAP on any grade
STEP_M = 10.0        # m, integration step while a fast entry slows to the climb's steady speed
DROP_S = 10.0        # s a target must stay out of reach before it is dropped: one GPS jump mustn't cost the PR
RATIO = (0.8, 1.25)  # the own-power correction stays within this: wind and drafting aren't the model's error


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

    def inertia(self):
        """Rider and bike mass over drivetrain efficiency: kinetic energy in the meter's watts."""
        return (self.rider_kg + self.bike_kg) / self.eff

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


def every(dist, step):
    """Indexes of one point every `step` metres along `dist`, plus the last: what the Edge keeps in memory."""
    idx = [0]
    for i in range(1, len(dist)):
        if dist[i] - dist[idx[-1]] >= step:
            idx.append(i)
    if idx[-1] != len(dist) - 1:
        idx.append(len(dist) - 1)
    return idx


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
    """Steady speed (m/s) for meter power p, capped at CAP. Power is convex in speed, so Newton steps down from CAP land
    on the one speed below it that costs p; 8 steps where bisection takes 40, which the Edge's time limit needs."""
    if a * CAP + b * CAP ** 3 <= p:
        return CAP
    v = CAP
    for _ in range(NEWTON):
        v -= (a * v + b * v ** 3 - p) / (a + 3 * b * v * v)
    return v


def section_times(p, lengths, ab, v0=None, inertia=0.0):
    """Seconds per section at even meter power p. Entering at v0 m/s (inertia: total mass / drivetrain efficiency):
    a fast entry coasts down to the steady speed, integrated in STEP_M steps, which saves time; a slow one pays the
    energy to speed up at power p. After that every section is ridden at its steady speed."""
    ts, v = [], v0
    for length, (a, b) in zip(lengths, ab):
        vs, t, x = speed_for(p, a, b), 0.0, 0.0
        if v is not None and v < vs:
            # shortcut: ignores the air drag while speeding up, up to 3 s pessimistic on a fast flat start; integrate if it matters
            t, v = 0.5 * inertia * (vs * vs - v * v) / p, None
        while v is not None and v - vs >= 0.1 and x < length:
            dx = min(STEP_M, length - x)
            vm = max(v + (p - a * v - b * v ** 3) / (inertia * v * v) * dx / 2, vs)  # midpoint step
            v = max(v + (p - a * vm - b * vm ** 3) / (inertia * vm * vm) * dx, vs)
            t, x = t + dx / vm, x + dx
        if v is not None and v - vs < 0.1:
            v = None  # settled
        ts.append(t + (length - x) / vs)
    return ts


def ride_time(p, lengths, ab, v0=None, inertia=0.0):
    """Seconds to ride these sections at even power p, entering at v0 (section_times)."""
    return sum(section_times(p, lengths, ab, v0, inertia))


def solve(lengths, ab, time_left, v0=None, inertia=0.0):
    """Even power that rides these sections in time_left, or None if it needs more than MAX_P."""
    if not lengths or time_left <= 0 or ride_time(MAX_P, lengths, ab, v0, inertia) > time_left:
        return None
    lo, hi = 0.0, MAX_P
    for _ in range(40):
        p = (lo + hi) / 2
        if ride_time(p, lengths, ab, v0, inertia) > time_left:
            lo = p
        else:
            hi = p
    return hi


def fastest(lengths, ab, rider, w_left=None, v0=None):
    """(watts, seconds): the fastest even-power ride the rider can hold on these sections with this much reserve left,
    entering at v0 m/s if given."""
    lo, hi = 1.0, 4 * 3600.0
    for _ in range(40):
        t = (lo + hi) / 2
        if ride_time(rider.max_power(t, w_left), lengths, ab, v0, rider.inertia()) > t:
            lo = t
        else:
            hi = t
    return rider.max_power(hi, w_left), hi


def beat(record_s, margin=0.01, min_s=2.0):
    """Target time: the record minus 1 %, at least 2 s, since Strava's GPS timing differs from the Edge's by 1-3 s."""
    return record_s - max(min_s, margin * record_s)


def entry_speed(sections, rider):
    """The speed to arrive at: riding in at FTP on a flat road, as fast as possible without touching the reserve the
    climb needs (above FTP). Strava's clock starts at the line, so the run-in is free speed."""
    # shortcut: the run-in's grade isn't in the segment, so it is taken as flat; add the road before the start if starts on a hill matter
    return speed_for(rider.ftp, *coeffs(Section(1.0, 0.0, sections[0].alt), rider))


class Pacer:
    """Live pacing on one segment at even power, toward one target.
    The reference curve says where the rider must be at each metre to finish exactly at target_s: the PR ride (ghost,
    {"distance": [...], "time": [...]} from the segment start) when given, else this plan, either one scaled to
    end at target_s. Every gap reads from it. The watts never exceed what the rider can hold (Rider.max_power).
    next: the target to fall back to, for good, once this one is out of reach. w_left: the reserve at the start line.
    v0: the speed at the start line, if known (entry_speed); the plan then uses the momentum.
    d0, t0: where and when this plan starts, for a fallback that starts mid-climb (restart)."""

    def __init__(self, sections, rider, target_s, ghost=None, name="Target", next=None, w_left=None, v0=None):
        self.rider, self.target_s, self.name, self.next, self.dropped = rider, target_s, name, next, False
        self.sections, self.d0, self.t0, self.out_since = sections, 0.0, 0.0, None
        self.lengths = [s.length for s in sections]
        self.ab = [coeffs(s, rider) for s in sections]
        self.power = self._watts(self.lengths, self.ab, target_s, w_left, v0)
        if ghost:
            d, t = clean(ghost["distance"], ghost["time"])
        else:
            d, t = [0.0], [0.0]
            for length, secs in zip(self.lengths, section_times(self.power, self.lengths, self.ab, v0, rider.inertia())):
                d.append(d[-1] + length)
                t.append(t[-1] + secs)
        scale_d, scale_t = sum(self.lengths) / d[-1], target_s / t[-1]  # the recording measures the climb a little differently
        self.curve = ([x * scale_d for x in d], [x * scale_t for x in t])
        self.ghost = bool(ghost)

    def _need_and_cap(self, lengths, ab, time_left, w_left=None, v0=None):
        cap, _ = fastest(lengths, ab, self.rider, w_left, v0)  # one ceiling for the watts shown and for "out of reach"
        return solve(lengths, ab, time_left, v0, self.rider.inertia()), cap

    def _watts(self, lengths, ab, time_left, w_left=None, v0=None):
        need, cap = self._need_and_cap(lengths, ab, time_left, w_left, v0)
        return cap if need is None else min(need, cap)

    def planned_time(self, done_m):
        """Seconds the reference curve takes to reach done_m."""
        return interp(done_m, *self.curve)

    def now(self, done_m, elapsed_s, w_left=None, power=None, speed=None, steady_s=0.0, since_stop_s=None):
        """(target name, watts to hold or None, gap in s: + ahead of the target pace, - behind).
        elapsed_s runs from the start line and keeps running through stops, like Strava's elapsed time.
        w_left: the rider's reserve right now (Rider.w_after), full if unknown.
        Ahead: the plan's watts, to bank the time. Behind: the even power that still finishes on time; once even the
        most the rider can hold can't make it for DROP_S, drop to the next target for good (the most they can hold
        meanwhile).
        power, speed, steady_s: the rider's smoothed power and speed and how long they have ridden steadily; after
        STEADY_S the watts are their own power scaled to the speed needed, which cancels the model's defaults.
        since_stop_s: seconds since the rider last got going after a stop on this climb; no watts for RESTART_S."""
        if self.dropped:
            return self.next.now(done_m, elapsed_s, w_left, power, speed, steady_s, since_stop_s)
        here, since = done_m - self.d0, elapsed_s - self.t0
        gap = self.planned_time(here) - since
        lengths, ab, start = [], [], 0.0
        for length, coef in zip(self.lengths, self.ab):
            left = start + length - here
            if left > 1e-6:
                lengths.append(min(left, length))
                ab.append(coef)
            start += length
        if not lengths:
            return self.name, None, gap
        need, cap = self._need_and_cap(lengths, ab, self.target_s - since, w_left)
        out = gap < 0 and (need is None or need > cap)
        self.out_since = (self.out_since if self.out_since is not None else since) if out else None
        if gap >= 0:
            watts = min(self.power, cap)
        elif out and self.next and since - self.out_since >= DROP_S:
            self.dropped = True
            if self.next.next is None:  # the last target: the fastest from here on, not from the start line
                self.next = self.next.restart(done_m, elapsed_s, w_left, speed)
            return self.next.now(done_m, elapsed_s, w_left, power, speed, steady_s, since_stop_s)
        else:
            watts = cap if need is None else min(need, cap)
        if since_stop_s is not None and since_stop_s < RESTART_S:
            return self.name, None, gap
        a, b = ab[0]
        if power and speed and steady_s >= STEADY_S and a * speed + b * speed ** 3 > 0:
            # what the model says this rider needs, relative to what it says their current speed costs
            ratio = min(max(power / (a * speed + b * speed ** 3), RATIO[0]), RATIO[1])
            watts = min(cap, watts * ratio)
        return self.name, watts, gap


    def restart(self, done_m, elapsed_s, w_left=None, speed=None):
        """This target planned afresh from done_m at elapsed_s: the fastest the rider can still ride the rest, so its
        gap starts at zero. Falling back to the plan made at the start line would ask for even more."""
        rest, start = [], 0.0
        for s in self.sections:
            left = start + s.length - done_m
            if left > 1e-6:
                rest.append(replace(s, length=min(left, s.length)))
            start += s.length
        _, best = fastest([s.length for s in rest], [coeffs(s, self.rider) for s in rest], self.rider, w_left, speed)
        p = Pacer(rest, self.rider, best, name=self.name, w_left=w_left, v0=speed)
        p.d0, p.t0 = done_m, elapsed_s
        return p


def choose(sections, rider, kom_s=None, pr_s=None, ghost=None, w_left=None, v0=None):
    """What to chase, with no input from the rider: the KOM (or QOM) if they can beat it, then their PR if they can,
    then the fastest they can hold. Returns the first Pacer; the others are its fallbacks. v0: entry speed."""
    lengths, ab = [s.length for s in sections], [coeffs(s, rider) for s in sections]
    _, best = fastest(lengths, ab, rider, w_left, v0)
    pacer = Pacer(sections, rider, best, name="Fastest", w_left=w_left, v0=v0)
    if pr_s and best <= beat(pr_s):
        pacer = Pacer(sections, rider, beat(pr_s), ghost, "PR", pacer, w_left, v0)
    if kom_s and best <= beat(kom_s):
        pacer = Pacer(sections, rider, beat(kom_s), None, "KOM", pacer, w_left, v0)
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
    v_in = entry_speed(secs, rider)
    pacer = choose(secs, rider, kom, pr, seg and seg.get("ghost"), v0=v_in)
    _, best = fastest(pacer.lengths, pacer.ab, rider, v0=v_in)
    chain, p = [], pacer
    while p:
        chain.append(p.name)
        p = p.next

    print(f"Fastest your FTP allows: {fmt_time(best)}" + (f" (record {fmt_time(kom)})" if kom else ""))
    print(f"Chasing {' -> '.join(chain)}: aim {fmt_time(pacer.target_s)}, hold {pacer.power:.0f} W "
          f"({pacer.power / a.weight:.2f} W/kg)" + (", measured against your PR ride" if pacer.ghost else ""))
    print(f"Ride in at {rider.ftp:.0f} W (about {v_in * 3.6:.0f} km/h on the flat): the clock starts at the line.")
    print(f"{'km':>5} {'grade':>6} {'km/h':>6} {'be here at':>10}")
    done = 0.0
    for s, coef in zip(secs, pacer.ab):
        done += s.length
        v = speed_for(pacer.power, *coef)
        print(f"{done / 1000:5.2f} {s.grade * 100:5.1f}% {v * 3.6:6.1f} {fmt_time(pacer.planned_time(done)):>10}")


if __name__ == "__main__":
    main()
