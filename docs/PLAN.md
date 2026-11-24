# EdgePacer: pacing logic plan

Status: **draft for review**. Nothing here is built yet, except the first prototype in `model/pacer.py`.

EdgePacer is a Connect IQ data field for Garmin Edge 540 and newer. Riders have a power meter.
On a Strava segment it shows **how many watts to hold on each part of the climb** to beat your PR or the KOM.
It updates every second from where you are and how much time you have left.

The test climb used for the numbers below is made up: 3 km, rider 72 kg, bike 8 kg, target 9:30.

---

## 1. What matters most

| Change on the 9:30 test climb | Effect on finish time |
|---|---|
| Leave out air drag | **+50 s** (targets ~30 W too low) |
| Rider weight off by 2 kg | +11 s |
| Power meter reads 2 % high | +10 s |
| Tyres: Crr 0.004 → 0.005 | +6 s |
| Position: CdA 0.32 → 0.36 | +5 s |
| 30 °C instead of 15 °C | −2 s |
| Best plan shape vs even power, same effort | **−0.7 s** |

So the order of work is:
1. **Complete physics:** gravity, rolling, air drag in still air. No wind forecast.
2. **Calibration from your own past efforts** (section 8). This removes most of the 5–11 s errors.
3. **Plan shape** (section 4). It's cheap and correct, but small.

---

## 2. Inputs

| Input | Source | Default |
|---|---|---|
| Segment path, elevation profile | Strava (prepared before the ride) | n/a |
| Your PR time, KOM / QOM time | Strava | n/a |
| Rider weight | Garmin user profile on the Edge | n/a |
| Bike + kit weight | Setting | 9 kg |
| Position (CdA) | Setting: hoods 0.32, drops 0.28, tops 0.36 m² | hoods |
| Road (Crr) | Setting: smooth 0.004, average 0.005, rough 0.006 | smooth |
| Air density | Edge barometer + temperature; else standard air for the altitude | n/a |
| FTP or CP, and W′ | Settings (for "can you do it?") | W′ 20 kJ |
| Margin | Setting: beat the target by 1 %, at least 2 s | 1 % |
| Calibration factor | Fitted from your past efforts (section 8) | 1.0 |

Live from the Edge: position, speed, wheel distance, power (3 s), barometric pressure, clock.

---

## 3. Physics

For each section, at steady speed `v`:

```
P_meter × η × k = v × [ m·g·sin(a) + m·g·Crr·cos(a) + ½·ρ·CdA·v² ]
```

- `m` = rider + bike + kit, `a` = atan(grade), `g` = 9.81, `η` = drivetrain efficiency 0.975.
- `k` = your calibration factor. It absorbs power-meter offset and small errors in weight, Crr and CdA.
- `ρ` = pressure / (287.05 × (temp °C + 273.15)). Use the Edge barometer when it's available. Otherwise use standard pressure for the altitude.
- **In:** gravity, rolling, still-air drag.
- **Out:**
  - Wind, by your choice. The live replan corrects for it anyway (section 6).
  - Wheel inertia.
  - Speed changes inside a section. The speed you gain is mostly given back when you slow down again.
- **Flying start:** you cross the start line already moving. That speed is extra energy (½·m·v²). The plan ignores it, but the live replan sees it as "ahead". On segments under 1 min it matters, so there the plan uses your real speed at the start line.

---

## 4. The plan

### 4.1 Sections

Build them from the Strava stream (distance, altitude, lat/lon every few metres):
1. Lightly smooth the altitude (30 m moving average) to remove noise.
2. Work out the grade in 25 m chunks.
3. Merge neighbouring chunks while their grades differ by less than 1 point. Keep each section between 50 m and 200 m (25 m minimum on segments under 1 km).
4. Clamp grades to ±25 %.

Typical result: 10–40 sections, each with a start distance, length, grade and altitude. Fewer, longer sections mean the target changes less often on screen.

### 4.2 Watts per section: the "lowest effort" rule

Of all the ways to hit the target time, choose the one with the **lowest normalized power (NP)**. NP is the standard measure of how hard a variable effort feels. The maths gives one simple rule. Every section satisfies:

```
P³ × (3·P + 8·P_air) = the same constant everywhere
```

`P_air` is the part of P that goes into air drag in that section. What this means:
- With no air drag, it gives **even power**.
- Where you're fast and air drag is a big share (shallow parts), it gives **a bit less** power.
- Where you're slow (steep parts), it gives **a bit more**.
- On the test climb: 346–349 W on the 8–9 % parts, 330–337 W on 5–6 %, 310 W on 3 %. This replaces the `bias` guess in the prototype, and there's no magic number to tune.

