import time
import unittest
import urllib.error

from pacer import parse_time
from strava_sync import RateLimited, pr_ghost, sync

PROFILE = {"distance": [0, 300], "altitude": [10, 30], "latlng": [[32.0, 118.8], [32.0, 118.9]]}
GHOST = {"distance": [0, 280, 559], "time": [0, 60, 120]}
EFFORTS = [{"elapsed_time": 125, "activity": {"id": 99}, "start_index": 1, "end_index": 4},
           {"elapsed_time": 120, "activity": {"id": 98}, "start_index": 1, "end_index": 3,
            "average_watts": 310.0, "device_watts": True}]


def fake_strava(limit_after=None, not_found=None, efforts=EFFORTS):
    """Two saved routes sharing segment 7; segment 8 has a 45 s KOM. Records every call."""
    calls = []
    data = {
        "/athletes/1/routes": {1: [{"id": 11, "id_str": "11"}, {"id": 12, "id_str": "12"}], 2: []},
        "/routes/11": {"name": "Purple Mt loop", "segments": [{"id": 7}, {"id": 8}]},
        "/routes/12": {"name": "Wall repeats", "segments": [{"id": 7}]},
        "/segments/7": {"name": "Wall", "distance": 559, "average_grade": 4.9, "xoms": {"kom": "1:21", "qom": "1:40"},
                        "athlete_segment_stats": {"pr_elapsed_time": 120, "pr_date": "2026-04-11"}},
        "/segments/8": {"name": "Kicker", "distance": 300, "average_grade": 6.0, "xoms": {"kom": "45s"}, "hazardous": True},
        "/segments/7/streams": {k: {"data": v} for k, v in PROFILE.items()},
        "/segments/8/streams": {k: {"data": v} for k, v in PROFILE.items()},
        "/segment_efforts": efforts,
        "/activities/98/streams": {"time": {"data": [0, 10, 70, 130, 140]}, "distance": {"data": [0, 20, 300, 579, 600]}},
    }

    def get(path, **params):
        calls.append(path)
        if limit_after is not None and len(calls) > limit_after:
            raise RateLimited
        if path == not_found:
            raise urllib.error.HTTPError(path, 404, "Not Found", None, None)
        reply = data[path]
        return reply[params["page"]] if "page" in params else reply
    return get, calls


class SyncTest(unittest.TestCase):
    def test_collects_route_segments_with_kom_and_pr(self):
        get, calls = fake_strava()
        segments, complete = sync(get, 1, known={})
        self.assertTrue(complete)
        self.assertEqual(segments["7"]["routes"], ["Purple Mt loop", "Wall repeats"])
        self.assertEqual((segments["7"]["kom_s"], segments["7"]["qom_s"], segments["7"]["pr_s"]), (81, 100, 120))
        self.assertEqual((segments["8"]["kom_s"], segments["8"]["pr_s"]), (45, None))
        self.assertEqual(segments["8"]["profile"], PROFILE)
        self.assertEqual(calls.count("/segments/7"), 1)  # shared segment fetched once
        self.assertEqual(segments["7"]["ghost"], GHOST)  # the 120 s effort, cut to the segment
        self.assertIsNone(segments["8"]["ghost"])  # never ridden
        self.assertEqual(segments["7"]["pr_watts"], 310.0)
        self.assertEqual((segments["7"]["hazardous"], segments["8"]["hazardous"]), (False, True))

    def test_ghost_keeps_one_point_every_25_m(self):
        n = 601  # a 600 m PR ride recorded every metre
        stream = {"time": {"data": [i * 0.3 for i in range(n)]}, "distance": {"data": [float(i) for i in range(n)]}}
        effort = [{"elapsed_time": 180, "activity": {"id": 5}, "start_index": 0, "end_index": n - 1}]
        get = lambda path, **_: effort if path == "/segment_efforts" else stream
        ghost, _, _ = pr_ghost(get, "7", 180)
        self.assertEqual(len(ghost["distance"]), 25)  # 0, 25, ..., 600
        self.assertEqual(ghost["distance"][-1], 600.0)
        self.assertAlmostEqual(ghost["time"][-1], 180.0)

    def test_ghost_is_the_pr_ride_itself_or_none_with_a_reason(self):
        get, _ = fake_strava(efforts=EFFORTS[:1])  # the PR is beyond the listed efforts, only a slower one is there
        segments, _ = sync(get, 1, known={})
        self.assertIsNone(segments["7"]["ghost"])
        self.assertIn("isn't among", segments["7"]["ghost_missing"])

    def test_estimated_power_is_not_kept(self):
        get, _ = fake_strava(efforts=[EFFORTS[0], {**EFFORTS[1], "device_watts": False}])
        self.assertIsNone(sync(get, 1, known={})[0]["7"]["pr_watts"])

    def test_ghost_is_kept_until_the_pr_changes(self):
        get, calls = fake_strava()
        segments, _ = sync(get, 1, known={"7": {"profile": PROFILE, "pr_s": 120, "ghost": GHOST, "pr_watts": 310.0}})
        self.assertNotIn("/segment_efforts", calls)
        self.assertEqual(segments["7"]["pr_watts"], 310.0)
        get, calls = fake_strava()
        sync(get, 1, known={"7": {"profile": PROFILE, "pr_s": 130, "ghost": GHOST}})  # beaten since
        self.assertIn("/segment_efforts", calls)

    def test_known_profiles_are_not_downloaded_again(self):
        get, calls = fake_strava()
        sync(get, 1, known={"7": {"profile": PROFILE}})
        self.assertNotIn("/segments/7/streams", calls)
        self.assertIn("/segments/8/streams", calls)

    def test_recently_fetched_segments_are_reused_whole(self):
        get, calls = fake_strava()
        fresh = {"id": "7", "name": "Wall", "kom_s": 81, "profile": PROFILE, "fetched": time.time()}
        segments, _ = sync(get, 1, known={"7": fresh, "8": {"profile": PROFILE, "fetched": 0}})
        self.assertNotIn("/segments/7", calls)
        self.assertIn("/segments/8", calls)  # stale: KOM and PR fetched again
        self.assertEqual(segments["7"]["routes"], ["Purple Mt loop", "Wall repeats"])

    def test_unreadable_pr_ride_skips_the_ghost_not_the_sync(self):
        get, _ = fake_strava(not_found="/activities/98/streams")  # e.g. no activity permission
        segments, complete = sync(get, 1, known={})
        self.assertTrue(complete)
        self.assertIsNone(segments["7"]["ghost"])
        self.assertIn("won't share", segments["7"]["ghost_missing"])
        self.assertEqual(segments["7"]["pr_s"], 120)

    def test_rate_limit_keeps_last_runs_records(self):
        get, _ = fake_strava(limit_after=6)  # dies before segment 8
        old = {"8": {"name": "Kicker (last run)"}}
        segments, complete = sync(get, 1, known=old)
        self.assertFalse(complete)
        self.assertEqual(segments["8"], old["8"])
        self.assertIn("7", segments)

    def test_strava_time_formats(self):
        self.assertEqual([parse_time(t) for t in ("5:34", "1:02:03", "45s")], [334, 3723, 45])


if __name__ == "__main__":
    unittest.main()
