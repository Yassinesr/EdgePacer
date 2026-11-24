# What a Connect IQ data field can read about the rider

Checked in the Connect IQ SDK 9.3.0 reference (October 2026). Every item below is listed as supported on the
Edge 540, 550, 840, 850, 1040 and 1050. **Used** = in the design; **v1** = goes into the first Edge version;
**Later** / **Cut** = decided against for v1, with the reason.

## Rider profile (`Toybox.UserProfile`, permission `UserProfile`)

| Data | API | Since | Use in EdgePacer | Status |
|---|---|---|---|---|
| FTP | `getFunctionalThresholdPower(Activity.SPORT_CYCLING)` | 5.2.2 | targets and the most the rider can hold; never changed by EdgePacer | **Used** |
| Weight | `getProfile().weight`, in **grams** | 1.0.0 | physics, drag area | **Used** |
| Sex | `getProfile().gender` | 1.0.0 | QOM for women, KOM for men, no course record when unset | **v1** |
| Height | `getProfile().height`, in cm | 1.0.0 | Heil's drag area uses height too, but riding position matters more; relative watts cancel it anyway | Later |
| Cycling VO2max | `getProfile().vo2maxCycling`, null without history | 3.3.0 | a second opinion on FTP | Later |
| Power zones | `getPowerZones(Activity.SPORT_CYCLING)` | 5.2.2 | derived from FTP, adds nothing | Cut |
| Heart-rate zones, resting HR | `getHeartRateZones()`, `getProfile().restingHeartRate` | 1.0.0 | heart rate lags; the reserve (W′) tracked from power does the braking | Cut for v1 |
| Birth year, activity history | `getProfile().birthYear`, `getUserActivityHistory()` | 1.0.0 / 3.3.0 | nothing useful for pacing | Cut |

All of these can be null or a never-changed default. FTP and weight are sanity-checked (FTP 50–700 W,
weight 30–200 kg); a bad value shows "SET FTP" or "SET WEIGHT" instead of numbers.

## The ride right now (`Activity.getActivityInfo()`)

| Data | Field | Use in EdgePacer | Status |
|---|---|---|---|
| Power | `currentPower` (W) | the reserve above FTP (W′), and after 20 s of steady riding the watts to hold | **Used** |
| Position | `currentLocation`, `currentLocationAccuracy` (permission `Positioning`) | start/finish lines, place on the climb; "NO FIX" when GPS is poor | **Used** |
| Direction | `track` (GPS direction of travel) | the heading check at the start | **Used** |
| Distance, speed | `elapsedDistance` (m), `currentSpeed` (m/s) | progress between GPS fixes; relative watts | **Used** |
| Clock, pause | `elapsedTime`, `timerTime` (ms), `timerState` | elapsed time since the start line; no watts for 10 s after a stop | **Used** |
| Altitude, pressure | `altitude`, `ambientPressure` (Pa) | air density from the barometer | **v1** |
| Heart rate, cadence | `currentHeartRate`, `currentCadence` | | Cut for v1 |
| Navigation | `distanceToNextPoint`, `elevationAtNextPoint`, `offCourseDistance` | | Later |
| Effort so far | `energyExpenditure`, `trainingEffect`, `totalAscent` | the live reserve already covers fatigue on the climb | Cut |

## Weather and sensors

| Data | API | Since | Why not in v1 |
|---|---|---|---|
| Wind, temperature, humidity | `Weather.getCurrentConditions()` | 3.2.0 | a forecast for 10 m above ground, minutes old, while climbs are sheltered; relative watts already absorb the real wind. Cut for v1 |
| Car behind | `AntPlus.BikeRadar`: `RadarTarget.threat`, `range`, `speed` | 3.0.0 | the watts are capped and blanked after stops; Later |
| Power meter type, pedal balance | `AntPlus.BikePower`: `powerSensorType`; `AntPlus.PedalPowerBalance` | 2.2.0 | can't reliably spot a one-leg meter; Cut |

## What the app itself can do

| Capability | API | Use in EdgePacer | Status |
|---|---|---|---|
| Ride file fields | `FitContributor` (permission `FitContributor`) | gap, target letter, watts target, model speed: every ride becomes a replay test | **v1, first build** |
| Beep | `Attention.playTone` | once when the target switches | **v1** |
| Storage and settings | `Application.Storage`, `Application.Properties` | climbs, ghosts, the new-PR ghost | **v1** |
| Web requests from the field | `Communications.makeWebRequest` (foreground data fields, API 5.0.0+) | fetch climbs through the phone instead of building them into the app | Later (needs the server) |
| Background jobs | `Background` (permission `Background`) | refresh climbs while not riding | Later |

## Not available to a data field

Switching data pages, popping up its own screen, and taking button input. That's why EdgePacer lives in one spot
of the rider's own page, and why the "FTP looks out of date" warning goes on the idle line before a climb.
