import Toybox.Lang;
import Toybox.Math;

// Port of model/pacer.py, which holds the tests and the reasoning (docs/PLAN.md). Keep the two in step.
module Pacer {
    const G = 9.81;
    const CAP = 13.889;        // m/s, top speed on descents: corners and braking can't be modelled
    const MAX_P = 2000.0;      // W, a plan needing more is out of reach
    const ANAEROBIC_S = 70.0;  // W' in seconds of FTP: about 21 kJ at FTP 300
    const MAX_FTP = 1.5;       // never ask for more than 1.5 x FTP
    const STEADY_S = 20.0;     // s of steady riding before the watts come from the rider's own power
    const RESTART_S = 10.0;    // s after a stop with no watts shown
    const ITER = 20;           // bisection steps for power and time: 0.002 W, 0.01 s (the Python uses 40)
    const NEWTON = 8;          // Newton steps in speedFor, as in the Python
    const STEP_M = 10.0;       // m, integration step while a fast entry slows to the climb's steady speed
    const DROP_S = 10.0;       // s a target must stay out of reach before it is dropped: one GPS jump mustn't cost the PR
    const RATIO_MIN = 0.8;     // the own-power correction stays within these: wind and drafting aren't the model's error
    const RATIO_MAX = 1.25;

    class Rider {
        var kg, ftp, cda;
        var bikeKg = 9.0;      // bike + kit
        var crr = 0.005;       // average road
        var eff = 0.975;       // drivetrain

        function initialize(kg, ftp) {
            self.kg = kg;
            self.ftp = ftp;
            cda = 0.32 * Math.pow(kg / 72.0, 0.425);  // frontal area grows with mass^0.425 (Heil 2001)
        }

        // rider and bike mass over drivetrain efficiency: kinetic energy in the meter's watts
        function inertia() {
            return (kg + bikeKg) / eff;
        }

        function wPrime() {
            return ANAEROBIC_S * ftp;
        }

        // FTP plus the reserve above it (full unless given) spread over the effort; FTP at one hour
        function curve(seconds, wLeft) {
            var w = wLeft == null ? wPrime() : (wLeft > 0.0 ? wLeft : 0.0);
            var f = 1.0 / (seconds > 60.0 ? seconds : 60.0) - 1.0 / 3600.0;
            return ftp + w * (f > 0.0 ? f : 0.0);
        }

        function maxPower(seconds, wLeft) {
            var c = curve(seconds, wLeft);
            return c < MAX_FTP * ftp ? c : MAX_FTP * ftp;
        }

        // reserve left after `seconds` at `watts` (Skiba): drains above FTP, refills below it
        function wAfter(w, watts, seconds) {
            if (watts > ftp) {
                return w - (watts - ftp) * seconds;
            }
            return wPrime() - (wPrime() - w) * Math.pow(Math.E, -(ftp - watts) * seconds / wPrime());
        }
    }

    // [lengths, a, b, grades]: per section, meter power = a*v + b*v^3
    function prep(sec, rider) {
        var n = sec.size() / 3;
        var lengths = new [n], ca = new [n], cb = new [n], grades = new [n];
        var rho = 101325.0 / (287.05 * 288.15);  // standard air at 15 C; scaled by altitude below
        for (var i = 0; i < n; i++) {
            var grade = sec[3 * i + 1].toFloat();
            var alt = sec[3 * i + 2].toFloat();
            var ang = Math.atan(grade);
            lengths[i] = sec[3 * i].toFloat();
            grades[i] = grade;
            ca[i] = (rider.kg + rider.bikeKg) * G * (Math.sin(ang) + rider.crr * Math.cos(ang)) / rider.eff;
            cb[i] = 0.5 * rho * Math.pow(1.0 - 2.25577e-5 * alt, 5.25588) * rider.cda / rider.eff;
        }
        return [lengths, ca, cb, grades];
    }

