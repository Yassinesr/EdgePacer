import Toybox.Lang;
import Toybox.Math;

// Where the rider is on one climb: before its start line, how far along its path, past its finish.
// Flat-earth metres around the climb's first point, the same numbers model/export_edge.py used for the path.
class Tracker {
    const M_PER_DEG = 111320.0d;
    const START_M = 25.0;   // the start line is this wide each side of the first point
    const OFF_M = 50.0;     // further than this from the path is off it
    const OFF_S = 15;       // seconds off the path before the climb is abandoned

    var climb, path, pd, total, lat0, lon0, kx, ux, uy;
    var seg = 0;            // path piece the rider was last on
    var done = 0.0;         // metres along the climb, never backwards
    var off = 0;            // seconds in a row off the path
    var prevAlong = null;   // metres past the start line at the last fix

    // climb: one entry of climbs.json; path = [east, north, ...] metres from o = [lat, lon] in millionths of a degree
    function initialize(climb) {
        self.climb = climb;
        path = climb["path"];
        pd = climb["pd"];
        total = pd[pd.size() - 1].toFloat();
        lat0 = climb["o"][0].toDouble() / 1000000.0d;
        lon0 = climb["o"][1].toDouble() / 1000000.0d;
        kx = M_PER_DEG * Math.cos(Math.toRadians(lat0));
        var len = Math.sqrt(path[2] * path[2] + path[3] * path[3]);
        ux = path[2] / len;  // direction of the first piece: the start line is square to it
        uy = path[3] / len;
    }

    // [east, north] metres of a position given in degrees
    function xy(ll) {
        return [((ll[1] - lon0) * kx).toFloat(), ((ll[0] - lat0) * M_PER_DEG).toFloat()];
    }

    // fraction of the last second at which the rider crossed the start line, or null if they didn't
    function crossedStart(p) {
        var along = p[0] * ux + p[1] * uy, across = (p[0] * uy - p[1] * ux).abs();
        var prev = prevAlong;
        prevAlong = along;
        if (prev != null && prev < 0 && along >= 0 && across < START_M) {
            return -prev / (along - prev);
        }
        return null;
    }

    function start() {
        seg = 0;
        done = 0.0;
        off = 0;
    }

    // metres done after a fix at p, secs after the last one; -1 once the rider has left the climb
    function update(p, secs) {
        var n = pd.size() - 1, best = -1, bestD = 0.0, bestAlong = 0.0;
        for (var i = seg > 0 ? seg - 1 : 0; i < n && i <= seg + 2 + secs; i++) {  // pieces are 25 m: one a second, plus slack
            var ax = path[2 * i], ay = path[2 * i + 1];
            var bx = path[2 * i + 2] - ax, by = path[2 * i + 3] - ay;
            var len2 = bx * bx + by * by;
            var t = len2 > 0 ? ((p[0] - ax) * bx + (p[1] - ay) * by) / len2 : 0.0;
            t = t < 0 ? 0.0 : (t > 1 && i < n - 1 ? 1.0 : t);  // past the last point still counts, to see the finish
            var dx = p[0] - ax - t * bx, dy = p[1] - ay - t * by;
            var d = dx * dx + dy * dy;
            if (best < 0 || d < bestD) {
                best = i;
                bestD = d;
                bestAlong = pd[i] + t * (pd[i + 1] - pd[i]);
            }
        }
        if (best < 0 || bestD > OFF_M * OFF_M) {
            off += secs;
            return off >= OFF_S ? -1 : done;
        }
        off = 0;
        seg = best;
        if (bestAlong > done) {
            done = bestAlong;
        }
        return done;
    }

    // straight-line metres from the rider to the start point, for the "climb ahead" hint
    function toStart(p) {
        return Math.sqrt(p[0] * p[0] + p[1] * p[1]);
    }
}
