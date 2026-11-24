"""Download the Strava segments on your saved routes: profile, KOM/QOM, your PR and, on climbs, your PR ride (the ghost).

One-time setup:
  1. Create an API app at https://www.strava.com/settings/api
     and set its "Authorization Callback Domain" to: localhost
  2. export STRAVA_CLIENT_ID=<Client ID>  STRAVA_CLIENT_SECRET=<Client Secret>
Then, whenever you save a new route:
  python3 strava_sync.py
The first run opens Strava in your browser to sign in. The login is kept in
~/.config/edgepacer/strava-token.json, never in the repo.
Writes data/segments.json, kept out of git: KOM times are other riders' data.
Each run uses about 1 Strava request per route and per segment (2 for a new segment, 2 more for a new PR on a climb);
your app's limit is on strava.com/settings/api. If it stops at the limit, run it again 15 minutes later.
"""
import collections
import http.server
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from pacer import parse_time

API = "https://www.strava.com/api/v3"
TOKEN = Path.home() / ".config" / "edgepacer" / "strava-token.json"
OUT = Path(__file__).parent / "data" / "segments.json"
PORT = 8723
FRESH = 12 * 3600  # s; KOM and PR times older than this are fetched again
MIN_GRADE = 2.0    # %, EdgePacer paces climbs this steep or steeper, so only they get a ghost


class RateLimited(Exception):
    pass


def post_token(params):
    with urllib.request.urlopen("https://www.strava.com/oauth/token", urllib.parse.urlencode(params).encode()) as r:
        return json.load(r)


def save_token(tok):
    TOKEN.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(TOKEN, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the refresh token is a password
    with os.fdopen(fd, "w") as f:
        json.dump(tok, f)


def sign_in(client_id, client_secret):
    """Browser sign-in, once. Returns the saved token."""
    state, reply = secrets.token_urlsafe(16), {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            reply.update({k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).items()})
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"EdgePacer: done, you can close this tab.")

        def log_message(self, *args):
            pass

    url = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode({
        "client_id": client_id, "response_type": "code", "redirect_uri": f"http://localhost:{PORT}/",
        # read_all: private routes; activity:read_all: your PR rides, for the ghosts
        "approval_prompt": "auto", "scope": "read,read_all,activity:read_all", "state": state})
    print("Sign in to Strava in your browser:", url)
    webbrowser.open(url)
    with http.server.HTTPServer(("localhost", PORT), Handler) as server:
        while "code" not in reply and "error" not in reply:
            server.handle_request()
    if reply.get("state") != state or "code" not in reply:
        raise SystemExit(f"Strava sign-in failed: {reply.get('error', 'unexpected reply')}")
    tok = post_token({"client_id": client_id, "client_secret": client_secret,
                      "code": reply["code"], "grant_type": "authorization_code"})
    tok = {k: tok[k] for k in ("access_token", "refresh_token", "expires_at")} | {"athlete_id": tok["athlete"]["id"],
                                                                                "scope": reply.get("scope", "")}
    if "activity:read_all" not in tok["scope"]:
        print("Without permission to read your rides there are no ghosts. Run again and keep that box ticked.")
    save_token(tok)
    return tok


