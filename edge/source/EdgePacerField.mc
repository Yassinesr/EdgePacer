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
    const TINT = {"K" => Graphics.COLOR_ORANGE, "Q" => Graphics.COLOR_ORANGE, "P" => Graphics.COLOR_BLUE, "F" => Graphics.COLOR_DK_GRAY};
    const ZONES = [0.55, 0.75, 0.90, 1.05, 1.20, 1.50];  // Coggan power zones, fractions of FTP
    const ZONE_TINT = [Graphics.COLOR_DK_GRAY, Graphics.COLOR_BLUE, Graphics.COLOR_DK_GREEN, Graphics.COLOR_YELLOW, Graphics.COLOR_ORANGE, Graphics.COLOR_RED, Graphics.COLOR_PURPLE];
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
        var line = "first time", tint = Graphics.COLOR_DK_GRAY;
        if (rec != null && secs < rec) {
            line = "under the " + NAMES[recLetter];
            tint = Graphics.COLOR_ORANGE;
        } else if (pr != null && secs < pr) {
            line = "NEW PR by " + (pr - secs).format("%.1f") + " s";
            tint = Graphics.COLOR_DK_GREEN;
        } else if (pr != null) {
            line = "PR +" + (secs - pr).format("%.1f") + " s";
        }
        result = [(secs / 60).toNumber() + ":" + (secs.toNumber() % 60).format("%02d"), line, c["name"], tint];
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

    // Four bands, like a Garmin field: label, big number, one line, a bar. Always a number worth glancing at:
    // 3 s power between climbs and on the approach, the gap on the climb, the time at the finish.
    function onUpdate(dc as Graphics.Dc) as Void {
        var w = dc.getWidth(), h = dc.getHeight();
        var bg = getBackgroundColor(), dark = bg == Graphics.COLOR_BLACK;
        var fg = dark ? Graphics.COLOR_WHITE : Graphics.COLOR_BLACK;
        if (letter != null && System.getTimer() < flashUntil) {
            bg = TINT[letter];  // the new target's colour fills the field for a moment
            fg = Graphics.COLOR_WHITE;
        }
        dc.setColor(fg, bg);
        dc.clear();
        var good = dark ? Graphics.COLOR_GREEN : Graphics.COLOR_DK_GREEN, soft = dark ? Graphics.COLOR_LT_GRAY : Graphics.COLOR_DK_GRAY;
        var lab = h * 16 / 100, big = h * 46 / 100, row = h * 22 / 100, barH = h * 6 / 100 > 5 ? h * 6 / 100 : 5;
        var barY = h - barH - h * 3 / 100, power = p3 == null ? "--" : Math.round(p3).toNumber().toString();

        if (result != null) {
            label(dc, result[2], fg, w, lab);
            number(dc, result[0], "", fg, w, lab, big, 0);
            pill(dc, result[1], result[3], w / 2, lab + big, row, w - 16);
            bar(dc, 1.0, result[3], null, w, barY, barH, dark);
        } else if (out != null) {
            var g = Math.round(out[2]).toNumber(), tint = g > 0 ? good : (g < 0 ? Graphics.COLOR_RED : fg);
            var left = ((tr.total - lastDone) / 1000.0).format("%.1f") + " km  " + (target.gradeAt(lastDone) * 100).format("%.0f") + "%";
            label(dc, noFix ? "NO GPS FIX" : (NAMES[out[0]] + " pace  ").toUpper() + left, noFix ? Graphics.COLOR_RED : soft, w, lab);
            number(dc, g.toString(), g > 0 ? "s ahead" : (g < 0 ? "s behind" : "s on pace"), tint, w, lab, big, badge(dc, out[0], w, h, lab));
            pill(dc, out[1] == null ? "get going" : "hold " + Math.round(out[1]).toNumber() + " W", out[1] == null ? Graphics.COLOR_DK_GRAY : TINT[out[0]], w / 2, lab + big, row, w - 16);
            bar(dc, lastDone / tr.total, tint, null, w, barY, barH, dark);
        } else if (ahead != null) {
            // the approach: your power now against the watts to ride in at, and the metres left
            var c = aheadFor.climb, entry = rider.ftp;
            label(dc, Pacer.ftpOutOfDate(rider, c["pr"], c["prw"]) ? "FTP LOOKS OUT OF DATE" : c["name"], Graphics.COLOR_ORANGE, w, lab);
            number(dc, power, "W", fg, w, lab, big, badge(dc, ahead.letter, w, h, lab));
            dc.setColor(fg, Graphics.COLOR_TRANSPARENT);
            fit(dc, noFix ? gpsNote : ((hintM / 10).toNumber() * 10) + " m", TXT, w / 4, lab + big, w / 2, row);
            pill(dc, "ride in " + entry + " W", TINT[ahead.letter], w * 3 / 4, lab + big, row, w / 2 - 8);
            var r = p3 == null ? 0.0 : p3 / entry;
            bar(dc, r / 1.5, r < 0.95 ? Graphics.COLOR_BLUE : (r > 1.05 ? Graphics.COLOR_RED : good), 1 / 1.5, w, barY, barH, dark);
        } else {
            // a power field: 3 s power with its zone; a status word when EdgePacer can't pace
            var z = rider == null || p3 == null ? null : zone(p3);
            label(dc, status != null ? status : "3S POWER", status != null ? Graphics.COLOR_RED : soft, w, lab);
            if (z != null) {
                pill(dc, "Z" + (z + 1), zoneTint(z, dark), w - lab * 2, 0, lab, lab * 3);
            }
            number(dc, power, "W", fg, w, lab, big, 0);
            var note = trackers.size() + (trackers.size() == 1 ? " climb" : " climbs");
            var age = Time.now().value() - synced;
            if (status != null) {
                note = status.equals("NO CLIMBS") ? "sync, then rebuild" : "in your Garmin profile";
            } else if (noFix) {
                note = gpsNote;
            } else if (age > OLD_S) {
                note = "climbs " + age / 86400 + " days old";
            }
            dc.setColor(soft, Graphics.COLOR_TRANSPARENT);
            fit(dc, note, TXT, w / 2, lab + big, w, row);
            if (z != null) {
                bar(dc, p3 / (1.5 * rider.ftp), zoneTint(z, dark), 1 / 1.5, w, barY, barH, dark);
            }
        }
    }

    function zone(p) {
        var i = 0;
        while (i < ZONES.size() && p >= ZONES[i] * rider.ftp) {
            i++;
        }
        return i;
    }

    function zoneTint(z, dark) {
        return z == 0 && dark ? Graphics.COLOR_LT_GRAY : (z == 2 && dark ? Graphics.COLOR_GREEN : ZONE_TINT[z]);
    }

    // small text at the top left, in the band 0..lab
    function label(dc, text, tint, w, lab) {
        dc.setColor(tint, Graphics.COLOR_TRANSPARENT);
        var f = font(dc, text, [Graphics.FONT_SMALL, Graphics.FONT_TINY, Graphics.FONT_XTINY], w * 3 / 4, lab);
        dc.drawText(8, lab / 2, f, text, Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
    }

    // the target letter in a disc, top right under the label; returns the margin the big number keeps clear of it
    function badge(dc, text, w, h, lab) {
        var r = (h < w ? h : w) / 10;
        dc.setColor(TINT[text], Graphics.COLOR_TRANSPARENT);
        dc.fillCircle(w - r - 8, lab + r + 2, r);
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w - r - 8, lab + r + 2, font(dc, text, TXT, r * 2, r * 2), text, Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
        return 2 * r + 16;
    }

    // text on a rounded tag in its colour, centred on cx in the band y..y+bh, at most mw wide
    function pill(dc, text, tint, cx, y, bh, mw) {
        var ph = bh * 8 / 10, f = font(dc, text, TXT, mw - ph, ph * 9 / 10);
        var pw = dc.getTextWidthInPixels(text, f) + ph;
        dc.setColor(tint, Graphics.COLOR_TRANSPARENT);
        dc.fillRoundedRectangle(cx - pw / 2, y + (bh - ph) / 2, pw, ph, ph / 2);
        dc.setColor(tint == Graphics.COLOR_YELLOW ? Graphics.COLOR_BLACK : Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(cx, y + bh / 2, f, text, Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
    }

    // a rounded gauge across the field, filled to frac (0..1) in its colour, with a mark at tick if given
    function bar(dc, frac, tint, tick, w, y, bh, dark) {
        var x = 8, bw = w - 16;
        frac = frac < 0 ? 0.0 : (frac > 1 ? 1.0 : frac);
        dc.setColor(dark ? Graphics.COLOR_DK_GRAY : Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.fillRoundedRectangle(x, y, bw, bh, bh / 2);
        if (frac > 0) {
            dc.setColor(tint, Graphics.COLOR_TRANSPARENT);
            var fw = (bw * frac).toNumber();
            dc.fillRoundedRectangle(x, y, fw > bh ? fw : bh, bh, bh / 2);
        }
        if (tick != null) {
            dc.setColor(dark ? Graphics.COLOR_WHITE : Graphics.COLOR_BLACK, Graphics.COLOR_TRANSPARENT);
            dc.fillRectangle(x + (bw * tick).toNumber() - 1, y - 3, 3, bh + 6);
        }
    }

    // the big number centred in the band y..y+bh, its unit after it in a small font, clear of `margin` px each side
    function number(dc, text, unit, tint, w, y, bh, margin) {
        var uw = unit.length() > 0 ? dc.getTextWidthInPixels(unit, Graphics.FONT_SMALL) + 6 : 0;
        dc.setColor(tint, Graphics.COLOR_TRANSPARENT);
        var f = fit(dc, text, NUM, w / 2 - uw / 2, y, w - 2 * margin - uw, bh);
        if (uw > 0) {
            var x = w / 2 - uw / 2 + dc.getTextWidthInPixels(text, f) / 2 + 6;
            dc.drawText(x, y + bh / 2 + dc.getFontHeight(f) / 4, Graphics.FONT_SMALL, unit, Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
        }
    }

    // the biggest of fonts (largest first) that fits text in bw x bh
    function font(dc, text, fonts, bw, bh) {
        for (var i = 0; i < fonts.size(); i++) {
            if (dc.getTextWidthInPixels(text, fonts[i]) <= bw && dc.getFontHeight(fonts[i]) <= bh) {
                return fonts[i];
            }
        }
        return fonts[fonts.size() - 1];
    }

    // draws text in the biggest of fonts (largest first) that fits a bw x bh box centred on cx; returns the font
    function fit(dc, text, fonts, cx, y, bw, bh) {
        var f = font(dc, text, fonts, bw, bh);
        dc.drawText(cx, y + bh / 2, f, text, Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
        return f;
    }
}
