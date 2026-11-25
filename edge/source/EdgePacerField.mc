import Toybox.Activity;
import Toybox.Attention;
import Toybox.FitContributor;
import Toybox.Graphics;
import Toybox.Lang;
import Toybox.Math;
import Toybox.Position;
import Toybox.System;
import Toybox.Time;
import Toybox.UserProfile;
import Toybox.WatchUi;

// The whole app, in the rider's own data field slot: idle between climbs, pacing from each start line in
// climbs.json, the result for 5 s after the finish. Layout: docs/layout/on-your-page.png.
class EdgePacerField extends WatchUi.DataField {
    const RESULT_MS = 5000;    // the result stays this long, then back to idle
    const FLASH_MS = 2000;     // colours swap this long when the target changes
    const NEAR_M = 500.0;      // this close to a climb start the approach screen replaces the 3 s power
    const OLD_S = 1209600;     // climbs synced more than 14 days ago ask for a sync
    const NAMES = {"K" => "KOM", "Q" => "QOM", "P" => "PR", "F" => "best"};
    const CODES = {"K" => 1, "Q" => 2, "P" => 3, "F" => 4};  // the ride file's target field; 0 between climbs
    const NUM = [Graphics.FONT_NUMBER_THAI_HOT, Graphics.FONT_NUMBER_HOT, Graphics.FONT_NUMBER_MEDIUM, Graphics.FONT_NUMBER_MILD];
    const TXT = [Graphics.FONT_LARGE, Graphics.FONT_MEDIUM, Graphics.FONT_SMALL, Graphics.FONT_TINY, Graphics.FONT_XTINY];

    var rider = null, status = null, recKey = "kom", recLetter = "K";
    var trackers = [], synced = 0;
    var lastFixMs = null, noFix = false, gpsNote = "";
    var wBal = 0.0, p3 = null, p10 = null, v10 = null;  // reserve, ~3 s and ~10 s power, ~10 s speed
    var tr = null, target = null, startMs = 0, steady = 0, sinceStop = null, lastDone = 0.0, lastElapsed = 0.0;
    var out = null, letter = null, flashUntil = 0, result = null, resultUntil = 0, hint = null, hintM = 0.0;
    var ahead = null, aheadFor = null;  // the target planned on the approach to `aheadFor`
    var fGap, fWatts, fTarget, fSpeed;

    function initialize() {
        DataField.initialize();
        fGap = createField("gap", 0, FitContributor.DATA_TYPE_SINT16, {:mesgType => FitContributor.MESG_TYPE_RECORD, :units => "s"});
        fWatts = createField("target_power", 1, FitContributor.DATA_TYPE_UINT16, {:mesgType => FitContributor.MESG_TYPE_RECORD, :units => "W"});
        fTarget = createField("target", 2, FitContributor.DATA_TYPE_UINT8, {:mesgType => FitContributor.MESG_TYPE_RECORD});
        fSpeed = createField("model_speed", 3, FitContributor.DATA_TYPE_UINT16, {:mesgType => FitContributor.MESG_TYPE_RECORD, :units => "cm/s"});

        // zero input: everything about the rider comes from the Garmin profile; same limits as Rider in model/pacer.py
        var prof = UserProfile.getProfile();
        var ftp = UserProfile.getFunctionalThresholdPower(Activity.SPORT_CYCLING);
        if (ftp == null || ftp < 50 || ftp > 700) {
            status = "SET FTP";
        } else if (prof.weight == null || prof.weight < 30000 || prof.weight > 200000) {
            status = "SET WEIGHT";
        } else {
            rider = new Pacer.Rider(prof.weight / 1000.0, ftp);
            wBal = rider.wPrime();
        }
        if (prof.gender == UserProfile.GENDER_FEMALE) {
            recKey = "qom";
            recLetter = "Q";
        }
        // shortcut: every climb loads into memory at once, fine for the ~15 climbs of a few saved routes; split into one resource per climb if the simulator's memory view runs high
        var data = WatchUi.loadResource(Rez.JsonData.climbs);
        synced = data["synced"];
        var cl = data["climbs"];
        for (var i = 0; i < cl.size(); i++) {
            trackers.add(new Tracker(cl[i]));
        }
        if (status == null && trackers.size() == 0) {
            status = "NO CLIMBS";
        }
    }