    // steady speed for meter power p, capped at CAP; power is convex in speed, so Newton steps down from CAP converge
    function speedFor(p, a, b) {
        if (a * CAP + b * CAP * CAP * CAP <= p) {
            return CAP;
        }
        var v = CAP;
        for (var i = 0; i < NEWTON; i++) {
            v -= (a * v + b * v * v * v - p) / (a + 3.0 * b * v * v);
        }
        return v;
    }

    // seconds per section at even meter power p, entering at v0 (null: steady from the line); see section_times
    function sectionTimes(p, lengths, ca, cb, v0, inertia) {
        var n = lengths.size(), ts = new [n], v = v0;
        for (var i = 0; i < n; i++) {
            var a = ca[i], b = cb[i], len = lengths[i], vs = speedFor(p, a, b), t = 0.0, x = 0.0;
            if (v != null && v < vs) {
                t = 0.5 * inertia * (vs * vs - v * v) / p;  // slow entry: the energy to speed up
                v = null;
            }
            while (v != null && v - vs >= 0.1 && x < len) {  // fast entry: coast down to the steady speed
                var dx = len - x < STEP_M ? len - x : STEP_M;
                var vm = v + (p - a * v - b * v * v * v) / (inertia * v * v) * dx / 2.0;
                vm = vm > vs ? vm : vs;
                v = v + (p - a * vm - b * vm * vm * vm) / (inertia * vm * vm) * dx;
                v = v > vs ? v : vs;
                t += dx / vm;
                x += dx;
            }
            if (v != null && v - vs < 0.1) {
                v = null;  // settled
            }
            ts[i] = t + (len - x) / vs;
        }
        return ts;
    }

    function rideTime(p, lengths, ca, cb, v0, inertia) {
        var t = 0.0;
        if (v0 != null) {
            var ts = sectionTimes(p, lengths, ca, cb, v0, inertia);
            for (var i = 0; i < ts.size(); i++) {
                t += ts[i];
            }
            return t;
        }
        for (var i = 0; i < lengths.size(); i++) {
            t += lengths[i] / speedFor(p, ca[i], cb[i]);
        }
        return t;
    }

    // even power that rides these sections in timeLeft, or null if it needs more than MAX_P
    function solve(lengths, ca, cb, timeLeft, v0, inertia) {
        if (lengths.size() == 0 || timeLeft <= 0 || rideTime(MAX_P, lengths, ca, cb, v0, inertia) > timeLeft) {
            return null;
        }
        var lo = 0.0, hi = MAX_P;
        for (var i = 0; i < ITER; i++) {
            var p = (lo + hi) / 2.0;
            if (rideTime(p, lengths, ca, cb, v0, inertia) > timeLeft) {
                lo = p;
            } else {
                hi = p;
            }
        }
        return hi;
    }

    // [watts, seconds]: the fastest even-power ride the rider can hold with this much reserve left
    function fastest(lengths, ca, cb, rider, wLeft, v0) {
        var lo = 1.0, hi = 14400.0;
        for (var i = 0; i < ITER; i++) {
            var t = (lo + hi) / 2.0;
            if (rideTime(rider.maxPower(t, wLeft), lengths, ca, cb, v0, rider.inertia()) > t) {
                lo = t;
            } else {
                hi = t;
            }
        }
        return [rider.maxPower(hi, wLeft), hi];
    }

    // the record minus 1 %, at least 2 s: Strava's GPS timing differs from the Edge's by 1-3 s
    function beat(record) {
        var m = 0.01 * record;
        return record - (m > 2.0 ? m : 2.0);
    }

