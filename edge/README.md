# EdgePacer for the Edge

Connect IQ data field for the Edge 540, 840, 1040, 550, 850 and 1050. Design: [docs/PLAN.md](../docs/PLAN.md).

| File | What it does |
|---|---|
| `source/Pacer.mc` | the pacing model, a port of `model/pacer.py` (its tests are the spec: change both together) |
| `source/Tracker.mc` | start line, metres done along the climb, finish, leaving the climb |
| `source/EdgePacerField.mc` | reads the Garmin profile, runs the climb, draws the field, writes the ride-file fields |
| `resources/jsonData/climbs.json` | your climbs, written by `model/export_edge.py`; not in git (other riders' KOM times) |

## Build

1. Climbs into the app, from the repo root:

   ```bash
   python3 model/strava_sync.py
   ```

   ```bash
   python3 model/export_edge.py
   ```

   or, without Strava, the made-up test climb: `python3 model/export_edge.py --example`.

2. Install the [Connect IQ SDK Manager](https://developer.garmin.com/connect-iq/sdk/), sign in, download the SDK and
   the Edge 540, 550, 840, 850, 1040 and 1050 devices. In VS Code, install the Monkey C extension and run
   "Monkey C: Generate a Developer Key" (keep the key out of git; `*.der` is ignored).

3. Open this `edge` folder in VS Code and run "Monkey C: Build for Device", or from the repo root:

   ```bash
   monkeyc -f edge/monkey.jungle -d edge540 -o edge/bin/EdgePacer.prg -y /path/to/developer_key.der
   ```

4. Simulator: Run → edge540, then play back one of your ride files over a real climb (File → Playback File).
   Check memory (File → View Memory) and the time per second (File → View Profiler) on your longest climb.

5. On the Edge: copy `EdgePacer.prg` to `GARMIN/APPS` over USB, then put EdgePacer in the top half of a data page.
   It needs the FTP and weight set in your Garmin profile.