    function compute(info as Activity.Info) as Void {
        var ms = System.getTimer(), pw = info.currentPower, v = info.currentSpeed;
        if (pw != null) {
            p3 = p3 == null ? pw : p3 + (pw - p3) / 3.0;
            p10 = p10 == null ? pw : p10 + (pw - p10) * 0.1;
            if (rider != null) {
                wBal = rider.wAfter(wBal, pw, 1.0);  // compute runs once a second
            }
        }
        if (v != null) {
            v10 = v10 == null ? v : v10 + (v - v10) * 0.1;
        }
        if (result != null && ms > resultUntil) {
            result = null;
        }
        var loc = info.currentLocation, acc = info.currentLocationAccuracy;
        noFix = loc == null || (acc != null && acc < Position.QUALITY_USABLE);  // ride-file playback gives no accuracy
        gpsNote = loc == null ? "no GPS" : "weak GPS " + acc;  // Position.Quality: 1 last known, 2 poor
        if (rider != null && !noFix) {
            var secs = lastFixMs == null ? 1 : (ms - lastFixMs + 500) / 1000;
            lastFixMs = ms;
            var ll = loc.toDegrees();
            if (tr == null) {
                lookForStart(ll, ms, secs);
            } else {
                pace(tr.update(tr.xy(ll), secs), ms, v);
            }
        }
        record();
    }

    // name the nearest climb start, and begin pacing when the rider crosses one
    function lookForStart(ll, ms, secs) {
        var near = NEAR_M;
        hint = null;
        for (var i = 0; i < trackers.size(); i++) {
            var t = trackers[i], p = t.xy(ll), f = t.crossedStart(p);
            if (f != null) {
                tr = t;
                tr.start();
                startMs = ms - ((1.0 - f) * secs * 1000).toNumber();  // when the line was crossed, between fixes
                steady = 0;
                sinceStop = null;
                lastDone = 0.0;
                lastElapsed = (ms - startMs) / 1000.0;
                target = aheadFor == t ? ahead : plan(t);  // planned on the approach: no search at the start line
                letter = target.letter;
                hint = null;
                return;
            }
            var d = t.toStart(p);
            if (d < near && t.prevAlong < 0) {  // only starts still ahead, not one just ridden
                near = d;
                hint = t;
                hintM = d;
            }
        }
        if (hint != aheadFor) {
            aheadFor = hint;
            ahead = hint == null ? null : plan(hint);
        }
    }

    function plan(t) {
        var c = t.climb;
        return Pacer.choose(c["sec"], rider, c[recKey], recLetter, c["pr"], c["ghost"], wBal);
    }

    function pace(done, ms, v) {
        var elapsed = (ms - startMs) / 1000.0;
        if (done < 0) {
            stop();  // left the climb: no result
            return;
        }
        if (done >= tr.total) {
            // the finish was crossed between the last fix and this one
            finish(lastElapsed + (tr.total - lastDone) / (done - lastDone) * (elapsed - lastElapsed), ms);
            return;
        }
        if (v == null || v > 1.0) {  // no speed reading is not a stop
            steady++;
            if (sinceStop != null) {
                sinceStop++;
            }
        } else {
            steady = 0;
            sinceStop = 0;  // stopped: no watts until RESTART_S after getting going again
        }
        out = target.now(done, elapsed, wBal, p10, v10, steady, sinceStop);
        if (!out[0].equals(letter)) {
            letter = out[0];
            flashUntil = ms + FLASH_MS;
            if (Attention has :playTone) {
                Attention.playTone(Attention.TONE_ALERT_HI);
            }
        }
        lastDone = done;
        lastElapsed = elapsed;
    }

    // Edge timing: Strava's own may differ by 1-3 s
    function finish(secs, ms) {
        var c = tr.climb, rec = c[recKey], pr = c["pr"];
        var line = "first time";
        if (rec != null && secs < rec) {
            line = "under the " + NAMES[recLetter];
        } else if (pr != null && secs < pr) {
            line = "NEW PR by " + (pr - secs).format("%.1f") + " s";
        } else if (pr != null) {
            line = "PR +" + (secs - pr).format("%.1f") + " s";
        }
        result = [(secs / 60).toNumber() + ":" + (secs.toNumber() % 60).format("%02d"), line, c["name"]];
        resultUntil = ms + RESULT_MS;
        stop();
    }

    function stop() {
        tr = null;
        target = null;
        ahead = null;  // a target, once ridden, may have dropped: plan afresh next time
        aheadFor = null;
        hint = null;
        out = null;
        letter = null;
    }

    // the ride file's fields, every second, for checking the model after the ride
    function record() {
        var o = out;
        fTarget.setData(o == null ? 0 : CODES[o[0]]);
        fGap.setData(o == null ? 0 : Math.round(o[2]).toNumber());
        fWatts.setData(o == null || o[1] == null ? 0 : Math.round(o[1]).toNumber());
        fSpeed.setData(o == null || o[1] == null ? 0 : Math.round(target.speedAt(lastDone, o[1]) * 100).toNumber());
    }

