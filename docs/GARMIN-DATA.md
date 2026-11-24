# What a Connect IQ data field can read about the rider

Checked in the Connect IQ SDK 9.3.0 reference (October 2026). Every item below is listed as supported on the
Edge 540, 550, 840, 850, 1040 and 1050. **Used** = in the design now; **Next** = planned; **Idea** = worth testing.

## Rider profile (`Toybox.UserProfile`, permission `UserProfile`)

| Data | API | Since | Use in EdgePacer | Status |
|---|---|---|---|---|
| FTP | `getFunctionalThresholdPower(Activity.SPORT_CYCLING)` | 5.2.2 | targets and the power the rider can hold | **Used** |
| Weight | `getProfile().weight`, in **grams** | 1.0.0 | physics, drag area | **Used** |
| Power zones | `getPowerZones(Activity.SPORT_CYCLING)` | 5.2.2 | cross-check FTP; show the zone of the target watts | Idea |
| Height | `getProfile().height`, in cm | 1.0.0 | better drag area: Heil's formula uses height and weight | Next |
| Sex | `getProfile().gender` | 1.0.0 | chase the QOM instead of the KOM; PR when unset | Next |
| Cycling VO2max | `getProfile().vo2maxCycling`, null without history | 3.3.0 | sanity-check FTP; estimate it when FTP is unset | Idea |
| Heart-rate zones, resting HR | `getHeartRateZones()`, `getProfile().restingHeartRate` | 1.0.0 | stop asking for more watts when heart rate is already at the top zone | Idea |
| Birth year | `getProfile().birthYear` | 1.0.0 | nothing useful for pacing | — |
| Activity history | `getUserActivityHistory()` | 3.3.0 | type, distance, duration only, no power: little use | — |

All of these can be null or a never-changed default, so each is sanity-checked before use (FTP 50–700 W,
weight 30–200 kg); a bad profile blanks the field.

## The ride right now (`Activity.getActivityInfo()`)

| Data | Field | Use in EdgePacer | Status |
|---|---|---|---|
| Power | `currentPower`, `averagePower`, `maxPower` (W) | live power; later the W′ balance during the climb | **Used** |
| Position | `currentLocation`, `currentLocationAccuracy` (permission `Positioning`) | start/finish lines, place on the climb; blank the gap when GPS is poor | **Used** |
| Direction | `track` (GPS direction of travel), `currentHeading` | the heading check at the start; headwind component with the weather below | **Used** / Next |
| Distance, speed | `elapsedDistance` (m), `currentSpeed` (m/s) | progress between GPS fixes | **Used** |
| Clock, pause | `elapsedTime`, `timerTime` (ms), `timerState` | elapsed time since the start line; the "paused" tag | **Used** |
| Altitude, pressure | `altitude`, `ambientPressure` (Pa) | air density from the barometer instead of standard air | Next |
| Heart rate, cadence | `currentHeartRate`, `currentCadence` | effort guard; spotting coasting | Idea |
| Navigation | `distanceToNextPoint`, `elevationAtNextPoint`, `offCourseDistance` | whether the rider follows the route that holds the climb | Idea |
| Effort so far | `energyExpenditure`, `trainingEffect`, `totalAscent` | how tired the rider already is (later climbs of a long ride) | Idea |

## Weather and sensors

| Data | API | Since | Use in EdgePacer | Status |
|---|---|---|---|---|
| Wind speed and direction | `Weather.getCurrentConditions()`: `windSpeed` (m/s), `windBearing` (°) | 3.2.0 | headwind or tailwind along the climb, with no input: the missing piece on shallow climbs | Next |
| Temperature, humidity | same: `temperature` (°C), `relativeHumidity` | 3.2.0 | air density | Next |
| Car behind | `AntPlus.BikeRadar`: `RadarTarget.threat`, `range`, `speed` | 3.0.0 | hide "go harder" cues while a car is approaching (`THREAT_LEVEL_VEHICLE_APPROACHING`) | Idea |
| Power meter type | `AntPlus.BikePower`: `powerSensorType` | 2.2.0 | flag hub vs crank meters in the ride file | Idea |
| Pedal balance | `AntPlus.PedalPowerBalance`: `pedalPowerPercent`, `rightPedalIndicator` | 2.2.0 | no balance at all hints at a one-leg meter that doubles one side | Idea, unverified hint |

Weather is the phone's latest forecast cached on the Edge, so it can be minutes old and approximate.

## What the app itself can do

| Capability | API | Since | Use in EdgePacer |
|---|---|---|---|
| Web requests from the field | `Communications.makeWebRequest` (foreground data fields) | 5.0.0 | fetch climbs and ghosts from a server through the phone, instead of building them into the app |
| Background jobs | `Background` (permission `Background`) | 2.3.0 | refresh climbs while not riding |
| Data in the ride file | `FitContributor` (permission `FitContributor`) | — | record the gap, the watts target and the target letter for replays |
| Storage and settings | `Application.Storage`, `Application.Properties` | — | keep climbs and ghosts; optional overrides for a bad profile |
| Beep | `Attention.playTone` | — | a tone at the start and finish of a climb |

## Not available to a data field

Switching data pages, popping up its own screen, and taking button input. That's why EdgePacer lives in one spot
of the rider's own page.
