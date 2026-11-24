"""Download the Strava segments on your saved routes: profile, KOM/QOM and your PR.

One-time setup:
  1. Create an API app at https://www.strava.com/settings/api
     and set its "Authorization Callback Domain" to: localhost
  2. export STRAVA_CLIENT_ID=<Client ID>  STRAVA_CLIENT_SECRET=<Client Secret>
Then, whenever you save a new route:
  python3 strava_sync.py
The first run opens Strava in your browser to sign in. The login is kept in
~/.config/edgepacer/strava-token.json, never in the repo.
Writes data/segments.json, kept out of git: KOM times are other riders' data.
Each run uses about 1 Strava request per route and per segment (2 for a new segment);
Strava allows 100 every 15 minutes. If it stops at the limit, run it again later.
"""
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
        "approval_prompt": "auto", "scope": "read,read_all", "state": state})
    print("Sign in to Strava in your browser:", url)
    webbrowser.open(url)
    with http.server.HTTPServer(("localhost", PORT), Handler) as server:
        while "code" not in reply and "error" not in reply:
            server.handle_request()
    if reply.get("state") != state or "code" not in reply:
        raise SystemExit(f"Strava sign-in failed: {reply.get('error', 'unexpected reply')}")
    tok = post_token({"client_id": client_id, "client_secret": client_secret,
                      "code": reply["code"], "grant_type": "authorization_code"})
    tok = {k: tok[k] for k in ("access_token", "refresh_token", "expires_at")} | {"athlete_id": tok["athlete"]["id"]}
    save_token(tok)
    return tok


def access_token():
    client_id, client_secret = os.environ.get("STRAVA_CLIENT_ID"), os.environ.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit("Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET first (see the top of strava_sync.py).")
    tok = json.loads(TOKEN.read_text()) if TOKEN.exists() else sign_in(client_id, client_secret)
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


def collect(get, athlete_id, known, found):
    """Fill found with {segment id: record} for every segment on your saved routes.
    known: records from the last run; their profiles are reused, since a segment's shape doesn't change."""
    page = 1
    while routes := get(f"/athletes/{athlete_id}/routes", page=page, per_page=100):
        for route in routes:
            detail = get(f"/routes/{route.get('id_str') or route['id']}")
            for s in detail.get("segments") or []:
                sid = str(s["id"])
                if sid not in found:
                    seg = get(f"/segments/{sid}")
                    xoms = seg.get("xoms") or {}
                    mine = seg.get("athlete_segment_stats") or seg.get("athlete_pr_effort") or {}
                    profile = known.get(sid, {}).get("profile")
                    if not profile:
                        streams = get(f"/segments/{sid}/streams", keys="distance,altitude,latlng", key_by_type="true")
                        profile = {k: streams[k]["data"] for k in ("distance", "altitude", "latlng") if k in streams}
                    found[sid] = {
                        "id": sid, "name": seg["name"], "distance_m": seg["distance"], "avg_grade": seg["average_grade"],
                        "kom_s": parse_time(xoms["kom"]) if xoms.get("kom") else None,
                        "qom_s": parse_time(xoms["qom"]) if xoms.get("qom") else None,
                        "pr_s": mine.get("pr_elapsed_time"), "pr_date": mine.get("pr_date"),
                        "routes": [], "profile": profile}
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
    if not complete:
        print("Stopped at Strava's limit of 100 requests per 15 minutes. Run it again later to finish.")


if __name__ == "__main__":
    main()