Limits:
- **Descents inside a segment:** if the rule asks for less than 0 W, coast (0 W). Speed is capped at a descent limit (setting, default 50 km/h), because corners and braking can't be modelled.
- **Short hard sections:** a section's power can't go above what you can hold for that long, `CP + W′left / section time`. If it hits that cap, the rest of the climb is planned again around it.
- **Option:** "even power" pacing, for riders who prefer one number.

### 4.3 Solving it fast enough for the Edge

There are two nested 1-D searches:
- Outer: find the constant that makes the total time equal the target.
- Inner: for each section, find the speed that satisfies the rule.

Use the secant method outside and Newton's method inside, starting from last second's answer. That's about 1–2 thousand cheap evaluations per update, which is small enough for Connect IQ's limit on how long one update may take. The first plan is computed before the start line.

---

## 5. Targets

- **PR:** your best time on the segment.
- **KOM / QOM:** a setting. The default comes from the Garmin profile.
- **Custom time:** later, set from the phone or computer.
- **Best possible:** no time target. The plan uses your W′ exactly by the finish (section 7). It also gives "at your current form, you'd do 9:41".
- **Margin:** the target is the record minus 1 % (at least 2 s). Strava's timing from GPS can differ from the Edge's by 1–3 s.
- **Target ladder:** if the chosen target is out of reach, before or during the climb, drop to the next one: **KOM → PR → Best possible**. The screen always shows which target is active.
- **Never ridden:** there's no PR, so use the KOM or Best possible.

---

## 6. On the bike

### 6.1 States

| State | When | Screen |
|---|---|---|
| Idle | No segment within 1 km ahead | Normal power field |
| Approaching | < 1 km from a start, heading towards it | Preview: name, length, average grade, target time, average W and W/kg needed, verdict (section 7), "start at 330 W" |
| Armed | < 50 m from the start, heading within 45° of the segment's direction, speed > 5 km/h | "Go in 40 m" |
| Active | Crossed the start line | Pacing screen (6.4) |
| Finished | Crossed the finish line | Result for 60 s |
| Aborted | > 50 m off the segment for > 15 s, riding backwards, or stopped > 5 min | "Abandoned" for 10 s |

### 6.2 Start, progress, clock

- **Start and finish lines** are perpendicular to the segment at its start and end points. The crossing time is interpolated between two GPS fixes, which gives sub-second accuracy.
- **Distance along the segment:** project the GPS position onto the segment path, searching forward from the last match. Between fixes, add wheel distance. Each second, blend: `d = d_wheel + 0.2 × (d_gps − d_wheel)`. It never goes backwards, which gives a smooth distance without jumps.
- **Clock = real time since the start line,** not the activity timer. Strava counts elapsed time, so stops at traffic lights count, and auto-pause doesn't stop the clock.

### 6.3 Every second

1. `time_left = target − elapsed`, then plan the rest of the climb from `d` (section 4).
2. **Ahead of plan:** keep showing the original plan's watts to bank the time (default). The setting "ease off" shows the lower replanned number instead.
3. **Behind plan:** the replan raises the watts. If that's no longer doable (section 7), drop down the target ladder.
4. **Gap** = planned time at `d` − elapsed. Projected finish = target − gap.
5. **Wind is handled here:** a headwind makes you fall behind, so the target rises, and a tailwind does the opposite. The pacing adapts without a weather model.

### 6.4 Pacing screen (full-screen layout)

- **Target watts now**, large, rounded to 5 W. It only changes when the target moves ≥ 5 W or at a section change, so it doesn't flicker.
- **Your 3 s power,** coloured: green within ±3 % of target, orange above, red below.
- **Gap:** "+4 s" ahead or "−3 s" behind, plus the projected finish.
- **Next change:** "in 150 m: 350 W", shown when it differs by ≥ 10 W, so you can prepare.
- **Distance left, grade now and next,** and a small profile with your position.
- **W′ left** as a bar, if CP is set.
- **Early warning:** in the first 60 s, if 3 s power is > 10 % above target, show "EASY". Starting too hard is the most common mistake.
- **Smaller layouts:** target watts and gap only.
- **Power dropout:** shows "--". The replan keeps going from distance and time.

### 6.5 Finish

- Show the result for 60 s: your time vs target, average power vs planned, and where you lost or gained the most.
- Write to the ride file with FitContributor: target watts and gap every second, plus segment and result per lap. You can then compare plan and actual afterwards in Garmin Connect.

---

## 7. Can you do it? (W′ balance)

- **CP** (critical power) is close to FTP. **W′** is the reserve of work above CP, typically 15–25 kJ.
- Use Skiba's model:
  - Above CP, W′ drains by `(P − CP)` per second.
  - Below CP, it refills: the gap to full shrinks by a factor `exp(−(CP − P)·t / W′)`.