    // linear interpolation in increasing xs; the end pieces extend past the data
    function interp(x, xs, ys) {
        var n = xs.size();
        var i = 1;
        while (i < n - 1 && xs[i] <= x) {
            i++;
        }
        return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - xs[i - 1]) / (xs[i] - xs[i - 1]);
    }

    // One target on one climb: the reference curve every gap reads from, and the watts to hold.
    class Target {
        var letter, targetS, power, fallback, rider, p, curveD, curveT;
        var dropped = false;
        var d0 = 0.0, t0 = 0.0;  // where and when this plan starts: not the start line for a fresh fallback (restart)
        var outSince = null;     // when this target went out of reach, while it waits DROP_S to be dropped

        // power: the even watts for targetS, from choose; ghost: flat [distance, time, ...] of the PR ride, or null;
        // v0: the speed at the start line, or null
        function initialize(p, rider, targetS, power, ghost, letter, fallback, v0) {
            self.p = p;
            self.rider = rider;
            self.targetS = targetS.toFloat();
            self.power = power;
            self.letter = letter;
            self.fallback = fallback;
            curveD = [0.0];
            curveT = [0.0];
            var total = 0.0, ts = sectionTimes(power, p[0], p[1], p[2], v0, rider.inertia());
            for (var i = 0; i < p[0].size(); i++) {
                total += p[0][i];
                if (ghost == null) {
                    curveD.add(total);
                    curveT.add(curveT[i] + ts[i]);
                }
            }
            if (ghost != null) {
                curveD = [];
                curveT = [];
                for (var i = 0; i < ghost.size(); i += 2) {
                    curveD.add(ghost[i].toFloat());
                    curveT.add(ghost[i + 1].toFloat());
                }
            }
            // scale to the climb's length and to end exactly at the target time
            var sd = total / curveD[curveD.size() - 1], st = self.targetS / curveT[curveT.size() - 1];
            for (var i = 0; i < curveD.size(); i++) {
                curveD[i] = curveD[i] * sd;
                curveT[i] = curveT[i] * st;
            }
        }

        // [need, cap]: one ceiling for the watts shown and for "out of reach"
        function needAndCap(lengths, ca, cb, timeLeft, wLeft) {
            return [solve(lengths, ca, cb, timeLeft, null, 0.0), fastest(lengths, ca, cb, rider, wLeft, null)[0]];
        }

        function plannedTime(done) {
            return interp(done, curveD, curveT);
        }

        function gradeAt(done) {
            var start = 0.0;
            for (var i = 0; i < p[0].size(); i++) {
                start += p[0][i];
                if (done < start) {
                    return p[3][i];
                }
            }
            return p[3][p[3].size() - 1];
        }

        // [letter, watts or null, gap s (+ ahead, - behind)]; see Pacer.now in model/pacer.py
        function now(done, elapsed, wLeft, pw, speed, steadyS, sinceStopS) {
            if (dropped) {
                return fallback.now(done, elapsed, wLeft, pw, speed, steadyS, sinceStopS);
            }
            var here = done - d0, since = elapsed - t0;
            var gap = plannedTime(here) - since;
            var rl = [] as Array, ra = [] as Array, rb = [] as Array;  // typed: the checker reads [] as always empty
            var start = 0.0;
            for (var i = 0; i < p[0].size(); i++) {
                var left = start + p[0][i] - here;
                if (left > 0.000001) {
                    rl.add(left < p[0][i] ? left : p[0][i]);
                    ra.add(p[1][i]);
                    rb.add(p[2][i]);
                }
                start += p[0][i];
            }
            if (rl.size() == 0) {
                return [letter, null, gap];
            }
            // shortcut: need and cap are recomputed every second; recompute only when the gap moves if the watchdog trips
            var nc = needAndCap(rl, ra, rb, targetS - since, wLeft);
            var need = nc[0], cap = nc[1], w;
            var out = gap < 0 && (need == null || need > cap);
            outSince = out ? (outSince != null ? outSince : since) : null;
            if (gap >= 0) {
                w = power < cap ? power : cap;
            } else if (out && fallback != null && since - outSince >= DROP_S) {
                dropped = true;
                if (fallback.fallback == null) {
                    fallback = fallback.restart(done, elapsed, wLeft, speed);  // the fastest from here, not from the start line
                }
                return fallback.now(done, elapsed, wLeft, pw, speed, steadyS, sinceStopS);
            } else {
                w = (need == null || need > cap) ? cap : need;
            }
            if (sinceStopS != null && sinceStopS < RESTART_S) {
                return [letter, null, gap];
            }
            var a = ra[0], b = rb[0];
            if (pw != null && pw > 0 && speed != null && speed > 0 && steadyS >= STEADY_S && a * speed + b * speed * speed * speed > 0) {
                // the rider's own power, scaled by what the model says the needed speed costs vs their current speed
                var ratio = pw / (a * speed + b * speed * speed * speed);
                ratio = ratio < RATIO_MIN ? RATIO_MIN : (ratio > RATIO_MAX ? RATIO_MAX : ratio);
                w = w * ratio < cap ? w * ratio : cap;
            }
            return [letter, w, gap];
        }

        // this target planned afresh from `done` at `elapsed`: the fastest the rest can be ridden, gap starting at zero
        function restart(done, elapsed, wLeft, speed) {
            var rl = [] as Array, ra = [] as Array, rb = [] as Array, rg = [] as Array;
            var start = 0.0;
            for (var i = 0; i < p[0].size(); i++) {
                var left = start + p[0][i] - done;
                if (left > 0.000001) {
                    rl.add(left < p[0][i] ? left : p[0][i]);
                    ra.add(p[1][i]);
                    rb.add(p[2][i]);
                    rg.add(p[3][i]);
                }
                start += p[0][i];
            }
            var f = fastest(rl, ra, rb, rider, wLeft, speed);
            var t = new Target([rl, ra, rb, rg], rider, f[1], f[0], null, letter, null, speed);
            t.d0 = done;
            t.t0 = elapsed;
            return t;
        }

        // what the model says riding at v costs on the section at `done`, for the ride file's correction ratio
        function powerAt(done, v) {
            var start = 0.0;
            for (var i = 0; i < p[0].size(); i++) {
                start += p[0][i];
                if (done < start) {
                    return p[1][i] * v + p[2][i] * v * v * v;
                }
            }
            return 0.0;
        }

        // the model's speed at the current watts, for the ride file
        function speedAt(done, w) {
            var start = 0.0;
            for (var i = 0; i < p[0].size(); i++) {
                start += p[0][i];
                if (done < start) {
                    return speedFor(w, p[1][i], p[2][i]);
                }
            }
            return 0.0;
        }
    }

    // KOM/QOM (letter K or Q) if the rider can beat it, then their PR, then the fastest they can hold, all planned
    // for riding in at FTP on the flat (entry_speed in the model). A target slower than the fastest ride needs at
    // most the fastest watts, so one search serves all three (the Python recomputes it per target; same numbers).
    function choose(sec, rider, record, recordLetter, pr, ghost, wLeft) {
        var p = prep(sec, rider);
        var v0 = speedFor(rider.ftp, (rider.kg + rider.bikeKg) * G * rider.crr / rider.eff, p[2][0]);
        var f = fastest(p[0], p[1], p[2], rider, wLeft, v0);
        var t = new Target(p, rider, f[1], f[0], null, "F", null, v0);
        if (pr != null && f[1] <= beat(pr)) {
            t = new Target(p, rider, beat(pr), even(p, beat(pr), f[0], v0, rider), ghost, "P", t, v0);
        }
        if (record != null && f[1] <= beat(record)) {
            t = new Target(p, rider, beat(record), even(p, beat(record), f[0], v0, rider), null, recordLetter, t, v0);
        }
        return t;
    }

    function even(p, targetS, cap, v0, rider) {
        var need = solve(p[0], p[1], p[2], targetS, v0, rider.inertia());
        return (need == null || need > cap) ? cap : need;
    }

    // the PR ride's measured average power beats what the Garmin FTP says is possible: a warning only
    function ftpOutOfDate(rider, pr, prw) {
        return pr != null && prw != null && prw > rider.curve(pr.toFloat(), null);
    }
}
