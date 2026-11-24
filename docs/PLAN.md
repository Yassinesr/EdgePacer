# EdgePacer: design and logic

EdgePacer is a Connect IQ data field for Garmin Edge 540 and newer, for riders with a power meter.
On every Strava climb on the rider's saved routes it shows **how far ahead of or behind the target pace they are**,
and the **watts to hold** to make it. The target is the KOM (or QOM) if they can beat it, else their PR, else the
fastest pace they can hold.

**Nothing to type into EdgePacer.** It uses the FTP and weight already in the rider's Garmin profile and a one-time
Strava sign-in; the rider has to keep those two profile values current. Bike weight, tyres and position are fixed
defaults. What else Garmin offers: [GARMIN-DATA.md](GARMIN-DATA.md).

What EdgePacer adds over Strava Live Segments, ClimbPro and Power Guide, which the Edge already has: the watts to hold
to beat the target, and falling back from KOM to PR to the fastest possible. Lead with that.

Reference model: `model/pacer.py` (tests: `model/test_pacer.py`). Strava download: `model/strava_sync.py`.

---

## 1. Inputs

| Input | Source | Notes |
|---|---|---|
| FTP | Garmin: `UserProfile.getFunctionalThresholdPower(Activity.SPORT_CYCLING)` | API 5.2.2+, Edge 540/550/840/850/1040/1050 |
| Weight | Garmin: `UserProfile.getProfile().weight` | **grams**: convert to kg |
| Sex | Garmin: `UserProfile.getProfile().gender` | chase the QOM for women, the KOM for men, no course record when unset |
| Sanity check | FTP 50–700 W, weight 30–200 kg | otherwise show "SET FTP" or "SET WEIGHT", never a number |
| Position, speed, distance, power, timer | Garmin: `Activity.Info` | every second; position needs the `Positioning` permission |
| Altitude, barometric pressure | Garmin: `Activity.Info.altitude`, `ambientPressure` | air density; standard air until wired |
| Climbs on saved routes: profile, start/end | Strava, via `strava_sync.py` | hazardous segments are never paced |
| KOM / QOM time, your PR time | Strava | |
| Your PR ride on the climb (the **ghost**) | Strava: the effort whose time equals your PR; time and distance every 25 m | needs a Strava subscription (unverified) and `activity:read` |
| PR ride average power | Strava: that effort's `average_watts`, only when `device_watts` is true | only for the "FTP looks out of date" warning |
| Bike + kit 9 kg, Crr 0.005, drivetrain 97.5 % | fixed | low-trust values, never asked |
| Air drag area | from weight: 0.32 m² × (weight / 72 kg)^0.425 (Heil 2001) | |

There is no stored calibration factor and EdgePacer never changes the FTP. Once the rider has ridden steadily for
20 s on a climb, the watts come from their own power (section 3), which cancels the defaults' errors live.

## 2. What it chases

The rider chooses nothing. EdgePacer tries, in order:
1. **KOM** (K), or the QOM for women, if the fastest the rider can hold beats it.
2. **PR** (P), if the rider has one and can beat it. The gap is measured against their PR ride.
3. **Fastest** (F): the fastest pace they can hold.

- **Target:** the record minus 1 %, at least 2 s, because Strava's GPS timing differs from the Edge's by 1–3 s.
- **Out of reach mid-climb:** once even the most the rider can hold for the time left (with the reserve they have
  left) can't make the target, EdgePacer drops to the next one **for good**. The letter flashes and the Edge beeps once.
- **FTP looks out of date:** if the PR ride's measured power is above what the Garmin FTP allows for that long,
  the idle line says so **before** the climb. A warning only: one ride (a tailwind, a one-sided meter) never changes
  the FTP.
- **Which climbs:** segments averaging 2 % or more (a setting), never one Strava marks hazardous.

## 3. The model

- **Physics, per section, steady speed, still air:** `P × 0.975 = v × (m·g·(sin a + Crr·cos a) + ½·ρ·CdA·v²)`.
  Air drag stays (leaving it out makes targets ~30 W too low on fast climbs); wind is not modelled.
- **Sections:** 50–200 m (25 m on climbs under 1 km), each with a steady grade (within 1 point), grade clamped to ±25 %.
  Descents are capped at 50 km/h.
- **Even power.** The optimal power shape gained only 0.7 s on a 9:30 climb.
- **The most a rider can hold for t seconds:** `min(1.5 × FTP, FTP + W′left × (1/t − 1/3600))`, t at least 60 s.
  W′ is the reserve above FTP, 70 s × FTP when fresh (about 21 kJ at 300 W), tracked from live power with Skiba's
  model: it drains above FTP and refills below it. Fresh, this reaches FTP at one hour. **The same ceiling sets the
  watts shown and decides when a target is out of reach,** so "K" never stays up while the plan needs 640 W.
- **Fastest:** the even power where the climb's time equals the time that power can be held.
- **Reference curve:** where the rider must be at each metre to finish exactly at the target time. It's the PR ride
  (or, without one, the plan), scaled to end at the target time. **Every gap on screen reads from it.**
- **Watts after 20 s of steady riding:** the rider's own smoothed power × (model watts needed ÷ model watts at their
  current speed). The model only splits climbing from air drag; the level comes from the rider's meter, which cancels
  weight, tyres, position, wind and air density. Weaker on 2–4 % grades and when drafting. Before 20 s: the model.