    function onUpdate(dc as Graphics.Dc) as Void {
        var w = dc.getWidth(), h = dc.getHeight();
        var bg = getBackgroundColor(), fg = bg == Graphics.COLOR_BLACK ? Graphics.COLOR_WHITE : Graphics.COLOR_BLACK;
        if (System.getTimer() < flashUntil) {
            var x = bg;
            bg = fg;
            fg = x;
        }
        dc.setColor(fg, bg);
        dc.clear();
        dc.setColor(fg, Graphics.COLOR_TRANSPARENT);
        var top = (h * 0.6).toNumber(), line = (h - top) / 2;

        if (status != null) {
            fit(dc, status, TXT, w / 2, 0, w, top);
            fit(dc, status.equals("NO CLIMBS") ? "sync, then rebuild" : "in your Garmin profile", TXT, w / 2, top, w, line);
        } else if (result != null) {
            fit(dc, result[0], NUM, w / 2, 0, w, top);
            fit(dc, result[1], TXT, w / 2, top, w, line);
            fit(dc, result[2], TXT, w / 2, top + line, w, line);
        } else if (out != null) {
            var g = Math.round(out[2]).toNumber(), name = NAMES[out[0]];
            number(dc, g.toString(), "s", w, top, badge(dc, out[0], w, h, fg, bg));  // no "+": number fonts may lack it; colour and words say ahead
            if (g != 0) {
                var good = g > 0;
                dc.setColor(good ? (bg == Graphics.COLOR_BLACK ? Graphics.COLOR_GREEN : Graphics.COLOR_DK_GREEN) : Graphics.COLOR_RED, Graphics.COLOR_TRANSPARENT);
                fit(dc, good ? "ahead of " + name + " pace" : "behind " + name + " pace", TXT, w / 2, top, w, line);
                dc.setColor(fg, Graphics.COLOR_TRANSPARENT);
            } else {
                fit(dc, "on " + name + " pace", TXT, w / 2, top, w, line);
            }
            var left = ((tr.total - lastDone) / 1000.0).format("%.1f") + " km " + (target.gradeAt(lastDone) * 100).format("%.0f") + "%";
            fit(dc, noFix ? "no GPS fix" : (out[1] == null ? left : "hold " + Math.round(out[1]).toNumber() + " W  " + left), TXT, w / 2, top + line, w, line);
        } else if (ahead != null) {
            // approaching a climb: the countdown, the target, and the watts and speed to arrive at
            number(dc, ((hintM / 10).toNumber() * 10).toString(), "m", w, top, badge(dc, ahead.letter, w, h, fg, bg));
            var c = aheadFor.climb;
            fit(dc, Pacer.ftpOutOfDate(rider, c["pr"], c["prw"]) ? "FTP looks out of date" : c["name"], TXT, w / 2, top, w, line);
            var start = "start " + Math.round(ahead.power).toNumber() + " W  " + (ahead.speedAt(0.0, ahead.power) * 3.6).format("%.0f") + " km/h";
            fit(dc, noFix ? gpsNote : start, TXT, w / 2, top + line, w, line);
        } else {
            number(dc, p3 == null ? "--" : Math.round(p3).toNumber().toString(), "W", w, top, 0);
            fit(dc, "EdgePacer", TXT, w / 2, top, w, line);
            var note = trackers.size() + (trackers.size() == 1 ? " climb" : " climbs");
            var age = Time.now().value() - synced;
            if (noFix) {
                note = gpsNote;
            } else if (age > OLD_S) {
                note = "climbs " + age / 86400 + " days old";
            }
            fit(dc, note, TXT, w / 2, top + line, w, line);
        }
    }

    // the target letter in a disc, top right; returns the margin the big number must keep clear of it
    function badge(dc, text, w, h, fg, bg) {
        var r = (h < w ? h : w) / 9;
        dc.fillCircle(w - r - 4, r + 4, r);
        dc.setColor(bg, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w - r - 4, r + 4, Graphics.FONT_SMALL, text, Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
        dc.setColor(fg, Graphics.COLOR_TRANSPARENT);
        return 2 * r + 8;
    }

    // the big number with its unit after it, centred in the top part, clear of `margin` px each side
    function number(dc, text, unit, w, top, margin) {
        var uw = dc.getTextWidthInPixels(unit, Graphics.FONT_SMALL) + 4;
        var f = fit(dc, text, NUM, w / 2 - uw / 2, 0, w - 2 * margin - uw, top);
        var x = w / 2 - uw / 2 + dc.getTextWidthInPixels(text, f) / 2 + 4;
        dc.drawText(x, top / 2 + dc.getFontHeight(f) / 4, Graphics.FONT_SMALL, unit, Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
    }

    // draws text in the biggest of fonts (largest first) that fits a bw x bh box centred on cx; returns the font
    function fit(dc, text, fonts, cx, y, bw, bh) {
        var f = fonts[fonts.size() - 1];
        for (var i = 0; i < fonts.size(); i++) {
            if (dc.getTextWidthInPixels(text, fonts[i]) <= bw && dc.getFontHeight(fonts[i]) <= bh) {
                f = fonts[i];
                break;
            }
        }
        dc.drawText(cx, y + bh / 2, f, text, Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
        return f;
    }
}