def access_token():
    client_id, client_secret = os.environ.get("STRAVA_CLIENT_ID"), os.environ.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET first (see the top of strava_sync.py).")
    tok = json.loads(TOKEN.read_text()) if TOKEN.exists() else {}
    if "activity:read_all" not in tok.get("scope", ""):  # no login yet, or one that can't read rides
        tok = sign_in(client_id, client_secret)
    if tok["expires_at"] < time.time() + 60:  # Strava logins last 6 hours
        new = post_token({"client_id": client_id, "client_secret": client_secret,
                          "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        tok.update({k: new[k] for k in ("access_token", "refresh_token", "expires_at")})
        save_token(tok)
    return tok


def getter(access):
    def get(path, **params):
        url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": f"Bearer {access}"})) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RateLimited from e
            raise
    return get


def pr_ghost(get, sid, pr_s):
    """(ghost, pr_watts, why). ghost: your PR ride on the segment as {"distance", "time"} from its start, about
    100 points. pr_watts: its measured average power, None if Strava estimated it. why: the reason there is no ghost."""
    try:
        efforts = get("/segment_efforts", segment_id=sid, per_page=200)
    except urllib.error.HTTPError:
        return None, None, "Strava won't list your efforts (needs a Strava subscription)"
    e = next((e for e in efforts if e["elapsed_time"] == pr_s), None)  # the PR itself, not just the fastest listed
    if not e:
        return None, None, "your PR ride isn't among your listed efforts"
    try:
        s = get(f"/activities/{e['activity']['id']}/streams", keys="time,distance", key_by_type="true")
    except urllib.error.HTTPError:
        return None, None, "Strava won't share the PR ride (permission, or the ride is private or deleted)"
    i0, i1 = e["start_index"], e["end_index"]
    t, d = s["time"]["data"][i0:i1 + 1], s["distance"]["data"][i0:i1 + 1]
    if len(t) < 2:
        return None, None, "the PR ride has no data on this segment"
    step = max(1, len(t) // 100)  # the Edge holds every climb's ghost in memory
    idx = list(range(0, len(t) - 1, step)) + [len(t) - 1]
    ghost = {"distance": [d[i] - d[0] for i in idx], "time": [t[i] - t[0] for i in idx]}
    return ghost, e.get("average_watts") if e.get("device_watts") else None, None


def collect(get, athlete_id, known, found):
    """Fill found with {segment id: record} for every segment on your saved routes.
    known: records from the last run. Their profiles are reused, since a segment's shape doesn't change,
    and records fetched within FRESH seconds are reused whole, so a run cut off by the rate limit can finish."""
    page = 1
    while routes := get(f"/athletes/{athlete_id}/routes", page=page, per_page=100):
        for route in routes:
            detail = get(f"/routes/{route.get('id_str') or route['id']}")
            for s in detail.get("segments") or []:
                sid = str(s["id"])
                old = known.get(sid, {})
                if sid not in found and time.time() - old.get("fetched", 0) < FRESH:
                    found[sid] = {**old, "routes": []}
                elif sid not in found:
                    seg = get(f"/segments/{sid}")
                    xoms = seg.get("xoms") or {}
                    mine = seg.get("athlete_segment_stats") or seg.get("athlete_pr_effort") or {}
                    profile = old.get("profile")
                    if not profile:
                        streams = get(f"/segments/{sid}/streams", keys="distance,altitude,latlng", key_by_type="true")
                        profile = {k: streams[k]["data"] for k in ("distance", "altitude", "latlng") if k in streams}
                    pr_s = mine.get("pr_elapsed_time")
                    ghost, pr_watts, why = None, None, None
                    if old.get("pr_s") == pr_s:  # a new PR needs a new ghost
                        ghost, pr_watts, why = old.get("ghost"), old.get("pr_watts"), old.get("ghost_missing")
                    if not ghost and pr_s and seg["average_grade"] >= MIN_GRADE:
                        ghost, pr_watts, why = pr_ghost(get, sid, pr_s)
                    found[sid] = {
                        "id": sid, "name": seg["name"], "distance_m": seg["distance"], "avg_grade": seg["average_grade"],
                        "kom_s": parse_time(xoms["kom"]) if xoms.get("kom") else None,
                        "qom_s": parse_time(xoms["qom"]) if xoms.get("qom") else None,
                        "pr_s": pr_s, "pr_date": mine.get("pr_date"),
                        "routes": [], "profile": profile, "ghost": ghost, "pr_watts": pr_watts, "ghost_missing": why,
                        "fetched": time.time()}
                if detail["name"] not in found[sid]["routes"]:
                    found[sid]["routes"].append(detail["name"])
        page += 1


def sync(get, athlete_id, known):
    """(segments, complete). At Strava's rate limit it keeps last run's records for segments it didn't reach."""
    found = {}
    try:
        collect(get, athlete_id, known, found)
        return found, True
    except RateLimited:
        return {**known, **found}, False


def main():
    tok = access_token()
    known = json.loads(OUT.read_text())["segments"] if OUT.exists() else {}
    segments, complete = sync(getter(tok["access_token"]), tok["athlete_id"], known)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")  # write then rename, so an interrupted run never leaves a broken file
    tmp.write_text(json.dumps({"updated": time.strftime("%Y-%m-%dT%H:%M:%S"), "segments": segments},
                              ensure_ascii=False, indent=1))
    os.replace(tmp, OUT)
    print(f"{len(segments)} segments saved to {OUT}")
    ridden = [s for s in segments.values() if s.get("pr_s") and s.get("avg_grade", 0) >= MIN_GRADE]
    print(f"Ghosts for {sum(1 for s in ridden if s.get('ghost'))} of the {len(ridden)} climbs you've ridden.")
    for why, n in collections.Counter(s.get("ghost_missing") for s in ridden if not s.get("ghost")).items():
        print(f"  {n} without a ghost: {why or 'not fetched yet, run again'}")
    if not complete:
        print("Stopped at your Strava app's rate limit (see strava.com/settings/api). Run it again in 15 minutes to finish.")


if __name__ == "__main__":
    main()
