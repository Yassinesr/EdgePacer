# EdgePacer: design and logic

EdgePacer is a Connect IQ data field for Garmin Edge 540 and newer, for riders with a power meter.
On every Strava climb on the rider's saved routes it shows **how far ahead of or behind the target pace they are**,
and the **watts to hold** to make it. The target is the KOM if their FTP can beat it, else their PR, else the
fastest pace their FTP allows.

**Zero input.** The rider types nothing. FTP and weight come from the Garmin profile, the climbs, KOMs, PRs
and PR rides from Strava. Bike weight, tyres and position are fixed defaults. What else Garmin offers: [GARMIN-DATA.md](GARMIN-DATA.md).

Reference model: `model/pacer.py` (tests: `model/test_pacer.py`). Strava download: `model/strava_sync.py`.

---

## 1. Inputs, all automatic

| Input | Source | Notes |
|---|---|---|
| FTP | Garmin: `UserProfile.getFunctionalThresholdPower(Activity.SPORT_CYCLING)` | API 5.2.2+, Edge 540/550/840/850/1040/1050 |
| Weight | Garmin: `UserProfile.getProfile().weight` | **grams**: convert to kg |
| Sanity check | FTP 50–700 W, weight 30–200 kg | otherwise the field stays blank: never pace off a default, stale or unconverted profile |
| Position, speed, distance, power, timer | Garmin: `Activity.Info` | every second; position needs the `Positioning` permission |
| Climbs on saved routes: profile, start/end | Strava, via `strava_sync.py` | |
| KOM / QOM time, your PR time | Strava | |
| Your PR ride on the climb (the **ghost**) | Strava: the effort whose time equals your PR; its time and distance | needs a Strava subscription (unverified) and `activity:read_all` |
| PR ride average power | Strava: that effort's `average_watts`, only when `device_watts` is true | a measured number, never Strava's estimate |
| Bike + kit 9 kg, Crr 0.005, drivetrain 97.5 % | fixed | low-trust values, never asked |
| Air drag area | from weight: 0.32 m² × (weight / 72 kg)^0.425 (Heil 2001) | |
| Air density | from each section's altitude (standard air) | the Edge's barometer can replace it |

There is no calibration factor. One ride's factor also soaked up that day's wind, drafting, a different power meter
and an old weight, and one coasting stretch could halve every target. Bring it back only if replaying real rides
shows it beats the defaults.

## 2. What it chases

The rider chooses nothing. EdgePacer tries, in order:
1. **KOM** (K), if the fastest time the FTP allows beats it.
2. **PR** (P), if the rider has one and the FTP can beat it. The gap is measured against their PR ride.
3. **Fastest** (F): the fastest pace their FTP allows.

- **Target:** the record minus 1 %, at least 2 s, because Strava's GPS timing differs from the Edge's by 1–3 s.
- **Out-of-date FTP:** if the PR ride's measured power is above what the Garmin FTP allows for that long, the FTP is
  out of date. EdgePacer uses the FTP that ride implies and says "FTP looks out of date".
- **Out of reach mid-climb:** once even the most the FTP allows for the time left can't make the target,
  EdgePacer drops to the next one **for good**, instead of telling a beaten rider to ride at their limit.
- **Which climbs:** segments averaging 2 % or more (a setting).

## 3. The model

- **Physics, per section, steady speed, still air:** `P × 0.975 = v × (m·g·(sin a + Crr·cos a) + ½·ρ·CdA·v²)`.
  Air drag stays (leaving it out makes targets ~30 W too low on fast climbs); wind is not modelled.
- **Sections:** 50–200 m (25 m on climbs under 1 km), each with a steady grade (within 1 point), grade clamped to ±25 %.
  Descents are capped at 50 km/h.
- **Even power.** The optimal power shape gained only 0.7 s on a 9:30 climb.
- **What the rider can hold for t seconds:** `FTP × (1 + 70 s × (1/t − 1/3600))`, with t at least 60 s.
  It reaches FTP at one hour and stays there.
- **Fastest:** the even power where the climb's time equals the time that power can be held.
- **Reference curve:** where the rider must be at each metre to finish exactly at the target time. It's the PR ride
  (or, without one, the plan), scaled to end at the target time. **Every gap on screen reads from it.**

## 4. On the bike