- **Before the start:** simulate the plan and look at the lowest W′ point.
  - **Comfortable:** ≥ 50 % left.
  - **Hard:** 0–50 % left.
  - **Out of reach:** below 0, which also says where you'd run out.
- With CP 320 W and W′ 20 kJ on the test climb:
  - 10:00 leaves 17.4 kJ (comfortable).
  - 9:30 leaves 7.9 kJ (hard).
  - 9:00 is −3.2 kJ (out of reach).
- **Live:** W′ is tracked from the start of the ride, so an earlier attack counts. Out of reach mid-climb drops down the target ladder.
- **Best possible:** the fastest target whose plan never drops below 0 W′.

---

## 8. Calibration from your past efforts (biggest accuracy win)

- **Data:** your past efforts on the segment, with power, from Strava streams or Garmin FIT files.
- **Method:**
  1. Split each effort into the sections.
  2. Feed in the average power you actually rode on each section, and let the model predict the time.
  3. Fit `k` so that predicted times match actual times. Use the median across efforts, to ignore outliers.
- **Separating air drag:** if the efforts cover both slow and fast sections, also fit CdA. Climb data alone can't tell CdA from Crr.
- **Per segment, not only per rider:** a segment factor absorbs errors in Strava's elevation data and the road surface. Your PR effort is the best calibration for your PR target.
- **No past efforts with power:** use the defaults and say "uncalibrated" in the preview.
- **Drafting** in group rides makes the model too optimistic. Prefer solo efforts, and give a lower weight to efforts that are unusually fast for their power.

---

## 9. Edge cases

| Case | Handling |
|---|---|
| Flat or shallow segment (avg < 2 %) | Wind dominates, so mark it "low accuracy". The live replan still corrects. |
| Very short segment (< 1 min) | 25 m sections, real entry speed in the plan, W′ dominates |
| Very long climb (> 30 min) | Plan sits near CP. Fatigue over the ride (durability) comes later. |
| Overlapping or nested segments | One active at a time: the first one started. On the same start point, the longer one. |
| Same road, other direction | Heading check at the start |
| GPS lost | Wheel distance only. > 30 s marks the result "unreliable". |
| Stopped (lights) | Clock runs, like Strava. > 5 min aborts. |
| Out of reach before start | Preview says so and shows the next target on the ladder |
| Way ahead | Hold the plan (default) or ease off (setting) |
| Single-sided power meter | Calibration factor absorbs the bias |
| Riding in a group | Targets too high. We can't detect it; the app assumes you're riding solo. |
| Edge's own Strava Live Segments | Might take over the screen at the start line. Test it; you may need Live Segments off for these segments. |
| Imperial units | Follow the device settings |

---

## 10. Device limits

- **Update time limit:** keep each update small (section 4.3). Replan at most once per second.
- **Memory:** keep only segments within ~10 km in memory, stored compactly. Check the data field memory limit on the 540 and newer.
- **No popups, no buttons:** a data field can't open screens or take input. It must be on a data page, and everything else is a setting.
- **Devices:** Edge 540, 840, 1040, 1050, plus 550 and 850 if the SDK lists them.

---

## 11. Build phases

1. **Model in Python (`model/`).**
   - Sections, the lowest-effort planner, W′ check, target ladder, live replan, calibration.
   - Tests, then validation on your real efforts.
   - This is the reference the Edge code must match.
2. **Edge data field v1.**
   - A script on your computer pulls your starred segments and builds them into the app.
   - State machine, pacing screen, FIT fields.
   - Copy it to the Edge over USB.
3. **Sync and publish.**
   - Starred segments fetched through the phone or a small server.
   - Settings in the Connect IQ app.
   - Strava app review, then a store listing.

---

## 12. Decisions for you (recommended first)

1. **When ahead:** hold the plan to bank time, *or* ease off to the minimum needed.
2. **Margin:** 1 % (at least 2 s), *or* a fixed number of seconds.
3. **Overlapping segments:** first started, *or* longest.
4. **Pacing shape:** lowest effort, *or* even power, as the default.
5. **"Can you do it?":** on, using FTP/CP and W′ settings, *or* off.

## 13. Not verified yet

- **Strava API:**
  - Segment streams (distance, altitude, lat/lon).
  - KOM / QOM / PR times in segment details.
  - Your efforts on a segment. Some endpoints need a Strava subscription.
- **Connect IQ:**
  - Data field memory limit on the 540 and newer.
  - Whether FTP, bike weight, barometric pressure and temperature can be read.
  - How Live Segments interacts with a data field on screen.
