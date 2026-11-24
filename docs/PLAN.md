# EdgePacer: design and logic

EdgePacer is a Connect IQ data field for Garmin Edge 540 and newer, for riders with a power meter.
On every Strava climb on the rider's saved routes it shows **how far ahead of or behind their PR ride they are**,
and the **watts to hold** to beat it. On a climb they have never ridden, it shows the fastest pace their FTP allows.

**Zero input.** The rider types nothing. FTP and weight come from the Garmin profile, the climbs, KOMs, PRs
and PR rides from Strava. Bike weight, tyres and position are fixed defaults, corrected by the rider's own PR ride.

Reference model: `model/pacer.py` (tests: `model/test_pacer.py`). Strava download: `model/strava_sync.py`.

---

## 1. Inputs, all automatic

| Input | Source | Notes |
|---|---|---|
| FTP | Garmin: `UserProfile.getFunctionalThresholdPower(Activity.SPORT_CYCLING)` | API 5.2.2+ |
| Weight | Garmin: `UserProfile.getProfile().weight` | grams |
| Paused (auto-pause, stops) | Garmin: `Activity.Info.timerState` | `TIMER_STATE_PAUSED` |
| Position, speed, distance, power | Garmin: `Activity.Info` | every second |
| Climbs on saved routes: profile, start/end | Strava, via `strava_sync.py` | |
| KOM / QOM time, your PR time | Strava | |
| Your PR ride on the climb (the **ghost**) | Strava: your fastest effort, its time, distance and power | needs a Strava subscription and `activity:read_all` |
| Bike + kit 9 kg, Crr 0.005, drivetrain 97.5 % | fixed | low-trust values, never asked |
| Air drag area | from weight: 0.32 m² × (weight / 72 kg)^0.425 (Heil 2001) | |
| Air density | Edge barometer and temperature, else standard air at the altitude | |
| Calibration factor k | fitted from the PR ride on that climb; for climbs you never rode, the median of your other climbs | absorbs bike, tyres, position, road, power meter |

## 2. What it chases

The rider chooses nothing:
1. **KOM**, if the rider's FTP can beat it (fastest possible time ≤ KOM − margin).
2. Else **PR**, if they have ridden the climb. The gap is measured against their PR ride.
3. Else **Fastest**: the fastest pace their FTP allows, measured against that plan.

The target is the record minus 1 %, at least 2 s, because Strava's GPS timing differs from the Edge's by 1–3 s.
Only climbs averaging **2 % or more** are paced (a setting).

## 3. The model

- **Physics, per section, steady speed, still air:** `P × 0.975 × k = v × (m·g·(sin a + Crr·cos a) + ½·ρ·CdA·v²)`.
  Leaving out air drag would make targets ~30 W too low on fast climbs, so it stays. Wind is not modelled.
- **Sections:** 50–200 m (25 m on climbs under 1 km), each with a steady grade (within 1 point), grade clamped to ±25 %.
  Descents are capped at 50 km/h.
- **Even power.** The optimal power shape only gained 0.7 s on a 9:30 climb, while input errors cost 5–11 s each.
- **What the rider can hold:** `FTP × (1 + 70 s / t)` for an effort of t seconds, with t at least 60 s.
  70 s of FTP is a typical W′ (about 20 kJ at FTP 300).
- **Fastest:** the even power where the climb's time equals the time that power can be held.

## 4. On the bike

| State | When | Screen (top half of the rider's page) |
|---|---|---|
| Idle | not on a climb | a metric the rider picks (3 s power by default); within 1 km of a climb: "next climb in 1.2 km · name · PR 1:56" |
| Active | crossed the start line of a climb ≥ 2 % | gap, large; watts to hold, small; distance to go and grade |
| Paused | Garmin auto-pause during a climb | "paused", nothing to chase |
| Finished | crossed the finish line | result for 5 s ("NEW PR −2.6 s"), then back to idle |

- **Start and finish:** within 50 m, heading within 45° of the segment, crossing a line square to it.
  The crossing time is interpolated between GPS fixes.
- **Distance along the climb:** GPS projected onto the segment, blended with wheel distance, never going backwards.
- **Clock:** time since the start line, like Strava's elapsed time, so a stop still counts.
- **Every second:**
  - **Gap** = time on the ghost (or the plan) at this distance − elapsed. This is the big number, in one colour.
  - **Watts:** ahead → the plan's watts, to bank the time. Behind → the even power that still finishes on time,
    **never more than the rider can hold for the time left**. A slow start, a stop or an out-of-reach target never asks for a spike.
- **Abandon:** more than 50 m off the climb for 15 s, or riding the wrong way.
- **New PR:** the Edge keeps the effort as the new ghost until the next Strava sync.
- **Ride file:** the gap and the watts target go into the FIT file as developer fields, so every ride can be replayed against the plan.

Mockups at Edge 540/840 size: [layout/on-your-page.png](layout/on-your-page.png) (source next to it).

## 5. Strava sync (`model/strava_sync.py`)

- Runs on the rider's computer: one browser sign-in to their own Strava API app (scopes `read,read_all,activity:read_all`).
- Lists saved routes, collects every segment on them with profile, KOM/QOM and PR.
- For climbs ≥ 2 % that the rider has ridden: downloads the fastest effort's time, distance and power (~100 points),
  only again when the PR changes.
- Writes `model/data/segments.json`, kept out of git: KOM times are other riders' data.
- Stops cleanly at the rate limit and keeps what it has; reuses anything fetched in the last 12 hours.

## 6. Device limits

- A data field can't switch pages, take input or pop up screens, so it lives in one spot of the rider's own page.
- The even-power solve is one 1-D search. On the Edge it must use Newton's method for speed per section
  and run only when the rider is behind, at most once a second, to stay inside Connect IQ's time limit.
- Memory: about 15 climbs with ~100-point ghosts and 50–200 m sections. Check the 540's data field limit.

## 7. Next steps

1. **Replay harness (Python):** run the whole on-bike loop over the rider's past rides, second by second.
   Measure the finish error and how much the watts number jumps.
2. **Device stub (one day):** a field that reads FTP, weight and power and draws one number.
   Ride it on a climb with ClimbPro and Strava Live Segments on, to see whether they cover it.
3. **Edge data field v1:** the logic above, with climbs and ghosts built into the app from `segments.json`.
4. **Publishing:** phone or server sync, Strava app review.

## 8. Not verified yet

- Whether ClimbPro or Strava Live Segments covers the field when a climb starts.
- The data field memory limit on the 540 and newer.
- Downloading PR rides from Strava. Requesting your efforts works; reading the ride needs `activity:read_all`,
  which the script now asks for but hasn't been tested with.
- Strava's API terms for publishing an app that shows KOM times.
- Entry speed is not modelled: on climbs under ~1 minute the plan is optimistic. The ghost gap is not affected.