| State | When | Screen (top half of the rider's page) |
|---|---|---|
| Idle | not on a climb | a metric the rider picks (3 s power by default); within 1 km of a climb: "next climb in 1.2 km · name · PR 1:56"; how old the last sync is |
| Active | crossed the start line of a climb ≥ 2 % | target letter (K, P or F); the gap, large; watts to hold, small; distance to go and grade |
| Finished | crossed the finish line | result for 5 s ("NEW PR −2.6 s"), then back to idle |

- **Gap:** time on the reference curve at this distance − elapsed. + means ahead, − behind, everywhere.
  Shown in **whole seconds**, "on pace" within 1 s, until the replay harness says how accurate the gap really is.
- **Clock:** time since the start line, like Strava's elapsed time. It **keeps running through stops and auto-pause**,
  so the gap keeps counting; a small "paused" tag is all that changes.
- **Watts:** ahead → the plan's watts, to bank the time. Behind → the even power that still finishes on time,
  never more than the rider can hold for the time left; when that's not enough, drop to the next target (section 2).
- **Start and finish:** within 50 m, heading within 45° of the segment, crossing a line square to it,
  interpolated between GPS fixes.
- **Distance along the climb:** GPS projected onto the segment, blended with wheel distance, never going backwards.
- **Abandon:** more than 50 m off the climb for 15 s, or riding the wrong way.
- **New PR:** the Edge keeps the effort as the new ghost until the next Strava sync.
- **Ride file:** the gap, the watts target and the target letter go into the FIT file as developer fields
  (`FitContributor`), so every ride can be replayed against the plan.

Mockups at Edge 540/840 size: [layout/on-your-page.png](layout/on-your-page.png) (source next to it).

## 5. Strava sync (`model/strava_sync.py`)

- Runs on the rider's computer: one browser sign-in to their own Strava API app, scopes `read,read_all,activity:read_all`.
  A saved login without `activity:read_all` (older, or with the box unticked) asks to sign in again.
- Lists saved routes and collects every segment on them with profile, KOM/QOM and PR.
- For climbs ≥ 2 % with a PR: finds the effort whose time equals the PR and keeps its time and distance (~100 points)
  and, if measured, its average power. Fetched again only when the PR changes.
- When a climb has no ghost, it says why: no subscription, PR ride not listed, ride not shared.
- Writes `model/data/segments.json`, kept out of git: KOM times are other riders' data.
- Stops cleanly at the rate limit and keeps what it has; reuses anything fetched in the last 12 hours.

## 6. Device limits

- A data field can't switch pages, take input or pop up screens, so it lives in one spot of the rider's own page.
- Since API 5.0 a foreground data field can make web requests through the phone (`Communications`), so a later
  version can fetch climbs itself instead of building them into the app.
- The even-power solve is one 1-D search. On the Edge it must use Newton's method for speed per section and run only
  when the rider is behind, at most once a second, to stay inside Connect IQ's time limit.
- Memory: about 15 climbs with ~100-point ghosts and 50–200 m sections. Measure it on the 540.

## 7. Next steps

1. **Real sync on your own account** with ride permission: the ghost download hasn't run against real Strava yet.
2. **Replay harness (Python)** on your Edge ride files, judged against Strava's elapsed times for the same efforts.
   Test start and finish detection first, then report the total gap error per effort: that number sets the margin
   and whether tenths can ever be shown.
3. **Device stub:** read FTP and weight (grams → kg) with the sanity check, an optional setting to override a bad
   profile, the target letter and sync age, the "FTP looks out of date" note, and measure memory and data size.
   Ride it on a climb with ClimbPro and Strava Live Segments on.
4. **Edge data field v1:** the logic above, climbs and ghosts built into the app from `segments.json`.

**Before publishing (written down, not built):** a small server with a cache (the Python sign-in and its secret can't
ship, and the rate limit of 200 / 15 min and 2,000 / day is shared by every rider); Strava's app review (one athlete
until then); a fallback for riders without a Strava subscription; Strava's API terms for showing KOM/QOM; QOM for
riders whose Garmin profile says female, PR when it's unset; one segment per stretch of road where segments overlap.

## 8. Not verified yet

- Whether `/segment_efforts` needs a Strava subscription. It worked on a subscriber account.
- Downloading PR rides with `activity:read_all`: requesting your efforts works; reading the ride hasn't run yet.
- Whether ClimbPro or Strava Live Segments covers the field, or shows a second, different gap.
- The data field memory limit on the 540 and newer.
- Strava's "hazardous" segment flag (don't push KOM watts there) and its API terms for KOM/QOM on a device.
- Entry speed is not modelled: on climbs under ~1 minute the plan is optimistic. The ghost gap is not affected.