## 4. On the bike

| State | When | Screen (top half of the rider's page) |
|---|---|---|
| Idle | not on a climb | a metric the rider picks (3 s power by default); within 1 km of a climb: "next climb in 1.2 km · name · PR 1:56"; sync age; "FTP looks out of date" when it applies |
| Active | crossed the start line of a climb ≥ 2 % | target letter (K, P or F); the gap, large, with the word "ahead" or "behind"; watts to hold, small; distance to go and grade |
| Finished | crossed the finish line | result for 5 s ("NEW PR −2.6 s"), then back to idle |
| Can't pace | bad profile, no GPS fix | "SET FTP", "SET WEIGHT" or "NO FIX" instead of numbers |

- **Gap:** time on the reference curve at this distance − elapsed, in **whole seconds**, "on pace" within 1 s,
  until the replay harness says how accurate it really is.
- **Clock:** time since the start line, like Strava's elapsed time. It **keeps running through stops and auto-pause**,
  so the gap keeps counting.
- **Watts:** ahead → the plan's watts, to bank the time. Behind → the even power that still finishes on time,
  never above the ceiling (section 3). **No watts while stopped and for 10 s after**, so nobody sprints away from
  a red light.
- **Target switch:** the letter flashes and the Edge beeps once (`Attention.playTone`).
- **Start and finish:** within 50 m, heading within 45° of the segment, crossing a line square to it,
  interpolated between GPS fixes.
- **Distance along the climb:** GPS projected onto the segment, blended with wheel distance, never going backwards.
  Its error is unmeasured and matters more than the watts: 1–2 % on 5 km is 10–20 s of false gap.
- **Abandon:** more than 50 m off the climb for 15 s, or riding the wrong way.
- **New PR:** the Edge keeps the effort as the new ghost until the next Strava sync.
- **Ride file, from the first build:** the gap, the target letter, the watts target and the model's speed go into
  the FIT file as developer fields (`FitContributor`), so every ride can be replayed and the defaults checked.

Mockups at Edge 540/840 size: [layout/on-your-page.png](layout/on-your-page.png) (source next to it).

## 5. Strava sync (`model/strava_sync.py`)

- Runs on the rider's computer: one browser sign-in to their own Strava API app, scopes `read,read_all,activity:read`
  (`read_all` for private saved routes; private PR rides stay private and get no ghost).
  A saved login without ride permission asks to sign in again.
- Lists saved routes and collects every segment on them with profile, KOM/QOM, PR and Strava's hazardous flag.
- For climbs ≥ 2 % with a PR: finds the effort whose time equals the PR, keeps one point every 25 m and, if measured,
  its average power. Fetched again only when the PR changes.
- When a climb has no ghost, it says why: no subscription, PR ride not listed, ride not shared.
- Writes `model/data/segments.json`, kept out of git: KOM times are other riders' data.
- Stops cleanly at the rate limit and keeps what it has; reuses anything fetched in the last 12 hours.

## 6. Device limits

- A data field can't switch pages, take input or pop up screens, so it lives in one spot of the rider's own page.
- Since API 5.0 a foreground data field can make web requests through the phone (`Communications`).
- The even-power solve is one 1-D search. On the Edge it must use Newton's method for speed per section and run only
  when needed, at most once a second, to stay inside Connect IQ's time limit.
- Memory: about 15 climbs with 25 m ghosts and 50–200 m sections. Measure it on the 540.
- Permissions: `UserProfile`, `Positioning`, `FitContributor`, and `Communications` once climbs come through the phone.

## 7. Next steps

1. **First, it decides the product:** run the ghost download end to end on your account and on a free Strava account;
   check the PR ride's time and distance match Strava to the second. Then read Strava's API agreement against what the
   field shows (KOM/QOM times, a Live Segments-like gap).
2. **Replay harness (Python)** on your Edge ride files, judged against Strava's elapsed times for the same efforts.
   Start and finish detection first, then the total gap error per effort: that sets the margin.
3. **Device stub:** read FTP and weight (grams → kg) with the sanity check and status words, the target letter, sync age,
   the FTP warning, the ride-file fields; measure memory. Ride it with ClimbPro and Strava Live Segments on.
4. **Edge data field v1:** the logic above, climbs and ghosts built into the app from `segments.json`.

**Before publishing:** apply for Strava's app review now (one athlete until then); a privacy policy; sign-in on the
phone or a web page with a small server and cache (the Python sign-in and its secret can't ship, and the rate limit of
200 / 15 min and 2,000 / day is shared by every rider); a fallback for riders without a Strava subscription; store text
that says what the rider must set up; one segment per stretch of road where segments overlap.

## 8. Not verified yet

- Whether `/segment_efforts` needs a Strava subscription. It worked on a subscriber account.
- Downloading PR rides with `activity:read`: requesting your efforts works; reading the ride hasn't run yet.
- Strava's API terms for showing KOM/QOM times and a gap like Live Segments' on a device.
- Strava's `hazardous` field (stored when present).
- Whether ClimbPro or Strava Live Segments covers the field, or shows a second, different gap.
- The data field memory limit on the 540 and newer.
- Entry speed is not modelled: on climbs under ~1 minute the plan is optimistic. The ghost gap is not affected.
